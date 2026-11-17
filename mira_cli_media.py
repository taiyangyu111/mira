"""Probe an existing local media file and compare explicit technical expectations."""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def frame_rate(stream):
    for key in ('avg_frame_rate', 'r_frame_rate'):
        try:
            value = float(Fraction(stream.get(key, '0/0')))
            if math.isfinite(value) and value > 0:
                return value
        except (ValueError, ZeroDivisionError, TypeError):
            pass
    return None


def analyze(data, expected, duration_tolerance=1, fps_tolerance=0.1):
    videos = [s for s in data.get('streams', []) if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic')]
    audio = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    video = next((v for v in videos if (v.get('disposition') or {}).get('default')), videos[0] if videos else {})
    duration = numeric((data.get('format') or {}).get('duration'))
    if duration is None:
        duration = numeric(video.get('duration'))
    actual = {'width': video.get('width'), 'height': video.get('height'), 'duration': duration,
              'fps': frame_rate(video), 'hasAudio': bool(audio), 'videoCodec': video.get('codec_name'),
              'audioTracks': [{k: s.get(k) for k in ('codec_name', 'channels', 'sample_rate')} for s in audio]}
    checks = []
    for field, wanted in expected.items():
        found = actual[field]
        tolerance = {'duration': duration_tolerance, 'fps': fps_tolerance}.get(field, 0)
        passed = found is not None and (abs(found - wanted) <= tolerance if field in ('duration', 'fps') else found == wanted)
        checks.append({'field': field, 'expected': wanted, 'actual': found, 'tolerance': tolerance, 'passed': passed})
    return {'actual': actual, 'checks': checks, 'passed': all(c['passed'] for c in checks) if checks else None,
            'notChecked': ['画面真实感与清晰度', '音乐是否合适或可听；有音轨不代表有声音', '全片可解码性与逐帧缺陷', '音乐授权与署名']}


def inspect(path, *, ffprobe=None, expected=None, duration_tolerance=1, fps_tolerance=0.1):
    expected = expected or {}
    for key, value in expected.items():
        if key not in ('width', 'height', 'duration', 'fps', 'hasAudio'):
            raise ValueError('未知验收字段：' + key)
        if key == 'hasAudio':
            if type(value) is not bool:
                raise ValueError('hasAudio 必须为布尔值')
        elif numeric(value) is None or value <= 0:
            raise ValueError('预期尺寸、时长和帧率必须为正数')
    if any(numeric(v) is None or v < 0 for v in (duration_tolerance, fps_tolerance)):
        raise ValueError('容差必须是非负有限数值')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('请提供已经下载到本机的媒体文件')
    executable = ffprobe or os.environ.get('MIRA_FFPROBE') or shutil.which('ffprobe')
    if not executable:
        raise ValueError('未找到 ffprobe；安装 FFmpeg 并加入 PATH，或通过 --ffprobe / MIRA_FFPROBE 指定')
    try:
        result = subprocess.run([str(executable), '-v', 'error', '-protocol_whitelist', 'file,pipe',
            '-show_entries', 'format=duration,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,duration,channels,sample_rate:stream_disposition=default,attached_pic',
            '-of', 'json', str(path)], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('ffprobe 超时，未能完成验收') from exc
    if result.returncode:
        raise ValueError('ffprobe 无法读取媒体：' + result.stderr.decode('utf-8', errors='replace')[-1000:])
    data = json.loads(result.stdout)
    if not data.get('streams'):
        raise ValueError('未发现可读取的媒体轨道')
    return {'file': str(path), 'bytes': path.stat().st_size, **analyze(data, expected, duration_tolerance, fps_tolerance)}
