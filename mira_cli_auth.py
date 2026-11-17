"""CLI browser login: device approval, then store the token in the OS keyring."""
import math
import os
import re
import sys
import time
import webbrowser
import httpx

PREFIX = '/api/mira-plugin/v1'


def login(base, service, *, scope='generate', label='Mira CLI', no_browser=False, timeout=300,
          client=None, save=None, opener=None, announce=None, sleep=time.sleep, clock=time.monotonic):
    if not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise ValueError('--timeout 必须在 1–300 秒之间')
    if scope not in {'read', 'edit', 'generate'} or not label.strip() or len(label) > 60:
        raise ValueError('登录权限或客户端名称无效')
    announce = announce or (lambda msg: print(msg, file=sys.stderr, flush=True))
    if save is None:
        import keyring
        if keyring.get_keyring().priority <= 0:
            raise ValueError('系统凭据库不可用，请先配置 keyring 后重试。')
        save = lambda token: keyring.set_password(service, base, token)
    opener = opener or webbrowser.open
    owned = client is None
    client = client or httpx.Client(timeout=20, follow_redirects=False)
    def request(path, data):
        response = client.post(base+PREFIX+path, json=data)
        if response.status_code in {404, 405}:
            raise ValueError('当前服务端尚未支持浏览器登录，请更新服务端，或暂用 auth-save。')
        if not response.is_success:
            # Never echo a server response that could contain credentials.
            if response.status_code == 429:
                raise ValueError('登录请求过于频繁，请稍后重试。')
            if response.status_code == 409:
                raise ValueError('有效令牌数量已达上限，请在 Mira 插件页撤销不用的令牌。')
            raise ValueError(f'登录服务返回 HTTP {response.status_code}，请稍后重试。')
        try:
            return response.json()
        except ValueError:
            raise ValueError('登录服务响应无效，请检查 MIRA_URL 和服务端版本。') from None
    try:
        start = request('/auth/device', {'scope': scope, 'label': label.strip()})
        code, device = start.get('user_code', ''), start.get('device_code', '')
        if not re.fullmatch(r'[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}', code) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', device):
            raise ValueError('登录服务返回的授权信息无效')
        url = base+'/cli/authorize?code='+code
        announce(f'请在浏览器确认登录，核对授权码：{code}\n{url}')
        if not no_browser:
            try:
                opened = opener(url)
            except Exception:
                opened = False
            if not opened:
                announce('未能自动打开浏览器，请手动打开上方链接。')
        deadline = clock()+min(timeout, float(start.get('expires_in', 300)))
        interval = max(3, min(10, float(start.get('interval', 3))))
        while clock() < deadline:
            result = request('/auth/poll', {'device_code': device})
            status = result.get('status')
            if status == 'authorized':
                token = result.get('token', '')
                if not re.fullmatch(r'mira_[A-Za-z0-9_-]{43}', token):
                    raise ValueError('登录凭据无效，未覆盖本机凭据。')
                checked = client.get(base+PREFIX+'/account', headers={'Authorization': 'Bearer '+token})
                if checked.status_code != 200:
                    raise ValueError('账号验证未通过，未覆盖本机凭据。请重新登录。')
                account = checked.json()
                try:
                    save(token)
                except Exception:
                    raise ValueError('无法保存到系统凭据库；原凭据未主动删除。请修复 keyring 后重新登录，可在插件页撤销刚授权的令牌。') from None
                if os.environ.get('MIRA_TOKEN'):
                    announce('注意：当前 MIRA_TOKEN 环境变量会优先于新保存的登录凭据。')
                return {'account': account['username'], 'origin': base, 'scope': result['scope'],
                        'expiresAt': result['expires_at'], 'savedTo': 'system-keyring'}
            if status in {'denied', 'expired', 'consumed'}:
                raise ValueError({'denied': '浏览器已拒绝授权。', 'expired': '授权已过期，请重新运行 login。',
                                  'consumed': '授权已领取，请重新运行 login。'}[status])
            if status not in {'pending', 'slow_down'}:
                raise ValueError('登录状态无效，请重新运行 login。')
            if status == 'slow_down':
                interval = max(interval, 5)
            sleep(min(interval, max(0, deadline-clock())))
        raise ValueError('等待登录超时，未修改本机凭据。请重新运行 login。')
    except KeyboardInterrupt:
        raise ValueError('已取消等待登录，未主动删除本机凭据。') from None
    finally:
        if owned:
            client.close()
