# -*- coding: utf-8 -*-
"""
Auto Threads — ReviewThreads Studio Pro (Modern GPU Web App Launcher)
Launches local Flask REST backend and opens GPU-accelerated pywebview native window.
"""
import sys
import time
import threading
import webview
from pathlib import Path

# Add current dir
CURRENT_DIR = Path(__file__).parent.resolve()
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from app_server import run_server

def start_backend():
    run_server(5500)

if __name__ == "__main__":
    # Start Flask Backend Thread
    server_thread = threading.Thread(target=start_backend, daemon=True)
    server_thread.start()
    
    # Wait for server to bind
    time.sleep(1.0)
    
    # Open PyWebView Native Window with Edge/Chromium GPU Hardware Acceleration
    print("🚀 Opening GPU-Accelerated Web App Window...")
    webview.create_window(
        title="🎬 Auto Threads — ReviewThreads Studio Pro (60 FPS GPU)",
        url="http://127.0.0.1:5500",
        width=1440,
        height=900,
        resizable=True,
        min_size=(1100, 700),
        confirm_close=True
    )
    webview.start(debug=False)
