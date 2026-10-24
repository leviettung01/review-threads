# -*- coding: utf-8 -*-
"""
ReviewThreads Studio Pro - Local Server Backend
Full Feature Server with Dashboard, TiktokShop Catalog, Account Manager, SSE Logging, and Local Image Streaming.
"""
import os
import sys
import json
import time
import uuid
import math
import shutil
import asyncio
import re
import threading
from pathlib import Path
from typing import Dict, List, Optional
from flask import Flask, request, jsonify, send_from_directory, Response, send_file

# Add current directory to path
CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

# Import core models and tools from threads.py
from threads import (
    ThreadsProject, ProductItem, ThreadsScene,
    DATA_ROLE_SKILL_DIR, PROJECTS_FILE, SETTINGS_FILE,
    DEFAULT_ROLE_SKILL_FILE, get_available_role_skills,
    parse_llm_json_robust, OUTPUT_DIR, DEFAULT_PROXY_URL,
    LLM_MODEL_LIST, IMAGE_MODEL_LIST, AccountManager, Account,
    vbee_create_tts, get_media_duration, find_ffmpeg, FFMPEG_PATH
)
from grok_video_tool import run_veo_flow, VideoJob, is_veo_model, upload_veo_image

TIKTOK_SHOP_DIR = CURRENT_DIR / "TiktokShop"
TIKTOK_CRAWLED_DIR = TIKTOK_SHOP_DIR / "crawled_products"

app = Flask(__name__, static_folder="web", static_url_path="")

import socket
import subprocess

def start_proxy():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.8)
            if s.connect_ex(("127.0.0.1", 8317)) == 0:
                return
    except Exception:
        pass
    
    candidates = [
        CURRENT_DIR / "CLIProxy" / "cli-proxy-api.exe",
        CURRENT_DIR.parent / "CLIProxy" / "cli-proxy-api.exe"
    ]
    cliproxy_exe = None
    for c in candidates:
        if c.exists():
            cliproxy_exe = c
            break

    if cliproxy_exe:
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.Popen(
                [str(cliproxy_exe)],
                cwd=str(cliproxy_exe.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags
            )
            time.sleep(2)
            print(f"⚡ Đã khởi động CLIproxy thành công (Port 8317) từ: {cliproxy_exe}")
        except Exception as e:
            print(f"⚠️ Không thể mở CLIproxy: {e}")


captcha_bridge = None

def start_captcha_bridge():
    global captcha_bridge
    if captcha_bridge is None:
        try:
            from grok_video_tool import CaptchaBridge
            captcha_bridge = CaptchaBridge(add_log)
            captcha_bridge.start()
            add_log("🟩 Khởi động Captcha Bridge Server (Port 4000 & 8415) kết nối Extension Google Flow thành công!")
        except Exception as e:
            add_log(f"⚠️ Lỗi khởi động Captcha Bridge Server: {e}")

# Global State
global_projects: List[ThreadsProject] = []
active_project_id: Optional[str] = None
runner_thread: Optional[threading.Thread] = None
is_running_pipeline: bool = False
should_stop_pipeline: bool = False
log_queue: List[str] = []
log_lock = threading.Lock()
project_lock = threading.Lock()  # Fix 2: Bảo vệ đọc/ghi file projects.json
acc_mgr = AccountManager()

def add_log(msg: str):
    ts = time.strftime('%H:%M:%S')
    log_entry = f"[{ts}] {msg}"
    print(log_entry)
    with log_lock:
        log_queue.append(log_entry)
        if len(log_queue) > 1000:
            log_queue.pop(0)

def _run_with_timeout(func, args=(), timeout=60, default=None):
    """Fix 1: Chạy function trong thread riêng với timeout. Tránh treo cả app."""
    result = [default]
    exc = [None]
    def _worker():
        try:
            result[0] = func(*args)
        except Exception as e:
            exc[0] = e
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        add_log(f"⏰ [Timeout] Function {func.__name__} vượt quá {timeout}s — bỏ qua (có thể do reCAPTCHA).")
        return default
    if exc[0]:
        add_log(f"❌ [Timeout Worker] Lỗi trong {func.__name__}: {exc[0]}")
        return default
    return result[0]

def load_all_projects():
    global global_projects, active_project_id
    with project_lock:  # Fix 2: Thread-safe file read
        if PROJECTS_FILE.exists():
            try:
                raw = json.loads(PROJECTS_FILE.read_text("utf-8"))
                global_projects = [ThreadsProject.from_dict(p) for p in raw]
            except Exception as e:
                add_log(f"Lỗi nạp projects.json: {e}")
                global_projects = []
        if not global_projects:
            default_proj = ThreadsProject(name="Dự Án Khởi Tạo")
            global_projects = [default_proj]
            save_all_projects_unlocked()
        if not active_project_id and global_projects:
            active_project_id = global_projects[0].id

def save_all_projects_unlocked():
    """Ghi file KHÔNG lock — dùng khi đã giữ project_lock."""
    try:
        data = [p.to_dict() for p in global_projects]
        PROJECTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        add_log(f"Lỗi lưu projects.json: {e}")

def save_all_projects():
    with project_lock:  # Fix 2: Thread-safe file write
        save_all_projects_unlocked()

def get_active_project() -> Optional[ThreadsProject]:
    for p in global_projects:
        if p.id == active_project_id:
            return p
    return global_projects[0] if global_projects else None

def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text("utf-8"))
        except Exception:
            pass
    return {
        "selected_role": "RoleSkill.MD",
        "voice_mode": "narrative_review",
        "voice_mode_display": "Review dẫn chuyện (narrative_review)",
        "llm_model": "gpt-5.5",
        "img_model": "gpt-image-2",
        "image_quality": "1K (1024x1280 / Standard)",
        "context": "Bối cảnh theo Role Skill",
        "scene_count": 8,
        "voice_engine": "gtts",
        "vbee_token": "",
        "vbee_appid": "",
        "vbee_voice": "vi_south_female_miien_48k",
        "vbee_speed": 1.0,
        "bg_folder": "",
        "no_duplicate_bg": True,
        "concurrency": 8,
        "burst_size": 4,
        "burst_delay": 10,
        "upscale_threads": 1,
        "delay_sec": 2
    }

