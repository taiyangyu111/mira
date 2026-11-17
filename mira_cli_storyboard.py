"""CLI-only storyboard planning. Creates visible nodes, never generation tasks."""
from copy import deepcopy
import json
import math
from pathlib import Path
from uuid import UUID, uuid4

TEMPLATES = {
    'storyboard': ('text', 'image', 'video'),
    'image-video': ('image', 'video'),
    'image-board': ('text', 'image'),
}
RATIOS = ('16:9', '9:16', '1:1')
PROPERTIES = {
    'aspectRatio': {'enum': list(RATIOS), 'default': '16:9'},
    'duration': {'type': 'integer', 'minimum': 4, 'maximum': 30, 'default': 10},
    'imageModel': {'type': 'string', 'minLength': 1, 'maxLength': 120},
    'videoModel': {'type': 'string', 'minLength': 1, 'maxLength': 120},
}
SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['shots'],
    'properties': {
        'template': {'enum': list(TEMPLATES), 'default': 'storyboard'},
        'defaults': {'type': 'object', 'additionalProperties': False, 'properties': PROPERTIES},
        'shots': {'type': 'array', 'minItems': 1, 'maxItems': 10, 'items': {
            'type': 'object', 'additionalProperties': False, 'required': ['title', 'imagePrompt'],
            'properties': {
                'title': {'type': 'string', 'minLength': 1, 'maxLength': 60},
                'imagePrompt': {'type': 'string', 'minLength': 1, 'maxLength': 10000},
                'videoPrompt': {'type': 'string', 'minLength': 1, 'maxLength': 10000},
                'description': {'type': 'string', 'maxLength': 4000},
                **PROPERTIES,
            },
        }},
    },
}
EXAMPLE = {
    'template': 'storyboard', 'defaults': {'aspectRatio': '16:9', 'duration': 10},
    'shots': [
        {'title': '草原晨光', 'description': '开场，明亮通透、真实自然',
         'imagePrompt': '清晨阳光照亮草原，露水闪烁，远处森林和山峦，自然纪录片摄影',
         'videoPrompt': '镜头缓慢向前移动，微风吹动草叶，保持原图构图和自然光线'},
        {'title': '林间溪流', 'imagePrompt': '明亮森林中的清澈溪流，阳光穿过树冠，真实自然摄影',
         'videoPrompt': '固定机位，溪水缓慢流过石头，树叶轻轻摇动'},
    ],
}


def validate(spec):
    from jsonschema import Draft202012Validator
    errors = []
    for e in Draft202012Validator(SCHEMA).iter_errors(spec):
        errors.append({'field': '.'.join(map(str, e.absolute_path)) or '$', 'message': e.message})
    if isinstance(spec, dict):
        objects = [('defaults', spec.get('defaults', {}))]
        if isinstance(spec.get('shots'), list):
            objects += [(f'shots.{i}', s) for i, s in enumerate(spec['shots'])]
        for path, obj in objects:
            if isinstance(obj, dict):
                for key in ('title', 'imagePrompt', 'videoPrompt', 'imageModel', 'videoModel'):
                    if isinstance(obj.get(key), str) and not obj[key].strip():
                        errors.append({'field': path + '.' + key, 'message': '不能为空或全为空格'})
    return {'valid': not errors, 'submitted': False, 'errors': errors}


def checked_spec(spec):
    report = validate(spec)
    if not report['valid']:
        raise ValueError('分镜参数错误：' + '; '.join(e['field'] + ': ' + e['message'] for e in report['errors']))
    return deepcopy(spec)


def media_size(ratio):
    # Match the frontend's 350px short edge, including portrait nodes.
    a, b = map(int, ratio.split(':'))
    return (round(350 * a / b), 350) if a >= b else (350, round(350 * b / a))


def document(project):
    payload = project.get('payload') or project.get('data') or project
    version = project.get('version', payload.get('version'))
    if type(version) is not int or version < 0:
        raise ValueError('画布响应缺少有效 version，未提交')
    return payload, version


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('画布位置和尺寸必须是有限数字')
    return value


