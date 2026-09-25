# -*- coding: utf-8 -*-
"""Đóng gói bản Windows để tải từ GitHub: python build_release.py

Ra release/IdeaNote-v<phiên bản>-win64.zip. Giải nén ra đâu cũng chạy (miễn không phải Program Files),
dữ liệu tạo trong thư mục data/ cạnh IdeaNote.exe. Điện thoại dùng luôn: app có sẵn máy chủ nhỏ + trang web.
Không đóng gói torch / transformers / opencv (app không dùng) cho nhẹ.
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "release")
VERSION = re.search(r'^VERSION = "([^"]+)"', open(os.path.join(ROOT, "server.py"), encoding="utf-8").read(), re.M).group(1)
EXCLUDE = ["torch", "torchvision", "torchaudio", "transformers", "matplotlib", "jedi", "IPython", "llvmlite", "numba",
           "cv2", "pandas", "scipy", "sklearn", "tkinter", "sympy", "tensorflow", "notebook", "pytest", "sentence_transformers"]
FILES = ["index.html", "icon.ico", "icon.png", "icon_round.png", "manifest.webmanifest",
         "mo-tuong-lua-cho-dien-thoai.cmd", "README.md"]


def main():
    dist, work = os.path.join(OUT, "dist"), os.path.join(OUT, "build")
    shutil.rmtree(dist, ignore_errors=True)
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--windowed", "--onedir", "--name", "IdeaNote",
           "--icon", os.path.join(ROOT, "icon.ico"), "--paths", ROOT, "--distpath", dist, "--workpath", work,
           "--specpath", work, "--hidden-import", "knowledge"]
    cmd += [f"--exclude-module={m}" for m in EXCLUDE] + [os.path.join(ROOT, "ideanote_app.py")]
    subprocess.run(cmd, check=True, cwd=ROOT)
    app = os.path.join(dist, "IdeaNote")
    for f in FILES:  # app tìm index.html cạnh IdeaNote.exe -> thư mục này là "nhà" của app
        shutil.copy2(os.path.join(ROOT, f), app)
    zpath = os.path.join(OUT, f"IdeaNote-v{VERSION}-win64.zip")
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for base, _, names in os.walk(app):
            for n in names:
                full = os.path.join(base, n)
                z.write(full, os.path.join("IdeaNote", os.path.relpath(full, app)))
    print(f"Xong: {zpath} ({os.path.getsize(zpath) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
