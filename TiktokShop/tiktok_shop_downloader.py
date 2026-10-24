#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tiktok_shop_downloader.py - TikTok Shop Crawler & Key Manager
Hỗ trợ ScrapeCreators API và Trình duyệt Playwright cục bộ (LocalBrowser).
"""

import json
import os
import re
import time
import threading
import requests
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────
#  CENTRAL CONFIG
# ─────────────────────────────────────────────
CONFIG_DIR = Path.home() / ".tiktok_img_tool"
CONFIG_DIR.mkdir(exist_ok=True)
API_KEYS_FILE = CONFIG_DIR / "api_keys.json"

SCRAPECREATORS_BASE = "https://api.scrapecreators.com"

# ─────────────────────────────────────────────
#  PROVIDER CONSTANTS
# ─────────────────────────────────────────────
PROVIDER_SCRAPECREATORS = "ScrapeCreators"
PROVIDER_LOCAL_BROWSER  = "LocalBrowser"
PROVIDER_AUTO           = "Auto"

def _safe_print(msg):
    """Safe print that won't crash when stdout is closed (GUI mode)."""
    try:
        print(msg)
    except (ValueError, OSError):
        pass


# ═══════════════════════════════════════════════════════════
#  SECTION 1 – ScrapeCreators Key Manager
# ═══════════════════════════════════════════════════════════
class APIKeyManager:
    """Quản lý nhiều ScrapeCreators API key với rotation và cooldown."""
    def __init__(self):
        self.keys = []
        self.current_index = 0
        self.disabled_keys = {}
        self.load_keys()

    def load_keys(self):
        if API_KEYS_FILE.exists():
            try:
                with open(API_KEYS_FILE, "r") as f:
                    self.keys = json.load(f).get("keys", [])
            except Exception:
                self.keys = []

    def save_keys(self):
        with open(API_KEYS_FILE, "w") as f:
            json.dump({"keys": self.keys}, f, indent=2)

    def add_key(self, key):
        key = key.strip()
        if key and key not in self.keys:
            self.keys.append(key)
            self.save_keys()
            return True
        return False

    def remove_key(self, key):
        if key in self.keys:
            self.keys.remove(key)
            self.save_keys()
            return True
        return False

    def get_current_key(self):
        available = [k for k in self.keys if k not in self.disabled_keys]
        if not available:
            now = time.time()
            for k in [k for k, t in list(self.disabled_keys.items()) if now - t > 300]:
                del self.disabled_keys[k]
            available = [k for k in self.keys if k not in self.disabled_keys]
        if not available:
            return None
        return available[self.current_index % len(available)]

    def rotate_key(self, failed_key=None):
        if failed_key:
            self.disabled_keys[failed_key] = time.time()
        self.current_index += 1
        return self.get_current_key()

    def has_keys(self):
        return bool(self.keys)


