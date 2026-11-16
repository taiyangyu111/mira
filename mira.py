"""Mira CLI: fixed operation catalog and HTTP client."""
from __future__ import annotations

import argparse
from getpass import getpass
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import sys
from urllib.parse import quote, urlsplit

import httpx

DEFAULT_ORIGIN = 'http://127.0.0.1:8091'
AUTH = '/api/mira-plugin/v1'
CANVAS = '/canvas/api'
OPERATIONS = {
    'account': ('GET', AUTH + '/account', '查看当前 Mira 账号'),
    'canvas-list': ('GET', CANVAS + '/canvases', '分页列出画布'),
    'canvas-get': ('GET', CANVAS + '/canvases/{project_id}', '读取画布节点、连接与版本'),
    'canvas-create': ('POST', CANVAS + '/canvases', '创建画布'),
    'canvas-apply': ('POST', CANVAS + '/canvases/{project_id}/actions', '按 baseVersion 和 operationId 批量编辑画布'),
    'asset-search': ('GET', '/api/asset-library/items', '按 Mira 资产分类和关键词搜索'),
    'asset-import': ('POST', CANVAS + '/mira/assets/{asset_id}/import', '将已有 Mira 资产导入画布素材'),
    'asset-upload': ('POST', CANVAS + '/media/references', '上传图片、视频或音频参考素材'),
    'model-list': ('GET', CANVAS + '/ai/config', '读取 Mira 画布模型配置'),
    'generation-quote': ('POST', CANVAS + '/ai/generation/quote', '查询图片或视频估价及是否可用'),
    'task-get': ('GET', CANVAS + '/ai/tasks/{task_id}', '查询自己的画布任务状态与结果'),
    'task-download': ('GET', CANVAS + '/ai/tasks/{task_id}/download', '下载已归档的任务结果到 output'),
    'material-list': ('GET', CANVAS + '/materials/canvas-library', '查询画布素材及服务端审核状态'),
    'material-get': ('GET', CANVAS + '/materials/{material_id}', '读取当前账号可查看的素材'),
    'material-preparations': ('POST', CANVAS + '/materials/preparations', '批量查询审核状态，最多100个素材ID'),
    'material-refresh-previews': ('POST', CANVAS + '/materials/refresh-previews', '刷新当前账号素材的预览地址'),
    'material-authenticate': ('POST', CANVAS + '/materials/authenticate-reference', '使用与画布@相同的素材认证流程'),
    'material-retry': ('POST', CANVAS + '/materials/{material_id}/preparation/retry', '请求服务端重试素材审核，不代表通过审核'),
    'material-reference': ('POST', CANVAS + '/materials/{material_id}/canvas-reference', '登记素材在画布节点中的引用'),
}
GENERATION_OPERATIONS = {
    'text-generate': ('/chat/completions', '执行文本创作'),
    'image-generate': ('/images/generate', '提交图片生成'),
    'video-generate': ('/videos/generate', '提交视频生成'),
    'audio-generate': ('/audio/seed/generate', '提交音频生成'),
}
OPERATIONS.update({name: ('POST', CANVAS + '/ai' + path, description)
                   for name, (path, description) in GENERATION_OPERATIONS.items()})
KEYRING_SERVICE = 'Mira Plugin'


def origin(value=None):
    parsed = urlsplit(value or os.environ.get('MIRA_URL') or DEFAULT_ORIGIN)
    hostname = parsed.hostname
    if hostname is None or parsed.username is not None or parsed.password is not None or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise ValueError('MIRA_URL 必须是完整站点地址，且不含凭据、查询参数或路径')
    try:
        loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        loopback = hostname == 'localhost'
    if parsed.scheme != 'https' and (parsed.scheme != 'http' or not loopback):
        raise ValueError('远程 Mira 地址必须使用 HTTPS')
    return parsed.scheme + '://' + parsed.netloc


def saved_token(base):
    if token := os.environ.get('MIRA_TOKEN'):
        return token.strip()
    import keyring
    return keyring.get_password(KEYRING_SERVICE, base)


