"""CLI-only documentation, validation and output views; no generation requests."""
from functools import lru_cache
import json
import math
import mimetypes
from pathlib import Path
import re


@lru_cache(maxsize=1)
def contracts():
    return json.loads(Path(__file__).with_name('mira_contracts.json').read_text(encoding='utf-8'))['commands']


def command_help(operation):
    entry = contracts()[operation]
    schema = entry['schema']
    lines = ['JSON 参数（--json 或 --file）：']
    for name, field in schema['properties'].items():
        kind = field.get('type') or '/'.join(x.get('type', 'object') for x in field.get('anyOf', [])) or 'enum'
        constraints = []
        for key, label in [('minimum', '最小'), ('maximum', '最大'), ('minLength', '最短'), ('maxLength', '最长'), ('minItems', '最少项数'), ('maxItems', '最多项数'), ('enum', '可选值'), ('default', '默认')]:
            if key in field:
                constraints.append(f'{label}={json.dumps(field[key], ensure_ascii=False)}')
        required = '必填' if name in schema.get('required', []) else '可选'
        aliases = [k for k, v in entry.get('aliases', {}).items() if v == name]
        lines.append(f"  {name} [{required}, {kind}] {field.get('description', '')} {'; '.join(constraints)}".rstrip())
        if aliases:
            lines.append('    兼容字段名：' + ', '.join(aliases))
    if not schema['properties']:
        lines.append('  无 JSON 参数。')
    lines += ['', '示例参数（也可用 --example 输出并保存）：', json.dumps(entry['example'], ensure_ascii=False, indent=2), '', entry.get('notes', ''),
              '用 --schema 查看完整嵌套字段；--validate-only 仅本地检查，不联网、不提交。',
              '本地规则不代表模型实时能力；审核、余额、模型限制以服务端为准。']
    return '\n'.join(lines)


def _nonfinite(value):
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_nonfinite(v) for v in value.values())
    if isinstance(value, list):
        return any(_nonfinite(v) for v in value)
    return False


