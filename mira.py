"""Mira CLI operation catalog and HTTP client."""
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
    parser.add_argument('--version', action='version', version='Mira CLI 0.8.0')
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
        if name in {'image-generate', 'video-generate'}:
            command.add_argument('--check-model', action='store_true', help='提交前读取当前服务端模型规则并检查；仅检查请用 generation-check')
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
    catalog_commands = {
        'node-list': '离线列出前端可见的基础节点与用途',
        'node-info': '离线查询节点输入输出、参数映射、运行方式与示例',
        'workflow-list': '离线列出常见创作流程',
        'workflow-info': '离线查询创作步骤、所需命令与能力边界',
    }
    for name, description in catalog_commands.items():
        command = commands.add_parser(name, help=description, description=description)
        if name == 'node-info':
            command.add_argument('--node', required=True, help='node-list 中的节点 ID')
            command.add_argument('--example', action='store_true', help='只输出创建节点的 canvas-apply 示例；不会提交')
        if name == 'workflow-info':
            command.add_argument('--workflow', required=True, help='workflow-list 中的流程 ID')
        command.add_argument('--fields', help='只输出指定字段，逗号分隔')
        command.add_argument('--save-response', type=Path, help='保存查询结果，不覆盖已有文件')
    storyboard = commands.add_parser('storyboard-plan', help='预览分镜节点布局并保存计划，只读画布、不生成',
                                    description='最多 10 个镜头。默认说明→图片→视频；自动放在现有节点下方。用 --example/--schema 离线查询输入。')
    inputs = storyboard.add_mutually_exclusive_group()
    inputs.add_argument('--file', type=Path, help='UTF-8 分镜 JSON 文件')
    inputs.add_argument('--json', help='分镜 JSON 对象')
    info = storyboard.add_mutually_exclusive_group()
    info.add_argument('--example', action='store_true', help='离线输出完整输入示例')
    info.add_argument('--schema', action='store_true', help='离线输出输入规则')
    info.add_argument('--validate-only', action='store_true', help='仅检查分镜输入，不联网')
    storyboard.add_argument('--project-id', help='已有画布 UUID')
    storyboard.add_argument('--out', type=Path, help='保存可审阅、可重复应用的计划，不覆盖已有文件')
    storyboard.add_argument('--x', type=float, help='可选起点，必须与 --y 同时设置；检查现有节点重叠')
    storyboard.add_argument('--y', type=float, help='可选起点；默认放到已有内容下方')
    storyboard_apply = commands.add_parser('storyboard-apply', help='应用保存的布局计划，只新增节点和连线，不生成')
    storyboard_apply.add_argument('--file', type=Path, required=True, help='storyboard-plan 保存的计划；重试沿用原文件')
    check = commands.add_parser('generation-check', help='读取当前模型规则，检查图片/视频参数，不生成')
    check.add_argument('--kind', choices=['image', 'video'], required=True)
    check_inputs = check.add_mutually_exclusive_group(required=True)
    check_inputs.add_argument('--file', type=Path)
    check_inputs.add_argument('--json')
    batch_create = commands.add_parser('batch-create', help='将已有任务记录组成批次，不生成')
    batch_create.add_argument('--run-file', type=Path, action='append', required=True, help='重复指定，每批最多100个已有记录')
    batch_create.add_argument('--out', type=Path, required=True, help='新批次文件，不覆盖')
    batch_create.add_argument('--name')
    batch_status = commands.add_parser('batch-status', help='汇总任务与回填状态，默认读取本地记录')
    batch_status.add_argument('--file', type=Path, required=True)
    batch_status.add_argument('--refresh', action='store_true', help='向服务端查询最新任务状态，不回填、不改记录')
    batch_status.add_argument('--summary', action='store_true', help='只输出计数及需要处理的任务')
    batch_resume = commands.add_parser('batch-resume', help='接续整批已有任务，自动回填；不重新生成')
    batch_resume.add_argument('--file', type=Path, required=True)
    batch_resume.add_argument('--timeout', type=float, default=0, help='批次等待秒数，0为每任务仅检查一次')
    batch_resume.add_argument('--poll-interval', type=float, default=5)
    batch_resume.add_argument('--workers', type=int, default=3, help='并行查询/回填数量，1–8，默认3')
    batch_resume.add_argument('--attachments-only', action='store_true', help='仅重试已生成成功的待回填/回填失败任务')
    batch_resume.add_argument('--summary', action='store_true', help='只输出计数及需要处理的任务')
    media = commands.add_parser('media-inspect', help='验收本机成品实际尺寸、时长、帧率和音轨，不联网')
    media.add_argument('--file', type=Path, required=True)
    media.add_argument('--ffprobe', help='ffprobe 可执行文件路径；也可设置 MIRA_FFPROBE 或 PATH')
    for name, kind in [('width', int), ('height', int), ('duration', float), ('fps', float)]:
        media.add_argument('--expect-' + name, type=kind)
    media.add_argument('--expect-audio', choices=['yes', 'no'])
    media.add_argument('--duration-tolerance', type=float, default=1)
    media.add_argument('--fps-tolerance', type=float, default=0.1)
    production_create = commands.add_parser('production-create', help='从已应用的分镜布局创建执行计划，只读画布')
    production_create.add_argument('--layout', type=Path, required=True, help='storyboard-plan 保存的布局文件')
    production_create.add_argument('--out', type=Path, required=True, help='新执行计划文件，不覆盖')
    production_create.add_argument('--image-model', help='节点未设置模型时使用的图片模型')
    production_create.add_argument('--video-model', help='节点未设置模型时使用的视频模型')
    production_create.add_argument('--image-size', help='可选统一图片尺寸，例如2560x1440；默认按画幅计算2K档位')
    production_commands = {'production-create': production_create}
    for name, description in (
        ('production-preflight', '整批检查参数并报价，保存报价ID，不生成'),
        ('production-status', '读取执行计划及每镜任务进度，不联网'),
        ('production-select', '记录审图后选定的首帧，不生成'),
        ('production-run', '按已审阅报价分阶段提交新任务，可能消耗积分'),
        ('production-resume', '接续已有任务与回填，绝不提交新生成'),
    ):
        command = commands.add_parser(name, help=description, description=description)
        command.add_argument('--file', type=Path, required=True)
        if name == 'production-select':
            selection = command.add_mutually_exclusive_group(required=True)
            selection.add_argument('--shot', help='两位镜头ID，例如01')
            selection.add_argument('--all', action='store_true', help='确认全部镜头当前图片已审阅')
            command.add_argument('--source-node', help='可选使用同画布的另一图片节点，仅限单个镜头')
        if name in ('production-run', 'production-resume'):
            command.add_argument('--stage', choices=['images', 'videos'], required=True)
        if name == 'production-run':
            command.add_argument('--quote-id', required=True, help='已审阅的预检报价ID')
            command.add_argument('--max-points', required=True, help='本阶段待提交任务的报价总额上限，不是实际结算硬上限')
            command.add_argument('--confirm-spend', action='store_true', help='确认提交生成并可能扣费')
        if name == 'production-resume':
            command.add_argument('--timeout', type=float, default=0)
            command.add_argument('--workers', type=int, default=3)
        production_commands[name] = command
    for custom in (check, batch_status, batch_resume, media, *production_commands.values()):
        custom.add_argument('--fields', help='逗号分隔的输出字段')
        custom.add_argument('--save-response', type=Path, help='保存完整报告，不覆盖')
    commands.add_parser('auth-save', help='在系统凭据库保存令牌')
    commands.add_parser('auth-clear', help='移除本机保存的令牌')
    options = parser.parse_args()
    managed = options.operation in {'task-wait', 'task-resume'} or (options.operation in jobs.MEDIA_OPERATIONS and
                any(getattr(options, k, None) for k in ('wait', 'run_file', 'project_id', 'node_id')))
    workflow_exit = 0
    try:
        if options.operation in {'generation-check', 'batch-create', 'batch-status', 'batch-resume', 'media-inspect'} or options.operation in production_commands:
            if __package__:
                from . import mira_cli_capabilities as capabilities, mira_cli_batch as batch, mira_cli_media as media_cli
            else:
                import mira_cli_capabilities as capabilities, mira_cli_batch as batch, mira_cli_media as media_cli
            if getattr(options, 'fields', None) and any(not part.strip() for part in options.fields.split(',')):
                raise ValueError('--fields 不允许空字段名')
            destination = getattr(options, 'save_response', None)
            stream = destination.expanduser().open('x', encoding='utf-8') if destination else None
            received = False
            try:
                if options.operation in production_commands:
                    if __package__:
                        from . import mira_cli_production as production
                    else:
                        import mira_cli_production as production
                    result = production.command(options, call, origin)
                    workflow_exit = result.get('exitCode', 2 if result.get('valid') is False else 0)
                elif options.operation == 'generation-check':
                    raw = options.file.read_text(encoding='utf-8-sig') if options.file else options.json
                    result = capabilities.check(options.kind, json.loads(raw), call)
                    workflow_exit = 0 if result['valid'] else 2
                elif options.operation == 'batch-create':
                    result = batch.create(options.run_file, options.out, origin(), options.name)
                elif options.operation == 'batch-status':
                    result = batch.status(options.file, origin(), call, options.refresh)
                elif options.operation == 'batch-resume':
                    result = batch.resume(options.file, origin(), call, timeout=options.timeout,
                                          poll_interval=options.poll_interval, workers=options.workers,
                                          attachments_only=options.attachments_only)
                    workflow_exit = result['exitCode']
                else:
                    expected = {key: getattr(options, 'expect_' + key) for key in ('width', 'height', 'duration', 'fps')
                                if getattr(options, 'expect_' + key) is not None}
                    if options.expect_audio is not None:
                        expected['hasAudio'] = options.expect_audio == 'yes'
                    result = media_cli.inspect(options.file, ffprobe=options.ffprobe, expected=expected,
                                               duration_tolerance=options.duration_tolerance, fps_tolerance=options.fps_tolerance)
                    workflow_exit = 2 if result['passed'] is False else 0
                received = True
                if stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                    stream.flush()
            except OSError as exc:
                if received:
                    print(json.dumps({'saveError': str(exc), 'response': result}, ensure_ascii=False, indent=2))
                raise
            finally:
                if stream:
                    stream.close()
                    if not received:
                        destination.expanduser().unlink(missing_ok=True)
            if getattr(options, 'summary', False):
                result = {**{k: v for k, v in result.items() if k != 'tasks'},
                          'attention': [{k: r.get(k) for k in ('runFile', 'taskId', 'nodeId', 'status', 'error')}
                                        for r in result['tasks'] if r['status'] != 'completed']}
            if getattr(options, 'fields', None):
                result = cli.select_fields(result, options.fields)
            elif destination and not getattr(options, 'summary', False):
                result = {'savedResponse': str(destination.expanduser().resolve()), 'exitCode': workflow_exit}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if workflow_exit:
                parser.exit(workflow_exit)
            return
        if options.operation in {'storyboard-plan', 'storyboard-apply'}:
            if __package__:
                from . import mira_cli_storyboard as storyboard_cli
            else:
                import mira_cli_storyboard as storyboard_cli
            result = storyboard_cli.run(options, call, origin)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result.get('valid') is False:
                parser.exit(2)
            return
        if options.operation in catalog_commands:
            if __package__:
                from . import mira_cli_catalog as catalog
            else:
                import mira_cli_catalog as catalog
            result = catalog.query(options.operation, getattr(options, 'node', None) or getattr(options, 'workflow', None),
                                   example=getattr(options, 'example', False))
            # Local guidance never touches credentials, network, canvas or task APIs.
            if options.fields:
                result = cli.select_fields(result, options.fields)
            if options.save_response:
                with options.save_response.expanduser().open('x', encoding='utf-8') as stream:
                    json.dump(result, stream, ensure_ascii=False, indent=2)
                    stream.write('\n')
                result = {'savedResponse': str(options.save_response.expanduser().resolve())}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
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
                if getattr(options, 'check_model', False) and options.validate_only:
                    raise ValueError('--validate-only 只做离线检查；联网检查模型请使用 generation-check')
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
                if getattr(options, 'check_model', False):
                    if options.skip_validation:
                        raise ValueError('--check-model 不能与 --skip-validation 同用')
                    if __package__:
                        from . import mira_cli_capabilities as capabilities
                    else:
                        import mira_cli_capabilities as capabilities
                    report = capabilities.check(options.operation.split('-')[0], arguments, call)
                    if not report['valid']:
                        parser.exit(2, '模型参数检查失败，未提交生成：\n' + json.dumps(report, ensure_ascii=False, indent=2) + '\n')
                    if not report['modelChecked']:
                        raise ValueError('服务端未提供可用的模型规则，--check-model 未通过；请先核对 model-info。')
                    for warning in report['warnings']:
                        print('模型提示：' + warning, file=sys.stderr)
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
