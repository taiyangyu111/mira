"""Visible canvas media actions: download selection and trim audio without generation."""
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile, ZIP_STORED
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile

if __package__:
    from . import mira_cli_jobs as jobs
else:
    import mira_cli_jobs as jobs


def register(commands):
    archive = commands.add_parser('images-download', help='把画布中选定图片打包 ZIP；失败时不输出残缺压缩包')
    archive.add_argument('--project-id', required=True)
    selection = archive.add_mutually_exclusive_group(required=True)
    selection.add_argument('--node-id', action='append', help='可重复，下载指定图片节点')
    selection.add_argument('--group-id', help='下载指定分组中的图片')
    selection.add_argument('--all-images', action='store_true', help='下载画布全部图片节点')
    archive.add_argument('--all-results', action='store_true', help='包含每个节点的全部生成结果；默认只下载当前展示图片')
    archive.add_argument('--output', type=Path, required=True)
    trim = commands.add_parser('audio-trim', help='裁剪已有音频，新建结果节点并连回源节点；不调用生成服务')
    trim.add_argument('--project-id', required=True)
    trim.add_argument('--node-id', required=True)
    trim.add_argument('--start', type=float, required=True, help='起点秒数')
    trim.add_argument('--end', type=float, required=True, help='终点秒数，至少保留 0.05 秒')
    trim.add_argument('--run-file', type=Path, required=True, help='接续记录；重复使用相同记录不会重复创建节点')
    trim.add_argument('--preview-only', action='store_true', help='只在本机生成 WAV 供试听，不上传、不改变画布')


def document(canvas):
    return canvas.get('payload') or canvas.get('data') or canvas


def safe_name(value):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value)).strip(' .')[:70] or 'image'


def publish_file(source, destination):
    created = False
    try:
        with Path(destination).open('xb') as out, Path(source).open('rb') as inp:
            created = True
            shutil.copyfileobj(inp, out)
    except BaseException:
        if created:
            Path(destination).unlink(missing_ok=True)
        raise


def download_images(options, call):
    UUID(options.project_id)
    destination = options.output.expanduser().resolve()
    if destination.exists():
        raise ValueError('下载目标已存在，请选择新文件')
    canvas = call('canvas-get', {'project_id': options.project_id})
    nodes = document(canvas).get('nodes', [])
    if options.node_id:
        ids = list(dict.fromkeys(options.node_id))
        lookup = {str(n.get('id')): n for n in nodes}
        if any(i not in lookup or lookup[i].get('type') != 'image' for i in ids):
            raise ValueError('所选节点不存在或不是图片节点')
        nodes = [lookup[i] for i in ids]
    else:
        nodes = [n for n in nodes if n.get('type') == 'image' and
                 (not options.group_id or (n.get('metadata') or {}).get('groupId') == options.group_id)]
    if not 1 <= len(nodes) <= 100:
        raise ValueError('请选择 1–100 个图片节点')
    selections = [(n, i) for n in nodes for i in
                  (range(len((n.get('metadata') or {}).get('resultUrls') or [None])) if options.all_results else [None])]
    if len(selections) > 100:
        raise ValueError('一次最多下载 100 张图片，请缩小选择范围')
    manifest, failures = [], []
    with tempfile.TemporaryDirectory(prefix='mira-images-') as temp:
        bundle = Path(temp) / 'images.zip'
        with ZipFile(bundle, 'w', compression=ZIP_STORED) as archive:
            for order, (node, index) in enumerate(selections, 1):
                file = Path(temp) / f'{order}.media'
                args = {'project_id': options.project_id, 'node_id': node['id'], 'output': str(file)}
                if index is not None:
                    args['result_index'] = index
                try:
                    receipt = call('node-download', args)
                    suffix = Path(receipt.get('filename') or '').suffix.lower()
                    if suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.heic', '.heif'}:
                        raise ValueError('节点下载结果不是支持的图片文件')
                    title = (node.get('metadata') or {}).get('title') or node['id']
                    name = f'{order:03d}-{safe_name(title)}{suffix}'
                    archive.write(file, name)
                    with file.open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    manifest.append({'nodeId': node['id'], 'resultIndex': index, 'file': name,
                                     'bytes': file.stat().st_size, 'sha256': digest})
                except (ValueError, OSError, jobs.httpx.HTTPError) as exc:
                    failures.append({'nodeId': node['id'], 'resultIndex': index, 'error': str(exc)})
                finally:
                    file.unlink(missing_ok=True)
            archive.writestr('manifest.json', json.dumps({'projectId': options.project_id, 'images': manifest}, ensure_ascii=False, indent=2))
        if failures:
            return {'exitCode': 4, 'downloaded': len(manifest), 'failures': failures,
                    'saved': False, 'message': '部分图片下载失败，未保存残缺 ZIP；修复后重试'}
        publish_file(bundle, destination)
    return {'exitCode': 0, 'output': str(destination), 'imageCount': len(manifest), 'manifest': 'manifest.json'}


