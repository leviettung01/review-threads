"""
tiktok_shop_crawler.py
Module tải video TikTok qua TikWm API và convert thành MP3 (voice only).
Không cần Chrome / đăng nhập TikTok.
"""
import os
import re
import json
import time
import uuid
import shutil
import subprocess
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path


# ──────────────────────────────────────────────────
# TikWm API endpoints
# ──────────────────────────────────────────────────
TIKWM_API_URL   = "https://www.tikwm.com/api/"
TIKWM_API_V2    = "https://www.tikwm.com/api/"   # same, kept for clarity
_REQUEST_TIMEOUT = 30  # seconds


def _tikwm_request(video_url: str, api_key: str = None) -> dict:
    """Gọi TikWm API để lấy thông tin video."""
    params = {"url": video_url, "hd": "1"}
    if api_key:
        params["api_key"] = api_key
    encoded = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        TIKWM_API_URL,
        data=encoded,
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except Exception as e:
        return {"code": -1, "msg": str(e), "data": None}


def _download_file(url: str, dest_path: Path, chunk_size: int = 65536) -> bool:
    """Download file từ URL về disk."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Referer": "https://www.tiktok.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
        return dest_path.stat().st_size > 1024  # at least 1KB
    except Exception:
        return False


def _find_ffmpeg() -> str:
    """Tìm ffmpeg trong PATH hoặc các thư mục thông dụng."""
    # Try system PATH first
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    # Common install locations on Windows
    candidates = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        os.path.join(os.path.dirname(__file__), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.path.dirname(__file__), "ffmpeg.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return "ffmpeg"  # fallback: hope it's in PATH


def _video_to_mp3(video_path: Path, mp3_path: Path) -> bool:
    """
    Convert video → MP3 (chỉ lấy audio / voice).
    Dùng ffmpeg: -vn (no video), -acodec mp3, -q:a 2 (high quality VBR).
    """
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-i", str(video_path),
        "-vn",                    # bỏ video track
        "-acodec", "libmp3lame",  # encode thành MP3
        "-q:a", "2",              # VBR quality 2 (~190kbps)
        "-ar", "44100",           # sample rate
        "-ac", "2",               # stereo
        str(mp3_path),
    ]
    try:
        creation_flags = 0
        try:
            creation_flags = subprocess.CREATE_NO_WINDOW
        except AttributeError:
            pass
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
            creationflags=creation_flags,
        )
        return mp3_path.exists() and mp3_path.stat().st_size > 1024
    except subprocess.TimeoutExpired:
        return False
    except FileNotFoundError:
        # ffmpeg not found
        return False
    except Exception:
        return False


def extract_video_id(url: str) -> str:
    """Trích xuất video ID từ URL TikTok."""
    # Handles: /video/1234567890, /v/1234, etc.
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{15,25})", url)
    if m:
        return m.group(1)
    return uuid.uuid4().hex[:12]


def download_video_and_voice_tikwm(
    video_url: str,
    output_dir,
    api_key: str = None,
    keep_video: bool = False,
) -> str:
    """
    Tải video TikTok qua TikWm, convert sang MP3.

    Args:
        video_url: URL video TikTok
        output_dir: Thư mục lưu output (Path hoặc str)
        api_key: TikWm API key (tuỳ chọn, dùng free tier nếu None)
        keep_video: Giữ lại file video gốc hay xóa sau khi convert

    Returns:
        Đường dẫn file MP3 nếu thành công, raise Exception nếu thất bại.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    vid_id = extract_video_id(video_url)

    # ── Bước 1: Gọi TikWm API ──
    resp = _tikwm_request(video_url, api_key)
    code = resp.get("code", -1)
    if code != 0:
        msg = resp.get("msg", "Unknown error")
        raise RuntimeError(f"TikWm API lỗi [{code}]: {msg}")

    data = resp.get("data") or {}
    
    # Lấy URL video (ưu tiên nowm = no watermark)
    play_url = (
        data.get("play")          # no-watermark (free)
        or data.get("hdplay")     # HD no-watermark
        or data.get("wmplay")     # with watermark fallback
    )
    if not play_url:
        raise RuntimeError("TikWm API không trả về URL video khả dụng.")

    # ── Bước 2: Download video ──
    video_path = output_dir / f"{vid_id}.mp4"
    if not _download_file(play_url, video_path):
        raise RuntimeError(f"Download video thất bại từ: {play_url[:80]}")

    # ── Bước 3: Convert → MP3 ──
    mp3_path = output_dir / f"{vid_id}.mp3"
    ok = _video_to_mp3(video_path, mp3_path)

    # Xóa video tạm (trừ khi keep_video=True)
    if not keep_video and video_path.exists():
        try:
            video_path.unlink()
        except Exception:
            pass

    if not ok:
        # Thử dùng audio URL trực tiếp nếu có
        music = data.get("music_info") or data.get("music") or {}
        audio_url = music.get("play") or music.get("url") or data.get("music")
        if audio_url and isinstance(audio_url, str) and audio_url.startswith("http"):
            if _download_file(audio_url, mp3_path):
                return str(mp3_path)
        raise RuntimeError(
            "FFmpeg convert thất bại. Hãy kiểm tra FFmpeg đã được cài và có trong PATH."
        )

    return str(mp3_path)


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS & PATHS  (required by grok_video_tool.py imports)
# ═══════════════════════════════════════════════════════════════

CDP_PORT    = 9222
_APP_DIR    = Path(__file__).parent
PROFILE_DIR = _APP_DIR / "TiktokShop" / "profiles" / "tiktok_chrome_profile"
DOWNLOADS_DIR = _APP_DIR / "TiktokShop" / "crawled_products"


# ═══════════════════════════════════════════════════════════════
#  CHROME / PORT UTILITIES
# ═══════════════════════════════════════════════════════════════

def is_port_in_use(port: int) -> bool:
    """Kiểm tra xem một cổng TCP có đang được dùng không."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def is_chrome_cdp_active(port: int = CDP_PORT) -> bool:
    """Kiểm tra Chrome đang chạy với CDP debugging trên port cho trước."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/version",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
            return "Browser" in data
    except Exception:
        return False


def find_chrome_path() -> str:
    """Tìm đường dẫn Chrome trên Windows."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    # Try PATH
    chrome = shutil.which("chrome") or shutil.which("google-chrome")
    return chrome or ""


def normalize_tiktok_url(url: str) -> str:
    """Chuẩn hoá URL TikTok: loại bỏ query params không cần thiết."""
    if not url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        # Keep only the path, strip tracking params
        clean = urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, parsed.path, "", "", "")
        )
        return clean.rstrip("/")
    except Exception:
        return url


# ═══════════════════════════════════════════════════════════════
#  CRAWL WORKER STUB  (real implementation uses Playwright/CDP)
#  grok_video_tool.py imports this class — keep interface intact.
# ═══════════════════════════════════════════════════════════════

try:
    from PyQt6.QtCore import QThread, pyqtSignal
    from TiktokShop.tiktok_shop_crawler import CrawlWorker
except ImportError:
    # If PyQt6 not available, provide a minimal stub
    class CrawlWorker:  # type: ignore
        def __init__(self, urls, thread_count=2, parent=None):
            self.urls = urls
        def start(self): pass
        def stop(self): pass