def bounds(node):
    width, height = number(node.get('width', 640)), number(node.get('height', 350))
    if node.get('type') in ('image', 'video'):
        metadata = node.get('metadata') or {}
        ratio = metadata.get(node['type'] + 'AspectRatio', '16:9')
        if ratio in RATIOS:
            mw, mh = media_size(ratio)
            width, height = max(width, mw), max(height, mh)
    if width <= 0 or height <= 0:
        raise ValueError('画布节点尺寸必须大于零')
    return number(node.get('x', 0)), number(node.get('y', 0)), width, height


def compile_plan(spec, project_id, version, base, x, y, operation_id=None):
    spec = checked_spec(spec)
    project_id = str(UUID(project_id))
    x, y = number(x), number(y)
    if type(version) is not int or version < 0:
        raise ValueError('baseVersion 必须是非负整数')
    operation_id = operation_id or str(uuid4())
    prefix = 'sb-' + UUID(operation_id).hex
    kinds = TEMPLATES[spec.get('template', 'storyboard')]
    shots = [{'aspectRatio': '16:9', 'duration': 10, **spec.get('defaults', {}), **s} for s in spec['shots']]
    media_width = max(media_size(s['aspectRatio'])[0] for s in shots)
    columns = {}; left = x
    for kind in kinds:
        columns[kind] = left
        left += (360 if kind == 'text' else media_width) + 220
    actions, rows = [], []
    top = y
    for index, shot in enumerate(shots, 1):
        mw, mh = media_size(shot['aspectRatio'])
        row_height = max(350, mh)
        ids = {}
        for kind in kinds:
            node_id = f'{prefix}-{index:02d}-{kind}'
            ids[kind] = node_id
            width, height = (360, 350) if kind == 'text' else (mw, mh)
            metadata = {'title': f'{index:02d} {shot["title"]} | ' + {'text': '说明', 'image': '构图', 'video': '视频'}[kind], 'status': 'idle'}
            if kind == 'text':
                metadata.update(textManualEntry=True, content=(shot.get('description') or shot['imagePrompt']))
                if 'video' in kinds:
                    metadata['content'] += f'\n镜头时长：{shot["duration"]} 秒；画幅：{shot["aspectRatio"]}'
            elif kind == 'image':
                metadata.update(prompt=shot['imagePrompt'], imageAspectRatio=shot['aspectRatio'], numImages=1, imageCount=1)
                if 'imageModel' in shot:
                    metadata['imageModel'] = shot['imageModel']
            else:
                metadata.update(prompt=shot.get('videoPrompt', shot['imagePrompt']), videoAspectRatio=shot['aspectRatio'],
                                videoDuration=shot['duration'], videoMode='keyframe', videoResolution='720p',
                                videoFps=24, videoAutoDuration=False, videoGenAudio=False, generateAudio=False)
                if 'videoModel' in shot:
                    metadata['videoModel'] = shot['videoModel']
            node = {'id': node_id, 'type': kind, 'x': columns[kind], 'y': top + (row_height - height) / 2,
                    'width': width, 'height': height, 'metadata': metadata}
            actions.append({'type': 'add_node', 'payload': {'node': node}})
        for source, target in zip(kinds, kinds[1:]):
            actions.append({'type': 'add_connection', 'payload': {'connection': {
                'id': f'{prefix}-{index:02d}-{source}-{target}', 'sourceId': ids[source], 'targetId': ids[target],
            }}})
        rows.append({'shot': index, 'title': shot['title'], 'duration': shot['duration'] if 'video' in kinds else None,
                     'nodes': ids, 'y': top, 'height': row_height})
        top += row_height + 200
    return {
        'format': 'mira-storyboard-v1', 'origin': base, 'spec': spec, 'placement': {'x': x, 'y': y},
        'request': {'project_id': project_id, 'baseVersion': version, 'operationId': operation_id, 'actions': actions},
        'summary': {'projectId': project_id, 'baseVersion': version, 'operationId': operation_id,
                    'template': spec.get('template', 'storyboard'), 'shotCount': len(shots),
                    'nodeCount': len(shots) * len(kinds), 'connectionCount': len(shots) * (len(kinds) - 1),
                    'totalDuration': sum(s['duration'] for s in shots) if 'video' in kinds else None,
                    'columns': columns, 'rows': rows},
        'warnings': ['仅创建节点与连线，不执行生成。模型专属限制需在生成前核对。',
                     '未指定模型时沿用前端默认选择；videoPrompt 未填写时使用 imagePrompt。',
                     '计划绑定站点和画布版本；画布变更导致 409 时重新规划，不覆盖现有内容。'],
    }


