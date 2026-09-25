# -*- coding: utf-8 -*-
"""Bấm icon Idea Note: bật máy chủ ngầm (nếu chưa chạy) rồi mở cửa sổ app."""
import os
import socket
import subprocess
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 41900
URL = f"http://127.0.0.1:{PORT}/"


def up():
    try:
        socket.create_connection(("127.0.0.1", PORT), 0.4).close()
        return True
    except OSError:
        return False


if not up():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    subprocess.Popen([pyw, os.path.join(ROOT, "server.py")], cwd=ROOT,
                     creationflags=0x00000008 | 0x00000200)  # DETACHED + NEW_PROCESS_GROUP
    for _ in range(40):
        if up():
            break
        time.sleep(0.2)

# bong bóng nổi (tự thoát nếu đã có một cái đang chạy)
subprocess.Popen([os.path.join(os.path.dirname(sys.executable), "pythonw.exe"),
                  os.path.join(ROOT, "bubble.pyw")], cwd=ROOT, creationflags=0x00000008 | 0x00000200)

if "--server-only" in sys.argv:
    sys.exit(0)

for edge in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"):
    if os.path.exists(edge):
        subprocess.Popen([edge, f"--app={URL}", "--window-size=1180,860"])
        break
else:
    webbrowser.open(URL)
