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


def call(operation, arguments=None, *, token=None, base=None, upload_root=None):
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
    with httpx.Client(base_url=base, headers=headers, trust_env=False, timeout=90) as client:
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
    parser = argparse.ArgumentParser(prog='mira', description='Mira CLI 创作工具')
    commands = parser.add_subparsers(dest='operation', required=True)
    for name, (_, _, description) in OPERATIONS.items():
        command = commands.add_parser(name, help=description)
        inputs = command.add_mutually_exclusive_group()
        inputs.add_argument('--json', help='操作参数 JSON 对象')
        inputs.add_argument('--file', type=Path, help='从 UTF-8 JSON 文件读取操作参数')
        if name in GENERATION_OPERATIONS:
            command.add_argument('--confirm-spend', action='store_true', help='确认执行可能消耗积分的操作')
    commands.add_parser('auth-save', help='在系统凭据库保存令牌')
    commands.add_parser('auth-clear', help='移除本机保存的令牌')
    options = parser.parse_args()
    try:
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
            if options.operation in GENERATION_OPERATIONS and not options.confirm_spend:
                raise ValueError('此操作可能消耗积分，请先查看报价并传入 --confirm-spend')
            raw = options.file.read_text(encoding='utf-8-sig') if options.file else options.json
            arguments = json.loads(raw) if raw else {}
            if not isinstance(arguments, dict):
                raise ValueError('操作参数必须是 JSON 对象')
            result = call(options.operation, arguments)
    except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
        parser.exit(1, f'{exc}\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