# ═══════════════════════════════════════════════════════════
#  SECTION 2 – ScrapeCreators Client
# ═══════════════════════════════════════════════════════════
class TikTokAPIClient:
    """Client ScrapeCreators – giữ đầy đủ logic scraping cũ."""

    def __init__(self, key_manager: APIKeyManager, log_func=None):
        self.km      = key_manager
        self.log     = log_func or _safe_print
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.debug_data = {}

    def _api_call(self, endpoint, params, max_retries=3):
        for attempt in range(max_retries):
            api_key = self.km.get_current_key()
            if not api_key:
                self.log("    ✗ Không có ScrapeCreators API key khả dụng!")
                return None

            url = f"{SCRAPECREATORS_BASE}{endpoint}"
            try:
                self.log(f"    [SC] {endpoint} (key:...{api_key[-6:]}, lần {attempt+1})")
                resp = self.session.get(
                    url, params=params,
                    headers={"x-api-key": api_key},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self.debug_data[f"{endpoint}_{len(self.debug_data)}"] = data
                    return data
                elif resp.status_code in (401, 402, 429):
                    self.log(f"    ⚠ Key lỗi {resp.status_code}, chuyển key...")
                    self.km.rotate_key(failed_key=api_key)
                elif resp.status_code in (400, 404):
                    msg = ""
                    try:
                        msg = resp.json().get("message", "")[:120]
                    except Exception:
                        msg = resp.text[:120]
                    self.log(f"    ✗ {resp.status_code}: {msg}")
                    return None
                else:
                    self.log(f"    ✗ HTTP {resp.status_code}")
                    return None
            except requests.exceptions.Timeout:
                self.log("    ⚠ Timeout, thử lại...")
            except Exception as e:
                self.log(f"    ✗ Lỗi: {e}")
                return None
        return None

    # ── Helpers ──────────────────────────────────────────────
    def _find_product_url(self, video_response):
        if not video_response or not isinstance(video_response, dict):
            return None
        url = video_response.get("shop_product_url")
        if url:
            self.log("    ✓ shop_product_url ở top-level")
            return url
        aweme = video_response.get("aweme_detail", {})
        if isinstance(aweme, dict):
            url = aweme.get("shop_product_url")
            if url:
                self.log("    ✓ shop_product_url trong aweme_detail")
                return url
        s = json.dumps(video_response)
        m = re.search(r'tiktok\.com/shop/pdp/(\d+)', s)
        if m:
            url = f"https://www.tiktok.com/shop/pdp/{m.group(1)}"
            self.log(f"    ✓ Tìm bằng regex: {m.group(1)}")
            return url
        for p in [r'"product_id"\s*:\s*"?(\d{15,25})"?', r'"productId"\s*:\s*"?(\d{15,25})"?']:
            m = re.search(p, s)
            if m:
                url = f"https://www.tiktok.com/shop/pdp/{m.group(1)}"
                self.log(f"    ✓ Tìm product_id: {m.group(1)}")
                return url
        return None

    def _extract_product_id(self, product_url):
        m = re.search(r'(\d{15,25})', product_url)
        return m.group(1) if m else None

    def _get_author_handle(self, video_response):
        if not video_response:
            return None
        aweme = video_response.get("aweme_detail", {})
        if isinstance(aweme, dict):
            author = aweme.get("author", {})
            if isinstance(author, dict):
                return author.get("unique_id")
        return None

    def _get_video_desc(self, video_response):
        if not video_response:
            return ""
        aweme = video_response.get("aweme_detail", {})
        if isinstance(aweme, dict):
            return aweme.get("desc", "")
        return ""

    def _parse_shop_product(self, product_obj, extra_shop_info=None):
        info = {
            "title":      product_obj.get("title", "Không rõ tên"),
            "product_id": str(product_obj.get("product_id", "")),
            "price":      "",
            "seller":     "",
        }
        pp = product_obj.get("product_price_info", {})
        if isinstance(pp, dict):
            sym  = pp.get("currency_symbol", "")
            sale = pp.get("sale_price_format", "")
            orig = pp.get("origin_price_format", "")
            if sale:
                info["price"] = f"{sym}{sale}"
                if orig and orig != sale:
                    info["price"] += f" (gốc {sym}{orig})"
        si = product_obj.get("seller_info", {})
        if isinstance(si, dict):
            info["seller"] = si.get("shop_name", "")
        if not info["seller"] and extra_shop_info and isinstance(extra_shop_info, dict):
            info["seller"] = extra_shop_info.get("shop_name", "")
        images = []
        img = product_obj.get("image", {})
        if isinstance(img, dict):
            urls = img.get("url_list", [])
            if urls:
                images.extend(urls[:2])
        return {"product_info": info, "image_urls": images}

    def _parse_product_detail(self, data):
        pi = data.get("product_info", data)
        if "data" in data and isinstance(data["data"], dict):
            pi = data["data"].get("product_info", data["data"])
        pb = pi.get("product_base", pi)
        info = {
            "title":      pb.get("title", pi.get("title", "Không rõ")),
            "product_id": str(pi.get("product_id", "")),
            "price":      "",
            "seller":     "",
        }
        price = pb.get("price", {})
        if isinstance(price, dict):
            real = price.get("real_price", price.get("original_price", ""))
            info["price"] = real
        seller = pi.get("seller", {})
        if isinstance(seller, dict):
            info["seller"] = seller.get("name", "")
        si = data.get("shop_info", {})
        if not info["seller"] and isinstance(si, dict):
            info["seller"] = si.get("shop_name", "")
        images = []
        for img in pb.get("images", pi.get("images", [])):
            if isinstance(img, dict):
                urls = img.get("url_list", [])
                if urls:
                    images.append(urls[0])
            elif isinstance(img, str):
                images.append(img)
        sale_props = pi.get("sale_props", data.get("sale_props", []))
        if isinstance(sale_props, list):
            for prop in sale_props:
                for val in prop.get("sale_prop_values", []):
                    v_img = val.get("image", {})
                    if isinstance(v_img, dict):
                        urls = v_img.get("url_list", [])
                        if urls:
                            images.append(urls[0])
        sc = pb.get("size_chart", pi.get("size_chart", {}))
        if isinstance(sc, dict):
            sc_img = sc.get("image", {})
            if isinstance(sc_img, dict):
                urls = sc_img.get("url_list", [])
                if urls:
                    images.append(urls[0])
        return {"product_info": info, "image_urls": list(dict.fromkeys(images))}

    # ── Strategy methods ──────────────────────────────────────
    def _try_product_detail(self, product_url, region=None):
        self.log(f"  [SC-A] /v1/tiktok/product (region={region or 'VN'})...")
        params = {"url": product_url}
        if region:
            params["region"] = region.upper()
        data = self._api_call("/v1/tiktok/product", params)
        if data:
            result = self._parse_product_detail(data)
            if result["image_urls"]:
                self.log(f"    ✓ Có {len(result['image_urls'])} ảnh!")
                return result
        return None

    def _try_user_showcase(self, handle, product_id, region):
        if not handle:
            self.log("  [SC-B] Bỏ qua - không có handle")
            return None
        self.log(f"  [SC-B] /v1/tiktok/user/showcase (handle={handle})...")
        params = {"handle": handle}
        if region:
            params["region"] = region.upper()
        data = self._api_call("/v1/tiktok/user/showcase", params)
        if not data:
            return None
        products = data.get("products", [])
        self.log(f"    → Showcase có {len(products)} sản phẩm")
        for p in products:
            pid = str(p.get("product_id", ""))
            if pid == product_id:
                self.log("    ✓ KHỚP product_id!")
                return self._parse_shop_product(p)
        cursor = data.get("cursor", "")
        page = 1
        while cursor and page < 10:
            page += 1
            self.log(f"    → Showcase trang {page}...")
            params_next = {"handle": handle, "cursor": cursor}
            if region:
                params_next["region"] = region.upper()
            data = self._api_call("/v1/tiktok/user/showcase", params_next)
            if not data:
                break
            for p in data.get("products", []):
                if str(p.get("product_id", "")) == product_id:
                    self.log(f"    ✓ KHỚP ở trang {page}!")
                    return self._parse_shop_product(p)
            cursor = data.get("cursor", "")
            if not data.get("products"):
                break
        self.log("    ✗ Không tìm thấy trong showcase")
        return None

    def _try_shop_search(self, product_id, region):
        self.log(f"  [SC-C] /v1/tiktok/shop/search (query={product_id})...")
        data = self._api_call("/v1/tiktok/shop/search", {
            "query":  product_id,
            "region": region.upper(),
        })
        if not data:
            return None
        products = data.get("products", [])
        self.log(f"    → Search trả về {len(products)} sản phẩm")
        for p in products:
            if str(p.get("product_id", "")) == product_id:
                self.log("    ✓ KHỚP chính xác!")
                return self._parse_shop_product(p)
        self.log("    ✗ Không có kết quả khớp product_id")
        return None

    def _try_shop_search_by_desc(self, desc, product_id, region):
        if not desc or len(desc) < 5:
            self.log("  [SC-D] Bỏ qua - không có mô tả video")
            return None
        cleaned = re.sub(r'[#@]\S+', '', desc).strip()
        words   = cleaned.split()[:6]
        query   = " ".join(words)
        if len(query) < 3:
            return None
        self.log(f"  [SC-D] Shop search by desc: \"{query[:40]}...\"")
        data = self._api_call("/v1/tiktok/shop/search", {
            "query":  query,
            "region": region.upper(),
        })
        if not data:
            return None
        for p in data.get("products", []):
            if str(p.get("product_id", "")) == product_id:
                self.log("    ✓ KHỚP!")
                return self._parse_shop_product(p)
        self.log("    ✗ Không khớp product_id")
        return None

    def _try_shop_products_with_store_url(self, product_id, video_response, region):
        if not video_response:
            return None
        s = json.dumps(video_response)
        m = re.search(r'(https?://(?:www\.)?tiktok\.com/shop/store/[^"\\]+)', s)
        if m:
            store_url = m.group(1)
            self.log(f"  [SC-E] shop/products (store: {store_url[:60]}...)...")
            data = self._api_call("/v1/tiktok/shop/products", {
                "url":    store_url,
                "region": region.upper(),
            })
            if data:
                for p in data.get("products", []):
                    if str(p.get("product_id", "")) == product_id:
                        self.log("    ✓ KHỚP!")
                        return self._parse_shop_product(p, data.get("shopInfo"))
                self.log("    ✗ Không khớp trong trang đầu")
        return None

    def _extract_from_anchor(self, vdata, product_id):
        aweme   = vdata.get("aweme_detail", {})
        anchors = aweme.get("anchors", [])
        if not anchors:
            return None
        for a in anchors:
            if a.get("component_key") == "anchor_complex_shop":
                for item in a.get("extra", []):
                    extra_str = item.get("extra", "")
                    if extra_str:
                        try:
                            d   = json.loads(extra_str)
                            pid = str(d.get("product_id", ""))
                            if pid == product_id or not product_id:
                                title    = d.get("title", "")
                                img_urls = []
                                cover    = d.get("cover_url") or d.get("cover")
                                if cover:
                                    if cover.startswith("http"):
                                        img_urls.append(cover)
                                    else:
                                        img_urls.append(f"https://p16-oec-va.ibyteimg.com/{cover}~tplv-oec-style:jpeg.jpeg")
                                for img_path in d.get("img", []):
                                    if img_path:
                                        if img_path.startswith("http"):
                                            img_urls.append(img_path)
                                        else:
                                            img_urls.append(f"https://p16-oec-va.ibyteimg.com/{img_path}~tplv-oec-style:jpeg.jpeg")
                                img_urls = list(dict.fromkeys(img_urls))
                                if title and img_urls:
                                    return {
                                        "product_info": {
                                            "title":      title,
                                            "product_id": pid or product_id,
                                            "price":      "",
                                            "seller":     "",
                                        },
                                        "image_urls": img_urls,
                                    }
                        except Exception as e:
                            self.log(f"    ✗ Lỗi phân tích anchor extra: {e}")
        return None

    def _try_shop_search_by_title(self, title, product_id, region):
        if not title or len(title) < 5:
            return None
        cleaned = re.sub(r'[#@]\S+', '', title).strip()
        words   = cleaned.split()[:6]
        query   = " ".join(words)
        if len(query) < 3:
            return None
        self.log(f"  [SC-Title] shop/search by title: \"{query[:40]}...\"")
        data = self._api_call("/v1/tiktok/shop/search", {
            "query":  query,
            "region": region.upper(),
        })
        if not data:
            return None
        products = data.get("products", [])
        self.log(f"    → Search trả về {len(products)} sản phẩm")
        for p in products:
            pid = str(p.get("product_id", ""))
            if pid == product_id or (product_id and product_id in pid):
                self.log("    ✓ KHỚP!")
                return self._parse_shop_product(p)
        return None

    # ── Public process methods ────────────────────────────────
    def _get_video_data(self, video_url, region):
        """Lấy video data từ ScrapeCreators (bước dùng chung)."""
        return self._api_call("/v2/tiktok/video", {
            "url":    video_url,
            "region": region.upper(),
        })

    def process_video(self, video_url, region="VN", stop_event=None):
        self.debug_data = {}
        self.log(f"\n[SC] Bắt đầu quét video: {video_url}")

        vdata = self._get_video_data(video_url, region)
        if not vdata:
            self.log("  ✗ Không lấy được thông tin video từ SC!")
            return None

        product_url = self._find_product_url(vdata)
        if not product_url:
            self.log("  ✗ Video không chứa liên kết sản phẩm (giỏ hàng vàng)!")
            return None

        product_id = self._extract_product_id(product_url)
        handle     = self._get_author_handle(vdata)
        desc       = self._get_video_desc(vdata)

        self.log(f"  → Product ID: {product_id}")
        self.log(f"  → Tác giả: @{handle or 'Không rõ'}")

        if stop_event and stop_event.is_set():
            return None

        result = self._try_product_detail(product_url, region)
        if not result:
            result = self._try_user_showcase(handle, product_id, region)
        if not result:
            result = self._try_shop_search(product_id, region)
        if not result:
            result = self._try_shop_search_by_desc(desc, product_id, region)
        if not result:
            result = self._try_shop_products_with_store_url(product_id, vdata, region)

        if not result:
            anchor_result = self._extract_from_anchor(vdata, product_id)
            if anchor_result:
                self.log("  → Dùng dữ liệu trực tiếp từ Anchor video...")
                title    = anchor_result["product_info"]["title"]
                enriched = self._try_shop_search_by_title(title, product_id, region)
                if enriched:
                    anchor_result["product_info"]["price"]  = enriched["product_info"]["price"]
                    anchor_result["product_info"]["seller"] = enriched["product_info"]["seller"]
                    all_imgs = anchor_result["image_urls"] + enriched["image_urls"]
                    anchor_result["image_urls"] = list(dict.fromkeys(all_imgs))
                    self.log("    ✓ Đã làm giàu dữ liệu từ search!")
                else:
                    anchor_result["product_info"]["seller"] = handle or "TikTok Shop"
                    anchor_result["product_info"]["price"]  = "Xem tại Shop"
                result = anchor_result

        if (not result or not result.get("image_urls")) and product_url:
            self.log("  → [SC] Không lấy được thông tin chi tiết sản phẩm hoặc không có ảnh từ API!")
            # Try Chrome CDP first (connects to running Chrome TikTok)
            self.log("  → Thử quét qua Chrome TikTok (CDP)...")
            try:
                from tiktok_shop import crawl_product_via_chrome
                cdp_res = crawl_product_via_chrome(product_url, log_func=self.log)
                if cdp_res and cdp_res.get("image_urls"):
                    self.log("    ✓ Quét qua Chrome CDP thành công!")
                    if not result:
                        result = cdp_res
                    else:
                        result["image_urls"] = list(dict.fromkeys(result.get("image_urls", []) + cdp_res.get("image_urls", [])))
                        if cdp_res.get("product_info"):
                            for k, v in cdp_res["product_info"].items():
                                if not result["product_info"].get(k) or result["product_info"][k] in ["Không tìm thấy giá", "Xem tại Shop", "Không tìm thấy tên", "Không tìm thấy cửa hàng"]:
                                    result["product_info"][k] = v
                else:
                    self.log("    ✗ Chrome CDP không có kết quả, thử Playwright...")
                    raise Exception("CDP no result")
            except Exception as cdp_err:
                # Fallback to Playwright local browser
                self.log("  → Đang tự động chuyển sang chế độ dự phòng bằng Trình duyệt cục bộ (Playwright)...")
                try:
                    from tiktok_shop import crawl_product
                    local_res = crawl_product(product_url)
                    if local_res:
                        self.log("    ✓ Cào bằng trình duyệt cục bộ thành công!")
                        if not result:
                            result = local_res
                        else:
                            result["image_urls"] = list(dict.fromkeys(result.get("image_urls", []) + local_res.get("image_urls", [])))
                            if local_res.get("product_info"):
                                for k, v in local_res["product_info"].items():
                                    if not result["product_info"].get(k) or result["product_info"][k] in ["Không tìm thấy giá", "Xem tại Shop"]:
                                        result["product_info"][k] = v
                    else:
                        self.log("    ✗ Cào bằng trình duyệt cục bộ thất bại!")
                except Exception as e:
                    self.log(f"    ✗ Lỗi khi chạy trình duyệt cục bộ: {e}")

        if result:
            self.log(f"  ✓ [SC] Thành công: {result['product_info']['title'][:50]}...")
        else:
            self.log("  ✗ [SC] Không trích xuất được ảnh!")
        return result

    def process_product(self, product_url, region="VN"):
        self.debug_data = {}
        product_id = self._extract_product_id(product_url)
        self.log(f"\n[SC] Bắt đầu quét link sản phẩm: {product_url}")
        result = self._try_product_detail(product_url, region)
        if not result and product_id:
            result = self._try_shop_search(product_id, region)
        if result:
            self.log(f"  ✓ [SC] Thành công: {result['product_info']['title'][:50]}...")
        else:
            self.log("  ✗ [SC] Không lấy được thông tin sản phẩm!")
        return result


# ═══════════════════════════════════════════════════════════
#  SECTION 3 – Unified Downloader
# ═══════════════════════════════════════════════════════════
class TikTokShopDownloader:
    """
    Downloader thống nhất, hỗ trợ 3 chế độ:
      - PROVIDER_AUTO           : tự động chạy ScrapeCreators, nếu lỗi/thiếu ảnh thì dùng Trình duyệt cục bộ.
      - PROVIDER_SCRAPECREATORS : chỉ dùng ScrapeCreators.
      - PROVIDER_LOCAL_BROWSER  : chỉ dùng Trình duyệt cục bộ Playwright.
    """

    def __init__(self, log_func=None):
        self.log          = log_func or _safe_print
        self.key_manager  = APIKeyManager()
        self.sc_client    = TikTokAPIClient(self.key_manager, log_func=self.log)
        self.provider     = PROVIDER_AUTO  # mặc định: auto

        # Thống kê
        self._stats = {
            PROVIDER_SCRAPECREATORS: {"success": 0, "fail": 0},
            PROVIDER_LOCAL_BROWSER:  {"success": 0, "fail": 0},
        }

    # ── Provider selection ────────────────────────────────────
    def set_provider(self, provider: str):
        """
        Chọn nền tảng: 'Auto', 'ScrapeCreators', 'LocalBrowser'
        """
        valid = (PROVIDER_AUTO, PROVIDER_SCRAPECREATORS, PROVIDER_LOCAL_BROWSER)
        if provider not in valid:
            raise ValueError(f"Provider không hợp lệ: {provider}")
        self.provider = provider
        self.log(f"[Downloader] Provider đã chọn: {provider}")

    def get_provider(self):
        return self.provider

    def get_available_providers(self):
        providers = [PROVIDER_AUTO]
        if self.key_manager.has_keys():
            providers.append(PROVIDER_SCRAPECREATORS)
        providers.append(PROVIDER_LOCAL_BROWSER)
        return providers

    # ── ScrapeCreators key helpers ────────────────────────────
    def add_sc_key(self, key: str):
        return self.key_manager.add_key(key)

    def remove_sc_key(self, key: str):
        return self.key_manager.remove_key(key)

    def list_sc_keys(self):
        return list(self.key_manager.keys)

    # ── Stats ─────────────────────────────────────────────────
    def get_stats(self):
        return dict(self._stats)

    def _record_success(self, provider):
        if provider in self._stats:
            self._stats[provider]["success"] += 1

    def _record_fail(self, provider):
        if provider in self._stats:
            self._stats[provider]["fail"] += 1

    # ── URL normalize ────────────────────────────────────────────
    def _normalize_product_url(self, url: str) -> str:
        """
        shop.tiktok.com/vn/pdp/ID -> https://www.tiktok.com/shop/pdp/ID
        """
        if 'shop.tiktok.com' in url or ('tiktok' in url and '/pdp/' in url
                                        and '/shop/pdp/' not in url):
            m = re.search(r'(\d{15,25})', url)
            if m:
                return f'https://www.tiktok.com/shop/pdp/{m.group(1)}'
        return url

    # ── Public: process_video ─────────────────────────────────
    def process_video(self, video_url: str, region: str = 'VN') -> dict | None:
        self.log(f'\n[Downloader] process_video | provider={self.provider} | region={region}')
        self.log(f'[Downloader] URL: {video_url}')

        # We always use ScrapeCreators as the primary API for extracting product URL from video details,
        # which will automatically fall back to LocalBrowser Playwright if VN product details fail.
        r = self.sc_client.process_video(video_url, region)
        if r:
            self._record_success(PROVIDER_SCRAPECREATORS)
        else:
            self._record_fail(PROVIDER_SCRAPECREATORS)
        return r

    # ── Public: process_product ───────────────────────────────
    def process_product(self, product_url: str, region: str = 'VN') -> dict | None:
        norm_url = self._normalize_product_url(product_url)
        if norm_url != product_url:
            self.log(f'[Downloader] URL chuẩn hóa: {norm_url}')

        self.log(f'\n[Downloader] process_product | provider={self.provider} | region={region}')
        self.log(f'[Downloader] URL: {norm_url}')

        if self.provider == PROVIDER_SCRAPECREATORS:
            r = self.sc_client.process_product(norm_url, region)
            self._record_success(PROVIDER_SCRAPECREATORS) if r else self._record_fail(PROVIDER_SCRAPECREATORS)
            return r
        elif self.provider == PROVIDER_LOCAL_BROWSER:
            # Try Chrome CDP first, then Playwright as fallback
            self.log("[Downloader] Thử quét qua Chrome TikTok (CDP)...")
            try:
                from tiktok_shop import crawl_product_via_chrome
                r = crawl_product_via_chrome(norm_url, log_func=self.log)
                if r and r.get("image_urls"):
                    self.log("[Downloader] ✓ Quét qua Chrome CDP thành công!")
                    self._record_success(PROVIDER_LOCAL_BROWSER)
                    return r
                else:
                    self.log("[Downloader] Chrome CDP không có kết quả, thử Playwright...")
            except Exception as cdp_e:
                self.log(f"[Downloader] Chrome CDP lỗi: {cdp_e}, thử Playwright...")
            
            try:
                from tiktok_shop import crawl_product
                r = crawl_product(norm_url)
                if r:
                    self.log("[Downloader] ✓ Cào bằng Playwright thành công!")
                    self._record_success(PROVIDER_LOCAL_BROWSER)
                else:
                    self.log("[Downloader] ✗ Cào bằng Playwright thất bại!")
                    self._record_fail(PROVIDER_LOCAL_BROWSER)
                return r
            except Exception as e:
                self.log(f"[Downloader] Lỗi Playwright: {e}")
                self._record_fail(PROVIDER_LOCAL_BROWSER)
                return None
        else:  # AUTO
            # Priority 1: Chrome CDP (nhanh nhất, dùng Chrome đang mở)
            self.log("[Auto] -> Thử quét qua Chrome TikTok (CDP)...")
            try:
                from tiktok_shop import crawl_product_via_chrome
                r = crawl_product_via_chrome(norm_url, log_func=self.log)
                if r and r.get("image_urls"):
                    self.log("[Auto] ✓ Quét qua Chrome CDP thành công!")
                    self._record_success(PROVIDER_LOCAL_BROWSER)
                    return r
                else:
                    self.log("[Auto] Chrome CDP không có kết quả.")
            except Exception as cdp_e:
                self.log(f"[Auto] Chrome CDP lỗi: {cdp_e}")
            
            # Priority 2: ScrapeCreators API
            r = None
            if self.key_manager.has_keys():
                self.log("[Auto] -> Thử ScrapeCreators API...")
                try:
                    r = self.sc_client.process_product(norm_url, region)
                except Exception as e:
                    self.log(f"[Auto] ScrapeCreators exception: {e}")
            
            if r and r.get("image_urls"):
                self.log('[Auto] ✓ ScrapeCreators thành công!')
                self._record_success(PROVIDER_SCRAPECREATORS)
                return r
            
            # Priority 3: Playwright
            self.log("[Auto] -> Chuyển sang Trình duyệt cục bộ (Playwright)...")
            try:
                from tiktok_shop import crawl_product
                r = crawl_product(norm_url)
                if r:
                    self.log("[Downloader] ✓ Cào dự phòng bằng Playwright thành công!")
                    self._record_success(PROVIDER_LOCAL_BROWSER)
                else:
                    self.log("[Downloader] ✗ Cào dự phòng bằng Playwright thất bại!")
                    self._record_fail(PROVIDER_LOCAL_BROWSER)
                return r
            except Exception as e:
                self.log(f"[Downloader] Lỗi Playwright: {e}")
                self._record_fail(PROVIDER_LOCAL_BROWSER)
                return None

    # ── Backward compat ───────────────────────────────────────
    # ── Backward compat ───────────────────────────────────────
    @property
    def debug_data(self):
        return self.sc_client.debug_data


# ═══════════════════════════════════════════════════════════
#  SECTION 6 – Utilities
# ═══════════════════════════════════════════════════════════
def clean_filename(name):
    name = re.sub(r'[<>:"/\\|?*]', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return name[:60].strip() or "product"