def validate_arguments(operation, arguments):
    from jsonschema import Draft202012Validator, FormatChecker
    entry = contracts()[operation]
    errors = []
    warnings = []
    def error(field, message):
        item = {'field': field, 'message': message}
        if item not in errors:
            errors.append(item)
    if not isinstance(arguments, dict):
        return {'valid': False, 'errors': [{'field': '$', 'message': '参数必须是 JSON 对象'}], 'warnings': []}
    if _nonfinite(arguments):
        error('$', '不允许 NaN 或 Infinity，请使用有限数值')
    values = dict(arguments)
    if operation == 'canvas-create' and (not isinstance(values.get('title'), str) or not values['title'].strip()):
        error('title', '创建画布必须填写名称，请根据创作内容填写非空 title')
    for alias, target in entry.get('aliases', {}).items():
        if alias in values:
            if target in values and values[target] != values[alias]:
                error(alias, f'与 {target} 重复且值不同，请只保留一种字段名')
            else:
                values[target] = values[alias]
            del values[alias]
    validator = Draft202012Validator(entry['schema'], format_checker=FormatChecker())
    for e in validator.iter_errors(values):
        path = '.'.join(map(str, e.absolute_path)) or '$'
        messages = {'type': f'类型应为 {e.validator_value}', 'minLength': f'长度不能少于 {e.validator_value}',
                    'maxLength': f'长度不能超过 {e.validator_value}', 'minimum': f'不能小于 {e.validator_value}',
                    'maximum': f'不能大于 {e.validator_value}', 'exclusiveMinimum': f'必须大于 {e.validator_value}',
                    'exclusiveMaximum': f'必须小于 {e.validator_value}', 'minItems': f'至少需要 {e.validator_value} 项',
                    'maxItems': f'最多允许 {e.validator_value} 项', 'enum': '必须使用列出的可选值：' + json.dumps(e.validator_value, ensure_ascii=False),
                    'format': f'格式应为 {e.validator_value}', 'anyOf': '类型或格式不符合要求，请查看 --schema'}
        if e.validator == 'required':
            for name in e.validator_value:
                if name not in e.instance:
                    error(name if path == '$' else path + '.' + name, '必填字段缺失')
        elif e.validator == 'additionalProperties':
            for name in e.instance:
                if name not in e.schema.get('properties', {}):
                    error(name if path == '$' else path + '.' + name, '未知字段，请查看 --help 或 --schema；确认服务端支持新字段时可用 --skip-validation')
        else:
            error(path, messages.get(e.validator, '不符合参数规则，请查看 --schema'))
    for key in ('project_id', 'asset_id', 'task_id', 'material_id'):
        value = values.get(key)
        if isinstance(value, str) and (value in {'.', '..'} or '/' in value or '\\' in value):
            error(key, '资源 ID 不能包含路径')
    if operation == 'image-generate':
        if isinstance(values.get('prompt'), str) and not values['prompt'].strip():
            error('prompt', '提示词不能为空或全为空格')
        size = values.get('size')
        if isinstance(size, str) and size:
            if not re.fullmatch(r'\d+[xX]\d+', size):
                error('size', '请使用 1024x1024 这样的尺寸格式')
            elif not all(64 <= int(x) <= 12288 for x in size.lower().split('x')):
                error('size', '宽和高必须在 64 到 12288 像素之间；所选模型可能有更严格限制')
        if ('width' in values or 'height' in values) and 'size' not in values:
            warnings.append('服务端默认 size=1024x1024，会覆盖 width/height；请显式填写 size，或用 size=null 保留宽高。')
        if 'n' in values and 'numImages' in values and values['n'] != values['numImages']:
            error('n', '与 numImages 不一致，请只保留一个数量字段')
    if operation == 'video-generate':
        refs = any(values.get(k) for k in ('firstFrameImage', 'lastFrameImage', 'referenceImages', 'referenceVideos', 'referenceAudios', 'imageUrl'))
        if not refs and not str(values.get('prompt') or '').strip():
            error('prompt', '没有参考素材时必须填写提示词')
        # Mirror the server's mode rules, including its H3 provider exceptions.
        model = str(values.get('model') or '').strip().lower().replace('_', '-')
        h3_models = {'runninghub-minimax-h3', 'minimax-h3', 'hailuo-h3', 'minimax-h3-multimodal',
                     'runninghub-minimax-h3-turbo', 'minimax-h3-turbo', 'hailuo-h3-turbo', 'minimax-h3-turbo-ai-app'}
        if model not in h3_models:
            mode = values.get('mode', 'text_to_video')
            if mode in ('image_to_video', 'first_last_frame') and not values.get('firstFrameImage'):
                error('firstFrameImage', f'mode={mode} 时必须提供首帧图片地址')
            if mode == 'first_last_frame' and not values.get('lastFrameImage'):
                error('lastFrameImage', '首尾帧模式必须提供尾帧图片地址')
            reference_fields = ('referenceImages', 'referenceVideos', 'referenceAudios')
            if mode == 'multimodal':
                for field in ('firstFrameImage', 'lastFrameImage'):
                    if values.get(field):
                        error(field, '多模态模式不能混用首尾帧，请使用 referenceImages/referenceVideos/referenceAudios')
                if not any(values.get(field) for field in reference_fields):
                    error('referenceImages', '多模态模式至少提供 referenceImages、referenceVideos、referenceAudios 中的一种素材')
            else:
                for field in reference_fields:
                    if values.get(field):
                        error(field, '参考素材数组只用于 mode=multimodal，不能与其他模式混用')
    if operation == 'text-generate':
        messages = values.get('messages')
        if isinstance(messages, list) and not any(isinstance(x, dict) and x.get('role') == 'user' for x in messages):
            error('messages', '至少需要一条 role=user 的消息')
        if values.get('stream') is True:
            error('stream', '当前 CLI 只支持非流式 JSON 响应，请使用 false')
    if operation == 'audio-generate' and isinstance(values.get('text_prompt'), str) and not values['text_prompt'].strip():
        error('text_prompt', '提示词不能为空或全为空格')
    if operation == 'material-authenticate':
        key = 'id' if values.get('source') == 'material' else 'url'
        if values.get('source') in {'material', 'generated', 'library'} and not values.get(key):
            error(key, f"source={values['source']} 时必填")
    if operation == 'asset-upload' and isinstance(values.get('file'), str):
        path = Path(values['file']).expanduser()
        if not path.is_file():
            error('file', '本地文件不存在或不是普通文件')
        elif path.stat().st_size > 80 * 1024**2:
            error('file', '文件不能超过 80 MB')
        mime = mimetypes.guess_type(path.name)[0] or ''
        if not mime.startswith(('image/', 'video/', 'audio/')):
            error('file', '仅支持图片、视频、音频文件')
    if operation == 'task-download' and isinstance(values.get('output'), str):
        path = Path(values['output']).expanduser()
        if path.exists():
            error('output', '目标已存在，请选择新文件')
        elif not path.parent.is_dir():
            error('output', '目标目录不存在，请先创建目录')
    return {'valid': not errors, 'operation': operation, 'scope': 'local', 'submitted': False, 'errors': errors, 'warnings': warnings,
            'notChecked': ['模型实时可用性与专属限制', '素材审核', '账号权限与余额', '画布业务状态']}


