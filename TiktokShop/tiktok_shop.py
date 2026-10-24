#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tiktok_shop.py - TikTok Shop Crawler using Direct Chrome Cookie Decryption
========================================================================
Author: Antigravity
Usage:
  python tiktok_shop.py crawl "https://www.tiktok.com/shop/vn/pdp/1735963771375289697?source=anchor"
"""

import os
import sys
import io
import re
import json
import time
import base64
import shutil
import sqlite3
from pathlib import Path
from playwright.sync_api import sync_playwright

# Fix Windows console encoding issues for printing Vietnamese text
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

BASE_DIR = Path(__file__).parent.resolve()
COOKIE_JSON_FILE = BASE_DIR / "tiktok_extracted_cookies.json"


def get_chrome_user_agent():
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

def extract_chrome_tiktok_cookies():
    """
    Extracts and decrypts TikTok cookies from all Google Chrome profiles on Windows.
    This works even while Google Chrome is running.
    """
    if sys.platform != "win32":
        print("[Error] Tính năng giải mã cookie Chrome trực tiếp chỉ hỗ trợ hệ điều hành Windows.")
        return []
        
    import ctypes
    import ctypes.wintypes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    
    user_profile = os.environ.get("USERPROFILE", "")
    if not user_profile:
        print("[Error] Không tìm thấy thư mục USERPROFILE.")
        return []
        
    chrome_data = Path(user_profile) / "AppData" / "Local" / "Google" / "Chrome" / "User Data"
    if not chrome_data.exists():
        print(f"[Error] Không tìm thấy cấu hình Chrome tại: {chrome_data}")
        return []
        
    # 1. Read AES Key and Profile Display Names from Chrome Local State
    local_state_file = chrome_data / "Local State"
    if not local_state_file.exists():
        print("[Error] Không tìm thấy file Local State của Chrome.")
        return []
        
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _dpapi_decrypt(encrypted):
        if not encrypted or len(encrypted) < 4:
            return b""
        blob_in = DATA_BLOB(len(encrypted), ctypes.create_string_buffer(encrypted, len(encrypted)))
        blob_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
            raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
            return raw
        return b""

    aes_key = None
    profile_names = {}
    try:
        ls_data = json.loads(local_state_file.read_text("utf-8"))
        
        # Get AES Key for cookie decryption
        encrypted_key_b64 = ls_data.get("os_crypt", {}).get("encrypted_key", "")
        if encrypted_key_b64:
            encrypted_key = base64.b64decode(encrypted_key_b64)
            if encrypted_key[:5] == b"DPAPI":
                aes_key_blob = encrypted_key[5:]
                aes_key = _dpapi_decrypt(aes_key_blob)
                
        # Get Profile Display Names mapping
        info_cache = ls_data.get("profile", {}).get("info_cache", {})
        for folder, info in info_cache.items():
            name = info.get("name", "")
            user_name = info.get("user_name", "")
            display = name
            if user_name:
                display += f" ({user_name})"
            profile_names[folder] = display
            
    except Exception as e:
        print(f"[Error] Không giải mã được Chrome Local State: {e}")
        return []
        
    if not aes_key:
        print("[Error] Không lấy được khóa AES giải mã của Chrome.")
        return []

    def decrypt_value(encrypted):
        if not encrypted or len(encrypted) < 4:
            return ""
        if encrypted[:3] == b"v10" or encrypted[:3] == b"v20":
            try:
                nonce = encrypted[3:15]
                ciphertext = encrypted[15:]
                return AESGCM(aes_key).decrypt(nonce, ciphertext, None).decode("utf-8", errors="replace")
            except Exception:
                pass
        try:
            return _dpapi_decrypt(encrypted).decode("utf-8", errors="replace")
        except Exception:
            return ""

    # 2. Scan and list available Profile directories
    profile_dirs = [chrome_data / "Default"] + list(chrome_data.glob("Profile *"))
    profile_dirs = [d for d in profile_dirs if d.is_dir() and ((d / "Network" / "Cookies").exists() or (d / "Cookies").exists())]
    
    if not profile_dirs:
        print("[Error] Không tìm thấy bất kỳ thư mục profile nào chứa Cookies của Chrome.")
        return []
        
    # Ask user to choose one profile
    print("\n" + "="*70)
    print(" DANH SÁCH CÁC HỒ SƠ CHROME (PROFILES) TRÊN MÁY BẠN:")
    print("="*70)
    for idx, p_dir in enumerate(profile_dirs):
        display_name = profile_names.get(p_dir.name, "(Không rõ tên hồ sơ)")
        print(f" [{idx + 1}] {display_name:<40} (Hồ sơ: {p_dir.name})")
    print("="*70)
    
    target_dirs = []
    try:
        choice = input(f"👉 Chọn số thứ tự Hồ sơ bạn đã đăng nhập TikTok (1-{len(profile_dirs)}): ").strip()
        choice_idx = int(choice) - 1
        if 0 <= choice_idx < len(profile_dirs):
            target_dirs = [profile_dirs[choice_idx]]
            selected_name = profile_names.get(profile_dirs[choice_idx].name, profile_dirs[choice_idx].name)
            print(f"[Chrome] Bạn chọn trích xuất từ hồ sơ: {selected_name}")
        else:
            print("[Warning] Chọn sai số thứ tự, sẽ quét tất cả các profile.")
            target_dirs = profile_dirs
    except Exception:
        print("[Warning] Đầu vào không hợp lệ, sẽ quét tất cả các profile.")
        target_dirs = profile_dirs

    all_tiktok_cookies = []
    same_site_map = {0: "None", 1: "Lax", 2: "Strict"}
    
    print("\n[Chrome] Bắt đầu tìm kiếm phiên đăng nhập TikTok...")
    for p_dir in target_dirs:
        cookies_db = None
        for candidate in [
            p_dir / "Network" / "Cookies",
            p_dir / "Cookies"
        ]:
            if candidate.exists():
                cookies_db = candidate
                break
                
        if not cookies_db:
            continue
            
        tmp_db = BASE_DIR / "temp_cookies_db"
        try:
            # Try Windows native copy command which bypasses sharing locks more reliably
            copied = False
            if sys.platform == "win32":
                try:
                    import subprocess
                    # Using copy /y is extremely robust on Windows for copying locked sqlite files
                    cmd = f'copy /y "{str(cookies_db)}" "{str(tmp_db)}"'
                    res = subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    if res.returncode == 0 and tmp_db.exists():
                        copied = True
                except Exception:
                    pass
            
            if not copied:
                tmp_db.write_bytes(cookies_db.read_bytes())
                
            conn = sqlite3.connect(str(tmp_db))
            cur = conn.cursor()
            
            # Query cookies associated with tiktok.com (omitted same_site for backward compatibility)
            cur.execute(
                "SELECT host_key, name, path, is_secure, is_httponly, expires_utc, value, encrypted_value "
                "FROM cookies "
                "WHERE host_key LIKE '%tiktok.com'"
            )
            rows = cur.fetchall()
            conn.close()
            
            count = 0
            for host, name, path, secure, httponly, exp, value, encrypted_value in rows:
                val = value
                if not val and encrypted_value:
                    val = decrypt_value(encrypted_value)
                if val:
                    exp_s = exp / 1000000 - 11644473600 if exp > 0 else -1
                    all_tiktok_cookies.append({
                        "name": name,
                        "value": val,
                        "domain": host,
                        "path": path,
                        "secure": bool(secure),
                        "httpOnly": bool(httponly),
                        "expires": exp_s,
                        "sameSite": "Lax" # Default fallback safe value
                    })
                    count += 1
            if count > 0:
                print(f"  -> Profile [{p_dir.name}]: Đã lấy {count} cookies thành công.")
        except Exception as e:
            print(f"  -> Lỗi khi đọc profile {p_dir.name}: {e}")
        finally:
            if tmp_db.exists():
                try:
                    tmp_db.unlink()
                except Exception:
                    pass
                
    return all_tiktok_cookies

def parse_embedded_json(html_content):
    """
    Parses embedded JSON state scripts inside TikTok Shop PDP page.
    """
    product_info = {}
    images = []
    
    # 1. Try modern router data first
    try:
        modern_match = re.search(r'<script\s+[^>]*id="__MODERN_ROUTER_DATA__"[^>]*>(.*?)</script>', html_content, re.DOTALL)
        if modern_match:
            data = json.loads(modern_match.group(1).strip())
            loader_data = data.get("loaderData", {})
            page_data = None
            for k, v in loader_data.items():
                if isinstance(v, dict) and "page_config" in v:
                    page_data = v
                    break
            if page_data:
                components = page_data.get("page_config", {}).get("components_map", [])
                for comp in components:
                    comp_type = comp.get("component_type")
                    comp_data = comp.get("component_data", {})
                    
                    if comp_type == "product_info":
                        prod_model = comp_data.get("product_info", {}).get("product_model", {})
                        title = prod_model.get("name")
                        if title:
                            product_info["title"] = title
                            
                        # Seller/Shop
                        shop_name = comp_data.get("shop_info", {}).get("shop_name")
                        if shop_name:
                            product_info["seller"] = shop_name
                            
                        # Description
                        desc_raw = prod_model.get("description")
                        if desc_raw:
                            try:
                                desc_blocks = json.loads(desc_raw)
                                desc_texts = []
                                for block in desc_blocks:
                                    if block.get("type") == "text":
                                        desc_texts.append(block.get("text", ""))
                                    elif block.get("type") == "ul" and isinstance(block.get("content"), list):
                                        desc_texts.extend(block.get("content"))
                                product_info["description"] = "\n".join(desc_texts)
                            except:
                                product_info["description"] = desc_raw
                                
                        # Price
                        price_info = comp_data.get("product_info", {}).get("promotion_model", {}).get("promotion_product_price", {}).get("range_price", {})
                        if not price_info:
                            price_info = comp_data.get("promotion_tag", {}).get("promotion_product_price", {}).get("range_price", {})
                        if price_info:
                            range_p = price_info.get("range_price")
                            symbol = price_info.get("currency_symbol", "đ")
                            if range_p:
                                product_info["price"] = f"{symbol}{range_p}"
                            else:
                                min_p = price_info.get("min_price_integer_part_format")
                                max_p = price_info.get("max_price_integer_part_format")
                                if min_p and max_p and min_p != max_p:
                                    product_info["price"] = f"{symbol}{min_p} - {max_p}"
                                elif min_p:
                                    product_info["price"] = f"{symbol}{min_p}"
                                    
                        # Images
                        imgs = prod_model.get("images", [])
                        for img in imgs:
                            url_list = img.get("url_list", [])
                            if url_list:
                                images.append(url_list[0])
                                
                    elif comp_type == "shop_info":
                        shop_name = comp_data.get("shop_name")
                        if shop_name:
                            product_info["seller"] = shop_name
    except Exception as e:
        print(f"[Warning] Lỗi khi phân tích __MODERN_ROUTER_DATA__: {e}")

    # 2. Generic fallback parser
    try:
        json_patterns = [
            r'<script\s+[^>]*id="__UNIVERSAL_DATA_FOR_WEB__"[^>]*>(.*?)</script>',
            r'<script\s+[^>]*id="RENDER_DATA"[^>]*>(.*?)</script>',
            r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});'
        ]
        
        for pattern in json_patterns:
            matches = re.findall(pattern, html_content, re.DOTALL)
            for match in matches:
                try:
                    data = json.loads(match.strip())
                    
                    def find_keys_recursive(obj, target_keys):
                        results = {k: None for k in target_keys}
                        def search(item):
                            if isinstance(item, dict):
                                for k, v in item.items():
                                    if k in target_keys and results[k] is None:
                                        results[k] = v
                                    search(v)
                            elif isinstance(item, list):
                                for sub in item:
                                    search(sub)
                        search(obj)
                        return results
                    
                    extracted = find_keys_recursive(data, [
                        "title", "productTitle", "product_title", 
                        "price", "salePrice", "sale_price", 
                        "shopName", "shop_name", "sellerName", "seller_name",
                        "description", "product_description"
                    ])
                    
                    title = extracted.get("title") or extracted.get("productTitle") or extracted.get("product_title")
                    price = extracted.get("price") or extracted.get("salePrice") or extracted.get("sale_price")
                    seller = extracted.get("shopName") or extracted.get("shop_name") or extracted.get("sellerName") or extracted.get("seller_name")
                    desc = extracted.get("description") or extracted.get("product_description")
                    
                    if title and not product_info.get("title"):
                        product_info["title"] = title
                    if price and not product_info.get("price"):
                        product_info["price"] = price
                    if seller and not product_info.get("seller"):
                        product_info["seller"] = seller
                    if desc and not product_info.get("description"):
                        product_info["description"] = desc
                        
                    img_urls = re.findall(r'https://[a-zA-Z0-9-]+\.(?:ibyteimg|tiktokcdn)\.com/[a-zA-Z0-9_/~.-]+', match)
                    for url in img_urls:
                        clean_url = url.replace("\\u002F", "/").replace("\\", "")
                        if "~tplv" in clean_url or ".image" in clean_url or "/obj/" in clean_url:
                            images.append(clean_url)
                            
                    if product_info.get("title"):
                        break
                except Exception:
                    continue
                    
    except Exception as e:
        print(f"[Warning] Lỗi khi phân tích JSON fallback: {e}")
        
    return product_info, list(dict.fromkeys(images))

def normalize_cookies(raw_cookies):
    """
    Normalizes cookies loaded from JSON to the format expected by Playwright.
    Supports standard EditThisCookie exports and Playwright formats.
    """
    normalized = []
    if not isinstance(raw_cookies, list):
        return []
        
    for c in raw_cookies:
        if not isinstance(c, dict):
            continue
            
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain")
        path = c.get("path", "/")
        
        if not name or not value or not domain:
            continue
            
        # Map expirationDate (EditThisCookie) to expires
        expires = c.get("expires")
        if expires is None:
            expires = c.get("expirationDate")
        if expires is not None:
            try:
                expires = float(expires)
            except Exception:
                expires = -1
        else:
            expires = -1
            
        secure = c.get("secure", True)
        http_only = c.get("httpOnly", c.get("http_only", False))
        
        normalized_cookie = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "secure": bool(secure),
            "httpOnly": bool(http_only)
        }
        if expires > 0:
            normalized_cookie["expires"] = expires
            
        # Map sameSite values to standard Playwright values
        same_site_raw = c.get("sameSite")
        if same_site_raw:
            same_site_lower = str(same_site_raw).lower()
            if same_site_lower == "no_restriction":
                normalized_cookie["sameSite"] = "None"
            elif same_site_lower in ["lax", "strict", "none"]:
                normalized_cookie["sameSite"] = same_site_lower.capitalize()
                
        normalized.append(normalized_cookie)
        
    return normalized

def _crawl_product_internal(url, show_browser=False):
    """
    Crawls product info from TikTok Shop PDP URL using active decrypted Chrome cookies.
    """
    cookies = []
    
    # 1. Try to load cached/exported cookies from JSON file first
    if COOKIE_JSON_FILE.exists():
        try:
            raw_data = json.loads(COOKIE_JSON_FILE.read_text("utf-8"))
            # Normalize cookies if they come from standard extension exports
            cookies = normalize_cookies(raw_data)
            if cookies:
                _safe_log(f"[Cookie] Đã nạp {len(cookies)} cookies từ file lưu trữ {COOKIE_JSON_FILE.name}")
        except Exception as e:
            _safe_log(f"[Warning] Không đọc được file cookies cache: {e}")
            
    # 2. If cache is empty, extract directly from Chrome profiles (requires Chrome to be closed)
    if not cookies:
        cookies = extract_chrome_tiktok_cookies()
        if cookies:
            try:
                COOKIE_JSON_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), "utf-8")
                _safe_log(f"[Cookie] Giải mã thành công và lưu cache vào file: {COOKIE_JSON_FILE.name}")
            except Exception as e:
                _safe_log(f"[Warning] Không ghi được file cache: {e}")
        else:
            _safe_log("[Error] Không thể giải mã cookies từ Chrome.")
            return None
    
    # Check if we successfully got any login session cookies
    has_session = any(c["name"] in ["sessionid", "sessionid_ss"] for c in cookies)
    if not has_session:
        _safe_log("[Warning] Không tìm thấy cookie phiên đăng nhập TikTok.")
    else:
        _safe_log("[Chrome] Đã tìm thấy cookie phiên đăng nhập TikTok hợp lệ!")

    _safe_log(f"[CRAWL] Đang tải trang sản phẩm bằng Playwright: {url}")
    
    with sync_playwright() as p:
        # Launch headless browser
        browser = p.chromium.launch(
            headless=not show_browser,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        # Create standard context
        context = browser.new_context(
            user_agent=get_chrome_user_agent(),
            viewport={"width": 1280, "height": 800}
        )
        
        # Inject extracted cookies into context
        if cookies:
            context.add_cookies(cookies)
            
        page = context.new_page()
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        
        try:
            # Go to product URL
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # Check for security captcha redirect
            page.wait_for_timeout(2000)
            current_url = page.url
            page_title = page.title()
            
            body_text = page.locator("body").text_content() or ""
            is_captcha = ("security-check" in current_url or 
                          "security" in page_title.lower() or
                          "verify" in page_title.lower() or 
                          "captcha" in page_title.lower() or
                          page.locator("iframe[src*='security-check']").count() > 0 or 
                          page.locator(".captcha-puzzle").count() > 0 or
                          "Verify to continue" in body_text or
                          "Drag the puzzle piece" in body_text)
            
            if is_captcha:
                _safe_log("⚠ PHÁT HIỆN YÊU CẦU CAPTCHA / BẢO MẬT!")
                if not show_browser:
                    context.close()
                    browser.close()
                    return "TRIGGER_CAPTCHA"
                else:
                    _safe_log("-> Vui lòng giải CAPTCHA trên màn hình trình duyệt...")
                    try:
                        page.wait_for_function(
                            "() => !document.title.toLowerCase().includes('verify') && "
                            "!document.title.toLowerCase().includes('security') && "
                            "!window.location.href.includes('security-check') && "
                            "document.querySelector('h1, [data-e2e=\"product-title\"]') !== null",
                            timeout=120000
                        )
                        _safe_log("[OK] Đã giải xong CAPTCHA!")
                        page.wait_for_timeout(3000)
                    except Exception as ex:
                        _safe_log(f"[Warning] Chờ giải CAPTCHA hết hạn hoặc có lỗi: {ex}")
            
            # Wait for content to load
            page.wait_for_timeout(3000)
            
            html = page.content()
            
            # Extract details
            info, images = parse_embedded_json(html)
            
            # DOM selector fallback
            if not info.get("title"):
                title_el = page.locator("h1, [data-e2e='product-title'], [class*='Title'], [class*='title']").first
                if title_el.count() > 0:
                    info["title"] = title_el.text_content().strip()
                    
            if not info.get("price"):
                price_el = page.locator("[data-e2e='product-price'], [class*='Price'], [class*='price']").first
                if price_el.count() > 0:
                    info["price"] = price_el.text_content().strip()
                    
            if not info.get("seller"):
                seller_el = page.locator("[data-e2e='seller-name'], [class*='ShopName'], [class*='StoreName'], [class*='seller'], [class*='shop']").first
                if seller_el.count() > 0:
                    info["seller"] = seller_el.text_content().strip()

            if not info.get("description"):
                desc_el = page.locator("[class*='Description'], [class*='description'], [class*='desc']").first
                if desc_el.count() > 0:
                    info["description"] = desc_el.text_content().strip()

            if not images:
                img_els = page.locator("img").all()
                for el in img_els:
                    src = el.get_attribute("src") or el.get_attribute("data-src")
                    if src and ("ibyteimg.com" in src or "tiktokcdn.com" in src):
                        if "~tplv" in src or ".image" in src:
                            images.append(src)
            
            # Clean images
            cleaned_images = []
            for img in images:
                cleaned_url = img.split("?")[0]
                if not any(size in cleaned_url for size in ["/100x100/", "/200x200/", "/150x150/"]):
                    cleaned_images.append(cleaned_url)
            cleaned_images = list(dict.fromkeys(cleaned_images))
            
            product_data = {
                "product_info": {
                    "title": info.get("title", "Không tìm thấy tên"),
                    "price": info.get("price", "Không tìm thấy giá"),
                    "seller": info.get("seller", "Không tìm thấy cửa hàng"),
                    "description": info.get("description", "Không tìm thấy mô tả"),
                    "product_id": re.search(r'(\d{15,25})', url).group(1) if re.search(r'(\d{15,25})', url) else ""
                },
                "image_urls": cleaned_images
            }
            
            # Save cookies to JSON local file for debug/backup
            try:
                COOKIE_JSON_FILE.write_text(json.dumps(cookies, indent=2, ensure_ascii=False), "utf-8")
            except:
                pass
                
            context.close()
            browser.close()
            return product_data
            
        except Exception as e:
            _safe_log(f"[Error] Lỗi khi cào dữ liệu sản phẩm: {e}")
            try:
                context.close()
            except:
                pass
            try:
                browser.close()
            except:
                pass
            return None

def crawl_product(url, show_browser=False):
    """
    Crawls product info. Runs headless first; if captcha triggered, runs headed.
    """
    res = _crawl_product_internal(url, show_browser=show_browser)
    if res == "TRIGGER_CAPTCHA":
        return _crawl_product_internal(url, show_browser=True)
    return res

def open_spy_browser(log_func=None):
    """
    Launches real Chrome browser with a dedicated profile and auto-installs
    TikTok Downloader extension from Chrome Web Store.
    
    - First launch: Opens Chrome Web Store page for user to install extension.
    - Subsequent launches: Extension is already in profile, auto-loaded.
    - Non-blocking: Chrome runs in background, Python returns immediately.
    """
    import json
    import subprocess
    
    # Safe print wrapper – stdout may be closed when called from GUI
    def _log(msg):
        if log_func:
            try: log_func(msg)
            except: pass
        try:
            print(msg)
        except (ValueError, OSError):
            pass
    
    EXTENSION_CWS_URL = "https://chromewebstore.google.com/detail/tiktok-downloader-sort-an/penohjgblobinadflplocjekaclnoemh"
    EXTENSION_ID = "penohjgblobinadflplocjekaclnoemh"
    
    # 0. Self-healing process cleanup: terminate any hung Chrome using this profile
    try:
        cmd = 'wmic process where "name=\'chrome.exe\'" get CommandLine,ProcessId /format:csv'
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        pids_to_kill = []
        for line in res.stdout.splitlines():
            if 'tiktok_chrome_profile' in line and 'python' not in line.lower() and 'wmic' not in line.lower():
                parts = line.split(',')
                if parts and parts[-1].strip().isdigit():
                    pids_to_kill.append(parts[-1].strip())
        for pid in pids_to_kill:
            subprocess.run(f'taskkill /F /PID {pid}', shell=True, capture_output=True)
        if pids_to_kill:
            _log(f"[Chrome TikTok] Đã dọn dẹp {len(pids_to_kill)} tiến trình Chrome cũ bị treo.")
            import time
            time.sleep(1)
    except Exception:
        pass

    # 1. Find Chrome executable
    chrome_path = None
    import os
    for c in [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]:
        if os.path.exists(c):
            chrome_path = c
            break
    
    if not chrome_path:
        _log("[Chrome TikTok] ❌ Không tìm thấy Chrome! Hãy cài Google Chrome.")
        return False
    
    # 2. Setup dedicated profile directory
    profile_dir = Path.home() / ".tiktok_img_tool" / "profiles" / "tiktok_chrome_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    
    # 3. Determine if extension has been installed before
    ext_marker_file = Path.home() / ".tiktok_img_tool" / "ext_installed.json"
    ext_installed = False
    if ext_marker_file.exists():
        try:
            marker_data = json.loads(ext_marker_file.read_text("utf-8"))
            ext_installed = marker_data.get(EXTENSION_ID, False)
        except Exception:
            pass
    
    # Also check if extension exists in profile's Extensions folder
    ext_profile_path = profile_dir / "Default" / "Extensions" / EXTENSION_ID
    try:
        if ext_profile_path.exists() and any(ext_profile_path.iterdir()):
            ext_installed = True
            # Update marker file
            try:
                marker_data = {}
                if ext_marker_file.exists():
                    try:
                        marker_data = json.loads(ext_marker_file.read_text("utf-8"))
                    except Exception:
                        pass
                marker_data[EXTENSION_ID] = True
                ext_marker_file.parent.mkdir(parents=True, exist_ok=True)
                ext_marker_file.write_text(json.dumps(marker_data, indent=2), "utf-8")
            except Exception:
                pass
    except (OSError, StopIteration):
        pass
    
    # 4. Check if port 9222 is already occupied by another process
    cdp_port = 9222
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(('127.0.0.1', cdp_port))
        s.close()
        if result == 0:
            # Port is in use - check if it's OUR Chrome (tiktok_chrome_profile)
            _log(f"[Chrome TikTok] ⚠️ Port {cdp_port} đang bị chiếm. Đang kiểm tra...")
            try:
                import urllib.request
                resp = urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/version", timeout=2)
                ver_info = json.loads(resp.read().decode())
                user_agent = ver_info.get("Browser", "")
                ws_url = ver_info.get("webSocketDebuggerUrl", "")
                _log(f"[Chrome TikTok] Port {cdp_port} → {user_agent}")
                
                # Check if it's our profile by looking at the tabs
                resp2 = urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json", timeout=2)
                tabs = json.loads(resp2.read().decode())
                is_our_chrome = False
                for t in tabs:
                    tu = t.get("url", "")
                    if "tiktok" in tu.lower() or "shop.tiktok" in tu.lower():
                        is_our_chrome = True
                        break
                
                if is_our_chrome:
                    _log(f"[Chrome TikTok] ✅ Chrome TikTok đang chạy. Không cần mở lại.")
                    # Just inject panel
                    try:
                        inject_tool_panel(port=cdp_port, log_func=_log)
                    except Exception:
                        pass
                    return True
                else:
                    _log(f"[Chrome TikTok] ⚠️ Chrome KHÁC đang chiếm port {cdp_port}. Đang tắt...")
                    # Kill Chrome processes on this port
                    try:
                        cmd = f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr :{cdp_port} ^| findstr LISTENING\') do taskkill /F /PID %a'
                        subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
                        time.sleep(2)
                        _log(f"[Chrome TikTok] ✅ Đã giải phóng port {cdp_port}")
                    except Exception:
                        _log(f"[Chrome TikTok] ⚠️ Không tắt được. Hãy đóng Chrome khác thủ công.")
            except Exception:
                # Can't determine, try to free the port
                try:
                    cmd = f'for /f "tokens=5" %a in (\'netstat -aon ^| findstr :{cdp_port} ^| findstr LISTENING\') do taskkill /F /PID %a'
                    subprocess.run(cmd, shell=True, capture_output=True, timeout=5)
                    time.sleep(2)
                except Exception:
                    pass
    except Exception:
        pass
    
    # 5. Build Chrome launch command
    chrome_args = [
        chrome_path,
        f"--user-data-dir={profile_dir.resolve()}",
        f"--remote-debugging-port={cdp_port}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-blink-features=AutomationControlled",
    ]
    
    # 5. Determine which URLs to open
    if ext_installed:
        # Extension already installed → go straight to TikTok
        chrome_args.append("https://www.tiktok.com")
        _log("[Chrome TikTok] ✅ Extension đã cài đặt. Mở TikTok...")
    else:
        # First time → open extension install page + TikTok
        chrome_args.append(EXTENSION_CWS_URL)
        chrome_args.append("https://www.tiktok.com")
        _log("[Chrome TikTok] 📦 Lần đầu: Mở trang cài đặt Extension...")
        _log("[Chrome TikTok] → Hãy bấm 'Add to Chrome' để cài extension: TikTok Downloader, Sort and Filter")
    
    # 6. Launch Chrome (non-blocking)
    try:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        subprocess.Popen(chrome_args, creationflags=creation_flags)
        _log(f"[Chrome TikTok] 🚀 Chrome đã mở với profile riêng: {profile_dir}")
        # Inject floating panel after Chrome starts (wait for CDP)
        import threading as _th
        def _delayed_inject():
            time.sleep(4)  # Wait for Chrome to start
            try:
                inject_tool_panel(log_func=_log)
            except Exception as ex:
                _log(f"[Chrome TikTok] ⚠️ Inject panel: {ex}")
        _th.Thread(target=_delayed_inject, daemon=True).start()
        return True
    except Exception as e:
        _log(f"[Chrome TikTok] ❌ Không mở được Chrome: {e}")
        return False


# ═══════════════════════════════════════════════════════════
#  HTTP Bridge Server – receives URLs from Chrome panel
# ═══════════════════════════════════════════════════════════
_bridge_callbacks = []  # list of callable(url)

def register_bridge_callback(fn):
    """Register a callback to receive URLs from Chrome panel."""
    if fn not in _bridge_callbacks:
        _bridge_callbacks.append(fn)

def _start_bridge_server(port=9223):
    """Start a tiny HTTP server to receive URLs from the Chrome floating panel."""
    import http.server
    from urllib.parse import urlparse, parse_qs

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if '/send' in self.path:
                qs = parse_qs(urlparse(self.path).query)
                url = qs.get('url', [''])[0]
                if url:
                    for cb in _bridge_callbacks:
                        try: cb(url)
                        except: pass
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "received": url}).encode())
            elif '/ping' in self.path:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(404)
                self.end_headers()
        def do_OPTIONS(self):
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', '*')
            self.end_headers()
        def log_message(self, format, *args):
            pass  # Suppress log

    try:
        server = http.server.HTTPServer(('127.0.0.1', port), Handler)
        server.serve_forever()
    except OSError:
        pass  # Port already in use (server already running)

_bridge_started = False
def ensure_bridge_server(port=9223):
    """Ensure the bridge server is running (idempotent)."""
    global _bridge_started
    if _bridge_started:
        return
    _bridge_started = True
    import threading
    threading.Thread(target=_start_bridge_server, args=(port,), daemon=True).start()


# ═══════════════════════════════════════════════════════════
#  Inject floating panel into Chrome via CDP
# ═══════════════════════════════════════════════════════════
_INJECT_JS = r"""
(function() {
    if (document.getElementById('kol-tool-panel')) return;
    var d = document.createElement('div');
    d.id = 'kol-tool-panel';
    d.innerHTML = '<div id="kol-panel-main" style="position:fixed;bottom:16px;right:16px;z-index:2147483647;background:linear-gradient(135deg,#1a1a2e,#16213e);border:2px solid #6d28d9;border-radius:14px;padding:14px;width:340px;box-shadow:0 8px 32px rgba(109,40,217,0.3);font-family:system-ui,sans-serif;transition:all 0.3s;">'
        + '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">'
        + '<span style="color:#38bdf8;font-weight:bold;font-size:14px;">📥 KOL Studio</span>'
        + '<div><button id="kol-min-btn" style="background:none;border:none;color:#888;cursor:pointer;font-size:16px;" title="Thu nhỏ">—</button></div>'
        + '</div>'
        + '<input id="kol-url-input" type="text" placeholder="Dán link sản phẩm TikTok Shop..." style="width:100%;padding:9px 10px;border:1px solid #3d3d6a;border-radius:8px;background:#0d0d1e;color:white;font-size:12px;box-sizing:border-box;margin-bottom:8px;outline:none;">'
        + '<div style="display:flex;gap:6px;">'
        + '<button id="kol-send-btn" style="flex:1;padding:8px;background:linear-gradient(90deg,#6d28d9,#a855f7);color:white;border:none;border-radius:8px;font-weight:bold;cursor:pointer;font-size:12px;transition:opacity 0.2s;">🚀 Gửi về Tool</button>'
        + '<button id="kol-auto-btn" style="padding:8px 10px;background:#059669;color:white;border:none;border-radius:8px;cursor:pointer;font-size:11px;transition:opacity 0.2s;" title="Lấy link trang hiện tại">📋 Trang này</button>'
        + '</div>'
        + '<div id="kol-status" style="color:#888;font-size:10px;margin-top:6px;min-height:14px;"></div>'
        + '</div>'
        + '<div id="kol-panel-mini" style="display:none;position:fixed;bottom:16px;right:16px;z-index:2147483647;cursor:pointer;" title="Mở KOL Studio panel">'
        + '<div style="background:linear-gradient(135deg,#6d28d9,#a855f7);border-radius:50%;width:48px;height:48px;display:flex;align-items:center;justify-content:center;box-shadow:0 4px 16px rgba(109,40,217,0.4);font-size:20px;transition:transform 0.2s;">📥</div>'
        + '</div>';
    document.body.appendChild(d);
    var main = document.getElementById('kol-panel-main');
    var mini = document.getElementById('kol-panel-mini');
    document.getElementById('kol-min-btn').onclick = function() { main.style.display='none'; mini.style.display='block'; };
    mini.onclick = function() { mini.style.display='none'; main.style.display='block'; };
    document.getElementById('kol-send-btn').onclick = function() {
        var url = document.getElementById('kol-url-input').value.trim();
        if (!url) { document.getElementById('kol-status').textContent='⚠️ Dán link trước!'; return; }
        document.getElementById('kol-status').textContent = '⏳ Đang gửi...';
        fetch('http://127.0.0.1:9223/send?url=' + encodeURIComponent(url))
            .then(function(r){return r.json();})
            .then(function(d){
                document.getElementById('kol-status').textContent='✅ Đã gửi thành công!';
                document.getElementById('kol-url-input').value='';
                setTimeout(function(){document.getElementById('kol-status').textContent='';},3000);
            })
            .catch(function(e){
                document.getElementById('kol-status').textContent='❌ Tool chưa mở hoặc lỗi kết nối';
            });
    };
    document.getElementById('kol-url-input').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') document.getElementById('kol-send-btn').click();
    });
    document.getElementById('kol-auto-btn').onclick = function() {
        document.getElementById('kol-url-input').value = window.location.href;
    };
})();
"""

def inject_tool_panel(port=9222, log_func=None):
    """Inject the floating KOL Studio panel into all Chrome tabs via CDP."""
    _log = log_func or _safe_log
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3)
        tabs = json.loads(resp.read().decode())
    except Exception as ex:
        _log(f"[Inject] Không kết nối được Chrome CDP: {ex}")
        return False

    injected = 0
    for tab in tabs:
        ws_url = tab.get("webSocketDebuggerUrl", "")
        if not ws_url or tab.get("type") != "page":
            continue
        tab_url = tab.get("url", "")
        # Skip chrome:// and extension pages
        if tab_url.startswith("chrome://") or tab_url.startswith("chrome-extension://"):
            continue
        try:
            import websocket
            ws = websocket.create_connection(ws_url, timeout=5, suppress_origin=True)
            # Inject into current page
            msg = json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {"expression": _INJECT_JS}})
            ws.send(msg)
            resp1 = ws.recv()
            # Persist across navigations
            msg2 = json.dumps({"id": 2, "method": "Page.addScriptToEvaluateOnNewDocument", "params": {"source": _INJECT_JS}})
            ws.send(msg2)
            ws.recv()
            ws.close()
            injected += 1
            _log(f"[Inject] ✅ Tab: {tab_url[:60]}")
        except Exception as ex:
            _log(f"[Inject] ⚠️ Tab {tab_url[:40]}: {ex}")

    if injected:
        _log(f"[Chrome] ✅ Đã inject panel KOL Studio vào {injected} tab")
    else:
        _log("[Chrome] ⚠️ Không inject được tab nào. Thử bấm lại 'Inject Panel'")
    return injected > 0


# ═══════════════════════════════════════════════════════════
#  CDP-based product scraper – connects to running Chrome
# ═══════════════════════════════════════════════════════════
CDP_PORT = 9222

def _safe_log(msg):
    """Print that won't crash when stdout is closed (GUI mode)."""
    try:
        print(msg)
    except (ValueError, OSError):
        pass