def save_settings(st: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

# ── API ROUTES ──────────────────────────────────────────────────────────

@app.route("/")
def serve_index():
    return send_from_directory("web", "index.html")

# Image Streaming Endpoint (Fixes Broken Local Image Icons in Chromium)
@app.route("/api/images")
def serve_image():
    img_path = request.args.get("path", "")
    if not img_path:
        return jsonify({"error": "No path provided"}), 400
    
    p = Path(img_path)
    if not p.exists() or not p.is_file():
        return jsonify({"error": "File not found"}), 404
    
    return send_file(str(p))

@app.route("/api/state", methods=["GET"])
def get_state():
    load_all_projects()
    st = load_settings()
    act = get_active_project()
    roles = list(get_available_role_skills().keys())
    
    total_projects = len(global_projects)
    total_products = sum(len(p.products) for p in global_projects)
    completed_videos = sum(sum(1 for pr in p.products if "Hoàn thành" in pr.status or "✅" in pr.status) for p in global_projects)

    return jsonify({
        "projects": [p.to_dict() for p in global_projects],
        "active_project_id": active_project_id,
        "active_project": act.to_dict() if act else None,
        "settings": st,
        "roles": roles,
        "llm_models": LLM_MODEL_LIST,
        "img_models": IMAGE_MODEL_LIST,
        "is_running": is_running_pipeline,
        "stats": {
            "total_projects": total_projects,
            "total_products": total_products,
            "completed_videos": completed_videos
        }
    })

@app.route("/api/projects/select", methods=["POST"])
def select_project():
    global active_project_id
    data = request.json or {}
    pid = data.get("id")
    if pid:
        active_project_id = pid
    return get_state()

@app.route("/api/projects/create", methods=["POST"])
def create_project():
    global active_project_id
    data = request.json or {}
    name = data.get("name", "").strip() or "Dự Án Mới"
    new_p = ThreadsProject(name=name)
    global_projects.append(new_p)
    active_project_id = new_p.id
    save_all_projects()
    add_log(f"Đã tạo dự án mới: {name}")
    return get_state()

@app.route("/api/projects/delete", methods=["POST"])
def delete_project():
    global active_project_id, global_projects
    data = request.json or {}
    pid = data.get("id")
    if pid:
        global_projects = [p for p in global_projects if p.id != pid]
        if not global_projects:
            global_projects = [ThreadsProject(name="Dự Án Mới")]
        active_project_id = global_projects[0].id
        save_all_projects()
        add_log("Đã xóa dự án.")
    return get_state()

@app.route("/api/products/add", methods=["POST"])
def add_product():
    act = get_active_project()
    if not act:
        return jsonify({"error": "No active project"}), 400
    data = request.json or {}
    title = data.get("title", "").strip() or f"Sản phẩm #{len(act.products)+1}"
    info = data.get("info", "").strip() or "Mô tả sản phẩm..."
    images = data.get("images", [])
    prod = ProductItem(title=title, info=info, images=images)
    act.products.append(prod)
    save_all_projects()
    add_log(f"Thêm SP mới: {title}")
    return jsonify({"success": True, "product": prod.to_dict()})

@app.route("/api/products/update", methods=["POST"])
def update_product():
    act = get_active_project()
    if not act:
        return jsonify({"error": "No active project"}), 400
    data = request.json or {}
    pid = data.get("id")
    for p in act.products:
        if p.id == pid:
            if "title" in data: p.title = data["title"]
            if "info" in data: p.info = data["info"]
            if "context" in data: p.context = data["context"]
            if "images" in data: p.images = data["images"]
            if "scenes" in data:
                p.scenes = [ThreadsScene.from_dict(s) for s in data["scenes"]]
            save_all_projects()
            return jsonify({"success": True, "product": p.to_dict()})
    return jsonify({"error": "Product not found"}), 404

@app.route("/api/products/delete", methods=["POST"])
def delete_product():
    act = get_active_project()
    if not act:
        return jsonify({"error": "No active project"}), 400
    data = request.json or {}
    pid = data.get("id")
    if pid:
        act.products = [p for p in act.products if p.id != pid]
        save_all_projects()
        add_log("Đã xóa sản phẩm.")
    return jsonify({"success": True, "active_project": act.to_dict()})

# ── TIKTOKSHOP API ──────────────────────────────────────────────────────

@app.route("/api/tiktokshop/list", methods=["GET"])
def list_tiktok_products():
    prod_json = TIKTOK_CRAWLED_DIR / "products.json"
    if not prod_json.exists() and (GROK_DIR / "TiktokShop" / "crawled_products" / "products.json").exists():
        prod_json = GROK_DIR / "TiktokShop" / "crawled_products" / "products.json"

    products = []
    if prod_json.exists():
        try:
            products = json.loads(prod_json.read_text(encoding="utf-8"))
        except Exception as e:
            add_log(f"Lỗi nạp TiktokShop products.json: {e}")
            
    for p in products:
        try:
            pid = p.get("product_id", "")
            folder_name = p.get("folder", "Chưa phân loại")
            clean_t = re.sub(r'[\\/*?:"<>|]', "_", p.get("title", "product"))
            clean_t = re.sub(r'_+', '_', clean_t)[:60].strip(' _') or "product"
            
            p_dir = None
            if folder_name and folder_name != "Chưa phân loại":
                p_dir = TIKTOK_CRAWLED_DIR / folder_name / f"{pid}_{clean_t}"
            else:
                p_dir = TIKTOK_CRAWLED_DIR / f"{pid}_{clean_t}"
            
            # Fallback: recursive search across all category subdirectories
            try:
                if (not p_dir or not p_dir.exists()) and TIKTOK_CRAWLED_DIR.exists():
                    for entry in TIKTOK_CRAWLED_DIR.rglob("*"):
                        if entry.is_dir() and (entry.name.startswith(pid) or pid in entry.name):
                            p_dir = entry
                            break
            except Exception:
                pass
            
            # Find images - check images/ subdirectory first, then direct
            imgs = []
            if p_dir:
                try:
                    images_subdir = p_dir / "images"
                    search_dirs = []
                    if images_subdir.exists():
                        search_dirs.append(images_subdir)
                    if p_dir.exists():
                        search_dirs.append(p_dir)
                    for sd in search_dirs:
                        try:
                            for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp"]:
                                imgs.extend([str(x) for x in sd.glob(ext)])
                        except Exception:
                            pass
                    imgs = list(dict.fromkeys(imgs))
                except Exception:
                    pass
            p["image_files"] = imgs
            
            # Find voice files - check voices/{pid}/ directory AND product's voices field
            voice_files = []
            try:
                voices_dir = TIKTOK_CRAWLED_DIR / "voices" / pid
                if voices_dir.exists():
                    for ext in ["*.mp3", "*.wav", "*.m4a"]:
                        voice_files.extend([str(x) for x in voices_dir.glob(ext)])
            except Exception:
                pass
                
            # Also include voices from products.json
            for v in p.get("voices", []):
                try:
                    v_path = str(TIKTOK_CRAWLED_DIR / v) if not Path(v).is_absolute() else v
                    if Path(v_path).exists() and v_path not in voice_files:
                        voice_files.append(v_path)
                except Exception:
                    pass
            p["voice_files"] = voice_files
        except Exception:
            p["image_files"] = p.get("image_files", [])
            p["voice_files"] = p.get("voice_files", [])
        
    return jsonify({"products": products})

@app.route("/api/tiktokshop/import", methods=["POST"])
def import_tiktok_product():
    act = get_active_project()
    if not act:
        return jsonify({"error": "No active project"}), 400
    data = request.json or {}
    title = data.get("title", "Sản phẩm TikTokShop")
    info = data.get("description", "Mô tả sản phẩm TikTokShop")
    image_path = data.get("image_path")
    all_images = data.get("all_images", [])
    voice_files = data.get("voice_files", [])
    
    # Use all images if available, otherwise just the selected one
    images = all_images if all_images else ([image_path] if image_path else [])
    # Filter to only existing files
    images = [img for img in images if img and Path(img).exists()]
    
    # Find first existing voice file
    voice_path = ""
    voice_dur = 0.0
    for vp in voice_files:
        if vp and Path(vp).exists():
            voice_path = vp
            try:
                from threads import get_media_duration
                voice_dur = get_media_duration(vp)
            except Exception:
                pass
            break
    
    prod = ProductItem(title=title, info=info, images=images, voice_path=voice_path, voice_duration=voice_dur)
    act.products.append(prod)
    save_all_projects()
    add_log(f"➕ Đã import sản phẩm TikTokShop: {title[:40]}... ({len(images)} ảnh, voice: {'Có' if voice_path else 'Không'})")
    return jsonify({"success": True, "product": prod.to_dict()})

@app.route("/api/tiktokshop/collage", methods=["POST"])
def tiktok_collage_route():
    data = request.json or {}
    image_paths = data.get("image_paths", [])
    layout_type = data.get("layout", "Lưới 2x2 (Tối đa 4 ảnh)")
    ratio_type = data.get("aspect_ratio", "Vuông (1:1)")
    
    selected_paths = [p for p in image_paths if p and Path(p).exists()]
    if not selected_paths:
        return jsonify({"error": "Vui lòng chọn ít nhất 1 ảnh để ghép!"}), 400
        
    selected_count = len(selected_paths)
    max_allowed = 99
    if "1x1" in layout_type: max_allowed = 1
    elif "2x2" in layout_type: max_allowed = 4
    elif "2x3" in layout_type: max_allowed = 6
    elif "3x3" in layout_type: max_allowed = 9
    
    if selected_count > max_allowed:
        return jsonify({"error": f"Bố cục {layout_type} chỉ hỗ trợ tối đa {max_allowed} ảnh. Bạn đã chọn {selected_count} ảnh!"}), 400
        
    seg_w, seg_h = 600, 600
    if "Ngang" in layout_type:
        canvas_w = seg_w * selected_count
        canvas_h = seg_h
        cols, rows = selected_count, 1
    elif "Dọc" in layout_type:
        canvas_w = seg_w
        canvas_h = seg_h * selected_count
        cols, rows = 1, selected_count
    else:
        if "1x1" in layout_type: cols, rows = 1, 1
        elif "2x2" in layout_type: cols, rows = 2, 2
        elif "2x3" in layout_type: cols, rows = 2, 3
        elif "3x3" in layout_type: cols, rows = 3, 3
        else: cols, rows = 2, 2
        
        if "1:1" in ratio_type: canvas_w, canvas_h = 1200, 1200
        elif "16:9" in ratio_type: canvas_w, canvas_h = 1600, 900
        elif "9:16" in ratio_type: canvas_w, canvas_h = 900, 1600
        else: canvas_w, canvas_h = 1200, 1200
        
    try:
        from PIL import Image, ImageDraw, ImageFont
        canvas = Image.new("RGB", (canvas_w, canvas_h), color=(15, 23, 42))
        draw = ImageDraw.Draw(canvas)
        gap = 8
        cell_w = canvas_w // cols
        cell_h = canvas_h // rows
        
        try:
            font = ImageFont.truetype("arial.ttf", 16)
        except Exception:
            font = ImageFont.load_default()
            
        for idx, path_str in enumerate(selected_paths):
            try:
                img = Image.open(path_str).convert("RGBA")
            except Exception:
                continue
                
            row = idx // cols
            col = idx % cols
            box_x = col * cell_w + gap
            box_y = row * cell_h + gap
            box_w = cell_w - 2 * gap
            box_h = cell_h - 2 * gap
            if box_w <= 0 or box_h <= 0: continue
            
            img_copy = img.copy()
            img_copy.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
            sw, sh = img_copy.size
            dx = box_x + (box_w - sw) // 2
            dy = box_y + (box_h - sh) // 2
            
            canvas.paste(img_copy, (dx, dy), mask=img_copy)
            draw.rectangle([dx, dy, dx + sw, dy + sh], outline=(71, 85, 105), width=2)
            
            badge_radius = 14
            badge_cx = dx + 6 + badge_radius
            badge_cy = dy + 6 + badge_radius
            draw.ellipse([badge_cx - badge_radius, badge_cy - badge_radius, badge_cx + badge_radius, badge_cy + badge_radius], fill=(124, 58, 237))
            
            txt = str(idx + 1)
            bbox = font.getbbox(txt)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((badge_cx - tw // 2, badge_cy - th // 2 - 1), txt, fill=(255, 255, 255), font=font)
            
        temp_dir = TIKTOK_CRAWLED_DIR / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / f"collage_{uuid.uuid4().hex[:8]}.jpg"
        canvas.save(out_path, "JPEG", quality=95)
        add_log(f"🧩 Đã tạo ảnh ghép thành công: {out_path.name}")
        return jsonify({"success": True, "collage_path": str(out_path)})
    except Exception as err:
        return jsonify({"error": f"Lỗi tạo ảnh ghép: {err}"}), 500


@app.route("/api/tiktokshop/crop", methods=["POST"])
def tiktok_crop_route():
    data = request.json or {}
    image_path = data.get("image_path", "")
    x = float(data.get("x", 0))
    y = float(data.get("y", 0))
    w = float(data.get("w", 100))
    h = float(data.get("h", 100))
    
    if not image_path or not Path(image_path).exists():
        return jsonify({"error": "File ảnh không tồn tại!"}), 400
        
    try:
        from PIL import Image
        img = Image.open(image_path)
        W, H = img.size
        left = max(0, int(W * (x / 100.0)))
        top = max(0, int(H * (y / 100.0)))
        right = min(W, int(W * ((x + w) / 100.0)))
        bottom = min(H, int(H * ((y + h) / 100.0)))
        
        if right - left < 10 or bottom - top < 10:
            return jsonify({"error": "Vùng cắt quá nhỏ!"}), 400
            
        cropped = img.crop((left, top, right, bottom))
        temp_dir = TIKTOK_CRAWLED_DIR / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        out_path = temp_dir / f"cropped_{uuid.uuid4().hex[:8]}.jpg"
        cropped.convert("RGB").save(out_path, "JPEG", quality=95)
        add_log(f"✂️ Đã cắt ảnh thành công: {out_path.name}")
        return jsonify({"success": True, "cropped_path": str(out_path)})
    except Exception as err:
        return jsonify({"error": f"Lỗi cắt ảnh: {err}"}), 500

# ── ACCOUNTS & SETTINGS ─────────────────────────────────────────────────

import webbrowser

@app.route("/api/accounts", methods=["GET"])
def get_accounts():
    acc_mgr.load()
    return jsonify({"accounts": [a.to_dict() for a in acc_mgr.accounts]})

@app.route("/api/accounts/add", methods=["POST"])
def add_account_route():
    data = request.json or {}
    name = data.get("name", "").strip()
    proxy_url = data.get("proxy_url", "").strip()
    api_key = data.get("api_key", "").strip()
    if not name:
        return jsonify({"error": "Tên tài khoản không được để trống!"}), 400
    acc_mgr.add(name, proxy_url, api_key)
    add_log(f"➕ Đã thêm tài khoản thành công: [{name}]")
    return jsonify({"success": True, "accounts": [a.to_dict() for a in acc_mgr.accounts]})

@app.route("/api/accounts/toggle", methods=["POST"])
def toggle_account_route():
    data = request.json or {}
    idx = data.get("index", -1)
    if 0 <= idx < len(acc_mgr.accounts):
        acc_mgr.toggle(idx)
        acc = acc_mgr.accounts[idx]
        st_text = "Bật" if acc.active else "Tắt"
        add_log(f"⚙️ Thay đổi trạng thái tài khoản [{acc.name}]: {st_text}")
    return jsonify({"success": True, "accounts": [a.to_dict() for a in acc_mgr.accounts]})

@app.route("/api/accounts/delete", methods=["POST"])
def delete_account_route():
    data = request.json or {}
    idx = data.get("index", -1)
    if 0 <= idx < len(acc_mgr.accounts):
        acc = acc_mgr.accounts[idx]
        acc_mgr.remove(idx)
        add_log(f"🗑️ Đã xóa tài khoản: [{acc.name}]")
    return jsonify({"success": True, "accounts": [a.to_dict() for a in acc_mgr.accounts]})

@app.route("/api/accounts/open_admin", methods=["POST"])
def open_admin_route():
    try:
        start_proxy()
        webbrowser.open("http://localhost:8317/")
        add_log("🌐 Đã mở CLIproxy Web Admin (http://localhost:8317/) trên trình duyệt!")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/accounts/sync_cliproxy", methods=["POST"])
def sync_cliproxy_route():
    start_proxy()
    acc_mgr.load()
    added, removed = acc_mgr.sync_from_cliproxy()
    add_log(f"🔄 Đồng bộ CLIproxy thành công: +{added} tài khoản mới thêm vào, -{removed} tài khoản đã xóa trên CLIproxy!")
    return jsonify({"success": True, "accounts": [a.to_dict() for a in acc_mgr.accounts], "added": added, "removed": removed})

@app.route("/api/accounts/login_browser", methods=["POST"])
def login_browser_route():
    data = request.json or {}
    login_type = data.get("type", "xAI Grok (SuperGrok)")
    profile_name = data.get("profile_name", "").strip()
    acc_mgr.load()
    
    if "Google" in login_type and not profile_name:
        g_accs = [a for a in acc_mgr.accounts if a.name.startswith("Google_")]
        if g_accs:
            profile_name = g_accs[0].name.replace("Google_", "", 1)
        else:
            profile_name = "default"

    add_log(f"🔑 Bắt đầu mở trình duyệt đăng nhập cho: [{login_type}] (Profile: {profile_name})...")
    
    def do_browser_login():
        try:
            if "Google" in login_type:
                from threads import GoogleLoginWorker
                worker = GoogleLoginWorker(profile_name=profile_name)
                worker.status_sig.connect(lambda msg, color: add_log(f"🌐 [Google Login - {profile_name}] {msg}"))
                def on_success(email, token, proj_id):
                    add_log(f"✅ [Google Login] Đăng nhập thành công: {email}")
                    acc_name = f"Google_{email}" if not email.startswith("Google_") else email
                    matched = False
                    for a in acc_mgr.accounts:
                        if a.name == acc_name or a.name == f"Google_{profile_name}":
                            a.api_key = token
                            if proj_id: a.proxy_url = proj_id
                            a.active = True
                            matched = True
                    if not matched:
                        acc_mgr.add(acc_name, proj_id, token)
                    acc_mgr.save()
                worker.success_sig.connect(on_success)
                worker.error_sig.connect(lambda err: add_log(f"❌ Lỗi đăng nhập Google: {err}"))
                worker.run()
            else:
                from threads import LoginWorker
                worker = LoginWorker(login_type=login_type)
                worker.status_sig.connect(lambda msg, color: add_log(f"🔑 [OAuth Login] {msg}"))
                worker.success_sig.connect(lambda info: (add_log(f"✅ Đăng nhập thành công: {info}"), acc_mgr.load()))
                worker.run()
        except Exception as err:
            add_log(f"❌ Lỗi mở trình duyệt login: {err}")
            
    threading.Thread(target=do_browser_login, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/accounts/check_tokens", methods=["POST"])
def check_tokens_route():
    add_log("🔍 Bắt đầu kiểm tra hiệu lực tất cả Token & làm mới Google Cookies...")
    acc_mgr.load()
    
    def do_token_check():
        try:
            from threads import GoogleTokenRefreshWorker
            g_accs = [a for a in acc_mgr.accounts if a.active and (a.name.startswith("Google") or "google" in a.name.lower())]
            if g_accs:
                worker = GoogleTokenRefreshWorker(g_accs)
                worker.status_sig.connect(lambda msg, color: add_log(f"🔍 [Google Token] {msg}"))
                worker.log_sig.connect(lambda msg: add_log(f"📝 {msg}"))
                def on_updated(acc_name, token, proj_id, credits_val):
                    add_log(f"✅ [Google Token Updated] {acc_name}: ProjectID = {proj_id}, Credits = {credits_val if credits_val >= 0 else 'N/A'}")
                    for a in acc_mgr.accounts:
                        if a.name == acc_name:
                            a.api_key = token
                            if proj_id: a.proxy_url = proj_id
                            if credits_val >= 0: a.credits = credits_val
                    acc_mgr.save()
                worker.token_updated.connect(on_updated)
                worker.run()
            else:
                add_log("ℹ️ Không có tài khoản Google nào cần kiểm tra token.")
        except Exception as err:
            add_log(f"❌ Lỗi kiểm tra token Google: {err}")
            
    threading.Thread(target=do_token_check, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/accounts/single_refresh", methods=["POST"])
def single_refresh_route():
    data = request.json or {}
    idx = data.get("index", -1)
    acc_mgr.load()
    if 0 <= idx < len(acc_mgr.accounts):
        acc = acc_mgr.accounts[idx]
        profile_name = acc.name.replace("Google_", "", 1) if acc.name.startswith("Google_") else acc.name
        add_log(f"🔍 Bắt đầu kiểm tra token & credit riêng cho [{profile_name}]...")
        
        def do_single_refresh():
            try:
                from threads import GoogleSingleRefreshWorker
                worker = GoogleSingleRefreshWorker(acc, profile_name)
                worker.status_sig.connect(lambda msg, color: add_log(f"🔍 [{profile_name}] {msg}"))
                worker.log_sig.connect(lambda msg: add_log(f"📝 [{profile_name}] {msg}"))
                def on_updated(acc_name, token, proj_id, credits_val):
                    add_log(f"✅ [{acc_name}] Cập nhật Token thành công: ProjectID = {proj_id}, Credits = {credits_val if credits_val >= 0 else 'N/A'}")
                    acc.api_key = token
                    if proj_id: acc.proxy_url = proj_id
                    if credits_val >= 0: acc.credits = credits_val
                    acc_mgr.save()
                worker.token_updated.connect(on_updated)
                worker.run()
            except Exception as err:
                add_log(f"❌ Lỗi kiểm tra token [{profile_name}]: {err}")
                
        threading.Thread(target=do_single_refresh, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/accounts/single_relogin", methods=["POST"])
def single_relogin_route():
    data = request.json or {}
    idx = data.get("index", -1)
    acc_mgr.load()
    if 0 <= idx < len(acc_mgr.accounts):
        acc = acc_mgr.accounts[idx]
        profile_name = acc.name.replace("Google_", "", 1) if acc.name.startswith("Google_") else acc.name
        add_log(f"🔑 Bắt đầu mở trình duyệt login lại riêng cho profile [{profile_name}]...")
        
        def do_single_relogin():
            try:
                if acc.name.startswith("Google_") or "google" in acc.name.lower():
                    from threads import GoogleLoginWorker
                    worker = GoogleLoginWorker(profile_name=profile_name)
                    worker.status_sig.connect(lambda msg, color: add_log(f"🌐 [Google Relogin - {profile_name}] {msg}"))
                    def on_success(email, token, proj_id):
                        add_log(f"✅ [Google Relogin - {profile_name}] Đăng nhập thành công: {email}")
                        acc.api_key = token
                        if proj_id: acc.proxy_url = proj_id
                        acc.active = True
                        acc_mgr.save()
                    worker.success_sig.connect(on_success)
                    worker.error_sig.connect(lambda err: add_log(f"❌ Lỗi login Google [{profile_name}]: {err}"))
                    worker.run()
                else:
                    from threads import LoginWorker
                    l_type = "OpenAI Codex" if "codex" in acc.name.lower() else "xAI Grok (SuperGrok)"
                    worker = LoginWorker(login_type=l_type)
                    worker.status_sig.connect(lambda msg, color: add_log(f"🔑 [OAuth Relogin] {msg}"))
                    worker.success_sig.connect(lambda info: (add_log(f"✅ Đăng nhập lại thành công: {info}"), acc_mgr.load()))
                    worker.run()
            except Exception as err:
                add_log(f"❌ Lỗi mở trình duyệt relogin: {err}")
                
        threading.Thread(target=do_single_relogin, daemon=True).start()
    return jsonify({"success": True})

@app.route("/api/accounts/update_tag", methods=["POST"])
def update_tag_route():
    data = request.json or {}
    idx = data.get("index", -1)
    tag = data.get("tag", "Pro")
    acc_mgr.load()
    if 0 <= idx < len(acc_mgr.accounts):
        acc_mgr.accounts[idx].tag = tag
        acc_mgr.save()
        add_log(f"🏷️ Cập nhật Tag tài khoản [{acc_mgr.accounts[idx].name}]: {tag}")
    return jsonify({"success": True})

VBEE_CACHE_FILE = CURRENT_DIR / "data" / "vbee_voices.json"

DEFAULT_VBEE_VOICES = [
    ["vi_south_female_miien_48k", "[VBEE] Miền (Nữ Miền Nam)"],
    ["vi_north_female_hn_48k", "[VBEE] Hà Nội (Nữ Miền Bắc)"],
    ["vi_north_male_hn_48k", "[VBEE] Hà Nội (Nam Miền Bắc)"],
    ["vi_south_male_saigon_48k", "[VBEE] Sài Gòn (Nam Miền Nam)"],
    ["vi_central_female_hue_48k", "[VBEE] Huế (Nữ Miền Trung)"]
]

@app.route("/api/vbee/voices", methods=["GET"])
def get_vbee_voices_route():
    st = load_settings()
    token = request.args.get("token") or st.get("vbee_token", "")
    app_id = request.args.get("app_id") or st.get("vbee_appid", "")
    force_refresh = request.args.get("refresh", "false").lower() == "true"
    
    VBEE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    if not force_refresh and VBEE_CACHE_FILE.exists():
        try:
            cached_voices = json.loads(VBEE_CACHE_FILE.read_text("utf-8"))
            if cached_voices:
                return jsonify({"voices": cached_voices, "from_cache": True, "count": len(cached_voices)})
        except Exception: pass
        
    if token and app_id:
        from threads import vbee_get_voices
        add_log("🎙️ Đang kết nối Vbee API để lấy danh sách giọng đọc mới nhất...")
        fetched = vbee_get_voices(token, app_id)
        if fetched:
            try:
                VBEE_CACHE_FILE.write_text(json.dumps(fetched, ensure_ascii=False, indent=2), "utf-8")
            except Exception: pass
            add_log(f"✅ Đã tải thành công {len(fetched)} giọng Vbee từ API!")
            return jsonify({"voices": fetched, "from_cache": False, "count": len(fetched)})
        else:
            add_log("⚠️ Không lấy được giọng từ Vbee API hoặc sai Token/App-ID. Đang dùng danh sách mặc định.")
    
    if not VBEE_CACHE_FILE.exists():
        try:
            VBEE_CACHE_FILE.write_text(json.dumps(DEFAULT_VBEE_VOICES, ensure_ascii=False, indent=2), "utf-8")
        except Exception: pass
        
    return jsonify({"voices": DEFAULT_VBEE_VOICES, "from_cache": True, "count": len(DEFAULT_VBEE_VOICES)})

@app.route("/api/settings/save", methods=["POST"])
def save_settings_route():
    data = request.json or {}
    st = load_settings()
    
    # Detailed log entries for setting changes
    if "selected_role" in data and data["selected_role"] != st.get("selected_role"):
        add_log(f"🎭 Đã chọn Role Skill: [{data['selected_role']}]")
    if "llm_model" in data and data["llm_model"] != st.get("llm_model"):
        add_log(f"🤖 Đã chọn Model LLM Biên kịch: [{data['llm_model']}]")
    if "voice_mode" in data and data["voice_mode"] != st.get("voice_mode"):
        add_log(f"🎙️ Đã chọn Chế độ Voiceover: [{data['voice_mode']}]")
    if "img_model" in data and data["img_model"] != st.get("img_model"):
        add_log(f"🖼️ Đã chọn Model Ảnh AI: [{data['img_model']}]")
    if "image_quality" in data and data["image_quality"] != st.get("image_quality"):
        add_log(f"📐 Đã chọn Chất lượng Ảnh AI: [{data['image_quality']}]")
    if "context" in data and data["context"] != st.get("context"):
        add_log(f"🌐 Đã chọn Bối cảnh Context: [{data['context']}]")
    if "voice_engine" in data and data["voice_engine"] != st.get("voice_engine"):
        add_log(f"🗣️ Đã chọn Engine Voiceover: [{data['voice_engine']}]")
    if "concurrency" in data and data["concurrency"] != st.get("concurrency"):
        add_log(f"⚡ Đã cập nhật Số luồng song song: {data['concurrency']}")
    if "scene_count" in data and data["scene_count"] != st.get("scene_count"):
        add_log(f"🎬 Đã cập nhật Số phân cảnh: {data['scene_count']}")

    st.update(data)
    save_settings(st)
    return jsonify({"success": True, "settings": st})

@app.route("/api/roles/list", methods=["GET"])
def list_roles():
    roles = get_available_role_skills()
    res = []
    for fname, fpath in roles.items():
        sz = fpath.stat().st_size if fpath.exists() else 0
        res.append({"name": fname, "size": sz, "path": str(fpath)})
    return jsonify({"roles": res})

@app.route("/api/roles/read", methods=["GET"])
def read_role():
    name = request.args.get("name", "")
    roles = get_available_role_skills()
    if name in roles and roles[name].exists():
        content = roles[name].read_text("utf-8", errors="replace")
        return jsonify({"success": True, "name": name, "content": content})
    return jsonify({"error": "File not found"}), 404

@app.route("/api/roles/save", methods=["POST"])
def save_role():
    DATA_ROLE_SKILL_DIR.mkdir(parents=True, exist_ok=True)
    data = request.json or {}
    name = data.get("name", "").strip()
    content = data.get("content", "")
    if not name:
        return jsonify({"error": "Invalid name"}), 400
    if not name.lower().endswith(".md") and not name.lower().endswith(".txt"):
        name = f"{name}.MD"
    fpath = DATA_ROLE_SKILL_DIR / name
    fpath.write_text(content, encoding="utf-8")
    add_log(f"Đã lưu file Role Skill: {name}")
    return jsonify({"success": True, "name": name})

@app.route("/api/roles/delete", methods=["POST"])
def delete_role():
    data = request.json or {}
    name = data.get("name", "").strip()
    roles = get_available_role_skills()
    if name in roles and roles[name].exists():
        try:
            roles[name].unlink()
            add_log(f"Đã xóa file Role Skill: {name}")
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "File not found"}), 404

@app.route("/api/logs/stream")
def stream_logs():
    def event_stream():
        last_idx = 0
        start_time = time.time()
        heartbeat_interval = 15  # Fix 3: Heartbeat mỗi 15s
        max_duration = 1800  # Fix 3: Tự ngắt sau 30 phút
        last_heartbeat = time.time()
        while True:
            # Fix 3: Timeout tổng để tránh leak connection
            if time.time() - start_time > max_duration:
                yield f"data: {json.dumps({'message': '[SSE] Connection timeout — hãy refresh trang.'})}\n\n"
                break
            with log_lock:
                if last_idx < len(log_queue):
                    for msg in log_queue[last_idx:]:
                        yield f"data: {json.dumps({'message': msg})}\n\n"
                    last_idx = len(log_queue)
                    last_heartbeat = time.time()
            # Fix 3: Heartbeat giữ connection sống
            if time.time() - last_heartbeat >= heartbeat_interval:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()
            time.sleep(0.5)
    return Response(event_stream(), mimetype="text/event-stream")

# ── BACKEND PIPELINE ENGINE ──────────────────────────────────────────────

def run_backend_pipeline(products: List[ProductItem], st: dict):
    global is_running_pipeline, should_stop_pipeline
    is_running_pipeline = True
    should_stop_pipeline = False
    
    try:
        add_log("🚀 [Engine Backend] Bắt đầu chạy Pipeline...")
        total = len(products)
        api_url = st.get("api_url", DEFAULT_PROXY_URL)
        api_key = st.get("api_key", "grok-video-key-001")
        llm = st.get("llm_model", "gpt-5.5")
        img_m = st.get("img_model", "gpt-image-2")
        sc_cnt = st.get("scene_count", 8)
        ctx = st.get("context", "Bối cảnh theo Role Skill")
        rsp = st.get("role_skill_path")
        rs_text = Path(rsp).read_text(encoding="utf-8") if rsp and Path(rsp).exists() else ""
        bg_folder = st.get("bg_folder", "")
        voice_engine = st.get("voice_engine", "gtts")
        voice_mode = st.get("voice_mode", "narrative_review")

        # Detailed startup log
        add_log(f"  ╔══════════════════════════════════════════════════")
        add_log(f"  ║ 📋 Cấu hình Pipeline:")
        add_log(f"  ║   🤖 LLM Model: {llm}")
        add_log(f"  ║   🖼️ Image Model: {img_m}")
        add_log(f"  ║   🎭 Role Skill: {Path(rsp).name if rsp else 'Mặc định'}")
        add_log(f"  ║   🎙️ Voice Engine: {voice_engine}")
        add_log(f"  ║   🎤 Voice Mode: {voice_mode}")
        add_log(f"  ║   📸 Số phân cảnh: {sc_cnt}")
        add_log(f"  ║   🏞️ Bối cảnh: {ctx}")
        add_log(f"  ║   📦 Tổng sản phẩm: {total}")
        add_log(f"  ╚══════════════════════════════════════════════════")

        bg_files = []
        if bg_folder and Path(bg_folder).exists():
            for ext in ["*.mp4", "*.mov", "*.mkv", "*.avi"]:
                bg_files.extend(list(Path(bg_folder).glob(ext)))
            add_log(f"  🎬 Tìm thấy {len(bg_files)} video background trong thư mục.")

        for pi, prod in enumerate(products):
            if should_stop_pipeline:
                add_log("⏸️ Pipeline bị tạm dừng.")
                break
            
            out_base = CURRENT_DIR / "output_threads"
            out_base.mkdir(parents=True, exist_ok=True)
            clean_title = re.sub(r'[\\/*?:"<>|]', "", prod.title or 'san_pham')[:80]
            prod_folder = out_base / f"{clean_title}_{prod.id}"
            prod_folder.mkdir(parents=True, exist_ok=True)
            prod_images_dir = prod_folder / "images"
            prod_voice_dir = prod_folder / "voice"
            prod_images_dir.mkdir(exist_ok=True)
            prod_voice_dir.mkdir(exist_ok=True)

            add_log(f"")
            add_log(f"🚀 ━━━ [{pi+1}/{total}] {prod.title} ━━━")
            add_log(f"  📂 Thư mục: {prod_folder}")
            add_log(f"  📸 Ảnh tham chiếu: {len(prod.images)} file")
            prod.status = "⏳ Đang chạy..."
            # Fix 4: Không save ở đây — chờ cuối step để save gom

            # 1. LLM Generation
            if not prod.scenes:
                add_log(f"  ──────────────────────────────────────")
                add_log(f"  [1/4] 📝 Tạo kịch bản LLM [{llm}]...")
                add_log(f"    🎭 Role Skill: {Path(rsp).name if rsp else 'Mặc định'} ({len(rs_text)} ký tự)")
                add_log(f"    📸 Số phân cảnh yêu cầu: {sc_cnt}")
                add_log(f"    🎤 Voice Mode: {voice_mode}")
                vm_code = voice_mode
                vm_desc = "narrative_review (Review dẫn chuyện thứ 3 mượt mà)" if vm_code == "narrative_review" else "verbatim_read (Đọc nguyên văn bài/bình luận)"
                sp = (
                    f"{rs_text}\n\n"
                    f"BỐI CẢNH: {ctx}\n"
                    f"SỐ PHÂN CẢNH: {sc_cnt}\n"
                    f"THAM SỐ VOICE_MODE BẮT BUỘC (PHẦN 8): {vm_desc}\n"
                    f"YÊU CẦU QUAN TRỌNG: Tuân thủ Phần 6 (Suy luận aspect_ratio 4:3, 9:16, 1:1, 3:4, 4:5 cho từng scene) và Phần 8. Trả về đúng JSON schema Phần 10."
                )
                add_log(f"    ⏳ Đang gửi yêu cầu tới {api_url} ...")
                import requests
                r = requests.post(
                    f"{api_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json={
                        "model": llm,
                        "messages": [
                            {"role": "system", "content": sp},
                            {"role": "user", "content": f"SẢN PHẨM: {prod.title}\nTHÔNG TIN: {prod.info}"}
                        ],
                        "temperature": 0.7
                    },
                    timeout=900
                )
                if r.status_code == 200:
                    content_raw = r.json()["choices"][0]["message"]["content"]
                    d = parse_llm_json_robust(content_raw)
                    sr = d.get("scenarios", [{}])[0]
                    prod.voiceover_text = sr.get("voiceover", "")
                    prod.scenes = [ThreadsScene.from_dict(s) for s in sr.get("scenes", [])]
                    add_log(f"    ✅ Đã nhận {len(prod.scenes)} phân cảnh từ LLM [{llm}].")
                    add_log(f"    📝 Voiceover text: {len(prod.voiceover_text)} ký tự")
                    for si, sc in enumerate(prod.scenes):
                        add_log(f"      Scene #{si+1} [{sc.aspect_ratio}]: {(sc.prompt or '')[:80]}...")
                    # Save raw LLM output to file (like threads.py)
                    try:
                        script_file = prod_folder / "LLM.json"
                        script_file.write_text(content_raw, encoding="utf-8")
                        add_log(f"    💾 Đã lưu kịch bản vào: {script_file}")
                    except Exception as e_save:
                        add_log(f"    ⚠️ Lỗi lưu file kịch bản: {e_save}")
                    save_all_projects()  # Save sau LLM (có data mới quan trọng)
                else:
                    prod.status = "❌ Lỗi LLM"
                    add_log(f"    ❌ Lỗi API LLM (HTTP {r.status_code}): {r.text[:300]}")
                    save_all_projects()
                    continue
            else:
                add_log(f"  [1/4] ⏩ Bỏ qua kịch bản LLM (đã có sẵn {len(prod.scenes)} phân cảnh).")

            # 2. Image Generation
            burst_size = st.get("burst_size", 4)
            burst_delay = st.get("burst_delay", 10)
            is_veo = is_veo_model(img_m)
            
            if is_veo:
                acc_mgr.load()
                g_accs = [a for a in acc_mgr.accounts if a.active and (a.name.startswith("Google") or "google" in a.name.lower()) and len(a.api_key or "") > 20]
                if g_accs:
                    from grok_video_tool import GoogleTokenRefreshWorker
                    valid_found = False
                    
                    add_log(f"🔍 [Pre-Check] Đang kiểm tra hiệu lực Token của {len(g_accs)} tài khoản Google...")
                    for chosen_acc in g_accs:
                        candidate_token = chosen_acc.api_key or ""
                        is_valid, info = GoogleTokenRefreshWorker._check_token(candidate_token)
                        if is_valid:
                            st["veo_bearer"] = candidate_token
                            st["veo_project_id"] = chosen_acc.proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b"
                            add_log(f"🔑 [Pre-Check] ✅ Đã chọn Google Token hợp lệ của [{chosen_acc.name}] ({info}) - ProjectID: {st['veo_project_id']}")
                            valid_found = True
                            break
                        else:
                            add_log(f"⚠️ [Pre-Check] Token của [{chosen_acc.name}] hết hạn ({info}). Tự động thử làm mới (timeout 60s)...")
                            profile_name = chosen_acc.name.replace("Google_", "", 1) if chosen_acc.name.startswith("Google_") else chosen_acc.name
                            # Fix 1: Wrap trong timeout 60s — tránh treo vĩnh viễn do reCAPTCHA
                            refresh_result = _run_with_timeout(
                                GoogleTokenRefreshWorker._try_headless_refresh,
                                args=(profile_name,),
                                timeout=60,
                                default=(False, "", "", "Timeout 60s — có thể gặp reCAPTCHA")
                            )
                            success, new_token, new_project_id, err_msg = refresh_result
                            if success:
                                is_valid_new, new_email = GoogleTokenRefreshWorker._check_token(new_token)
                                if is_valid_new:
                                    add_log(f"✅ [Pre-Check] Tự động làm mới Token thành công cho [{chosen_acc.name}] ({new_email})!")
                                    st["veo_bearer"] = new_token
                                    if new_project_id:
                                        st["veo_project_id"] = new_project_id
                                    chosen_acc.api_key = new_token
                                    if new_project_id:
                                        chosen_acc.proxy_url = new_project_id
                                    acc_mgr.save()
                                    valid_found = True
                                    break
                            add_log(f"❌ [Pre-Check] Không thể làm mới token cho [{chosen_acc.name}]: {err_msg}")
                            
                    if not valid_found and g_accs:
                        st["veo_bearer"] = g_accs[0].api_key
                        st["veo_project_id"] = g_accs[0].proxy_url or "378bfc6c-422d-4bfb-92d1-6e978432ca7b"
                        add_log(f"⚠️ Tất cả tài khoản Google đều hết hạn! Tiếp tục thử với token hiện tại của [{g_accs[0].name}]...")
                else:
                    add_log("⚠️ Cảnh báo: Chưa chọn tài khoản Google nào có Token hợp lệ!")

            missing_scenes = []
            for idx, sc in enumerate(prod.scenes):
                target_img = prod_images_dir / f"scene_{sc.id}.png"
                if not sc.image_path or not Path(sc.image_path).exists() or not target_img.exists():
                    missing_scenes.append((idx, sc, target_img))
                else:
                    sc.image_path = str(target_img)
                    sc.status = "done"

            if missing_scenes:
                add_log(f"  ──────────────────────────────────────")
                add_log(f"  [2/4] 🖼️ Sinh {len(missing_scenes)} ảnh [{img_m}]...")
                add_log(f"    📊 Mode: {'Veo (Google AI Studio)' if is_veo else 'API Image Generation'}")
                add_log(f"    🔢 Burst size: {burst_size} (xử lý song song)")
                add_log(f"    📸 Tổng {len(prod.scenes)} scenes, còn thiếu {len(missing_scenes)} ảnh")
                ref_media_ids = []
                if is_veo and prod.images and st.get("veo_bearer"):
                    add_log(f"    📸 Tải lên {len(prod.images)} ảnh sản phẩm tham chiếu cho AI...")
                    loop_up = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop_up)
                    try:
                        async def _do_upload():
                            for img_p in prod.images:
                                if Path(img_p).exists():
                                    mid = await upload_veo_image(
                                        client=None, file_path=str(img_p),
                                        bearer_token=st.get("veo_bearer", ""),
                                        project_id=st.get("veo_project_id", ""),
                                        log=lambda m: add_log(f"    [Ref Upload] {m}"),
                                        captcha_bridge=captcha_bridge
                                    )
                                    if mid: ref_media_ids.append(mid)
                        loop_up.run_until_complete(_do_upload())
                    except Exception as e_up:
                        add_log(f"  ⚠️ Lỗi tải ảnh tham chiếu: {e_up}")
                    finally:
                        try: loop_up.close()
                        except Exception: pass

                for i in range(0, len(missing_scenes), burst_size):
                    if should_stop_pipeline: break
                    chunk = missing_scenes[i:i + burst_size]
                    add_log(f"    🚀 Đợt {i//burst_size + 1}: Xử lý {len(chunk)} ảnh...")
                    if is_veo:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            async def process_single(idx_val, sc_val, target_img_val):
                                try:
                                    add_log(f"      🎨 Phân cảnh #{idx_val+1}: {sc_val.prompt[:60]}...")
                                    sc_aspect = getattr(sc_val, "aspect_ratio", "4:3") or "4:3"
                                    job = VideoJob(
                                        id=f"img_{uuid.uuid4().hex[:6]}", model=img_m, mode="image-generation",
                                        prompt=sc_val.prompt, aspect_ratio=sc_aspect,
                                        output_name=f"scene_{sc_val.id}", local_path=str(prod_images_dir), use_proxy=True,
                                        ref_images=ref_media_ids if ref_media_ids else None
                                    )
                                    success = await run_veo_flow(
                                        job, st.get("veo_bearer", ""), st.get("veo_project_id", ""),
                                        captcha_bridge,
                                        log=lambda m: add_log(f"        [Labs #{idx_val+1}] {m}"),
                                        progress=None
                                    )
                                    if success and job.local_path and Path(job.local_path).exists():
                                        p_src = Path(job.local_path)
                                        if p_src.is_file(): p_src.replace(target_img_val)
                                        elif (p_src / f"scene_{sc_val.id}.png").exists(): (p_src / f"scene_{sc_val.id}.png").replace(target_img_val)
                                        elif (p_src / f"{job.id}.png").exists(): (p_src / f"{job.id}.png").replace(target_img_val)
                                        sc_val.image_path = str(target_img_val)
                                        sc_val.status = "done"
                                        add_log(f"      ✔ Scene #{idx_val+1} OK: {target_img_val.name}")
                                    else:
                                        sc_val.status = "error"
                                        add_log(f"      ❌ Scene #{idx_val+1} Lỗi: {job.error or 'Không rõ'}")
                                except Exception as ex:
                                    sc_val.status = "error"
                                    add_log(f"      ❌ Scene #{idx_val+1} Unhandled Exception: {ex}")
                            tasks = [process_single(idx_v, sc_v, t_v) for idx_v, sc_v, t_v in chunk]
                            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
                        finally:
                            try: loop.close()
                            except Exception: pass
                    else:
                        import requests, base64
                        for idx_v, sc_v, t_v in chunk:
                            ir = requests.post(
                                f"{api_url}/v1/images/generations",
                                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                json={"model": img_m, "prompt": sc_v.prompt, "n": 1, "size": "1024x1280", "response_format": "b64_json"},
                                timeout=600
                            )
                            if ir.status_code == 200:
                                it = ir.json().get("data", [{}])[0]
                                if "b64_json" in it:
                                    t_v.write_bytes(base64.b64decode(it["b64_json"]))
                                    sc_v.image_path = str(t_v)
                                    sc_v.status = "done"
                                    add_log(f"      ✔ Scene #{idx_v+1} OK: {t_v.name}")
                                else: sc_v.status = "error"
                            else: sc_v.status = "error"
                    # Fix 4: Không save mỗi burst — chờ cuối step images
                    # Burst delay between batches (like threads.py)
                    if is_veo and i + burst_size < len(missing_scenes) and not should_stop_pipeline:
                        add_log(f"    ⏳ Nghỉ {burst_delay}s giữa các đợt (Khớp cơ chế Veo3)...")
                        time.sleep(burst_delay)
                # Fix 4: Save 1 lần sau khi hoàn tất TOÀN BỘ ảnh
                save_all_projects()

            # 3. Voiceover Generation
            add_log(f"  ──────────────────────────────────────")
            target_voice = prod_voice_dir / f"voice_{prod.id}.mp3"
            if not prod.voice_path or not Path(prod.voice_path).exists() or not target_voice.exists():
                add_log(f"  [3/4] 🎙️ Tạo giọng nói Voiceover...")
                vt = prod.voiceover_text or prod.title
                add_log(f"    📝 Text voiceover: {len(vt)} ký tự")
                add_log(f"    🎤 Engine: {voice_engine}")
                if voice_engine == "vbee" and st.get("vbee_token"):
                    vbee_voice_code = st.get("vbee_voice", "vi_south_female_miien_48k")
                    add_log(f"    🗣️ Vbee Voice Code: {vbee_voice_code}")
                    ov = vbee_create_tts(
                        vt, st.get("vbee_token"), st.get("vbee_appid"),
                        vbee_voice_code,
                        st.get("vbee_speed", 1.0), prod_voice_dir, add_log
                    )
                    if ov and Path(ov).exists():
                        try: Path(ov).replace(target_voice)
                        except Exception: pass
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)
                        add_log(f"    ✅ Vbee Voice OK: {target_voice.name} ({prod.voice_duration:.1f}s)")
                if not prod.voice_path or not Path(prod.voice_path).exists():
                    add_log(f"    🔄 Dùng gTTS Fallback (Tiếng Việt)...")
                    try:
                        from gtts import gTTS
                        gTTS(text=vt, lang='vi').save(str(target_voice))
                        prod.voice_path = str(target_voice)
                        prod.voice_duration = get_media_duration(target_voice)
                        add_log(f"    ✅ gTTS Voice OK: {target_voice.name} ({prod.voice_duration:.1f}s)")
                    except Exception as e_gtts:
                        add_log(f"    ❌ Lỗi gTTS: {e_gtts}")
            else:
                add_log(f"  [3/4] ⏩ Bỏ qua Voice (đã có: {target_voice.name})")
            # Fix 4: Không save ở đây — đã save cuối step 2, sẽ save cuối step 4

            # 4. Edit Video Assembly
            add_log(f"  ──────────────────────────────────────")
            cbg = None
            if bg_files:
                cbg = bg_files[0]
            target_video = prod_folder / f"final_{prod.id}.mp4"
            if cbg and Path(cbg).exists():
                add_log(f"  [4/4] 🎬 Ghép dựng Video...")
                add_log(f"    🎞️ Video Background: {Path(cbg).name}")
                vs = [s for s in prod.scenes if s.image_path and Path(s.image_path).exists()]
                add_log(f"    🖼️ Số ảnh phân cảnh: {len(vs)}/{len(prod.scenes)}")
                add_log(f"    🎵 Voice: {prod.voice_path or 'Không có'}")
                if vs:
                    vd = prod.voice_duration if (prod.voice_duration and prod.voice_duration > 1.0) else 10.0
                    add_log(f"    ⏱️ Thời lượng video: {vd:.1f}s")
                    tc = sum(max(1, len(s.text_segment)) for s in vs)
                    sds = [max(1.5, round(vd * (max(1, len(s.text_segment)) / tc), 2)) for s in vs]
                    ins = ["-stream_loop", "-1", "-i", str(cbg)]
                    has_audio = False
                    if prod.voice_path and Path(prod.voice_path).exists():
                        ins.extend(["-i", str(prod.voice_path)])
                        has_audio = True
                    for s in vs: ins.extend(["-i", str(s.image_path)])
                    audio_idx = 1 if has_audio else None
                    img_start_idx = 2 if has_audio else 1
                    fc = [f"[0:v]trim=0:{vd:.2f},setpts=PTS-STARTPTS,scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[bg];"]
                    ct = 0.0
                    ls = "bg"
                    for si, dur in enumerate(sds):
                        st_t, et_t = ct, min(vd, ct + dur)
                        ct = et_t
                        os_ = f"v{si}"
                        fc.append(f"[{si+img_start_idx}:v]scale=960:-1[img{si}];[{ls}][img{si}]overlay=(W-w)/2:(H-h)/2:enable='between(t,{st_t:.2f},{et_t:.2f})'[{os_}];")
                        ls = os_
                    cmd = [FFMPEG_PATH, "-y"] + ins + ["-filter_complex", "".join(fc).rstrip(';')]
                    if has_audio: cmd.extend(["-map", f"[{ls}]", "-map", f"{audio_idx}:a", "-c:a", "aac", "-b:a", "192k"])
                    else: cmd.extend(["-map", f"[{ls}]"])
                    cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "22", "-t", f"{vd:.2f}", str(target_video)])
                    add_log(f"    ⏳ Đang chạy FFmpeg dựng video...")
                    import subprocess
                    rs = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if rs.returncode == 0 and target_video.exists():
                        prod.final_video_path = str(target_video)
                        prod.status = "✅ Hoàn thành"
                        add_log(f"    ✅ Dựng Video Thành Công: {target_video.name}")
                        add_log(f"    📁 Output: {target_video}")
                    else:
                        add_log(f"    ❌ Lỗi FFmpeg: {rs.stderr[:300]}")
                else:
                    add_log(f"    ⚠️ Không có ảnh phân cảnh nào để ghép video!")
            else:
                add_log(f"  [4/4] ⚠️ Chưa chọn thư mục Video Background hoặc không có file video!")
            save_all_projects()

        add_log("")
        add_log("🎉 ━━━ Pipeline hoàn tất! ━━━")
    except Exception as e_main:
        add_log(f"🔥 Lỗi Pipeline: {e_main}")
    finally:
        is_running_pipeline = False