def canvas_view(result, node_id=None, summary=False):
    project = result
    document = project.get('payload') or project.get('data') or project
    nodes = document.get('nodes', [])
    edges = document.get('connections', [])
    view = {'projectId': project.get('id'), 'title': project.get('title'), 'version': project.get('version', document.get('version'))}
    if node_id is not None:
        nodes = [n for n in nodes if str(n.get('id')) == node_id]
        if not nodes:
            raise ValueError('指定节点不存在：' + node_id)
        edges = [e for e in edges if str(e.get('sourceId', e.get('source'))) == node_id or str(e.get('targetId', e.get('target'))) == node_id]
    if summary:
        nodes = [{'id': n.get('id'), 'name': n.get('title') or n.get('name') or (n.get('metadata') or {}).get('title') or n.get('label'), 'type': n.get('type'),
                  'status': n.get('status') or (n.get('metadata') or {}).get('status')} for n in nodes]
        edges = [{'id': e.get('id'), 'sourceId': e.get('sourceId', e.get('source')), 'targetId': e.get('targetId', e.get('target'))} for e in edges]
    view.update(nodes=nodes, connections=edges, nodeCount=len(nodes), connectionCount=len(edges))
    return view


def task_billing(result):
    value = result.get('billing')
    if not isinstance(value, dict):
        return None
    status = value.get('status')
    exact = value.get('chargedPointsExact')
    mode = value.get('accountingMode')
    accounting = {'accountingMode': mode} if mode in ('usage', 'external') else {}
    if status == 'settled' and isinstance(exact, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', exact):
        return {'status': status, 'chargedPointsExact': exact, **accounting}
    if status in ('pending', 'unavailable'):
        return {'status': status, 'chargedPointsExact': None, **accounting}
    return None


def task_view(result):
    task = result.get('task') or result
    view = {'taskId': result.get('task_id') or result.get('taskId') or task.get('id'),
            'status': task.get('status') or result.get('status'), 'progress': task.get('progress'),
            'resultUrl': task.get('result_url') or task.get('resultUrl'),
            'error': task.get('error_message') or task.get('errorMessage') or result.get('message')}
    if billing := task_billing(result):
        view['billing'] = billing
    return view


def model_view(config, kind=None, model=None):
    catalog = config.get('miraModels')
    if not isinstance(catalog, dict):
        raise ValueError('服务端未提供 Mira 公开模型目录；可用 model-list 查看原始响应，不能据此推断模型能力')
    rows = []
    for category, key in [('image', 'images'), ('video', 'videos'), ('text', 'texts'), ('audio', 'audios')]:
        if kind and category != kind:
            continue
        for item in catalog.get(key, []):
            # Allowlist public fields: never copy arbitrary provider configuration.
            row = {k: item[k] for k in ('id', 'upstream', 'label', 'resolutions', 'provider') if k in item}
            row['kind'] = category
            rows.append(row)
    if model:
        rows = [r for r in rows if model in (r.get('id'), r.get('upstream'), r.get('label'))]
        if not rows:
            raise ValueError('公开模型目录中未找到该模型，请先运行 model-list --summary')
        if len(rows) > 1:
            raise ValueError('模型名称匹配多项，请使用精确模型 ID 和 --kind')
        row = rows[0]
        row['parameterSchema'] = contracts()[row['kind'] + '-generate']['schema']
        row['limits'] = {'referenceImages': None, 'referenceVideos': None, 'referenceAudios': None, 'outputCount': None, 'mediaFormats': None}
        row['notes'] = ['parameterSchema 是通用接口规则，不代表模型专属上限。', 'limits 中 null 表示服务端公开目录未提供，不能解释为无限制。', 'resolutions（若存在）来自当前服务端模型配置。']
        if __package__:
            from .mira_cli_capabilities import capability
        else:
            from mira_cli_capabilities import capability
        rules = capability(config, row['kind'], row['id'])
        if rules:
            row['capabilities'] = rules
            row['limits'] = rules.get('limits', row['limits'])
        return row
    warnings = []
    if (kind is None or kind == 'audio') and 'audios' not in catalog:
        warnings.append('公开目录未提供音频模型信息；这不代表音频生成功能不可用。')
    return {'models': rows, 'count': len(rows), 'warnings': warnings}


def select_fields(value, fields):
    selected = {}
    missing = []
    for path in fields.split(','):
        path = path.strip()
        if not path:
            raise ValueError('--fields 请使用逗号分隔的字段路径，如 task.status,task.result_url')
        current = value
        try:
            for part in path.split('.'):
                current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, TypeError, ValueError):
            missing.append(path)
            current = None
        selected[path] = current
    if missing:
        selected['_missingFields'] = missing
    return selected


def output_view(operation, result, *, summary=False, node_id=None, kind=None, model=None, fields=None):
    if operation == 'canvas-get' and (summary or node_id is not None):
        result = canvas_view(result, node_id, summary)
    elif operation == 'task-get' and summary:
        result = task_view(result)
    elif operation == 'model-info' or (operation == 'model-list' and (summary or kind)):
        result = model_view(result, kind, model)
    return select_fields(result, fields) if fields else result
