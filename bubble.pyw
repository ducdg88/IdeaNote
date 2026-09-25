# -*- coding: utf-8 -*-
"""Bong bóng Idea Note: thu nhỏ app -> hiện icon nổi trên màn hình, kéo đi đâu cũng được.

Bấm vào bong bóng: mở lại cửa sổ Idea Note. Chuột phải: menu.
Chấm đỏ = số việc gấp chưa xong + nhắc hẹn đã tới giờ.
"""
import ctypes
import ctypes.wintypes as W
import datetime as dt
import json
import os
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
POS_F = os.path.join(ROOT, "data", "bubble.json")
PORT = 41900
SIZE = 64
KEY = "#ff00fe"  # màu làm trong suốt

# chỉ chạy 1 bong bóng
_lock = socket.socket()
try:
    _lock.bind(("127.0.0.1", 41901))
except OSError:
    sys.exit(0)

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.windll.user32
ENUM = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)


def find_app():
    """Tìm cửa sổ app Idea Note (Edge --app, tiêu đề 'Idea Note' hoặc '⏰ …' khi đang báo)."""
    found = []

    def cb(h, _):
        if user32.IsWindowVisible(h):
            n = user32.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(h, b, n + 1)
                if b.value == "Idea Note" or b.value.startswith("⏰ "):
                    c = ctypes.create_unicode_buffer(64)
                    user32.GetClassNameW(h, c, 64)
                    if c.value.startswith("Chrome_WidgetWin"):
                        found.append(h)
        return True

    user32.EnumWindows(ENUM(cb), 0)
    return found


def open_app():
    wins = find_app()
    if wins:
        normal = [h for h in wins if not user32.IsIconic(h)]
        h = normal[0] if normal else wins[0]
        user32.ShowWindow(h, 9 if user32.IsIconic(h) else 5)  # SW_RESTORE / SW_SHOW
        user32.SetForegroundWindow(h)
    else:
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        subprocess.Popen([pyw, os.path.join(ROOT, "launch.pyw")], cwd=ROOT)


def load_pos(sw, sh):
    try:
        with open(POS_F, encoding="utf-8") as f:
            p = json.load(f)
        if 0 <= p["x"] < sw - 20 and 0 <= p["y"] < sh - 20:
            return p
    except Exception:
        pass
    return {"x": sw - 120, "y": sh - 220, "always": False}


S = {"count": 0, "moved": False}


def poll_count():
    """Đếm việc gấp chưa xong + nhắc đã tới giờ, chạy nền mỗi 15 giây."""
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/notes", timeout=5) as r:
                notes = json.loads(r.read())["notes"]
            now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M")
            S["count"] = sum(1 for n in notes if not n.get("deleted") and n.get("status") != "done" and (
                n.get("urgent") or (n.get("remind_at") and n["remind_at"] <= now)))
        except Exception:
            pass
        time.sleep(15)


root = tk.Tk()
root.title("Idea Note bubble")
root.overrideredirect(True)
root.attributes("-topmost", True)
root.config(bg=KEY)
root.attributes("-transparentcolor", KEY)
pos = load_pos(root.winfo_screenwidth(), root.winfo_screenheight())
root.geometry(f"+{pos['x']}+{pos['y']}")

cv = tk.Canvas(root, width=SIZE + 12, height=SIZE + 12, bg=KEY, highlightthickness=0, cursor="hand2")
cv.pack()
img = tk.PhotoImage(file=os.path.join(ROOT, "bubble.png"))
cv.create_image(0, 12, image=img, anchor="nw")
badge = cv.create_oval(SIZE - 14, 0, SIZE + 10, 24, fill="#d93636", outline="white", width=2, state="hidden")
badge_t = cv.create_text(SIZE - 2, 12, text="", fill="white", font=("Segoe UI", 9, "bold"), state="hidden")


def save_pos():
    pos.update(x=root.winfo_x(), y=root.winfo_y())
    try:
        with open(POS_F, "w", encoding="utf-8") as f:
            json.dump(pos, f)
    except OSError:
        pass


def press(e):
    S.update(x0=e.x_root, y0=e.y_root, wx=root.winfo_x(), wy=root.winfo_y(), moved=False)


def motion(e):
    dx, dy = e.x_root - S["x0"], e.y_root - S["y0"]
    if abs(dx) + abs(dy) > 4:
        S["moved"] = True
    if S["moved"]:
        root.geometry(f"+{S['wx'] + dx}+{S['wy'] + dy}")


def release(e):
    if S["moved"]:
        save_pos()
    else:
        open_app()


always = tk.BooleanVar(value=bool(pos.get("always")))


def toggle_always():
    pos["always"] = always.get()
    save_pos()


menu = tk.Menu(root, tearoff=0)
menu.add_command(label="📝  Mở Idea Note", command=open_app)
menu.add_checkbutton(label="📌  Luôn hiện bong bóng", variable=always, command=toggle_always)
menu.add_separator()
menu.add_command(label="✕  Tắt bong bóng (tới lần mở app sau)", command=root.destroy)

cv.bind("<ButtonPress-1>", press)
cv.bind("<B1-Motion>", motion)
cv.bind("<ButtonRelease-1>", release)
cv.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))

shown = [True]


def tick():
    try:
        # hiện bong bóng khi không còn cửa sổ Idea Note nào đang mở trên màn hình
        want = always.get() or all(user32.IsIconic(h) for h in find_app())
        if want and not shown[0]:
            root.deiconify()
            root.attributes("-topmost", True)
            root.lift()
            shown[0] = True
        elif not want and shown[0]:
            root.withdraw()
            shown[0] = False
        c = S["count"]
        st = "normal" if c else "hidden"
        cv.itemconfigure(badge, state=st)
        cv.itemconfigure(badge_t, state=st, text=str(c) if c < 100 else "99+")
    except Exception:
        pass
    root.after(400, tick)


threading.Thread(target=poll_count, daemon=True).start()
root.after(300, tick)
root.mainloop()