def plan(spec, project_id, base, call, x=None, y=None):
    checked_spec(spec)
    project_id = str(UUID(project_id))
    if (x is None) != (y is None):
        raise ValueError('--x 与 --y 必须同时提供')
    payload, version = document(call('canvas-get', {'project_id': project_id}))
    existing = [bounds(n) for n in payload.get('nodes', [])]
    if x is None:
        x, y = 80, max((ny + h + 240 for _, ny, _, h in existing), default=120)
    result = compile_plan(spec, project_id, version, base, x, y)
    for action in result['request']['actions']:
        if action['type'] != 'add_node':
            continue
        nx, ny, w, h = bounds(action['payload']['node'])
        if any(nx < ex + ew + 40 and nx + w + 40 > ex and ny < ey + eh + 40 and ny + h + 40 > ey
               for ex, ey, ew, eh in existing):
            raise ValueError('指定位置与现有节点重叠，请调整 --x/--y 或使用默认自动放置')
    return result


def apply(plan_data, base, call):
    if not isinstance(plan_data, dict) or plan_data.get('format') != 'mira-storyboard-v1':
        raise ValueError('请使用 storyboard-plan 生成的计划文件')
    if plan_data.get('origin') != base:
        raise ValueError('计划绑定的站点与当前 MIRA_URL 不一致，未提交')
    try:
        request = plan_data['request']
        placement = plan_data['placement']
        if not isinstance(request, dict) or not isinstance(placement, dict):
            raise ValueError('request/placement 必须是对象')
        if not isinstance(request.get('project_id'), str) or not isinstance(request.get('operationId'), str):
            raise ValueError('缺少画布 ID 或 operationId')
        expected = compile_plan(plan_data['spec'], request['project_id'], request['baseVersion'], base,
                                placement['x'], placement['y'], request['operationId'])
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('计划文件不完整，请用 storyboard-plan 重新生成') from exc
    if expected['request'] != request:
        raise ValueError('计划 actions 被改动，请修改原始分镜输入后重新规划；仅允许模板中的新增节点和连线')
    # Read before every write. Keep the original version/operationId: the server
    # deduplicates completed operations BEFORE checking version, so a lost response
    # can be retried safely, even if the user has subsequently edited the canvas.
    _, current = document(call('canvas-get', {'project_id': request['project_id']}))
    try:
        response = call('canvas-apply', request)
    except ValueError as exc:
        if 'HTTP 409' in str(exc):
            raise ValueError(f'画布版本已变化（计划 {request["baseVersion"]}，读取到 {current}），未覆盖画布。请重新生成布局计划。') from exc
        raise
    return {**expected['summary'], 'version': response.get('version'), 'applied': True,
            'idempotent': bool(response.get('idempotent')), 'generationSubmitted': False}


def run(options, call, origin):
    if options.operation == 'storyboard-apply':
        data = json.loads(options.file.read_text(encoding='utf-8-sig'))
        return apply(data, origin(), call)
    if options.schema:
        return SCHEMA
    if options.example:
        return EXAMPLE
    if not options.file and not options.json:
        raise ValueError('请提供 --file 或 --json；用 --example 查看输入示例')
    spec = json.loads(options.file.read_text(encoding='utf-8-sig') if options.file else options.json)
    if options.validate_only:
        return validate(spec)
    if not options.project_id or not options.out:
        raise ValueError('预览需要 --project-id 和 --out；不会提交画布修改')
    checked_spec(spec)
    destination = options.out.expanduser()
    # Reserve before network access. Never overwrite a plan used for a retry.
    created = False
    try:
        with destination.open('x', encoding='utf-8') as stream:
            created = True
            result = plan(spec, options.project_id, origin(), call, options.x, options.y)
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {**result['summary'], 'planFile': str(destination.resolve()), 'applied': False,
            'generationSubmitted': False, 'warnings': result['warnings']}
