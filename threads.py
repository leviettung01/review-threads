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

import os, sys, json, time, re, uuid, random, shutil, socket, subprocess, asyncio, math, threading, queue, hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta

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
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QSize, QTimer, QRect, QThreadPool
from PyQt6.QtGui import QFont, QColor, QPixmap, QIcon, QImage, QPainter, QPen, QBrush, QImageReader

# Flow capacity belongs to the Google account, not to a product/project
# worker.  The gate is process-wide so independent projects share the same
# configured per-account limit instead of each opening their own pool.
class _VRAccountConcurrencyGate:
    """A dynamically sizeable semaphore shared by all runners for one account."""

    def __init__(self, limit=1):
        self._condition = threading.Condition()
        self._limit = max(1, int(limit))
        self._active = 0

    def set_limit(self, limit):
        with self._condition:
            self._limit = max(1, int(limit))
            self._condition.notify_all()

    def acquire(self, blocking=True, timeout=None):
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            if not blocking and self._active >= self._limit:
                return False
            while self._active >= self._limit:
                if not blocking:
                    return False
                if deadline is None:
                    self._condition.wait()
                    continue
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            self._active += 1
            return True

    def release(self):
        with self._condition:
            if self._active <= 0:
                raise RuntimeError("VR account gate released too many times")
            self._active -= 1
            self._condition.notify()


_VR_GOOGLE_ACCOUNT_SLOTS_LOCK = threading.Lock()
_VR_GOOGLE_ACCOUNT_SLOTS = {}
_VR_GOOGLE_ACCOUNT_RR_LOCK = threading.Lock()
_VR_GOOGLE_ACCOUNT_RR_INDEX = 0
_VR_GOOGLE_ACCOUNT_HEALTH_LOCK = threading.Lock()
_VR_GOOGLE_ACCOUNT_429_STREAK = {}
_VR_GOOGLE_ACCOUNT_COOLDOWN_UNTIL = {}

# ─── PATHS (SELF-CONTAINED IN REVIEWTHREADS) ─────────────────────────
CURRENT_DIR = Path(__file__).parent.resolve()
GROK_DIR = CURRENT_DIR.parent
if str(GROK_DIR) not in sys.path:
    sys.path.append(str(GROK_DIR))
from google_auth_store import (
    ensure_auth_store,
    get_auth_root,
    migrate_legacy_store,
)

# ReviewThreads owns its own Flow profile/cookie/token store.  The previous
# resolver searched Grok and legacy KOL directories, allowing either tool to
# reopen or clean the other tool's browser session.
THREADS_AUTH_ROOT = get_auth_root(CURRENT_DIR, "threads")
ensure_auth_store(THREADS_AUTH_ROOT, "threads")
migrate_legacy_store(CURRENT_DIR / "Flow_nhieu_worker_data", THREADS_AUTH_ROOT, "threads")
THREADS_CONFIG_FILE = THREADS_AUTH_ROOT / "veo3_config.json"
# Locks are part of the app-owned auth store as well.  Sharing a lock filename
# with Grok made a Threads-only browser profile look busy whenever the other
# tool refreshed an account with the same email/profile label.
THREADS_RUNTIME_LOCK_ROOT = THREADS_AUTH_ROOT / "locks"
_legacy_threads_config = CURRENT_DIR / "veo3_config.json"
if not THREADS_CONFIG_FILE.exists() and _legacy_threads_config.exists():
    try:
        shutil.copy2(_legacy_threads_config, THREADS_CONFIG_FILE)
    except Exception:
        pass

def _resolve_flow_shared_dir(profile_name: str = "") -> Path:
    """Resolve only the ReviewThreads-owned Flow directory."""
    candidates = [THREADS_AUTH_ROOT]
    if profile_name:
        aliases = _google_profile_aliases(profile_name)
        for cand in candidates:
            if not cand.exists():
                continue
            for alias in aliases:
                cf = cand / "cookies" / f"{alias}.json"
                pd = cand / "profiles" / alias
                if cf.exists() or pd.exists():
                    return cand

            # The login screen stores the user-selected profile path in this
            # registry.  It is the source of truth when the account email and
            # the Chrome profile name are different.
            registry = cand / "Flow_nhieu_worker_accounts.json"
            if registry.exists():
                try:
                    rows = _flow_registry_rows(
                        json.loads(registry.read_text(encoding="utf-8"))
                    )
                    for row in rows:
                        email = str(row.get("email", "")).strip().lower()
                        if email and email in {a.lower() for a in aliases}:
                            profile_dir = Path(str(row.get("profile_dir", "")))
                            cookies_file = Path(str(row.get("cookies_file", "")))
                            if profile_dir.exists() or cookies_file.exists():
                                return cand
                except Exception:
                    pass
    THREADS_AUTH_ROOT.mkdir(parents=True, exist_ok=True)
    return THREADS_AUTH_ROOT


def _google_profile_aliases(value: str) -> list:
    """Return account/profile aliases without assuming email == profile name."""
    raw = str(value or "").strip()
    if raw.lower().startswith("google_"):
        raw = raw[7:]
    aliases = []
    for item in (raw, raw.split("@", 1)[0] if "@" in raw else ""):
        item = str(item or "").strip()
        if item and item.lower() not in {a.lower() for a in aliases}:
            aliases.append(item)
    return aliases


def _flow_registry_rows(payload) -> list:
    """Read both legacy list registries and single-account JSON objects."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("accounts", "items", "data"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


# Flow has used both `/api/auth/session` and `/fx/api/auth/session` during its
# domain migration.  Keep the probe in one place so login, startup refresh and
# single-account refresh do not disagree about what "logged in" means.
FLOW_DEFAULT_PROJECT_ID = "378bfc6c-422d-4bfb-92d1-6e978432ca7b"


def _flow_session_values(payload):
    from google_flow_auth import session_values
    return session_values(payload)


def _probe_flow_session(context, page=None, allow_navigation=False):
    from google_flow_auth import probe_session
    return probe_session(context, page, allow_navigation)


def _flow_shared_candidates() -> list:
    """Return only the ReviewThreads-owned auth root for cleanup."""
    return [THREADS_AUTH_ROOT]


def _purge_google_account_data(account_or_name, profile_name: str = "") -> list:
    """Remove only authentication/browser data belonging to one Google account.

    Rendered projects and their media are intentionally not touched.  The
    cleanup covers accounts.json (handled by AccountManager), Flow cookies,
    saved Chrome profiles, legacy browser_profiles folders and stale Flow
    registry rows.  All filesystem targets are constrained to known app
    directories and exact account/profile aliases.
    """
    account_name = str(getattr(account_or_name, "name", account_or_name) or "").strip()
    stored_profile = str(
        profile_name or getattr(account_or_name, "profile_name", "") or ""
    ).strip()
    aliases = []
    for value in (account_name, stored_profile):
        for alias in _google_profile_aliases(value):
            if alias.lower() not in {item.lower() for item in aliases}:
                aliases.append(alias)
    if not aliases:
        return []

    alias_keys = {item.lower() for item in aliases}
    removed = []

    def _safe_remove(path: Path, root: Path, kind: str, expected_name: str = ""):
        try:
            target = path.resolve()
            base = root.resolve()
            if target == base or base not in target.parents:
                return
            if expected_name and target.name.lower() != expected_name.lower():
                return
            if kind == "profile" and target.parent.name.lower() != "profiles":
                return
            if kind == "cookie" and target.parent.name.lower() != "cookies":
                return
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink(missing_ok=True)
            if not target.exists():
                removed.append(str(target))
        except Exception:
            # A browser may still have a file open.  Do not make account
            # deletion crash the UI; the next explicit cleanup can retry it.
            pass

    for shared_dir in _flow_shared_candidates():
        if not shared_dir.exists():
            continue

        # Remove exact legacy/current profile and cookie aliases.
        for alias in aliases:
            _safe_remove(shared_dir / "profiles" / alias, shared_dir, "profile", alias)
            _safe_remove(shared_dir / "cookies" / f"{alias}.json", shared_dir, "cookie", f"{alias}.json")

        registry = shared_dir / "Flow_nhieu_worker_accounts.json"
        if registry.exists():
            try:
                rows = _flow_registry_rows(
                    json.loads(registry.read_text(encoding="utf-8"))
                )
                kept = []
                changed = False
                for row in rows:
                    if not isinstance(row, dict):
                        kept.append(row)
                        continue
                    email = str(row.get("email", "")).strip()
                    profile_path = str(row.get("profile_dir", "")).strip()
                    cookie_path = str(row.get("cookies_file", "")).strip()
                    row_keys = {email.lower()}
                    row_keys.update(item.lower() for item in _google_profile_aliases(email))
                    for path_text in (profile_path, cookie_path):
                        if path_text:
                            row_keys.add(Path(path_text).name.lower())
                            row_keys.add(Path(path_text).stem.lower())
                    if row_keys & alias_keys:
                        changed = True
                        # Registry paths are only removed when they resolve
                        # inside the same known Flow directory.
                        if profile_path:
                            _safe_remove(Path(profile_path), shared_dir, "profile")
                        if cookie_path:
                            _safe_remove(Path(cookie_path), shared_dir, "cookie")
                        continue
                    kept.append(row)
                if changed:
                    registry.write_text(
                        json.dumps(kept, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    removed.append(str(registry))
            except Exception:
                pass

    # Older builds stored a second browser cache outside Flow_nhieu_worker_data.
    for browser_root in (CURRENT_DIR / "browser_profiles",):
        if browser_root.exists():
            for alias in aliases:
                _safe_remove(browser_root / alias, browser_root, "browser", alias)

    # Do not erase shared project settings.  Only clear a bearer token when it
    # is exactly the token owned by the account being deleted.
    account_token = str(getattr(account_or_name, "api_key", "") or "").strip()
    if account_token and len(account_token) > 20:
        for config_path in (THREADS_CONFIG_FILE,):
            if not config_path.exists():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and str(data.get("bearer_token", "")) == account_token:
                    data["bearer_token"] = ""
                    config_path.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    removed.append(str(config_path))
            except Exception:
                pass
    return removed


def _resolve_google_profile_name(value: str) -> str:
    """Resolve the actual saved Chrome profile name for a Google account."""
    aliases = _google_profile_aliases(value)
    shared_dir = _resolve_flow_shared_dir(value)

    for alias in aliases:
        if (shared_dir / "profiles" / alias).exists() or (shared_dir / "native_profiles" / alias).exists() or (shared_dir / "cookies" / f"{alias}.json").exists():
            return alias

    registry = shared_dir / "Flow_nhieu_worker_accounts.json"
    if registry.exists():
        try:
            rows = _flow_registry_rows(
                json.loads(registry.read_text(encoding="utf-8"))
            )
            wanted = {a.lower() for a in aliases}
            for row in rows:
                email = str(row.get("email", "")).strip().lower()
                if email in wanted:
                    profile_dir = str(row.get("profile_dir", "")).strip()
                    if profile_dir:
                        return Path(profile_dir).name
                    cookies_file = str(row.get("cookies_file", "")).strip()
                    if cookies_file:
                        return Path(cookies_file).stem
        except Exception:
            pass

    return aliases[0] if aliases else str(value or "")


def _resolve_flow_expected_email(profile_name: str) -> str:
    """Find the Google email bound to a saved Chrome profile."""
    profile_name = str(profile_name or "").strip()
    if not profile_name:
        return ""
    shared_dir = _resolve_flow_shared_dir(profile_name)
    registry = shared_dir / "Flow_nhieu_worker_accounts.json"
    if registry.exists():
        try:
            rows = _flow_registry_rows(
                json.loads(registry.read_text(encoding="utf-8"))
            )
            wanted = profile_name.lower()
            for row in rows:
                if not isinstance(row, dict):
                    continue
                paths = (
                    str(row.get("profile_dir", "")),
                    str(row.get("cookies_file", "")),
                )
                names = {Path(p).name.lower() for p in paths if p}
                names.update(Path(p).stem.lower() for p in paths if p)
                email = str(row.get("email", "")).strip()
                if wanted in names or wanted == email.lower():
                    return email
        except Exception:
            pass
    return profile_name if "@" in profile_name else ""


def _google_identity_matches(expected_email: str, actual_email: str) -> bool:
    from google_flow_auth import identity_matches
    return identity_matches(expected_email, actual_email)


# A Chrome user-data directory cannot be opened safely by two Playwright
# persistent contexts at the same time.  Video workers can discover an
# expired token concurrently, so serialize refreshes per saved profile.
_google_profile_refresh_locks = {}
_google_profile_refresh_locks_guard = threading.Lock()
_google_profile_process_locks = {}
_flow_registry_lock = threading.RLock()


class _CrossProcessProfileLock(__import__('google_flow_auth').ProfileLock):
    pass


def _get_google_profile_refresh_lock(profile_name: str):
    profile_key = (
        _resolve_google_profile_name(profile_name)
        or str(profile_name or "").strip()
        or "__default__"
    ).lower()
    # A Threads profile is intentionally isolated from the profile with the
    # same label in Grok.  Only workers inside this auth root serialize here.
    key = f"{THREADS_AUTH_ROOT.resolve()}::{profile_key}"
    with _google_profile_refresh_locks_guard:
        local_lock = _google_profile_process_locks.get(key)
        if local_lock is None:
            local_lock = threading.Lock()
            _google_profile_process_locks[key] = local_lock
        lock_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        lock_path = THREADS_RUNTIME_LOCK_ROOT / f"google_profile_{lock_digest}.lock"
        lock = _google_profile_refresh_locks.get(key)
        if lock is None:
            lock = _CrossProcessProfileLock(local_lock, lock_path)
            _google_profile_refresh_locks[key] = lock
        return lock


def _atomic_write_json(path: Path, payload) -> bool:
    """Write JSON in one replacement so an interrupted refresh cannot corrupt it."""
    temp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp.replace(path)
        return True
    except Exception:
        return False
    finally:
        if temp is not None:
            try:
                if temp.exists():
                    temp.unlink()
            except Exception:
                pass


def _persist_flow_storage_state(context, cookies_file: Path) -> bool:
    """Persist a verified Flow session, including IndexedDB when supported.

    This is intentionally called only after the OAuth token has passed Google
    validation.  It never replaces a previous snapshot with a logged-out
    browser state.
    """
    try:
        try:
            state = context.storage_state(indexed_db=True)
        except Exception:
            # Older Playwright releases do not expose the indexed_db option;
            # a plain storage export is still better than losing persistence.
            state = context.storage_state()
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
            return False
        # A valid OAuth response alone is not enough reason to replace the
        # prior durable state.  Avoid turning a good cookie snapshot into an
        # empty logged-out JSON document when a browser closes mid-login.
        has_cookie = bool(state.get("cookies"))
        has_origin_state = any(
            isinstance(origin, dict)
            and bool(origin.get("localStorage") or origin.get("indexedDB"))
            for origin in state.get("origins", [])
        )
        if not has_cookie and not has_origin_state:
            return False
        return _atomic_write_json(cookies_file, state)
    except Exception:
        return False


def _upsert_flow_account_registry(profile_name: str, email: str = "", project_id: str = ""):
    """Persist the account -> profile/cookies mapping used by every refresh path."""
    profile_name = _resolve_google_profile_name(profile_name) or str(profile_name or "").strip()
    if not profile_name:
        return False
    shared_dir = _resolve_flow_shared_dir(profile_name)
    registry = shared_dir / "Flow_nhieu_worker_accounts.json"
    from google_flow_auth import profile_paths
    profile_dir, _ = profile_paths(shared_dir, profile_name)
    cookies_file = shared_dir / "cookies" / f"{profile_name}.json"
    account_email = str(email or "").strip() or profile_name
    with _flow_registry_lock:
        try:
            payload = json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else []
            if isinstance(payload, list):
                rows = [row for row in payload if isinstance(row, dict)]
            elif isinstance(payload, dict):
                rows = next(
                    (
                        [row for row in payload.get(key, []) if isinstance(row, dict)]
                        for key in ("accounts", "items", "data")
                        if isinstance(payload.get(key), list)
                    ),
                    [payload],
                )
            else:
                rows = []
        except Exception:
            rows = []

        wanted = account_email.lower()
        match = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_email = str(row.get("email", "")).strip().lower()
            row_profile = Path(str(row.get("profile_dir", ""))).name.lower()
            if row_email == wanted or row_profile == profile_name.lower():
                match = row
                break
        if match is None:
            match = {"email": account_email}
            rows.append(match)
        match.update({
            "email": account_email,
            "profile_dir": str(profile_dir),
            "cookies_file": str(cookies_file),
            "project_id": str(project_id or match.get("project_id", "") or ""),
            "status": "ready",
            "last_seen": datetime.now().isoformat(timespec="seconds"),
        })
        return _atomic_write_json(registry, rows)

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

# Keep a private account file for Threads.  The old implementation wrote to
# the parent Grok file, so token refreshes and account deletion in one tool
# could change the other tool's state.
_SHARED_ACCOUNTS = GROK_DIR / "accounts.json"
_LEGACY_ACCOUNTS = CURRENT_DIR / "accounts.json"
ACCOUNTS_FILE = THREADS_AUTH_ROOT / "accounts.json"
PROJECTS_FILE      = CURRENT_DIR / "threads_projects.json"
SETTINGS_FILE      = CURRENT_DIR / "threads_settings.json"
TIKTOK_SHOP_DIR    = CURRENT_DIR / "TiktokShop"
TIKTOK_PRODUCTS_FILE = TIKTOK_SHOP_DIR / "crawled_products" / "products.json"


def _read_account_rows(path: Path) -> list:
    try:
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    except Exception:
        return []


def _is_threads_google_account(account) -> bool:
    """Use the hardened Grok classifier without treating Codex email as Flow."""
    return bool(_parent_is_google_flow_account(account))


def _migrate_legacy_accounts_once() -> None:
    """Seed Threads accounts from legacy files without sharing future writes."""
    if ACCOUNTS_FILE.exists():
        return

    merged = {}
    for source in (_SHARED_ACCOUNTS, _LEGACY_ACCOUNTS):
        for row in _read_account_rows(source):
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            key = name.lower()
            if key not in merged:
                merged[key] = dict(row)

    rows = []
    for row in merged.values():
        if _is_threads_google_account(row):
            profile = _resolve_google_profile_name(
                row.get("profile_name") or row.get("name", "")
            )
            # A Google account is imported only when its session belongs to
            # the migrated ReviewThreads store.  Grok-only profiles stay out.
            if not profile:
                continue
            row["profile_name"] = profile
        rows.append(row)

    if rows:
        _atomic_write_json(ACCOUNTS_FILE, rows)

sys.path.append(str(GROK_DIR))
try:
    from grok_video_tool import (
        run_veo_flow, VideoJob, is_veo_model, CaptchaBridge, upload_veo_image,
        GrokClient, AccountManager, Account,
        GoogleTokenRefreshWorker as ParentGoogleTokenRefreshWorker,
        _is_google_flow_account as _parent_is_google_flow_account,
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
        make_httpx_client,
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
    def _parent_is_google_flow_account(account):
        name = str(getattr(account, "name", "") or "").strip().lower()
        return name.startswith(("google", "flow", "veo")) and not name.startswith(("codex", "grok", "openai", "xai"))
    def is_veo_model(m): return False
    def make_httpx_client(timeout=120, target_url="", use_proxy=True, **kwargs):
        return httpx.Client(timeout=timeout, **kwargs)
    GROK_DEFAULT_PROXY_URL = "http://127.0.0.1:8317"
    GROK_DEFAULT_PROJECT_ID = ""


# CaptchaBridge normally serializes token requests, but its reset commands
# used to bypass that lock.  A score reset could therefore reload the Flow tab
# while another worker was still minting a token.  Keep the existing bridge
# protocol and add one shared gate for token mint + soft/hard reset.  This is
# intentionally local to ReviewThreads so the other tools keep their current
# behavior.
if has_grok_deps:
    _BaseCaptchaBridge = CaptchaBridge

    class ReviewCaptchaBridge(_BaseCaptchaBridge):
        def reset_session(self, kind="hard_reset", timeout=35):
            request_lock = getattr(self, "_request_lock", None)
            if request_lock is None:
                return super().reset_session(kind, timeout)
            with request_lock:
                return super().reset_session(kind, timeout)

    CaptchaBridge = ReviewCaptchaBridge

CLIPROXY_DIR = CURRENT_DIR / "CLIProxy"
if not CLIPROXY_DIR.exists(): CLIPROXY_DIR = GROK_DIR / "CLIProxy"
if not CLIPROXY_DIR.exists(): CLIPROXY_DIR = GROK_DIR / "cli-proxy-api"
CLIPROXY_EXE = CLIPROXY_DIR / "cli-proxy-api.exe"
DEFAULT_PROXY_URL = "http://127.0.0.1:8317"

for d in [OUTPUT_DIR, OUTPUT_IMAGES_DIR, OUTPUT_VOICE_DIR, OUTPUT_VIDEOS_DIR, TIKTOK_SHOP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ─── CONSTANTS ────────────────────────────────────────────────────────
LLM_MODEL_LIST = [
    "gpt-5.5","gpt-5.6-sol","gpt-5.6-terra","gpt-5.6-luna",
    "gpt-5.4","gpt-5.4-mini","gpt-5.3-codex-spark","gpt-oss-120b-medium",
    "claude-sonnet-4-6","claude-opus-4-6-thinking",
    "gemini-3.5-flash","gemini-3-flash","gemini-3.1-flash-lite","gemini-pro-agent",
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

_CRASH_LOG_LOCK = threading.Lock()

def _record_background_exception(context: str) -> str:
    """Ghi đầy đủ traceback của lỗi đã bị worker bắt lại."""
    import traceback
    details = traceback.format_exc()
    try:
        with _CRASH_LOG_LOCK:
            with open(CURRENT_DIR / "crash_log.txt", "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {context}:\n{details}\n")
    except Exception:
        pass
    return details

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
    # Google account email and saved Chrome profile name are not always equal.
    # Keep the mapping with the account instead of deriving it on every retry.
    profile_name: str = ""
    active: bool = True
    jobs_done: int = 0
    tag: str = "Pro"
    credits: Optional[int] = None
    auth_state: str = ""
    google_email: str = ""
    auth_revision: int = 0

    def to_dict(self):
        return {
            "name": self.name,
            "proxy_url": self.proxy_url,
            "api_key": self.api_key,
            "profile_name": self.profile_name,
            "active": self.active,
            "jobs_done": self.jobs_done,
            "tag": self.tag,
            "credits": self.credits,
            "auth_state": self.auth_state,
            "google_email": self.google_email,
            "auth_revision": self.auth_revision,
        }

    @staticmethod
    def from_dict(d):
        return Account(
            name=d.get("name", "Acc"),
            proxy_url=d.get("proxy_url", DEFAULT_PROXY_URL),
            api_key=d.get("api_key", "grok-video-key-001"),
            profile_name=d.get("profile_name", ""),
            active=d.get("active", True),
            jobs_done=d.get("jobs_done", 0),
            tag=d.get("tag", "Pro"),
            credits=d.get("credits", None),
            auth_state=d.get("auth_state", "ready" if d.get("api_key", "").startswith("ya29.") else ""),
            google_email=d.get("google_email", ""),
            auth_revision=d.get("auth_revision", 0),
        )

class AccountManager:
    def __init__(self):
        self.accounts: List[Account] = []
        self._loaded_account_keys = set()
        self._idx = 0
        # Separate round-robin indices for parallel pipeline phases
        self._idx_codex = 0   # For LLM + Codex Image requests
        self._idx_google = 0  # For Google Labs (Veo/Banana) requests
        self._idx_general = 0 # General fallback
        # RLock vì các hàm next_* giữ lock rồi gọi tiếp load()/save().
        # Lock thường không bảo vệ được load/get_all_* và dễ tạo race khi
        # nhiều worker ảnh/video cùng đọc lại accounts.json.
        self._lock = threading.RLock()
        _migrate_legacy_accounts_once()
        from google_flow_auth import recover_login_commit, ProfileLock, _commit_lock
        with ProfileLock(_commit_lock, THREADS_AUTH_ROOT / 'locks' / 'account_commit.lock'):
            recover_login_commit(THREADS_AUTH_ROOT)
        self.load()
        self.discover_google_profiles()

    def discover_google_profiles(self):
        from google_flow_auth import discover_profiles
        for profile in discover_profiles(THREADS_AUTH_ROOT):
            self.ensure_google_profile(profile)

    def ensure_google_profile(self, profile):
        from google_flow_auth import pending_record
        with self._lock:
            if any(a.profile_name == profile for a in self.accounts):
                return
            self.accounts.append(Account.from_dict(pending_record(THREADS_AUTH_ROOT, profile)))
            if not self.save():
                raise OSError("Không lưu được profile Google đang chờ đăng nhập")

    def load(self):
        with self._lock:
            if ACCOUNTS_FILE.exists():
                try:
                    loaded = [Account.from_dict(d) for d in json.loads(ACCOUNTS_FILE.read_text("utf-8"))]
                    changed = False
                    for account in loaded:
                        if _is_threads_google_account(account):
                            resolved = _resolve_google_profile_name(
                                account.profile_name or account.name
                            )
                            if resolved and resolved != account.profile_name:
                                account.profile_name = resolved
                                changed = True
                            if account.api_key and account.auth_state != "profile_known":
                                _upsert_flow_account_registry(
                                    account.profile_name or resolved or account.name,
                                    email=account.google_email or account.name.replace("Google_", "", 1),
                                    project_id=account.proxy_url,
                                )
                    self.accounts = loaded
                    from google_flow_auth import account_key
                    self._loaded_account_keys = {account_key(a.to_dict()) for a in loaded}
                    if changed:
                        self.save()
                except Exception:
                    # A transient read failure must not make the next save
                    # erase the existing account table.
                    return

    def save(self):
        with self._lock:
            from google_flow_auth import ProfileLock, _commit_lock, read_store_json, account_key, merge_account_rows
            with ProfileLock(_commit_lock, THREADS_AUTH_ROOT / 'locks' / 'account_commit.lock'):
                rows = [a.to_dict() for a in self.accounts]
                try:
                    disk = read_store_json(ACCOUNTS_FILE, [])
                    rows = merge_account_rows(rows, disk, self._loaded_account_keys)
                except ValueError:
                    return False
                if not _atomic_write_json(ACCOUNTS_FILE, rows):
                    return False
                self.accounts = [Account.from_dict(r) for r in rows]
                self._loaded_account_keys = {account_key(r) for r in rows}
                return True

    def add(self, name, proxy_url, api_key):
        with self._lock:
            self.accounts.append(Account(name, proxy_url, api_key))
            self.save()

    def remove(self, i):
        with self._lock:
            if 0 <= i < len(self.accounts):
                self.accounts.pop(i)
                self.save()

    def toggle(self, i):
        with self._lock:
            if 0 <= i < len(self.accounts):
                self.accounts[i].active = not self.accounts[i].active
                self.save()

    @property
    def active(self):
        with self._lock:
            return [a for a in self.accounts if a.active]

    def next(self) -> Optional[Account]:
        with self._lock:
            self.load()
            a = self.active
            if not a: return None
            acc = a[self._idx % len(a)]
            self._idx += 1
            acc.jobs_done += 1
            self.save()
            return acc

    def next_codex(self) -> Optional[Account]:
        with self._lock:
            self.load()
            codex = [a for a in self.active if a.name.lower().startswith("codex")]
            if not codex: return None
            acc = codex[self._idx_codex % len(codex)]
            self._idx_codex += 1
            acc.jobs_done += 1
            self.save()
            return acc

    def next_codex_rr(self) -> Optional[Account]:
        """Round-robin Codex accounts for pipeline phases (thread-safe)."""
        with self._lock:
            self.load()
            codex = [a for a in self.active if a.name.lower().startswith("codex")]
            if not codex:
                # Fallback to any active account
                a = self.active
                if not a: return None
                acc = a[self._idx_general % len(a)]
                self._idx_general += 1
                acc.jobs_done += 1
                self.save()
                return acc
            acc = codex[self._idx_codex % len(codex)]
            self._idx_codex += 1
            acc.jobs_done += 1
            self.save()
            return acc

    def next_google_rr(self) -> Optional[Account]:
        """Round-robin Google accounts for Veo/Banana pipeline phases (thread-safe)."""
        with self._lock:
            self.load()
            g_accs = [a for a in self.active if _is_threads_google_account(a)]
            if not g_accs: return None
            acc = g_accs[self._idx_google % len(g_accs)]
            self._idx_google += 1
            acc.jobs_done += 1
            self.save()
            return acc

    def get_all_google(self) -> List[Account]:
        """Get all active Google accounts with valid bearer tokens."""
        with self._lock:
            self.load()
            return [
                a for a in self.active
                if _is_threads_google_account(a) and len(a.api_key or "") > 20
            ]

    def get_all_codex(self) -> List[Account]:
        """Get all active Codex accounts."""
        with self._lock:
            self.load()
            return [a for a in self.active if a.name.lower().startswith("codex")]

    def next_grok(self) -> Optional[Account]:
        with self._lock:
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


# ════════════════════════════════════════════════════════════════════════
#  TTS PROVIDERS — Vbee, Resona.live, ElevenLabs (11labs.id.vn), RevidAPI
#  Mỗi hàm *_get_voices trả về list voice chuẩn hoá:
#     (voice_id, label, gender, vtype)
#       gender ∈ {"male","female",""}      vtype = nhóm/loại giọng (để lọc)
#  Mỗi hàm *_create_tts trả về đường dẫn file .mp3 hoặc None.
# ════════════════════════════════════════════════════════════════════════

def _norm_gender(raw) -> str:
    """Chuẩn hoá nhãn giới tính về male/female/''."""
    s = str(raw or "").strip().lower()
    if s in ("male", "nam", "m", "man", "boy"):
        return "male"
    if s in ("female", "nữ", "nu", "f", "woman", "girl"):
        return "female"
    if "female" in s or "nữ" in s or "nu" in s:
        return "female"
    if "male" in s or "nam" in s:
        return "male"
    return ""

def _gender_icon(g: str) -> str:
    return "👨" if g == "male" else ("👩" if g == "female" else "🎙️")

def _vbee_region(code: str) -> str:
    c = (code or "").lower()
    if "north" in c or "hanoi" in c or "_bac" in c:
        return "Miền Bắc"
    if "central" in c or "hue" in c or "_trung" in c:
        return "Miền Trung"
    if "south" in c or "saigon" in c or "_nam" in c:
        return "Miền Nam"
    return "Khác"

# ─── VBEE API ─────────────────────────────────────────────────────────
def vbee_get_voices(token, app_id):
    if not token or not app_id: return []
    url = "https://vbee.vn/api/public/v1/voices"
    hdrs = {"Authorization": f"Bearer {token}", "app-id": app_id, "Content-Type": "application/json"}
    own_label = {"VBEE": "VBEE", "COMMUNITY": "Cộng đồng", "PERSONAL": "Cá nhân"}
    voices = []
    for own in ["VBEE", "COMMUNITY", "PERSONAL"]:
        try:
            r = requests.get(url, headers=hdrs, params={"voiceOwnership": own, "languageCode": "vi-VN", "limit": 100}, timeout=10)
            if r.status_code == 200:
                for v in r.json().get("result", {}).get("voices", []):
                    code = v.get("code")
                    if not code:
                        continue
                    g = _norm_gender(v.get("gender"))
                    region = _vbee_region(code)
                    tag = "🧬 " if own == "PERSONAL" else ""
                    label = f"{tag}{_gender_icon(g)} {v.get('name', code)} · {region} [{own_label.get(own, own)}]"
                    voices.append((code, label, g, region))
        except Exception:
            pass
    return voices


# ─── RESONA.LIVE ──────────────────────────────────────────────────────
RESONA_BASE = "https://resona.live/api/v1"

def resona_get_voices(api_key):
    """Lấy danh sách voice Resona. Nếu API không trả list, dùng combo tự nhập ID."""
    if not api_key:
        return []
    hdrs = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    for url in (f"{RESONA_BASE}/voices", f"{RESONA_BASE}/speech/voices", "https://resona.live/api/voices"):
        try:
            r = requests.get(url, headers=hdrs, timeout=12)
            if r.status_code != 200:
                continue
            data = r.json()
            items = data.get("voices") or data.get("data") or data.get("result") or (data if isinstance(data, list) else [])
            out = []
            for v in items:
                if not isinstance(v, dict):
                    continue
                vid = v.get("voice_id") or v.get("id") or v.get("code")
                if not vid:
                    continue
                g = _norm_gender(v.get("gender"))
                vtype = v.get("accent") or v.get("category") or v.get("language") or "Resona"
                name = v.get("name") or v.get("display_name") or vid
                out.append((str(vid), f"{_gender_icon(g)} {name} · {vtype}", g, str(vtype)))
            if out:
                return out
        except Exception:
            continue
    return []

def resona_create_tts(text, api_key, voice_id, speed=1.0, out_dir=OUTPUT_VOICE_DIR, log_fn=print):
    if not api_key:
        log_fn("❌ [Resona] Chưa nhập API Key.")
        return None
    hdrs = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "Accept": "application/json"}
    body = {
        "text": text,
        "voice_ids": [voice_id] if voice_id else [],
        "language": "vi",
        "speed": round(max(0.5, min(2.0, float(speed))), 2),
    }
    try:
        log_fn(f"🎙️ [Resona] TTS ({len(text)} ký tự, voice: {voice_id})...")
        r = requests.post(f"{RESONA_BASE}/speech/generate", json=body, headers=hdrs, timeout=30)
        if r.status_code not in (200, 201, 202):
            log_fn(f"❌ [Resona Error] HTTP {r.status_code}: {r.text[:200]}")
            return None
        res = r.json() if r.text else {}
        # Trả audio ngay?
        audio_url = res.get("audio_url") or res.get("url") or (res.get("result") or {}).get("audio_url")
        job_id = res.get("job_id") or res.get("id") or res.get("task_id") or (res.get("result") or {}).get("job_id")
        deadline = time.time() + 300
        while not audio_url and job_id and time.time() < deadline:
            time.sleep(2)
            pr = requests.get(f"{RESONA_BASE}/speech/status/{job_id}", headers=hdrs, timeout=15)
            if pr.status_code == 200:
                pres = pr.json() if pr.text else {}
                st = str(pres.get("status") or pres.get("job_status") or "").lower()
                audio_url = pres.get("audio_url") or pres.get("url") or (pres.get("result") or {}).get("audio_url")
                if audio_url:
                    break
                if st in ("failed", "error"):
                    log_fn(f"❌ [Resona] Job thất bại: {pres}")
                    return None
        if audio_url:
            out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
            op = out_dir / f"resona_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp3"
            op.write_bytes(requests.get(audio_url, headers=hdrs, timeout=120).content)
            log_fn(f"✔ [Resona] OK: {op.name}")
            return str(op)
        log_fn(f"❌ [Resona] Không lấy được audio (res={str(res)[:150]}).")
        return None
    except Exception as e:
        log_fn(f"❌ [Resona Exception] {e}")
        return None


# ─── ELEVENLABS (11labs.id.vn reseller) ───────────────────────────────
ELEVEN_IDVN_BASE = "https://11labs.id.vn/api/external/v1"

def elevenlabs_idvn_get_voices(api_key):
    if not api_key:
        return []
    out = []
    try:
        r = requests.get(f"{ELEVEN_IDVN_BASE}/voices.php",
                          params={"api_key": api_key, "language": "vi"},
                          headers={"x-api-key": api_key}, timeout=12)
        if r.status_code == 200:
            data = r.json()
            items = data.get("voices") or data.get("data") or (data if isinstance(data, list) else [])
            for v in items:
                if not isinstance(v, dict):
                    continue
                vid = v.get("voice_id") or v.get("id")
                if not vid:
                    continue
                labels = v.get("labels") or {}
                g = _norm_gender(labels.get("gender") or v.get("gender"))
                vtype = v.get("provider") or labels.get("accent") or labels.get("use_case") or "ElevenLabs"
                name = v.get("name") or vid
                out.append((str(vid), f"{_gender_icon(g)} {name} · {vtype}", g, str(vtype)))
    except Exception:
        pass
    if not out:
        # Voice mặc định phổ biến của ElevenLabs
        out = [
            ("21m00Tcm4TlvDq8ikWAM", "👩 Rachel · ElevenLabs", "female", "ElevenLabs"),
            ("EXAVITQu4vr4xnSDxMaL", "👩 Bella · ElevenLabs", "female", "ElevenLabs"),
            ("pNInz6obpgDQGcFmaJgB", "👨 Adam · ElevenLabs", "male", "ElevenLabs"),
            ("ErXwobaYiN019PkySvjV", "👨 Antoni · ElevenLabs", "male", "ElevenLabs"),
        ]
    return out

def elevenlabs_idvn_create_tts(text, api_key, voice_id, speed=1.0, out_dir=OUTPUT_VOICE_DIR, log_fn=print):
    if not api_key:
        log_fn("❌ [11Labs] Chưa nhập API Key.")
        return None
    hdrs = {"x-api-key": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    body = {
        "text": text,
        "voice_id": voice_id or "21m00Tcm4TlvDq8ikWAM",
        "voice_settings": {
            "speed": round(max(0.5, min(2.0, float(speed))), 2),
            "language": "Vietnamese",
            "normalization": True,
        },
    }
    try:
        log_fn(f"🎙️ [11Labs] TTS ({len(text)} ký tự, voice: {voice_id})...")
        r = requests.post(f"{ELEVEN_IDVN_BASE}/tts.php", json=body, headers=hdrs, timeout=30)
        if r.status_code not in (200, 201, 202):
            log_fn(f"❌ [11Labs Error] HTTP {r.status_code}: {r.text[:200]}")
            return None
        res = r.json() if r.text else {}
        job_id = res.get("job_id") or res.get("id") or (res.get("result") or {}).get("job_id")
        download_url = res.get("download_url") or res.get("audio_url")
        deadline = time.time() + 300
        while not download_url and job_id and time.time() < deadline:
            time.sleep(2)
            pr = requests.get(f"{ELEVEN_IDVN_BASE}/status.php",
                              params={"api_key": api_key, "job_id": job_id},
                              headers=hdrs, timeout=15)
            if pr.status_code == 200:
                pres = pr.json() if pr.text else {}
                st = str(pres.get("status", "")).lower()
                download_url = pres.get("download_url") or pres.get("audio_url")
                if download_url:
                    break
                if st == "failed":
                    log_fn(f"❌ [11Labs] Job thất bại: {pres}")
                    return None
        if download_url:
            out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
            op = out_dir / f"el11_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp3"
            op.write_bytes(requests.get(download_url, timeout=120).content)
            log_fn(f"✔ [11Labs] OK: {op.name}")
            return str(op)
        log_fn(f"❌ [11Labs] Không lấy được audio (res={str(res)[:150]}).")
        return None
    except Exception as e:
        log_fn(f"❌ [11Labs Exception] {e}")
        return None


# ─── REVIDAPI ─────────────────────────────────────────────────────────
REVIDAPI_BASE = "https://api.revidapi.com/paid"

def revidapi_get_voices(api_key=None):
    """RevidAPI (engine edge/capcut) — dùng danh sách voice tiếng Việt tích hợp."""
    return [
        ("vi-VN-HoaiMyNeural",  "👩 Hoài My · Edge (Nữ)",  "female", "Edge"),
        ("vi-VN-NamMinhNeural", "👨 Nam Minh · Edge (Nam)", "male",   "Edge"),
        ("vi-VN-HoaiMy",        "👩 Hoài My · CapCut (Nữ)", "female", "CapCut"),
        ("vi-VN-NamMinh",       "👨 Nam Minh · CapCut (Nam)", "male", "CapCut"),
        ("en-US-JennyNeural",   "👩 Jenny · Edge EN (Nữ)",  "female", "Edge EN"),
        ("en-US-GuyNeural",     "👨 Guy · Edge EN (Nam)",   "male",   "Edge EN"),
    ]

def revidapi_create_tts(text, api_key, voice_name, speed=1.0, out_dir=OUTPUT_VOICE_DIR, log_fn=print):
    if not api_key:
        log_fn("❌ [RevidAPI] Chưa nhập API Key.")
        return None
    hdrs = {"x-api-key": api_key, "Content-Type": "application/json"}
    engine = "capcut" if "capcut" in str(voice_name).lower() else "edge"
    body = {
        "text": text,
        "engine": engine,
        "voice": voice_name or "vi-VN-HoaiMyNeural",
        "speed": round(max(0.5, min(2.0, float(speed))), 2),
    }
    try:
        log_fn(f"🎙️ [RevidAPI] TTS ({len(text)} ký tự, voice: {voice_name}, engine: {engine})...")
        r = requests.post(f"{REVIDAPI_BASE}/text-to-speech", json=body, headers=hdrs, timeout=30)
        if r.status_code not in (200, 201, 202):
            log_fn(f"❌ [RevidAPI Error] HTTP {r.status_code}: {r.text[:200]}")
            return None
        res = r.json() if r.text else {}
        audio_url = res.get("audio_url") or (res.get("response") or {}).get("audio_url")
        job_id = res.get("task_id") or res.get("job_id") or res.get("id")
        deadline = time.time() + 300
        while not audio_url and job_id and time.time() < deadline:
            time.sleep(2)
            pr = requests.post(f"{REVIDAPI_BASE}/get/job/status", json={"job_id": job_id}, headers=hdrs, timeout=15)
            if pr.status_code == 200:
                pres = pr.json() if pr.text else {}
                st = str(pres.get("job_status") or pres.get("status") or "").lower()
                resp_obj = pres.get("response") or {}
                audio_url = resp_obj.get("audio_url") or pres.get("audio_url")
                if audio_url:
                    break
                if st in ("failed", "error"):
                    log_fn(f"❌ [RevidAPI] Job thất bại: {pres}")
                    return None
        if audio_url:
            out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
            op = out_dir / f"revid_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp3"
            op.write_bytes(requests.get(audio_url, timeout=120).content)
            log_fn(f"✔ [RevidAPI] OK: {op.name}")
            return str(op)
        log_fn(f"❌ [RevidAPI] Không lấy được audio (res={str(res)[:150]}).")
        return None
    except Exception as e:
        log_fn(f"❌ [RevidAPI Exception] {e}")
        return None


# ─── TTS DISPATCHER (thống nhất mọi provider) ─────────────────────────
# Mã engine ↔ nhãn hiển thị. Thứ tự = thứ tự trong combo chọn engine.
TTS_ENGINES = [
    ("gtts",            "gTTS Tiếng Việt (Miễn phí)"),
    ("vbee",            "Vbee API"),
    ("resona",          "Resona.live"),
    ("elevenlabs_idvn", "ElevenLabs (11labs.id.vn)"),
    ("revidapi",        "RevidAPI (Edge / CapCut)"),
]
TTS_MULTIVOICE_ENGINES = {"vbee", "resona", "elevenlabs_idvn", "revidapi"}

def tts_get_voices(engine, settings):
    """Lấy danh sách voice cho engine hiện tại (dùng cho UI)."""
    if engine == "vbee":
        return vbee_get_voices(settings.get("vbee_token", ""), settings.get("vbee_appid", ""))
    if engine == "resona":
        return resona_get_voices(settings.get("resona_key", ""))
    if engine == "elevenlabs_idvn":
        return elevenlabs_idvn_get_voices(settings.get("el_idvn_key", ""))
    if engine == "revidapi":
        return revidapi_get_voices(settings.get("revidapi_key", ""))
    return []

def tts_provider_ready(settings) -> bool:
    """Provider (khác gTTS) đã có đủ thông tin đăng nhập chưa."""
    engine = settings.get("voice_engine", "gtts")
    if engine == "vbee":
        return bool(settings.get("vbee_token") and settings.get("vbee_appid"))
    if engine == "resona":
        return bool(settings.get("resona_key"))
    if engine == "elevenlabs_idvn":
        return bool(settings.get("el_idvn_key"))
    if engine == "revidapi":
        return bool(settings.get("revidapi_key"))
    return False

def tts_synthesize(text, settings, out_dir=OUTPUT_VOICE_DIR, voice_override=None, log_fn=print):
    """Tổng hợp giọng nói theo engine đang chọn. Trả về path .mp3 hoặc None."""
    engine = settings.get("voice_engine", "gtts")
    speed = float(settings.get("vbee_speed", 1.0) or 1.0)
    voice = voice_override or settings.get("vbee_voice", "")
    if engine == "vbee":
        return vbee_create_tts(text, settings.get("vbee_token", ""), settings.get("vbee_appid", ""),
                               voice or "vi_south_female_miien_48k", speed, out_dir, log_fn)
    if engine == "resona":
        return resona_create_tts(text, settings.get("resona_key", ""), voice, speed, out_dir, log_fn)
    if engine == "elevenlabs_idvn":
        return elevenlabs_idvn_create_tts(text, settings.get("el_idvn_key", ""),
                                          voice or "21m00Tcm4TlvDq8ikWAM", speed, out_dir, log_fn)
    if engine == "revidapi":
        return revidapi_create_tts(text, settings.get("revidapi_key", ""),
                                   voice or "vi-VN-HoaiMyNeural", speed, out_dir, log_fn)
    # gTTS mặc định
    try:
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        op = out_dir / f"gtts_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp3"
        gTTS(text=text, lang='vi').save(str(op))
        return str(op)
    except Exception as e:
        log_fn(f"❌ [gTTS] {e}")
        return None

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

# ─── UI SMOOTHNESS HELPERS (cache ảnh + việc nặng chạy nền) ──────────────
# Cache pixmap đã scale theo (path, mtime, size) — tránh decode lại ảnh lớn
# từ đĩa mỗi lần UI refresh (nguyên nhân chính gây khựng khi pipeline chạy).
_PIXMAP_CACHE: Dict[tuple, QPixmap] = {}
_PIXMAP_CACHE_MAX = 512

def load_scaled_pixmap(path, w: int, h: int) -> Optional[QPixmap]:
    """Load ảnh + scale (giữ tỉ lệ) với cache. Decode trực tiếp ở kích thước
    nhỏ qua QImageReader nên nhanh hơn nhiều so với QPixmap full-size."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size, w, h)
    pix = _PIXMAP_CACHE.get(key)
    if pix is not None:
        return pix
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    sz = reader.size()
    if sz.isValid() and sz.width() > 0 and sz.height() > 0:
        reader.setScaledSize(sz.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatio))
    img = reader.read()
    if img.isNull():
        return None
    pix = QPixmap.fromImage(img)
    if len(_PIXMAP_CACHE) >= _PIXMAP_CACHE_MAX:
        # Bỏ bớt các entry cũ nhất (dict giữ thứ tự chèn)
        for _k in list(_PIXMAP_CACHE.keys())[:64]:
            _PIXMAP_CACHE.pop(_k, None)
    _PIXMAP_CACHE[key] = pix
    return pix

# Trích thumbnail video bằng ffmpeg ở thread nền (trước đây chạy đồng bộ
# trên UI thread, block tới 10s mỗi clip).
_VIDEO_THUMB_JOBS: set = set()
_VIDEO_JOB_LOCK = threading.Lock()

def spawn_video_thumb_job(video_path: str, thumb_path: str):
    """Chạy ffmpeg tạo thumbnail ở thread nền; bỏ qua nếu job đã chạy."""
    with _VIDEO_JOB_LOCK:
        if thumb_path in _VIDEO_THUMB_JOBS:
            return
        _VIDEO_THUMB_JOBS.add(thumb_path)
    def _job():
        try:
            cmd = [FFMPEG_PATH, "-y", "-i", str(video_path), "-ss", "0.5", "-vframes", "1",
                   "-vf", "scale=160:-1", str(thumb_path)]
            subprocess.run(cmd, capture_output=True, timeout=15,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except Exception:
            pass
        finally:
            with _VIDEO_JOB_LOCK:
                _VIDEO_THUMB_JOBS.discard(thumb_path)
    threading.Thread(target=_job, daemon=True).start()

# Đọc duration video bằng ffprobe ở thread nền, kết quả cache theo (path, mtime).
_MEDIA_INFO_CACHE: Dict[tuple, Optional[float]] = {}
_MEDIA_INFO_JOBS: set = set()

def get_video_duration_cached(path) -> tuple:
    """Trả về ("ready", duration|None) nếu đã có kết quả; ("pending", None) nếu
    ffprobe đang chạy nền — UI poll lại sau, không bao giờ block."""
    try:
        st = os.stat(path)
    except OSError:
        return ("ready", None)
    key = (str(path), st.st_mtime_ns)
    if key in _MEDIA_INFO_CACHE:
        return ("ready", _MEDIA_INFO_CACHE[key])
    with _VIDEO_JOB_LOCK:
        if key in _MEDIA_INFO_JOBS:
            return ("pending", None)
        _MEDIA_INFO_JOBS.add(key)
    def _job():
        dur = None
        try:
            result = subprocess.run(
                [FFPROBE_PATH, "-v", "quiet", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, timeout=10, encoding='utf-8', errors='replace',
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            dur = float(result.stdout.strip())
        except Exception:
            dur = None
        _MEDIA_INFO_CACHE[key] = dur
        with _VIDEO_JOB_LOCK:
            _MEDIA_INFO_JOBS.discard(key)
    threading.Thread(target=_job, daemon=True).start()
    return ("pending", None)

def get_media_duration(fp):
    if not fp or not Path(fp).exists(): return 0.0
    try:
        r = subprocess.run([FFPROBE_PATH,"-v","quiet","-show_entries","format=duration","-of","csv=p=0",str(fp)],capture_output=True,timeout=10,encoding='utf-8',errors='replace')
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
            r = subprocess.run(cmd, capture_output=True, timeout=15, encoding='utf-8', errors='replace')
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
            r = subprocess.run(cmd, capture_output=True, timeout=15, encoding='utf-8', errors='replace')
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
    video_prompt_override: str = "" # Prompt thay thế do safety rewrite, không đổi ảnh
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
            video_prompt_override=d.get("video_prompt_override", ""),
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
    created_at:str=field(default_factory=lambda:datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    products:List[ProductItem]=field(default_factory=list)
    def to_dict(self): return {"id":self.id,"name":self.name,"created_at":self.created_at,"products":[p.to_dict() for p in self.products]}
    @staticmethod
    def from_dict(d): return ThreadsProject(d.get("id",uuid.uuid4().hex[:8]),d.get("name","Dự Án Mới"),d.get("created_at",""),[ProductItem.from_dict(p) for p in d.get("products",[])])

# ─── VIDEO REVIEW PROCESSING UTILITIES ────────────────────────────────

def process_external_video(input_path, output_path, settings, bg_path=None, log_fn=None):
    """Xử lý video file ngoài với các tham số anti-reup.
    Crop, speed, zoom, flip, noise, random cut."""
    import random as _rnd
    filters = []
    keep_audio = settings.get("vr_keep_audio", True)

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

    # 3. Zoom custom
    if settings.get("vr_zoom_custom", False):
        zoom_val = settings.get("vr_zoom_factor", 100.0) / 100.0
        if abs(zoom_val - 1.0) > 0.001:
            if zoom_val > 1.0:
                filters.append(f"scale={zoom_val:.3f}*iw:{zoom_val:.3f}*ih,crop=iw/{zoom_val:.3f}:ih/{zoom_val:.3f}")
            else:
                filters.append(f"scale={zoom_val:.3f}*iw:{zoom_val:.3f}*ih,pad=iw/{zoom_val:.3f}:ih/{zoom_val:.3f}:(ow-iw)/2:(oh-ih)/2")
    # Zoom random fallback
    elif settings.get("vr_zoom_random", False):
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
                           "-t", f"{se - ss:.3f}"]
                    if not keep_audio:
                        cmd.append("-an")
                    else:
                        cmd.extend(["-c:a", "aac"])
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
                           "-crf", "22"]
                    if keep_audio:
                        cmd.extend(["-c:a", "aac"])
                    cmd.append(str(output_path))
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
    
    # Fallback: simple filter without random cut
    if not Path(output_path).exists():
        vf = ",".join(filters) if filters else None
        cmd = [FFMPEG_PATH, "-y", "-i", str(input_path), "-t", str(max_dur)]
        if not keep_audio:
            cmd.append("-an")
        else:
            cmd.extend(["-c:a", "aac"])
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

    # Apply background overlay if requested
    if Path(output_path).exists() and bg_path and settings.get("vr_overlay_bg", False) and Path(bg_path).exists():
        if log_fn: log_fn("    🏞️ Đang lồng Video Review lên nền background (Overlay)...")
        temp_overlay = Path(output_path).parent / f"temp_overlay_{uuid.uuid4().hex[:6]}.mp4"
        overlay_scale = settings.get("vr_overlay_scale", 85) / 100.0
        
        bg_dur = get_media_duration(bg_path) or 10.0
        start_time = _rnd.uniform(0.0, bg_dur)
        filter_complex = (
            f"[1:v]scale=1080*{overlay_scale}:-1[over_v];"
            f"[0:v]trim={start_time:.2f}:{start_time + get_media_duration(str(output_path)):.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg_v];"
            f"[bg_v][over_v]overlay=(W-w)/2:(H-h)/2[out_v]"
        )
        cmd = [
            FFMPEG_PATH, "-y",
            "-stream_loop", "-1", "-i", str(bg_path),
            "-i", str(output_path),
            "-filter_complex", filter_complex,
            "-map", "[out_v]"
        ]
        
        has_audio = False
        try:
            cmd_probe = [FFPROBE_PATH, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output_path)]
            r = subprocess.run(cmd_probe, capture_output=True, timeout=10, encoding='utf-8', errors='replace')
            if "audio" in r.stdout:
                has_audio = True
        except Exception:
            pass
            
        if keep_audio and has_audio:
            cmd.extend(["-map", "1:a", "-c:a", "aac"])
        else:
            cmd.append("-an")
            
        cmd.extend([
            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
            "-t", f"{get_media_duration(str(output_path)):.2f}",
            str(temp_overlay)
        ])
        
        try:
            subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if temp_overlay.exists():
                try: Path(output_path).unlink(missing_ok=True)
                except: pass
                temp_overlay.replace(output_path)
                if log_fn: log_fn("      ✔ Lồng background hoàn tất.")
        except Exception as e_overlay:
            if log_fn: log_fn(f"      ❌ Lỗi lồng background: {e_overlay}")
            try: Path(temp_overlay).unlink(missing_ok=True)
            except: pass

    return str(output_path) if Path(output_path).exists() else None


def add_voiceover_to_video(video_path, voice_path, keep_original_audio, output_path, log_fn=None):
    if not voice_path or not Path(voice_path).exists():
        import shutil
        shutil.copy2(video_path, output_path)
        return str(output_path)
        
    has_audio = False
    try:
        cmd_probe = [FFPROBE_PATH, "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(video_path)]
        r = subprocess.run(cmd_probe, capture_output=True, timeout=10, encoding='utf-8', errors='replace')
        if "audio" in r.stdout:
            has_audio = True
    except Exception:
        pass

    if has_audio and keep_original_audio:
        if log_fn: log_fn("      🔊 Trộn Voiceover AI với âm thanh gốc của Video Review (giảm âm lượng gốc xuống 30%)...")
        filter_complex = "[0:a]volume=0.3[bg_a];[bg_a][1:a]amix=inputs=2:duration=first[out_a]"
        cmd = [
            FFMPEG_PATH, "-y",
            "-i", str(video_path),
            "-i", str(voice_path),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[out_a]",
            "-c:v", "copy",
            "-c:a", "aac",
            str(output_path)
        ]
    else:
        if log_fn: log_fn("      🔇 Chỉ ghép Voiceover AI vào Video Review (âm thanh gốc tắt)...")
        cmd = [
            FFMPEG_PATH, "-y",
            "-i", str(video_path),
            "-i", str(voice_path),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            str(output_path)
        ]
        
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return str(output_path) if Path(output_path).exists() else str(video_path)
    except Exception as e:
        if log_fn: log_fn(f"      ⚠️ Lỗi trộn audio: {e}. Giữ video gốc.")
        import shutil
        shutil.copy2(video_path, output_path)
        return str(output_path)


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


# ─── FAKE METADATA (giả lập video quay/dựng từ thiết bị & app tin cậy) ──
# Bộ hồ sơ thiết bị + phần mềm dựng thực tế. Mục tiêu: metadata trông tự nhiên
# như clip quay bằng điện thoại và biên tập bằng CapCut/Premiere...
_DEVICE_PROFILES = [
    # (make, model, os_software, com.android.version | None)
    ("Apple",   "iPhone 15 Pro",       "17.5.1", None),
    ("Apple",   "iPhone 14 Pro Max",   "17.4.1", None),
    ("Apple",   "iPhone 13",           "16.6",   None),
    ("samsung", "SM-S918B",            "14",     "34"),   # Galaxy S23 Ultra
    ("samsung", "SM-A546E",            "13",     "33"),   # Galaxy A54
    ("Xiaomi",  "23021RAA2Y",          "13",     "33"),   # Redmi Note 12
    ("OPPO",    "CPH2451",             "13",     "33"),
]
_EDITOR_SOFTWARE = [
    "CapCut", "CapCut 11.5.0", "VN - Video Editor",
    "Adobe Premiere Pro 2024", "InShot", "Filmora",
]

def apply_natural_metadata(video_path, settings=None, log_fn=None):
    """Ghi đè metadata của video output cuối cho tự nhiên (thiết bị + app dựng).
    Chỉ remux (-c copy) nên rất nhanh, không giảm chất lượng.
    Bỏ qua nếu settings tắt fake_metadata."""
    try:
        if settings is not None and not settings.get("fake_metadata", True):
            return str(video_path)
        vp = Path(video_path)
        if not vp.exists():
            return str(video_path)

        make, model, os_ver, android_ver = random.choice(_DEVICE_PROFILES)
        editor = random.choice(_EDITOR_SOFTWARE)
        is_apple = (make == "Apple")

        # Thời điểm "quay" ngẫu nhiên trong vòng ~45 ngày gần đây
        shot_dt = datetime.now() - timedelta(
            days=random.randint(1, 45), hours=random.randint(0, 23),
            minutes=random.randint(0, 59), seconds=random.randint(0, 59)
        )
        creation_iso = shot_dt.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        apple_date = shot_dt.strftime("%Y-%m-%dT%H:%M:%S+0700")

        v_handler = "Core Media Video" if is_apple else "VideoHandle"
        a_handler = "Core Media Audio" if is_apple else "SoundHandle"

        # -fflags +bitexact: chặn ffmpeg tự ghi tag "encoder=LavfXX" (dấu vết lộ ffmpeg).
        cmd = [FFMPEG_PATH, "-y", "-i", str(vp), "-map_metadata", "-1", "-fflags", "+bitexact",
               "-metadata", f"creation_time={creation_iso}",
               "-metadata", f"date={shot_dt.strftime('%Y-%m-%d')}",
               "-metadata", f"comment=Edited with {editor}",
               "-metadata", f"make={make}",
               "-metadata", f"model={model}",
               "-metadata:s:v", f"handler_name={v_handler}",
               "-metadata:s:a", f"handler_name={a_handler}"]

        if is_apple:
            cmd += [
                "-metadata", f"com.apple.quicktime.make={make}",
                "-metadata", f"com.apple.quicktime.model={model}",
                "-metadata", f"com.apple.quicktime.software={os_ver}",
                "-metadata", f"com.apple.quicktime.creationdate={apple_date}",
            ]
        else:
            cmd += [
                "-metadata", f"com.android.version={android_ver or '13'}",
                "-metadata", f"com.android.manufacturer={make}",
                "-metadata", f"com.android.model={model}",
            ]

        tmp_out = vp.parent / f".meta_{uuid.uuid4().hex[:6]}_{vp.name}"
        cmd += ["-movflags", "use_metadata_tags+faststart", "-c", "copy", str(tmp_out)]

        subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if tmp_out.exists() and tmp_out.stat().st_size > 0:
            try: vp.unlink(missing_ok=True)
            except Exception: pass
            tmp_out.replace(vp)
            if log_fn: log_fn(f"    🛡️ Fake metadata OK: giả lập {make} {model} · dựng bằng {editor}")
        return str(vp)
    except Exception as e:
        if log_fn: log_fn(f"    ⚠️ Bỏ qua fake metadata (lỗi: {e}). Giữ video gốc.")
        return str(video_path)


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
        # Products may enter the early Video Review stream concurrently during
        # Phase 2.  Phase 3 uses this set to assemble those clips instead of
        # rendering them a second time (especially when "render lại" is on).
        self._vr_pre_rendered_products = set()
        self._vr_pre_rendered_lock = threading.Lock()
        # ThreadPoolExecutor workers must never emit PyQt signals directly.
        # Events are drained by the GUI timer on the Qt main thread instead.
        self._gui_event_queue = queue.Queue()

    def _log(self, msg, prod_tag=None):
        if prod_tag:
            text = f"[{prod_tag}] {msg}"
        else:
            text = msg
        self._gui_event_queue.put(("log", text))

    def _queue_product_updated(self, product_id):
        self._gui_event_queue.put(("product", product_id))

    def _queue_finished(self, ok, message):
        self._gui_event_queue.put(("done", (bool(ok), str(message))))

    def drain_gui_events(self, limit=500):
        """Return worker events without touching Qt from a render thread."""
        events = []
        for _ in range(limit):
            try:
                events.append(self._gui_event_queue.get_nowait())
            except queue.Empty:
                break
        return events

    def _next_vr_google_account(self, accounts, exclude_names=None):
        """Choose a non-throttled account across all runners in this process."""
        global _VR_GOOGLE_ACCOUNT_RR_INDEX
        if not accounts:
            return None
        excluded = {
            str(name or "").strip().lower()
            for name in (exclude_names or set())
            if str(name or "").strip()
        }
        now = time.time()
        with _VR_GOOGLE_ACCOUNT_HEALTH_LOCK:
            ready = [
                account for account in accounts
                if str(account.name or "").strip().lower() not in excluded
                and _VR_GOOGLE_ACCOUNT_COOLDOWN_UNTIL.get(
                    str(account.name or "").strip().lower(), 0.0
                ) <= now
            ]
            if not ready:
                ready = [
                    account for account in accounts
                    if str(account.name or "").strip().lower() not in excluded
                ]
            if not ready:
                ready = list(accounts)
        with _VR_GOOGLE_ACCOUNT_RR_LOCK:
            account = ready[_VR_GOOGLE_ACCOUNT_RR_INDEX % len(ready)]
            _VR_GOOGLE_ACCOUNT_RR_INDEX += 1
        return account

    def _get_vr_google_account_cooldown(self, account_name):
        key = str(account_name or "").strip().lower()
        if not key:
            return 0.0
        with _VR_GOOGLE_ACCOUNT_HEALTH_LOCK:
            return max(
                0.0,
                _VR_GOOGLE_ACCOUNT_COOLDOWN_UNTIL.get(key, 0.0) - time.time(),
            )

    def _mark_vr_google_account_429(self, account_name):
        """Temporarily quarantine an account that Flow has rate-limited."""
        key = str(account_name or "").strip().lower()
        if not key:
            return 0.0
        with _VR_GOOGLE_ACCOUNT_HEALTH_LOCK:
            streak = _VR_GOOGLE_ACCOUNT_429_STREAK.get(key, 0) + 1
            _VR_GOOGLE_ACCOUNT_429_STREAK[key] = streak
            # 5, 10, then 15 minutes. RESOURCE_EXHAUSTED commonly lasts much
            # longer than one scene retry; a 60-second quarantine caused the
            # same bad account to re-enter the pool repeatedly in one project.
            # A successful render resets this streak. Healthy accounts keep
            # rendering while only the exhausted account cools down.
            cooldown = min(900.0, 300.0 * (2 ** min(streak - 1, 2)))
            until = time.time() + cooldown
            _VR_GOOGLE_ACCOUNT_COOLDOWN_UNTIL[key] = max(
                _VR_GOOGLE_ACCOUNT_COOLDOWN_UNTIL.get(key, 0.0), until
            )
        return cooldown

    def _mark_vr_google_account_healthy(self, account_name):
        key = str(account_name or "").strip().lower()
        if not key:
            return
        with _VR_GOOGLE_ACCOUNT_HEALTH_LOCK:
            _VR_GOOGLE_ACCOUNT_429_STREAK.pop(key, None)
            _VR_GOOGLE_ACCOUNT_COOLDOWN_UNTIL.pop(key, None)

    def _get_vr_google_account_slot(self, account_name):
        key = str(account_name or "__google_default__").strip().lower()
        try:
            limit = max(1, min(8, int(self.settings.get("vid_threads", 6))))
        except Exception:
            limit = 6
        with _VR_GOOGLE_ACCOUNT_SLOTS_LOCK:
            slot = _VR_GOOGLE_ACCOUNT_SLOTS.get(key)
            if slot is None:
                slot = _VRAccountConcurrencyGate(limit)
                _VR_GOOGLE_ACCOUNT_SLOTS[key] = slot
            else:
                slot.set_limit(limit)
        return slot

    def _sanitize_filename(self, name):
        s = re.sub(r'[\\/*?:"<>|]', "", name)
        return s.strip()[:100]

    def _prepare_product_dirs(self, prod, out_base):
        """Create dedicated product folder and sub-directories."""
        prod_dir_name = f"{self._sanitize_filename(prod.title or 'san_pham')}_{prod.id}"
        prod_folder = out_base / prod_dir_name
        prod_folder.mkdir(parents=True, exist_ok=True)
        (prod_folder / "images").mkdir(exist_ok=True)
        (prod_folder / "voice").mkdir(exist_ok=True)
        return prod_folder

    def _generate_vr_image(self, prod, prod_folder, scene_idx, vr_scene,
                           image_model, aspect, api_url, api_key, prod_tag,
                           use_product_reference=True):
        """Create one Video Review image and return its final path.

        This deliberately uses the same reference-image API path as the
        normal product-image renderer.  Keeping it in one small worker makes
        the streaming scheduler below able to hand a completed image directly
        to a video worker instead of waiting for the whole image batch.
        """
        if self._stopped:
            return None

        vr_dir = prod_folder / "vr_clips"
        vr_dir.mkdir(parents=True, exist_ok=True)
        target = vr_dir / f"scene_{scene_idx + 1}.png"
        acc = self.acc_mgr.next_codex_rr()
        request_url = str((acc.proxy_url if acc and acc.proxy_url else api_url) or "").rstrip("/")
        request_key = acc.api_key if acc else api_key
        account_name = acc.name if acc else "Default"
        prompt = (getattr(vr_scene, "image_prompt", "") or "").strip()
        aspect = aspect or "9:16"
        size = (
            "1024x1792" if "9:16" in aspect
            else ("1792x1024" if "16:9" in aspect else "1024x1024")
        )

        try:
            self._log(
                f"    [VR image {scene_idx + 1}] ({account_name}) "
                f"{prompt[:60]}...",
                prod_tag,
            )

            # The first product image is the reference image when available.
            # This preserves the existing product-consistency behavior.
            reference_path = None
            if use_product_reference:
                for ref in (prod.images or []):
                    if ref and Path(ref).exists():
                        reference_path = Path(ref)
                        break

            if reference_path:
                with reference_path.open("rb") as ref_file:
                    ref_bytes = ref_file.read()
                ext = reference_path.suffix.lower().lstrip(".") or "jpeg"
                mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                response = requests.post(
                    f"{request_url}/v1/images/edits",
                    headers={"Authorization": f"Bearer {request_key}"},
                    files={"image": (reference_path.name, ref_bytes, mime)},
                    data={"model": image_model, "prompt": prompt, "n": "1", "size": size},
                    timeout=600,
                )
            else:
                response = requests.post(
                    f"{request_url}/v1/images/generations",
                    headers={
                        "Authorization": f"Bearer {request_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": image_model,
                        "prompt": prompt,
                        "n": 1,
                        "size": size,
                        "response_format": "b64_json",
                    },
                    timeout=600,
                )

            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")

            item = (response.json().get("data") or [{}])[0]
            image_bytes = None
            if item.get("b64_json"):
                import base64
                image_bytes = base64.b64decode(item["b64_json"])
            elif item.get("url"):
                image_response = requests.get(item["url"], timeout=60)
                if image_response.status_code == 200:
                    image_bytes = image_response.content

            if not image_bytes:
                raise RuntimeError("API không trả về dữ liệu ảnh")

            # Never expose a partially written image to the video worker.
            temp_target = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            temp_target.write_bytes(image_bytes)
            temp_target.replace(target)
            vr_scene.image_path = str(target)
            vr_scene.status = "processing"
            self._queue_product_updated(prod.id)
            self._log(f"      [VR image {scene_idx + 1}] OK -> bắt đầu tạo video", prod_tag)
            return str(target)
        except Exception as exc:
            vr_scene.status = "error"
            self._queue_product_updated(prod.id)
            self._log(f"      [VR image {scene_idx + 1}] lỗi: {exc}", prod_tag)
            return None

    def _run_vr_scene_stream(self, prod, prod_folder, scenes, scene_data,
                             video_model, resolution, clip_duration, aspect,
                             image_model, api_url, api_key, force_regen,
                             image_threads, video_threads):
        """Run the Video Review image -> video producer/consumer pipeline.

        Images are the producer.  Each completed image immediately submits one
        video job to the bounded video pool; image completion is never gated by
        the rest of the image batch.  The small safety caps are local to this
        AI-review pipeline so the normal product image/video paths keep their
        configured concurrency unchanged.
        """
        if not scenes or self._stopped:
            return []

        vr_dir = prod_folder / "vr_clips"
        vr_dir.mkdir(parents=True, exist_ok=True)
        prod_tag = (prod.title or "SP")[:20]
        scene_data = scene_data or []
        use_veo = is_veo_model(video_model)
        google_accounts = list(self.acc_mgr.get_all_google()) if use_veo else []
        results = {}
        results_lock = threading.Lock()
        image_futures = {}
        video_futures = {}
        # Prompt replacements are local to this render run.  The reference
        # image is never changed; only the text sent to Veo may be replaced.
        prompt_overrides = {}

        def _valid_local_file(value):
            if not value:
                return False
            try:
                path = Path(value)
                return path.is_file() and path.stat().st_size > 0
            except (OSError, TypeError, ValueError):
                return False

        def _int_setting(value, fallback):
            try:
                return max(1, int(value))
            except Exception:
                return fallback

        # Keep image production bounded so it cannot flood the video queue.
        # Video concurrency still honors the UI setting (up to its existing
        # maximum of 8); the combined VR worker budget is capped at 12.
        image_cap = min(4, _int_setting(image_threads, 1))
        video_cap = min(8, _int_setting(video_threads, 1), max(1, 12 - image_cap))

        def _scene_payload(scene_idx):
            data = scene_data[scene_idx] if scene_idx < len(scene_data) else {}
            image_prompt = (data.get("image_prompt") or getattr(scenes[scene_idx], "image_prompt", "") or "").strip()
            video_prompt = (data.get("video_prompt") or getattr(scenes[scene_idx], "video_prompt", "") or "").strip()
            full_prompt = f"{video_prompt}. {image_prompt}" if video_prompt and image_prompt else (video_prompt or image_prompt)
            persisted_override = data.get("video_prompt_override") or getattr(scenes[scene_idx], "video_prompt_override", "")
            if persisted_override:
                full_prompt = persisted_override
            elif scene_idx in prompt_overrides:
                full_prompt = prompt_overrides[scene_idx]
            return image_prompt, full_prompt

        def _is_prominent_people_filter_error(error_text):
            text = str(error_text or "").lower()
            return "public_error_prominent_people_filter_failed" in text or "prominent_people_filter_failed" in text

        def _rewrite_vr_prompt(scene_idx, bad_prompt, revision_no):
            """Ask the existing LLM channel to rewrite only one failed Veo prompt."""
            try:
                acc = self.acc_mgr.next_codex_rr()
                llm_url = acc.proxy_url if acc else self.settings.get("api_url", DEFAULT_PROXY_URL)
                llm_key = acc.api_key if acc else self.settings.get("api_key", "grok-video-key-001")
                llm_model = self.settings.get("llm_model", "gpt-5.5")
                if not llm_url or not llm_key:
                    raise RuntimeError("Không có tài khoản LLM khả dụng")

                system_prompt = (
                    "You are a safety-focused Veo prompt editor. Rewrite exactly one video prompt. "
                    "Keep the same product, action, camera movement, lighting, duration and vertical 9:16 intent. "
                    "The existing reference image must remain unchanged. Remove every reference to a real, famous, "
                    "recognizable or public person, celebrity, KOL, influencer, idol, actress, singer, politician, "
                    "brand ambassador or lookalike. Use a fictional adult model with a non-identifiable face, or "
                    "hands/product-only framing when a person is not necessary. Do not mention this safety error. "
                    "Return JSON only: {\"video_prompt\": \"rewritten English prompt\"}."
                )
                user_prompt = (
                    f"Scene index: {scene_idx + 1}\n"
                    f"Original Veo prompt:\n{bad_prompt}\n\n"
                    "Rewrite only the prompt text. Do not request a new image and do not change the reference image."
                )
                response = requests.post(
                    f"{str(llm_url).rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
                    json={
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                    },
                    timeout=180,
                )
                if response.status_code != 200:
                    raise RuntimeError(f"LLM HTTP {response.status_code}: {response.text[:180]}")
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = parse_llm_json_robust(content)
                rewritten = parsed.get("video_prompt", "") if isinstance(parsed, dict) else ""
                if not rewritten:
                    rewritten = str(content or "").strip().strip("`")
                rewritten = re.sub(r"^json\s*", "", rewritten, flags=re.IGNORECASE).strip()
                if len(rewritten) < 30:
                    raise RuntimeError("LLM trả về prompt quá ngắn")
                self._log(
                    f"    [VR video {scene_idx + 1}] LLM đã viết lại prompt lần {revision_no}: {rewritten[:100]}...",
                    prod_tag,
                )
                return rewritten
            except Exception as exc:
                self._log(
                    f"    [VR video {scene_idx + 1}] không thể viết lại prompt bằng LLM: {exc}",
                    prod_tag,
                )
                return None

        def _safe_vr_image_fallback():
            """Return a product-aware frame prompt with no portrait subject."""
            product_title = re.sub(
                r"^\s*\[[^\]]+\]\s*", "", str(getattr(prod, "title", "") or "")
            ).strip()
            product_title = re.sub(r"\s+", " ", product_title)[:180]
            product_label = product_title or "the current product"
            return (
                f"Vertical 9:16 photorealistic commercial product demonstration still for {product_label}. "
                "Show only the correct product category, its own packaging and its defining physical details, "
                "with a clean neutral studio surface and simple product-focused composition. "
                "Preserve the product identity from the title and scene context; never substitute an unrelated product. "
                "Use anonymous hands or neutral non-human props only when needed. Crisp realistic materials, "
                "soft diffused commercial lighting, no portrait, no identifiable subject, no added text overlay and no watermark."
            )

        def _rewrite_vr_image_prompt(scene_idx, bad_image_prompt, revision_no):
            """Ask the LLM for a safe replacement image prompt.

            A prompt rewrite alone cannot repair an image that already contains
            a face.  The replacement is intentionally product/hand/mannequin
            focused and is rendered without the original face-containing
            product reference image.
            """
            fallback = _safe_vr_image_fallback()
            try:
                acc = self.acc_mgr.next_codex_rr()
                llm_url = acc.proxy_url if acc else self.settings.get("api_url", DEFAULT_PROXY_URL)
                llm_key = acc.api_key if acc else self.settings.get("api_key", "grok-video-key-001")
                llm_model = self.settings.get("llm_model", "gpt-5.5")
                if not llm_url or not llm_key:
                    return fallback

                product_title = str(getattr(prod, "title", "") or "").strip()
                system_prompt = (
                    "You are a safety-focused image prompt editor for a product commercial. "
                    "Rewrite one image prompt while preserving the exact current product category, product packaging, "
                    "materials, action and vertical 9:16 composition. The output must be product-focused and may use "
                    "anonymous hands or neutral non-human props only; do not create a portrait or identifiable subject. "
                    "Never replace the current product with an unrelated category. Do not include names, social handles, "
                    "public figures, celebrity references, lookalikes or influencer language. "
                    f"Current product title: {product_title}\n"
                    "Return JSON only: {\"image_prompt\": \"rewritten English prompt\"}."
                )
                user_prompt = (
                    f"Scene index: {scene_idx + 1}\n"
                    f"Current product title: {product_title}\n"
                    f"Original image prompt:\n{bad_image_prompt}\n\n"
                    "Rewrite only the still-image prompt. Keep the current product category and make the result suitable as a Veo reference frame."
                )
                response = requests.post(
                    f"{str(llm_url).rstrip('/')}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {llm_key}", "Content-Type": "application/json"},
                    json={
                        "model": llm_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.2,
                    },
                    timeout=180,
                )
                if response.status_code != 200:
                    return fallback
                content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                parsed = parse_llm_json_robust(content)
                rewritten = parsed.get("image_prompt", "") if isinstance(parsed, dict) else ""
                if not rewritten:
                    rewritten = str(content or "").strip().strip("`")
                rewritten = re.sub(r"^json\s*", "", rewritten, flags=re.IGNORECASE).strip()

                # Do not trust an LLM result that reintroduces a portrait or
                # identity-bearing wording. Use the deterministic safe prompt.
                risky_terms = (
                    "celebrity", "famous", "public figure", "lookalike", "influencer",
                    "kol", "idol", "actress", "singer", "politician", "portrait",
                    "selfie", "young woman", "young girl", "recognizable face",
                    "full face", "realistic woman", "real person", "woman", "girl",
                    "man", "boy", "person", "people", "human", "adult", "female",
                    "male", "face", "skin", "hair", "eyebrow", "cheek", "lips",
                    "smile", "model",
                )
                if len(rewritten) < 30 or any(term in rewritten.lower() for term in risky_terms):
                    return fallback
                return rewritten
            except Exception:
                return fallback

        def _regenerate_safe_vr_image(scene_idx, revision_no):
            """Regenerate the failed scene image without the original face reference."""
            if scene_idx >= len(scenes):
                return None
            scene = scenes[scene_idx]
            old_prompt = getattr(scene, "image_prompt", "") or ""
            old_image_path = getattr(scene, "image_path", "") or ""
            old_status = getattr(scene, "status", "pending")
            safe_prompt = _rewrite_vr_image_prompt(scene_idx, old_prompt, revision_no)
            scene.image_prompt = safe_prompt
            self._log(
                f"    [VR scene {scene_idx + 1}] tạo lại ảnh tham chiếu an toàn "
                f"(lần {revision_no}), không dùng ảnh gốc có khuôn mặt",
                prod_tag,
            )
            try:
                new_path = self._generate_vr_image(
                    prod,
                    prod_folder,
                    scene_idx,
                    scene,
                    image_model,
                    aspect,
                    api_url,
                    api_key,
                    prod_tag,
                    use_product_reference=False,
                )
            except Exception as exc:
                new_path = None
                self._log(
                    f"    [VR scene {scene_idx + 1}] không tạo lại được ảnh an toàn: {exc}",
                    prod_tag,
                )

            if not new_path:
                scene.image_prompt = old_prompt
                scene.image_path = old_image_path
                scene.status = old_status
                return None

            scene.image_prompt = safe_prompt
            scene.image_path = str(new_path)
            if scene_idx < len(scene_data) and isinstance(scene_data[scene_idx], dict):
                scene_data[scene_idx]["image_prompt"] = safe_prompt
                scene_data[scene_idx]["image_safety_rewritten"] = True
            try:
                script_file = prod_folder / "vr_script.json"
                if script_file.exists():
                    saved_script = json.loads(script_file.read_text(encoding="utf-8"))
                    saved_scenes = saved_script.get("scenes", [])
                    if scene_idx < len(saved_scenes) and isinstance(saved_scenes[scene_idx], dict):
                        saved_scenes[scene_idx]["image_prompt"] = safe_prompt
                        saved_scenes[scene_idx]["image_safety_rewritten"] = True
                        script_file.write_text(
                            json.dumps(saved_script, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
            except Exception as save_exc:
                self._log(
                    f"    [VR scene {scene_idx + 1}] không lưu được prompt ảnh an toàn: {save_exc}",
                    prod_tag,
                )
            self._queue_product_updated(prod.id)
            return Path(new_path)

        def _is_product_mismatch(image_prompt):
            """Detect the old hard-coded safety prompt on another product."""
            prompt_text = str(image_prompt or "").lower()
            product_text = " ".join(
                str(getattr(prod, field, "") or "")
                for field in ("title", "info", "context")
            ).lower()
            unrelated_lash_markers = (
                "lash clusters", "false eyelashes", "pre-glued black lash",
                "silicone makeup practice eye", "light blue and white tray",
            )
            is_lash_product = bool(re.search(
                r"\b(?:lash|eyelash|eyelashes)\b|mi\s+(?:giá|sẵn keo)|lông mi",
                product_text,
            ))
            return bool(
                any(marker in prompt_text for marker in unrelated_lash_markers)
                and not is_lash_product
            )

        def _vr_error_is_retryable(error_text):
            text = str(error_text or "").lower()
            permanent_markers = (
                "content policy", "model_access_denied", "model access denied",
                "quota hàng ngày", "quota_reached", "token google hết hạn",
                "http 401", "unauthorized", "không có quyền dùng model",
            )
            return not any(marker in text for marker in permanent_markers)

        def _sleep_vr_delay(seconds):
            deadline = time.time() + max(0.0, float(seconds))
            while not self._stopped:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return True
                time.sleep(min(1.0, remaining))
            return False

        def _render_vr_clip(scene_idx, image_path, prompt):
            if self._stopped:
                return None

            output_path = vr_dir / f"clip_{scene_idx + 1}.mp4"
            try:
                scene_retry_count = max(0, int(self.settings.get("retry_count", 2)))
            except Exception:
                scene_retry_count = 2
            max_attempts = scene_retry_count + 1
            prompt_retry_block = max(3, max_attempts)
            try:
                max_prompt_rewrites = max(1, int(self.settings.get("vr_prompt_rewrite_max", 5)))
            except Exception:
                max_prompt_rewrites = 5
            # Persisted overrides were already produced by the safety rewrite
            # path on an earlier run. If that text still triggers the same
            # filter, the reference image—not the wording—is the next likely
            # cause and should be regenerated immediately.
            has_persisted_safe_prompt = bool(
                scene_idx < len(scenes)
                and str(getattr(scenes[scene_idx], "video_prompt_override", "") or "").strip()
            )
            prompt_revision_count = 1 if has_persisted_safe_prompt else 0
            image_regeneration_count = 0
            max_image_regenerations = 2
            prominent_filter_failures = 0
            rate_limit_failures = 0
            try:
                max_rate_limit_attempts = max(
                    max_attempts,
                    int(self.settings.get("vr_429_retry_count", 8)),
                )
            except Exception:
                max_rate_limit_attempts = max(max_attempts, 8)
            last_error = ""
            attempt = 0
            # Keep a stable account for ordinary failures. Only an explicit
            # Flow 429 may fail over to another account; every attempt creates
            # a fresh job with that account name so token persistence cannot
            # be written back to the wrong profile.
            bound_account_name = ""
            if use_veo and google_accounts:
                selected_account = self._next_vr_google_account(google_accounts)
                bound_account_name = selected_account.name if selected_account else ""

            while attempt < max_attempts:
                attempt += 1
                if self._stopped:
                    return None

                # A failed Google job can leave a zero-byte/partial file.  It
                # must never be accepted by a later assembly pass.
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    pass

                # A 429 retry will fail over to another healthy account below,
                # so it needs only a small jitter. Account-local quarantine
                # provides the long wait when no alternate account is ready.
                if attempt > 1:
                    is_rate_limited = "429" in last_error or "resource_exhausted" in last_error.lower()
                    if is_rate_limited:
                        delay = random.uniform(3.0, 7.0)
                    else:
                        delay = min(
                            60.0,
                            6.0 * (2 ** (attempt - 2)) + random.uniform(1.0, 5.0),
                        )
                    self._log(
                        f"    [VR video {scene_idx + 1}] chờ {delay:.1f}s trước lần thử "
                        f"{attempt}/{max_attempts}" + (" (429/backoff)" if is_rate_limited else ""),
                        prod_tag,
                    )
                    if not _sleep_vr_delay(delay):
                        return None

                loop = None
                job = None
                success = False
                attempt_account_name = ""
                try:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    job = VideoJob(
                        id=uuid.uuid4().hex[:8],
                        mode="image-to-video",
                        prompt=prompt,
                        model=video_model,
                        duration=clip_duration,
                        resolution=resolution,
                        aspect_ratio=aspect,
                        image_url=str(image_path),
                        local_path=str(output_path),
                        output_name=f"vr_clip_{scene_idx + 1}",
                        # The scene-level loop owns retries.  Keeping the
                        # library retry at one prevents it from silently
                        # switching to the first account in accounts.json.
                        max_retries=1,
                        google_auth_root=str(THREADS_AUTH_ROOT),
                        google_accounts_file=str(ACCOUNTS_FILE),
                    )
                    # Keep the configured workers concurrent, but reserve the
                    # actual Google submit instants five seconds apart.  The
                    # Flow client applies this immediately before POST, so
                    # upload, polling and download do not serialize the account.
                    try:
                        job.submit_gap_seconds = max(
                            0.0, float(self.settings.get("pipeline_delay", 5))
                        )
                    except Exception:
                        job.submit_gap_seconds = 5.0

                    if use_veo:
                        if not google_accounts:
                            raise RuntimeError("Không tìm thấy tài khoản Google Flow")
                        fresh_accounts = list(self.acc_mgr.get_all_google()) or list(google_accounts)
                        previous_rate_limited = any(
                            marker in str(last_error or "").lower()
                            for marker in ("429", "resource_exhausted", "resource has been exhausted")
                        )
                        if previous_rate_limited:
                            replacement = self._next_vr_google_account(
                                fresh_accounts,
                                exclude_names={bound_account_name},
                            )
                            if replacement and replacement.name != bound_account_name:
                                self._log(
                                    f"    [VR video {scene_idx + 1}] tài khoản {bound_account_name} "
                                    f"bị 429 — chuyển sang {replacement.name}",
                                    prod_tag,
                                )
                                bound_account_name = replacement.name
                        preferred_account = next(
                            (a for a in fresh_accounts if a.name == bound_account_name),
                            None,
                        )
                        if preferred_account is None:
                            preferred_account = self._next_vr_google_account(fresh_accounts)
                        # Refresh the selected account by name, never by list
                        # position.  Token refresh can update accounts.json
                        # while a long video job is polling.
                        account = next(
                            (a for a in fresh_accounts if a.name == preferred_account.name),
                            preferred_account,
                        )
                        cooldown_left = self._get_vr_google_account_cooldown(account.name)
                        if cooldown_left > 0:
                            replacement = self._next_vr_google_account(
                                fresh_accounts,
                                exclude_names={account.name},
                            )
                            if (
                                replacement
                                and replacement.name != account.name
                                and self._get_vr_google_account_cooldown(replacement.name) <= 0
                            ):
                                self._log(
                                    f"    [VR video {scene_idx + 1}] bỏ qua {account.name} đang nghỉ 429 — "
                                    f"dùng {replacement.name}",
                                    prod_tag,
                                )
                                account = replacement
                                bound_account_name = account.name
                            else:
                                self._log(
                                    f"    [VR video {scene_idx + 1}] tài khoản {account.name} "
                                    f"đang nghỉ 429, chờ {cooldown_left:.1f}s",
                                    prod_tag,
                                )
                                if not _sleep_vr_delay(cooldown_left):
                                    return None
                        bearer = account.api_key
                        project_id = account.proxy_url or GROK_DEFAULT_PROJECT_ID
                        job.account_name = account.name
                        job.google_profile_name = getattr(account, "profile_name", "") or ""
                        attempt_account_name = account.name
                        account_slot = self._get_vr_google_account_slot(account.name)
                        acquired_slot = account_slot.acquire(blocking=False)
                        if not acquired_slot:
                            self._log(
                                f"    [VR video {scene_idx + 1}] chờ lượt tài khoản {account.name}...",
                                prod_tag,
                            )
                            while not self._stopped:
                                if account_slot.acquire(timeout=1.0):
                                    acquired_slot = True
                                    break
                        if not acquired_slot:
                            return None
                        try:
                            self._log(
                                f"    [VR video {scene_idx + 1}] bắt đầu lần {attempt}/{max_attempts} "
                                f"(Acc: {account.name}): {prompt[:70]}...",
                                prod_tag,
                            )
                            bridge = getattr(self, "captcha_bridge", None) or self.captcha_bridge
                            success = bool(loop.run_until_complete(
                                run_veo_flow(job, bearer, project_id, bridge, log=self._log, progress=None)
                            ))
                        finally:
                            account_slot.release()
                    else:
                        # A separate client per worker avoids sharing mutable
                        # async client state between concurrent Grok requests.
                        client = GrokClient(api_url, api_key)
                        request_id = loop.run_until_complete(client.submit(job))
                        for _ in range(90):
                            if self._stopped:
                                break
                            time.sleep(10)
                            poll_result = loop.run_until_complete(client.poll(request_id, use_proxy=False))
                            state = poll_result.get("status", "")
                            if state == "completed":
                                video_url = poll_result.get("video_url", "") or poll_result.get("url", "")
                                if video_url:
                                    loop.run_until_complete(client.download(video_url, output_path, use_proxy=False))
                                success = True
                                break
                            if state == "failed":
                                last_error = str(poll_result.get("error", "Video generation failed"))
                                break

                    if success and output_path.exists() and output_path.stat().st_size > 0:
                        if attempt_account_name:
                            self._mark_vr_google_account_healthy(attempt_account_name)
                        with results_lock:
                            results[scene_idx] = str(output_path)
                        scene = scenes[scene_idx]
                        scene.video_clip_path = str(output_path)
                        scene.status = "done"
                        scene.error_msg = ""
                        self._queue_product_updated(prod.id)
                        self._log(f"    [VR video {scene_idx + 1}] OK", prod_tag)
                        return str(output_path)

                    last_error = str(getattr(job, "error", "") or last_error or "không có output video")
                    self._log(
                        f"    [VR video {scene_idx + 1}] thất bại lần {attempt}: {last_error}",
                        prod_tag,
                    )
                except Exception as exc:
                    last_error = str(exc)
                    _record_background_exception(f"VR CLIP #{scene_idx + 1} EXCEPTION")
                    self._log(
                        f"    [VR video {scene_idx + 1}] lỗi lần {attempt}: {exc}",
                        prod_tag,
                    )
                finally:
                    if loop is not None and not loop.is_closed():
                        try:
                            # run_veo_flow closes its HTTP clients itself.
                            # shutdown_asyncgens may wait forever after "OK"
                            # and prevent the product pool from advancing.
                            loop.stop()
                        except Exception:
                            pass
                        try:
                            loop.close()
                        except Exception:
                            pass
                    try:
                        asyncio.set_event_loop(None)
                    except Exception:
                        pass

                if _is_prominent_people_filter_error(last_error):
                    prominent_filter_failures += 1
                    self._log(
                        f"    [VR video {scene_idx + 1}] Google chặn prompt do filter người nổi bật "
                        f"({prominent_filter_failures}/3) — đang giữ ảnh hiện tại",
                        prod_tag,
                    )

                    # Honour the user's three attempts even when retry_count
                    # is configured lower than three.
                    if attempt >= max_attempts and prominent_filter_failures < 3:
                        max_attempts += 1

                    # If a prompt has already been rewritten (including a
                    # persisted override from a previous run) and Google still
                    # returns the same filter, retrying that same portrait is
                    # wasteful. Regenerate a product-only reference frame now.
                    if (
                        prompt_revision_count > 0
                        and image_regeneration_count < max_image_regenerations
                    ):
                        image_regeneration_count += 1
                        self._log(
                            f"    [VR video {scene_idx + 1}] prompt an toàn vẫn bị chặn — "
                            f"tạo lại ảnh tham chiếu không có khuôn mặt "
                            f"(lần {image_regeneration_count}/{max_image_regenerations})",
                            prod_tag,
                        )
                        regenerated_image = _regenerate_safe_vr_image(
                            scene_idx, image_regeneration_count
                        )
                        if regenerated_image:
                            image_path = regenerated_image
                            prominent_filter_failures = 0
                            max_attempts += prompt_retry_block
                            self._log(
                                f"    [VR video {scene_idx + 1}] đã có ảnh an toàn mới — "
                                "tiếp tục render video",
                                prod_tag,
                            )
                            continue
                        self._log(
                            f"    [VR video {scene_idx + 1}] tạo lại ảnh không thành công — "
                            "tiếp tục cơ chế prompt hiện tại",
                            prod_tag,
                        )

                    if prominent_filter_failures >= 3:
                        if prompt_revision_count >= max_prompt_rewrites:
                            self._log(
                                f"    [VR video {scene_idx + 1}] đã dùng hết {max_prompt_rewrites} lần "
                                "LLM viết lại prompt nhưng Google vẫn chặn",
                                prod_tag,
                            )
                            break

                        next_revision = prompt_revision_count + 1
                        self._log(
                            f"    [VR video {scene_idx + 1}] lỗi cùng loại 3 lần — "
                            f"yêu cầu LLM viết lại prompt (lần {next_revision}/{max_prompt_rewrites})",
                            prod_tag,
                        )
                        rewritten_prompt = _rewrite_vr_prompt(scene_idx, prompt, next_revision)
                        if not rewritten_prompt:
                            break
                        prompt_revision_count = next_revision
                        prominent_filter_failures = 0
                        prompt = rewritten_prompt
                        prompt_overrides[scene_idx] = rewritten_prompt
                        if scene_idx < len(scenes):
                            scenes[scene_idx].video_prompt_override = rewritten_prompt
                        if scene_idx < len(scene_data) and isinstance(scene_data[scene_idx], dict):
                            scene_data[scene_idx]["video_prompt_override"] = rewritten_prompt
                        try:
                            script_file = prod_folder / "vr_script.json"
                            if script_file.exists():
                                saved_script = json.loads(script_file.read_text(encoding="utf-8"))
                                saved_scenes = saved_script.get("scenes", [])
                                if scene_idx < len(saved_scenes) and isinstance(saved_scenes[scene_idx], dict):
                                    saved_scenes[scene_idx]["video_prompt_override"] = rewritten_prompt
                                    script_file.write_text(
                                        json.dumps(saved_script, indent=2, ensure_ascii=False),
                                        encoding="utf-8",
                                    )
                        except Exception as save_exc:
                            self._log(
                                f"    [VR video {scene_idx + 1}] không lưu được prompt thay thế: {save_exc}",
                                prod_tag,
                            )
                        # Give the new prompt a fresh three-attempt window.
                        max_attempts += prompt_retry_block

                rate_limited_now = any(marker in str(last_error or "").lower() for marker in (
                    "429", "resource_exhausted", "resource has been exhausted",
                ))
                if rate_limited_now:
                    rate_limit_failures += 1
                    if attempt_account_name:
                        account_cooldown = self._mark_vr_google_account_429(
                            attempt_account_name
                        )
                        self._log(
                            f"    [VR video {scene_idx + 1}] tạm nghỉ tài khoản "
                            f"{attempt_account_name} trong {account_cooldown:.0f}s do 429",
                            prod_tag,
                        )
                    # 429 is transient and must not consume the ordinary
                    # two-attempt scene budget. Extend only this scene's
                    # window; account quarantine prevents immediate reuse.
                    if attempt >= max_attempts and rate_limit_failures < max_rate_limit_attempts:
                        max_attempts += 1

                if not _vr_error_is_retryable(last_error):
                    break

            scenes[scene_idx].status = "error"
            scenes[scene_idx].error_msg = str(last_error or "Video Review render failed")
            self._queue_product_updated(prod.id)
            self._log(
                f"    [VR video {scene_idx + 1}] thất bại sau {max_attempts} lần: {last_error}",
                prod_tag,
            )
            return None

        def _submit_video(video_pool, scene_idx, image_path, prompt):
            if scene_idx in video_futures or self._stopped:
                return
            video_futures[scene_idx] = video_pool.submit(
                _render_vr_clip, scene_idx, image_path, prompt
            )

        # First classify scenes. Existing valid clips are reused unless the
        # persisted safety-repair prompt belongs to another product.
        image_jobs = []
        direct_video_jobs = []
        for scene_idx, scene in enumerate(scenes):
            image_prompt, full_prompt = _scene_payload(scene_idx)
            target_image = vr_dir / f"scene_{scene_idx + 1}.png"
            product_mismatch = _is_product_mismatch(image_prompt)
            existing_clip = Path(scene.video_clip_path) if scene.video_clip_path else None

            if product_mismatch:
                self._log(
                    f"    [VR scene {scene_idx + 1}] phát hiện prompt/ảnh cũ sai loại sản phẩm — "
                    "xoá riêng output sai và tạo lại theo sản phẩm hiện tại",
                    prod_tag,
                )
                if existing_clip and existing_clip.exists():
                    try:
                        if existing_clip.resolve().parent == vr_dir.resolve():
                            existing_clip.unlink(missing_ok=True)
                    except Exception:
                        pass
                stale_image = Path(scene.image_path) if scene.image_path else target_image
                try:
                    if stale_image.exists() and stale_image.resolve().parent == vr_dir.resolve():
                        stale_image.unlink(missing_ok=True)
                except Exception:
                    pass
                scene.video_clip_path = ""
                scene.image_path = ""
                scene.status = "pending"
                scene.image_prompt = _safe_vr_image_fallback()
                image_prompt = scene.image_prompt
                if scene_idx < len(scene_data) and isinstance(scene_data[scene_idx], dict):
                    scene_data[scene_idx]["image_prompt"] = image_prompt
                    scene_data[scene_idx]["image_safety_rewritten"] = True
                image_prompt, full_prompt = _scene_payload(scene_idx)
                self._queue_product_updated(prod.id)

            if _valid_local_file(existing_clip) and not force_regen and not product_mismatch:
                with results_lock:
                    results[scene_idx] = str(existing_clip)
                continue

            scene_image = Path(scene.image_path) if scene.image_path else None
            if _valid_local_file(scene_image) and not force_regen and not product_mismatch:
                target_image = scene_image
            elif _valid_local_file(target_image) and not force_regen and not product_mismatch:
                scene.image_path = str(target_image)
            elif image_prompt:
                image_jobs.append((scene_idx, scene, product_mismatch))
                continue

            if _valid_local_file(target_image) and full_prompt:
                direct_video_jobs.append((scene_idx, target_image, full_prompt))
            else:
                scene.status = "error"
                self._queue_product_updated(prod.id)
                self._log(f"    [VR scene {scene_idx + 1}] thiếu prompt/ảnh", prod_tag)

        total_video_jobs = len(direct_video_jobs) + len(image_jobs)
        self._log(
            f"    VR streaming: {len(image_jobs)} ảnh producer -> "
            f"{total_video_jobs} video consumer "
            f"({image_cap} ảnh + {video_cap} video đồng thời)",
            prod_tag,
        )

        # Both executors remain bounded. FIRST_COMPLETED is the key detail:
        # the first finished image is submitted to video immediately.
        with ThreadPoolExecutor(max_workers=video_cap) as video_pool:
            for scene_idx, image_path, prompt in direct_video_jobs:
                _submit_video(video_pool, scene_idx, image_path, prompt)

            with ThreadPoolExecutor(max_workers=image_cap) as image_pool:
                for scene_idx, scene, safe_image in image_jobs:
                    image_futures[image_pool.submit(
                        self._generate_vr_image,
                        prod,
                        prod_folder,
                        scene_idx,
                        scene,
                        image_model,
                        aspect,
                        api_url,
                        api_key,
                        prod_tag,
                        use_product_reference=not safe_image,
                    )] = scene_idx

                pending = set(image_futures) | set(video_futures.values())
                while pending and not self._stopped:
                    done, pending = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        if future in image_futures:
                            scene_idx = image_futures[future]
                            try:
                                image_path = future.result()
                            except Exception as exc:
                                image_path = None
                                self._log(f"    [VR image {scene_idx + 1}] worker lỗi: {exc}", prod_tag)
                            if image_path:
                                _, prompt = _scene_payload(scene_idx)
                                _submit_video(video_pool, scene_idx, Path(image_path), prompt)
                        else:
                            try:
                                future.result()
                            except Exception as exc:
                                self._log(f"    [VR video] worker lỗi: {exc}", prod_tag)

                        # A newly submitted video must join the wait set.
                        pending.update(
                            f for idx, f in video_futures.items()
                            if f not in done and f not in pending
                        )

            # The image executor is closed here. Any video futures submitted
            # by its final callbacks are still drained before this method ends.
            while video_futures:
                remaining_videos = {f for f in video_futures.values() if not f.done()}
                if not remaining_videos:
                    break
                done, _ = wait(remaining_videos, return_when=FIRST_COMPLETED)
                for future in done:
                    try:
                        future.result()
                    except Exception as exc:
                        self._log(f"    [VR video] worker lỗi: {exc}", prod_tag)

        return [results[idx] for idx in range(len(scenes)) if idx in results and _valid_local_file(results[idx])]

    def _start_early_vr_stream(self, prod, prod_folder, image_threads,
                                video_threads, force_regen=False):
        """Start Video Review image->video work as soon as product prep ends.

        Phase 2 used to prepare only the normal scene images and voices.  The
        AI Review stream was then delayed until the global Phase 3 loop, which
        made the "image producer -> video consumer" optimization ineffective.
        This helper starts that same bounded stream per product; Phase 3 only
        assembles the completed clips afterwards.
        """
        if (
            self._stopped
            or not has_grok_deps
            or not self.settings.get("vr_enabled", False)
            or self.settings.get("vr_mode") != "ai_generate"
            or not self.settings.get("vr_stream_pipeline", True)
        ):
            return []

        with self._vr_pre_rendered_lock:
            if prod.id in self._vr_pre_rendered_products and not force_regen:
                return []

        scenes = list(getattr(prod, "video_review_scenes", None) or [])
        if not scenes:
            return []

        # A complete existing result still follows the normal skip behavior.
        # Missing clips or an explicit re-render enter the early stream.
        final_path = getattr(prod, "final_combined_path", "") or ""
        clips_complete = all(
            bool(sc.video_clip_path and Path(sc.video_clip_path).exists())
            for sc in scenes
        )
        if final_path and Path(final_path).exists() and clips_complete and not force_regen:
            return []

        scene_data = [
            {
                "image_prompt": getattr(sc, "image_prompt", "") or "",
                "video_prompt": getattr(sc, "video_prompt", "") or "",
                "video_prompt_override": getattr(sc, "video_prompt_override", "") or "",
                "voiceover_text": getattr(sc, "voiceover_text", "") or "",
            }
            for sc in scenes
        ]
        video_model = self.settings.get(
            "vr_video_model", "Veo 3.1 - Lite [Lower Priority]"
        )
        resolution = self.settings.get("vr_resolution", "720p")
        clip_duration = self.settings.get("vr_clip_duration", 8)
        aspect = self.settings.get("vr_aspect_ratio", "9:16")
        image_model = self.settings.get("img_model", "gpt-image-2")
        api_url = self.settings.get("api_url", DEFAULT_PROXY_URL)
        api_key = self.settings.get("api_key", "grok-video-key-001")
        prod_tag = (prod.title or "SP")[:20]

        self._log(
            f"  [VR ưu tiên] Video Review AI sẵn sàng: ưu tiên ảnh -> video "
            f"({len(scenes)} cảnh)...",
            prod_tag,
        )
        clips = self._run_vr_scene_stream(
            prod=prod,
            prod_folder=prod_folder,
            scenes=scenes,
            scene_data=scene_data,
            video_model=video_model,
            resolution=resolution,
            clip_duration=clip_duration,
            aspect=aspect,
            image_model=image_model,
            api_url=api_url,
            api_key=api_key,
            force_regen=bool(force_regen),
            image_threads=image_threads,
            video_threads=video_threads,
        )
        # Mark the product even when only some clips succeeded: Phase 3 will
        # reuse successful clips and retry only the missing ones.
        with self._vr_pre_rendered_lock:
            self._vr_pre_rendered_products.add(prod.id)
        self._queue_product_updated(prod.id)
        return clips

    def _repair_missing_vr_until_complete(self, products, prod_folders,
                                           image_threads, video_threads):
        """Requeue only missing AI Review scenes until every clip exists.

        A normal scene retry window is intentionally finite, but a project
        run must not declare completion while transient Flow 429 responses
        have left clips missing. Successful images/clips are always reused.
        The loop remains interruptible through the existing Stop button.
        """
        if (
            self._stopped
            or not has_grok_deps
            or not self.settings.get("vr_enabled", False)
            or self.settings.get("vr_mode") != "ai_generate"
            or not self.settings.get("vr_stream_pipeline", True)
        ):
            return

        def _clip_ok(scene):
            if not scene.video_clip_path:
                return False
            try:
                path = Path(scene.video_clip_path)
                return path.is_file() and path.stat().st_size > 0
            except (OSError, TypeError, ValueError):
                return False

        repair_round = 0
        while not self._stopped:
            incomplete_products = [
                prod for prod in products
                if prod.video_review_scenes
                and any(not _clip_ok(scene) for scene in prod.video_review_scenes)
            ]
            missing_before = sum(
                not _clip_ok(scene)
                for prod in incomplete_products
                for scene in prod.video_review_scenes
            )
            if missing_before <= 0:
                if repair_round:
                    self._log("  ✅ Đã render đủ toàn bộ phân cảnh Video Review AI.")
                return

            repair_round += 1
            self._log(
                f"  🔁 Hoàn thiện Video Review — vòng {repair_round}: "
                f"còn {missing_before} cảnh của {len(incomplete_products)} sản phẩm."
            )

            repair_workers = max(
                1, min(len(incomplete_products), max(1, int(video_threads)))
            )
            repair_image_threads = max(1, math.ceil(int(image_threads) / repair_workers))
            repair_video_threads = max(1, math.ceil(int(video_threads) / repair_workers))

            def _repair_product(prod):
                if self._stopped:
                    return
                scenes = list(prod.video_review_scenes or [])
                missing_indexes = [
                    str(idx + 1) for idx, scene in enumerate(scenes)
                    if not _clip_ok(scene)
                ]
                prod.status = "⏳ Hoàn thiện Video Review..."
                # A combined file created before these clips existed is a
                # partial/stale result. Keep the old file recoverable on disk,
                # but clear its registration so Phase 3 must assemble a new
                # final after all missing clips have been repaired.
                if prod.final_combined_path:
                    prod.final_combined_path = ""
                self._queue_product_updated(prod.id)
                self._log(
                    f"    ↻ {prod.title[:38]}: render lại cảnh {', '.join(missing_indexes)}"
                )
                scene_data = [
                    {
                        "image_prompt": scene.image_prompt or "",
                        "video_prompt": scene.video_prompt or "",
                        "video_prompt_override": getattr(scene, "video_prompt_override", "") or "",
                        "voiceover_text": scene.voiceover_text or "",
                    }
                    for scene in scenes
                ]
                self._run_vr_scene_stream(
                    prod=prod,
                    prod_folder=prod_folders[prod.id],
                    scenes=scenes,
                    scene_data=scene_data,
                    video_model=self.settings.get(
                        "vr_video_model", "Veo 3.1 - Lite [Lower Priority]"
                    ),
                    resolution=self.settings.get("vr_resolution", "720p"),
                    clip_duration=self.settings.get("vr_clip_duration", 8),
                    aspect=self.settings.get("vr_aspect_ratio", "9:16"),
                    image_model=self.settings.get("img_model", "gpt-image-2"),
                    api_url=self.settings.get("api_url", DEFAULT_PROXY_URL),
                    api_key=self.settings.get("api_key", "grok-video-key-001"),
                    force_regen=False,
                    image_threads=repair_image_threads,
                    video_threads=repair_video_threads,
                )

            # Repair products concurrently as well. The old sequential loop
            # left five configured video slots idle whenever one product had
            # only a single missing scene.
            with ThreadPoolExecutor(max_workers=repair_workers) as repair_pool:
                repair_futures = {
                    repair_pool.submit(_repair_product, prod): prod
                    for prod in incomplete_products
                }
                for future in as_completed(repair_futures):
                    try:
                        future.result()
                    except Exception as exc:
                        prod = repair_futures[future]
                        self._log(
                            f"    ⚠️ Repair Video Review lỗi [{prod.title[:30]}]: {exc}"
                        )

            missing_after = sum(
                not _clip_ok(scene)
                for prod in products
                for scene in (prod.video_review_scenes or [])
            )
            if missing_after <= 0:
                self._log("  ✅ Đã render đủ toàn bộ phân cảnh Video Review AI.")
                return

            made_progress = missing_after < missing_before
            wait_seconds = 20 if made_progress else 90
            self._log(
                f"  ⏳ Còn {missing_after} cảnh chưa xong; chờ {wait_seconds}s "
                "rồi tự động quét lại (có thể bấm Dừng)."
            )
            for _ in range(wait_seconds):
                if self._stopped:
                    return
                time.sleep(1)

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 1: LLM Script Generation (single product)
    # ═══════════════════════════════════════════════════════════════════
    def _run_llm_single(self, prod, prod_folder, acc, delay_sec):
        """Run LLM for a single product. Returns True on success."""
        if self._stopped:
            return False

        ptag = (prod.title or 'SP')[:20]
        api_url = acc.proxy_url if acc else self.settings.get("api_url", DEFAULT_PROXY_URL)
        api_key = acc.api_key if acc else self.settings.get("api_key", "grok-video-key-001")
        acc_name = acc.name if acc else "Default"
        llm = self.settings.get("llm_model", "gpt-5.5")
        img_m = self.settings.get("img_model", "gpt-image-2")
        sc_cnt = self.settings.get("scene_count", 4)
        ctx = self.settings.get("context", "Bối cảnh theo Role Skill")
        rsp = self.settings.get("role_skill_path")
        rs_text = Path(rsp).read_text(encoding="utf-8") if rsp and Path(rsp).exists() else ""

        self._log(f"  💬 LLM [{llm}] → Acc: {acc_name}", ptag)

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
        chosen_style = None
        if ctx == "Random Phong cách":
            available_styles = [name for name, desc in DEFAULT_ARCHETYPES]
            chosen_style = random.choice(available_styles) if available_styles else "Tự chọn phong cách tối ưu nhất"
        elif ctx == "Chọn theo Phong cách dưới từng sản phẩm":
            chosen_style = prod.context or "Tự chọn phong cách tối ưu nhất"
        else:
            chosen_style = ctx or prod.context or "Tự chọn phong cách tối ưu nhất"

        # Final safety fallback checks
        if chosen_style == "Random Phong cách":
            available_styles = [name for name, desc in DEFAULT_ARCHETYPES]
            chosen_style = random.choice(available_styles) if available_styles else "Tự chọn phong cách tối ưu nhất"
        elif chosen_style == "Chọn theo Phong cách dưới từng sản phẩm":
            chosen_style = prod.context or "Tự chọn phong cách tối ưu nhất"
            if chosen_style == "Chọn theo Phong cách dưới từng sản phẩm":
                chosen_style = "Tự chọn phong cách tối ưu nhất"

        style_instruction = ""
        if chosen_style and chosen_style != "Tự chọn phong cách tối ưu nhất":
            custom_archetypes = self.settings.get("custom_archetypes", [])
            desc_lookup = next((d for n, d in DEFAULT_ARCHETYPES if n == chosen_style), "")
            if not desc_lookup:
                desc_lookup = next((item[1] for item in custom_archetypes if isinstance(item, list) and len(item) == 2 and item[0] == chosen_style), "")
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

        # Video Review instruction (if enabled)
        if self.settings.get("vr_enabled", False):
            vr_mode = self.settings.get("vr_mode", "ai_generate")
            if vr_mode == "ai_generate":
                vr_scene_count = self.settings.get("vr_scene_count", 4)
                vr_aspect = self.settings.get("vr_aspect_ratio", "9:16")
                vr_clip_dur = self.settings.get("vr_clip_duration", 8)
                vr_rs_name = self.settings.get("vr_role_skill", "Video AI Mỹ Phẩm.MD")
                vr_rs_path = DATA_ROLE_SKILL_DIR / vr_rs_name
                vr_rs_text = vr_rs_path.read_text(encoding="utf-8") if vr_rs_path.exists() else ""

                vr_instruction = (
                    f"\n\n=========================================\n"
                    f"🎬 YÊU CẦU BẮT BUỘC — SINH ĐỒNG THỜI 2 PHẦN TRONG CÙNG 1 LẦN TRẢ LỜI:\n"
                    f"Video cuối cùng gồm 2 phần nối tiếp nhau, PHẢI là MỘT THỂ THỐNG NHẤT, liền mạch:\n"
                    f"  • PHẦN 1 = kịch bản đọc bình luận/review (trường 'scenes' như trên).\n"
                    f"  • PHẦN 2 = Video Review Sản Phẩm quay cận cảnh sản phẩm thật (trường 'video_review').\n\n"
                    f"NGUYÊN TẮC KẾT NỐI (RẤT QUAN TRỌNG):\n"
                    f"1. Phần 2 là đoạn CAO TRÀO/CHỐT: sau khi phần 1 khơi gợi tò mò từ các bình luận, "
                    f"phần 2 cho người xem THẤY TẬN MẮT sản phẩm hoạt động/hiệu quả thật.\n"
                    f"2. Câu voiceover đầu tiên của phần 2 PHẢI nối tiếp tự nhiên câu cuối của phần 1 "
                    f"(vd: 'Và đây chính là sản phẩm mà mọi người đang nhắc tới...'). KHÔNG mở lại như một video mới.\n"
                    f"3. Giọng đọc, ngôi kể, phong cách của phần 2 GIỐNG HỆT phần 1 (cùng một người dẫn).\n"
                    f"4. Nối toàn bộ 'voiceover_text' của phần 2 theo id tăng dần = trường 'voiceover' tổng của phần 2, "
                    f"và phải là lời GIỚI THIỆU SẢN PHẨM mạch lạc, có mở-thân-chốt, kèm lời kêu gọi hành động cuối cùng.\n\n"
                    f"HƯỚNG DẪN NỘI DUNG/PHONG CÁCH PHẦN 2 (Role Skill Video):\n{vr_rs_text}\n\n"
                    f"Số phân cảnh phần 2: ĐÚNG {vr_scene_count} phân cảnh. Mỗi voiceover_text ~{vr_clip_dur} giây đọc "
                    f"(khoảng {max(8, vr_clip_dur*3)}-{vr_clip_dur*5} từ), khớp thời lượng clip {vr_clip_dur}s.\n"
                    f"Cấu trúc JSON: bổ sung trường 'video_review' vào scenario (NGANG HÀNG với 'scenes'):\n"
                    f'  "video_review": {{\n'
                    f'    "voiceover": "Toàn bộ lời giới thiệu sản phẩm liền mạch của phần 2 (nối các voiceover_text).",\n'
                    f'    "scenes": [\n'
                    f'      {{\n'
                    f'        "id": 1,\n'
                    f'        "image_prompt": "Prompt tiếng Anh chi tiết mô tả ảnh tĩnh sản phẩm/bối cảnh dùng thật.",\n'
                    f'        "video_prompt": "Prompt tiếng Anh mô tả chuyển động camera/sản phẩm (i2v).",\n'
                    f'        "voiceover_text": "Lời giới thiệu sản phẩm cho phân cảnh này (tiếng Việt)."\n'
                    f'      }}\n'
                    f'    ]\n'
                    f'  }}\n'
                    f"Tỷ lệ khung hình phần review: {vr_aspect}. Thời lượng mỗi clip: {vr_clip_dur}s.\n"
                )
            else:  # external_file mode
                vr_instruction = (
                    f"\n\n=========================================\n"
                    f"🎬 YÊU CẦU BẮT BUỘC — SINH ĐỒNG THỜI cả lời thoại giới thiệu sản phẩm (Video Review) cho phần 2:\n"
                    f"Video cuối gồm PHẦN 1 (đọc bình luận) + PHẦN 2 (video sản phẩm thật) — MỘT THỂ THỐNG NHẤT.\n"
                    f"Lời thoại phần 2 PHẢI nối tiếp tự nhiên câu cuối phần 1 (không mở lại như video mới), "
                    f"cùng giọng/ngôi kể, là lời GIỚI THIỆU SẢN PHẨM có mở-thân-chốt + kêu gọi hành động.\n"
                    f"Cấu trúc JSON: bổ sung trường 'video_review' vào scenario:\n"
                    f'  "video_review": {{\n'
                    f'    "voiceover": "Lời thoại review giới thiệu sản phẩm mượt mà nối tiếp phần 1 (khoảng 40-70 từ)."\n'
                    f'  }}\n'
                )
            sp += vr_instruction

        self._log(f"    • Phong cách kịch bản: {chosen_style}", ptag)

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
                self._log(f"  ⚠️ Lỗi encode ảnh {img_p} cho LLM: {e_enc}")
            return None

        if prod.images:
            blk = _get_image_block(prod.images[0])
            if blk: user_content.append(blk)

        for ref_p in prod.images[1:]:
            blk = _get_image_block(ref_p)
            if blk: user_content.append(blk)

        user_msg = user_content if len(user_content) > 1 else f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info}"

        retry_cnt = self.settings.get("retry_count", 2)
        for attempt in range(1, retry_cnt + 2):
            if self._stopped:
                return False
            try:
                self._log(f"  📤 Gửi LLM request (lần {attempt}/{retry_cnt+1})...", ptag)
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

                if r.status_code == 200:
                    content_raw = r.json()["choices"][0]["message"]["content"]
                    self._log(f"  📥 LLM OK: {content_raw[:100]}...", ptag)

                    # Save raw LLM output
                    script_file = prod_folder / "LLM.json"
                    try:
                        script_file.write_text(content_raw, encoding="utf-8")
                    except Exception:
                        pass

                    d = parse_llm_json_robust(content_raw)
                    sr = d.get("scenarios", [{}])[0]
                    prod.voiceover_text = sr.get("voiceover", "")
                    prod.scenes = [ThreadsScene.from_dict(s) for s in sr.get("scenes", [])]
                    self._log(f"  ✔ {len(prod.scenes)} phân cảnh.", ptag)

                    # Parse Video Review if generated
                    vr_data = sr.get("video_review", {})
                    if vr_data:
                        vr_scenes_list = vr_data.get("scenes", [])
                        if vr_scenes_list:
                            prod.video_review_scenes = []
                            for si_init, sd_init in enumerate(vr_scenes_list):
                                vrs = VideoReviewScene(
                                    id=si_init + 1,
                                    scene_title=f"Scene {si_init + 1}",
                                    image_prompt=sd_init.get("image_prompt", ""),
                                    video_prompt=sd_init.get("video_prompt", ""),
                                    voiceover_text=sd_init.get("voiceover_text", ""),
                                    status="pending"
                                )
                                prod.video_review_scenes.append(vrs)
                            self._log(f"  ✔ Đã gộp kịch bản Video Review: {len(prod.video_review_scenes)} phân cảnh.", ptag)
                        elif vr_data.get("voiceover"):
                            prod.video_review_scenes = [
                                VideoReviewScene(
                                    id=1,
                                    scene_title="Review Voiceover",
                                    voiceover_text=vr_data.get("voiceover"),
                                    status="pending"
                                )
                            ]

                    self._queue_product_updated(prod.id)
                    time.sleep(delay_sec)
                    return True
                else:
                    self._log(f"  ❌ LLM Error (lần {attempt}): {r.status_code}", ptag)
            except Exception as ex:
                self._log(f"  ❌ LLM Exception (lần {attempt}): {ex}", ptag)

            if attempt < retry_cnt + 1:
                time.sleep(3)

        prod.status = "❌ Lỗi LLM"
        self._queue_product_updated(prod.id)
        return False

    # ═══════════════════════════════════════════════════════════════════
    #  PHASE 2: Image + Voice Generation (single product, all scenes)
    # ═══════════════════════════════════════════════════════════════════
    def _run_images_and_voice(self, prod, prod_folder,
                              image_threads_override=None,
                              video_threads_override=None):
        """Generate images (parallel per-scene) + voice for a single product."""
        if self._stopped:
            return False

        prod_images_dir = prod_folder / "images"
        prod_voice_dir = prod_folder / "voice"
        prod_images_dir.mkdir(exist_ok=True)
        prod_voice_dir.mkdir(exist_ok=True)

        api_url = self.settings.get("api_url", DEFAULT_PROXY_URL)
        api_key = self.settings.get("api_key", "grok-video-key-001")
        img_m = self.settings.get("img_model", "gpt-image-2")
        img_threads = image_threads_override or self.settings.get("img_threads", 5)
        pipeline_delay = self.settings.get("pipeline_delay", 5)
        burst_size = self.settings.get("burst_size", 4)
        burst_delay = self.settings.get("burst_delay", 10)
        concurrency = self.settings.get("concurrency", 8)
        is_veo = has_grok_deps and is_veo_model(img_m)

        # ── Identify missing scenes ──
        missing_scenes = []
        for idx, sc in enumerate(prod.scenes):
            target_img = prod_images_dir / f"scene_{sc.id}.png"
            if not sc.image_path or not Path(sc.image_path).exists() or not target_img.exists():
                missing_scenes.append((idx, sc, target_img))
            else:
                sc.image_path = str(target_img)
                sc.status = "done"

        # ── Generate missing images ──
        if missing_scenes:
            ptag = (prod.title or 'SP')[:20]
            self._log(f"  [2/4] Sinh {len(missing_scenes)} ảnh [{img_m}] (Luồng: {img_threads})...", ptag)

            if is_veo:
                # Google Labs flow with round-robin accounts
                g_accs = self.acc_mgr.get_all_google()
                ref_media_ids_per_acc = {}

                if g_accs and prod.images:
                    # Upload reference images to each Google account
                    if getattr(prod, "use_ref_for_generation", False):
                        images_to_upload = prod.images
                    else:
                        images_to_upload = [prod.images[0]] if prod.images else []

                    self._log(f"  📸 Tải lên {len(images_to_upload)} ảnh tham chiếu cho {len(g_accs)} tài khoản...")
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
                                            client=None, file_path=str(img_p),
                                            bearer_token=bearer, project_id=proj_id,
                                            log=lambda m: self._log(f"    [{acc.name}] {m}"),
                                            captcha_bridge=self.captcha_bridge
                                        )
                                        if mid: ref_media_ids_per_acc[bearer].append(mid)
                        loop_up.run_until_complete(_do_upload_all())
                    except Exception as e_up:
                        self._log(f"  ⚠️ Lỗi tải ảnh tham chiếu: {e_up}")
                    finally:
                        try: loop_up.close()
                        except Exception: pass

                # Process in bursts (keeping existing Veo3 burst logic)
                for i in range(0, len(missing_scenes), burst_size):
                    if self._stopped:
                        break
                    chunk = missing_scenes[i:i + burst_size]
                    self._log(f"    🚀 Đợt {i//burst_size + 1}/{math.ceil(len(missing_scenes)/burst_size)}: {len(chunk)} ảnh song song...")

                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        async def process_single_veo(idx_val, sc_val, target_img_val, acc_idx):
                            retry_cnt = self.settings.get("retry_count", 2)
                            first_acc = g_accs[acc_idx % len(g_accs)] if g_accs else None
                            first_acc_name = first_acc.name if first_acc else "Default"

                            if acc_idx > 0:
                                stagger_delay = acc_idx * 5.0
                                self._log(f"      ⏳ Trì hoãn {stagger_delay}s (Acc: {first_acc_name})...")
                                await asyncio.sleep(stagger_delay)

                            for attempt in range(1, retry_cnt + 2):
                                acc = g_accs[(acc_idx + attempt - 1) % len(g_accs)] if g_accs else None
                                bearer = acc.api_key if acc else self.settings.get("veo_bearer", "")
                                proj_id = (acc.proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b") if acc else self.settings.get("veo_project_id", "")
                                acc_name = acc.name if acc else "Default"
                                acc_ref_ids = ref_media_ids_per_acc.get(bearer, [])
                                try:
                                    self._log(f"      🎨 Scene #{idx_val+1} (Acc: {acc_name}, Lần {attempt}): {sc_val.prompt[:60]}...")
                                    sc_aspect_raw = getattr(sc_val, "aspect_ratio", "4:3") or "4:3"
                                    if "1:1" in sc_aspect_raw or "square" in sc_aspect_raw.lower():
                                        sc_aspect = "1:1"
                                    elif "9:16" in sc_aspect_raw or "3:4" in sc_aspect_raw or "4:5" in sc_aspect_raw or "portrait" in sc_aspect_raw.lower():
                                        sc_aspect = "9:16"
                                    else:
                                        sc_aspect = "16:9"
                                    job = VideoJob(
                                        id=f"img_{uuid.uuid4().hex[:6]}",
                                        model=img_m, mode="image-generation",
                                        prompt=sc_val.prompt, aspect_ratio=sc_aspect,
                                        output_name=f"scene_{sc_val.id}",
                                        local_path=str(prod_images_dir),
                                        use_proxy=True,
                                        ref_images=acc_ref_ids if acc_ref_ids else None,
                                        account_name=acc_name,
                                        google_profile_name=(
                                            getattr(acc, "profile_name", "") or ""
                                            if acc else ""
                                        ),
                                        google_auth_root=str(THREADS_AUTH_ROOT),
                                        google_accounts_file=str(ACCOUNTS_FILE),
                                    )
                                    # The outer scene loop rotates accounts on
                                    # retry.  Do not let run_veo_flow reload the
                                    # first account from accounts.json here.
                                    job.max_retries = 1

                                    success = await run_veo_flow(
                                        job, bearer, proj_id, self.captcha_bridge,
                                        log=lambda m: self._log(f"        [Google #{idx_val+1}] {m}"),
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
                                        self._log(f"      ✔ Scene #{idx_val+1} OK")
                                        return
                                    else:
                                        sc_val.status = "error"
                                        self._log(f"      ❌ Scene #{idx_val+1} Thất bại lần {attempt}")
                                except Exception as ex_single:
                                    sc_val.status = "error"
                                    self._log(f"      ❌ Scene #{idx_val+1} Exception lần {attempt}: {ex_single}")

                                if attempt < retry_cnt + 1:
                                    error_text = str(getattr(locals().get("job"), "error", "") or "").lower()
                                    base_delay = 18.0 if ("429" in error_text or "resource_exhausted" in error_text) else 3.0
                                    await asyncio.sleep(base_delay + random.uniform(0.5, 2.5))

                        tasks = [process_single_veo(idx_val, sc_val, target_img_val, chunk_idx) for chunk_idx, (idx_val, sc_val, target_img_val) in enumerate(chunk)]
                        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                    except Exception as e:
                        self._log(f"    ❌ Lỗi luồng song song Google Labs: {e}")
                    finally:
                        try: loop.close()
                        except Exception: pass

                    self._queue_product_updated(prod.id)
                    if i + burst_size < len(missing_scenes) and not self._stopped:
                        self._log(f"    ⏳ Nghỉ {burst_delay}s giữa các đợt...")
                        time.sleep(burst_delay)
            else:
                # Codex/Grok API flow — round-robin accounts with ThreadPoolExecutor
                # Prepare product reference image for /v1/images/edits (multipart)
                _ref_img_path = None
                if prod.images:
                    for _ri in prod.images:
                        if _ri and Path(_ri).exists():
                            _ref_img_path = _ri
                            break
                if _ref_img_path:
                    self._log(f"  📸 Dùng ảnh tham chiếu sản phẩm: {Path(_ref_img_path).name}", ptag)

                def _gen_image_codex(idx_val, sc_val, target_img_val):
                    if self._stopped:
                        return
                    acc = self.acc_mgr.next_codex_rr()
                    _api_url = acc.proxy_url if acc else api_url
                    _api_key = acc.api_key if acc else api_key
                    _acc_name = acc.name if acc else "Default"
                    retry_cnt = self.settings.get("retry_count", 2)
                    for attempt in range(1, retry_cnt + 2):
                        self._log(f"    🎨 Scene #{idx_val+1} (Acc: {_acc_name}, Lần {attempt}): {sc_val.prompt[:50]}...", ptag)
                        try:
                            # Use /v1/images/edits with reference image if available
                            if _ref_img_path and Path(_ref_img_path).exists():
                                with open(_ref_img_path, "rb") as f_ref:
                                    ref_bytes = f_ref.read()
                                ext = Path(_ref_img_path).suffix.lower().lstrip(".") or "jpeg"
                                mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                                files = {"image": (Path(_ref_img_path).name, ref_bytes, mime)}
                                data_form = {
                                    "model": img_m,
                                    "prompt": sc_val.prompt,
                                    "n": "1",
                                    "size": "1024x1280"
                                }
                                ir = requests.post(
                                    f"{_api_url}/v1/images/edits",
                                    headers={"Authorization": f"Bearer {_api_key}"},
                                    files=files,
                                    data=data_form,
                                    timeout=600
                                )
                            else:
                                ir = requests.post(
                                    f"{_api_url}/v1/images/generations",
                                    headers={"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"},
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
                                    self._log(f"      ✔ Scene #{idx_val+1} OK", ptag)
                                    return
                                elif "url" in it:
                                    img_resp = requests.get(it["url"], timeout=60)
                                    if img_resp.status_code == 200:
                                        target_img_val.write_bytes(img_resp.content)
                                        sc_val.image_path = str(target_img_val)
                                        sc_val.status = "done"
                                        self._log(f"      ✔ Scene #{idx_val+1} OK (URL)", ptag)
                                        return
                            sc_val.status = "error"
                            self._log(f"      ❌ Scene #{idx_val+1} API Error: {ir.status_code}", ptag)
                        except Exception as ex_single:
                            sc_val.status = "error"
                            self._log(f"      ❌ Scene #{idx_val+1} Exception: {ex_single}", ptag)
                        if attempt < retry_cnt + 1:
                            time.sleep(pipeline_delay)

                with ThreadPoolExecutor(max_workers=img_threads) as pool:
                    futures = [pool.submit(_gen_image_codex, idx_val, sc_val, target_img_val) for idx_val, sc_val, target_img_val in missing_scenes]
                    for f in as_completed(futures):
                        try:
                            f.result()
                        except Exception as e:
                            self._log(f"    ❌ Image thread error: {e}")

            self._queue_product_updated(prod.id)

        # ── Voice Generation (runs after/alongside images) ──
        target_voice = prod_voice_dir / f"voice_{prod.id}.mp3"
        if prod.voice_path and Path(prod.voice_path).exists() and not target_voice.exists():
            try:
                import shutil
                shutil.copy2(prod.voice_path, target_voice)
                prod.voice_path = str(target_voice)
                prod.voice_duration = get_media_duration(target_voice)
                self._log(f"    ✔ Voice có sẵn: {target_voice.name} ({prod.voice_duration}s)")
            except Exception as e_cp:
                self._log(f"    ⚠️ Lỗi sao chép voice: {e_cp}")

        if not prod.voice_path or not Path(prod.voice_path).exists() or not target_voice.exists():
            self._log("  [3/4] Voice...")
            vm_code = self.settings.get("voice_mode", "narrative_review")
            main_voice = self.settings.get("vbee_voice", "vi_south_female_miien_48k")
            sub_voices = self.settings.get("vbee_sub_voices", [])
            engine = self.settings.get("voice_engine", "gtts")
            engine_label = dict(TTS_ENGINES).get(engine, engine)
            use_provider = tts_provider_ready(self.settings)

            if use_provider and vm_code in ("verbatim_read", "hybrid_narrate_read") and prod.scenes:
                self._log(f"    🎭 Mode: {vm_code} — Voice từng phân cảnh ({engine_label})...")
                scene_audio_files = []
                
                # Trộn ngẫu nhiên danh sách giọng phụ để phân bổ không lặp
                available_subs = list(sub_voices) if sub_voices else []
                random.shuffle(available_subs)

                for sc in prod.scenes:
                    seg = sc.text_segment.strip()
                    if not seg:
                        continue
                    if seg and seg[-1] not in '.!?…,;:':
                        seg += '.'
                    if vm_code == "hybrid_narrate_read":
                        if sc.voice_role == "narrator":
                            chosen_voice = main_voice
                        else:
                            if not available_subs:
                                available_subs = list(sub_voices) if sub_voices else []
                                random.shuffle(available_subs)
                            chosen_voice = available_subs.pop(0) if available_subs else main_voice
                    else:
                        if not available_subs:
                            available_subs = list(sub_voices) if sub_voices else []
                            random.shuffle(available_subs)
                        chosen_voice = available_subs.pop(0) if available_subs else main_voice

                    sc_audio = tts_synthesize(seg, self.settings, prod_voice_dir, voice_override=chosen_voice, log_fn=self._log)
                    if sc_audio and Path(sc_audio).exists():
                        sc_dur = get_media_duration(sc_audio)
                        sc.voice_duration = sc_dur if sc_dur else 0.0
                        scene_audio_files.append(sc_audio)
                    else:
                        try:
                            fallback = prod_voice_dir / f"gtts_sc{sc.id}_{uuid.uuid4().hex[:6]}.mp3"
                            gTTS(text=seg, lang='vi').save(str(fallback))
                            scene_audio_files.append(str(fallback))
                        except Exception as e_gtts:
                            self._log(f"      ❌ gTTS thất bại: {e_gtts}")

                if scene_audio_files:
                    try:
                        _concat_audio_with_silence(scene_audio_files, str(target_voice), silence_ms=400)
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)
                        self._log(f"    ✔ Ghép {len(scene_audio_files)} đoạn → {target_voice.name} ({prod.voice_duration}s)")
                    except Exception as e_concat:
                        self._log(f"    ❌ Lỗi ghép audio: {e_concat}")
                    for sf in scene_audio_files:
                        try: Path(sf).unlink(missing_ok=True)
                        except: pass
            else:
                vt = prod.voiceover_text or prod.title
                if use_provider:
                    ov = tts_synthesize(vt, self.settings, prod_voice_dir, voice_override=main_voice, log_fn=self._log)
                    if ov and Path(ov).exists():
                        try: Path(ov).replace(target_voice)
                        except Exception: pass
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)
                        self._log(f"    ✔ Voice {engine_label}: {target_voice.name} ({prod.voice_duration}s)")

        if not prod.voice_path or not Path(prod.voice_path).exists():
            vt = prod.voiceover_text or prod.title
            try:
                gTTS(text=vt, lang='vi').save(str(target_voice))
                prod.voice_path = str(target_voice)
                prod.voice_duration = get_media_duration(target_voice)
                self._log(f"    ✔ gTTS Voice: {target_voice.name} ({prod.voice_duration}s)")
            except Exception as e:
                self._log(f"    ❌ Lỗi gTTS: {e}")

        # ── Video Review Images (pre-generate in Phase 2 for parallelism) ──
        if (
            self.settings.get("vr_enabled", False)
            and self.settings.get("vr_mode") == "ai_generate"
            and self.settings.get("vr_stream_pipeline", True) is False
        ):
            vr_img_model = self.settings.get("img_model", "gpt-image-2")
            vr_aspect = self.settings.get("vr_aspect_ratio", "9:16")
            vr_clip_dir = prod_folder / "vr_clips"
            vr_clip_dir.mkdir(exist_ok=True)

            if prod.video_review_scenes:
                vr_missing = []
                for si_vr, vr_sc in enumerate(prod.video_review_scenes):
                    vr_img_path = vr_clip_dir / f"scene_{si_vr+1}.png"
                    if vr_sc.image_prompt and (not vr_sc.image_path or not Path(vr_sc.image_path).exists()):
                        vr_missing.append((si_vr, vr_sc, vr_img_path))
                    elif vr_img_path.exists():
                        vr_sc.image_path = str(vr_img_path)

                if vr_missing:
                    ptag = (prod.title or 'SP')[:20]
                    self._log(f"  [2b] Sinh {len(vr_missing)} ảnh Video Review [{vr_img_model}]...", ptag)
                    api_url_vr = self.settings.get("api_url", DEFAULT_PROXY_URL)
                    api_key_vr = self.settings.get("api_key", "grok-video-key-001")

                    def _gen_vr_img(si_vr_val, vr_sc_val, vr_img_val):
                        if self._stopped:
                            return
                        acc = self.acc_mgr.next_codex_rr()
                        _url = acc.proxy_url if acc else api_url_vr
                        _key = acc.api_key if acc else api_key_vr
                        _name = acc.name if acc else "Default"
                        try:
                            self._log(f"    🎨 VR Scene #{si_vr_val+1} (Acc: {_name}): {vr_sc_val.image_prompt[:50]}...", ptag)
                            _ref_path = None
                            if prod.images:
                                for _ri in prod.images:
                                    if _ri and Path(_ri).exists():
                                        _ref_path = _ri
                                        break
                            if _ref_path:
                                with open(_ref_path, "rb") as f_ref:
                                    ref_bytes = f_ref.read()
                                ext = Path(_ref_path).suffix.lower().lstrip(".") or "jpeg"
                                mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                                files = {"image": (Path(_ref_path).name, ref_bytes, mime)}
                                vr_size = "1024x1792" if "9:16" in vr_aspect else ("1792x1024" if "16:9" in vr_aspect else "1024x1024")
                                data_form = {"model": vr_img_model, "prompt": vr_sc_val.image_prompt, "n": "1", "size": vr_size}
                                ir = requests.post(f"{_url}/v1/images/edits",
                                    headers={"Authorization": f"Bearer {_key}"},
                                    files=files, data=data_form, timeout=600)
                            else:
                                vr_size = "1024x1792" if "9:16" in vr_aspect else ("1792x1024" if "16:9" in vr_aspect else "1024x1024")
                                ir = requests.post(f"{_url}/v1/images/generations",
                                    headers={"Authorization": f"Bearer {_key}", "Content-Type": "application/json"},
                                    json={"model": vr_img_model, "prompt": vr_sc_val.image_prompt, "n": 1, "size": vr_size, "response_format": "b64_json"},
                                    timeout=600)
                            if ir.status_code == 200:
                                it = ir.json().get("data", [{}])[0]
                                if "b64_json" in it:
                                    import base64
                                    vr_img_val.write_bytes(base64.b64decode(it["b64_json"]))
                                    vr_sc_val.image_path = str(vr_img_val)
                                    self._log(f"      ✔ VR Scene #{si_vr_val+1} OK", ptag)
                                    return
                                elif "url" in it:
                                    img_resp = requests.get(it["url"], timeout=60)
                                    if img_resp.status_code == 200:
                                        vr_img_val.write_bytes(img_resp.content)
                                        vr_sc_val.image_path = str(vr_img_val)
                                        self._log(f"      ✔ VR Scene #{si_vr_val+1} OK (URL)", ptag)
                                        return
                            self._log(f"      ❌ VR Scene #{si_vr_val+1}: {ir.status_code}", ptag)
                        except Exception as e_vr_img:
                            self._log(f"      ❌ VR Scene #{si_vr_val+1}: {e_vr_img}", ptag)

                    with ThreadPoolExecutor(max_workers=min(img_threads, len(vr_missing))) as vr_pool:
                        vr_futs = [vr_pool.submit(_gen_vr_img, *args) for args in vr_missing]
                        for vr_f in as_completed(vr_futs):
                            try: vr_f.result()
                            except Exception as e_vr: self._log(f"    ❌ VR Image error: {e_vr}")
                    self._queue_product_updated(prod.id)

        # ── Video Review Voice ──
        if self.settings.get("vr_enabled", False):
            target_vr_voice = prod_voice_dir / f"vr_voice_{prod.id}.mp3"
            if prod.video_review_scenes and not target_vr_voice.exists():
                self._log("  [3b] Video Review Voice...")
                main_voice = self.settings.get("vbee_voice", "vi_south_female_miien_48k")
                engine_label = dict(TTS_ENGINES).get(self.settings.get("voice_engine", "gtts"), "TTS")
                use_provider = tts_provider_ready(self.settings)
                vr_scene_audio_files = []
                for sc in prod.video_review_scenes:
                    seg = sc.voiceover_text.strip()
                    if not seg:
                        continue
                    if seg and seg[-1] not in '.!?…,;:':
                        seg += '.'
                    if use_provider:
                        sc_audio = tts_synthesize(seg, self.settings, prod_voice_dir, voice_override=main_voice, log_fn=self._log)
                        if sc_audio and Path(sc_audio).exists():
                            sc.voice_duration = get_media_duration(sc_audio)
                            vr_scene_audio_files.append(sc_audio)
                        else:
                            try:
                                fallback = prod_voice_dir / f"gtts_vr_sc{sc.id}_{uuid.uuid4().hex[:6]}.mp3"
                                gTTS(text=seg, lang='vi').save(str(fallback))
                                sc.voice_duration = get_media_duration(fallback)
                                vr_scene_audio_files.append(str(fallback))
                            except Exception:
                                pass
                    else:
                        try:
                            fallback = prod_voice_dir / f"gtts_vr_sc{sc.id}_{uuid.uuid4().hex[:6]}.mp3"
                            gTTS(text=seg, lang='vi').save(str(fallback))
                            sc.voice_duration = get_media_duration(fallback)
                            vr_scene_audio_files.append(str(fallback))
                        except Exception:
                            pass
                if vr_scene_audio_files:
                    try:
                        _concat_audio_with_silence(vr_scene_audio_files, str(target_vr_voice), silence_ms=400)
                        prod.video_review_voice_path = str(target_vr_voice)
                        prod.video_review_voice_duration = get_media_duration(target_vr_voice)
                        self._log(f"    ✔ VR Voice: {target_vr_voice.name} ({prod.video_review_voice_duration}s)")
                    except Exception:
                        pass
                    for sf in vr_scene_audio_files:
                        try: Path(sf).unlink(missing_ok=True)
                        except: pass
            elif target_vr_voice.exists():
                prod.video_review_voice_path = str(target_vr_voice)
                prod.video_review_voice_duration = get_media_duration(target_vr_voice)

        self._queue_product_updated(prod.id)
        return True

    # ═══════════════════════════════════════════════════════════════════
    #  MAIN RUN METHOD — 3-Phase Parallel Pipeline
    # ═══════════════════════════════════════════════════════════════════
    def run(self):
        try:
            total = len(self.products)
            if not total:
                self._queue_finished(False, "Chưa có sản phẩm để chạy.")
                return

            llm_threads = self.settings.get("llm_threads", 3)
            img_threads = self.settings.get("img_threads", 5)
            vid_threads = self.settings.get("vid_threads", 3)
            pipeline_delay = self.settings.get("pipeline_delay", 5)
            only_vid = self.settings.get("only_video_assembly", False)
            only_reimg = self.settings.get("only_rerender_images", False)
            project_output_dir = self.settings.get("project_output_dir", "")
            out_base = (
                Path(project_output_dir)
                if project_output_dir
                else Path(self.settings.get("output_dir", str(OUTPUT_DIR)))
            )
            out_base.mkdir(parents=True, exist_ok=True)
            bg_folder = self.settings.get("bg_folder", "")
            no_dup = self.settings.get("no_duplicate_bg", True)

            bg_files = []
            if bg_folder and Path(bg_folder).exists():
                for ext in ["*.mp4", "*.mov", "*.mkv", "*.avi"]:
                    bg_files.extend(list(Path(bg_folder).glob(ext)))

            # Print settings summary
            vm_display = self.settings.get("voice_mode_display", "Review dẫn chuyện (narrative_review)")
            self._log(f"⚙️ CẤU HÌNH PIPELINE (3-Phase Parallel):")
            self._log(f"  • Role Skill: {self.settings.get('selected_role')}")
            self._log(f"  • LLM Model: {self.settings.get('llm_model', 'gpt-5.5')}")
            self._log(f"  • Image Model: {self.settings.get('img_model', 'gpt-image-2')}")
            self._log(f"  • Voice Mode: {vm_display}")
            self._log(f"  • 💬 Luồng LLM: {llm_threads} | 🖼️ Luồng Ảnh: {img_threads} | 🎬 Luồng Video: {vid_threads}")
            self._log(f"  • ⏳ Delay: {pipeline_delay}s | Video nền: {len(bg_files)} video")
            self._log(f"  • 📦 Tổng SP: {total}")
            if only_reimg:
                self._log(f"  • 🔄 CHẾ ĐỘ: Chỉ Render lại Ảnh (giữ nguyên kịch bản + voice)")
            elif only_vid:
                self._log(f"  • ⚡ CHẾ ĐỘ: Chỉ ghép dựng Video")

            # Prepare product folders
            prod_folders = {}
            for prod in self.products:
                prod_folders[prod.id] = self._prepare_product_dirs(prod, out_base)

            # ════════════════════════════════════════════════════════════
            #  PHASE 1: LLM — Parallel script generation
            # ════════════════════════════════════════════════════════════
            products_need_llm = [p for p in self.products if not only_vid and not only_reimg and not p.scenes]
            products_skip_llm = [p for p in self.products if p not in products_need_llm]

            if products_need_llm:
                self._log(f"\n{'='*50}")
                self._log(f"📝 PHASE 1: LLM ({len(products_need_llm)} SP, {llm_threads} luồng, round-robin accounts)")
                self._log(f"{'='*50}")

                codex_accs = self.acc_mgr.get_all_codex()
                all_accs = self.acc_mgr.active
                use_accs = codex_accs if codex_accs else all_accs
                self._log(f"  🔑 Tài khoản LLM khả dụng: {len(use_accs)} ({', '.join(a.name for a in use_accs[:5])})")

                def _llm_task(pi, prod):
                    if self._stopped:
                        return prod, False
                    # Round-robin: pick account based on product index
                    acc = use_accs[pi % len(use_accs)] if use_accs else None
                    self._log(f"🚀 [{pi+1}/{total}] {prod.title[:40]}... → Acc: {acc.name if acc else 'Default'}")
                    prod.status = "⏳ LLM..."
                    self._queue_product_updated(prod.id)
                    self.progress_signal.emit(pi+1, total, "LLM")
                    success = self._run_llm_single(prod, prod_folders[prod.id], acc, pipeline_delay)
                    return prod, success

                llm_done = []
                with ThreadPoolExecutor(max_workers=llm_threads) as pool:
                    futures = {}
                    for pi, prod in enumerate(products_need_llm):
                        f = pool.submit(_llm_task, pi, prod)
                        futures[f] = prod
                    for f in as_completed(futures):
                        try:
                            prod, success = f.result()
                            if success:
                                llm_done.append(prod)
                            else:
                                self._log(f"  ⚠️ LLM thất bại cho: {prod.title[:30]}")
                        except Exception as e:
                            self._log(f"  ❌ LLM thread crash: {e}")

                self._log(f"  ✅ Phase 1 xong: {len(llm_done)}/{len(products_need_llm)} SP thành công.")
            else:
                llm_done = []
                if not only_vid:
                    for p in self.products:
                        if p.scenes:
                            self._log(f"  ✔ [{p.title[:30]}] đã có {len(p.scenes)} phân cảnh sẵn.")

            # Products ready for Phase 2 = LLM done + products that already had scenes
            phase2_products = llm_done + products_skip_llm

            # ════════════════════════════════════════════════════════════
            #  PHASE 2: Image + Voice — Parallel generation
            # ════════════════════════════════════════════════════════════
            if not only_vid and not only_reimg and phase2_products:
                self._log(f"\n{'='*50}")
                self._log(f"🎨 PHASE 2: Ảnh + Voice ({len(phase2_products)} SP, {img_threads} luồng ảnh)")
                self._log(f"{'='*50}")

                def _img_voice_task(pi, prod, image_threads_for_product):
                    if self._stopped:
                        return prod, False
                    self._log(f"🖼️ [{pi+1}/{len(phase2_products)}] {prod.title[:40]}...")
                    prod.status = "⏳ Ảnh + Voice..."
                    self._queue_product_updated(prod.id)
                    success = self._run_images_and_voice(
                        prod,
                        prod_folders[prod.id],
                        image_threads_override=image_threads_for_product,
                    )
                    return prod, success

                def _vr_task(prod, image_threads_for_product, video_threads_for_product):
                    if self._stopped:
                        return prod, []
                    try:
                        clips = self._start_early_vr_stream(
                            prod,
                            prod_folders[prod.id],
                            image_threads=image_threads_for_product,
                            video_threads=video_threads_for_product,
                            force_regen=bool(self.settings.get("re_video_review", False)),
                        )
                        return prod, clips
                    except Exception as exc:
                        # The normal image/voice executor must never wait on or
                        # fail because Google Flow has a problem.  The repair
                        # pass below will requeue only the missing VR clips.
                        self._log(
                            f"  ⚠️ Video Review sớm lỗi cho {prod.title[:30]}, "
                            f"sẽ thử lại phần còn thiếu: {exc}"
                        )
                        return prod, []

                # The old scheduler started one product worker per product and
                # each worker created ``img_threads`` image workers.  With six
                # products and the UI value 8 that could create 48 API workers
                # before voice/VR work was counted.  Keep products concurrent,
                # but divide the configured image budget across at most two
                # products so the total image fan-out stays bounded.
                img_voice_workers = max(1, min(len(phase2_products), 2, img_threads))
                phase2_img_threads = max(1, int(img_threads) // img_voice_workers)

                vr_products = [
                    prod for prod in phase2_products
                    if (
                        self.settings.get("vr_enabled", False)
                        and self.settings.get("vr_mode") == "ai_generate"
                        and self.settings.get("vr_stream_pipeline", True)
                        and prod.video_review_scenes
                    )
                ]
                vr_product_workers = max(
                    1,
                    min(
                        len(vr_products) or 1,
                        max(1, int(vid_threads)),
                    ),
                )
                # Let enough products feed the queue to actually occupy all
                # configured video slots. With six products and six video
                # threads this becomes 6 x 1; with two products it becomes
                # 2 x 3. The aggregate consumer budget remains six.
                phase2_vr_img_threads = max(1, int(img_threads) // vr_product_workers)
                phase2_vid_threads = max(1, math.ceil(int(vid_threads) / vr_product_workers))

                vr_pool = None
                vr_futures = {}
                try:
                    if vr_products:
                        self._log(
                            f"  🚀 Video Review chạy luồng độc lập: "
                            f"{vr_product_workers} SP đồng thời, tổng tối đa {int(vid_threads)} video"
                        )
                        # Submit this executor first so Review images receive
                        # CPU/API slots before normal scene-image work starts.
                        vr_pool = ThreadPoolExecutor(max_workers=vr_product_workers)
                        for prod in vr_products:
                            future = vr_pool.submit(
                                _vr_task,
                                prod,
                                phase2_vr_img_threads,
                                phase2_vid_threads,
                            )
                            vr_futures[future] = prod

                    # Normal images and Voice now have their own executor.
                    # Google video retries, cooldowns and polling cannot occupy
                    # these product workers anymore.
                    with ThreadPoolExecutor(max_workers=img_voice_workers) as pool:
                        futures = {}
                        for pi, prod in enumerate(phase2_products):
                            f = pool.submit(_img_voice_task, pi, prod, phase2_img_threads)
                            futures[f] = prod
                        for f in as_completed(futures):
                            try:
                                prod, success = f.result()
                                if not success:
                                    self._log(f"  ⚠️ Ảnh/Voice chưa hoàn thành cho: {prod.title[:30]}")
                            except Exception as e:
                                self._log(f"  ❌ Img/Voice thread crash: {e}")

                    for future in as_completed(vr_futures):
                        try:
                            future.result()
                        except Exception as exc:
                            prod = vr_futures[future]
                            self._log(f"  ❌ Video Review worker crash [{prod.title[:30]}]: {exc}")
                finally:
                    if vr_pool is not None:
                        vr_pool.shutdown(wait=True)

                self._log(f"  ✅ Phase 2 xong.")

            if self._stopped:
                self._queue_finished(False, "⏹️ Đã dừng — dự án chưa được đánh dấu hoàn tất.")
                return

            # A normal Start run is not complete while any AI Review scene is
            # still missing its video.  Reuse every finished image/clip and
            # keep requeuing only the gaps; transient Flow 429 bursts must not
            # silently turn into a partially rendered project.
            if not only_vid and not only_reimg and not self._stopped:
                self._repair_missing_vr_until_complete(
                    self.products,
                    prod_folders,
                    image_threads=max(1, int(img_threads)),
                    video_threads=max(1, int(vid_threads)),
                )

            # ═══════════ PHASE 2b: ONLY RE-RENDER IMAGES (skip voice) ═══════════
            if only_reimg:
                reimg_products = [p for p in self.products if p.scenes]
                if reimg_products:
                    self._log(f"\n{'='*50}")
                    self._log(f"🔄 PHASE 2b: Render lại Ảnh ({len(reimg_products)} SP, {img_threads} luồng)")
                    self._log(f"{'='*50}")

                    def _reimg_task(pi, prod):
                        if self._stopped:
                            return prod, False
                        ptag = (prod.title or 'SP')[:20]
                        self._log(f"🖼️ [{pi+1}/{len(reimg_products)}] {prod.title[:40]}...", ptag)
                        prod.status = "⏳ Render ảnh..."
                        self._queue_product_updated(prod.id)

                        prod_folder = prod_folders[prod.id]
                        prod_images_dir = prod_folder / "images"
                        prod_images_dir.mkdir(exist_ok=True)

                        api_url = self.settings.get("api_url", DEFAULT_PROXY_URL)
                        api_key = self.settings.get("api_key", "grok-video-key-001")
                        img_m = self.settings.get("img_model", "gpt-image-2")
                        is_veo = has_grok_deps and is_veo_model(img_m)

                        # Find reference image
                        _ref_img_path = None
                        if prod.images:
                            for _ri in prod.images:
                                if _ri and Path(_ri).exists():
                                    _ref_img_path = _ri
                                    break
                        if _ref_img_path:
                            self._log(f"  📸 Ảnh tham chiếu: {Path(_ref_img_path).name}", ptag)
                        else:
                            self._log(f"  ⚠️ Không có ảnh tham chiếu SP!", ptag)

                        # Identify missing/cleared scenes
                        missing_scenes = []
                        for idx, sc in enumerate(prod.scenes):
                            target_img = prod_images_dir / f"scene_{sc.id}.png"
                            if not sc.image_path or not Path(sc.image_path).exists() or not target_img.exists():
                                missing_scenes.append((idx, sc, target_img))

                        if not missing_scenes:
                            self._log(f"  ✔ Tất cả ảnh đã có sẵn.", ptag)
                            return prod, True

                        self._log(f"  🎨 Sinh {len(missing_scenes)} ảnh [{img_m}]...", ptag)

                        if not is_veo:
                            # Codex flow with reference images
                            def _gen_img(idx_val, sc_val, target_img_val):
                                if self._stopped:
                                    return
                                acc = self.acc_mgr.next_codex_rr()
                                _api_url = acc.proxy_url if acc else api_url
                                _api_key = acc.api_key if acc else api_key
                                _acc_name = acc.name if acc else "Default"
                                retry_cnt = self.settings.get("retry_count", 2)
                                for attempt in range(1, retry_cnt + 2):
                                    self._log(f"    🎨 Scene #{idx_val+1} (Acc: {_acc_name}): {sc_val.prompt[:50]}...", ptag)
                                    try:
                                        if _ref_img_path and Path(_ref_img_path).exists():
                                            with open(_ref_img_path, "rb") as f_ref:
                                                ref_bytes = f_ref.read()
                                            ext = Path(_ref_img_path).suffix.lower().lstrip(".") or "jpeg"
                                            mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                                            files = {"image": (Path(_ref_img_path).name, ref_bytes, mime)}
                                            data_form = {"model": img_m, "prompt": sc_val.prompt, "n": "1", "size": "1024x1280"}
                                            ir = requests.post(
                                                f"{_api_url}/v1/images/edits",
                                                headers={"Authorization": f"Bearer {_api_key}"},
                                                files=files, data=data_form, timeout=600
                                            )
                                        else:
                                            ir = requests.post(
                                                f"{_api_url}/v1/images/generations",
                                                headers={"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"},
                                                json={"model": img_m, "prompt": sc_val.prompt, "n": 1, "size": "1024x1280", "response_format": "b64_json"},
                                                timeout=600
                                            )
                                        if ir.status_code == 200:
                                            it = ir.json().get("data", [{}])[0]
                                            if "b64_json" in it:
                                                import base64
                                                target_img_val.write_bytes(base64.b64decode(it["b64_json"]))
                                                sc_val.image_path = str(target_img_val)
                                                sc_val.status = "done"
                                                self._log(f"      ✔ Scene #{idx_val+1} OK", ptag)
                                                return
                                            elif "url" in it:
                                                img_resp = requests.get(it["url"], timeout=60)
                                                if img_resp.status_code == 200:
                                                    target_img_val.write_bytes(img_resp.content)
                                                    sc_val.image_path = str(target_img_val)
                                                    sc_val.status = "done"
                                                    self._log(f"      ✔ Scene #{idx_val+1} OK (URL)", ptag)
                                                    return
                                        sc_val.status = "error"
                                        self._log(f"      ❌ Scene #{idx_val+1}: {ir.status_code}", ptag)
                                    except Exception as ex:
                                        sc_val.status = "error"
                                        self._log(f"      ❌ Scene #{idx_val+1}: {ex}", ptag)
                                    if attempt < retry_cnt + 1:
                                        time.sleep(pipeline_delay)

                            with ThreadPoolExecutor(max_workers=img_threads) as pool:
                                futures = [pool.submit(_gen_img, idx_val, sc_val, target_img_val) for idx_val, sc_val, target_img_val in missing_scenes]
                                for f in as_completed(futures):
                                    try: f.result()
                                    except Exception as e:
                                        self._log(f"    ❌ Image thread error: {e}", ptag)
                        else:
                            # Veo/Google flow — delegate to _run_images_and_voice
                            self._run_images_and_voice(prod, prod_folder)

                        self._queue_product_updated(prod.id)
                        return prod, True

                    img_workers = min(llm_threads, len(reimg_products))
                    with ThreadPoolExecutor(max_workers=img_workers) as pool:
                        futures = {}
                        for pi, prod in enumerate(reimg_products):
                            f = pool.submit(_reimg_task, pi, prod)
                            futures[f] = prod
                        for f in as_completed(futures):
                            try: f.result()
                            except Exception as e:
                                self._log(f"  ❌ Reimg thread crash: {e}")

                    self._log(f"  ✅ Render lại ảnh xong.")

                if self._stopped:
                    self._queue_finished(False, "⏹️ Đã dừng — ảnh chưa render hoàn tất.")
                else:
                    self._queue_finished(True, "🎉 Hoàn tất Render lại Ảnh!")
                return

            # ════════════════════════════════════════════════════════════
            #  PHASE 3: Video Assembly + Video Review (per product)
            # ════════════════════════════════════════════════════════════
            all_products = self.products  # Process all products (including only_vid mode)
            self._log(f"\n{'='*50}")
            self._log(f"🎬 PHASE 3: Video Assembly ({len(all_products)} SP)")
            self._log(f"{'='*50}")

            for pi, prod in enumerate(all_products):
                if self._stopped:
                    break

                prod_folder = prod_folders[prod.id]
                prod_images_dir = prod_folder / "images"
                prod_voice_dir = prod_folder / "voice"
                target_voice = prod_voice_dir / f"voice_{prod.id}.mp3"

                # For only_vid mode, recover voice path
                if only_vid:
                    if target_voice.exists():
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)

                    target_vr_voice = prod_voice_dir / f"vr_voice_{prod.id}.mp3"
                    if target_vr_voice.exists():
                        prod.video_review_voice_path = str(target_vr_voice)
                        prod.video_review_voice_duration = get_media_duration(target_vr_voice)

                self._log(f"🎬 [{pi+1}/{len(all_products)}] {prod.title[:40]}...")
                self.progress_signal.emit(pi+1, total, "Video")

                # ── 4. Edit Video Assembly ──
                try:
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
                            scene_gap_sec = 0.4
                            num_gaps = max(0, len(vs) - 1)

                            detected_segments = None
                            if prod.voice_path and Path(prod.voice_path).exists():
                                detected_segments = _detect_voice_segments(prod.voice_path, len(vs), self._log)

                            if detected_segments:
                                sds = [seg[1] for seg in detected_segments]
                                seg_starts = [seg[0] for seg in detected_segments]
                            elif all(s.voice_duration > 0 for s in vs):
                                sds = [s.voice_duration for s in vs]
                                seg_starts = None
                            else:
                                total_gap = scene_gap_sec * num_gaps
                                usable_voice_time = max(1.0, vd - total_gap)
                                tc = sum(max(1, len(s.text_segment)) for s in vs)
                                sds = [max(1.5, round(usable_voice_time * (max(1, len(s.text_segment)) / tc), 2)) for s in vs]
                                seg_starts = None

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

                            bg_dur = get_media_duration(cbg) or 10.0
                            start_time = random.uniform(0.0, bg_dur)
                            end_time = start_time + vd
                            fc = [f"[0:v]trim={start_time:.2f}:{end_time:.2f},setpts=PTS-STARTPTS,{','.join(bf)}[bg];"]
                            img_lead = 0.1
                            ls = "bg"

                            for si, dur in enumerate(sds):
                                if seg_starts:
                                    voice_st = seg_starts[si]
                                else:
                                    voice_st = sum(sds[:si]) + si * scene_gap_sec
                                voice_et = voice_st + dur

                                if si == 0:
                                    img_st = 0.0
                                else:
                                    img_st = max(0, voice_st - img_lead)

                                if si < len(sds) - 1:
                                    if seg_starts:
                                        next_voice_st = seg_starts[si + 1]
                                    else:
                                        next_voice_st = voice_et + scene_gap_sec
                                    img_et = min(vd, next_voice_st - img_lead)
                                else:
                                    img_et = vd

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

                            rs = subprocess.run(cmd, capture_output=True, timeout=600, encoding='utf-8', errors='replace')
                            if rs.returncode == 0 and target_video.exists():
                                apply_natural_metadata(str(target_video), self.settings, self._log)
                                prod.final_video_path = str(target_video)
                                prod.status = "✅ Hoàn thành"
                                self._log(f"      ✔ FFmpeg OK: {target_video.name}")
                            else:
                                prod.status = "❌ Lỗi FFmpeg"
                                err_out = rs.stderr[:300] if rs.stderr else "Không rõ lỗi"
                                self._log(f"      ❌ FFmpeg Error: {err_out}")
                        else:
                            prod.status = "❌ Lỗi: Không có ảnh cảnh"
                    else:
                        prod.final_video_path = ""
                        prod.status = "✔ Ảnh & Voice xong"
                        self._log("    ⚠️ Chưa cấu hình Video nền.")
                except Exception as e_vid:
                    prod.status = "❌ Lỗi dựng phim"
                    self._log(f"    ❌ Lỗi dựng video: {e_vid}")

                self._queue_product_updated(prod.id)

                # ─── [5/5] VIDEO REVIEW SẢN PHẨM ──────────────────────────
                vr_enabled = self.settings.get("vr_enabled", False)
                vr_mode = self.settings.get("vr_mode", "")
                # A combined file alone is not enough to declare an AI Video Review
                # complete.  A previous run could have assembled a partial result
                # after one or more scene renders failed.  In that case the missing
                # scenes must be sent through the render path again.
                vr_scene_manifest = (
                    vr_mode == "ai_generate" and bool(prod.video_review_scenes)
                )
                vr_clips_complete = True
                if vr_scene_manifest:
                    vr_clips_complete = all(
                        bool(sc.video_clip_path and Path(sc.video_clip_path).exists())
                        for sc in prod.video_review_scenes
                    )
                vr_final_exists = bool(
                    prod.final_combined_path and Path(prod.final_combined_path).exists()
                )
                vr_final_complete = vr_final_exists and (
                    not vr_scene_manifest or vr_clips_complete
                )

                if not only_vid and vr_enabled and vr_mode and vr_final_complete:
                    if not self.settings.get("re_video_review", False):
                        self._log("    ✔ Video Review đã có sẵn, bỏ qua.")
                        vr_enabled = False
                elif (
                    vr_enabled
                    and vr_mode == "ai_generate"
                    and vr_final_exists
                    and vr_scene_manifest
                    and not vr_clips_complete
                ):
                    completed = sum(
                        bool(sc.video_clip_path and Path(sc.video_clip_path).exists())
                        for sc in prod.video_review_scenes
                    )
                    self._log(
                        f"    ⚠️ Video Review chưa đủ cảnh ({completed}/{len(prod.video_review_scenes)}), "
                        "sẽ render lại các cảnh còn thiếu."
                    )
                    # Do not leave a partial combined file registered as the
                    # product's final output while the missing scenes are being
                    # repaired.  The old file is intentionally left on disk as
                    # a recoverable artifact; only its validity pointer is reset.
                    prod.final_combined_path = ""
                    self._queue_product_updated(prod.id)

                if vr_enabled and vr_mode and prod.final_video_path and Path(prod.final_video_path).exists():
                    try:
                        self._log(f"  [5/5] Video Review (mode: {vr_mode})...")
                        prod_dir = Path(prod.final_video_path).parent

                        if vr_mode == "external_file":
                            ext_path = prod.external_video_path or self.settings.get("vr_external_path", "")
                            if ext_path and Path(ext_path).exists():
                                vr_processed = prod_dir / f"vr_processed_{prod.id}.mp4"
                                result = process_external_video(str(ext_path), str(vr_processed), self.settings, cbg, self._log)
                                if result and Path(result).exists():
                                    vr_final = prod_dir / f"vr_final_{prod.id}.mp4"
                                    result = add_voiceover_to_video(
                                        result, prod.video_review_voice_path,
                                        self.settings.get("vr_keep_audio", True),
                                        str(vr_final), self._log
                                    )
                                    combined_path = prod_dir / f"combined_{prod.id}.mp4"
                                    final = combine_final_video(
                                        prod.final_video_path, str(result),
                                        str(combined_path), "direct_cut", self._log
                                    )
                                    if final:
                                        apply_natural_metadata(final, self.settings, self._log)
                                        prod.final_combined_path = final
                                        prod.external_video_path = ext_path
                                        prod.video_review_mode = "external_file"
                                        prod.status = "✅ Hoàn thành (+ Video Review)"
                                        self._log(f"    ✅ Video Review OK: {Path(final).name}")

                        elif vr_mode == "ai_generate":
                            self._log(f"    🤖 AI Video Review Pipeline...")
                            vr_rs_name = self.settings.get("vr_role_skill", "Video AI Mỹ Phẩm.MD")
                            vr_rs_dir = Path(__file__).resolve().parent / "data" / "Role Skill"
                            vr_rs_path = vr_rs_dir / vr_rs_name
                            vr_rs_text = vr_rs_path.read_text(encoding="utf-8") if vr_rs_path.exists() else ""
                            vr_scene_count = self.settings.get("vr_scene_count", 4)
                            vr_video_model = self.settings.get("vr_video_model", "Veo 3.1 - Lite [Lower Priority]")
                            vr_resolution = self.settings.get("vr_resolution", "720p")
                            vr_clip_dur = self.settings.get("vr_clip_duration", 8)
                            vr_aspect = self.settings.get("vr_aspect_ratio", "9:16")
                            vr_img_model = self.settings.get("img_model", "gpt-image-2")
                            api_url = self.settings.get("api_url", DEFAULT_PROXY_URL)
                            api_key = self.settings.get("api_key", "grok-video-key-001")
                            llm_model = self.settings.get("llm_model", "gpt-5.5")
                            force_regen = self.settings.get("re_video_review", False)
                            with self._vr_pre_rendered_lock:
                                pre_rendered_this_run = prod.id in self._vr_pre_rendered_products
                            # Early Phase 2 rendering already honored the
                            # explicit re-render flag.  Do not render those
                            # same clips a second time in Phase 3; reuse the
                            # successful ones and repair only missing clips.
                            if pre_rendered_this_run:
                                force_regen = False

                            def _clip_ok(_sc):
                                return bool(_sc.video_clip_path and Path(_sc.video_clip_path).exists())

                            vr_clip_dir = prod_dir / "vr_clips"
                            vr_clip_dir.mkdir(exist_ok=True)

                            # Use pre-generated scenes or create new ones
                            vr_scenes_data = []
                            if prod.video_review_scenes:
                                vr_scenes_data = [
                                    {
                                        "image_prompt": s.image_prompt,
                                        "video_prompt": s.video_prompt,
                                        "video_prompt_override": getattr(s, "video_prompt_override", ""),
                                        "voiceover_text": s.voiceover_text,
                                    }
                                    for s in prod.video_review_scenes
                                ]
                            elif only_vid:
                                self._log("    ⚠️ Chưa có kịch bản Video Review.")
                            elif not has_grok_deps:
                                self._log("    ❌ Thiếu grok_video_tool.py")
                            else:
                                # Fallback LLM for VR script
                                vr_system_prompt = (
                                    f"{vr_rs_text}\n\n"
                                    f"Bạn là chuyên gia tạo kịch bản video review sản phẩm.\n"
                                    f"Tạo CHÍNH XÁC {vr_scene_count} phân cảnh video review.\n"
                                    f"Mỗi phân cảnh gồm:\n- image_prompt\n- video_prompt\n- voiceover_text\n"
                                    f'Trả về JSON: {{"scenes": [{{"image_prompt": "...", "video_prompt": "...", "voiceover_text": "..."}}]}}\n'
                                    f"Aspect ratio: {vr_aspect}. Clip: {vr_clip_dur}s."
                                )
                                try:
                                    acc = self.acc_mgr.next_codex_rr()
                                    _url = acc.proxy_url if acc else api_url
                                    _key = acc.api_key if acc else api_key
                                    vr_user_content = [{"type": "text", "text": f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info[:500]}"}]
                                    import base64 as _b64
                                    if prod.images:
                                        try:
                                            if Path(prod.images[0]).exists():
                                                with open(prod.images[0], "rb") as f_img:
                                                    data_b64 = _b64.b64encode(f_img.read()).decode("utf-8")
                                                suffix = Path(prod.images[0]).suffix.lower()
                                                mime = "image/png"
                                                if suffix in [".jpg", ".jpeg"]: mime = "image/jpeg"
                                                elif suffix == ".webp": mime = "image/webp"
                                                vr_user_content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data_b64}"}})
                                        except Exception:
                                            pass
                                    vr_user_msg = vr_user_content if len(vr_user_content) > 1 else f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info[:500]}"
                                    vr_llm_r = requests.post(
                                        f"{_url}/v1/chat/completions",
                                        headers={"Authorization": f"Bearer {_key}", "Content-Type": "application/json"},
                                        json={"model": llm_model, "messages": [{"role": "system", "content": vr_system_prompt}, {"role": "user", "content": vr_user_msg}], "temperature": 0.7},
                                        timeout=600
                                    )
                                    if vr_llm_r.status_code == 200:
                                        vr_content = vr_llm_r.json()["choices"][0]["message"]["content"]
                                        vr_script = parse_llm_json_robust(vr_content)
                                        vr_scenes_data = vr_script.get("scenes", [])
                                        (prod_dir / "vr_script.json").write_text(json.dumps(vr_script, indent=2, ensure_ascii=False), encoding="utf-8")
                                except Exception as e_llm:
                                    self._log(f"    ❌ LLM VR Script: {e_llm}")

                            if vr_scenes_data and not prod.video_review_scenes:
                                prod.video_review_scenes = []
                                for si_init, sd_init in enumerate(vr_scenes_data):
                                    prod.video_review_scenes.append(VideoReviewScene(
                                        id=si_init + 1, scene_title=f"Scene {si_init + 1}",
                                        image_prompt=sd_init.get("image_prompt", ""),
                                        video_prompt=sd_init.get("video_prompt", ""),
                                        voiceover_text=sd_init.get("voiceover_text", ""),
                                        status="pending"))
                                self._queue_product_updated(prod.id)

                            # Generate VR clips (with parallel threads for I2V)
                            vr_clips = []
                            scenes = prod.video_review_scenes or []
                            have_all_clips = bool(scenes) and all(_clip_ok(s) for s in scenes)
                            # Reuse/assemble only when every declared scene has a
                            # valid clip.  This also lets only-video mode repair a
                            # previously partial Video Review instead of assembling
                            # another incomplete final file.
                            assembly_only = have_all_clips and not force_regen

                            if assembly_only:
                                vr_clips = [s.video_clip_path for s in scenes if _clip_ok(s)]
                                if vr_clips:
                                    self._log(f"    ♻️ Tái sử dụng {len(vr_clips)} clip AI.")
                            elif not has_grok_deps:
                                self._log("    ❌ Thiếu grok_video_tool.py")
                            elif scenes and self.settings.get("vr_stream_pipeline", True):
                                vr_clips = self._run_vr_scene_stream(
                                    prod=prod,
                                    prod_folder=prod_dir,
                                    scenes=scenes,
                                    scene_data=vr_scenes_data,
                                    video_model=vr_video_model,
                                    resolution=vr_resolution,
                                    clip_duration=vr_clip_dur,
                                    aspect=vr_aspect,
                                    image_model=vr_img_model,
                                    api_url=api_url,
                                    api_key=api_key,
                                    force_regen=force_regen,
                                    image_threads=img_threads,
                                    video_threads=vid_threads,
                                )
                            elif scenes:
                                grok_client = GrokClient(api_url, api_key)
                                use_veo = is_veo_model(vr_video_model)

                                for si, sc_data in enumerate(vr_scenes_data):
                                    if self._stopped:
                                        break
                                    sc_obj = scenes[si] if si < len(scenes) else None
                                    if sc_obj is not None and _clip_ok(sc_obj) and not force_regen:
                                        vr_clips.append(sc_obj.video_clip_path)
                                        continue

                                    img_prompt = sc_data.get("image_prompt", "")
                                    vid_prompt = sc_data.get("video_prompt", "")
                                    if not img_prompt:
                                        continue

                                    # Image: sử dụng ảnh đã pre-generate ở Phase 2, fallback tạo mới
                                    img_path = vr_clip_dir / f"scene_{si+1}.png"
                                    if sc_obj and sc_obj.image_path and Path(sc_obj.image_path).exists() and not force_regen:
                                        img_path = Path(sc_obj.image_path)
                                        self._log(f"    ✔ VR Ảnh scene {si+1}: đã có sẵn từ Phase 2")
                                    elif not img_path.exists() or force_regen:
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
                                            if si < len(scenes):
                                                scenes[si].image_path = str(img_path)
                                                scenes[si].status = "processing"
                                                self._queue_product_updated(prod.id)
                                        except Exception as e_img:
                                            self._log(f"      ❌ VR Ảnh scene {si+1}: {e_img}")
                                            if si < len(scenes):
                                                scenes[si].status = "error"
                                            continue

                                    if not img_path.exists():
                                        continue

                                    if si < len(scenes):
                                        scenes[si].image_path = str(img_path)

                                # ── Song song tất cả VR Video I2V requests ──
                                vr_pending = []
                                for si, sc_data in enumerate(vr_scenes_data):
                                    sc_obj = scenes[si] if si < len(scenes) else None
                                    if sc_obj and _clip_ok(sc_obj) and not force_regen:
                                        continue  # Đã có clip, skip
                                    img_path = vr_clip_dir / f"scene_{si+1}.png"
                                    if sc_obj and sc_obj.image_path and Path(sc_obj.image_path).exists() and not force_regen:
                                        img_path = Path(sc_obj.image_path)
                                    if not img_path.exists():
                                        continue
                                    vid_prompt = sc_data.get("video_prompt", "")
                                    img_prompt = sc_data.get("image_prompt", "")
                                    full_vid_prompt = f"{vid_prompt}. {img_prompt}" if vid_prompt else img_prompt
                                    vr_pending.append((si, img_path, full_vid_prompt))

                                if vr_pending:
                                    _vr_results = {}  # {si: clip_path}
                                    _vr_lock = threading.Lock()
                                    # Chụp snapshot account một lần trước khi mở các worker.
                                    # CaptchaBridge đã tự khóa riêng bước lấy reCAPTCHA token;
                                    # các request submit/poll video vẫn được phép chạy song song.
                                    _g_accs_vr = list(self.acc_mgr.get_all_google()) if use_veo else []

                                    def _gen_vr_clip(si_val, img_p, prompt_val):
                                        if self._stopped:
                                            return
                                        vid_out_t = vr_clip_dir / f"clip_{si_val+1}.mp4"
                                        loop_t = None
                                        success_t = False
                                        try:
                                            loop_t = asyncio.new_event_loop()
                                            asyncio.set_event_loop(loop_t)
                                            vr_job_t = VideoJob(
                                                id=uuid.uuid4().hex[:8], mode="image-to-video",
                                                prompt=prompt_val, model=vr_video_model,
                                                duration=vr_clip_dur, resolution=vr_resolution,
                                                aspect_ratio=vr_aspect, image_url=str(img_p),
                                                local_path=str(vid_out_t), output_name=f"vr_clip_{si_val+1}",
                                                max_retries=2,
                                                google_auth_root=str(THREADS_AUTH_ROOT),
                                                google_accounts_file=str(ACCOUNTS_FILE),
                                            )
                                            if use_veo:
                                                if _g_accs_vr:
                                                    _g_acc_t = _g_accs_vr[si_val % len(_g_accs_vr)]
                                                    veo_bearer_t = _g_acc_t.api_key
                                                    veo_pid_t = _g_acc_t.proxy_url or GROK_DEFAULT_PROJECT_ID
                                                    vr_job_t.account_name = _g_acc_t.name
                                                    vr_job_t.google_profile_name = (
                                                        getattr(_g_acc_t, "profile_name", "") or ""
                                                    )
                                                    self._log(f"🎬 VR Video Render #{si_val+1}: {prompt_val[:70]}...")
                                                else:
                                                    self._log(f"  ❌ VR Render #{si_val+1}: Không tìm thấy tài khoản Google!")
                                                    return
                                                cb_t = getattr(self, "captcha_bridge", None) or self.captcha_bridge
                                                success_t = loop_t.run_until_complete(
                                                    run_veo_flow(vr_job_t, veo_bearer_t, veo_pid_t, cb_t, log=self._log, progress=None)
                                                )
                                            else:
                                                try:
                                                    rid_t = loop_t.run_until_complete(grok_client.submit(vr_job_t))
                                                    success_t = False
                                                    for _ in range(90):
                                                        time.sleep(10)
                                                        poll_r_t = loop_t.run_until_complete(grok_client.poll(rid_t, use_proxy=False))
                                                        st_t = poll_r_t.get("status", "")
                                                        if st_t == "completed":
                                                            vid_url_t = poll_r_t.get("video_url", "") or poll_r_t.get("url", "")
                                                            if vid_url_t:
                                                                loop_t.run_until_complete(grok_client.download(vid_url_t, vid_out_t, use_proxy=False))
                                                            success_t = True
                                                            break
                                                        elif st_t == "failed":
                                                            break
                                                except Exception:
                                                    success_t = False

                                            if success_t and vid_out_t.exists():
                                                with _vr_lock:
                                                    _vr_results[si_val] = str(vid_out_t)
                                                if si_val < len(scenes):
                                                    scenes[si_val].video_clip_path = str(vid_out_t)
                                                    scenes[si_val].status = "done"
                                                    self._queue_product_updated(prod.id)
                                                self._log(f"  ✔ VR Video clip #{si_val+1} render OK!")
                                            else:
                                                if si_val < len(scenes):
                                                    scenes[si_val].status = "error"
                                                self._log(f"  ❌ VR Render #{si_val+1} Exception: {getattr(vr_job_t, 'error', 'Video generation failed or output not downloaded.')}")
                                        except Exception as e_vid_t:
                                            _record_background_exception(f"VR CLIP #{si_val+1} EXCEPTION")
                                            self._log(f"      ❌ VR Clip {si_val+1}: {e_vid_t}")
                                        finally:
                                            if loop_t is not None and not loop_t.is_closed():
                                                try:
                                                    loop_t.stop()
                                                except Exception:
                                                    pass
                                                try:
                                                    loop_t.close()
                                                except Exception:
                                                    pass
                                            try:
                                                asyncio.set_event_loop(None)
                                            except Exception:
                                                pass

                                    # Dùng đúng cấu hình "Luồng Video" cho cả Veo và Grok.
                                    # Mỗi worker có event loop riêng giống JobWorker của tool gốc;
                                    # chỉ bước cấp token được CaptchaBridge tuần tự hóa nội bộ.
                                    vr_max_workers = max(1, min(vid_threads, len(vr_pending)))
                                    with ThreadPoolExecutor(max_workers=vr_max_workers) as vr_vid_pool:
                                        vr_vid_futs = [vr_vid_pool.submit(_gen_vr_clip, *args) for args in vr_pending]
                                        for vr_f in as_completed(vr_vid_futs):
                                            try: vr_f.result()
                                            except Exception as e_vr_f: self._log(f"    ❌ VR Video thread error: {e_vr_f}")

                                    # Collect results in order
                                    for si_c in range(len(vr_scenes_data)):
                                        if si_c in _vr_results:
                                            vr_clips.append(_vr_results[si_c])
                                        elif scenes[si_c].video_clip_path and Path(scenes[si_c].video_clip_path).exists():
                                            vr_clips.append(scenes[si_c].video_clip_path)

                            # Assemble VR clips → final combined video only when all
                            # declared scenes are present.  Partial clips must remain
                            # retryable and must never overwrite the final status.
                            # Re-check the scene objects themselves immediately
                            # before assembly.  This is intentionally stricter
                            # than counting temporary clip results: a failed
                            # retry must never be able to produce a final from a
                            # stale/partial clip list.
                            expected_vr_clips = len(scenes)
                            valid_scene_clips = sum(1 for sc in scenes if _clip_ok(sc))
                            clips_ready_for_assembly = (
                                expected_vr_clips > 0
                                and valid_scene_clips == expected_vr_clips
                                and len(vr_clips) == expected_vr_clips
                                and all(Path(cp).exists() for cp in vr_clips)
                            )
                            if expected_vr_clips > 0 and not clips_ready_for_assembly:
                                self._log(
                                    f"    ⚠️ Chưa ghép Video Review: chỉ có "
                                    f"{valid_scene_clips}/{expected_vr_clips} cảnh hợp lệ."
                                )

                            if clips_ready_for_assembly:
                                vr_part2 = prod_dir / f"vr_part2_{prod.id}.mp4"

                                processed_vr_clips = []
                                valid_scenes = [s for s in scenes if hasattr(s, 'video_clip_path') and s.video_clip_path and Path(s.video_clip_path).exists()]
                                for i, cp in enumerate(vr_clips):
                                    temp_settings = dict(self.settings)
                                    if self.settings.get("vr_auto_speed", True):
                                        vid_dur = get_media_duration(cp)
                                        voice_dur = 0
                                        if i < len(valid_scenes) and hasattr(valid_scenes[i], 'voice_duration'):
                                            voice_dur = getattr(valid_scenes[i], 'voice_duration', 0)
                                        if voice_dur > 0 and vid_dur > 0:
                                            target_dur = voice_dur
                                            speed = vid_dur / target_dur
                                            if i == len(vr_clips) - 1:
                                                speed = min(speed, 3.0)
                                            temp_settings["vr_speed"] = speed
                                    proc_cp = str(vr_clip_dir / f"proc_{Path(cp).name}")
                                    if process_external_video(cp, proc_cp, temp_settings, log_fn=None):
                                        processed_vr_clips.append(proc_cp)
                                    else:
                                        processed_vr_clips.append(cp)
                                vr_clips = processed_vr_clips

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
                                    except Exception:
                                        cmd = [FFMPEG_PATH, "-y", "-f", "concat", "-safe", "0",
                                               "-i", str(concat_file), "-c:v", "libx264", "-preset", "fast",
                                               "-crf", "22", str(vr_part2)]
                                        subprocess.run(cmd, capture_output=True, timeout=300,
                                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

                                if vr_part2.exists():
                                    # Overlay background if enabled
                                    if self.settings.get("vr_overlay_bg", False) and cbg and Path(cbg).exists():
                                        vr_overlay_out = prod_dir / f"vr_overlay_{prod.id}.mp4"
                                        overlay_scale = self.settings.get("vr_overlay_scale", 85) / 100.0
                                        bg_dur = get_media_duration(cbg) or 10.0
                                        start_time = random.uniform(0.0, bg_dur)
                                        filter_complex = (
                                            f"[1:v]scale=1080*{overlay_scale}:-1[over_v];"
                                            f"[0:v]trim={start_time:.2f}:{start_time + get_media_duration(str(vr_part2)):.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg_v];"
                                            f"[bg_v][over_v]overlay=(W-w)/2:(H-h)/2[out_v]"
                                        )
                                        cmd = [
                                            FFMPEG_PATH, "-y",
                                            "-stream_loop", "-1", "-i", str(cbg),
                                            "-i", str(vr_part2),
                                            "-filter_complex", filter_complex,
                                            "-map", "[out_v]",
                                            "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                                            "-t", f"{get_media_duration(str(vr_part2)):.2f}",
                                            str(vr_overlay_out)
                                        ]
                                        try:
                                            subprocess.run(cmd, capture_output=True, timeout=120, check=True,
                                                          creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                                            if vr_overlay_out.exists():
                                                vr_part2 = vr_overlay_out
                                        except Exception:
                                            pass

                                    # Add voiceover
                                    vr_final = prod_dir / f"vr_final_{prod.id}.mp4"
                                    vr_part2_str = add_voiceover_to_video(
                                        str(vr_part2), prod.video_review_voice_path,
                                        self.settings.get("vr_keep_audio", True),
                                        str(vr_final), self._log
                                    )
                                    vr_part2 = Path(vr_part2_str)

                                    # Combine Part1 + Part2
                                    combined_path = prod_dir / f"combined_{prod.id}.mp4"
                                    final = combine_final_video(
                                        prod.final_video_path, str(vr_part2),
                                        str(combined_path), "fade_black", self._log
                                    )
                                    if final:
                                        apply_natural_metadata(final, self.settings, self._log)
                                        prod.final_combined_path = final
                                        prod.video_review_mode = "ai_generate"
                                        prod.status = "✅ Hoàn thành (+ AI Video Review)"
                                        self._log(f"    ✅ AI Video Review OK: {Path(final).name}")

                    except Exception as e_vr:
                        _record_background_exception("VIDEO REVIEW EXCEPTION")
                        self._log(f"    ⚠️ Lỗi Video Review: {e_vr}")

                self._queue_product_updated(prod.id)
                time.sleep(self.settings.get("delay_sec", 2))

            if self._stopped:
                self._queue_finished(False, "⏹️ Đã dừng — dự án chưa được đánh dấu hoàn tất.")
            else:
                self._queue_finished(True, "🎉 Hoàn tất!")
        except Exception as e:
            _record_background_exception("FULL PIPELINE EXCEPTION")
            self._queue_finished(False, f"Lỗi hệ thống: {e}")




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
        self.profile_name = _resolve_google_profile_name(profile_name) or profile_name
        self.is_running = True
        self.context = None
        self.browser = None
        self.playwright = None

    def stop(self):
        # Playwright objects must be closed by their owning worker thread.
        self.is_running = False

    def run(self):
        from google_flow_auth import acquire_session, expected_email
        lock = _get_google_profile_refresh_lock(self.profile_name)
        acquired = False
        try:
            deadline = time.monotonic() + 120
            self.status_sig.emit("Đang chờ profile Google sẵn sàng...", "#7c3aed")
            while self.is_running and time.monotonic() < deadline:
                if lock.acquire(timeout=0.5):
                    acquired = True
                    break
            if not self.is_running:
                return
            if not acquired:
                self.error_sig.emit("profile_busy: profile đang được sử dụng; hãy kết thúc tác vụ đó trước.")
                return
            result = acquire_session(
                THREADS_AUTH_ROOT, self.profile_name, GoogleTokenRefreshWorker._check_token,
                _persist_flow_storage_state, interactive=True,
                expected=expected_email(THREADS_AUTH_ROOT, self.profile_name),
                cancelled=lambda: not self.is_running,
                status=lambda msg: self.status_sig.emit(msg, "#7c3aed"),
            )
            if result.ok:
                self.success_sig.emit(result.email, result.token, result.project)
            elif self.is_running:
                self.error_sig.emit(result.error)
        except Exception as exc:
            self.error_sig.emit(f"auth_error: {type(exc).__name__}")
        finally:
            if acquired:
                lock.release()


class GoogleTokenRefreshWorker(QThread):
    status_sig = pyqtSignal(str, str)
    token_updated = pyqtSignal(str, str, str, int)
    finished_all = pyqtSignal(int, int)
    log_sig = pyqtSignal(str)

    def __init__(
        self,
        accounts: list,
        sync_credits: bool = True,
        allow_interactive_login: bool = False,
    ):
        super().__init__()
        self.accounts = accounts
        # Startup/periodic token health checks should not open the saved
        # Chrome profile a second time merely to read the Credits page.  That
        # extra browser session is unrelated to authentication and can race a
        # video worker that is refreshing the same profile.  Explicit 🔑
        # login is the only owner of visible interactive recovery.
        self.sync_credits = bool(sync_credits)
        # Health checks must never seize the profile for a 10-minute visible
        # login window.  Explicit 🔑 login owns that interactive recovery.
        self.allow_interactive_login = bool(allow_interactive_login)
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
            profile_name = getattr(acc, "profile_name", "") or _resolve_google_profile_name(acc_name)
            
            self.status_sig.emit(f"🔍 Đang kiểm tra token cho {profile_name}...", "#2563eb")
            self.log_sig.emit(f"🔍 [Token Check] Kiểm tra hiệu lực token: {profile_name}")
            
            is_valid = False
            email_or_err = "Token trống"
            if token and len(token) >= 20:
                try: is_valid, email_or_err = self._check_token(token)
                except Exception as e: email_or_err = str(e)
            expected_email = (
                _resolve_flow_expected_email(profile_name)
                or acc_name.replace("Google_", "", 1)
            )
            if is_valid and not _google_identity_matches(expected_email, email_or_err):
                is_valid = False
                email_or_err = (
                    f"Token thuộc {email_or_err}, profile cần {expected_email}"
                )
            
            credits_val = -1
            from google_flow_auth import transient_auth_error
            if not is_valid and transient_auth_error(email_or_err):
                self.log_sig.emit(f"⚠️ [Token Check] Chưa kiểm tra được phiên: {email_or_err}. Giữ nguyên tài khoản.")
                expired += 1
                continue
            if is_valid:
                self.log_sig.emit(f"✅ [Token Check] {profile_name}: Token còn hiệu lực ({email_or_err})")
                if self.sync_credits:
                    self.status_sig.emit(f"💰 Đang đồng bộ credit cho {profile_name}...", "#8b5cf6")
                    success_cr, balance_cr, limit_cr, err_cr = self._try_check_credits(profile_name)
                    if success_cr:
                        credits_val = balance_cr
                        self.log_sig.emit(f"💰 [Token Check] {profile_name}: Credit còn lại: {balance_cr}")
                self.token_updated.emit(acc_name, token, acc.proxy_url or DEFAULT_PROJECT_ID, credits_val)
                valid += 1
            else:
                self.log_sig.emit(f"⚠️ [Token Check] {profile_name}: Chưa có token được xác minh ({email_or_err}). Đang đọc lại phiên...")
                self.status_sig.emit(f"⏳ Đang tự động làm mới token cho {profile_name}...", "#f59e0b")
                
                # Always start from the real saved profile.  Cookie snapshots
                # are only a fallback because they may contain an expired
                # NextAuth session even while Chrome is still signed in.
                success, new_token, new_project_id, err_msg = self._try_headless_refresh(
                    profile_name,
                    prefer_profile=True,
                    allow_visible=self.allow_interactive_login,
                )
                if not self.is_running:
                    break
                if success:
                    new_email = str(err_msg).removeprefix('session_verified:')
                    is_valid_new = str(err_msg).startswith('session_verified:') and _google_identity_matches(expected_email, new_email)
                    if is_valid_new:
                        self.log_sig.emit(f"✅ [Token Check] {profile_name}: Tự động refresh thành công! ({new_email})")
                        if self.sync_credits:
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
    def _try_headless_refresh(profile_name: str, prefer_profile: bool = True, allow_visible: bool = False) -> tuple:
        from google_flow_auth import acquire_session, expected_email
        profile_name = _resolve_google_profile_name(profile_name)
        result = acquire_session(
            THREADS_AUTH_ROOT, profile_name, GoogleTokenRefreshWorker._check_token,
            _persist_flow_storage_state, use_snapshot=not prefer_profile,
            expected=expected_email(THREADS_AUTH_ROOT, profile_name),
        )
        return result.ok, result.token, result.project, (
            "session_verified:" + result.email if result.ok else result.error
        )

    @staticmethod
    def _try_visible_browser_login(profile_name, profile_dir, cookies_file, FLOW_URL):
        from google_flow_auth import acquire_session, expected_email
        root = Path(profile_dir).parent.parent.resolve()
        result = acquire_session(root, profile_name, GoogleTokenRefreshWorker._check_token,
            _persist_flow_storage_state, interactive=True,
            expected=expected_email(root, profile_name))
        return result.ok, result.token, result.project, (
            "session_verified:" + result.email if result.ok else result.error)

    @staticmethod
    def _check_token(token):
        from google_flow_auth import check_token
        return check_token(token, make_httpx_client)

    @staticmethod
    def _try_check_credits(profile_name: str) -> tuple:
        from playwright.sync_api import sync_playwright
        profile_name = _resolve_google_profile_name(profile_name)
        shared_dir = _resolve_flow_shared_dir(profile_name)
        from google_flow_auth import profile_paths
        profile_dir, _ = profile_paths(shared_dir, profile_name)
        cookies_file = shared_dir / "cookies" / f"{profile_name}.json"
        
        use_cookie_file = cookies_file.exists()
        use_profile_dir = profile_dir.exists()
        if not use_cookie_file and not use_profile_dir:
            return False, 0, 0, "No profile/cookies"

        profile_lock = _get_google_profile_refresh_lock(profile_name)
        if not profile_lock.acquire(timeout=45):
            return False, 0, 0, "Profile đang được tool khác sử dụng"
            
        playwright = None
        browser = None
        context = None
        try:
            playwright = sync_playwright().start()
            from google_flow_auth import launch_options
            options = launch_options(shared_dir, profile_name, True)
            if use_profile_dir:
                context = playwright.chromium.launch_persistent_context(str(profile_dir), **options)
            else:
                browser = playwright.chromium.launch(**options)
                context = browser.new_context(storage_state=str(cookies_file))
            page = context.pages[0] if context.pages else context.new_page()
            url = "https://one.google.com/ai/activity?utm_source=flow&utm_medium=web&utm_campaign=flow_ai_credits_page&dm=1&g1_landing_page=0"
            page.goto(url, timeout=25000, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)
            
            body_text = page.evaluate("document.body.innerText")
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
            try:
                profile_lock.release()
            except Exception:
                pass


# run_veo_flow lives in grok_video_tool.py and calls that module's refresh
# worker internally.  Bind it to the corrected resolver above for this tool,
# so automatic refresh during a video job uses the same profile mapping as the
# Account Manager and the saved Chrome profiles.  The wrapper also prevents
# two video workers from opening the same persistent profile simultaneously.
_raw_google_headless_refresh = GoogleTokenRefreshWorker._try_headless_refresh


def _locked_google_headless_refresh(profile_name, prefer_profile=True, allow_visible=False):
    from google_flow_auth import single_refresh
    def action():
        lock = _get_google_profile_refresh_lock(profile_name)
        if not lock.acquire(timeout=90):
            return False, '', '', 'profile_busy: profile đang được sử dụng'
        try:
            result = _raw_google_headless_refresh(profile_name, prefer_profile=prefer_profile, allow_visible=False)
            if result[0] or not prefer_profile or str(result[3]).startswith(('flow_transport_required:', 'google_signin_rejected:')):
                return result
            snapshot = THREADS_AUTH_ROOT / 'cookies' / (profile_name + '.json')
            if snapshot.is_file():
                fallback = _raw_google_headless_refresh(profile_name, prefer_profile=False, allow_visible=False)
                if fallback[0]:
                    return fallback
            return result
        finally:
            lock.release()
    return single_refresh(THREADS_AUTH_ROOT, profile_name, action)


GoogleTokenRefreshWorker._try_headless_refresh = staticmethod(_locked_google_headless_refresh)
# The parent tool already wraps its own refresh worker in the shared lock
# (`_refresh_google_profile`).  Do not replace the parent class method here:
# doing so makes that wrapper acquire the same profile lock a second time when
# this module imports the parent tool, which can look like a token/login hang.


class GoogleSingleRefreshWorker(QThread):
    status_sig = pyqtSignal(str, str)
    token_updated = pyqtSignal(str, str, str, int)
    error_sig = pyqtSignal(str)
    log_sig = pyqtSignal(str)

    def __init__(self, account, profile_name: str):
        super().__init__()
        self.account = account
        self.profile_name = profile_name
        self.is_running = True

    def stop(self):
        self.is_running = False

    def run(self):
        if not self.is_running:
            return
        self.status_sig.emit(f"🔍 Đang kiểm tra token cho {self.profile_name}...", "#2563eb")
        token = self.account.api_key
        
        is_valid = False
        info = "Token trống"
        if token and len(token) >= 20:
            try: is_valid, info = GoogleTokenRefreshWorker._check_token(token)
            except Exception as e: info = str(e)
                
        credits_val = -1
        from google_flow_auth import transient_auth_error, identity_matches, expected_email
        if not is_valid and transient_auth_error(info):
            self.status_sig.emit(f"Chưa kiểm tra được: {info}. Phiên vẫn được giữ lại.", "#d97706")
            return
        if is_valid and not identity_matches(expected_email(THREADS_AUTH_ROOT, self.profile_name), info):
            self.error_sig.emit("wrong_account: token không thuộc profile đã chọn")
            return
        if is_valid:
            if not self.is_running:
                return
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
            
            success, new_token, new_project_id, err_msg = GoogleTokenRefreshWorker._try_headless_refresh(
                self.profile_name,
                prefer_profile=True,
                allow_visible=False,
            )
            if not self.is_running:
                return
            if success:
                new_email = str(err_msg).removeprefix('session_verified:')
                is_valid_new = str(err_msg).startswith('session_verified:') and identity_matches(expected_email(THREADS_AUTH_ROOT, self.profile_name), new_email)
                if is_valid_new:
                    self.status_sig.emit(f"💰 Đang đồng bộ credit cho {self.profile_name}...", "#8b5cf6")
                    success_cr, balance_cr, limit_cr, err_cr = GoogleTokenRefreshWorker._try_check_credits(self.profile_name)
                    if success_cr:
                        credits_val = balance_cr
                        self.log_sig.emit(f"💰 [Token Check] {self.profile_name}: Credit còn lại: {balance_cr}")
                    self.token_updated.emit(self.account.name, new_token, new_project_id, credits_val)
                    self.status_sig.emit(f"✅ Đã tự động làm mới token: {self.profile_name} ({new_email})", "#16a34a")
                    return
            self.error_sig.emit(
                "Không thể tự động làm mới token. "
                f"{err_msg or 'Cần đăng nhập lại bằng nút 🔑.'}"
            )
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
            self.main_win._load_archetypes_into_combo(self.main_win.cb_ctx, is_global=True)
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
        
        # Thêm QListWidget làm sidebar thư mục ở bên trái
        self.folder_list = QListWidget()
        self.folder_list.setFixedWidth(180)
        self.folder_list.setStyleSheet("""
            QListWidget { background-color: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; font-weight: bold; }
            QListWidget::item { padding: 8px 10px; border-bottom: 1px solid #334155; }
            QListWidget::item:selected { background-color: #7c3aed; color: white; border-radius: 4px; }
        """)
        self.folder_list.itemSelectionChanged.connect(self._on_folder_changed)
        select_lay.addWidget(self.folder_list, stretch=1)

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
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["ID", "Tên sản phẩm", "Giá", "Yêu thích", "Voice MP3"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
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

    def _on_folder_changed(self):
        self._filter_products()

    def _toggle_favorite(self, prod):
        prod["favorite"] = not prod.get("favorite", False)
        selected_pid = None
        selected_row = self.table.currentRow()
        if selected_row >= 0:
            selected_pid = self.table.item(selected_row, 0).text()
            
        prod_json = DOWNLOADS_DIR / "products.json"
        try:
            prod_json.write_text(json.dumps(self.products, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"Lỗi lưu trạng thái yêu thích: {e}")
            
        self.load_crawled_products()
        
        if selected_pid:
            for r in range(self.table.rowCount()):
                if self.table.item(r, 0).text() == selected_pid:
                    self.table.setCurrentCell(r, 0)
                    break

    def load_crawled_products(self):
        self.products = []
        prod_json = DOWNLOADS_DIR / "products.json"
        if prod_json.exists():
            try:
                self.products = json.loads(prod_json.read_text(encoding="utf-8"))
            except: pass
            
        curr_folder_item = self.folder_list.currentItem()
        curr_folder_text = curr_folder_item.text() if curr_folder_item else "📁 Tất cả thư mục"
        
        self.folder_list.clear()
        self.folder_list.addItem("📁 Tất cả thư mục")
        self.folder_list.addItem("📁 Chưa phân loại")
        self.folder_list.addItem("❤️ Yêu thích")
        self.folder_list.addItem("🕒 Gần đây")
        
        custom_folders = set()
        for p in self.products:
            f = p.get("folder", "Chưa phân loại")
            if f and f != "Chưa phân loại":
                custom_folders.add(f)
                
        for f in sorted(list(custom_folders)):
            self.folder_list.addItem(f"📁 {f}")
            
        found_item = None
        for i in range(self.folder_list.count()):
            item = self.folder_list.item(i)
            if item.text() == curr_folder_text:
                found_item = item
                break
        if found_item:
            self.folder_list.setCurrentItem(found_item)
        else:
            self.folder_list.setCurrentRow(0)
            
        self._filter_products()

    def _filter_products(self):
        txt = self.edit_search.text().strip().lower()
        self.table.setRowCount(0)
        
        curr_folder_item = self.folder_list.currentItem()
        selected_folder = curr_folder_item.text() if curr_folder_item else "📁 Tất cả thư mục"
        
        folder_filtered = []
        if selected_folder == "📁 Tất cả thư mục":
            folder_filtered = list(self.products)
        elif selected_folder == "📁 Chưa phân loại":
            folder_filtered = [p for p in self.products if p.get("folder", "Chưa phân loại") == "Chưa phân loại"]
        elif selected_folder == "❤️ Yêu thích":
            folder_filtered = [p for p in self.products if p.get("favorite", False)]
        elif selected_folder == "🕒 Gần đây":
            recent_prods = [p for p in self.products if p.get("last_used", 0) > 0]
            recent_prods.sort(key=lambda x: x.get("last_used", 0), reverse=True)
            folder_filtered = recent_prods[:20]
        else:
            f_name = selected_folder.replace("📁 ", "").strip()
            folder_filtered = [p for p in self.products if p.get("folder", "Chưa phân loại") == f_name]
            
        filtered = []
        for p in folder_filtered:
            pid = p.get("product_id", "")
            title = p.get("title", "")
            if not txt or txt in pid.lower() or txt in title.lower():
                filtered.append(p)
                
        self.table.setRowCount(len(filtered))
        for r, p in enumerate(filtered):
            self.table.setItem(r, 0, QTableWidgetItem(p.get("product_id", "")))
            self.table.setItem(r, 1, QTableWidgetItem(p.get("title", "")))
            self.table.setItem(r, 2, QTableWidgetItem(p.get("price", "")))
            
            is_fav = p.get("favorite", False)
            fav_btn = QPushButton("❤️" if is_fav else "🖤")
            fav_btn.setStyleSheet("background: transparent; border: none; font-size: 14px;")
            fav_btn.setToolTip("Click để yêu thích / bỏ yêu thích")
            fav_btn.clicked.connect(lambda _, _p=p: self._toggle_favorite(_p))
            self.table.setCellWidget(r, 3, fav_btn)
            
            voices = p.get("voices", [])
            voice_item = QTableWidgetItem(f"✅ {len(voices)} files" if voices else "—")
            voice_item.setForeground(QColor("#06b6d4") if voices else QColor("#64748b"))
            self.table.setItem(r, 4, voice_item)
            
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
            if item:
                w = item.widget()
                if w:
                    w.deleteLater()
            
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
        row = self.table.currentRow()
        if row >= 0:
            pid = self.table.item(row, 0).text()
            prod = next((p for p in self.products if p.get("product_id") == pid), None)
            if prod:
                prod["last_used"] = time.time()
                prod_json = DOWNLOADS_DIR / "products.json"
                try:
                    prod_json.write_text(json.dumps(self.products, ensure_ascii=False, indent=2), encoding="utf-8")
                except:
                    pass
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
        self.btn_option.clicked.connect(self._show_scene_menu)

        self.btn_rerender = QPushButton("🔄")
        self.btn_rerender.setToolTip("Render lại ảnh này (dùng ảnh tham chiếu SP)")
        self.btn_rerender.setFixedSize(26, 26)
        self.btn_rerender.setStyleSheet(self._btn_style("#ea580c", "#c2410c"))
        self.btn_rerender.clicked.connect(self._rerender_single_scene)
        
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
        h_btns_lay.addWidget(self.btn_rerender)
        h_btns_lay.addWidget(self.btn_option)
        
        c6.setFixedWidth(90)
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

    def _show_scene_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#1e293b;color:white;border:1px solid #334155;padding:4px;}"
                          "QMenu::item{padding:6px 16px;border-radius:4px;}"
                          "QMenu::item:selected{background:#4338ca;}")
        a_rerender = menu.addAction("🔄 Render lại ảnh (tham chiếu SP)")
        a_rerender.triggered.connect(self._rerender_single_scene)
        a_del = menu.addAction("🗑️ Xóa ảnh phân cảnh")
        a_del.triggered.connect(self._delete_scene_image)
        menu.exec(self.btn_option.mapToGlobal(self.btn_option.rect().bottomLeft()))

    def _delete_scene_image(self):
        if self.scene.image_path and Path(self.scene.image_path).exists():
            try: Path(self.scene.image_path).unlink()
            except: pass
        self.scene.image_path = ""
        self.scene.status = "pending"
        self.refresh_image()
        win = self.window()
        if hasattr(win, "_save_projects"): win._save_projects()

    def _rerender_single_scene(self):
        """Re-render this single scene's image using product reference image."""
        # Find the product card and main window
        card = self.card
        if not card or not hasattr(card, 'product'):
            return
        prod = card.product
        win = self.window()
        if not win or not hasattr(win, '_collect_settings'):
            return

        # Find product reference image
        ref_img_path = None
        if prod.images:
            for ri in prod.images:
                if ri and Path(ri).exists():
                    ref_img_path = ri
                    break

        if not ref_img_path:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Không có ảnh SP", "Sản phẩm không có ảnh tham chiếu. Hãy thêm ảnh sản phẩm trước.")
            return

        # Determine output path
        st = win._collect_settings()
        prod_folder = get_product_folder(prod, st)
        prod_images_dir = prod_folder / "images"
        prod_images_dir.mkdir(parents=True, exist_ok=True)

        target_img = prod_images_dir / f"scene_{self.scene.id}.png"

        # Delete old image
        if self.scene.image_path and Path(self.scene.image_path).exists():
            try: Path(self.scene.image_path).unlink()
            except: pass
        self.scene.image_path = ""
        self.scene.status = "pending"
        self.refresh_image()

        # Start background re-render
        self.btn_rerender.setEnabled(False)
        self.btn_rerender.setText("⏳")
        self.lbl_status.setText("⏳ Render...")
        self.lbl_status.setStyleSheet("color: #f59e0b; font-size: 8px; font-weight: bold;")

        import threading
        def _do_render():
            try:
                img_m = st.get("img_model", "gpt-image-2")
                is_veo = has_grok_deps and is_veo_model(img_m)

                prompt = self.scene.prompt
                if hasattr(win, '_log'):
                    win._log(f"🔄 Re-render Scene #{self.scene_idx+1}: {prompt[:60]}... (Ref: {Path(ref_img_path).name})")

                if is_veo:
                    g_accs = win.acc_mgr.get_all_google() if hasattr(win, 'acc_mgr') else []
                    acc = g_accs[0] if g_accs else None
                    bearer = acc.api_key if acc else st.get("veo_bearer", "")
                    proj_id = (acc.proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b") if acc else st.get("veo_project_id", "")
                    
                    import asyncio
                    import uuid
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    mid = loop.run_until_complete(
                        upload_veo_image(
                            client=None, file_path=str(ref_img_path),
                            bearer_token=bearer, project_id=proj_id,
                            log=lambda m: win._log(f"    [Google Upload] {m}") if hasattr(win, '_log') else None,
                            captcha_bridge=getattr(win, "captcha_bridge", None)
                        )
                    )
                    
                    if not mid:
                        raise Exception("Không thể upload ảnh tham chiếu lên Google Labs.")
                    
                    sc_aspect_raw = getattr(self.scene, "aspect_ratio", "4:3") or "4:3"
                    if "1:1" in sc_aspect_raw or "square" in sc_aspect_raw.lower():
                        sc_aspect = "1:1"
                    elif "9:16" in sc_aspect_raw or "3:4" in sc_aspect_raw or "4:5" in sc_aspect_raw or "portrait" in sc_aspect_raw.lower():
                        sc_aspect = "9:16"
                    else:
                        sc_aspect = "16:9"

                    job = VideoJob(
                        id=f"img_{uuid.uuid4().hex[:6]}",
                        model=img_m, mode="image-generation",
                        prompt=prompt, aspect_ratio=sc_aspect,
                        output_name=f"scene_{self.scene.id}",
                        local_path=str(prod_images_dir),
                        use_proxy=True,
                        ref_images=[mid],
                        account_name=acc.name if acc else "",
                        google_profile_name=(
                            getattr(acc, "profile_name", "") or ""
                            if acc else ""
                        ),
                        google_auth_root=str(THREADS_AUTH_ROOT),
                        google_accounts_file=str(ACCOUNTS_FILE),
                    )
                    job.max_retries = 2
                    
                    success = loop.run_until_complete(
                        run_veo_flow(
                            job, bearer, proj_id,
                            getattr(win, "captcha_bridge", None),
                            log=lambda m: win._log(f"        [Google Flow] {m}") if hasattr(win, '_log') else None,
                            progress=None
                        )
                    )
                    loop.close()

                    if success and job.local_path and Path(job.local_path).exists():
                        p_src = Path(job.local_path)
                        if p_src.is_file():
                            p_src.replace(target_img)
                        elif (p_src / f"scene_{self.scene.id}.png").exists():
                            (p_src / f"scene_{self.scene.id}.png").replace(target_img)
                        elif (p_src / f"{job.id}.png").exists():
                            (p_src / f"{job.id}.png").replace(target_img)
                        
                        self.scene.image_path = str(target_img)
                        self.scene.status = "done"
                        if hasattr(win, '_log'):
                            win._log(f"  ✔ Scene #{self.scene_idx+1} render lại OK!")
                    else:
                        raise Exception("Google Labs image generation failed.")
                else:
                    api_url = st.get("api_url", DEFAULT_PROXY_URL)
                    api_key = st.get("api_key", "")
                    acc = None
                    if hasattr(win, 'acc_mgr'):
                        acc = win.acc_mgr.next_codex_rr()
                    if acc:
                        api_url = acc.proxy_url or api_url
                        api_key = acc.api_key or api_key

                    with open(ref_img_path, "rb") as f_ref:
                        ref_bytes = f_ref.read()
                    ext = Path(ref_img_path).suffix.lower().lstrip(".") or "jpeg"
                    mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    files = {"image": (Path(ref_img_path).name, ref_bytes, mime)}
                    data_form = {"model": img_m, "prompt": prompt, "n": "1", "size": "1024x1280"}

                    ir = requests.post(
                        f"{api_url}/v1/images/edits",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files=files, data=data_form, timeout=600
                    )

                    if ir.status_code == 200:
                        it = ir.json().get("data", [{}])[0]
                        if "b64_json" in it:
                            import base64
                            target_img.write_bytes(base64.b64decode(it["b64_json"]))
                            self.scene.image_path = str(target_img)
                            self.scene.status = "done"
                        elif "url" in it:
                            img_resp = requests.get(it["url"], timeout=60)
                            if img_resp.status_code == 200:
                                target_img.write_bytes(img_resp.content)
                                self.scene.image_path = str(target_img)
                                self.scene.status = "done"

                        if hasattr(win, '_log'):
                            win._log(f"  ✔ Scene #{self.scene_idx+1} render lại OK!")
                    else:
                        raise Exception(f"API Error {ir.status_code}: {ir.text[:200]}")

            except Exception as e:
                self.scene.status = "error"
                if hasattr(win, '_log'):
                    win._log(f"  ❌ Scene #{self.scene_idx+1} Exception: {e}")
            finally:
                # Update UI from main thread
                from PyQt6.QtCore import QMetaObject, Qt as QtCore_Qt, Q_ARG
                QMetaObject.invokeMethod(self, "_on_rerender_done", QtCore_Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=_do_render, daemon=True).start()

    @pyqtSlot()
    def _on_rerender_done(self):
        self.btn_rerender.setEnabled(True)
        self.btn_rerender.setText("🔄")
        self.refresh_image()
        self.update_row_data()
        win = self.window()
        if hasattr(win, '_save_projects'): win._save_projects()


    def _on_voiceover_changed(self):
        self.scene.text_segment = self.edit_voiceover.toPlainText()
        win = self.window()
        if hasattr(win, "_save_projects"):
            win._save_projects()

    def refresh_image(self):
        img_path = self.scene.image_path
        if not img_path or not Path(img_path).exists():
            self._last_thumb_pix = None
            self.lbl_thumb.setPixmap(QPixmap())
            self.lbl_thumb.setText("⏳" if self.scene.status == "pending" else "Chưa tạo")
            self.lbl_thumb.setStyleSheet("background: #e2e8f0; border: 1px solid #cbd5e1; border-radius: 4px; color: #94a3b8; font-size: 8px;")
            return
        pix = load_scaled_pixmap(img_path, self.lbl_thumb.width() - 2, self.lbl_thumb.height() - 2)
        if pix is not None:
            if getattr(self, "_last_thumb_pix", None) is pix:
                return  # ảnh không đổi — khỏi repaint
            self._last_thumb_pix = pix
            self.lbl_thumb.setPixmap(pix)
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

        self.btn_rerender = QPushButton("🔄")
        self.btn_rerender.setToolTip("Lựa chọn Render lại phân cảnh Video Review")
        self.btn_rerender.setFixedSize(26, 26)
        self.btn_rerender.setStyleSheet(
            "QPushButton { background:#ea580c; color:white; border:none; border-radius:5px; font-size:10px; font-weight:bold; }"
            "QPushButton:hover { background:#c2410c; }"
        )
        self.btn_rerender.clicked.connect(self._show_vr_rerender_menu)
        c5l.addWidget(self.btn_rerender)

        c5.setFixedWidth(50)
        c5l.addStretch()
        lay.addWidget(c5)

        self._refresh_thumbnails()
        self._refresh_status()

    def _refresh_status(self):
        vp = self.vr_scene.video_clip_path
        if vp and Path(vp).exists():
            self.lbl_st.setText("✅ Xong")
            self.lbl_st.setStyleSheet("font-size:9px;font-weight:bold;color:#16a34a;")
        else:
            st = self.vr_scene.status
            txt = "Chờ" if st == "pending" else ("Lỗi" if st == "error" else st)
            color = "#dc2626" if st == "error" else ("#f59e0b" if st == "processing" else "#64748b")
            self.lbl_st.setText(txt)
            self.lbl_st.setStyleSheet(f"font-size:9px;font-weight:bold;color:{color};")

    def _refresh_thumbnails(self):
        ip = self.vr_scene.image_path
        if ip and Path(ip).exists():
            pix = load_scaled_pixmap(str(ip), self.lbl_img.width() - 2, self.lbl_img.height() - 2)
            if pix is not None:
                self.lbl_img.setPixmap(pix)
                self.lbl_img.setStyleSheet("border:1.5px solid #7c3aed; border-radius:4px;")
            else:
                self.lbl_img.setText("🖼")
                self.lbl_img.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8;")
        else:
            self.lbl_img.setText("🖼")
            self.lbl_img.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8;")

        vp = self.vr_scene.video_clip_path
        if vp and Path(vp).exists():
            thumb_path = Path(vp).parent / f".thumb_{Path(vp).stem}.jpg"
            if thumb_path.exists():
                pix = load_scaled_pixmap(str(thumb_path), self.lbl_vid.width() - 2, self.lbl_vid.height() - 2)
                if pix is not None:
                    if getattr(self, "_last_vid_pix", None) is not pix:
                        self._last_vid_pix = pix
                        self.lbl_vid.setPixmap(pix)
                        self.lbl_vid.setStyleSheet("border:1.5px solid #38bdf8; border-radius:4px;")
                else:
                    self._last_vid_pix = None
                    self.lbl_vid.setText("🎬")
            else:
                self._last_vid_pix = None
                spawn_video_thumb_job(str(vp), str(thumb_path))
                self._start_thumb_poll()
                self.lbl_vid.setText("🎬✔")
                self.lbl_vid.setStyleSheet("background:#dcfce7; border:1px solid #22c55e; border-radius:4px; color:#16a34a; font-size:14px;")
        else:
            self._last_vid_pix = None
            self.lbl_vid.setText("🎬")
            self.lbl_vid.setStyleSheet("background:#f3f4f6; border:1px solid #cbd5e1; border-radius:4px; color:#94a3b8;")

    def _start_thumb_poll(self):
        """Poll chờ thumbnail video được tạo xong ở thread nền."""
        if not hasattr(self, "_thumb_timer"):
            self._thumb_timer = QTimer(self)
            self._thumb_timer.setInterval(700)
            self._thumb_timer.timeout.connect(self._on_thumb_poll)
        if not self._thumb_timer.isActive():
            self._thumb_ticks = 0
            self._thumb_timer.start()

    def _on_thumb_poll(self):
        self._thumb_ticks = getattr(self, "_thumb_ticks", 0) + 1
        vp = self.vr_scene.video_clip_path
        if not vp or not Path(vp).exists():
            self._thumb_timer.stop()
            return
        thumb_path = Path(vp).parent / f".thumb_{Path(vp).stem}.jpg"
        if thumb_path.exists():
            self._thumb_timer.stop()
            self._refresh_thumbnails()
        elif self._thumb_ticks > 25:
            self._thumb_timer.stop()

    def cleanup(self):
        """Stop active timers before widget destruction to prevent C++ crash."""
        if hasattr(self, "_thumb_timer") and self._thumb_timer.isActive():
            self._thumb_timer.stop()


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

    def _show_vr_rerender_menu(self):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        menu.setStyleSheet("QMenu{background:#1e293b;color:white;border:1px solid #334155;padding:4px;}"
                          "QMenu::item{padding:6px 16px;border-radius:4px;}"
                          "QMenu::item:selected{background:#4338ca;}")
        
        a_img = menu.addAction("🎨 Chỉ render lại ảnh (tham chiếu SP)")
        a_img.triggered.connect(lambda: self._start_vr_rerender_flow("image"))
        
        a_vid = menu.addAction("🎬 Chỉ render lại video clip (I2V từ ảnh)")
        a_vid.triggered.connect(lambda: self._start_vr_rerender_flow("video"))
        
        a_both = menu.addAction("✨ Render lại cả Ảnh & Video")
        a_both.triggered.connect(lambda: self._start_vr_rerender_flow("both"))
        
        menu.exec(self.btn_rerender.mapToGlobal(self.btn_rerender.rect().bottomLeft()))

    def _start_vr_rerender_flow(self, mode: str):
        """Unified runner for VR scene re-render (image, video or both) using reference image & Grok/Veo."""
        card = self.card
        if not card or not hasattr(card, 'product'):
            return
        prod = card.product
        win = card.main_win if hasattr(card, 'main_win') else self.window()
        if not win or not hasattr(win, '_collect_settings'):
            return

        st = win._collect_settings()
        
        ref_img_path = None
        if mode in ["image", "both"]:
            if prod.images:
                for ri in prod.images:
                    if ri and Path(ri).exists():
                        ref_img_path = ri
                        break
            if not ref_img_path:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(self, "Không có ảnh SP", "Sản phẩm không có ảnh tham chiếu. Hãy thêm ảnh sản phẩm trước.")
                return

        prod_folder = get_product_folder(prod, st)
        vr_clips_dir = prod_folder / "vr_clips"
        vr_clips_dir.mkdir(parents=True, exist_ok=True)
        target_img = vr_clips_dir / f"scene_{self.vr_scene.id}.png"
        target_vid = vr_clips_dir / f"clip_{self.vr_scene.id}.mp4"

        self.btn_rerender.setEnabled(False)
        self.btn_rerender.setText("⏳")
        self.lbl_st.setText("⏳ Chạy...")
        self.lbl_st.setStyleSheet("font-size:8px;font-weight:bold;color:#f59e0b;")

        import threading
        
        def _do_render_flow():
            try:
                api_url = st.get("api_url", DEFAULT_PROXY_URL)
                api_key = st.get("api_key", "")
                acc = None
                if hasattr(win, 'acc_mgr'):
                    acc = win.acc_mgr.next_grok() if (mode in ["video", "both"]) else win.acc_mgr.next_codex_rr()
                if acc:
                    api_url = acc.proxy_url or api_url
                    api_key = acc.api_key or api_key

                # ── Step 1: Render Image ──
                if mode in ["image", "both"]:
                    if target_img.exists():
                        try: target_img.unlink()
                        except: pass
                    self.vr_scene.image_path = ""
                    
                    img_m = st.get("img_model", "gpt-image-2")
                    prompt = self.vr_scene.image_prompt or ""
                    
                    if hasattr(win, '_log'):
                        win._log(f"🔄 VR Image Render #{self.scene_idx+1}: {prompt[:60]}... (Ref: {Path(ref_img_path).name})")

                    is_veo = has_grok_deps and is_veo_model(img_m)
                    if is_veo:
                        g_accs = win.acc_mgr.get_all_google() if hasattr(win, 'acc_mgr') else []
                        acc = g_accs[0] if g_accs else None
                        bearer = acc.api_key if acc else st.get("veo_bearer", "")
                        proj_id = (acc.proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b") if acc else st.get("veo_project_id", "")
                        
                        import asyncio
                        import uuid
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        
                        mid = loop.run_until_complete(
                            upload_veo_image(
                                client=None, file_path=str(ref_img_path),
                                bearer_token=bearer, project_id=proj_id,
                                log=lambda m: win._log(f"    [Google Upload] {m}") if hasattr(win, '_log') else None,
                                captcha_bridge=getattr(win, "captcha_bridge", None)
                            )
                        )
                        if not mid:
                            raise Exception("Không thể upload ảnh tham chiếu lên Google Labs.")
                            
                        sc_aspect_raw = getattr(self.vr_scene, "aspect_ratio", "4:3") or "4:3"
                        if "1:1" in sc_aspect_raw or "square" in sc_aspect_raw.lower():
                            sc_aspect = "1:1"
                        elif "9:16" in sc_aspect_raw or "3:4" in sc_aspect_raw or "4:5" in sc_aspect_raw or "portrait" in sc_aspect_raw.lower():
                            sc_aspect = "9:16"
                        else:
                            sc_aspect = "16:9"

                        job = VideoJob(
                            id=f"img_{uuid.uuid4().hex[:6]}",
                            model=img_m, mode="image-generation",
                            prompt=prompt, aspect_ratio=sc_aspect,
                            output_name=f"scene_{self.vr_scene.id}",
                            local_path=str(vr_clips_dir),
                            use_proxy=True,
                            ref_images=[mid],
                            account_name=acc.name if acc else "",
                            google_profile_name=(
                                getattr(acc, "profile_name", "") or ""
                                if acc else ""
                            ),
                            google_auth_root=str(THREADS_AUTH_ROOT),
                            google_accounts_file=str(ACCOUNTS_FILE),
                        )
                        job.max_retries = 2
                        
                        success = loop.run_until_complete(
                            run_veo_flow(
                                job, bearer, proj_id,
                                getattr(win, "captcha_bridge", None),
                                log=lambda m: win._log(f"        [Google Flow] {m}") if hasattr(win, '_log') else None,
                                progress=None
                            )
                        )
                        loop.close()

                        if success and job.local_path and Path(job.local_path).exists():
                            p_src = Path(job.local_path)
                            if p_src.is_file():
                                p_src.replace(target_img)
                            elif (p_src / f"scene_{self.vr_scene.id}.png").exists():
                                (p_src / f"scene_{self.vr_scene.id}.png").replace(target_img)
                            elif (p_src / f"{job.id}.png").exists():
                                (p_src / f"{job.id}.png").replace(target_img)
                                
                            self.vr_scene.image_path = str(target_img)
                            if hasattr(win, '_log'):
                                win._log(f"  ✔ VR Image #{self.scene_idx+1} render OK!")
                        else:
                            raise Exception("Google Labs image generation failed.")
                    else:
                        with open(ref_img_path, "rb") as f_ref:
                            ref_bytes = f_ref.read()
                        ext = Path(ref_img_path).suffix.lower().lstrip(".") or "jpeg"
                        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                        files = {"image": (Path(ref_img_path).name, ref_bytes, mime)}
                        data_form = {"model": img_m, "prompt": prompt, "n": "1", "size": "1024x1280"}

                        ir = requests.post(
                            f"{api_url}/v1/images/edits",
                            headers={"Authorization": f"Bearer {api_key}"},
                            files=files, data=data_form, timeout=600
                        )
                        
                        if ir.status_code == 200:
                            it = ir.json().get("data", [{}])[0]
                            if "b64_json" in it:
                                import base64
                                target_img.write_bytes(base64.b64decode(it["b64_json"]))
                                self.vr_scene.image_path = str(target_img)
                            elif "url" in it:
                                img_resp = requests.get(it["url"], timeout=60)
                                if img_resp.status_code == 200:
                                    target_img.write_bytes(img_resp.content)
                                    self.vr_scene.image_path = str(target_img)
                            if hasattr(win, '_log'):
                                win._log(f"  ✔ VR Image #{self.scene_idx+1} render OK!")
                        else:
                            raise Exception(f"Failed to render image: status {ir.status_code}")

                # ── Step 2: Render Video Clip ──
                if mode in ["video", "both"]:
                    if not target_img.exists():
                        raise Exception("Không tìm thấy ảnh gốc để chạy video (I2V). Hãy render ảnh trước.")
                    
                    if target_vid.exists():
                        try: target_vid.unlink()
                        except: pass
                    self.vr_scene.video_clip_path = ""
                    self.vr_scene.status = "pending"

                    vr_video_model = st.get("vr_video_model", "Veo 3.1 - Lite [Lower Priority]")
                    vr_clip_dur = int(st.get("vr_clip_duration", 8))
                    vr_resolution = st.get("vr_resolution", "720p")
                    vr_aspect = st.get("vr_aspect_ratio", "9:16")
                    
                    full_vid_prompt = f"{self.vr_scene.video_prompt or ''}. {self.vr_scene.image_prompt or ''}"
                    
                    if hasattr(win, '_log'):
                        win._log(f"🎬 VR Video Render #{self.scene_idx+1}: {full_vid_prompt[:60]}...")

                    import asyncio
                    import uuid
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    vr_job = VideoJob(
                        id=uuid.uuid4().hex[:8], mode="image-to-video",
                        prompt=full_vid_prompt, model=vr_video_model,
                        duration=vr_clip_dur, resolution=vr_resolution,
                        aspect_ratio=vr_aspect, image_url=str(target_img),
                        local_path=str(target_vid), output_name=f"vr_clip_{self.scene_idx+1}",
                        max_retries=2,
                        google_auth_root=str(THREADS_AUTH_ROOT),
                        google_accounts_file=str(ACCOUNTS_FILE),
                    )

                    use_veo = is_veo_model(vr_video_model)
                    success = False
                    if use_veo:
                        veo_bearer = st.get("veo_bearer", "")
                        veo_project_id = st.get("veo_project_id", "") or GROK_DEFAULT_PROJECT_ID
                        # Resolve account_name
                        veo_acc = None
                        if hasattr(win, 'acc_mgr'):
                            for a in win.acc_mgr.get_all_google():
                                if a.api_key == veo_bearer:
                                    veo_acc = a
                                    break
                            if not veo_acc:
                                veo_accs = list(win.acc_mgr.get_all_google())
                                if veo_accs: veo_acc = veo_accs[0]
                        if veo_acc:
                            vr_job.account_name = veo_acc.name
                            vr_job.google_profile_name = (
                                getattr(veo_acc, "profile_name", "") or ""
                            )
                        
                        cb = getattr(win, "captcha_bridge", None)
                        success = loop.run_until_complete(
                            run_veo_flow(vr_job, veo_bearer, veo_project_id, cb, log=win._log if hasattr(win, '_log') else None, progress=None)
                        )
                    else:
                        grok_client = GrokClient(api_url, api_key)
                        rid = loop.run_until_complete(grok_client.submit(vr_job))
                        if rid:
                            for _ in range(90):
                                time.sleep(10)
                                poll_r = loop.run_until_complete(grok_client.poll(rid, use_proxy=False))
                                status_str = poll_r.get("status", "")
                                if status_str == "completed":
                                    vid_url = poll_r.get("video_url", "") or poll_r.get("url", "")
                                    if vid_url:
                                        loop.run_until_complete(grok_client.download(vid_url, target_vid, use_proxy=False))
                                    success = True
                                    break
                                elif status_str == "failed":
                                    break
                    
                    loop.close()

                    if success and target_vid.exists():
                        self.vr_scene.video_clip_path = str(target_vid)
                        self.vr_scene.status = "done"
                        if hasattr(win, '_log'):
                            win._log(f"  ✔ VR Video clip #{self.scene_idx+1} render OK!")
                    else:
                        raise Exception("Video generation failed or output not downloaded.")

            except Exception as e:
                self.vr_scene.status = "error"
                if hasattr(win, '_log'):
                    win._log(f"  ❌ VR Render #{self.scene_idx+1} Exception: {e}")
            finally:
                from PyQt6.QtCore import QMetaObject, Qt as QtCore_Qt
                QMetaObject.invokeMethod(self, "_on_vr_rerender_done", QtCore_Qt.ConnectionType.QueuedConnection)

        threading.Thread(target=_do_render_flow, daemon=True).start()

    @pyqtSlot()
    def _on_vr_rerender_done(self):
        self.btn_rerender.setEnabled(True)
        self.btn_rerender.setText("🔄")
        self._refresh_thumbnails()
        self._refresh_status()
        win = self.window()
        if hasattr(win, '_save_projects'): win._save_projects()


def get_product_folder(product: ProductItem, settings: dict = None) -> Path:
    # Prefer the active project root so product-level render/reprocess actions
    # follow the same project/product hierarchy as the main pipeline.
    project_dir_setting = (settings or {}).get("project_output_dir") or (settings or {}).get("_active_project_output_dir")
    if project_dir_setting:
        import re
        clean_title = re.sub(r'[\\/*?:"<>|]', "", (product.title or "san_pham")).strip()[:100]
        project_product_dir = Path(project_dir_setting) / f"{clean_title or 'san_pham'}_{product.id}"
        project_product_dir.mkdir(parents=True, exist_ok=True)
        return project_product_dir

    # Fallback for legacy projects: if an existing rendered path is known, use it.
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
        # ffprobe/ffmpeg chạy nền + cache — hàm này không bao giờ block UI nữa;
        # nếu dữ liệu chưa sẵn sàng thì poll refresh lại sau.
        pending = False

        # 1. Preview Original Video
        orig_path = self.product.external_video_path
        if orig_path and Path(orig_path).exists():
            self.lbl_name_orig.setText(f"📹 {Path(orig_path).name}")
            size_mb = Path(orig_path).stat().st_size / 1024 / 1024
            status, dur = get_video_duration_cached(orig_path)
            if status == "pending":
                pending = True
                self.lbl_info_orig.setText(f"📦 {size_mb:.1f} MB")
            elif dur is not None:
                mins, secs = divmod(int(dur), 60)
                self.lbl_info_orig.setText(f"⏱ {mins:02d}:{secs:02d}  •  {size_mb:.1f} MB")
            else:
                self.lbl_info_orig.setText(f"📦 {size_mb:.1f} MB")

            # Extract thumbnail (nền)
            thumb_orig = Path(orig_path).parent / f".thumb_orig_{self.product.id}.jpg"
            if not thumb_orig.exists():
                spawn_video_thumb_job(str(orig_path), str(thumb_orig))
                pending = True
            else:
                pix = load_scaled_pixmap(str(thumb_orig), self.lbl_thumb_orig.width() - 2, self.lbl_thumb_orig.height() - 2)
                if pix is not None:
                    self.lbl_thumb_orig.setPixmap(pix)
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
            size_mb = proc_path.stat().st_size / 1024 / 1024
            status, dur = get_video_duration_cached(proc_path)
            if status == "pending":
                pending = True
                self.lbl_info_proc.setText(f"📦 {size_mb:.1f} MB")
            elif dur is not None:
                mins, secs = divmod(int(dur), 60)
                self.lbl_info_proc.setText(f"⏱ {mins:02d}:{secs:02d}  •  {size_mb:.1f} MB")
            else:
                self.lbl_info_proc.setText(f"📦 {size_mb:.1f} MB")

            # Extract thumbnail (nền)
            thumb_proc = prod_dir / f".thumb_proc_{self.product.id}.jpg"
            if not thumb_proc.exists():
                spawn_video_thumb_job(str(proc_path), str(thumb_proc))
                pending = True
            else:
                pix = load_scaled_pixmap(str(thumb_proc), self.lbl_thumb_proc.width() - 2, self.lbl_thumb_proc.height() - 2)
                if pix is not None:
                    self.lbl_thumb_proc.setPixmap(pix)
                    self.lbl_thumb_proc.setStyleSheet("border:1.5px solid #c084fc; border-radius:4px;")
        else:
            self.lbl_name_proc.setText("Chưa xử lý video review")
            self.lbl_info_proc.setText("")
            self.lbl_thumb_proc.setPixmap(QPixmap())
            self.lbl_thumb_proc.setText("🎬")
            self.lbl_thumb_proc.setStyleSheet("background:#f3f4f6; border:1px dashed #cbd5e1; border-radius:4px; color:#94a3b8; font-size:18px;")

        # Poll lại khi còn dữ liệu đang tính ở nền
        if not hasattr(self, "_preview_timer"):
            self._preview_timer = QTimer(self)
            self._preview_timer.setInterval(700)
            self._preview_timer.timeout.connect(self._on_preview_poll)
            self._preview_ticks = 0
        if pending:
            if not self._preview_timer.isActive():
                self._preview_ticks = 0
                self._preview_timer.start()
        else:
            self._preview_timer.stop()

    def _on_preview_poll(self):
        self._preview_ticks = getattr(self, "_preview_ticks", 0) + 1
        if self._preview_ticks > 25:
            self._preview_timer.stop()
            return
        self._refresh_preview()

    def cleanup(self):
        """Stop active timers before widget destruction to prevent C++ crash."""
        if hasattr(self, "_preview_timer") and self._preview_timer.isActive():
            self._preview_timer.stop()

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

        bg_path = None
        if vr_settings.get("vr_overlay_bg", False):
            bg_folder = settings.get("bg_folder", "") if settings else ""
            if bg_folder and Path(bg_folder).exists():
                bg_files = []
                for ext in ["*.mp4", "*.mov", "*.mkv", "*.avi"]:
                    bg_files.extend(list(Path(bg_folder).glob(ext)))
                if bg_files:
                    bg_path = str(random.choice(bg_files))

        result = process_external_video(str(ext_path), str(output), vr_settings, bg_path, log_fn)
        if result and Path(result).exists():
            # Combine with Part 1 if it exists
            if self.product.final_video_path and Path(self.product.final_video_path).exists():
                combined = prod_dir / f"combined_{self.product.id}.mp4"
                final = combine_final_video(
                    self.product.final_video_path, str(result),
                    str(combined), "direct_cut", log_fn
                )
                if final:
                    apply_natural_metadata(final, settings, log_fn)
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
            pix = load_scaled_pixmap(path, 43, 43)
            if pix is not None:
                self.lbl.setPixmap(pix)
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
        self.scene_placeholder = None
        
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

        # Full pipeline re-render: LLM → Images → Voice → Video
        self.btn_render_full = QPushButton("⚡ Render toàn bộ")
        self.btn_render_full.setFixedHeight(26)
        self.btn_render_full.setToolTip("Render lại toàn bộ từ LLM đến Video")
        self.btn_render_full.setStyleSheet(
            "QPushButton { background: #ea580c; color: white; border: none; border-radius: 5px; "
            "font-size: 10px; font-weight: bold; padding: 0 8px; }"
            "QPushButton:hover { background: #c2410c; }"
        )
        self.btn_render_full.clicked.connect(self._on_render_full_clicked)
        sb_lay.addWidget(self.btn_render_full)
        
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
        # Empty products should not reserve a 500px card. Once scenes exist,
        # retain the previous minimum so existing layouts stay unchanged.
        min_height = 500 if N else 320
        self.setFixedHeight(max(min_height, h))

    def load_scenes(self):
        # Update existing rows in-place to prevent UI freeze from widget destruction/creation
        if len(self.scene_rows) == len(self.product.scenes) and len(self.scene_rows) > 0:
            for row, sc in zip(self.scene_rows, self.product.scenes):
                row.scene = sc
                row.update_row_data()
            return

        if not self.product.scenes and self.scene_placeholder is not None:
            try:
                if self.scene_placeholder.parent() is not None:
                    return
            except RuntimeError:
                self.scene_placeholder = None

        # Clear every layout item, including the old placeholder and stretch.
        # Previously only scene_rows were removed, so each 200ms refresh added
        # another placeholder and created the large blank/laggy UI shown in the
        # screenshot.
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self.scene_rows.clear()
        self.scene_placeholder = None
        
        # Add scene rows
        for idx, sc in enumerate(self.product.scenes):
            row = ThreadsSceneRow(idx, sc, self)
            self.list_layout.addWidget(row)
            self.scene_rows.append(row)
        
        # Empty scenes placeholder
        if not self.product.scenes:
            self.scene_placeholder = QLabel("💡 Chưa có phân cảnh — Bấm [🖼️ Render] hoặc [▶️ Bắt đầu] để AI tự động tạo kịch bản và ảnh")
            self.scene_placeholder.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 20px; font-style: italic;")
            self.scene_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.list_layout.addWidget(self.scene_placeholder)
        else:
            self.list_layout.addStretch()

    def _load_legacy_vr_scenes(self):
        """Check for legacy/existing vr_script.json on disk."""
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
                        video_prompt_override=s_data.get("video_prompt_override", ""),
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

    def load_vr_scenes(self):
        """Load/refresh Video Review panel nội dung."""
        if not self.product.video_review_scenes:
            # Quét đĩa tìm vr_script.json cũ — giới hạn 5s/lần để không lặp lại
            # việc duyệt thư mục mỗi khi pipeline bắn tín hiệu cập nhật.
            now = time.monotonic()
            if now - getattr(self, "_vr_scan_ts", 0.0) >= 5.0:
                self._vr_scan_ts = now
                self._load_legacy_vr_scenes()

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
                if it:
                    w = it.widget()
                    if w:
                        if hasattr(w, "cleanup"):
                            try: w.cleanup()
                            except Exception: pass
                        w.setParent(None)
                        w.deleteLater()
        
        if display_mode == "ai_generate" and len(vr_scenes) > 0:
            # Update in-place if same count
            if len(self.vr_scene_rows) == len(vr_scenes) and len(self.vr_scene_rows) > 0:
                for row, vrs in zip(self.vr_scene_rows, vr_scenes):
                    row.vr_scene = vrs
                    row.update_row_data()
            else:
                for r in self.vr_scene_rows:
                    if hasattr(r, "cleanup"):
                        try: r.cleanup()
                        except Exception: pass
                    r.setParent(None)
                    r.deleteLater()
                self.vr_scene_rows.clear()
                if self.vr_ext_preview:
                    if hasattr(self.vr_ext_preview, "cleanup"):
                        try: self.vr_ext_preview.cleanup()
                        except Exception: pass
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
                if hasattr(r, "cleanup"):
                    try: r.cleanup()
                    except Exception: pass
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
                if hasattr(r, "cleanup"):
                    try: r.cleanup()
                    except Exception: pass
                r.setParent(None)
                r.deleteLater()
            self.vr_scene_rows.clear()
            if self.vr_ext_preview:
                if hasattr(self.vr_ext_preview, "cleanup"):
                    try: self.vr_ext_preview.cleanup()
                    except Exception: pass
                self.vr_ext_preview.setParent(None)
                self.vr_ext_preview.deleteLater()
                self.vr_ext_preview = None
            _clear_content()
            
            lbl_empty = QLabel("💡 Chưa có Video Review — Bật tính năng trong Settings → Video Review Sản Phẩm")
            lbl_empty.setStyleSheet("color: #7c3aed; font-size: 10px; padding: 12px; font-style: italic;")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.vr_content_layout.addWidget(lbl_empty)
        
        # Update final video button state.  For AI Video Review, a combined
        # file is usable only when every declared scene has a real clip.
        has_final = bool(self.product.final_combined_path and Path(self.product.final_combined_path).exists())
        if self.product.video_review_mode == "ai_generate" and self.product.video_review_scenes:
            has_final = has_final and all(
                bool(sc.video_clip_path and Path(sc.video_clip_path).exists())
                for sc in self.product.video_review_scenes
            )
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
            pix = load_scaled_pixmap(img_path, 76, 76)
            if pix is None:
                return
            if getattr(self, "_last_thumb_pix", None) is pix:
                return
            self._last_thumb_pix = pix
            self.lbl_thumb.setPixmap(pix)
            self.lbl_thumb.setStyleSheet("background: white; border: 1px solid #cbd5e1; border-radius: 8px;")
        else:
            self._last_thumb_pix = None
            self.lbl_thumb.setText("📷")
            self.lbl_thumb.setStyleSheet("background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 24px; color: #94a3b8;")

    def refresh_ref_images(self):
        # Clear layout
        while self.ref_lay.count() > 1:
            it = self.ref_lay.takeAt(0)
            if it:
                w = it.widget()
                if w:
                    w.setParent(None)
                    w.deleteLater()
                
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
        if obj == self and event.type() == event.Type.KeyPress:
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

    def _on_render_full_clicked(self):
        win = self.window()
        if hasattr(win, "_run_single_full"):
            win._run_single_full(self.product)

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
            for item in cached_voices:
                vc, lb = item[0], item[1] if len(item) >= 2 else item[0]
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
            for cv in cached_voices:
                vc, lb = cv[0], cv[1] if len(cv) >= 2 else cv[0]
                lw_item = QListWidgetItem(lb)
                lw_item.setData(Qt.ItemDataRole.UserRole, vc)
                lw_item.setFlags(lw_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                lw_item.setCheckState(Qt.CheckState.Checked if vc in saved_sub else Qt.CheckState.Unchecked)
                self.list_re_sub_voices.addItem(lw_item)
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
        
        vr_final_exists = bool(product.final_combined_path and Path(product.final_combined_path).exists())
        vr_scene_manifest = (
            product.video_review_mode == "ai_generate"
            and bool(product.video_review_scenes)
        )
        vr_clips_complete = (
            all(
                bool(sc.video_clip_path and Path(sc.video_clip_path).exists())
                for sc in product.video_review_scenes
            )
            if vr_scene_manifest else True
        )
        has_vr = vr_final_exists and vr_clips_complete
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
    # Tín hiệu nhận kết quả check Chrome Spy từ thread nền (an toàn cross-thread)
    ts_chrome_status_sig = pyqtSignal(bool)
    # Mọi cập nhật widget từ worker Python phải đi qua main Qt thread.
    vox_ui_call_sig = pyqtSignal(object)
    # Cầu nối cho FullPipelineRunnerWorker. Các runner có thể phát signal từ
    # ThreadPoolExecutor, vì vậy không nối trực tiếp callback có thao tác Qt.
    runner_log_bridge_sig = pyqtSignal(str, str)
    runner_product_bridge_sig = pyqtSignal(str)
    runner_done_bridge_sig = pyqtSignal(str, bool, str)
    runner_cleanup_bridge_sig = pyqtSignal(str, object)
    # Log callbacks may originate from CaptchaBridge/HTTP worker threads.
    # Queue them back to the Qt GUI thread before touching QTextEdit.
    ui_log_bridge_sig = pyqtSignal(str)

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
        self.runners: Dict[str, 'FullPipelineRunnerWorker'] = {}
        self.project_logs: Dict[str, List[str]] = {}
        self._qt_main_thread_ident = threading.get_ident()
        self._ui_log_queue = queue.Queue()
        self.vox_ui_call_sig.connect(self._run_vox_ui_call)
        queued = Qt.ConnectionType.QueuedConnection
        self.runner_log_bridge_sig.connect(self._on_runner_log_bridge, queued)
        self.runner_product_bridge_sig.connect(self._on_runner_product_bridge, queued)
        self.runner_done_bridge_sig.connect(self._on_runner_done_bridge, queued)
        self.runner_cleanup_bridge_sig.connect(self._on_runner_cleanup_bridge, queued)
        self.ui_log_bridge_sig.connect(self._on_ui_log_bridge, queued)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_projects_now)

        # Gộp nhiều tín hiệu cập nhật liên tiếp từ pipeline thành 1 lần render
        # (worker emit product_updated rất dày — render từng phát một gây giật UI)
        self._pending_product_refresh_ids = set()
        self._pending_full_product_refresh = False
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._flush_product_updates)

        # Drain worker/CaptchaBridge events only from the Qt main thread.
        self._runner_event_timer = QTimer(self)
        self._runner_event_timer.setInterval(75)
        self._runner_event_timer.timeout.connect(self._drain_async_events)
        self._runner_event_timer.start()

        self._load_projects()
        self.load_veo_config()
        self._apply_qss()
        self._init_ui()
        self.start_captcha_bridge_if_needed()
        self.start_proxy()

        # Match the main Grok tool: validate every saved Google account as
        # soon as the UI is ready, then keep tokens warm in the background.
        # The worker opens Chrome only when userinfo says the bearer expired.
        QTimer.singleShot(2500, lambda: self.auto_refresh_google_tokens("startup"))
        self._google_token_health_timer = QTimer(self)
        self._google_token_health_timer.setInterval(10 * 60 * 1000)
        self._google_token_health_timer.timeout.connect(
            lambda: self.auto_refresh_google_tokens("periodic")
        )
        self._google_token_health_timer.start()

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
            try:
                self.proxy_process=subprocess.Popen([str(CLIPROXY_EXE)],cwd=str(CLIPROXY_DIR),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                # Chờ proxy mở port nhưng thoát sớm ngay khi sẵn sàng
                # (trước đây cứng time.sleep(2) — treo UI đủ 2s mỗi lần)
                deadline = time.monotonic() + 2.0
                while time.monotonic() < deadline:
                    try:
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                            s.settimeout(0.2); s.connect(("127.0.0.1", 8317)); break
                    except OSError:
                        time.sleep(0.1)
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

        # Công cụ
        self._sb_section(sb_lay, "CÔNG CỤ")
        self._sb_add_btn(sb_lay, "🎬  Vox Director", "vox")

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
        self._build_vox_panel()        # 5

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
            if it:
                w = it.widget()
                if w:
                    w.deleteLater()
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

        idx_map = {"dashboard": 0, "studio": 1, "tiktokshop": 2, "accs": 3, "sett": 4, "vox": 5}
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
        # Tắt repaint trong lúc rebuild grid để tránh nháy khi chuyển trang
        self.dash_cards_w.setUpdatesEnabled(False)
        try:
            while self.dash_grid.count():
                it = self.dash_grid.takeAt(0)
                if it:
                    w = it.widget()
                    if w:
                        w.deleteLater()
            if not self.projects:
                el = QLabel("Chưa có dự án nào.\nBấm '+ Tạo Dự Án Mới' để bắt đầu."); el.setAlignment(Qt.AlignmentFlag.AlignCenter)
                el.setStyleSheet("font-size:14px;color:#94a3b8;padding:60px;"); self.dash_grid.addWidget(el,0,0,1,3); return

            cols = 3
            for i,p in enumerate(self.projects):
                self.dash_grid.addWidget(self._build_proj_card(p), i//cols, i%cols)
            self.dash_grid.setRowStretch(self.dash_grid.rowCount(), 1)
            self._refresh_sidebar_projects()
        finally:
            self.dash_cards_w.setUpdatesEnabled(True)

    def _build_proj_card(self, proj):
        card = QFrame(); card.setObjectName("card")
        cl = QVBoxLayout(card); cl.setContentsMargins(16,14,16,14); cl.setSpacing(10)

        # Title + Badge + Icons
        tr = QHBoxLayout()
        tl = QLabel(proj.name); tl.setStyleSheet("font-size:15px;font-weight:bold;color:#1e293b;"); tr.addWidget(tl); tr.addStretch()
        badge = QLabel("Role Skill"); badge.setStyleSheet("background:#f97316;color:white;font-size:10px;font-weight:bold;padding:3px 8px;border-radius:4px;"); tr.addWidget(badge)
        bd = QPushButton("📋"); bd.setFixedSize(24,24); bd.setStyleSheet("border:none;background:transparent;"); tr.addWidget(bd)
        bd.setToolTip("Nhân bản dự án")
        bd.clicked.connect(lambda _,p=proj: self._clone_project(p))
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
        left_sc = QScrollArea(); left_sc.setWidgetResizable(True); left_sc.setMinimumWidth(320); left_sc.setMaximumWidth(550)
        left_sc.setStyleSheet("QScrollArea{background:#f8fafc;border:none;}")
        left_w = QWidget(); ll = QVBoxLayout(left_w); ll.setContentsMargins(12,12,12,12); ll.setSpacing(8)
        ll.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Card: Sản phẩm
        c1,c1l = self._mk_card("📦 SẢN PHẨM & CHẠY TỰ ĐỘNG")
        r1 = QHBoxLayout()
        ba = QPushButton("➕ Thêm ảnh SP"); ba.setObjectName("accent_btn"); ba.clicked.connect(self._add_product); r1.addWidget(ba)
        bt = QPushButton("🛍️ TiktokShop"); bt.setObjectName("normal_btn"); bt.clicked.connect(self._open_tiktok_dialog_in_studio); r1.addWidget(bt)
        c1l.addLayout(r1)
        r2 = QHBoxLayout()
        self.btn_start = QPushButton("▶️ Bắt đầu"); self.btn_start.setStyleSheet("background:#16a34a;color:white;font-weight:bold;padding:8px;border-radius:6px;"); self.btn_start.clicked.connect(self._start_pipeline); r2.addWidget(self.btn_start)
        self.btn_pause = QPushButton("⏸️ Tạm dừng"); self.btn_pause.setStyleSheet("background:#0284c7;color:white;font-weight:bold;padding:8px;border-radius:6px;"); self.btn_pause.clicked.connect(self._pause_pipeline); r2.addWidget(self.btn_pause)
        self.btn_render_all = QPushButton("⚡ Render toàn bộ")
        self.btn_render_all.setToolTip("Xóa kết quả đã tạo và render lại toàn bộ sản phẩm từ LLM đến Video")
        self.btn_render_all.setStyleSheet("background:#ea580c;color:white;font-weight:bold;padding:8px;border-radius:6px;")
        self.btn_render_all.clicked.connect(self._run_all_full)
        r2.addWidget(self.btn_render_all)
        c1l.addLayout(r2)
        r3 = QHBoxLayout()
        bds = QPushButton("🗑️ Xóa SP cuối"); bds.setStyleSheet("background:#ef4444;color:white;font-weight:bold;padding:5px;border-radius:6px;"); bds.clicked.connect(self._del_last_product); r3.addWidget(bds)
        bda = QPushButton("🗑️ Xóa tất cả"); bda.setStyleSheet("background:#991b1b;color:white;font-weight:bold;padding:5px;border-radius:6px;"); bda.clicked.connect(self._del_all_products); r3.addWidget(bda)
        c1l.addLayout(r3)
        r4 = QHBoxLayout()
        btn_reimg = QPushButton("🔄 Render lại Ảnh (tất cả SP)")
        btn_reimg.setStyleSheet("background:#7c3aed;color:white;font-weight:bold;padding:6px;border-radius:6px;")
        btn_reimg.setToolTip("Xóa ảnh cũ, sinh lại ảnh MỚI dùng ảnh tham chiếu sản phẩm")
        btn_reimg.clicked.connect(self._rerender_all_images)
        r4.addWidget(btn_reimg)
        btn_revid = QPushButton("🎬 Chỉ ghép Video (tất cả)")
        btn_revid.setStyleSheet("background:#0369a1;color:white;font-weight:bold;padding:6px;border-radius:6px;")
        btn_revid.clicked.connect(self._run_all_video_only)
        r4.addWidget(btn_revid)
        c1l.addLayout(r4)
        ll.addWidget(c1)

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
        self._load_archetypes_into_combo(self.cb_ctx, is_global=True)
        saved_style = self.settings.get("context", "Chọn theo Phong cách dưới từng sản phẩm")
        self.cb_ctx.setCurrentText(saved_style)
        c2l.addWidget(self.cb_ctx)
        
        ll.addWidget(c2)
        self._reload_role_combo()

        # ══════════════════════════════════════════════════════════
        # Card: Pipeline Thread Controls (CARD RIÊNG — LUỒNG SONG SONG)
        # ══════════════════════════════════════════════════════════
        c_pipe, c_pipe_l = self._mk_card("🧵 CẤU HÌNH LUỒNG SONG SONG (PIPELINE)")
        c_pipe.setStyleSheet(c_pipe.styleSheet() + "QGroupBox { border: 2px solid #7c3aed; }")

        g_lay_threads = QHBoxLayout()
        # LLM Threads
        v_llm = QVBoxLayout()
        v_llm.addWidget(QLabel("💬 Luồng LLM:"))
        self.sp_llm_threads = QSpinBox()
        self.sp_llm_threads.setRange(1, 5)
        self.sp_llm_threads.setValue(self.settings.get("llm_threads", 3))
        self.sp_llm_threads.setToolTip("Số sản phẩm LLM phân tích đồng thời")
        v_llm.addWidget(self.sp_llm_threads)
        g_lay_threads.addLayout(v_llm)

        # Image Threads
        v_img = QVBoxLayout()
        v_img.addWidget(QLabel("🖼️ Luồng Ảnh:"))
        self.sp_img_threads = QSpinBox()
        self.sp_img_threads.setRange(1, 12)
        self.sp_img_threads.setValue(self.settings.get("img_threads", 5))
        self.sp_img_threads.setToolTip("Số ảnh tạo song song (round-robin accounts)")
        v_img.addWidget(self.sp_img_threads)
        g_lay_threads.addLayout(v_img)

        # Video Threads
        v_vid = QVBoxLayout()
        v_vid.addWidget(QLabel("🎬 Luồng Video:"))
        self.sp_vid_threads = QSpinBox()
        self.sp_vid_threads.setRange(1, 8)
        self.sp_vid_threads.setValue(self.settings.get("vid_threads", 3))
        self.sp_vid_threads.setToolTip("Số video clip tạo song song (Video Review AI)")
        v_vid.addWidget(self.sp_vid_threads)
        g_lay_threads.addLayout(v_vid)

        # Pipeline Delay
        v_delay = QVBoxLayout()
        v_delay.addWidget(QLabel("⏳ Delay (s):"))
        self.sp_pipeline_delay = QSpinBox()
        self.sp_pipeline_delay.setRange(0, 30)
        self.sp_pipeline_delay.setValue(self.settings.get("pipeline_delay", 5))
        self.sp_pipeline_delay.setToolTip("Thời gian nghỉ giữa các request round-robin")
        v_delay.addWidget(self.sp_pipeline_delay)
        g_lay_threads.addLayout(v_delay)
        c_pipe_l.addLayout(g_lay_threads)

        # Veo3 Burst Controls (cho Google Labs)
        c_pipe_l.addWidget(QLabel("── Google Labs / Veo3 ──", styleSheet="font-weight:bold;color:#9ca3af;margin-top:4px;"))
        g_lay_burst = QHBoxLayout()
        v_c1 = QVBoxLayout()
        v_c1.addWidget(QLabel("⚡ Song song (Google Labs):"))
        self.sp_concurrency = QSpinBox()
        self.sp_concurrency.setRange(1, 12)
        self.sp_concurrency.setValue(self.settings.get("concurrency", 8))
        v_c1.addWidget(self.sp_concurrency)
        g_lay_burst.addLayout(v_c1)

        v_c2 = QVBoxLayout()
        v_c2.addWidget(QLabel("⏳ Nghỉ sau đợt (s):"))
        self.sp_burst_delay = QSpinBox()
        self.sp_burst_delay.setRange(1, 60)
        self.sp_burst_delay.setValue(self.settings.get("burst_delay", 10))
        v_c2.addWidget(self.sp_burst_delay)
        g_lay_burst.addLayout(v_c2)
        c_pipe_l.addLayout(g_lay_burst)

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
        c_pipe_l.addLayout(g_lay2)
        ll.addWidget(c_pipe)

        # Card: Voice
        c3,c3l = self._mk_card("🎙️ CẤU HÌNH VOICE & PHÂN CẢNH")
        c3l.addWidget(QLabel("Engine Voiceover:"))
        self.cb_voice = QComboBox()
        for code, label in TTS_ENGINES:
            self.cb_voice.addItem(label, code)
        saved_engine = self.settings.get("voice_engine", "vbee")
        eidx = self.cb_voice.findData(saved_engine)
        self.cb_voice.setCurrentIndex(eidx if eidx >= 0 else 1)
        self.cb_voice.currentIndexChanged.connect(self._on_voice_changed)
        c3l.addWidget(self.cb_voice)

        # ── Khối thông tin đăng nhập (mỗi provider 1 widget, show/hide) ──
        _cred_input_css = ("QLineEdit{background:#ffffff;border:1px solid #cbd5e1;border-radius:5px;"
                           "padding:5px;font-size:11px;color:#0f172a;}")

        # Vbee: Token + App-ID
        self.w_cred_vbee = QWidget(); _wv = QVBoxLayout(self.w_cred_vbee); _wv.setContentsMargins(0,0,0,0); _wv.setSpacing(3)
        _wv.addWidget(QLabel("Vbee Token:"))
        self.txt_vbee_tok = QLineEdit(self.settings.get("vbee_token","")); self.txt_vbee_tok.setStyleSheet(_cred_input_css); _wv.addWidget(self.txt_vbee_tok)
        _wv.addWidget(QLabel("Vbee App-ID:"))
        self.txt_vbee_aid = QLineEdit(self.settings.get("vbee_appid","")); self.txt_vbee_aid.setStyleSheet(_cred_input_css); _wv.addWidget(self.txt_vbee_aid)
        c3l.addWidget(self.w_cred_vbee)

        # Resona: API Key
        self.w_cred_resona = QWidget(); _wr = QVBoxLayout(self.w_cred_resona); _wr.setContentsMargins(0,0,0,0); _wr.setSpacing(3)
        _wr.addWidget(QLabel("Resona API Key (Bearer):"))
        self.txt_resona_key = QLineEdit(self.settings.get("resona_key","")); self.txt_resona_key.setStyleSheet(_cred_input_css)
        self.txt_resona_key.setPlaceholderText("Token từ resona.live → API Key")
        _wr.addWidget(self.txt_resona_key)
        c3l.addWidget(self.w_cred_resona)

        # ElevenLabs (11labs.id.vn): API Key
        self.w_cred_el = QWidget(); _we = QVBoxLayout(self.w_cred_el); _we.setContentsMargins(0,0,0,0); _we.setSpacing(3)
        _we.addWidget(QLabel("11labs.id.vn API Key (PL_...):"))
        self.txt_el_idvn_key = QLineEdit(self.settings.get("el_idvn_key","")); self.txt_el_idvn_key.setStyleSheet(_cred_input_css)
        self.txt_el_idvn_key.setPlaceholderText("x-api-key dạng PL_xxxxx")
        _we.addWidget(self.txt_el_idvn_key)
        c3l.addWidget(self.w_cred_el)

        # RevidAPI: API Key
        self.w_cred_revid = QWidget(); _wd = QVBoxLayout(self.w_cred_revid); _wd.setContentsMargins(0,0,0,0); _wd.setSpacing(3)
        _wd.addWidget(QLabel("RevidAPI Key (x-api-key):"))
        self.txt_revidapi_key = QLineEdit(self.settings.get("revidapi_key","")); self.txt_revidapi_key.setStyleSheet(_cred_input_css)
        self.txt_revidapi_key.setPlaceholderText("API key từ revidapi.com")
        _wd.addWidget(self.txt_revidapi_key)
        c3l.addWidget(self.w_cred_revid)

        # ── Nút tải danh sách voice (provider-aware) ──
        self.btn_fetch_voices = QPushButton("🔄 Tải danh sách Voice")
        self.btn_fetch_voices.setObjectName("normal_btn")
        self.btn_fetch_voices.clicked.connect(self._fetch_voices)
        c3l.addWidget(self.btn_fetch_voices)

        # ── Khối chọn voice thông minh (ẩn khi gTTS) ──
        self.w_voice_select = QWidget()
        vl = QVBoxLayout(self.w_voice_select); vl.setContentsMargins(0,0,0,0); vl.setSpacing(4)

        # Hàng lọc: Giới tính + Loại
        filt_row = QHBoxLayout(); filt_row.setSpacing(6)
        _fc1 = QVBoxLayout(); _fc1.setSpacing(2)
        _fc1.addWidget(QLabel("👥 Giới tính:"))
        self.cb_voice_gender = QComboBox()
        self.cb_voice_gender.addItem("Tất cả", "")
        self.cb_voice_gender.addItem("👩 Nữ", "female")
        self.cb_voice_gender.addItem("👨 Nam", "male")
        self.cb_voice_gender.currentIndexChanged.connect(self._apply_voice_filter)
        _fc1.addWidget(self.cb_voice_gender); filt_row.addLayout(_fc1)
        _fc2 = QVBoxLayout(); _fc2.setSpacing(2)
        _fc2.addWidget(QLabel("🎨 Loại voice:"))
        self.cb_voice_type = QComboBox()
        self.cb_voice_type.currentIndexChanged.connect(self._apply_voice_filter)
        _fc2.addWidget(self.cb_voice_type); filt_row.addLayout(_fc2)
        vl.addLayout(filt_row)

        # Ô tìm kiếm
        self.txt_voice_search = QLineEdit()
        self.txt_voice_search.setPlaceholderText("🔎 Tìm theo tên voice...")
        self.txt_voice_search.setStyleSheet(_cred_input_css)
        self.txt_voice_search.textChanged.connect(self._apply_voice_filter)
        vl.addWidget(self.txt_voice_search)

        # Voice chính
        vl.addWidget(QLabel("🎤 Voice Chính (Dẫn chuyện):"))
        self.cb_vbee_v = QComboBox()
        self.cb_vbee_v.setEditable(True)
        vl.addWidget(self.cb_vbee_v)

        # Voice phụ (random đọc CMT)
        self.lbl_sub_voices = QLabel("👥 Voice Phụ (Đọc CMT, Random):")
        vl.addWidget(self.lbl_sub_voices)
        self.list_sub_voices = QListWidget()
        self.list_sub_voices.setMinimumHeight(150)
        self.list_sub_voices.setMaximumHeight(240)
        self.list_sub_voices.setStyleSheet("QListWidget{background:#1e293b;color:#f8fafc;border:1px solid #475569;border-radius:4px;font-size:12px;} QListWidget::item{padding:5px 4px;border-bottom:1px solid #334155;} QListWidget::item:hover{background:#334155;} QListWidget::indicator{width:16px;height:16px;}")
        self.list_sub_voices.itemChanged.connect(self._on_sub_voice_toggled)
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
        c3l.addWidget(self.w_voice_select)

        # Speed (dùng chung mọi engine)
        c3l.addWidget(QLabel("⏩ Speed:"))
        self.sp_vbee_spd = QDoubleSpinBox()
        self.sp_vbee_spd.setRange(0.5,2.0)
        self.sp_vbee_spd.setSingleStep(0.1)
        self.sp_vbee_spd.setValue(self.settings.get("vbee_speed",1.0))
        c3l.addWidget(self.sp_vbee_spd)

        # Trạng thái set này lưu các voice phụ đã chọn (theo ID) — giữ qua lúc lọc
        self._sub_selected = set(self.settings.get("vbee_sub_voices", []))
        self._voice_all = []
        # Khởi tạo hiển thị đúng theo engine đang chọn
        self._on_voice_changed()

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
        self.chk_meta = QCheckBox("🛡️ Fake Metadata (giả lập quay/dựng tự nhiên)"); self.chk_meta.setChecked(self.settings.get("fake_metadata",True))
        self.chk_meta.setToolTip("Ghi metadata giả lên video output cuối: giả lập quay bằng iPhone/Samsung/Xiaomi\nvà dựng bằng CapCut/Premiere... để trông tự nhiên như quay tay.")
        c4l.addWidget(self.chk_meta)
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

        c5l.addWidget(self.w_vr_ext)

        # ── Common Adjustment Settings (Crop, Speed, Anti-Reup) ──
        self.w_vr_adjust = QWidget()
        vr_adj_l = QVBoxLayout(self.w_vr_adjust); vr_adj_l.setContentsMargins(0,10,0,0); vr_adj_l.setSpacing(6)
        
        lbl_adjust = QLabel("⚙️ Các tham số điều chỉnh Video (Crop, Speed, Anti-Reup...):")
        lbl_adjust.setStyleSheet("font-weight: bold; color: #475569;")
        vr_adj_l.addWidget(lbl_adjust)

        row_crop = QHBoxLayout()
        row_crop.addWidget(QLabel("Crop Video:"))
        self.cb_vr_crop = QComboBox()
        self.cb_vr_crop.addItems(["Vuông giữa (1:1)", "9:16 giữa", "Giữ nguyên"])
        cur_crop = self.settings.get("vr_crop_mode", "square_center")
        crop_map = {"square_center": 0, "9_16_center": 1, "keep_original": 2}
        self.cb_vr_crop.setCurrentIndex(crop_map.get(cur_crop, 0))
        row_crop.addWidget(self.cb_vr_crop)
        vr_adj_l.addLayout(row_crop)

        row_speed_max = QHBoxLayout()
        self.chk_vr_auto_speed = QCheckBox("Auto Sync (Khớp Voice)")
        self.chk_vr_auto_speed.setChecked(self.settings.get("vr_auto_speed", True))
        row_speed_max.addWidget(self.chk_vr_auto_speed)

        row_speed_max.addWidget(QLabel("Tốc độ(Manual):"))
        self.sp_vr_speed = QDoubleSpinBox(); self.sp_vr_speed.setRange(0.5, 2.0)
        self.sp_vr_speed.setSingleStep(0.1); self.sp_vr_speed.setValue(self.settings.get("vr_speed", 1.0))
        self.sp_vr_speed.setSuffix("x")
        row_speed_max.addWidget(self.sp_vr_speed)
        
        row_speed_max.addWidget(QLabel("  Max(s):"))
        self.sp_vr_max_dur = QSpinBox(); self.sp_vr_max_dur.setRange(5, 120)
        self.sp_vr_max_dur.setValue(self.settings.get("vr_max_duration", 30))
        row_speed_max.addWidget(self.sp_vr_max_dur)
        row_speed_max.addStretch()
        vr_adj_l.addLayout(row_speed_max)

        # Zoom settings row
        row_zoom = QHBoxLayout()
        self.chk_vr_zoom_custom = QCheckBox("🔍 Bật Zoom (%)")
        self.chk_vr_zoom_custom.setChecked(self.settings.get("vr_zoom_custom", False))
        row_zoom.addWidget(self.chk_vr_zoom_custom)
        self.sp_vr_zoom_factor = QDoubleSpinBox()
        self.sp_vr_zoom_factor.setRange(50.0, 200.0)
        self.sp_vr_zoom_factor.setSingleStep(5.0)
        self.sp_vr_zoom_factor.setValue(self.settings.get("vr_zoom_factor", 100.0))
        self.sp_vr_zoom_factor.setSuffix("%")
        row_zoom.addWidget(self.sp_vr_zoom_factor)
        row_zoom.addStretch()
        vr_adj_l.addLayout(row_zoom)

        # Background Overlay settings row
        row_overlay = QHBoxLayout()
        self.chk_vr_overlay = QCheckBox("🏞️ Đặt trên nền Background")
        self.chk_vr_overlay.setChecked(self.settings.get("vr_overlay_bg", False))
        row_overlay.addWidget(self.chk_vr_overlay)
        self.sp_vr_overlay_scale = QSpinBox()
        self.sp_vr_overlay_scale.setRange(50, 100)
        self.sp_vr_overlay_scale.setValue(self.settings.get("vr_overlay_scale", 85))
        self.sp_vr_overlay_scale.setSuffix("%")
        row_overlay.addWidget(self.sp_vr_overlay_scale)
        row_overlay.addStretch()
        vr_adj_l.addLayout(row_overlay)

        # Audio settings row
        row_audio = QHBoxLayout()
        self.chk_vr_keep_audio = QCheckBox("🔊 Giữ âm thanh gốc video review")
        self.chk_vr_keep_audio.setChecked(self.settings.get("vr_keep_audio", True))
        row_audio.addWidget(self.chk_vr_keep_audio)
        row_audio.addStretch()
        vr_adj_l.addLayout(row_audio)

        # Anti-reup checkboxes (Flip, Noise) - Vertical
        self.chk_vr_flip = QCheckBox("↔️ Lật ngang (anti-reup)")
        self.chk_vr_flip.setChecked(self.settings.get("vr_flip_h", False))
        vr_adj_l.addWidget(self.chk_vr_flip)

        self.chk_vr_noise = QCheckBox("✨ Noise nhẹ (anti-detect)")
        self.chk_vr_noise.setChecked(self.settings.get("vr_noise", True))
        vr_adj_l.addWidget(self.chk_vr_noise)

        self.chk_vr_rcut = QCheckBox("✂️ Cắt random khúc (0.2-0.5s)")

        self.chk_vr_rcut.setChecked(self.settings.get("vr_random_cut", True))
        row_rcut = QHBoxLayout()
        row_rcut.addWidget(self.chk_vr_rcut)
        row_rcut.addWidget(QLabel("Mỗi:"))
        self.sp_vr_rcut_min = QSpinBox(); self.sp_vr_rcut_min.setRange(2, 10)
        self.sp_vr_rcut_min.setValue(self.settings.get("vr_rcut_interval_min", 3))
        row_rcut.addWidget(self.sp_vr_rcut_min)
        row_rcut.addWidget(QLabel("→"))
        self.sp_vr_rcut_max = QSpinBox(); self.sp_vr_rcut_max.setRange(3, 15)
        self.sp_vr_rcut_max.setValue(self.settings.get("vr_rcut_interval_max", 5))
        row_rcut.addWidget(self.sp_vr_rcut_max)
        row_rcut.addWidget(QLabel("(s)"))
        row_rcut.addStretch()
        vr_adj_l.addLayout(row_rcut)

        c5l.addWidget(self.w_vr_adjust)

        # Initial visibility
        self._toggle_vr_settings()

        ll.addWidget(c5)

        left_sc.setWidget(left_w); content.addWidget(left_sc)

        # RIGHT: Products + Logs
        right_w = QWidget(); rl = QVBoxLayout(right_w); rl.setContentsMargins(12,12,12,12); rl.setSpacing(8)
        self.prod_scroll = QScrollArea(); self.prod_scroll.setWidgetResizable(True)
        self.prod_scroll.setStyleSheet("QScrollArea{background:#ffffff;border:none;}")
        self.prod_content = QWidget(); self.prod_lay = QVBoxLayout(self.prod_content); self.prod_lay.setSpacing(12)
        self.prod_scroll.setWidget(self.prod_content); rl.addWidget(self.prod_scroll)

        rl.addWidget(QLabel("🖥️ Logs chi tiết:"))
        self.txt_log = QTextEdit(); self.txt_log.setFixedHeight(120); self.txt_log.setReadOnly(True)
        self.txt_log.setUndoRedoEnabled(False)
        self.txt_log.document().setMaximumBlockCount(1500)  # giữ log gọn, append luôn nhanh
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
        
        self.ts_btn_import_cookies = QPushButton("🔑 NHẬP COOKIES")
        self.ts_btn_import_cookies.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.ts_btn_import_cookies.setStyleSheet("background-color: #7c3aed; color: white;")
        self.ts_btn_import_cookies.clicked.connect(self.ts_action_import_cookies)
        header_layout.addWidget(self.ts_btn_import_cookies)
        
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
        self.ts_txt_logs.setUndoRedoEnabled(False)
        self.ts_txt_logs.document().setMaximumBlockCount(1500)
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

        self.ts_chrome_status_sig.connect(self._ts_apply_chrome_status)
        self.ts_status_timer = QTimer(self)
        self.ts_status_timer.timeout.connect(self.ts_check_chrome_status)
        self.ts_status_timer.start(5000)
        
        self.ts_worker = None
        self.ts_refresh_folder_list()
        self.ts_refresh_products_table()
        self.stacked.addWidget(panel)

    def ts_check_chrome_status(self):
        # Check HTTP CDP chạy ở thread nền (timeout 2s từng chặn UI mỗi 5 giây)
        if getattr(self, "_ts_status_busy", False):
            return
        self._ts_status_busy = True
        def _job():
            try:
                ok = is_chrome_cdp_active(CDP_PORT)
                self.ts_chrome_status_sig.emit(ok)
            except Exception:
                pass
            finally:
                self._ts_status_busy = False
        threading.Thread(target=_job, daemon=True).start()

    def _ts_apply_chrome_status(self, ok: bool):
        try:
            if ok:
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
            "https://www.tiktok.com"
        ]
        self.ts_write_log("🌐 Đang khởi động Google Chrome Spy với debug port 9222...")
        try:
            subprocess.Popen(cmd, creationflags=_CNOW)
            # Check trạng thái sau 2s mà không chặn UI (thay cho time.sleep(2))
            QTimer.singleShot(2000, self.ts_check_chrome_status)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không thể mở Chrome: {e}")

    def ts_action_import_cookies(self):
        cookie_str, ok = QInputDialog.getMultiLineText(
            self, "Nhập Cookies TikTok",
            "Dán Cookie String (dạng name1=value1; name2=value2) hoặc JSON Cookies vào đây:"
        )
        if not ok or not cookie_str.strip():
            return
            
        cookies_list = []
        try:
            parsed = json.loads(cookie_str.strip())
            if isinstance(parsed, list):
                for c in parsed:
                    if isinstance(c, dict) and "name" in c and "value" in c:
                        if "domain" not in c or not c["domain"]:
                            c["domain"] = ".tiktok.com"
                        cookies_list.append(c)
            else:
                raise ValueError("JSON must be a list of cookies")
        except Exception:
            pairs = [p.strip() for p in cookie_str.split(';') if p.strip()]
            for p in pairs:
                if '=' in p:
                    k, v = p.split('=', 1)
                    cookies_list.append({
                        "name": k.strip(),
                        "value": v.strip(),
                        "domain": ".tiktok.com",
                        "path": "/"
                    })
                    
        if not cookies_list:
            QMessageBox.warning(self, "Lỗi", "Không tìm thấy cookies hợp lệ!")
            return
            
        if not is_chrome_cdp_active(CDP_PORT):
            QMessageBox.warning(self, "Lỗi", "Vui lòng bấm 'MỞ CHROME SPY' trước để mở trình duyệt, sau đó mới nhập cookies!")
            return
            
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                context.add_cookies(cookies_list)
                page = context.new_page()
                page.goto("https://www.tiktok.com", timeout=15000)
                page.close()
                browser.close()
            self.ts_write_log(f"✅ Đã nhập và áp dụng thành công {len(cookies_list)} cookies vào Chrome Spy!")
            QMessageBox.information(self, "Thành công", f"Đã nhập thành công {len(cookies_list)} cookies vào Chrome Spy!")
        except Exception as e:
            self.ts_write_log(f"❌ Lỗi áp dụng cookies: {e}")
            QMessageBox.critical(self, "Lỗi", f"Không thể áp dụng cookies vào Chrome Spy:\n{e}")

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
        
        if not hasattr(self, '_ts_voice_workers'):
            self._ts_voice_workers = []
        worker = VoiceDownloadWorker(url, str(output_dir))
        self._ts_voice_workers.append(worker)

        def on_done(success, path, _worker=worker):
            if _worker in self._ts_voice_workers:
                self._ts_voice_workers.remove(_worker)
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
        
        if not hasattr(self, '_ts_voice_workers'):
            self._ts_voice_workers = []
        
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
            self._ts_voice_workers.append(worker)

            def on_done(success, path, _worker=worker):
                if _worker in self._ts_voice_workers:
                    self._ts_voice_workers.remove(_worker)
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
        
        self.accs_oauth_status = QLabel("Google Flow: đăng nhập bằng Chrome thường trong profile riêng. Vào được Flow rồi đóng cửa sổ đó để tool kiểm tra phiên.")
        self.accs_oauth_status.setWordWrap(True)
        self.accs_oauth_status.setStyleSheet("color: #e2d6ff; font-size: 11px; background: transparent;")
        
        def run_oauth_tab():
            items = ["xAI Grok (SuperGrok)", "OpenAI Codex (ChatGPT Plus)", "Google Flow / Veo 3"]
            item, ok = QInputDialog.getItem(self, "Đăng Nhập Tài Khoản", "Chọn loại tài khoản đăng nhập:", items, 0, False)
            if not ok or not item: return
                
            login_type = item
            if "Google Flow" in login_type:
                from google_flow_auth import discover_profiles
                prof_name, ok2 = QInputDialog.getItem(
                    self, "Đăng Nhập Google",
                    "Chọn đúng profile đã đăng nhập hoặc nhập tên profile mới:",
                    discover_profiles(THREADS_AUTH_ROOT), 0, True,
                )
                if not ok2 or not prof_name.strip(): return
                prof_name = prof_name.strip()
                try:
                    self.acc_mgr.ensure_google_profile(prof_name)
                    self.draw_tab_accounts_list()
                except Exception:
                    self.accs_oauth_status.setText("Tên profile không hợp lệ hoặc không ghi được dữ liệu tài khoản.")
                    return
                existing_login = getattr(self, "tab_google_login_worker", None)
                try:
                    if existing_login is not None and existing_login.isRunning():
                        self.accs_oauth_status.setText("⏳ Một cửa sổ đăng nhập Google đang mở. Hãy hoàn tất hoặc đóng cửa sổ đó trước.")
                        self.accs_oauth_status.setStyleSheet("color: #d97706; font-weight: bold; background: transparent;")
                        return
                except Exception:
                    pass
                self._stop_background_google_checks_for_login(prof_name)
                self.accs_oauth_status.setText("🔍 Đang khởi chạy trình duyệt Google...")
                self.accs_oauth_status.setStyleSheet("color: #7c3aed; font-weight: bold;")
                
                self.tab_google_login_worker = GoogleLoginWorker(profile_name=prof_name)
                self.tab_google_login_worker.status_sig.connect(lambda t, c: (self.accs_oauth_status.setText(t), self.accs_oauth_status.setStyleSheet(f"color: {c}; font-weight: bold; background: transparent;")))
                
                def on_google_success(email, token, proj_id):
                    saved_profile = (
                        getattr(self.tab_google_login_worker, "profile_name", "")
                        or prof_name
                    )
                    try:
                        self._commit_google_login_result(
                            email,
                            token,
                            proj_id,
                            saved_profile,
                        )
                    except Exception as exc:
                        self.accs_oauth_status.setText(
                            f"❌ Đã xác thực Google nhưng chưa lưu được tài khoản: {exc}"
                        )
                        self.accs_oauth_status.setStyleSheet(
                            "color: #ef4444; font-weight: bold; background: transparent;"
                        )
                        return
                    
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

        google_accs = [a for a in self.acc_mgr.accounts if _is_threads_google_account(a)]
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
                if self._google_account_worker_running():
                    self.accs_oauth_status.setText("Đang có tác vụ Google; hãy hoàn tất tác vụ trước.")
                    return
                g_accs = [a for a in self.acc_mgr.accounts if a.active and _is_threads_google_account(a)]
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
                        QMessageBox.warning(self, "Kiểm tra Token", f"✅ Đã xác minh: {valid}\n⚠️ Chưa xác minh được: {expired}\n\nXem lỗi cụ thể; chỉ đăng nhập lại khi Flow yêu cầu.")
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
            is_google = _is_threads_google_account(acc)
            bg_color = "#ede9fe" if is_google else "#f1f5f9"
            row.setStyleSheet(f"QFrame#acc_row {{ background-color: {bg_color}; border-radius: 6px; padding: 4px; }}")
            r_lay = QHBoxLayout(row)
            r_lay.setContentsMargins(10, 4, 10, 4)

            type_icon = "🌐" if is_google else ("🤖" if "Grok" in acc.name else "💻")
            token_preview = ("Đã lưu — kiểm tra để xác nhận hiệu lực" if acc.api_key else "Chưa có Bearer được xác minh — xem trạng thái phiên phía trên; không đồng nghĩa logout") if is_google else (f"{acc.api_key[:20]}..." if len(acc.api_key) > 20 else acc.api_key)
            
            credits_str = ""
            if is_google:
                cr_val = getattr(acc, "credits", None)
                cr_txt = f"{cr_val} cr" if cr_val is not None else "--"
                tag_val = getattr(acc, "tag", "Pro")
                from google_flow_auth import auth_attempt_text
                state_text = auth_attempt_text(THREADS_AUTH_ROOT, acc.profile_name, bool(acc.api_key))
                credits_str = f"  │  🏷️ Tag: {tag_val}  │  💰 Credits: {cr_txt}\n{state_text} · Profile: {acc.profile_name}"
                
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
                        if self._google_account_worker_running():
                            self.accs_oauth_status.setText("Đang có tác vụ Google; hãy hoàn tất tác vụ trước.")
                            return
                        profile_name = getattr(a, "profile_name", "") or _resolve_google_profile_name(a.name)
                        active_login = getattr(self, "tab_google_login_worker", None)
                        try:
                            if active_login is not None and active_login.isRunning():
                                self.accs_oauth_status.setText(
                                    "⏳ Đăng nhập Google đang chạy; không kiểm tra token song song."
                                )
                                return
                        except Exception:
                            pass
                        self._single_refresh = GoogleSingleRefreshWorker(a, profile_name)
                        self._single_refresh.token_updated.connect(self._on_google_token_updated)
                        self._single_refresh.status_sig.connect(lambda t, c: self.accs_oauth_status.setText(t) if hasattr(self, 'accs_oauth_status') else None)
                        self._single_refresh.error_sig.connect(
                            lambda err: self.accs_oauth_status.setText(f"❌ {err}")
                            if hasattr(self, 'accs_oauth_status') else None
                        )
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
                        profile_name = getattr(a, "profile_name", "") or _resolve_google_profile_name(a.name)
                        existing_login = getattr(self, "tab_google_login_worker", None)
                        try:
                            if existing_login is not None and existing_login.isRunning():
                                self.accs_oauth_status.setText(
                                    "⏳ Một cửa sổ đăng nhập Google đang mở. Hãy hoàn tất hoặc đóng cửa sổ đó trước."
                                )
                                self.accs_oauth_status.setStyleSheet(
                                    "color: #d97706; font-weight: bold; background: transparent;"
                                )
                                return
                        except Exception:
                            pass

                        # A startup/periodic probe can be holding this exact
                        # profile for a short headless check.  It must yield to
                        # an explicit user login instead of making the login
                        # button report a misleading "profile is busy" error.
                        self._stop_background_google_checks_for_login(profile_name)
                        self.accs_oauth_status.setText(f"🔍 Đang mở trình duyệt cho {profile_name}...")
                        self.accs_oauth_status.setStyleSheet("color: #7c3aed; font-weight: bold; background: transparent;")
                        self.tab_google_login_worker = GoogleLoginWorker(profile_name=profile_name)
                        self.tab_google_login_worker.status_sig.connect(lambda t, c: (self.accs_oauth_status.setText(t), self.accs_oauth_status.setStyleSheet(f"color: {c}; font-weight: bold; background: transparent;")))
                        
                        def on_success(email, token, proj_id):
                            saved_profile = (
                                getattr(self.tab_google_login_worker, "profile_name", "")
                                or profile_name
                            )
                            try:
                                self._commit_google_login_result(
                                    email,
                                    token,
                                    proj_id,
                                    saved_profile,
                                    account_name_hint=a.name,
                                )
                            except Exception as exc:
                                self.accs_oauth_status.setText(
                                    f"❌ Đã xác thực Google nhưng chưa lưu được tài khoản: {exc}"
                                )
                                self.accs_oauth_status.setStyleSheet(
                                    "color: #ef4444; font-weight: bold; background: transparent;"
                                )
                                return
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
                        # Snapshot the complete account before removing it;
                        # profile_name is required when email and Chrome
                        # profile names differ.
                        acc_obj = self.acc_mgr.accounts[i]
                        acc_name = acc_obj.name
                        profile_name = (
                            getattr(acc_obj, "profile_name", "")
                            or _resolve_google_profile_name(acc_name)
                        )
                        cleanup_items = _purge_google_account_data(acc_obj, profile_name)
                        self.acc_mgr.remove(i)
                        self.acc_mgr.load()
                        self.draw_tab_accounts_list()
                        self.refresh_sidebar_acc_lbl()
                        self.refresh_accounts_label_studio()
                        
                        if cleanup_items:
                            self.accs_oauth_status.setText(
                                f"✅ Đã xoá tài khoản và dữ liệu đăng nhập cũ: {acc_name}"
                            )
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
        config_path = THREADS_CONFIG_FILE
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text("utf-8"))
                self.veo_bearer = data.get("bearer_token", "")
                self.veo_project_id = data.get("project_id", "")
                self.veo_flow_profile_name = _resolve_google_profile_name(
                    data.get("flow_profile_name", "")
                )
                self.veo_bridge_port = data.get("bridge_port", 4000)
                self.veo_jitter_min_ms = data.get("jitter_min_ms", 2000)
                self.veo_jitter_max_ms = data.get("jitter_max_ms", 5000)
            except Exception:
                self.veo_bearer = ""
                self.veo_project_id = ""
                self.veo_flow_profile_name = ""
                self.veo_bridge_port = 4000
                self.veo_jitter_min_ms = 2000
                self.veo_jitter_max_ms = 5000
        else:
            self.veo_bearer = ""
            self.veo_project_id = ""
            self.veo_flow_profile_name = ""
            self.veo_bridge_port = 4000
            self.veo_jitter_min_ms = 2000
            self.veo_jitter_max_ms = 5000

        # Override bridge port from threads_settings if saved there
        if getattr(self, "settings", None) and "veo_bridge_port" in self.settings:
            self.veo_bridge_port = int(self.settings["veo_bridge_port"])

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
                port_val = getattr(self, "veo_bridge_port", 4000)
                self.captcha_bridge = CaptchaBridge(self._log, port=port_val)
                self.captcha_bridge.start()
                self._log(f"✔ Khởi động Captcha Bridge Server thành công (Port {port_val})!")
            except Exception as e:
                self._log(f"⚠️ Lỗi khởi động Captcha Bridge Server: {e}")

    def _google_account_worker_running(self) -> bool:
        """Return True while any UI-owned Google login/refresh worker is active."""
        for attr_name in (
            "_token_refresh_worker",
            "_bulk_refresh_worker",
            "_single_refresh",
            "tab_google_login_worker",
        ):
            worker = getattr(self, attr_name, None)
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    return True
            except Exception:
                continue
        return False

    def _stop_background_google_checks_for_login(self, profile_name: str) -> bool:
        """Ask non-interactive checks to yield before an explicit Google login.

        This does not force-close a Playwright process or delete a Chrome lock.
        The worker that already owns the profile finishes safely, and the
        manual login waits on the same app-owned lock instead.
        """
        stopped = []
        for attr_name in ("_token_refresh_worker", "_bulk_refresh_worker", "_single_refresh"):
            worker = getattr(self, attr_name, None)
            try:
                if worker is None or not worker.isRunning():
                    continue
                stop_method = getattr(worker, "stop", None)
                if callable(stop_method):
                    stop_method()
                stopped.append(attr_name)
            except Exception:
                continue
        if stopped:
            self._log(
                f"⏳ [Google Login] Đã yêu cầu dừng kiểm tra nền trước khi mở profile {profile_name}."
            )
        return bool(stopped)

    def auto_refresh_google_tokens(self, reason: str = "periodic"):
        """Validate saved Google tokens and recover them from their Chrome profiles.

        This mirrors the startup behaviour of grok_video_tool.py.  It is safe
        to call periodically: valid tokens only receive a lightweight
        userinfo check; Chrome is opened only for an expired token.  Profile
        access itself is serialized by the existing cross-process lock.
        """
        if not has_grok_deps or self._google_account_worker_running():
            return

        # run_veo_flow may have refreshed accounts.json from a video worker.
        # Reload before taking the snapshot so this check never restores an
        # older in-memory bearer token over the newly persisted one.
        try:
            self.acc_mgr.load()
        except Exception:
            pass
        google_accs = [
            account for account in self.acc_mgr.accounts
            if account.active and _is_threads_google_account(account)
        ]
        if not google_accs:
            if reason == "startup":
                self._log("ℹ️ [Token Check] Không có tài khoản Google đang hoạt động.")
            return

        label = "khi khởi động" if reason == "startup" else "định kỳ"
        self._log(
            f"🔍 [Token Check] Kiểm tra tự động {label} cho "
            f"{len(google_accs)} tài khoản Google..."
        )
        worker = GoogleTokenRefreshWorker(google_accs, sync_credits=False)
        self._token_refresh_worker = worker
        worker.log_sig.connect(self._log)
        worker.token_updated.connect(self._on_google_token_updated)
        worker.finished_all.connect(self._on_google_refresh_done)
        worker.start()

    def _on_google_refresh_done(self, valid_count: int, expired_count: int):
        """Update account state after automatic startup/periodic validation."""
        try:
            self.acc_mgr.load()
        except Exception:
            pass
        if expired_count:
            self._log(
                f"⚠️ [Token Check] Hoàn tất: {valid_count} token OK, "
                f"{expired_count} tài khoản chưa xác minh được; xem lỗi từng bước."
            )
        else:
            self._log(
                f"✅ [Token Check] Hoàn tất: {valid_count} tài khoản Google sẵn sàng."
            )
        try:
            self.refresh_sidebar_acc_lbl()
            if hasattr(self, "tab_accs_scroll_lay"):
                self.draw_tab_accounts_list()
        except Exception:
            pass

    def closeEvent(self, event):
        token_timer = getattr(self, "_google_token_health_timer", None)
        if token_timer is not None:
            try:
                token_timer.stop()
            except Exception:
                pass

        account_workers = []
        for attr_name in (
            "_token_refresh_worker",
            "_bulk_refresh_worker",
            "_single_refresh",
            "tab_google_login_worker",
        ):
            worker = getattr(self, attr_name, None)
            try:
                if worker is not None and worker.isRunning():
                    account_workers.append(worker)
                    stop_method = getattr(worker, "stop", None)
                    if callable(stop_method):
                        stop_method()
            except Exception:
                pass
        for worker in account_workers:
            try:
                worker.wait(800)
            except Exception:
                pass
        if any(worker.isRunning() for worker in account_workers):
            QMessageBox.information(
                self,
                "Đang kết thúc đăng nhập Google",
                "Trình duyệt đang lưu lại phiên Google an toàn. Vui lòng chờ một chút rồi đóng lại."
            )
            event.ignore()
            return

        running = [r for r in getattr(self, "runners", {}).values() if r and r.isRunning()]
        vox_worker = getattr(self, "_vox_worker_thread", None)
        if vox_worker and vox_worker.is_alive():
            self._vox_cancel = True
            vox_worker.join(timeout=0.5)
            if vox_worker.is_alive():
                QMessageBox.information(
                    self,
                    "Đang dừng tác vụ",
                    "Tác vụ video vẫn đang kết thúc an toàn. Vui lòng chờ một chút rồi đóng lại."
                )
                event.ignore()
                return
        if running:
            for runner in running:
                runner._stopped = True
            # Không cho Qt huỷ object QThread khi worker/API vẫn còn chạy;
            # đây là nguyên nhân điển hình của lỗi native không có traceback.
            for runner in running:
                runner.wait(500)
            if any(r.isRunning() for r in running):
                QMessageBox.information(
                    self,
                    "Đang dừng tác vụ",
                    "Tác vụ video vẫn đang kết thúc an toàn. Vui lòng chờ một chút rồi đóng lại."
                )
                event.ignore()
                return
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
            "flow_profile_name": getattr(self, "veo_flow_profile_name", ""),
            "bridge_port": self.veo_bridge_port,
            "jitter_min_ms": getattr(self, "veo_jitter_min_ms", 2000),
            "jitter_max_ms": getattr(self, "veo_jitter_max_ms", 5000)
        }
        # Write to local ReviewThreads config to keep it independent
        try:
            return _atomic_write_json(THREADS_CONFIG_FILE, data)
        except Exception:
            return False

    def _on_google_token_updated(self, acc_name: str, token: str, project_id: str, credits_val: int = -1):
        # A running video worker may have updated the shared accounts.json
        # while this UI worker was checking Chrome. Reload first so saving one
        # account never rolls back another account's newly refreshed token.
        try:
            self.acc_mgr.load()
        except Exception:
            pass
        updated_account = None
        for acc in self.acc_mgr.accounts:
            if acc.name == acc_name:
                from google_flow_auth import token_result_is_current
                if not token_result_is_current(THREADS_AUTH_ROOT, acc.profile_name, token):
                    return
                acc.api_key = token
                if project_id: acc.proxy_url = project_id
                if credits_val >= 0: acc.credits = credits_val
                acc.auth_state = "ready"
                acc.auth_revision = time.time_ns()
                updated_account = acc
                break
        resolved_profile = _resolve_google_profile_name(
            getattr(updated_account, "profile_name", "") or acc_name
        )
        if updated_account is None:
            return
        if updated_account is not None and resolved_profile:
            updated_account.profile_name = resolved_profile
        self.acc_mgr.save()

        try:
            email = acc_name.replace("Google_", "", 1)
            profile_for_registry = resolved_profile or email
            _upsert_flow_account_registry(
                profile_for_registry,
                email=email,
                project_id=project_id,
            )
            shared_dir = _resolve_flow_shared_dir(profile_for_registry)
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
        
        configured_profile = _resolve_google_profile_name(
            getattr(self, "veo_flow_profile_name", "")
        )
        google_accs = [a for a in self.acc_mgr.accounts if _is_threads_google_account(a) and a.active]
        is_configured_account = bool(
            configured_profile and resolved_profile and configured_profile == resolved_profile
        )
        use_first_google_as_default = not configured_profile and google_accs and google_accs[0].name == acc_name
        if is_configured_account or use_first_google_as_default:
            self.veo_bearer = token
            if project_id: self.veo_project_id = project_id
            if resolved_profile: self.veo_flow_profile_name = resolved_profile
            self.save_veo_config()

    def _commit_google_login_result(self, email, token, project_id, profile_name, account_name_hint=''):
        from google_flow_auth import commit_login
        row = commit_login(THREADS_AUTH_ROOT, profile_name, email, token, project_id, account_name_hint)
        self.acc_mgr.load()
        self.veo_bearer = row['api_key']
        self.veo_project_id = row['proxy_url']
        self.veo_flow_profile_name = row['profile_name']
        return row['name'], row['profile_name']

    # ── PAGE 5: VOX DIRECTOR STUDIO (REDESIGNED) ──────────────────────
    def _build_vox_panel(self):
        panel = QWidget()
        panel.setStyleSheet("background:#f8fafc;")
        main_lay = QVBoxLayout(panel)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # ── Gradient Top Bar ──
        top_bar = QFrame()
        top_bar.setStyleSheet("QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4c1d95,stop:1 #7c3aed);}")
        top_bar.setFixedHeight(56)
        tb_lay = QHBoxLayout(top_bar); tb_lay.setContentsMargins(24, 0, 24, 0)
        tl = QLabel("🎬 Vox Director Studio"); tl.setStyleSheet("font-size:18px;font-weight:bold;color:#ffffff;background:transparent;")
        tb_lay.addWidget(tl)
        ts = QLabel("CLIProxy × Vbee × ChatGPT Image 2"); ts.setStyleSheet("font-size:10px;color:#c4b5fd;background:transparent;margin-left:8px;")
        tb_lay.addWidget(ts, alignment=Qt.AlignmentFlag.AlignBottom)
        tb_lay.addStretch()
        # Status badge
        self.vox_global_status = QLabel("⏸️ Sẵn sàng"); self.vox_global_status.setStyleSheet("font-size:11px;color:#a5f3fc;background:rgba(255,255,255,0.1);border-radius:10px;padding:4px 12px;font-weight:bold;")
        tb_lay.addWidget(self.vox_global_status)
        main_lay.addWidget(top_bar)

        # ── Main split: Left (config + controls) | Right (preview + log) ──
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setStyleSheet("QSplitter{background:transparent;} QSplitter::handle{background:#e2e8f0;width:3px;}")

        # ═══════════════════════════════════════════════════════════════
        # LEFT PANEL — CẤU HÌNH (scroll)
        # ═══════════════════════════════════════════════════════════════
        left_scroll = QScrollArea(); left_scroll.setWidgetResizable(True)
        left_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        left_w = QWidget(); left_w.setStyleSheet("background:transparent;")
        ll = QVBoxLayout(left_w); ll.setContentsMargins(16, 12, 8, 12); ll.setSpacing(10)

        card_ss = "QFrame#vxc{background:#ffffff;border:1px solid #e5e7eb;border-radius:10px;}"
        lbl_ss = "font-size:11px;color:#374151;font-weight:600;background:transparent;"
        inp_ss = "QLineEdit{font-size:11px;padding:6px 10px;border:1px solid #d1d5db;border-radius:5px;background:#fff;color:#111827;}QLineEdit:focus{border-color:#7c3aed;}"
        cmb_ss = "QComboBox{font-size:11px;padding:6px 10px;border:1px solid #d1d5db;border-radius:5px;background:#fff;color:#111827;}QComboBox:focus{border-color:#7c3aed;}"
        txt_ss = "QTextEdit{font-size:11px;padding:6px;border:1px solid #d1d5db;border-radius:5px;background:#fff;color:#111827;}QTextEdit:focus{border-color:#7c3aed;}"
        sec_ss = "font-size:12px;font-weight:bold;color:#4c1d95;background:transparent;"
        spn_ss = "QSpinBox,QDoubleSpinBox{font-size:11px;padding:4px 8px;border:1px solid #d1d5db;border-radius:5px;background:#fff;color:#111827;min-width:80px;}"
        desc_ss = "font-size:9px;color:#6b7280;background:transparent;font-style:italic;"

        def _lbl(t): l = QLabel(t); l.setStyleSheet(lbl_ss); return l
        def _sec(t): l = QLabel(t); l.setStyleSheet(sec_ss); return l

        # ── CARD: CHỦ ĐỀ ──
        c1 = QFrame(); c1.setObjectName("vxc"); c1.setStyleSheet(card_ss)
        c1l = QVBoxLayout(c1); c1l.setContentsMargins(14, 10, 14, 10); c1l.setSpacing(6)
        c1l.addWidget(_sec("📝 CHỦ ĐỀ & DỰ ÁN"))
        r = QHBoxLayout()
        r.addWidget(_lbl("📁 Tên:"))
        self.vox_project_name = QLineEdit("my-vox-video"); self.vox_project_name.setStyleSheet(inp_ss)
        self.vox_project_name.setPlaceholderText("Tên thư mục (không dấu)"); r.addWidget(self.vox_project_name)
        c1l.addLayout(r)
        c1l.addWidget(_lbl("💡 Chủ đề video:"))
        self.vox_topic = QTextEdit(); self.vox_topic.setStyleSheet(txt_ss)
        self.vox_topic.setPlaceholderText("Ví dụ: Lịch sử cà phê Việt Nam từ thời Pháp đến hiện đại, phong cách Vox explainer")
        self.vox_topic.setFixedHeight(55); c1l.addWidget(self.vox_topic)
        r2 = QHBoxLayout(); r2.setSpacing(8)
        r2.addWidget(_lbl("🌐")); self.vox_language = QComboBox(); self.vox_language.addItems(["vi","en","zh","ja","ko","fr","de","es"]); self.vox_language.setStyleSheet(cmb_ss); r2.addWidget(self.vox_language)
        r2.addWidget(_lbl("📐")); self.vox_aspect = QComboBox(); self.vox_aspect.addItems(["9:16 (TikTok)","16:9 (YouTube)","1:1 (Instagram)","3:4"]); self.vox_aspect.setStyleSheet(cmb_ss); r2.addWidget(self.vox_aspect)
        r2.addWidget(_lbl("⏱️")); self.vox_duration = QSpinBox(); self.vox_duration.setRange(15,120); self.vox_duration.setValue(30); self.vox_duration.setSuffix("s"); self.vox_duration.setStyleSheet(spn_ss); r2.addWidget(self.vox_duration)
        c1l.addLayout(r2)
        # LLM Model selector
        r_llm = QHBoxLayout(); r_llm.setSpacing(8)
        r_llm.addWidget(_lbl("🤖 Model AI (LLM):"))
        self.vox_llm_model = QComboBox(); self.vox_llm_model.setStyleSheet(cmb_ss + "QComboBox{min-width:200px;}")
        self.vox_llm_model.addItems(LLM_MODEL_LIST)
        self.vox_llm_model.setCurrentText(self.settings.get("llm_model", "gpt-5.5"))
        r_llm.addWidget(self.vox_llm_model)
        r_llm.addWidget(_lbl("⚡ Luồng song song:"))
        self.vox_concurrent = QSpinBox(); self.vox_concurrent.setRange(1, 10)
        self.vox_concurrent.setValue(min(len(self.acc_mgr.get_all_codex()), 3) or 2)
        self.vox_concurrent.setStyleSheet(spn_ss); self.vox_concurrent.setToolTip("Số ảnh tạo đồng thời (mỗi luồng dùng 1 tài khoản Codex)")
        r_llm.addWidget(self.vox_concurrent)
        r_llm.addStretch()
        c1l.addLayout(r_llm)
        ll.addWidget(c1)

        # ── CARD: PHONG CÁCH (Vietnamized + descriptions) ──
        c2 = QFrame(); c2.setObjectName("vxc"); c2.setStyleSheet(card_ss)
        c2l = QVBoxLayout(c2); c2l.setContentsMargins(14, 10, 14, 10); c2l.setSpacing(6)
        c2l.addWidget(_sec("🎨 PHONG CÁCH VISUAL"))

        # Theme with Vietnamese names + descriptions
        c2l.addWidget(_lbl("🖌️ Chủ đề hình ảnh:"))
        self.vox_theme = QComboBox(); self.vox_theme.setStyleSheet(cmb_ss + "QComboBox{min-width:260px;}")
        _themes = [
            ("american-retro", "🇺🇸 Retro Mỹ — Poster vintage 50-60s, màu pastel ấm, phông chữ cổ điển. Phù hợp: quảng cáo, giới thiệu sản phẩm"),
            ("swiss-modern", "🇨🇭 Hiện đại Thuỵ Sĩ — Tối giản, grid rõ ràng, sạch sẽ. Phù hợp: công nghệ, giáo dục"),
            ("punk-zine", "🎸 Punk Tạp chí — Cắt dán thô, chữ xé, sáng tạo mạnh. Phù hợp: âm nhạc, nghệ thuật, viral"),
            ("soviet-constructivist", "🔴 Xây dựng Xô Viết — Hình học mạnh, đỏ-đen-vàng, tuyên truyền. Phù hợp: lịch sử, chính trị"),
            ("wpa-propaganda", "🏛️ Tuyên truyền WPA — Poster chính phủ Mỹ thập niên 30, nét vẽ đậm. Phù hợp: công ích, du lịch"),
            ("70s-groovy", "🕺 Groovy thập niên 70 — Màu neon, hoa văn retro, disco vibes. Phù hợp: giải trí, thời trang"),
            ("chinese-ink", "🎋 Thuỷ mặc Trung Hoa — Tranh thuỷ mặc, bút lông, thanh nhã. Phù hợp: văn hoá Á Đông, trà, ẩm thực"),
            ("atomic-age", "⚛️ Thời đại Nguyên tử — Sci-fi 50s, tương lai retro, vũ trụ. Phù hợp: khoa học, công nghệ mới"),
            ("newsprint-editorial", "📰 Báo chí Biên tập — Layout tờ báo cổ, serif, tin tức. Phù hợp: tin tức, phân tích, review"),
            ("gilded-deco", "✨ Art Deco Dát vàng — Vàng-đen sang trọng, hoa văn Gatsby. Phù hợp: xa xỉ phẩm, tài chính, sự kiện"),
        ]
        for val, label in _themes:
            self.vox_theme.addItem(label, val)
        self.vox_theme_desc = QLabel(""); self.vox_theme_desc.setStyleSheet(desc_ss); self.vox_theme_desc.setWordWrap(True)
        self.vox_theme.currentIndexChanged.connect(lambda i: self.vox_theme_desc.setText(_themes[i][1].split("—")[1].strip() if i < len(_themes) else ""))
        c2l.addWidget(self.vox_theme); c2l.addWidget(self.vox_theme_desc)
        self.vox_theme_desc.setText(_themes[0][1].split("—")[1].strip())

        # Arc (story structure)
        c2l.addWidget(_lbl("📖 Cấu trúc kịch bản:"))
        self.vox_arc = QComboBox(); self.vox_arc.setStyleSheet(cmb_ss + "QComboBox{min-width:260px;}")
        _arcs = [
            ("timeline", "⏳ Dòng thời gian — Kể theo trình tự thời gian. Phù hợp: lịch sử, tiểu sử"),
            ("pas", "🎯 Problem→Agitate→Solution — Nêu vấn đề, khuấy động, đưa giải pháp. Phù hợp: bán hàng"),
            ("bab", "🌟 Before→After→Bridge — Trước-Sau-Cầu nối. Phù hợp: review, quảng cáo sản phẩm"),
            ("how_it_works", "⚙️ Cách hoạt động — Giải thích cơ chế từng bước. Phù hợp: hướng dẫn, giáo dục"),
            ("man_in_hole", "🕳️ Nhân vật gặp khó → Vượt qua — Kịch tính, cảm xúc. Phù hợp: câu chuyện cá nhân"),
            ("listicle", "📋 Danh sách — Top N điều cần biết. Phù hợp: tips, ranking, viral"),
            ("before_after", "🔄 Trước & Sau — So sánh trực quan. Phù hợp: review, makeover"),
        ]
        for val, label in _arcs:
            self.vox_arc.addItem(label, val)
        self.vox_arc_desc = QLabel(""); self.vox_arc_desc.setStyleSheet(desc_ss); self.vox_arc_desc.setWordWrap(True)
        self.vox_arc.currentIndexChanged.connect(lambda i: self.vox_arc_desc.setText(_arcs[i][1].split("—")[1].strip() if i < len(_arcs) else ""))
        c2l.addWidget(self.vox_arc); c2l.addWidget(self.vox_arc_desc)
        self.vox_arc_desc.setText(_arcs[0][1].split("—")[1].strip())

        r3 = QHBoxLayout(); r3.setSpacing(8)
        r3.addWidget(_lbl("🎭 Chuyển động:"))
        self.vox_motion = QComboBox(); self.vox_motion.setStyleSheet(cmb_ss)
        self.vox_motion.addItem("🎬 Năng động (Punchy) — Cắt nhanh, zoom mạnh", "punchy")
        self.vox_motion.addItem("🌊 Nhẹ nhàng (Calm) — Pan chậm, chuyển mượt", "calm")
        self.vox_motion.addItem("🚀 Tối đa (Max) — Hiệu ứng mạnh nhất", "max")
        r3.addWidget(self.vox_motion)
        c2l.addLayout(r3)

        r4 = QHBoxLayout(); r4.setSpacing(8)
        r4.addWidget(_lbl("🖼️ Model ảnh:"))
        self.vox_img_model = QComboBox(); self.vox_img_model.setStyleSheet(cmb_ss)
        self.vox_img_model.addItem("✨ GPT Image 2 (Khuyên dùng)", "gpt-image-2")
        self.vox_img_model.addItem("📸 GPT Image 1.5", "gpt-image-1.5")
        self.vox_img_model.addItem("🎨 Grok Imagine", "grok-imagine-image")
        self.vox_img_model.addItem("💎 Grok Imagine Quality", "grok-imagine-image-quality")
        r4.addWidget(self.vox_img_model)
        r4.addWidget(_lbl("📏")); self.vox_img_res = QComboBox(); self.vox_img_res.addItems(["1k","2k"]); self.vox_img_res.setStyleSheet(cmb_ss); r4.addWidget(self.vox_img_res)
        c2l.addLayout(r4)
        ll.addWidget(c2)

        # ── CARD: GIỌNG ĐỌC VBEE ──
        c3 = QFrame(); c3.setObjectName("vxc"); c3.setStyleSheet(card_ss)
        c3l = QVBoxLayout(c3); c3l.setContentsMargins(14, 10, 14, 10); c3l.setSpacing(6)
        c3l.addWidget(_sec("🎙️ GIỌNG ĐỌC (VBEE TTS)"))
        g3 = QGridLayout(); g3.setHorizontalSpacing(8); g3.setVerticalSpacing(6)
        g3.addWidget(_lbl("🔑 Token:"), 0, 0)
        self.vox_vbee_token = QLineEdit(); self.vox_vbee_token.setStyleSheet(inp_ss); self.vox_vbee_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.vox_vbee_token.setPlaceholderText("Bearer token Vbee.vn"); self.vox_vbee_token.setText(self.settings.get("vbee_token", "")); g3.addWidget(self.vox_vbee_token, 0, 1)
        g3.addWidget(_lbl("🆔 App-ID:"), 1, 0)
        self.vox_vbee_appid = QLineEdit(); self.vox_vbee_appid.setStyleSheet(inp_ss)
        self.vox_vbee_appid.setPlaceholderText("App ID Vbee.vn"); self.vox_vbee_appid.setText(self.settings.get("vbee_appid", "")); g3.addWidget(self.vox_vbee_appid, 1, 1)
        g3.addWidget(_lbl("🗣️ Voice:"), 2, 0)
        self.vox_vbee_voice = QComboBox(); self.vox_vbee_voice.setEditable(True); self.vox_vbee_voice.setStyleSheet(cmb_ss)
        self.vox_vbee_voice.addItems(["vi_south_female_miien_48k","vi_north_female_lien_48k","vi_south_male_phuc_48k","vi_north_male_duc_48k"]); g3.addWidget(self.vox_vbee_voice, 2, 1)
        g3.addWidget(_lbl("⚡ Tốc độ:"), 3, 0)
        self.vox_vbee_speed = QDoubleSpinBox(); self.vox_vbee_speed.setRange(0.5,2.0); self.vox_vbee_speed.setValue(1.0); self.vox_vbee_speed.setSingleStep(0.1); self.vox_vbee_speed.setStyleSheet(spn_ss); g3.addWidget(self.vox_vbee_speed, 3, 1)
        g3.setColumnStretch(0,0); g3.setColumnStretch(1,1); c3l.addLayout(g3)
        btn_f = QPushButton("🔄 Lấy Danh Sách Voice"); btn_f.setFixedHeight(30)
        btn_f.setStyleSheet("QPushButton{background:#7c3aed;color:white;font-weight:bold;padding:0 12px;border:none;border-radius:5px;font-size:10px;}QPushButton:hover{background:#6d28d9;}")
        btn_f.clicked.connect(self._vox_fetch_voices); c3l.addWidget(btn_f)
        ll.addWidget(c3)

        # ── CARD: NHẠC NỀN ──
        c4 = QFrame(); c4.setObjectName("vxc"); c4.setStyleSheet(card_ss)
        c4l = QVBoxLayout(c4); c4l.setContentsMargins(14, 10, 14, 10); c4l.setSpacing(6)
        c4l.addWidget(_sec("🎵 NHẠC NỀN (BGM)"))
        r5 = QHBoxLayout()
        self.vox_bgm_path = QLineEdit(); self.vox_bgm_path.setStyleSheet(inp_ss); self.vox_bgm_path.setPlaceholderText("Chọn file .mp3 (bỏ trống = tự tạo)")
        r5.addWidget(self.vox_bgm_path)
        bb = QPushButton("📂"); bb.setFixedSize(32,30); bb.setStyleSheet("QPushButton{border:1px solid #d1d5db;border-radius:5px;background:#f3f4f6;}QPushButton:hover{background:#e5e7eb;}")
        bb.clicked.connect(self._vox_browse_bgm); r5.addWidget(bb); c4l.addLayout(r5)
        self.vox_music_prompt = QLineEdit("cinematic warm instrumental, Vietnamese traditional fusion"); self.vox_music_prompt.setStyleSheet(inp_ss)
        self.vox_music_prompt.setPlaceholderText("Mô tả nhạc (nếu không có file)"); c4l.addWidget(self.vox_music_prompt)
        ll.addWidget(c4)

        ll.addStretch()
        left_scroll.setWidget(left_w)

        # ═══════════════════════════════════════════════════════════════
        # RIGHT PANEL — PREVIEW + CONTROLS + LOG
        # ═══════════════════════════════════════════════════════════════
        right_w = QWidget(); right_w.setStyleSheet("background:transparent;")
        rl = QVBoxLayout(right_w); rl.setContentsMargins(8, 12, 16, 12); rl.setSpacing(8)

        # ── PIPELINE CONTROL BAR ──
        ctrl = QFrame(); ctrl.setObjectName("vxc"); ctrl.setStyleSheet(card_ss)
        ctrl_l = QVBoxLayout(ctrl); ctrl_l.setContentsMargins(14, 10, 14, 10); ctrl_l.setSpacing(8)

        # Step indicators
        steps_row = QHBoxLayout(); steps_row.setSpacing(4)
        self.vox_step_frames = {}
        self.vox_status_labels = {}
        for step, icon, name in [("beats","📋","Kịch bản AI"),("keyframes","🖼️","Tạo ảnh"),("audio","🎙️","Giọng đọc"),("assemble","🔧","Ghép video")]:
            sf = QFrame(); sf.setStyleSheet("QFrame{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;}")
            sfl = QVBoxLayout(sf); sfl.setContentsMargins(6,4,6,4); sfl.setSpacing(1)
            sl = QLabel(f"{icon} {name}"); sl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sl.setStyleSheet("font-size:9px;font-weight:bold;color:#64748b;background:transparent;"); sfl.addWidget(sl)
            st = QLabel("⏳ Chờ"); st.setAlignment(Qt.AlignmentFlag.AlignCenter)
            st.setStyleSheet("font-size:8px;color:#94a3b8;background:transparent;"); sfl.addWidget(st)
            self.vox_status_labels[step] = st
            self.vox_step_frames[step] = sf
            steps_row.addWidget(sf)
        ctrl_l.addLayout(steps_row)

        # Progress
        self.vox_progress = QProgressBar(); self.vox_progress.setRange(0,100); self.vox_progress.setValue(0); self.vox_progress.setFixedHeight(8)
        self.vox_progress.setTextVisible(False)
        self.vox_progress.setStyleSheet("QProgressBar{border:none;border-radius:4px;background:#e2e8f0;}QProgressBar::chunk{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #7c3aed,stop:1 #a78bfa);border-radius:4px;}")
        ctrl_l.addWidget(self.vox_progress)

        # Elapsed time
        self.vox_elapsed_lbl = QLabel(""); self.vox_elapsed_lbl.setStyleSheet("font-size:9px;color:#94a3b8;background:transparent;"); self.vox_elapsed_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_l.addWidget(self.vox_elapsed_lbl)

        # ── BUTTONS ──
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        self.vox_btn_start = QPushButton("▶ Bắt Đầu"); self.vox_btn_start.setFixedHeight(38)
        self.vox_btn_start.setStyleSheet("QPushButton{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #059669,stop:1 #10b981);color:white;font-weight:bold;padding:0 18px;border:none;border-radius:8px;font-size:12px;}QPushButton:hover{background:#047857;}QPushButton:disabled{background:#94a3b8;}")
        self.vox_btn_start.clicked.connect(self._vox_start_full)
        btn_row.addWidget(self.vox_btn_start)

        self.vox_btn_pause = QPushButton("⏸️ Dừng lại"); self.vox_btn_pause.setFixedHeight(38); self.vox_btn_pause.setEnabled(False)
        self.vox_btn_pause.setStyleSheet("QPushButton{background:#f59e0b;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:8px;font-size:12px;}QPushButton:hover{background:#d97706;}QPushButton:disabled{background:#d1d5db;color:#9ca3af;}")
        self.vox_btn_pause.clicked.connect(self._vox_toggle_pause)
        btn_row.addWidget(self.vox_btn_pause)

        self.vox_btn_view = QPushButton("🎬 Xem Video"); self.vox_btn_view.setFixedHeight(38); self.vox_btn_view.setEnabled(False)
        self.vox_btn_view.setStyleSheet("QPushButton{background:#7c3aed;color:white;font-weight:bold;padding:0 14px;border:none;border-radius:8px;font-size:12px;}QPushButton:hover{background:#6d28d9;}QPushButton:disabled{background:#d1d5db;color:#9ca3af;}")
        self.vox_btn_view.clicked.connect(self._vox_view_video)
        btn_row.addWidget(self.vox_btn_view)

        self.vox_btn_folder = QPushButton("📂 Mở Thư Mục"); self.vox_btn_folder.setFixedHeight(38)
        self.vox_btn_folder.setStyleSheet("QPushButton{background:#fff;color:#334155;border:1px solid #cbd5e1;border-radius:8px;padding:0 14px;font-size:11px;font-weight:600;}QPushButton:hover{background:#f1f5f9;}")
        self.vox_btn_folder.clicked.connect(self._vox_open_folder)
        btn_row.addWidget(self.vox_btn_folder)
        ctrl_l.addLayout(btn_row)
        rl.addWidget(ctrl)

        # ── PREVIEW PANEL (beats / images / audio) ──
        prev_card = QFrame(); prev_card.setObjectName("vxc"); prev_card.setStyleSheet(card_ss)
        prev_l = QVBoxLayout(prev_card); prev_l.setContentsMargins(14, 10, 14, 10); prev_l.setSpacing(6)
        ph = QHBoxLayout()
        ph.addWidget(_sec("👁️ PREVIEW — KỊCH BẢN & KẾT QUẢ"))
        ph.addStretch()
        self.vox_preview_counter = QLabel(""); self.vox_preview_counter.setStyleSheet("font-size:9px;color:#7c3aed;background:#f5f3ff;border-radius:8px;padding:2px 8px;font-weight:bold;")
        ph.addWidget(self.vox_preview_counter)
        prev_l.addLayout(ph)

        # Preview scroll area — this shows beats as cards with images
        self.vox_preview_scroll = QScrollArea(); self.vox_preview_scroll.setWidgetResizable(True)
        self.vox_preview_scroll.setStyleSheet("QScrollArea{background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;}")
        self.vox_preview_scroll.setMinimumHeight(180)
        self.vox_preview_inner = QWidget(); self.vox_preview_inner.setStyleSheet("background:transparent;")
        self.vox_preview_layout = QVBoxLayout(self.vox_preview_inner)
        self.vox_preview_layout.setContentsMargins(8, 6, 8, 6); self.vox_preview_layout.setSpacing(6)
        # Placeholder
        ph_lbl = QLabel("💡 Nhấn ▶ Bắt Đầu để tự động tạo kịch bản, ảnh, giọng đọc và ghép video.\nKết quả từng bước sẽ hiện ở đây theo thời gian thực.")
        ph_lbl.setStyleSheet("font-size:11px;color:#94a3b8;padding:20px;background:transparent;"); ph_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter); ph_lbl.setWordWrap(True)
        self.vox_preview_layout.addWidget(ph_lbl)
        self.vox_preview_layout.addStretch()
        self.vox_preview_scroll.setWidget(self.vox_preview_inner)
        prev_l.addWidget(self.vox_preview_scroll)
        rl.addWidget(prev_card, stretch=1)

        # ── LOG PANEL ──
        log_card = QFrame(); log_card.setObjectName("vxc"); log_card.setStyleSheet(card_ss)
        log_l = QVBoxLayout(log_card); log_l.setContentsMargins(14, 8, 14, 8); log_l.setSpacing(4)
        lh = QHBoxLayout()
        lh.addWidget(_sec("📜 LOG THỜI GIAN THỰC")); lh.addStretch()
        bc = QPushButton("🗑️ Xoá"); bc.setFixedHeight(22)
        bc.setStyleSheet("QPushButton{background:#f1f5f9;color:#64748b;border:1px solid #e2e8f0;border-radius:3px;padding:0 8px;font-size:9px;}QPushButton:hover{background:#e2e8f0;}")
        bc.clicked.connect(lambda: self.vox_log.clear()); lh.addWidget(bc)
        log_l.addLayout(lh)
        self.vox_log = QTextEdit(); self.vox_log.setReadOnly(True)
        self.vox_log.setStyleSheet("QTextEdit{font-family:'Consolas','Cascadia Code',monospace;font-size:10px;background:#0f172a;color:#e2e8f0;border:1px solid #334155;border-radius:5px;padding:6px;}")
        log_l.addWidget(self.vox_log)
        rl.addWidget(log_card, stretch=1)

        # ── Assemble splitter ──
        body.addWidget(left_scroll)
        body.addWidget(right_w)
        body.setSizes([380, 620])
        main_lay.addWidget(body, stretch=1)

        self.stacked.addWidget(panel)

        # ── Internal state ──
        self._vox_running = False
        self._vox_paused = False
        self._vox_cancel = False
        self._vox_start_time = 0
        self._vox_elapsed_timer = QTimer(self)
        self._vox_elapsed_timer.timeout.connect(self._vox_tick_elapsed)

    # ══════════════════════════════════════════════════════════════════
    #  VOX HELPER METHODS
    # ══════════════════════════════════════════════════════════════════
    def _vox_styled_label(self, text, ss):
        lbl = QLabel(text); lbl.setStyleSheet(ss); return lbl

    @pyqtSlot(object)
    def _run_vox_ui_call(self, callback):
        """Thực thi callback UI trên Qt main thread."""
        try:
            callback()
        except RuntimeError:
            # Cửa sổ/widget có thể đã được đóng trong lúc worker kết thúc.
            pass
        except Exception:
            _record_background_exception("VOX UI CALLBACK EXCEPTION")

    def _vox_ui_call(self, callback):
        self.vox_ui_call_sig.emit(callback)

    def _vox_log(self, msg):
        ts = time.strftime("%H:%M:%S")
        txt = f"[{ts}] {msg}"
        print(txt)
        self._vox_ui_call(lambda: self._vox_log_ui(txt))

    def _vox_log_ui(self, txt):
        self.vox_log.append(txt)
        sb = self.vox_log.verticalScrollBar(); sb.setValue(sb.maximum())

    def _vox_set_status(self, step, status, color="#64748b"):
        self._vox_ui_call(lambda: self._vox_set_status_ui(step, status, color))

    def _vox_set_status_ui(self, step, status, color="#64748b"):
        if step in self.vox_status_labels:
            self.vox_status_labels[step].setText(status)
            self.vox_status_labels[step].setStyleSheet(f"font-size:8px;color:{color};background:transparent;font-weight:bold;")
        if step in self.vox_step_frames:
            if "Đang" in status:
                self.vox_step_frames[step].setStyleSheet("QFrame{background:#fef3c7;border:2px solid #f59e0b;border-radius:6px;}")
            elif "✔" in status or "🎉" in status:
                self.vox_step_frames[step].setStyleSheet("QFrame{background:#dcfce7;border:2px solid #16a34a;border-radius:6px;}")
            elif "❌" in status:
                self.vox_step_frames[step].setStyleSheet("QFrame{background:#fee2e2;border:2px solid #dc2626;border-radius:6px;}")
            else:
                self.vox_step_frames[step].setStyleSheet("QFrame{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;}")

    def _vox_tick_elapsed(self):
        if self._vox_start_time:
            elapsed = int(time.time() - self._vox_start_time)
            m, s = divmod(elapsed, 60)
            self.vox_elapsed_lbl.setText(f"⏱️ Thời gian: {m:02d}:{s:02d}")

    def _vox_set_global(self, text, color="#a5f3fc"):
        self._vox_ui_call(lambda: self._vox_set_global_ui(text, color))

    def _vox_set_global_ui(self, text, color):
        self.vox_global_status.setText(text)
        self.vox_global_status.setStyleSheet(f"font-size:11px;color:{color};background:rgba(255,255,255,0.15);border-radius:10px;padding:4px 12px;font-weight:bold;")

    def _vox_get_project_dir(self):
        vox_dir = CURRENT_DIR.parent / "Remotion" / "vox-director"
        name = self.vox_project_name.text().strip() or "my-vox-video"
        proj_dir = vox_dir / "out" / name
        proj_dir.mkdir(parents=True, exist_ok=True)
        return proj_dir

    def _vox_clear_preview(self):
        """Clear all items in the preview panel."""
        self._vox_ui_call(self._vox_clear_preview_ui)

    def _vox_clear_preview_ui(self):
        lay = self.vox_preview_layout
        while lay.count():
            item = lay.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        # Re-add stretch at bottom
        self.vox_preview_layout.addStretch()

    def _vox_add_preview_beat(self, idx, beat):
        self._vox_ui_call(lambda: self._vox_add_preview_beat_ui(idx, beat))

    def _vox_add_preview_beat_ui(self, idx, beat):
        card = QFrame(); card.setStyleSheet("QFrame{background:#ffffff;border:1px solid #e2e8f0;border-radius:8px;}")
        cl = QHBoxLayout(card); cl.setContentsMargins(10, 8, 10, 8); cl.setSpacing(10)
        badge = QLabel(f"#{idx+1}"); badge.setFixedSize(28, 28); badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet("font-size:11px;font-weight:bold;color:white;background:#7c3aed;border-radius:14px;")
        cl.addWidget(badge)
        info = QVBoxLayout(); info.setSpacing(2)
        title = beat.get("title_en", f"Beat {idx+1}")
        narr = beat.get("narration", "")[:80]
        tl = QLabel(f"<b>{title}</b>"); tl.setStyleSheet("font-size:11px;color:#1e293b;background:transparent;"); tl.setWordWrap(True)
        nl = QLabel(f"🗣️ {narr}{'...' if len(beat.get('narration',''))>80 else ''}"); nl.setStyleSheet("font-size:9px;color:#64748b;background:transparent;"); nl.setWordWrap(True)
        info.addWidget(tl); info.addWidget(nl)
        shots = beat.get("shots", [])
        if shots:
            sp = shots[0].get("scene", "")[:60]
            if sp:
                spl = QLabel(f"🖼️ {sp}..."); spl.setStyleSheet("font-size:9px;color:#7c3aed;background:#f5f3ff;border-radius:3px;padding:1px 4px;"); spl.setWordWrap(True)
                info.addWidget(spl)
        cl.addLayout(info, stretch=1)
        self._vox_beat_thumbs = getattr(self, '_vox_beat_thumbs', {})
        img_lbl = QLabel("🖼️"); img_lbl.setFixedSize(64, 64); img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_lbl.setStyleSheet("font-size:24px;background:#f1f5f9;border:1px dashed #cbd5e1;border-radius:6px;")
        self._vox_beat_thumbs[idx] = img_lbl
        cl.addWidget(img_lbl)
        self._vox_beat_audio = getattr(self, '_vox_beat_audio', {})
        audio_lbl = QLabel("🔇"); audio_lbl.setFixedSize(28, 28); audio_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audio_lbl.setStyleSheet("font-size:14px;background:#f1f5f9;border-radius:14px;")
        self._vox_beat_audio[idx] = audio_lbl
        cl.addWidget(audio_lbl)
        self.vox_preview_layout.insertWidget(self.vox_preview_layout.count() - 1, card)

    def _vox_update_preview_image(self, idx, img_path):
        self._vox_ui_call(lambda: self._vox_update_preview_image_ui(idx, img_path))

    def _vox_update_preview_image_ui(self, idx, img_path):
        thumbs = getattr(self, '_vox_beat_thumbs', {})
        if idx in thumbs:
            try:
                pm = QPixmap(str(img_path)).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                thumbs[idx].setPixmap(pm)
                thumbs[idx].setStyleSheet("background:transparent;border:1px solid #7c3aed;border-radius:6px;")
            except Exception:
                pass

    def _vox_update_preview_audio(self, idx, done=True):
        self._vox_ui_call(lambda: self._vox_update_preview_audio_ui(idx, done))

    def _vox_update_preview_audio_ui(self, idx, done):
        audios = getattr(self, '_vox_beat_audio', {})
        if idx in audios:
            audios[idx].setText("🔊" if done else "⏳")
            audios[idx].setStyleSheet(f"font-size:14px;background:{'#dcfce7' if done else '#fef3c7'};border-radius:14px;")

    def _vox_progress_set_val(self, val):
        self._vox_ui_call(lambda: self.vox_progress.setValue(val))

    def _vox_set_preview_counter(self, txt):
        self._vox_ui_call(lambda: self.vox_preview_counter.setText(txt))

    def _vox_build_beats_json(self):
        aspect_raw = self.vox_aspect.currentText().split(" ")[0]
        theme_val = self.vox_theme.currentData() or "american-retro"
        arc_val = self.vox_arc.currentData() or "timeline"
        motion_val = self.vox_motion.currentData() or "punchy"
        img_model_val = self.vox_img_model.currentData() or "gpt-image-2"
        voice_code = self.vox_vbee_voice.currentData() or self.vox_vbee_voice.currentText()
        return {
            "project": self.vox_project_name.text().strip() or "my-vox-video",
            "topic": self.vox_topic.toPlainText().strip(),
            "language": self.vox_language.currentText(),
            "aspect": aspect_raw,
            "style": "collage",
            "provider": "cliproxy",
            "theme": theme_val,
            "arc": arc_val,
            "image_model": img_model_val,
            "image_resolution": self.vox_img_res.currentText(),
            "motion_style": motion_val,
            "constraints": "strict",
            "voice": {
                "engine": "vbee",
                "vbee_token": self.vox_vbee_token.text().strip(),
                "vbee_appid": self.vox_vbee_appid.text().strip(),
                "voice_code": voice_code,
                "speed": self.vox_vbee_speed.value(),
                "language": self.vox_language.currentText(),
            },
            "music": self.vox_music_prompt.text().strip(),
            "bgm_path": self.vox_bgm_path.text().strip() or None,
            "captions": True,
            "beats": [],
        }

    # ── Button handlers ───────────────────────────────────────────────
    def _vox_fetch_voices(self):
        token = self.vox_vbee_token.text().strip()
        appid = self.vox_vbee_appid.text().strip()
        if not token or not appid:
            QMessageBox.warning(self, "Thiếu thông tin", "Nhập Vbee Token và App-ID trước."); return
        self._vox_log("🔄 Đang lấy danh sách voice Vbee...")

        def _fetch():
            try:
                vox_dir = CURRENT_DIR.parent / "Remotion" / "vox-director" / "scripts"
                if str(vox_dir) not in sys.path:
                    sys.path.insert(0, str(vox_dir))
                from vbee_tts import vbee_get_voices
                self._vox_log(f"  📡 Token: {token[:12]}... | AppID: {appid[:12]}...")
                voices = vbee_get_voices(token, appid)
                if voices:
                    self._vox_ui_call(lambda: self._vox_populate_voices(voices))
                    self._vox_log(f"✔ Tìm thấy {len(voices)} voice!")
                    self.settings["vbee_token"] = token; self.settings["vbee_appid"] = appid
                    self._vox_ui_call(lambda: self._save_settings(self.settings))
                else:
                    self._vox_log("❌ Không tìm thấy voice. Kiểm tra Token/AppID.")
            except Exception as e:
                import traceback
                self._vox_log(f"❌ Lỗi fetch voice: {e}")
                self._vox_log(f"  📋 {traceback.format_exc().splitlines()[-1]}")

        threading.Thread(target=_fetch, daemon=True).start()

    def _vox_populate_voices(self, voices):
        """Populate voice combo from main thread."""
        self.vox_vbee_voice.clear()
        for code, label, g, region in voices:
            self.vox_vbee_voice.addItem(label, code)


    def _vox_browse_bgm(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Chọn file nhạc nền", "", "Audio (*.mp3 *.wav *.m4a)")
        if fp: self.vox_bgm_path.setText(fp)

    def _vox_open_folder(self):
        d = self._vox_get_project_dir()
        if sys.platform == "win32": os.startfile(str(d))
        else: subprocess.Popen(["xdg-open", str(d)])

    def _vox_view_video(self):
        final = self._vox_get_project_dir() / "final.mp4"
        if final.exists():
            if sys.platform == "win32": os.startfile(str(final))
            else: subprocess.Popen(["xdg-open", str(final)])
        else:
            QMessageBox.information(self, "Chưa có video", "Video chưa được tạo. Nhấn ▶ Bắt Đầu để chạy pipeline.")

    def _vox_toggle_pause(self):
        if self._vox_paused:
            self._vox_paused = False
            self.vox_btn_pause.setText("⏸️ Dừng lại")
            self._vox_log("▶ Tiếp tục pipeline...")
            self._vox_set_global("🔄 Đang chạy...", "#a5f3fc")
        else:
            self._vox_paused = True
            self.vox_btn_pause.setText("▶ Tiếp tục")
            self._vox_log("⏸️ Đã tạm dừng pipeline.")
            self._vox_set_global("⏸️ Tạm dừng", "#fcd34d")

    # ── FULL PIPELINE: START ──────────────────────────────────────────
    def _vox_start_full(self):
        """One-click: generate beats → keyframes → audio → assemble."""
        topic = self.vox_topic.toPlainText().strip()
        if not topic:
            QMessageBox.warning(self, "Thiếu chủ đề", "Nhập chủ đề video trước."); return

        # Snapshot toàn bộ cấu hình Qt khi còn ở main thread. Worker chỉ dùng dữ
        # liệu Python thuần để tránh native crash do đọc widget từ luồng nền.
        job_doc = self._vox_build_beats_json()
        job_duration = self.vox_duration.value()
        job_llm_model = self.vox_llm_model.currentText() or "gpt-5.5"
        job_concurrent = self.vox_concurrent.value()

        # Reset state
        self._vox_running = True; self._vox_paused = False; self._vox_cancel = False
        self._vox_start_time = time.time()
        self._vox_elapsed_timer.start(1000)
        self._vox_beat_thumbs = {}; self._vox_beat_audio = {}
        self.vox_btn_start.setEnabled(False); self.vox_btn_pause.setEnabled(True); self.vox_btn_view.setEnabled(False)
        self.vox_progress.setValue(0); self.vox_log.clear()
        self._vox_set_global("🔄 Đang chạy...", "#a5f3fc")
        self._vox_clear_preview()
        for step in self.vox_status_labels:
            self._vox_set_status(step, "⏳ Chờ", "#94a3b8")

        def _full_pipeline():
            try:
                vox_scripts = CURRENT_DIR.parent / "Remotion" / "vox-director" / "scripts"
                if str(vox_scripts) not in sys.path:
                    sys.path.insert(0, str(vox_scripts))

                doc = job_doc
                vox_dir = CURRENT_DIR.parent / "Remotion" / "vox-director"
                proj_dir = vox_dir / "out" / doc["project"]
                proj_dir.mkdir(parents=True, exist_ok=True)

                # ═══ STEP 1: GENERATE BEATS ═══
                self._vox_set_status("beats", "⏳ Đang tạo...", "#f59e0b")
                self._vox_log("═══════════════════════════════════════")
                self._vox_log("📋 BƯỚC 1/4: TẠO KỊCH BẢN BẰNG AI")
                self._vox_log("═══════════════════════════════════════")
                t0 = time.time()

                from cliproxy_provider import CLIProxyProvider
                prov = CLIProxyProvider()
                lang = doc["language"]; dur = job_duration
                theme = doc["theme"]; arc = doc["arc"]; aspect = doc["aspect"]

                llm_model = job_llm_model
                self._vox_log(f"  📡 Gọi LLM ({llm_model}) qua CLIProxy...")
                self._vox_log(f"  🎨 Theme: {theme} | Arc: {arc} | {dur}s | {aspect}")

                system_prompt = f"""You are a Vox-style video director. Create a beat map (JSON) for a {dur}-second paper-collage explainer video.
Topic: {topic}
Language: {lang} | Aspect: {aspect} | Theme: {theme} | Arc: {arc}

Rules:
- Create {"6-8" if dur <= 30 else "10-12"} beats. Beat-1 ≤3s hook.
- Each beat: id, title_en, bg (hex color), feel, narration (in {lang}), hook (bool).
- Each beat has 2 shots: a (WIDE, title=true) + b (CLOSE, title=false).
- Each shot: id, dur (3-6s), title (bool), shot_size, camera_move, scene, element_motion.
- camera_move: vary (static, push_in, pull_out, pan, tilt, parallax).
Output ONLY valid JSON. No commentary."""

                reply = prov.chat(llm_model, [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Create beat map for: {topic}"},
                ], max_tokens=8192)

                import re as _re
                json_match = _re.search(r'\{[\s\S]*\}', reply)
                if json_match:
                    beats_data = json.loads(json_match.group())
                    beats = beats_data.get("beats", [])
                else:
                    raise ValueError("AI không trả về JSON hợp lệ")

                doc["beats"] = beats
                beats_path = proj_dir / "beats.json"
                beats_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

                elapsed1 = round(time.time() - t0, 1)
                self._vox_log(f"  ✔ Tạo xong {len(beats)} beats ({elapsed1}s)")
                self._vox_set_status("beats", f"✔ {len(beats)} beats", "#16a34a")
                self._vox_progress_set_val(15)
                self._vox_set_preview_counter(f"{len(beats)} beats")

                # Show beats in preview
                for i, b in enumerate(beats):
                    self._vox_add_preview_beat(i, b)
                    title = b.get("title_en", "")
                    narr_preview = (b.get("narration", ""))[:50]
                    self._vox_log(f"  🎯 Beat {i+1}: {title} → \"{narr_preview}...\"")

                # Wait for pause
                while self._vox_paused and not self._vox_cancel:
                    time.sleep(0.3)
                if self._vox_cancel: raise InterruptedError("Đã huỷ")

                # ═══ STEP 2: KEYFRAMES ═══
                self._vox_set_status("keyframes", "⏳ Đang tạo ảnh...", "#f59e0b")
                self._vox_log("\n═══════════════════════════════════════")
                self._vox_log("🖼️ BƯỚC 2/4: TẠO KEYFRAME (ẢNH COLLAGE)")
                self._vox_log("═══════════════════════════════════════")
                t0 = time.time()

                n_codex = len(prov.acc_mgr.get_all_codex())
                n_workers = min(job_concurrent, n_codex) if n_codex > 0 else 1
                img_model = doc.get("image_model", "gpt-image-2")
                self._vox_log(f"  📡 Model: {img_model} qua CLIProxy")
                self._vox_log(f"  ⚡ {n_workers} luồng song song ({n_codex} tài khoản Codex)")

                # Collect all shots that need keyframes
                shot_tasks = []  # (beat_idx, shot, shot_key)
                for bi, beat in enumerate(beats):
                    shots = beat.get("shots", [])
                    if shots:
                        for s in shots:
                            key = f"{beat.get('id','')}{s.get('id','')}"
                            shot_tasks.append((bi, beat, s, key))
                    else:
                        shot_tasks.append((bi, beat, beat, str(beat.get('id',''))))

                total_shots = len(shot_tasks)
                self._vox_log(f"  🎯 Cần tạo {total_shots} keyframe...")

                kf_dir = proj_dir / "keyframes"
                kf_dir.mkdir(parents=True, exist_ok=True)

                # Import styling helpers
                from styles import compose_collage_prompt, compose_keyframe_prompt, resolve_theme, image_params

                theme_obj = resolve_theme(doc.get("theme")) or {}
                aspect = doc.get("aspect", "9:16")
                img_res = doc.get("image_resolution", "1k")
                style = doc.get("style", "collage")
                collage_style = theme_obj.get("idiom") or doc.get("collage_style", "american-retro")
                t_palette = theme_obj.get("palette") or doc.get("palette")
                t_type = theme_obj.get("type_style") or doc.get("type_style")
                t_finish = theme_obj.get("finish") or doc.get("finish")

                completed = [0]  # mutable counter for threads
                _lock = threading.Lock()

                def _gen_one_keyframe(task):
                    """Generate a single keyframe — runs in ThreadPoolExecutor."""
                    bi, beat, shot, key = task
                    try:
                        scene = shot.get("scene", "")
                        if style == "collage":
                            prompt = compose_collage_prompt(
                                scene, beat.get("title_cn",""), beat.get("title_en",""),
                                beat.get("bg", "warm ochre"), aspect,
                                with_title=shot.get("title", True),
                                style=collage_style, palette=t_palette,
                                type_style=t_type, finish=t_finish)
                        else:
                            prompt = compose_keyframe_prompt(
                                doc.get("era"), scene, beat.get("title_cn",""),
                                beat.get("title_en",""), aspect)

                        # Each thread creates its own provider (thread-safe account rotation)
                        from cliproxy_provider import CLIProxyProvider as _P
                        _prov = _P()
                        job_id = _prov.submit_image(img_model, prompt, **image_params(img_model, aspect, img_res))
                        status = _prov.get_status(job_id)

                        if status["status"] == "completed" and status["output"]:
                            dest = str(kf_dir / f"kf_{key}.jpg")
                            _prov.download(status["output"], dest)
                            shot["keyframe_url"] = status["output"]
                            shot["keyframe_path"] = dest
                            shot["keyframe_prompt"] = prompt

                            with _lock:
                                completed[0] += 1
                                n = completed[0]
                            pct = 15 + int(n / total_shots * 35)  # 15-50%
                            self._vox_log(f"  ✔ [{n}/{total_shots}] kf_{key}.jpg — {Path(dest).stat().st_size//1024}KB")
                            self._vox_progress_set_val(pct)
                            self._vox_update_preview_image(bi, dest)
                            return key, dest
                        else:
                            self._vox_log(f"  ❌ [{key}] Tạo ảnh thất bại")
                            return key, None
                    except Exception as e:
                        self._vox_log(f"  ❌ [{key}] {e}")
                        return key, None

                try:
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    with ThreadPoolExecutor(max_workers=n_workers) as pool:
                        futures = {pool.submit(_gen_one_keyframe, t): t for t in shot_tasks}
                        ok_count = 0
                        for fut in as_completed(futures):
                            result = fut.result()
                            if result and result[1]:
                                ok_count += 1

                    # Save updated beats.json
                    beats_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")

                    elapsed2 = round(time.time() - t0, 1)
                    self._vox_log(f"  ✔ Keyframes hoàn thành: {ok_count}/{total_shots} ảnh ({elapsed2}s)")
                    self._vox_set_status("keyframes", f"✔ {ok_count}/{total_shots} ({elapsed2}s)", "#16a34a")
                    self._vox_progress_set_val(50)
                except Exception as e:
                    self._vox_log(f"  ⚠️ Keyframes: {e}")
                    self._vox_set_status("keyframes", "⚠️ Lỗi", "#f59e0b")

                while self._vox_paused and not self._vox_cancel: time.sleep(0.3)
                if self._vox_cancel: raise InterruptedError("Đã huỷ")

                # ═══ STEP 3: AUDIO (VBEE TTS) ═══
                self._vox_set_status("audio", "⏳ Đang tạo giọng đọc...", "#f59e0b")
                self._vox_log("\n═══════════════════════════════════════")
                self._vox_log("🎙️ BƯỚC 3/4: TẠO GIỌNG ĐỌC (VBEE TTS)")
                self._vox_log("═══════════════════════════════════════")
                t0 = time.time()

                voice_code = doc["voice"].get("voice_code", "vi_south_female_miien_48k")
                self._vox_log(f"  🗣️ Voice: {voice_code} | Speed: {doc['voice'].get('speed', 1.0)}x")

                try:
                    import audio as audio_mod
                    audio_mod.run(str(proj_dir))
                    elapsed3 = round(time.time() - t0, 1)
                    self._vox_log(f"  ✔ Audio hoàn thành ({elapsed3}s)")
                    self._vox_set_status("audio", f"✔ Xong ({elapsed3}s)", "#16a34a")
                    self._vox_progress_set_val(75)

                    # Mark audio done in preview
                    for i in range(len(beats)):
                        self._vox_update_preview_audio(i, True)
                except Exception as e:
                    self._vox_log(f"  ⚠️ Audio: {e}")
                    self._vox_set_status("audio", "⚠️ Bỏ qua", "#f59e0b")

                while self._vox_paused and not self._vox_cancel: time.sleep(0.3)
                if self._vox_cancel: raise InterruptedError("Đã huỷ")

                # ═══ STEP 4: ASSEMBLE ═══
                self._vox_set_status("assemble", "⏳ Đang ghép video...", "#f59e0b")
                self._vox_log("\n═══════════════════════════════════════")
                self._vox_log("🔧 BƯỚC 4/4: GHÉP VIDEO (FFMPEG)")
                self._vox_log("═══════════════════════════════════════")
                t0 = time.time()

                try:
                    import assemble as asm_mod
                    asm_mod.run(str(proj_dir))
                    elapsed4 = round(time.time() - t0, 1)
                    self._vox_log(f"  ✔ Assemble hoàn thành ({elapsed4}s)")
                    self._vox_set_status("assemble", f"✔ Xong ({elapsed4}s)", "#16a34a")
                except Exception as e:
                    self._vox_log(f"  ⚠️ Assemble: {e}")
                    self._vox_set_status("assemble", "⚠️ Lỗi", "#f59e0b")

                self._vox_progress_set_val(100)

                # Check result
                final = proj_dir / "final.mp4"
                total_time = round(time.time() - self._vox_start_time, 1)
                if final.exists():
                    self._vox_log(f"\n🎉 ═══ HOÀN THÀNH! ═══")
                    self._vox_log(f"  📁 Video: {final}")
                    self._vox_log(f"  ⏱️ Tổng thời gian: {total_time}s")
                    self._vox_set_global("🎉 Hoàn thành!", "#4ade80")
                    self._vox_ui_call(lambda: self.vox_btn_view.setEnabled(True))
                else:
                    self._vox_log(f"\n⚠️ Pipeline kết thúc ({total_time}s) nhưng chưa có final.mp4")
                    self._vox_set_global("⚠️ Cần kiểm tra", "#fbbf24")

            except InterruptedError:
                self._vox_log("🛑 Pipeline đã bị huỷ.")
                self._vox_set_global("🛑 Đã huỷ", "#f87171")
            except Exception as e:
                self._vox_log(f"❌ Lỗi pipeline: {e}")
                self._vox_set_global("❌ Lỗi", "#f87171")
            finally:
                self._vox_running = False
                self._vox_ui_call(self._vox_elapsed_timer.stop)
                self._vox_ui_call(lambda: self.vox_btn_start.setEnabled(True))
                self._vox_ui_call(lambda: self.vox_btn_pause.setEnabled(False))

        self._vox_worker_thread = threading.Thread(target=_full_pipeline, daemon=True)
        self._vox_worker_thread.start()

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
        google_accs = [a for a in self.acc_mgr.accounts if _is_threads_google_account(a) and a.active]
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
        old_port = getattr(self, "veo_bridge_port", 4000)
        self.veo_bridge_port = self.sett_bridge_port.value()
        self.veo_jitter_min_ms = self.sett_jitter_min.value()
        self.veo_jitter_max_ms = self.sett_jitter_max.value()
        self.save_veo_config()
        if hasattr(self, 'sett_status_lbl'):
            self.sett_status_lbl.setText(f"✅ Đã lưu Veo config lúc {time.strftime('%H:%M:%S')}")
            
        if self.veo_bridge_port != old_port:
            if getattr(self, "captcha_bridge", None):
                try: 
                    self.captcha_bridge.stop()
                except: 
                    pass
                self.captcha_bridge = None
            self.start_captcha_bridge_if_needed()

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

    def _log_for_project(self, project_id, msg):
        log_line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        if not hasattr(self, 'project_logs'):
            self.project_logs = {}
        if project_id not in self.project_logs:
            self.project_logs[project_id] = []
        self.project_logs[project_id].append(log_line)
        if len(self.project_logs[project_id]) > 1000:
            self.project_logs[project_id] = self.project_logs[project_id][-1000:]
        
        try:
            log_file = Path(OUTPUT_DIR) / f"project_{project_id}.log"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_line + "\n")
        except:
            pass

        if self.active_project and self.active_project.id == project_id:
            if hasattr(self, "txt_log") and self.txt_log is not None:
                self.txt_log.append(log_line)
                self.txt_log.moveCursor(self.txt_log.textCursor().MoveOperation.End)

    @pyqtSlot(str)
    def _on_ui_log_bridge(self, msg):
        self._log_main_thread(msg)

    def _log(self, msg):
        """Log safely even when called by CaptchaBridge or a render thread."""
        if threading.get_ident() != self._qt_main_thread_ident:
            self._ui_log_queue.put(str(msg))
            return
        self._log_main_thread(msg)

    def _drain_runner_events_for(self, runner, project_id=None):
        """Consume one runner queue; this method is called only on the GUI thread."""
        if runner is None:
            return
        project_id = project_id or getattr(runner, "project_id", "")
        if not project_id:
            return
        try:
            events = runner.drain_gui_events()
        except Exception:
            return
        for event_type, payload in events:
            if event_type == "log":
                self._on_runner_log_bridge(project_id, payload)
            elif event_type == "product":
                self._on_product_updated_from_runner(project_id, payload)
            elif event_type == "done":
                ok, message = payload
                self._on_done(project_id, ok, message)

    def _drain_async_events(self):
        """Move background log/update events into Qt without cross-thread signals."""
        for _ in range(500):
            try:
                msg = self._ui_log_queue.get_nowait()
            except queue.Empty:
                break
            self._log_main_thread(msg)

        for runner in list(getattr(self, "runners", {}).values()):
            self._drain_runner_events_for(runner)

    def _log_main_thread(self, msg):
        if self.active_project:
            self._log_for_project(self.active_project.id, msg)
        else:
            if hasattr(self, "txt_log") and self.txt_log is not None:
                self.txt_log.append(f"[{time.strftime('%H:%M:%S')}] {msg}")
                self.txt_log.moveCursor(self.txt_log.textCursor().MoveOperation.End)
            else:
                print(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def _clear_layout(self, lay):
        if not lay: return
        while lay.count():
            it = lay.takeAt(0)
            if it:
                w = it.widget()
                if w:
                    if hasattr(w, "cleanup"):
                        try: w.cleanup()
                        except Exception: pass
                    w.setParent(None)
                    w.deleteLater()
                elif it.layout():
                    self._clear_layout(it.layout())

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

    # ── VOICE (Đa provider + danh sách thông minh) ────────────────────
    def _current_voice_engine(self) -> str:
        return self.cb_voice.currentData() or "gtts"

    def _norm_cached_voices(self, raw) -> list:
        """Chuẩn hoá cache voice về list 4-tuple (id, label, gender, vtype).
        Tương thích ngược với cache cũ dạng (code, label)."""
        out = []
        for it in (raw or []):
            if isinstance(it, (list, tuple)):
                if len(it) >= 4:
                    out.append((it[0], it[1], it[2], it[3]))
                elif len(it) == 2:
                    out.append((it[0], it[1], "", ""))
        return out

    def _voice_cache_key(self, engine: str) -> str:
        return "vbee_voices_cached" if engine == "vbee" else f"voices_cache_{engine}"

    def _on_voice_changed(self, *args):
        """Đổi engine: hiện credential đúng provider + nạp lại danh sách voice."""
        engine = self._current_voice_engine()
        self.w_cred_vbee.setVisible(engine == "vbee")
        self.w_cred_resona.setVisible(engine == "resona")
        self.w_cred_el.setVisible(engine == "elevenlabs_idvn")
        self.w_cred_revid.setVisible(engine == "revidapi")
        is_provider = engine in TTS_MULTIVOICE_ENGINES
        self.w_voice_select.setVisible(is_provider)
        self.btn_fetch_voices.setVisible(is_provider)
        if is_provider:
            self._reload_voice_widgets()

    def _reload_voice_widgets(self):
        """Nạp danh sách voice của engine hiện tại từ cache (hoặc mặc định) rồi lọc."""
        engine = self._current_voice_engine()
        cached = self._norm_cached_voices(self.settings.get(self._voice_cache_key(engine), []))
        # RevidAPI luôn có danh sách tĩnh; các provider khác nếu chưa fetch thì để trống
        if not cached and engine == "revidapi":
            cached = revidapi_get_voices()
        self._voice_all = cached
        # Xây lại combo "Loại voice" từ các vtype có trong danh sách
        types = []
        for _id, _lb, _g, vt in self._voice_all:
            if vt and vt not in types:
                types.append(vt)
        self.cb_voice_type.blockSignals(True)
        self.cb_voice_type.clear()
        self.cb_voice_type.addItem("Tất cả", "")
        for vt in types:
            self.cb_voice_type.addItem(vt, vt)
        self.cb_voice_type.blockSignals(False)
        self._apply_voice_filter()

    def _apply_voice_filter(self, *args):
        """Lọc danh sách voice theo giới tính + loại + từ khoá; cập nhật combo chính & list phụ."""
        if not hasattr(self, "cb_vbee_v"):
            return
        want_g = self.cb_voice_gender.currentData() or ""
        want_t = self.cb_voice_type.currentData() or ""
        kw = self.txt_voice_search.text().strip().lower()
        filtered = []
        for vid, label, g, vt in self._voice_all:
            if want_g and g != want_g:
                continue
            if want_t and vt != want_t:
                continue
            if kw and kw not in f"{label} {vid}".lower():
                continue
            filtered.append((vid, label, g, vt))

        # Combo voice chính — giữ lựa chọn hiện tại nếu vẫn còn
        cur = self.cb_vbee_v.currentData() or self.settings.get("vbee_voice", "")
        self.cb_vbee_v.blockSignals(True)
        self.cb_vbee_v.clear()
        for vid, label, g, vt in filtered:
            self.cb_vbee_v.addItem(label, vid)
        if cur:
            i = self.cb_vbee_v.findData(cur)
            if i >= 0:
                self.cb_vbee_v.setCurrentIndex(i)
            else:
                self.cb_vbee_v.setEditText(cur)
        self.cb_vbee_v.blockSignals(False)

        # List voice phụ — check theo self._sub_selected (bền qua lúc lọc)
        self.list_sub_voices.blockSignals(True)
        self.list_sub_voices.clear()
        for vid, label, g, vt in filtered:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, vid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if vid in self._sub_selected else Qt.CheckState.Unchecked)
            self.list_sub_voices.addItem(item)
        self.list_sub_voices.blockSignals(False)
        n = len(filtered)
        self.lbl_sub_voices.setText(f"👥 Voice Phụ (Đọc CMT, Random) — {n} giọng:")

    def _on_sub_voice_toggled(self, item):
        vid = item.data(Qt.ItemDataRole.UserRole)
        if not vid:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self._sub_selected.add(vid)
        else:
            self._sub_selected.discard(vid)
        try:
            self._save_settings(self._collect_settings())
        except:
            pass

    def _fetch_voices(self):
        """Tải danh sách voice cho provider đang chọn."""
        engine = self._current_voice_engine()
        if engine not in TTS_MULTIVOICE_ENGINES:
            return
        st = self._collect_settings()  # để lấy credential mới nhất
        self.btn_fetch_voices.setEnabled(False)
        self.btn_fetch_voices.setText("⏳ Đang tải...")
        QApplication.processEvents()
        try:
            voices = tts_get_voices(engine, st)
        except Exception as e:
            voices = []
            self._log(f"❌ Lỗi tải voice: {e}")
        self.btn_fetch_voices.setEnabled(True)
        self.btn_fetch_voices.setText("🔄 Tải danh sách Voice")
        if voices:
            self.settings[self._voice_cache_key(engine)] = [list(v) for v in voices]
            self._save_settings(self._collect_settings())
            self._reload_voice_widgets()
            QMessageBox.information(self, "OK", f"Đã tải {len(voices)} giọng {dict(TTS_ENGINES).get(engine, engine)}.")
        else:
            QMessageBox.warning(self, "Không có voice",
                                "Không tải được danh sách voice. Kiểm tra lại API Key/Token,\n"
                                "hoặc nhập trực tiếp Voice ID vào ô Voice Chính.")

    def _toggle_all_sub_voices(self, select: bool):
        """Chọn/bỏ chọn toàn bộ voice phụ đang hiển thị."""
        self.list_sub_voices.blockSignals(True)
        for i in range(self.list_sub_voices.count()):
            item = self.list_sub_voices.item(i)
            vid = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(Qt.CheckState.Checked if select else Qt.CheckState.Unchecked)
            if select:
                self._sub_selected.add(vid)
            else:
                self._sub_selected.discard(vid)
        self.list_sub_voices.blockSignals(False)
        try:
            self._save_settings(self._collect_settings())
        except:
            pass

    def _get_selected_sub_voices(self) -> list:
        """Trả về danh sách ID voice phụ đã chọn (kể cả voice hiện bị ẩn do lọc)."""
        return list(self._sub_selected)

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
            p = ThreadsProject(name=n.strip()); self.projects.append(p)
            self._project_output_dir(p)
            self._sort_projects()  # đưa dự án mới nhất lên đầu
            self._save_projects()
            self._refresh_sidebar_projects()
            self._open_project(p)

    def _project_output_dir(self, proj):
        """Return/create the project root used for all rendered product data."""
        out_base = Path(self.settings.get("output_dir", str(OUTPUT_DIR)))
        clean_name = re.sub(r'[\\/*?:"<>|]', "", (proj.name or "du_an")).strip().strip(".")[:100]
        project_dir = out_base / (clean_name or "du_an")
        project_dir.mkdir(parents=True, exist_ok=True)
        return project_dir

    @staticmethod
    def _runner_key(project_id, product_id=None):
        """Full-project runs use the project key; single-product runs use a scoped key."""
        return project_id if not product_id else f"{project_id}:{product_id}"

    def _project_has_running_runner(self, project_id):
        prefix = f"{project_id}:"
        for key, runner in getattr(self, "runners", {}).items():
            if key == project_id or key.startswith(prefix):
                try:
                    if runner.isRunning():
                        return True
                except RuntimeError:
                    pass
        return False

    def _project_full_runner_running(self, project_id):
        runner = getattr(self, "runners", {}).get(project_id)
        try:
            return bool(runner and runner.isRunning())
        except RuntimeError:
            return False

    def _sync_project_run_controls(self, project_id):
        if not self.active_project or self.active_project.id != project_id:
            return
        any_running = self._project_has_running_runner(project_id)
        full_running = self._project_full_runner_running(project_id)
        self.btn_start.setEnabled(not any_running)
        if hasattr(self, "btn_render_all"):
            self.btn_render_all.setEnabled(not any_running)
        if hasattr(self, "btn_pause"):
            self.btn_pause.setEnabled(full_running)

    def _clone_project(self, source_project):
        """Clone product inputs only; generated scripts/media/output paths are reset."""
        default_name = f"{source_project.name} - Bản sao"
        name, ok = QInputDialog.getText(self, "Nhân bản dự án", "Tên dự án mới:", text=default_name)
        if not ok or not name.strip():
            return

        cloned_project = ThreadsProject(name=name.strip())
        project_root = self._project_output_dir(cloned_project)
        for source in source_project.products:
            cloned = ProductItem(
                title=source.title,
                info=source.info,
                context=source.context,
                use_ref_for_generation=source.use_ref_for_generation,
            )

            # Copy user-supplied reference images into the cloned project's
            # product folder.  Generated scenes/media are deliberately not copied.
            clean_title = re.sub(r'[\\/*?:"<>|]', "", (source.title or "san_pham")).strip().strip(".")[:100]
            product_root = project_root / f"{clean_title or 'san_pham'}_{cloned.id}"
            ref_dir = product_root / "references"
            ref_dir.mkdir(parents=True, exist_ok=True)
            cloned.images = []
            for ref_idx, source_path in enumerate(source.images):
                src_path = Path(source_path) if source_path else None
                if src_path and src_path.exists() and src_path.is_file():
                    suffix = src_path.suffix.lower() or ".png"
                    copied_path = ref_dir / f"ref_{ref_idx + 1}{suffix}"
                    try:
                        shutil.copy2(src_path, copied_path)
                        cloned.images.append(str(copied_path))
                    except Exception:
                        cloned.images.append(str(src_path))
                elif source_path:
                    cloned.images.append(str(source_path))

            # An external video is an input file, not generated output. Preserve it;
            # all AI scripts, scenes, voices and rendered paths stay empty.
            if source.video_review_mode == "external_file":
                cloned.video_review_mode = "external_file"
                cloned.external_video_path = source.external_video_path
            cloned_project.products.append(cloned)

        self.projects.append(cloned_project)
        self._sort_projects()
        self._save_projects()
        self._refresh_dashboard()
        self._refresh_sidebar_projects()
        self._open_project(cloned_project)

    def _open_project(self, proj):
        self.active_project = proj
        pid = proj.id
        # Keep the active project root available to product-level tools while
        # retaining the global output setting for all other features.
        self.settings["_active_project_output_dir"] = str(self._project_output_dir(proj))
        self.sb_proj_lbl.setText(f"📁 {proj.name}"); self.proj_nav_w.setVisible(True)
        self.studio_proj_lbl.setText(f"🎭  Role Skill Pipeline — {proj.name}")
        
        # Load and display the logs of this project
        if hasattr(self, "txt_log") and self.txt_log is not None:
            self.txt_log.clear()
            if not hasattr(self, 'project_logs'):
                self.project_logs = {}
            if pid in self.project_logs:
                self.txt_log.setPlainText("\n".join(self.project_logs[pid]))
            else:
                log_file = Path(OUTPUT_DIR) / f"project_{pid}.log"
                if log_file.exists():
                    try:
                        lines = log_file.read_text("utf-8").splitlines()
                        last_lines = lines[-500:]
                        self.project_logs[pid] = last_lines
                        self.txt_log.setPlainText("\n".join(last_lines))
                    except:
                        pass
            self.txt_log.moveCursor(self.txt_log.textCursor().MoveOperation.End)

        # Update Start/Pause button states for this project
        self._sync_project_run_controls(pid)

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

    def _request_render_products(self, product_id=None):
        """Yêu cầu render lại danh sách SP, gộp các yêu cầu dồn dập thành 1 lần
        (dùng cho tín hiệu từ worker pipeline để UI không bị giật)."""
        if product_id:
            self._pending_product_refresh_ids.add(str(product_id))
        else:
            self._pending_full_product_refresh = True
        if not self._render_timer.isActive():
            self._render_timer.start(200)

    def _flush_product_updates(self):
        full_refresh = self._pending_full_product_refresh
        product_ids = set(self._pending_product_refresh_ids)
        self._pending_full_product_refresh = False
        self._pending_product_refresh_ids.clear()
        self._render_products(None if full_refresh else product_ids)

    def _render_products(self, target_product_ids=None):
        if not hasattr(self, "product_cards"):
            self.product_cards = {}

        if not self.active_project:
            self._clear_layout(self.prod_lay)
            self.product_cards = {}
            return

        # Keep only wrappers whose underlying C++ widget is still alive.
        # Gọi parent() trên wrapper đã bị Qt xoá sẽ ném RuntimeError và có thể
        # làm vỡ cả lượt refresh UI.
        live_cards = {}
        for product_id, card in self.product_cards.items():
            try:
                if card is not None and card.parent() is not None:
                    live_cards[product_id] = card
            except RuntimeError:
                pass
        self.product_cards = live_cards

        existing_ids = list(self.product_cards.keys())
        active_ids = [p.id for p in self.active_project.products]

        # Tắt repaint trong lúc dựng/cập nhật hàng loạt để tránh nháy + giật
        self.prod_scroll.setUpdatesEnabled(False)
        try:
            if set(existing_ids) != set(active_ids) or len(existing_ids) != len(active_ids):
                self._clear_layout(self.prod_lay)
                self.product_cards = {}
                for i, prod in enumerate(self.active_project.products):
                    card = ThreadsProductCard(i, prod, self)
                    self.prod_lay.addWidget(card)
                    self.product_cards[prod.id] = card
            else:
                for i, prod in enumerate(self.active_project.products):
                    if target_product_ids is not None and prod.id not in target_product_ids:
                        continue
                    if prod.id in self.product_cards:
                        c = self.product_cards[prod.id]
                        c.product = prod
                        c.load_scenes()
                        c.load_vr_scenes()
                        c.adjust_height()
                        c.update_status_ui()
        finally:
            self.prod_scroll.setUpdatesEnabled(True)

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
            "scene_count":self.sp_scenes.value(),
            "voice_engine": (self.cb_voice.currentData() or "gtts") if hasattr(self, 'cb_voice') else "gtts",
            "vbee_token":self.txt_vbee_tok.text().strip(),"vbee_appid":self.txt_vbee_aid.text().strip(),
            "resona_key": self.txt_resona_key.text().strip() if hasattr(self, 'txt_resona_key') else "",
            "el_idvn_key": self.txt_el_idvn_key.text().strip() if hasattr(self, 'txt_el_idvn_key') else "",
            "revidapi_key": self.txt_revidapi_key.text().strip() if hasattr(self, 'txt_revidapi_key') else "",
            "vbee_voice":self.cb_vbee_v.currentData() or self.cb_vbee_v.currentText(),"vbee_speed":self.sp_vbee_spd.value(),
            "vbee_sub_voices": sub_voices, "vbee_sub_voices_display": sub_voices,
            "bg_folder":self.txt_bg.text().strip(),"no_duplicate_bg":self.chk_nodup.isChecked(),
            "fake_metadata":self.chk_meta.isChecked(),"anti_flip_h":self.chk_fh.isChecked(),"anti_noise":self.chk_noise.isChecked(),
            "delay_sec":self.sp_delay.value(),"retry_count":self.sp_retry.value(),
            "concurrency": self.sp_concurrency.value() if hasattr(self, 'sp_concurrency') else 8,
            "burst_size": self.sp_burst_size.value() if hasattr(self, 'sp_burst_size') else 4,
            "burst_delay": self.sp_burst_delay.value() if hasattr(self, 'sp_burst_delay') else 10,
            "upscale_threads": self.sp_upscale_threads.value() if hasattr(self, 'sp_upscale_threads') else 1,
            "llm_threads": self.sp_llm_threads.value() if hasattr(self, 'sp_llm_threads') else 3,
            "img_threads": self.sp_img_threads.value() if hasattr(self, 'sp_img_threads') else 5,
            "vid_threads": self.sp_vid_threads.value() if hasattr(self, 'sp_vid_threads') else 3,
            "pipeline_delay": self.sp_pipeline_delay.value() if hasattr(self, 'sp_pipeline_delay') else 5,
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
            "vr_auto_speed": self.chk_vr_auto_speed.isChecked() if hasattr(self, 'chk_vr_auto_speed') else True,
            "vr_max_duration": self.sp_vr_max_dur.value() if hasattr(self, 'sp_vr_max_dur') else 30,
            "vr_zoom_custom": self.chk_vr_zoom_custom.isChecked() if hasattr(self, 'chk_vr_zoom_custom') else False,
            "vr_zoom_factor": self.sp_vr_zoom_factor.value() if hasattr(self, 'sp_vr_zoom_factor') else 100.0,
            "vr_overlay_bg": self.chk_vr_overlay.isChecked() if hasattr(self, 'chk_vr_overlay') else False,
            "vr_overlay_scale": self.sp_vr_overlay_scale.value() if hasattr(self, 'sp_vr_overlay_scale') else 85,
            "vr_keep_audio": self.chk_vr_keep_audio.isChecked() if hasattr(self, 'chk_vr_keep_audio') else True,
            "vr_flip_h": self.chk_vr_flip.isChecked() if hasattr(self, 'chk_vr_flip') else False,
            "vr_noise": self.chk_vr_noise.isChecked() if hasattr(self, 'chk_vr_noise') else True,
            "vr_random_cut": self.chk_vr_rcut.isChecked() if hasattr(self, 'chk_vr_rcut') else True,
            "vr_rcut_interval_min": self.sp_vr_rcut_min.value() if hasattr(self, 'sp_vr_rcut_min') else 3,
            "vr_rcut_interval_max": self.sp_vr_rcut_max.value() if hasattr(self, 'sp_vr_rcut_max') else 5,
            "output_dir": self.settings.get("output_dir", str(OUTPUT_DIR)),
            "project_output_dir": self.settings.get("_active_project_output_dir", ""),
        }

    def _find_project_id_for_product(self, prod):
        if not hasattr(self, 'projects') or not self.projects:
            return self.active_project.id if self.active_project else None
        for p in self.projects:
            if prod in p.products:
                return p.id
        return self.active_project.id if self.active_project else None

    def _start_pipeline(self):
        if not self.active_project or not self.active_project.products:
            QMessageBox.warning(self,"Cảnh Báo","Chưa có SP!"); return
        pid = self.active_project.id
        if not hasattr(self, 'runners'):
            self.runners = {}
        if self._project_has_running_runner(pid):
            QMessageBox.warning(self, "Cảnh Báo", "Dự án này đang chạy rồi!"); return

        self.start_proxy(); st = self._collect_settings(); self._save_settings(st)
        st["project_output_dir"] = str(self._project_output_dir(self.active_project))
        self.btn_start.setEnabled(False)
        if hasattr(self, "btn_pause"):
            self.btn_pause.setEnabled(True)

        self._log_for_project(pid, f"🚀 Bắt đầu [{st['selected_role']}]...")
        
        runner = FullPipelineRunnerWorker(self.active_project.products, st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        self.runners[pid] = runner

        self._wire_runner_signals(runner, pid)
        runner.start()

    def _pause_pipeline(self):
        if not self.active_project:
            return
        pid = self.active_project.id
        if not hasattr(self, 'runners'):
            self.runners = {}
        if pid in self.runners and self.runners[pid].isRunning():
            self.runners[pid]._stopped = True
            self._log_for_project(pid, "⏸️ Yêu cầu tạm dừng luồng dự án.")

    def _run_single_full(self, prod):
        """Reset one product's generated state and run LLM → media → video."""
        pid = self._find_project_id_for_product(prod)
        if not pid:
            return
        if not hasattr(self, 'runners'):
            self.runners = {}
        runner_key = self._runner_key(pid, prod.id)
        if self._project_full_runner_running(pid) or (runner_key in self.runners and self.runners[runner_key].isRunning()):
            QMessageBox.warning(self, "Cảnh Báo", "Sản phẩm hoặc toàn bộ dự án này đang chạy rồi!")
            return

        confirm = QMessageBox.question(
            self,
            "Xác nhận Render lại toàn bộ",
            f"Sẽ tạo lại từ đầu LLM, Ảnh, Voice và Video cho:\n\n{prod.title}\n\nTiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        project = next((p for p in self.projects if p.id == pid), self.active_project)
        if not project:
            return

        # Clear only generated state. Product inputs and user reference images
        # remain untouched; the worker will create fresh outputs in the project
        # product folder.
        prod.scenes.clear()
        prod.voiceover_text = ""
        prod.voice_path = ""
        prod.voice_duration = 0.0
        prod.final_video_path = ""
        prod.status = "Chưa chạy"
        prod.video_review_scenes.clear()
        prod.video_review_voice_path = ""
        prod.video_review_voice_duration = 0.0
        prod.final_combined_path = ""
        self._save_projects()
        self._request_render_products()

        self.start_proxy()
        st = self._collect_settings()
        st["project_output_dir"] = str(self._project_output_dir(project))
        st["only_video_assembly"] = False
        st["only_rerender_images"] = False
        st["re_video_review"] = True

        if self.active_project and self.active_project.id == pid:
            self.btn_start.setEnabled(False)
            if hasattr(self, "btn_pause"):
                self.btn_pause.setEnabled(False)

        self._log_for_project(pid, f"⚡ Render lại toàn bộ sản phẩm: {prod.title[:40]}...")
        runner = FullPipelineRunnerWorker([prod], st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        runner.product_id = prod.id
        self.runners[runner_key] = runner
        self._wire_runner_signals(runner, pid, runner_key)
        runner.start()

    def _run_single(self, prod):
        pid = self._find_project_id_for_product(prod)
        if not pid:
            return
        if not hasattr(self, 'runners'):
            self.runners = {}
        runner_key = self._runner_key(pid, prod.id)
        if self._project_full_runner_running(pid) or (runner_key in self.runners and self.runners[runner_key].isRunning()):
            QMessageBox.warning(self, "Cảnh Báo", "Dự án này đang chạy rồi!"); return

        self.start_proxy(); st = self._collect_settings()
        project = next((p for p in self.projects if p.id == pid), self.active_project)
        if project:
            st["project_output_dir"] = str(self._project_output_dir(project))
        
        if self.active_project and self.active_project.id == pid:
            self.btn_start.setEnabled(False)
            if hasattr(self, "btn_pause"):
                self.btn_pause.setEnabled(True)

        self._log_for_project(pid, f"🚀 Bắt đầu chạy sản phẩm đơn lẻ...")

        runner = FullPipelineRunnerWorker([prod], st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        runner.product_id = prod.id
        self.runners[runner_key] = runner

        self._wire_runner_signals(runner, pid, runner_key)
        runner.start()

    def _run_single_video_only(self, prod):
        pid = self._find_project_id_for_product(prod)
        if not pid:
            return
        if not hasattr(self, 'runners'):
            self.runners = {}
        runner_key = self._runner_key(pid, prod.id)
        if self._project_full_runner_running(pid) or (runner_key in self.runners and self.runners[runner_key].isRunning()):
            QMessageBox.warning(self, "Cảnh Báo", "Dự án này đang chạy rồi!"); return

        self.start_proxy(); st = self._collect_settings()
        project = next((p for p in self.projects if p.id == pid), self.active_project)
        if project:
            st["project_output_dir"] = str(self._project_output_dir(project))
        st["only_video_assembly"] = True

        if self.active_project and self.active_project.id == pid:
            self.btn_start.setEnabled(False)
            if hasattr(self, "btn_pause"):
                self.btn_pause.setEnabled(True)

        self._log_for_project(pid, f"🚀 Bắt đầu chỉ ghép video sản phẩm...")

        runner = FullPipelineRunnerWorker([prod], st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        runner.product_id = prod.id
        self.runners[runner_key] = runner

        self._wire_runner_signals(runner, pid, runner_key)
        runner.start()

    def _run_all_full(self):
        """Reset and render every product in the active project from LLM to final video."""
        project = self.active_project
        if not project or not project.products:
            QMessageBox.warning(self, "Chưa có sản phẩm", "Chưa có sản phẩm trong dự án.")
            return

        pid = project.id
        if not hasattr(self, "runners"):
            self.runners = {}
        if self._project_has_running_runner(pid):
            QMessageBox.warning(self, "Cảnh báo", "Dự án này đang chạy rồi!")
            return

        confirm = QMessageBox.question(
            self,
            "Xác nhận Render toàn bộ",
            f"Sẽ xóa kết quả đã tạo và render lại từ LLM, Ảnh, Voice đến Video cho {len(project.products)} sản phẩm.\n\nTiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        # Reset generated data only; keep product metadata and user reference images.
        for prod in project.products:
            prod.scenes.clear()
            prod.voiceover_text = ""
            prod.voice_path = ""
            prod.voice_duration = 0.0
            prod.final_video_path = ""
            prod.status = "Chưa chạy"
            prod.video_review_scenes.clear()
            prod.video_review_voice_path = ""
            prod.video_review_voice_duration = 0.0
            prod.final_combined_path = ""

        self._save_projects()
        self._request_render_products()
        self.start_proxy()

        st = self._collect_settings()
        st["project_output_dir"] = str(self._project_output_dir(project))
        st["only_video_assembly"] = False
        st["only_rerender_images"] = False
        st["re_video_review"] = True

        self.btn_start.setEnabled(False)
        self.btn_render_all.setEnabled(False)
        if hasattr(self, "btn_pause"):
            self.btn_pause.setEnabled(True)

        self._log_for_project(pid, f"⚡ Render toàn bộ dự án: {len(project.products)} sản phẩm...")
        runner = FullPipelineRunnerWorker(
            project.products,
            st,
            self.used_bg,
            getattr(self, "captcha_bridge", None),
            self.acc_mgr,
        )
        runner.project_id = pid
        self.runners[pid] = runner
        self._wire_runner_signals(runner, pid)
        runner.start()

    def _run_single_selective(self, prod, opts: dict):
        """Run pipeline selectively based on ReRenderDialog options."""
        pid = self._find_project_id_for_product(prod)
        if not pid:
            return
        if not hasattr(self, 'runners'):
            self.runners = {}
        runner_key = self._runner_key(pid, prod.id)
        if self._project_full_runner_running(pid) or (runner_key in self.runners and self.runners[runner_key].isRunning()):
            QMessageBox.warning(self, "Cảnh Báo", "Dự án này đang chạy rồi!"); return

        self.start_proxy()
        st = self._collect_settings()
        project = next((p for p in self.projects if p.id == pid), self.active_project)
        if project:
            st["project_output_dir"] = str(self._project_output_dir(project))
        
        # Apply overrides from dialog
        if opts.get("re_script"):
            if opts.get("re_llm_model"):
                st["llm_model"] = opts["re_llm_model"]
        
        if opts.get("re_voice"):
            st["voice_engine"] = opts.get("re_voice_engine", st.get("voice_engine"))
            if opts.get("re_main_voice"):
                st["vbee_voice"] = opts["re_main_voice"]
            if opts.get("re_sub_voices") is not None:
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
            self._log_for_project(pid, f"🗑️ Xóa kịch bản cũ ({len(prod.scenes)} scenes) để tạo lại...")
            prod.scenes.clear()
            prod.voiceover_text = ""
        
        if opts.get("re_voice"):
            self._log_for_project(pid, "🗑️ Xóa voice cũ để tạo lại...")
            if prod.voice_path and Path(prod.voice_path).exists():
                try: Path(prod.voice_path).unlink()
                except: pass
            prod.voice_path = ""
            prod.voice_duration = 0.0
        
        if opts.get("re_images"):
            self._log_for_project(pid, "🗑️ Xóa ảnh cũ để render lại...")
            for sc in prod.scenes:
                if sc.image_path and Path(sc.image_path).exists():
                    try: Path(sc.image_path).unlink()
                    except: pass
                sc.image_path = ""
                sc.status = "pending"
                
        if opts.get("re_video_review"):
            self._log_for_project(pid, "🗑️ Xóa video review cũ để render lại...")
            if prod.final_combined_path and Path(prod.final_combined_path).exists():
                try: Path(prod.final_combined_path).unlink()
                except: pass
            prod.final_combined_path = ""
            prod.video_review_scenes.clear()
        
        self._save_projects()
        
        if self.active_project and self.active_project.id == pid:
            self.btn_start.setEnabled(False)
            if hasattr(self, "btn_pause"):
                self.btn_pause.setEnabled(True)

        runner = FullPipelineRunnerWorker([prod], st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        runner.product_id = prod.id
        self.runners[runner_key] = runner

        self._wire_runner_signals(runner, pid, runner_key)
        runner.start()

    def _rerender_all_images(self):
        """Re-render images for ALL products using reference images."""
        if not self.active_project or not self.active_project.products:
            QMessageBox.warning(self, "Chưa có SP", "Chưa có sản phẩm trong dự án.")
            return
        
        pid = self.active_project.id
        if not hasattr(self, 'runners'):
            self.runners = {}
        if self._project_has_running_runner(pid):
            QMessageBox.warning(self, "Cảnh Báo", "Dự án này đang chạy rồi!"); return

        products_with_scenes = [p for p in self.active_project.products if p.scenes]
        if not products_with_scenes:
            QMessageBox.warning(self, "Chưa có kịch bản", "Các sản phẩm chưa có kịch bản (scenes). Hãy chạy pipeline đầy đủ trước.")
            return
        
        ret = QMessageBox.question(
            self, "Xác nhận Render lại Ảnh",
            f"Sẽ XÓA toàn bộ ảnh cũ và sinh lại {sum(len(p.scenes) for p in products_with_scenes)} ảnh "
            f"cho {len(products_with_scenes)} sản phẩm.\n\n"
            f"Ảnh mới sẽ dùng ẢNH THAM CHIẾU sản phẩm (nếu có).\n"
            f"Giữ nguyên: Kịch bản, Voice, Video.\n\nTiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        
        # Clear all existing images
        for prod in products_with_scenes:
            self._log_for_project(pid, f"🗑️ Xóa {len(prod.scenes)} ảnh cũ: {prod.title[:30]}...")
            for sc in prod.scenes:
                if sc.image_path and Path(sc.image_path).exists():
                    try: Path(sc.image_path).unlink()
                    except: pass
                sc.image_path = ""
                sc.status = "pending"
        
        self._save_projects()
        self._request_render_products()
        
        # Run pipeline — only_images mode
        self.start_proxy()
        st = self._collect_settings()
        st["only_rerender_images"] = True  # New flag: skip LLM, skip voice, only images
        
        self.btn_start.setEnabled(False)
        if hasattr(self, "btn_pause"):
            self.btn_pause.setEnabled(True)

        runner = FullPipelineRunnerWorker(products_with_scenes, st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        st["project_output_dir"] = str(self._project_output_dir(self.active_project))
        self.runners[pid] = runner

        self._wire_runner_signals(runner, pid)
        runner.start()

    def _run_all_video_only(self):
        """Run video assembly only for all products."""
        if not self.active_project or not self.active_project.products:
            QMessageBox.warning(self, "Chưa có SP", "Chưa có sản phẩm trong dự án.")
            return
        
        pid = self.active_project.id
        if not hasattr(self, 'runners'):
            self.runners = {}
        if self._project_has_running_runner(pid):
            QMessageBox.warning(self, "Cảnh Báo", "Dự án này đang chạy rồi!"); return

        self.start_proxy()
        st = self._collect_settings()
        st["only_video_assembly"] = True
        st["project_output_dir"] = str(self._project_output_dir(self.active_project))
        
        self.btn_start.setEnabled(False)
        if hasattr(self, "btn_pause"):
            self.btn_pause.setEnabled(True)

        runner = FullPipelineRunnerWorker(self.active_project.products, st, self.used_bg, getattr(self, "captcha_bridge", None), self.acc_mgr)
        runner.project_id = pid
        self.runners[pid] = runner

        self._wire_runner_signals(runner, pid)
        runner.start()

    def _wire_runner_signals(self, runner, project_id, runner_key=None):
        """Route runner events through main Qt thread before touching any UI."""
        runner_key = runner_key or project_id
        runner.log_signal.connect(
            lambda msg, p_id=project_id: self.runner_log_bridge_sig.emit(p_id, msg)
        )
        runner.product_updated_signal.connect(
            lambda _, p_id=project_id: self.runner_product_bridge_sig.emit(p_id)
        )
        runner.finished_signal.connect(
            lambda ok, msg, p_id=project_id: self.runner_done_bridge_sig.emit(p_id, ok, msg)
        )
        runner.finished.connect(
            lambda r_key=runner_key, r=runner: self.runner_cleanup_bridge_sig.emit(r_key, r)
        )

    @pyqtSlot(str, str)
    def _on_runner_log_bridge(self, project_id, msg):
        self._log_for_project(project_id, msg)

    @pyqtSlot(str)
    def _on_runner_product_bridge(self, project_id):
        self._on_product_updated_from_runner(project_id)

    @pyqtSlot(str, bool, str)
    def _on_runner_done_bridge(self, project_id, ok, msg):
        self._on_done(project_id, ok, msg)

    @pyqtSlot(str, object)
    def _on_runner_cleanup_bridge(self, project_id, runner):
        self._cleanup_runner(project_id, runner)

    def _on_product_updated_from_runner(self, project_id, product_id=None):
        self._save_projects()
        if self.active_project and self.active_project.id == project_id:
            self._request_render_products(product_id)

    def _on_done(self, project_id, ok, msg):
        self._log_for_project(project_id, msg)
        
        if self.active_project and self.active_project.id == project_id:
            self._sync_project_run_controls(project_id)
            self._save_projects()
            self._render_products()
            if ok:
                QMessageBox.information(self, "Hoàn Thành", msg)
        else:
            self._save_projects()

    def _cleanup_runner(self, runner_key, runner):
        """Chỉ giải phóng QThread sau khi tín hiệu QThread.finished đã phát."""
        self._drain_runner_events_for(runner)
        if getattr(self, "runners", {}).get(runner_key) is runner:
            self.runners.pop(runner_key, None)
        project_id = getattr(runner, "project_id", "")
        if project_id:
            self._sync_project_run_controls(project_id)
        runner.deleteLater()

    def _load_archetypes_into_combo(self, combo, is_global=False):
        combo.clear()
        if is_global:
            combo.addItem("Chọn theo Phong cách dưới từng sản phẩm", "Chọn theo Phong cách dưới từng sản phẩm")
            combo.addItem("Random Phong cách", "Random Phong cách")
            
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
        self._load_archetypes_into_combo(self.cb_ctx, is_global=True)
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
    @staticmethod
    def _proj_sort_key(proj):
        """Ngày tạo dự án → datetime để sắp xếp; dự án thiếu ngày xếp cuối."""
        for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
            try:
                return datetime.strptime((proj.created_at or "").strip(), fmt)
            except ValueError:
                continue
        return datetime.min

    def _sort_projects(self):
        """Sắp xếp danh sách dự án: mới nhất lên trước.
        Tie-break bằng thứ tự thêm vào (dự án thêm sau được ưu tiên)."""
        indexed = list(enumerate(self.projects))
        indexed.sort(key=lambda t: (self._proj_sort_key(t[1]), t[0]), reverse=True)
        self.projects = [p for _, p in indexed]

    def _load_projects(self):
        if PROJECTS_FILE.exists():
            try: self.projects = [ThreadsProject.from_dict(p) for p in json.loads(PROJECTS_FILE.read_text("utf-8"))]
            except: self.projects = []
        self._sort_projects()
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
                # Giữ lại các cache voice đã fetch (vbee + provider mới)
                for k, v in self.settings.items():
                    if k == "vbee_voices_cached" or k.startswith("voices_cache_"):
                        st[k] = v
            self.settings = st
            # Project output roots are runtime context, not global settings.
            # Keep them in memory for the active project-level tools, but do not
            # let the last opened project redirect a future project on startup.
            persisted = dict(st)
            persisted.pop("project_output_dir", None)
            persisted.pop("_active_project_output_dir", None)
            SETTINGS_FILE.write_text(json.dumps(persisted,indent=2,ensure_ascii=False),encoding="utf-8")
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

def global_thread_excepthook(args):
    """Đưa lỗi từ threading.Thread vào cùng crash_log với lỗi main UI."""
    global_excepthook(args.exc_type, args.exc_value, args.exc_traceback)

def main():
    sys.excepthook = global_excepthook
    threading.excepthook = global_thread_excepthook
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = ThreadsStudioWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
