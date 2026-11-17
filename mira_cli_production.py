"""Reviewable, quoted, resumable image-to-video plans. CLI only."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
from uuid import UUID, uuid4

if __package__:
    from . import mira_cli_jobs as jobs, mira_cli_storyboard as layout
    from . import mira_cli_capabilities as caps, mira_cli_support as support, mira_cli_batch as batch
else:
    import mira_cli_jobs as jobs, mira_cli_storyboard as layout
    import mira_cli_capabilities as caps, mira_cli_support as support, mira_cli_batch as batch

FORMAT = 'mira-production-v1'
KINDS = {'images': 'image', 'videos': 'video'}
DEFAULT_SIZES = {'16:9': '2560x1440', '9:16': '1440x2560', '1:1': '2048x2048'}
STOP_PHASES = {'generation_failed', 'attach_failed', 'submission_unknown', 'preflight_failed', 'query_failed'}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(jobs.normalized(value), sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError('积分必须是非负有限数值')
        return number
    except InvalidOperation as exc:
        raise ValueError('无法识别积分金额') from exc


def run_path(path, plan, shot, kind):
    return path.parent / '.mira' / 'productions' / plan['id'] / f'{shot["id"]}-{kind}.json'


def load(path, base):
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('format') != FORMAT or data.get('origin') != base:
        raise ValueError('执行计划格式或站点不匹配')
    UUID(data['id']); UUID(data['projectId'])
    shots = data.get('shots')
    if not isinstance(shots, list) or not 1 <= len(shots) <= 10:
        raise ValueError('执行计划需要 1–10 个镜头')
    nodes = []
    for i, shot in enumerate(shots, 1):
        if shot.get('id') != f'{i:02d}' or not isinstance(shot.get('image'), dict):
            raise ValueError('镜头 ID 或图片步骤无效，请重新创建计划')
        for kind in ('image', 'video'):
            step = shot.get(kind)
            if step is None:
                continue
            if not isinstance(step, dict) or not isinstance(step.get('arguments'), dict) or not isinstance(step.get('nodeId'), str) or not step['nodeId']:
                raise ValueError('步骤参数或目标节点无效')
            nodes.append(step['nodeId'])
            allowed = {'model', 'prompt', 'size', 'numImages'} if kind == 'image' else {
                'model', 'prompt', 'mode', 'duration', 'resolution', 'aspectRatio', 'fps', 'generateAudio', 'autoDuration', 'outputFormat'}
            if set(step['arguments']) - allowed:
                raise ValueError('执行计划含不支持的参数，请使用单任务命令处理扩展参数')
            if kind == 'image' and step['arguments'].get('numImages') != 1:
                raise ValueError('本版每镜仅生成一张构图图')
            if kind == 'video' and (step['arguments'].get('mode') != 'image_to_video' or step['arguments'].get('autoDuration')):
                raise ValueError('本版仅支持固定时长的首帧图生视频')
    if len(nodes) != len(set(nodes)):
        raise ValueError('一个执行计划不能重复绑定同一个节点')
    return data


def node_from(canvas, node_id, kind):
    return jobs.target_node(canvas, node_id, kind)[0]


def availability(node):
    m = node.get('metadata') or {}
    task = m.get('taskId')
    status = str(m.get('taskStatus') or m.get('status') or '').lower()
    if (m.get('src') or m.get('resultUrl')) and (not task or status in jobs.SUCCEEDED):
        return 'existing_media'
    if task:
        return 'existing_task'
    if status in jobs.PENDING:
        raise ValueError(f'节点 {node["id"]} 正在处理但缺少任务 ID，不能重复提交')
    return 'new'


def source_signature(node):
    m = node.get('metadata') or {}
    return digest({'id': node['id'], 'type': node['type'], 'taskId': m.get('taskId'),
                   'url': m.get('src') or m.get('resultUrl')})


def source_url(node):
    if availability(node) != 'existing_media':
        raise ValueError('首帧图片还未成功生成或导入')
    url = (node.get('metadata') or {}).get('src') or (node.get('metadata') or {}).get('resultUrl')
    if not isinstance(url, str) or not url.startswith(('https://', 'http://', 'asset://')):
        raise ValueError('首帧需要服务端可访问的素材地址，不能使用 blob 或临时浏览器地址')
    return url


def numeric_setting(value):
    if isinstance(value, str):
        try:
            number = float(value)
            return int(number) if number.is_integer() else number
        except ValueError:
            return value
    return value


def create(layout_file, out, base, call, image_model=None, video_model=None, image_size=None):
    source = json.loads(Path(layout_file).read_text(encoding='utf-8-sig'))
    if source.get('format') != 'mira-storyboard-v1' or source.get('origin') != base:
        raise ValueError('需要当前站点的 storyboard-plan 文件')
    request = source['request']; place = source['placement']
    canonical = layout.compile_plan(source['spec'], request['project_id'], request['baseVersion'], base,
                                    place['x'], place['y'], request['operationId'])
    if canonical['request'] != request:
        raise ValueError('布局计划被改动，请重新规划')
    canvas = call('canvas-get', {'project_id': request['project_id']})
    result = {'format': FORMAT, 'id': str(uuid4()), 'origin': base, 'projectId': request['project_id'],
              'layoutOperationId': request['operationId'], 'createdAt': now(), 'shots': [], 'quote': None}
    for row in canonical['summary']['rows']:
        shot = {'id': f'{row["shot"]:02d}', 'title': row['title'], 'selection': None}
        for kind in ('image', 'video'):
            if kind not in row['nodes']:
                continue
            node = node_from(canvas, row['nodes'][kind], kind)
            m = node.get('metadata') or {}
            model = m.get(kind + 'Model') or (image_model if kind == 'image' else video_model)
            if not model:
                raise ValueError(f'镜头 {shot["id"]} 缺少 {kind} 模型；请配置节点或提供 --{kind}-model')
            if kind == 'image':
                if numeric_setting(m.get('numImages', m.get('imageCount', 1))) != 1:
                    raise ValueError('本版每镜一张构图图，请将图片节点数量设为1')
                ratio = m.get('imageAspectRatio') or '16:9'
                size = image_size
                if not size and m.get('imageResolutionMode') == 'custom' and m.get('imageCustomWidth') and m.get('imageCustomHeight'):
                    size = f'{m["imageCustomWidth"]}x{m["imageCustomHeight"]}'
                size = size or DEFAULT_SIZES.get(ratio)
                if not size:
                    raise ValueError('无法确定图片尺寸，请提供 --image-size')
                args = {'model': model, 'prompt': m.get('prompt', ''), 'size': size, 'numImages': 1}
            else:
                if m.get('videoMode') not in (None, '', 'keyframe', 'image_to_video') or m.get('videoAutoDuration'):
                    raise ValueError('执行计划仅支持首帧模式、固定时长；请先调整视频节点')
                args = {'model': model, 'prompt': m.get('prompt', ''), 'mode': 'image_to_video',
                        'duration': numeric_setting(m.get('videoDuration', 10)), 'resolution': m.get('videoResolution', '720p'),
                        'aspectRatio': m.get('videoAspectRatio', '16:9'), 'fps': numeric_setting(m.get('videoFps', 24)),
                        'generateAudio': m.get('videoGenAudio', m.get('generateAudio', False)),
                        'autoDuration': False, 'outputFormat': 'mp4'}
            shot[kind] = {'nodeId': node['id'], 'arguments': args, 'fingerprintVersion': 3,
                          'targetFingerprint': jobs.fingerprint(node, ignore_runtime=True, normalize_numbers=True),
                          'initialState': availability(node), 'existingTaskId': m.get('taskId')}
        result['shots'].append(shot)
    # Creation never changes a canvas or submits a task.
    with Path(out).expanduser().open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return {'planFile': str(Path(out).expanduser().resolve()), 'projectId': result['projectId'],
            'shotCount': len(result['shots']), 'generationSubmitted': False,
            'next': 'production-preflight', 'notes': ['请检查计划中的尺寸、模型、提示词与目标节点；默认图片尺寸为按画幅计算的 2K 档位。']}


def step_state(path, plan, shot, kind):
    step = shot[kind]; receipt = run_path(path, plan, shot, kind)
    if receipt.exists():
        state = batch.read_receipt(receipt, plan['origin'])
        if state.get('projectId') != plan['projectId'] or state.get('nodeId') != step['nodeId']:
            raise ValueError('任务记录的回填目标与执行计划不一致')
        return state.get('phase', 'needs_attention'), state
    return step['initialState'], None


def resolve(shot, kind, canvas, preview=False):
    arguments = deepcopy(shot[kind]['arguments'])
    if kind == 'video':
        selected = shot.get('selection')
        if not selected:
            if not preview:
                raise ValueError(f'镜头 {shot["id"]} 尚未选定图片，请先 production-select')
            # Used ONLY for local schema checks; never sent to generation or quotes.
            arguments['firstFrameImage'] = 'mira-planned-reference://awaiting-selection'
        else:
            node = node_from(canvas, selected['nodeId'], 'image')
            if source_signature(node) != selected['signature']:
                raise ValueError(f'镜头 {shot["id"]} 的首帧已变化，请重新审图选择')
            arguments['firstFrameImage'] = source_url(node)  # refresh expiring URL
    return arguments


def quotation(kind, arguments):
    if kind == 'image':
        w, h = map(int, arguments['size'].lower().split('x'))
        return {'type': 'image', 'model': arguments['model'], 'prompt': arguments['prompt'],
                'width': w, 'height': h, 'imageResolutionMode': 'custom', 'numImages': 1, 'taskCount': 1}
    return {'type': 'video', **{k: arguments[k] for k in ('model', 'prompt', 'duration', 'resolution', 'aspectRatio', 'fps', 'generateAudio', 'autoDuration', 'mode')},
            'taskCount': 1, 'imageReferenceCount': 1}


def analyze(path, plan, call):
    canvas = call('canvas-get', {'project_id': plan['projectId']})
    config = None
    rows = []
    def read(op, args):
        nonlocal config
        if op == 'model-list':
            if config is None:
                config = call(op, args)
            return config
        raise ValueError('能力检查不应调用其他接口')
    for shot in plan['shots']:
        for kind in ('image', 'video'):
            if kind not in shot:
                continue
            step = shot[kind]
            row = {'shot': shot['id'], 'title': shot['title'], 'kind': kind, 'nodeId': step['nodeId'], 'errors': [],
                   'estimatedPointsExact': None, 'unit': None}
            rows.append(row)
            try:
                phase, state = step_state(path, plan, shot, kind)
                row['state'] = phase
                if state is not None or phase != 'new':
                    row['taskId'] = (state or {}).get('taskId') or step.get('existingTaskId')
                    if phase in STOP_PHASES or phase == 'existing_task' or (state and not state.get('taskId')):
                        row['errors'].append('已有任务需要处理，请接续或核对，不会重新生成')
                    continue
                node = node_from(canvas, step['nodeId'], kind)
                if availability(node) != 'new' or jobs.fingerprint(node, ignore_runtime=True, normalize_numbers=step.get('fingerprintVersion') == 3) != step['targetFingerprint']:
                    raise ValueError('目标节点已变化，请重新创建执行计划，已有任务不会重生成')
                args = resolve(shot, kind, canvas, preview=True)
                row['requestDigest'] = digest(args)
                report = caps.check(kind, args, read)
                row['check'] = report
                if not report['valid'] or not report['modelChecked']:
                    row['errors'] += report['errors'] or ['当前服务端没有可用模型规则，不能批量提交']
                    continue
                qargs = quotation(kind, args)
                qr = support.validate_arguments('generation-quote', qargs)
                if not qr['valid']:
                    row['errors'] += qr['errors']; continue
                quoted = call('generation-quote', qargs)
                if quoted.get('estimateAvailable') is not True:
                    raise ValueError('报价不可用，不能作为免费或零积分处理')
                row['estimatedPointsExact'] = str(money(quoted.get('estimatedPointsExact', quoted.get('estimatedPoints'))))
                row['unit'] = quoted.get('estimatedUnit') or 'points'
                row['capabilityRevision'] = report.get('capabilityRevision')
                row['state'] = 'waiting_for_selection' if kind == 'video' and not shot.get('selection') else 'ready'
            except jobs.ERRORS as exc:
                row['errors'].append(str(exc))
    return rows


def quote_summary(rows):
    units = {r['unit'] for r in rows if r.get('unit')}
    errors = sum(bool(r['errors']) for r in rows)
    if len(units) > 1:
        errors += 1
    known = {kind: str(sum((money(r['estimatedPointsExact']) for r in rows
                            if r['kind'] == kind and r.get('estimatedPointsExact') is not None), Decimal(0))) for kind in ('image', 'video')}
    totals = {kind: None if any(r['errors'] for r in rows if r['kind'] == kind) or len(units) > 1 else known[kind] for kind in known}
    return {'valid': errors == 0, 'errorCount': errors, 'unit': next(iter(units), None) if len(units) <= 1 else 'mixed',
            'stageEstimatedPointsExact': totals, 'knownSubtotalExact': str(sum(map(money, known.values()))) if len(units) <= 1 else None,
            'totalEstimatedPointsExact': str(sum(map(money, known.values()))) if not errors else None,
            'notes': ['仅报价尚未开始的步骤；视频首帧未选择时为暂估，选择后须重新预检。',
                      '报价不是实际结算的硬上限；图片模型部分专属限制仍未知，请查看 check.notChecked。']}


def public_rows(rows):
    return [{k: r[k] for k in ('shot', 'title', 'kind', 'nodeId', 'state', 'taskId', 'estimatedPointsExact', 'unit', 'errors') if k in r} for r in rows]


def preflight(path, plan, call):
    rows = analyze(path, plan, call)
    quote = {'id': str(uuid4()), 'at': now(), 'rows': rows, **quote_summary(rows)}
    plan['quote'] = quote
    jobs.save(path, plan)
    return {'quoteId': quote['id'], 'quotedAt': quote['at'], **quote_summary(rows), 'steps': public_rows(rows),
            'generationSubmitted': False, 'planFile': str(path)}


def select(path, plan, call, shot_id=None, all_shots=False, source_node=None):
    chosen = plan['shots'] if all_shots else [s for s in plan['shots'] if s['id'] == shot_id]
    if not chosen:
        raise ValueError('镜头不存在，请使用两位镜头 ID，例如 01')
    if source_node and all_shots:
        raise ValueError('--source-node 只用于单个镜头')
    canvas = call('canvas-get', {'project_id': plan['projectId']})
    selections = []
    for shot in chosen:
        if 'video' not in shot:
            raise ValueError('该模板没有视频步骤，无需选首帧')
        phase, record = step_state(path, plan, shot, 'video')
        if record is not None or phase != 'new':
            raise ValueError('视频步骤已有任务或素材，不能改变其输入；需要新版本时新建节点')
        node = node_from(canvas, source_node or shot['image']['nodeId'], 'image')
        selections.append((shot, {'nodeId': node['id'], 'url': source_url(node), 'signature': source_signature(node), 'at': now()}))
    for shot, selection in selections:
        shot['selection'] = selection
    plan['quote'] = None
    jobs.save(path, plan)
    return {'selectedShots': [s['id'] for s, _ in selections], 'generationSubmitted': False,
            'next': 'production-preflight；首帧变更后需要重新报价'}


def stage_shots(plan, stage):
    kind = KINDS[stage]
    return kind, [s for s in plan['shots'] if kind in s]


def status(path, plan):
    rows = []
    for shot in plan['shots']:
        for kind in ('image', 'video'):
            if kind not in shot:
                continue
            try:
                phase, state = step_state(path, plan, shot, kind)
                if kind == 'video' and phase == 'new' and not shot.get('selection'):
                    phase = 'waiting_for_selection'
                rows.append({'shot': shot['id'], 'title': shot['title'], 'kind': kind, 'nodeId': shot[kind]['nodeId'],
                             'state': phase, 'taskId': (state or {}).get('taskId') or shot[kind].get('existingTaskId'),
                             'runFile': str(run_path(path, plan, shot, kind)) if state else None, 'error': (state or {}).get('error')})
            except jobs.ERRORS as exc:
                rows.append({'shot': shot['id'], 'kind': kind, 'state': 'needs_attention', 'error': str(exc)})
    return {'planFile': str(path), 'projectId': plan['projectId'], 'steps': rows,
            'quoteId': (plan.get('quote') or {}).get('id'), 'generationSubmitted': False}


def run(path, plan, base, call, stage, quote_id, max_points, confirm_spend=False):
    if not confirm_spend:
        raise ValueError('批量生成可能消耗积分，请先审阅报价，再使用 --confirm-spend 和 --max-points')
    limit = money(max_points)
    approved = plan.get('quote') or {}
    if approved.get('id') != quote_id or not approved.get('valid'):
        raise ValueError('报价 ID 无效或整批预检未通过，请先 production-preflight')
    if (datetime.now(timezone.utc) - datetime.fromisoformat(approved['at'])).total_seconds() > 900:
        raise ValueError('报价超过15分钟，请重新预检报价')
    kind, shots = stage_shots(plan, stage)
    if not shots:
        raise ValueError('计划没有该阶段')
    # Recheck ALL steps before the first new paid submission, using current rules.
    current = analyze(path, plan, call)
    if not quote_summary(current)['valid']:
        return {'submittedCount': 0, 'blocked': True, 'exitCode': 4, 'steps': public_rows(current), 'generationSubmitted': False}
    pending = [r for r in current if r['kind'] == kind and r['state'] in ('ready', 'waiting_for_selection')]
    if any(r['state'] == 'waiting_for_selection' for r in pending):
        raise ValueError('视频阶段尚有未审图选择的镜头；不会提交任何新任务')
    for row in pending:
        previous = next((r for r in approved['rows'] if r['shot'] == row['shot'] and r['kind'] == kind), None)
        if not previous or any(row.get(k) != previous.get(k) for k in ('requestDigest', 'estimatedPointsExact', 'unit', 'capabilityRevision')):
            raise ValueError('请求参数、模型规则或报价已变化，请重新预检并审阅报价')
    total = sum((money(r['estimatedPointsExact']) for r in pending), Decimal(0))
    if total > limit:
        raise ValueError(f'本阶段待提交报价合计 {total} 超过 --max-points {limit}，未生成')
    count = 0; stopped = None
    for row in pending:
        shot = next(s for s in shots if s['id'] == row['shot'])
        receipt = run_path(path, plan, shot, kind)
        try:
            # Check again immediately before each paid call, after earlier tasks may have completed.
            if receipt.exists():
                raise ValueError('该镜头已有任务记录，停止新提交，请接续原任务')
            canvas = call('canvas-get', {'project_id': plan['projectId']})
            node = node_from(canvas, shot[kind]['nodeId'], kind)
            if availability(node) != 'new' or jobs.fingerprint(node, ignore_runtime=True, normalize_numbers=shot[kind].get('fingerprintVersion') == 3) != shot[kind]['targetFingerprint']:
                raise ValueError('目标节点已变化，停止剩余提交')
            arguments = resolve(shot, kind, canvas)
            if digest(arguments) != row['requestDigest']:
                raise ValueError('首帧或参数已变化，停止剩余提交')
            quote = call('generation-quote', quotation(kind, arguments))
            if (quote.get('estimateAvailable') is not True or (quote.get('estimatedUnit') or 'points') != row['unit']
                    or money(quote.get('estimatedPointsExact', quote.get('estimatedPoints'))) != money(row['estimatedPointsExact'])):
                raise ValueError('即时价格发生变化，停止剩余提交，请重新报价')

            def guarded(operation, args, **kwargs):
                # Runner persists uncertainty and obtains a fresh node snapshot
                # before this paid call. Reject user edits in that final read too.
                response = call(operation, args, **kwargs)
                if operation == 'canvas-get' and not paid[0]:
                    target = node_from(response, shot[kind]['nodeId'], kind)
                    if jobs.fingerprint(target, ignore_runtime=True, normalize_numbers=shot[kind].get('fingerprintVersion') == 3) != shot[kind]['targetFingerprint']:
                        raise ValueError('提交前目标节点发生变化，未生成')
                    if digest(resolve(shot, kind, response)) != row['requestDigest']:
                        raise ValueError('提交前首帧发生变化，未生成')
                return response

            paid = [False]
            def tracked(operation, args, **kwargs):
                if operation == kind + '-generate':
                    paid[0] = True
                elif operation not in ('canvas-get', 'canvas-apply', 'task-get'):
                    raise ValueError('执行计划禁止调用其他操作')
                return guarded(operation, args, **kwargs)

            result = jobs.Runner(tracked, base).start(kind + '-generate', arguments, path=receipt,
                project_id=plan['projectId'], node_id=shot[kind]['nodeId'], timeout=0)
            count += int(paid[0])
            if result['phase'] in STOP_PHASES or not result.get('taskId'):
                stopped = result.get('error') or result['phase']; break
        except jobs.ERRORS as exc:
            stopped = str(exc); break
    result = status(path, plan)
    result.update(submittedCount=count, generationSubmitted=bool(count), stoppedReason=stopped,
                  exitCode=4 if stopped else 0, quotedRemainingPointsExact=str(total), next='production-resume')
    return result


def resume(path, plan, base, call, stage, timeout=0, workers=3):
    if not math.isfinite(timeout) or timeout < 0 or type(workers) is not int or not 1 <= workers <= 8:
        raise ValueError('timeout 必须非负有限，workers 必须是1–8的整数')
    kind, shots = stage_shots(plan, stage)
    paths = []; errors = []
    for shot in shots:
        receipt = run_path(path, plan, shot, kind)
        try:
            phase, record = step_state(path, plan, shot, kind)
            if record is None and phase == 'existing_task':
                canvas = call('canvas-get', {'project_id': plan['projectId']})
                node = node_from(canvas, shot[kind]['nodeId'], kind)
                if (node.get('metadata') or {}).get('taskId') != shot[kind]['existingTaskId']:
                    raise ValueError('节点任务 ID 已变化，请重新创建计划')
                jobs.Runner(call, base).start('task-wait', {}, path=receipt, project_id=plan['projectId'],
                    node_id=shot[kind]['nodeId'], task_id=shot[kind]['existingTaskId'], timeout=0)
            if receipt.exists():
                paths.append(receipt)
        except jobs.ERRORS as exc:
            errors.append({'shot': shot['id'], 'error': str(exc)})
    result = None
    if paths:
        manifest = path.parent / '.mira' / 'productions' / plan['id'] / f'{kind}-batch.json'
        jobs.save(manifest, {'format': 'mira-task-batch-v1', 'origin': base, 'name': f'production-{kind}', 'runFiles': list(map(str, paths))})
        result = batch.resume(manifest, base, call, timeout=timeout, workers=workers)
    report = status(path, plan)
    report.update(resumeErrors=errors, exitCode=4 if errors else (result or {}).get('exitCode', 0),
                  batchCounts=(result or {}).get('counts', {}))
    return report


def command(options, call, origin):
    base = origin()
    if options.operation == 'production-create':
        return create(options.layout, options.out, base, call, options.image_model, options.video_model, options.image_size)
    path = options.file.expanduser().resolve()
    if not path.is_file():
        raise ValueError('执行计划文件不存在')
    with jobs.locked(path):
        plan = load(path, base)
        if options.operation == 'production-status':
            return status(path, plan)
        if options.operation == 'production-preflight':
            return preflight(path, plan, call)
        if options.operation == 'production-select':
            return select(path, plan, call, options.shot, options.all, options.source_node)
        if options.operation == 'production-run':
            return run(path, plan, base, call, options.stage, options.quote_id, options.max_points, options.confirm_spend)
        return resume(path, plan, base, call, options.stage, options.timeout, options.workers)
