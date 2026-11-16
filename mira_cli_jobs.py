"""Resumable CLI waits. Only start() can submit a paid operation, exactly once."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx

MEDIA_OPERATIONS = {'image-generate': 'image', 'video-generate': 'video', 'audio-generate': 'audio'}
SUCCEEDED = {'completed', 'succeeded', 'success'}
FAILED = {'failed', 'cancelled', 'canceled', 'error'}
PENDING = {'pending', 'queued', 'processing', 'running', 'waiting', 'submitted', 'accepted', 'in_progress'}
ERRORS = (OSError, ValueError, KeyError, httpx.HTTPError)


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
def locked(path):
    """A separate persistent lock file avoids inode replacement races."""
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.with_suffix(path.suffix + '.lock').open('a+b')
    acquired = False
    try:
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise ValueError('此任务记录正在被另一个 CLI 使用，请等待该进程结束') from None
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
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
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


def fingerprint(node):
    return hashlib.sha256(json.dumps(normalized(node), sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def target_node(canvas, node_id, kind=None):
    document = canvas.get('payload') or canvas.get('data') or canvas
    node = next((n for n in document.get('nodes', []) if str(n.get('id')) == node_id), None)
    if node is None:
        raise ValueError('目标节点不存在；请先创建节点')
    if node.get('type') not in {'image', 'video', 'audio'} or (kind and node.get('type') != kind):
        raise ValueError('目标必须是与生成类型一致的 image/video/audio 节点')
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
    return {'taskId': response.get('task_id') or response.get('taskId') or task.get('id'),
            'taskType': task.get('type'), 'taskStatus': str(task.get('status') or response.get('status') or '').lower(),
            'progress': task.get('progress'), 'resultUrl': task.get('result_url') or task.get('resultUrl'),
            'thumbnailUrl': task.get('thumbnail_url') or task.get('thumbnailUrl'),
            'taskError': task.get('error_message') or task.get('errorMessage') or response.get('message')}


class Runner:
    def __init__(self, call, base, *, clock=time.monotonic, sleep=time.sleep, announce=None):
        self.call, self.base, self.clock, self.sleep = call, base, clock, sleep
        self.announce = announce or (lambda msg: print(msg, file=sys.stderr, flush=True))

    def request(self, operation, arguments, timeout=90):
        return self.call(operation, arguments, base=self.base, request_timeout=timeout)

    def report(self, state, path):
        keys = ('phase', 'generation', 'attachment', 'taskId', 'taskStatus', 'progress', 'projectId', 'nodeId', 'resultUrl', 'error', 'operationId')
        result = {k: state.get(k) for k in keys}
        result.update(runFile=str(path), origin=self.base, resume=['task-resume', '--run-file', str(path)])
        result['exitCode'] = 0 if state['phase'] in {'completed', 'attached'} else (3 if state['phase'] in {'wait_timeout', 'interrupted'} else 4)
        result['nextAction'] = {
            'completed': '已取得结果，无需重新提交。', 'attached': '已回填，无需重新提交。',
            'attach_failed': '修复回填问题后使用 task-resume；不要再次生成。',
            'generation_failed': '生成已失败，请查看原因后决定是否另开新任务；不要无意义地重复轮询。',
            'submission_unknown': '请先在网站核对任务记录，取得任务 ID 后使用 task-wait；不要重复提交。',
            'preflight_failed': '未提交生成；修正目标后使用新的记录文件启动。',
        }.get(state['phase'], '使用 task-resume 继续已有任务，不要重新提交生成。')
        if not state.get('taskId') and state['generation'] == 'unknown':
            result['nextAction'] = '尚无任务 ID，请先在网站核对是否已提交；不要自动重新生成。'
        return result

    def persist(self, state, path):
        try:
            save(path, state)
        except OSError as exc:
            self.announce(json.dumps({'taskId': state.get('taskId'), 'runFile': str(path), 'state': state,
                                      'warning': '任务状态写盘失败，请保留这条回执；不要重新提交生成。'}, ensure_ascii=False))
            raise ValueError('任务记录保存失败：' + str(exc)) from exc

    def finish(self, state, path, phase, error=None):
        state.update(phase=phase, error=error)
        self.persist(state, path)
        return self.report(state, path)

    def start(self, operation, arguments, *, path=None, project_id=None, node_id=None, timeout=600, poll_interval=5, task_id=None):
        path = (Path(path) if path else Path.cwd()/'.mira/runs'/f'{uuid4()}.json').expanduser().resolve()
        with locked(path):
            # Reusing a receipt can never submit a second generation.
            with path.open('x', encoding='utf-8'):
                pass
            state = {'schemaVersion': 1, 'origin': self.base, 'operation': operation, 'projectId': project_id, 'nodeId': node_id,
                     'taskId': task_id, 'phase': 'prepared', 'generation': 'existing' if task_id else 'not_submitted',
                     'attachment': 'pending' if project_id else 'not_requested', 'kind': MEDIA_OPERATIONS.get(operation),
                     'operationId': 'cli-attach-' + str(uuid4()) if project_id else None}
            self.persist(state, path)
            try:
                if project_id:
                    canvas = self.request('canvas-get', {'project_id': project_id})
                    node, _ = target_node(canvas, node_id, state['kind'])
                    state['kind'] = node['type']
                    state['targetFingerprint'] = fingerprint(node)
                    self.persist(state, path)
                if task_id:
                    return self.wait(state, path, timeout, poll_interval)
                # Save uncertainty before entering the network: crash/timeout must never resubmit.
                state.update(phase='submitting', generation='unknown')
                self.persist(state, path)
                try:
                    response = self.request(operation, arguments)
                except ERRORS as exc:
                    return self.finish(state, path, 'submission_unknown', str(exc) + '；未自动重试。请核对服务端任务记录。')
                details = public_task(response)
                state.update(details)
                if not details['taskId']:
                    return self.finish(state, path, 'submission_unknown', '响应缺少任务 ID；请核对服务端任务记录，不要重复生成。')
                state.update(phase='waiting', generation='pending')
                self.persist(state, path)
                self.announce(json.dumps({'taskId': state['taskId'], 'runFile': str(path), 'message': '任务已提交；中断后使用 task-resume 接续'}, ensure_ascii=False))
                return self.wait(state, path, timeout, poll_interval, initial=response)
            except KeyboardInterrupt:
                return self.finish(state, path, 'interrupted', '已停止等待；未取消服务端任务。')
            except ERRORS as exc:
                phase = ('preflight_failed' if state['generation'] == 'not_submitted' else
                         'submission_unknown' if state['generation'] == 'unknown' and not state.get('taskId') else 'query_failed')
                return self.finish(state, path, phase, str(exc))

    def resume(self, path, *, timeout=600, poll_interval=5):
        path = Path(path).expanduser().resolve()
        if not path.is_file():
            raise ValueError('任务记录不存在')
        with locked(path):
            state = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(state, dict) or state.get('schemaVersion') != 1:
                raise ValueError('不支持的任务记录格式')
            if state.get('origin') != self.base:
                raise ValueError('MIRA_URL 与任务记录的站点不同，请切回原站点后接续')
            if not state.get('taskId'):
                return self.finish(state, path, 'submission_unknown', '记录中没有任务 ID，不能自动接续或重新生成。请在服务端核对后使用 task-wait --task-id。')
            try:
                # Always refresh authoritative status; never reuse a stale result URL.
                return self.wait(state, path, timeout, poll_interval)
            except KeyboardInterrupt:
                return self.finish(state, path, 'interrupted', '已停止等待；未取消服务端任务。')

    def wait(self, state, path, timeout, poll_interval, initial=None):
        deadline = self.clock() + timeout
        response = initial
        while True:
            if response is None:
                try:
                    response = self.request('task-get', {'task_id': state['taskId']}, timeout=min(90, max(1, deadline-self.clock())))
                except ERRORS as exc:
                    return self.finish(state, path, 'query_failed', str(exc))
            try:
                info = public_task(response)
            except ERRORS as exc:
                return self.finish(state, path, 'query_failed', str(exc))
            if info['taskId'] and str(info['taskId']) != str(state['taskId']):
                return self.finish(state, path, 'query_failed', '任务响应 ID 不匹配，已停止')
            info.pop('taskId')
            state.update(info)
            status = state['taskStatus']
            if status in FAILED:
                state.update(generation='failed', attachment='not_attempted' if state.get('projectId') else 'not_requested')
                return self.finish(state, path, 'generation_failed', state.get('taskError') or status)
            if status in SUCCEEDED:
                state['generation'] = 'succeeded'
                if state.get('resultUrl'):
                    if state.get('projectId'):
                        return self.attach(state, path)
                    return self.finish(state, path, 'completed')
                state['phase'] = 'waiting_result'
            elif status in PENDING or not status:
                state.update(generation='pending', phase='waiting')
            else:
                return self.finish(state, path, 'query_failed', '未知任务状态：' + status)
            self.persist(state, path)
            remaining = deadline-self.clock()
            if remaining <= 0:
                return self.finish(state, path, 'wait_timeout', '等待时间已到；任务未取消。成功但无公开结果地址时也不会回填。')
            self.sleep(min(poll_interval, remaining))
            response = None

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
                if (metadata.get('taskId') == state['taskId'] and normalized(metadata.get('resultUrl')) == normalized(state['resultUrl'])
                        and normalized(metadata.get('src')) == normalized(state['resultUrl'])):
                    state['attachment'] = 'succeeded'
                    return self.finish(state, path, 'attached')
                if fingerprint(node) != state.get('targetFingerprint'):
                    raise ValueError('等待期间目标节点已修改，已停止回填。确认目标后可用 task-wait 显式指定节点重新挂接已有任务。')
                # These identify the old output, not input references. The server
                # registers and checks the new media; never copy old approval flags.
                for key in ('materialId', 'fileKey', 'storageKey', 'assetId', 'assetUri', 'assetStatus',
                            'volcAsset', 'canvasPreparation', 'canUseForGeneration', 'referenceUrl', 'url',
                            'fileName', 'mimeType', 'format'):
                    metadata.pop(key, None)
                metadata.update(taskId=state['taskId'], resultUrl=state['resultUrl'], src=state['resultUrl'],
                                resultUrls=[state['resultUrl']], activeResultIndex=0, status='succeeded', taskStatus='completed', progress=100, errorMessage='')
                if state.get('thumbnailUrl'):
                    metadata['thumbnailUrl'] = state['thumbnailUrl']
                else:
                    metadata.pop('thumbnailUrl', None)
                metadata.pop('generatedContentId', None)
                self.request('canvas-apply', {'project_id': state['projectId'], 'baseVersion': version,
                    'operationId': state['operationId'], 'actions': [{'type': 'update_node', 'payload': {
                        'nodeId': state['nodeId'], 'changes': {'metadata': metadata}}}]})
                state['attachment'] = 'succeeded'
                return self.finish(state, path, 'attached')
            except ERRORS as exc:
                if 'Mira HTTP 409 ' in str(exc) and attempt < 2:
                    continue
                state['attachment'] = 'failed'
                return self.finish(state, path, 'attach_failed', str(exc))
