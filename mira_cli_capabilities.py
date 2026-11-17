"""Read current public model rules; never submit generation or inspect secrets."""
from copy import deepcopy

if __package__:
    from . import mira_cli_support as support
else:
    import mira_cli_support as support


def capability(config, kind, model_id):
    group = config.get('miraCapabilities') or {}
    if group.get('schemaVersion') != 1:
        return None
    rows = [r for r in group.get('models', []) if r.get('kind') == kind and r.get('id') == model_id]
    if len(rows) != 1:
        return None
    row = rows[0]
    return {k: deepcopy(row[k]) for k in ('id', 'kind', 'coverage', 'source', 'requestSchema', 'limits',
             'notChecked', 'notes', 'audioOnlyReference', 'defaults', 'revision', 'resolutionLevels') if k in row}


def check(kind, arguments, call):
    from jsonschema import Draft202012Validator
    operation = kind + '-generate'
    if kind not in ('image', 'video'):
        raise ValueError('模型规则检查目前支持 image/video')
    report = support.validate_arguments(operation, arguments)
    report.update(scope='local', modelChecked=False, submitted=False)
    if not report['valid']:
        return report
    values = dict(arguments)
    entry = support.contracts()[operation]
    for alias, target in entry.get('aliases', {}).items():
        if alias in values:
            values[target] = values.pop(alias)
    model = values.get('model', entry['schema']['properties']['model']['default'])
    config = call('model-list', {})
    try:
        row = support.model_view(config, kind=kind, model=model)
    except ValueError as exc:
        report['warnings'].append(str(exc))
        report['notChecked'].append('未匹配到公开模型，模型能力未验证')
        return report
    rules = capability(config, kind, row['id'])
    report.update(model=row['id'], scope='server-catalog')
    if rules is None or not (rules.get('requestSchema') or {}).get('properties'):
        report['warnings'].append('服务端未提供版本化模型规则；仅完成通用校验，不能保证模型支持。')
        return report
    report.update(modelChecked=True, capabilityRevision=rules.get('revision'), coverage=rules.get('coverage', 'partial'))
    effective = {**rules.get('defaults', {}), **values}
    # These are explicit server normalization aliases, not silent resolution changes.
    if kind == 'video':
        effective['resolution'] = str(effective.get('resolution') or '720p').strip().lower()
        for key in ('ratio', 'aspectRatio'):
            if isinstance(effective.get(key), str) and not effective[key].strip():
                effective[key] = 'adaptive'
        if effective.get('ratio'):
            effective.pop('aspectRatio', None)
    schema = rules.get('requestSchema') or {'type': 'object'}
    Draft202012Validator.check_schema(schema)
    for e in Draft202012Validator(schema).iter_errors(effective):
        field = '.'.join(map(str, e.absolute_path)) or '$'
        report['errors'].append({'field': field, 'message': '当前模型规则：' + e.message})
    if kind == 'video' and effective.get('mode') == 'multimodal':
        if rules.get('audioOnlyReference') is False and effective.get('referenceAudios') and not (effective.get('referenceImages') or effective.get('referenceVideos') or effective.get('imageUrl')):
            report['errors'].append({'field': 'referenceAudios', 'message': '该模型不支持仅音频参考，请提供参考图片或视频'})
        count = len(effective.get('referenceImages') or [])
        if effective.get('imageUrl') and effective['imageUrl'] not in (effective.get('referenceImages') or []):
            count += 1
        maximum = rules.get('limits', {}).get('referenceImages')
        if maximum is not None and count > maximum:
            report['errors'].append({'field': 'referenceImages', 'message': f'包含 imageUrl 后参考图片共 {count} 张，上限 {maximum}'})
    if values.get('parameters'):
        report['warnings'].append('parameters 扩展对象可能覆盖顶层参数，未完成模型专属校验。')
    if kind == 'video' and (values.get('autoDuration') or values.get('referenceVideos')):
        report['warnings'].append('自动时长或视频编辑可能改变成品时长，生成后请验收实际文件。')
    report['warnings'] += rules.get('notes', [])
    report['notChecked'] = rules.get('notChecked', [])
    report['valid'] = not report['errors']
    return report