def crawl_product_via_chrome(product_url, timeout=30, log_func=None):
    """
    Scrape product data from TikTok Shop by connecting to the running
    Chrome instance via Chrome DevTools Protocol (CDP).
    
    Returns dict with 'product_info' and 'image_urls' keys, or None on failure.
    """
    import requests as _req
    import json as _json
    import time as _time
    
    _log = log_func or _safe_log
    
    _log(f"[CDP] Kết nối Chrome (port {CDP_PORT}) để quét sản phẩm...")
    
    # 1. Check if Chrome CDP is available
    try:
        resp = _req.get(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3)
        if resp.status_code != 200:
            _log("[CDP] ✗ Chrome không phản hồi trên cổng CDP.")
            return None
        _log(f"[CDP] ✓ Đã kết nối Chrome: {resp.json().get('Browser', 'unknown')}")
    except Exception:
        _log(f"[CDP] ✗ Không kết nối được Chrome (port {CDP_PORT}). Hãy mở Chrome TikTok trước!")
        return None
    
    # 2. Extract product ID
    product_id = ""
    m = re.search(r'(\d{15,25})', product_url)
    if m:
        product_id = m.group(1)
    
    # Use shop.tiktok.com/vn/pdp/ format (matches user's actual Chrome URL)
    if product_id:
        nav_url = f"https://shop.tiktok.com/vn/pdp/{product_id}?source=anchor"
    else:
        nav_url = product_url
    
    # 3. Get existing tabs
    try:
        tabs_resp = _req.get(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3)
        tabs = tabs_resp.json()
    except Exception:
        _log("[CDP] ✗ Không lấy được danh sách tab.")
        return None
    
    # Find existing tab with matching product
    ws_url = None
    need_navigate = True
    for tab in tabs:
        tab_url = tab.get("url", "")
        if product_id and product_id in tab_url:
            ws_url = tab.get("webSocketDebuggerUrl")
            _log(f"[CDP] ✓ Tìm thấy tab sản phẩm: {tab_url[:80]}")
            need_navigate = False
            break
    
    # If no matching tab found, use first available page tab
    if not ws_url:
        _log(f"[CDP] Không tìm thấy tab sản phẩm, mở tab mới...")
        # Create new tab via CDP HTTP API
        try:
            new_tab_resp = _req.put(
                f"http://127.0.0.1:{CDP_PORT}/json/new?{nav_url}",
                timeout=10
            )
            new_tab = new_tab_resp.json()
            ws_url = new_tab.get("webSocketDebuggerUrl")
            need_navigate = False  # CDP /json/new already navigates
            _log(f"[CDP] ✓ Đã tạo tab mới")
        except Exception:
            # Fallback: try GET method
            try:
                new_tab_resp = _req.get(
                    f"http://127.0.0.1:{CDP_PORT}/json/new?{nav_url}",
                    timeout=10
                )
                new_tab = new_tab_resp.json()
                ws_url = new_tab.get("webSocketDebuggerUrl")
                need_navigate = False
                _log(f"[CDP] ✓ Đã tạo tab mới (GET)")
            except Exception as e:
                _log(f"[CDP] ✗ Không tạo được tab mới: {e}")
                # Last resort: use any existing tab and navigate it
                for tab in tabs:
                    if tab.get("type") == "page":
                        ws_url = tab.get("webSocketDebuggerUrl")
                        need_navigate = True
                        _log("[CDP] → Sử dụng tab hiện có và navigate...")
                        break
    
    if not ws_url:
        _log("[CDP] ✗ Không lấy được WebSocket URL.")
        return None
    
    # 4. Connect via WebSocket
    import websocket
    
    try:
        ws = websocket.create_connection(ws_url, timeout=timeout, suppress_origin=True)
    except Exception as e:
        _log(f"[CDP] ✗ Không kết nối WebSocket: {e}")
        return None
    
    cmd_id = 1
    
    def cdp_send(method, params=None):
        nonlocal cmd_id
        msg = {"id": cmd_id, "method": method}
        if params:
            msg["params"] = params
        ws.send(_json.dumps(msg))
        cur_id = cmd_id
        cmd_id += 1
        # Wait for response with matching id
        deadline = _time.time() + timeout
        while _time.time() < deadline:
            try:
                ws.settimeout(max(0.5, deadline - _time.time()))
                raw = ws.recv()
                data = _json.loads(raw)
                if data.get("id") == cur_id:
                    return data
                # Skip events, keep waiting for our response
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                break
        return None
    
    try:
        # Navigate if needed
        if need_navigate:
            _log(f"[CDP] Navigate → {nav_url[:80]}")
            cdp_send("Page.navigate", {"url": nav_url})
        
        # Wait for page to fully load
        _log("[CDP] Đợi trang tải (8s)...")
        _time.sleep(8)
        
        # Get current page info for debugging
        debug_resp = cdp_send("Runtime.evaluate", {
            "expression": "JSON.stringify({url: location.href, title: document.title, bodyLen: document.body ? document.body.innerHTML.length : 0})",
            "returnByValue": True
        })
        if debug_resp:
            try:
                debug_info = _json.loads(debug_resp.get("result", {}).get("result", {}).get("value", "{}"))
                _log(f"[CDP] Trang: {debug_info.get('title', '?')[:60]} | URL: {debug_info.get('url', '?')[:80]} | Body: {debug_info.get('bodyLen', 0)} chars")
            except Exception:
                pass
        
        scrape_js = r"""
(function() {
    var result = {title: '', price: '', seller: '', description: '', images: [], product_id: '', url: ''};
    
    result.url = window.location.href;
    var m = window.location.href.match(/(\d{15,25})/);
    if (m) result.product_id = m[1];
    
    // Title
    var titleSels = ['h1', '[data-e2e="product-title"]', '[class*="ProductTitle"]', 
        '[class*="product-title"]', '[class*="pdp-title"]', '[class*="ProductName"]'];
    for (var i = 0; i < titleSels.length; i++) {
        var el = document.querySelector(titleSels[i]);
        if (el && el.textContent.trim().length > 3) { result.title = el.textContent.trim(); break; }
    }
    
    // Price
    var bestPrice = '', bestPriceLen = 9999;
    var priceEls = document.querySelectorAll('span, div, p');
    for (var i = 0; i < Math.min(priceEls.length, 800); i++) {
        var el = priceEls[i];
        if (el.children.length > 3) continue;
        var txt = (el.innerText || el.textContent || '').trim();
        if (!txt || txt.length > 30 || txt.length < 3) continue;
        var pm = txt.match(/₫\s?([\d.,]+)/) || txt.match(/([\d.,]+)\s?₫/);
        if (pm && pm[1].length >= 3 && txt.length < bestPriceLen) { bestPrice = txt; bestPriceLen = txt.length; }
    }
    result.price = bestPrice;
    
    // Seller
    var sellerFound = false;
    var allTextEls = document.querySelectorAll('span, a, div, p');
    for (var i = 0; i < Math.min(allTextEls.length, 600); i++) {
        var el = allTextEls[i];
        var txt = (el.innerText || el.textContent || '').trim();
        if (!txt || txt.length > 200) continue;
        var sm = txt.match(/Sold\s+by\s+(.+)/i);
        if (sm) {
            var name = sm[1].trim().replace(/[\d.]+\s*[★⭐]\s*\(?\d+\)?\s*\d*\s*sold.*/i, '').replace(/\d+\s*sold.*/i, '').trim();
            if (name.length > 1 && name.length < 80) { result.seller = name; sellerFound = true; break; }
        }
    }
    if (!sellerFound) {
        var shopLinks = document.querySelectorAll('a[href*="/shop/"], a[href*="/@"]');
        for (var i = 0; i < shopLinks.length; i++) {
            var t = (shopLinks[i].innerText || shopLinks[i].textContent || '').trim();
            if (t.length > 1 && t.length < 60 && !/sold|rating|review/i.test(t)) { result.seller = t; break; }
        }
    }
    
    // Description – ONLY from product description section, NOT reviews
    var descText = '';
    var descHeadings = document.querySelectorAll('h1, h2, h3, h4, h5, [role="heading"]');
    for (var i = 0; i < descHeadings.length; i++) {
        var ht = (descHeadings[i].innerText || descHeadings[i].textContent || '').trim();
        if (/^(About this product|Product description|Mô tả sản phẩm|Chi tiết sản phẩm)/i.test(ht)) {
            var container = descHeadings[i].parentElement;
            for (var up = 0; up < 4 && container; up++) {
                var sections = container.querySelectorAll('div');
                for (var s = 0; s < sections.length; s++) {
                    var sec = sections[s];
                    if (sec === descHeadings[i] || sec.contains(descHeadings[i])) continue;
                    var secText = (sec.innerText || sec.textContent || '').trim();
                    if (/global reviews|Verified purchase|reviews|Filter by|Sort by|Displaying.*of.*reviews|Photos from reviews/i.test(secText)) continue;
                    if (secText.length > 30 && secText.length < 5000) {
                        secText = secText.replace(/^(About this product|Product description|Mô tả sản phẩm|Chi tiết sản phẩm)\s*/gi, '').trim();
                        if (secText.length > 30) { descText = secText; break; }
                    }
                }
                if (descText) break;
                container = container.parentElement;
            }
            if (descText) break;
        }
    }
    if (!descText) {
        var descSels = ['[class*="product-description"]', '[class*="ProductDescription"]',
            '[class*="pdp-description"]', '[class*="about-product"]'];
        for (var i = 0; i < descSels.length; i++) {
            var el = document.querySelector(descSels[i]);
            if (el) {
                var dt = (el.innerText || el.textContent || '').trim();
                if (dt.length > 20 && !/global reviews|Verified purchase|Sort by/i.test(dt)) {
                    descText = dt.replace(/^(About this product|Product description)\s*/gi, '').trim();
                    break;
                }
            }
        }
    }
    if (descText) { result.description = descText.substring(0, 2000); }
    
    // Images – ONLY product gallery, NOT reviews/recommendations
    var seen = {};
    
    // Helper: check if element is inside review/recommendation section
    function isInExcluded(el) {
        var p = el;
        for (var depth = 0; depth < 12 && p; depth++) {
            var cls = (p.className || '').toString().toLowerCase();
            if (/review|comment|recommend|related|youmay|similar|suggestion|rating/i.test(cls)) return true;
            // Also check data attributes
            var de = (p.getAttribute && p.getAttribute('data-e2e')) || '';
            if (/review|comment/i.test(de)) return true;
            p = p.parentElement;
        }
        return false;
    }
    
    // Step 1: Find product gallery container
    var gallerySels = [
        '[class*="gallery"]', '[class*="carousel"]', '[class*="swiper"]',
        '[class*="ImageGallery"]', '[class*="image-gallery"]', '[class*="pdp-image"]',
        '[class*="ProductImage"]', '[class*="product-image"]',
        '[class*="SlideImage"]', '[class*="slider"]'
    ];
    var galleryContainer = null;
    for (var i = 0; i < gallerySels.length; i++) {
        var el = document.querySelector(gallerySels[i]);
        if (el) {
            var imgs = el.querySelectorAll('img');
            if (imgs.length >= 1) { galleryContainer = el; break; }
        }
    }
    
    // Step 2: Collect from gallery container
    if (galleryContainer) {
        var gImgs = galleryContainer.querySelectorAll('img');
        for (var j = 0; j < gImgs.length; j++) {
            var src = gImgs[j].src || gImgs[j].getAttribute('data-src') || '';
            if (!src || src.startsWith('data:') || src.length < 20) continue;
            if (src.indexOf('ibyteimg') === -1 && src.indexOf('tiktokcdn') === -1) continue;
            var cleanSrc = src.split('?')[0];
            if (/\/\d{2,3}x\d{2,3}\//.test(cleanSrc) || /\/50x50\/|\/32x32\/|\/48x48\//.test(cleanSrc)) continue;
            if (!seen[cleanSrc]) { seen[cleanSrc] = true; result.images.push(src); }
        }
    }
    
    // Step 3: If few gallery images, also collect from main product area (excluding reviews/reco)
    if (result.images.length < 15) {
        var allImgs = document.querySelectorAll('img');
        for (var j = 0; j < allImgs.length; j++) {
            var img = allImgs[j];
            var src = img.src || img.getAttribute('data-src') || '';
            if (!src || src.startsWith('data:') || src.length < 20) continue;
            if (src.indexOf('ibyteimg') === -1 && src.indexOf('tiktokcdn') === -1) continue;
            if (isInExcluded(img)) continue;
            var w = img.naturalWidth || img.width || 0;
            var h = img.naturalHeight || img.height || 0;
            if (w > 0 && w < 80 && h > 0 && h < 80) continue;
            var cleanSrc = src.split('?')[0];
            if (/\/\d{2,3}x\d{2,3}\//.test(cleanSrc) || /\/50x50\/|\/32x32\/|\/48x48\//.test(cleanSrc)) continue;
            if (!seen[cleanSrc]) { seen[cleanSrc] = true; result.images.push(src); }
        }
    }
    
    return JSON.stringify(result);
})()
        """
        
        resp = cdp_send("Runtime.evaluate", {
            "expression": scrape_js,
            "returnByValue": True
        })
        
        if not resp or "result" not in resp:
            _log("[CDP] ✗ Không nhận được kết quả từ JS.")
            err = resp.get("result", {}).get("exceptionDetails") if resp else None
            if err:
                _log(f"[CDP]   JS Error: {err}")
            ws.close()
            return None
        
        js_result = resp.get("result", {}).get("result", {})
        raw_value = js_result.get("value", "")
        
        if not raw_value:
            _log("[CDP] ✗ JS trả về rỗng.")
            exc = resp.get("result", {}).get("exceptionDetails")
            if exc:
                _log(f"[CDP]   Exception: {_json.dumps(exc, ensure_ascii=False)[:200]}")
            ws.close()
            return None
        
        try:
            scraped = _json.loads(raw_value)
        except Exception:
            _log(f"[CDP] ✗ Không parse được JSON: {raw_value[:200]}")
            ws.close()
            return None
        
        ws.close()
        
        # 5. Build result
        title = scraped.get("title", "").strip()
        price = scraped.get("price", "").strip()
        seller = scraped.get("seller", "").strip()
        description = scraped.get("description", "").strip()
        images = scraped.get("images", [])
        pid = scraped.get("product_id", product_id)
        product_page_url = scraped.get("url", product_url)
        
        # Clean images
        cleaned_images = []
        for img in images:
            cleaned_url = img.split("?")[0]
            if not any(size in cleaned_url for size in ["/100x100/", "/200x200/", "/150x150/", "/50x50/", "/32x32/"]):
                cleaned_images.append(img)
        cleaned_images = list(dict.fromkeys(cleaned_images))  # dedupe
        
        _log(f"[CDP] Kết quả: title='{title[:50]}' | price='{price}' | seller='{seller}' | images={len(cleaned_images)}")
        
        if not title and not cleaned_images:
            _log("[CDP] ✗ Không quét được dữ liệu.")
            return None
        
        result = {
            "product_info": {
                "title": title or "Không tìm thấy tên",
                "price": price or "Không tìm thấy giá",
                "seller": seller or "Không tìm thấy cửa hàng",
                "description": description or "",
                "product_id": pid,
                "url": product_page_url
            },
            "image_urls": cleaned_images
        }
        
        _log(f"[CDP] ✓ Quét thành công: {title[:50]}... | Ảnh: {len(cleaned_images)}")
        return result
        
    except Exception as e:
        _log(f"[CDP] ✗ Lỗi: {e}")
        try:
            ws.close()
        except Exception:
            pass
        return None


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Cách dùng:")
        print("  python tiktok_shop.py crawl <product_url>")
        print("  python tiktok_shop.py open_spy")
        sys.exit(1)
        
    action = sys.argv[1].lower()
    if action == "crawl":
        if len(sys.argv) < 3:
            print("Vui lòng cung cấp link sản phẩm!")
            sys.exit(1)
        url = sys.argv[2]
        res = crawl_product(url)
        if res:
            print("\n" + "="*50)
            print(" KẾT QUẢ CÀO SẢN PHẨM:")
            print("="*50)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            print("="*50 + "\n")
        else:
            print("\n[FAIL] Không thể cào dữ liệu sản phẩm này.")
    elif action == "open_spy":
        open_spy_browser()
    else:
        # Backward compatibility
        url = sys.argv[1]
        res = crawl_product(url)
        if res:
            print(json.dumps(res, indent=2, ensure_ascii=False))
