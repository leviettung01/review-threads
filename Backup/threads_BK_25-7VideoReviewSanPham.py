#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎬 Auto Threads - ReviewThreads Studio v6.0
════════════════════════════════════════
Kiến trúc UI chuẩn Auto Threads:
  - Sidebar 220px cố định LUÔN hiển thị (không bao giờ biến mất).
  - QStackedWidget bên phải chứa các trang: Dashboard, Studio, TikTokShop, Accounts.
  - Studio page chia 2 phần: Config Panel trái + Products View phải (không có sidebar riêng).
  - QSS theme 100% clone từ grok_video_tool.py gốc.
"""

import os, sys, json, time, re, uuid, random, shutil, socket, subprocess, asyncio, math
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime

import requests
import httpx
import urllib.request
import urllib.parse
from gtts import gTTS

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QComboBox, QTextEdit, QLineEdit,
    QProgressBar, QScrollArea, QFrame, QFileDialog, QMessageBox, QTabWidget,
    QSplitter, QTableWidget, QTableWidgetItem, QHeaderView, QSpinBox,
    QDoubleSpinBox, QGroupBox, QListWidget, QListWidgetItem, QDialog,
    QCheckBox, QStackedWidget, QInputDialog, QRubberBand, QBoxLayout,
    QToolButton, QSizePolicy, QMenu
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer, QRect, QThreadPool
from PyQt6.QtGui import QFont, QColor, QPixmap, QIcon, QImage, QPainter, QPen, QBrush

# ─── PATHS (SELF-CONTAINED IN REVIEWTHREADS) ─────────────────────────
CURRENT_DIR = Path(__file__).parent.resolve()
GROK_DIR = CURRENT_DIR.parent

DATA_ROLE_SKILL_DIR = CURRENT_DIR / "data" / "Role Skill"
DATA_ROLE_SKILL_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_ROLE_SKILL_FILE = CURRENT_DIR / "RoleSkill.MD"
if DEFAULT_ROLE_SKILL_FILE.exists() and not (DATA_ROLE_SKILL_DIR / "RoleSkill.MD").exists():
    try: shutil.copy(DEFAULT_ROLE_SKILL_FILE, DATA_ROLE_SKILL_DIR / "RoleSkill.MD")
    except Exception: pass

OUTPUT_DIR        = CURRENT_DIR / "output_threads"
OUTPUT_IMAGES_DIR = OUTPUT_DIR / "images"
OUTPUT_VOICE_DIR  = OUTPUT_DIR / "voice"
OUTPUT_VIDEOS_DIR = OUTPUT_DIR / "videos"

# Share accounts with the parent Grok tool
_SHARED_ACCOUNTS   = GROK_DIR / "accounts.json"
ACCOUNTS_FILE      = _SHARED_ACCOUNTS if _SHARED_ACCOUNTS.exists() else (CURRENT_DIR / "accounts.json")
PROJECTS_FILE      = CURRENT_DIR / "threads_projects.json"
SETTINGS_FILE      = CURRENT_DIR / "threads_settings.json"
TIKTOK_SHOP_DIR    = CURRENT_DIR / "TiktokShop"
TIKTOK_PRODUCTS_FILE = TIKTOK_SHOP_DIR / "crawled_products" / "products.json"

sys.path.append(str(GROK_DIR))
try:
    from grok_video_tool import (
        run_veo_flow, VideoJob, is_veo_model, CaptchaBridge, upload_veo_image,
        GrokClient, AccountManager, Account,
        get_veo_model_key, get_veo_aspect_ratio,
        DEFAULT_PROXY_URL as GROK_DEFAULT_PROXY_URL,
        DEFAULT_MODEL as GROK_DEFAULT_MODEL,
        DEFAULT_DURATION as GROK_DEFAULT_DURATION,
        DEFAULT_RESOLUTION as GROK_DEFAULT_RESOLUTION,
        DEFAULT_ASPECT as GROK_DEFAULT_ASPECT,
        DEFAULT_PROJECT_ID as GROK_DEFAULT_PROJECT_ID,
        POLL_INTERVAL as GROK_POLL_INTERVAL,
        MAX_WAIT as GROK_MAX_WAIT,
        VEO_MODEL_KEYWORDS,
        ASPECT_MAP as GROK_ASPECT_MAP,
        apply_inter_profile_jitter,
        make_httpx_async_client,
    )
    has_grok_deps = True
except Exception as e:
    has_grok_deps = False
    # Fallback stubs
    class GrokClient:
        def __init__(self, *a, **kw): pass
    class AccountManager:
        def __init__(self): self.accounts = []
    class Account:
        def __init__(self, *a, **kw): pass
    def is_veo_model(m): return False
    GROK_DEFAULT_PROXY_URL = "http://127.0.0.1:8317"
    GROK_DEFAULT_PROJECT_ID = ""

CLIPROXY_DIR = CURRENT_DIR / "CLIProxy"
if not CLIPROXY_DIR.exists(): CLIPROXY_DIR = GROK_DIR / "CLIProxy"
if not CLIPROXY_DIR.exists(): CLIPROXY_DIR = GROK_DIR / "cli-proxy-api"
CLIPROXY_EXE = CLIPROXY_DIR / "cli-proxy-api.exe"
DEFAULT_PROXY_URL = "http://127.0.0.1:8317"

for d in [OUTPUT_DIR, OUTPUT_IMAGES_DIR, OUTPUT_VOICE_DIR, OUTPUT_VIDEOS_DIR, TIKTOK_SHOP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────
LLM_MODEL_LIST = [
    "gpt-5.5","gpt-5.6-sol","gpt-5.4","gpt-5.4-mini",
    "gpt-5.3-codex-spark","gpt-5.6-terra","gpt-5.6-luna","gpt-oss-120b-medium",
    "grok-4.5","grok-4.3","grok-3-mini","grok-4.20-0309-reasoning",
    "grok-3","gpt-4o","gemini-2.5-flash","gemini-3.5-flash"
]
IMAGE_MODEL_LIST = [
    "gpt-image-2","gpt-image-1.5",
    "grok-imagine-image","grok-imagine-image-quality",
    "Nano Banana Pro","Nano Banana 2","Nano Banana 2 Lite",
    "dall-e-3","flux-dev"
]
VIDEO_MODEL_LIST = [
    # Grok Video (xAI) — via CLI Proxy API
    "grok-imagine-video-1.5-preview",
    "grok-imagine-video",
    # Veo 3.1 (Google AI Sandbox) — via reCAPTCHA + Bearer Token
    "Veo 3.1 - Lite [Lower Priority]",
    "Veo 3.1 - Lite",
    "Veo 3.1 - Fast",
    "Veo 3.1 - Quality",
    # Omni (Google)
    "Omni Flash",
]
VIDEO_DURATION_LIST = ["4", "5", "6", "8", "10", "12", "15"]
VIDEO_RESOLUTION_LIST = ["720p", "1080p"]
VIDEO_ASPECT_LIST = ["9:16", "16:9", "1:1"]

# Import from TiktokShop module
sys.path.append(str(CURRENT_DIR / "TiktokShop"))
try:
    from TiktokShop.tiktok_shop_crawler import (
        CrawlWorker, find_chrome_path, is_port_in_use,
        is_chrome_cdp_active, normalize_tiktok_url,
        PROFILE_DIR, DOWNLOADS_DIR, CDP_PORT, clean_filename
    )
    _TIKTOK_CRAWLER_LOADED = True
except ImportError:
    _TIKTOK_CRAWLER_LOADED = False
    CDP_PORT = 9222
    PROFILE_DIR = CURRENT_DIR / "TiktokShop" / "profiles" / "tiktok_chrome_profile"
    DOWNLOADS_DIR = CURRENT_DIR / "TiktokShop" / "crawled_products"
    def clean_filename(name):
        import re
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        name = re.sub(r'_+', '_', name).strip('_')
        return name[:60].strip() or "product"

DEFAULT_PROJECT_ID = "378bfc6c-422d-4bfb-92d1-6e978432ca7b"
_CNOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
VEO_MODEL_KEYWORDS = (
    "veo", "omni", "abra", "nano_banana",
    "Veo 3", "Omni Flash", "Nano Banana",
)

def is_veo_model(model_name: str) -> bool:
    if not model_name:
        return False
    m = model_name.lower()
    return any(kw.lower() in m for kw in VEO_MODEL_KEYWORDS) and "grok" not in m

def get_available_role_skills() -> Dict[str, Path]:
    roles = {}
    if DATA_ROLE_SKILL_DIR.exists():
        for f in sorted(DATA_ROLE_SKILL_DIR.glob("*.[mM][dD]")): roles[f.name] = f
        for f in sorted(DATA_ROLE_SKILL_DIR.glob("*.txt")): roles[f.name] = f
    if not roles and DEFAULT_ROLE_SKILL_FILE.exists():
        roles[DEFAULT_ROLE_SKILL_FILE.name] = DEFAULT_ROLE_SKILL_FILE
    return roles

# ─── ACCOUNT MANAGER ──────────────────────────────────────────────────
@dataclass
class Account:
    name: str
    proxy_url: str = DEFAULT_PROXY_URL
    api_key: str = "grok-video-key-001"
    active: bool = True
    jobs_done: int = 0
    tag: str = "Pro"
    credits: Optional[int] = None

    def to_dict(self):
        return {
            "name": self.name,
            "proxy_url": self.proxy_url,
            "api_key": self.api_key,
            "active": self.active,
            "jobs_done": self.jobs_done,
            "tag": self.tag,
            "credits": self.credits
        }

    @staticmethod
    def from_dict(d):
        return Account(
            name=d.get("name", "Acc"),
            proxy_url=d.get("proxy_url", DEFAULT_PROXY_URL),
            api_key=d.get("api_key", "grok-video-key-001"),
            active=d.get("active", True),
            jobs_done=d.get("jobs_done", 0),
            tag=d.get("tag", "Pro"),
            credits=d.get("credits", None)
        )

class AccountManager:
    def __init__(self):
        self.accounts: List[Account] = []
        self._idx = 0
        self.load()

    def load(self):
        if ACCOUNTS_FILE.exists():
            try:
                self.accounts = [Account.from_dict(d) for d in json.loads(ACCOUNTS_FILE.read_text("utf-8"))]
            except Exception:
                self.accounts = []

    def save(self):
        try:
            ACCOUNTS_FILE.write_text(json.dumps([a.to_dict() for a in self.accounts], indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def add(self, name, proxy_url, api_key):
        self.accounts.append(Account(name, proxy_url, api_key))
        self.save()

    def remove(self, i):
        if 0 <= i < len(self.accounts):
            self.accounts.pop(i)
            self.save()

    def toggle(self, i):
        if 0 <= i < len(self.accounts):
            self.accounts[i].active = not self.accounts[i].active
            self.save()

    @property
    def active(self):
        return [a for a in self.accounts if a.active]

    def next(self) -> Optional[Account]:
        self.load()
        a = self.active
        if not a: return None
        acc = a[self._idx % len(a)]
        self._idx += 1
        acc.jobs_done += 1
        self.save()
        return acc

    def next_codex(self) -> Optional[Account]:
        self.load()
        codex = [a for a in self.active if a.name.lower().startswith("codex")]
        if not codex: return None
        acc = codex[self._idx % len(codex)]
        self._idx += 1
        acc.jobs_done += 1
        self.save()
        return acc

    def next_grok(self) -> Optional[Account]:
        self.load()
        grok = [a for a in self.active if a.name.lower().startswith("grok")]
        if not grok: return None
        acc = grok[self._idx % len(grok)]
        self._idx += 1
        acc.jobs_done += 1
        self.save()
        return acc

    def next_for_model(self, model: str) -> Optional[Account]:
        if is_veo_model(model):
            return None
        m = model.lower()
        if m.startswith("gpt-") or m.startswith("dall"):
            acc = self.next_codex()
            if acc: return acc
        elif m.startswith("grok-"):
            acc = self.next_grok()
            if acc: return acc
        return self.next()

    def sync_from_cliproxy(self) -> Tuple[int, int]:
        auth_dir = Path("~/.cli-proxy-api").expanduser()
        if not auth_dir.exists(): return 0, 0
        
        xai_files = list(auth_dir.glob("xai-*.json"))
        codex_files = list(auth_dir.glob("codex-*.json"))
        active_accounts_data = []

        for fpath in xai_files:
            try:
                d = json.loads(fpath.read_text("utf-8"))
                email = d.get("email") or fpath.stem.replace("xai-", "")
                if email:
                    active_accounts_data.append((f"Grok - {email}", DEFAULT_PROXY_URL, "grok-video-key-001"))
            except Exception: pass

        for fpath in codex_files:
            try:
                d = json.loads(fpath.read_text("utf-8"))
                email = d.get("email") or fpath.stem.replace("codex-", "")
                if email:
                    active_accounts_data.append((f"Codex - {email}", DEFAULT_PROXY_URL, "grok-video-key-001"))
            except Exception: pass

        active_names = {item[0] for item in active_accounts_data}
        existing_accounts = self.accounts.copy()
        new_accounts = []
        removed_count = 0
        added_count = 0

        for acc in existing_accounts:
            if not (acc.name.startswith("Grok - ") or acc.name.startswith("Codex - ")):
                new_accounts.append(acc)
            elif acc.name in active_names:
                new_accounts.append(acc)
            else:
                removed_count += 1

        existing_names = {acc.name for acc in new_accounts}
        for acc_name, proxy_url, api_key in active_accounts_data:
            if acc_name not in existing_names:
                new_accounts.append(Account(acc_name, proxy_url, api_key))
                added_count += 1

        self.accounts = new_accounts
        self.save()
        return added_count, removed_count


# ─── VBEE API ─────────────────────────────────────────────────────────
def vbee_get_voices(token, app_id):
    if not token or not app_id: return []
    url = "https://vbee.vn/api/public/v1/voices"
    hdrs = {"Authorization": f"Bearer {token}", "app-id": app_id, "Content-Type": "application/json"}
    voices = []
    for own in ["VBEE","COMMUNITY","PERSONAL"]:
        try:
            r = requests.get(url,headers=hdrs,params={"voiceOwnership":own,"languageCode":"vi-VN","limit":100},timeout=10)
            if r.status_code==200:
                for v in r.json().get("result",{}).get("voices",[]):
                    if v.get("code"): voices.append((v["code"], f"{'🧬 ' if own=='PERSONAL' else ''}[{own}] {v.get('name','')} ({v.get('gender','')})"))
        except: pass
    return voices

def vbee_create_tts(text, token, app_id, voice_code, speed=1.0, out_dir=OUTPUT_VOICE_DIR, log_fn=print):
    if not token or not app_id:
        log_fn("❌ [Vbee] Chưa nhập Token hoặc App-ID.")
        return None
    hdrs = {
        "Authorization": f"Bearer {token}",
        "app-id": app_id,
        "App-Id": app_id,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    body = {
        "app_id": app_id,
        "input_text": text,
        "voice_code": voice_code or "vi_south_female_miien_48k",
        "audio_type": "mp3",
        "speed_rate": round(max(0.5, min(2.0, float(speed))), 1),
        "bitrate": 128,
        "callback_url": "https://vbee.vn",
        "callbackUrl": "https://vbee.vn"
    }
    try:
        log_fn(f"🎙️ [Vbee] TTS ({len(text)} ký tự, voice: {voice_code})...")
        r = requests.post("https://vbee.vn/api/v1/tts", json=body, headers=hdrs, timeout=30)
        if r.status_code != 200:
            log_fn(f"❌ [Vbee Error] HTTP {r.status_code}: {r.text[:200]}")
            return None
        res_data = r.json()
        req_id = res_data.get("request_id") or (res_data.get("result") or {}).get("request_id")
        if not req_id:
            log_fn(f"❌ [Vbee] Không lấy được request_id từ kết quả: {res_data}")
            return None
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(2)
            pr = requests.get(f"https://vbee.vn/api/v1/tts/{req_id}", headers=hdrs, timeout=15)
            if pr.status_code == 200:
                res = pr.json().get("result") or pr.json() or {}
                st = str(res.get("status", "")).upper()
                if st in ["SUCCESS", "COMPLETED", "FINISHED"]:
                    au = res.get("audio_link") or res.get("url")
                    if au:
                        out_dir.mkdir(parents=True, exist_ok=True)
                        op = out_dir / f"vbee_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp3"
                        op.write_bytes(requests.get(au, timeout=120).content)
                        log_fn(f"✔ [Vbee] OK: {op.name}")
                        return str(op)
                    break
                elif st in ["FAILED", "ERROR"]:
                    log_fn(f"❌ [Vbee] Tiến trình TTS thất bại: {res}")
                    break
        return None
    except Exception as e:
        log_fn(f"❌ [Vbee Exception] {e}")
        return None

# ─── FFMPEG ───────────────────────────────────────────────────────────
def find_ffmpeg():
    b = GROK_DIR/"tools"/"ffmpeg.exe"; return str(b) if b.exists() else (shutil.which("ffmpeg") or "ffmpeg")
def find_ffprobe():
    b = GROK_DIR/"tools"/"ffprobe.exe"; return str(b) if b.exists() else (shutil.which("ffprobe") or "ffprobe")
FFMPEG_PATH = find_ffmpeg(); FFPROBE_PATH = find_ffprobe()

def get_media_duration(fp):
    if not fp or not Path(fp).exists(): return 0.0
    try:
        r = subprocess.run([FFPROBE_PATH,"-v","quiet","-show_entries","format=duration","-of","csv=p=0",str(fp)],capture_output=True,text=True,timeout=10)
        return float(r.stdout.strip()) if r.stdout.strip() else 0.0
    except: return 0.0

def _concat_audio_with_silence(audio_files, output_path, silence_ms=500):
    """Ghép nối nhiều file audio MP3 với khoảng im lặng giữa các file."""
    if not audio_files:
        return
    if len(audio_files) == 1:
        import shutil
        shutil.copy2(audio_files[0], output_path)
        return
    # Build ffmpeg concat filter with silence
    inputs = []
    filter_parts = []
    for i, f in enumerate(audio_files):
        inputs.extend(["-i", str(f)])
        filter_parts.append(f"[{i}:a]aresample=44100[a{i}]")
    # Add silence between each segment
    concat_inputs = []
    for i in range(len(audio_files)):
        concat_inputs.append(f"[a{i}]")
        if i < len(audio_files) - 1:
            # Generate silence segment
            filter_parts.append(f"aevalsrc=0:d={silence_ms/1000}:s=44100[s{i}]")
            concat_inputs.append(f"[s{i}]")
    total_streams = len(audio_files) + max(0, len(audio_files) - 1)
    concat_str = "".join(concat_inputs) + f"concat=n={total_streams}:v=0:a=1[out]"
    filter_parts.append(concat_str)
    full_filter = ";".join(filter_parts)
    cmd = [FFMPEG_PATH, "-y"] + inputs + ["-filter_complex", full_filter, "-map", "[out]", "-c:a", "libmp3lame", "-q:a", "2", output_path]
    subprocess.run(cmd, capture_output=True, timeout=120, check=True)

def _detect_voice_segments(audio_path, expected_count, log_fn=None):
    """Detect voice segments in concatenated audio using ffprobe silencedetect.
    Tries multiple duration/noise combinations to find exactly (expected_count - 1) gaps,
    filtering out small intra-scene punctuation pauses.
    Returns list of (start, duration) for each voice segment."""
    try:
        if expected_count <= 1:
            return [(0.0, get_media_duration(audio_path))]
            
        total_dur = get_media_duration(audio_path)
        if total_dur <= 0:
            return None
            
        num_gaps_needed = expected_count - 1
        
        # We try to detect the gaps. Inter-scene gaps are digital silences of 0.4s or 0.5s.
        # We try various duration and noise thresholds to find exactly num_gaps_needed gaps.
        # We prioritize larger duration check first to ignore short intra-scene pauses (e.g. commas/periods).
        param_combinations = [
            (0.35, -45),
            (0.35, -40),
            (0.35, -35),
            (0.30, -45),
            (0.30, -40),
            (0.30, -35),
            (0.25, -45),
            (0.25, -40),
            (0.25, -35),
        ]
        
        best_gaps = None
        for d_val, noise_val in param_combinations:
            cmd = [
                FFMPEG_PATH, "-i", str(audio_path),
                "-af", f"silencedetect=noise={noise_val}dB:d={d_val}",
                "-f", "null", "-"
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            stderr = r.stderr
            
            silence_starts = []
            silence_ends = []
            for line in stderr.split('\n'):
                if 'silence_start:' in line:
                    m = re.search(r'silence_start:\s*([\d.]+)', line)
                    if m: silence_starts.append(float(m.group(1)))
                if 'silence_end:' in line:
                    m = re.search(r'silence_end:\s*([\d.]+)', line)
                    if m: silence_ends.append(float(m.group(1)))
            
            gaps = []
            for i in range(min(len(silence_starts), len(silence_ends))):
                gaps.append((silence_starts[i], silence_ends[i]))
            
            if len(gaps) == num_gaps_needed:
                best_gaps = gaps
                if log_fn:
                    log_fn(f"      ✔ Tìm thấy {len(gaps)} khoảng im lặng bằng silencedetect (d={d_val}, noise={noise_val}dB)")
                break
                
        # Fallback grouping/filtering if no exact match found:
        # We run at a low threshold (d=0.2, noise=-35dB) and filter for silences around 0.3s - 0.7s
        if not best_gaps:
            cmd = [
                FFMPEG_PATH, "-i", str(audio_path),
                "-af", "silencedetect=noise=-35dB:d=0.2",
                "-f", "null", "-"
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            silence_starts = []
            silence_ends = []
            for line in r.stderr.split('\n'):
                if 'silence_start:' in line:
                    m = re.search(r'silence_start:\s*([\d.]+)', line)
                    if m: silence_starts.append(float(m.group(1)))
                if 'silence_end:' in line:
                    m = re.search(r'silence_end:\s*([\d.]+)', line)
                    if m: silence_ends.append(float(m.group(1)))
            
            raw_gaps = []
            for i in range(min(len(silence_starts), len(silence_ends))):
                gap_dur = silence_ends[i] - silence_starts[i]
                raw_gaps.append((silence_starts[i], silence_ends[i], gap_dur))
            
            # Gaps between scenes should be around 0.3s - 0.7s
            inter_scene_gaps = [g for g in raw_gaps if 0.3 <= g[2] <= 0.8]
            if len(inter_scene_gaps) == num_gaps_needed:
                best_gaps = [(g[0], g[1]) for g in inter_scene_gaps]
                if log_fn: log_fn(f"      ✔ Lọc được {len(best_gaps)} khoảng im lặng giữa các scene")
                
        if best_gaps:
            segments = []
            last_end = 0.0
            for g_start, g_end in best_gaps:
                segments.append((last_end, max(0.1, g_start - last_end)))
                last_end = g_end
            segments.append((last_end, max(0.1, total_dur - last_end)))
            
            if log_fn:
                log_fn(f"      📊 Kết quả phân tích segments:")
                for i, (st, dur) in enumerate(segments):
                    log_fn(f"         Scene {i+1}: {st:.2f}s → {st+dur:.2f}s (dur={dur:.2f}s)")
            return segments
            
        if log_fn: log_fn("      ⚠️ Không phát hiện đúng số lượng gap phân cảnh, sử dụng fallback")
        return None
    except Exception as e:
        if log_fn: log_fn(f"      ⚠️ Lỗi silence detect: {e}")
        return None

def parse_llm_json_robust(raw):
    c = raw.strip()
    if c.startswith("```"): c = re.sub(r"^```(?:json)?\s*","",c); c = re.sub(r"```\s*$","",c)
    m = re.search(r'\{[\s\S]*\}', c.strip())
    if m:
        try: return json.loads(m.group())
        except: pass
    return json.loads(c.strip())

# ─── DATA MODELS ──────────────────────────────────────────────────────
@dataclass
class ThreadsScene:
    id: int = 1
    type: str = "TEXT2IMG"
    ui_layout: str = "single_post"
    aspect_ratio: str = "4:3"
    caption_raw: str = ""
    text_segment: str = ""
    prompt: str = ""
    image_path: str = ""
    status: str = "pending"
    error_msg: str = ""
    voice_role: str = ""  # "narrator" | "comment" | "" (default = main voice)
    voice_duration: float = 0.0  # Actual duration of this scene's voice segment (seconds)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return ThreadsScene(
            id=d.get("id", 1),
            type=d.get("type", "TEXT2IMG"),
            ui_layout=d.get("ui_layout", "single_post"),
            aspect_ratio=d.get("aspect_ratio", "4:3"),
            caption_raw=d.get("caption_raw", ""),
            text_segment=d.get("text_segment", ""),
            prompt=d.get("prompt", ""),
            image_path=d.get("image_path", ""),
            status=d.get("status", "pending"),
            error_msg=d.get("error_msg", ""),
            voice_role=d.get("voice_role", ""),
            voice_duration=d.get("voice_duration", 0.0)
        )

@dataclass
class VideoReviewScene:
    """Một phân cảnh video review sản phẩm (AI-generated hoặc external)."""
    id: int = 1
    scene_title: str = ""
    scene_description: str = ""
    voiceover_text: str = ""       # Lời dẫn review cho scene này
    image_prompt: str = ""          # Prompt tạo ảnh (AI mode)
    video_prompt: str = ""          # Prompt tạo video (AI mode)
    image_path: str = ""            # Ảnh đầu vào
    video_clip_path: str = ""       # Video clip output
    voice_path: str = ""            # Voice riêng cho scene này
    voice_duration: float = 0.0
    status: str = "pending"
    error_msg: str = ""

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return VideoReviewScene(
            id=d.get("id", 1),
            scene_title=d.get("scene_title", ""),
            scene_description=d.get("scene_description", ""),
            voiceover_text=d.get("voiceover_text", ""),
            image_prompt=d.get("image_prompt", ""),
            video_prompt=d.get("video_prompt", ""),
            image_path=d.get("image_path", ""),
            video_clip_path=d.get("video_clip_path", ""),
            voice_path=d.get("voice_path", ""),
            voice_duration=d.get("voice_duration", 0.0),
            status=d.get("status", "pending"),
            error_msg=d.get("error_msg", "")
        )

@dataclass
class ProductItem:
    id:str=field(default_factory=lambda:uuid.uuid4().hex[:8]); title:str=""; info:str=""
    images:List[str]=field(default_factory=list); scenes:List[ThreadsScene]=field(default_factory=list)
    voiceover_text:str=""; voice_path:str=""; voice_duration:float=0.0
    final_video_path:str=""; status:str="Chưa chạy"
    context:str="Bối cảnh theo Role Skill"
    use_ref_for_generation:bool=False
    # Video Review fields
    video_review_mode:str=""  # "" | "ai_generate" | "external_file"
    video_review_scenes:List[VideoReviewScene]=field(default_factory=list)
    video_review_voice_path:str=""; video_review_voice_duration:float=0.0
    external_video_path:str=""  # Path video file ngoài (mode external)
    final_combined_path:str=""  # Video cuối cùng (Phần 1 + Phần 2)

    def to_dict(self):
        return {
            **{k: getattr(self, k) for k in [
                "id", "title", "info", "images", "voiceover_text", "voice_path",
                "voice_duration", "final_video_path", "status", "context",
                "video_review_mode", "video_review_voice_path",
                "video_review_voice_duration", "external_video_path", "final_combined_path",
                "use_ref_for_generation"
            ]},
            "scenes": [s.to_dict() for s in self.scenes],
            "video_review_scenes": [s.to_dict() for s in self.video_review_scenes]
        }

    @staticmethod
    def from_dict(d):
        sc = [ThreadsScene.from_dict(s) for s in d.get("scenes", [])]
        vr_sc = [VideoReviewScene.from_dict(s) for s in d.get("video_review_scenes", [])]
        p = ProductItem(
            d.get("id", uuid.uuid4().hex[:8]), d.get("title", ""), d.get("info", ""),
            d.get("images", []), sc, d.get("voiceover_text", ""),
            d.get("voice_path", ""), d.get("voice_duration", 0.0),
            d.get("final_video_path", ""), d.get("status", "Chưa chạy"),
            d.get("context", "Bối cảnh theo Role Skill")
        )
        p.video_review_mode = d.get("video_review_mode", "")
        p.video_review_scenes = vr_sc
        p.video_review_voice_path = d.get("video_review_voice_path", "")
        p.video_review_voice_duration = d.get("video_review_voice_duration", 0.0)
        p.external_video_path = d.get("external_video_path", "")
        p.final_combined_path = d.get("final_combined_path", "")
        p.use_ref_for_generation = d.get("use_ref_for_generation", False)
        return p

@dataclass
class ThreadsProject:
    id:str=field(default_factory=lambda:uuid.uuid4().hex[:8]); name:str="Dự Án Mới"
    created_at:str=field(default_factory=lambda:datetime.now().strftime("%d/%m/%Y %H:%M"))
    products:List[ProductItem]=field(default_factory=list)
    def to_dict(self): return {"id":self.id,"name":self.name,"created_at":self.created_at,"products":[p.to_dict() for p in self.products]}
    @staticmethod
    def from_dict(d): return ThreadsProject(d.get("id",uuid.uuid4().hex[:8]),d.get("name","Dự Án Mới"),d.get("created_at",""),[ProductItem.from_dict(p) for p in d.get("products",[])])

# ─── VIDEO REVIEW PROCESSING UTILITIES ────────────────────────────────

def process_external_video(input_path, output_path, settings, log_fn=None):
    """Xử lý video file ngoài với các tham số anti-reup.
    Crop, speed, zoom, flip, noise, random cut."""
    import random as _rnd
    filters = []

    # 1. Crop
    crop_mode = settings.get("vr_crop_mode", "square_center")
    if crop_mode == "square_center":
        filters.append("crop=min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2")
        filters.append("scale=1080:1080")
    elif crop_mode == "9_16_center":
        filters.append("crop=ih*9/16:ih:(iw-ih*9/16)/2:0")
        filters.append("scale=1080:1920")

    # 2. Speed
    speed = settings.get("vr_speed", 1.0)
    if speed != 1.0:
        filters.append(f"setpts={1/speed}*PTS")

    # 3. Zoom random
    if settings.get("vr_zoom_random", False):
        zoom = _rnd.uniform(1.0, 1.1)
        filters.append(f"scale=iw*{zoom:.3f}:ih*{zoom:.3f},crop=iw/{zoom:.3f}:ih/{zoom:.3f}")

    # 4. Flip
    if settings.get("vr_flip_h", False):
        filters.append("hflip")

    # 5. Noise
    if settings.get("vr_noise", True):
        filters.append("noise=alls=3:allf=t+u")

    # 6. Max duration
    max_dur = settings.get("vr_max_duration", 30)

    # 7. Random cut (cắt bỏ 0.2-0.5s mỗi 3-5s)
    if settings.get("vr_random_cut", True):
        total_dur = get_media_duration(input_path)
        if total_dur > 0:
            interval_min = settings.get("vr_rcut_interval_min", 3)
            interval_max = settings.get("vr_rcut_interval_max", 5)
            # Build segment select expression
            segments = []
            t = 0.0
            while t < min(total_dur, max_dur):
                seg_len = _rnd.uniform(interval_min, interval_max)
                seg_end = min(t + seg_len, total_dur, max_dur)
                segments.append((t, seg_end))
                cut_dur = _rnd.uniform(0.2, 0.5)
                t = seg_end + cut_dur
            
            if segments:
                # Use concat demuxer approach with temp files
                temp_dir = Path(output_path).parent / "vr_temp"
                temp_dir.mkdir(exist_ok=True)
                seg_files = []
                vf = ",".join(filters) if filters else None
                
                for i, (ss, se) in enumerate(segments):
                    seg_out = str(temp_dir / f"seg_{i:03d}.mp4")
                    cmd = [FFMPEG_PATH, "-y", "-ss", f"{ss:.3f}", "-i", str(input_path),
                           "-t", f"{se - ss:.3f}", "-an"]
                    if vf:
                        cmd.extend(["-vf", vf])
                    cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "22", seg_out])
                    try:
                        subprocess.run(cmd, capture_output=True, timeout=60, check=True,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        seg_files.append(seg_out)
                    except Exception as e:
                        if log_fn: log_fn(f"      ⚠️ Lỗi cắt segment {i}: {e}")
                
                if seg_files:
                    # Create concat list file
                    concat_list = str(temp_dir / "concat.txt")
                    with open(concat_list, "w", encoding="utf-8") as f:
                        for sf in seg_files:
                            f.write(f"file '{sf}'\n")
                    
                    cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0",
                           "-i", concat_list, "-c:v", "libx264", "-preset", "fast",
                           "-crf", "22", str(output_path)]
                    try:
                        subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        if log_fn: log_fn(f"      ✔ Đã xử lý video: {len(seg_files)} segments")
                    except Exception as e:
                        if log_fn: log_fn(f"      ❌ Lỗi ghép segments: {e}")
                    
                    # Cleanup
                    for sf in seg_files:
                        try: Path(sf).unlink(missing_ok=True)
                        except: pass
                    try: Path(concat_list).unlink(missing_ok=True)
                    except: pass
                    try: temp_dir.rmdir()
                    except: pass
                    
                    if Path(output_path).exists():
                        return str(output_path)
    
    # Fallback: simple filter without random cut
    vf = ",".join(filters) if filters else None
    cmd = [FFMPEG_PATH, "-y", "-i", str(input_path), "-t", str(max_dur), "-an"]
    if vf:
        cmd.extend(["-vf", vf])
    cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "22", str(output_path)])
    
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if log_fn: log_fn(f"      ✔ Đã xử lý video external")
    except Exception as e:
        if log_fn: log_fn(f"      ❌ Lỗi xử lý video: {e}")
        return None
    return str(output_path) if Path(output_path).exists() else None


def combine_final_video(part1_path, part2_path, output_path, transition="fade_black", log_fn=None):
    """Ghép 2 video (Phần Ảnh CMT + Phần Video Review) thành 1 video cuối."""
    try:
        p1_dur = get_media_duration(part1_path)
        
        if transition == "fade_black":
            fade_dur = 0.5
            cmd = [FFMPEG_PATH, "-y",
                   "-i", str(part1_path), "-i", str(part2_path),
                   "-filter_complex",
                   f"[0:v]fade=t=out:st={max(0, p1_dur - fade_dur):.2f}:d={fade_dur}[v0];"
                   f"[1:v]fade=t=in:st=0:d={fade_dur}[v1];"
                   f"[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
                   "-map", "[v]", "-map", "[a]",
                   "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                   "-c:a", "aac", "-b:a", "192k",
                   str(output_path)]
        else:
            # Direct concat
            temp_list = str(Path(output_path).parent / "concat_final.txt")
            with open(temp_list, "w", encoding="utf-8") as f:
                f.write(f"file '{part1_path}'\n")
                f.write(f"file '{part2_path}'\n")
            cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0",
                   "-i", temp_list, "-c", "copy", str(output_path)]
        
        subprocess.run(cmd, capture_output=True, timeout=300, check=True,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        
        # Cleanup temp list
        temp_list_path = Path(output_path).parent / "concat_final.txt"
        if temp_list_path.exists():
            try: temp_list_path.unlink()
            except: pass
        
        if log_fn: log_fn(f"    ✔ Ghép final OK: {Path(output_path).name}")
        return str(output_path) if Path(output_path).exists() else None
    except Exception as e:
        if log_fn: log_fn(f"    ❌ Lỗi ghép final: {e}")
        return None


# ─── PIPELINE WORKER ─────────────────────────────────────────────────
class FullPipelineRunnerWorker(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int,int,str)
    product_updated_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool,str)

    def __init__(self, products, settings, used_bg, captcha_bridge=None, acc_mgr=None):
        super().__init__()
        self.products=products; self.settings=settings; self.used_bg=used_bg
        self.captcha_bridge=captcha_bridge
        self.acc_mgr = acc_mgr or AccountManager()
        self._stopped=False

    def run(self):
        try:
            total = len(self.products)
            if not total:
                self.finished_signal.emit(False, "Chưa có sản phẩm để chạy.")
                return
            
            api_url = self.settings.get("api_url", DEFAULT_PROXY_URL)
            api_key = self.settings.get("api_key", "grok-video-key-001")
            llm = self.settings.get("llm_model", "gpt-5.5")
            img_m = self.settings.get("img_model", "gpt-image-2")
            sc_cnt = self.settings.get("scene_count", 4)
            ctx = self.settings.get("context", "Bối cảnh theo Role Skill")
            rsp = self.settings.get("role_skill_path")
            rs_text = Path(rsp).read_text(encoding="utf-8") if rsp and Path(rsp).exists() else ""
            bg_folder = self.settings.get("bg_folder", "")
            no_dup = self.settings.get("no_duplicate_bg", True)
            
            # Print settings summary
            vm_display = self.settings.get("voice_mode_display", "Review dẫn chuyện (narrative_review)")
            self.log_signal.emit(f"⚙️ CẤU HÌNH PIPELINE CHẠY:")
            self.log_signal.emit(f"  • Role Skill: {self.settings.get('selected_role')}")
            self.log_signal.emit(f"  • LLM Model: {llm}")
            self.log_signal.emit(f"  • Voice Mode: {vm_display}")
            self.log_signal.emit(f"  • Image Model: {img_m}")
            self.log_signal.emit(f"  • Context: {ctx}")
            self.log_signal.emit(f"  • Số phân cảnh: {sc_cnt}")
            self.log_signal.emit(f"  • Voice Engine: {self.settings.get('voice_engine')}")
            self.log_signal.emit(f"  • API URL: {api_url}")
            
            bg_files = []
            if bg_folder and Path(bg_folder).exists():
                for ext in ["*.mp4", "*.mov", "*.mkv", "*.avi"]:
                    bg_files.extend(list(Path(bg_folder).glob(ext)))
            self.log_signal.emit(f"  • Video nền khả dụng: {len(bg_files)} video")
            
            def sanitize_filename(name):
                s = re.sub(r'[\\/*?:"<>|]', "", name)
                return s.strip()[:100]

            for pi, prod in enumerate(self.products):
                if self._stopped:
                    break
                
                # Create dedicated product folder
                out_base = Path(self.settings.get("output_dir", str(OUTPUT_DIR)))
                prod_dir_name = f"{sanitize_filename(prod.title or 'san_pham')}_{prod.id}"
                prod_folder = out_base / prod_dir_name
                prod_folder.mkdir(parents=True, exist_ok=True)
                
                prod_images_dir = prod_folder / "images"
                prod_voice_dir = prod_folder / "voice"
                prod_images_dir.mkdir(exist_ok=True)
                prod_voice_dir.mkdir(exist_ok=True)
                
                self.log_signal.emit(f"🚀 [{pi+1}/{total}] {prod.title[:40]}...")
                self.log_signal.emit(f"  📂 Folder lưu: {prod_folder}")
                self.progress_signal.emit(pi+1, total, "")
                prod.status = "⏳ Đang chạy..."
                self.product_updated_signal.emit(prod.id)
                
                only_vid = self.settings.get("only_video_assembly", False)
                if only_vid:
                    self.log_signal.emit("  ⚡ Chế độ chỉ ghép dựng Video (Bỏ qua LLM, Sinh Ảnh, Giọng)...")
                
                # ── 1. LLM Step ──
                if not only_vid and not prod.scenes:
                    self.log_signal.emit(f"  [1/4] LLM [{llm}]...")
                    vm_code = self.settings.get("voice_mode", "narrative_review")
                    if vm_code == "narrative_review":
                        vm_desc = "narrative_review (Review dẫn chuyện thứ 3 mượt mà)"
                    elif vm_code == "hybrid_narrate_read":
                        vm_desc = "hybrid_narrate_read (Review kèm Đọc CMT — voice dẫn chuyện xen kẽ voice đọc CMT)"
                    else:
                        vm_desc = "verbatim_read (Đọc nguyên văn bài/bình luận)"
                    
                    # Aspect ratio restrictions based on current image model
                    ratios = get_supported_aspect_ratios(img_m)
                    ratios_str = ", ".join(ratios)
                    
                    # Style constraint
                    chosen_style = prod.context or ctx
                    style_instruction = ""
                    if chosen_style and chosen_style != "Tự chọn phong cách tối ưu nhất":
                        # Lookup description if it's a default archetype
                        desc_lookup = next((d for n, d in DEFAULT_ARCHETYPES if n == chosen_style), "")
                        style_instruction = f"YÊU CẦU PHONG CÁCH KỊCH BẢN BẮT BUỘC: Bạn BẮT BUỘC phải viết kịch bản này tuân theo phong cách: {chosen_style}. Chi tiết phong cách: {desc_lookup or 'Viết theo mô tả phong cách này.'}\n"
                    
                    # Voice mode strict instructions
                    voice_mode_instruction = ""
                    if vm_code == "verbatim_read":
                        voice_mode_instruction = (
                            "YÊU CẦU CHẾ ĐỘ GIỌNG ĐỌC BẮT BUỘC (verbatim_read - ĐỌC NGUYÊN VĂN):\n"
                            "1. CẤM HOÀN TOÀN lời dẫn/người kể chuyện/MC. Tuyệt đối KHÔNG viết các câu kiểu: 'Và đây là...', 'Nhiều bạn đã...', 'Theo các bình luận...', 'Cùng xem...', 'Kết quả thật...'. Đây là lỗi NGHIÊM TRỌNG NHẤT.\n"
                            "2. Trường 'text_segment' cho từng scene PHẢI là bản đọc chuẩn hóa CHÍNH XÁC 100% của 'caption_raw' — không thêm, không bớt, không diễn giải. Nối tất cả 'text_segment' lại phải khít 100% với 'voiceover' tổng.\n"
                            "3. Chuẩn hóa teencode: 'ko'→'không', 'mng'→'mọi người'. Bỏ emoji, ':)))', 'hihi', nhãn '1/2'.\n"
                            "4. Cả kịch bản chỉ gồm NỘI DUNG các bài đăng và bình luận nối tiếp nhau — KHÔNG có câu nào của người ngoài cuộc.\n"
                            "5. Mỗi scene PHẢI có trường 'voice_role': 'comment' (vì tất cả đều là nội dung bình luận).\n"
                        )
                    elif vm_code == "hybrid_narrate_read":
                        voice_mode_instruction = (
                            "YÊU CẦU CHẾ ĐỘ GIỌNG ĐỌC BẮT BUỘC (hybrid_narrate_read - REVIEW KÈM ĐỌC CMT):\n"
                            "1. Kịch bản MIX giữa lời dẫn chuyện (narrator) và đọc nguyên văn bình luận (comment).\n"
                            "2. Mỗi scene BẮT BUỘC có trường 'voice_role' với giá trị 'narrator' hoặc 'comment'.\n"
                            "3. Scene đầu tiên luôn là 'narrator' (dẫn dắt mở đầu câu chuyện).\n"
                            "4. Scene có voice_role='comment': text_segment là bản đọc chuẩn hóa 100% của caption_raw (đọc nguyên văn nội dung bình luận, KHÔNG thêm bớt).\n"
                            "5. Scene có voice_role='narrator': text_segment là lời dẫn chuyện mượt mà của MC/reviewer, 1-2 câu ngắn gọn dẫn dắt giữa các khối bình luận.\n"
                            "6. Xen kẽ narrator 1-2 câu giữa các khối bình luận, tổng narrator chiếm khoảng 20-30% kịch bản.\n"
                            "7. Nối tất cả text_segment theo thứ tự id tăng dần phải = voiceover tổng.\n"
                        )
                    else:
                        voice_mode_instruction = (
                            "YÊU CẦU CHẾ ĐỘ GIỌNG ĐỌC BẮT BUỘC (narrative_review - REVIEW DẪN CHUYỆN):\n"
                            "1. Giọng đọc là lời kể của người dẫn chuyện thứ ba, tóm ý và chia sẻ trải nghiệm mượt mà, không đọc nguyên văn từng bình luận trong ảnh.\n"
                        )
                    
                    # Image illustration requirement
                    image_rule = (
                        "YÊU CẦU HÌNH MINH HOẠ BẮT BUỘC: Ít nhất 70% các phân cảnh phải có hình minh hoạ (media) trong prompt. "
                        "Phân cảnh hook (scene 1-2) BẮT BUỘC có ảnh minh hoạ gây hài hước/tranh cãi/thu hút. "
                        "Phân cảnh cuối (gài sản phẩm) luôn có ảnh sản phẩm. "
                        "Tối đa 2-3 scene text thuần (không media) và KHÔNG BAO GIỜ ở vị trí scene 1 hoặc 2.\n"
                    )

                    sp = (
                        f"{rs_text}\n\n"
                        f"YÊU CẦU TỶ LỆ KHUNG HÌNH BẮT BUỘC CHO MODEL {img_m}: Bạn CHỈ ĐƯỢC phép trả về trường aspect_ratio trong các giá trị sau: {ratios_str}. Tuyệt đối không chọn tỷ lệ nào ngoài danh sách này. Hãy điều chỉnh mô tả ảnh prompt sao cho khớp chính xác với tỷ lệ đã chọn.\n"
                        f"{style_instruction}"
                        f"{voice_mode_instruction}\n"
                        f"{image_rule}\n"
                        f"SỐ PHÂN CẢNH: {sc_cnt}\n"
                        f"THAM SỐ VOICE_MODE BẮT BUỘC (PHẦN 7): {vm_desc}\n"
                        f"YÊU CẦU QUAN TRỌNG: Tuân thủ Phần 6 (Suy luận aspect_ratio cho từng scene), Phần 7 (Voice Mode), Phần 8 (Template 8 đoạn + Quy tắc hình minh hoạ), và Phần 9 (Prompt mẫu tham chiếu). Trả về đúng JSON schema Phần 10."
                    )
                    
                    # Log prompt details for debug
                    self.log_signal.emit(f"  💬 Gửi request LLM...")
                    self.log_signal.emit(f"    • Voice Mode: {vm_code} ({vm_desc[:40]}...)")
                    self.log_signal.emit(f"    • Phong cách kịch bản: {chosen_style}")
                    self.log_signal.emit(f"    • Product: {prod.title} | Info: {prod.info[:100]}...")
                    
                    # Encode images as base64 for vision LLM analysis
                    user_content = [{"type": "text", "text": f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info}"}]
                    import base64 as _b64
                    
                    def _get_image_block(img_p):
                        try:
                            if img_p and Path(img_p).exists():
                                with open(img_p, "rb") as f_img:
                                    data_b64 = _b64.b64encode(f_img.read()).decode("utf-8")
                                suffix = Path(img_p).suffix.lower()
                                mime = "image/png"
                                if suffix in [".jpg", ".jpeg"]:
                                    mime = "image/jpeg"
                                elif suffix == ".webp":
                                    mime = "image/webp"
                                return {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime};base64,{data_b64}"}
                                }
                        except Exception as e_enc:
                            self.log_signal.emit(f"  ⚠️ Lỗi encode ảnh {img_p} cho LLM: {e_enc}")
                        return None

                    if prod.images:
                        blk = _get_image_block(prod.images[0])
                        if blk: user_content.append(blk)
                    
                    for ref_p in prod.images[1:]:
                        blk = _get_image_block(ref_p)
                        if blk: user_content.append(blk)

                    user_msg = user_content if len(user_content) > 1 else f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info}"
                    
                    r = requests.post(
                        f"{api_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json={
                            "model": llm,
                            "messages": [
                                {"role": "system", "content": sp},
                                {"role": "user", "content": user_msg}
                            ],
                            "temperature": 0.7
                        },
                        timeout=900
                    )
                    
                    self.log_signal.emit(f"  📥 LLM Response Status: {r.status_code}")
                    if r.status_code == 200:
                        content_raw = r.json()["choices"][0]["message"]["content"]
                        self.log_signal.emit(f"  📥 Nội dung trả về: {content_raw[:200]}...")
                        
                        # Save raw LLM output to script file
                        script_file = prod_folder / "LLM.json"
                        try:
                            script_file.write_text(content_raw, encoding="utf-8")
                            self.log_signal.emit(f"  💾 Đã lưu kịch bản vào: {script_file}")
                        except Exception as e:
                            self.log_signal.emit(f"  ⚠️ Lỗi lưu file kịch bản: {e}")
                            
                        d = parse_llm_json_robust(content_raw)
                        sr = d.get("scenarios", [{}])[0]
                        prod.voiceover_text = sr.get("voiceover", "")
                        prod.scenes = [ThreadsScene.from_dict(s) for s in sr.get("scenes", [])]
                        self.log_signal.emit(f"  ✔ {len(prod.scenes)} phân cảnh.")
                        self.product_updated_signal.emit(prod.id)
                    else:
                        prod.status = "❌ Lỗi LLM"
                        self.log_signal.emit(f"  ❌ Gặp lỗi từ máy chủ API: {r.text}")
                        self.product_updated_signal.emit(prod.id)
                        continue
                else:
                    self.log_signal.emit(f"  [1/4] Bỏ qua kịch bản LLM (đã có sẵn {len(prod.scenes)} phân cảnh).")
                
                # ── 2. Image Generation (Veo3 High-Speed Burst Parallel Engine) ──
                missing_scenes = []
                if not only_vid:
                    burst_size = self.settings.get("burst_size", 4)
                    burst_delay = self.settings.get("burst_delay", 10)
                    concurrency = self.settings.get("concurrency", 8)
                    upscale_threads = self.settings.get("upscale_threads", 1)
                    is_veo = has_grok_deps and is_veo_model(img_m)
                    g_accs = []
                    if is_veo:
                        self.acc_mgr.load()
                        g_accs = [a for a in self.acc_mgr.accounts if a.active and (a.name.startswith("Google") or "google" in a.name.lower()) and len(a.api_key or "") > 20]
                        if g_accs:
                            self.log_signal.emit(f"🔑 Tìm thấy {len(g_accs)} tài khoản Google.")

                    for idx, sc in enumerate(prod.scenes):
                        target_img = prod_images_dir / f"scene_{sc.id}.png"
                        if not sc.image_path or not Path(sc.image_path).exists() or not target_img.exists():
                            missing_scenes.append((idx, sc, target_img))
                        else:
                            sc.image_path = str(target_img)
                            sc.status = "done"
                            self.log_signal.emit(f"    ✔ Phân cảnh #{idx+1} đã có sẵn: {target_img.name}")

                if missing_scenes:
                    ref_media_ids_per_acc = {}
                    if is_veo and prod.images and g_accs:
                        # Determine which images to upload as references for generation
                        if getattr(prod, "use_ref_for_generation", False):
                            images_to_upload = prod.images
                        else:
                            images_to_upload = [prod.images[0]] if prod.images else []
                        
                        self.log_signal.emit(f"  📸 Tải lên {len(images_to_upload)} ảnh sản phẩm tham chiếu cho từng tài khoản hoạt động...")
                        loop_up = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop_up)
                        try:
                            async def _do_upload_all():
                                for acc in g_accs:
                                    bearer = acc.api_key
                                    proj_id = acc.proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b"
                                    ref_media_ids_per_acc[bearer] = []
                                    for img_p in images_to_upload:
                                        if Path(img_p).exists():
                                            mid = await upload_veo_image(
                                                client=None,
                                                file_path=str(img_p),
                                                bearer_token=bearer,
                                                project_id=proj_id,
                                                log=lambda m: self.log_signal.emit(f"    [{acc.name}] {m}"),
                                                captcha_bridge=self.captcha_bridge
                                            )
                                            if mid: ref_media_ids_per_acc[bearer].append(mid)
                            loop_up.run_until_complete(_do_upload_all())
                        except Exception as e_up:
                            self.log_signal.emit(f"  ⚠️ Lỗi tải ảnh tham chiếu: {e_up}")
                        finally:
                            try: loop_up.close()
                            except Exception: pass

                    self.log_signal.emit(f"  [2/4] Sinh {len(missing_scenes)} ảnh [{img_m}] (Song song: {concurrency} luồng | Burst: {burst_size} ảnh/đợt | Nghỉ: {burst_delay}s)...")
                    
                    for i in range(0, len(missing_scenes), burst_size):
                        if self._stopped:
                            break
                        
                        chunk = missing_scenes[i:i + burst_size]
                        self.log_signal.emit(f"    🚀 Đợt {i//burst_size + 1}/{math.ceil(len(missing_scenes)/burst_size)}: Xử lý {len(chunk)} ảnh song song...")
                        
                        if is_veo:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                async def process_single_veo(idx_val, sc_val, target_img_val, acc_idx):
                                    retry_cnt = self.settings.get("retry_count", 2)
                                    acc = g_accs[acc_idx % len(g_accs)] if g_accs else None
                                    bearer = acc.api_key if acc else self.settings.get("veo_bearer", "")
                                    proj_id = (acc.proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b") if acc else self.settings.get("veo_project_id", "")
                                    acc_name = acc.name if acc else "Default"
                                    acc_ref_ids = ref_media_ids_per_acc.get(bearer, [])
                                    
                                    # Stagger starting time to prevent reCAPTCHA extension collision
                                    if acc_idx > 0:
                                        stagger_delay = acc_idx * 5.0
                                        self.log_signal.emit(f"      ⏳ Trì hoãn {stagger_delay}s trước khi gửi Phân cảnh #{idx_val+1} để tránh nghẽn reCAPTCHA...")
                                        await asyncio.sleep(stagger_delay)
                                        
                                    for attempt in range(1, retry_cnt + 2):
                                        try:
                                            self.log_signal.emit(f"      🎨 Phân cảnh #{idx_val+1} (Tài khoản: {acc_name}, Lần {attempt}/{retry_cnt+1}): {sc_val.prompt[:60]}...")
                                            sc_aspect_raw = getattr(sc_val, "aspect_ratio", "4:3") or "4:3"
                                            # Map strictly to 1:1, 16:9, or 9:16 to prevent Google Labs API errors (HTTP 400)
                                            if "1:1" in sc_aspect_raw or "square" in sc_aspect_raw.lower():
                                                sc_aspect = "1:1"
                                            elif "9:16" in sc_aspect_raw or "3:4" in sc_aspect_raw or "4:5" in sc_aspect_raw or "portrait" in sc_aspect_raw.lower():
                                                sc_aspect = "9:16"
                                            else:
                                                sc_aspect = "16:9"
                                            job = VideoJob(
                                                id=f"img_{uuid.uuid4().hex[:6]}",
                                                model=img_m,
                                                mode="image-generation",
                                                prompt=sc_val.prompt,
                                                aspect_ratio=sc_aspect,
                                                output_name=f"scene_{sc_val.id}",
                                                local_path=str(prod_images_dir),
                                                use_proxy=True,
                                                ref_images=acc_ref_ids if acc_ref_ids else None
                                            )
                                            job.max_retries = 2
                                            
                                            success = await run_veo_flow(
                                                job,
                                                bearer,
                                                proj_id,
                                                self.captcha_bridge,
                                                log=lambda m: self.log_signal.emit(f"        [Google Labs #{idx_val+1}] {m}"),
                                                progress=None
                                            )
                                            
                                            if success and job.local_path and Path(job.local_path).exists():
                                                try:
                                                    p_src = Path(job.local_path)
                                                    if p_src.is_file():
                                                        p_src.replace(target_img_val)
                                                    elif (p_src / f"scene_{sc_val.id}.png").exists():
                                                        (p_src / f"scene_{sc_val.id}.png").replace(target_img_val)
                                                    elif (p_src / f"{job.id}.png").exists():
                                                        (p_src / f"{job.id}.png").replace(target_img_val)
                                                except Exception:
                                                    pass
                                                sc_val.image_path = str(target_img_val)
                                                sc_val.status = "done"
                                                self.log_signal.emit(f"      ✔ Scene #{idx_val+1} OK: {target_img_val.name}")
                                                return
                                            else:
                                                sc_val.status = "error"
                                                self.log_signal.emit(f"      ❌ Scene #{idx_val+1} Thất bại lần {attempt}: {job.error or 'Không rõ'}")
                                        except Exception as ex_single:
                                            sc_val.status = "error"
                                            self.log_signal.emit(f"      ❌ Scene #{idx_val+1} Exception lần {attempt}: {ex_single}")
                                            
                                        if attempt < retry_cnt + 1:
                                            import random
                                            await asyncio.sleep(random.uniform(3.0, 5.0))

                                tasks = [process_single_veo(idx_val, sc_val, target_img_val, chunk_idx) for chunk_idx, (idx_val, sc_val, target_img_val) in enumerate(chunk)]
                                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                            except Exception as e:
                                self.log_signal.emit(f"    ❌ Lỗi luồng song song đợt Google Labs: {e}")
                            finally:
                                try:
                                    loop.close()
                                except Exception:
                                    pass
                        else:
                            for idx_val, sc_val, target_img_val in chunk:
                                retry_cnt = self.settings.get("retry_count", 2)
                                for attempt in range(1, retry_cnt + 2):
                                    self.log_signal.emit(f"    🎨 Tạo ảnh cho Scene #{idx_val+1} (Lần {attempt}/{retry_cnt+1})...")
                                    try:
                                        ir = requests.post(
                                            f"{api_url}/v1/images/generations",
                                            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                            json={
                                                "model": img_m,
                                                "prompt": sc_val.prompt,
                                                "n": 1,
                                                "size": "1024x1280",
                                                "response_format": "b64_json"
                                            },
                                            timeout=600
                                        )
                                        if ir.status_code == 200:
                                            it = ir.json().get("data", [{}])[0]
                                            if "b64_json" in it:
                                                import base64
                                                target_img_val.write_bytes(base64.b64decode(it["b64_json"]))
                                                sc_val.image_path = str(target_img_val)
                                                sc_val.status = "done"
                                                self.log_signal.emit(f"      ✔ Lưu ảnh thành công: {target_img_val.name}")
                                                break
                                            else:
                                                sc_val.status = "error"
                                        else:
                                            sc_val.status = "error"
                                    except Exception as ex_single:
                                        sc_val.status = "error"
                                        self.log_signal.emit(f"      ❌ Lỗi sinh ảnh Scene #{idx_val+1}: {ex_single}")
                                        
                                    if sc_val.status == "error" and attempt < retry_cnt + 1:
                                        time.sleep(3)
                        
                        self.product_updated_signal.emit(prod.id)
                        
                        if i + burst_size < len(missing_scenes) and not self._stopped:
                            self.log_signal.emit(f"    ⏳ Nghỉ {burst_delay}s giữa các đợt (Khớp cơ chế Veo3)...")
                            time.sleep(burst_delay)
                
                # ── 3. Voice Generation ──
                target_voice = prod_voice_dir / f"voice_{prod.id}.mp3"
                if not only_vid:
                    if prod.voice_path and Path(prod.voice_path).exists() and not target_voice.exists():
                        try:
                            import shutil
                            shutil.copy2(prod.voice_path, target_voice)
                            prod.voice_path = str(target_voice)
                            prod.voice_duration = get_media_duration(target_voice)
                            self.log_signal.emit(f"    ✔ Sử dụng voice đã có sẵn: {target_voice.name} (Thời lượng: {prod.voice_duration}s)")
                        except Exception as e_cp:
                            self.log_signal.emit(f"    ⚠️ Lỗi sao chép voice có sẵn: {e_cp}")

                    if not prod.voice_path or not Path(prod.voice_path).exists() or not target_voice.exists():
                        self.log_signal.emit("  [3/4] Voice...")
                        vm_code = self.settings.get("voice_mode", "narrative_review")
                        main_voice = self.settings.get("vbee_voice", "vi_south_female_miien_48k")
                        sub_voices = self.settings.get("vbee_sub_voices", [])
                        vbee_token = self.settings.get("vbee_token")
                        vbee_appid = self.settings.get("vbee_appid")
                        vbee_speed = self.settings.get("vbee_speed", 1.0)
                        use_vbee = self.settings.get("voice_engine") == "vbee" and vbee_token
                        
                        # Per-scene voice generation for verbatim_read and hybrid modes
                        if use_vbee and vm_code in ("verbatim_read", "hybrid_narrate_read") and prod.scenes:
                            self.log_signal.emit(f"    🎭 Mode: {vm_code} — Tạo voice riêng từng phân cảnh...")
                            scene_audio_files = []
                            for sc in prod.scenes:
                                seg = sc.text_segment.strip()
                                if not seg:
                                    continue
                                # Ensure punctuation at end for natural pause
                                if seg and seg[-1] not in '.!?…,;:':
                                    seg += '.'
                                    
                                # Choose voice based on role
                                if vm_code == "hybrid_narrate_read":
                                    if sc.voice_role == "narrator":
                                        chosen_voice = main_voice
                                    else:
                                        chosen_voice = random.choice(sub_voices) if sub_voices else main_voice
                                else:  # verbatim_read — random voice for each CMT
                                    chosen_voice = random.choice(sub_voices) if sub_voices else main_voice
                                
                                self.log_signal.emit(f"      🎙️ Scene {sc.id} ({sc.voice_role or 'comment'}): voice={chosen_voice[:30]}...")
                                sc_audio = vbee_create_tts(
                                    seg, vbee_token, vbee_appid, chosen_voice, vbee_speed,
                                    prod_voice_dir, self.log_signal.emit
                                )
                                if sc_audio and Path(sc_audio).exists():
                                    sc_dur = get_media_duration(sc_audio)
                                    sc.voice_duration = sc_dur if sc_dur else 0.0
                                    scene_audio_files.append(sc_audio)
                                else:
                                    self.log_signal.emit(f"      ⚠️ Scene {sc.id}: Vbee thất bại, thử gTTS...")
                                    try:
                                        fallback = prod_voice_dir / f"gtts_sc{sc.id}_{uuid.uuid4().hex[:6]}.mp3"
                                        gTTS(text=seg, lang='vi').save(str(fallback))
                                        scene_audio_files.append(str(fallback))
                                    except Exception as e_gtts:
                                        self.log_signal.emit(f"      ❌ gTTS cũng thất bại: {e_gtts}")
                            
                            # Concatenate all scene audio files with silence gap
                            if scene_audio_files:
                                try:
                                    _concat_audio_with_silence(scene_audio_files, str(target_voice), silence_ms=400)
                                    prod.voice_path = str(target_voice)
                                    prod.voice_duration = get_media_duration(target_voice)
                                    self.log_signal.emit(f"    ✔ Đã ghép {len(scene_audio_files)} đoạn voice → {target_voice.name} ({prod.voice_duration}s)")
                                except Exception as e_concat:
                                    self.log_signal.emit(f"    ❌ Lỗi ghép audio: {e_concat}")
                                # Cleanup temp scene audio files
                                for sf in scene_audio_files:
                                    try: Path(sf).unlink(missing_ok=True)
                                    except: pass
                        else:
                            # Default: single voice for whole voiceover (narrative_review or fallback)
                            vt = prod.voiceover_text or prod.title
                            if use_vbee:
                                self.log_signal.emit(f"    🎙️ [Vbee] Tạo giọng đọc cho: {vt[:80]}...")
                                ov = vbee_create_tts(
                                    vt, vbee_token, vbee_appid, main_voice, vbee_speed,
                                    prod_voice_dir, self.log_signal.emit
                                )
                                if ov and Path(ov).exists():
                                    try: Path(ov).replace(target_voice)
                                    except Exception: pass
                                    prod.voice_path = str(target_voice)
                                    prod.voice_duration = get_media_duration(target_voice)
                                    self.log_signal.emit(f"    ✔ Đã tải Voice Vbee: {target_voice.name} (Thời lượng: {prod.voice_duration}s)")
                                else:
                                    self.log_signal.emit("    ❌ Lỗi tạo Vbee Voice. Thử chuyển đổi sang gTTS.")
                                
                        if not prod.voice_path or not Path(prod.voice_path).exists():
                            vt = prod.voiceover_text or prod.title
                            self.log_signal.emit(f"    🎙️ [gTTS] Tạo giọng đọc: {vt[:80]}...")
                            try:
                                gTTS(text=vt, lang='vi').save(str(target_voice))
                                prod.voice_path = str(target_voice)
                                prod.voice_duration = get_media_duration(target_voice)
                                self.log_signal.emit(f"    ✔ Đã tải gTTS Voice: {target_voice.name} (Thời lượng: {prod.voice_duration}s)")
                            except Exception as e:
                                self.log_signal.emit(f"    ❌ Lỗi tạo gTTS: {e}")
                    else:
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)
                        self.log_signal.emit(f"  [3/4] Bỏ qua Voice (đã có sẵn: {target_voice.name}, duration: {prod.voice_duration}s).")
                else:
                    if target_voice.exists():
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)
                        self.log_signal.emit(f"  [3/4] Bỏ qua Voice (Chế độ chỉ dựng Video, sử dụng file sẵn có: {target_voice.name}).")
                
                self.product_updated_signal.emit(prod.id)
                
                # ── 4. Edit Video Assembly ──
                try:
                    self.log_signal.emit("  [4/4] Edit Video...")
                    cbg = None
                    if bg_files:
                        ab = [b for b in bg_files if str(b) not in self.used_bg] if no_dup else bg_files
                        cbg = random.choice(ab if ab else bg_files)
                        if no_dup:
                            self.used_bg.add(str(cbg))
                    
                    target_video = prod_folder / f"final_{prod.id}.mp4"
                    
                    if cbg and Path(cbg).exists():
                        vs = [s for s in prod.scenes if s.image_path and Path(s.image_path).exists()]
                        if vs:
                            vd = prod.voice_duration if (prod.voice_duration and prod.voice_duration > 1.0) else 10.0
                            scene_gap_sec = 0.4  # Must match silence_ms in _concat_audio_with_silence
                            num_gaps = max(0, len(vs) - 1)
                            
                            # Strategy: detect actual voice segments from audio file
                            detected_segments = None  # list of (start, duration) 
                            if prod.voice_path and Path(prod.voice_path).exists():
                                self.log_signal.emit(f"    🔍 Phân tích audio để tìm timing chính xác...")
                                detected_segments = _detect_voice_segments(
                                    prod.voice_path, len(vs), self.log_signal.emit
                                )
                            
                            if detected_segments:
                                # Use detected segments — most accurate
                                sds = [seg[1] for seg in detected_segments]
                                seg_starts = [seg[0] for seg in detected_segments]
                                self.log_signal.emit(f"    📐 Dùng timing từ silence detection ✅")
                            elif all(s.voice_duration > 0 for s in vs):
                                # Use saved per-scene durations
                                sds = [s.voice_duration for s in vs]
                                seg_starts = None  # Will calculate from sds + gaps
                                self.log_signal.emit(f"    📐 Dùng duration thực tế từng scene: {[round(d,2) for d in sds]}")
                            else:
                                # Fallback: estimate proportionally by text length
                                total_gap = scene_gap_sec * num_gaps
                                usable_voice_time = max(1.0, vd - total_gap)
                                tc = sum(max(1, len(s.text_segment)) for s in vs)
                                sds = [max(1.5, round(usable_voice_time * (max(1, len(s.text_segment)) / tc), 2)) for s in vs]
                                seg_starts = None
                                self.log_signal.emit(f"    📐 ⚠️ Ước tính duration theo text (có thể lệch): {[round(d,2) for d in sds]}")
                            
                            self.log_signal.emit(f"    🎬 Bắt đầu FFmpeg ghép dựng video...")
                            self.log_signal.emit(f"      • Video nền: {Path(cbg).name}")
                            self.log_signal.emit(f"      • Thời lượng tổng: {vd:.2f}s (gap: {scene_gap_sec}s × {num_gaps})")
                            
                            bf = ["scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"]
                            if self.settings.get("anti_flip_h"):
                                bf.append("hflip")
                            if self.settings.get("anti_noise"):
                                bf.append("noise=alls=3:allf=t+u")
                            
                            ins = ["-stream_loop", "-1", "-i", str(cbg)]
                            has_audio = False
                            if prod.voice_path and Path(prod.voice_path).exists():
                                ins.extend(["-i", str(prod.voice_path)])
                                has_audio = True
                            
                            for s in vs:
                                ins.extend(["-i", str(s.image_path)])
                            
                            audio_idx = 1 if has_audio else None
                            img_start_idx = 2 if has_audio else 1
                            
                            fc = [f"[0:v]trim=0:{vd:.2f},setpts=PTS-STARTPTS,{','.join(bf)}[bg];"]
                            img_lead = 0.1  # Image appears 0.1s before voice
                            ls = "bg"
                            
                            self.log_signal.emit(f"      📊 Chi tiết timing từng scene:")
                            for si, dur in enumerate(sds):
                                # Get actual voice start time
                                if seg_starts:
                                    voice_st = seg_starts[si]
                                else:
                                    # Calculate from accumulated durations + gaps
                                    voice_st = sum(sds[:si]) + si * scene_gap_sec
                                voice_et = voice_st + dur
                                
                                # First scene image starts at 0.0. Subsequent scenes start 0.1s before their voice.
                                if si == 0:
                                    img_st = 0.0
                                else:
                                    img_st = max(0, voice_st - img_lead)
                                    
                                # Image ends exactly when next image starts. Last scene extends to video end.
                                if si < len(sds) - 1:
                                    if seg_starts:
                                        next_voice_st = seg_starts[si + 1]
                                    else:
                                        next_voice_st = voice_et + scene_gap_sec
                                    img_et = min(vd, next_voice_st - img_lead)
                                else:
                                    img_et = vd
                                
                                self.log_signal.emit(f"         Scene {si+1}: voice={voice_st:.2f}→{voice_et:.2f}s | img={img_st:.2f}→{img_et:.2f}s")
                                
                                os_ = f"v{si}"
                                input_stream = si + img_start_idx
                                fc.append(f"[{input_stream}:v]scale=960:-1[img{si}];[{ls}][img{si}]overlay=(W-w)/2:(H-h)/2:enable='between(t,{img_st:.2f},{img_et:.2f})'[{os_}];")
                                ls = os_
                            
                            cmd = [FFMPEG_PATH, "-y"] + ins + [
                                "-filter_complex", "".join(fc).rstrip(';')
                            ]
                            
                            if has_audio:
                                cmd.extend(["-map", f"[{ls}]", "-map", f"{audio_idx}:a", "-c:a", "aac", "-b:a", "192k"])
                            else:
                                cmd.extend(["-map", f"[{ls}]"])
                                
                            cmd.extend([
                                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                                "-t", f"{vd:.2f}",
                                str(target_video)
                            ])
                            
                            self.log_signal.emit(f"      • Đang ghép ảnh Threads vào CHÍNH GIỮA Video nền...")
                            rs = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                            if rs.returncode == 0 and target_video.exists():
                                prod.final_video_path = str(target_video)
                                prod.status = "✅ Hoàn thành"
                                self.log_signal.emit(f"      ✔ FFmpeg Dựng Phim OK: {target_video.name}")
                            else:
                                prod.status = "❌ Lỗi FFmpeg"
                                err_out = rs.stderr[:300] if rs.stderr else "Không rõ lỗi"
                                self.log_signal.emit(f"      ❌ FFmpeg Error (code {rs.returncode}): {err_out}")
                        else:
                            prod.status = "❌ Lỗi: Không có ảnh cảnh"
                            self.log_signal.emit("    ❌ Không tìm thấy ảnh scene nào hợp lệ để ghép video.")
                    else:
                        prod.final_video_path = ""
                        prod.status = "✔ Ảnh & Voice xong"
                        self.log_signal.emit("    ⚠️ Chưa cấu hình thư mục Video nền hoặc không tìm thấy video. Bỏ qua ghép final video.")
                except Exception as e_vid:
                    prod.status = "❌ Lỗi dựng phim"
                    self.log_signal.emit(f"    ❌ Lỗi trong tiến trình dựng video: {e_vid}")
                
                self.product_updated_signal.emit(prod.id)
                
                # ─── [5/5] VIDEO REVIEW SẢN PHẨM ──────────────────────────
                vr_enabled = self.settings.get("vr_enabled", False)
                vr_mode = self.settings.get("vr_mode", "")
                if vr_enabled and vr_mode and prod.final_combined_path and Path(prod.final_combined_path).exists():
                    if not self.settings.get("re_video_review", False):
                        self.log_signal.emit("    ✔ Video Review đã có sẵn, bỏ qua theo tùy chọn.")
                        vr_enabled = False
                
                if vr_enabled and vr_mode and prod.final_video_path and Path(prod.final_video_path).exists():
                    try:
                        self.log_signal.emit(f"  [5/5] Video Review Sản Phẩm (mode: {vr_mode})...")
                        prod_dir = Path(prod.final_video_path).parent
                        
                        if vr_mode == "external_file":
                            ext_path = prod.external_video_path or self.settings.get("vr_external_path", "")
                            if ext_path and Path(ext_path).exists():
                                self.log_signal.emit(f"    📹 Xử lý video ngoài: {Path(ext_path).name}")
                                vr_processed = prod_dir / f"vr_processed_{prod.id}.mp4"
                                result = process_external_video(
                                    str(ext_path), str(vr_processed),
                                    self.settings, self.log_signal.emit
                                )
                                if result and Path(result).exists():
                                    # Tạo voice review (dùng voice chính)
                                    vr_voice_text = f"Đây là video review thực tế sản phẩm {prod.title}."
                                    # Voice cho phần video review (optional - nếu có voiceover)
                                    vr_with_voice = prod_dir / f"vr_with_voice_{prod.id}.mp4"
                                    # Ghép voice vào video review nếu cần, hoặc dùng video không voice
                                    
                                    # Combine Part1 + Part2
                                    combined_path = prod_dir / f"combined_{prod.id}.mp4"
                                    self.log_signal.emit(f"    🔗 Ghép video: Phần ảnh + Phần video review...")
                                    final = combine_final_video(
                                        prod.final_video_path, str(result),
                                        str(combined_path), "direct_cut",
                                        self.log_signal.emit
                                    )
                                    if final:
                                        prod.final_combined_path = final
                                        prod.external_video_path = ext_path
                                        prod.video_review_mode = "external_file"
                                        prod.status = "✅ Hoàn thành (+ Video Review)"
                                        self.log_signal.emit(f"    ✅ Video Review OK: {Path(final).name}")
                                        self.product_updated_signal.emit(prod.id)
                                    else:
                                        self.log_signal.emit(f"    ⚠️ Lỗi ghép video review, giữ video gốc")
                                else:
                                    self.log_signal.emit(f"    ⚠️ Không xử lý được video ngoài")
                            else:
                                self.log_signal.emit(f"    ⚠️ Chưa chọn file video ngoài")
                        
                        elif vr_mode == "ai_generate":
                            if not has_grok_deps:
                                self.log_signal.emit(f"    ❌ Không tìm thấy grok_video_tool.py. Cần cài đặt module.")
                            else:
                                self.log_signal.emit(f"    🤖 AI Video Generation Pipeline...")
                                vr_rs_name = self.settings.get("vr_role_skill", "Video AI Mỹ Phẩm.MD")
                                vr_rs_dir = Path(__file__).resolve().parent / "data" / "Role Skill"
                                vr_rs_path = vr_rs_dir / vr_rs_name
                                vr_rs_text = ""
                                if vr_rs_path.exists():
                                    vr_rs_text = vr_rs_path.read_text(encoding="utf-8")
                                
                                vr_scene_count = self.settings.get("vr_scene_count", 4)
                                vr_video_model = self.settings.get("vr_video_model", "Veo 3.1 - Lite [Lower Priority]")
                                vr_resolution = self.settings.get("vr_resolution", "720p")
                                vr_clip_dur = self.settings.get("vr_clip_duration", 8)
                                vr_aspect = self.settings.get("vr_aspect_ratio", "9:16")
                                vr_img_model = self.settings.get("img_model", "gpt-image-2")
                                
                                api_url = self.settings.get("api_url", DEFAULT_PROXY_URL)
                                api_key = self.settings.get("api_key", "grok-video-key-001")
                                llm_model = self.settings.get("llm_model", "gpt-5.5")
                                
                                self.log_signal.emit(f"      • Video Model: {vr_video_model} | Resolution: {vr_resolution}")
                                self.log_signal.emit(f"      • Scenes: {vr_scene_count} | Clip: {vr_clip_dur}s | Aspect: {vr_aspect}")
                                self.log_signal.emit(f"      • Role Skill: {vr_rs_name}")
                                
                                # ── 5a. LLM tạo kịch bản video review ──
                                self.log_signal.emit(f"    📝 [5a] LLM tạo kịch bản video review ({llm_model})...")
                                vr_system_prompt = (
                                    f"{vr_rs_text}\n\n"
                                    f"Bạn là chuyên gia tạo kịch bản video review sản phẩm.\n"
                                    f"Tạo CHÍNH XÁC {vr_scene_count} phân cảnh video review cho sản phẩm.\n"
                                    f"Mỗi phân cảnh gồm:\n"
                                    f"- image_prompt: Prompt tiếng Anh mô tả ảnh tĩnh để AI sinh (ảnh sản phẩm, ứng dụng thực tế)\n"
                                    f"- video_prompt: Prompt tiếng Anh mô tả chuyển động video từ ảnh đó (camera movement, product action)\n"
                                    f"- voiceover_text: Lời dẫn tiếng Việt cho phân cảnh (~3-5 giây đọc)\n"
                                    f"Trả về JSON format:\n"
                                    f'{{"scenes": [{{"image_prompt": "...", "video_prompt": "...", "voiceover_text": "..."}}]}}\n'
                                    f"Aspect ratio: {vr_aspect}. Thời lượng mỗi clip: {vr_clip_dur}s."
                                )
                                # Encode images as base64 for vision LLM analysis (VR script generation)
                                vr_user_content = [{"type": "text", "text": f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info[:500]}"}]
                                import base64 as _b64
                                
                                def _get_image_block_vr(img_p):
                                    try:
                                        if img_p and Path(img_p).exists():
                                            with open(img_p, "rb") as f_img:
                                                data_b64 = _b64.b64encode(f_img.read()).decode("utf-8")
                                            suffix = Path(img_p).suffix.lower()
                                            mime = "image/png"
                                            if suffix in [".jpg", ".jpeg"]:
                                                mime = "image/jpeg"
                                            elif suffix == ".webp":
                                                mime = "image/webp"
                                            return {
                                                "type": "image_url",
                                                "image_url": {"url": f"data:{mime};base64,{data_b64}"}
                                            }
                                    except Exception as e_enc:
                                        self.log_signal.emit(f"  ⚠️ Lỗi encode ảnh {img_p} cho LLM: {e_enc}")
                                    return None

                                if prod.images:
                                    blk = _get_image_block_vr(prod.images[0])
                                    if blk: vr_user_content.append(blk)
                                
                                for ref_p in prod.images[1:]:
                                    blk = _get_image_block_vr(ref_p)
                                    if blk: vr_user_content.append(blk)

                                vr_user_msg = vr_user_content if len(vr_user_content) > 1 else f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info[:500]}"
                                
                                try:
                                    vr_llm_r = requests.post(
                                        f"{api_url}/v1/chat/completions",
                                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                        json={
                                            "model": llm_model,
                                            "messages": [
                                                {"role": "system", "content": vr_system_prompt},
                                                {"role": "user", "content": vr_user_msg}
                                            ],
                                            "temperature": 0.7
                                        },
                                        timeout=600
                                    )
                                    if vr_llm_r.status_code != 200:
                                        self.log_signal.emit(f"    ❌ LLM Error: {vr_llm_r.status_code}")
                                        raise RuntimeError(f"LLM failed: {vr_llm_r.status_code}")
                                    
                                    vr_content = vr_llm_r.json()["choices"][0]["message"]["content"]
                                    vr_script = parse_llm_json_robust(vr_content)
                                    vr_scenes_data = vr_script.get("scenes", [])
                                    self.log_signal.emit(f"    ✔ Kịch bản: {len(vr_scenes_data)} phân cảnh video")
                                    
                                    # Save script
                                    vr_script_file = prod_dir / "vr_script.json"
                                    vr_script_file.write_text(json.dumps(vr_script, indent=2, ensure_ascii=False), encoding="utf-8")
                                    
                                except Exception as e_llm:
                                    self.log_signal.emit(f"    ❌ Lỗi LLM kịch bản video: {e_llm}")
                                    vr_scenes_data = []
                                
                                if vr_scenes_data:
                                    vr_clips = []
                                    vr_clip_dir = prod_dir / "vr_clips"
                                    vr_clip_dir.mkdir(exist_ok=True)
                                    
                                    # Initialize VideoReviewScene objects for UI tracking
                                    prod.video_review_scenes = []
                                    for si_init, sd_init in enumerate(vr_scenes_data):
                                        vrs = VideoReviewScene(
                                            id=si_init + 1,
                                            scene_title=f"Scene {si_init + 1}",
                                            image_prompt=sd_init.get("image_prompt", ""),
                                            video_prompt=sd_init.get("video_prompt", ""),
                                            voiceover_text=sd_init.get("voiceover_text", ""),
                                            status="pending"
                                        )
                                        prod.video_review_scenes.append(vrs)
                                    self.product_updated_signal.emit(prod.id)
                                    
                                    grok_client = GrokClient(api_url, api_key)
                                    use_veo = is_veo_model(vr_video_model)
                                    
                                    for si, sc_data in enumerate(vr_scenes_data):
                                        if self._stopped:
                                            break
                                        img_prompt = sc_data.get("image_prompt", "")
                                        vid_prompt = sc_data.get("video_prompt", "")
                                        if not img_prompt:
                                            continue
                                        
                                        self.log_signal.emit(f"    🖼 [5b] Scene {si+1}/{len(vr_scenes_data)}: Tạo ảnh...")
                                        
                                        # ── 5b. AI tạo ảnh cho phân cảnh ──
                                        img_path = vr_clip_dir / f"scene_{si+1}.png"
                                        try:
                                            loop = asyncio.new_event_loop()
                                            asyncio.set_event_loop(loop)
                                            
                                            img_type, img_data = loop.run_until_complete(
                                                grok_client.generate_image(
                                                    prompt=img_prompt,
                                                    model=vr_img_model,
                                                    size="1024x1792" if "9:16" in vr_aspect else ("1792x1024" if "16:9" in vr_aspect else "1024x1024"),
                                                    use_proxy=False
                                                )
                                            )
                                            
                                            if img_type == "base64":
                                                import base64 as _b64
                                                img_path.write_bytes(_b64.b64decode(img_data))
                                            elif img_type == "url":
                                                img_resp = requests.get(img_data, timeout=60)
                                                img_path.write_bytes(img_resp.content)
                                            
                                            self.log_signal.emit(f"      ✔ Ảnh scene {si+1}: {img_path.name}")
                                            # Update VR scene tracking
                                            if si < len(prod.video_review_scenes):
                                                prod.video_review_scenes[si].image_path = str(img_path)
                                                prod.video_review_scenes[si].status = "processing"
                                                self.product_updated_signal.emit(prod.id)
                                        except Exception as e_img:
                                            self.log_signal.emit(f"      ❌ Lỗi tạo ảnh scene {si+1}: {e_img}")
                                            if si < len(prod.video_review_scenes):
                                                prod.video_review_scenes[si].status = "error"
                                                prod.video_review_scenes[si].error_msg = str(e_img)
                                            continue
                                        
                                        if not img_path.exists():
                                            continue
                                        
                                        # ── 5c. AI tạo video clip (image-to-video) ──
                                        self.log_signal.emit(f"    🎬 [5c] Scene {si+1}: Tạo video clip (I2V)...")
                                        vid_out = vr_clip_dir / f"clip_{si+1}.mp4"
                                        
                                        full_vid_prompt = f"{vid_prompt}. {img_prompt}" if vid_prompt else img_prompt
                                        
                                        try:
                                            loop = asyncio.new_event_loop()
                                            asyncio.set_event_loop(loop)
                                            
                                            vr_job = VideoJob(
                                                id=uuid.uuid4().hex[:8],
                                                mode="image-to-video",
                                                prompt=full_vid_prompt,
                                                model=vr_video_model,
                                                duration=vr_clip_dur,
                                                resolution=vr_resolution,
                                                aspect_ratio=vr_aspect,
                                                image_url=str(img_path),
                                                local_path=str(vid_out),
                                                output_name=f"vr_clip_{si+1}",
                                                max_retries=2
                                            )
                                            
                                            if use_veo:
                                                # Google Veo Flow: cần bearer token + captcha bridge
                                                veo_bearer = self.settings.get("veo_bearer", "")
                                                veo_project_id = self.settings.get("veo_project_id", "") or GROK_DEFAULT_PROJECT_ID
                                                cb = getattr(self, "captcha_bridge", None) or self.captcha_bridge
                                                
                                                success = loop.run_until_complete(
                                                    run_veo_flow(
                                                        vr_job, veo_bearer, veo_project_id, cb,
                                                        log=self.log_signal.emit, progress=None
                                                    )
                                                )
                                            else:
                                                # Grok Flow: submit → poll → download
                                                try:
                                                    rid = loop.run_until_complete(grok_client.submit(vr_job))
                                                    self.log_signal.emit(f"      📤 Submitted: {rid[:12]}...")
                                                    
                                                    # Poll until done
                                                    for _ in range(90):  # max 15 min
                                                        time.sleep(10)
                                                        poll_r = loop.run_until_complete(grok_client.poll(rid, use_proxy=False))
                                                        st = poll_r.get("status", "")
                                                        if st == "completed":
                                                            vid_url = poll_r.get("video_url", "") or poll_r.get("url", "")
                                                            if vid_url:
                                                                loop.run_until_complete(grok_client.download(vid_url, vid_out, use_proxy=False))
                                                            success = True
                                                            break
                                                        elif st == "failed":
                                                            success = False
                                                            break
                                                    else:
                                                        success = False
                                                except Exception as e_grok:
                                                    self.log_signal.emit(f"      ❌ Grok Error: {e_grok}")
                                                    success = False
                                            
                                            loop.close()
                                            
                                            if success and vid_out.exists():
                                                vr_clips.append(str(vid_out))
                                                self.log_signal.emit(f"      ✔ Video clip {si+1}: {vid_out.name}")
                                                # Update VR scene tracking
                                                if si < len(prod.video_review_scenes):
                                                    prod.video_review_scenes[si].video_clip_path = str(vid_out)
                                                    prod.video_review_scenes[si].status = "done"
                                                    self.product_updated_signal.emit(prod.id)
                                            else:
                                                self.log_signal.emit(f"      ❌ Clip {si+1} failed (success={success})")
                                                if si < len(prod.video_review_scenes):
                                                    prod.video_review_scenes[si].status = "error"
                                                
                                        except Exception as e_vid:
                                            self.log_signal.emit(f"      ❌ Lỗi tạo video clip {si+1}: {e_vid}")
                                        
                                        time.sleep(2)  # Delay giữa các scene
                                    
                                    # ── 5e. FFmpeg ghép tất cả clips thành Part 2 ──
                                    if vr_clips:
                                        self.log_signal.emit(f"    🔗 [5e] Ghép {len(vr_clips)} clips → Part 2...")
                                        vr_part2 = prod_dir / f"vr_part2_{prod.id}.mp4"
                                        
                                        if len(vr_clips) == 1:
                                            shutil.copy2(vr_clips[0], str(vr_part2))
                                        else:
                                            concat_file = vr_clip_dir / "concat_clips.txt"
                                            with open(concat_file, "w", encoding="utf-8") as cf:
                                                for cp in vr_clips:
                                                    cf.write(f"file '{cp}'\n")
                                            cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0",
                                                   "-i", str(concat_file), "-c", "copy", str(vr_part2)]
                                            try:
                                                subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                                                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                                            except Exception as e_concat:
                                                self.log_signal.emit(f"      ❌ Lỗi ghép clips: {e_concat}")
                                                # Fallback: re-encode
                                                cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0",
                                                       "-i", str(concat_file), "-c:v", "libx264", "-preset", "fast",
                                                       "-crf", "22", str(vr_part2)]
                                                subprocess.run(cmd, capture_output=True, timeout=300,
                                                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                                        
                                        if vr_part2.exists():
                                            # ── 5f. Combine Part1 + Part2 ──
                                            self.log_signal.emit(f"    🔗 [5f] Ghép Part1 (ảnh CMT) + Part2 (video AI)...")
                                            combined_path = prod_dir / f"combined_{prod.id}.mp4"
                                            final = combine_final_video(
                                                prod.final_video_path, str(vr_part2),
                                                str(combined_path), "fade_black",
                                                self.log_signal.emit
                                            )
                                            if final:
                                                prod.final_combined_path = final
                                                prod.video_review_mode = "ai_generate"
                                                prod.status = "✅ Hoàn thành (+ AI Video Review)"
                                                self.log_signal.emit(f"    ✅ AI Video Review OK: {Path(final).name}")
                                            else:
                                                self.log_signal.emit(f"    ⚠️ Lỗi ghép video, giữ video gốc")
                                        else:
                                            self.log_signal.emit(f"    ⚠️ Không tạo được Part 2 từ clips")
                                    else:
                                        self.log_signal.emit(f"    ⚠️ Không có clip nào tạo thành công")
                    
                    except Exception as e_vr:
                        self.log_signal.emit(f"    ⚠️ Lỗi Video Review: {e_vr}")
                
                self.product_updated_signal.emit(prod.id)
                time.sleep(self.settings.get("delay_sec", 2))
                
            self.finished_signal.emit(True, "🎉 Hoàn tất!")
        except Exception as e:
            self.finished_signal.emit(False, f"Lỗi hệ thống: {e}")


# ─── STEALTH SCRIPTS FOR PLAYWRIGHT ───────────────────────────────────
def inject_stealth_scripts(context, timezone_id=None):
    tz = timezone_id or "Asia/Ho_Chi_Minh"
    webgl_vendors = ["Google Inc. (Intel)", "Google Inc. (NVIDIA)", "Google Inc. (AMD)"]
    webgl_renderers = [
        "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)",
        "ANGLE (AMD, Radeon(TM) RX 580 Series Direct3D11 vs_5_0 ps_5_0, D3D11)"
    ]
    idx = random.randint(0, len(webgl_vendors)-1)
    vendor = webgl_vendors[idx]
    renderer = webgl_renderers[idx]
    cores = random.choice([4, 6, 8, 12, 16])
    memory = random.choice([8, 16, 32])
    
    stealth_js = f"""
    (function() {{
        'use strict';
        try {{
            Object.defineProperty(navigator, 'webdriver', {{
                get: () => undefined,
                configurable: true
            }});
        }} catch(e) {{}}

        const originalGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return "{vendor}";
            if (parameter === 37446) return "{renderer}";
            return originalGetParameter.apply(this, arguments);
        }};

        if (typeof WebGL2RenderingContext !== 'undefined') {{
            const originalGetParameter2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(parameter) {{
                if (parameter === 37445) return "{vendor}";
                if (parameter === 37446) return "{renderer}";
                return originalGetParameter2.apply(this, arguments);
            }};
        }}

        let canvasFingerprint = Math.floor(Math.random() * 1000000);
        const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
        const originalToBlob = HTMLCanvasElement.prototype.toBlob;
        const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;

        function injectCanvasNoise(canvas) {{
            if (canvas.width <= 16 && canvas.height <= 16) return;
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            try {{
                const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                const data = imageData.data;
                for (let i = 0; i < data.length; i += 4) {{
                    if (Math.random() < 0.01) {{
                        data[i]     = (data[i]     + (canvasFingerprint % 10)) % 256;
                        data[i + 1] = (data[i + 1] + (canvasFingerprint % 7))  % 256;
                        data[i + 2] = (data[i + 2] + (canvasFingerprint % 5))  % 256;
                    }}
                }}
                ctx.putImageData(imageData, 0, 0);
            }} catch (e) {{}}
        }}

        HTMLCanvasElement.prototype.toDataURL = function() {{
            injectCanvasNoise(this);
            return originalToDataURL.apply(this, arguments);
        }};

        HTMLCanvasElement.prototype.toBlob = function() {{
            injectCanvasNoise(this);
            return originalToBlob.apply(this, arguments);
        }};
    }})();
    """
    try:
        context.add_init_script(stealth_js)
    except Exception:
        pass


# ─── OAUTH & LOGIN WORKERS ────────────────────────────────────────────
class GoogleLoginWorker(QThread):
    status_sig = pyqtSignal(str, str)
    success_sig = pyqtSignal(str, str, str)
    error_sig = pyqtSignal(str)

    def __init__(self, profile_name="default"):
        super().__init__()
        self.profile_name = profile_name
        self.is_running = True
        self.context = None
        self.browser = None
        self.playwright = None

    def stop(self):
        self.is_running = False
        try:
            if self.context: self.context.close()
        except: pass

    def run(self):
        from playwright.sync_api import sync_playwright
        import urllib.parse
        
        self.status_sig.emit("🔍 Đang chuẩn bị trình duyệt...", "#7c3aed")
        kol_dir = CURRENT_DIR.parent / "KOL Veo 3"
        shared_dir = (kol_dir / "Flow_nhieu_worker_data") if kol_dir.exists() else (CURRENT_DIR / "Flow_nhieu_worker_data")
        profile_dir = shared_dir / "profiles" / self.profile_name
        profile_dir.mkdir(parents=True, exist_ok=True)
        cookies_file = shared_dir / "cookies" / f"{self.profile_name}.json"
        cookies_file.parent.mkdir(parents=True, exist_ok=True)
        
        JS_SESSION = "async () => {try {const r = await fetch('https://labs.google/fx/api/auth/session', {credentials: 'include'});if (!r.ok) return null;const j = await r.json();return j.access_token || null;} catch(e) { return null; }}"
        JS_EMAIL = "async () => {try {const r = await fetch('https://labs.google/fx/api/auth/session', {credentials: 'include'});if (!r.ok) return null;const j = await r.json();return j.user ? (j.user.email || j.user.name || null) : null;} catch(e) { return null; }}"
        
        try:
            self.playwright = sync_playwright().start()
            chrome_args = ["--disable-blink-features=AutomationControlled", "--no-first-run", "--no-sandbox"]
            
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=chrome_args
            )
            inject_stealth_scripts(self.context)
            page = self.context.pages[0] if self.context.pages else self.context.new_page()
            
            token_found = [None]
            project_found = [None]
            email_found = [None]
            
            self.status_sig.emit("🌐 Đang mở trình duyệt Google Flow...", "#2563eb")
            try:
                page.goto("https://labs.google/fx/tools/flow?project=378bfc6c-422d-4bfb-92d1-6e978432ca7b", timeout=30000, wait_until="domcontentloaded")
            except: pass
            
            waited = 0
            success_emitted = False
            last_warned_email = None

            while self.is_running and waited < 600:
                if not self.is_running: break
                
                try:
                    if not self.context.pages or self.context.pages[0].is_closed():
                        break
                except: break

                curr_token = None
                curr_email = None
                try:
                    curr_token = page.evaluate(JS_SESSION)
                except: pass

                if not project_found[0]:
                    try:
                        qs = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
                        if "project" in qs: project_found[0] = qs["project"][0]
                    except: pass

                if curr_token and len(curr_token) > 20:
                    try:
                        curr_email = page.evaluate(JS_EMAIL)
                    except: pass

                    if not curr_email:
                        try:
                            import httpx
                            r = httpx.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {curr_token}"}, timeout=5)
                            if r.status_code == 200:
                                d = r.json()
                                curr_email = d.get("email") or d.get("name")
                        except: pass

                    if curr_email:
                        target_clean = self.profile_name.strip().lower()
                        detected_clean = curr_email.strip().lower()

                        # Check if user is logged into the WRONG account
                        if target_clean != "default" and "@" in target_clean and detected_clean != target_clean:
                            if last_warned_email != curr_email:
                                self.status_sig.emit(
                                    f"⚠️ Đang đăng nhập: [{curr_email}] nhưng cần: [{self.profile_name}]! Vui lòng bấm Đăng Xuất trên trình duyệt và Đăng Nhập lại [{self.profile_name}]...",
                                    "#d97706"
                                )
                                last_warned_email = curr_email
                            time.sleep(2)
                            waited += 2
                            continue

                        # Correct account matched! Save cookies
                        try:
                            cookies_file.write_text(json.dumps(self.context.storage_state(), ensure_ascii=False, indent=2), "utf-8")
                        except: pass

                        if not success_emitted:
                            self.status_sig.emit(f"✅ Đã lưu cookies thành công cho [{curr_email}]! Trình duyệt vẫn mở, bạn có thể tự đóng khi xong.", "#16a34a")
                            self.success_sig.emit(curr_email, curr_token, project_found[0] or "378bfc6c-422d-4bfb-92d1-6e978432ca7b")
                            success_emitted = True

                        # Keep browser open for user to interact or close manually
                        time.sleep(2)
                        waited += 2
                        continue

                time.sleep(1)
                waited += 1

            if not success_emitted and not token_found[0]:
                self.error_sig.emit("Chưa hoàn tất đăng nhập Google.")
        except Exception as e:
            self.error_sig.emit(str(e))
        finally:
            try:
                if self.context: self.context.close()
                if self.playwright: self.playwright.stop()
            except: pass


class GoogleTokenRefreshWorker(QThread):
    status_sig = pyqtSignal(str, str)
    token_updated = pyqtSignal(str, str, str, int)
    finished_all = pyqtSignal(int, int)
    log_sig = pyqtSignal(str)

    def __init__(self, accounts: list):
        super().__init__()
        self.accounts = accounts
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        valid = 0
        expired = 0
        for acc in self.accounts:
            if not self.is_running: break
            acc_name = acc.name
            token = acc.api_key
            profile_name = acc_name.replace("Google_", "", 1) if acc_name.startswith("Google_") else acc_name
            
            self.status_sig.emit(f"🔍 Đang kiểm tra token cho {profile_name}...", "#2563eb")
            self.log_sig.emit(f"🔍 [Token Check] Kiểm tra hiệu lực token: {profile_name}")
            
            is_valid = False
            email_or_err = "Token trống"
            if token and len(token) >= 20:
                try: is_valid, email_or_err = self._check_token(token)
                except Exception as e: email_or_err = str(e)
            
            credits_val = -1
            if is_valid:
                self.log_sig.emit(f"✅ [Token Check] {profile_name}: Token còn hiệu lực ({email_or_err})")
                self.status_sig.emit(f"💰 Đang đồng bộ credit cho {profile_name}...", "#8b5cf6")
                success_cr, balance_cr, limit_cr, err_cr = self._try_check_credits(profile_name)
                if success_cr:
                    credits_val = balance_cr
                    self.log_sig.emit(f"💰 [Token Check] {profile_name}: Credit còn lại: {balance_cr}")
                self.token_updated.emit(acc_name, token, acc.proxy_url or DEFAULT_PROJECT_ID, credits_val)
                valid += 1
            else:
                self.log_sig.emit(f"⚠️ [Token Check] {profile_name}: Hết hạn ({email_or_err}). Đang tự động làm mới...")
                self.status_sig.emit(f"⏳ Đang tự động làm mới token cho {profile_name}...", "#f59e0b")
                
                success, new_token, new_project_id, err_msg = self._try_headless_refresh(profile_name)
                if success:
                    is_valid_new, new_email = self._check_token(new_token)
                    if is_valid_new:
                        self.log_sig.emit(f"✅ [Token Check] {profile_name}: Tự động refresh thành công! ({new_email})")
                        self.status_sig.emit(f"💰 Đang đồng bộ credit cho {profile_name}...", "#8b5cf6")
                        success_cr, balance_cr, limit_cr, err_cr = self._try_check_credits(profile_name)
                        if success_cr:
                            credits_val = balance_cr
                            self.log_sig.emit(f"💰 [Token Check] {profile_name}: Credit còn lại: {balance_cr}")
                        self.token_updated.emit(acc_name, new_token, new_project_id, credits_val)
                        valid += 1
                        continue
                self.log_sig.emit(f"❌ [Token Check] {profile_name}: Không thể tự động làm mới: {err_msg}")
                expired += 1
                
        self.finished_all.emit(valid, expired)

    @staticmethod
    def _try_headless_refresh(profile_name: str) -> tuple:
        import urllib.parse
        from playwright.sync_api import sync_playwright
        kol_dir = CURRENT_DIR.parent / "KOL Veo 3"
        shared_dir = (kol_dir / "Flow_nhieu_worker_data") if kol_dir.exists() else (CURRENT_DIR / "Flow_nhieu_worker_data")
        profile_dir = shared_dir / "profiles" / profile_name
        cookies_file = shared_dir / "cookies" / f"{profile_name}.json"
        
        use_cookie_file = cookies_file.exists()
        use_profile_dir = profile_dir.exists()
        if not use_cookie_file and not use_profile_dir:
            return False, "", "", "Không tìm thấy file cookies hoặc profile."
            
        JS_SESSION = "async () => {try {const r = await fetch('https://labs.google/fx/api/auth/session', {credentials: 'include'});if (!r.ok) return null;const j = await r.json();return j.access_token || null;} catch(e) { return null; }}"
        FLOW_URL = "https://labs.google/fx/tools/flow?project=378bfc6c-422d-4bfb-92d1-6e978432ca7b"
        
        playwright = None
        browser = None
        context = None
        try:
            playwright = sync_playwright().start()
            chrome_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            
            if use_cookie_file:
                browser = playwright.chromium.launch(headless=True, args=chrome_args)
                context = browser.new_context(storage_state=str(cookies_file))
            else:
                context = playwright.chromium.launch_persistent_context(user_data_dir=str(profile_dir), headless=True, args=chrome_args)
            
            inject_stealth_scripts(context)
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(FLOW_URL, timeout=25000, wait_until="domcontentloaded")
            time.sleep(5)
            
            btn = page.locator("text=Create with Google Flow").first
            try:
                if btn.count() > 0:
                    btn.click()
                    time.sleep(5)
            except: pass
            
            token = page.evaluate(JS_SESSION)
            project_id = "378bfc6c-422d-4bfb-92d1-6e978432ca7b"
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
                if "project" in qs: project_id = qs["project"][0]
            except: pass
                
            if token and len(token) > 20:
                try:
                    cookies_file.write_text(json.dumps(context.storage_state(), ensure_ascii=False, indent=2), "utf-8")
                except: pass
                return True, token, project_id, ""
            else:
                needs_login = "accounts.google.com" in page.url
                # Close headless browser before trying visible
                try:
                    if context: context.close()
                    if browser: browser.close()
                    if playwright: playwright.stop()
                except: pass
                context = None; browser = None; playwright = None
                
                if needs_login and use_profile_dir:
                    # Fallback: open VISIBLE browser with saved profile for auto-login
                    return GoogleTokenRefreshWorker._try_visible_browser_login(profile_name, profile_dir, cookies_file, JS_SESSION, FLOW_URL)
                elif needs_login:
                    return False, "", "", "Google yêu cầu đăng nhập lại."
                return False, "", "", "Không tìm thấy token."
        except Exception as e:
            return False, "", "", str(e)
        finally:
            try:
                if context: context.close()
                if browser: browser.close()
                if playwright: playwright.stop()
            except: pass

    @staticmethod
    def _try_visible_browser_login(profile_name: str, profile_dir: Path, cookies_file: Path, JS_SESSION: str, FLOW_URL: str) -> tuple:
        """Open a visible browser with saved profile to auto-login when headless fails."""
        import urllib.parse
        from playwright.sync_api import sync_playwright
        
        playwright = None
        context = None
        try:
            # Remove lock files that block profile reuse
            for lock_file in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
                fp = profile_dir / lock_file
                if fp.exists():
                    try: fp.unlink()
                    except: pass
            
            playwright = sync_playwright().start()
            chrome_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-sandbox"
            ]
            
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                args=chrome_args
            )
            inject_stealth_scripts(context)
            page = context.pages[0] if context.pages else context.new_page()
            
            try:
                page.goto(FLOW_URL, timeout=30000, wait_until="domcontentloaded")
            except: pass
            
            # Wait up to 90 seconds for login to complete (auto or manual)
            waited = 0
            max_wait = 90
            token = None
            project_id = "378bfc6c-422d-4bfb-92d1-6e978432ca7b"
            
            while waited < max_wait:
                try:
                    # Check if the page has navigated away from accounts.google.com
                    current_url = page.url
                    
                    # Try to click "Create with Google Flow" button if present
                    try:
                        btn = page.locator("text=Create with Google Flow").first
                        if btn.count() > 0:
                            btn.click()
                            time.sleep(3)
                    except: pass
                    
                    token = page.evaluate(JS_SESSION)
                    if token and len(token) > 20:
                        try:
                            qs = urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
                            if "project" in qs: project_id = qs["project"][0]
                        except: pass
                        
                        # Save updated cookies
                        try:
                            cookies_file.parent.mkdir(parents=True, exist_ok=True)
                            cookies_file.write_text(json.dumps(context.storage_state(), ensure_ascii=False, indent=2), "utf-8")
                        except: pass
                        
                        return True, token, project_id, ""
                except: pass
                
                time.sleep(2)
                waited += 2
            
            return False, "", "", "Hết thời gian chờ đăng nhập (90s). Vui lòng thử lại."
        except Exception as e:
            return False, "", "", f"Lỗi mở trình duyệt: {str(e)}"
        finally:
            try:
                if context: context.close()
                if playwright: playwright.stop()
            except: pass

    @staticmethod
    def _check_token(token: str):
        try:
            r = httpx.get("https://www.googleapis.com/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                return True, data.get("email") or "OK"
            return False, f"HTTP {r.status_code}"
        except Exception as e:
            return False, str(e)

    @staticmethod
    def _try_check_credits(profile_name: str) -> tuple:
        from playwright.sync_api import sync_playwright
        kol_dir = CURRENT_DIR.parent / "KOL Veo 3"
        shared_dir = (kol_dir / "Flow_nhieu_worker_data") if kol_dir.exists() else (CURRENT_DIR / "Flow_nhieu_worker_data")
        profile_dir = shared_dir / "profiles" / profile_name
        cookies_file = shared_dir / "cookies" / f"{profile_name}.json"
        
        use_cookie_file = cookies_file.exists()
        use_profile_dir = profile_dir.exists()
        if not use_cookie_file and not use_profile_dir:
            return False, 0, 0, "No profile/cookies"
            
        playwright = None
        browser = None
        context = None
        try:
            playwright = sync_playwright().start()
            chrome_args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
            
            if use_cookie_file:
                browser = playwright.chromium.launch(headless=True, args=chrome_args)
                context = browser.new_context(storage_state=str(cookies_file))
            else:
                for f in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
                    fp = profile_dir / f
                    if fp.exists():
                        try: fp.unlink()
                        except: pass
                context = playwright.chromium.launch_persistent_context(user_data_dir=str(profile_dir), headless=True, args=chrome_args)
            
            inject_stealth_scripts(context)
            page = context.pages[0] if context.pages else context.new_page()
            url = "https://one.google.com/ai/activity?utm_source=flow&utm_medium=web&utm_campaign=flow_ai_credits_page&dm=1&g1_landing_page=0"
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            time.sleep(5)
            
            body_text = page.evaluate("document.body.innerText")
            if use_cookie_file:
                try: cookies_file.write_text(json.dumps(context.storage_state(), ensure_ascii=False, indent=2), "utf-8")
                except: pass
            
            clean = re.sub(r'\s+', ' ', body_text)
            def _parse_num(num_str):
                return int(re.sub(r'\D', '', num_str))
                
            balance = None
            match_flow = re.search(r'(?:tín\s+dụng\s+google\s+flow|google\s+flow\s+credits|flow\s+credits)\s*([\d.,]+)', clean, re.IGNORECASE)
            if match_flow:
                balance = _parse_num(match_flow.group(1))
            else:
                for kw in ["tín dụng google flow", "google flow credits", "flow credits", "tín dụng google"]:
                    idx = clean.lower().find(kw)
                    if idx != -1:
                        window = clean[idx + len(kw):idx + len(kw) + 150]
                        num_match = re.search(r'\b\d+(?:[.,]\d+)?\b', window)
                        if num_match:
                            balance = _parse_num(num_match.group(0))
                            break
                            
            if balance is not None: return True, balance, 25000, ""
            return False, 0, 0, "No credits parsed"
        except Exception as e:
            return False, 0, 0, str(e)
        finally:
            try:
                if context: context.close()
                if browser: browser.close()
                if playwright: playwright.stop()
            except: pass


class GoogleSingleRefreshWorker(QThread):
    status_sig = pyqtSignal(str, str)
    token_updated = pyqtSignal(str, str, str, int)
    error_sig = pyqtSignal(str)
    log_sig = pyqtSignal(str)

    def __init__(self, account, profile_name: str):
        super().__init__()
        self.account = account
        self.profile_name = profile_name

    def run(self):
        self.status_sig.emit(f"🔍 Đang kiểm tra token cho {self.profile_name}...", "#2563eb")
        token = self.account.api_key
        
        is_valid = False
        info = "Token trống"
        if token and len(token) >= 20:
            try: is_valid, info = GoogleTokenRefreshWorker._check_token(token)
            except Exception as e: info = str(e)
                
        credits_val = -1
        if is_valid:
            self.status_sig.emit(f"💰 Đang đồng bộ credit cho {self.profile_name}...", "#8b5cf6")
            success_cr, balance_cr, limit_cr, err_cr = GoogleTokenRefreshWorker._try_check_credits(self.profile_name)
            if success_cr:
                credits_val = balance_cr
                self.log_sig.emit(f"💰 [Token Check] {self.profile_name}: Credit còn lại: {balance_cr}")
            self.token_updated.emit(self.account.name, token, self.account.proxy_url or DEFAULT_PROJECT_ID, credits_val)
            self.status_sig.emit(f"✅ Token còn hiệu lực: {self.profile_name} ({info})", "#16a34a")
        else:
            self.log_sig.emit(f"⚠️ [Token Check] {self.profile_name} hết hạn. Đang tự động làm mới...")
            self.status_sig.emit(f"⏳ Đang tự động làm mới token cho {self.profile_name}...", "#f59e0b")
            
            success, new_token, new_project_id, err_msg = GoogleTokenRefreshWorker._try_headless_refresh(self.profile_name)
            if success:
                is_valid_new, new_email = GoogleTokenRefreshWorker._check_token(new_token)
                if is_valid_new:
                    self.status_sig.emit(f"💰 Đang đồng bộ credit cho {self.profile_name}...", "#8b5cf6")
                    success_cr, balance_cr, limit_cr, err_cr = GoogleTokenRefreshWorker._try_check_credits(self.profile_name)
                    if success_cr:
                        credits_val = balance_cr
                        self.log_sig.emit(f"💰 [Token Check] {self.profile_name}: Credit còn lại: {balance_cr}")
                    self.token_updated.emit(self.account.name, new_token, new_project_id, credits_val)
                    self.status_sig.emit(f"✅ Đã tự động làm mới token: {self.profile_name} ({new_email})", "#16a34a")
                    return
            self.error_sig.emit(f"Không thể tự động làm mới token. Cần đăng nhập lại.")
            self.status_sig.emit(f"❌ Cần login lại: {self.profile_name}", "#ef4444")


class LoginWorker(QThread):
    status_sig = pyqtSignal(str, str)
    success_sig = pyqtSignal(str)

    def __init__(self, login_type="xAI Grok (SuperGrok)"):
        super().__init__()
        self.login_type = login_type

    def kill_port(self, port: int):
        try:
            res = subprocess.run(["netstat", "-ano"], capture_output=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
            for line in res.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        except: pass

    def find_browser_path(self) -> tuple:
        chrome_paths = [
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Google\\Chrome\\Application\\chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Google\\Chrome\\Application\\chrome.exe",
            Path(os.environ.get("LocalAppData", "C:\\Users\\ADMIN\\AppData\\Local")) / "Google\\Chrome\\Application\\chrome.exe",
        ]
        for p in chrome_paths:
            if p.exists(): return "chrome", str(p)
        edge_paths = [
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Microsoft\\Edge\\Application\\msedge.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")) / "Microsoft\\Edge\\Application\\msedge.exe",
        ]
        for p in edge_paths:
            if p.exists(): return "edge", str(p)
        return None, None

    def run(self):
        try:
            auth_dir = Path("~/.cli-proxy-api").expanduser()
            auth_dir.mkdir(parents=True, exist_ok=True)
            self.kill_port(56121)
            self.kill_port(3000)
            self.kill_port(1455)
            
            if "Codex" in self.login_type or "chatgpt" in self.login_type.lower():
                login_cmd = "--codex-login"
                pattern = "codex-*.json"
                prefix = "codex-"
            else:
                login_cmd = "-xai-login"
                pattern = "xai-*.json"
                prefix = "xai-"

            initial_files = set(auth_dir.glob(pattern))
            temp_profile = Path(os.environ.get("TEMP", "C:\\Temp")) / f"profile_{uuid.uuid4().hex[:8]}"
            temp_profile.mkdir(parents=True, exist_ok=True)

            env = os.environ.copy()
            env["BROWSER_DATA_DIR"] = str(temp_profile)
            self.status_sig.emit("🔑 Đang khởi động tiến trình đăng nhập...", "#0284c7")
            
            process = subprocess.Popen(
                [str(CLIPROXY_EXE), login_cmd],
                cwd=str(CLIPROXY_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=env
            )

            auth_url = ""
            start_extract = time.time()
            buffer = bytearray()
            while time.time() - start_extract < 15:
                b = process.stdout.read(1)
                if not b: break
                buffer.extend(b)
                if b == b'\n':
                    line_str = buffer.decode("utf-8", errors="ignore").strip()
                    buffer.clear()
                    idx = line_str.find("https://")
                    if idx == -1: idx = line_str.find("http://")
                    if idx != -1:
                        raw_url = line_str[idx:]
                        auth_url = raw_url.split()[0].strip('"\'()[]{}<>')
                        break

            if not auth_url:
                self.status_sig.emit("❌ Lỗi: Không lấy được URL đăng nhập.", "#dc2626")
                try: process.terminate()
                except: pass
                return

            b_type, b_path = self.find_browser_path()
            if b_path:
                args = [b_path]
                if b_type == "chrome": args.append("--incognito")
                else: args.append("--inprivate")
                user_agents = [
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"
                ]
                args.append(f"--user-agent={random.choice(user_agents)}")
                args.append(auth_url)
                subprocess.Popen(args, creationflags=_CNOW)

            self.status_sig.emit("👉 Đã mở trình duyệt ẩn danh. Hãy bấm Authorize.", "#d97706")

            start_time = time.time()
            success = False
            timeout = 300

            while time.time() - start_time < timeout:
                time.sleep(1)
                ret = process.poll()
                current_files = set(auth_dir.glob(pattern))
                new_files = current_files - initial_files

                if new_files:
                    new_file = list(new_files)[0]
                    email = "Unknown"
                    try:
                        with open(new_file, "r", encoding="utf-8") as f:
                            cred_data = json.load(f)
                            email = cred_data.get("email", new_file.stem.replace(prefix, ""))
                    except:
                        email = new_file.stem.replace(prefix, "")

                    self.status_sig.emit(f"✅ Đăng nhập thành công: {email}", "#16a34a")
                    self.success_sig.emit(f"{self.login_type}|{email}")
                    success = True
                    try: process.terminate()
                    except: pass
                    break

                if ret is not None:
                    self.status_sig.emit("❌ Đăng nhập bị hủy hoặc cửa sổ đã đóng.", "#dc2626")
                    break

            if not success and time.time() - start_time >= timeout:
                self.status_sig.emit("⏰ Hết thời gian chờ đăng nhập.", "#dc2626")
                try: process.terminate()
                except: pass

            try: shutil.rmtree(temp_profile, ignore_errors=True)
            except: pass
        except Exception as e:
            self.status_sig.emit(f"❌ Lỗi: {e}", "#dc2626")


class VoiceDownloadWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, video_url, output_dir, api_key=None):
        super().__init__()
        self.video_url = video_url
        self.output_dir = Path(output_dir)
        self.api_key = api_key

    def run(self):
        try:
            from TiktokShop.tiktok_shop_crawler import download_video_and_voice_tikwm
            mp3_path = download_video_and_voice_tikwm(self.video_url, self.output_dir, self.api_key)
            self.finished_signal.emit(True, mp3_path)
        except Exception as e:
            self.finished_signal.emit(False, str(e))


# ─── TIKTOKSHOP DIALOGS ───────────────────────────────────────────────
class ClickableCropperLabel(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rubber_band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.origin = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
            self.rubber_band.setGeometry(QRect(self.origin, QSize()))
            self.rubber_band.show()

    def mouseMoveEvent(self, event):
        if self.origin is not None:
            self.rubber_band.setGeometry(QRect(self.origin, event.position().toPoint()).normalized())

    def mouseReleaseEvent(self, event):
        pass


class ClickableImageLabel(QLabel):
    clicked = pyqtSignal(str)
    def __init__(self, img_path, parent=None):
        super().__init__(parent)
        self.img_path = img_path
        self.setFixedSize(110, 110)
        self.set_selected(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pix = QPixmap(img_path)
        if not pix.isNull():
            self.setPixmap(pix.scaled(104, 104, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.img_path)
            
    def set_selected(self, active):
        if active:
            self.setStyleSheet("border: 3px solid #7c3aed; border-radius: 6px; background-color: #1e293b;")
        else:
            self.setStyleSheet("border: 1px solid #475569; border-radius: 6px; background-color: #1e293b;")


class CropperDialog(QDialog):
    def __init__(self, img_path: str, output_dir=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("✂️ Cắt Ảnh Sản Phẩm")
        self.setMinimumSize(800, 600)
        self.img_path = img_path
        self.output_dir = output_dir
        self.cropped_path = None
        self.zoom_factor = 1.0
        
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; background: transparent; }
            QPushButton { background-color: #334155; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background-color: #475569; }
        """)
        lay = QVBoxLayout(self)
        inst = QLabel("👉 Nhấp giữ chuột và kéo để chọn vùng cắt. Giữ Ctrl + Cuộn chuột để Zoom In/Out.")
        inst.setStyleSheet("font-weight: bold; color: #38bdf8; margin: 5px;")
        lay.addWidget(inst)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.lbl_img = ClickableCropperLabel()
        self.pixmap = QPixmap(img_path)
        self.lbl_img.setPixmap(self.pixmap)
        self.lbl_img.setFixedSize(self.pixmap.size())
        scroll.setWidget(self.lbl_img)
        lay.addWidget(scroll, stretch=1)
        
        # Install event filter to capture Ctrl + scroll
        scroll.installEventFilter(self)
        if scroll.viewport():
            scroll.viewport().installEventFilter(self)
        self.lbl_img.installEventFilter(self)
        
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        btn_cancel = QPushButton("❌ Hủy")
        btn_cancel.setFixedSize(100, 32)
        btn_cancel.clicked.connect(self.reject)
        btn_lay.addWidget(btn_cancel)
        
        btn_crop = QPushButton("✂️ Cắt & Lưu")
        btn_crop.setFixedSize(130, 32)
        btn_crop.setStyleSheet("background-color: #16a34a; color: white; font-weight: bold;")
        btn_crop.clicked.connect(self._crop_image)
        btn_lay.addWidget(btn_crop)
        lay.addLayout(btn_lay)
        
    def eventFilter(self, obj, event):
        if event.type() == event.Type.Wheel:
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoom_factor *= 1.15
                else:
                    self.zoom_factor /= 1.15
                self.zoom_factor = max(0.1, min(10.0, self.zoom_factor))
                self._apply_zoom()
                return True
        return super().eventFilter(obj, event)
        
    def _apply_zoom(self):
        new_size = self.pixmap.size() * self.zoom_factor
        scaled_pix = self.pixmap.scaled(
            new_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        self.lbl_img.setFixedSize(scaled_pix.size())
        self.lbl_img.setPixmap(scaled_pix)
        self.lbl_img.rubber_band.hide()
        
    def _crop_image(self):
        geom = self.lbl_img.rubber_band.geometry()
        if geom.isEmpty() or geom.width() < 10 or geom.height() < 10:
            QMessageBox.warning(self, "Lỗi", "Vui lòng kéo chọn một vùng cắt hợp lệ!")
            return
            
        scale_x = self.lbl_img.width() / self.pixmap.width()
        scale_y = self.lbl_img.height() / self.pixmap.height()
        
        orig_x = int(geom.x() / scale_x)
        orig_y = int(geom.y() / scale_y)
        orig_w = int(geom.width() / scale_x)
        orig_h = int(geom.height() / scale_y)
        
        orig_x = max(0, min(self.pixmap.width() - 1, orig_x))
        orig_y = max(0, min(self.pixmap.height() - 1, orig_y))
        orig_w = max(10, min(self.pixmap.width() - orig_x, orig_w))
        orig_h = max(10, min(self.pixmap.height() - orig_y, orig_h))
        
        cropped_pix = self.pixmap.copy(orig_x, orig_y, orig_w, orig_h)
        temp_dir = self.output_dir if self.output_dir else (DOWNLOADS_DIR / "temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / f"cropped_{uuid.uuid4().hex[:8]}.jpg"
        
        if cropped_pix.save(str(out_path), "JPG", 95):
            self.cropped_path = str(out_path)
            self.accept()
        else:
            QMessageBox.critical(self, "Lỗi", "Không thể lưu tệp ảnh đã cắt!")


class ClickableCollageThumbnailLabel(QLabel):
    clicked = pyqtSignal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class CollageDialog(QDialog):
    def __init__(self, img_paths: list, output_dir=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🧩 Ghép Nhiều Ảnh Sản Phẩm")
        self.setMinimumSize(700, 500)
        self.img_paths = img_paths
        self.output_dir = output_dir
        self.collage_path = None
        
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; background: transparent; }
            QCheckBox { color: #e2e8f0; background: transparent; font-size: 11px; }
            QComboBox { background-color: #1e293b; color: #e2e8f0; border: 1px solid #475569; border-radius: 6px; padding: 4px 8px; font-size: 11px; }
            QPushButton { background-color: #334155; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background-color: #475569; }
        """)
        
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(12)
        
        header = QLabel("🧩 Ghép Ảnh Phân Cảnh / Collage")
        header.setStyleSheet("font-size: 14px; font-weight: bold; color: #38bdf8;")
        lay.addWidget(header)
        
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_w = QWidget()
        scroll_w.setStyleSheet("background: transparent;")
        grid = QGridLayout(scroll_w)
        grid.setSpacing(10)
        
        self.checkboxes = []
        for idx, path in enumerate(img_paths):
            row = idx // 4
            col = idx % 4
            item_w = QWidget()
            item_lay = QVBoxLayout(item_w)
            item_lay.setContentsMargins(4, 4, 4, 4)
            
            lbl_thumb = ClickableCollageThumbnailLabel()
            lbl_thumb.setFixedSize(120, 120)
            lbl_thumb.setStyleSheet("border: 2px solid #475569; border-radius: 6px; background: #1e293b;")
            pix = QPixmap(path)
            if not pix.isNull():
                lbl_thumb.setPixmap(pix.scaled(114, 114, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            item_lay.addWidget(lbl_thumb, alignment=Qt.AlignmentFlag.AlignCenter)
            
            cb = QCheckBox(f"Ảnh {idx + 1}")
            cb.setProperty("img_path", path)
            cb.setChecked(idx < 2)
            if idx < 2:
                lbl_thumb.setStyleSheet("border: 3px solid #7c3aed; border-radius: 6px; background: #1e293b;")
            item_lay.addWidget(cb, alignment=Qt.AlignmentFlag.AlignCenter)
            
            def toggle_cb(c=cb, t=lbl_thumb):
                c.setChecked(not c.isChecked())
                if c.isChecked(): t.setStyleSheet("border: 3px solid #7c3aed; border-radius: 6px; background: #1e293b;")
                else: t.setStyleSheet("border: 2px solid #475569; border-radius: 6px; background: #1e293b;")
            lbl_thumb.clicked.connect(toggle_cb)
            
            def on_cb_changed(state, c=cb, t=lbl_thumb):
                if c.isChecked(): t.setStyleSheet("border: 3px solid #7c3aed; border-radius: 6px; background: #1e293b;")
                else: t.setStyleSheet("border: 2px solid #475569; border-radius: 6px; background: #1e293b;")
            cb.stateChanged.connect(on_cb_changed)
            
            self.checkboxes.append(cb)
            grid.addWidget(item_w, row, col)
            
        scroll.setWidget(scroll_w)
        lay.addWidget(scroll, stretch=1)
        
        cfg_w = QWidget()
        cfg_w.setStyleSheet("background: transparent;")
        cfg_lay = QHBoxLayout(cfg_w)
        cfg_lay.setContentsMargins(0, 0, 0, 0)
        
        cfg_lay.addWidget(QLabel("📐 Kiểu bố cục (Layout):"))
        self.combo_layout = QComboBox()
        self.combo_layout.addItems([
            "Lưới 1x1 (Tối đa 1 ảnh)",
            "Lưới 2x2 (Tối đa 4 ảnh)",
            "Lưới 2x3 (Tối đa 6 ảnh)",
            "Lưới 3x3 (Tối đa 9 ảnh)",
            "Ngang (Tự động ghép ngang)",
            "Dọc (Tự động ghép dọc)"
        ])
        self.combo_layout.setCurrentIndex(1)
        cfg_lay.addWidget(self.combo_layout)
        
        cfg_lay.addSpacing(15)
        cfg_lay.addWidget(QLabel("🖼️ Tỷ lệ ảnh đầu ra:"))
        self.combo_ratio = QComboBox()
        self.combo_ratio.addItems(["Vuông (1:1)", "Ngang (16:9)", "Dọc (9:16)"])
        self.combo_ratio.setCurrentIndex(0)
        cfg_lay.addWidget(self.combo_ratio)
        cfg_lay.addStretch()
        lay.addWidget(cfg_w)
        
        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("❌ Hủy")
        btn_cancel.setFixedSize(90, 32)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        
        btn_make = QPushButton("🧩 Ghép Ảnh")
        btn_make.setFixedSize(120, 32)
        btn_make.setStyleSheet("background-color: #7c3aed; color: white; font-weight: bold;")
        btn_make.clicked.connect(self._create_collage)
        btns.addWidget(btn_make)
        lay.addLayout(btns)
        
    def _create_collage(self):
        selected_paths = [cb.property("img_path") for cb in self.checkboxes if cb.isChecked()]
        if not selected_paths:
            QMessageBox.warning(self, "Lỗi", "Vui lòng chọn ít nhất 1 ảnh để ghép!")
            return
            
        layout_type = self.combo_layout.currentText()
        selected_count = len(selected_paths)
        
        max_allowed = 99
        if "1x1" in layout_type: max_allowed = 1
        elif "2x2" in layout_type: max_allowed = 4
        elif "2x3" in layout_type: max_allowed = 6
        elif "3x3" in layout_type: max_allowed = 9
            
        if selected_count > max_allowed:
            QMessageBox.warning(self, "Lỗi bố cục", f"Bố cục đã chọn ({layout_type}) chỉ hỗ trợ tối đa {max_allowed} ảnh. Bạn đã chọn {selected_count} ảnh!")
            return
            
        seg_w, seg_h = 600, 600
        if "Ngang" in layout_type:
            canvas_w = seg_w * selected_count
            canvas_h = seg_h
        elif "Dọc" in layout_type:
            canvas_w = seg_w
            canvas_h = seg_h * selected_count
        else:
            if "1x1" in layout_type: cols, rows = 1, 1
            elif "2x2" in layout_type: cols, rows = 2, 2
            elif "2x3" in layout_type: cols, rows = 2, 3
            elif "3x3" in layout_type: cols, rows = 3, 3
            else: cols, rows = 2, 2
                
            ratio_type = self.combo_ratio.currentText()
            if "1:1" in ratio_type: canvas_w, canvas_h = 1200, 1200
            elif "16:9" in ratio_type: canvas_w, canvas_h = 1600, 900
            elif "9:16" in ratio_type: canvas_w, canvas_h = 900, 1600
            else: canvas_w, canvas_h = 1200, 1200
                
        canvas = QImage(canvas_w, canvas_h, QImage.Format.Format_ARGB32)
        canvas.fill(QColor("#0f172a"))
        
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        gap = 8
        
        if "Ngang" in layout_type or "Dọc" in layout_type:
            for idx, path in enumerate(selected_paths):
                pix = QPixmap(path)
                if pix.isNull(): continue
                if "Ngang" in layout_type:
                    x = idx * seg_w + gap
                    y = gap
                    w = seg_w - 2 * gap
                    h = seg_h - 2 * gap
                else:
                    x = gap
                    y = idx * seg_h + gap
                    w = seg_w - 2 * gap
                    h = seg_h - 2 * gap
                    
                scaled_pix = pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                dx = (w - scaled_pix.width()) // 2
                dy = (h - scaled_pix.height()) // 2
                target_rect = QRect(x + dx, y + dy, scaled_pix.width(), scaled_pix.height())
                painter.drawPixmap(target_rect, scaled_pix)
                painter.setPen(QPen(QColor("#475569"), 2))
                painter.drawRect(target_rect)
                
                badge_w, badge_h = 28, 28
                badge_rect = QRect(target_rect.x() + 6, target_rect.y() + 6, badge_w, badge_h)
                painter.setBrush(QBrush(QColor("#7c3aed")))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(badge_rect)
                painter.setPen(QPen(QColor("#ffffff")))
                painter.setFont(QFont("Arial", 12, QFont.Weight.Bold))
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(idx + 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
        else:
            cell_w = canvas_w // cols
            cell_h = canvas_h // rows
            for idx, path in enumerate(selected_paths):
                pix = QPixmap(path)
                if pix.isNull(): continue
                row = idx // cols
                col = idx % cols
                x = col * cell_w + gap
                y = row * cell_h + gap
                w = cell_w - 2 * gap
                h = cell_h - 2 * gap
                
                scaled_pix = pix.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                dx = (w - scaled_pix.width()) // 2
                dy = (h - scaled_pix.height()) // 2
                target_rect = QRect(x + dx, y + dy, scaled_pix.width(), scaled_pix.height())
                painter.drawPixmap(target_rect, scaled_pix)
                painter.setPen(QPen(QColor("#475569"), 2))
                painter.drawRect(target_rect)
                
                badge_w, badge_h = 28, 28
                badge_rect = QRect(target_rect.x() + 6, target_rect.y() + 6, badge_w, badge_h)
                painter.setBrush(QBrush(QColor("#7c3aed")))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(badge_rect)
                painter.setPen(QPen(QColor("#ffffff")))
                painter.setFont(QFont("Arial", 12, QFont.Weight.Bold))
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, str(idx + 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                
        painter.end()
        temp_dir = self.output_dir if self.output_dir else (DOWNLOADS_DIR / "temp")
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / f"collage_{uuid.uuid4().hex[:8]}.jpg"
        
        if canvas.save(str(out_path), "JPG", 95):
            self.collage_path = str(out_path)
            self.accept()
        else:
            QMessageBox.critical(self, "Lỗi", "Không thể tạo tệp ghép ảnh!")


class SceneImageViewerDialog(QDialog):
    def __init__(self, scene: ThreadsScene, product: ProductItem, parent_row: "ThreadsSceneRow", main_win: QMainWindow):
        super().__init__(main_win)
        self.scene = scene
        self.product = product
        self.parent_row = parent_row
        self.main_win = main_win
        
        self.setWindowTitle(f"🖼️ Xem & Sửa Phân Cảnh #{scene.id}")
        self.setMinimumSize(900, 750)
        self.resize(950, 800)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; background: transparent; }
            QPushButton { background-color: #334155; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background-color: #475569; }
        """)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(8)
        
        # Scene info bar
        info_lay = QHBoxLayout()
        info_lay.addWidget(QLabel(f"<b>Scene #{scene.id}</b> | Layout: <i>{scene.ui_layout}</i> | Ratio: <i>{scene.aspect_ratio}</i>" + (f" | Voice: <i>{scene.voice_role}</i>" if scene.voice_role else "")))
        info_lay.addStretch()
        self.layout.addLayout(info_lay)
        
        # Image Display Area — large
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("border: 1px solid #334155; background: #1e293b; border-radius: 8px;")
        
        self.lbl_image = QLabel()
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.lbl_image)
        self.layout.addWidget(self.scroll_area, stretch=1)
        
        # Prompt section — collapsible (hidden by default)
        self.btn_toggle_prompt = QPushButton("📝 Xem Prompt ▼")
        self.btn_toggle_prompt.setStyleSheet("QPushButton{background:#1e293b;color:#94a3b8;font-size:11px;padding:4px 10px;border:1px solid #334155;border-radius:4px;}QPushButton:hover{background:#334155;}")
        self.btn_toggle_prompt.setFixedHeight(26)
        self.btn_toggle_prompt.clicked.connect(self._toggle_prompt)
        self.layout.addWidget(self.btn_toggle_prompt)
        
        self.txt_prompt = QTextEdit()
        self.txt_prompt.setPlainText(scene.prompt)
        self.txt_prompt.setReadOnly(True)
        self.txt_prompt.setMaximumHeight(120)
        self.txt_prompt.setStyleSheet("QTextEdit{color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:4px;font-size:11px;padding:4px;}")
        self.txt_prompt.setVisible(False)
        self.layout.addWidget(self.txt_prompt)
        
        # Controls Layout
        self.btn_lay = QHBoxLayout()
        self.btn_lay.setSpacing(10)
        
        # Crop Button
        self.btn_crop = QPushButton("✂️ Crop Ảnh")
        self.btn_crop.setStyleSheet("QPushButton { background-color: #eab308; color: black; } QPushButton:hover { background-color: #ca8a04; }")
        self.btn_crop.clicked.connect(self._on_crop_clicked)
        self.btn_lay.addWidget(self.btn_crop)
        
        # Render Single Image Button
        self.btn_render_img = QPushButton("🎨 Render Lại Ảnh")
        self.btn_render_img.setStyleSheet("QPushButton { background-color: #3b82f6; color: white; } QPushButton:hover { background-color: #2563eb; }")
        self.btn_render_img.clicked.connect(self._on_render_img_clicked)
        self.btn_lay.addWidget(self.btn_render_img)
        
        self.btn_lay.addStretch()
        
        # Save / Close Button
        self.btn_save = QPushButton("💾 Lưu & Đóng")
        self.btn_save.setStyleSheet("QPushButton { background-color: #7c3aed; color: white; width: 100px; } QPushButton:hover { background-color: #6d28d9; }")
        self.btn_save.clicked.connect(self.accept)
        self.btn_lay.addWidget(self.btn_save)
        
        self.layout.addLayout(self.btn_lay)
        
        self.refresh_preview()
        
    def refresh_preview(self):
        img_path = self.scene.image_path
        if img_path and Path(img_path).exists():
            pix = QPixmap(img_path)
            if not pix.isNull():
                self.lbl_image.setPixmap(pix.scaled(850, 700, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                return
        self.lbl_image.setText("Chưa có ảnh hoặc ảnh đã bị xoá")
        self.lbl_image.setStyleSheet("color: #64748b; font-size: 14px; font-weight: bold;")

    def _toggle_prompt(self):
        vis = not self.txt_prompt.isVisible()
        self.txt_prompt.setVisible(vis)
        self.btn_toggle_prompt.setText("📝 Ẩn Prompt ▲" if vis else "📝 Xem Prompt ▼")
        
    def _on_crop_clicked(self):
        img_path = self.scene.image_path
        if not img_path or not Path(img_path).exists():
            QMessageBox.warning(self, "Cảnh báo", "Không tìm thấy tệp ảnh phân cảnh để crop!")
            return
        
        # Create temp folder inside product's directory
        temp_dir = Path(img_path).parent / "temp"
        temp_dir.mkdir(exist_ok=True)
        
        dlg = CropperDialog(img_path, temp_dir, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cropped_file = Path(dlg.cropped_path)
            if cropped_file.exists():
                try:
                    # Overwrite the original image
                    import shutil
                    shutil.copy2(cropped_file, img_path)
                    
                    # Refresh dialog and parent widget
                    self.refresh_preview()
                    if self.parent_row:
                        self.parent_row.refresh_image()
                        
                    # Save changes
                    if self.main_win and hasattr(self.main_win, "_save_projects"):
                        self.main_win._save_projects()
                        
                    QMessageBox.information(self, "Thành công", "Đã crop và ghi đè ảnh gốc thành công!")
                except Exception as e:
                    QMessageBox.critical(self, "Lỗi", f"Không thể ghi đè ảnh gốc: {e}")
                    
    def _on_render_img_clicked(self):
        img_path = self.scene.image_path
        # Delete original image if it exists to trigger re-generation
        if img_path and Path(img_path).exists():
            try: Path(img_path).unlink()
            except Exception: pass
            
        self.scene.image_path = ""
        self.scene.status = "pending"
        
        # Save project changes
        if self.main_win and hasattr(self.main_win, "_save_projects"):
            self.main_win._save_projects()
            
        # Update row UI
        if self.parent_row:
            self.parent_row.refresh_image()
            self.parent_row.update_row_data()
            
        # Close dialog and trigger rendering
        self.accept()
        
        if self.main_win and hasattr(self.main_win, "_run_single"):
            self.main_win._run_single(self.product)
            



class StyleManagerDialog(QDialog):
    def __init__(self, main_win):
        super().__init__(main_win)
        self.main_win = main_win
        self.setWindowTitle("🎭 Quản Lý Phong Cách Kịch Bản")
        self.setMinimumSize(700, 500)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; background: transparent; font-weight: bold; }
            QListWidget { background-color: #1e293b; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 6px; }
            QListWidget::item { padding: 6px; border-bottom: 1px solid #334155; }
            QListWidget::item:selected { background-color: #7c3aed; color: white; border-radius: 4px; }
            QTextEdit { background-color: #1e293b; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 6px; }
            QPushButton { background-color: #334155; color: white; border: none; border-radius: 6px; padding: 8px 12px; font-weight: bold; font-size: 11px; }
            QPushButton:hover { background-color: #475569; }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(15)
        
        # Left side: List of styles
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 0, 0)
        
        left_lay.addWidget(QLabel("Danh sách phong cách:"))
        self.list_widget = QListWidget()
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        left_lay.addWidget(self.list_widget)
        
        # Action buttons for styles
        btn_lay = QHBoxLayout()
        self.btn_add = QPushButton("➕ Thêm mới")
        self.btn_add.setStyleSheet("background-color: #10b981;")
        self.btn_add.clicked.connect(self._on_add_clicked)
        btn_lay.addWidget(self.btn_add)
        
        self.btn_edit = QPushButton("✏️ Sửa")
        self.btn_edit.setStyleSheet("background-color: #3b82f6;")
        self.btn_edit.clicked.connect(self._on_edit_clicked)
        btn_lay.addWidget(self.btn_edit)
        
        self.btn_delete = QPushButton("❌ Xóa")
        self.btn_delete.setStyleSheet("background-color: #ef4444;")
        self.btn_delete.clicked.connect(self._on_delete_clicked)
        btn_lay.addWidget(self.btn_delete)
        
        left_lay.addLayout(btn_lay)
        layout.addWidget(left_w, stretch=1)
        
        # Right side: Detail view
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)
        
        right_lay.addWidget(QLabel("Mô tả chi tiết phong cách:"))
        self.txt_desc = QTextEdit()
        self.txt_desc.setReadOnly(True)
        self.txt_desc.setStyleSheet("background-color: #0f172a; color: #cbd5e1; font-size: 12px;")
        right_lay.addWidget(self.txt_desc)
        
        # Close Button
        self.btn_close = QPushButton("Đóng")
        self.btn_close.clicked.connect(self.accept)
        right_lay.addWidget(self.btn_close, alignment=Qt.AlignmentFlag.AlignRight)
        
        layout.addWidget(right_w, stretch=1)
        
        self._load_styles()
        
    def _load_styles(self):
        self.list_widget.clear()
        self.txt_desc.clear()
        
        # Add default archetypes
        for name, desc in DEFAULT_ARCHETYPES:
            item = QListWidgetItem(f"🔒 {name}")
            item.setData(Qt.ItemDataRole.UserRole, (name, desc, True)) # True = system default
            self.list_widget.addItem(item)
            
        # Add custom archetypes
        customs = self.main_win.settings.get("custom_archetypes", [])
        for item in customs:
            if isinstance(item, list) and len(item) == 2:
                list_item = QListWidgetItem(f"👤 {item[0]}")
                list_item.setData(Qt.ItemDataRole.UserRole, (item[0], item[1], False)) # False = custom
                self.list_widget.addItem(list_item)
                
        # Select first item
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
            
    def _on_selection_changed(self):
        curr = self.list_widget.currentItem()
        if not curr:
            self.txt_desc.clear()
            self.btn_edit.setEnabled(False)
            self.btn_delete.setEnabled(False)
            return
            
        name, desc, is_default = curr.data(Qt.ItemDataRole.UserRole)
        self.txt_desc.setPlainText(desc)
        
        # Only allow edit/delete for custom archetypes
        self.btn_edit.setEnabled(not is_default)
        self.btn_delete.setEnabled(not is_default)
        
    def _on_add_clicked(self):
        name, ok1 = QInputDialog.getText(self, "Thêm Phong Cách Mới", "Tên phong cách (tiếng Việt):")
        if not ok1 or not name.strip(): return
        desc, ok2 = QInputDialog.getMultiLineText(self, "Thêm Phong Cách Mới", "Nội dung/Mô tả phong cách (tiếng Việt):")
        if not ok2 or not desc.strip(): return
        
        customs = self.main_win.settings.setdefault("custom_archetypes", [])
        customs.append([name.strip(), desc.strip()])
        self.main_win._save_settings(self.main_win.settings)
        
        self._load_styles()
        self._sync_comboboxes()
        
        # Select the newly added style
        for r in range(self.list_widget.count()):
            item = self.list_widget.item(r)
            if item.text() == f"👤 {name.strip()}":
                self.list_widget.setCurrentItem(item)
                break
                
    def _on_edit_clicked(self):
        curr = self.list_widget.currentItem()
        if not curr: return
        name, desc, is_default = curr.data(Qt.ItemDataRole.UserRole)
        if is_default: return
        
        new_name, ok1 = QInputDialog.getText(self, "Sửa Phong Cách", "Tên phong cách (tiếng Việt):", text=name)
        if not ok1 or not new_name.strip(): return
        new_desc, ok2 = QInputDialog.getMultiLineText(self, "Sửa Phong Cách", "Mô tả phong cách (tiếng Việt):", text=desc)
        if not ok2 or not new_desc.strip(): return
        
        customs = self.main_win.settings.get("custom_archetypes", [])
        for i, item in enumerate(customs):
            if isinstance(item, list) and item[0] == name:
                customs[i] = [new_name.strip(), new_desc.strip()]
                break
        self.main_win._save_settings(self.main_win.settings)
        
        self._load_styles()
        self._sync_comboboxes()
        
    def _on_delete_clicked(self):
        curr = self.list_widget.currentItem()
        if not curr: return
        name, desc, is_default = curr.data(Qt.ItemDataRole.UserRole)
        if is_default: return
        
        if QMessageBox.question(self, "Xóa Phong Cách", f"Bạn có chắc chắn muốn xóa phong cách '{name}'?") == QMessageBox.StandardButton.Yes:
            customs = self.main_win.settings.get("custom_archetypes", [])
            customs = [item for item in customs if isinstance(item, list) and item[0] != name]
            self.main_win.settings["custom_archetypes"] = customs
            self.main_win._save_settings(self.main_win.settings)
            
            self._load_styles()
            self._sync_comboboxes()
            
    def _sync_comboboxes(self):
        # Sync main combobox and all products
        if hasattr(self.main_win, "cb_ctx") and hasattr(self.main_win, "_load_archetypes_into_combo"):
            curr_text = self.main_win.cb_ctx.currentText()
            self.main_win._load_archetypes_into_combo(self.main_win.cb_ctx)
            self.main_win.cb_ctx.setCurrentText(curr_text)
            
        for card in getattr(self.main_win, "product_cards", {}).values():
            if hasattr(card, "context_combo") and hasattr(self.main_win, "_load_archetypes_into_combo"):
                card_curr_text = card.context_combo.currentText()
                self.main_win._load_archetypes_into_combo(card.context_combo)
                card.context_combo.setCurrentText(card_curr_text)


class TiktokShopSelectDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🛍️ Lựa Chọn Sản Phẩm Từ TiktokShop")
        self.setMinimumSize(950, 720)
        self.selected_image_path = None
        self.selected_title = ""
        self.selected_description = ""
        self.selected_voices = []
        self.products = []
        self.app = parent or QApplication.activeWindow()
        
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; background: transparent; }
            QTextEdit { background-color: #1e293b; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 6px; }
            QLineEdit { background-color: #1e293b; color: #f8fafc; border: 1px solid #475569; border-radius: 6px; padding: 6px; }
            QTableWidget { background-color: #1e293b; color: #e2e8f0; gridline-color: #334155; border: 1px solid #334155; border-radius: 8px; }
            QTableWidget::item:selected { background-color: #7c3aed; color: white; }
            QHeaderView::section { background-color: #0f172a; color: #38bdf8; font-weight: bold; border: 1px solid #334155; }
            QPushButton { background-color: #334155; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background-color: #475569; }
            QTabWidget::pane { border: 1px solid #334155; background: #0f172a; border-radius: 8px; }
            QTabBar::tab { background: #1e293b; color: #94a3b8; border: 1px solid #334155; padding: 8px 16px; border-top-left-radius: 6px; border-top-right-radius: 6px; }
            QTabBar::tab:selected { background: #7c3aed; color: white; font-weight: bold; }
            QGroupBox { border: 1px solid #334155; border-radius: 8px; margin-top: 10px; padding: 10px; color: #38bdf8; font-weight: bold; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; }
        """)
        
        dialog_lay = QVBoxLayout(self)
        dialog_lay.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        
        self.tab_select = QWidget()
        select_lay = QHBoxLayout(self.tab_select)
        select_lay.setContentsMargins(12, 12, 12, 12)
        select_lay.setSpacing(12)
        
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 0, 0)
        
        search_lay = QHBoxLayout()
        search_lay.addWidget(QLabel("🔍 Tìm kiếm:"))
        self.edit_search = QLineEdit()
        self.edit_search.setPlaceholderText("Nhập tên hoặc ID sản phẩm...")
        self.edit_search.textChanged.connect(self._filter_products)
        search_lay.addWidget(self.edit_search)
        left_lay.addLayout(search_lay)
        
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["ID", "Tên sản phẩm", "Giá", "Voice MP3"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self._on_product_selected)
        left_lay.addWidget(self.table)
        select_lay.addWidget(left_w, stretch=4)
        
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w)
        right_lay.setContentsMargins(0, 0, 0, 0)
        
        right_lay.addWidget(QLabel("📋 Thông tin chi tiết:"))
        self.lbl_title = QLabel("Chưa chọn sản phẩm")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setStyleSheet("font-size: 13px; font-weight: bold; color: #a78bfa;")
        right_lay.addWidget(self.lbl_title)
        
        self.txt_desc = QTextEdit()
        self.txt_desc.setReadOnly(True)
        right_lay.addWidget(self.txt_desc)
        
        right_lay.addWidget(QLabel("🖼️ Danh sách ảnh sản phẩm (Click chọn ảnh):"))
        self.img_scroll = QScrollArea()
        self.img_scroll.setWidgetResizable(True)
        self.img_scroll.setFixedHeight(160)
        self.img_container = QWidget()
        self.img_container.setStyleSheet("background: transparent;")
        self.img_lay = QHBoxLayout(self.img_container)
        self.img_lay.setContentsMargins(4, 4, 4, 4)
        self.img_lay.setSpacing(8)
        self.img_scroll.setWidget(self.img_container)
        right_lay.addWidget(self.img_scroll)
        
        self.lbl_selected_img = QLabel("Chưa chọn ảnh sản phẩm")
        self.lbl_selected_img.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 10px;")
        right_lay.addWidget(self.lbl_selected_img)
        
        self.lbl_voice_info = QLabel("🗣️ Lồng tiếng: 0 file voice")
        self.lbl_voice_info.setStyleSheet("color: #38bdf8; font-size: 11px; font-weight: bold; background: transparent;")
        right_lay.addWidget(self.lbl_voice_info)
        
        self.manual_download_w = QWidget()
        self.manual_download_w.setStyleSheet("background: transparent;")
        manual_down_lay = QHBoxLayout(self.manual_download_w)
        manual_down_lay.setContentsMargins(0, 0, 0, 0)
        manual_down_lay.setSpacing(6)
        
        self.edit_manual_video_url = QLineEdit()
        self.edit_manual_video_url.setPlaceholderText("Dán link video TikTok để tải voice...")
        self.edit_manual_video_url.setEnabled(False)
        manual_down_lay.addWidget(self.edit_manual_video_url, stretch=3)
        
        self.btn_download_manual_video = QPushButton("📥 Tải Voice")
        self.btn_download_manual_video.setStyleSheet(
            "QPushButton { background-color: #10b981; color: white; padding: 6px 12px; font-size: 10px; font-weight: bold; border-radius: 6px; }"
            "QPushButton:hover { background-color: #059669; }"
            "QPushButton:disabled { background-color: #334155; color: #64748b; }"
        )
        self.btn_download_manual_video.setEnabled(False)
        self.btn_download_manual_video.clicked.connect(self._download_manual_video_voice)
        manual_down_lay.addWidget(self.btn_download_manual_video, stretch=1)
        right_lay.addWidget(self.manual_download_w)
        
        act_lay = QHBoxLayout()
        self.btn_crop = QPushButton("✂️ Cắt ảnh")
        self.btn_crop.setEnabled(False)
        self.btn_crop.clicked.connect(self._crop_selected)
        act_lay.addWidget(self.btn_crop)
        
        self.btn_collage = QPushButton("🧩 Ghép nhiều ảnh")
        self.btn_collage.setEnabled(False)
        self.btn_collage.clicked.connect(self._collage_selected)
        act_lay.addWidget(self.btn_collage)
        right_lay.addLayout(act_lay)
        
        btns = QHBoxLayout()
        btns.addStretch()
        btn_cancel = QPushButton("❌ Hủy")
        btn_cancel.setFixedSize(100, 36)
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)
        
        self.btn_confirm = QPushButton("✅ Chọn Sản Phẩm")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.setFixedSize(160, 36)
        self.btn_confirm.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7c3aed, stop:1 #06b6d4); color: white; border: none; border-radius: 8px; font-weight: bold; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6d28d9, stop:1 #0891b2); }"
            "QPushButton:disabled { background: #1e293b; color: #64748b; }"
        )
        self.btn_confirm.clicked.connect(self._confirm_selection)
        btns.addWidget(self.btn_confirm)
        right_lay.addLayout(btns)
        select_lay.addWidget(right_w, stretch=3)
        
        self.tabs.addTab(self.tab_select, "Danh sách Sản phẩm")
        dialog_lay.addWidget(self.tabs)
        self.load_crawled_products()

    def load_crawled_products(self):
        self.products = []
        prod_json = DOWNLOADS_DIR / "products.json"
        if prod_json.exists():
            try:
                self.products = json.loads(prod_json.read_text(encoding="utf-8"))
            except: pass
        self._filter_products()

    def _filter_products(self):
        txt = self.edit_search.text().strip().lower()
        self.table.setRowCount(0)
        filtered = []
        for p in self.products:
            pid = p.get("product_id", "")
            title = p.get("title", "")
            if not txt or txt in pid.lower() or txt in title.lower():
                filtered.append(p)
                
        self.table.setRowCount(len(filtered))
        for r, p in enumerate(filtered):
            self.table.setItem(r, 0, QTableWidgetItem(p.get("product_id", "")))
            self.table.setItem(r, 1, QTableWidgetItem(p.get("title", "")))
            self.table.setItem(r, 2, QTableWidgetItem(p.get("price", "")))
            voices = p.get("voices", [])
            voice_item = QTableWidgetItem(f"✅ {len(voices)} files" if voices else "—")
            voice_item.setForeground(QColor("#06b6d4") if voices else QColor("#64748b"))
            self.table.setItem(r, 3, voice_item)
            
    def _on_product_selected(self):
        row = self.table.currentRow()
        if row < 0:
            self.btn_confirm.setEnabled(False)
            self.edit_manual_video_url.setEnabled(False)
            self.btn_download_manual_video.setEnabled(False)
            return
            
        pid = self.table.item(row, 0).text()
        prod = next((p for p in self.products if p.get("product_id") == pid), None)
        if not prod: return
        
        self.lbl_title.setText(prod.get("title", "Sản phẩm"))
        self.txt_desc.setPlainText(prod.get("description", ""))
        self.selected_title = prod.get("title", "")
        self.selected_description = prod.get("description", "")
        
        # Resolve voices
        self.selected_voices = []
        for v in prod.get("voices", []):
            p = Path(v)
            if p.is_absolute():
                if p.exists():
                    self.selected_voices.append(str(p))
                else:
                    # Try current and parent crawled_products
                    parts = p.parts
                    if "crawled_products" in parts:
                        idx = parts.index("crawled_products")
                        sub_v = Path(*parts[idx+1:])
                        p_curr = DOWNLOADS_DIR / sub_v
                        if p_curr.exists():
                            self.selected_voices.append(str(p_curr))
                        else:
                            parent_downloads = DOWNLOADS_DIR.parent.parent.parent / "TiktokShop" / "crawled_products"
                            p_parent = parent_downloads / sub_v
                            if p_parent.exists():
                                self.selected_voices.append(str(p_parent))
            else:
                p_curr = DOWNLOADS_DIR / v
                if p_curr.exists():
                    self.selected_voices.append(str(p_curr))
                else:
                    parent_downloads = DOWNLOADS_DIR.parent.parent.parent / "TiktokShop" / "crawled_products"
                    p_parent = parent_downloads / v
                    if p_parent.exists():
                        self.selected_voices.append(str(p_parent))

        self.edit_manual_video_url.setEnabled(True)
        self.btn_download_manual_video.setEnabled(True)
        
        # Load images
        while self.img_lay.count():
            item = self.img_lay.takeAt(0)
            if item.widget(): item.widget().deleteLater()
            
        self._loaded_images = []
        
        # 1. Resolve local_images from JSON
        if prod.get("local_images"):
            for img_path_str in prod.get("local_images", []):
                p = Path(img_path_str)
                if p.exists() and p.is_file():
                    if p not in self._loaded_images:
                        self._loaded_images.append(p)
                else:
                    parts = p.parts
                    sub_p = None
                    if "crawled_products" in parts:
                        idx = parts.index("crawled_products")
                        sub_p = Path(*parts[idx+1:])
                    else:
                        sub_p = Path(p.name)
                    
                    p_downloads = DOWNLOADS_DIR / sub_p
                    if p_downloads.exists() and p_downloads.is_file():
                        if p_downloads not in self._loaded_images:
                            self._loaded_images.append(p_downloads)
                    else:
                        parent_downloads = DOWNLOADS_DIR.parent.parent.parent / "TiktokShop" / "crawled_products"
                        p_parent = parent_downloads / sub_p
                        if p_parent.exists() and p_parent.is_file():
                            if p_parent not in self._loaded_images:
                                self._loaded_images.append(p_parent)

        # 2. Fallback scan on directories
        if not self._loaded_images:
            folder_name = prod.get("folder", "Chưa phân loại")
            safe_title = clean_filename(prod.get("title", "product"))
            parent_downloads = DOWNLOADS_DIR.parent.parent.parent / "TiktokShop" / "crawled_products"
            
            for downloads_base in [DOWNLOADS_DIR, parent_downloads]:
                if not downloads_base.exists():
                    continue
                if folder_name and folder_name != "Chưa phân loại":
                    prod_dir = downloads_base / folder_name / f"{pid}_{safe_title}"
                else:
                    prod_dir = downloads_base / f"{pid}_{safe_title}"
                
                if not prod_dir.exists():
                    for entry in downloads_base.iterdir():
                        if entry.is_dir():
                            if entry.name.startswith(pid) or pid in entry.name:
                                prod_dir = entry
                                break
                            if not entry.name[0].isdigit():
                                try:
                                    for sub_entry in entry.iterdir():
                                        if sub_entry.is_dir() and (sub_entry.name.startswith(pid) or pid in sub_entry.name):
                                            prod_dir = sub_entry
                                            break
                                except Exception: pass
                        if prod_dir and prod_dir.exists():
                            break
                
                if prod_dir and prod_dir.exists():
                    for d in [prod_dir, prod_dir / "images"]:
                        if d.exists() and d.is_dir():
                            for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
                                for img_f in d.glob(ext):
                                    if img_f not in self._loaded_images:
                                        self._loaded_images.append(img_f)
                                        
        self.selected_image_path = None
        self.btn_confirm.setEnabled(True)
        self.btn_crop.setEnabled(False)
        self.btn_collage.setEnabled(False)
        
        if self._loaded_images:
            self.btn_collage.setEnabled(True)
            for idx_img, img in enumerate(self._loaded_images):
                lbl = ClickableImageLabel(str(img))
                def on_click(p, l=lbl):
                    for idx in range(self.img_lay.count()):
                        w = self.img_lay.itemAt(idx).widget()
                        if isinstance(w, ClickableImageLabel): w.set_selected(False)
                    l.set_selected(True)
                    self.selected_image_path = p
                    self.lbl_selected_img.setText(f"Đã chọn: {os.path.basename(p)}")
                    self.btn_confirm.setEnabled(True)
                    self.btn_crop.setEnabled(True)
                lbl.clicked.connect(on_click)
                self.img_lay.addWidget(lbl)
                if idx_img == 0:
                    lbl.set_selected(True)
                    self.selected_image_path = str(img)
                    self.lbl_selected_img.setText(f"Đã chọn: {os.path.basename(str(img))}")
                    self.btn_crop.setEnabled(True)
                    
        self.lbl_voice_info.setText(f"🗣️ Lồng tiếng: {len(self.selected_voices)} file voice")

    def _download_manual_video_voice(self):
        url = self.edit_manual_video_url.text().strip()
        if not url.startswith("http"):
            QMessageBox.warning(self, "Lỗi", "Vui lòng nhập link video TikTok hợp lệ!")
            return
        row = self.table.currentRow()
        if row < 0: return
        pid = self.table.item(row, 0).text()
        
        output_dir = DOWNLOADS_DIR / "voices" / pid
        output_dir.mkdir(parents=True, exist_ok=True)
        
        self.btn_download_manual_video.setEnabled(False)
        self.btn_download_manual_video.setText("⏳ Đang tải...")
        
        worker = VoiceDownloadWorker(url, str(output_dir))
        def on_done(success, path):
            self.btn_download_manual_video.setEnabled(True)
            self.btn_download_manual_video.setText("📥 Tải Voice")
            if success:
                QMessageBox.information(self, "Thành công", f"Đã tải xong voice MP3:\n{path}")
                # Save to products.json
                prod_json = DOWNLOADS_DIR / "products.json"
                if prod_json.exists():
                    try:
                        products = json.loads(prod_json.read_text(encoding="utf-8"))
                        for p in products:
                            if p.get("product_id") == pid:
                                if "voices" not in p: p["voices"] = []
                                if path not in p["voices"]: p["voices"].append(path)
                                break
                        prod_json.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
                    except: pass
                self.load_crawled_products()
            else:
                QMessageBox.critical(self, "Lỗi", f"Tải voice thất bại:\n{path}")
        worker.finished_signal.connect(on_done)
        worker.start()

    def _crop_selected(self):
        if not self.selected_image_path: return
        dlg = CropperDialog(self.selected_image_path, DOWNLOADS_DIR / "temp", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.selected_image_path = dlg.cropped_path
            self.lbl_selected_img.setText(f"Đã chọn (Đã cắt): {os.path.basename(dlg.cropped_path)}")
            
    def _collage_selected(self):
        if not self._loaded_images: return
        paths = [str(x) for x in self._loaded_images]
        dlg = CollageDialog(paths, DOWNLOADS_DIR / "temp", self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.selected_image_path = dlg.collage_path
            self.lbl_selected_img.setText(f"Đã chọn (Ghép ảnh): {os.path.basename(dlg.collage_path)}")
            self.btn_confirm.setEnabled(True)
            
    def _confirm_selection(self):
        if self.selected_image_path:
            self.accept()



class ThreadsSceneRow(QWidget):
    """Một dòng hiển thị kết quả phân cảnh bên trong ThreadsProductCard."""
    def __init__(self, scene_idx: int, scene: ThreadsScene, parent=None):
        super().__init__(parent)
        self.scene_idx = scene_idx
        self.scene = scene
        self.card = parent
        self.setObjectName(f"scene_row_{scene_idx}")
        
        self.setStyleSheet("QWidget { background: transparent; }")
        
        # Cảnh # label + title
        self.lbl_title = QLabel(f"🎬 #{scene_idx + 1}")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 10px; color: #475569;")
        
        # Status text
        self.lbl_status = QLabel("Chờ" if scene.status == "pending" else (
            "✅ Xong" if scene.status == "done" else ("❌ Lỗi" if scene.status == "error" else scene.status)
        ))
        st_color = "#16a34a" if scene.status == "done" else ("#dc2626" if scene.status == "error" else "#64748b")
        self.lbl_status.setStyleSheet(f"color: {st_color}; font-size: 8px; font-weight: bold;")
        
        # Storyboard Thumbnail (Click to zoom/view)
        self.lbl_thumb = QLabel()
        self.lbl_thumb.setStyleSheet("background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 4px; color: #94a3b8; font-size: 8px;")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb.setText("⏳")
        self.lbl_thumb.mouseDoubleClickEvent = self._on_double_click_storyboard
        
        # Text edits
        self.edit_prompt = QTextEdit(self)
        self.edit_prompt.setPlaceholderText("Prompt tạo ảnh storyboard...")
        self.edit_prompt.setStyleSheet("QTextEdit { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 9px; color: #1e293b; }")
        self.edit_prompt.blockSignals(True)
        self.edit_prompt.setText(scene.prompt)
        self.edit_prompt.blockSignals(False)
        self.edit_prompt.textChanged.connect(self._on_prompt_changed)
        
        self.edit_voiceover = QTextEdit(self)
        self.edit_voiceover.setPlaceholderText("Lời đọc voiceover phân cảnh này...")
        self.edit_voiceover.setStyleSheet("QTextEdit { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 9px; color: #1e293b; }")
        self.edit_voiceover.blockSignals(True)
        self.edit_voiceover.setText(scene.text_segment)
        self.edit_voiceover.blockSignals(False)
        self.edit_voiceover.textChanged.connect(self._on_voiceover_changed)
        
        # Action Buttons
        self.btn_play = QPushButton("▶")
        self.btn_play.setToolTip("Nghe thử giọng nói")
        self.btn_play.setFixedSize(26, 26)
        self.btn_play.setStyleSheet(self._btn_style("#0284c7", "#0369a1"))
        self.btn_play.clicked.connect(self._play_voice)
        
        self.btn_dl = QPushButton("⬇")
        self.btn_dl.setToolTip("Lưu âm thanh phân cảnh")
        self.btn_dl.setFixedSize(26, 26)
        self.btn_dl.setStyleSheet(self._btn_style("#475569", "#334155"))
        self.btn_dl.clicked.connect(self._save_voice)
        
        self.btn_option = QPushButton("🎞")
        self.btn_option.setToolTip("Lựa chọn")
        self.btn_option.setFixedSize(26, 26)
        self.btn_option.setStyleSheet(self._btn_style("#7c3aed", "#6d28d9"))
        
        # Main Layout
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(6)
        
        self.setFixedHeight(100)
        self.lbl_thumb.setFixedSize(90, 56)
        
        # Column 1: Info (Title & Status)
        c1 = QWidget()
        c1.setFixedWidth(90)
        c1_lay = QVBoxLayout(c1)
        c1_lay.setContentsMargins(0, 2, 0, 2)
        c1_lay.setSpacing(4)
        c1_lay.addWidget(self.lbl_title)
        c1_lay.addWidget(self.lbl_status)
        c1_lay.addStretch()
        lay.addWidget(c1)
        
        # Column 2: Storyboard Image Prompt
        c2 = QWidget()
        c2_lay = QVBoxLayout(c2)
        c2_lay.setContentsMargins(0, 0, 0, 0)
        c2_lay.setSpacing(2)
        
        c2_hdr = QWidget()
        c2_hdr_lay = QHBoxLayout(c2_hdr)
        c2_hdr_lay.setContentsMargins(0, 0, 0, 0)
        c2_hdr_lay.setSpacing(4)
        lbl_img_title = QLabel("🖼️ Prompt Ảnh:")
        lbl_img_title.setStyleSheet("font-size: 9px; font-weight: bold; color: #7c3aed;")
        c2_hdr_lay.addWidget(lbl_img_title)
        c2_hdr_lay.addStretch()
        
        btn_zoom_img = QPushButton("✏️")
        btn_zoom_img.setFixedSize(18, 18)
        btn_zoom_img.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 10px; } QPushButton:hover { background: #e2e8f0; border-radius: 3px; }")
        btn_zoom_img.clicked.connect(self._zoom_img_prompt)
        c2_hdr_lay.addWidget(btn_zoom_img)
        c2_lay.addWidget(c2_hdr)
        
        self.edit_prompt.setFixedHeight(70)
        c2_lay.addWidget(self.edit_prompt)
        lay.addWidget(c2, stretch=3)
        
        # Column 3: Storyboard Image Thumbnail
        c3 = QWidget()
        c3.setFixedWidth(100)
        c3_lay = QVBoxLayout(c3)
        c3_lay.setContentsMargins(0, 0, 0, 0)
        c3_lay.setSpacing(2)
        lbl_thumb_title = QLabel("🔍 Ảnh gốc:")
        lbl_thumb_title.setStyleSheet("font-size: 9px; font-weight: bold; color: #64748b;")
        c3_lay.addWidget(lbl_thumb_title)
        c3_lay.addWidget(self.lbl_thumb)
        c3_lay.addStretch()
        lay.addWidget(c3)
        
        # Column 4: Voiceover (instead of video motion prompt)
        c4 = QWidget()
        c4_lay = QVBoxLayout(c4)
        c4_lay.setContentsMargins(0, 0, 0, 0)
        c4_lay.setSpacing(2)
        
        c4_hdr = QWidget()
        c4_hdr_lay = QHBoxLayout(c4_hdr)
        c4_hdr_lay.setContentsMargins(0, 0, 0, 0)
        c4_hdr_lay.setSpacing(4)
        lbl_vid_title = QLabel("🎙️ Text VoiceOver:")
        lbl_vid_title.setStyleSheet("font-size: 9px; font-weight: bold; color: #06b6d4;")
        c4_hdr_lay.addWidget(lbl_vid_title)
        c4_hdr_lay.addStretch()
        
        btn_zoom_vid = QPushButton("✏️")
        btn_zoom_vid.setFixedSize(18, 18)
        btn_zoom_vid.setStyleSheet("QPushButton { background: transparent; border: none; font-size: 10px; } QPushButton:hover { background: #e2e8f0; border-radius: 3px; }")
        btn_zoom_vid.clicked.connect(self._zoom_voiceover)
        c4_hdr_lay.addWidget(btn_zoom_vid)
        c4_lay.addWidget(c4_hdr)
        
        self.edit_voiceover.setFixedHeight(70)
        c4_lay.addWidget(self.edit_voiceover)
        lay.addWidget(c4, stretch=3)
        
        # Column 5: Action Buttons
        c6 = QWidget()
        c6.setFixedWidth(65)
        c6_lay = QVBoxLayout(c6)
        c6_lay.setContentsMargins(0, 0, 0, 0)
        c6_lay.setSpacing(4)
        
        h_btns = QWidget()
        h_btns_lay = QHBoxLayout(h_btns)
        h_btns_lay.setContentsMargins(0, 0, 0, 0)
        h_btns_lay.setSpacing(4)
        h_btns_lay.addWidget(self.btn_play)
        h_btns_lay.addWidget(self.btn_dl)
        h_btns_lay.addWidget(self.btn_option)
        
        c6_lay.addStretch()
        c6_lay.addWidget(h_btns)
        c6_lay.addStretch()
        lay.addWidget(c6)
        
        self.refresh_image()

    def _btn_style(self, bg: str, hover_bg: str) -> str:
        return (
            f"QPushButton {{ background: {bg}; color: white; border: none; border-radius: 5px; "
            f"font-size: 10px; font-weight: bold; }}"
            f"QPushButton:hover {{ background: {hover_bg}; }}"
            f"QPushButton:disabled {{ background: #e2e8f0; color: #cbd5e1; }}"
        )

    def _on_prompt_changed(self):
        self.scene.prompt = self.edit_prompt.toPlainText()
        win = self.window()
        if hasattr(win, "_save_projects"):
            win._save_projects()

    def _on_voiceover_changed(self):
        self.scene.text_segment = self.edit_voiceover.toPlainText()
        win = self.window()
        if hasattr(win, "_save_projects"):
            win._save_projects()

    def refresh_image(self):
        img_path = self.scene.image_path
        if not img_path or not Path(img_path).exists():
            self.lbl_thumb.setPixmap(QPixmap())
            self.lbl_thumb.setText("⏳" if self.scene.status == "pending" else "Chưa tạo")
            self.lbl_thumb.setStyleSheet("background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 4px; color: #94a3b8; font-size: 8px;")
            return
        pix = QPixmap(img_path)
        if not pix.isNull():
            self.lbl_thumb.setPixmap(pix.scaled(self.lbl_thumb.width() - 2, self.lbl_thumb.height() - 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
            self.lbl_thumb.setStyleSheet("border: 1px solid #7c3aed; border-radius: 4px;")

    def _on_double_click_storyboard(self, event):
        win = self.window()
        product = getattr(self.card, "product", None)
        if product:
            dlg = SceneImageViewerDialog(self.scene, product, self, win)
            dlg.exec()

    def _zoom_img_prompt(self):
        txt, ok = QInputDialog.getMultiLineText(self, "Sửa Prompt Ảnh", "Nội dung:", self.edit_prompt.toPlainText())
        if ok:
            self.edit_prompt.setPlainText(txt)

    def _zoom_voiceover(self):
        txt, ok = QInputDialog.getMultiLineText(self, "Sửa Text VoiceOver", "Nội dung:", self.edit_voiceover.toPlainText())
        if ok:
            self.edit_voiceover.setPlainText(txt)

    def _play_voice(self):
        if self.scene.image_path:
            p = Path(self.scene.image_path).parent / f"voice_{self.scene.id}.mp3"
            if p.exists():
                try: os.startfile(p)
                except Exception: pass

    def _save_voice(self):
        pass

    def update_row_data(self):
        st_color = "#16a34a" if self.scene.status == "done" else ("#dc2626" if self.scene.status == "error" else "#64748b")
        self.lbl_status.setText("Chờ" if self.scene.status == "pending" else (
            "✅ Xong" if self.scene.status == "done" else ("❌ Lỗi" if self.scene.status == "error" else self.scene.status)
        ))
        self.lbl_status.setStyleSheet(f"color: {st_color}; font-size: 8px; font-weight: bold;")
        
        if self.edit_prompt.toPlainText() != (self.scene.prompt or ""):
            self.edit_prompt.blockSignals(True)
            self.edit_prompt.setPlainText(self.scene.prompt or "")
            self.edit_prompt.blockSignals(False)
            
        if self.edit_voiceover.toPlainText() != (self.scene.text_segment or ""):
            self.edit_voiceover.blockSignals(True)
            self.edit_voiceover.setPlainText(self.scene.text_segment or "")
            self.edit_voiceover.blockSignals(False)
            
        self.refresh_image()


# ─── VIDEO REVIEW SCENE ROW ──────────────────────────────────────────────
class VRSceneRow(QWidget):
    """Một dòng hiển thị phân cảnh Video Review (AI-generated) bên trong ThreadsProductCard.
    Hiển thị rõ ràng: Prompt tạo ảnh + Ảnh gốc, Prompt tạo video + Video clip, và Lời dẫn."""
    def __init__(self, scene_idx: int, vr_scene: 'VideoReviewScene', parent_card=None):
        super().__init__(parent_card)
        self.scene_idx = scene_idx
        self.vr_scene = vr_scene
        self.card = parent_card
        self.setObjectName(f"vr_scene_row_{scene_idx}")
        self.setStyleSheet("QWidget { background: transparent; }")
        self.setFixedHeight(120)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(8)

        # ── Col 1: Index & Status (50px) ──
        c1 = QWidget(); c1.setFixedWidth(50)
        c1l = QVBoxLayout(c1); c1l.setContentsMargins(0, 2, 0, 2); c1l.setSpacing(4)
        self.lbl_idx = QLabel(f"🎞 #{scene_idx + 1}")
        self.lbl_idx.setStyleSheet("font-weight: bold; font-size: 11px; color: #7c3aed;")
        c1l.addWidget(self.lbl_idx)
        self.lbl_st = QLabel()
        c1l.addWidget(self.lbl_st)
        c1l.addStretch()
        lay.addWidget(c1)

        # ── Col 2: Image Section (Prompt + Preview) ──
        c2 = QWidget()
        c2l = QVBoxLayout(c2); c2l.setContentsMargins(0, 0, 0, 0); c2l.setSpacing(2)
        lbl_img_title = QLabel("🖼️ Prompt Tạo Ảnh:")
        lbl_img_title.setStyleSheet("font-size: 9px; font-weight: bold; color: #7c3aed;")
        c2l.addWidget(lbl_img_title)
        
        row_img = QHBoxLayout()
        row_img.setSpacing(4)
        self.edit_img_prompt = QTextEdit()
        self.edit_img_prompt.setFixedHeight(65)
        self.edit_img_prompt.setPlaceholderText("Prompt tạo ảnh...")
        self.edit_img_prompt.setStyleSheet(
            "QTextEdit { background:#fff; border:1px solid #cbd5e1; border-radius:4px; padding:2px; font-size:8px; color:#1e293b; }"
        )
        self.edit_img_prompt.blockSignals(True)
        self.edit_img_prompt.setPlainText(vr_scene.image_prompt or "")
        self.edit_img_prompt.blockSignals(False)
        self.edit_img_prompt.textChanged.connect(self._on_img_prompt_changed)
        row_img.addWidget(self.edit_img_prompt, stretch=1)
        
        self.lbl_img = QLabel()
        self.lbl_img.setFixedSize(96, 60)
        self.lbl_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_img.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8; font-size:16px;")
        self.lbl_img.setText("🖼")
        self.lbl_img.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_img.mouseDoubleClickEvent = self._on_dbl_click_image
        row_img.addWidget(self.lbl_img)
        c2l.addLayout(row_img)
        lay.addWidget(c2, stretch=3)

        # ── Col 3: Video Section (Prompt + Preview) ──
        c3 = QWidget()
        c3l = QVBoxLayout(c3); c3l.setContentsMargins(0, 0, 0, 0); c3l.setSpacing(2)
        lbl_vid_title = QLabel("🎬 Prompt Motion Video:")
        lbl_vid_title.setStyleSheet("font-size: 9px; font-weight: bold; color: #2563eb;")
        c3l.addWidget(lbl_vid_title)
        
        row_vid = QHBoxLayout()
        row_vid.setSpacing(4)
        self.edit_vid_prompt = QTextEdit()
        self.edit_vid_prompt.setFixedHeight(65)
        self.edit_vid_prompt.setPlaceholderText("Prompt tạo chuyển động video...")
        self.edit_vid_prompt.setStyleSheet(
            "QTextEdit { background:#fff; border:1px solid #cbd5e1; border-radius:4px; padding:2px; font-size:8px; color:#1e293b; }"
        )
        self.edit_vid_prompt.blockSignals(True)
        self.edit_vid_prompt.setPlainText(vr_scene.video_prompt or "")
        self.edit_vid_prompt.blockSignals(False)
        self.edit_vid_prompt.textChanged.connect(self._on_vid_prompt_changed)
        row_vid.addWidget(self.edit_vid_prompt, stretch=1)
        
        self.lbl_vid = QLabel()
        self.lbl_vid.setFixedSize(96, 60)
        self.lbl_vid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_vid.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8; font-size:16px;")
        self.lbl_vid.setText("🎬")
        self.lbl_vid.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_vid.mouseDoubleClickEvent = self._on_dbl_click_video
        row_vid.addWidget(self.lbl_vid)
        c3l.addLayout(row_vid)
        lay.addWidget(c3, stretch=3)

        # ── Col 4: Voiceover Section (Lời Dẫn) ──
        c4 = QWidget()
        c4l = QVBoxLayout(c4); c4l.setContentsMargins(0, 0, 0, 0); c4l.setSpacing(2)
        lbl_voice_title = QLabel("🎙️ Lời Dẫn Review:")
        lbl_voice_title.setStyleSheet("font-size: 9px; font-weight: bold; color: #06b6d4;")
        c4l.addWidget(lbl_voice_title)
        
        self.edit_voice = QTextEdit()
        self.edit_voice.setFixedHeight(65)
        self.edit_voice.setPlaceholderText("Lời thoại dẫn dắt review sản phẩm...")
        self.edit_voice.setStyleSheet(
            "QTextEdit { background:#fff; border:1px solid #cbd5e1; border-radius:4px; padding:2px; font-size:8px; color:#1e293b; }"
        )
        self.edit_voice.blockSignals(True)
        self.edit_voice.setPlainText(vr_scene.voiceover_text or "")
        self.edit_voice.blockSignals(False)
        self.edit_voice.textChanged.connect(self._on_voice_changed)
        c4l.addWidget(self.edit_voice)
        lay.addWidget(c4, stretch=2)

        # ── Col 5: Actions (45px) ──
        c5 = QWidget(); c5.setFixedWidth(45)
        c5l = QVBoxLayout(c5); c5l.setContentsMargins(0, 0, 0, 0); c5l.setSpacing(4)
        c5l.addStretch()
        
        self.btn_play = QPushButton("▶")
        self.btn_play.setToolTip("Xem video clip")
        self.btn_play.setFixedSize(26, 26)
        self.btn_play.setStyleSheet(
            "QPushButton { background:#7c3aed; color:white; border:none; border-radius:5px; font-size:10px; font-weight:bold; }"
            "QPushButton:hover { background:#6d28d9; }"
        )
        self.btn_play.clicked.connect(self._play_video)
        c5l.addWidget(self.btn_play)
        
        self.btn_dir = QPushButton("📂")
        self.btn_dir.setToolTip("Mở thư mục chứa")
        self.btn_dir.setFixedSize(26, 26)
        self.btn_dir.setStyleSheet(
            "QPushButton { background:#475569; color:white; border:none; border-radius:5px; font-size:10px; font-weight:bold; }"
            "QPushButton:hover { background:#334155; }"
        )
        self.btn_dir.clicked.connect(self._open_folder)
        c5l.addWidget(self.btn_dir)
        c5l.addStretch()
        lay.addWidget(c5)

        self._refresh_thumbnails()
        self._refresh_status()

    def _refresh_status(self):
        s = self.vr_scene.status
        if s == "done":
            self.lbl_st.setText("✅ Xong")
            self.lbl_st.setStyleSheet("color:#16a34a; font-size:9px; font-weight:bold;")
        elif s == "error":
            self.lbl_st.setText("❌ Lỗi")
            self.lbl_st.setStyleSheet("color:#dc2626; font-size:9px; font-weight:bold;")
        elif s == "processing":
            self.lbl_st.setText("⏳ Chạy...")
            self.lbl_st.setStyleSheet("color:#2563eb; font-size:9px; font-weight:bold;")
        else:
            self.lbl_st.setText("⏳ Chờ")
            self.lbl_st.setStyleSheet("color:#64748b; font-size:9px; font-weight:bold;")

    def _refresh_thumbnails(self):
        # Image preview
        ip = self.vr_scene.image_path
        if ip and Path(ip).exists():
            pix = QPixmap(ip)
            if not pix.isNull():
                self.lbl_img.setPixmap(pix.scaled(self.lbl_img.width() - 2, self.lbl_img.height() - 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                self.lbl_img.setStyleSheet("border:1.5px solid #7c3aed; border-radius:4px;")
            else:
                self.lbl_img.setText("🖼")
                self.lbl_img.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8;")
        else:
            self.lbl_img.setText("🖼")
            self.lbl_img.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8;")

        # Video clip preview
        vp = self.vr_scene.video_clip_path
        if vp and Path(vp).exists():
            thumb_path = Path(vp).parent / f".thumb_{Path(vp).stem}.jpg"
            if not thumb_path.exists():
                try:
                    cmd = [FFMPEG_PATH, "-y", "-i", str(vp), "-ss", "0.5", "-vframes", "1",
                           "-vf", "scale=160:-1", str(thumb_path)]
                    subprocess.run(cmd, capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except Exception:
                    pass
            if thumb_path.exists():
                pix = QPixmap(str(thumb_path))
                if not pix.isNull():
                    self.lbl_vid.setPixmap(pix.scaled(self.lbl_vid.width() - 2, self.lbl_vid.height() - 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    self.lbl_vid.setStyleSheet("border:1.5px solid #38bdf8; border-radius:4px;")
                else:
                    self.lbl_vid.setText("🎬")
            else:
                self.lbl_vid.setText("🎬✔")
                self.lbl_vid.setStyleSheet("background:#dcfce7; border:1px solid #22c55e; border-radius:4px; color:#16a34a; font-size:14px;")
        else:
            self.lbl_vid.setText("🎬")
            self.lbl_vid.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8;")

    def _on_img_prompt_changed(self):
        self.vr_scene.image_prompt = self.edit_img_prompt.toPlainText()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _on_vid_prompt_changed(self):
        self.vr_scene.video_prompt = self.edit_vid_prompt.toPlainText()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _on_voice_changed(self):
        self.vr_scene.voiceover_text = self.edit_voice.toPlainText()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _play_video(self):
        vp = self.vr_scene.video_clip_path
        if vp and Path(vp).exists():
            try: os.startfile(vp)
            except Exception: pass
        elif self.vr_scene.image_path and Path(self.vr_scene.image_path).exists():
            try: os.startfile(self.vr_scene.image_path)
            except Exception: pass

    def _open_folder(self):
        for p in [self.vr_scene.video_clip_path, self.vr_scene.image_path]:
            if p and Path(p).exists():
                try: os.startfile(Path(p).parent)
                except Exception: pass
                return

    def _on_dbl_click_image(self, event):
        win = self.card.main_win if (self.card and hasattr(self.card, 'main_win')) else self.window()
        dlg = VRSceneImageViewerDialog(self.vr_scene, self.card.product, self, win)
        dlg.exec()

    def _on_dbl_click_video(self, event):
        self._play_video()

    def update_row_data(self):
        self._refresh_status()
        if self.edit_img_prompt.toPlainText() != (self.vr_scene.image_prompt or ""):
            self.edit_img_prompt.blockSignals(True)
            self.edit_img_prompt.setPlainText(self.vr_scene.image_prompt or "")
            self.edit_img_prompt.blockSignals(False)
            
        if self.edit_vid_prompt.toPlainText() != (self.vr_scene.video_prompt or ""):
            self.edit_vid_prompt.blockSignals(True)
            self.edit_vid_prompt.setPlainText(self.vr_scene.video_prompt or "")
            self.edit_vid_prompt.blockSignals(False)

        if self.edit_voice.toPlainText() != (self.vr_scene.voiceover_text or ""):
            self.edit_voice.blockSignals(True)
            self.edit_voice.setPlainText(self.vr_scene.voiceover_text or "")
            self.edit_voice.blockSignals(False)
        self._refresh_thumbnails()


def get_product_folder(product: ProductItem, settings: dict = None) -> Path:
    # 1. If final_combined_path or final_video_path exists, use its directory
    for path_str in [product.final_combined_path, product.final_video_path]:
        if path_str:
            p = Path(path_str).parent
            if p.exists():
                return p
    # 2. Re-create the path using the title and ID
    import re
    def sanitize_filename(name):
        return re.sub(r'[\\/*?:"<>|]', "", name).strip()[:100]
    
    out_dir_setting = settings.get("output_dir") if settings else None
    out_base = Path(out_dir_setting) if out_dir_setting else OUTPUT_DIR
    prod_dir_name = f"{sanitize_filename(product.title or 'san_pham')}_{product.id}"
    p = out_base / prod_dir_name
    p.mkdir(parents=True, exist_ok=True)
    return p


class VRSceneImageViewerDialog(QDialog):
    def __init__(self, vr_scene: VideoReviewScene, product: ProductItem, parent_row: "VRSceneRow", main_win: QMainWindow):
        super().__init__(main_win)
        self.vr_scene = vr_scene
        self.product = product
        self.parent_row = parent_row
        self.main_win = main_win
        
        self.setWindowTitle(f"🖼️ Xem & Sửa Phân Cảnh Video Review #{vr_scene.id}")
        self.setMinimumSize(900, 750)
        self.resize(950, 800)
        self.setStyleSheet("""
            QDialog { background-color: #0f172a; }
            QLabel { color: #e2e8f0; background: transparent; }
            QPushButton { background-color: #334155; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton:hover { background-color: #475569; }
        """)
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 12, 12, 12)
        self.layout.setSpacing(8)
        
        # Scene info bar
        info_lay = QHBoxLayout()
        info_lay.addWidget(QLabel(f"<b>Phân cảnh #{vr_scene.id}</b>" + (f" | Lời thoại: <i>{vr_scene.voiceover_text}</i>" if vr_scene.voiceover_text else "")))
        info_lay.addStretch()
        self.layout.addLayout(info_lay)
        
        # Image Display Area — large
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("border: 1px solid #334155; background: #1e293b; border-radius: 8px;")
        
        self.lbl_image = QLabel()
        self.lbl_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll_area.setWidget(self.lbl_image)
        self.layout.addWidget(self.scroll_area, stretch=1)
        
        # Prompt section — collapsible
        self.btn_toggle_prompt = QPushButton("📝 Xem Prompt ▼")
        self.btn_toggle_prompt.setStyleSheet("QPushButton{background:#1e293b;color:#94a3b8;font-size:11px;padding:4px 10px;border:1px solid #334155;border-radius:4px;}QPushButton:hover{background:#334155;}")
        self.btn_toggle_prompt.setFixedHeight(26)
        self.btn_toggle_prompt.clicked.connect(self._toggle_prompt)
        self.layout.addWidget(self.btn_toggle_prompt)
        
        self.txt_prompt = QTextEdit()
        self.txt_prompt.setPlainText(vr_scene.image_prompt)
        self.txt_prompt.setReadOnly(True)
        self.txt_prompt.setMaximumHeight(120)
        self.txt_prompt.setStyleSheet("QTextEdit{color:#94a3b8;background:#1e293b;border:1px solid #334155;border-radius:4px;font-size:11px;padding:4px;}")
        self.txt_prompt.setVisible(False)
        self.layout.addWidget(self.txt_prompt)
        
        # Controls Layout
        self.btn_lay = QHBoxLayout()
        self.btn_lay.setSpacing(10)
        
        # Crop Button
        self.btn_crop = QPushButton("✂️ Crop Ảnh")
        self.btn_crop.setStyleSheet("QPushButton { background-color: #eab308; color: black; } QPushButton:hover { background-color: #ca8a04; }")
        self.btn_crop.clicked.connect(self._on_crop_clicked)
        self.btn_lay.addWidget(self.btn_crop)
        
        self.btn_lay.addStretch()
        
        # Save / Close Button
        self.btn_save = QPushButton("💾 Lưu & Đóng")
        self.btn_save.setStyleSheet("QPushButton { background-color: #7c3aed; color: white; width: 100px; } QPushButton:hover { background-color: #6d28d9; }")
        self.btn_save.clicked.connect(self.accept)
        self.btn_lay.addWidget(self.btn_save)
        
        self.layout.addLayout(self.btn_lay)
        
        self.refresh_preview()
        
    def refresh_preview(self):
        img_path = self.vr_scene.image_path
        if img_path and Path(img_path).exists():
            pix = QPixmap(img_path)
            if not pix.isNull():
                self.lbl_image.setPixmap(pix.scaled(850, 700, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                return
        self.lbl_image.setText("Chưa có ảnh hoặc ảnh đã bị xoá")
        self.lbl_image.setStyleSheet("color: #64748b; font-size: 14px; font-weight: bold;")

    def _toggle_prompt(self):
        vis = not self.txt_prompt.isVisible()
        self.txt_prompt.setVisible(vis)
        self.btn_toggle_prompt.setText("📝 Ẩn Prompt ▲" if vis else "📝 Xem Prompt ▼")
        
    def _on_crop_clicked(self):
        img_path = self.vr_scene.image_path
        if not img_path or not Path(img_path).exists():
            QMessageBox.warning(self, "Cảnh báo", "Không tìm thấy tệp ảnh phân cảnh để crop!")
            return
        
        # Create temp folder inside product's directory
        temp_dir = Path(img_path).parent / "temp"
        temp_dir.mkdir(exist_ok=True)
        
        dlg = CropperDialog(img_path, temp_dir, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cropped_file = Path(dlg.cropped_path)
            if cropped_file.exists():
                try:
                    # Overwrite the original image
                    import shutil
                    shutil.copy2(cropped_file, img_path)
                    
                    # Refresh dialog and parent widget
                    self.refresh_preview()
                    if self.parent_row:
                        self.parent_row._refresh_thumbnails()
                        
                    # Save changes
                    if self.main_win and hasattr(self.main_win, "_save_projects"):
                        self.main_win._save_projects()
                        
                    QMessageBox.information(self, "Thành công", "Đã crop và ghi đè ảnh gốc thành công!")
                except Exception as e:
                    QMessageBox.critical(self, "Lỗi", f"Không thể ghi đè ảnh gốc: {e}")


class VRExternalPreview(QWidget):
    """Widget hiển thị preview + editing controls cho External Video file.
    Hiển thị song song: Video Gốc vs. Video Review Sản Phẩm (Đã Xử Lý)."""
    def __init__(self, product: 'ProductItem', parent=None):
        super().__init__(parent)
        self.product = product
        self.setStyleSheet("QWidget { background: transparent; }")

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(6, 6, 6, 6)
        main_lay.setSpacing(6)

        # ── Row 1: Side-by-Side Previews ──
        previews_row = QHBoxLayout()
        previews_row.setSpacing(12)

        # 1. Left Panel: Original Video
        orig_box = QFrame()
        orig_box.setStyleSheet("QFrame { background:#f1f5f9; border:1px solid #cbd5e1; border-radius:6px; }")
        orig_lay = QVBoxLayout(orig_box)
        orig_lay.setContentsMargins(6, 6, 6, 6)
        orig_lay.setSpacing(4)

        lbl_orig_title = QLabel("📹 Video Gốc (Đầu Vào):")
        lbl_orig_title.setStyleSheet("font-size:10px; font-weight:bold; color:#475569; border:none;")
        orig_lay.addWidget(lbl_orig_title)

        row_orig_media = QHBoxLayout()
        row_orig_media.setSpacing(6)
        self.lbl_thumb_orig = QLabel()
        self.lbl_thumb_orig.setFixedSize(96, 56)
        self.lbl_thumb_orig.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb_orig.setStyleSheet("background:#e2e8f0; border:1px solid #94a3b8; border-radius:4px; color:#64748b; font-size:18px;")
        self.lbl_thumb_orig.setText("🎥")
        self.lbl_thumb_orig.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_thumb_orig.mouseDoubleClickEvent = self._play_orig
        row_orig_media.addWidget(self.lbl_thumb_orig)

        info_orig = QVBoxLayout()
        info_orig.setSpacing(2)
        self.lbl_name_orig = QLabel("Chưa chọn video")
        self.lbl_name_orig.setStyleSheet("font-size:10px; font-weight:bold; color:#1e293b; border:none;")
        info_orig.addWidget(self.lbl_name_orig)
        self.lbl_info_orig = QLabel("")
        self.lbl_info_orig.setStyleSheet("font-size:8px; color:#64748b; border:none;")
        info_orig.addWidget(self.lbl_info_orig)
        row_orig_media.addLayout(info_orig, 1)
        orig_lay.addLayout(row_orig_media)

        # File picker inline
        file_row = QHBoxLayout()
        file_row.setSpacing(4)
        self.txt_file = QLineEdit()
        self.txt_file.setPlaceholderText("Chọn file video...")
        self.txt_file.setFixedHeight(22)
        self.txt_file.setStyleSheet(
            "QLineEdit { background:#ffffff; border:1px solid #cbd5e1; border-radius:4px; "
            "padding:1px 4px; font-size:9px; color:#1e293b; }"
        )
        self.txt_file.setText(product.external_video_path or "")
        self.txt_file.textChanged.connect(self._on_file_changed)
        file_row.addWidget(self.txt_file)
        btn_browse = QPushButton("📂")
        btn_browse.setFixedSize(22, 22)
        btn_browse.setStyleSheet(
            "QPushButton { background:#64748b; color:white; border:none; border-radius:4px; font-size:10px; }"
            "QPushButton:hover { background:#475569; }"
        )
        btn_browse.clicked.connect(self._browse_file)
        file_row.addWidget(btn_browse)
        orig_lay.addLayout(file_row)
        previews_row.addWidget(orig_box, stretch=1)

        # 2. Right Panel: Processed Video Review
        proc_box = QFrame()
        proc_box.setStyleSheet("QFrame { background:#faf5ff; border:1px solid #d8b4fe; border-radius:6px; }")
        proc_lay = QVBoxLayout(proc_box)
        proc_lay.setContentsMargins(6, 6, 6, 6)
        proc_lay.setSpacing(4)

        lbl_proc_title = QLabel("🎬 Video Review Sản Phẩm (Đã Xử Lý):")
        lbl_proc_title.setStyleSheet("font-size:10px; font-weight:bold; color:#7c3aed; border:none;")
        proc_lay.addWidget(lbl_proc_title)

        row_proc_media = QHBoxLayout()
        row_proc_media.setSpacing(6)
        self.lbl_thumb_proc = QLabel()
        self.lbl_thumb_proc.setFixedSize(96, 56)
        self.lbl_thumb_proc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumb_proc.setStyleSheet("background:#ede9fe; border:1px solid #c084fc; border-radius:4px; color:#7c3aed; font-size:18px;")
        self.lbl_thumb_proc.setText("🎬")
        self.lbl_thumb_proc.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_thumb_proc.mouseDoubleClickEvent = self._play_proc
        row_proc_media.addWidget(self.lbl_thumb_proc)

        info_proc = QVBoxLayout()
        info_proc.setSpacing(2)
        self.lbl_name_proc = QLabel("Chưa xử lý")
        self.lbl_name_proc.setStyleSheet("font-size:10px; font-weight:bold; color:#7c3aed; border:none;")
        info_proc.addWidget(self.lbl_name_proc)
        self.lbl_info_proc = QLabel("")
        self.lbl_info_proc.setStyleSheet("font-size:8px; color:#7c3aed; border:none;")
        info_proc.addWidget(self.lbl_info_proc)
        row_proc_media.addLayout(info_proc, 1)
        proc_lay.addLayout(row_proc_media)

        # Process actions buttons
        proc_btn_row = QHBoxLayout()
        proc_btn_row.setSpacing(4)
        btn_play_proc = QPushButton("▶ Xem Thử")
        btn_play_proc.setFixedHeight(22)
        btn_play_proc.setStyleSheet(
            "QPushButton { background:#7c3aed; color:white; border:none; border-radius:4px; font-size:9px; font-weight:bold; }"
            "QPushButton:hover { background:#6d28d9; }"
        )
        btn_play_proc.clicked.connect(self._play_proc)
        proc_btn_row.addWidget(btn_play_proc, stretch=1)

        btn_dir_proc = QPushButton("📂 Folder")
        btn_dir_proc.setFixedHeight(22)
        btn_dir_proc.setStyleSheet(
            "QPushButton { background:#334155; color:white; border:none; border-radius:4px; font-size:9px; font-weight:bold; }"
            "QPushButton:hover { background:#475569; }"
        )
        btn_dir_proc.clicked.connect(self._open_folder)
        proc_btn_row.addWidget(btn_dir_proc)
        proc_lay.addLayout(proc_btn_row)
        previews_row.addWidget(proc_box, stretch=1)

        main_lay.addLayout(previews_row)

        # ── Divider ──
        div = QFrame()
        div.setFrameShape(QFrame.Shape.HLine)
        div.setStyleSheet("color:#e2e8f0;")
        main_lay.addWidget(div)

        # ── Row 2: Editing Controls ──
        lbl_edit = QLabel("⚙️ Các tham số điều chỉnh Video (Crop, Speed, Anti-Reup, Cắt Random...):")
        lbl_edit.setStyleSheet("font-size:10px; font-weight:bold; color:#4c1d95;")
        main_lay.addWidget(lbl_edit)

        # Row 2a: Crop + Speed + Max Duration
        row2a = QHBoxLayout()
        row2a.setSpacing(8)

        row2a.addWidget(self._mk_lbl("Crop:"))
        self.cb_crop = QComboBox()
        self.cb_crop.addItems(["Vuông giữa (1:1)", "9:16 giữa", "Giữ nguyên"])
        self.cb_crop.setFixedHeight(24)
        self.cb_crop.setStyleSheet(self._combo_style())
        row2a.addWidget(self.cb_crop)

        row2a.addWidget(self._mk_lbl("Tốc độ:"))
        self.sp_speed = QDoubleSpinBox()
        self.sp_speed.setRange(0.5, 2.0)
        self.sp_speed.setSingleStep(0.1)
        self.sp_speed.setValue(1.0)
        self.sp_speed.setSuffix("x")
        self.sp_speed.setFixedHeight(24)
        self.sp_speed.setFixedWidth(65)
        row2a.addWidget(self.sp_speed)

        row2a.addWidget(self._mk_lbl("Max(s):"))
        self.sp_max_dur = QSpinBox()
        self.sp_max_dur.setRange(5, 120)
        self.sp_max_dur.setValue(30)
        self.sp_max_dur.setFixedHeight(24)
        self.sp_max_dur.setFixedWidth(55)
        row2a.addWidget(self.sp_max_dur)
        row2a.addStretch()
        main_lay.addLayout(row2a)

        # Row 2b: Anti-reup checkboxes
        row2b = QHBoxLayout()
        row2b.setSpacing(12)
        self.chk_zoom = QCheckBox("🔍 Zoom random (1.0-1.1x)")
        self.chk_zoom.setStyleSheet("font-size:9px; color:#334155;")
        row2b.addWidget(self.chk_zoom)
        self.chk_flip = QCheckBox("↔️ Lật ngang (anti-reup)")
        self.chk_flip.setStyleSheet("font-size:9px; color:#334155;")
        row2b.addWidget(self.chk_flip)
        self.chk_noise = QCheckBox("✨ Noise nhẹ (anti-detect)")
        self.chk_noise.setChecked(True)
        self.chk_noise.setStyleSheet("font-size:9px; color:#334155;")
        row2b.addWidget(self.chk_noise)
        row2b.addStretch()
        main_lay.addLayout(row2b)

        # Row 2c: Random cut
        row2c = QHBoxLayout()
        row2c.setSpacing(6)
        self.chk_rcut = QCheckBox("✂️ Cắt random khúc (0.2-0.5s)")
        self.chk_rcut.setChecked(True)
        self.chk_rcut.setStyleSheet("font-size:9px; color:#334155;")
        row2c.addWidget(self.chk_rcut)
        row2c.addWidget(self._mk_lbl("mỗi"))
        self.sp_rcut_min = QSpinBox()
        self.sp_rcut_min.setRange(2, 10)
        self.sp_rcut_min.setValue(3)
        self.sp_rcut_min.setFixedWidth(42)
        self.sp_rcut_min.setFixedHeight(22)
        row2c.addWidget(self.sp_rcut_min)
        row2c.addWidget(self._mk_lbl("→"))
        self.sp_rcut_max = QSpinBox()
        self.sp_rcut_max.setRange(3, 15)
        self.sp_rcut_max.setValue(5)
        self.sp_rcut_max.setFixedWidth(42)
        self.sp_rcut_max.setFixedHeight(22)
        row2c.addWidget(self.sp_rcut_max)
        row2c.addWidget(self._mk_lbl("(s)"))
        row2c.addStretch()

        # Re-process button
        self.btn_reprocess = QPushButton("🔄 Xử lý lại Video Review")
        self.btn_reprocess.setFixedHeight(26)
        self.btn_reprocess.setStyleSheet(
            "QPushButton { background:qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #16a34a, stop:1 #10b981); "
            "color:white; border:none; border-radius:5px; font-size:10px; font-weight:bold; padding:0 14px; }"
            "QPushButton:hover { background:#15803d; }"
        )
        self.btn_reprocess.clicked.connect(self._on_reprocess)
        row2c.addWidget(self.btn_reprocess)
        main_lay.addLayout(row2c)

        self._load_settings()
        self._refresh_preview()

        # Auto-save settings on change
        self.cb_crop.currentIndexChanged.connect(self._save_current_settings)
        self.sp_speed.valueChanged.connect(self._save_current_settings)
        self.sp_max_dur.valueChanged.connect(self._save_current_settings)
        self.chk_zoom.stateChanged.connect(self._save_current_settings)
        self.chk_flip.stateChanged.connect(self._save_current_settings)
        self.chk_noise.stateChanged.connect(self._save_current_settings)
        self.chk_rcut.stateChanged.connect(self._save_current_settings)
        self.sp_rcut_min.valueChanged.connect(self._save_current_settings)
        self.sp_rcut_max.valueChanged.connect(self._save_current_settings)

    def _mk_lbl(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size:9px; color:#475569; font-weight:bold;")
        return lbl

    def _combo_style(self):
        return (
            "QComboBox { background:#ffffff; border:1px solid #cbd5e1; border-radius:4px; "
            "padding:2px 6px; font-size:9px; color:#1e293b; min-height:20px; }"
        )

    def _load_settings(self):
        win = self.window()
        s = getattr(win, 'settings', {}) if win else {}
        crop = s.get("vr_crop_mode", "square_center")
        crop_map = {"square_center": 0, "9_16_center": 1, "keep_original": 2}
        self.cb_crop.setCurrentIndex(crop_map.get(crop, 0))
        self.sp_speed.setValue(s.get("vr_speed", 1.0))
        self.sp_max_dur.setValue(s.get("vr_max_duration", 30))
        self.chk_zoom.setChecked(s.get("vr_zoom_random", False))
        self.chk_flip.setChecked(s.get("vr_flip_h", False))
        self.chk_noise.setChecked(s.get("vr_noise", True))
        self.chk_rcut.setChecked(s.get("vr_random_cut", True))
        self.sp_rcut_min.setValue(s.get("vr_rcut_interval_min", 3))
        self.sp_rcut_max.setValue(s.get("vr_rcut_interval_max", 5))

    def _get_vr_settings(self) -> dict:
        crop_modes = ["square_center", "9_16_center", "keep_original"]
        return {
            "vr_crop_mode": crop_modes[self.cb_crop.currentIndex()],
            "vr_speed": self.sp_speed.value(),
            "vr_max_duration": self.sp_max_dur.value(),
            "vr_zoom_random": self.chk_zoom.isChecked(),
            "vr_flip_h": self.chk_flip.isChecked(),
            "vr_noise": self.chk_noise.isChecked(),
            "vr_random_cut": self.chk_rcut.isChecked(),
            "vr_rcut_interval_min": self.sp_rcut_min.value(),
            "vr_rcut_interval_max": self.sp_rcut_max.value(),
        }

    def _save_current_settings(self):
        win = self.window()
        if win and hasattr(win, "settings"):
            win.settings.update(self._get_vr_settings())
            if hasattr(win, "_save_settings"):
                win._save_settings(win.settings)

    def _refresh_preview(self):
        # 1. Preview Original Video
        orig_path = self.product.external_video_path
        if orig_path and Path(orig_path).exists():
            self.lbl_name_orig.setText(f"📹 {Path(orig_path).name}")
            try:
                result = subprocess.run(
                    [FFPROBE_PATH, "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(orig_path)],
                    capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                )
                dur = float(result.stdout.strip())
                mins, secs = divmod(int(dur), 60)
                self.lbl_info_orig.setText(f"⏱ {mins:02d}:{secs:02d}  •  {Path(orig_path).stat().st_size / 1024 / 1024:.1f} MB")
            except Exception:
                self.lbl_info_orig.setText(f"📦 {Path(orig_path).stat().st_size / 1024 / 1024:.1f} MB")

            # Extract thumbnail
            thumb_orig = Path(orig_path).parent / f".thumb_orig_{self.product.id}.jpg"
            if not thumb_orig.exists():
                try:
                    cmd = [FFMPEG_PATH, "-y", "-i", str(orig_path), "-ss", "0.5", "-vframes", "1",
                           "-vf", "scale=160:-1", str(thumb_orig)]
                    subprocess.run(cmd, capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except Exception:
                    pass
            if thumb_orig.exists():
                pix = QPixmap(str(thumb_orig))
                if not pix.isNull():
                    self.lbl_thumb_orig.setPixmap(pix.scaled(self.lbl_thumb_orig.width() - 2, self.lbl_thumb_orig.height() - 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    self.lbl_thumb_orig.setStyleSheet("border:1.5px solid #64748b; border-radius:4px;")
        else:
            self.lbl_name_orig.setText("Chưa chọn video")
            self.lbl_info_orig.setText("")
            self.lbl_thumb_orig.setPixmap(QPixmap())
            self.lbl_thumb_orig.setText("🎥")
            self.lbl_thumb_orig.setStyleSheet("background:#e2e8f0; border:1px dashed #94a3b8; border-radius:4px; color:#64748b; font-size:18px;")

        # 2. Preview Processed Video Review
        win = self.window()
        settings = getattr(win, "settings", None)
        prod_dir = get_product_folder(self.product, settings)
        proc_path = prod_dir / f"vr_processed_{self.product.id}.mp4"

        if proc_path and proc_path.exists():
            self.lbl_name_proc.setText(f"✅ {proc_path.name}")
            try:
                result = subprocess.run(
                    [FFPROBE_PATH, "-v", "quiet", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(proc_path)],
                    capture_output=True, text=True, timeout=5,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
                )
                dur = float(result.stdout.strip())
                mins, secs = divmod(int(dur), 60)
                self.lbl_info_proc.setText(f"⏱ {mins:02d}:{secs:02d}  •  {proc_path.stat().st_size / 1024 / 1024:.1f} MB")
            except Exception:
                self.lbl_info_proc.setText(f"📦 {proc_path.stat().st_size / 1024 / 1024:.1f} MB")

            # Extract thumbnail
            thumb_proc = prod_dir / f".thumb_proc_{self.product.id}.jpg"
            if not thumb_proc.exists():
                try:
                    cmd = [FFMPEG_PATH, "-y", "-i", str(proc_path), "-ss", "0.5", "-vframes", "1",
                           "-vf", "scale=160:-1", str(thumb_proc)]
                    subprocess.run(cmd, capture_output=True, timeout=10,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                except Exception:
                    pass
            if thumb_proc.exists():
                pix = QPixmap(str(thumb_proc))
                if not pix.isNull():
                    self.lbl_thumb_proc.setPixmap(pix.scaled(self.lbl_thumb_proc.width() - 2, self.lbl_thumb_proc.height() - 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                    self.lbl_thumb_proc.setStyleSheet("border:1.5px solid #c084fc; border-radius:4px;")
        else:
            self.lbl_name_proc.setText("Chưa xử lý video review")
            self.lbl_info_proc.setText("")
            self.lbl_thumb_proc.setPixmap(QPixmap())
            self.lbl_thumb_proc.setText("🎬")
            self.lbl_thumb_proc.setStyleSheet("background:#f3f4f6; border:1px dashed #cbd5e1; border-radius:4px; color:#94a3b8; font-size:18px;")

    def _on_file_changed(self, text):
        self.product.external_video_path = text.strip()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()
        self._refresh_preview()

    def _browse_file(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn video sản phẩm", "", "Video (*.mp4 *.mov *.avi *.mkv *.webm)")
        if f:
            self.txt_file.setText(f)

    def _play_orig(self, event=None):
        p = self.product.external_video_path
        if p and Path(p).exists():
            try: os.startfile(p)
            except Exception: pass

    def _play_proc(self, event=None):
        win = self.window()
        settings = getattr(win, "settings", None)
        prod_dir = get_product_folder(self.product, settings)
        proc_path = prod_dir / f"vr_processed_{self.product.id}.mp4"
        if proc_path and proc_path.exists():
            try: os.startfile(proc_path)
            except Exception: pass

    def _open_folder(self):
        win = self.window()
        settings = getattr(win, "settings", None)
        prod_dir = get_product_folder(self.product, settings)
        if prod_dir and prod_dir.exists():
            try: os.startfile(prod_dir)
            except Exception: pass

    def _on_reprocess(self):
        """Re-process external video with current panel settings."""
        ext_path = self.product.external_video_path or self.txt_file.text().strip()
        if not ext_path or not Path(ext_path).exists():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Lỗi", "Chưa chọn file video hoặc file không tồn tại.")
            return

        vr_settings = self._get_vr_settings()
        win = self.window()
        settings = getattr(win, "settings", None)
        prod_dir = get_product_folder(self.product, settings)
        output = prod_dir / f"vr_processed_{self.product.id}.mp4"

        # Log
        log_fn = None
        if hasattr(win, 'txt_log'):
            def log_fn(msg):
                win.txt_log.append(msg)

        if log_fn: log_fn(f"  🔄 Re-processing: {Path(ext_path).name}...")

        result = process_external_video(str(ext_path), str(output), vr_settings, log_fn)
        if result and Path(result).exists():
            # Combine with Part 1 if it exists
            if self.product.final_video_path and Path(self.product.final_video_path).exists():
                combined = prod_dir / f"combined_{self.product.id}.mp4"
                final = combine_final_video(
                    self.product.final_video_path, str(result),
                    str(combined), "direct_cut", log_fn
                )
                if final:
                    self.product.final_combined_path = final
            else:
                self.product.final_combined_path = result

            self.product.external_video_path = ext_path
            self.product.video_review_mode = "external_file"
            if hasattr(win, "_save_projects"): win._save_projects()
            
            # Delete old thumbnail to force re-extract
            thumb = prod_dir / f".thumb_proc_{self.product.id}.jpg"
            try: thumb.unlink(missing_ok=True)
            except: pass
            
            self._refresh_preview()
            if log_fn: log_fn(f"  ✅ Re-process hoàn thành!")
        else:
            if log_fn: log_fn(f"  ❌ Lỗi re-process video")

    def _refresh(self):
        """Public refresh method called from load_vr_scenes."""
        self._refresh_preview()


class ThreadsRefImageWidget(QWidget):
    def __init__(self, idx, path, parent_card):
        super().__init__()
        self.idx = idx
        self.path = path
        self.parent_card = parent_card
        self.setFixedSize(45, 45)
        
        # Grid layout to stack widgets on top of each other
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Thumbnail label
        self.lbl = QLabel()
        self.lbl.setFixedSize(45, 45)
        self.lbl.setStyleSheet("border: 1px solid #cbd5e1; border-radius: 4px; background: white;")
        if Path(path).exists():
            pix = QPixmap(path)
            self.lbl.setPixmap(pix.scaled(43, 43, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(self.lbl, 0, 0)
        
        # Delete button overlay
        self.btn_del = QPushButton("×", self)
        self.btn_del.setFixedSize(14, 14)
        self.btn_del.setStyleSheet(
            "QPushButton { background: rgba(239, 68, 68, 0.85); color: white; border: none; border-radius: 7px; "
            "font-size: 10px; font-weight: bold; line-height: 12px; }"
            "QPushButton:hover { background: rgb(220, 38, 38); }"
        )
        self.btn_del.move(31, 0)
        self.btn_del.clicked.connect(self.delete_self)
        
    def delete_self(self):
        if self.path in self.parent_card.product.images:
            self.parent_card.product.images.remove(self.path)
            win = self.parent_card.window()
            if hasattr(win, "_save_projects"): win._save_projects()
            self.parent_card.refresh_ref_images()


class ThreadsProductCard(QFrame):
    """Card hiển thị một Sản Phẩm với các Phân cảnh Video kết quả."""
    def __init__(self, product_idx: int, product: ProductItem, parent=None):
        super().__init__(parent)
        self.product_idx = product_idx
        self.product = product
        self.scene_rows = []
        
        self.setObjectName("threads_prod_card")
        self.setStyleSheet(
            "QFrame#threads_prod_card { background-color: #ffffff; border: 1.5px solid #e2e8f0; border-radius: 12px; }"
        )
        
        # Main Layout (Horizontal)
        self.main_lay = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.main_lay.setContentsMargins(10, 10, 10, 10)
        self.main_lay.setSpacing(10)
        
        # --- Left Section: Product Info (width 240px) ---
        self.info_widget = QWidget()
        self.info_widget.setFixedWidth(240)
        self.info_widget.setStyleSheet("background: transparent; border: none;")
        info_lay = QVBoxLayout(self.info_widget)
        info_lay.setContentsMargins(0, 0, 0, 0)
        info_lay.setSpacing(6)
        
        # Product Image Thumbnail
        self.lbl_thumb = QLabel()
        self.lbl_thumb.setFixedSize(80, 80)
        self.lbl_thumb.setStyleSheet("background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 8px;")
        self.lbl_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.refresh_thumb()
        self.lbl_thumb.mouseDoubleClickEvent = self._on_double_click_product_image
        info_lay.addWidget(self.lbl_thumb, alignment=Qt.AlignmentFlag.AlignCenter)
        
        # Product Name
        name_lay = QHBoxLayout()
        name_lay.setSpacing(4)
        lbl_name_title = QLabel("Tên:")
        lbl_name_title.setStyleSheet("font-weight: bold; font-size: 10px; color: #475569;")
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Tên sản phẩm...")
        self.edit_name.setStyleSheet("QLineEdit { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 10px; color: #1e293b; }")
        self.edit_name.blockSignals(True)
        self.edit_name.setText(product.title)
        self.edit_name.blockSignals(False)
        self.edit_name.textChanged.connect(self._on_name_changed)
        name_lay.addWidget(lbl_name_title)
        name_lay.addWidget(self.edit_name)
        info_lay.addLayout(name_lay)
        
        # Product Requirements
        lbl_req = QLabel("Yêu cầu riêng:")
        lbl_req.setStyleSheet("font-weight: bold; font-size: 10px; color: #475569;")
        info_lay.addWidget(lbl_req)
        
        self.edit_req = QTextEdit()
        self.edit_req.setPlaceholderText("Yêu cầu riêng (ví dụ: nền sang trọng, slow motion, v.v.)...")
        self.edit_req.setStyleSheet("QTextEdit { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 9px; color: #1e293b; }")
        self.edit_req.setFixedHeight(90)
        self.edit_req.blockSignals(True)
        self.edit_req.setText(product.info)
        self.edit_req.blockSignals(False)
        self.edit_req.textChanged.connect(self._on_req_changed)
        info_lay.addWidget(self.edit_req)
        
        # ── Reference Images Section ──
        ref_title_lay = QHBoxLayout()
        ref_title_lay.setContentsMargins(0, 0, 0, 0)
        ref_title_lay.setSpacing(4)
        lbl_ref = QLabel("Ảnh tham chiếu:")
        lbl_ref.setStyleSheet("font-weight: bold; font-size: 10px; color: #475569;")
        ref_title_lay.addWidget(lbl_ref)
        ref_title_lay.addStretch()
        
        self.chk_use_ref_gen = QCheckBox("Dùng cho cả tạo ảnh")
        self.chk_use_ref_gen.setStyleSheet("QCheckBox { font-size: 9px; color: #475569; font-weight: normal; }")
        self.chk_use_ref_gen.setChecked(getattr(self.product, "use_ref_for_generation", False))
        self.chk_use_ref_gen.toggled.connect(self._on_use_ref_gen_toggled)
        ref_title_lay.addWidget(self.chk_use_ref_gen)
        
        self.add_ref_btn = QPushButton("➕ Thêm")
        self.add_ref_btn.setFixedSize(50, 20)
        self.add_ref_btn.setStyleSheet(
            "QPushButton { background: #7c3aed; color: white; border-radius: 4px; "
            "font-weight: bold; font-size: 9px; }"
            "QPushButton:hover { background: #6d28d9; }"
        )
        self.add_ref_btn.clicked.connect(self._on_browse_ref_images)
        ref_title_lay.addWidget(self.add_ref_btn)
        info_lay.addLayout(ref_title_lay)
        
        self.ref_scroll = QScrollArea()
        self.ref_scroll.setFixedHeight(55)
        self.ref_scroll.setWidgetResizable(True)
        self.ref_scroll.setStyleSheet("QScrollArea { border: 1px solid #cbd5e1; border-radius: 4px; background: #f8fafc; }")
        self.ref_widget = QWidget()
        self.ref_lay = QHBoxLayout(self.ref_widget)
        self.ref_lay.setContentsMargins(2, 2, 2, 2)
        self.ref_lay.setSpacing(4)
        self.ref_lay.addStretch()
        self.ref_scroll.setWidget(self.ref_widget)
        info_lay.addWidget(self.ref_scroll)
        self.refresh_ref_images()
        
        # ── Phong cách Section ──
        ctx_title_lay = QHBoxLayout()
        ctx_title_lay.setContentsMargins(0, 0, 0, 0)
        ctx_title_lay.setSpacing(4)
        lbl_ctx_title = QLabel("🎭 Phong cách:")
        lbl_ctx_title.setStyleSheet("font-weight: bold; font-size: 10px; color: #475569;")
        ctx_title_lay.addWidget(lbl_ctx_title)
        ctx_title_lay.addStretch()
        info_lay.addLayout(ctx_title_lay)
        
        self.context_combo = QComboBox()
        self.context_combo.setStyleSheet(
            "QComboBox { background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 9px; color: #1e293b; }"
        )
        self.main_win = parent
        if self.main_win and hasattr(self.main_win, "_load_archetypes_into_combo"):
            self.main_win._load_archetypes_into_combo(self.context_combo)
        else:
            self.context_combo.addItem("Tự chọn phong cách tối ưu nhất", "Để LLM tự chọn phong cách kịch bản.")
        self.context_combo.setCurrentText(product.context)
        self.context_combo.currentIndexChanged.connect(self._on_context_changed)
        info_lay.addWidget(self.context_combo)
        
        self.context_preview = QTextEdit()
        self.context_preview.setReadOnly(True)
        self.context_preview.setFixedHeight(45)
        self.context_preview.setPlaceholderText("Mô tả phong cách...")
        self.context_preview.setStyleSheet(
            "QTextEdit { background: #fefcbf; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 8px; color: #78350f; }"
        )
        info_lay.addWidget(self.context_preview)
        self.update_context_preview()
        
        info_lay.addStretch()
        self.main_lay.addWidget(self.info_widget)
        
        # --- Right Section: Status Bar & Scenes List ---
        self.right_w = QWidget()
        self.right_w.setStyleSheet("background: transparent; border: none;")
        self.right_lay = QVBoxLayout(self.right_w)
        self.right_lay.setContentsMargins(0, 0, 0, 0)
        self.right_lay.setSpacing(6)
        
        # Status Bar
        self.status_bar = QWidget()
        sb_lay = QHBoxLayout(self.status_bar)
        sb_lay.setContentsMargins(0, 0, 0, 0)
        sb_lay.setSpacing(6)
        
        self.chk_select = QCheckBox()
        self.chk_select.setStyleSheet("QCheckBox::indicator { width: 14px; height: 14px; }")
        sb_lay.addWidget(self.chk_select)
        
        self.lbl_title = QLabel(f"📦 SP #{self.product_idx + 1}")
        self.lbl_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #1e293b;")
        sb_lay.addWidget(self.lbl_title)
        sb_lay.addStretch()
        
        self.lbl_status = QLabel("⏳ Chờ")
        self.lbl_status.setStyleSheet("background: #f1f5f9; color: #64748b; font-size: 9px; border-radius: 6px; padding: 2px 6px; font-weight: bold;")
        self.update_status_ui()
        sb_lay.addWidget(self.lbl_status)
        
        # Action Menu Button ⋯
        self.btn_menu = QToolButton()
        self.btn_menu.setText("⋯")
        self.btn_menu.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_menu.setFixedSize(26, 26)
        self.btn_menu.setStyleSheet(
            "QToolButton { background-color: #f1f5f9; color: #475569; border: 1px solid #cbd5e1; border-radius: 5px; font-weight: bold; font-size: 12px; }"
            "QToolButton:hover { background-color: #e2e8f0; }"
            "QToolButton::menu-indicator { image: none; }"
        )
        self.menu_actions = QMenu(self)
        self.menu_actions.setStyleSheet(
            "QMenu { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; padding: 4px; }"
            "QMenu::item { padding: 6px 20px; border-radius: 4px; color: #334155; }"
            "QMenu::item:selected { background-color: #7c3aed; color: white; }"
        )
        self.btn_menu.setMenu(self.menu_actions)
        act_open_dir = self.menu_actions.addAction("📂 Mở thư mục")
        act_open_dir.triggered.connect(self._open_output_folder)
        act_del = self.menu_actions.addAction("🗑 Xóa sản phẩm")
        act_del.triggered.connect(self._delete_card)
        sb_lay.addWidget(self.btn_menu)
        
        # Render Button → Re-render with options dialog
        self.btn_render = QPushButton("🔄 Render lại")
        self.btn_render.setFixedHeight(26)
        self.btn_render.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #16a34a, stop:1 #10b981); "
            "color: white; border: none; border-radius: 5px; font-size: 10px; font-weight: bold; padding: 0 10px; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #15803d, stop:1 #059669); }"
        )
        self.btn_render.clicked.connect(self._on_render_clicked)
        sb_lay.addWidget(self.btn_render)
        
        # Render Video Button (Assemble Video only)
        self.btn_render_video = QPushButton("🎬 Ghép Video")
        self.btn_render_video.setFixedHeight(26)
        self.btn_render_video.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7c3aed, stop:1 #06b6d4); "
            "color: white; border: none; border-radius: 5px; font-size: 10px; font-weight: bold; padding: 0 10px; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #6d28d9, stop:1 #0891b2); }"
        )
        self.btn_render_video.clicked.connect(self._on_render_video_only_clicked)
        sb_lay.addWidget(self.btn_render_video)
        
        self.right_lay.addWidget(self.status_bar)
        
        # Divider or Visual details banner
        self.lbl_video_title = QLabel(product.title)
        self.lbl_video_title.setStyleSheet("font-weight: bold; font-size: 11px; color: #7c3aed;")
        self.right_lay.addWidget(self.lbl_video_title)
        
        # Scenes list scroll area
        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setStyleSheet("QScrollArea { border: 1px solid #cbd5e1; border-radius: 8px; background: #f8fafc; }")
        self.list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.list_widget = QWidget()
        self.list_widget.setStyleSheet("background: #f8fafc;")
        self.list_layout = QVBoxLayout(self.list_widget)
        self.list_layout.setContentsMargins(4, 4, 4, 4)
        self.list_layout.setSpacing(4)
        
        self.list_scroll.setWidget(self.list_widget)
        self.right_lay.addWidget(self.list_scroll, stretch=1)
        
        # ─── VIDEO REVIEW PANEL ───────────────────────────────────────
        self.vr_scene_rows = []
        self.vr_ext_preview = None
        
        # Collapsible header
        self.btn_vr_toggle = QPushButton("▶ 🎬 Video Review")
        self.btn_vr_toggle.setCheckable(True)
        self.btn_vr_toggle.setChecked(True)
        self.btn_vr_toggle.setFixedHeight(30)
        self.btn_vr_toggle.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4c1d95, stop:1 #1e3a5f); "
            "color: #e2e8f0; border: none; border-radius: 6px; font-size: 10px; font-weight: bold; "
            "padding: 0 14px; text-align: left; }"
            "QPushButton:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5b21b6, stop:1 #1e40af); }"
            "QPushButton:checked { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5b21b6, stop:1 #1e40af); }"
        )
        self.btn_vr_toggle.clicked.connect(self._toggle_vr_panel)
        self.right_lay.addWidget(self.btn_vr_toggle)
        
        # VR content container — directly holds VR content (no scroll wrapper needed)
        self.vr_container = QWidget()
        self.vr_container.setStyleSheet(
            "QWidget#vr_panel { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #f5f3ff, stop:1 #ede9fe); "
            "border: 1.5px solid #a78bfa; border-radius: 8px; }"
        )
        self.vr_container.setObjectName("vr_panel")
        self.vr_container_lay = QVBoxLayout(self.vr_container)
        self.vr_container_lay.setContentsMargins(6, 6, 6, 6)
        self.vr_container_lay.setSpacing(4)
        
        # VR inner content area (scenes or ext preview go here)
        self.vr_content_widget = QWidget()
        self.vr_content_widget.setStyleSheet("background: transparent;")
        self.vr_content_layout = QVBoxLayout(self.vr_content_widget)
        self.vr_content_layout.setContentsMargins(0, 0, 0, 0)
        self.vr_content_layout.setSpacing(4)
        self.vr_container_lay.addWidget(self.vr_content_widget)
        
        # VR action buttons row
        vr_btn_row = QWidget()
        vr_btn_row.setStyleSheet("background: transparent;")
        vr_btn_lay = QHBoxLayout(vr_btn_row)
        vr_btn_lay.setContentsMargins(0, 2, 0, 0)
        vr_btn_lay.setSpacing(6)
        vr_btn_lay.addStretch()
        
        self.btn_vr_play_final = QPushButton("▶ Xem Video Final")
        self.btn_vr_play_final.setFixedHeight(26)
        self.btn_vr_play_final.setStyleSheet(
            "QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #7c3aed, stop:1 #06b6d4); "
            "color: white; border: none; border-radius: 5px; font-size: 10px; font-weight: bold; padding: 0 14px; }"
            "QPushButton:hover { background: #6d28d9; }"
        )
        self.btn_vr_play_final.clicked.connect(self._play_vr_final)
        vr_btn_lay.addWidget(self.btn_vr_play_final)
        
        self.btn_vr_open_dir = QPushButton("📂 Mở Folder")
        self.btn_vr_open_dir.setFixedHeight(26)
        self.btn_vr_open_dir.setStyleSheet(
            "QPushButton { background: #334155; color: white; border: none; border-radius: 5px; "
            "font-size: 10px; font-weight: bold; padding: 0 12px; }"
            "QPushButton:hover { background: #475569; }"
        )
        self.btn_vr_open_dir.clicked.connect(self._open_vr_folder)
        vr_btn_lay.addWidget(self.btn_vr_open_dir)
        
        self.vr_container_lay.addWidget(vr_btn_row)
        self.right_lay.addWidget(self.vr_container)
        
        self.main_lay.addWidget(self.right_w, stretch=1)
        
        # Setup event filter and focus policy for Ctrl+V pasting reference images
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.ref_scroll.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.ref_widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.installEventFilter(self)
        self.ref_scroll.installEventFilter(self)
        self.ref_widget.installEventFilter(self)

        self.load_scenes()
        self.load_vr_scenes()
        self.adjust_height()

    def adjust_height(self):
        N = len(self.product.scenes)
        h = 280 + N * 105
        # Add height for VR panel
        vr_visible = self.vr_container.isVisible() if hasattr(self, 'vr_container') else False
        if vr_visible:
            display_mode = self.product.video_review_mode
            if not display_mode:
                if self.main_win and hasattr(self.main_win, "settings"):
                    display_mode = self.main_win.settings.get("vr_mode", "ai_generate")
                else:
                    display_mode = "ai_generate"

            if display_mode == "ai_generate" and len(self.product.video_review_scenes) > 0:
                n_vr = len(self.product.video_review_scenes)
                h += 50 + n_vr * 125  # header + scene rows
            elif display_mode == "external_file":
                h += 50 + 320  # header + ext preview with editing controls (increased to 320 for safety)
            else:
                h += 50 + 50  # header + empty placeholder
        else:
            h += 38  # just the toggle button
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setFixedHeight(max(500, h))

    def load_scenes(self):
        # Update existing rows in-place to prevent UI freeze from widget destruction/creation
        if len(self.scene_rows) == len(self.product.scenes) and len(self.scene_rows) > 0:
            for row, sc in zip(self.scene_rows, self.product.scenes):
                row.scene = sc
                row.update_row_data()
            return

        # Clear old rows
        for r in self.scene_rows:
            r.setParent(None)
            r.deleteLater()
        self.scene_rows.clear()
        
        # Add scene rows
        for idx, sc in enumerate(self.product.scenes):
            row = ThreadsSceneRow(idx, sc, self)
            self.list_layout.addWidget(row)
            self.scene_rows.append(row)
        
        # Empty scenes placeholder
        if not self.product.scenes:
            lbl = QLabel("💡 Chưa có phân cảnh — Bấm [🖼️ Render] hoặc [▶️ Bắt đầu] để AI tự động tạo kịch bản và ảnh")
            lbl.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 20px; font-style: italic;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(lbl)
        else:
            self.list_layout.addStretch()

    def load_vr_scenes(self):
        """Load/refresh Video Review panel nội dung."""
        if not self.product.video_review_scenes:
            # Check for legacy/existing vr_script.json on disk
            import json
            import re
            def sanitize_filename(name):
                return re.sub(r'[\\/*?:"<>|]', "", name).strip()[:100]
            
            prod_folder = None
            if self.product.final_combined_path:
                p = Path(self.product.final_combined_path).parent
                if p.exists() and (p / "vr_script.json").exists():
                    prod_folder = p
            if not prod_folder and self.product.final_video_path:
                p = Path(self.product.final_video_path).parent
                if p.exists() and (p / "vr_script.json").exists():
                    prod_folder = p
            if not prod_folder:
                out_dir_setting = None
                if self.main_win and hasattr(self.main_win, "settings"):
                    out_dir_setting = self.main_win.settings.get("output_dir")
                out_base = Path(out_dir_setting) if out_dir_setting else OUTPUT_DIR
                prod_dir_name = f"{sanitize_filename(self.product.title or 'san_pham')}_{self.product.id}"
                p = out_base / prod_dir_name
                if p.exists() and (p / "vr_script.json").exists():
                    prod_folder = p
            if not prod_folder and OUTPUT_DIR.exists():
                for item in OUTPUT_DIR.iterdir():
                    if item.is_dir() and item.name.endswith(f"_{self.product.id}"):
                        if (item / "vr_script.json").exists():
                            prod_folder = item
                            break
            
            if prod_folder:
                try:
                    script_path = prod_folder / "vr_script.json"
                    script_data = json.loads(script_path.read_text("utf-8"))
                    scenes_list = script_data.get("scenes", [])
                    loaded_scenes = []
                    for i, s_data in enumerate(scenes_list):
                        img_p = prod_folder / "vr_clips" / f"scene_{i+1}.png"
                        vid_p = prod_folder / "vr_clips" / f"clip_{i+1}.mp4"
                        
                        vrs = VideoReviewScene(
                            id=i + 1,
                            scene_title=f"Scene {i + 1}",
                            image_prompt=s_data.get("image_prompt", ""),
                            video_prompt=s_data.get("video_prompt", ""),
                            voiceover_text=s_data.get("voiceover_text", ""),
                            image_path=str(img_p) if img_p.exists() else "",
                            video_clip_path=str(vid_p) if vid_p.exists() else "",
                            status="done" if vid_p.exists() else "pending"
                        )
                        loaded_scenes.append(vrs)
                    if loaded_scenes:
                        self.product.video_review_scenes = loaded_scenes
                        self.product.video_review_mode = "ai_generate"
                        if self.main_win and hasattr(self.main_win, "_save_projects"):
                            self.main_win._save_projects()
                except Exception as e:
                    print(f"Error loading legacy VR scenes: {e}")

        vr_scenes = self.product.video_review_scenes
        vr_mode = self.product.video_review_mode
        
        display_mode = vr_mode
        if not display_mode:
            if self.main_win and hasattr(self.main_win, "settings"):
                display_mode = self.main_win.settings.get("vr_mode", "ai_generate")
            else:
                display_mode = "ai_generate"

        # Update toggle button text
        arrow = "▼" if self.btn_vr_toggle.isChecked() else "▶"
        if display_mode == "ai_generate" and len(vr_scenes) > 0:
            self.btn_vr_toggle.setText(f"{arrow} 🎬 Video Review — {len(vr_scenes)} clips — AI")
        elif display_mode == "external_file" and self.product.external_video_path:
            fname = Path(self.product.external_video_path).name
            self.btn_vr_toggle.setText(f"{arrow} 🎬 Video Review — {fname}")
        else:
            self.btn_vr_toggle.setText(f"{arrow} 🎬 Video Review — Chưa có")
        
        # Toggle visibility
        self.vr_container.setVisible(self.btn_vr_toggle.isChecked())
        
        # Helper: clear vr_content_layout
        def _clear_content():
            while self.vr_content_layout.count():
                it = self.vr_content_layout.takeAt(0)
                if it and it.widget():
                    it.widget().setParent(None)
                    it.widget().deleteLater()
        
        if display_mode == "ai_generate" and len(vr_scenes) > 0:
            # Update in-place if same count
            if len(self.vr_scene_rows) == len(vr_scenes) and len(self.vr_scene_rows) > 0:
                for row, vrs in zip(self.vr_scene_rows, vr_scenes):
                    row.vr_scene = vrs
                    row.update_row_data()
            else:
                for r in self.vr_scene_rows:
                    r.setParent(None)
                    r.deleteLater()
                self.vr_scene_rows.clear()
                if self.vr_ext_preview:
                    self.vr_ext_preview.setParent(None)
                    self.vr_ext_preview.deleteLater()
                    self.vr_ext_preview = None
                _clear_content()
                
                for idx, vrs in enumerate(vr_scenes):
                    row = VRSceneRow(idx, vrs, self)
                    self.vr_content_layout.addWidget(row)
                    self.vr_scene_rows.append(row)
        elif display_mode == "external_file":
            # External video preview with editing controls
            for r in self.vr_scene_rows:
                r.setParent(None)
                r.deleteLater()
            self.vr_scene_rows.clear()
            
            if not self.vr_ext_preview:
                _clear_content()
                self.vr_ext_preview = VRExternalPreview(self.product, self)
                self.vr_content_layout.addWidget(self.vr_ext_preview)
            else:
                self.vr_ext_preview.product = self.product
                self.vr_ext_preview._refresh()
        else:
            # Empty placeholder
            for r in self.vr_scene_rows:
                r.setParent(None)
                r.deleteLater()
            self.vr_scene_rows.clear()
            if self.vr_ext_preview:
                self.vr_ext_preview.setParent(None)
                self.vr_ext_preview.deleteLater()
                self.vr_ext_preview = None
            _clear_content()
            
            lbl_empty = QLabel("💡 Chưa có Video Review — Bật tính năng trong Settings → Video Review Sản Phẩm")
            lbl_empty.setStyleSheet("color: #7c3aed; font-size: 10px; padding: 12px; font-style: italic;")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vr_content_layout.addWidget(lbl_empty)
        
        # Update final video button state
        has_final = bool(self.product.final_combined_path and Path(self.product.final_combined_path).exists())
        self.btn_vr_play_final.setEnabled(has_final)
        self.adjust_height()

    def _toggle_vr_panel(self):
        """Toggle visibility of VR content container."""
        is_open = self.btn_vr_toggle.isChecked()
        self.vr_container.setVisible(is_open)
        # Update arrow
        current_text = self.btn_vr_toggle.text()
        if is_open:
            self.btn_vr_toggle.setText(current_text.replace("▶", "▼", 1))
        else:
            self.btn_vr_toggle.setText(current_text.replace("▼", "▶", 1))
        self.adjust_height()

    def _play_vr_final(self):
        """Play the final combined video."""
        for p in [self.product.final_combined_path, self.product.final_video_path]:
            if p and Path(p).exists():
                try: os.startfile(p)
                except Exception: pass
                return

    def _open_vr_folder(self):
        """Open the output folder."""
        for p in [self.product.final_combined_path, self.product.final_video_path]:
            if p and Path(p).exists():
                try: os.startfile(Path(p).parent)
                except Exception: pass
                return

    def refresh_thumb(self):
        img_path = self.product.images[0] if self.product.images else ""
        if img_path and Path(img_path).exists():
            pix = QPixmap(img_path)
            self.lbl_thumb.setPixmap(pix.scaled(76, 76, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self.lbl_thumb.setStyleSheet("background: white; border: 1px solid #cbd5e1; border-radius: 8px;")
        else:
            self.lbl_thumb.setText("📷")
            self.lbl_thumb.setStyleSheet("background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 24px; color: #94a3b8;")

    def refresh_ref_images(self):
        # Clear layout
        while self.ref_lay.count() > 1:
            it = self.ref_lay.takeAt(0)
            if it and it.widget():
                it.widget().setParent(None)
                it.widget().deleteLater()
                
        # Load ref images (all index >= 1)
        for idx, path in enumerate(self.product.images[1:]):
            widget = ThreadsRefImageWidget(idx, path, self)
            self.ref_lay.insertWidget(idx, widget)

    def _on_browse_ref_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn ảnh tham chiếu", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if files:
            self.product.images.extend(files)
            win = self.window()
            if hasattr(win, "_save_projects"): win._save_projects()
            self.refresh_ref_images()

    def _on_use_ref_gen_toggled(self, checked):
        self.product.use_ref_for_generation = checked
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def eventFilter(self, obj, event):
        # Intercept Ctrl+V key press
        if event.type() == event.Type.KeyPress:
            if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
                # Get the focused widget in the application
                focused = QApplication.focusWidget()
                # Check if the focused widget is this card or a child of this card
                if focused and (self.isAncestorOf(focused) or focused == self):
                    # But do NOT trigger paste when we are currently editing a text edit or line edit
                    if not isinstance(focused, (QTextEdit, QLineEdit)):
                        self._paste_ref_image()
                        return True
        return super().eventFilter(obj, event)

    def _paste_ref_image(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if mime_data.hasImage():
            image = clipboard.image()
            if not image.isNull():
                OUTPUT_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
                pasted_filename = f"pasted_ref_{self.product.id}_{uuid.uuid4().hex[:6]}.png"
                target_path = OUTPUT_IMAGES_DIR / pasted_filename
                if image.save(str(target_path), "PNG"):
                    self.product.images.append(str(target_path))
                    win = self.window()
                    if hasattr(win, "_save_projects"): win._save_projects()
                    self.refresh_ref_images()
                    if hasattr(win, "_log"):
                        win._log(f"📋 Đã dán ảnh tham chiếu từ clipboard: {pasted_filename}")

    def update_context_preview(self):
        desc = self.context_combo.currentData() or "Mô tả phong cách kịch bản..."
        self.context_preview.setPlainText(desc)

    def _on_context_changed(self, idx):
        self.product.context = self.context_combo.currentText()
        self.update_context_preview()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _on_name_changed(self, text):
        self.product.title = self.edit_name.text().strip()
        self.lbl_video_title.setText(self.product.title)
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _on_req_changed(self):
        self.product.info = self.edit_req.toPlainText().strip()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _on_double_click_product_image(self, event):
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn ảnh sản phẩm", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if files and files[0]:
            if not self.product.images:
                self.product.images.append(files[0])
            else:
                self.product.images[0] = files[0]
            win = self.window()
            if hasattr(win, "_save_projects"): win._save_projects()
            self.refresh_thumb()

    def update_status_ui(self):
        st = self.product.status
        self.lbl_status.setText(st)
        if "✅" in st:
            self.lbl_status.setStyleSheet("background: #dcfce7; color: #16a34a; font-size: 9px; border-radius: 6px; padding: 2px 6px; font-weight: bold;")
        elif "⏳" in st:
            self.lbl_status.setStyleSheet("background: #dbeafe; color: #2563eb; font-size: 9px; border-radius: 6px; padding: 2px 6px; font-weight: bold;")
        elif "❌" in st:
            self.lbl_status.setStyleSheet("background: #fee2e2; color: #dc2626; font-size: 9px; border-radius: 6px; padding: 2px 6px; font-weight: bold;")
        else:
            self.lbl_status.setStyleSheet("background: #f1f5f9; color: #64748b; font-size: 9px; border-radius: 6px; padding: 2px 6px; font-weight: bold;")

    def _open_output_folder(self):
        if self.product.final_video_path and Path(self.product.final_video_path).parent.exists():
            try: os.startfile(Path(self.product.final_video_path).parent)
            except Exception: pass

    def _delete_card(self):
        win = self.window()
        if hasattr(win, "_delete_product_card"):
            win._delete_product_card(self)

    def _on_render_clicked(self):
        win = self.window()
        dlg = ReRenderDialog(self.product, win)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_options()
            if hasattr(win, "_run_single_selective"):
                win._run_single_selective(self.product, opts)

    def _on_render_video_only_clicked(self):
        win = self.window()
        if hasattr(win, "_run_single_video_only"):
            win._run_single_video_only(self.product)


class ReRenderDialog(QDialog):
    """Dialog cho phép chọn từng phần cần render lại: Kịch bản, Voice, Ảnh."""
    def __init__(self, product: ProductItem, main_win: QMainWindow):
        super().__init__(main_win)
        self.product = product
        self.main_win = main_win
        self.setWindowTitle(f"🔄 Render Lại — {product.title[:40]}")
        self.setMinimumWidth(560)
        self.setStyleSheet("""
            QDialog { background-color: #1a1a2e; }
            QLabel { color: #c8d6e5; background: transparent; font-size: 12px; }
            QGroupBox {
                background-color: #16213e;
                border: 1px solid #334155;
                border-radius: 10px;
                padding: 16px 12px 12px 12px;
                margin-top: 14px;
                font-size: 13px;
                font-weight: bold;
                color: #e2e8f0;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 14px;
                padding: 2px 10px;
                background-color: #1a1a2e;
                border-radius: 4px;
                color: #a78bfa;
            }
            QCheckBox {
                color: #f1f5f9;
                font-size: 13px;
                font-weight: bold;
                spacing: 8px;
                padding: 4px 0;
            }
            QCheckBox::indicator {
                width: 20px; height: 20px;
                border-radius: 4px;
                border: 2px solid #6366f1;
                background: #0f172a;
            }
            QCheckBox::indicator:checked {
                background: #6366f1;
                border-color: #818cf8;
                image: none;
            }
            QCheckBox::indicator:hover {
                border-color: #818cf8;
            }
            QComboBox {
                background: #0f172a;
                color: #e2e8f0;
                border: 1px solid #4c566a;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
                min-height: 22px;
            }
            QComboBox:hover { border-color: #6366f1; }
            QComboBox::drop-down { border: none; width: 24px; }
            QComboBox QAbstractItemView {
                background: #1e293b; color: #e2e8f0;
                selection-background-color: #6366f1;
                border: 1px solid #475569;
            }
            QPushButton {
                background-color: #334155;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 9px 20px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #475569; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        # Header
        lbl = QLabel(f"<span style='font-size:14px;font-weight:bold;color:#e2e8f0;'>Chọn các phần cần render lại</span><br>"
                     f"<span style='color:#a78bfa;font-size:12px;'>📦 {product.title}</span>")
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        has_scenes = bool(product.scenes)
        has_voice = bool(product.voice_path and Path(product.voice_path).exists())
        has_images = has_scenes and any(s.image_path and Path(s.image_path).exists() for s in product.scenes)
        
        # Detect if product uses sub-voices (hybrid/verbatim with voice_role)
        has_sub_voices = has_scenes and any(s.voice_role == "comment" for s in product.scenes)
        settings = getattr(main_win, 'settings', {})

        # ── 1. Kịch bản ──
        self.grp_script = QGroupBox("📝  Kịch bản (LLM)")
        grp_s_lay = QVBoxLayout(self.grp_script)
        grp_s_lay.setSpacing(6)
        self.chk_script = QCheckBox("Tạo lại kịch bản từ LLM")
        self.chk_script.setChecked(not has_scenes)
        grp_s_lay.addWidget(self.chk_script)
        status_text = f"✅ Đã có {len(product.scenes)} phân cảnh" if has_scenes else "⚠️ Chưa có kịch bản"
        status_color = "#22c55e" if has_scenes else "#f59e0b"
        info_s = QLabel(f"<span style='color:{status_color};font-size:11px;'>{status_text}</span>")
        grp_s_lay.addWidget(info_s)
        
        # LLM Model selector
        h_llm = QHBoxLayout()
        lbl_llm = QLabel("LLM Model:")
        lbl_llm.setStyleSheet("color:#8b9dc3;font-size:11px;")
        h_llm.addWidget(lbl_llm)
        self.cb_re_llm = QComboBox()
        if hasattr(main_win, 'cb_llm'):
            for i in range(main_win.cb_llm.count()):
                self.cb_re_llm.addItem(main_win.cb_llm.itemText(i))
            self.cb_re_llm.setCurrentText(main_win.cb_llm.currentText())
        h_llm.addWidget(self.cb_re_llm)
        grp_s_lay.addLayout(h_llm)
        
        self.chk_script.toggled.connect(lambda checked: self.cb_re_llm.setEnabled(checked))
        self.cb_re_llm.setEnabled(self.chk_script.isChecked())
        layout.addWidget(self.grp_script)

        # ── 2. Voice ──
        self.grp_voice = QGroupBox("🎙️  Voice")
        grp_v_lay = QVBoxLayout(self.grp_voice)
        grp_v_lay.setSpacing(6)
        self.chk_voice = QCheckBox("Tạo lại Voice")
        self.chk_voice.setChecked(not has_voice)
        grp_v_lay.addWidget(self.chk_voice)
        v_status = f"✅ Voice sẵn ({round(product.voice_duration, 1)}s)" if has_voice else "⚠️ Chưa có voice"
        v_color = "#22c55e" if has_voice else "#f59e0b"
        info_v = QLabel(f"<span style='color:{v_color};font-size:11px;'>{v_status}</span>")
        grp_v_lay.addWidget(info_v)

        # Voice config grid
        grid_v = QGridLayout()
        grid_v.setHorizontalSpacing(10)
        grid_v.setVerticalSpacing(6)
        
        lbl_eng = QLabel("Engine:")
        lbl_eng.setStyleSheet("color:#8b9dc3;font-size:11px;")
        grid_v.addWidget(lbl_eng, 0, 0)
        self.cb_re_engine = QComboBox()
        self.cb_re_engine.addItems(["Vbee API", "gTTS"])
        self.cb_re_engine.setCurrentIndex(0 if settings.get("voice_engine") == "vbee" else 1)
        grid_v.addWidget(self.cb_re_engine, 0, 1)
        
        lbl_vc = QLabel("Voice chính:")
        lbl_vc.setStyleSheet("color:#8b9dc3;font-size:11px;")
        grid_v.addWidget(lbl_vc, 0, 2)
        self.cb_re_main_voice = QComboBox()
        self.cb_re_main_voice.setEditable(True)
        cached_voices = settings.get("vbee_voices_cached", [])
        if cached_voices:
            for vc, lb in cached_voices:
                self.cb_re_main_voice.addItem(lb, vc)
            saved_v = settings.get("vbee_voice", "")
            if saved_v:
                idx = self.cb_re_main_voice.findData(saved_v)
                if idx >= 0: self.cb_re_main_voice.setCurrentIndex(idx)
        else:
            self.cb_re_main_voice.addItem("vi_south_female_miien_48k")
        grid_v.addWidget(self.cb_re_main_voice, 0, 3)
        
        lbl_vm = QLabel("Voice Mode:")
        lbl_vm.setStyleSheet("color:#8b9dc3;font-size:11px;")
        grid_v.addWidget(lbl_vm, 1, 0)
        self.cb_re_voice_mode = QComboBox()
        self.cb_re_voice_mode.addItems([
            "Review dẫn chuyện (narrative_review)",
            "Đọc nguyên văn (verbatim_read)",
            "Review kèm Đọc CMT (hybrid_narrate_read)"
        ])
        saved_vm = settings.get("voice_mode_display", "")
        if saved_vm:
            self.cb_re_voice_mode.setCurrentText(saved_vm)
        grid_v.addWidget(self.cb_re_voice_mode, 1, 1, 1, 3)
        grp_v_lay.addLayout(grid_v)
        
        # Sub-voice list (Voice Phụ)
        sub_voice_label = QLabel("👥 Voice Phụ (Đọc CMT, Random):")
        sub_voice_label.setStyleSheet("color:#8b9dc3;font-size:11px;margin-top:4px;")
        grp_v_lay.addWidget(sub_voice_label)
        
        if has_sub_voices:
            sub_note = QLabel("<span style='color:#22c55e;font-size:10px;'>🔍 Phát hiện dự án sử dụng multi-voice (hybrid/verbatim)</span>")
        else:
            sub_note = QLabel("<span style='color:#94a3b8;font-size:10px;'>ℹ️ Chỉ dùng cho mode Đọc nguyên văn / Review kèm Đọc CMT</span>")
        grp_v_lay.addWidget(sub_note)
        
        self.list_re_sub_voices = QListWidget()
        self.list_re_sub_voices.setMinimumHeight(100)
        self.list_re_sub_voices.setMaximumHeight(140)
        self.list_re_sub_voices.setStyleSheet(
            "QListWidget{background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:6px;font-size:11px;}"
            "QListWidget::item{padding:4px 4px;border-bottom:1px solid #1e293b;}"
            "QListWidget::item:hover{background:#1e293b;}"
            "QListWidget::indicator{width:16px;height:16px;}"
        )
        # Populate from cached voices, restore saved sub-voice selections
        saved_sub = settings.get("vbee_sub_voices_display", [])
        if cached_voices:
            for vc, lb in cached_voices:
                item = QListWidgetItem(lb)
                item.setData(Qt.ItemDataRole.UserRole, vc)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if vc in saved_sub else Qt.CheckState.Unchecked)
                self.list_re_sub_voices.addItem(item)
        grp_v_lay.addWidget(self.list_re_sub_voices)
        
        # Quick select buttons for sub-voices
        sub_btn_lay = QHBoxLayout()
        btn_sv_all = QPushButton("✅ Tất cả")
        btn_sv_all.setStyleSheet("QPushButton{background:#16213e;color:#a78bfa;font-size:10px;padding:3px 8px;border:1px solid #334155;border-radius:4px;}QPushButton:hover{background:#334155;}")
        btn_sv_all.clicked.connect(lambda: self._toggle_re_sub_voices(True))
        sub_btn_lay.addWidget(btn_sv_all)
        btn_sv_none = QPushButton("❌ Bỏ chọn")
        btn_sv_none.setStyleSheet("QPushButton{background:#16213e;color:#94a3b8;font-size:10px;padding:3px 8px;border:1px solid #334155;border-radius:4px;}QPushButton:hover{background:#334155;}")
        btn_sv_none.clicked.connect(lambda: self._toggle_re_sub_voices(False))
        sub_btn_lay.addWidget(btn_sv_none)
        sub_btn_lay.addStretch()
        grp_v_lay.addLayout(sub_btn_lay)

        # Enable/disable all voice options
        voice_widgets = [self.cb_re_engine, self.cb_re_main_voice, self.cb_re_voice_mode, self.list_re_sub_voices, btn_sv_all, btn_sv_none]
        self.chk_voice.toggled.connect(lambda checked: [w.setEnabled(checked) for w in voice_widgets])
        for w in voice_widgets:
            w.setEnabled(self.chk_voice.isChecked())
        layout.addWidget(self.grp_voice)

        # ── 3. Ảnh ──
        self.grp_images = QGroupBox("🖼️  Ảnh")
        grp_i_lay = QVBoxLayout(self.grp_images)
        grp_i_lay.setSpacing(6)
        self.chk_images = QCheckBox("Render lại tất cả ảnh")
        self.chk_images.setChecked(not has_images)
        grp_i_lay.addWidget(self.chk_images)
        img_count = sum(1 for s in product.scenes if s.image_path and Path(s.image_path).exists()) if has_scenes else 0
        i_status = f"✅ {img_count}/{len(product.scenes)} ảnh" if has_images else "⚠️ Chưa có ảnh"
        i_color = "#22c55e" if has_images else "#f59e0b"
        info_i = QLabel(f"<span style='color:{i_color};font-size:11px;'>{i_status}</span>")
        grp_i_lay.addWidget(info_i)

        h_img = QHBoxLayout()
        lbl_im = QLabel("Image Model:")
        lbl_im.setStyleSheet("color:#8b9dc3;font-size:11px;")
        h_img.addWidget(lbl_im)
        self.cb_re_img_model = QComboBox()
        if hasattr(main_win, 'cb_img'):
            for i in range(main_win.cb_img.count()):
                self.cb_re_img_model.addItem(main_win.cb_img.itemText(i))
            self.cb_re_img_model.setCurrentText(main_win.cb_img.currentText())
        h_img.addWidget(self.cb_re_img_model)
        grp_i_lay.addLayout(h_img)

        self.chk_images.toggled.connect(lambda checked: self.cb_re_img_model.setEnabled(checked))
        self.cb_re_img_model.setEnabled(self.chk_images.isChecked())
        layout.addWidget(self.grp_images)

        # ── 4. Video Review ──
        self.grp_vr = QGroupBox("📹  Video Review")
        grp_vr_lay = QVBoxLayout(self.grp_vr)
        grp_vr_lay.setSpacing(6)
        
        has_vr = bool(product.final_combined_path and Path(product.final_combined_path).exists())
        self.chk_vr = QCheckBox("Tạo lại Video Review (Video AI / Video ngoài)")
        self.chk_vr.setChecked(not has_vr and settings.get("vr_enabled", False))
        grp_vr_lay.addWidget(self.chk_vr)
        
        vr_status = f"✅ Video Review sẵn" if has_vr else "⚠️ Chưa có Video Review"
        vr_color = "#22c55e" if has_vr else "#f59e0b"
        info_vr = QLabel(f"<span style='color:{vr_color};font-size:11px;'>{vr_status}</span>")
        grp_vr_lay.addWidget(info_vr)
        
        layout.addWidget(self.grp_vr)

        layout.addSpacing(8)

        # ── Buttons ──
        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        btn_cancel = QPushButton("Hủy")
        btn_cancel.setStyleSheet("QPushButton{background:#374151;color:#d1d5db;border:1px solid #4b5563;border-radius:8px;padding:9px 22px;}QPushButton:hover{background:#4b5563;}")
        btn_cancel.clicked.connect(self.reject)
        btn_lay.addWidget(btn_cancel)
        btn_ok = QPushButton("🚀 Render Lại")
        btn_ok.setStyleSheet("QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #6366f1,stop:1 #8b5cf6);color:white;padding:10px 28px;font-size:13px;border-radius:8px;font-weight:bold;}QPushButton:hover{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4f46e5,stop:1 #7c3aed);}")
        btn_ok.clicked.connect(self.accept)
        btn_lay.addWidget(btn_ok)
        layout.addLayout(btn_lay)

    def _toggle_re_sub_voices(self, select: bool):
        for i in range(self.list_re_sub_voices.count()):
            self.list_re_sub_voices.item(i).setCheckState(Qt.CheckState.Checked if select else Qt.CheckState.Unchecked)

    def _get_re_sub_voices(self) -> list:
        result = []
        for i in range(self.list_re_sub_voices.count()):
            item = self.list_re_sub_voices.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def get_options(self) -> dict:
        """Return dict of selected re-render options."""
        return {
            "re_script": self.chk_script.isChecked(),
            "re_voice": self.chk_voice.isChecked(),
            "re_images": self.chk_images.isChecked(),
            "re_video_review": self.chk_vr.isChecked(),
            "re_llm_model": self.cb_re_llm.currentText() if hasattr(self, 'cb_re_llm') else "",
            "re_voice_engine": "vbee" if self.cb_re_engine.currentIndex() == 0 else "gtts",
            "re_main_voice": self.cb_re_main_voice.currentData() or self.cb_re_main_voice.currentText(),
            "re_sub_voices": self._get_re_sub_voices(),
            "re_voice_mode": self.cb_re_voice_mode.currentText(),
            "re_img_model": self.cb_re_img_model.currentText() if hasattr(self, 'cb_re_img_model') else "",
        }

DEFAULT_ARCHETYPES = [
    ("Tự chọn phong cách tối ưu nhất", "Để LLM tự phân tích sản phẩm và tự chọn phong cách phù hợp nhất."),
    ("Hook hài hước dẫn dắt", "Mở đầu bằng một bài đăng mang tính than thở hoặc cảm xúc cá nhân hài hước, không liên quan trực tiếp sản phẩm. Chuỗi bình luận troll nhẹ/đồng cảm dẫn dắt đến giải pháp là sản phẩm ở bình luận cuối."),
    ("Top Pick / Danh sách mẹo", "Bài đăng dạng liệt kê (Top 3 mẹo, 3 món phải có...). Mỗi mục có ảnh minh họa riêng, gài sản phẩm vào vị trí nổi bật hoặc bổ sung ở bình luận cuối."),
    ("Debate Thread / Tranh luận", "Mở đầu bằng tuyên bố gây tranh cãi nhẹ hoặc phản trực giác. Nhiều lớp bình luận phản đối-đồng tình tạo không khí thảo luận, tác giả chốt bằng bình luận giải thích lồng sản phẩm."),
    ("Before/After trực quan", "Tập trung vào ảnh so sánh trước-sau đặt cạnh nhau kèm thanh chữ chú thích đen. Voiceover ngắn gọn, trực quan, phù hợp sản phẩm làm đẹp, chăm sóc da."),
    ("Meme + Reaction Overlay", "Dùng ảnh minh họa/chế ảnh đơn giản kèm khung phản ứng (reaction sticker) góc dưới. Bình luận ngắn xếp chồng đông vui, bình luận cuối nhắc sản phẩm."),
    ("Bình luận chồng nhanh", "Không có bài đăng dài, bắt đầu ngay bằng câu hỏi/than ngắn, sau đó các bình luận ngắn liên tiếp xếp chồng, nhịp đọc cực nhanh phù hợp video ngắn."),
    ("Own-Post Confession / Tự thuật", "Tác giả tự đăng bài và tự trả lời bình luận của chính mình, tạo cảm giác chia sẻ trải nghiệm cá nhân chân thực, đáng tin."),
    ("Q&A Thread", "Mở đầu bằng câu hỏi ngắn kiểu 'Có ai biết...'. Các bình luận trả lời lồng nhau, tác giả phản hồi và bình luận tương tác cao nhất tiết lộ sản phẩm."),
    ("Confession ẩn danh", "Tài khoản ẩn danh đăng thú nhận riêng tư/xấu hổ về vấn đề sản phẩm giải quyết. Bình luận đồng cảm và gợi ý sản phẩm khéo léo ở cuối."),
    ("Storytime nhiều phần", "Chuỗi bài đăng cùng tác giả đánh số phần (1/4, 2/4...), kể câu chuyện sâu sắc có mở đầu, cao trào và kết thúc giới thiệu sản phẩm."),
    ("Săn sale / Đếm ngược khan hàng", "Mở đầu gấp gáp thông báo giảm giá/sắp hết hàng. Bình luận xác nhận đã mua hoặc tiếc nuối tạo hiệu ứng FOMO mua hàng ngay."),
    ("Bình chọn / Khảo sát vui", "Mở đầu bằng câu hỏi bình chọn vui nhộn. Các bình luận tranh luận đa dạng ý kiến, bình luận nổi bật cuối giới thiệu giải pháp sản phẩm."),
    ("Cảnh báo / PSA cộng đồng", "Tông giọng nghiêm túc cảnh báo vấn đề phổ biến, hướng dẫn người xem đến giải pháp an toàn là sản phẩm."),
    ("Thử thách/Challenge công khai", "Thách đố công khai kiểm chứng sản phẩm. Bình luận của những người đã thử xác nhận kết quả bất ngờ tạo hiệu ứng lan truyền."),
    ("Hook hỏi ý kiến / Tự trào", "Tác giả tự đăng ảnh kết quả vẽ/trang điểm/làm tay chưa hoàn hảo kèm câu hỏi tự trào hài hước. Các bình luận cợt nhả dễ thương và giới thiệu sản phẩm.")
]

def get_supported_aspect_ratios(model_name: str) -> list:
    name_lower = model_name.lower()
    if "banana" in name_lower:
        return ["16:9", "9:16", "1:1", "4:3", "3:4", "4:5"]
    elif "dall-e" in name_lower or "gpt-image" in name_lower:
        return ["1:1", "16:9", "9:16"]
    elif "grok" in name_lower or "flux" in name_lower:
        return ["1:1", "16:9", "9:16", "4:3", "3:4", "4:5"]
    else:
        return ["1:1", "16:9", "9:16", "4:3", "3:4", "4:5"]


# ══════════════════════════════════════════════════════════════════════
#  MAIN WINDOW — Kiến trúc chuẩn Auto Threads: Sidebar 220px + Stacked
# ══════════════════════════════════════════════════════════════════════
class ThreadsStudioWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎬 Auto Threads - ReviewThreads Studio")
        self.resize(1420, 850)
        self.setMinimumSize(1150, 750)

        self.acc_mgr = AccountManager()
        self.projects: List[ThreadsProject] = []
        self.active_project: Optional[ThreadsProject] = None
        self.used_bg: set = set()
        self.settings: dict = self._load_settings()
        self.role_skills_map: Dict[str, Path] = get_available_role_skills()
        self.proxy_process = None
        self.sb_buttons: Dict[str, QPushButton] = {}

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_projects_now)

        self._load_projects()
        self.load_veo_config()
        self._apply_qss()
        self._init_ui()
        self.start_captcha_bridge_if_needed()
        self.start_proxy()

    # ── QSS THEME (100% CLONE TỪ grok_video_tool.py GỐC) ────────────
    def _apply_qss(self):
        QApplication.instance().setStyleSheet("""
            QMainWindow { background-color: #ffffff; }
            QWidget { font-family: "Segoe UI", sans-serif; color: #1e293b; }
            QFrame#sidebar { background-color: #f8fafc; border-right: 1px solid #e2e8f0; }
            QFrame#card { background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; }
            QFrame#section_card { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 8px; }
            QLineEdit, QTextEdit { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px; color: #0f172a; }
            QComboBox { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px; padding-right: 24px; color: #0f172a; }
            QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 20px; border-left-width: 0px; border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
            QComboBox::down-arrow { image: none; border-top: 5px solid #64748b; border-left: 4px solid transparent; border-right: 4px solid transparent; width: 0; height: 0; margin-right: 8px; }
            QComboBox QAbstractItemView, QComboBox QListView { background-color: #ffffff; color: #0f172a; selection-background-color: #7c3aed; selection-color: #ffffff; border: 1px solid #cbd5e1; outline: 0px; }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus { border: 1px solid #7c3aed; }
            QPushButton#accent_btn { background-color: #7c3aed; color: white; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; }
            QPushButton#accent_btn:hover { background-color: #6d28d9; }
            QPushButton#normal_btn { background-color: #ffffff; color: #334155; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 16px; }
            QPushButton#normal_btn:hover { background-color: #f1f5f9; }
            QPushButton#sidebar_btn { text-align: left; background-color: transparent; border: none; border-radius: 6px; padding: 10px 16px; font-weight: 500; color: #475569; }
            QPushButton#sidebar_btn:hover { background-color: #f1f5f9; }
            QPushButton#sidebar_btn[active="true"] { background-color: #7c3aed; color: white; font-weight: bold; }
            QTableWidget { background-color: #ffffff; border: 1px solid #e2e8f0; gridline-color: #e2e8f0; }
            QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; text-align: center; background-color: #f1f5f9; }
            QProgressBar::chunk { background-color: #7c3aed; border-radius: 3px; }
            QScrollBar:vertical { border: none; background: #f8fafc; width: 8px; }
            QScrollBar::handle:vertical { background: #cbd5e1; border-radius: 4px; }
            QCheckBox { color: #1e293b; font-weight: 500; }
            QSpinBox, QDoubleSpinBox { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 4px; color: #0f172a; }
            QGroupBox { font-weight: bold; color: #1e293b; border: 1px solid #e2e8f0; border-radius: 8px; margin-top: 10px; background-color: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; color: #4c1d95; }
        """)

    # ── PROXY ─────────────────────────────────────────────────────────
    def start_proxy(self):
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
                s.settimeout(0.8); s.connect(("127.0.0.1",8317)); return
        except: pass
        if CLIPROXY_EXE.exists():
            try: self.proxy_process=subprocess.Popen([str(CLIPROXY_EXE)],cwd=str(CLIPROXY_DIR),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0); time.sleep(2)
            except: pass

    # ── INIT UI ───────────────────────────────────────────────────────
    def _init_ui(self):
        main_w = QWidget(self); self.setCentralWidget(main_w)
        h_lay = QHBoxLayout(main_w); h_lay.setContentsMargins(0,0,0,0); h_lay.setSpacing(0)

        # ┌─────────────────────────────────────────────────────────────┐
        # │  SIDEBAR 220px — CỐ ĐỊNH, LUÔN HIỂN THỊ                   │
        # └─────────────────────────────────────────────────────────────┘
        self.sidebar_frame = QFrame(); self.sidebar_frame.setObjectName("sidebar"); self.sidebar_frame.setFixedWidth(220)
        sb_scroll = QScrollArea(self.sidebar_frame); sb_scroll.setWidgetResizable(True); sb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        sb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sb_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")

        sb_inner = QWidget(); sb_lay = QVBoxLayout(sb_inner); sb_lay.setContentsMargins(10,16,10,16); sb_lay.setSpacing(1)

        # Logo
        logo_w = QWidget(); logo_lay = QVBoxLayout(logo_w); logo_lay.setContentsMargins(6,0,6,12); logo_lay.setSpacing(3)
        t_l = QLabel("🎬 Auto Threads"); t_l.setStyleSheet("font-size:17px;font-weight:bold;color:#7c3aed;"); logo_lay.addWidget(t_l)
        s_l = QLabel("ReviewThreads Studio · Tự Động Hoá"); s_l.setStyleSheet("font-size:10px;color:#475569;font-weight:600;"); logo_lay.addWidget(s_l)
        sb_lay.addWidget(logo_w)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setStyleSheet("color:#e2e8f0;margin:0 0 4px 0;"); sb_lay.addWidget(sep)

        # Dashboard
        self._sb_add_btn(sb_lay, "🏠  Dashboard", "dashboard")

        # Dự án
        self._sb_section(sb_lay, "DỰ ÁN")
        btn_new = QPushButton("+ Tạo Dự Án Mới"); btn_new.setObjectName("sidebar_btn")
        btn_new.setStyleSheet("QPushButton{color:#7c3aed;font-weight:bold;border:1px dashed #c4b5fd;border-radius:6px;margin:2px 0;}QPushButton:hover{background:#f5f3ff;}")
        btn_new.clicked.connect(self._create_project_dialog); sb_lay.addWidget(btn_new)

        # Project nav (project list + Studio button)
        self.proj_nav_w = QWidget(); pnav_lay = QVBoxLayout(self.proj_nav_w); pnav_lay.setContentsMargins(0,4,0,0); pnav_lay.setSpacing(1)
        self.sb_proj_lbl = QLabel("— Chưa chọn dự án —")
        self.sb_proj_lbl.setStyleSheet("font-size:10px;color:#7c3aed;font-weight:bold;padding:4px 12px;background:#f5f3ff;border-radius:4px;")
        self.sb_proj_lbl.setWordWrap(True); pnav_lay.addWidget(self.sb_proj_lbl)

        btn_studio = QPushButton("🎬  Studio"); btn_studio.setObjectName("sidebar_btn")
        btn_studio.setStyleSheet("QPushButton{font-size:12px;padding:8px 12px;}"); btn_studio.clicked.connect(lambda: self.switch_panel("studio"))
        pnav_lay.addWidget(btn_studio); self.sb_buttons["studio"] = btn_studio

        self.proj_nav_w.setVisible(False); sb_lay.addWidget(self.proj_nav_w)

        # Sidebar project list
        self.sb_proj_list = QWidget(); spl_lay = QVBoxLayout(self.sb_proj_list); spl_lay.setContentsMargins(0,2,0,0); spl_lay.setSpacing(1)
        sb_lay.addWidget(self.sb_proj_list)

        # Quản lý
        self._sb_section(sb_lay, "QUẢN LÝ")
        for txt, k in [("🛍️  TikTok Shop Spy", "tiktokshop"), ("👥  Quản Lý Tài Khoản", "accs"), ("⚙️  Cài Đặt", "sett")]:
            self._sb_add_btn(sb_lay, txt, k)

        sb_lay.addStretch()
        self.sb_acc_lbl = QLabel(f"👥  {len(self.acc_mgr.accounts)} tài khoản hoạt động")
        self.sb_acc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sb_acc_lbl.setStyleSheet("font-size:10px;font-weight:bold;padding:4px;color:#16a34a;")
        sb_lay.addWidget(self.sb_acc_lbl)

        sb_scroll.setWidget(sb_inner)
        sb_frame_lay = QVBoxLayout(self.sidebar_frame); sb_frame_lay.setContentsMargins(0,0,0,0); sb_frame_lay.addWidget(sb_scroll)
        h_lay.addWidget(self.sidebar_frame)

        # ┌─────────────────────────────────────────────────────────────┐
        # │  STACKED WIDGET — NỘI DUNG CHÍNH                          │
        # └─────────────────────────────────────────────────────────────┘
        self.stacked = QStackedWidget(); h_lay.addWidget(self.stacked, stretch=1)

        self._build_dashboard_panel()  # 0
        self._build_studio_panel()     # 1
        self._build_tiktok_panel()     # 2
        self._build_accs_panel()       # 3
        self._build_settings_panel()   # 4

        self._refresh_sidebar_projects()
        self.switch_panel("dashboard")

    # ── SIDEBAR HELPERS ───────────────────────────────────────────────
    def _sb_section(self, lay, text):
        l = QLabel(f"  {text}"); l.setStyleSheet("font-size:9px;font-weight:bold;color:#94a3b8;margin-top:10px;margin-bottom:2px;letter-spacing:0.5px;"); lay.addWidget(l)

    def _sb_add_btn(self, lay, text, key):
        btn = QPushButton(text); btn.setObjectName("sidebar_btn"); btn.setProperty("active","false")
        btn.clicked.connect(lambda _,k=key: self.switch_panel(k)); lay.addWidget(btn); self.sb_buttons[key] = btn



    def _refresh_sidebar_projects(self):
        lay = self.sb_proj_list.layout()
        while lay.count():
            it = lay.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        for p in self.projects:
            btn = QPushButton(f"📁  {p.name}"); btn.setObjectName("sidebar_btn"); btn.setProperty("active","false")
            btn.setStyleSheet("QPushButton{font-size:11px;padding:6px 14px;}")
            btn.clicked.connect(lambda _,pr=p: self._open_project(pr)); lay.addWidget(btn)

    def switch_panel(self, key):
        for k, b in self.sb_buttons.items():
            b.setProperty("active", "true" if k == key else "false")
            b.style().unpolish(b)
            b.style().polish(b)
        
        # Hide sidebar when inside project Studio, show it for other pages
        if key == "studio":
            if hasattr(self, 'sidebar_frame'):
                self.sidebar_frame.hide()
        else:
            if hasattr(self, 'sidebar_frame'):
                self.sidebar_frame.show()

        idx_map = {"dashboard": 0, "studio": 1, "tiktokshop": 2, "accs": 3, "sett": 4}
        self.stacked.setCurrentIndex(idx_map.get(key, 0))
        if key == "dashboard":
            self._refresh_dashboard()

    # ── PAGE 0: DASHBOARD ─────────────────────────────────────────────
    def _build_dashboard_panel(self):
        panel = QWidget(); lay = QVBoxLayout(panel); lay.setContentsMargins(24,20,24,20); lay.setSpacing(16)

        # Header
        hdr = QHBoxLayout()
        tl = QLabel("🏠  Dashboard"); tl.setStyleSheet("font-size:22px;font-weight:bold;color:#1e293b;"); hdr.addWidget(tl)
        sl = QLabel("Quản lý dự án tạo video AI"); sl.setStyleSheet("font-size:12px;color:#64748b;margin-top:4px;"); hdr.addWidget(sl,alignment=Qt.AlignmentFlag.AlignBottom)
        hdr.addStretch()
        rb = QPushButton("🎭 Quản Lý Role Skill"); rb.setFixedHeight(36)
        rb.setStyleSheet("QPushButton{background:#f97316;color:white;font-weight:bold;padding:0 16px;border:none;border-radius:8px;font-size:12px;}QPushButton:hover{background:#ea580c;}")
        rb.clicked.connect(self._open_role_skill_manager); hdr.addWidget(rb)
        nb = QPushButton("+ Tạo Dự Án Mới"); nb.setObjectName("accent_btn"); nb.setFixedHeight(36)
        nb.clicked.connect(self._create_project_dialog); hdr.addWidget(nb)
        lay.addLayout(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setStyleSheet("color:#e2e8f0;"); lay.addWidget(sep)

        self.dash_scroll = QScrollArea(); self.dash_scroll.setWidgetResizable(True); self.dash_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.dash_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self.dash_cards_w = QWidget(); self.dash_cards_w.setStyleSheet("background:transparent;")
        self.dash_grid = QGridLayout(self.dash_cards_w); self.dash_grid.setSpacing(16); self.dash_grid.setContentsMargins(0,0,0,0)
        self.dash_scroll.setWidget(self.dash_cards_w); lay.addWidget(self.dash_scroll)
        self.stacked.addWidget(panel)

    def _refresh_dashboard(self):
        while self.dash_grid.count():
            it = self.dash_grid.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        if not self.projects:
            el = QLabel("Chưa có dự án nào.\nBấm '+ Tạo Dự Án Mới' để bắt đầu."); el.setAlignment(Qt.AlignmentFlag.AlignCenter)
            el.setStyleSheet("font-size:14px;color:#94a3b8;padding:60px;"); self.dash_grid.addWidget(el,0,0,1,3); return

        cols = 3
        for i,p in enumerate(self.projects):
            self.dash_grid.addWidget(self._build_proj_card(p), i//cols, i%cols)
        self.dash_grid.setRowStretch(self.dash_grid.rowCount(), 1)
        self._refresh_sidebar_projects()

    def _build_proj_card(self, proj):
        card = QFrame(); card.setObjectName("card")
        cl = QVBoxLayout(card); cl.setContentsMargins(16,14,16,14); cl.setSpacing(10)

        # Title + Badge + Icons
        tr = QHBoxLayout()
        tl = QLabel(proj.name); tl.setStyleSheet("font-size:15px;font-weight:bold;color:#1e293b;"); tr.addWidget(tl); tr.addStretch()
        badge = QLabel("Role Skill"); badge.setStyleSheet("background:#f97316;color:white;font-size:10px;font-weight:bold;padding:3px 8px;border-radius:4px;"); tr.addWidget(badge)
        bd = QPushButton("📋"); bd.setFixedSize(24,24); bd.setStyleSheet("border:none;background:transparent;"); tr.addWidget(bd)
        dd = QPushButton("🗑️"); dd.setFixedSize(24,24); dd.setStyleSheet("border:none;background:transparent;"); dd.clicked.connect(lambda _,p=proj: self._delete_project(p)); tr.addWidget(dd)
        cl.addLayout(tr)

        # Stats
        done_n = len([p for p in proj.products if "✅" in p.status])
        run_n = len([p for p in proj.products if "⏳" in p.status])
        fail_n = len([p for p in proj.products if "❌" in p.status])
        sr = QHBoxLayout()
        for val,color,label in [(done_n,"#16a34a","Hoàn thành"),(run_n,"#2563eb","Đang xử lý"),(fail_n,"#dc2626","Thất bại")]:
            bx = QVBoxLayout()
            nl = QLabel(str(val)); nl.setAlignment(Qt.AlignmentFlag.AlignCenter); nl.setStyleSheet(f"font-size:20px;font-weight:900;color:{color};"); bx.addWidget(nl)
            ll = QLabel(label); ll.setAlignment(Qt.AlignmentFlag.AlignCenter); ll.setStyleSheet("font-size:10px;color:#64748b;"); bx.addWidget(ll)
            sr.addLayout(bx)
        cl.addLayout(sr)

        # Status pill + timestamp
        pr = QHBoxLayout()
        pill = QLabel("CHƯA CHẠY"); pill.setStyleSheet("background:#475569;color:white;font-size:9px;font-weight:bold;padding:2px 8px;border-radius:3px;"); pr.addWidget(pill)
        pr.addStretch()
        ts = QLabel(proj.created_at); ts.setStyleSheet("font-size:10px;color:#94a3b8;"); pr.addWidget(ts)
        cl.addLayout(pr)

        # Open button
        bo = QPushButton("▶  Mở Dự Án"); bo.setFixedHeight(34)
        bo.setStyleSheet("QPushButton{background:#f3e8ff;color:#7c3aed;border:none;border-radius:8px;font-weight:bold;font-size:13px;}QPushButton:hover{background:#ede9fe;}")
        bo.clicked.connect(lambda _,p=proj: self._open_project(p)); cl.addWidget(bo)

        # Sub-buttons row
        sbr = QHBoxLayout()
        b1 = QPushButton("🔄 Chạy lại toàn bộ"); b1.setStyleSheet("QPushButton{background:#fff7ed;border:1px solid #ffedd5;color:#c2410c;border-radius:6px;padding:5px;font-weight:bold;font-size:10px;}QPushButton:hover{background:#ffedd5;}")
        b1.clicked.connect(lambda _,p=proj: self._open_project(p)); sbr.addWidget(b1)
        b2 = QPushButton("🔁 Retry phân cảnh lỗi"); b2.setStyleSheet("QPushButton{background:#f0f9ff;border:1px solid #e0f2fe;color:#0369a1;border-radius:6px;padding:5px;font-weight:bold;font-size:10px;}QPushButton:hover{background:#e0f2fe;}")
        sbr.addWidget(b2); cl.addLayout(sbr)
        return card

    # ── PAGE 1: STUDIO (HIỆN BÊN TRONG STACKED, SIDEBAR VẪN GIỮ) ─────
    def _build_studio_panel(self):
        panel = QWidget(); lay = QVBoxLayout(panel); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)

        # Top Bar
        top = QWidget(); top.setFixedHeight(48); top.setStyleSheet("background:#1e1b4b;border-bottom:2px solid #4c1d95;")
        tl = QHBoxLayout(top); tl.setContentsMargins(16,0,16,0); tl.setSpacing(10)
        
        # Back button
        btn_back = QPushButton("⬅ Quay lại Dashboard")
        btn_back.setStyleSheet("QPushButton { background:#4338ca; color:white; font-weight:bold; padding:6px 14px; border:none; border-radius:6px; } QPushButton:hover { background:#3730a3; }")
        btn_back.clicked.connect(lambda: self.switch_panel("dashboard"))
        tl.addWidget(btn_back)

        self.studio_proj_lbl = QLabel("🎭  Role Skill Pipeline — Chưa chọn dự án"); self.studio_proj_lbl.setStyleSheet("font-size:14px;font-weight:bold;color:#facc15;"); tl.addWidget(self.studio_proj_lbl)
        tl.addStretch()
        self.lbl_header_role = QLabel("Role: [RoleSkill.MD]"); self.lbl_header_role.setStyleSheet("color:#e0e7ff;font-weight:bold;font-size:11px;background:#312e81;padding:4px 10px;border-radius:6px;"); tl.addWidget(self.lbl_header_role)
        ba = QPushButton("➕ Thêm Role"); ba.setStyleSheet("background:#10b981;color:white;font-weight:bold;padding:5px 10px;border-radius:6px;"); ba.clicked.connect(self._add_role_skill); tl.addWidget(ba)
        be = QPushButton("📝 Sửa Role MD"); be.setStyleSheet("background:rgba(196,181,253,0.2);color:white;border:1px solid rgba(196,181,253,0.4);font-weight:bold;padding:5px 10px;border-radius:6px;"); be.clicked.connect(self._open_role_md); tl.addWidget(be)
        bd = QPushButton("🗑️ Xóa Role"); bd.setStyleSheet("background:#dc2626;color:white;font-weight:bold;padding:5px 10px;border-radius:6px;"); bd.clicked.connect(self._del_role_skill); tl.addWidget(bd)
        lay.addWidget(top)

        # Content: Config Left + Products Right
        content = QSplitter(Qt.Orientation.Horizontal); content.setStyleSheet("QSplitter::handle{background:#e2e8f0;width:1px;}")

        # LEFT: Config scroll
        left_sc = QScrollArea(); left_sc.setWidgetResizable(True); left_sc.setMinimumWidth(320); left_sc.setMaximumWidth(430)
        left_sc.setStyleSheet("QScrollArea{background:#f8fafc;border:none;}")
        left_w = QWidget(); ll = QVBoxLayout(left_w); ll.setContentsMargins(12,12,12,12); ll.setSpacing(8)

        # Card: Sản phẩm
        c1,c1l = self._mk_card("📦 SẢN PHẨM & CHẠY TỰ ĐỘNG")
        r1 = QHBoxLayout()
        ba = QPushButton("➕ Thêm ảnh SP"); ba.setObjectName("accent_btn"); ba.clicked.connect(self._add_product); r1.addWidget(ba)
        bt = QPushButton("🛍️ TiktokShop"); bt.setObjectName("normal_btn"); bt.clicked.connect(self._open_tiktok_dialog_in_studio); r1.addWidget(bt)
        c1l.addLayout(r1)
        r2 = QHBoxLayout()
        self.btn_start = QPushButton("▶️ Bắt đầu"); self.btn_start.setStyleSheet("background:#16a34a;color:white;font-weight:bold;padding:8px;border-radius:6px;"); self.btn_start.clicked.connect(self._start_pipeline); r2.addWidget(self.btn_start)
        bp = QPushButton("⏸️ Tạm dừng"); bp.setStyleSheet("background:#0284c7;color:white;font-weight:bold;padding:8px;border-radius:6px;"); bp.clicked.connect(self._pause_pipeline); r2.addWidget(bp)
        c1l.addLayout(r2)
        r3 = QHBoxLayout()
        bds = QPushButton("🗑️ Xóa SP cuối"); bds.setStyleSheet("background:#ef4444;color:white;font-weight:bold;padding:5px;border-radius:6px;"); bds.clicked.connect(self._del_last_product); r3.addWidget(bds)
        bda = QPushButton("🗑️ Xóa tất cả"); bda.setStyleSheet("background:#991b1b;color:white;font-weight:bold;padding:5px;border-radius:6px;"); bda.clicked.connect(self._del_all_products); r3.addWidget(bda)
        c1l.addLayout(r3); ll.addWidget(c1)

        # Card: Model AI
        c2,c2l = self._mk_card("🤖 CẤU HÌNH MODEL AI & LUỒNG VEO3")
        r_head = QHBoxLayout()
        r_head.addWidget(QLabel("🎭 Role Skill (Kịch Bản Mẫu):"))
        btn_quick_role = QPushButton("📝 Chỉnh Sửa Data")
        btn_quick_role.setStyleSheet("QPushButton{background:#3b82f6;color:white;font-weight:bold;padding:2px 8px;border:none;border-radius:4px;font-size:11px;}QPushButton:hover{background:#2563eb;}")
        btn_quick_role.clicked.connect(lambda: self._open_role_skill_manager(self.cb_role.currentText()))
        r_head.addWidget(btn_quick_role)
        c2l.addLayout(r_head)
        self.cb_role = QComboBox(); self.cb_role.currentIndexChanged.connect(self._on_role_changed); c2l.addWidget(self.cb_role)
        c2l.addWidget(QLabel("LLM Model (Biên Kịch):"))
        self.cb_llm = QComboBox(); self.cb_llm.addItems(LLM_MODEL_LIST); self.cb_llm.setCurrentText(self.settings.get("llm_model","gpt-5.5")); c2l.addWidget(self.cb_llm)
        c2l.addWidget(QLabel("🎙️ Chế Độ Voiceover (Voice Mode):"))
        self.cb_voice_mode = QComboBox()
        self.cb_voice_mode.addItems([
            "Review dẫn chuyện (narrative_review)",
            "Đọc nguyên văn (verbatim_read)",
            "Review kèm Đọc CMT (hybrid_narrate_read)"
        ])
        saved_vm = self.settings.get("voice_mode_display", "Review dẫn chuyện (narrative_review)")
        self.cb_voice_mode.setCurrentText(saved_vm)
        c2l.addWidget(self.cb_voice_mode)
        c2l.addWidget(QLabel("Ảnh AI Model (Threads UI):"))
        self.cb_img = QComboBox(); self.cb_img.addItems(IMAGE_MODEL_LIST); self.cb_img.setCurrentText(self.settings.get("img_model","gpt-image-2")); c2l.addWidget(self.cb_img)
        c2l.addWidget(QLabel("🖼️ Chất Lượng Ảnh (Resolution):"))
        self.cb_img_quality = QComboBox()
        self.cb_img_quality.addItems([
            "1K (1024x1280 / Standard)",
            "2K (2048x2560 / Ultra HD)"
        ])
        self.cb_img_quality.setCurrentText(self.settings.get("image_quality", "1K (1024x1280 / Standard)"))
        c2l.addWidget(self.cb_img_quality)
        # Phong cách kịch bản (Archetype) instead of Context
        lay_style_header = QHBoxLayout()
        lay_style_header.setContentsMargins(0,0,0,0)
        lbl_style_title = QLabel("🎭 Phong cách kịch bản (Archetype):")
        lbl_style_title.setStyleSheet("font-weight: bold;")
        lay_style_header.addWidget(lbl_style_title)
        lay_style_header.addStretch()
        
        btn_manage_styles = QPushButton("⚙️ Quản lý")
        btn_manage_styles.setFixedSize(65, 20)
        btn_manage_styles.setStyleSheet("QPushButton { background-color: #6366f1; color: white; border-radius: 4px; font-weight: bold; font-size: 9px; } QPushButton:hover { background-color: #4f46e5; }")
        btn_manage_styles.clicked.connect(self._on_manage_styles_clicked)
        lay_style_header.addWidget(btn_manage_styles)
        
        btn_add_style = QPushButton("➕ Thêm")
        btn_add_style.setFixedSize(55, 20)
        btn_add_style.setStyleSheet("QPushButton { background-color: #3b82f6; color: white; border-radius: 4px; font-weight: bold; font-size: 9px; } QPushButton:hover { background-color: #2563eb; }")
        btn_add_style.clicked.connect(self._on_add_custom_style)
        lay_style_header.addWidget(btn_add_style)
        c2l.addLayout(lay_style_header)

        self.cb_ctx = QComboBox()
        self._load_archetypes_into_combo(self.cb_ctx)
        saved_style = self.settings.get("context", "Tự chọn phong cách tối ưu nhất")
        self.cb_ctx.setCurrentText(saved_style)
        c2l.addWidget(self.cb_ctx)
        
        # Veo3 High-Speed Controls (Chuẩn 100% Veo3Studio)
        g_lay1 = QHBoxLayout()
        v_c1 = QVBoxLayout()
        v_c1.addWidget(QLabel("⚡ Số luồng song song:"))
        self.sp_concurrency = QSpinBox()
        self.sp_concurrency.setRange(1, 12)
        self.sp_concurrency.setValue(self.settings.get("concurrency", 8))
        v_c1.addWidget(self.sp_concurrency)
        g_lay1.addLayout(v_c1)

        v_c2 = QVBoxLayout()
        v_c2.addWidget(QLabel("⏳ Nghỉ sau đợt (s):"))
        self.sp_burst_delay = QSpinBox()
        self.sp_burst_delay.setRange(1, 60)
        self.sp_burst_delay.setValue(self.settings.get("burst_delay", 10))
        v_c2.addWidget(self.sp_burst_delay)
        g_lay1.addLayout(v_c2)
        c2l.addLayout(g_lay1)

        g_lay2 = QHBoxLayout()
        v_c3 = QVBoxLayout()
        v_c3.addWidget(QLabel("🖼️ Số ảnh mỗi đợt (Burst):"))
        self.sp_burst_size = QSpinBox()
        self.sp_burst_size.setRange(1, 8)
        self.sp_burst_size.setValue(self.settings.get("burst_size", 4))
        v_c3.addWidget(self.sp_burst_size)
        g_lay2.addLayout(v_c3)

        v_c4 = QVBoxLayout()
        v_c4.addWidget(QLabel("🔍 Luồng Upscale ảnh:"))
        self.sp_upscale_threads = QSpinBox()
        self.sp_upscale_threads.setRange(1, 4)
        self.sp_upscale_threads.setValue(self.settings.get("upscale_threads", 1))
        v_c4.addWidget(self.sp_upscale_threads)
        g_lay2.addLayout(v_c4)
        c2l.addLayout(g_lay2)
        ll.addWidget(c2)
        self._reload_role_combo()

        # Card: Voice
        c3,c3l = self._mk_card("🎙️ CẤU HÌNH VOICE & PHÂN CẢNH")
        c3l.addWidget(QLabel("Engine Voiceover:"))
        self.cb_voice = QComboBox()
        self.cb_voice.addItems(["gTTS Tiếng Việt", "Vbee API"])
        
        # Default to Vbee API, or whatever is saved in settings
        saved_engine = self.settings.get("voice_engine", "vbee")
        if saved_engine == "gtts":
            self.cb_voice.setCurrentIndex(0)
        else:
            self.cb_voice.setCurrentIndex(1)
            
        self.cb_voice.currentIndexChanged.connect(self._on_voice_changed)
        c3l.addWidget(self.cb_voice)
        
        self.w_vbee = QWidget()
        vl = QVBoxLayout(self.w_vbee)
        vl.setContentsMargins(0,0,0,0)
        vl.addWidget(QLabel("Token:"))
        self.txt_vbee_tok = QLineEdit(self.settings.get("vbee_token",""))
        vl.addWidget(self.txt_vbee_tok)
        vl.addWidget(QLabel("App-ID:"))
        self.txt_vbee_aid = QLineEdit(self.settings.get("vbee_appid",""))
        vl.addWidget(self.txt_vbee_aid)
        bv = QPushButton("🔄 Tải Voice Vbee")
        bv.setObjectName("normal_btn")
        bv.clicked.connect(self._fetch_vbee)
        vl.addWidget(bv)
        vl.addWidget(QLabel("🎤 Voice Chính (Dẫn chuyện):"))
        self.cb_vbee_v = QComboBox()
        self.cb_vbee_v.setEditable(True)
        
        # Load cached Vbee voices if they exist in settings
        cached_voices = self.settings.get("vbee_voices_cached", [])
        if cached_voices:
            for vc, lb in cached_voices:
                self.cb_vbee_v.addItem(lb, vc)
            # Restore currently selected voice code if possible
            saved_vbee_voice = self.settings.get("vbee_voice", "")
            if saved_vbee_voice:
                idx = self.cb_vbee_v.findData(saved_vbee_voice)
                if idx >= 0:
                    self.cb_vbee_v.setCurrentIndex(idx)
                else:
                    self.cb_vbee_v.setCurrentText(saved_vbee_voice)
        else:
            self.cb_vbee_v.addItem("vi_south_female_miien_48k", "vi_south_female_miien_48k")
            saved_vbee_voice = self.settings.get("vbee_voice", "")
            if saved_vbee_voice:
                self.cb_vbee_v.setCurrentText(saved_vbee_voice)
                
        vl.addWidget(self.cb_vbee_v)
        
        # Sub-voice list for random per-scene assignment
        vl.addWidget(QLabel("👥 Voice Phụ (Đọc CMT, Random):"))
        self.list_sub_voices = QListWidget()
        self.list_sub_voices.setMinimumHeight(180)
        self.list_sub_voices.setMaximumHeight(260)
        self.list_sub_voices.setStyleSheet("QListWidget{background:#1e293b;color:#f8fafc;border:1px solid #475569;border-radius:4px;font-size:12px;} QListWidget::item{padding:5px 4px;border-bottom:1px solid #334155;} QListWidget::item:hover{background:#334155;} QListWidget::indicator{width:16px;height:16px;}")
        self._populate_sub_voice_list()
        vl.addWidget(self.list_sub_voices)
        sub_v_btns = QHBoxLayout()
        btn_sel_all = QPushButton("✅ Chọn tất cả")
        btn_sel_all.setStyleSheet("QPushButton{background:#10b981;color:white;font-size:10px;padding:3px 6px;border:none;border-radius:3px;}QPushButton:hover{background:#059669;}")
        btn_sel_all.clicked.connect(lambda: self._toggle_all_sub_voices(True))
        sub_v_btns.addWidget(btn_sel_all)
        btn_desel_all = QPushButton("❌ Bỏ chọn")
        btn_desel_all.setStyleSheet("QPushButton{background:#ef4444;color:white;font-size:10px;padding:3px 6px;border:none;border-radius:3px;}QPushButton:hover{background:#dc2626;}")
        btn_desel_all.clicked.connect(lambda: self._toggle_all_sub_voices(False))
        sub_v_btns.addWidget(btn_desel_all)
        sub_v_btns.addStretch()
        vl.addLayout(sub_v_btns)
        
        vl.addWidget(QLabel("Speed:"))
        self.sp_vbee_spd = QDoubleSpinBox()
        self.sp_vbee_spd.setRange(0.5,2.0)
        self.sp_vbee_spd.setValue(self.settings.get("vbee_speed",1.0))
        vl.addWidget(self.sp_vbee_spd)
        c3l.addWidget(self.w_vbee)
        self.w_vbee.setVisible(self.cb_voice.currentIndex() == 1)
        c3l.addWidget(QLabel("Số Phân cảnh:"))
        self.sp_scenes = QSpinBox(); self.sp_scenes.setRange(2,10); self.sp_scenes.setValue(self.settings.get("scene_count",4)); c3l.addWidget(self.sp_scenes)
        dr = QHBoxLayout(); dr.addWidget(QLabel("Delay(s):")); self.sp_delay = QSpinBox(); self.sp_delay.setValue(self.settings.get("delay_sec",2)); dr.addWidget(self.sp_delay)
        dr.addWidget(QLabel("Retry:")); self.sp_retry = QSpinBox(); self.sp_retry.setValue(self.settings.get("retry_count",3)); dr.addWidget(self.sp_retry)
        c3l.addLayout(dr); ll.addWidget(c3)

        # Card: Edit
        c4,c4l = self._mk_card("🎬 EDIT & VIDEO BACKGROUND")
        c4l.addWidget(QLabel("Thư mục BG Nguồn:"))
        bgr = QHBoxLayout(); self.txt_bg = QLineEdit(self.settings.get("bg_folder","")); bgr.addWidget(self.txt_bg)
        bbg = QPushButton("📂"); bbg.setObjectName("normal_btn"); bbg.setFixedWidth(36); bbg.clicked.connect(self._browse_bg); bgr.addWidget(bbg); c4l.addLayout(bgr)
        self.chk_nodup = QCheckBox("🎲 Random không trùng"); self.chk_nodup.setChecked(self.settings.get("no_duplicate_bg",True)); c4l.addWidget(self.chk_nodup)
        self.chk_meta = QCheckBox("🛡️ Fake Metadata"); self.chk_meta.setChecked(self.settings.get("fake_metadata",True)); c4l.addWidget(self.chk_meta)
        self.chk_fh = QCheckBox("↔️ Flip H"); self.chk_fh.setChecked(self.settings.get("anti_flip_h",False)); c4l.addWidget(self.chk_fh)
        self.chk_noise = QCheckBox("✨ Noise/Color Shift"); self.chk_noise.setChecked(self.settings.get("anti_noise",True)); c4l.addWidget(self.chk_noise)
        ll.addWidget(c4)

        # Card: Video Review Sản Phẩm
        c5, c5l = self._mk_card("🎬 VIDEO REVIEW SẢN PHẨM")
        self.chk_vr_enable = QCheckBox("Thêm Video Review sau phần ảnh")
        self.chk_vr_enable.setChecked(self.settings.get("vr_enabled", False))
        self.chk_vr_enable.stateChanged.connect(self._toggle_vr_settings)
        c5l.addWidget(self.chk_vr_enable)

        # Mode selector
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self.cb_vr_mode = QComboBox()
        self.cb_vr_mode.addItems(["AI Tạo Video", "Video File Ngoài"])
        vr_mode_idx = 0 if self.settings.get("vr_mode", "ai_generate") == "ai_generate" else 1
        self.cb_vr_mode.setCurrentIndex(vr_mode_idx)
        self.cb_vr_mode.currentIndexChanged.connect(self._toggle_vr_mode)
        mode_row.addWidget(self.cb_vr_mode)
        c5l.addLayout(mode_row)

        # ── AI Mode Settings ──
        self.w_vr_ai = QWidget()
        vr_ai_l = QVBoxLayout(self.w_vr_ai); vr_ai_l.setContentsMargins(0,4,0,0); vr_ai_l.setSpacing(4)

        vr_ai_l.addWidget(QLabel("Model Video:"))
        self.cb_vr_video_model = QComboBox()
        self.cb_vr_video_model.addItems(VIDEO_MODEL_LIST)
        cur_vr_model = self.settings.get("vr_video_model", "Veo 3.1 - Lite [Lower Priority]")
        idx = self.cb_vr_video_model.findText(cur_vr_model)
        if idx >= 0: self.cb_vr_video_model.setCurrentIndex(idx)
        vr_ai_l.addWidget(self.cb_vr_video_model)

        vr_ai_l.addWidget(QLabel("Chất lượng video:"))
        self.cb_vr_resolution = QComboBox()
        self.cb_vr_resolution.addItems(VIDEO_RESOLUTION_LIST)
        cur_res = self.settings.get("vr_resolution", "720p")
        idx = self.cb_vr_resolution.findText(cur_res)
        if idx >= 0: self.cb_vr_resolution.setCurrentIndex(idx)
        vr_ai_l.addWidget(self.cb_vr_resolution)

        vr_ai_l.addWidget(QLabel("Role Skill Video:"))
        self.cb_vr_role_skill = QComboBox()
        vr_rs_dir = Path(__file__).resolve().parent / "data" / "Role Skill"
        vr_rs_files = []
        if vr_rs_dir.exists():
            for ext in ("*.md", "*.MD"):
                vr_rs_files.extend(f.name for f in vr_rs_dir.glob(ext))
        vr_rs_files = sorted(set(vr_rs_files))
        self.cb_vr_role_skill.addItems(vr_rs_files)
        cur_vr_rs = self.settings.get("vr_role_skill", "Video AI Mỹ Phẩm.MD")
        idx = self.cb_vr_role_skill.findText(cur_vr_rs)
        if idx >= 0: self.cb_vr_role_skill.setCurrentIndex(idx)
        vr_ai_l.addWidget(self.cb_vr_role_skill)

        row_sc = QHBoxLayout()
        row_sc.addWidget(QLabel("Số phân cảnh:"))
        self.sp_vr_scenes = QSpinBox(); self.sp_vr_scenes.setRange(2, 8)
        self.sp_vr_scenes.setValue(self.settings.get("vr_scene_count", 4))
        row_sc.addWidget(self.sp_vr_scenes)
        row_sc.addWidget(QLabel("Clip(s):"))
        self.cb_vr_clip_dur = QComboBox()
        self.cb_vr_clip_dur.addItems(VIDEO_DURATION_LIST)
        cur_dur = str(self.settings.get("vr_clip_duration", 8))
        idx = self.cb_vr_clip_dur.findText(cur_dur)
        if idx >= 0: self.cb_vr_clip_dur.setCurrentIndex(idx)
        row_sc.addWidget(self.cb_vr_clip_dur)
        vr_ai_l.addLayout(row_sc)

        vr_ai_l.addWidget(QLabel("Aspect Ratio:"))
        self.cb_vr_aspect = QComboBox()
        self.cb_vr_aspect.addItems(VIDEO_ASPECT_LIST)
        cur_aspect = self.settings.get("vr_aspect_ratio", "9:16")
        idx = self.cb_vr_aspect.findText(cur_aspect)
        if idx >= 0: self.cb_vr_aspect.setCurrentIndex(idx)
        vr_ai_l.addWidget(self.cb_vr_aspect)

        c5l.addWidget(self.w_vr_ai)

        # ── External File Mode Settings ──
        self.w_vr_ext = QWidget()
        vr_ext_l = QVBoxLayout(self.w_vr_ext); vr_ext_l.setContentsMargins(0,4,0,0); vr_ext_l.setSpacing(4)

        vr_ext_l.addWidget(QLabel("Video sản phẩm:"))
        vr_file_row = QHBoxLayout()
        self.txt_vr_file = QLineEdit(self.settings.get("vr_external_path", ""))
        self.txt_vr_file.setPlaceholderText("Chọn file video...")
        vr_file_row.addWidget(self.txt_vr_file)
        btn_vr_browse = QPushButton("📂"); btn_vr_browse.setObjectName("normal_btn"); btn_vr_browse.setFixedWidth(36)
        btn_vr_browse.clicked.connect(self._browse_vr_video)
        vr_file_row.addWidget(btn_vr_browse)
        vr_ext_l.addLayout(vr_file_row)

        vr_ext_l.addWidget(QLabel("Crop:"))
        self.cb_vr_crop = QComboBox()
        self.cb_vr_crop.addItems(["Vuông giữa (1:1)", "9:16 giữa", "Giữ nguyên"])
        cur_crop = self.settings.get("vr_crop_mode", "square_center")
        crop_map = {"square_center": 0, "9_16_center": 1, "keep_original": 2}
        self.cb_vr_crop.setCurrentIndex(crop_map.get(cur_crop, 0))
        vr_ext_l.addWidget(self.cb_vr_crop)

        row_speed = QHBoxLayout()
        row_speed.addWidget(QLabel("Tốc độ:"))
        self.sp_vr_speed = QDoubleSpinBox(); self.sp_vr_speed.setRange(0.5, 2.0)
        self.sp_vr_speed.setSingleStep(0.1); self.sp_vr_speed.setValue(self.settings.get("vr_speed", 1.0))
        self.sp_vr_speed.setSuffix("x")
        row_speed.addWidget(self.sp_vr_speed)
        row_speed.addWidget(QLabel("Thời lượng max(s):"))
        self.sp_vr_max_dur = QSpinBox(); self.sp_vr_max_dur.setRange(5, 120)
        self.sp_vr_max_dur.setValue(self.settings.get("vr_max_duration", 30))
        row_speed.addWidget(self.sp_vr_max_dur)
        vr_ext_l.addLayout(row_speed)

        self.chk_vr_zoom = QCheckBox("🔍 Zoom random (1.0-1.1x)")
        self.chk_vr_zoom.setChecked(self.settings.get("vr_zoom_random", False))
        vr_ext_l.addWidget(self.chk_vr_zoom)
        self.chk_vr_flip = QCheckBox("↔️ Lật ngang (anti-reup)")
        self.chk_vr_flip.setChecked(self.settings.get("vr_flip_h", False))
        vr_ext_l.addWidget(self.chk_vr_flip)
        self.chk_vr_noise = QCheckBox("✨ Noise nhẹ (anti-detect)")
        self.chk_vr_noise.setChecked(self.settings.get("vr_noise", True))
        vr_ext_l.addWidget(self.chk_vr_noise)

        self.chk_vr_rcut = QCheckBox("✂️ Cắt random nhiều khúc (0.2-0.5s)")
        self.chk_vr_rcut.setChecked(self.settings.get("vr_random_cut", True))
        vr_ext_l.addWidget(self.chk_vr_rcut)
        row_rcut = QHBoxLayout()
        row_rcut.addWidget(QLabel("  Khoảng cách cắt(s):"))
        self.sp_vr_rcut_min = QSpinBox(); self.sp_vr_rcut_min.setRange(2, 10)
        self.sp_vr_rcut_min.setValue(self.settings.get("vr_rcut_interval_min", 3))
        row_rcut.addWidget(self.sp_vr_rcut_min)
        row_rcut.addWidget(QLabel("→"))
        self.sp_vr_rcut_max = QSpinBox(); self.sp_vr_rcut_max.setRange(3, 15)
        self.sp_vr_rcut_max.setValue(self.settings.get("vr_rcut_interval_max", 5))
        row_rcut.addWidget(self.sp_vr_rcut_max)
        vr_ext_l.addLayout(row_rcut)

        c5l.addWidget(self.w_vr_ext)

        # Initial visibility
        self._toggle_vr_settings()

        ll.addWidget(c5)
        ll.addStretch()

        left_sc.setWidget(left_w); content.addWidget(left_sc)

        # RIGHT: Products + Logs
        right_w = QWidget(); rl = QVBoxLayout(right_w); rl.setContentsMargins(12,12,12,12); rl.setSpacing(8)
        self.prod_scroll = QScrollArea(); self.prod_scroll.setWidgetResizable(True)
        self.prod_scroll.setStyleSheet("QScrollArea{background:#ffffff;border:none;}")
        self.prod_content = QWidget(); self.prod_lay = QVBoxLayout(self.prod_content); self.prod_lay.setSpacing(12)
        self.prod_scroll.setWidget(self.prod_content); rl.addWidget(self.prod_scroll)

        rl.addWidget(QLabel("🖥️ Logs chi tiết:"))
        self.txt_log = QTextEdit(); self.txt_log.setFixedHeight(120); self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background:#0f172a;color:#38bdf8;font-family:Consolas;font-size:11px;border-radius:6px;"); rl.addWidget(self.txt_log)

        content.addWidget(right_w); content.setSizes([350,800])
        lay.addWidget(content)
        self.stacked.addWidget(panel)

    # ── PAGE 2: TIKTOKSHOP ────────────────────────────────────────────
    def _build_tiktok_panel(self):
        panel = QWidget()
        panel.setStyleSheet("""
            QWidget { background-color: #ffffff; color: #1e293b; }
            QGroupBox { border: 1px solid #cbd5e1; border-radius: 8px; margin-top: 15px; padding-top: 15px; font-weight: bold; color: #7c3aed; background-color: #f8fafc; }
            QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 15px; padding: 0 5px; }
            QPushButton { background-color: #7c3aed; border: none; border-radius: 6px; padding: 8px 16px; font-weight: bold; color: white; }
            QPushButton:hover { background-color: #6d28d9; }
            QTextEdit, QLineEdit, QSpinBox { background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 6px; color: #0f172a; }
            QProgressBar { border: 1px solid #cbd5e1; border-radius: 6px; text-align: center; background-color: #f1f5f9; color: #1e293b; font-weight: bold; }
            QProgressBar::chunk { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7c3aed, stop:1 #06b6d4); border-radius: 5px; }
            QTableWidget { background-color: #ffffff; border: 1px solid #cbd5e1; gridline-color: #cbd5e1; border-radius: 8px; }
            QHeaderView::section { background-color: #f8fafc; color: #7c3aed; padding: 6px; font-weight: bold; border: 1px solid #cbd5e1; }
        """)
        main_layout = QVBoxLayout(panel)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        header_layout = QHBoxLayout()
        self.ts_chrome_status = QLabel("🔎 Đang kiểm tra trạng thái Chrome Spy...")
        self.ts_chrome_status.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        header_layout.addWidget(self.ts_chrome_status)
        
        self.ts_btn_open_spy = QPushButton("🌐 MỞ CHROME SPY")
        self.ts_btn_open_spy.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.ts_btn_open_spy.setStyleSheet("background-color: #059669; color: white;")
        self.ts_btn_open_spy.clicked.connect(self.ts_action_open_chrome_spy)
        header_layout.addWidget(self.ts_btn_open_spy)
        main_layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        
        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        grp_inputs = QGroupBox("Danh sách liên kết sản phẩm (mỗi link trên 1 dòng)")
        inputs_layout = QVBoxLayout(grp_inputs)
        self.ts_txt_urls = QTextEdit()
        self.ts_txt_urls.setPlaceholderText("Dán danh sách các link sản phẩm TikTok Shop vào đây (mỗi link trên 1 dòng)...\nHoặc nhấn 'Chọn file chứa Links' bên dưới.")
        inputs_layout.addWidget(self.ts_txt_urls)
        
        btn_file_layout = QHBoxLayout()
        self.ts_btn_import_file = QPushButton("📁 Chọn file chứa Links")
        self.ts_btn_import_file.clicked.connect(self.ts_action_import_file)
        btn_file_layout.addWidget(self.ts_btn_import_file)
        
        self.ts_lbl_file_path = QLabel("Chưa chọn file")
        self.ts_lbl_file_path.setStyleSheet("color: #64748b;")
        btn_file_layout.addWidget(self.ts_lbl_file_path)
        btn_file_layout.addStretch()
        inputs_layout.addLayout(btn_file_layout)
        top_layout.addWidget(grp_inputs, 3)

        grp_config = QGroupBox("Cấu hình Crawl")
        config_layout = QVBoxLayout(grp_config)
        spin_layout = QHBoxLayout()
        spin_layout.addWidget(QLabel("Số luồng:"))
        self.ts_spin_threads = QSpinBox()
        self.ts_spin_threads.setRange(1, 5)
        self.ts_spin_threads.setValue(2)
        spin_layout.addWidget(self.ts_spin_threads)
        config_layout.addLayout(spin_layout)
        
        config_layout.addSpacing(10)
        self.ts_btn_start = QPushButton("🚀 BẮT ĐẦU CRAWL")
        self.ts_btn_start.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.ts_btn_start.setMinimumHeight(45)
        self.ts_btn_start.clicked.connect(self.ts_action_start_crawl)
        config_layout.addWidget(self.ts_btn_start)
        
        self.ts_btn_stop = QPushButton("🛑 DỪNG CÀO")
        self.ts_btn_stop.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.ts_btn_stop.setStyleSheet("background-color: #dc2626; color: white;")
        self.ts_btn_stop.setEnabled(False)
        self.ts_btn_stop.clicked.connect(self.ts_action_stop_crawl)
        config_layout.addWidget(self.ts_btn_stop)

        config_layout.addStretch()
        lbl_out_info = QLabel("Thư mục lưu trữ:")
        lbl_out_info.setStyleSheet("font-weight: bold; color: #7c3aed;")
        config_layout.addWidget(lbl_out_info)
        
        btn_open_folder = QPushButton("📁 Mở thư mục sản phẩm")
        btn_open_folder.setStyleSheet("background-color: #475569; color: white;")
        btn_open_folder.clicked.connect(self.ts_open_product_folder)
        config_layout.addWidget(btn_open_folder)
        top_layout.addWidget(grp_config, 1)
        splitter.addWidget(top_container)

        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        
        grp_table = QGroupBox("Danh sách sản phẩm cào được")
        table_layout = QVBoxLayout(grp_table)
        
        self.ts_table_products = QTableWidget()
        self.ts_table_products.setColumnCount(6)
        self.ts_table_products.setHorizontalHeaderLabels(["ID Sản phẩm", "Tiêu đề", "Giá", "Shop/Cửa hàng", "Trạng thái", "🗣 Voice MP3"])
        self.ts_table_products.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.ts_table_products.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.ts_table_products.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.ts_table_products.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.ts_table_products.doubleClicked.connect(self.ts_open_product_folder)

        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(6)
        lbl_folder = QLabel("📂 Thư mục phân loại:")
        lbl_folder.setStyleSheet("font-weight: bold; color: #7c3aed;")
        folder_layout.addWidget(lbl_folder)
        
        self.ts_combo_folder = QComboBox()
        self.ts_combo_folder.setStyleSheet("QComboBox { background-color: #ffffff; color: #1e293b; border: 1px solid #cbd5e1; border-radius: 6px; padding: 4px 8px; font-weight: bold; min-width: 180px; }")
        self.ts_combo_folder.currentIndexChanged.connect(self.ts_refresh_products_table)
        folder_layout.addWidget(self.ts_combo_folder)
        
        btn_ts_move_folder = QPushButton("📁 Phân loại...")
        btn_ts_move_folder.setToolTip("Di chuyển các sản phẩm đang chọn vào một thư mục phân loại")
        btn_ts_move_folder.setStyleSheet("QPushButton { background-color: #475569; color: white; border: none; border-radius: 5px; font-weight: bold; padding: 5px 12px; } QPushButton:hover { background-color: #334155; }")
        btn_ts_move_folder.clicked.connect(self.ts_action_move_folder)
        folder_layout.addWidget(btn_ts_move_folder)
        folder_layout.addStretch()
        table_layout.addLayout(folder_layout)

        ts_toolbar = QHBoxLayout()
        ts_toolbar.setSpacing(6)
        
        btn_ts_refresh = QPushButton("🔄 Làm mới")
        btn_ts_refresh.setFixedHeight(28)
        btn_ts_refresh.setStyleSheet("background-color: #f1f5f9; color: #1e293b; border: 1px solid #cbd5e1;")
        btn_ts_refresh.clicked.connect(self.ts_refresh_products_table)
        ts_toolbar.addWidget(btn_ts_refresh)
        ts_toolbar.addStretch()

        self.ts_edit_voice_url = QLineEdit()
        self.ts_edit_voice_url.setPlaceholderText("Dán link video TikTok để tải voice (tuỳ chọn)...")
        self.ts_edit_voice_url.setFixedHeight(28)
        self.ts_edit_voice_url.setMinimumWidth(280)
        ts_toolbar.addWidget(self.ts_edit_voice_url)

        btn_ts_dl_voice = QPushButton("🎵 Tải Voice")
        btn_ts_dl_voice.setFixedHeight(28)
        btn_ts_dl_voice.setStyleSheet("QPushButton { background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7c3aed,stop:1 #06b6d4); color:white; border:none; border-radius:5px; font-weight:bold; padding:0 10px; } QPushButton:hover { opacity:0.85; }")
        btn_ts_dl_voice.clicked.connect(self.ts_action_download_voice_selected)
        ts_toolbar.addWidget(btn_ts_dl_voice)

        btn_ts_dl_voice_all = QPushButton("🎵 Tải Voice Tất Cả")
        btn_ts_dl_voice_all.setFixedHeight(28)
        btn_ts_dl_voice_all.setStyleSheet("QPushButton { background: #0e7490; color:white; border:none; border-radius:5px; font-weight:bold; padding:0 10px; } QPushButton:hover { background: #155e75; }")
        btn_ts_dl_voice_all.clicked.connect(self.ts_action_download_voice_all)
        ts_toolbar.addWidget(btn_ts_dl_voice_all)

        btn_ts_add_proj = QPushButton("➕ Thêm vào Dự Án")
        btn_ts_add_proj.setFixedHeight(28)
        btn_ts_add_proj.setStyleSheet("QPushButton { background: #16a34a; color:white; border:none; border-radius:5px; font-weight:bold; padding:0 10px; } QPushButton:hover { background: #15803d; }")
        btn_ts_add_proj.clicked.connect(self.ts_action_add_to_current_project)
        ts_toolbar.addWidget(btn_ts_add_proj)

        table_layout.addLayout(ts_toolbar)
        table_layout.addWidget(self.ts_table_products)
        bottom_layout.addWidget(grp_table, 2)

        grp_log = QGroupBox("Nhật ký tiến trình")
        log_layout = QVBoxLayout(grp_log)
        self.ts_txt_logs = QTextEdit()
        self.ts_txt_logs.setReadOnly(True)
        self.ts_txt_logs.setStyleSheet("background-color: #0f172a; color: #38bdf8; font-family: Consolas, monospace; font-size: 11px;")
        log_layout.addWidget(self.ts_txt_logs)
        bottom_layout.addWidget(grp_log, 1)
        splitter.addWidget(bottom_container)
        main_layout.addWidget(splitter)

        progress_layout = QHBoxLayout()
        self.ts_progress_bar = QProgressBar()
        self.ts_progress_bar.setValue(0)
        progress_layout.addWidget(self.ts_progress_bar)
        
        self.ts_lbl_stats = QLabel("Đã cào: 0/0 | Thành công: 0 | Thất bại: 0")
        self.ts_lbl_stats.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        progress_layout.addWidget(self.ts_lbl_stats)
        main_layout.addLayout(progress_layout)

        self.ts_status_timer = QTimer(self)
        self.ts_status_timer.timeout.connect(self.ts_check_chrome_status)
        self.ts_status_timer.start(5000)
        
        self.ts_worker = None
        self.ts_refresh_folder_list()
        self.ts_refresh_products_table()
        self.stacked.addWidget(panel)

    def ts_check_chrome_status(self):
        try:
            if is_chrome_cdp_active(CDP_PORT):
                self.ts_chrome_status.setText("✅ Chrome Spy đang HOẠT ĐỘNG (CDP Port 9222 active)")
                self.ts_chrome_status.setStyleSheet("color: #16a34a; font-weight: bold;")
            else:
                self.ts_chrome_status.setText("❌ Chrome Spy chưa mở (Cần mở Chrome Spy để cào)")
                self.ts_chrome_status.setStyleSheet("color: #ef4444; font-weight: bold;")
        except Exception:
            pass

    def ts_action_open_chrome_spy(self):
        from TiktokShop.tiktok_shop_crawler import find_chrome_path
        chrome_path = find_chrome_path()
        if not chrome_path:
            QMessageBox.critical(self, "Lỗi", "Không tìm thấy trình duyệt Google Chrome trên hệ thống của bạn!")
            return
            
        if is_port_in_use(CDP_PORT):
            QMessageBox.warning(self, "Cảnh báo", f"Cổng {CDP_PORT} đang bị chiếm dụng bởi tiến trình khác. Vui lòng đóng tất cả các cửa sổ Chrome đang chạy rồi thử lại.")
            return
            
        PROFILE_DIR.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            chrome_path,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={PROFILE_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://seller-vn.tiktok.com"
        ]
        self.ts_write_log("🌐 Đang khởi động Google Chrome Spy với debug port 9222...")
        try:
            subprocess.Popen(cmd, creationflags=_CNOW)
            time.sleep(2)
            self.ts_check_chrome_status()
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể mở Chrome: {e}")

    def ts_action_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Chọn file chứa liên kết sản phẩm", "", "Text Files (*.txt);;All Files (*)")
        if file_path:
            self.ts_lbl_file_path.setText(os.path.basename(file_path))
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    urls = [line.strip() for line in f if line.strip()]
                self.ts_txt_urls.setPlainText("\n".join(urls))
                self.ts_write_log(f"📁 Đã nhập {len(urls)} links từ file: {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Không thể đọc file: {e}")

    def ts_action_download_voice_selected(self):
        row = self.ts_table_products.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Chưa chọn", "Vui lòng chọn sản phẩm trong bảng để tải voice!")
            return
            
        pid = self.ts_table_products.item(row, 0).text().strip()
        title = self.ts_table_products.item(row, 1).text().strip()
        url = self.ts_edit_voice_url.text().strip()
        
        if not url:
            url, ok = QInputDialog.getText(self, "Dán link video TikTok", f"Nhập link video TikTok chứa voice cho SP:\n{title}")
            if not ok or not url.strip():
                return
            url = url.strip()

        output_dir = DOWNLOADS_DIR / "voices" / pid
        output_dir.mkdir(parents=True, exist_ok=True)
        
        self.ts_write_log(f"🎵 Đang tải voice lồng tiếng cho sản phẩm {pid}...")
        
        worker = VoiceDownloadWorker(url, str(output_dir))
        def on_done(success, path):
            if success:
                self.ts_write_log(f"✅ Tải voice thành công cho {pid}: {path}")
                prod_json = DOWNLOADS_DIR / "products.json"
                if prod_json.exists():
                    try:
                        products = json.loads(prod_json.read_text(encoding="utf-8"))
                        for p in products:
                            if p.get("product_id") == pid:
                                if "voices" not in p: p["voices"] = []
                                rel_path = str(Path(path).relative_to(DOWNLOADS_DIR.parent)) if Path(path).is_relative_to(DOWNLOADS_DIR.parent) else path
                                if rel_path not in p["voices"]:
                                    p["voices"].append(rel_path)
                                break
                        prod_json.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
                    except: pass
                self.ts_refresh_products_table()
            else:
                self.ts_write_log(f"❌ Tải voice thất bại cho {pid}: {path}")
                QMessageBox.critical(self, "Lỗi", f"Tải voice lồng tiếng thất bại:\n{path}")
        worker.finished_signal.connect(on_done)
        worker.start()

    def ts_action_download_voice_all(self):
        url = self.ts_edit_voice_url.text().strip()
        if not url:
            QMessageBox.warning(self, "Lỗi", "Vui lòng dán link video TikTok để tải voice cho tất cả!")
            return
            
        prod_json = DOWNLOADS_DIR / "products.json"
        if not prod_json.exists():
            return
            
        try:
            products = json.loads(prod_json.read_text(encoding="utf-8"))
        except: return
        
        self.ts_write_log(f"🎵 Bắt đầu tải voice lồng tiếng hàng loạt cho {len(products)} sản phẩm...")
        self._dl_queue = products.copy()
        
        def process_next_dl():
            if not self._dl_queue:
                self.ts_write_log("✅ Hoàn thành tải voice lồng tiếng hàng loạt.")
                self.ts_refresh_products_table()
                return
            p = self._dl_queue.pop(0)
            pid = p.get("product_id")
            out_dir = DOWNLOADS_DIR / "voices" / pid
            out_dir.mkdir(parents=True, exist_ok=True)
            
            worker = VoiceDownloadWorker(url, str(out_dir))
            def on_done(success, path):
                if success:
                    self.ts_write_log(f"✅ Tải voice thành công cho {pid}: {path}")
                    try:
                        prods = json.loads(prod_json.read_text(encoding="utf-8"))
                        for pr in prods:
                            if pr.get("product_id") == pid:
                                if "voices" not in pr: pr["voices"] = []
                                rel_path = str(Path(path).relative_to(DOWNLOADS_DIR.parent)) if Path(path).is_relative_to(DOWNLOADS_DIR.parent) else path
                                if rel_path not in pr["voices"]:
                                    pr["voices"].append(rel_path)
                                break
                        prod_json.write_text(json.dumps(prods, ensure_ascii=False, indent=2), encoding="utf-8")
                    except: pass
                else:
                    self.ts_write_log(f"❌ Tải voice thất bại cho {pid}")
                process_next_dl()
            worker.finished_signal.connect(on_done)
            worker.start()
            
        process_next_dl()

    def _open_tiktok_dialog_in_studio(self):
        if not self.active_project:
            self._create_project_dialog()
        if not self.active_project:
            return
        dlg = TiktokShopSelectDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            sel_img = dlg.selected_image_path
            sel_title = dlg.selected_title
            sel_desc = dlg.selected_description
            sel_voices = dlg.selected_voices
            if sel_img:
                self._open_tiktokshop_product_in_project(sel_img, sel_title, sel_desc, sel_voices)
            elif sel_title:
                prod = ProductItem(title=sel_title, info=sel_desc)
                self.active_project.products.append(prod)
                self._save_projects()
                self._render_products()
                self._log(f"➕ Đã thêm sản phẩm TiktokShop: {sel_title[:30]}")

    def ts_action_add_to_current_project(self):
        row = self.ts_table_products.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Chưa chọn", "Vui lòng chọn sản phẩm trong bảng!")
            return
            
        if not self.active_project:
            self._create_project_dialog()
        if not self.active_project:
            return
            
        dlg = TiktokShopSelectDialog(self)
        pid = self.ts_table_products.item(row, 0).text().strip()
        for r in range(dlg.table.rowCount()):
            if dlg.table.item(r, 0).text() == pid:
                dlg.table.selectRow(r)
                break
                
        if dlg.exec() == QDialog.DialogCode.Accepted:
            sel_img = dlg.selected_image_path
            sel_title = dlg.selected_title
            sel_desc = dlg.selected_description
            sel_voices = dlg.selected_voices
            if sel_img:
                self._open_tiktokshop_product_in_project(sel_img, sel_title, sel_desc, sel_voices)
            elif sel_title:
                prod = ProductItem(title=sel_title, info=sel_desc)
                self.active_project.products.append(prod)
                self._save_projects()
                self._render_products()
                self._log(f"➕ Đã thêm sản phẩm TiktokShop (không ảnh): {sel_title[:30]}")

    def _open_tiktokshop_product_in_project(self, image_path: str, title: str, description: str, voice_paths: list):
        if not image_path or not Path(image_path).exists():
            QMessageBox.warning(self, "Lỗi", f"Ảnh sản phẩm không tồn tại:\n{image_path}")
            return
            
        if not self.active_project:
            QMessageBox.warning(self, "Lỗi", "Chưa có dự án đang hoạt động!")
            return
            
        def _clean_product_name(raw: str, max_words: int = 8) -> str:
            if not raw or not raw.strip(): return "product"
            words = raw.strip().split()[:max_words]
            short = " ".join(words)
            safe = re.sub(r'[\\/:*?"<>|]', '', short).strip().strip(" .")
            return safe if safe else "product"

        clean_title = _clean_product_name(title, max_words=8)
        
        voice_path = ""
        voice_dur = 0.0
        if voice_paths:
            for vp in voice_paths:
                if vp and Path(vp).exists():
                    voice_path = vp
                    voice_dur = get_media_duration(vp)
                    break

        prod_item = ProductItem(
            title=clean_title,
            info=description or title,
            images=[image_path],
            voice_path=voice_path,
            voice_duration=voice_dur,
            status="Chưa chạy"
        )
        self.active_project.products.append(prod_item)
        self._save_projects()
        self._render_products()
        self._log(f"➕ Đã thêm sản phẩm TiktokShop: {clean_title}")
        self.switch_panel("studio")

    def ts_action_start_crawl(self):
        urls_text = self.ts_txt_urls.toPlainText().strip()
        if not urls_text:
            QMessageBox.warning(self, "Thiếu liên kết", "Vui lòng dán liên kết sản phẩm cần cào!")
            return
            
        if not is_chrome_cdp_active(CDP_PORT):
            QMessageBox.critical(self, "Chrome Spy chưa mở", "Vui lòng mở Google Chrome Spy trước khi cào!")
            return
            
        urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
        self.ts_btn_start.setEnabled(False)
        self.ts_btn_stop.setEnabled(True)
        self.ts_progress_bar.setValue(0)
        self.ts_lbl_stats.setText(f"Đã cào: 0/{len(urls)} | Thành công: 0 | Thất bại: 0")
        
        self.ts_worker = CrawlWorker(urls, thread_count=self.ts_spin_threads.value())
        self.ts_worker.log_signal.connect(self.ts_write_log)
        self.ts_worker.progress_signal.connect(self.ts_on_crawl_progress)
        self.ts_worker.product_done_signal.connect(self.ts_on_product_done)
        self.ts_worker.finished_signal.connect(self.ts_on_crawl_finished)
        self.ts_worker.start()

    def ts_action_stop_crawl(self):
        if self.ts_worker and self.ts_worker.isRunning():
            self.ts_worker.stop()
            self.ts_write_log("🛑 Đang gửi yêu cầu dừng cào...")

    def ts_on_crawl_progress(self, current, total):
        self.ts_progress_bar.setValue(int(current / total * 100) if total > 0 else 0)
        success = getattr(self.ts_worker, 'success_count', 0) if hasattr(self, 'ts_worker') and self.ts_worker else 0
        failed = getattr(self.ts_worker, 'failed_count', 0) if hasattr(self, 'ts_worker') and self.ts_worker else 0
        self.ts_lbl_stats.setText(f"Đã cào: {current}/{total} | Thành công: {success} | Thất bại: {failed}")

    def ts_on_product_done(self, product_data):
        title = product_data.get('title', product_data.get('url', 'N/A')) if isinstance(product_data, dict) else str(product_data)
        status = product_data.get('status', product_data.get('crawl_status', 'OK')) if isinstance(product_data, dict) else 'OK'
        self.ts_write_log(f"📦 [Done] Sản phẩm: {title} -> Trạng thái: {status}")
        self.ts_refresh_products_table()

    def ts_on_crawl_finished(self, success_count, total_count):
        self.ts_btn_start.setEnabled(True)
        self.ts_btn_stop.setEnabled(False)
        self.ts_write_log(f"🎉 Hoàn thành cào sản phẩm! Thành công: {success_count}/{total_count}")
        self.ts_refresh_folder_list()
        self.ts_refresh_products_table()
        QMessageBox.information(self, "Hoàn thành", f"Đã cào xong danh sách sản phẩm!\nThành công: {success_count}/{total_count}")

    def ts_refresh_products_table(self):
        self.ts_table_products.setRowCount(0)
        prod_json = DOWNLOADS_DIR / "products.json"
        if not prod_json.exists():
            return
            
        try:
            products = json.loads(prod_json.read_text(encoding="utf-8"))
        except: return
        
        selected_folder = self.ts_combo_folder.currentText() if hasattr(self, "ts_combo_folder") and self.ts_combo_folder.count() > 0 else "📁 Tất cả thư mục"
        
        filtered = []
        for p in products:
            folder = p.get("folder", "Chưa phân loại")
            if selected_folder == "📁 Tất cả thư mục":
                filtered.append(p)
            elif selected_folder == "📁 Chưa phân loại" and (not folder or folder == "Chưa phân loại"):
                filtered.append(p)
            elif selected_folder == f"📁 {folder}":
                filtered.append(p)
                
        self.ts_table_products.setRowCount(len(filtered))
        for r, p in enumerate(filtered):
            self.ts_table_products.setItem(r, 0, QTableWidgetItem(p.get("product_id", "")))
            self.ts_table_products.setItem(r, 1, QTableWidgetItem(p.get("title", "")))
            self.ts_table_products.setItem(r, 2, QTableWidgetItem(p.get("price", "")))
            self.ts_table_products.setItem(r, 3, QTableWidgetItem(p.get("shop_name", "")))
            
            status = p.get("crawl_status", "success")
            status_item = QTableWidgetItem("Thành công" if status == "success" else f"Lỗi ({status})")
            status_item.setForeground(QColor("#16a34a") if status == "success" else QColor("#ef4444"))
            self.ts_table_products.setItem(r, 4, status_item)
            
            voices = p.get("voices", [])
            voice_item = QTableWidgetItem(f"✅ {len(voices)} files" if voices else "—")
            voice_item.setForeground(QColor("#06b6d4") if voices else QColor("#64748b"))
            self.ts_table_products.setItem(r, 5, voice_item)

    def ts_refresh_folder_list(self):
        current_folder = self.ts_combo_folder.currentText() if hasattr(self, "ts_combo_folder") and self.ts_combo_folder.count() > 0 else "📁 Tất cả thư mục"
        folders = set()
        prod_json = DOWNLOADS_DIR / "products.json"
        if prod_json.exists():
            try:
                products = json.loads(prod_json.read_text(encoding="utf-8"))
                for p in products:
                    f = p.get("folder", "Chưa phân loại")
                    if f: folders.add(f)
            except: pass
            
        folder_list = sorted(list(folders))
        if "Chưa phân loại" in folder_list:
            folder_list.remove("Chưa phân loại")
            
        display_list = ["📁 Tất cả thư mục", "📁 Chưa phân loại"] + [f"📁 {f}" for f in folder_list]
        self.ts_combo_folder.blockSignals(True)
        self.ts_combo_folder.clear()
        self.ts_combo_folder.addItems(display_list)
        idx = self.ts_combo_folder.findText(current_folder)
        if idx >= 0: self.ts_combo_folder.setCurrentIndex(idx)
        else: self.ts_combo_folder.setCurrentIndex(0)
        self.ts_combo_folder.blockSignals(False)

    def ts_action_move_folder(self):
        selected_rows = [index.row() for index in self.ts_table_products.selectionModel().selectedRows()]
        if not selected_rows:
            QMessageBox.warning(self, "Chưa chọn", "Vui lòng chọn các sản phẩm cần phân loại!")
            return
            
        folders = set()
        prod_json = DOWNLOADS_DIR / "products.json"
        if prod_json.exists():
            try:
                products = json.loads(prod_json.read_text(encoding="utf-8"))
                for p in products:
                    f = p.get("folder", "Chưa phân loại")
                    if f: folders.add(f)
            except: pass
            
        folder_list = sorted(list(folders))
        if "Chưa phân loại" in folder_list:
            folder_list.remove("Chưa phân loại")
        folder_list = ["Chưa phân loại"] + folder_list + ["➕ Tạo thư mục mới..."]
        
        item, ok = QInputDialog.getItem(self, "Phân loại sản phẩm", "Chọn thư mục lưu trữ:", folder_list, 0, False)
        if not ok: return
        
        target_folder = item
        if item == "➕ Tạo thư mục mới...":
            new_name, ok = QInputDialog.getText(self, "Thư mục mới", "Nhập tên thư mục phân loại mới:")
            if not ok or not new_name.strip(): return
            target_folder = new_name.strip()
            
        if prod_json.exists():
            try:
                products = json.loads(prod_json.read_text(encoding="utf-8"))
                selected_pids = [self.ts_table_products.item(r, 0).text() for r in selected_rows if self.ts_table_products.item(r, 0)]
                
                moved_count = 0
                for p in products:
                    if p.get("product_id") in selected_pids:
                        old_folder = p.get("folder", "Chưa phân loại")
                        p["folder"] = target_folder
                        safe_title = clean_filename(p.get("title", "product"))
                        pid = p.get("product_id")
                        
                        if old_folder and old_folder != "Chưa phân loại":
                            old_dir = DOWNLOADS_DIR / old_folder / f"{pid}_{safe_title}"
                        else:
                            old_dir = DOWNLOADS_DIR / f"{pid}_{safe_title}"
                            
                        if target_folder and target_folder != "Chưa phân loại":
                            new_dir = DOWNLOADS_DIR / target_folder / f"{pid}_{safe_title}"
                        else:
                            new_dir = DOWNLOADS_DIR / f"{pid}_{safe_title}"
                            
                        if old_dir.exists() and old_dir.resolve() != new_dir.resolve():
                            new_dir.parent.mkdir(parents=True, exist_ok=True)
                            try:
                                import shutil
                                shutil.move(str(old_dir), str(new_dir))
                                if p.get("local_images"):
                                    p["local_images"] = [x.replace(str(old_dir), str(new_dir)) for x in p["local_images"]]
                                if p.get("voices"):
                                    p["voices"] = [x.replace(str(old_dir), str(new_dir)) for x in p["voices"]]
                            except Exception as ex:
                                self.ts_write_log(f"⚠️ Không thể di chuyển thư mục: {ex}")
                        moved_count += 1
                        
                prod_json.write_text(json.dumps(products, ensure_ascii=False, indent=2), encoding="utf-8")
                self.ts_refresh_folder_list()
                self.ts_refresh_products_table()
                self.ts_write_log(f"📁 Đã chuyển {moved_count} sản phẩm vào: {target_folder}")
            except Exception as e:
                QMessageBox.critical(self, "Lỗi", f"Không thể lưu phân loại: {e}")

    def ts_open_product_folder(self):
        row = self.ts_table_products.currentRow()
        if row < 0:
            os.startfile(DOWNLOADS_DIR)
            return
            
        pid_item = self.ts_table_products.item(row, 0)
        title_item = self.ts_table_products.item(row, 1)
        if not pid_item:
            os.startfile(DOWNLOADS_DIR)
            return
            
        pid = pid_item.text().strip()
        title = title_item.text().strip() if title_item else ""
        if "  [" in title:
            title = title.split("  [")[0].strip()
            
        folder_name = "Chưa phân loại"
        prod_json = DOWNLOADS_DIR / "products.json"
        if prod_json.exists():
            try:
                products = json.loads(prod_json.read_text(encoding="utf-8"))
                prod = next((p for p in products if p.get("product_id") == pid), None)
                if prod: folder_name = prod.get("folder", "Chưa phân loại")
            except: pass
            
        safe_title = clean_filename(title)
        if folder_name and folder_name != "Chưa phân loại":
            prod_dir = DOWNLOADS_DIR / folder_name / f"{pid}_{safe_title}"
        else:
            prod_dir = DOWNLOADS_DIR / f"{pid}_{safe_title}"
            
        if not prod_dir.exists():
            found_dir = None
            try:
                search_dirs = [DOWNLOADS_DIR]
                if folder_name and folder_name != "Chưa phân loại":
                    search_dirs.insert(0, DOWNLOADS_DIR / folder_name)
                for s_dir in search_dirs:
                    if s_dir.exists():
                        for entry in s_dir.iterdir():
                            if entry.is_dir() and entry.name.startswith(pid):
                                found_dir = entry
                                break
                        if found_dir: break
            except: pass
            if found_dir: prod_dir = found_dir
            else: prod_dir = DOWNLOADS_DIR
            
        os.startfile(str(prod_dir))

    def ts_write_log(self, msg):
        self.ts_txt_logs.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
        self.ts_txt_logs.moveCursor(self.ts_txt_logs.textCursor().MoveOperation.End)

    # ── PAGE 3: ACCOUNTS ──────────────────────────────────────────────
    def _build_accs_panel(self):
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(20, 15, 20, 15)

        h_title = QLabel("👥  Account Manager (Tài khoản)")
        h_title.setFont(QFont("Segoe UI", 20, QFont.Weight.Bold))
        lay.addWidget(h_title)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setStyleSheet("QSplitter::handle { background-color: #cbd5e1; width: 1px; }")

        left_box = QFrame()
        left_box.setObjectName("card")
        l_lay = QVBoxLayout(left_box)
        
        oauth_f = QFrame()
        oauth_f.setStyleSheet("background-color: #7c3aed; border-radius: 8px; padding: 10px;")
        oa_lay = QVBoxLayout(oauth_f)
        
        lbl_oauth_title = QLabel("🔑 ĐĂNG NHẬP GROK / CODEX QUA CLIPROXY")
        lbl_oauth_title.setStyleSheet("color: white; font-weight: bold; font-size: 13px; background: transparent;")
        oa_lay.addWidget(lbl_oauth_title)
        
        btn_lay = QHBoxLayout()
        oa_btn = QPushButton("🔑  Login Qua Trình Duyệt")
        oa_btn.setStyleSheet("background-color: #4c1d95; color: white; font-weight: bold; border-radius: 6px; padding: 8px 12px;")
        btn_lay.addWidget(oa_btn)

        admin_btn = QPushButton("🌐  Mở Web Admin")
        admin_btn.setStyleSheet("background-color: #ffffff; color: #7c3aed; font-weight: bold; border-radius: 6px; padding: 8px 12px;")
        
        def open_admin_web():
            import webbrowser
            webbrowser.open("http://127.0.0.1:8317/management.html")
            
        admin_btn.clicked.connect(open_admin_web)
        btn_lay.addWidget(admin_btn)
        oa_lay.addLayout(btn_lay)
        
        self.accs_oauth_status = QLabel("Mở trình duyệt sạch tự động dọn cache.")
        self.accs_oauth_status.setStyleSheet("color: #e2d6ff; font-size: 11px; background: transparent;")
        
        def run_oauth_tab():
            items = ["xAI Grok (SuperGrok)", "OpenAI Codex (ChatGPT Plus)", "Google Flow / Veo 3"]
            item, ok = QInputDialog.getItem(self, "Đăng Nhập Tài Khoản", "Chọn loại tài khoản đăng nhập:", items, 0, False)
            if not ok or not item: return
                
            login_type = item
            if "Google Flow" in login_type:
                prof_name, ok2 = QInputDialog.getText(self, "Đăng Nhập Google", "Nhập tên cấu hình trình duyệt (Viết liền không dấu):")
                if not ok2 or not prof_name.strip(): return
                prof_name = prof_name.strip()
                self.accs_oauth_status.setText("🔍 Đang khởi chạy trình duyệt Google...")
                self.accs_oauth_status.setStyleSheet("color: #7c3aed; font-weight: bold;")
                
                self.tab_google_login_worker = GoogleLoginWorker(profile_name=prof_name)
                self.tab_google_login_worker.status_sig.connect(lambda t, c: (self.accs_oauth_status.setText(t), self.accs_oauth_status.setStyleSheet(f"color: {c}; font-weight: bold; background: transparent;")))
                
                def on_google_success(email, token, proj_id):
                    acc_name = f"Google_{email}"
                    exists = False
                    for a in self.acc_mgr.accounts:
                        if a.name == acc_name:
                            a.api_key = token
                            a.proxy_url = proj_id
                            a.active = True
                            exists = True
                            break
                    if not exists:
                        self.acc_mgr.accounts.append(Account(name=acc_name, proxy_url=proj_id, api_key=token, active=True))
                    self.acc_mgr.save()
                    
                    try:
                        kol_dir = CURRENT_DIR.parent / "KOL Veo 3"
                        shared_dir = (kol_dir / "Flow_nhieu_worker_data") if kol_dir.exists() else (CURRENT_DIR / "Flow_nhieu_worker_data")
                        shared_dir.mkdir(parents=True, exist_ok=True)
                        
                        kol_acc_file = shared_dir / "Flow_nhieu_worker_accounts.json"
                        kol_accounts = []
                        if kol_acc_file.exists():
                            try:
                                kol_accounts = json.loads(kol_acc_file.read_text("utf-8"))
                            except: pass
                                
                        p_dir = str(shared_dir / "profiles" / prof_name)
                        c_file = str(shared_dir / "cookies" / f"{prof_name}.json")
                        k_exists = False
                        for ka in kol_accounts:
                            if ka.get("email") == email or ka.get("email") == prof_name:
                                ka["email"] = email
                                ka["profile_dir"] = p_dir
                                ka["cookies_file"] = c_file
                                ka["project_id"] = proj_id
                                ka["status"] = "ready"
                                ka["last_login"] = datetime.now().isoformat()
                                ka["is_grok"] = False
                                k_exists = True
                                break
                        if not k_exists:
                            kol_accounts.append({
                                "email": email, "profile_dir": p_dir, "cookies_file": c_file, "project_id": proj_id,
                                "total_generated": 0, "last_login": datetime.now().isoformat(), "status": "ready",
                                "total_credits_used": 0, "monthly_credits_used": 0, "credit_limit": 25000,
                                "credits_remaining": 25000, "credits_last_updated": "", "is_grok": False,
                                "grok_proxy_url": "http://localhost:8317", "grok_api_key": "grok-video-key-001"
                            })
                        kol_acc_file.write_text(json.dumps(kol_accounts, indent=2, ensure_ascii=False), "utf-8")
                    except: pass
                    
                    self.acc_mgr.load()
                    self.draw_tab_accounts_list()
                    self.refresh_sidebar_acc_lbl()
                    self.refresh_accounts_label_studio()
                    self.accs_oauth_status.setText(f"✅ Đăng nhập thành công Google: {email}!")
                    self.accs_oauth_status.setStyleSheet("color: #16a34a; font-weight: bold; background: transparent;")
                    
                self.tab_google_login_worker.success_sig.connect(on_google_success)
                self.tab_google_login_worker.error_sig.connect(lambda err: (self.accs_oauth_status.setText(f"❌ Lỗi: {err}"), self.accs_oauth_status.setStyleSheet("color: #ef4444; font-weight: bold; background: transparent;")))
                self.tab_google_login_worker.start()
                return

            if not CLIPROXY_EXE.exists():
                QMessageBox.critical(self, "Lỗi", f"Không tìm thấy file CLIProxy tại:\n{CLIPROXY_EXE}")
                return
            self.accs_oauth_status.setText("🔍 Đang chuẩn bị cổng kết nối...")
            self.tab_login_worker = LoginWorker(login_type=login_type)
            self.tab_login_worker.status_sig.connect(lambda t, c: (self.accs_oauth_status.setText(t), self.accs_oauth_status.setStyleSheet(f"color: {c}; font-weight: bold; background: transparent;")))
            
            def on_oauth_success(info):
                if "|" in info: login_type, email = info.split("|", 1)
                else: login_type, email = "xAI Grok", info
                acc_prefix = "Codex" if "Codex" in login_type else "Grok"
                acc_name = f"{acc_prefix} - {email}"
                existing = [a.name for a in self.acc_mgr.accounts]
                if acc_name not in existing:
                    self.acc_mgr.add(acc_name, DEFAULT_PROXY_URL, "grok-video-key-001")
                self.acc_mgr.load()
                self.draw_tab_accounts_list()
                self.refresh_sidebar_acc_lbl()
                self.refresh_accounts_label_studio()
            self.tab_login_worker.success_sig.connect(on_oauth_success)
            self.tab_login_worker.start()

        oa_btn.clicked.connect(run_oauth_tab)
        oa_lay.addWidget(oa_btn)

        sync_btn = QPushButton("🔄  Đồng Bộ CLIproxy")
        sync_btn.setStyleSheet("background-color: #0d9488; color: white; font-weight: bold; border-radius: 6px; padding: 8px 12px;")

        def run_sync_tab():
            added, removed = self.acc_mgr.sync_from_cliproxy()
            self.acc_mgr.load()
            self.draw_tab_accounts_list()
            self.refresh_sidebar_acc_lbl()
            self.refresh_accounts_label_studio()
            QMessageBox.information(
                self, "Đồng Bộ Thành Công", 
                f"Đã đồng bộ xong danh sách tài khoản từ CLIproxy!\n\n"
                f"➕ Thêm mới: {added} tài khoản\n"
                f"🗑️ Đã xóa (hết hạn/đã xóa trên admin): {removed} tài khoản"
            )
        sync_btn.clicked.connect(run_sync_tab)
        oa_lay.addWidget(sync_btn)
        oa_lay.addWidget(self.accs_oauth_status)
        l_lay.addWidget(oauth_f)

        l_lay.addWidget(QLabel("➕ Thêm Thủ Công", styleSheet="font-weight: bold; margin-top: 10px; color: #7c3aed;"))
        self.acc_type_combo = QComboBox()
        self.acc_type_combo.addItems(["xAI Grok / OpenAI Codex", "Google Flow / Sandbox (Token)"])
        self.acc_type_combo.setStyleSheet("QComboBox { background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 4px; font-size: 11px; color: #1e293b; }")
        l_lay.addWidget(self.acc_type_combo)

        ne = QLineEdit(placeholderText="Tên / Email tài khoản...")
        l_lay.addWidget(ne)
        ue = QLineEdit(placeholderText="Proxy URL hoặc GCP Project ID...")
        l_lay.addWidget(ue)
        ke = QLineEdit(placeholderText="API Key hoặc Bearer Token...", echoMode=QLineEdit.EchoMode.Password)
        l_lay.addWidget(ke)

        def add():
            n = ne.text().strip()
            u = ue.text().strip()
            k = ke.text().strip()
            if not n or not k:
                QMessageBox.warning(self, "⚠️", "Tên và API Key/Token bắt buộc!")
                return
            is_google = self.acc_type_combo.currentIndex() == 1
            if is_google:
                if not n.startswith("Google_"): n = f"Google_{n}"
                if not u: u = DEFAULT_PROJECT_ID
            else:
                if not u: u = DEFAULT_PROXY_URL
            self.acc_mgr.add(n, u, k)
            ne.clear(); ue.clear(); ke.clear()
            self.acc_mgr.load()
            self.draw_tab_accounts_list()
            self.refresh_sidebar_acc_lbl()
            self.refresh_accounts_label_studio()

        add_b = QPushButton("➕  Thêm")
        add_b.setObjectName("accent_btn")
        add_b.clicked.connect(add)
        l_lay.addWidget(add_b)
        l_lay.addStretch()
        split.addWidget(left_box)

        right_box = QFrame()
        right_box.setObjectName("card")
        r_box_lay = QVBoxLayout(right_box)
        r_box_lay.addWidget(QLabel("📋 DANH SÁCH TÀI KHOẢN", styleSheet="font-weight: bold;"))

        self.tab_accs_scroll = QScrollArea()
        self.tab_accs_scroll.setWidgetResizable(True)
        self.tab_accs_scroll_content = QWidget()
        self.tab_accs_scroll_lay = QVBoxLayout(self.tab_accs_scroll_content)
        self.tab_accs_scroll_lay.setSpacing(5)
        self.tab_accs_scroll_lay.setContentsMargins(0, 0, 0, 0)
        self.tab_accs_scroll.setWidget(self.tab_accs_scroll_content)
        r_box_lay.addWidget(self.tab_accs_scroll)

        split.addWidget(right_box)
        split.setSizes([280, 360])
        lay.addWidget(split, stretch=1)

        self.draw_tab_accounts_list()
        self.stacked.addWidget(panel)

    def draw_tab_accounts_list(self):
        for i in reversed(range(self.tab_accs_scroll_lay.count())):
            widget = self.tab_accs_scroll_lay.itemAt(i).widget()
            if widget: widget.setParent(None)

        if not self.acc_mgr.accounts:
            lbl = QLabel("Chưa có tài khoản nào.", styleSheet="color: #94a3b8; font-style: italic; text-align: center;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tab_accs_scroll_lay.addWidget(lbl)
            return

        google_accs = [a for a in self.acc_mgr.accounts if a.name.startswith("Google") or "google" in a.name.lower()]
        if google_accs:
            bulk_row = QFrame()
            bulk_row.setStyleSheet("background-color: #dbeafe; border-radius: 6px; padding: 6px;")
            bulk_lay = QHBoxLayout(bulk_row)
            bulk_lay.setContentsMargins(10, 4, 10, 4)
            
            bulk_info = QLabel(f"🌐 {len(google_accs)} tài khoản Google", styleSheet="font-weight: bold; color: #1e40af; font-size: 12px; background: transparent;")
            bulk_lay.addWidget(bulk_info, stretch=1)
            
            bulk_refresh = QPushButton("🔍 Kiểm tra tất cả Token")
            bulk_refresh.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 6px 12px; border-radius: 4px;")
            
            def do_bulk_refresh():
                g_accs = [a for a in self.acc_mgr.accounts if a.active and (a.name.startswith("Google") or "google" in a.name.lower())]
                if not g_accs:
                    QMessageBox.warning(self, "⚠️", "Không có tài khoản Google nào đang hoạt động!")
                    return
                bulk_refresh.setEnabled(False)
                bulk_refresh.setText("⏳ Đang kiểm tra...")
                self._bulk_refresh_worker = GoogleTokenRefreshWorker(g_accs)
                self._bulk_refresh_worker.token_updated.connect(self._on_google_token_updated)
                def on_done(valid, expired):
                    try:
                        bulk_refresh.setEnabled(True)
                        bulk_refresh.setText("🔍 Kiểm tra tất cả Token")
                    except: pass
                    
                    try:
                        self.draw_tab_accounts_list()
                        self.refresh_sidebar_acc_lbl()
                    except: pass

                    if expired > 0:
                        QMessageBox.warning(self, "Kiểm tra Token", f"✅ Còn hiệu lực: {valid}\n⚠️ Hết hạn: {expired}\n\nNhấn 🔑 để login lại các tài khoản hết hạn.")
                    else:
                        QMessageBox.information(self, "Kiểm tra Token", f"✅ Tất cả {valid} token đều còn hiệu lực!")
                self._bulk_refresh_worker.finished_all.connect(on_done)
                self._bulk_refresh_worker.start()
            
            bulk_refresh.clicked.connect(do_bulk_refresh)
            bulk_lay.addWidget(bulk_refresh)
            self.tab_accs_scroll_lay.addWidget(bulk_row)

        for idx, acc in enumerate(self.acc_mgr.accounts):
            row = QFrame()
            row.setObjectName("acc_row")
            is_google = acc.name.startswith("Google") or "google" in acc.name.lower()
            bg_color = "#ede9fe" if is_google else "#f1f5f9"
            row.setStyleSheet(f"QFrame#acc_row {{ background-color: {bg_color}; border-radius: 6px; padding: 4px; }}")
            r_lay = QHBoxLayout(row)
            r_lay.setContentsMargins(10, 4, 10, 4)

            type_icon = "🌐" if is_google else ("🤖" if "Grok" in acc.name else "💻")
            token_preview = f"{acc.api_key[:20]}..." if len(acc.api_key) > 20 else acc.api_key
            
            credits_str = ""
            if is_google:
                cr_val = getattr(acc, "credits", None)
                cr_txt = f"{cr_val} cr" if cr_val is not None else "--"
                tag_val = getattr(acc, "tag", "Pro")
                credits_str = f"  │  🏷️ Tag: {tag_val}  │  💰 Credits: {cr_txt}"
                
            name_lbl = QLabel(f"{type_icon}  {acc.name}\n🔗  {acc.proxy_url}  │  Done: {acc.jobs_done}{credits_str}\n🔑  Token: {token_preview}", styleSheet="font-size: 11px; background: transparent;")
            r_lay.addWidget(name_lbl, stretch=1)

            if is_google:
                tag_combo = QComboBox()
                tag_combo.addItems(["Pro", "Ultra"])
                tag_combo.setCurrentText(getattr(acc, "tag", "Pro"))
                tag_combo.setStyleSheet("QComboBox { background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; font-size: 10px; min-width: 60px; color: #1e293b; }")
                def make_tag_change(i=idx, combo=tag_combo):
                    def handler():
                        self.acc_mgr.accounts[i].tag = combo.currentText()
                        self.acc_mgr.save()
                        self.draw_tab_accounts_list()
                    return handler
                tag_combo.currentTextChanged.connect(make_tag_change(idx, tag_combo))
                r_lay.addWidget(tag_combo)

                refresh_btn = QPushButton("🔍")
                refresh_btn.setToolTip("Kiểm tra Token & Đồng bộ Credits")
                refresh_btn.setStyleSheet("background-color: #2563eb; color: white; padding: 4px; border-radius: 4px; min-width: 25px; font-weight: bold;")
                
                def make_refresh(i=idx, a=acc):
                    def handler():
                        profile_name = a.name.replace("Google_", "", 1)
                        self._single_refresh = GoogleSingleRefreshWorker(a, profile_name)
                        self._single_refresh.token_updated.connect(self._on_google_token_updated)
                        self._single_refresh.status_sig.connect(lambda t, c: self.accs_oauth_status.setText(t) if hasattr(self, 'accs_oauth_status') else None)
                        self._single_refresh.finished.connect(lambda: (self.draw_tab_accounts_list(), self.refresh_sidebar_acc_lbl()))
                        self._single_refresh.start()
                    return handler
                
                refresh_btn.clicked.connect(make_refresh(idx, acc))
                r_lay.addWidget(refresh_btn)

                login_btn = QPushButton("🔑")
                login_btn.setToolTip("Login lại (mở trình duyệt)")
                login_btn.setStyleSheet("background-color: #7c3aed; color: white; padding: 4px; border-radius: 4px; min-width: 25px; font-weight: bold;")
                
                def make_relogin(i=idx, a=acc):
                    def handler():
                        profile_name = a.name.replace("Google_", "", 1)
                        self.accs_oauth_status.setText(f"🔍 Đang mở trình duyệt cho {profile_name}...")
                        self.accs_oauth_status.setStyleSheet("color: #7c3aed; font-weight: bold; background: transparent;")
                        self.tab_google_login_worker = GoogleLoginWorker(profile_name=profile_name)
                        self.tab_google_login_worker.status_sig.connect(lambda t, c: (self.accs_oauth_status.setText(t), self.accs_oauth_status.setStyleSheet(f"color: {c}; font-weight: bold; background: transparent;")))
                        
                        def on_success(email, token, proj_id):
                            for acc2 in self.acc_mgr.accounts:
                                if acc2.name == a.name:
                                    acc2.api_key = token
                                    if proj_id: acc2.proxy_url = proj_id
                                    acc2.active = True
                                    break
                            self.acc_mgr.save()
                            self.draw_tab_accounts_list()
                            self.refresh_sidebar_acc_lbl()
                            self.accs_oauth_status.setText(f"✅ Đã login lại: {email}")
                            self.accs_oauth_status.setStyleSheet("color: #16a34a; font-weight: bold; background: transparent;")
                        
                        self.tab_google_login_worker.success_sig.connect(on_success)
                        self.tab_google_login_worker.error_sig.connect(lambda err: (self.accs_oauth_status.setText(f"❌ Lỗi: {err}"), self.accs_oauth_status.setStyleSheet("color: #ef4444; font-weight: bold; background: transparent;")))
                        self.tab_google_login_worker.start()
                    return handler
                
                login_btn.clicked.connect(make_relogin(idx, acc))
                r_lay.addWidget(login_btn)

            cb = QCheckBox("Bật" if acc.active else "Tắt")
            cb.setChecked(acc.active)
            
            def make_toggle(i=idx, c=cb):
                def handler():
                    self.acc_mgr.toggle(i)
                    c.setText("Bật" if self.acc_mgr.accounts[i].active else "Tắt")
                    self.refresh_sidebar_acc_lbl()
                    self.refresh_accounts_label_studio()
                return handler
            
            cb.stateChanged.connect(make_toggle(idx, cb))
            r_lay.addWidget(cb)

            del_btn = QPushButton("🗑️")
            del_btn.setStyleSheet("background-color: #ef4444; color: white; padding: 4px; border-radius: 4px; min-width: 25px; font-weight: bold;")
            
            def make_delete(i=idx):
                def handler():
                    if QMessageBox.question(self, "Xác nhận", f"Xóa tài khoản '{self.acc_mgr.accounts[i].name}'?") == QMessageBox.StandardButton.Yes:
                        acc_name = self.acc_mgr.accounts[i].name
                        self.acc_mgr.remove(i)
                        self.acc_mgr.load()
                        self.draw_tab_accounts_list()
                        self.refresh_sidebar_acc_lbl()
                        self.refresh_accounts_label_studio()
                        
                        try:
                            import shutil
                            profile_name = acc_name.replace("Google_", "", 1) if acc_name.startswith("Google_") else acc_name
                            kol_dir = CURRENT_DIR.parent / "KOL Veo 3"
                            shared_dir = (kol_dir / "Flow_nhieu_worker_data") if kol_dir.exists() else (CURRENT_DIR / "Flow_nhieu_worker_data")
                            p_dir = shared_dir / "profiles" / profile_name
                            if p_dir.exists(): shutil.rmtree(p_dir, ignore_errors=True)
                            c_file = shared_dir / "cookies" / f"{profile_name}.json"
                            if c_file.exists(): c_file.unlink(missing_ok=True)
                        except: pass
                return handler
            
            del_btn.clicked.connect(make_delete(idx))
            r_lay.addWidget(del_btn)
            self.tab_accs_scroll_lay.addWidget(row)
            
        self.tab_accs_scroll_lay.addStretch()

    def refresh_sidebar_acc_lbl(self):
        n = len([a for a in self.acc_mgr.accounts if a.active])
        if hasattr(self, 'sb_acc_lbl'):
            self.sb_acc_lbl.setText(f"👥  {n} tài khoản hoạt động")
            self.sb_acc_lbl.setStyleSheet(f"font-size:10px;font-weight:bold;padding:4px;color:{'#16a34a' if n else '#dc2626'};")

    def refresh_accounts_label_studio(self):
        pass

    def load_veo_config(self):
        # Try parent Grok tool first, then local
        config_path = GROK_DIR / "veo3_config.json"
        if not config_path.exists():
            config_path = CURRENT_DIR / "veo3_config.json"
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text("utf-8"))
                self.veo_bearer = data.get("bearer_token", "")
                self.veo_project_id = data.get("project_id", "")
                self.veo_bridge_port = data.get("bridge_port", 4000)
                self.veo_jitter_min_ms = data.get("jitter_min_ms", 2000)
                self.veo_jitter_max_ms = data.get("jitter_max_ms", 5000)
            except Exception:
                self.veo_bearer = ""
                self.veo_project_id = ""
                self.veo_bridge_port = 4000
                self.veo_jitter_min_ms = 2000
                self.veo_jitter_max_ms = 5000
        else:
            self.veo_bearer = ""
            self.veo_project_id = ""
            self.veo_bridge_port = 4000
            self.veo_jitter_min_ms = 2000
            self.veo_jitter_max_ms = 5000

        secret_path = os.path.join(os.environ.get("APPDATA", ""), "veo3studio", "captcha-bridge.secret")
        if os.path.exists(secret_path):
            try:
                with open(secret_path, "r", encoding="utf-8") as f:
                    self.veo_secret = f.read().strip()
            except Exception:
                self.veo_secret = ""
        else:
            try:
                os.makedirs(os.path.dirname(secret_path), exist_ok=True)
                self.veo_secret = uuid.uuid4().hex
                with open(secret_path, "w", encoding="utf-8") as f:
                    f.write(self.veo_secret)
            except Exception:
                self.veo_secret = ""

        # Auto start captcha bridge
        self.start_captcha_bridge_if_needed()

    def start_captcha_bridge_if_needed(self):
        if has_grok_deps and not getattr(self, "captcha_bridge", None):
            try:
                self.captcha_bridge = CaptchaBridge(self._log)
                self.captcha_bridge.start()
                self._log("✔ Khởi động Captcha Bridge Server thành công!")
            except Exception as e:
                self._log(f"⚠️ Lỗi khởi động Captcha Bridge Server: {e}")

    def closeEvent(self, event):
        self._save_projects_now()
        if getattr(self, "captcha_bridge", None):
            try: self.captcha_bridge.stop()
            except: pass
        if getattr(self, "proxy_process", None):
            try: self.proxy_process.terminate()
            except: pass
        event.accept()

    def save_veo_config(self):
        data = {
            "bearer_token": self.veo_bearer,
            "project_id": self.veo_project_id,
            "bridge_port": self.veo_bridge_port,
            "jitter_min_ms": getattr(self, "veo_jitter_min_ms", 2000),
            "jitter_max_ms": getattr(self, "veo_jitter_max_ms", 5000)
        }
        # Write to both parent Grok and local to keep in sync
        for cp in [GROK_DIR / "veo3_config.json", CURRENT_DIR / "veo3_config.json"]:
            try:
                cp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
            except Exception:
                pass

    def _on_google_token_updated(self, acc_name: str, token: str, project_id: str, credits_val: int = -1):
        for acc in self.acc_mgr.accounts:
            if acc.name == acc_name:
                acc.api_key = token
                if project_id: acc.proxy_url = project_id
                if credits_val >= 0: acc.credits = credits_val
                break
        self.acc_mgr.save()

        try:
            email = acc_name.replace("Google_", "", 1)
            kol_dir = CURRENT_DIR.parent / "KOL Veo 3"
            shared_dir = (kol_dir / "Flow_nhieu_worker_data") if kol_dir.exists() else (CURRENT_DIR / "Flow_nhieu_worker_data")
            kol_acc_file = shared_dir / "Flow_nhieu_worker_accounts.json"
            if kol_acc_file.exists():
                kol_accounts = json.loads(kol_acc_file.read_text("utf-8"))
                updated = False
                for ka in kol_accounts:
                    if ka.get("email") == email:
                        ka["project_id"] = project_id
                        if credits_val >= 0:
                            ka["credits_remaining"] = credits_val
                            ka["credits_last_updated"] = datetime.now().isoformat()
                        updated = True
                        break
                if updated:
                    kol_acc_file.write_text(json.dumps(kol_accounts, indent=2, ensure_ascii=False), "utf-8")
        except: pass
        
        google_accs = [a for a in self.acc_mgr.accounts if a.name.startswith("Google") and a.active]
        if google_accs and google_accs[0].name == acc_name:
            self.veo_bearer = token
            if project_id: self.veo_project_id = project_id
            self.save_veo_config()

    # ── PAGE 4: SETTINGS ──────────────────────────────────────────────
    def _build_settings_panel(self):
        panel = QWidget()
        panel.setStyleSheet("background:#f8fafc;")
        main_lay = QVBoxLayout(panel)
        main_lay.setContentsMargins(32, 24, 32, 24)
        main_lay.setSpacing(0)

        # ── Header ──
        hdr = QLabel("⚙️  Settings (Cấu Hình)")
        hdr.setStyleSheet("font-size:22px;font-weight:bold;color:#374151;margin-bottom:16px;background:transparent;")
        main_lay.addWidget(hdr)

        # ── Scroll area ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        scroll_w = QWidget()
        scroll_w.setStyleSheet("background:transparent;")
        slay = QVBoxLayout(scroll_w)
        slay.setSpacing(20)
        slay.setContentsMargins(0, 0, 8, 0)

        card_ss = """
            QFrame#sett_card {
                background-color: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 10px;
            }
        """
        label_ss = "font-size:12px;color:#374151;font-weight:500;background:transparent;"
        input_ss = "QLineEdit{font-size:12px;padding:8px 12px;border:1px solid #d1d5db;border-radius:6px;background:#ffffff;color:#111827;}QLineEdit:focus{border-color:#6366f1;}"
        spin_ss  = "QSpinBox{font-size:12px;padding:6px 10px;border:1px solid #d1d5db;border-radius:6px;background:#ffffff;color:#111827;min-width:120px;}QSpinBox:focus{border-color:#6366f1;}"
        section_title_ss = "font-size:13px;font-weight:bold;color:#4338ca;text-transform:uppercase;letter-spacing:0.5px;background:transparent;"

        # ═══════════════════════════════════════════════════════════════
        # SECTION 1: THƯ MỤC LƯU MẶC ĐỊNH
        # ═══════════════════════════════════════════════════════════════
        c1 = QFrame(); c1.setObjectName("sett_card"); c1.setStyleSheet(card_ss)
        c1l = QVBoxLayout(c1); c1l.setContentsMargins(20, 16, 20, 16); c1l.setSpacing(10)

        t1 = QLabel("📁  THƯ MỤC LƯU MẶC ĐỊNH")
        t1.setStyleSheet(section_title_ss)
        c1l.addWidget(t1)

        out_row = QHBoxLayout()
        self.sett_output_dir = QLineEdit(str(OUTPUT_DIR))
        self.sett_output_dir.setStyleSheet(input_ss)
        self.sett_output_dir.setReadOnly(True)
        out_row.addWidget(self.sett_output_dir)
        btn_browse = QPushButton("📂")
        btn_browse.setFixedSize(40, 36)
        btn_browse.setStyleSheet("QPushButton{font-size:16px;border:1px solid #d1d5db;border-radius:6px;background:#f3f4f6;}QPushButton:hover{background:#e5e7eb;}")
        btn_browse.clicked.connect(self._browse_output_dir)
        out_row.addWidget(btn_browse)
        c1l.addLayout(out_row)
        slay.addWidget(c1)

        # ═══════════════════════════════════════════════════════════════
        # SECTION 2: THÔNG SỐ XỬ LÝ HỆ THỐNG
        # ═══════════════════════════════════════════════════════════════
        c2 = QFrame(); c2.setObjectName("sett_card"); c2.setStyleSheet(card_ss)
        c2l = QVBoxLayout(c2); c2l.setContentsMargins(20, 16, 20, 16); c2l.setSpacing(12)

        t2 = QLabel("⚙️  THÔNG SỐ XỬ LÝ HỆ THỐNG")
        t2.setStyleSheet(section_title_ss)
        c2l.addWidget(t2)

        grid2 = QGridLayout(); grid2.setHorizontalSpacing(16); grid2.setVerticalSpacing(10)

        # Row 0: Tần suất kiểm tra
        lbl_poll = QLabel("⏱️ Tần suất kiểm tra (giây)")
        lbl_poll.setStyleSheet(label_ss)
        grid2.addWidget(lbl_poll, 0, 0)
        self.sett_poll_interval = QSpinBox()
        self.sett_poll_interval.setRange(1, 120)
        self.sett_poll_interval.setValue(self.settings.get("delay_sec", 10))
        self.sett_poll_interval.setStyleSheet(spin_ss)
        self.sett_poll_interval.valueChanged.connect(self._on_settings_changed)
        grid2.addWidget(self.sett_poll_interval, 0, 1)

        # Row 1: Thời gian chờ tối đa
        lbl_wait = QLabel("⏳ Thời gian chờ tối đa (giây)")
        lbl_wait.setStyleSheet(label_ss)
        grid2.addWidget(lbl_wait, 1, 0)
        self.sett_max_wait = QSpinBox()
        self.sett_max_wait.setRange(60, 3600)
        self.sett_max_wait.setValue(self.settings.get("max_wait_sec", 900))
        self.sett_max_wait.setStyleSheet(spin_ss)
        self.sett_max_wait.valueChanged.connect(self._on_settings_changed)
        grid2.addWidget(self.sett_max_wait, 1, 1)

        # Row 2: Số lần thử lại
        lbl_retry = QLabel("🔄 Số lần thử lại lỗi")
        lbl_retry.setStyleSheet(label_ss)
        grid2.addWidget(lbl_retry, 2, 0)
        self.sett_retry = QSpinBox()
        self.sett_retry.setRange(0, 10)
        self.sett_retry.setValue(self.settings.get("retry_count", 2))
        self.sett_retry.setStyleSheet(spin_ss)
        self.sett_retry.valueChanged.connect(self._on_settings_changed)
        grid2.addWidget(self.sett_retry, 2, 1)

        # Row 3: Số luồng chạy song song
        lbl_threads = QLabel("⚡ Số luồng chạy song song")
        lbl_threads.setStyleSheet(label_ss)
        grid2.addWidget(lbl_threads, 3, 0)
        self.sett_concurrent = QSpinBox()
        self.sett_concurrent.setRange(1, 10)
        self.sett_concurrent.setValue(self.settings.get("concurrent_threads", 3))
        self.sett_concurrent.setStyleSheet(spin_ss)
        self.sett_concurrent.valueChanged.connect(self._on_settings_changed)
        grid2.addWidget(self.sett_concurrent, 3, 1)

        grid2.setColumnStretch(0, 0)
        grid2.setColumnStretch(1, 1)
        c2l.addLayout(grid2)
        slay.addWidget(c2)

        # ═══════════════════════════════════════════════════════════════
        # SECTION 3: CẤU HÌNH VEO 3 / BANANA (GOOGLE LABS FLOW)
        # ═══════════════════════════════════════════════════════════════
        c3 = QFrame(); c3.setObjectName("sett_card"); c3.setStyleSheet(card_ss)
        c3l = QVBoxLayout(c3); c3l.setContentsMargins(20, 16, 20, 16); c3l.setSpacing(12)

        t3 = QLabel("🔧  CẤU HÌNH VEO 3 / BANANA (GOOGLE LABS FLOW)")
        t3.setStyleSheet(section_title_ss)
        c3l.addWidget(t3)

        # Info box
        info_frame = QFrame()
        info_frame.setStyleSheet("QFrame{background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:10px;}")
        info_lay = QVBoxLayout(info_frame); info_lay.setSpacing(4); info_lay.setContentsMargins(12, 8, 12, 8)
        info_t = QLabel("ℹ️ Token & Project ID được tự động lấy từ tài khoản Google đã đăng nhập.")
        info_t.setStyleSheet("font-size:11px;font-weight:bold;color:#1e40af;background:transparent;")
        info_t.setWordWrap(True)
        info_lay.addWidget(info_t)
        info_s = QLabel("→ Vào tab 'Quản Lý Tài Khoản' → nhấn '🔑 Login Qua Trình Duyệt' → chọn 'Google Flow / Veo 3' để đăng nhập.")
        info_s.setStyleSheet("font-size:10px;color:#3b82f6;background:transparent;")
        info_s.setWordWrap(True)
        info_lay.addWidget(info_s)
        c3l.addWidget(info_frame)

        grid3 = QGridLayout(); grid3.setHorizontalSpacing(16); grid3.setVerticalSpacing(10)

        # Row 0: Cổng Bridge
        lbl_port = QLabel("Cổng Bridge (Default: 4000):")
        lbl_port.setStyleSheet(label_ss)
        grid3.addWidget(lbl_port, 0, 0)
        self.sett_bridge_port = QSpinBox()
        self.sett_bridge_port.setRange(1000, 65535)
        self.sett_bridge_port.setValue(getattr(self, "veo_bridge_port", 4000))
        self.sett_bridge_port.setStyleSheet(spin_ss)
        self.sett_bridge_port.valueChanged.connect(self._on_veo_config_changed)
        grid3.addWidget(self.sett_bridge_port, 0, 1)

        # Row 1: Bridge Secret
        lbl_secret = QLabel("Bridge Secret (cho Extension):")
        lbl_secret.setStyleSheet(label_ss)
        grid3.addWidget(lbl_secret, 1, 0)
        self.sett_bridge_secret = QLineEdit(getattr(self, "veo_secret", ""))
        self.sett_bridge_secret.setStyleSheet(input_ss)
        self.sett_bridge_secret.setReadOnly(True)
        grid3.addWidget(self.sett_bridge_secret, 1, 1)

        # Row 2: Jitter Min
        lbl_jmin = QLabel("Jitter Min (ms, mặc định 2000):")
        lbl_jmin.setStyleSheet(label_ss)
        grid3.addWidget(lbl_jmin, 2, 0)
        self.sett_jitter_min = QSpinBox()
        self.sett_jitter_min.setRange(500, 30000)
        self.sett_jitter_min.setValue(getattr(self, "veo_jitter_min_ms", 2000))
        self.sett_jitter_min.setStyleSheet(spin_ss)
        self.sett_jitter_min.valueChanged.connect(self._on_veo_config_changed)
        grid3.addWidget(self.sett_jitter_min, 2, 1)

        # Row 3: Jitter Max
        lbl_jmax = QLabel("Jitter Max (ms, mặc định 5000):")
        lbl_jmax.setStyleSheet(label_ss)
        grid3.addWidget(lbl_jmax, 3, 0)
        self.sett_jitter_max = QSpinBox()
        self.sett_jitter_max.setRange(1000, 60000)
        self.sett_jitter_max.setValue(getattr(self, "veo_jitter_max_ms", 5000))
        self.sett_jitter_max.setStyleSheet(spin_ss)
        self.sett_jitter_max.valueChanged.connect(self._on_veo_config_changed)
        grid3.addWidget(self.sett_jitter_max, 3, 1)

        grid3.setColumnStretch(0, 0)
        grid3.setColumnStretch(1, 1)
        c3l.addLayout(grid3)

        # Google account status
        google_accs = [a for a in self.acc_mgr.accounts if a.name.startswith("Google") and a.active]
        n_google = len(google_accs)
        self.sett_google_status = QLabel(f"{'✅' if n_google else '❌'}  {n_google} tài khoản Google đã đăng nhập")
        self.sett_google_status.setStyleSheet(f"font-size:11px;font-weight:bold;color:{'#16a34a' if n_google else '#dc2626'};margin-top:4px;background:transparent;")
        c3l.addWidget(self.sett_google_status)

        slay.addWidget(c3)

        # ═══════════════════════════════════════════════════════════════
        # SECTION 4: CHỐNG PHÁT HIỆN (ANTI-DETECT)
        # ═══════════════════════════════════════════════════════════════
        c4 = QFrame(); c4.setObjectName("sett_card"); c4.setStyleSheet(card_ss)
        c4l = QVBoxLayout(c4); c4l.setContentsMargins(20, 16, 20, 16); c4l.setSpacing(12)

        t4 = QLabel("🛡️  CHỐNG PHÁT HIỆN (ANTI-DETECT)")
        t4.setStyleSheet(section_title_ss)
        c4l.addWidget(t4)

        anti_desc = QLabel("Các tuỳ chọn bên dưới tự động áp dụng vào video xuất ra, giúp tránh bị phát hiện trùng lặp.")
        anti_desc.setStyleSheet("font-size:11px;color:#6b7280;margin-bottom:4px;background:transparent;")
        anti_desc.setWordWrap(True)
        c4l.addWidget(anti_desc)

        sep_anti = QFrame(); sep_anti.setFrameShape(QFrame.Shape.HLine); sep_anti.setStyleSheet("color:#e5e7eb;")
        c4l.addWidget(sep_anti)

        # Helper for toggle rows
        toggle_ss = """
            QCheckBox::indicator { width: 40px; height: 22px; border-radius: 11px; }
            QCheckBox::indicator:unchecked { background-color: #d1d5db; border: 1px solid #9ca3af; border-radius: 11px; }
            QCheckBox::indicator:checked { background-color: #6366f1; border: 1px solid #4f46e5; border-radius: 11px; }
        """

        def _add_toggle(parent_lay, icon, title, desc, checked, slot):
            row = QHBoxLayout(); row.setSpacing(10)
            ic = QLabel(icon); ic.setStyleSheet("font-size:18px;background:transparent;"); ic.setFixedWidth(24)
            row.addWidget(ic)
            info = QVBoxLayout(); info.setSpacing(1)
            tl = QLabel(title); tl.setStyleSheet("font-size:12px;font-weight:bold;color:#1f2937;background:transparent;"); info.addWidget(tl)
            dl = QLabel(desc); dl.setStyleSheet("font-size:10px;color:#9ca3af;background:transparent;"); dl.setWordWrap(True); info.addWidget(dl)
            row.addLayout(info, stretch=1)
            chk = QCheckBox(); chk.setChecked(checked); chk.setStyleSheet(toggle_ss); chk.stateChanged.connect(slot)
            row.addWidget(chk)
            parent_lay.addLayout(row)
            return chk

        self.sett_chk_meta = _add_toggle(
            c4l, "📋", "Fake Metadata",
            "Ghi đè metadata video (tên camera, ngày tạo, GPS giả) để mỗi video có dấu vân tay khác nhau.",
            self.settings.get("fake_metadata", True), self._on_settings_changed
        )

        s1 = QFrame(); s1.setFrameShape(QFrame.Shape.HLine); s1.setStyleSheet("color:#f3f4f6;"); c4l.addWidget(s1)

        self.sett_chk_flip = _add_toggle(
            c4l, "↔️", "Lật Ngang Ngẫu Nhiên (Flip Horizontal)",
            "Lật ngang ngẫu nhiên một số frame, tạo khác biệt nhỏ mà mắt thường khó nhận ra.",
            self.settings.get("anti_flip_h", False), self._on_settings_changed
        )

        s2 = QFrame(); s2.setFrameShape(QFrame.Shape.HLine); s2.setStyleSheet("color:#f3f4f6;"); c4l.addWidget(s2)

        self.sett_chk_noise = _add_toggle(
            c4l, "✨", "Nhiễu Màu / Noise (Color Shift)",
            "Thêm nhiễu nhẹ và dịch chuyển màu sắc vi mô, khiến mỗi bản render duy nhất về mặt pixel.",
            self.settings.get("anti_noise", True), self._on_settings_changed
        )

        slay.addWidget(c4)

        # ═══════════════════════════════════════════════════════════════
        # STATUS FOOTER
        # ═══════════════════════════════════════════════════════════════
        footer = QFrame()
        footer.setStyleSheet("QFrame{background:#ffffff;border:1px solid #e5e7eb;border-radius:8px;padding:10px 16px;}")
        fl = QHBoxLayout(footer); fl.setContentsMargins(0, 0, 0, 0)
        self.sett_status_lbl = QLabel("✅ Cài đặt đã lưu tự động.")
        self.sett_status_lbl.setStyleSheet("font-size:11px;color:#16a34a;font-weight:bold;background:transparent;")
        fl.addWidget(self.sett_status_lbl)
        fl.addStretch()
        ver = QLabel("Auto Threads v1.0")
        ver.setStyleSheet("font-size:10px;color:#9ca3af;background:transparent;")
        fl.addWidget(ver)
        slay.addWidget(footer)

        slay.addStretch()
        scroll.setWidget(scroll_w)
        main_lay.addWidget(scroll)
        self.stacked.addWidget(panel)

    def _browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục lưu mặc định", str(OUTPUT_DIR))
        if d:
            self.sett_output_dir.setText(d)
            self._on_settings_changed()

    def _on_veo_config_changed(self):
        """Save Veo-specific config to veo3_config.json."""
        self.veo_bridge_port = self.sett_bridge_port.value()
        self.veo_jitter_min_ms = self.sett_jitter_min.value()
        self.veo_jitter_max_ms = self.sett_jitter_max.value()
        self.save_veo_config()
        if hasattr(self, 'sett_status_lbl'):
            self.sett_status_lbl.setText(f"✅ Đã lưu Veo config lúc {time.strftime('%H:%M:%S')}")

    def _on_settings_changed(self):
        """Sync settings toggles back to studio checkboxes and auto-save."""
        # Sync anti-detect to Studio panel checkboxes
        if hasattr(self, 'chk_meta') and hasattr(self, 'sett_chk_meta'):
            self.chk_meta.setChecked(self.sett_chk_meta.isChecked())
        if hasattr(self, 'chk_fh') and hasattr(self, 'sett_chk_flip'):
            self.chk_fh.setChecked(self.sett_chk_flip.isChecked())
        if hasattr(self, 'chk_noise') and hasattr(self, 'sett_chk_noise'):
            self.chk_noise.setChecked(self.sett_chk_noise.isChecked())

        # Sync system params to Studio panel spinboxes
        if hasattr(self, 'sp_delay') and hasattr(self, 'sett_poll_interval'):
            self.sp_delay.setValue(self.sett_poll_interval.value())
        if hasattr(self, 'sp_retry') and hasattr(self, 'sett_retry'):
            self.sp_retry.setValue(self.sett_retry.value())

        # Auto-save settings
        try:
            st = self._collect_settings()
            # Add settings-panel-specific fields
            st["max_wait_sec"] = self.sett_max_wait.value() if hasattr(self, 'sett_max_wait') else 900
            st["concurrent_threads"] = self.sett_concurrent.value() if hasattr(self, 'sett_concurrent') else 3
            st["output_dir"] = self.sett_output_dir.text() if hasattr(self, 'sett_output_dir') else str(OUTPUT_DIR)
            self._save_settings(st)
        except Exception:
            pass

        if hasattr(self, 'sett_status_lbl'):
            self.sett_status_lbl.setText(f"✅ Đã lưu lúc {time.strftime('%H:%M:%S')}")

    # ── HELPERS ───────────────────────────────────────────────────────
    def _mk_card(self, title):
        c = QFrame(); c.setObjectName("section_card"); l = QVBoxLayout(c); l.setContentsMargins(12,10,12,10); l.setSpacing(6)
        tl = QLabel(title); tl.setStyleSheet("font-size:11px;font-weight:bold;color:#4c1d95;text-transform:uppercase;"); l.addWidget(tl)
        return c, l

    def _log(self, msg):
        if hasattr(self, "txt_log") and self.txt_log is not None:
            self.txt_log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
            self.txt_log.moveCursor(self.txt_log.textCursor().MoveOperation.End)
            QApplication.processEvents()
        else:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _clear_layout(self, lay):
        if not lay: return
        while lay.count():
            it = lay.takeAt(0)
            if it and it.widget(): it.widget().setParent(None); it.widget().deleteLater()
            elif it and it.layout(): self._clear_layout(it.layout())

    def _open_file_in_editor(self, filepath):
        """Open a file in the system editor. Falls back to notepad on Windows."""
        fp = str(filepath)
        try:
            os.startfile(fp)
        except OSError:
            try:
                subprocess.Popen(["notepad.exe", fp])
            except Exception as e:
                QMessageBox.warning(self, "Lỗi", f"Không thể mở file:\n{fp}\n\n{e}")

    # ── ROLE SKILL MANAGER DIALOG ──────────────────────────────────────
    def _open_role_skill_manager(self, selected_file_name: str = None):
        """Open a full-featured Role Skill management dialog with direct file editing and saving."""
        DATA_ROLE_SKILL_DIR.mkdir(parents=True, exist_ok=True)
        dlg = QDialog(self)
        dlg.setWindowTitle("🎭 Quản Lý Role Skill (Trực Tiếp Data)")
        dlg.setMinimumSize(850, 600)
        dlg.setStyleSheet("""
            QDialog { background: #f8fafc; }
            QListWidget { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 12px; padding: 4px; }
            QListWidget::item { padding: 8px 12px; border-radius: 4px; }
            QListWidget::item:selected { background: #ede9fe; color: #4c1d95; font-weight: bold; }
            QListWidget::item:hover { background: #f5f3ff; }
            QTextEdit { background: #0f172a; color: #f8fafc; font-family: Consolas, monospace; font-size: 13px; border: 1px solid #334155; border-radius: 8px; padding: 10px; }
        """)

        ml = QVBoxLayout(dlg); ml.setContentsMargins(20, 16, 20, 16); ml.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        hl = QLabel("🎭 Quản Lý & Chỉnh Sửa Role Skill")
        hl.setStyleSheet("font-size:18px;font-weight:bold;color:#1e293b;")
        hdr.addWidget(hl)
        hdr.addStretch()
        hs = QLabel(f"📂 Thư mục: {DATA_ROLE_SKILL_DIR}")
        hs.setStyleSheet("font-size:11px;color:#64748b;font-weight:bold;")
        hdr.addWidget(hs)
        ml.addLayout(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine); sep.setStyleSheet("color:#e2e8f0;"); ml.addWidget(sep)

        # Main content: list + editor
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: File list + control buttons
        left_w = QWidget()
        left_lay = QVBoxLayout(left_w); left_lay.setContentsMargins(0, 0, 0, 0); left_lay.setSpacing(8)

        ll = QLabel("📋 Danh sách Role Skill:")
        ll.setStyleSheet("font-size:12px;font-weight:bold;color:#374151;")
        left_lay.addWidget(ll)

        role_list = QListWidget()
        left_lay.addWidget(role_list, stretch=1)

        # Action buttons on left
        btn_row1 = QHBoxLayout(); btn_row1.setSpacing(6)

        btn_add = QPushButton("➕ Thêm Mới")
        btn_add.setStyleSheet("QPushButton{background:#10b981;color:white;font-weight:bold;padding:8px;border:none;border-radius:6px;}QPushButton:hover{background:#059669;}")
        btn_row1.addWidget(btn_add)

        btn_del = QPushButton("🗑️ Xóa File")
        btn_del.setStyleSheet("QPushButton{background:#dc2626;color:white;font-weight:bold;padding:8px;border:none;border-radius:6px;}QPushButton:hover{background:#b91c1c;}")
        btn_row1.addWidget(btn_del)

        left_lay.addLayout(btn_row1)

        btn_open_dir = QPushButton("📂 Mở Thư Mục Data")
        btn_open_dir.setStyleSheet("QPushButton{background:#64748b;color:white;font-weight:bold;padding:8px;border:none;border-radius:6px;}QPushButton:hover{background:#475569;}")
        btn_open_dir.clicked.connect(lambda: os.startfile(DATA_ROLE_SKILL_DIR) if DATA_ROLE_SKILL_DIR.exists() else None)
        left_lay.addWidget(btn_open_dir)

        splitter.addWidget(left_w)

        # Right side: Live Editor + Save button
        right_w = QWidget()
        right_lay = QVBoxLayout(right_w); right_lay.setContentsMargins(0, 0, 0, 0); right_lay.setSpacing(8)

        preview_hdr = QHBoxLayout()
        self._role_preview_title = QLabel("📝 Nội dung File Data")
        self._role_preview_title.setStyleSheet("font-size:13px;font-weight:bold;color:#1e293b;")
        preview_hdr.addWidget(self._role_preview_title)
        preview_hdr.addStretch()
        self._role_preview_size = QLabel("")
        self._role_preview_size.setStyleSheet("font-size:11px;color:#64748b;")
        preview_hdr.addWidget(self._role_preview_size)
        right_lay.addLayout(preview_hdr)

        preview_txt = QTextEdit()
        preview_txt.setPlaceholderText("Chọn một Role Skill ở danh sách bên trái để chỉnh sửa...")
        preview_txt.setReadOnly(False)  # Cho phép sửa trực tiếp
        right_lay.addWidget(preview_txt, stretch=1)

        # Save button bar
        save_row = QHBoxLayout()
        self._role_mgr_status = QLabel(f"✅ {len(get_available_role_skills())} Role Skill files")
        self._role_mgr_status.setStyleSheet("font-size:12px;color:#16a34a;font-weight:bold;")
        save_row.addWidget(self._role_mgr_status)
        save_row.addStretch()

        btn_save = QPushButton("💾 LƯU FILE DATA TRỰC TIẾP")
        btn_save.setFixedHeight(36)
        btn_save.setStyleSheet("QPushButton{background:#2563eb;color:white;font-weight:bold;font-size:12px;padding:0 20px;border:none;border-radius:6px;}QPushButton:hover{background:#1d4ed8;}")
        save_row.addWidget(btn_save)
        right_lay.addLayout(save_row)

        splitter.addWidget(right_w)

        splitter.setSizes([260, 560])
        ml.addWidget(splitter, stretch=1)

        # ── State and Logic ──
        roles_ref = {}
        current_active_path = [None]

        def refresh_list():
            nonlocal roles_ref
            roles_ref = get_available_role_skills()
            role_list.clear()
            for fname, fpath in roles_ref.items():
                sz = fpath.stat().st_size if fpath.exists() else 0
                sz_kb = f"{sz/1024:.1f} KB" if sz >= 1024 else f"{sz} B"
                role_list.addItem(f"📄 {fname}  ({sz_kb})")
            self._role_mgr_status.setText(f"✅ Tổng {len(roles_ref)} file Role Skill trong Data")

        def on_select():
            idx = role_list.currentRow()
            if idx < 0:
                current_active_path[0] = None
                preview_txt.clear()
                return
            fname = list(roles_ref.keys())[idx]
            fpath = roles_ref[fname]
            current_active_path[0] = fpath
            if fpath.exists():
                content = fpath.read_text("utf-8", errors="replace")
                preview_txt.setPlainText(content)
                sz = fpath.stat().st_size
                sz_str = f"{sz/1024:.1f} KB" if sz >= 1024 else f"{sz} B"
                lines = content.count('\n') + 1
                self._role_preview_title.setText(f"📝 {fname}")
                self._role_preview_size.setText(f"{lines} dòng — {sz_str}")

        def on_save():
            fpath = current_active_path[0]
            if not fpath:
                QMessageBox.information(dlg, "Thông báo", "Vui lòng chọn một file Role Skill để lưu.")
                return
            try:
                new_text = preview_txt.toPlainText()
                fpath.write_text(new_text, encoding="utf-8")
                sz = fpath.stat().st_size
                sz_str = f"{sz/1024:.1f} KB" if sz >= 1024 else f"{sz} B"
                lines = new_text.count('\n') + 1
                self._role_preview_size.setText(f"{lines} dòng — {sz_str}")
                self._role_mgr_status.setText(f"✅ ĐÃ LƯU THÀNH CÔNG: {fpath.name}")
                refresh_list()
                # Select again
                for i in range(role_list.count()):
                    if fpath.name in role_list.item(i).text():
                        role_list.setCurrentRow(i)
                        break
            except Exception as e:
                QMessageBox.critical(dlg, "Lỗi khi lưu", f"Không thể lưu file Data:\n{e}")

        def on_add():
            n, ok = QInputDialog.getText(dlg, "Tạo Role Skill Mới", "Nhập tên file (vd: Role_TikTok.MD):")
            if ok and n.strip():
                fn = n.strip()
                if not fn.lower().endswith(".md") and not fn.lower().endswith(".txt"):
                    fn = f"{fn}.MD"
                tp = DATA_ROLE_SKILL_DIR / fn
                if tp.exists():
                    QMessageBox.warning(dlg, "Lỗi", f"File [{fn}] đã tồn tại trong thư mục Data!")
                    return
                default_content = f"# ROLE SKILL: {fn}\n\n## 1. PHÂN TÍCH SẢN PHẨM\n- Mục tiêu: \n\n## 2. KỊCH BẢN THREADS MẪU\n- Phân cảnh 1: \n"
                tp.write_text(default_content, encoding="utf-8")
                refresh_list()
                self._role_mgr_status.setText(f"✨ Đã tạo file mới: {fn}")
                # Select new file
                for i in range(role_list.count()):
                    if fn in role_list.item(i).text():
                        role_list.setCurrentRow(i)
                        preview_txt.setFocus()
                        break

        def on_del():
            idx = role_list.currentRow()
            if idx < 0:
                QMessageBox.information(dlg, "Thông báo", "Vui lòng chọn file cần xóa.")
                return
            fname = list(roles_ref.keys())[idx]
            fpath = roles_ref[fname]
            if QMessageBox.question(dlg, "Xác nhận xóa File Data", f"Bạn chắc chắn muốn XÓA VĨNH VIỄN file [{fname}] khỏi Data?\n\nThao tác này không thể hoàn tác.") == QMessageBox.StandardButton.Yes:
                try:
                    if fpath.exists(): fpath.unlink()
                    preview_txt.clear()
                    current_active_path[0] = None
                    self._role_preview_title.setText("📝 Nội dung File Data")
                    self._role_preview_size.setText("")
                    refresh_list()
                    self._role_mgr_status.setText(f"🗑️ Đã xóa file: {fname}")
                except Exception as e:
                    QMessageBox.warning(dlg, "Lỗi xóa", f"Không thể xóa file:\n{e}")

        # Connect signals
        btn_add.clicked.connect(on_add)
        btn_save.clicked.connect(on_save)
        btn_del.clicked.connect(on_del)
        role_list.currentRowChanged.connect(lambda _: on_select())

        # Load initial
        refresh_list()
        target_row = 0
        if selected_file_name:
            for i in range(role_list.count()):
                if selected_file_name.lower() in role_list.item(i).text().lower():
                    target_row = i
                    break
        if role_list.count() > 0:
            role_list.setCurrentRow(target_row)

        dlg.exec()

        # Refresh studio combo after dialog closes
        if hasattr(self, 'cb_role'):
            self._reload_role_combo()

    # ── ROLE SKILL ────────────────────────────────────────────────────
    def _reload_role_combo(self):
        self.role_skills_map = get_available_role_skills()
        self.cb_role.blockSignals(True); self.cb_role.clear()
        for fn in self.role_skills_map: self.cb_role.addItem(fn)
        sv = self.settings.get("selected_role")
        if sv and sv in self.role_skills_map: self.cb_role.setCurrentText(sv)
        self.cb_role.blockSignals(False); self._on_role_changed()

    def _on_role_changed(self):
        c = self.cb_role.currentText()
        if c: self.lbl_header_role.setText(f"Role: [{c}]")

    def _add_role_skill(self):
        n,ok = QInputDialog.getText(self,"Tạo Role Skill Mới","Tên (vd: Role_GiaDung.MD):")
        if ok and n.strip():
            fn = n.strip() if n.strip().lower().endswith(".md") else f"{n.strip()}.MD"
            tp = DATA_ROLE_SKILL_DIR/fn; tp.write_text(f"# ROLE SKILL: {fn}\n## 1. PHÂN TÍCH SẢN PHẨM\n",encoding="utf-8")
            self._reload_role_combo(); self.cb_role.setCurrentText(fn); self._open_file_in_editor(tp)

    def _open_role_md(self):
        c = self.cb_role.currentText()
        if c in self.role_skills_map: self._open_file_in_editor(self.role_skills_map[c])

    def _del_role_skill(self):
        c = self.cb_role.currentText()
        if c and c in self.role_skills_map:
            if QMessageBox.question(self,"Xóa",f"Xóa [{c}]?")==QMessageBox.StandardButton.Yes:
                fp = self.role_skills_map[c]
                if fp.exists(): fp.unlink()
                self._reload_role_combo()

    # ── VOICE ─────────────────────────────────────────────────────────
    def _on_voice_changed(self, i): self.w_vbee.setVisible(i==1)
    def _fetch_vbee(self):
        v = vbee_get_voices(self.txt_vbee_tok.text().strip(),self.txt_vbee_aid.text().strip())
        if v:
            self.cb_vbee_v.clear()
            for vc,lb in v: self.cb_vbee_v.addItem(lb,vc)
            self.settings["vbee_voices_cached"] = v
            # Save current settings to disk immediately to persist the list
            self._save_settings(self._collect_settings())
            # Refresh sub-voice list
            self._populate_sub_voice_list()
            QMessageBox.information(self,"OK",f"{len(v)} giọng Vbee.")

    def _populate_sub_voice_list(self):
        """Populate the sub-voice checkbox list from cached voices."""
        if not hasattr(self, 'list_sub_voices'):
            return
        self.list_sub_voices.clear()
        cached_voices = self.settings.get("vbee_voices_cached", [])
        saved_sub = self.settings.get("vbee_sub_voices_display", [])
        for vc, lb in cached_voices:
            item = QListWidgetItem(lb)
            item.setData(Qt.ItemDataRole.UserRole, vc)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if vc in saved_sub else Qt.CheckState.Unchecked)
            self.list_sub_voices.addItem(item)

    def _toggle_all_sub_voices(self, select: bool):
        """Select or deselect all sub-voices."""
        for i in range(self.list_sub_voices.count()):
            item = self.list_sub_voices.item(i)
            item.setCheckState(Qt.CheckState.Checked if select else Qt.CheckState.Unchecked)

    def _get_selected_sub_voices(self) -> list:
        """Return list of selected sub-voice codes."""
        result = []
        if not hasattr(self, 'list_sub_voices'):
            return result
        for i in range(self.list_sub_voices.count()):
            item = self.list_sub_voices.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.data(Qt.ItemDataRole.UserRole))
        return result

    def _browse_bg(self):
        f = QFileDialog.getExistingDirectory(self,"Chọn BG Folder")
        if f: self.txt_bg.setText(f)

    def _toggle_vr_settings(self):
        """Show/hide Video Review settings based on enable checkbox."""
        enabled = self.chk_vr_enable.isChecked()
        self.cb_vr_mode.setVisible(enabled)
        if enabled:
            self._toggle_vr_mode()
        else:
            self.w_vr_ai.setVisible(False)
            self.w_vr_ext.setVisible(False)

    def _toggle_vr_mode(self):
        """Show AI or External settings based on mode selector."""
        is_ai = self.cb_vr_mode.currentIndex() == 0
        self.w_vr_ai.setVisible(is_ai)
        self.w_vr_ext.setVisible(not is_ai)

    def _browse_vr_video(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "Chọn Video Sản Phẩm", "",
            "Video Files (*.mp4 *.mov *.mkv *.avi *.webm);;All Files (*)"
        )
        if f:
            self.txt_vr_file.setText(f)


    # ── PROJECT & PRODUCT MANAGEMENT ──────────────────────────────────
    def _create_project_dialog(self):
        n,ok = QInputDialog.getText(self,"Tạo Dự Án Mới","Tên dự án:")
        if ok and n.strip():
            p = ThreadsProject(name=n.strip()); self.projects.append(p); self._save_projects()
            self._open_project(p)

    def _open_project(self, proj):
        self.active_project = proj
        self.sb_proj_lbl.setText(f"📁 {proj.name}"); self.proj_nav_w.setVisible(True)
        self.studio_proj_lbl.setText(f"🎭  Role Skill Pipeline — {proj.name}")
        self._render_products(); self.switch_panel("studio")

    def _delete_project(self, proj):
        if QMessageBox.question(self,"Xóa Dự Án",f"Xóa [{proj.name}]?")==QMessageBox.StandardButton.Yes:
            self.projects = [p for p in self.projects if p.id!=proj.id]
            if self.active_project and self.active_project.id==proj.id:
                self.active_project = None; self.proj_nav_w.setVisible(False)
            self._save_projects(); self._refresh_dashboard()

    def _add_product(self):
        if not self.active_project: self._create_project_dialog()
        if not self.active_project: return
        files,_ = QFileDialog.getOpenFileNames(self,"Chọn Ảnh SP","","Images (*.png *.jpg *.jpeg *.webp)")
        p = ProductItem(title=f"Sản phẩm #{len(self.active_project.products)+1}",info="Nhập mô tả...",images=files)
        self.active_project.products.append(p); self._save_projects(); self._render_products()
        self._log(f"Thêm SP: {p.title}")

    def _del_last_product(self):
        if self.active_project and self.active_project.products:
            self.active_project.products.pop(); self._save_projects(); self._render_products()
    def _del_all_products(self):
        if self.active_project: self.active_project.products.clear(); self._save_projects(); self._render_products()

    def _delete_product_card(self, card):
        if self.active_project and card.product in self.active_project.products:
            self.active_project.products.remove(card.product)
            self._save_projects()
            self._render_products()

    def _render_products(self):
        if not hasattr(self, "product_cards"):
            self.product_cards = {}
            
        if not self.active_project:
            self._clear_layout(self.prod_lay)
            self.product_cards = {}
            return
        
        # Keep only valid cards that still have a parent and have not been deleted
        self.product_cards = {pid: card for pid, card in self.product_cards.items() 
                              if card and not card.parent() is None}
        
        existing_ids = list(self.product_cards.keys())
        active_ids = [p.id for p in self.active_project.products]
        
        if set(existing_ids) != set(active_ids) or len(existing_ids) != len(active_ids):
            self._clear_layout(self.prod_lay)
            self.product_cards = {}
            for i, prod in enumerate(self.active_project.products):
                card = ThreadsProductCard(i, prod, self)
                self.prod_lay.addWidget(card)
                self.product_cards[prod.id] = card
        else:
            for i, prod in enumerate(self.active_project.products):
                if prod.id in self.product_cards:
                    c = self.product_cards[prod.id]
                    c.product = prod
                    c.load_scenes()
                    c.load_vr_scenes()
                    c.adjust_height()
                    c.update_status_ui()
        QApplication.processEvents()

    # ── PIPELINE ──────────────────────────────────────────────────────
    def _collect_settings(self):
        cr = self.cb_role.currentText(); rp = self.role_skills_map.get(cr, DEFAULT_ROLE_SKILL_FILE)
        vm_text = self.cb_voice_mode.currentText() if hasattr(self, 'cb_voice_mode') else "Review dẫn chuyện (narrative_review)"
        if "hybrid" in vm_text.lower() or "kèm" in vm_text.lower():
            vm_code = "hybrid_narrate_read"
        elif "verbatim" in vm_text.lower() or "nguyên văn" in vm_text.lower():
            vm_code = "verbatim_read"
        else:
            vm_code = "narrative_review"
        sub_voices = self._get_selected_sub_voices() if hasattr(self, 'list_sub_voices') else []
        # Video Review crop mode mapping
        crop_modes = ["square_center", "9_16_center", "keep_original"]
        vr_crop_idx = self.cb_vr_crop.currentIndex() if hasattr(self, 'cb_vr_crop') else 0
        return {
            "api_url":DEFAULT_PROXY_URL,"api_key":"grok-video-key-001","selected_role":cr,"role_skill_path":str(rp),
            "voice_mode": vm_code, "voice_mode_display": vm_text,
            "llm_model":self.cb_llm.currentText(),"img_model":self.cb_img.currentText(),
            "image_quality": self.cb_img_quality.currentText() if hasattr(self, 'cb_img_quality') else "1K (1024x1280 / Standard)",
            "context":self.cb_ctx.currentText(),
            "scene_count":self.sp_scenes.value(),"voice_engine":"vbee" if self.cb_voice.currentIndex()==1 else "gtts",
            "vbee_token":self.txt_vbee_tok.text().strip(),"vbee_appid":self.txt_vbee_aid.text().strip(),
            "vbee_voice":self.cb_vbee_v.currentData() or self.cb_vbee_v.currentText(),"vbee_speed":self.sp_vbee_spd.value(),
            "vbee_sub_voices": sub_voices, "vbee_sub_voices_display": sub_voices,
            "bg_folder":self.txt_bg.text().strip(),"no_duplicate_bg":self.chk_nodup.isChecked(),
            "fake_metadata":self.chk_meta.isChecked(),"anti_flip_h":self.chk_fh.isChecked(),"anti_noise":self.chk_noise.isChecked(),
            "delay_sec":self.sp_delay.value(),"retry_count":self.sp_retry.value(),
            "concurrency": self.sp_concurrency.value() if hasattr(self, 'sp_concurrency') else 8,
            "burst_size": self.sp_burst_size.value() if hasattr(self, 'sp_burst_size') else 4,
            "burst_delay": self.sp_burst_delay.value() if hasattr(self, 'sp_burst_delay') else 10,
            "upscale_threads": self.sp_upscale_threads.value() if hasattr(self, 'sp_upscale_threads') else 1,
            "veo_bearer": getattr(self, "veo_bearer", ""),
            "veo_project_id": getattr(self, "veo_project_id", ""),
            "veo_secret": getattr(self, "veo_secret", ""),
            "veo_bridge_port": getattr(self, "veo_bridge_port", 4000),
            # Video Review settings
            "vr_enabled": self.chk_vr_enable.isChecked() if hasattr(self, 'chk_vr_enable') else False,
            "vr_mode": "ai_generate" if (hasattr(self, 'cb_vr_mode') and self.cb_vr_mode.currentIndex() == 0) else "external_file",
            "vr_video_model": self.cb_vr_video_model.currentText() if hasattr(self, 'cb_vr_video_model') else "Veo 3.1 - Lite [Lower Priority]",
            "vr_resolution": self.cb_vr_resolution.currentText() if hasattr(self, 'cb_vr_resolution') else "720p",
            "vr_role_skill": self.cb_vr_role_skill.currentText() if hasattr(self, 'cb_vr_role_skill') else "Video AI Mỹ Phẩm.MD",
            "vr_scene_count": self.sp_vr_scenes.value() if hasattr(self, 'sp_vr_scenes') else 4,
            "vr_clip_duration": int(self.cb_vr_clip_dur.currentText()) if hasattr(self, 'cb_vr_clip_dur') else 8,
            "vr_aspect_ratio": self.cb_vr_aspect.currentText() if hasattr(self, 'cb_vr_aspect') else "9:16",
            "vr_external_path": self.txt_vr_file.text().strip() if hasattr(self, 'txt_vr_file') else "",
            "vr_crop_mode": crop_modes[vr_crop_idx] if vr_crop_idx < len(crop_modes) else "square_center",
            "vr_speed": self.sp_vr_speed.value() if hasattr(self, 'sp_vr_speed') else 1.0,
            "vr_max_duration": self.sp_vr_max_dur.value() if hasattr(self, 'sp_vr_max_dur') else 30,
            "vr_zoom_random": self.chk_vr_zoom.isChecked() if hasattr(self, 'chk_vr_zoom') else False,
            "vr_flip_h": self.chk_vr_flip.isChecked() if hasattr(self, 'chk_vr_flip') else False,
            "vr_noise": self.chk_vr_noise.isChecked() if hasattr(self, 'chk_vr_noise') else True,
            "vr_random_cut": self.chk_vr_rcut.isChecked() if hasattr(self, 'chk_vr_rcut') else True,
            "vr_rcut_interval_min": self.sp_vr_rcut_min.value() if hasattr(self, 'sp_vr_rcut_min') else 3,
            "vr_rcut_interval_max": self.sp_vr_rcut_max.value() if hasattr(self, 'sp_vr_rcut_max') else 5,
        }

    def _start_pipeline(self):
        if not self.active_project or not self.active_project.products:
            QMessageBox.warning(self,"Cảnh Báo","Chưa có SP!"); return
        self.start_proxy(); st = self._collect_settings(); self._save_settings(st)
        self.btn_start.setEnabled(False); self._log(f"🚀 Bắt đầu [{st['selected_role']}]...")
        self.runner = FullPipelineRunnerWorker(self.active_project.products, st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        self.runner.log_signal.connect(self._log)
        self.runner.product_updated_signal.connect(lambda _: (self._save_projects(),self._render_products()))
        self.runner.finished_signal.connect(self._on_done)
        self.runner.start()

    def _pause_pipeline(self):
        if hasattr(self,'runner') and self.runner.isRunning(): self.runner._stopped = True; self._log("⏸️ Dừng.")

    def _run_single(self, prod):
        self.start_proxy(); st = self._collect_settings()
        self.runner = FullPipelineRunnerWorker([prod],st,self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        self.runner.log_signal.connect(self._log)
        self.runner.product_updated_signal.connect(lambda _: (self._save_projects(),self._render_products()))
        self.runner.finished_signal.connect(self._on_done); self.runner.start()

    def _run_single_video_only(self, prod):
        self.start_proxy(); st = self._collect_settings()
        st["only_video_assembly"] = True
        self.runner = FullPipelineRunnerWorker([prod],st,self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        self.runner.log_signal.connect(self._log)
        self.runner.product_updated_signal.connect(lambda _: (self._save_projects(),self._render_products()))
        self.runner.finished_signal.connect(self._on_done); self.runner.start()

    def _run_single_selective(self, prod, opts: dict):
        """Run pipeline selectively based on ReRenderDialog options."""
        self.start_proxy()
        st = self._collect_settings()
        
        # Apply overrides from dialog
        if opts.get("re_script"):
            if opts.get("re_llm_model"):
                st["llm_model"] = opts["re_llm_model"]
        
        if opts.get("re_voice"):
            st["voice_engine"] = opts.get("re_voice_engine", st.get("voice_engine"))
            if opts.get("re_main_voice"):
                st["vbee_voice"] = opts["re_main_voice"]
            if opts.get("re_sub_voices"):
                st["vbee_sub_voices"] = opts["re_sub_voices"]
            vm_text = opts.get("re_voice_mode", "")
            if "hybrid" in vm_text.lower() or "kèm" in vm_text.lower():
                st["voice_mode"] = "hybrid_narrate_read"
            elif "verbatim" in vm_text.lower() or "nguyên văn" in vm_text.lower():
                st["voice_mode"] = "verbatim_read"
            else:
                st["voice_mode"] = "narrative_review"
            st["voice_mode_display"] = vm_text
        
        if opts.get("re_images") and opts.get("re_img_model"):
            st["img_model"] = opts["re_img_model"]
        
        # Pass the re_video_review flag to the settings
        st["re_video_review"] = opts.get("re_video_review", False)
        
        # Clear data for selected parts so pipeline regenerates them
        if opts.get("re_script"):
            self._log(f"🗑️ Xóa kịch bản cũ ({len(prod.scenes)} scenes) để tạo lại...")
            prod.scenes.clear()
            prod.voiceover_text = ""
        
        if opts.get("re_voice"):
            self._log("🗑️ Xóa voice cũ để tạo lại...")
            if prod.voice_path and Path(prod.voice_path).exists():
                try: Path(prod.voice_path).unlink()
                except: pass
            prod.voice_path = ""
            prod.voice_duration = 0.0
        
        if opts.get("re_images"):
            self._log("🗑️ Xóa ảnh cũ để render lại...")
            for sc in prod.scenes:
                if sc.image_path and Path(sc.image_path).exists():
                    try: Path(sc.image_path).unlink()
                    except: pass
                sc.image_path = ""
                sc.status = "pending"
                
        if opts.get("re_video_review"):
            self._log("🗑️ Xóa video review cũ để render lại...")
            if prod.final_combined_path and Path(prod.final_combined_path).exists():
                try: Path(prod.final_combined_path).unlink()
                except: pass
            prod.final_combined_path = ""
            prod.video_review_scenes.clear()
        
        self._save_projects()
        
        # Set skip flags for parts NOT selected
        if not opts.get("re_script") and prod.scenes:
            # Script already exists, skip LLM step
            pass
        if not opts.get("re_voice") and prod.voice_path:
            # Voice already exists, will be skipped automatically
            pass
        if not opts.get("re_images"):
            # Don't re-render images if not selected (they still have paths)
            pass
        
        self.runner = FullPipelineRunnerWorker([prod], st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        self.runner.log_signal.connect(self._log)
        self.runner.product_updated_signal.connect(lambda _: (self._save_projects(), self._render_products()))
        self.runner.finished_signal.connect(self._on_done)
        self.runner.start()

    def _on_done(self, ok, msg):
        self.btn_start.setEnabled(True); self._save_projects(); self._render_products(); self._log(msg)
        if ok: QMessageBox.information(self,"Hoàn Thành",msg)

    def _load_archetypes_into_combo(self, combo):
        combo.clear()
        for name, desc in DEFAULT_ARCHETYPES:
            combo.addItem(name, desc)
        customs = self.settings.get("custom_archetypes", [])
        for item in customs:
            if isinstance(item, list) and len(item) == 2:
                combo.addItem(item[0], item[1])

    def _on_add_custom_style(self):
        name, ok1 = QInputDialog.getText(self, "Thêm Phong Cách Mới", "Tên phong cách (tiếng Việt):")
        if not ok1 or not name.strip(): return
        desc, ok2 = QInputDialog.getMultiLineText(self, "Thêm Phong Cách Mới", "Nội dung/Mô tả phong cách (tiếng Việt):")
        if not ok2 or not desc.strip(): return
        
        customs = self.settings.setdefault("custom_archetypes", [])
        customs.append([name.strip(), desc.strip()])
        self._save_settings(self.settings)
        
        # Reload comboboxes
        self._load_archetypes_into_combo(self.cb_ctx)
        # Also sync product card comboboxes
        # Use self.product_cards.values() instead of findChildren to prevent crash from deleted cards
        for card in getattr(self, "product_cards", {}).values():
            if hasattr(card, "context_combo"):
                self._load_archetypes_into_combo(card.context_combo)
        
        # Select the newly added style
        self.cb_ctx.setCurrentText(name.strip())

    def _on_manage_styles_clicked(self):
        dlg = StyleManagerDialog(self)
        dlg.exec()

    # ── PERSISTENCE ───────────────────────────────────────────────────
    def _load_projects(self):
        if PROJECTS_FILE.exists():
            try: self.projects = [ThreadsProject.from_dict(p) for p in json.loads(PROJECTS_FILE.read_text("utf-8"))]
            except: self.projects = []
    def _save_projects(self):
        """Debounced save: delays actual disk write by 500ms to eliminate UI typing lag."""
        if hasattr(self, "_save_timer"):
            self._save_timer.start(500)
        else:
            self._save_projects_now()

    def _save_projects_now(self):
        """Immediately write projects JSON to disk."""
        try:
            PROJECTS_FILE.write_text(json.dumps([p.to_dict() for p in self.projects], indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    def _load_settings(self):
        if SETTINGS_FILE.exists():
            try: return json.loads(SETTINGS_FILE.read_text("utf-8"))
            except: pass
        return {}
    def _save_settings(self, st):
        try:
            if hasattr(self, "settings"):
                if "custom_archetypes" in self.settings:
                    st["custom_archetypes"] = self.settings["custom_archetypes"]
                if "vbee_voices_cached" in self.settings:
                    st["vbee_voices_cached"] = self.settings["vbee_voices_cached"]
            SETTINGS_FILE.write_text(json.dumps(st,indent=2,ensure_ascii=False),encoding="utf-8")
        except: pass


def global_excepthook(exctype, value, tb):
    import traceback
    err_str = "".join(traceback.format_exception(exctype, value, tb))
    print(f"🔥 UNCAUGHT EXCEPTION:\n{err_str}", sys.stderr)
    try:
        crash_log_file = Path(__file__).parent / "crash_log.txt"
        with open(crash_log_file, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] UNCAUGHT EXCEPTION:\n{err_str}\n\n")
    except Exception:
        pass

def main():
    sys.excepthook = global_excepthook
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ThreadsStudioWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
