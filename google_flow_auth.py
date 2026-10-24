"""Google Flow authentication for app-owned browser contexts.

No Qt dependency. Callers own the profile lock. Secrets never enter diagnostics.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import threading
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

FLOW_HOME = 'https://flow.google.com/'
FLOW_HOSTS = {'flow.google.com', 'labs.google'}
SESSION_URLS = (
    'https://flow.google.com/api/auth/session',
    'https://flow.google.com/fx/api/auth/session',
    'https://labs.google/fx/api/auth/session',
)

class ProfileLock:
    """One-byte Windows lock, bounded across both local and OS lock waits."""
    def __init__(self, local_lock, lock_path):
        self._local_lock, self._lock_path = local_lock, Path(lock_path)
        self._handle = None
        self.last_error = ''

    def acquire(self, timeout=None):
        import msvcrt
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        if not self._local_lock.acquire(timeout=-1 if timeout is None else max(0, timeout)):
            return False
        handle = None
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self._lock_path.open('a+b')
            # Do not READ byte zero to test the size: another process may hold
            # that byte locked. Seek-to-end works without touching locked data.
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b'0')
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    self._handle = handle
                    return True
                except OSError:
                    if deadline is not None and time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
        except Exception as exc:
            self.last_error = type(exc).__name__
        if handle:
            handle.close()
        self._local_lock.release()
        return False

    def release(self):
        import msvcrt
        try:
            if self._handle:
                try:
                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                finally:
                    self._handle.close()
                    self._handle = None
        finally:
            self._local_lock.release()

    def __enter__(self):
        if not self.acquire(timeout=120):
            raise TimeoutError('profile_busy')
        return self

    def __exit__(self, *args):
        self.release()


_refresh_guard = threading.Lock()
_refresh_locks = {}
_refresh_results = {}


def single_refresh(root, profile, action):
    """Share a refresh only with callers already waiting when it completed."""
    key = os.path.normcase(str(profile_paths(root, profile)[0]))
    started = time.perf_counter_ns()
    with _refresh_guard:
        lock = _refresh_locks.setdefault(key, threading.Lock())
    with lock:
        previous = _refresh_results.get(key)
        if previous and previous[0] >= started:
            return previous[1]
        result = action()
        _refresh_results[key] = (time.perf_counter_ns(), result)
        return result


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def read_store_json(path, default):
    """Missing is normal; corrupt/unreadable existing data must never be reset."""
    path = Path(path)
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return default
    except (OSError, ValueError) as exc:
        raise ValueError('storage_unreadable: ' + path.name) from exc


def token_result_is_current(root, profile, token):
    metadata = read_json(Path(root) / 'session_metadata' / (profile + '.json'), {}) or {}
    fingerprint = metadata.get('token_fingerprint')
    return not fingerprint or fingerprint == hashlib.sha256(token.encode()).hexdigest()


def account_key(row):
    return ('profile', row['profile_name']) if row.get('profile_name') else ('name', row.get('name', ''))


def merge_account_rows(rows, disk, loaded_keys):
    """Keep new external rows and newer auth, but honor explicit local deletion."""
    if not isinstance(disk, list) or not all(isinstance(r, dict) for r in disk):
        raise ValueError('storage_invalid: accounts.json')
    by_key = {account_key(r): r for r in disk}
    present = {account_key(r) for r in rows}
    for row in rows:
        newer = by_key.get(account_key(row))
        if newer and newer.get('auth_revision', 0) > row.get('auth_revision', 0):
            if row.get('auth_state') == 'profile_known':
                row['active'] = newer.get('active', False)
            for key in ('api_key', 'google_email', 'auth_state', 'auth_revision', 'name', 'proxy_url'):
                if key in newer:
                    row[key] = newer[key]
    rows.extend(dict(r) for r in disk if account_key(r) not in loaded_keys and account_key(r) not in present)
    return rows


def profile_paths(root, name):
    root = Path(root).resolve()
    name = str(name or '').strip()
    if not name or name in ('.', '..') or re.search(r'[\\/:*?"<>|\x00-\x1f]', name) or name.endswith(('.', ' ')) or re.match(r'^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', name, re.I):
        raise ValueError('profile_invalid: tên profile không hợp lệ')
    settings = read_json(root / 'browser_settings' / (name + '.json'), {}) or {}
    folder = 'native_profiles' if settings.get('login_mode') == 'system_chrome' else 'profiles'
    profile = (root / folder / name).resolve()
    if profile.parent != (root / folder).resolve():
        raise ValueError('profile_invalid: đường dẫn nằm ngoài kho tài khoản')
    return profile, root / 'cookies' / (name + '.json')


def session_values(payload):
    """Only OAuth access-token fields; never ID token, CSRF or generic token."""
    if not isinstance(payload, dict):
        return '', ''
    token, email = '', ''
    pending = [payload]
    for _ in range(12):
        if not pending:
            break
        item = pending.pop(0)
        if not isinstance(item, dict):
            continue
        value = item.get('access_token') or item.get('accessToken')
        if isinstance(value, str) and len(value.strip()) > 20:
            token = token or value.strip()
        value = item.get('email')
        if isinstance(value, str) and '@' in value:
            email = email or value.strip()
        pending.extend(item.get(key) for key in ('session', 'data', 'user') if isinstance(item.get(key), dict))
    return token, email


def identity_matches(expected, actual):
    expected, actual = str(expected or '').strip().lower(), str(actual or '').strip().lower()
    # A nickname is not an email. Never compare only the local part of emails.
    return '@' in actual and ('@' not in expected or expected == actual)


def check_token(token, client_factory):
    if not isinstance(token, str) or len(token.strip()) < 20:
        return False, 'token_missing'
    try:
        with client_factory(timeout=10, target_url='https://www.googleapis.com/oauth2/v3/userinfo', use_proxy=True) as client:
            response = client.get('https://www.googleapis.com/oauth2/v3/userinfo', headers={'Authorization': 'Bearer ' + token})
        if response.status_code == 200:
            payload = response.json()
            email = payload.get('email', '')
            if not isinstance(email, str) or '@' not in email:
                return False, 'identity_missing: userinfo chưa trả email'
            if payload.get('email_verified') is False:
                return False, 'identity_unverified'
            return True, email
        code = {401: 'token_rejected', 403: 'permission_denied', 429: 'rate_limited'}.get(response.status_code, 'service_error')
        return False, f'{code}: userinfo HTTP {response.status_code}'
    except Exception as exc:
        return False, f'network_error: {type(exc).__name__}'


def transient_auth_error(info):
    return str(info).startswith(('network_error', 'service_error', 'rate_limited', 'permission_denied', 'identity_missing'))


def expected_email(root, profile):
    rows = read_json(Path(root) / 'accounts.json', [])
    for row in rows if isinstance(rows, list) else []:
        if row.get('profile_name') == profile:
            email = row.get('google_email') or str(row.get('name', '')).removeprefix('Google_')
            if '@' in email:
                return email
    return profile if '@' in profile else ''


def flow_pages(context):
    result = []
    for page in list(context.pages):
        try:
            if not page.is_closed() and urlsplit(page.url).hostname in FLOW_HOSTS:
                result.append(page)
        except Exception:
            pass
    return result


class SessionObserver:
    """Observe only Flow/aisandbox traffic in the explicitly selected context."""
    def __init__(self, context):
        self.context = context
        self.candidates = []
        self.diagnostic = 'flow_session_missing'
        context.on('request', self.on_request)
        context.on('response', self.on_response)

    def remember(self, token, email, source):
        if token and not any(item[0] == token for item in self.candidates):
            self.candidates.append((token, email, source))
            self.candidates = self.candidates[-8:]

    def on_request(self, request):
        try:
            url = urlsplit(request.url)
            if url.scheme != 'https' or url.hostname != 'aisandbox-pa.googleapis.com':
                return
            if urlsplit(request.frame.url).hostname not in FLOW_HOSTS:
                return
            value = request.header_value('authorization') or ''
            if value.startswith('Bearer ya29.'):
                self.remember(value[7:].strip(), '', 'flow-api-request')
        except Exception:
            pass

    def on_response(self, response):
        try:
            url = urlsplit(response.url)
            if url.scheme != 'https' or url.hostname not in FLOW_HOSTS or url.path not in ('/api/auth/session', '/fx/api/auth/session'):
                return
            self.consume(response, url.hostname)
        except Exception:
            pass

    def consume(self, response, host):
        if response.status != 200:
            self.diagnostic = f'session_http: {host} HTTP {response.status}'
            return
        if 'json' not in response.headers.get('content-type', ''):
            self.diagnostic = f'session_html: {host} trả HTML, chưa có JSON session'
            return
        payload = response.json()
        token, email = session_values(payload)
        self.remember(token, email, 'flow-session-json')
        if token:
            self.diagnostic = 'token_received'
        elif email:
            self.diagnostic = 'token_missing: session có email, chưa có access token'
        else:
            self.diagnostic = 'flow_session_missing: session rỗng hoặc schema chưa hỗ trợ'

    def probe(self, deadline):
        # APIRequestContext shares the browser cookie jar and handles Set-Cookie.
        # No cross-origin JS fetch or navigation away from the user's login tab.
        for endpoint in SESSION_URLS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                response = self.context.request.get(endpoint, timeout=min(3000, max(1, int(remaining * 1000))))
                try:
                    self.consume(response, urlsplit(endpoint).hostname)
                finally:
                    response.dispose()
            except Exception as exc:
                self.diagnostic = f'session_network: {type(exc).__name__}'


def probe_session(context, page=None, allow_navigation=False):
    observer = SessionObserver(context)
    try:
        observer.probe(time.monotonic() + 9)
        if observer.candidates:
            return observer.candidates[-1]
        return '', '', observer.diagnostic
    finally:
        context.remove_listener('request', observer.on_request)
        context.remove_listener('response', observer.on_response)


def project_from_context(context):
    for page in reversed(flow_pages(context)):
        url = urlsplit(page.url)
        value = parse_qs(url.query).get('project', [''])[0]
        match = re.search(r'/project/([0-9a-fA-F-]{36})(?:/|$)', url.path)
        if value or match:
            return value or match.group(1)
    return ''


def launch_options(root, name, headless):
    """Persist the browser choice, use it identically for login and refresh."""
    config = Path(root) / 'browser_settings' / (name + '.json')
    settings = read_json(config, {}) or {}
    # Existing profiles were created with bundled Chromium; do not silently
    # migrate their cookie encryption to another executable/channel.
    options = {'headless': headless, 'timeout': 20000}
    channel = settings.get('channel', 'chromium')
    if channel in ('chrome', 'msedge'):
        options['channel'] = channel
    try:
        from proxy_manager import _global_proxy_manager
        if _global_proxy_manager:
            server = _global_proxy_manager.get_chrome_proxy_arg()
            if server:
                options['proxy'] = {'server': server}
    except ImportError:
        pass
    if not config.exists():
        atomic_json(config, {'channel': channel})
    return options


def find_system_chrome():
    """Use installed Chrome, not the browser bundled for automated tests."""
    for env, suffix in (('PROGRAMFILES', 'Google/Chrome/Application/chrome.exe'),
                        ('PROGRAMFILES(X86)', 'Google/Chrome/Application/chrome.exe'),
                        ('LOCALAPPDATA', 'Google/Chrome/Application/chrome.exe')):
        base = os.environ.get(env)
        if base:
            candidate = Path(base) / suffix
            if candidate.is_file():
                return str(candidate)
    raise FileNotFoundError('chrome_missing: cần cài Google Chrome trước khi đăng nhập')


def native_login_command(executable, profile_dir):
    # No remote debugging, automation, UA spoofing or security-disabling flags.
    return [executable, '--user-data-dir=' + str(profile_dir), '--no-first-run',
            '--no-default-browser-check', '--restore-last-session', FLOW_HOME]


def manual_chrome_login(root, profile, cancelled, status, deadline):
    """The human owns sign-in. Never attach automation to the login window."""
    if cancelled():
        return 'cancelled: đã dừng trước khi mở Chrome'
    profile_paths(root, profile)  # validate before constructing settings paths
    executable = find_system_chrome()
    config = Path(root) / 'browser_settings' / (profile + '.json')
    previous = read_store_json(config, {})
    if not isinstance(previous, dict):
        raise ValueError('storage_invalid: browser_settings')
    # Preserve old Chromium data/snapshot. Chrome owns a separate directory;
    # never copy a running browser database or silently change its encryption.
    settings = dict(previous, channel='chrome', login_mode='system_chrome')
    atomic_json(config, settings)
    directory, _ = profile_paths(root, profile)
    directory.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(native_login_command(executable, directory),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    status('Chrome thường đã mở. Đăng nhập và vào Flow; sau đó ĐÓNG cửa sổ Chrome này để tool kiểm tra phiên. Không cần đóng Chrome khác.')
    while process.poll() is None:
        if cancelled():
            return 'cancelled: đã dừng chờ; cửa sổ Chrome và dữ liệu được giữ nguyên'
        if time.monotonic() >= deadline:
            return 'manual_login_timeout: chưa hoàn tất; cửa sổ Chrome và dữ liệu vẫn được giữ nguyên'
        time.sleep(.2)
    if cancelled():
        return 'cancelled: đã dừng chờ'
    status('Đang kiểm tra phiên Flow. Nếu Chrome profile này vẫn mở, hãy đóng riêng cửa sổ đó; tool không tự tắt Chrome của bạn.')
    return ''


def is_google_signin_rejected(pages):
    for page in pages:
        url = urlsplit(page.url)
        if url.hostname == 'accounts.google.com' and ('/signin/rejected' in url.path or '/deniedsigninrejected' in url.path):
            return True
    return False


def has_signed_in_flow_ui(context):
    """UI evidence only: not OAuth identity verification or API readiness."""
    for page in flow_pages(context):
        try:
            if urlsplit(page.url).hostname != 'flow.google.com':
                continue
            signout = page.locator('a[href*="accounts.google.com/SignOutOptions"]').count() > 0
            projects = page.locator('a[href*="/project/"]').count() > 0
            if signout and (projects or '/project/' in urlsplit(page.url).path):
                return True
        except Exception:
            pass
    return False


@dataclass(repr=False)
class AuthResult:
    ok: bool = False
    token: str = ''
    email: str = ''
    project: str = ''
    error: str = ''


def auth_attempt_text(root, profile, has_token=False):
    if not str(profile or '').strip():
        return 'Chưa liên kết với profile Chrome riêng'
    profile_paths(root, profile)
    attempt = read_json(Path(root) / 'auth_attempts' / (profile + '.json'), {}) or {}
    phase = attempt.get('phase', '')
    if phase in ('starting', 'awaiting_manual_close', 'checking_flow') and time.time() - attempt.get('updated_at', 0) > 720:
        return 'Lần kiểm tra trước đã gián đoạn; bấm kiểm tra lại'
    labels = {
        'starting': 'Đang chuẩn bị kiểm tra phiên',
        'awaiting_manual_close': 'Chờ bạn vào Flow rồi đóng cửa sổ Chrome riêng',
        'checking_flow': 'Đang kiểm tra phiên Flow đã lưu',
        'google_signin_rejected': 'Google từ chối trình duyệt — dùng Chrome thường',
        'flow_token_unavailable': 'Chưa lấy được Bearer Google; profile vẫn được giữ — cần kiểm tra luồng lấy token',
        'flow_transport_required': 'Phiên Flow còn sau khi mở lại; chưa lấy được Bearer Google, không cần login lại',
        'manual_login_timeout': 'Hết thời gian chờ đóng Chrome; profile vẫn được giữ',
        'profile_busy': 'Profile đang mở; đóng riêng cửa sổ profile trước khi kiểm tra',
        'cancelled': 'Đã dừng kiểm tra; không xóa profile',
        'storage_write_failed': 'Lỗi lưu phiên — chưa hoàn tất',
        'verified': 'Đã xác minh token' if has_token else 'Đã xác minh phiên; chưa thấy token được lưu vào tài khoản',
    }
    return labels.get(phase, 'Đã có token lưu; cần kiểm tra hiệu lực' if has_token else 'Profile có sẵn / chưa có token được xác minh')


def acquire_session(root, profile, validator, persist, *, interactive=False,
                    use_snapshot=False, expected='', cancelled=lambda: False,
                    status=lambda message: None, timeout=None):
    """Keep non-secret phase diagnostics, without treating browser login as ready."""
    profile_paths(root, profile)
    path = Path(root) / 'auth_attempts' / (profile + '.json')
    attempt_id = uuid.uuid4().hex
    def record(phase, initial=False):
        try:
            previous = read_json(path, {}) or {}
            if not initial and previous.get('attempt_id') != attempt_id:
                return
            atomic_json(path, {'attempt_id': attempt_id, 'phase': phase, 'updated_at': time.time()})
        except (OSError, ValueError):
            pass  # Diagnostics cannot replace the actual persistence result.
    record('starting', initial=True)
    def report(message):
        record('awaiting_manual_close' if message.startswith('Chrome thường đã mở') else 'checking_flow')
        status(message)
    try:
        result = _acquire_session_impl(root, profile, validator, persist, interactive=interactive,
            use_snapshot=use_snapshot, expected=expected, cancelled=cancelled, status=report, timeout=timeout)
    except Exception:
        record('auth_error')
        raise
    known = {'google_signin_rejected', 'flow_token_unavailable', 'flow_transport_required', 'manual_login_timeout', 'profile_busy',
             'cancelled', 'storage_write_failed', 'browser_missing', 'browser_error', 'native_browser_error',
             'profile_missing', 'timeout', 'snapshot_not_verified', 'browser_closed'}
    code = result.error.split(':', 1)[0]
    record('verified' if result.ok else code if code in known else 'auth_error')
    return result


def _acquire_session_impl(root, profile, validator, persist, *, interactive=False,
                    use_snapshot=False, expected='', cancelled=lambda: False,
                    status=lambda message: None, timeout=None):
    """Run entirely on the calling worker thread, under its profile lock."""
    from playwright.sync_api import sync_playwright
    deadline = time.monotonic() + (timeout if timeout is not None else (600 if interactive else 55))
    if interactive:
        try:
            error = manual_chrome_login(root, profile, cancelled, status, deadline)
        except Exception as exc:
            return AuthResult(error='native_browser_error: ' + type(exc).__name__ + '; kiểm tra Chrome cài trên máy và quyền ghi profile')
        if error:
            return AuthResult(error=error)
        # Automated work starts only AFTER the human-controlled browser exits.
        interactive = False
        # A human finishing near the login deadline still gets a full probe
        # window. Explicit test/caller timeouts remain end-to-end bounded.
        deadline = time.monotonic() + 55 if timeout is None else min(deadline, time.monotonic() + 55)
    profile_dir, snapshot = profile_paths(root, profile)
    settings = read_json(Path(root) / 'browser_settings' / (profile + '.json'), {}) or {}
    native = settings.get('login_mode') == 'system_chrome'
    if use_snapshot and native:
        metadata = read_json(Path(root) / 'session_metadata' / (profile + '.json'), {}) or {}
        if metadata.get('browser_mode') != 'system_chrome':
            return AuthResult(error='snapshot_not_verified: không dùng snapshot Chromium cũ cho Chrome mới')
    if not interactive and not (snapshot.is_file() if use_snapshot else profile_dir.is_dir()):
        return AuthResult(error='profile_missing: chưa có dữ liệu phiên cho profile này')
    if interactive:
        profile_dir.mkdir(parents=True, exist_ok=True)
    context, browser = None, None
    last_diagnostic = 'flow_session_missing'
    try:
        with sync_playwright() as pw:
            try:
                options = launch_options(root, profile, not interactive)
                if use_snapshot:
                    browser = pw.chromium.launch(**options)
                    context = browser.new_context(storage_state=str(snapshot))
                else:
                    context = pw.chromium.launch_persistent_context(str(profile_dir), **options)
                context.set_default_timeout(3000)
                observer = SessionObserver(context)
                status('Đã mở đúng profile; đang chờ phiên Google Flow.')
                page = flow_pages(context)[-1] if flow_pages(context) else context.new_page()
                if urlsplit(page.url).hostname not in FLOW_HOSTS:
                    try:
                        page.goto(FLOW_HOME, wait_until='domcontentloaded', timeout=min(20000, max(1, int((deadline-time.monotonic())*1000))))
                    except Exception:
                        pass
                checked = {}
                next_probe, next_status = 0.0, 0.0
                while time.monotonic() < deadline and not cancelled():
                    live = [p for p in context.pages if not p.is_closed()]
                    if is_google_signin_rejected(live):
                        return AuthResult(error='google_signin_rejected: Google chặn trình duyệt tự động. Đăng nhập bằng Chrome thường từ nút khóa; không lặp lại đăng nhập trong browser kiểm thử.')
                    if not live:
                        return AuthResult(error='browser_closed: đã đóng cửa sổ trước khi nhận được token Flow')
                    now = time.monotonic()
                    if now >= next_probe:
                        observer.probe(min(deadline, now + 8))
                        next_probe = time.monotonic() + 8
                    for token, session_email, source in reversed(observer.candidates):
                        if cancelled() or time.monotonic() >= deadline:
                            break
                        if token in checked and time.monotonic() - checked[token] < 15:
                            continue
                        checked[token] = time.monotonic()
                        valid, info = validator(token)
                        if not valid:
                            last_diagnostic = info
                            continue
                        if not identity_matches(expected, info) or (session_email and not identity_matches(session_email, info)):
                            last_diagnostic = 'wrong_account: email token khác tài khoản/profile đã chọn'
                            continue
                        if not persist(context, snapshot):
                            return AuthResult(error='storage_write_failed: không ghi được snapshot phiên đã xác minh')
                        project = project_from_context(context)
                        atomic_json(Path(root) / 'session_metadata' / (profile + '.json'), {
                            'profile': profile, 'email': info, 'project': project,
                            'verified_at': time.time(), 'token_source': source,
                            'browser_mode': 'system_chrome' if native else 'chromium',
                            'token_fingerprint': hashlib.sha256(token.encode()).hexdigest(),
                        })
                        return AuthResult(True, token, info, project)
                    if time.monotonic() >= next_status:
                        pages = flow_pages(context)
                        about = any(urlsplit(p.url).path.startswith('/about') for p in pages)
                        diagnostic = last_diagnostic if checked else observer.diagnostic
                        if about and not observer.candidates:
                            diagnostic = 'flow_session_missing: đang ở trang giới thiệu; bấm đăng nhập/Start trong cửa sổ Flow này'
                        status(diagnostic)
                        next_status = time.monotonic() + 12
                    # Pump Playwright events (plain time.sleep starves request callbacks).
                    live[-1].wait_for_timeout(400)
                if cancelled():
                    return AuthResult(error='cancelled: đã dừng; profile được giữ lại')
                if native and not checked:
                    if has_signed_in_flow_ui(context):
                        return AuthResult(error='flow_transport_required: đã quan sát phiên Flow đăng nhập và dự án sau khi mở lại profile; chưa nhận được Bearer Google. Cần kiểm tra luồng lấy Bearer riêng; token reCAPTCHA của Captcha Bridge không thay thế Bearer. Không đăng nhập lại; chưa xác minh email OAuth hoặc khả năng tạo video.')
                    return AuthResult(error='flow_token_unavailable: chưa nhận được Bearer token Flow. Profile vẫn được giữ. Cần kiểm tra luồng lấy Bearer của tài khoản; trạng thái này không chứng minh đã logout và không yêu cầu đổi extension.')
                return AuthResult(error='timeout: ' + (last_diagnostic if checked else observer.diagnostic))
            finally:
                if context:
                    try:
                        context.close()
                    except Exception:
                        pass
                if browser:
                    try:
                        browser.close()
                    except Exception:
                        pass
    except Exception as exc:
        message = str(exc).lower()
        if any(s in message for s in ('processsingleton', 'singletonlock', 'profile is in use', 'user data directory is already in use')):
            return AuthResult(error='profile_busy: profile đang mở ở tác vụ khác')
        if 'executable doesn' in message or 'playwright install' in message:
            return AuthResult(error='browser_missing: chưa cài browser cho Python đang chạy tool')
        return AuthResult(error=f'browser_error: {type(exc).__name__}; profile và cookies được giữ lại')


def discover_profiles(root):
    root = Path(root)
    return sorted({p.name for name in ('profiles', 'native_profiles') for folder in [root / name]
                   if folder.exists() for p in folder.iterdir() if p.is_dir()})


def pending_record(root, profile):
    profile_paths(root, profile)
    return {
        'name': 'Google_' + profile, 'profile_name': profile,
        'proxy_url': '', 'api_key': '', 'active': False,
        'auth_state': 'profile_known', 'google_email': '',
    }


def recover_login_commit(root):
    """Finish config/registry writes after an account commit interrupted by exit.

    The journal carries a fingerprint, never another plaintext token copy.
    Callers serialize the operation with the account-store lock.
    """
    root = Path(root)
    journal = read_store_json(root / 'login_commit.json', {}) or {}
    if not journal:
        return
    rows = read_store_json(root / 'accounts.json', [])
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError('storage_invalid: accounts.json')
    row = next((r for r in rows if r.get('profile_name') == journal.get('profile')), None)
    if not row or hashlib.sha256(str(row.get('api_key', '')).encode()).hexdigest() != journal.get('fingerprint'):
        return
    profile = row['profile_name']
    directory, snapshot = profile_paths(root, profile)
    registry = read_store_json(root / 'Flow_nhieu_worker_accounts.json', [])
    if isinstance(registry, dict):
        registry = next((registry[k] for k in ('accounts','items','data') if isinstance(registry.get(k), list)), [registry])
    if not isinstance(registry, list):
        raise ValueError('storage_invalid: registry không phải danh sách')
    entry = next((r for r in registry if isinstance(r, dict) and
        (r.get('email') == row.get('google_email') or Path(r.get('profile_dir', '')).name == profile)), None)
    if entry is None:
        entry = {}
        registry.append(entry)
    entry.update(email=row['google_email'], profile_dir=str(directory), cookies_file=str(snapshot),
                 project_id=row.get('proxy_url', ''), status='ready')
    atomic_json(root / 'Flow_nhieu_worker_accounts.json', registry)
    config = read_store_json(root / 'veo3_config.json', {})
    if not isinstance(config, dict):
        raise ValueError('storage_invalid: veo3_config.json')
    if not journal.get('refresh') or config.get('flow_profile_name') in (None, '', profile):
        config.update(bearer_token=row['api_key'], flow_profile_name=profile, project_id=row.get('proxy_url', ''))
    atomic_json(root / 'veo3_config.json', config)
    (root / 'login_commit.json').unlink(missing_ok=True)


_commit_lock = threading.Lock()

def commit_login(root, profile, email, token, project='', hint='', *, refresh=False):
    if not identity_matches('', email):
        raise ValueError('identity_missing')
    root = Path(root).resolve()
    profile_paths(root, profile)
    lock = ProfileLock(_commit_lock, root / 'locks' / 'account_commit.lock')
    with lock:
        recover_login_commit(root)
        rows = read_store_json(root / 'accounts.json', [])
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError('storage_invalid: accounts.json không phải danh sách')
        row = next((r for r in rows if r.get('profile_name') == profile), None)
        if row is None:
            row = next((r for r in rows if r.get('name') == hint or r.get('name') == 'Google_' + email), None)
        if row is None:
            if refresh:
                raise ValueError('account_missing: tài khoản đã bị xóa; không tạo lại từ refresh cũ')
            row = pending_record(root, profile)
            rows.append(row)
        if refresh and (not token_result_is_current(root, profile, token) or not identity_matches(row.get('google_email', ''), email)):
            raise ValueError('stale_or_wrong_account: bỏ kết quả refresh cũ')
        row.update(name=row.get('name', 'Google_' + email) if refresh else 'Google_' + email, google_email=email, api_key=token,
                   proxy_url=project or row.get('proxy_url', ''), profile_name=profile,
                   auth_state='ready', active=row.get('active', True) if refresh else True, auth_revision=time.time_ns())
        atomic_json(root / 'login_commit.json', {
            'profile': profile, 'fingerprint': hashlib.sha256(token.encode()).hexdigest(), 'refresh': refresh})
        atomic_json(root / 'accounts.json', rows)
        recover_login_commit(root)
        return dict(row)
