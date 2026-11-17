"""Group and resume existing task receipts. No generation submission path."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import time

if __package__:
    from . import mira_cli_jobs as jobs
else:
    import mira_cli_jobs as jobs

TERMINAL = {'attached', 'completed', 'generation_failed', 'submission_unknown', 'preflight_failed'}
PENDING = {'waiting', 'waiting_result', 'wait_timeout', 'interrupted', 'prepared'}


def read_receipt(path, base):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('schemaVersion') != 1:
        raise ValueError('不支持的任务记录格式')
    if data.get('origin') != base:
        raise ValueError('任务记录与当前站点不同')
    return data


def load(path, base):
    path = Path(path).expanduser().resolve()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('format') != 'mira-task-batch-v1' or data.get('origin') != base:
        raise ValueError('批次格式或站点不匹配')
    files = data.get('runFiles')
    if not isinstance(files, list) or not 1 <= len(files) <= 100 or not all(isinstance(p, str) and p.strip() for p in files):
        raise ValueError('批次需要 1–100 个任务记录文件')
    resolved = [(path.parent / p).resolve() for p in files]
    if len(set(resolved)) != len(resolved):
        raise ValueError('批次包含重复记录文件')
    return data, resolved


def create(paths, out, base, name=None):
    resolved = [Path(p).expanduser().resolve() for p in paths]
    if not 1 <= len(resolved) <= 100 or len(set(resolved)) != len(resolved):
        raise ValueError('请提供 1–100 个不重复的任务记录文件')
    for path in resolved:
        read_receipt(path, base)
    destination = Path(out).expanduser().resolve()
    manifest = {'format': 'mira-task-batch-v1', 'origin': base, 'name': name or destination.stem,
                'runFiles': [str(p) for p in resolved]}
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return {'batchFile': str(destination), 'taskCount': len(resolved), 'submitted': False}


def row(path, state):
    phase = state.get('phase')
    generation = state.get('generation')
    if generation == 'failed' or phase == 'generation_failed':
        status = 'generation_failed'
    elif phase == 'attach_failed' or state.get('attachment') == 'failed':
        status = 'attach_failed'
    elif phase in ('completed', 'attached'):
        status = 'completed'
    elif generation == 'succeeded' and state.get('resultUrl'):
        status = 'ready_to_attach' if state.get('projectId') else 'completed'
    elif not state.get('taskId') or phase in ('submission_unknown', 'preflight_failed'):
        status = 'needs_attention'
    elif phase == 'query_failed':
        status = 'query_failed'
    else:
        status = 'running'
    return {'runFile': str(path), 'status': status, **{k: state.get(k) for k in (
        'taskId', 'phase', 'generation', 'attachment', 'taskStatus', 'progress', 'projectId', 'nodeId', 'resultUrl', 'error')}}


def error_row(path, exc):
    return {'runFile': str(path), 'status': 'needs_attention', 'error': str(exc)}


def summarize(data, paths, rows, *, refreshed=False):
    counts = dict(Counter(r['status'] for r in rows))
    pending = sum(counts.get(k, 0) for k in ('running', 'ready_to_attach'))
    errors = sum(counts.get(k, 0) for k in ('generation_failed', 'attach_failed', 'needs_attention', 'query_failed'))
    return {'name': data.get('name'), 'origin': data['origin'], 'taskCount': len(paths), 'counts': counts,
            'tasks': rows, 'refreshed': refreshed, 'generationSubmitted': False,
            'exitCode': 4 if errors else 3 if pending else 0}


def status(path, base, call, refresh=False):
    data, paths = load(path, base)
    rows = []
    canvases = {}
    for receipt in paths:
        state = None
        try:
            state = read_receipt(receipt, base)
            if refresh and state.get('taskId'):
                info = jobs.public_task(call('task-get', {'task_id': state['taskId']}))
                if info.get('taskId') and str(info['taskId']) != str(state['taskId']):
                    raise ValueError('任务响应 ID 不匹配')
                info.pop('taskId', None)
                state.update(info)
                if info['taskStatus'] in jobs.FAILED:
                    state.update(generation='failed', phase='generation_failed', error=info.get('taskError'))
                elif info['taskStatus'] in jobs.SUCCEEDED:
                    state['generation'] = 'succeeded'
                    # A refreshed task status cannot certify that a node is attached.
                    if state.get('phase') not in ('attached', 'attach_failed'):
                        state.update(phase='waiting_result' if not info.get('resultUrl') else 'ready_to_attach', error=None)
                    if state.get('projectId') and state.get('nodeId') and info.get('resultUrl'):
                        project = state['projectId']
                        if project not in canvases:
                            canvases[project] = call('canvas-get', {'project_id': project})
                        try:
                            node, _ = jobs.target_node(canvases[project], state['nodeId'], state.get('kind'))
                            metadata = node.get('metadata') or {}
                            matches = metadata.get('taskId') == state['taskId'] and all(
                                jobs.normalized(metadata.get(key)) == jobs.normalized(info['resultUrl'])
                                for key in ('src', 'resultUrl'))
                            if matches:
                                state.update(phase='attached', attachment='succeeded', error=None)
                            elif state.get('phase') == 'attached':
                                state.update(phase='attach_failed', attachment='failed', error='画布当前结果已与任务记录不同；只查询未覆盖，请先核对用户修改')
                        except ValueError as exc:
                            state.update(phase='attach_failed', attachment='failed', error=str(exc))
                elif info['taskStatus'] in jobs.PENDING:
                    state.update(generation='pending', phase='waiting', error=None)
                else:
                    raise ValueError('未知任务状态：' + info['taskStatus'])
            rows.append(row(receipt, state))
        except jobs.ERRORS as exc:
            rows.append({**row(receipt, state), 'status': 'query_failed', 'error': str(exc)} if state else error_row(receipt, exc))
    return summarize(data, paths, rows, refreshed=refresh)


def resume(path, base, call, *, timeout=0, poll_interval=5, workers=3, attachments_only=False,
           clock=time.monotonic, sleep=time.sleep):
    if not math.isfinite(timeout) or timeout < 0 or not math.isfinite(poll_interval) or not 1 <= poll_interval <= 60:
        raise ValueError('timeout 必须非负，poll-interval 必须在 1–60 秒之间')
    if type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('workers 必须是 1–8 的整数')
    data, paths = load(path, base)
    deadline = clock() + timeout

    def advance(receipt):
        try:
            state = read_receipt(receipt, base)
            should_skip = state.get('phase') in TERMINAL or not state.get('taskId')
            if attachments_only and not (state.get('generation') == 'succeeded' and state.get('attachment') in ('pending', 'failed')):
                should_skip = True
            if should_skip or (timeout > 0 and clock() >= deadline):
                return row(receipt, state)

            def bounded_call(operation, arguments, **kwargs):
                if operation not in {'task-get', 'canvas-get', 'canvas-apply'}:
                    raise ValueError('批次接续只能读取任务或回填节点')
                if timeout > 0:
                    remaining = deadline - clock()
                    if remaining <= 0:
                        raise ValueError('批次等待时间已到，请继续使用原批次文件接续')
                    kwargs['request_timeout'] = min(kwargs.get('request_timeout', 90), max(0.1, remaining))
                return call(operation, arguments, **kwargs)

            runner = jobs.Runner(bounded_call, base, announce=lambda msg: None)
            result = runner.resume(receipt, timeout=0, poll_interval=poll_interval)
            return row(receipt, result)
        except jobs.ERRORS as exc:
            return error_row(receipt, exc)

    while True:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(advance, paths))
        result = summarize(data, paths, rows, refreshed=True)
        result['attachmentsOnly'] = attachments_only
        if timeout == 0 or attachments_only or clock() >= deadline or not any(r['status'] == 'running' for r in rows):
            return result
        sleep(min(poll_interval, max(0, deadline - clock())))