def call(operation, arguments=None, *, token=None, base=None, upload_root=None, request_timeout=90):
    method, path, _ = OPERATIONS[operation]
    arguments = dict(arguments or {})
    for key in ('project_id', 'asset_id', 'task_id', 'material_id'):
        placeholder = '{' + key + '}'
        if placeholder in path:
            identifier = str(arguments.pop(key))
            if identifier in {'', '.', '..'} or '/' in identifier or '\\' in identifier:
                raise ValueError('无效的资源标识')
            path = path.replace(placeholder, quote(identifier, safe=''))
    base = origin(base)
    token = token or saved_token(base)
    if not token:
        raise ValueError('请先运行 auth-save，或通过 MIRA_TOKEN 提供访问令牌')
    headers = {'Authorization': 'Bearer ' + token}
    with httpx.Client(base_url=base, headers=headers, trust_env=False, timeout=request_timeout) as client:
        if operation == 'asset-upload':
            file = Path(arguments.pop('file')).expanduser()
            if upload_root:
                root = Path(upload_root).resolve(strict=True)
                file = (root / file).resolve(strict=True)
                if not file.is_relative_to(root):
                    raise ValueError('该文件不在允许上传的目录中')
            if not file.is_file() or file.stat().st_size > 80 * 1024**2:
                raise ValueError('请选择不超过 80 MB 的普通文件')
            mime = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if not mime.startswith(('image/', 'video/', 'audio/')):
                raise ValueError('仅允许上传图片、视频或音频')
            with file.open('rb') as stream:
                response = client.post(path, files={'file': (file.name, stream, mime)}, data=arguments)
        elif operation == 'task-download':
            destination = Path(arguments.pop('output')).expanduser()
            if destination.exists():
                raise ValueError('下载目标已存在，请选择新文件')
            with client.stream('GET',path) as response:
                if response.is_success:
                    created = False
                    try:
                        with destination.open('xb') as stream:
                            created = True
                            for chunk in response.iter_bytes():
                                stream.write(chunk)
                    except (OSError, httpx.HTTPError):
                        if created:
                            destination.unlink(missing_ok=True)
                        raise
                    return {'output':str(destination.resolve())}
                response.read()
        elif method == 'GET':
            response = client.get(path, params=arguments)
        else:
            response = client.post(path, json=arguments)
        if not response.is_success:
            try:
                error = response.json().get('detail', '')
                if not isinstance(error, str):
                    error = json.dumps(error, ensure_ascii=False)
            except (ValueError, AttributeError):
                error = '服务返回了非 JSON 响应'
            raise ValueError(f'Mira HTTP {response.status_code} {error}')
        return response.json()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
    if __package__:
        from . import mira_cli_support as cli
        from . import mira_cli_jobs as jobs
    else:
        import mira_cli_support as cli
        import mira_cli_jobs as jobs
    parser = argparse.ArgumentParser(prog='mira', description='Mira CLI 创作工具')
    parser.add_argument('--version', action='version', version='Mira CLI 0.3.0')
    commands = parser.add_subparsers(dest='operation', required=True)

    def job_options(command, resume=False):
        command.add_argument('--run-file', type=Path, required=resume, help='任务记录 JSON；接续使用原文件，新任务不能覆盖已有记录')
        command.add_argument('--timeout', type=float, default=600, help='最多轮询等待秒数，默认 600；0 表示只检查一次')
        command.add_argument('--poll-interval', type=float, default=5, help='轮询间隔秒数，默认 5，范围 1–60')
        if not resume:
            command.add_argument('--project-id', help='回填目标画布 UUID，须同时指定 --node-id')
            command.add_argument('--node-id', help='回填目标媒体节点 ID')
    for name, (_, _, description) in OPERATIONS.items():
        command = commands.add_parser(name, help=description, description=description,
                                      epilog=cli.command_help(name), formatter_class=argparse.RawDescriptionHelpFormatter)
        inputs = command.add_mutually_exclusive_group()
        inputs.add_argument('--json', help='操作参数 JSON 对象')
        inputs.add_argument('--file', type=Path, help='从 UTF-8 JSON 文件读取操作参数')
        info = command.add_mutually_exclusive_group()
        info.add_argument('--schema', action='store_true', help='输出完整参数 JSON Schema，不联网')
        info.add_argument('--example', action='store_true', help='输出示例参数 JSON，不联网')
        validation = command.add_mutually_exclusive_group()
        validation.add_argument('--validate-only', action='store_true', help='仅本地检查参数，不联网、不提交')
        validation.add_argument('--skip-validation', action='store_true', help='跳过本地规则以兼容更新的服务端；服务端仍会校验')
        command.add_argument('--fields', help='仅输出指定字段，逗号分隔；支持 task.status 和数组下标')
        command.add_argument('--save-response', type=Path, help='保存完整响应到 UTF-8 JSON 文件，不覆盖；默认终端只输出保存回执')
        if name in {'canvas-get', 'task-get', 'model-list'}:
            command.add_argument('--summary', action='store_true', help='输出精简摘要')
        if name == 'canvas-get':
            command.add_argument('--node-id', help='仅输出指定节点及与它相连的连接（在客户端筛选）')
        if name == 'model-list':
            command.add_argument('--kind', choices=['image', 'video', 'text', 'audio'], help='仅输出指定类型的公开模型目录')
        if name in GENERATION_OPERATIONS:
            command.add_argument('--confirm-spend', action='store_true', help='确认执行可能消耗积分的操作')
        if name in jobs.MEDIA_OPERATIONS:
            command.add_argument('--wait', action='store_true', help='等待图片/视频/音频结果；指定回填目标或记录文件也会启用等待')
            job_options(command)
    for name, description in [('task-wait', '等待已有媒体任务，可回填指定节点；不会提交生成'),
                              ('task-resume', '读取任务记录，继续等待或只重试回填；不会提交生成')]:
        command = commands.add_parser(name, help=description, description=description)
        job_options(command, resume=name == 'task-resume')
        if name == 'task-wait':
            command.add_argument('--task-id', required=True, help='已有任务 UUID')
        command.add_argument('--fields', help='筛选接续回执字段')
        command.add_argument('--save-response', type=Path, help='另存完整接续回执，不覆盖已有文件')
    model_info = commands.add_parser('model-info', help='查询指定模型的公开配置与通用参数规则')
    model_info.add_argument('--model', required=True, help='模型 ID、上游 ID 或唯一展示名')
    model_info.add_argument('--kind', choices=['image', 'video', 'text', 'audio'])
    model_info.add_argument('--fields', help='逗号分隔的输出字段路径')
    model_info.add_argument('--save-response', type=Path, help='保存完整服务响应，不覆盖已有文件')
    commands.add_parser('auth-save', help='在系统凭据库保存令牌')
    commands.add_parser('auth-clear', help='移除本机保存的令牌')
    options = parser.parse_args()
    managed = options.operation in {'task-wait', 'task-resume'} or (options.operation in jobs.MEDIA_OPERATIONS and
                any(getattr(options, k, None) for k in ('wait', 'run_file', 'project_id', 'node_id')))
    workflow_exit = 0
    try:
        if managed:
            jobs.validate_options(options)
        if options.operation in {'auth-save', 'auth-clear'}:
            import keyring
            base = origin()
            if options.operation == 'auth-save':
                token = getpass('Mira access token: ').strip()
                if not token:
                    raise ValueError('请输入访问令牌')
                account = call('account', token=token, base=base)
                keyring.set_password(KEYRING_SERVICE, base, token)
                result = {'account': account['username'], 'origin': base}
            else:
                if keyring.get_password(KEYRING_SERVICE, base):
                    keyring.delete_password(KEYRING_SERVICE, base)
                result = {'origin': base, 'cleared': True}
        else:
            if options.operation not in {'model-info', 'task-wait', 'task-resume'}:
                if options.schema or options.example:
                    result = cli.contracts()[options.operation]['schema' if options.schema else 'example']
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return
                raw = options.file.read_text(encoding='utf-8-sig') if options.file else options.json
                arguments = json.loads(raw) if raw else {}
                if not isinstance(arguments, dict):
                    raise ValueError('操作参数必须是 JSON 对象')
                if not options.skip_validation:
                    report = cli.validate_arguments(options.operation, arguments)
                    if options.validate_only:
                        print(json.dumps(report, ensure_ascii=False, indent=2))
                        if not report['valid']:
                            parser.exit(2)
                        return
                    if not report['valid']:
                        parser.exit(2, '参数检查失败，未提交请求：\n' + '\n'.join(f"  {e['field']}: {e['message']}" for e in report['errors']) + '\n')
                    for warning in report['warnings']:
                        print('提示：' + warning, file=sys.stderr)
                if options.operation in GENERATION_OPERATIONS and not options.confirm_spend:
                    raise ValueError('此操作可能消耗积分，请先查看报价并传入 --confirm-spend')
            else:
                arguments = {}
            if options.fields and any(not part.strip() for part in options.fields.split(',')):
                raise ValueError('--fields 不允许空字段名')
            # Reserve output before sending a possibly billable request. Never overwrite.
            destination = options.save_response.expanduser() if options.save_response else None
            stream = destination.open('x', encoding='utf-8') if destination else None
            response_received = False
            try:
                if managed:
                    runner = jobs.Runner(call, origin())
                    if options.operation == 'task-resume':
                        result = runner.resume(options.run_file, timeout=options.timeout, poll_interval=options.poll_interval)
                    else:
                        result = runner.start(options.operation, arguments, path=options.run_file,
                                              project_id=options.project_id, node_id=options.node_id,
                                              task_id=getattr(options, 'task_id', None), timeout=options.timeout,
                                              poll_interval=options.poll_interval)
                    workflow_exit = result['exitCode']
                else:
                    result = call('model-list' if options.operation == 'model-info' else options.operation, arguments)
                response_received = True
                if stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                    stream.flush()
            except OSError as exc:
                if response_received:
                    print(json.dumps({'responseReceived': True, 'saveError': str(exc), 'response': result}, ensure_ascii=False, indent=2))
                    parser.exit(1, '请求已返回，但保存文件失败；请先检查上述任务 ID，不要重复提交生成。\n')
                raise
            finally:
                if stream:
                    stream.close()
                    if not response_received:
                        destination.unlink(missing_ok=True)
            has_view = managed or any(getattr(options, k, None) for k in ('summary', 'node_id', 'kind', 'fields')) or options.operation == 'model-info'
            if destination and not has_view:
                receipt = {'savedResponse': str(destination.resolve())}
                if isinstance(result, dict):
                    if options.operation in GENERATION_OPERATIONS or options.operation == 'task-get':
                        receipt.update({k: v for k, v in cli.task_view(result).items() if v is not None})
                    elif result.get('id') is not None:
                        receipt['id'] = result['id']
                result = receipt
            else:
                result = cli.output_view(options.operation, result, summary=getattr(options, 'summary', False),
                                         node_id=getattr(options, 'node_id', None), kind=getattr(options, 'kind', None),
                                         model=getattr(options, 'model', None), fields=options.fields)
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if workflow_exit:
        parser.exit(workflow_exit)


if __name__ == '__main__':
    main()
