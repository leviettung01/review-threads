#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tiktok_shop_crawler.py - Tool Crawl Sản phẩm Tiktok Shop qua Chrome mở sẵn
Hỗ trợ mở Chrome theo Profile/Cookies, cào đa luồng, tải ảnh gốc chất lượng cao, xuất CSV/JSON.
"""

import os
import sys
import re
import json
import csv
import time
import socket
import threading
import subprocess
import traceback
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QLineEdit, QProgressBar,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QSpinBox, QMessageBox, QGroupBox, QSplitter
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QIcon

# ─── Rate limiter cho TikWM Free API (1 request/giây toàn cục) ───────────────
_tikwm_lock = threading.Lock()
_tikwm_last_call = 0.0
TIKWM_MIN_INTERVAL = 1.2  # giây — hơn 1 giây một chút để an toàn

from playwright.sync_api import sync_playwright

# ─────────────────────────────────────────────
#  ĐỊNH NGHĨA THƯ MỤC & CẤU HÌNH
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
PROFILE_DIR = BASE_DIR / "profiles" / "tiktok_chrome_profile"
DOWNLOADS_DIR = BASE_DIR / "crawled_products"
CSV_FILE = DOWNLOADS_DIR / "products.csv"
JSON_FILE = DOWNLOADS_DIR / "products.json"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
PROFILE_DIR.mkdir(parents=True, exist_ok=True)

CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# Lock để đồng bộ việc ghi file
file_lock = threading.Lock()

# ─────────────────────────────────────────────
#  CÁC HÀM TIỆN ÍCH HỆ THỐNG
# ─────────────────────────────────────────────
def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def is_chrome_cdp_active(port):
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2)
        return resp.status == 200
    except Exception:
        return False

def find_chrome_path():
    if sys.platform != "win32":
        # Hỗ trợ cơ bản Linux/Mac nếu cần
        for c in ["google-chrome", "chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]:
            if subprocess.run(f"which {c}", shell=True, stdout=subprocess.DEVNULL).returncode == 0:
                return c
        return None
        
    for c in [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(c):
            return c
    return None

def clean_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name[:60].strip() or "product"

def normalize_tiktok_url(url):
    """
    Chuẩn hóa URL sản phẩm về dạng chuẩn: https://www.tiktok.com/shop/pdp/ID
    """
    url = url.strip()
    # Nếu là link rút gọn (không có dãy số 15-25 chữ số) hoặc link có dạng vt.tiktok.com / vm.tiktok.com
    if "tiktok.com" in url.lower() and not re.search(r'(\d{15,25})', url):
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            # Theo dõi redirect
            r = requests.get(url, headers=headers, allow_redirects=True, timeout=10)
            url = r.url
        except Exception:
            pass
            
    m = re.search(r'(\d{15,25})', url)
    if m:
        product_id = m.group(1)
        return f"https://www.tiktok.com/shop/pdp/{product_id}"
    return url

def _tikwm_api_call(video_url: str, api_key: str = None):
    """
    Gọi TikWM API.
    - Nếu có paid API key → gọi thẳng, không giới hạn tốc độ (đa luồng tự do).
    - Nếu dùng free → áp dụng rate limiting toàn cục 1 req/giây + auto-retry.
    """
    global _tikwm_last_call
    is_paid = bool(api_key and api_key.strip())
    MAX_RETRIES = 4 if not is_paid else 2

    for attempt in range(MAX_RETRIES):
        # Chỉ rate-limit ở gói free
        if not is_paid:
            with _tikwm_lock:
                now = time.time()
                wait = TIKWM_MIN_INTERVAL - (now - _tikwm_last_call)
                if wait > 0:
                    time.sleep(wait)
                _tikwm_last_call = time.time()

        headers = {}
        if is_paid:
            headers["x-tikwmapi-key"] = api_key.strip()
            url = "https://api.tikwmapi.com/"
            params = {"url": video_url, "hd": "1"}
            resp = requests.get(url, headers=headers, params=params, timeout=20)
        else:
            url = "https://www.tikwm.com/api/"
            data = {"url": video_url, "hd": "1"}
            resp = requests.post(url, data=data, timeout=20)

        if resp.status_code != 200:
            raise RuntimeError(f"TikWM HTTP error: {resp.status_code}")

        res_data = resp.json()
        if res_data.get("code") != 0:
            msg = res_data.get("msg", "Unknown error")
            # Chỉ retry khi bị rate limit ở gói free
            if not is_paid and "limit" in msg.lower() and attempt < MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise RuntimeError(f"TikWM API error: {msg}")

        return res_data

    raise RuntimeError("TikWM API error: Vượt quá số lần thử lại do giới hạn tốc độ.")


def download_video_and_voice_tikwm(video_url: str, output_dir: Path, api_key: str = None) -> str:
    """
    Tải video & âm thanh (voice) từ TikTok bằng TikWM API.
    Trả về đường dẫn tuyệt đối tới tệp MP3 lồng tiếng nếu thành công.
    """
    import shutil
    match = re.search(r'/video/(\d+)', video_url)
    if not match:
        match = re.search(r'(\d{15,25})', video_url)
    
    video_id = match.group(1) if match else f"video_{int(time.time())}"
    mp3_path = output_dir / f"{video_id}.mp3"
    
    if mp3_path.exists():
        return str(mp3_path.resolve())

    res_data = _tikwm_api_call(video_url, api_key)
    if res_data.get("code") != 0:
        raise RuntimeError(f"TikWM API error: {res_data.get('msg', 'Unknown error')}")
    data = res_data.get("data", {})
    
    # 1. Thử tải trực tiếp tệp âm thanh (MP3)
    music_url = data.get("music") or data.get("music_info", {}).get("play")
    if music_url:
        try:
            m_resp = requests.get(music_url, timeout=20)
            if m_resp.status_code == 200:
                mp3_path.write_bytes(m_resp.content)
                return str(mp3_path.resolve())
        except Exception:
            pass

    # 2. Nếu không lấy được mp3 trực tiếp, tải video không đóng dấu và dùng FFmpeg trích xuất
    video_play_url = data.get("play") or data.get("hdplay")
    if not video_play_url:
        raise RuntimeError("No play or hdplay URL found in TikWM response")

    temp_video_path = output_dir / f"{video_id}_temp.mp4"
    try:
        v_resp = requests.get(video_play_url, timeout=30)
        if v_resp.status_code != 200:
            raise RuntimeError(f"Failed to download temp video: {v_resp.status_code}")
        temp_video_path.write_bytes(v_resp.content)

        # Trích xuất âm thanh bằng FFmpeg
        cmd = [
            "ffmpeg", "-y", "-i", str(temp_video_path),
            "-q:a", "0", "-map", "a", str(mp3_path)
        ]
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = 0x08000000 # CREATE_NO_WINDOW
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=creation_flags)
        if res.returncode != 0:
            raise RuntimeError(f"FFmpeg error: {res.stderr[:200]}")
    finally:
        # Xóa video tạm
        if temp_video_path.exists():
            try: temp_video_path.unlink()
            except: pass

    if mp3_path.exists():
        return str(mp3_path.resolve())

    raise RuntimeError("Failed to extract audio using FFmpeg")

# ─────────────────────────────────────────────
#  JS SCRAPER ENGINE (EVALUATED IN BROWSER)
# ─────────────────────────────────────────────
SCRAPE_JS_SCRIPT = r"""
(function() {
    var result = {
        title: '',
        price: '',
        seller: '',
        description: '',
        images: [],
        product_id: '',
        url: window.location.href
    };
    
    // Lấy Product ID từ URL
    var m = window.location.href.match(/(\d{15,25})/);
    if (m) result.product_id = m[1];
    
    // 1. Tìm Tiêu đề
    var titleSels = [
        'h1', 
        '[data-e2e="product-title"]', 
        '[class*="ProductTitle"]', 
        '[class*="product-title"]', 
        '[class*="pdp-title"]', 
        '[class*="ProductName"]'
    ];
    for (var i = 0; i < titleSels.length; i++) {
        var el = document.querySelector(titleSels[i]);
        if (el && el.textContent.trim().length > 3) {
            result.title = el.textContent.trim();
            break;
        }
    }
    
    // 2. Tìm Giá sản phẩm
    var priceSels = [
        '[data-e2e="product-price"]',
        '[class*="Price"]',
        '[class*="price"]',
        '[class*="pdp-price"]'
    ];
    for (var i = 0; i < priceSels.length; i++) {
        var el = document.querySelector(priceSels[i]);
        if (el && el.textContent.trim().length >= 3) {
            result.price = el.textContent.trim();
            break;
        }
    }
    // Fallback nếu không có selector khớp: Tìm phần tử chứa ký tự ₫ hoặc $
    if (!result.price) {
        var bestPrice = '', bestPriceLen = 9999;
        var priceEls = document.querySelectorAll('span, div, p');
        for (var i = 0; i < Math.min(priceEls.length, 800); i++) {
            var el = priceEls[i];
            if (el.children.length > 3) continue;
            var txt = (el.innerText || el.textContent || '').trim();
            if (!txt || txt.length > 30 || txt.length < 3) continue;
            var pm = txt.match(/₫\s?([\d.,\-]+)/) || txt.match(/([\d.,\-]+)\s?₫/) || txt.match(/\$\s?([\d.,\-]+)/);
            if (pm && pm[1].length >= 3 && txt.length < bestPriceLen) {
                bestPrice = txt;
                bestPriceLen = txt.length;
            }
        }
        result.price = bestPrice;
    }
    
    // 3. Tìm Tên Shop/Seller
    var sellerFound = false;
    var allTextEls = document.querySelectorAll('span, a, div, p');
    for (var i = 0; i < Math.min(allTextEls.length, 600); i++) {
        var el = allTextEls[i];
        var txt = (el.innerText || el.textContent || '').trim();
        if (!txt || txt.length > 200) continue;
        var sm = txt.match(/Sold\s+by\s+(.+)/i) || txt.match(/Bán\s+bởi\s+(.+)/i);
        if (sm) {
            var name = sm[1].trim().replace(/[\d.]+\s*[★⭐]\s*\(?\d+\)?\s*\d*\s*sold.*/i, '').replace(/\d+\s*sold.*/i, '').trim();
            if (name.length > 1 && name.length < 80) {
                result.seller = name;
                sellerFound = true;
                break;
            }
        }
    }
    if (!sellerFound) {
        var shopLinks = document.querySelectorAll('a[href*="/shop/"], a[href*="/@"]');
        for (var i = 0; i < shopLinks.length; i++) {
            var t = (shopLinks[i].innerText || shopLinks[i].textContent || '').trim();
            if (t.length > 1 && t.length < 60 && !/sold|rating|review|đánh giá|lượt bán/i.test(t)) {
                result.seller = t;
                break;
            }
        }
    }
    
    // 4. Tìm Mô tả sản phẩm (Loại trừ đánh giá/reviews)
    var descText = '';
    var descHeadings = document.querySelectorAll('h1, h2, h3, h4, h5, [role="heading"]');
    for (var i = 0; i < descHeadings.length; i++) {
        var ht = (descHeadings[i].innerText || descHeadings[i].textContent || '').trim();
        if (/^(About this product|Product description|Mô tả sản phẩm|Chi tiết sản phẩm|Thông tin chi tiết)/i.test(ht)) {
            var container = descHeadings[i].parentElement;
            for (var up = 0; up < 4 && container; up++) {
                var sections = container.querySelectorAll('div');
                for (var s = 0; s < sections.length; s++) {
                    var sec = sections[s];
                    if (sec === descHeadings[i] || sec.contains(descHeadings[i])) continue;
                    var secText = (sec.innerText || sec.textContent || '').trim();
                    if (/global reviews|Verified purchase|reviews|Filter by|Sort by|Displaying.*of.*reviews|Photos from reviews|đánh giá từ|khách hàng/i.test(secText)) continue;
                    if (secText.length > 30 && secText.length < 5000) {
                        secText = secText.replace(/^(About this product|Product description|Mô tả sản phẩm|Chi tiết sản phẩm|Thông tin chi tiết)\s*/gi, '').trim();
                        if (secText.length > 30) {
                            descText = secText;
                            break;
                        }
                    }
                }
                if (descText) break;
                container = container.parentElement;
            }
            if (descText) break;
        }
    }
    if (!descText) {
        var descSels = [
            '[class*="product-description"]', 
            '[class*="ProductDescription"]',
            '[class*="pdp-description"]', 
            '[class*="about-product"]'
        ];
        for (var i = 0; i < descSels.length; i++) {
            var el = document.querySelector(descSels[i]);
            if (el) {
                var dt = (el.innerText || el.textContent || '').trim();
                if (dt.length > 20 && !/global reviews|Verified purchase|Sort by|đánh giá/i.test(dt)) {
                    descText = dt.replace(/^(About this product|Product description|Mô tả sản phẩm)\s*/gi, '').trim();
                    break;
                }
            }
        }
    }
    if (descText) {
        result.description = descText.substring(0, 3000);
    }
    
    // 5. Thu thập hình ảnh sản phẩm chính (Loại trừ bình luận, logo, gợi ý sản phẩm khác)
    var seen = {};
    var image_urls = [];
    
    function addImage(url) {
        if (!url || url.startsWith('data:') || url.length < 20) return;
        if (url.indexOf('ibyteimg.com') === -1 && url.indexOf('tiktokcdn.com') === -1) return;
        
        var cleanUrl = url.split('?')[0];
        
        // Bỏ qua các ảnh kích thước nhỏ (thumbnails)
        if (/\/\d{2,3}x\d{2,3}\//.test(cleanUrl) || 
            /\/50x50\/|\/32x32\/|\/48x48\/|\/100x100\/|\/200x200\/|\/150x150\//.test(cleanUrl)) {
            return;
        }
        
        // Bỏ qua logo, avatar, icons, hoặc các nút điều hướng
        if (cleanUrl.toLowerCase().includes("logo") || 
            cleanUrl.toLowerCase().includes("avatar") || 
            cleanUrl.toLowerCase().includes("icon") || 
            cleanUrl.toLowerCase().includes("button") || 
            cleanUrl.toLowerCase().includes("arrow")) {
            return;
        }
        
        if (!seen[cleanUrl]) {
            seen[cleanUrl] = true;
            image_urls.push(url); // Giữ url gốc
        }
    }
    
    // Cách 1: Trích xuất từ __MODERN_ROUTER_DATA__ (Chính xác 100%, chỉ lấy ảnh sản phẩm chính)
    try {
        var routerScript = document.getElementById('__MODERN_ROUTER_DATA__');
        if (routerScript) {
            var data = JSON.parse(routerScript.textContent.trim());
            var loaderData = data.loaderData || {};
            var pageData = null;
            for (var k in loaderData) {
                if (loaderData[k] && loaderData[k].page_config) {
                    pageData = loaderData[k];
                    break;
                }
            }
            if (pageData) {
                var components = pageData.page_config.components_map || [];
                for (var i = 0; i < components.length; i++) {
                    var comp = components[i];
                    if (comp.component_type === "product_info") {
                        var prodModel = comp.component_data.product_info.product_model || {};
                        
                        // Lấy tiêu đề và mô tả nếu DOM chưa lấy được
                        if (prodModel.name && !result.title) result.title = prodModel.name;
                        if (prodModel.description && !result.description) {
                            try {
                                var descBlocks = JSON.parse(prodModel.description);
                                var descTexts = [];
                                for (var b = 0; b < descBlocks.length; b++) {
                                    if (descBlocks[b].type === "text") {
                                        descTexts.push(descBlocks[b].text || "");
                                    } else if (descBlocks[b].type === "ul" && Array.isArray(descBlocks[b].content)) {
                                        descTexts = descTexts.concat(descBlocks[b].content);
                                    }
                                }
                                result.description = descTexts.join("\n");
                            } catch(e) {
                                result.description = prodModel.description;
                            }
                        }
                        
                        var imgs = prodModel.images || [];
                        for (var im = 0; im < imgs.length; im++) {
                            var urlList = imgs[im].url_list || [];
                            if (urlList.length > 0) {
                                addImage(urlList[0]);
                            }
                        }
                    }
                }
            }
        }
    } catch(e) {}
    
    // Cách 2: Trích xuất từ __UNIVERSAL_DATA_FOR_WEB__ or RENDER_DATA
    if (image_urls.length === 0) {
        try {
            var json_elements = [
                document.getElementById('__UNIVERSAL_DATA_FOR_WEB__'),
                document.getElementById('RENDER_DATA')
            ];
            for (var e = 0; e < json_elements.length; e++) {
                var el = json_elements[e];
                if (el) {
                    var data = JSON.parse(el.textContent.trim());
                    function searchMainProduct(obj) {
                        if (!obj) return false;
                        if (typeof obj === 'object') {
                            if (obj.component_type === 'product_info' || obj.product_info || obj.product_model) {
                                var model = obj.product_model || (obj.product_info ? obj.product_info.product_model : null);
                                if (model && Array.isArray(model.images)) {
                                    for (var im = 0; im < model.images.length; im++) {
                                        var urlList = model.images[im].url_list || [];
                                        if (urlList.length > 0) addImage(urlList[0]);
                                    }
                                    return true;
                                }
                            }
                            for (var k in obj) {
                                if (obj.hasOwnProperty(k) && typeof obj[k] === 'object') {
                                    if (searchMainProduct(obj[k])) return true;
                                }
                            }
                        }
                        return false;
                    }
                    searchMainProduct(data);
                }
            }
        } catch(e) {}
    }
    
    // Cách 3: Trích xuất từ DOM (Nếu các cách trên không có kết quả)
    if (image_urls.length === 0) {
        // Tìm vùng chứa gallery ảnh của sản phẩm bên trái
        var galleryContainer = document.querySelector('[class*="gallery"], [class*="carousel"], [class*="swiper"], [class*="ImageGallery"], [class*="ProductImage"], [class*="image-gallery"]');
        if (galleryContainer) {
            var imgs = galleryContainer.querySelectorAll('img');
            for (var j = 0; j < imgs.length; j++) {
                var src = imgs[j].src || imgs[j].getAttribute('data-src') || '';
                addImage(src);
            }
        }
        
        // Nếu vẫn không tìm thấy, quét tất cả img nhưng loại bỏ nghiêm ngặt các phần không liên quan
        if (image_urls.length === 0) {
            var imgs = document.querySelectorAll('img');
            for (var j = 0; j < imgs.length; j++) {
                var img = imgs[j];
                
                // Đi ngược lên cha để xem có thuộc phần gợi ý, bình luận, chân trang hay không
                var parent = img;
                var shouldSkip = false;
                for (var depth = 0; depth < 10 && parent; depth++) {
                    var cls = (parent.className || '').toString().toLowerCase();
                    var id = (parent.id || '').toString().toLowerCase();
                    var dataE2e = parent.getAttribute ? (parent.getAttribute('data-e2e') || '') : '';
                    if (/review|comment|recommend|related|youmay|similar|suggestion|rating|footer|feedback|similar|popup|modal|bình luận|đánh giá|gợi ý|đề xuất/i.test(cls) ||
                        /review|comment|recommend|related|youmay|similar|suggestion|rating|footer|feedback|bình luận|đánh giá|gợi ý|đề xuất/i.test(id) ||
                        /review|comment|recommend|related|youmay|similar|suggestion|rating/i.test(dataE2e)) {
                        shouldSkip = true;
                        break;
                    }
                    parent = parent.parentElement;
                }
                if (shouldSkip) continue;
                
                var w = img.naturalWidth || img.width || 0;
                var h = img.naturalHeight || img.height || 0;
                // Chỉ lấy ảnh kích thước lớn (ảnh sản phẩm chính thường >= 300x300)
                if (w >= 300 && h >= 300) {
                    var src = img.src || img.getAttribute('data-src') || '';
                    addImage(src);
                }
            }
        }
    }
    
    result.images = image_urls;
    return JSON.stringify(result);
})()
"""

# ─────────────────────────────────────────────
#  WORKER THREAD CRAWLER (XỬ LÝ ĐA LUỒNG)
# ─────────────────────────────────────────────
class CrawlWorker(QThread):
    # Các tín hiệu để gửi về GUI
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int) # completed, total
    product_done_signal = pyqtSignal(dict) # product_data dict
    finished_signal = pyqtSignal(int, int) # success, failed

    def __init__(self, urls, thread_count=2, target_folder="Chưa phân loại"):
        super().__init__()
        self.urls = urls
        self.thread_count = thread_count
        self.target_folder = target_folder
        self.is_running = True
        self.success_count = 0
        self.failed_count = 0
        self.completed_urls = 0
        self.total_urls = len(urls)
        self.tikwm_api_key = None

        # Hàng đợi URL thread-safe
        self.url_queue = urls.copy()
        self.queue_lock = threading.Lock()

    def run(self):
        self.log_signal.emit(f"🚀 Bắt đầu crawl {self.total_urls} sản phẩm với {self.thread_count} luồng...")
        
        threads = []
        for i in range(self.thread_count):
            t = threading.Thread(target=self.worker_loop, args=(i + 1,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        self.log_signal.emit(f"\n✨ ĐÃ HOÀN THÀNH TẤT CẢ! Thành công: {self.success_count} | Thất bại: {self.failed_count}")
        self.finished_signal.emit(self.success_count, self.failed_count)

    def stop(self):
        self.is_running = False

    def process_item(self, context, item, worker_id):
        # 1. Phân tích item nhập vào (string thô hoặc dict)
        if isinstance(item, str):
            if "|" in item:
                parts = item.split("|", 1)
                p_url = parts[0].strip()
                v_parts = [v.strip() for v in re.split(r'[;\s,]+', parts[1]) if v.strip()]
            else:
                p_url = item.strip()
                v_parts = []
        else:
            p_url = item.get("url", "").strip()
            v_parts = item.get("video_urls", [])

        # Chuẩn hóa URL sản phẩm
        norm_url = normalize_tiktok_url(p_url)
        m = re.search(r'(\d{15,25})', norm_url)
        if not m:
            self.log_signal.emit(f"❌ [Luồng {worker_id}] Link sản phẩm không hợp lệ: {p_url}")
            return None
        product_id = m.group(1)

        # 2. Kiểm tra xem sản phẩm đã có sẵn trong JSON chưa
        existing_data = None
        if JSON_FILE.exists():
            try:
                with file_lock:
                    all_products = json.loads(JSON_FILE.read_text("utf-8"))
                for prod in all_products:
                    if prod.get("product_id") == product_id:
                        safe_title = clean_filename(prod.get("title", "product"))
                        f_name = prod.get("folder", "Chưa phân loại")
                        prod_dir = DOWNLOADS_DIR / f"{product_id}_{safe_title}"
                        if f_name and f_name != "Chưa phân loại":
                            alt_dir = DOWNLOADS_DIR / f_name / f"{product_id}_{safe_title}"
                            if alt_dir.exists():
                                prod_dir = alt_dir
                        if prod_dir.exists():
                            existing_data = prod
                            break
            except Exception:
                pass

        product_data = None
        if existing_data and existing_data.get("status") == "Success":
            self.log_signal.emit(f"📦 [Luồng {worker_id}] Sản phẩm đã tồn tại trong kho cục bộ: {existing_data.get('title', '')[:40]}...")
            product_data = existing_data
        else:
            # Chỉ cào mới nếu chưa có sẵn hoặc chưa thành công
            product_data = self.scrape_single_product(context, norm_url, worker_id)

        if not product_data:
            return None

        # 3. Tải các video liên quan và convert sang MP3 lồng tiếng
        if v_parts:
            safe_title = clean_filename(product_data.get("title", "product"))
            folder_name = getattr(self, "target_folder", "Chưa phân loại")
            if folder_name and folder_name != "Chưa phân loại":
                prod_dir = DOWNLOADS_DIR / folder_name / f"{product_id}_{safe_title}"
            else:
                prod_dir = DOWNLOADS_DIR / f"{product_id}_{safe_title}"
            prod_dir.mkdir(parents=True, exist_ok=True)
            voice_dir = prod_dir / "voices"
            voice_dir.mkdir(parents=True, exist_ok=True)

            existing_voices = product_data.get("voices", [])
            new_voices = list(existing_voices)

            self.log_signal.emit(f"⏬ [Luồng {worker_id}] Đang tải {len(v_parts)} video liên quan từ TikWM...")
            for idx, v_url in enumerate(v_parts):
                try:
                    mp3_path = download_video_and_voice_tikwm(v_url, voice_dir, self.tikwm_api_key)
                    if mp3_path and mp3_path not in new_voices:
                        new_voices.append(mp3_path)
                        self.log_signal.emit(f"  ✔ [Luồng {worker_id}] Đã thêm file MP3: {Path(mp3_path).name}")
                except Exception as e:
                    self.log_signal.emit(f"  ❌ [Luồng {worker_id}] Lỗi tải/trích xuất {idx+1}: {str(e)}")

            product_data["voices"] = new_voices

            # Lưu lại vào storage
            with file_lock:
                self.save_to_local_storage(product_data)

        return product_data

    def worker_loop(self, worker_id):
        # Khởi tạo Playwright trong mỗi luồng riêng
        try:
            with sync_playwright() as p:
                self.log_signal.emit(f"[Luồng {worker_id}] Đang kết nối tới Chrome (CDP port 9222)...")
                try:
                    browser = p.chromium.connect_over_cdp(CDP_URL)
                except Exception as e:
                    self.log_signal.emit(f"❌ [Luồng {worker_id}] Lỗi kết nối Chrome CDP: Cần mở Chrome Spy trước! ({str(e)})")
                    return

                context = browser.contexts[0]

                while self.is_running:
                    # Lấy item tiếp theo từ hàng đợi
                    item = None
                    with self.queue_lock:
                        if self.url_queue:
                            item = self.url_queue.pop(0)

                    if not item:
                        break

                    # Lấy link sản phẩm để log
                    p_url = item
                    if isinstance(item, dict):
                        p_url = item.get("url", "")
                    elif isinstance(item, str) and "|" in item:
                        p_url = item.split("|", 1)[0].strip()

                    self.log_signal.emit(f"⏳ [Luồng {worker_id}] Đang xử lý sản phẩm: {p_url[:80]}...")
                    product_data = self.process_item(context, item, worker_id)

                    if product_data:
                        self.success_count += 1
                        self.product_done_signal.emit(product_data)
                    else:
                        self.failed_count += 1
                        self.product_done_signal.emit({"url": p_url, "status": "Failed"})

                    with self.queue_lock:
                        self.completed_urls += 1
                        self.progress_signal.emit(self.completed_urls, self.total_urls)

                try:
                    browser.close()
                except Exception:
                    pass
        except Exception as e:
            self.log_signal.emit(f"⚠️ [Luồng {worker_id}] Lỗi không mong muốn: {str(e)}")

    def scrape_single_product(self, context, url, worker_id):
        page = None
        try:
            # Chuẩn hóa URL
            norm_url = normalize_tiktok_url(url)
            
            # Tạo tab mới trong Chrome
            page = context.new_page()
            
            # Navigate tới trang sản phẩm
            page.goto(norm_url, wait_until="domcontentloaded", timeout=45000)
            
            # Chờ 4 giây để dữ liệu tải xong và bypass captcha nếu có
            page.wait_for_timeout(4000)
            
            # Kiểm tra xem có bị captcha không
            current_url = page.url
            page_title = page.title()
            body_text = page.locator("body").text_content() or ""
            
            is_captcha = ("security-check" in current_url or 
                          "security" in page_title.lower() or
                          "verify" in page_title.lower() or 
                          "captcha" in page_title.lower() or
                          "Verify to continue" in body_text or
                          "Drag the puzzle piece" in body_text)
                          
            if is_captcha:
                self.log_signal.emit(f"⚠️ [Luồng {worker_id}] Phát hiện CAPTCHA trên trang: {norm_url}. Vui lòng giải captcha trên Chrome nếu thấy tab này!")
                # Chờ thêm tối đa 30 giây để người dùng giải captcha thủ công trên màn hình Chrome đang mở
                try:
                    page.wait_for_function(
                        "() => !document.title.toLowerCase().includes('verify') && "
                        "!document.title.toLowerCase().includes('security') && "
                        "!window.location.href.includes('security-check') && "
                        "document.querySelector('h1, [data-e2e=\"product-title\"]') !== null",
                        timeout=30000
                    )
                    self.log_signal.emit(f"✅ [Luồng {worker_id}] Đã giải xong CAPTCHA. Tiếp tục cào...")
                    page.wait_for_timeout(2000)
                except Exception:
                    self.log_signal.emit(f"❌ [Luồng {worker_id}] Bỏ qua link do kẹt captcha (quá thời gian giải 30s): {norm_url}")
                    page.close()
                    return None

            # Thực thi đoạn code JS để trích xuất dữ liệu sản phẩm
            js_res_raw = page.evaluate(SCRAPE_JS_SCRIPT)
            page.close()
            page = None

            if not js_res_raw:
                return None
            
            scraped = json.loads(js_res_raw)
            title = scraped.get("title", "").strip()
            
            if not title or title == "Không tìm thấy tên":
                self.log_signal.emit(f"❌ [Luồng {worker_id}] Không lấy được tiêu đề sản phẩm: {norm_url}")
                return None

            product_id = scraped.get("product_id") or re.search(r'(\d{15,25})', norm_url).group(1)
            price = scraped.get("price", "Liên hệ")
            seller = scraped.get("seller", "TikTok Shop")
            description = scraped.get("description", "")
            images = scraped.get("images", [])

            # Dọn dẹp URL hình ảnh (loại bỏ tham số query, giữ ảnh gốc độ phân giải cao)
            cleaned_images = []
            for img in images:
                # Loại bỏ phần token/query phía sau ?
                clean_url = img.split("?")[0]
                # Lọc các ảnh quá nhỏ
                if not any(size in clean_url for size in ["/100x100/", "/200x200/", "/150x150/", "/50x50/", "/32x32/"]):
                    cleaned_images.append(img)
            cleaned_images = list(dict.fromkeys(cleaned_images)) # Loại bỏ ảnh trùng lặp

            self.log_signal.emit(f"💚 [Luồng {worker_id}] Đã lấy thông tin sản phẩm: {title[:40]}... (Ảnh: {len(cleaned_images)})")

            # Tải ảnh sản phẩm về thư mục cục bộ
            saved_image_paths = []
            if cleaned_images:
                # Đặt tên thư mục: <product_id>_<clean_title>
                safe_title = clean_filename(title)
                folder_name = getattr(self, "target_folder", "Chưa phân loại")
                if folder_name and folder_name != "Chưa phân loại":
                    prod_dir = DOWNLOADS_DIR / folder_name / f"{product_id}_{safe_title}"
                else:
                    prod_dir = DOWNLOADS_DIR / f"{product_id}_{safe_title}"
                prod_dir.mkdir(parents=True, exist_ok=True)
                
                img_dir = prod_dir / "images"
                img_dir.mkdir(parents=True, exist_ok=True)

                self.log_signal.emit(f"⏬ [Luồng {worker_id}] Đang tải {len(cleaned_images)} ảnh về thư mục...")
                for idx, img_url in enumerate(cleaned_images):
                    try:
                        # Thử tải ảnh gốc
                        resp = requests.get(img_url, timeout=15)
                        if resp.status_code == 200:
                            # Lưu tên ảnh tuần tự 1.jpg, 2.jpg...
                            img_path = img_dir / f"{idx + 1}.jpg"
                            img_path.write_bytes(resp.content)
                            saved_image_paths.append(str(img_path))
                    except Exception as e:
                        self.log_signal.emit(f"⚠️ [Luồng {worker_id}] Lỗi khi tải ảnh {idx+1}: {str(e)}")

            # Gộp kết quả
            data = {
                "product_id": product_id,
                "title": title,
                "price": price,
                "seller": seller,
                "description": description,
                "url": norm_url,
                "images_count": len(cleaned_images),
                "image_urls": cleaned_images,
                "local_images": saved_image_paths,
                "status": "Success",
                "folder": getattr(self, "target_folder", "Chưa phân loại")
            }

            # Lưu vào file CSV và JSON dùng chung (thread-safe lock)
            with file_lock:
                self.save_to_local_storage(data)

            return data

        except Exception as e:
            self.log_signal.emit(f"❌ [Luồng {worker_id}] Lỗi cào sản phẩm {url}: {str(e)}")
            if page:
                try: page.close()
                except Exception: pass
            return None

    def save_to_local_storage(self, data):
        # 1. Lưu vào file JSON
        all_products = []
        if JSON_FILE.exists():
            try:
                all_products = json.loads(JSON_FILE.read_text("utf-8"))
            except Exception:
                all_products = []

        # Cập nhật nếu trùng ID sản phẩm, không thì thêm mới
        pid = data["product_id"]
        updated = False
        for idx, item in enumerate(all_products):
            if item.get("product_id") == pid:
                # Bảo toàn các trường quan trọng không đổi
                for field in ["voices", "related_videos", "folder", "category"]:
                    if field in item and field not in data:
                        data[field] = item[field]
                    elif field in item and field == "folder" and data.get("folder") == "Chưa phân loại" and item.get("folder") != "Chưa phân loại":
                        data[field] = item[field]
                all_products[idx] = data
                updated = True
                break
        if not updated:
            all_products.append(data)
        
        try:
            JSON_FILE.write_text(json.dumps(all_products, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass

        # 2. Lưu vào file CSV (hỗ trợ Excel)
        write_header = not CSV_FILE.exists()
        try:
            with open(CSV_FILE, mode="a", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(["Product ID", "Tiêu đề", "Giá", "Cửa hàng", "Đường dẫn", "Số ảnh", "Mô tả"])
                writer.writerow([
                    data["product_id"],
                    data["title"],
                    data["price"],
                    data["seller"],
                    data["url"],
                    data["images_count"],
                    data["description"][:500] + "..." if len(data["description"]) > 500 else data["description"]
                ])
        except Exception:
            pass

# ─────────────────────────────────────────────
#  GIAO DIỆN CHÍNH (PYQT6 MAIN WINDOW)
# ─────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("📥 TikTok Shop Product Crawler v1.0")
        self.resize(1100, 750)
        self.setup_ui()
        self.check_chrome_status()

        # Timer kiểm tra định kỳ trạng thái Chrome
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.check_chrome_status)
        self.status_timer.start(5000)

        self.worker = None

    def setup_ui(self):
        # Phong cách giao diện tối Premium (Tương tự Grok Video Tool)
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f172a;
            }
            QWidget {
                color: #f1f5f9;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 13px;
            }
            QGroupBox {
                border: 1px solid #334155;
                border-radius: 8px;
                margin-top: 15px;
                padding-top: 15px;
                font-weight: bold;
                color: #38bdf8;
                background-color: #1e293b;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 15px;
                padding: 0 5px;
            }
            QPushButton {
                background-color: #4f46e5;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
                color: white;
            }
            QPushButton:hover {
                background-color: #6366f1;
            }
            QPushButton:pressed {
                background-color: #4338ca;
            }
            QPushButton#btn_spy {
                background-color: #059669;
            }
            QPushButton#btn_spy:hover {
                background-color: #10b981;
            }
            QPushButton#btn_stop {
                background-color: #dc2626;
            }
            QPushButton#btn_stop:hover {
                background-color: #ef4444;
            }
            QTextEdit, QLineEdit, QSpinBox {
                background-color: #0f172a;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 6px;
                color: #f8fafc;
            }
            QTextEdit:focus, QLineEdit:focus {
                border: 1px solid #6366f1;
            }
            QProgressBar {
                border: 1px solid #334155;
                border-radius: 6px;
                text-align: center;
                background-color: #0f172a;
                color: white;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6366f1, stop:1 #a855f7);
                border-radius: 5px;
            }
            QTableWidget {
                background-color: #1e293b;
                border: 1px solid #334155;
                gridline-color: #334155;
                border-radius: 8px;
            }
            QTableWidget::item {
                padding: 8px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #38bdf8;
                padding: 6px;
                font-weight: bold;
                border: 1px solid #334155;
            }
        """)

        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)

        # ── HÀNG TRÊN: TRẠNG THÁI CHROME & NÚT MỞ SPY CHROME ──
        header_layout = QHBoxLayout()
        self.lbl_chrome_status = QLabel("🔎 Đang kiểm tra trạng thái Chrome Spy...")
        self.lbl_chrome_status.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        header_layout.addWidget(self.lbl_chrome_status)
        
        self.btn_open_spy = QPushButton("🌐 MỞ CHROME SPY")
        self.btn_open_spy.setObjectName("btn_spy")
        self.btn_open_spy.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_open_spy.clicked.connect(self.action_open_chrome_spy)
        header_layout.addWidget(self.btn_open_spy)
        
        main_layout.addLayout(header_layout)

        # Cấu trúc chia đôi (Splitter) để tối ưu không gian hiển thị
        splitter = QSplitter(Qt.Orientation.Vertical)
        
        # ── KHU VỰC ĐẦU VÀO VÀ CẤU HÌNH CÀO (BÊN TRÊN) ──
        top_container = QWidget()
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        
        # Group nhập Links
        grp_inputs = QGroupBox("Danh sách liên kết sản phẩm (hoặc kéo thả file)")
        inputs_layout = QVBoxLayout(grp_inputs)
        
        self.txt_urls = QTextEdit()
        self.txt_urls.setPlaceholderText("Dán danh sách các link sản phẩm TikTok Shop vào đây (mỗi link trên 1 dòng)...\nHoặc nhấn 'Chọn file text' bên dưới.")
        inputs_layout.addWidget(self.txt_urls)
        
        btn_file_layout = QHBoxLayout()
        self.btn_import_file = QPushButton("📁 Chọn file chứa Links")
        self.btn_import_file.clicked.connect(self.action_import_file)
        btn_file_layout.addWidget(self.btn_import_file)
        
        self.lbl_file_path = QLabel("Chưa chọn file")
        self.lbl_file_path.setStyleSheet("color: #94a3b8;")
        btn_file_layout.addWidget(self.lbl_file_path)
        btn_file_layout.addStretch()
        inputs_layout.addLayout(btn_file_layout)
        top_layout.addWidget(grp_inputs, 3)

        # Group Cấu hình chạy
        grp_config = QGroupBox("Cấu hình Crawl")
        config_layout = QVBoxLayout(grp_config)
        
        # Số luồng (Tabs chạy song song)
        spin_layout = QHBoxLayout()
        spin_layout.addWidget(QLabel("Số luồng chạy song song:"))
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 5)
        self.spin_threads.setValue(2)
        spin_layout.addWidget(self.spin_threads)
        config_layout.addLayout(spin_layout)
        
        # Các nút Hành động
        config_layout.addSpacing(10)
        self.btn_start = QPushButton("🚀 BẮT ĐẦU CRAWL")
        self.btn_start.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.btn_start.setMinimumHeight(45)
        self.btn_start.clicked.connect(self.action_start_crawl)
        config_layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("🛑 DỪNG CÀO")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.action_stop_crawl)
        config_layout.addWidget(self.btn_stop)

        # Lối tắt thư mục đầu ra
        config_layout.addStretch()
        lbl_out_info = QLabel("Thư mục lưu trữ:")
        lbl_out_info.setStyleSheet("font-weight: bold; color: #a855f7;")
        config_layout.addWidget(lbl_out_info)
        
        btn_open_folder = QPushButton("📁 Mở thư mục sản phẩm")
        btn_open_folder.setStyleSheet("background-color: #334155;")
        btn_open_folder.clicked.connect(lambda: os.startfile(DOWNLOADS_DIR))
        config_layout.addWidget(btn_open_folder)
        
        top_layout.addWidget(grp_config, 1)
        splitter.addWidget(top_container)

        # ── KHU VỰC HIỂN THỊ KẾT QUẢ VÀ LOGS (BÊN DƯỚI) ──
        bottom_container = QWidget()
        bottom_layout = QHBoxLayout(bottom_container)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        
        # Bảng danh sách sản phẩm cào được
        grp_table = QGroupBox("Danh sách sản phẩm cào được")
        table_layout = QVBoxLayout(grp_table)
        
        self.table_products = QTableWidget()
        self.table_products.setColumnCount(6)
        self.table_products.setHorizontalHeaderLabels(["ID Sản phẩm", "Tiêu đề", "Giá", "Shop/Cửa hàng", "Ảnh tải về", "Trạng thái"])
        self.table_products.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table_products.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch) # Tiêu đề tự dãn
        table_layout.addWidget(self.table_products)
        
        bottom_layout.addWidget(grp_table, 2)
        
        # Log Panel
        grp_log = QGroupBox("Nhật ký tiến trình")
        log_layout = QVBoxLayout(grp_log)
        
        self.txt_logs = QTextEdit()
        self.txt_logs.setReadOnly(True)
        self.txt_logs.setStyleSheet("background-color: #020617; color: #38bdf8; font-family: Consolas, monospace; font-size: 11px;")
        log_layout.addWidget(self.txt_logs)
        
        bottom_layout.addWidget(grp_log, 1)
        splitter.addWidget(bottom_container)
        
        main_layout.addWidget(splitter)

        # ── DƯỚI CÙNG: THANH TIẾN TRÌNH VÀ THÔNG TIN ──
        progress_layout = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        progress_layout.addWidget(self.progress_bar)
        
        self.lbl_stats = QLabel("Đã cào: 0/0 | Thành công: 0 | Thất bại: 0")
        self.lbl_stats.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        progress_layout.addWidget(self.lbl_stats)
        
        main_layout.addLayout(progress_layout)

    # ─────────────────────────────────────────────
    #  LOGIC XỬ LÝ SỰ KIỆN TRÊN GIAO DIỆN
    # ─────────────────────────────────────────────
    def write_log(self, text):
        self.txt_logs.append(text)
        # Tự động cuộn xuống cuối
        self.txt_logs.ensureCursorVisible()

    def check_chrome_status(self):
        """
        Kiểm tra xem Chrome chạy debug port 9222 đã bật hay chưa để đổi màu label
        """
        if is_port_in_use(CDP_PORT) and is_chrome_cdp_active(CDP_PORT):
            self.lbl_chrome_status.setText("🟢 Trình duyệt Chrome Spy ĐANG HOẠT ĐỘNG (Port 9222)")
            self.lbl_chrome_status.setStyleSheet("color: #10b981;")
        else:
            self.lbl_chrome_status.setText("🔴 Trình duyệt Chrome Spy CHƯA BẬT (Hãy nhấn nút Mở Chrome Spy)")
            self.lbl_chrome_status.setStyleSheet("color: #ef4444;")

    def action_open_chrome_spy(self):
        self.write_log("[Chrome] Đang cố gắng khởi động Chrome với Profile & Cổng debug 9222...")
        success, message = self.launch_chrome_spy_process()
        if success:
            self.write_log(f"🟢 [Chrome] {message}")
            QMessageBox.information(self, "Chrome Spy", f"{message}\n\nBạn hãy sử dụng cửa sổ Chrome mới mở này để đăng nhập TikTok và Spy sản phẩm.")
        else:
            self.write_log(f"❌ [Chrome] {message}")
            QMessageBox.critical(self, "Lỗi Chrome Spy", f"Không mở được Chrome:\n{message}")
        self.check_chrome_status()

    def launch_chrome_spy_process(self):
        # Kiểm tra xem cổng đã bị dùng chưa
        if is_port_in_use(CDP_PORT):
            if is_chrome_cdp_active(CDP_PORT):
                return True, "Chrome Spy đã chạy sẵn trên cổng 9222."
            else:
                # Cố gắng tắt tiến trình chiếm cổng debug
                try:
                    self.write_log("⚠️ Cổng 9222 đang bị chiếm nhưng không phản hồi CDP. Đang dọn dẹp các tiến trình Chrome cũ...")
                    if sys.platform == "win32":
                        subprocess.run("taskkill /f /im chrome.exe", shell=True, capture_output=True)
                        time.sleep(1.5)
                except Exception:
                    pass
        
        chrome_path = find_chrome_path()
        if not chrome_path:
            return False, "Không tìm thấy đường dẫn cài đặt Google Chrome."
        
        # Mở rộng tham số khởi chạy Chrome
        cmd = [
            chrome_path,
            f"--user-data-dir={PROFILE_DIR.resolve()}",
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-default-apps",
            "--disable-blink-features=AutomationControlled",
            "https://www.tiktok.com"
        ]
        try:
            creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            subprocess.Popen(cmd, creationflags=creation_flags)
            return True, "Khởi động Chrome thành công."
        except Exception as e:
            return False, str(e)

    def action_import_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "Chọn file chứa liên kết sản phẩm", 
            "", 
            "Supported Files (*.txt *.csv *.xlsx *.xls);;Text Files (*.txt);;CSV Files (*.csv);;Excel Files (*.xlsx *.xls);;All Files (*)"
        )
        if file_path:
            self.lbl_file_path.setText(os.path.basename(file_path))
            try:
                urls = []
                ext = os.path.splitext(file_path)[1].lower()
                
                if ext in ['.xlsx', '.xls']:
                    self.write_log(f"📁 Đang đọc file Excel: {file_path}...")
                    import openpyxl
                    wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
                    sheet = wb.active
                    
                    # 1. Đọc hàng tiêu đề đầu tiên
                    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
                    link_col_idx = -1
                    
                    if header_row:
                        # Thử tìm cột dựa theo tên
                        for idx, val in enumerate(header_row):
                            if val:
                                val_str = str(val).strip().lower()
                                if any(term in val_str for term in ["liên kết sản phẩm", "link sản phẩm", "liên kết", "link", "url", "product link"]):
                                    link_col_idx = idx
                                    break
                        
                        # 2. Nếu không tìm thấy cột bằng tên, quét thử 10 hàng đầu để tìm cột chứa URL TikTok
                        if link_col_idx == -1:
                            for r_idx, row in enumerate(sheet.iter_rows(min_row=2, max_row=11, values_only=True)):
                                for c_idx, val in enumerate(row):
                                    if val and isinstance(val, str) and ("tiktok.com" in val or "shop.tiktok.com" in val):
                                        link_col_idx = c_idx
                                        break
                                if link_col_idx != -1:
                                    break
                    
                    # Nếu vẫn không xác định được, mặc định chọn cột cuối cùng
                    if link_col_idx == -1 and header_row:
                        link_col_idx = len(header_row) - 1
                        
                    if link_col_idx != -1:
                        # 3. Trích xuất tất cả URL từ cột được chỉ định
                        for row in sheet.iter_rows(min_row=2, values_only=True):
                            if len(row) > link_col_idx:
                                val = row[link_col_idx]
                                if val:
                                    val_str = str(val).strip()
                                    m = re.search(r'(https?://[^\s"\'<>\(\)]+)', val_str)
                                    if m:
                                        url = m.group(1)
                                        if "tiktok.com" in url or "shop.tiktok.com" in url:
                                            urls.append(url)
                    wb.close()
                else:
                    # Đọc file văn bản bình thường (txt, csv)
                    content = Path(file_path).read_text("utf-8-sig", errors="replace")
                    for line in content.splitlines():
                        line_str = line.strip()
                        if line_str:
                            m = re.search(r'(https?://[^\s"\'<>\(\)]+)', line_str)
                            if m:
                                url = m.group(1)
                                if "tiktok.com" in url or "shop.tiktok.com" in url:
                                    urls.append(url)
                            elif "tiktok.com" in line_str or "shop.tiktok.com" in line_str:
                                urls.append(line_str)
                
                # Loại bỏ trùng lặp
                urls = list(dict.fromkeys(urls))
                
                if urls:
                    self.txt_urls.setPlainText("\n".join(urls))
                    self.write_log(f"✅ Đã nạp thành công {len(urls)} links từ file: {file_path}")
                    
                    # Tự động bắt đầu crawl dữ liệu như yêu cầu
                    QTimer.singleShot(1000, self.action_start_crawl)
                else:
                    self.write_log(f"⚠️ Không tìm thấy liên kết TikTok Shop nào trong file: {file_path}")
                    QMessageBox.warning(self, "Không tìm thấy links", "Không tìm thấy liên kết sản phẩm TikTok Shop hợp lệ nào trong file đã chọn.")
                    
            except Exception as e:
                self.write_log(f"❌ Lỗi khi đọc file: {str(e)}")
                QMessageBox.critical(self, "Lỗi đọc file", f"Lỗi xảy ra khi đọc file:\n{str(e)}")

    def action_start_crawl(self):
        # 1. Đọc danh sách liên kết nhập vào
        raw_text = self.txt_urls.toPlainText().strip()
        if not raw_text:
            QMessageBox.warning(self, "Lỗi nhập liệu", "Vui lòng nhập ít nhất 1 liên kết sản phẩm để cào!")
            return

        urls = [normalize_tiktok_url(line.strip()) for line in raw_text.splitlines() if line.strip()]
        urls = list(dict.fromkeys(urls)) # Loại bỏ liên kết trùng lặp

        if not urls:
            QMessageBox.warning(self, "Lỗi nhập liệu", "Không tìm thấy liên kết hợp lệ!")
            return

        # 2. Kiểm tra trạng thái Chrome
        if not is_port_in_use(CDP_PORT) or not is_chrome_cdp_active(CDP_PORT):
            self.write_log("⚠️ Chrome Spy chưa được mở hoặc chưa bật CDP. Tự động cố gắng mở Chrome Spy...")
            success, _ = self.launch_chrome_spy_process()
            if success:
                self.write_log("⏳ Chờ Chrome Spy khởi động hoàn tất (3 giây)...")
                time.sleep(3)
                self.check_chrome_status()
            else:
                QMessageBox.critical(self, "Lỗi kết nối", "Vui lòng click nút 'Mở Chrome Spy' để chạy Chrome trước khi cào!")
                return

        # 3. Chuẩn bị giao diện
        self.table_products.setRowCount(0)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(len(urls))
        self.lbl_stats.setText(f"Đã cào: 0/{len(urls)} | Thành công: 0 | Thất bại: 0")
        
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_import_file.setEnabled(False)
        self.txt_urls.setEnabled(False)
        self.spin_threads.setEnabled(False)

        # 4. Khởi chạy tiến trình CrawlWorker chạy nền
        threads = self.spin_threads.value()
        self.worker = CrawlWorker(urls, thread_count=threads)
        self.worker.log_signal.connect(self.write_log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.product_done_signal.connect(self.update_table_row)
        self.worker.finished_signal.connect(self.crawl_finished)
        self.worker.start()

    def action_stop_crawl(self):
        if self.worker and self.worker.isRunning():
            self.write_log("🛑 Đang gửi yêu cầu dừng tiến trình cào...")
            self.worker.stop()
            self.btn_stop.setEnabled(False)

    def update_progress(self, completed, total):
        self.progress_bar.setValue(completed)
        # Thống kê sẽ được cập nhật liên tục qua hàm update_table_row

    def update_table_row(self, data):
        url = data.get("url")
        status = data.get("status")
        
        row = self.table_products.rowCount()
        self.table_products.insertRow(row)

        if status == "Success":
            # Thành công
            self.table_products.setItem(row, 0, QTableWidgetItem(data.get("product_id", "")))
            self.table_products.setItem(row, 1, QTableWidgetItem(data.get("title", "")))
            self.table_products.setItem(row, 2, QTableWidgetItem(data.get("price", "")))
            self.table_products.setItem(row, 3, QTableWidgetItem(data.get("seller", "")))
            self.table_products.setItem(row, 4, QTableWidgetItem(f"{data.get('images_count', 0)} ảnh"))
            
            status_item = QTableWidgetItem("Success")
            status_item.setForeground(QColor("#10b981")) # Màu xanh lục
            self.table_products.setItem(row, 5, status_item)
        else:
            # Thất bại
            self.table_products.setItem(row, 0, QTableWidgetItem("N/A"))
            self.table_products.setItem(row, 1, QTableWidgetItem(url[:50] + "..."))
            self.table_products.setItem(row, 2, QTableWidgetItem("N/A"))
            self.table_products.setItem(row, 3, QTableWidgetItem("N/A"))
            self.table_products.setItem(row, 4, QTableWidgetItem("0 ảnh"))
            
            status_item = QTableWidgetItem("Failed")
            status_item.setForeground(QColor("#ef4444")) # Màu đỏ
            self.table_products.setItem(row, 5, status_item)

        # Cập nhật Label thống kê dưới cùng
        success = 0
        failed = 0
        total_rows = self.table_products.rowCount()
        for r in range(total_rows):
            s_val = self.table_products.item(r, 5).text()
            if s_val == "Success":
                success += 1
            else:
                failed += 1
        
        total = self.progress_bar.maximum()
        self.lbl_stats.setText(f"Đã cào: {total_rows}/{total} | Thành công: {success} | Thất bại: {failed}")

    def crawl_finished(self, success, failed):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.btn_import_file.setEnabled(True)
        self.txt_urls.setEnabled(True)
        self.spin_threads.setEnabled(True)

        QMessageBox.information(
            self, "Hoàn thành", 
            f"Quá trình cào đã hoàn tất!\n\n"
            f"- Thành công: {success}\n"
            f"- Thất bại: {failed}\n\n"
            f"Kết quả được lưu tại:\n"
            f"- {CSV_FILE.name}\n"
            f"- {JSON_FILE.name}\n\n"
            f"Ảnh sản phẩm được lưu ở thư mục 'crawled_products'."
        )

# ─────────────────────────────────────────────
#  PHƯƠNG THỨC KHỞI CHẠY CHƯƠNG TRÌNH
# ─────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
