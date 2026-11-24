"""Resumable CLI waits. Only start() can submit a paid operation, exactly once."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sys
import tempfile
import threading
import time
from functools import wraps
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx

MEDIA_OPERATIONS = {'image-generate': 'image', 'video-generate': 'video', 'audio-generate': 'audio', 'video-white-model': 'video'}
MANAGED_OPERATIONS = {**MEDIA_OPERATIONS, 'text-generate': 'text'}
SUCCEEDED = {'completed', 'succeeded', 'success'}
FAILED = {'failed', 'cancelled', 'canceled', 'error'}
PENDING = {'pending', 'queued', 'queuing', 'processing', 'running', 'waiting', 'submitted', 'accepted', 'in_progress'}
ERRORS = (OSError, ValueError, KeyError, httpx.HTTPError)
_canvas_locks = threading.local()


@contextmanager
def canvas_write(base, project_id, timeout=300):
    """Same-user, cross-process serialization of a canvas read/modify/write.

    Reentrant for the transport POST inside a Runner transaction. Never hold
    this lock while submitting or waiting for a generation.
    """
    if not project_id:
        yield
        return
    key = hashlib.sha256((base.rstrip('/') + '\n' + str(project_id)).encode()).hexdigest()
    held = getattr(_canvas_locks, 'held', set())
    if key in held:
        yield
        return
    path = Path.home() / '.mira' / 'canvas-write-locks' / key
    with locked(path, timeout=timeout, busy_message='此画布的 CLI 写入等待超时；请用原记录接续'):
        _canvas_locks.held = held | {key}
        try:
            yield
        finally:
            _canvas_locks.held = held


def serialized_canvas(method):
    @wraps(method)
    def wrapped(self, state, path):
        with canvas_write(self.base, state.get('projectId')):
            return method(self, state, path)
    return wrapped


def retryable_error(exc):
    """Only reads/attachments may use this classification, never paid submission."""
    if isinstance(exc, httpx.TransportError):
        return True
    status = getattr(exc, 'status_code', None)
    if status is None:  # Compatibility with older HTTP adapters and receipts.
        match = re.match(r'Mira HTTP (\d{3}) ', str(exc))
        status = int(match[1]) if match else None
    return status in {408, 409, 425, 429, 500, 502, 503, 504}


def read_json(path, encoding='utf-8-sig'):
    """Coordinate short reads with atomic replacement, including across processes."""
    path = Path(path)
    with locked(path.with_suffix(path.suffix + '.io'), timeout=5):
        return json.loads(path.read_text(encoding=encoding))


def validate_options(options):
    for key in ('timeout', 'poll_interval'):
        value = getattr(options, key)
        if not math.isfinite(value) or value < 0 or (key == 'poll_interval' and not 1 <= value <= 60):
            raise ValueError('--timeout 必须是非负有限秒数，--poll-interval 必须在 1 到 60 秒之间')
    project, node = getattr(options, 'project_id', None), getattr(options, 'node_id', None)
    if bool(project) != bool(node):
        raise ValueError('--project-id 和 --node-id 必须同时填写')
    if project:
        UUID(project)
        if not node.strip():
            raise ValueError('--node-id 不能为空')
    if getattr(options, 'task_id', None):
        UUID(options.task_id)


@contextmanager
def locked(path, timeout=0, busy_message='此任务记录正在被另一个 CLI 使用，请等待该进程结束'):
    """A separate persistent lock file avoids inode replacement races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.with_suffix(path.suffix + '.lock').open('a+b')
    acquired = False
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise ValueError(busy_message) from None
                time.sleep(.01)
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def save(path, state):
    path = Path(path)
    with locked(path.with_suffix(path.suffix + '.io'), timeout=5):
        _save_atomic(path, state)


def _save_atomic(path, state):
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 1.5
        while True:
            try:
                os.replace(name, path)
                break
            except PermissionError as exc:
                if os.name != 'nt' or exc.winerror not in {5, 32, 33} or time.monotonic() >= deadline:
                    raise
                time.sleep(.025)
    finally:
        if name and Path(name).exists():
            Path(name).unlink()


def normalized(value):
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalized(v) for v in value]
    if isinstance(value, str) and value.startswith(('https://', 'http://')):
        url = urlsplit(value)
        # Canvas reads renew signed storage URLs; signatures are not user edits.
        query = [(k, v) for k, v in parse_qsl(url.query, keep_blank_values=True)
                 if k.lower() not in {'signature', 'expires', 'ossaccesskeyid', 'accesskeyid'}
                 and not k.lower().startswith(('x-amz-', 'x-tos-', 'x-oss-', 'x-goog-'))]
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), url.fragment))
    return value


