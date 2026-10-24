# ReviewThreads Studio Pro — AI Threads Video Generator

Hệ thống tạo video TikTok / Threads tự động từ dữ liệu review sản phẩm TikTok Shop và kịch bản Threads, tích hợp AI Prompt (CLIProxy), lồng tiếng TTS (gTTS / Vbee), ghép video nền và xuất video chất lượng cao.

## Các tính năng chính

- 🎬 **2 Chế độ giao diện:**
  - **Desktop App (PyQt6 Fusion):** Giao diện máy tính native mượt mà, đầy đủ tính năng cấu hình, hàng đợi render song song.
  - **Web App (PyWebView 60 FPS GPU):** Giao diện web chạy trên nhân Microsoft Edge Chromium tốc độ cao tại cổng `5500`.
- 🛒 **TikTok Shop Crawler:** Tự động lấy dữ liệu sản phẩm, review, đánh giá sao, tải ảnh sản phẩm HD 1000x1000.
- 🤖 **CLIProxy AI Server tích hợp:** Tự động khởi động `cli-proxy-api.exe` tại cổng `8317` phục vụ sinh kịch bản và prompt.
- 🔊 **Voice & TTS:** Hỗ trợ Vbee và gTTS tạo giọng đọc tự động khớp thời lượng video.
- 🎞️ **Ghép Video & Meme:** Thư viện meme và video nền gameplay / ambient dựng sẵn.

---

## Hướng dẫn cài đặt trên máy mới (Quick Start)

### 1. Yêu cầu hệ thống:
- Hệ điều hành: **Windows 10/11 (64-bit)**
- **Python 3.10+ hoặc 3.11** (nhớ tích chọn *Add Python to PATH* khi cài đặt)
- Cài đặt **FFmpeg** (thêm FFmpeg vào biến môi trường PATH để xử lý video và audio)
- Trình duyệt **Microsoft Edge** hoặc **Google Chrome**

### 2. Cài đặt thư viện Python:
Mở terminal tại thư mục này và chạy:
```bash
pip install -r requirements.txt
```

### 3. Khởi động ứng dụng:
- **Cách 1 (Giao diện Desktop PyQt6):**
  Nhấp đúp chuột vào file **`run_desktop_app.bat`** (hoặc chạy lệnh `python threads.py`).
- **Cách 2 (Giao diện Web GPU pywebview):**
  Nhấp đúp chuột vào file **`run_web_app.bat`** (hoặc chạy lệnh `python main_app.py`).

---

## Cấu trúc thư mục

- `run_desktop_app.bat`: Khởi động giao diện Desktop PyQt6.
- `run_web_app.bat`: Khởi động giao diện Web App pywebview.
- `threads.py`: Mã nguồn chính của giao diện Desktop PyQt6.
- `main_app.py` & `app_server.py`: Backend Flask và launcher Web App (cổng 5500).
- `web/`: Toàn bộ mã nguồn giao diện Web App.
- `CLIProxy/`: Server AI Proxy tích hợp sẵn (`cli-proxy-api.exe`, port 8317).
- `TiktokShop/`: Công cụ crawl sản phẩm TikTok Shop và danh sách sản phẩm mẫu.
- `data/`: Dữ liệu Role Skill, Vbee voices và cấu hình tài khoản.
- `Meme/`: Thư viện video meme hiệu ứng.
- `Video Background/`: Video nền mẫu phục vụ ghép video review.