@app.route("/api/pipeline/start", methods=["POST"])
def start_pipeline_route():
    global runner_thread
    if is_running_pipeline:
        return jsonify({"error": "Pipeline is already running"}), 400
    act = get_active_project()
    if not act or not act.products:
        return jsonify({"error": "Chưa có sản phẩm trong dự án!"}), 400
    st = load_settings()
    runner_thread = threading.Thread(target=run_backend_pipeline, args=(act.products, st), daemon=True)
    runner_thread.start()
    return jsonify({"success": True})

@app.route("/api/pipeline/pause", methods=["POST"])
def pause_pipeline_route():
    global should_stop_pipeline
    should_stop_pipeline = True
    add_log("⏸️ Yêu cầu tạm dừng Pipeline...")
    return jsonify({"success": True})

@app.route("/api/pipeline/rerender", methods=["POST"])
def rerender_pipeline_route():
    global runner_thread
    if is_running_pipeline:
        return jsonify({"error": "Pipeline đang chạy, vui lòng tạm dừng trước!"}), 400
    
    data = request.json or {}
    pid = data.get("product_id")
    act = get_active_project()
    if not act:
        return jsonify({"error": "Không tìm thấy dự án!"}), 404
        
    target_prod = next((p for p in act.products if p.id == pid), None)
    if not target_prod:
        return jsonify({"error": "Không tìm thấy sản phẩm!"}), 404
        
    st = load_settings()
    
    # Update settings if models were overridden in rerender dialog
    if data.get("llm_model"): st["llm_model"] = data["llm_model"]
    if data.get("voice_mode"): st["voice_mode"] = data["voice_mode"]
    if data.get("img_model"): st["img_model"] = data["img_model"]
    if data.get("voice_engine"): st["voice_engine"] = data["voice_engine"]
    save_settings(st)
    
    # Clear specific components based on user selection
    if data.get("rerender_script"):
        target_prod.scenes = []
        target_prod.voiceover_text = ""
        add_log(f"🔄 Reset Kịch bản LLM cho {target_prod.title[:30]}")
        
    if data.get("rerender_images"):
        sel_sc_ids = data.get("selected_scenes", [])
        for sc in target_prod.scenes:
            if not sel_sc_ids or sc.id in sel_sc_ids:
                sc.image_path = None
                sc.status = "pending"
        add_log(f"🔄 Reset Ảnh AI cho {target_prod.title[:30]}")
        
    if data.get("rerender_voice"):
        target_prod.voice_path = None
        add_log(f"🔄 Reset Voiceover cho {target_prod.title[:30]}")
        
    if data.get("rerender_video"):
        target_prod.final_video_path = None
        add_log(f"🔄 Reset Video ghép cho {target_prod.title[:30]}")
        
    target_prod.status = "⏳ Đang chạy..."
    save_all_projects()
    
    runner_thread = threading.Thread(target=run_backend_pipeline, args=([target_prod], st), daemon=True)
    runner_thread.start()
    return jsonify({"success": True})

# ── SERVER RUNNER ───────────────────────────────────────────────────────

def run_server(port=5500):
    start_proxy()
    start_captcha_bridge()
    load_all_projects()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)

if __name__ == "__main__":
    run_server(5500)