def canonical_numbers(value):
    if isinstance(value, dict):
        return {k: canonical_numbers(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical_numbers(v) for v in value]
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def fingerprint(node, ignore_runtime=False, normalize_numbers=False, ignore_geometry=False, ignore_registration=False):
    value = normalized(node)
    if normalize_numbers:
        value = canonical_numbers(value)
    if ignore_geometry:
        for key in ('x', 'y', 'width', 'height'):
            value.pop(key, None)
    if ignore_runtime and isinstance(value.get('metadata'), dict):
        for key in ('taskId', 'status', 'taskStatus', 'progress', 'errorMessage', 'errorDetails', 'imageGenerationStage',
                    'canvasPreparation', 'canUseForGeneration', 'updatedAt', 'cliSubmissionId'):
            value['metadata'].pop(key, None)
    if ignore_registration and isinstance(value.get('metadata'), dict):
        metadata = value['metadata']
        # The editor records measured media dimensions and normalizes card size
        # after loading a file. These are not generation size/duration settings.
        for key in ('mediaWidth', 'mediaHeight', 'mediaBaseSizeVersion'):
            metadata.pop(key, None)
        if any(metadata.get(key) for key in ('src', 'resultUrl', 'resultUrls')):
            for key in ('mediaDuration', 'durationSeconds'):
                metadata.pop(key, None)
        # Registration rewrites references in-place during any canvas save.
        # IDs remain significant when they are the only identity of a material.
        for descriptor in [metadata, *(metadata.get('references') or [])]:
            if not isinstance(descriptor, dict):
                continue
            for key in ('canvasPreparation', 'canUseForGeneration'):
                descriptor.pop(key, None)
            if any(descriptor.get(key) for key in ('src', 'url', 'resultUrl', 'resultUrls', 'referenceUrl', 'fileKey', 'storageKey')):
                descriptor.pop('materialId', None)
                if descriptor is not metadata:
                    descriptor.pop('id', None)
                    descriptor.pop('source', None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def derived_reference_target(node, canvas, source_id):
    """Ignore only an editor-derived first-frame reference verified against the live graph.

    Other references, settings and actual input replacements remain conflict-sensitive.
    The selected frame identity is separately checked by resolve before every paid call.
    """
    value = normalized(node)
    if not source_id or not canvas:
        return value
    document = canvas.get('payload') or canvas
    source = next((n for n in document.get('nodes', []) if n.get('id') == source_id and n.get('type') == 'image'), None)
    connected = any(c.get('sourceId') == source_id and c.get('targetId') == node['id'] for c in document.get('connections', []))
    url = (source or {}).get('metadata', {}).get('src')
    metadata = value.get('metadata') or {}
    if not connected or not url or metadata.get('videoMode') not in (None, '', 'keyframe', 'image_to_video'):
        return value
    refs = metadata.get('references')
    if isinstance(refs, list):
        kept = [r for r in refs if not (isinstance(r, dict) and r.get('type') == 'image'
                and r.get('sourceNodeId') == source_id
                and (r.get('referenceUrl') or r.get('url')) == normalized(url))]
        if kept:
            metadata['references'] = kept
        else:
            metadata.pop('references', None)
    return value


def target_node(canvas, node_id, kind=None):
    document = canvas.get('payload') or canvas.get('data') or canvas
    node = next((n for n in document.get('nodes', []) if str(n.get('id')) == node_id), None)
    if node is None:
        raise ValueError('目标节点不存在；请先创建节点')
    if node.get('type') not in {'image', 'video', 'audio', 'text'} or (kind and node.get('type') != kind):
        raise ValueError('目标必须是与生成类型一致的 image/video/audio/text 节点')
    version = canvas.get('version', document.get('version'))
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError('画布响应缺少有效 version，不能安全回填')
    return node, version


def public_task(response):
    if not isinstance(response, dict):
        raise ValueError('任务响应不是 JSON 对象')
    task = response.get('task') or response
    if not isinstance(task, dict):
        raise ValueError('响应中的 task 不是 JSON 对象')
    primary = task.get('result_url') or task.get('resultUrl')
    metadata = task.get('metadata_json') or task.get('metadata') or {}
    urls = task.get('resultUrls') or metadata.get('resultUrls') or metadata.get('result_urls') or []
    # Never bypass the server's result publication/review gate with raw metadata.
    urls = [url for url in urls if isinstance(url, str) and url.strip()] if primary and isinstance(urls, list) else []
    unique = {}
    for url in ([primary] if primary else []) + urls:
        unique.setdefault(normalized(url), url)
    urls = list(unique.values())
    count = task.get('resultCount') or metadata.get('resultCount')
    if primary and isinstance(count, int) and count > len(urls):
        raise ValueError('任务响应只有部分预览图，缺少完整结果列表；未报告回填成功，请查询完整任务详情后接续')
    details = task.get('errorDetails') or task.get('error_details')
    if not isinstance(details, dict) or details.get('schemaVersion') != 1:
        details = None
    else:
        # Copy only the public diagnostic contract, never provider payloads or inputs.
        details = {'schemaVersion': 1, **{k: v[:4000] for k, v in details.items() if isinstance(v, str) and k in (
            'code', 'title', 'stage', 'message', 'taskId', 'providerCode', 'providerMessage', 'requestId', 'diagnosticNote')}}
    return {'taskId': response.get('task_id') or response.get('taskId') or task.get('id'),
            'taskType': task.get('type'), 'taskStatus': str(task.get('status') or response.get('status') or '').lower(),
            'progress': task.get('progress'), 'resultUrl': primary, 'resultUrls': urls,
            'thumbnailUrl': task.get('thumbnail_url') or task.get('thumbnailUrl'),
            'taskError': task.get('error_message') or task.get('errorMessage') or (details or {}).get('message') or response.get('message'),
            'taskErrorDetails': details}


def attached_results(metadata, state):
    if state.get('kind') == 'text':
        return (metadata.get('cliTextRequestId') == state.get('requestId')
                and metadata.get('content') == state.get('resultText')
                and metadata.get('prompt') == state.get('resultText'))
    expected = normalized(state.get('resultUrls') or [state.get('resultUrl')])
    actual = normalized(metadata.get('resultUrls') or [metadata.get('resultUrl')])
    return (metadata.get('taskId') == state.get('taskId') and actual == expected
            and normalized(metadata.get('src')) in expected and normalized(metadata.get('resultUrl')) in expected)


class Runner:
    def __init__(self, call, base, *, clock=time.monotonic, sleep=time.sleep, announce=None):
        self.call, self.base, self.clock, self.sleep = call, base, clock, sleep
        self.announce = announce or (lambda msg: print(msg, file=sys.stderr, flush=True))

    def request(self, operation, arguments, timeout=90, **kwargs):
        return self.call(operation, arguments, base=self.base, request_timeout=timeout, **kwargs)

    def report(self, state, path):
        keys = ('phase', 'generation', 'attachment', 'taskId', 'taskStatus', 'progress', 'projectId', 'nodeId', 'resultUrl', 'resultUrls', 'error', 'taskErrorDetails', 'operationId', 'statusSyncError', 'retryable', 'claimReleaseError')
        result = {k: state.get(k) for k in keys}
        if state.get('kind') == 'text':
            result.update(resultText=state.get('resultText'), completionId=state.get('completionId'))
            result.update(state.get('textResult') or {})
        result.update(queueJobId=state.get('queueJobId'), requestId=state.get('requestId'))
        result.update(runFile=str(path), origin=self.base, resume=['task-resume', '--run-file', str(path)])
        result['exitCode'] = 0 if state['phase'] in {'submitted', 'completed', 'attached'} else (3 if state['phase'] in {'wait_timeout', 'interrupted'} else 4)
        result['nextAction'] = {
            'completed': '已取得结果，无需重新提交。', 'attached': '已回填，无需重新提交。',
            'submitted': '已提交并保存任务记录；使用 task-resume 查询或等待，不要重复提交生成。',
            'attach_failed': '修复回填问题后使用 task-resume；不要再次生成。',
            'generation_failed': '生成已失败，请查看原因后决定是否另开新任务；不要无意义地重复轮询。',
            'submission_unknown': '请先在网站核对任务记录，取得任务 ID 后使用 task-wait；不要重复提交。',
            'preflight_failed': '未提交生成；修正目标后使用新的记录文件启动。',
        }.get(state['phase'], '使用 task-resume 继续已有任务，不要重新提交生成。')
        if not state.get('taskId') and state['generation'] == 'unknown':
            result['nextAction'] = ('已保存原请求编号；使用 task-resume 查询原排队回执，不要重新生成。'
                                    if state.get('requestId') or state.get('queueJobId') else
                                    '尚无任务 ID，请先在网站核对是否已提交；不要自动重新生成。')
        return result

    def persist(self, state, path):
        try:
            save(path, state)
        except OSError as exc:
            self.announce(json.dumps({'taskId': state.get('taskId'), 'runFile': str(path), 'state': state,
                                      'warning': '任务状态写盘失败，请保留这条回执；不要重新提交生成。'}, ensure_ascii=False))
            raise ValueError('任务记录保存失败：' + str(exc)) from exc

    def finish(self, state, path, phase, error=None, retryable=False):
        state.update(phase=phase, error=error, retryable=retryable)
        self.persist(state, path)
        return self.report(state, path)

    @serialized_canvas
    def claim_target(self, state, path):
        """Use the canvas version as a cross-client compare-and-set before billing."""
        claim = state['submissionId']
        for attempt in range(3):
            canvas = self.request('canvas-get', {'project_id': state['projectId']})
            node, version = target_node(canvas, state['nodeId'], state['kind'])
            metadata = dict(node.get('metadata') or {})
            if metadata.get('cliSubmissionId') == claim:
                return
            previous_task = metadata.get('taskId')
            if previous_task:
                previous = public_task(self.request('task-get', {'task_id': previous_task}))
                if str(previous.get('taskId')) != str(previous_task) or previous['taskStatus'] not in SUCCEEDED | FAILED:
                    raise ValueError('目标节点已有未结束任务，请使用 task-wait/task-resume 接续；未重复生成')
            elif metadata.get('cliSubmissionId') or str(metadata.get('taskStatus') or metadata.get('status') or '').lower() in PENDING | {'submitting'}:
                raise ValueError('目标节点已被其他提交占用或提交结果待确认，请核对原任务记录；未重复生成')
            if fingerprint(derived_reference_target(node, canvas, state.get('referenceSourceNodeId')) if state.get('fingerprintVersion') == 6 else node, ignore_runtime=True, normalize_numbers=True,
                           ignore_geometry=state.get('fingerprintVersion') in (4, 5, 6),
                           ignore_registration=state.get('fingerprintVersion') in (5, 6)) != state['targetFingerprint']:
                raise ValueError('提交前节点已修改，未提交生成')
            state['claimPreviousRuntime'] = {key: metadata[key] for key in (
                'taskId', 'status', 'taskStatus', 'progress', 'errorMessage', 'imageGenerationStage', 'cliSubmissionId') if key in metadata}
            self.persist(state, path)
            metadata.pop('taskId', None)
            metadata.update(cliSubmissionId=claim, status='running', taskStatus='submitting', progress=0,
                            errorMessage='', imageGenerationStage='正在提交')
            try:
                self.request('canvas-apply', {'project_id': state['projectId'], 'baseVersion': version,
                    'operationId': 'cli-claim-' + claim, 'actions': [{'type': 'update_node', 'payload': {
                        'nodeId': state['nodeId'], 'changes': {'metadata': metadata}}}]})
                return
            except ERRORS as exc:
                if attempt == 2 or (not isinstance(exc, httpx.HTTPError) and 'Mira HTTP 409 ' not in str(exc)):
                    raise
                # A lost acknowledgement may already have saved our claim.
        raise ValueError('无法确认目标节点占用，未提交生成')

    @serialized_canvas
    def release_unsubmitted_claim(self, state, path):
        """Release only our own claim with durable proof generation never began."""
        if state.get('generation') != 'not_submitted' or not state.get('submissionId'):
            return
        for attempt in range(3):
            try:
                canvas = self.request('canvas-get', {'project_id': state['projectId']}, timeout=15)
                node, version = target_node(canvas, state['nodeId'], state['kind'])
                metadata = dict(node.get('metadata') or {})
                if metadata.get('cliSubmissionId') != state['submissionId']:
                    state['claimReleaseError'] = None
                    return
                if metadata.get('taskId'):
                    raise ValueError('节点已有任务，不能自动释放占用')
                for key in ('taskId', 'status', 'taskStatus', 'progress', 'errorMessage', 'imageGenerationStage', 'cliSubmissionId'):
                    metadata.pop(key, None)
                metadata.update(state.get('claimPreviousRuntime') or {})
                self.request('canvas-apply', {'project_id': state['projectId'], 'baseVersion': version,
                    'operationId': 'cli-release-' + state['submissionId'], 'actions': [{'type': 'update_node', 'payload': {
                        'nodeId': state['nodeId'], 'changes': {'metadata': metadata}}}]}, timeout=15)
                state['claimReleaseError'] = None
                return
            except ERRORS as exc:
                state['claimReleaseError'] = str(exc)
                if not retryable_error(exc) or attempt == 2:
                    return

    def start(self, operation, arguments, *, path=None, project_id=None, node_id=None, timeout=600, poll_interval=5, task_id=None, wait=True, reference_source_id=None):
        path = (Path(path) if path else Path.cwd()/'.mira/runs'/f'{uuid4()}.json').expanduser().resolve()
        with locked(path):
            # Reusing a receipt can never submit a second generation.
            with path.open('x', encoding='utf-8'):
                pass
            state = {'schemaVersion': 1, 'origin': self.base, 'operation': operation, 'projectId': project_id, 'nodeId': node_id,
                     'taskId': task_id, 'phase': 'prepared', 'generation': 'existing' if task_id else 'not_submitted',
                     'attachment': 'pending' if project_id else 'not_requested', 'kind': MANAGED_OPERATIONS.get(operation),
                     'operationId': 'cli-attach-' + str(uuid4()) if project_id else None}
            self.persist(state, path)
            try:
                if project_id:
                    canvas = self.request('canvas-get', {'project_id': project_id})
                    node, _ = target_node(canvas, node_id, state['kind'])
                    state['kind'] = node['type']
                    state['fingerprintVersion'] = 6 if reference_source_id else 5
                    state['referenceSourceNodeId'] = reference_source_id
                    state['originalTaskId'] = (node.get('metadata') or {}).get('taskId')
                    state['targetFingerprint'] = fingerprint(derived_reference_target(node, canvas, reference_source_id), ignore_runtime=True, normalize_numbers=True, ignore_geometry=True, ignore_registration=True)
                    self.persist(state, path)
                if task_id:
                    return self.wait(state, path, timeout, poll_interval)
                if project_id:
                    state.update(submissionId=str(uuid4()), originalTaskId=None)
                    self.persist(state, path)
                    self.claim_target(state, path)
                # Save uncertainty before entering the network: crash/timeout must never resubmit.
                state.update(phase='submitting', generation='unknown', requestId=uuid4().hex)
                self.persist(state, path)
                self.announce(json.dumps({'requestId': state['requestId'], 'runFile': str(path),
                    'message': '请求编号已保存；中断后使用 task-resume 接续，不要重复提交'}, ensure_ascii=False))
                try:
                    response = self.request(operation, arguments, request_id=state['requestId'])
                except ERRORS as exc:
                    if getattr(exc, 'queue_job_id', None):
                        self.accept_queue(state, {'canvasJob': True, 'jobId': exc.queue_job_id})
                        self.persist(state, path)
                        if not wait:
                            return self.finish(state, path, 'submitted')
                        return self.wait_queue(state, path, timeout, poll_interval)
                    if getattr(exc, 'status_code', None) in {400, 401, 403, 404, 405, 413, 415, 422}:
                        state.update(generation='not_submitted', submissionRejected=True,
                                     error=str(exc) + '；请求被拒绝，未自动重新提交。')
                        self.persist(state, path)
                        self.release_unsubmitted_claim(state, path)
                        state.update(generation='failed', taskStatus='failed',
                                     attachment='not_attempted' if project_id else 'not_requested')
                        return self.finish(state, path, 'generation_failed', str(exc) + '；请求被拒绝，未自动重新提交。')
                    if wait and retryable_error(exc):
                        return self.wait_queue(state, path, timeout, poll_interval)
                    return self.finish(state, path, 'submission_unknown', str(exc) + '；未自动重试。请核对服务端任务记录。')
                if response.get('canvasJob') is True:
                    self.accept_queue(state, response)
                    self.persist(state, path)
                    if not wait:
                        return self.finish(state, path, 'submitted')
                    return self.wait_queue(state, path, timeout, poll_interval)
                if state['kind'] == 'text':
                    return self.complete_text(state, path, response)
                details = public_task(response)
                state.update(details)
                if not details['taskId']:
                    return self.finish(state, path, 'submission_unknown', '响应缺少任务 ID；请核对服务端任务记录，不要重复生成。')
                state.update(phase='waiting', generation='pending')
                self.persist(state, path)
                self.announce(json.dumps({'taskId': state['taskId'], 'runFile': str(path), 'message': '任务已提交；中断后使用 task-resume 接续'}, ensure_ascii=False))
                if not wait:
                    self.sync_status(state, path)
                    receipt = self.finish(state, path, 'submitted')
                    return {**response, **receipt}
                return self.wait(state, path, timeout, poll_interval, initial=response)
            except KeyboardInterrupt:
                self.release_unsubmitted_claim(state, path)
                return self.finish(state, path, 'interrupted', '已停止等待；未取消服务端任务。')
            except ERRORS as exc:
                self.release_unsubmitted_claim(state, path)
                phase = ('preflight_failed' if state['generation'] == 'not_submitted' else
                         'submission_unknown' if state['generation'] == 'unknown' and not state.get('taskId') else 'query_failed')
                return self.finish(state, path, phase, str(exc))

    def resume(self, path, *, timeout=600, poll_interval=5):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise ValueError('任务记录不存在')
        with locked(path):
            state = read_json(path)
            if not isinstance(state, dict) or state.get('schemaVersion') != 1:
                raise ValueError('不支持的任务记录格式')
            if state.get('origin') != self.base:
                raise ValueError('MIRA_URL 与任务记录的站点不同，请切回原站点后接续')
            if state.get('submissionRejected'):
                self.release_unsubmitted_claim(state, path)
                state.update(generation='failed', taskStatus='failed',
                             attachment='not_attempted' if state.get('projectId') else 'not_requested')
                return self.finish(state, path, 'generation_failed', state.get('error'))
            if state.get('kind') == 'text' and state.get('generation') == 'succeeded' and isinstance(state.get('resultText'), str):
                return self.attach_text(state, path) if state.get('projectId') else self.finish(state, path, 'completed')
            if not state.get('taskId'):
                if state.get('generation') == 'not_submitted':
                    self.release_unsubmitted_claim(state, path)
                    return self.finish(state, path, 'preflight_failed', '确认未提交生成；已尝试释放本次占用，请核对 claimReleaseError 后使用新记录启动。')
                if state.get('requestId') or state.get('queueJobId'):
                    try:
                        return self.wait_queue(state, path, timeout, poll_interval)
                    except KeyboardInterrupt:
                        return self.finish(state, path, 'interrupted', '已停止等待；使用原记录接续排队任务，不要重新提交。')
                return self.finish(state, path, 'submission_unknown', '记录中没有任务 ID，不能自动接续或重新生成。请在服务端核对后使用 task-wait --task-id。')
            try:
                # Always refresh authoritative status; never reuse a stale result URL.
                return self.wait(state, path, timeout, poll_interval)
            except KeyboardInterrupt:
                return self.finish(state, path, 'interrupted', '已停止等待；未取消服务端任务。')

    def accept_queue(self, state, response):
        ident = response.get('jobId')
        if response.get('canvasJob') is not True or not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise ValueError('排队回执无效；请保留原请求编号核对，不要重新提交')
        if state.get('queueJobId') and state['queueJobId'] != ident:
            raise ValueError('排队回执编号不匹配，已停止接续')
        state.update(queueJobId=ident, generation='pending', phase='waiting', error=None)

    def wait_queue(self, state, path, timeout, poll_interval):
        """Read-only recovery. A missing receipt never authorizes another POST."""
        deadline = self.clock() + timeout
        while True:
            try:
                op, args = ('_queue-get', {'job_id': state['queueJobId']}) if state.get('queueJobId') else ('_queue-find', {'request_id': state['requestId']})
                response = self.request(op, args, timeout=30 if timeout == 0 else min(30, max(1, deadline-self.clock())))
                self.accept_queue(state, response)
                self.persist(state, path)
                status = response.get('status')
                if status == 'done':
                    result = response.get('result')
                    if not isinstance(result, dict):
                        raise ValueError('排队任务已结束但没有有效生成回执，请核查原任务')
                    if state.get('kind') == 'text':
                        return self.complete_text(state, path, result)
                    info = public_task(result)
                    if not info.get('taskId'):
                        raise ValueError('排队任务回执缺少生成任务 ID，请核查原任务')
                    state.update(info)
                    self.persist(state, path)
                    return self.wait(state, path, max(0, deadline-self.clock()), poll_interval)
                if status in {'failed', 'cancelled'}:
                    error = response.get('error') or {}
                    state.update(generation='failed', taskStatus='failed', taskError=error.get('message') or '排队任务失败',
                                 taskErrorDetails=error.get('errorDetails'))
                    self.sync_status(state, path)
                    return self.finish(state, path, 'generation_failed', state['taskError'])
                if status == 'uncertain':
                    return self.finish(state, path, 'submission_unknown', (response.get('error') or {}).get('message') or '原任务结果待核对；保留排队回执接续，不要重新生成')
                if status not in {'accepted', 'pending', 'published', 'running', 'deferred'}:
                    raise ValueError('未知排队状态：' + str(status))
                if state.get('kind') == 'text':
                    state['taskStatus'] = 'pending'
                    self.sync_status(state, path)
            except ERRORS as exc:
                transient = retryable_error(exc) or getattr(exc, 'status_code', None) == 404
                if not transient or self.clock() >= deadline:
                    return self.finish(state, path, 'query_failed', str(exc) + '；仅查询原请求，未重新生成。', retryable=transient)
            remaining = deadline-self.clock()
            if remaining <= 0:
                return self.finish(state, path, 'wait_timeout', '排队仍在进行；请使用原任务记录接续，未重新提交。')
            self.sleep(min(poll_interval, remaining))

    def complete_text(self, state, path, response):
        choices = response.get('choices')
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError('文本任务已结束但未返回有效 choices；请保留原记录核查，不要重复生成')
        message = choices[0].get('message') or {}
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError('文本任务未返回有效正文；请核查原任务，不要重复生成')
        # A chat completion ID is not a task UUID. Never send it to task-get.
        # Keep final prose only; reasoning and provider-internal data are omitted.
        public = {k: response[k] for k in ('id', 'model', 'created', 'usage', 'object') if k in response}
        public['choices'] = [{'index': 0, 'message': {'role': 'assistant', 'content': content},
                              'finish_reason': choices[0].get('finish_reason')}]
        state.update(resultText=content, completionId=response.get('id'), textResult=public,
                     generation='succeeded', taskStatus='completed', progress=100, retryable=False, error=None)
        self.persist(state, path)
        return self.attach_text(state, path) if state.get('projectId') else self.finish(state, path, 'completed')

    @serialized_canvas
    def attach_text(self, state, path):
        state['attachment'] = 'pending'
        for attempt in range(3):
            try:
                canvas = self.request('canvas-get', {'project_id': state['projectId']})
                node, version = target_node(canvas, state['nodeId'], 'text')
                metadata = dict(node.get('metadata') or {})
                if attached_results(metadata, state):
                    state.update(attachment='succeeded', statusSyncError=None)
                    return self.finish(state, path, 'attached')
                self.check_target(state, node, canvas)
                metadata.pop('cliSubmissionId', None)
                metadata.update(content=state['resultText'], prompt=state['resultText'],
                                cliTextRequestId=state['requestId'], status='succeeded', taskStatus='completed',
                                progress=100, errorMessage='', errorDetails=None)
                self.request('canvas-apply', {'project_id': state['projectId'], 'baseVersion': version,
                    'operationId': state['operationId'], 'actions': [{'type': 'update_node', 'payload': {
                        'nodeId': state['nodeId'], 'changes': {'metadata': metadata}}}]})
                verified = self.request('canvas-get', {'project_id': state['projectId']})
                actual, _ = target_node(verified, state['nodeId'], 'text')
                if not attached_results(actual.get('metadata') or {}, state):
                    raise ValueError('文本回填后节点内容不一致；请核对用户修改，不自动覆盖')
                state.update(attachment='succeeded', statusSyncError=None)
                return self.finish(state, path, 'attached')
            except ERRORS as exc:
                if 'Mira HTTP 409 ' in str(exc) and attempt < 2:
                    continue
                state['attachment'] = 'failed'
                return self.finish(state, path, 'attach_failed', str(exc), retryable=retryable_error(exc))

    def wait(self, state, path, timeout, poll_interval, initial=None):
        deadline = self.clock() + timeout
        response = initial
        failures = 0
        while True:
            if response is None:
                try:
                    response = self.request('task-get', {'task_id': state['taskId']}, timeout=90 if timeout == 0 else min(90, max(1, deadline-self.clock())))
                except ERRORS as exc:
                    transient = retryable_error(exc)
                    if transient and self.clock() < deadline:
                        failures += 1
                        self.finish(state, path, 'query_failed', str(exc), retryable=True)
                        self.sleep(min(poll_interval * 2**min(failures-1, 4), 30, deadline-self.clock()))
                        continue
                    return self.finish(state, path, 'query_failed', str(exc), retryable=transient)
            try:
                info = public_task(response)
            except ERRORS as exc:
                return self.finish(state, path, 'query_failed', str(exc))
            if info['taskId'] and str(info['taskId']) != str(state['taskId']):
                return self.finish(state, path, 'query_failed', '任务响应 ID 不匹配，已停止')
            info.pop('taskId')
            state.update(info)
            state.update(retryable=False, error=None)
            failures = 0
            status = state['taskStatus']
            if status in FAILED:
                state.update(generation='failed', attachment='not_attempted' if state.get('projectId') else 'not_requested')
                self.sync_status(state, path)
                return self.finish(state, path, 'generation_failed', state.get('taskError') or status)
            if status in SUCCEEDED:
                state['generation'] = 'succeeded'
                if state.get('resultUrl'):
                    if state.get('projectId'):
                        attached = self.attach(state, path)
                        if attached.get('retryable') and self.clock() < deadline:
                            self.sleep(min(poll_interval, deadline-self.clock()))
                            response = None
                            continue
                        return attached
                    return self.finish(state, path, 'completed')
                state['phase'] = 'waiting_result'
            elif status in PENDING or not status:
                state.update(generation='pending', phase='waiting')
            else:
                return self.finish(state, path, 'query_failed', '未知任务状态：' + status)
            self.sync_status(state, path)
            self.persist(state, path)
            remaining = deadline-self.clock()
            if remaining <= 0:
                return self.finish(state, path, 'wait_timeout', '等待时间已到；任务未取消。成功但无公开结果地址时也不会回填。')
            self.sleep(min(poll_interval, remaining))
            response = None

    def check_target(self, state, node, canvas=None):
        if state.get('fingerprintVersion') == 6:
            node = derived_reference_target(node, canvas, state.get('referenceSourceNodeId'))
        modern = state.get('fingerprintVersion') in (2, 3, 4, 5, 6)
        claim = (node.get('metadata') or {}).get('cliSubmissionId')
        if state.get('submissionId') and claim not in (None, state['submissionId']):
            raise ValueError('目标节点已被另一个提交占用，已停止同步')
        if modern and (node.get('metadata') or {}).get('taskId') not in {state.get('taskId'), state.get('originalTaskId')}:
            raise ValueError('目标节点已关联其他任务，已停止同步，避免覆盖新的生成。')
        if fingerprint(node, ignore_runtime=modern, normalize_numbers=state.get('fingerprintVersion') in (3, 4, 5, 6),
                       ignore_geometry=state.get('fingerprintVersion') in (4, 5, 6),
                       ignore_registration=state.get('fingerprintVersion') in (5, 6)) != state.get('targetFingerprint'):
            if state.get('kind') == 'text':
                raise ValueError('等待期间文本节点已修改，已停止回填；生成正文保留在 resultText，请核对后使用 canvas-apply，不要重复生成。')
            raise ValueError('等待期间目标节点已修改，已停止回填。确认目标后可用 task-wait 显式指定节点重新挂接已有任务。')
        if state.get('fingerprintVersion') not in (3, 4, 5, 6):
            state.update(fingerprintVersion=3, originalTaskId=(node.get('metadata') or {}).get('taskId'),
                         targetFingerprint=fingerprint(node, ignore_runtime=True, normalize_numbers=True))

    @serialized_canvas
    def sync_status(self, state, path):
        if not state.get('projectId') or (not state.get('taskId') and not state.get('queueJobId')):
            return
        failed = state.get('taskStatus') in FAILED
        queued = state.get('taskStatus') in {'pending', 'queued', 'queuing', 'submitted', 'accepted', ''}
        raw_progress = state.get('progress')
        progress = max(0, min(99, int(raw_progress))) if isinstance(raw_progress, (int, float)) and math.isfinite(raw_progress) else 0
        changes = {'taskId': state['taskId'], 'status': 'failed' if failed else 'running',
                   'taskStatus': state.get('taskStatus') if failed else ('pending' if queued else 'processing'),
                   'progress': progress, 'errorMessage': (state.get('taskError') or state.get('taskStatus')) if failed else '',
                   'errorDetails': state.get('taskErrorDetails') if failed else None,
                   'imageGenerationStage': ('生成失败' if failed else '排队中' if queued else
                                            '等待公开结果' if state.get('generation') == 'succeeded' else '生成中')}
        if changes == state.get('lastPublishedStatus'):
            return
        for attempt in range(3):
            try:
                canvas = self.request('canvas-get', {'project_id': state['projectId']})
                node, version = target_node(canvas, state['nodeId'], state['kind'])
                metadata = dict(node.get('metadata') or {})
                if metadata.get('taskId') == state['taskId'] and metadata.get('status') == 'succeeded':
                    return  # Browser reconciliation may already have published this result.
                self.check_target(state, node, canvas)
                if metadata.get('taskId') == state['taskId'] and not failed:
                    changes['progress'] = max(changes['progress'], min(99, int(metadata.get('progress') or 0)))
                digest = hashlib.sha256((str(state['operationId']) + json.dumps(changes, sort_keys=True)).encode()).hexdigest()[:48]
                if any(metadata.get(k) != v for k, v in changes.items()):
                    metadata.update(changes)
                    if failed and metadata.get('cliSubmissionId') == state.get('submissionId'):
                        metadata.pop('cliSubmissionId', None)
                    self.request('canvas-apply', {'project_id': state['projectId'], 'baseVersion': version,
                        'operationId': 'cli-progress-' + digest, 'actions': [{'type': 'update_node', 'payload': {
                            'nodeId': state['nodeId'], 'changes': {'metadata': metadata}}}]})
                state.update(lastPublishedStatus=changes, statusSyncError=None)
                self.persist(state, path)
                return
            except ERRORS as exc:
                if 'Mira HTTP 409 ' in str(exc) and attempt < 2:
                    continue
                if state.get('statusSyncError') != str(exc):
                    self.announce('节点状态同步暂未成功；任务查询仍会继续：' + str(exc))
                state['statusSyncError'] = str(exc)
                self.persist(state, path)
                return

    @serialized_canvas
    def attach(self, state, path):
        state['attachment'] = 'pending'
        kind = (state.get('taskType') or '').split('_')[0]
        if kind and kind != state.get('kind'):
            state['attachment'] = 'failed'
            return self.finish(state, path, 'attach_failed', '任务媒体类型与目标节点不一致')
        # Stable for retries of this receipt; a new explicit attachment gets a new ID.
        if not state.get('operationId'):
            state['operationId'] = 'cli-attach-' + str(uuid4())
        self.persist(state, path)
        for attempt in range(3):
            try:
                canvas = self.request('canvas-get', {'project_id': state['projectId']})
                node, version = target_node(canvas, state['nodeId'], state['kind'])
                metadata = dict(node.get('metadata') or {})
                if attached_results(metadata, state):
                    state.update(attachment='succeeded', statusSyncError=None)
                    return self.finish(state, path, 'attached')
                urls = state.get('resultUrls') or [state['resultUrl']]
                expected = normalized(urls)
                own_partial = (metadata.get('taskId') == state['taskId']
                    and normalized(metadata.get('resultUrl')) in expected and normalized(metadata.get('src')) in expected
                    and all(normalized(url) in expected for url in metadata.get('resultUrls') or []))
                if not own_partial:
                    self.check_target(state, node, canvas)
                else:
                    # Old clients may already have committed the original operationId
                    # with only one image. A repair needs a new, persisted idempotency key.
                    result_set = hashlib.sha256(json.dumps(expected, ensure_ascii=False).encode()).hexdigest()
                    if state.get('attachmentResultsFingerprint') != result_set:
                        state['operationId'] = 'cli-attach-' + hashlib.sha256((state['operationId'] + result_set).encode()).hexdigest()[:48]
                        state['attachmentResultsFingerprint'] = result_set
                        self.persist(state, path)
                selected = metadata.get('src') if own_partial else state['resultUrl']
                selected_index = expected.index(normalized(selected))
                # These identify the old output, not input references. The server
                # registers and checks the new media; never copy old approval flags.
                for key in ('materialId', 'fileKey', 'storageKey', 'assetId', 'assetUri', 'assetStatus',
                            'volcAsset', 'canvasPreparation', 'canUseForGeneration', 'referenceUrl', 'url',
                            'fileName', 'mimeType', 'format'):
                    metadata.pop(key, None)
                metadata.pop('cliSubmissionId', None)
                metadata.update(taskId=state['taskId'], resultUrl=urls[selected_index], src=urls[selected_index],
                                resultUrls=urls, activeResultIndex=selected_index, status='succeeded', taskStatus='completed', progress=100,
                                errorMessage='', errorDetails=None, imageGenerationStage='已完成')
                if state.get('thumbnailUrl'):
                    metadata['thumbnailUrl'] = state['thumbnailUrl']
                else:
                    metadata.pop('thumbnailUrl', None)
                metadata.pop('generatedContentId', None)
                self.request('canvas-apply', {'project_id': state['projectId'], 'baseVersion': version,
                    'operationId': state['operationId'], 'actions': [{'type': 'update_node', 'payload': {
                        'nodeId': state['nodeId'], 'changes': {'metadata': metadata}}}]})
                verified = self.request('canvas-get', {'project_id': state['projectId']})
                actual, _ = target_node(verified, state['nodeId'], state['kind'])
                if not attached_results(actual.get('metadata') or {}, state):
                    raise ValueError('回填操作已处理，但当前节点结果不一致；可能已被撤销或修改，请核对后显式挂接，不自动覆盖。')
                state.update(attachment='succeeded', statusSyncError=None)
                return self.finish(state, path, 'attached')
            except ERRORS as exc:
                if 'Mira HTTP 409 ' in str(exc) and attempt < 2:
                    continue
                state['attachment'] = 'failed'
                return self.finish(state, path, 'attach_failed', str(exc), retryable=retryable_error(exc))