def trim_file(source, output, start, end):
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise ValueError('音频裁剪需要本机安装 ffmpeg 和 ffprobe，并加入 PATH')
    try:
        probe = subprocess.run([ffprobe, '-v', 'error', '-show_entries', 'format=duration:stream=codec_type',
                                '-of', 'json', str(source)], capture_output=True, text=True, check=True, timeout=30)
        info = json.loads(probe.stdout)
        duration = float(info['format']['duration'])
        if not any(s.get('codec_type') == 'audio' for s in info.get('streams', [])):
            raise ValueError('素材没有音轨')
        if not math.isfinite(duration) or end > duration + 0.001:
            raise ValueError(f'裁剪终点超过源音频时长（{duration:.3f} 秒）')
        subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-i', str(source), '-ss', str(start),
                        '-t', str(end-start), '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', '-n', str(output)],
                       capture_output=True, check=True, timeout=180)
    except (subprocess.SubprocessError, KeyError, json.JSONDecodeError) as exc:
        raise ValueError('音频解析或裁剪失败，请检查源文件；没有提交生成任务') from exc


def trim_audio(options, base, call):
    UUID(options.project_id)
    if not all(math.isfinite(x) for x in (options.start, options.end)) or options.start < 0 or options.end - options.start < .05:
        raise ValueError('起点必须非负，起止时间必须有限，至少保留 0.05 秒')
    path = options.run_file.expanduser().resolve()
    if path.suffix.lower() != '.json':
        raise ValueError('run-file 必须使用 .json 扩展名，试听文件使用同名 .wav')
    args = {'projectId': options.project_id, 'sourceNodeId': options.node_id, 'start': options.start, 'end': options.end}
    with jobs.locked(path):
        state = jobs.read_json(path) if path.exists() else None
        if state and (state.get('format') != 'mira-audio-trim-v1' or state.get('origin') != base or state.get('arguments') != args):
            raise ValueError('接续记录与当前站点、节点或裁剪区间不同，请使用新的记录文件')
        canvas = call('canvas-get', {'project_id': options.project_id})
        source, _ = jobs.target_node(canvas, options.node_id, 'audio')
        source_url = (source.get('metadata') or {}).get('src') or (source.get('metadata') or {}).get('resultUrl')
        if not source_url:
            raise ValueError('源音频节点没有可裁剪结果')
        if state is None:
            state = {'format': 'mira-audio-trim-v1', 'origin': base, 'arguments': args,
                     'sourceUrl': jobs.normalized(source_url), 'nodeId': 'audio-trim-'+uuid4().hex,
                     'operationId': 'cli-audio-trim-'+uuid4().hex, 'phase': 'prepared'}
            jobs.save(path, state)
        if state['phase'] == 'attached':
            target, _ = jobs.target_node(canvas, state['nodeId'], 'audio')
            if (target.get('metadata') or {}).get('cliTrimOperationId') != state['operationId']:
                raise ValueError('已完成的裁剪结果被修改，请核对画布')
            return {**state, 'runFile': str(path), 'exitCode': 0}
        if jobs.normalized(source_url) != state['sourceUrl']:
            raise ValueError('源音频已更换，请使用新的裁剪记录，避免处理过期素材')
        output = path.with_suffix('.wav')
        if not state.get('localFile') and not state.get('uploaded'):
            if output.exists():
                raise ValueError(f'本地试听文件已存在，请选择新的 run-file：{output}')
            with tempfile.TemporaryDirectory(prefix='mira-trim-') as temp:
                src, clip = Path(temp)/'source.media', Path(temp)/'clip.wav'
                call('node-download', {'project_id': options.project_id, 'node_id': options.node_id, 'output': str(src)})
                trim_file(src, clip, options.start, options.end)
                publish_file(clip, output)
            state.update(localFile=str(output), phase='preview_ready')
            jobs.save(path, state)
        if options.preview_only:
            return {**state, 'runFile': str(path), 'exitCode': 0, 'canvasChanged': False}
        if not state.get('uploaded'):
            if not output.is_file():
                raise ValueError('试听文件已移动或删除，请恢复文件后接续')
            if output.stat().st_size > 15 * 1024**2:
                raise ValueError('裁剪 WAV 超过画布音频上传的 15 MB 限制，请缩短区间；试听文件已保留')
            uploaded = call('asset-upload', {'file': str(output)})
            if not uploaded.get('url'):
                raise ValueError('音频上传未返回地址，请检查响应')
            state.update(uploaded=uploaded, phase='uploaded')
            jobs.save(path, state)
        for attempt in range(3):
            canvas = call('canvas-get', {'project_id': options.project_id})
            source, version = jobs.target_node(canvas, options.node_id, 'audio')
            existing = next((n for n in document(canvas).get('nodes', []) if n.get('id') == state['nodeId']), None)
            if existing:
                if (existing.get('metadata') or {}).get('cliTrimOperationId') != state['operationId']:
                    raise ValueError('结果节点 ID 冲突，未覆盖现有节点')
                state['phase'] = 'attached'; jobs.save(path, state)
                return {**state, 'runFile': str(path), 'exitCode': 0}
            meta = source.get('metadata') or {}
            if jobs.normalized(meta.get('src') or meta.get('resultUrl')) != state['sourceUrl']:
                raise ValueError('源音频已更换；上传结果和记录已保留，未修改画布')
            uploaded = state['uploaded']; url = uploaded['url']
            node = {'id': state['nodeId'], 'type': 'audio',
                    'x': source.get('x', 0) + source.get('width', 640) + 80, 'y': source.get('y', 0),
                    'width': source.get('width', 640), 'height': source.get('height', 138),
                    'metadata': {'title': str(meta.get('title') or '音频')+'-裁剪', 'src': url, 'resultUrl': url,
                                 'resultUrls': [url], 'activeResultIndex': 0, 'fileKey': uploaded.get('fileKey'),
                                 'mimeType': 'audio/wav', 'duration': options.end-options.start,
                                 'audioDuration': options.end-options.start, 'audioTrimStart': options.start,
                                 'audioTrimEnd': options.end, 'actionType': 'audio-trim',
                                 'sourceNodeId': options.node_id, 'cliTrimOperationId': state['operationId'],
                                 'status': 'succeeded', 'taskStatus': 'completed', 'progress': 100}}
            actions = [{'type': 'add_node', 'payload': {'node': node}},
                       {'type': 'add_connection', 'payload': {'connection': {'id': state['nodeId']+'-source',
                        'sourceId': options.node_id, 'targetId': state['nodeId']}}}]
            try:
                call('canvas-apply', {'project_id': options.project_id, 'baseVersion': version,
                                     'operationId': state['operationId'], 'actions': actions})
            except jobs.ERRORS as exc:
                if getattr(exc, 'status_code', None) == 409 and attempt < 2:
                    continue
                raise
            verified = call('canvas-get', {'project_id': options.project_id})
            target, _ = jobs.target_node(verified, state['nodeId'], 'audio')
            if (target.get('metadata') or {}).get('cliTrimOperationId') != state['operationId']:
                raise ValueError('裁剪回填未通过验证，接续时不会重新生成')
            state['phase'] = 'attached'; jobs.save(path, state)
            return {**state, 'runFile': str(path), 'exitCode': 0}


def execute(options, base, call):
    if options.operation == 'images-download':
        return download_images(options, call)
    return trim_audio(options, base, call)
