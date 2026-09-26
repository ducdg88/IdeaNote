# -*- coding: utf-8 -*-
"""Idea Note — app Windows (Qt) ghi chú nhanh: chữ + ảnh, dự án, việc/gấp/tiến độ, nhắc hẹn.

Chạy chung dữ liệu với server.py (máy chủ nhỏ cho điện thoại + luồng nhắc hẹn chạy ngay trong app).
"""
import ctypes
import ctypes.wintypes as W
import datetime as dt
import html
import io
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
import urllib.parse
import webbrowser
import winsound

FROZEN = getattr(sys, "frozen", False)


def _find_home():
    """Bản .exe nằm trong E:\\IdeaNote\\bin\\IdeaNote\\ — đi ngược lên tìm thư mục có index.html + data."""
    d = os.path.dirname(sys.executable)
    for _ in range(4):
        if os.path.exists(os.path.join(d, "index.html")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(sys.executable)


HOME = os.environ.get("IDEANOTE_HOME") or (_find_home() if FROZEN else os.path.dirname(os.path.abspath(__file__)))
os.environ["IDEANOTE_HOME"] = HOME

import server as S  # noqa: E402  (phải đặt IDEANOTE_HOME trước)
import assistant as A  # noqa: E402
import voicerules as V  # noqa: E402
import mailer as M  # noqa: E402

from PySide6.QtCore import (QAbstractNativeEventFilter, QBuffer, QByteArray, QDate, QDateTime, QEvent, QIODevice,
                            QLocale, QObject, QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QFont, QFontMetrics, QIcon, QImage, QKeySequence,
                           QPainter, QPen, QPixmap, QShortcut, QTextCharFormat)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCalendarWidget, QCheckBox, QComboBox,
                               QDateEdit, QDateTimeEdit, QDialog, QFileDialog, QFrame, QGraphicsPixmapItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QTextBrowser, QScrollArea, QSizePolicy, QSlider, QSplitter, QStyle,
                               QStyledItemDelegate, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)

APP_F = os.path.join(S.DATA, "app.json")
ICON = os.path.join(HOME, "icon.ico")
C = dict(bg="#f6f5f2", panel="#ffffff", ink="#1d1d1f", muted="#6b6b72", line="#e6e3dc", soft="#f1efe9",
         accent="#e08a00", accent_soft="#fff3dc", red="#d93636", red_soft="#fde8e8", green="#1f9d55",
         green_soft="#e3f6ea", blue="#2f6fdb", blue_soft="#e6eefc", claude="#d97757")
STATUS = {"note": "💡 Ý tưởng / ghi chú", "todo": "⬜ Chưa làm", "doing": "🔄 Đang làm", "verify": "⏳ Chờ verify",
          "done": "✅ Hoàn thành"}
SPEC7_HEAD, SPEC7 = S.SPEC7_HEAD, S.SPEC7
SPEC7_SUGGEST = 300  # việc dài từ chừng này ký tự mà chưa có khung -> gợi ý 🪄 Sắp vào 7 mục
WD = ["Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật"]


# ---------- tiện ích ----------
def today():
    return f"{dt.date.today()}"


def now_str():
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M")


def fold(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


def fmt_remind(v):
    d, t = v[:10], v[11:16]
    if d == today():
        return t + " hôm nay"
    if d == f"{dt.date.today() + dt.timedelta(days=1)}":
        return t + " mai"
    return f"{t} {d[8:10]}/{d[5:7]}"


def day_label(ds):
    d = dt.date.fromisoformat(ds)
    base = f"{WD[d.weekday()]}, {d:%d/%m/%Y}"
    diff = (d - dt.date.today()).days
    return {0: "Hôm nay · ", -1: "Hôm qua · ", 1: "Ngày mai · "}.get(diff, "") + base


def img_path(name):
    return os.path.join(S.IMG, name)


def is_task(n):
    return n.get("status") != "note"


def is_open(n):
    """Còn phải làm: chưa xong và chưa tick "đã làm" (chờ verify không còn gấp / trễ hẹn nữa)."""
    return n.get("status") not in ("done", "verify")


def is_urgent(n):
    return n.get("urgent") and is_open(n)


def overdue(n):
    return n.get("remind_at") and is_open(n) and not n.get("deleted") and n["remind_at"] <= now_str()


FILTERS = [
    ("overview", "📊", "Tổng quan", lambda n: True),  # toàn bộ ghi chú (cả việc đã xong), gom theo dự án
    ("today", "📌", "Hôm nay", lambda n: n["date"] == today() or (n.get("remind_at") or "")[:10] == today()),
    ("all", "🗂", "Tất cả", lambda n: True),
    ("todo", "⬜", "Chưa làm", lambda n: n["status"] == "todo"),
    ("doing", "🔄", "Đang làm", lambda n: n["status"] == "doing"),
    ("verify", "⏳", "Chờ verify", lambda n: n["status"] == "verify"),
    ("urgent", "🔴", "Gấp", is_urgent),
    ("remind", "⏰", "Nhắc hẹn", lambda n: n.get("remind_at") and is_open(n)),
    ("done", "✅", "Hoàn thành", lambda n: n["status"] == "done"),
    ("note", "💡", "Ý tưởng / ghi chú", lambda n: n["status"] == "note"),
    ("trash", "🗑", "Thùng rác", lambda n: n.get("deleted")),
]


def snapshot():
    with S.LOCK:
        return [dict(n) for n in S.NOTES]


def find_live(nid):
    return next((n for n in S.NOTES if n["id"] == nid), None)


def update_note(nid, body):
    with S.LOCK:
        n = find_live(nid)
        if not n:
            return None
        S.apply(n, body)
        if "images" in body:
            drop = [x for x in n.get("images", []) if x not in body["images"]]
            S.drop_images(drop)
            n["images"] = list(body["images"])
        S.persist()
        return dict(n)


def delete_note(nid):
    with S.LOCK:
        n = find_live(nid)
        if not n:
            return
        if n.get("deleted"):
            S.NOTES.remove(n)
            S.drop_images(n.get("images", []))
        else:
            n["deleted"] = True
            n["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        S.persist()


def qimage_to_file(img: QImage):
    """Lưu ảnh dán/kéo vào data/img. Ảnh quá to thì thu về 2000px (JPEG) cho nhẹ."""
    big = max(img.width(), img.height())
    ext, fmt, q = "png", "PNG", -1
    if big > 2400:
        img = img.scaled(QSize(2000, 2000), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        ext, fmt, q = "jpg", "JPG", 86
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, fmt, q)
    return S.save_image_bytes(bytes(ba.data()), ext)


def images_from_mime(md):
    out = []
    if md.hasUrls():
        for u in md.urls():
            p = u.toLocalFile()
            if p and os.path.splitext(p)[1].lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
                img = QImage(p)
                if not img.isNull():
                    out.append(img)
    if not out and md.hasImage():
        img = QImage(md.imageData())
        if not img.isNull():
            out.append(img)
    return out


def gcal_url(n):
    text = n.get("text") or "Ghi chú"
    title = text.strip().split("\n")[0][:80]
    if n.get("remind_at"):
        s = dt.datetime.strptime(n["remind_at"], "%Y-%m-%dT%H:%M")
        dates = f"{s:%Y%m%dT%H%M%S}/{s + dt.timedelta(minutes=30):%Y%m%dT%H%M%S}"
    else:
        d = dt.date.fromisoformat(n["date"])
        dates = f"{d:%Y%m%d}/{d + dt.timedelta(days=1):%Y%m%d}"
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE&text=" + urllib.parse.quote(title)
            + "&dates=" + dates + "&details=" + urllib.parse.quote(text))


def gmail_url(n):
    text = n.get("text") or ""
    subj = text.strip().split("\n")[0][:80] or "Ghi chú"
    return ("https://mail.google.com/mail/?view=cm&fs=1&to=" + urllib.parse.quote(S.CONF.get("email", ""))
            + "&su=" + urllib.parse.quote(subj) + "&body=" + urllib.parse.quote(text))


def note_title(n):
    """Mục chính của ghi chú: tiêu đề đã đặt, không có thì lấy dòng đầu."""
    t = (n.get("title") or "").strip()
    if t:
        return t
    first = ((n.get("text") or "").strip().split("\n") or [""])[0].strip()
    return first[:80] or ("(ảnh)" if n.get("images") else "(trống)")


def note_rest(n):
    """Phần còn lại sau mục chính (để hiện mờ trên danh sách)."""
    text = (n.get("text") or "").strip()
    if n.get("title"):
        return " ".join(text.split())
    lines = text.split("\n")
    first = lines[0].strip()
    rest = " ".join(" ".join(lines[1:]).split())
    return (first[80:] + " " + rest).strip() if len(first) > 80 else rest


def note_block(n):
    tags = [is_urgent(n) and "GẤP",
            STATUS[n["status"]].split(" ", 1)[1] + (f" {n.get('progress', 0)}%" if n["status"] == "doing" else ""),
            f"{n['date'][8:10]}/{n['date'][5:7]}", n.get("remind_at") and "nhắc " + fmt_remind(n["remind_at"])]
    out = [f"- **{note_title(n)}** [" + " · ".join(t for t in tags if t) + "]" + ("" if n.get("text") else " (chỉ có ảnh)")]
    out += ["  " + line for line in (n.get("text") or "").strip().split("\n") if n.get("text")]
    out += ["  Ảnh: " + img_path(f) for f in n.get("images", [])]
    return "\n".join(out)


def context_text(notes, title):
    d = dt.datetime.now()
    has_img = any(n.get("images") for n in notes)
    lines = [f"# {title}", f"(Ngữ cảnh chép từ Idea Note lúc {d:%H:%M %d/%m/%Y} — {len(notes)} mục"
             + (". Ảnh là file trên máy tính, hãy mở/đọc theo đường dẫn.)" if has_img else ")")]
    groups = [("🔴 Gấp", is_urgent), ("🔄 Đang làm", lambda n: n["status"] == "doing"),
              ("⬜ Chưa làm", lambda n: n["status"] == "todo"), ("⏳ Chờ verify", lambda n: n["status"] == "verify"),
              ("💡 Ý tưởng / ghi chú", lambda n: n["status"] == "note"), ("✅ Đã xong", lambda n: n["status"] == "done")]
    used = set()
    for label, fn in groups:
        g = sorted((n for n in notes if n["id"] not in used and fn(n)), key=lambda n: n.get("created", ""))
        if g:
            used.update(n["id"] for n in g)
            lines += ["", f"## {label} ({len(g)})"] + [note_block(n) for n in g]
    return "\n".join(lines) + "\n"


def load_app_conf():
    try:
        with open(APP_F, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_APP_LOCK = threading.Lock()
_APP_LATEST = [None]


def save_app_conf(conf):
    """Ghi app.json ở luồng nền (kéo bong bóng / đổi cỡ cửa sổ không phải chờ ổ E: HDD)."""
    _APP_LATEST[0] = json.loads(json.dumps(conf))

    def run():
        with _APP_LOCK:  # luôn ghi bản MỚI NHẤT, luồng nào tới sau cũng không đè bản cũ lên
            try:
                S.save(APP_F, _APP_LATEST[0])
            except OSError:
                pass
    threading.Thread(target=run, daemon=True).start()


def rounded_icon(size=64):
    pm = QPixmap(os.path.join(HOME, "icon.png"))
    return pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


# ---------- ô gõ nhận ảnh dán / kéo thả ----------
class PasteEdit(QPlainTextEdit):
    imagesPasted = Signal(list)
    submit = Signal()
    focused = Signal(bool)

    def canInsertFromMimeData(self, md):
        return bool(images_from_mime(md)) or super().canInsertFromMimeData(md)

    def insertFromMimeData(self, md):
        imgs = images_from_mime(md)
        if imgs:
            self.imagesPasted.emit(imgs)
            if md.hasText() and not md.hasUrls():
                super().insertFromMimeData(md)
            return
        super().insertFromMimeData(md)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.submit.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focused.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focused.emit(False)


# ---------- vẽ cột trái ----------
class SideDelegate(QStyledItemDelegate):
    def sizeHint(self, opt, idx):
        return QSize(opt.rect.width(), 26 if idx.data(Qt.ItemDataRole.UserRole + 1) == "h" else 34)

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = opt.rect
        kind = idx.data(Qt.ItemDataRole.UserRole + 1)
        if kind == "h":
            f = QFont(opt.font)
            f.setPointSizeF(8.5)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(C["muted"]))
            p.drawText(r.adjusted(10, 0, 0, -3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, idx.data())
            p.restore()
            return
        sel = opt.state & QStyle.StateFlag.State_Selected
        hov = opt.state & QStyle.StateFlag.State_MouseOver
        box = r.adjusted(4, 1, -4, -1)
        if sel or hov:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(C["accent_soft"] if sel else C["soft"]))
            p.drawRoundedRect(box, 8, 8)
        f = QFont(opt.font)
        f.setBold(bool(sel))
        p.setFont(f)
        p.setPen(QColor(C["accent"] if sel else C["ink"]))
        count = str(idx.data(Qt.ItemDataRole.UserRole + 2) or 0)
        fm = QFontMetrics(opt.font)
        cw = fm.horizontalAdvance(count) + 14
        text = fm.elidedText(idx.data(), Qt.TextElideMode.ElideRight, box.width() - cw - 24)
        p.drawText(box.adjusted(10, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, text)
        pill = QRect(box.right() - cw - 6, box.center().y() - 9, cw, 18)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(C["panel"] if sel else C["soft"]))
        p.drawRoundedRect(pill, 9, 9)
        f2 = QFont(opt.font)
        f2.setPointSizeF(8.5)
        p.setFont(f2)
        p.setPen(QColor(C["muted"]))
        p.drawText(pill, Qt.AlignmentFlag.AlignCenter, count)
        p.restore()


# ---------- vẽ danh sách ghi chú (mỗi ghi chú 1 dòng) ----------
class NoteDelegate(QStyledItemDelegate):
    def __init__(self, win):
        super().__init__()
        self.win = win

    def sizeHint(self, opt, idx):
        return QSize(1, 30 if idx.data(Qt.ItemDataRole.UserRole + 1) == "h" else 42)  # rộng theo khung, không giữ bề ngang cũ

    @staticmethod
    def pill(p, x, cy, text, fg, bg, font):
        fm = QFontMetrics(font)
        w = fm.horizontalAdvance(text) + 12
        rect = QRect(int(x), int(cy - 10), w, 20)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(bg))
        p.drawRoundedRect(rect, 6, 6)
        p.setFont(font)
        p.setPen(QColor(fg))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return w

    def paint(self, p, opt, idx):
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = opt.rect
        if idx.data(Qt.ItemDataRole.UserRole + 1) == "h":
            p.fillRect(r, QColor(C["panel"]))
            f = QFont(opt.font)
            f.setPointSizeF(9)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(C["muted"]))
            p.drawText(r.adjusted(14, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, idx.data())
            p.setPen(QColor(C["line"]))
            p.drawLine(r.bottomLeft(), r.bottomRight())
            p.restore()
            return
        n = self.win.by_id.get(idx.data(Qt.ItemDataRole.UserRole))
        if not n:
            p.restore()
            return
        sel = opt.state & QStyle.StateFlag.State_Selected
        hov = opt.state & QStyle.StateFlag.State_MouseOver
        p.fillRect(r, QColor(C["accent_soft"] if sel else C["soft"] if hov else C["panel"]))
        if sel or is_urgent(n):
            p.fillRect(QRect(r.left(), r.top(), 3, r.height()), QColor(C["accent"] if sel else C["red"]))
        cy = r.center().y()
        x = r.left() + 14
        small = QFont(opt.font)
        small.setPointSizeF(8.5)
        bold_small = QFont(small)
        bold_small.setBold(True)
        done = n["status"] == "done"
        if is_task(n):
            box = QRect(x, cy - 9, 18, 18)
            if done:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(C["green"]))
                p.drawEllipse(box)
                p.setPen(QPen(QColor("white"), 2))
                p.drawPolyline([QPoint(x + 5, cy), QPoint(x + 8, cy + 3), QPoint(x + 13, cy - 3)])
            elif n["status"] == "verify":  # đã làm, chờ kiểm chứng: vòng cam + dấu tích cam
                p.setPen(QPen(QColor(C["accent"]), 2))
                p.setBrush(QColor(C["accent_soft"]))
                p.drawEllipse(box)
                p.drawPolyline([QPoint(x + 5, cy), QPoint(x + 8, cy + 3), QPoint(x + 13, cy - 3)])
            else:
                p.setPen(QPen(QColor(C["blue"] if n["status"] == "doing" else C["muted"]), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(box)
        else:
            p.setFont(opt.font)
            p.setPen(QColor(C["ink"]))
            p.drawText(QRect(x, r.top(), 20, r.height()), Qt.AlignmentFlag.AlignCenter, "💡")
        x += 28
        if is_urgent(n):
            x += self.pill(p, x, cy, "GẤP", C["red"], C["red_soft"], bold_small) + 8
        # phần thông tin bên phải, vẽ từ phải sang trái
        right = r.right() - 12
        fm_s = QFontMetrics(small)

        def rtext(t, color=C["muted"], font=small):
            nonlocal right
            w = QFontMetrics(font).horizontalAdvance(t)
            p.setFont(font)
            p.setPen(QColor(color))
            p.drawText(QRect(right - w, r.top(), w, r.height()), Qt.AlignmentFlag.AlignVCenter, t)
            right -= w + 10

        rtext(("📱" if n.get("from") == "phone" else "") + (n.get("created") or "")[11:16])
        if n.get("remind_at"):
            rtext(("🔁 " if n.get("repeat") else "⏰ ") + fmt_remind(n["remind_at"]), C["red"] if overdue(n) else C["accent"],
                  bold_small if overdue(n) else small)
        if n.get("images"):
            rtext("📷" + (str(len(n["images"])) if len(n["images"]) > 1 else ""))
        if n.get("project") and self.win.filter not in ("proj", "overview"):
            w = fm_s.horizontalAdvance(n["project"][:18]) + 12
            right -= w
            self.pill(p, right, cy, n["project"][:18], C["blue"], C["blue_soft"], small)
            right -= 10
        if n["status"] == "doing":
            rtext(f"🔄 {n.get('progress', 0)}%", C["blue"])
        if n["status"] == "verify":
            rtext("⏳ chờ verify", C["accent"], bold_small)
        # mục chính (in đậm) + phần còn lại của ghi chú (chữ mờ)
        width = max(10, right - x)
        f = QFont(opt.font)
        f.setBold(True)
        f.setStrikeOut(done)
        fm = QFontMetrics(f)
        head = fm.elidedText(note_title(n), Qt.TextElideMode.ElideRight, width)
        p.setFont(f)
        p.setPen(QColor(C["muted"] if done else C["ink"]))
        p.drawText(QRect(x, r.top(), width, r.height()), Qt.AlignmentFlag.AlignVCenter, head)
        rest, used = note_rest(n), fm.horizontalAdvance(head) + 12
        if rest and width - used > 40:
            f2 = QFont(opt.font)
            p.setFont(f2)
            p.setPen(QColor(C["muted"]))
            p.drawText(QRect(x + used, r.top(), width - used, r.height()), Qt.AlignmentFlag.AlignVCenter,
                       QFontMetrics(f2).elidedText("— " + rest, Qt.TextElideMode.ElideRight, width - used))
        p.setPen(QColor(C["soft"]))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        p.restore()


class NoteList(QListWidget):
    toggled = Signal(str)

    def mousePressEvent(self, e):
        it = self.itemAt(e.position().toPoint())
        if it and it.data(Qt.ItemDataRole.UserRole + 1) == "n":
            rect = self.visualItemRect(it)
            n = self.window().by_id.get(it.data(Qt.ItemDataRole.UserRole))
            if n and is_task(n) and e.position().x() < rect.left() + 36:
                self.toggled.emit(n["id"])
                return
        super().mousePressEvent(e)


# ---------- hàng nút tự xuống dòng (để cửa sổ thu nhỏ được) ----------
class FlowLayout(QLayout):
    """Xếp widget thành hàng, hết chỗ thì xuống dòng. Kích thước tối thiểu chỉ bằng widget to nhất."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._place(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._place(r, False)

    def sizeHint(self):  # 1 hàng đủ rộng -> layout cha cho đủ chỗ khi cửa sổ rộng
        shown = [it for it in self.items if not it.isEmpty()]
        w = sum(it.sizeHint().width() for it in shown) + self.spacing() * max(0, len(shown) - 1)
        h = max((it.sizeHint().height() for it in shown), default=0)
        return QSize(w, h)

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            if not it.isEmpty():
                s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, rect, test):
        sp, x, y, lines, cur = self.spacing(), rect.x(), rect.y(), [], []
        for it in self.items:
            if it.isEmpty():
                continue
            w = it.sizeHint().width()
            if cur and x + w > rect.right() + 1:
                lines.append(cur)
                cur, x = [], rect.x()
            cur.append(it)
            x += w + sp
        if cur:
            lines.append(cur)
        for line in lines:
            h = max(it.sizeHint().height() for it in line)
            x = rect.x()
            for it in line:
                sz = it.sizeHint()
                if not test:  # cùng hàng thì canh giữa theo chiều dọc
                    it.setGeometry(QRect(QPoint(x, y + (h - sz.height()) // 2), sz))
                x += sz.width() + sp
            y += h + sp
        return max(0, y - sp - rect.y())


def flow_widget(spacing=6):
    w = QWidget()
    return w, FlowLayout(w, spacing)


# ---------- xem ảnh ----------
class ZoomView(QGraphicsView):
    """Khung ảnh: lăn chuột để phóng to / thu nhỏ tại chỗ con trỏ, kéo để di chuyển, bấm đúp: vừa khung <-> 100%."""
    zoomed = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.pix = QGraphicsPixmapItem()
        self.pix.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self.pix)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor("#111114"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # ← → để chuyển ảnh, không cuộn khung
        self.fit = True

    def show_pixmap(self, pm):
        self.pix.setPixmap(pm)
        self.scene().setSceneRect(self.pix.boundingRect())
        self.fit = True
        self.refit()

    def refit(self):
        self.fit = True
        self.resetTransform()
        r = self.pix.boundingRect()
        if r.width() > self.viewport().width() or r.height() > self.viewport().height():  # ảnh nhỏ thì giữ nguyên cỡ
            self.fitInView(self.pix, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomed.emit()

    def scale_now(self):
        return self.transform().m11()

    def zoom_by(self, k):
        cur = self.scale_now()
        k = max(0.05, min(12.0, cur * k)) / cur
        self.scale(k, k)
        self.fit = False
        self.zoomed.emit()

    def actual(self, at=None):
        p = self.mapToScene(at) if at is not None else None
        self.resetTransform()
        self.fit = False
        if p is not None:
            self.centerOn(p)
        self.zoomed.emit()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.fit:
            self.refit()

    def wheelEvent(self, e):
        self.zoom_by(1.2 if e.angleDelta().y() > 0 else 1 / 1.2)

    def mouseDoubleClickEvent(self, e):
        if self.fit and self.scale_now() < 0.999:
            self.actual(e.position().toPoint())
        else:
            self.refit()


class GalleryViewer(QDialog):
    """Xem ảnh của 1 ghi chú: ← → chuyển ảnh, lăn chuột phóng to, dải ảnh nhỏ bên dưới, xem chữ máy đọc trong ảnh."""
    show_text = False  # nhớ giữa các lần mở

    def __init__(self, files, index=0, title="", parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.files = list(files)
        self.i = 0
        self.title_text = title
        self.setObjectName("gallery")
        self.setWindowIcon(QIcon(ICON))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QFrame()
        top.setObjectName("gtop")
        tl = QHBoxLayout(top)
        tl.setContentsMargins(10, 6, 10, 6)
        self.count = QLabel()
        self.count.setObjectName("gcount")
        tl.addWidget(self.count)
        self.cap = QLabel(title)
        self.cap.setObjectName("gcap")
        self.cap.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        tl.addWidget(self.cap, 1)
        self.zlabel = QLabel()
        self.zlabel.setObjectName("gcap")
        tl.addWidget(self.zlabel)
        self.b_text = None
        for text, tip, fn, key in (("⤢ Vừa khung", "Vừa khung (F)", lambda: self.view.refit(), "F"),
                                   ("1:1", "Cỡ thật 100% (0)", lambda: self.view.actual(), "0"),
                                   ("📝 Chữ trong ảnh", "Chữ máy đọc được trong ảnh, chép được (T)", self.toggle_text, "T"),
                                   ("📋 Chép ảnh", "Chép ảnh để dán vào Zalo / Claude (Ctrl+C)", self.copy, "Ctrl+C"),
                                   ("📂", "Mở thư mục chứa ảnh", lambda: subprocess.Popen(["explorer", "/select,", self.path()]), None),
                                   ("🖼", "Mở bằng app xem ảnh của Windows", lambda: os.startfile(self.path()), None),
                                   ("✕", "Đóng (Esc)", self.close, None)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(fn)
            tl.addWidget(b)
            if key:
                QShortcut(QKeySequence(key), self, fn)
            if text.startswith("📝"):
                b.setCheckable(True)
                self.b_text = b
        lay.addWidget(top)
        split = QSplitter()
        split.setObjectName("gsplit")
        stage = QWidget()
        stage.setObjectName("gstage")
        stage.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        sl = QHBoxLayout(stage)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.b_prev, self.b_next = QPushButton("‹"), QPushButton("›")
        self.view = ZoomView()
        self.view.zoomed.connect(self.show_zoom)
        for b, tip, step in ((self.b_prev, "Ảnh trước (←)", -1), (self.b_next, "Ảnh sau (→)", 1)):
            b.setObjectName("gnav")
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, s=step: self.go(self.i + s))
        sl.addWidget(self.b_prev)
        sl.addWidget(self.view, 1)
        sl.addWidget(self.b_next)
        split.addWidget(stage)
        self.textbox = QFrame()
        self.textbox.setObjectName("gtext")
        xl = QVBoxLayout(self.textbox)
        xl.setContentsMargins(10, 8, 10, 10)
        xh = QHBoxLayout()
        xh.addWidget(QLabel("<b>📝 Chữ máy đọc được trong ảnh</b>"), 1)
        bc = QPushButton("📋 Chép chữ")
        bc.clicked.connect(lambda: (QApplication.clipboard().setText(A_K().ocr_text(self.files[self.i])),
                                    self.flash("Đã chép chữ trong ảnh")))
        xh.addWidget(bc)
        xl.addLayout(xh)
        self.ocr = QPlainTextEdit()
        self.ocr.setReadOnly(True)
        xl.addWidget(self.ocr, 1)
        split.addWidget(self.textbox)
        split.setStretchFactor(0, 1)
        split.setSizes([900, 360])
        lay.addWidget(split, 1)
        self.strip = QListWidget()
        self.strip.setObjectName("gstrip")
        self.strip.setViewMode(QListWidget.ViewMode.IconMode)
        self.strip.setFlow(QListWidget.Flow.LeftToRight)
        self.strip.setWrapping(False)
        self.strip.setIconSize(QSize(72, 54))
        self.strip.setGridSize(QSize(84, 64))
        self.strip.setFixedHeight(82)
        self.strip.setMovement(QListWidget.Movement.Static)
        self.strip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.strip.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.strip.currentRowChanged.connect(lambda r: r >= 0 and r != self.i and self.go(r))
        for f in self.files:
            it = QListWidgetItem("")
            it.setToolTip(f)
            self.strip.addItem(it)
        self.strip.setVisible(len(self.files) > 1)
        lay.addWidget(self.strip)
        for key, fn in (("Right", lambda: self.go(self.i + 1)), ("Left", lambda: self.go(self.i - 1)),
                        ("PgDown", lambda: self.go(self.i + 1)), ("PgUp", lambda: self.go(self.i - 1)),
                        ("Space", lambda: self.go(self.i + 1)), ("Home", lambda: self.go(0)),
                        ("End", lambda: self.go(len(self.files) - 1)), ("+", lambda: self.view.zoom_by(1.25)),
                        ("=", lambda: self.view.zoom_by(1.25)), ("-", lambda: self.view.zoom_by(0.8))):
            QShortcut(QKeySequence(key), self, fn)
        scr = (QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()).availableGeometry()
        self.resize(int(scr.width() * 0.88), int(scr.height() * 0.9))
        self.move(scr.center() - self.rect().center())
        self.b_text.setChecked(GalleryViewer.show_text)
        self.textbox.setVisible(GalleryViewer.show_text)
        self.thumb_i = 0
        QTimer.singleShot(0, self.load_thumbs)
        self.go(index)

    def path(self):
        return img_path(self.files[self.i])

    def go(self, i):
        if not self.files:
            return
        self.i = i % len(self.files)
        pm = QPixmap(self.path())
        self.view.show_pixmap(pm)
        n = len(self.files)
        self.count.setText(f"{self.i + 1} / {n}" if n > 1 else "")
        self.b_prev.setVisible(n > 1)
        self.b_next.setVisible(n > 1)
        self.setWindowTitle(f"Ảnh {self.i + 1}/{n} · {self.title_text}" if self.title_text else "Ảnh · Idea Note")
        self.strip.blockSignals(True)
        self.strip.setCurrentRow(self.i)
        self.strip.blockSignals(False)
        self.strip.scrollToItem(self.strip.item(self.i))
        t = A_K().ocr_text(self.files[self.i])
        self.ocr.setPlainText(t or "Máy chưa đọc được chữ trong ảnh này (ảnh mới lưu thì đợi vài giây, máy đang đọc ở nền).")

    def load_thumbs(self):
        """Ảnh nhỏ ở dải dưới: nạp dần 4 ảnh một lần để cửa sổ mở ngay."""
        for _ in range(4):
            if self.thumb_i >= len(self.files):
                return
            k = self.thumb_i
            pm = QPixmap(img_path(self.files[k]))
            if not pm.isNull():
                self.strip.item(k).setIcon(QIcon(pm.scaled(144, 108, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation)))
            self.thumb_i += 1
        QTimer.singleShot(0, self.load_thumbs)

    def show_zoom(self):
        self.zlabel.setText(f"{round(self.view.scale_now() * 100)}%")

    def toggle_text(self):
        GalleryViewer.show_text = not self.textbox.isVisible()
        self.textbox.setVisible(GalleryViewer.show_text)
        self.b_text.setChecked(GalleryViewer.show_text)

    def copy(self):
        if self.ocr.hasFocus() and self.ocr.textCursor().hasSelection():
            self.ocr.copy()
            return
        QApplication.clipboard().setImage(QImage(self.path()))
        self.flash("Đã chép ảnh, Ctrl+V để dán")

    def flash(self, msg):
        self.zlabel.setText(msg)
        QTimer.singleShot(1800, self.show_zoom)


def A_K():
    import knowledge
    return knowledge


# ---------- hỏi kho kiến thức ----------
class KBView(QTextBrowser):
    """Khung trả lời: ảnh nguồn hiện bằng ảnh nhỏ (thumb:tên ảnh) để không phải nạp ảnh gốc mỗi lần vẽ lại."""
    cache = {}

    def loadResource(self, typ, url):
        s = url.toString()
        if s.startswith("thumb:"):
            f = s[6:]
            if f not in self.cache:
                self.cache[f] = QImage(img_path(f)).scaled(200, 130, Qt.AspectRatioMode.KeepAspectRatio,
                                                           Qt.TransformationMode.SmoothTransformation)
            return self.cache[f]
        return super().loadResource(typ, url)


def answer_html(text, qid):
    """Câu trả lời của AI -> HTML: [1] thành link tới nguồn, dòng "- " thành danh sách."""
    out, in_list = [], False
    for raw in (text or "").split("\n"):
        line = html.escape(raw)
        line = re.sub(r"\[(\d+)\]", lambda m: f'<a href="src:{qid}:{m.group(1)}" style="color:{C["blue"]};'
                                               f'text-decoration:none;font-weight:700">[{m.group(1)}]</a>', line)
        line = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", line)
        m = re.match(r"^\s*(?:[-*•]|\d+\.)\s+(.*)$", line)
        if m:
            if not in_list:
                out.append("<ul style='margin:2px 0 2px -18px'>")
                in_list = True
            out.append(f"<li>{m.group(1)}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if line.strip():
                out.append(f"<p style='margin:3px 0'>{line}</p>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


class KnowledgePanel(QWidget):
    """🧠 Hỏi kho kiến thức: tìm trong chữ ghi chú + chữ trong ảnh, AI trong máy trả lời kèm ghi chú nguồn."""
    done = Signal(int, dict)

    def __init__(self, win):
        super().__init__(None, Qt.WindowType.Window)
        self.win = win
        self.items = []
        self.busy = False
        self.setWindowTitle("🧠 Hỏi kho kiến thức · Idea Note")
        self.setWindowIcon(QIcon(ICON))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("assist")
        self.done.connect(self.on_done)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        tip = QLabel("Hỏi bằng lời thường về mọi thứ đã lưu: chữ ghi chú <b>và chữ trong ảnh chụp</b> (máy tự đọc). "
                     "Trả lời kèm <b>nguồn</b>: ghi chú nào, dự án nào, ảnh số mấy. Bấm nguồn để mở đúng chỗ. Xử lý <b>trong máy</b>.")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        lay.addWidget(tip)
        self.stat = QLabel()
        self.stat.setWordWrap(True)
        self.stat.setObjectName("muted")
        lay.addWidget(self.stat)
        self.view = KBView()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self.open_link)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.scope = QComboBox()
        self.scope.setToolTip("Hỏi trong toàn bộ ghi chú hay chỉ 1 dự án")
        self.scope.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.scope.setMinimumContentsLength(10)
        row.addWidget(self.scope)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ví dụ: kiến thức về CoS? Buổi anh Sơn nói gì về đội agent?")
        self.input.returnPressed.connect(self.ask)
        row.addWidget(self.input, 1)
        self.b_ask = QPushButton("Hỏi")
        self.b_ask.setObjectName("primary")
        self.b_ask.clicked.connect(self.ask)
        row.addWidget(self.b_ask)
        b_clear = QPushButton("🧹")
        b_clear.setToolTip("Xoá cuộc hỏi")
        b_clear.clicked.connect(lambda: (self.items.clear(), self.render()))
        row.addWidget(b_clear)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.show_status)
        self.resize(760, 760)
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center() - self.rect().center())
        self.render()

    def open(self):
        cur = self.scope.currentData() if self.scope.count() else "*"
        self.scope.clear()
        self.scope.addItem("🗂 Tất cả ghi chú", "*")
        for p in self.win.projects():
            self.scope.addItem(f"📁 {p['name']}", p["name"])
        want = self.win.proj if self.win.filter == "proj" and self.win.proj else cur
        self.scope.setCurrentIndex(max(0, self.scope.findData(want)))
        self.show_status()
        self.timer.start(3000)
        bring_front(self)
        self.input.setFocus()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def show_status(self):
        try:
            k = A_K().status()
        except Exception as e:
            self.stat.setText(f"Kho kiến thức chưa chạy: {e}")
            return
        bits = [f"📝 Đã đọc chữ {k['ocr_done']}/{k['images']} ảnh",
                f"🧭 Tìm theo nghĩa: {'bật' if k['semantic'] else 'tắt (cần Ollama + bge-m3)'}"]
        if k["images"] and not k["vietnamese_ocr"]:
            bits.append("⚠ chưa có gói đọc chữ tiếng Việt nên chữ trong ảnh có thể sai dấu")
        if k["busy"]:
            bits.append("⏳ " + k["busy"])
        self.stat.setText(" · ".join(bits))

    def ask(self, q=None):
        q = (q if isinstance(q, str) else self.input.text()).strip()
        if not q or self.busy:
            return
        self.input.clear()
        scope = self.scope.currentData()
        self.items.append({"q": q, "r": None})
        qid = len(self.items) - 1
        self.busy = True
        self.b_ask.setEnabled(False)
        self.render()

        def work():
            try:
                r = A_K().ask(q, None if scope in (None, "*") else scope)
            except Exception as e:
                S.log("hỏi kho kiến thức lỗi:", repr(e))
                r = {"q": q, "answer": "", "sources": [], "ai": False, "error": f"Lỗi: {e}", "took": 0}
            self.done.emit(qid, r)
        threading.Thread(target=work, daemon=True).start()

    def on_done(self, qid, r):
        if qid < len(self.items):
            self.items[qid]["r"] = r
        self.busy = False
        self.b_ask.setEnabled(True)
        self.render()
        self.show_status()

    def render(self):
        if not self.items:
            ex = ("Kiến thức về CoS (Chief of Staff)?", "Buổi chia sẻ anh Sơn nói gì về đội agent?", "Lịch Zoom BYS tháng 10 thế nào?")
            self.view.setHtml("<p style='color:%s'>Thử hỏi:</p>" % C["muted"] + "".join(
                f"<p><a href='ex:{html.escape(x)}' style='color:{C['blue']};text-decoration:none'>💬 {html.escape(x)}</a></p>" for x in ex))
            return
        h = []
        for qid, it in enumerate(self.items):
            h.append(f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:10px"><tr><td width="15%"></td>'
                     f'<td bgcolor="{C["accent_soft"]}" style="padding:8px">{html.escape(it["q"])}</td></tr></table>')
            r = it["r"]
            if not r:
                body = f"<span style='color:{C['muted']}'>⏳ Đang tìm trong ghi chú và hỏi AI trong máy… (khoảng 10 đến 40 giây)</span>"
            else:
                body = answer_html(r.get("answer"), qid)
                if r.get("error"):
                    body += f"<p style='color:{C['red']};margin:4px 0'>⚠ {html.escape(r['error'])}</p>"
                if r.get("sources"):
                    body += "<table width='100%' cellspacing='0' cellpadding='4' style='margin-top:6px'>"
                    for s in r["sources"]:
                        where = f" · 📷 chữ trong ảnh {s['idx'] + 1}/{s['images']}" if s.get("img") else ""
                        thumb = (f"<a href='img:{s['id']}:{s['idx']}'><img src='thumb:{html.escape(s['img'])}' width='100'></a>"
                                 if s.get("img") else "")
                        meta = ((f"📁 {html.escape(s['project'])} · " if s["project"] else "")
                                + f"{s['date'][8:10]}/{s['date'][5:7]} · {html.escape(s['status'])}{where}")
                        body += (f"<tr><td width='24' valign='top' style='color:{C['blue']};font-weight:700'>{s['n']}</td>"
                                 f"<td width='{106 if thumb else 1}' valign='top'>{thumb}</td><td valign='top'>"
                                 f"<a href='note:{s['id']}' style='color:{C['ink']};text-decoration:none'><b>{html.escape(s['title'])}</b></a><br>"
                                 f"<span style='color:{C['muted']};font-size:9pt'>{meta}</span><br>"
                                 f"<span style='font-size:9pt'>{html.escape(s['snippet'])}</span></td></tr>")
                    body += "</table>"
                body += (f"<p style='color:{C['muted']};font-size:8pt;margin:4px 0 0'>"
                         f"{'🤖 AI trong máy' if r.get('ai') else '🔎 Tìm theo từ khoá'} · {r.get('took', 0)}s</p>")
            h.append(f'<table width="100%" cellspacing="0" cellpadding="0" style="margin-top:6px"><tr>'
                     f'<td bgcolor="{C["panel"]}" style="padding:10px;border:1px solid {C["line"]}">{body}</td>'
                     f'<td width="4%"></td></tr></table>')
        self.view.setHtml("".join(h))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def source(self, qid, n):
        r = self.items[qid]["r"] if qid < len(self.items) else None
        return next((s for s in (r or {}).get("sources", []) if s["n"] == n), None)

    def open_link(self, url):
        s = url.toString()
        if s.startswith("ex:"):
            self.ask(s[3:])
        elif s.startswith("note:"):
            self.win.show_main()
            self.win.jump_to(s[5:])
        elif s.startswith("img:"):
            _, nid, idx = s.split(":")
            n = self.win.by_id.get(nid) or find_live(nid)
            if n and n.get("images"):
                GalleryViewer(n["images"], int(idx), note_title(n), self).exec()
        elif s.startswith("src:"):
            _, qid, n = s.split(":")
            src = self.source(int(qid), int(n))
            if src and src.get("img"):
                self.open_link(QUrl(f"img:{src['id']}:{src['idx']}"))
            elif src:
                self.open_link(QUrl(f"note:{src['id']}"))

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ---------- bong bóng nổi ----------
class Bubble(QWidget):
    def __init__(self, win):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.win = win
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAcceptDrops(True)
        self.setFixedSize(80, 80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Idea Note — bấm để mở, kéo để di chuyển\n🎙 Bấm nút mic (góc trái dưới) để nói ghi chú / giao việc\n"
                        "Kéo ảnh / chữ thả vào đây để lưu nhanh")
        self.mic_rect = QRect(0, 50, 28, 28)
        self.vstate, self.vlevel = "idle", 0.0
        self.icon = QPixmap(os.path.join(HOME, "icon_round.png")).scaled(
            62, 62, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.count = 0
        self.hot = False
        self.press = None
        conf = win.conf.get("bubble") or {}
        scr = QApplication.primaryScreen().availableGeometry()
        x, y = conf.get("x", scr.right() - 110), conf.get("y", scr.bottom() - 200)
        if not any(s.availableGeometry().contains(QPoint(x + 30, y + 30)) for s in QApplication.screens()):
            x, y = scr.right() - 110, scr.bottom() - 200
        self.move(x, y)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 40))
        p.drawEllipse(QRect(6, 12, 62, 62))
        p.drawPixmap(4, 9, self.icon)
        if self.vstate != "idle":  # đang nghe: viền đỏ nhấp nháy theo giọng · đang xử lý: viền xanh
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(C["red"] if self.vstate == "listen" else C["blue"]),
                          3 + (self.vlevel * 5 if self.vstate == "listen" else 0)))
            p.drawEllipse(QRect(5, 10, 60, 60))
        # nút mic nhỏ góc trái dưới
        p.setBrush(QColor(C["red"] if self.vstate == "listen" else C["blue"] if self.vstate != "idle" else "white"))
        p.setPen(QPen(QColor(C["line"] if self.vstate == "idle" else "white"), 1.5))
        p.drawEllipse(self.mic_rect.adjusted(1, 1, -1, -1))
        p.setFont(QFont("Segoe UI Emoji", 10))
        p.setPen(QColor("white" if self.vstate != "idle" else C["ink"]))
        p.drawText(self.mic_rect, Qt.AlignmentFlag.AlignCenter, "⏹" if self.vstate == "listen" else "🎙")
        p.setPen(Qt.PenStyle.NoPen)
        if self.hot:  # đang kéo thả vào: viền sáng
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#ffb020"), 3))
            p.drawEllipse(QRect(5, 10, 60, 60))
            p.setPen(Qt.PenStyle.NoPen)
        if self.count:
            p.setBrush(QColor(C["red"]))
            p.setPen(QPen(QColor("white"), 2))
            p.drawEllipse(QRect(46, 1, 26, 26))
            f = QFont("Segoe UI", 9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRect(46, 1, 26, 26), Qt.AlignmentFlag.AlignCenter, str(self.count) if self.count < 100 else "99+")

    def set_count(self, c):
        if c != self.count:
            self.count = c
            self.update()

    def set_voice(self, state=None, level=None):
        if state is not None:
            self.vstate = state
        if level is not None:
            self.vlevel = level
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.mic_rect.contains(e.position().toPoint()):
                self.press = None
                self.win.voice_bubble()
                return
            self.press = (e.globalPosition().toPoint(), self.pos(), False)

    def mouseMoveEvent(self, e):
        if self.press:
            start, pos, moved = self.press
            d = e.globalPosition().toPoint() - start
            if moved or d.manhattanLength() > 4:
                self.press = (start, pos, True)
                self.move(pos + d)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.press:
            moved = self.press[2]
            self.press = None
            if moved:
                self.win.conf["bubble"] = {"x": self.x(), "y": self.y()}
                save_app_conf(self.win.conf)
            else:
                self.win.show_main()

    def contextMenuEvent(self, e):
        self.win.tray_menu.exec(e.globalPos())

    def dragEnterEvent(self, e):
        md = e.mimeData()
        if md.hasUrls() or md.hasImage() or md.hasText():
            self.hot = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.hot = False
        self.update()

    def dropEvent(self, e):
        self.hot = False
        self.update()
        md = e.mimeData()
        imgs = images_from_mime(md)
        text = "" if md.hasUrls() and imgs else (md.text() or "").strip()
        if not imgs and not text:
            return
        names = [qimage_to_file(i) for i in imgs]
        S.new_note({"text": text, "image_files": names})
        self.win.flash(f"Đã lưu vào Idea Note ({len(names)} ảnh)" if names else "Đã lưu vào Idea Note")
        self.win.refresh(force=True)


# ---------- cửa sổ nhắc hẹn ----------
class ReminderPopup(QWidget):
    def __init__(self, win, n):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.win, self.nid = win, n["id"]
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("popup")
        self.setStyleSheet(f"""#popup{{background:{C['panel']};border:2px solid {C['red']};border-radius:14px}}
            QLabel#pt{{color:{C['red']};font-size:11pt;font-weight:700}}""")
        t = QLabel(("🔴 GẤP · " if n.get("urgent") else "") + "⏰ Đến giờ " + n["remind_at"][11:16])
        t.setObjectName("pt")
        body = QLabel((n.get("text") or "(ghi chú có ảnh)").strip()[:600])
        body.setWordWrap(True)
        body.setMaximumWidth(420)
        row = QHBoxLayout()
        for text, fn, primary in (("✅ Xong việc", self.done, True), ("⏰ Nhắc lại 10 phút", self.snooze, False),
                                  ("📝 Mở", self.open, False), ("Đã biết", self.close, False)):
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(fn)
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.addWidget(t)
        lay.addWidget(body)
        lay.addLayout(row)
        self.adjustSize()
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.top() + 24 + 20 * len(win.popups))
        winsound.PlaySound("SystemExclamation", winsound.SND_ALIAS | winsound.SND_ASYNC)

    def done(self):
        n = find_live(self.nid)
        update_note(self.nid, {"status": S.finish_status(n) if n else "done"})
        self.win.refresh(force=True)
        self.close()

    def snooze(self):
        t = (dt.datetime.now() + dt.timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M")
        update_note(self.nid, {"remind_at": t})
        self.win.refresh(force=True)
        self.close()

    def open(self):
        self.win.show_main()
        self.win.jump_to(self.nid)
        self.close()

    def closeEvent(self, e):
        if self in self.win.popups:
            self.win.popups.remove(self)
        super().closeEvent(e)


class Bridge(QObject):
    due = Signal(dict)
    sorted = Signal(str, str, str, str)  # id ghi chú, chữ gốc lúc bấm, chữ đã sắp 7 mục, lỗi


class HotkeyFilter(QAbstractNativeEventFilter):
    """Phím tắt toàn máy: 1 = Ctrl+Alt+N (mở để ghi), 2 = Ctrl+Alt+V (nói với trợ lý)."""

    def __init__(self, cbs):
        super().__init__()
        self.cbs = cbs

    def nativeEventFilter(self, event_type, message):
        try:
            msg = W.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam in self.cbs:
                # không mở cửa sổ ngay trong bộ lọc sự kiện của Windows — đẩy ra vòng lặp Qt
                QTimer.singleShot(0, self.cbs[msg.wParam])
                return True, 0
        except Exception:
            pass
        return False, 0


def bring_front(w):
    """Hiện cửa sổ và đưa lên trên cùng. Windows hay chặn giành tiêu điểm nên gắn tạm vào luồng đang ở trên cùng."""
    w.showNormal()
    w.raise_()
    w.activateWindow()
    try:
        u = ctypes.windll.user32
        fg = u.GetForegroundWindow()
        a, b = u.GetWindowThreadProcessId(fg, None), ctypes.windll.kernel32.GetCurrentThreadId()
        u.AttachThreadInput(a, b, True)
        u.ShowWindow(int(w.winId()), 5)
        u.SetForegroundWindow(int(w.winId()))
        u.AttachThreadInput(a, b, False)
    except Exception:
        pass


# ---------- trợ lý giọng nói ----------
class Voice(QObject):
    """Một bộ mic dùng chung cho: ô ghi nhanh (đọc chữ vào ô), bong bóng + phím tắt (trợ lý tự lưu / đặt nhắc)."""
    level = Signal(float, bool)
    state = Signal(str)          # listen / recognize / think / idle
    heard = Signal(str, str)     # (origin, chữ nghe được)
    answered = Signal(str, str, list)  # (origin, câu trả lời, thao tác)
    failed = Signal(str, str)    # (origin, lý do)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.brain = A.Brain()
        self.busy = self.recording = False
        self.origin = ""
        self.state.connect(self._on_state)

    def _on_state(self, s):
        if s == "idle":
            self.busy = self.recording = False

    def mic_index(self):
        name = self.win.conf.get("mic_name")
        if not name:
            return None
        try:
            return next((i for i, n in A.Ear.devices() if n.startswith(name)), None)
        except Exception:
            return None

    def toggle(self, origin):
        """origin: 'capture' = đọc chữ vào ô ghi nhanh · 'bubble' / 'panel' = trợ lý tự hiểu và làm."""
        if self.recording:
            self.win.ear.stop_flag.set()
            return
        if self.busy:
            return
        self.busy = self.recording = True
        self.origin = origin
        self.state.emit("listen")
        if origin == "panel":
            threading.Thread(target=A.Brain.warm, daemon=True).start()  # nạp AI song song lúc đang nói
        threading.Thread(target=self._job, daemon=True).start()

    def ask_text(self, origin, text):
        if self.busy or not text.strip():
            return
        self.busy, self.origin = True, origin
        threading.Thread(target=self._think, args=(text,), daemon=True).start()

    def _job(self):
        origin = self.origin
        try:
            self.win.ear.ensure()  # bộ nghe chưa nạp / đã nhả vì lâu không dùng -> nạp song song lúc đang nói
            pcm = self.win.ear.record(on_level=lambda lv, talk: self.level.emit(lv, talk), device=self.mic_index())
            self.recording = False
            st = self.win.ear.last_stats
            try:
                A.Ear.save_wav(pcm, os.path.join(S.DATA, "voice-last.wav"))
            except OSError:
                pass
            self.win.ear.ensure()
            if not self.win.ear.ready.is_set():
                self.state.emit("loading")
                self.win.ear.ready.wait()
            self.state.emit("recognize")
            hint = ", ".join(p["name"] for p in self.win.projects()[:8])
            text = self.win.ear.transcribe(pcm, hint=hint)
            S.log("mic nghe:", origin, self.win.conf.get("mic_name") or "mic mặc định", st, "->", repr(text[:120]))
            if not text:
                self.failed.emit(origin, f"Chưa nghe ra chữ nào (âm to nhất {st.get('max')}, ồn nền {st.get('floor')}). "
                                         "Nói gần mic hơn, hoặc đổi mic trong ⚙️ Cài đặt.")
                return
            self.heard.emit(origin, text)
            if origin == "bubble":  # ghi chú giọng nói: quy tắc tiếng Việt, lưu tức thì không chờ AI
                reply, acts = V.quick(text)
                self.answered.emit(origin, reply, acts)
            elif origin != "capture":
                self._think(text, emit_idle=False)
        except Exception as e:
            S.log("mic lỗi:", repr(e))
            self.failed.emit(origin, f"Lỗi mic / nhận dạng: {e}. Thử đổi mic trong ⚙️ Cài đặt.")
        finally:
            self.state.emit("idle")

    def _think(self, text, emit_idle=True):
        origin = self.origin
        try:
            self.state.emit("think")
            reply, acts = self.brain.ask(text)
            self.answered.emit(origin, reply, acts)
        finally:
            if emit_idle:
                self.state.emit("idle")


def actions_summary(win, acts):
    """[(kiểu, id)] -> các dòng 'Đã tạo: …' để hiện cho người dùng, kèm id ghi chú đầu tiên."""
    lines, first = [], None
    for kind, nid in acts or []:
        if kind not in ("create", "update", "delete"):
            continue
        n = win.by_id.get(nid) or next((x for x in snapshot() if x["id"] == nid), None)
        if not n:
            continue
        first = first or nid
        verb = {"create": "✓ Đã lưu", "update": "✓ Đã cập nhật", "delete": "🗑 Đã xoá"}[kind]
        extra = (" · ⏰ " + fmt_remind(n["remind_at"])) if n.get("remind_at") else ""
        extra += " · 🔴 gấp" if is_urgent(n) else ""
        extra += f" · 📁 {n['project']}" if n.get("project") else ""
        lines.append(f"{verb}: {((n.get('text') or '').strip().split(chr(10)) or [''])[0][:60]}{extra}")
    return lines, first


class BubbleToast(QLabel):
    """Dòng thông báo nhỏ cạnh bong bóng: đang nghe / đã lưu gì. Bấm vào để mở ghi chú."""

    def __init__(self, win):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.win, self.nid = win, None
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWordWrap(True)
        self.setMaximumWidth(340)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"QLabel{{background:{C['panel']};border:1px solid {C['line']};border-radius:12px;"
                           f"padding:9px 12px;font-size:10pt;color:{C['ink']}}}")
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def say(self, text, nid=None, ms=6000):
        self.nid = nid
        self.setText(text)
        self.adjustSize()
        b = self.win.bubble
        scr = QApplication.screenAt(b.geometry().center()) or QApplication.primaryScreen()
        g = scr.availableGeometry()
        x = b.x() - self.width() - 6 if b.x() - self.width() - 6 > g.left() else b.x() + b.width() + 6
        y = min(max(g.top(), b.y() + 20), g.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()
        if ms:
            self.timer.start(ms)
        else:
            self.timer.stop()

    def mousePressEvent(self, e):
        self.hide()
        self.win.show_main()
        if self.nid:
            self.win.jump_to(self.nid)


class AssistantPanel(QWidget):
    """Cửa sổ trò chuyện (tuỳ chọn) — dùng chung bộ mic Voice với ô ghi nhanh và bong bóng."""

    def __init__(self, win):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("💬 Trợ lý Idea Note")
        self.setWindowIcon(QIcon(ICON))
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("assist")
        self.win, self.v = win, win.voice
        self.v.level.connect(self.on_level)
        self.v.state.connect(self.on_state)
        self.v.heard.connect(lambda o, t: self.add_msg("me", t))
        self.v.answered.connect(lambda o, r, a: self.add_msg("bot", r, a))
        self.v.failed.connect(lambda o, m: o != "capture" and self.add_msg("bot", m))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)
        tip = QLabel("Nói hoặc gõ để giao việc, đặt nhắc hẹn, hỏi lịch. Mọi thứ xử lý <b>trong máy</b>.")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        lay.addWidget(tip)
        self.chat = QTextBrowser()
        self.chat.setOpenLinks(False)
        self.chat.anchorClicked.connect(self.open_link)
        lay.addWidget(self.chat, 1)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        self.meter.setFixedHeight(6)
        lay.addWidget(self.meter)
        self.status = QLabel("Sẵn sàng")
        self.status.setObjectName("muted")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.mic = QPushButton("🎙")
        self.mic.setObjectName("mic")
        self.mic.clicked.connect(lambda: self.v.toggle("panel"))
        row.addWidget(self.mic)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Hoặc gõ: nhắc anh 3 giờ chiều mai gọi anh Tú…")
        self.input.returnPressed.connect(self.send_text)
        row.addWidget(self.input, 1)
        b = QPushButton("Gửi")
        b.setObjectName("primary")
        b.clicked.connect(self.send_text)
        row.addWidget(b)
        lay.addLayout(row)
        self.resize(440, 560)
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 24, scr.bottom() - self.height() - 60)
        self.add_msg("bot", "Chào anh! Ví dụ:<br>• “Nhắc anh 9 giờ sáng mai họp team, gấp”<br>"
                            "• “Ghi ý tưởng làm video gỗ óc chó cho dự án Kênh gỗ”<br>• “Hôm nay còn việc gì gấp?”", raw=True)

    def add_msg(self, who, text, actions=None, raw=False):
        body = text if raw else html.escape(text).replace("\n", "<br>")
        lines, _ = actions_summary(self.win, actions)
        ids = [nid for k, nid in actions or [] if k in ("create", "update", "delete")]
        if lines:
            body += "<br><span style='font-size:9pt'>" + "<br>".join(
                f'<a href="note:{nid}" style="color:{C["blue"]};text-decoration:none">{html.escape(t)}</a>'
                for t, nid in zip(lines, ids)) + "</span>"
        cell = (f'<td width="18%"></td><td bgcolor="{C["soft"]}" style="padding:8px">{body}</td>' if who == "me" else
                f'<td bgcolor="{C["accent_soft"]}" style="padding:8px">🤖 {body}</td><td width="18%"></td>')
        self.chat.append(f'<table width="100%" cellspacing="0" cellpadding="0"><tr>{cell}</tr></table>')
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def open_link(self, url):
        s = url.toString()
        if s.startswith("note:"):
            self.win.show_main()
            self.win.jump_to(s[5:])

    def send_text(self):
        text = self.input.text().strip()
        if text and not self.v.busy:
            self.input.clear()
            self.add_msg("me", text)
            self.v.ask_text("panel", text)

    def on_level(self, lv, talking):
        self.meter.setValue(int(lv * 100))
        self.meter.setProperty("talk", talking)
        self.meter.style().polish(self.meter)

    def on_state(self, s):
        self.status.setText({"listen": "🎙 Đang nghe… nói xong ngừng 1 giây", "recognize": "⏳ Đang nhận dạng…",
                             "loading": "⏳ Đang nạp bộ nghe (lần đầu, khoảng 1 phút)…",
                             "think": "🤖 Đang xử lý…", "idle": "Sẵn sàng"}.get(s, s))
        self.mic.setText("⏹" if s == "listen" else "🎙")
        self.mic.setProperty("rec", s == "listen")
        self.mic.style().polish(self.mic)
        if s != "listen":
            self.meter.setValue(0)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ---------- cửa sổ chính ----------
class Main(QWidget):
    def __init__(self):
        super().__init__()
        self.conf = load_app_conf()
        self.filter, self.proj, self.day, self.q = "overview", None, None, ""
        self.done_scope = None  # tab Hoàn thành đang xem riêng 1 dự án
        self.notes, self.by_id, self.order = [], {}, []
        self.sel = None
        self.rev = -1
        self.pend = []           # ảnh chờ lưu ở ô ghi nhanh
        self.kind = "note"
        self.remind = None       # giờ nhắc của ô ghi nhanh
        self.loading = False
        self.detail_loaded = None
        self.popups = []
        self.ear = A.Ear()
        self.assist = None
        self.setWindowTitle("Idea Note")
        self.setWindowIcon(QIcon(ICON))
        self.build()
        self.build_tray()
        self.bubble = Bubble(self)
        self.toast = BubbleToast(self)
        self.voice = Voice(self)
        self.voice.level.connect(self.on_voice_level)
        self.voice.state.connect(self.on_voice_state)
        self.voice.heard.connect(self.on_voice_heard)
        self.voice.answered.connect(self.on_voice_answer)
        self.voice.failed.connect(self.on_voice_fail)
        self.bridge = Bridge()
        self.bridge.due.connect(self.show_reminder)
        self.bridge.sorted.connect(self.on_spec7_sorted)
        S.ON_DUE = lambda n: self.bridge.due.emit(dict(n))
        S.ON_VOICE = self.voice_from_phone
        g = self.conf.get("geom")
        if g:
            self.setGeometry(*g)
        else:
            self.resize(1320, 820)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1200)
        self.minute = QTimer(self)
        self.minute.timeout.connect(lambda: self.refresh(force=True))
        self.minute.start(60000)
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_text)
        self.refresh(force=True)
        # bộ nghe (~1,5 GB) KHÔNG nạp sẵn nữa: bấm nói mới nạp, 15 phút không dùng thì tự nhả (assistant.EAR_IDLE_S)

    # ----- dựng giao diện -----
    def build(self):
        QApplication.instance().setStyleSheet(STYLE)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        split = QSplitter()
        split.setHandleWidth(1)
        self.split = split
        split.splitterMoved.connect(lambda *a: setattr(self, "split_user", True))  # tự kéo thì giữ nguyên
        root.addWidget(split)

        # cột trái
        side = QFrame()
        side.setObjectName("side")
        self.side_frame = side
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 12, 10, 10)
        sl.setSpacing(8)
        logo = QHBoxLayout()
        li = QLabel()
        li.setPixmap(rounded_icon(28))
        lt = QLabel("Idea Note")
        lt.setObjectName("logo")
        logo.addWidget(li)
        logo.addWidget(lt)
        logo.addStretch()
        sl.addLayout(logo)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍  Tìm ghi chú…  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.on_search)
        sl.addWidget(self.search)
        self.side = QListWidget()
        self.side.setObjectName("sidelist")
        self.side.setItemDelegate(SideDelegate())
        self.side.setMouseTracking(True)
        self.side.itemClicked.connect(self.on_side)
        sl.addWidget(self.side, 1)
        self.cal = QCalendarWidget()
        self.cal.setGridVisible(False)
        self.cal.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.cal.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.cal.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.cal.setLocale(QLocale(QLocale.Language.Vietnamese, QLocale.Country.Vietnam))
        self.cal.setFixedHeight(222)
        self.cal.clicked.connect(self.on_day)
        self.cal.currentPageChanged.connect(lambda *a: self.mark_calendar())
        sl.addWidget(self.cal)
        bot = QHBoxLayout()
        for text, fn, tip in (("📱 Điện thoại", self.phone_dialog, "Mở Idea Note trên điện thoại"),
                              ("⚙️", self.settings_dialog, "Cài đặt")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            bot.addWidget(b)
        sl.addLayout(bot)
        split.addWidget(side)

        # cột giữa
        mid = QWidget()
        self.mid = mid
        mid.installEventFilter(self)
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(12, 12, 12, 12)
        ml.setSpacing(10)
        cap = QFrame()
        cap.setObjectName("card")
        cl = QVBoxLayout(cap)
        cl.setContentsMargins(12, 8, 12, 10)
        cl.setSpacing(6)
        self.ctitle = QLineEdit()
        self.ctitle.setObjectName("ctitle")
        self.ctitle.setPlaceholderText("📌 Mục chính (không bắt buộc — để trống sẽ lấy dòng đầu)")
        self.ctitle.returnPressed.connect(lambda: self.txt.setFocus())
        cl.addWidget(self.ctitle)
        self.txt = PasteEdit()
        self.txt.setObjectName("capture")
        self.txt.setPlaceholderText("Ghi nhanh ý tưởng, việc cần làm, link…   (Ctrl+V dán ảnh · kéo thả ảnh · Ctrl+Enter lưu)")
        self.txt.setFixedHeight(46)
        self.txt.imagesPasted.connect(self.add_pending)
        self.txt.submit.connect(self.create)
        self.txt.focused.connect(self.grow_capture)
        self.txt.textChanged.connect(lambda: self.grow_capture(self.txt.hasFocus()))
        cl.addWidget(self.txt)
        self.pend_box = QListWidget()
        self.pend_box.setViewMode(QListWidget.ViewMode.IconMode)
        self.pend_box.setIconSize(QSize(64, 64))
        self.pend_box.setFixedHeight(84)
        self.pend_box.setFlow(QListWidget.Flow.LeftToRight)
        self.pend_box.setToolTip("Chuột phải để bỏ ảnh")
        self.pend_box.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pend_box.customContextMenuRequested.connect(self.pend_menu)
        self.pend_box.hide()
        cl.addWidget(self.pend_box)
        self.vrow = QWidget()  # dòng trạng thái mic (chỉ hiện khi đang nói)
        vr = QHBoxLayout(self.vrow)
        vr.setContentsMargins(0, 0, 0, 0)
        self.vmeter = QProgressBar()
        self.vmeter.setRange(0, 100)
        self.vmeter.setTextVisible(False)
        self.vmeter.setFixedSize(120, 6)
        self.vlabel = QLabel()
        self.vlabel.setObjectName("muted")
        vr.addWidget(self.vmeter)
        vr.addWidget(self.vlabel, 1)
        self.vrow.hide()
        cl.addWidget(self.vrow)
        tbw, tb = flow_widget(6)  # hàng nút tự xuống dòng khi cửa sổ hẹp
        self.b_mic = QPushButton("🎙 Nói")
        self.b_mic.setObjectName("micsmall")
        self.b_mic.setToolTip("Bấm rồi nói: chữ hiện vào ô ghi chú (sửa lại được) · bấm lần nữa để dừng · Ctrl+Alt+V")
        self.b_mic.clicked.connect(lambda: self.voice.toggle("capture"))
        tb.addWidget(self.b_mic)
        b_img = QPushButton("📷 Ảnh")
        b_img.clicked.connect(self.pick_images)
        tb.addWidget(b_img)
        self.kind_grp = QButtonGroup(self)
        for k, text in (("note", "💡 Ghi chú"), ("todo", "☐ Việc cần làm")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setObjectName("seg")
            b.setChecked(k == "note")
            b.clicked.connect(lambda _=False, k=k: setattr(self, "kind", k))
            self.kind_grp.addButton(b)
            tb.addWidget(b)
        b_spec_cap = QPushButton("📐 7 mục")
        b_spec_cap.setToolTip("Điền khung spec 1 trang (7 mục) vào ô ghi nhanh.\nĐã gõ 1 dòng tên thì dòng đó thành tiêu đề.")
        b_spec_cap.clicked.connect(self.capture_spec7)
        tb.addWidget(b_spec_cap)
        self.cproj = QComboBox()
        self.cproj.setEditable(True)
        self.cproj.lineEdit().setPlaceholderText("📁 Dự án…")
        self.cproj.setMinimumWidth(120)
        self.cproj.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.cproj.setMinimumContentsLength(12)
        self.cproj.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        tb.addWidget(self.cproj)
        self.b_urg = QPushButton("🔴 Gấp")
        self.b_urg.setCheckable(True)
        self.b_urg.setObjectName("urg")
        tb.addWidget(self.b_urg)
        self.b_rem = QToolButton()
        self.b_rem.setText("⏰ Nhắc hẹn")
        self.b_rem.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        rm = QMenu(self)
        for text, fn in (("+30 phút", lambda: self.set_remind(minutes=30)), ("+1 giờ", lambda: self.set_remind(minutes=60)),
                         ("17:00 hôm nay", lambda: self.set_remind(at=(0, 17))), ("20:00 tối nay", lambda: self.set_remind(at=(0, 20))),
                         ("8:00 sáng mai", lambda: self.set_remind(at=(1, 8))), ("Chọn giờ…", self.pick_remind),
                         ("Bỏ nhắc", lambda: self.set_remind(clear=True))):
            rm.addAction(text, fn)
        rm.addSeparator()
        self.rep_act = {}
        for key, label in (("weekly", "🔁 Lặp lại hàng tuần"), ("daily", "🔁 Lặp lại hàng ngày")):
            a = QAction(label, rm, checkable=True)
            a.toggled.connect(lambda on, k=key: self.set_repeat(k if on else ""))
            rm.addAction(a)
            self.rep_act[key] = a
        self.repeat = ""
        self.b_rem.setMenu(rm)
        tb.addWidget(self.b_rem)
        self.b_save = QPushButton("Lưu")
        self.b_save.setObjectName("primary")
        self.b_save.clicked.connect(self.create)
        brow = QHBoxLayout()
        brow.addWidget(tbw, 1)
        brow.addWidget(self.b_save, 0, Qt.AlignmentFlag.AlignBottom)
        cl.addLayout(brow)
        ml.addWidget(cap)

        head = QHBoxLayout()
        self.b_side = QPushButton("☰")
        self.b_side.setToolTip("Ẩn / hiện cột trái (bộ lọc, dự án, lịch)")
        self.b_side.setFixedWidth(34)
        self.b_side.clicked.connect(self.toggle_side)
        head.addWidget(self.b_side, 0, Qt.AlignmentFlag.AlignTop)
        self.title = QLabel()
        self.title.setObjectName("title")
        self.title.setMinimumWidth(60)
        self.stats = QLabel()
        self.stats.setObjectName("muted")
        self.stats.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # hẹp thì cắt bớt, không giữ cửa sổ to
        self.stats.setMinimumWidth(1)
        head.addWidget(self.title, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(self.stats, 1, Qt.AlignmentFlag.AlignTop)
        hbw, hb = flow_widget(6)
        head.addWidget(hbw, 0)
        self.b_done = QPushButton("✅ Đã xong")
        self.b_done.setToolTip("Xem các việc đã hoàn thành của dự án này")
        self.b_done.clicked.connect(self.show_project_done)
        hb.addWidget(self.b_done)
        self.b_rename = QPushButton("✏️ Đổi tên")
        self.b_rename.clicked.connect(self.rename_project)
        hb.addWidget(self.b_rename)
        b_exp = QPushButton("📦 Xuất gói")
        b_exp.setToolTip("Tạo thư mục CONTEXT.md + ảnh để kéo thả vào claude.ai")
        b_exp.clicked.connect(self.export)
        hb.addWidget(b_exp)
        b_kb = QPushButton("🧠 Hỏi kiến thức")
        b_kb.setObjectName("kb")
        b_kb.setToolTip("Hỏi AI về mọi thứ đã lưu (cả chữ trong ảnh), trả lời kèm ghi chú nguồn · Ctrl+K")
        b_kb.clicked.connect(self.open_kb)
        hb.addWidget(b_kb)
        b_voice = QPushButton("💬 Trợ lý")
        b_voice.setObjectName("voice")
        b_voice.setToolTip("Trò chuyện với trợ lý: giao việc, đặt nhắc, hỏi lịch (nói hoặc gõ)")
        b_voice.clicked.connect(self.open_assistant)
        hb.addWidget(b_voice)
        b_claude = QPushButton("📋 Chép cho Claude")
        b_claude.setObjectName("claude")
        b_claude.setToolTip("Chép toàn bộ danh sách đang xem (chữ + đường dẫn ảnh) — dán vào Claude Code là đủ ngữ cảnh\nCtrl+Shift+C")
        b_claude.clicked.connect(self.copy_claude)
        hb.addWidget(b_claude)
        ml.addLayout(head)

        self.list = NoteList()
        self.list.setObjectName("notes")
        self.list.setItemDelegate(NoteDelegate(self))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self.on_select)
        self.list.toggled.connect(self.toggle_done)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.list_menu)
        ml.addWidget(self.list, 1)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ml.addWidget(self.empty)
        split.addWidget(mid)

        # cột phải: chi tiết, sửa trực tiếp
        det = QFrame()
        det.setObjectName("detail")
        dl = QVBoxLayout(det)
        dl.setContentsMargins(14, 14, 14, 14)
        dl.setSpacing(8)
        r1 = QHBoxLayout()
        self.d_status = QComboBox()
        for k, v in STATUS.items():
            self.d_status.addItem(v, k)
        self.d_status.currentIndexChanged.connect(lambda: self.save_field(status=self.d_status.currentData()))
        r1.addWidget(self.d_status)
        self.d_urg = QPushButton("🔴 Gấp")
        self.d_urg.setCheckable(True)
        self.d_urg.setObjectName("urg")
        self.d_urg.clicked.connect(lambda c: self.save_field(urgent=c))
        r1.addWidget(self.d_urg)
        self.d_proj = QComboBox()
        self.d_proj.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.d_proj.setMinimumContentsLength(8)
        self.d_proj.setEditable(True)
        self.d_proj.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.d_proj.lineEdit().setPlaceholderText("📁 Dự án…")
        self.d_proj.lineEdit().editingFinished.connect(lambda: self.save_field(project=self.d_proj.currentText().strip()))
        self.d_proj.activated.connect(lambda: self.save_field(project=self.d_proj.currentText().strip()))
        r1.addWidget(self.d_proj, 1)
        dl.addLayout(r1)
        self.d_meta = QLabel()
        self.d_meta.setObjectName("muted")
        dl.addWidget(self.d_meta)
        self.d_title = QLineEdit()
        self.d_title.setObjectName("dtitle")
        self.d_title.setToolTip("Mục chính của ghi chú — hiện in đậm trên danh sách. Để trống thì dùng dòng đầu của nội dung.")
        self.d_title.editingFinished.connect(self.save_title)
        dl.addWidget(self.d_title)
        # ghi chú có khung 7 mục: chấm tự động + tạo việc từ mục 6
        self.spec_row = QWidget()
        sr = QHBoxLayout(self.spec_row)
        sr.setContentsMargins(0, 0, 0, 0)
        self.d_spec = QLabel()
        self.d_spec.setObjectName("specbar")
        self.d_spec.setWordWrap(True)
        sr.addWidget(self.d_spec, 1)
        self.b_spec_tasks = QPushButton("➕ Tạo việc từ mục 6")
        self.b_spec_tasks.setToolTip("Mỗi dòng [ ] Làm ở mục 6 thành 1 việc riêng (cùng dự án, có dòng Verify).\n"
                                     "Ngày dd/mm ở đầu dòng thành ngày của việc. Bấm lại không tạo trùng.")
        self.b_spec_tasks.clicked.connect(self.spec_to_tasks)
        sr.addWidget(self.b_spec_tasks, 0, Qt.AlignmentFlag.AlignTop)
        dl.addWidget(self.spec_row)
        self.d_text = PasteEdit()
        self.d_text.setObjectName("dtext")
        self.d_text.setPlaceholderText("Nội dung ghi chú… (tự lưu)")
        self.d_text.textChanged.connect(lambda: None if self.loading else self.save_timer.start(500))
        self.d_text.textChanged.connect(lambda: self.update_spec_bar(self.d_text.toPlainText()))
        self.d_text.imagesPasted.connect(self.add_detail_images)
        dl.addWidget(self.d_text, 3)
        self.d_imghead = QLabel()
        self.d_imghead.setObjectName("muted")
        self.d_imghead.setWordWrap(True)
        dl.addWidget(self.d_imghead)
        self.d_imgs = QListWidget()
        self.d_imgs.setViewMode(QListWidget.ViewMode.IconMode)
        self.d_imgs.setIconSize(QSize(104, 104))
        self.d_imgs.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.d_imgs.setMovement(QListWidget.Movement.Static)
        self.d_imgs.setSpacing(4)
        self.d_imgs.setMinimumHeight(128)
        self.d_imgs.itemClicked.connect(lambda it: self.view_images(self.d_imgs.row(it)))
        self.d_imgs.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.d_imgs.customContextMenuRequested.connect(self.img_menu)
        dl.addWidget(self.d_imgs, 2)
        r2w, r2 = flow_widget(6)
        b_add = QPushButton("📷 Thêm ảnh")
        b_add.clicked.connect(self.pick_detail_images)
        r2.addWidget(b_add)
        b_spec = QPushButton("📐 Khung 7 mục")
        b_spec.setToolTip("Chèn khung spec 1 trang lên đầu nội dung: Vấn đề · Chân dung người dùng · Cách hoạt động ·\n"
                          "KHÔNG làm · Dữ liệu · Định nghĩa hoàn thành · Dễ hỏng ở đâu. Nội dung cũ giữ nguyên bên dưới.")
        b_spec.clicked.connect(self.insert_spec7)
        r2.addWidget(b_spec)
        self.b_sort = QPushButton("🪄 Sắp vào 7 mục")
        self.b_sort.setToolTip("AI trong máy đọc ghi chú tự do và xếp từng ý vào đúng mục (không gửi ra ngoài).\n"
                               "Mục chưa nói tới ghi \"?\". Nội dung gốc giữ nguyên ở cuối. Mất khoảng 10 đến 30 giây.")
        self.b_sort.clicked.connect(self.sort_spec7)
        r2.addWidget(self.b_sort)
        self.d_date = QDateEdit()
        self.d_date.setToolTip("Ngày của ghi chú")
        self.d_date.setCalendarPopup(True)
        self.d_date.setDisplayFormat("dd/MM/yyyy")
        self.d_date.dateChanged.connect(lambda d: self.save_field(date=d.toString("yyyy-MM-dd")))
        r2.addWidget(self.d_date)
        dl.addWidget(r2w)
        r3w, r3 = flow_widget(6)
        self.d_rem_on = QCheckBox("⏰ Nhắc lúc")
        self.d_rem_on.clicked.connect(self.save_remind)
        r3.addWidget(self.d_rem_on)
        self.d_rem = QDateTimeEdit()
        self.d_rem.setCalendarPopup(True)
        self.d_rem.setDisplayFormat("HH:mm  dd/MM/yyyy")
        self.d_rem.dateTimeChanged.connect(lambda *_: self.d_rem_on.isChecked() and self.save_remind())
        r3.addWidget(self.d_rem)
        self.d_repeat = QComboBox()
        for label, key in (("Không lặp", ""), ("🔁 Hàng ngày", "daily"), ("🔁 Hàng tuần", "weekly")):
            self.d_repeat.addItem(label, key)
        self.d_repeat.setToolTip("Nhắc lặp lại: tick xong buổi này sẽ tự hẹn buổi sau")
        self.d_repeat.currentIndexChanged.connect(self.save_repeat)
        r3.addWidget(self.d_repeat)
        dl.addWidget(r3w)
        self.prog_row = QWidget()
        pr = QHBoxLayout(self.prog_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.addWidget(QLabel("Tiến độ"))
        self.d_prog = QSlider(Qt.Orientation.Horizontal)
        self.d_prog.setRange(0, 100)
        self.d_prog.setSingleStep(10)
        self.d_prog.setPageStep(10)
        self.d_prog_l = QLabel("0%")
        self.d_prog.valueChanged.connect(lambda v: self.d_prog_l.setText(f"{v}%"))
        self.d_prog.sliderReleased.connect(lambda: self.save_field(progress=round(self.d_prog.value() / 10) * 10))
        pr.addWidget(self.d_prog, 1)
        pr.addWidget(self.d_prog_l)
        dl.addWidget(self.prog_row)
        # tick xong chưa phải là xong: phải có bằng chứng rồi bấm Verify
        self.verify_box = QFrame()
        self.verify_box.setObjectName("verifybox")
        vl = QVBoxLayout(self.verify_box)
        vl.setContentsMargins(10, 8, 10, 8)
        vl.setSpacing(6)
        self.d_vlabel = QLabel()
        self.d_vlabel.setWordWrap(True)
        vl.addWidget(self.d_vlabel)
        self.d_proof = QLineEdit()
        self.d_proof.setPlaceholderText("🔎 Bằng chứng: link, số liệu, ai đã xác nhận… (hoặc thêm ảnh chụp)")
        self.d_proof.editingFinished.connect(self.save_proof)
        vl.addWidget(self.d_proof)
        vr = QHBoxLayout()
        self.b_verify = QPushButton("✔ Verify: đạt")
        self.b_verify.setObjectName("verify")
        self.b_verify.setToolTip("Đã thấy bằng chứng, việc làm được thật → chuyển sang Hoàn thành")
        self.b_verify.clicked.connect(self.verify_current)
        vr.addWidget(self.b_verify)
        self.b_redo = QPushButton("↩ Chưa đạt, làm lại")
        self.b_redo.setToolTip("Bằng chứng chưa đủ → đưa về Đang làm")
        self.b_redo.clicked.connect(lambda: (self.save_field(status="doing"), self.flash("↩ Đã đưa về Đang làm")))
        vr.addWidget(self.b_redo)
        vr.addStretch()
        vl.addLayout(vr)
        dl.addWidget(self.verify_box)
        self.b_copy = QPushButton("📋 Chép")
        self.b_copy.setObjectName("primary")
        self.b_copy.setToolTip("Chép chữ + đường dẫn ảnh — dán vào Claude / Zalo")
        self.b_copy.clicked.connect(self.copy_note)
        dl.addWidget(self.b_copy)
        acts = QHBoxLayout()
        acts.setSpacing(4)
        for text, tip, fn in (("📅 Lịch", "Thêm vào Google Lịch — điện thoại tự nhắc", lambda: self.cur() and webbrowser.open(gcal_url(self.cur()))),
                              ("✉️ Gmail", "Soạn email với nội dung này", lambda: self.cur() and webbrowser.open(gmail_url(self.cur())))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch()
        self.b_restore = QPushButton("♻️ Khôi phục")
        self.b_restore.clicked.connect(lambda: self.save_field(deleted=False))
        acts.addWidget(self.b_restore)
        self.b_del = QPushButton("🗑 Xoá")
        self.b_del.setObjectName("danger")
        self.b_del.clicked.connect(self.delete_current)
        acts.addWidget(self.b_del)
        dl.addLayout(acts)
        self.d_empty = QLabel("Chọn một ghi chú ở giữa để xem và sửa đầy đủ ở đây.\n\n↑ ↓ để chuyển nhanh · Delete để xoá")
        self.d_empty.setObjectName("muted")
        self.d_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.d_empty.setWordWrap(True)
        dl.addWidget(self.d_empty)
        self.detail_widgets = [w for w in det.findChildren(QWidget) if w is not self.d_empty and w.parent() is det]
        self.dl = dl
        # khung chi tiết cuộn được: cửa sổ thấp vẫn thu nhỏ được, không bị các hàng nút giữ lại
        self.det_scroll = QScrollArea()
        self.det_scroll.setObjectName("detscroll")
        self.det_scroll.setWidget(det)
        self.det_scroll.setWidgetResizable(True)
        self.det_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.det_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.det_scroll.setMinimumWidth(300)
        det.setMinimumWidth(280)
        split.addWidget(self.det_scroll)
        split.setSizes([230, 640, 450])
        split.setStretchFactor(1, 1)
        split.setChildrenCollapsible(False)

        def key(seq, parent, fn, widget_only=False):
            sc = QShortcut(QKeySequence(seq), parent)
            if widget_only:
                sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(fn)

        # cột giữa hẹp -> nút chỉ còn biểu tượng (chữ đầy đủ nằm ở tooltip) để danh sách còn chỗ
        self.compact_btns = []
        for b in [self.b_mic, b_img, *self.kind_grp.buttons(), b_spec_cap, self.b_urg, self.b_rem, b_exp, b_kb, b_voice, b_claude]:
            full = b.text()
            b.setToolTip(b.toolTip() or full)
            self.compact_btns.append((b, full, full.split(" ")[0]))
        key("Ctrl+F", self, lambda: (self.search.setFocus(), self.search.selectAll()))
        key("Ctrl+N", self, self.txt.setFocus)
        key("Ctrl+Shift+C", self, self.copy_claude)
        key("Ctrl+K", self, self.open_kb)
        key("Delete", self.list, self.delete_current, True)
        key("Escape", self.search, self.search.clear, True)

    def build_tray(self):
        self.tray = QSystemTrayIcon(QIcon(ICON), self)
        self.tray.setToolTip("Idea Note")
        m = QMenu()
        m.addAction("📝  Mở Idea Note", self.show_main)
        m.addAction("✍️  Ghi nhanh  (Ctrl+Alt+N)", self.quick)
        m.addAction("🎙  Nói ghi chú / giao việc  (Ctrl+Alt+V)", self.voice_bubble)
        m.addAction("💬  Trò chuyện với trợ lý", self.open_assistant)
        m.addAction("🧠  Hỏi kho kiến thức  (Ctrl+K)", self.open_kb)
        self.always = QAction("📌  Luôn hiện bong bóng", m, checkable=True)
        self.always.setChecked(bool(self.conf.get("always_bubble")))
        self.always.toggled.connect(self.on_always)
        m.addAction(self.always)
        self.hide_bubble = QAction("🫧  Tắt bong bóng (thu nhỏ sẽ không hiện icon nổi)", m, checkable=True)
        self.hide_bubble.setChecked(bool(self.conf.get("no_bubble")))
        self.hide_bubble.toggled.connect(self.on_no_bubble)
        m.addAction(self.hide_bubble)
        m.addAction("📱  Mở trên điện thoại", self.phone_dialog)
        m.addSeparator()
        m.addAction("⏻  Thoát hẳn (tắt cả nhắc hẹn)", self.quit_all)
        self.tray_menu = m
        self.tray.setContextMenu(m)
        self.tray.activated.connect(lambda r: self.show_main() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    # ----- dữ liệu -> giao diện -----
    def refresh(self, force=False):
        if not force and S.REV[0] == self.rev:
            return
        self.rev = S.REV[0]
        self.notes = snapshot()
        self.by_id = {n["id"]: n for n in self.notes}
        if not self.isVisible():  # đang ẩn xuống khay: chỉ cập nhật số trên bong bóng, mở ra mới vẽ lại danh sách
            self._stale = True
            self.bubble.set_count(sum(1 for n in self.notes if not n.get("deleted") and (is_urgent(n) or overdue(n))))
            return
        self._stale = False
        self.render_side()
        self.render_list()
        self.mark_calendar()
        self.bubble.set_count(sum(1 for n in self.notes if not n.get("deleted") and (is_urgent(n) or overdue(n))))
        projs = self.projects()
        for combo in (self.cproj, self.d_proj):
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItems([p["name"] for p in projs])
            combo.setEditText(cur)
            combo.blockSignals(False)
        self.fill_detail()

    def projects(self):
        m = {}
        for n in self.notes:
            if n.get("deleted") or not n.get("project"):
                continue
            p = m.setdefault(n["project"], {"name": n["project"], "c": 0, "last": ""})
            p["c"] += n["status"] != "done"
            p["last"] = max(p["last"], n.get("updated") or n.get("created") or "")
        return sorted(m.values(), key=lambda p: p["last"], reverse=True)

    def render_side(self):
        live = [n for n in self.notes if not n.get("deleted")]
        self.side.blockSignals(True)
        self.side.clear()

        def add(label, key, count, kind="f"):
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, key)
            it.setData(Qt.ItemDataRole.UserRole + 1, kind)
            it.setData(Qt.ItemDataRole.UserRole + 2, count)
            if kind == "h":
                it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.side.addItem(it)
            return it

        cur = None
        open_ = [n for n in live if n["status"] != "done"]  # việc đã xong chỉ đếm ở tab Hoàn thành
        for key, icon, label, fn in FILTERS:
            pool = [n for n in self.notes if n.get("deleted")] if key == "trash" else live if key in ("done", "overview") else open_
            it = add(f"{icon}  {label}", key, len(pool) if key == "trash" else len([n for n in pool if fn(n)]))
            if self.filter == key and not self.q and (key != "done" or self.done_scope is None):
                cur = it
        add("DỰ ÁN", None, 0, "h")
        projs = self.projects()
        if not projs:
            hint = add("  Gõ tên ở ô “📁 Dự án” khi ghi", None, 0, "h")
            hint.setData(Qt.ItemDataRole.UserRole + 1, "h")
        for p in projs:
            it = add(f"📁  {p['name']}", "proj:" + p["name"], p["c"], "p")
            if self.filter == "proj" and self.proj == p["name"] and not self.q:
                cur = it
        none = len([n for n in open_ if not n.get("project")])
        if projs and none:
            it = add("·  Chưa gắn dự án", "proj:", none, "p")
            if self.filter == "proj" and self.proj == "" and not self.q:
                cur = it
        if cur:
            self.side.setCurrentItem(cur)
        self.side.blockSignals(False)

    def visible(self):
        trash = self.filter == "trash"
        arr = [n for n in self.notes if bool(n.get("deleted")) == trash]
        if self.q:  # tìm kiếm thì tìm cả việc đã xong
            q = fold(self.q)
            return [n for n in arr if q in fold(" ".join((n.get(k) or "") for k in ("title", "text", "project")))
                    or q in fold(A_K().note_ocr(n))]  # tìm cả chữ máy đọc được trong ảnh
        if self.filter == "done":  # tab Hoàn thành (có thể chỉ của 1 dự án)
            return [n for n in arr if n["status"] == "done" and (self.done_scope is None or (n.get("project") or "") == self.done_scope)]
        if self.filter == "overview":  # tổng quan: toàn bộ, kể cả việc đã xong
            return arr
        if not trash:  # việc đã xong chỉ nằm ở tab Hoàn thành
            arr = [n for n in arr if n["status"] != "done"]
        if self.filter == "proj":
            return [n for n in arr if (n.get("project") or "") == self.proj]
        if self.filter == "day":
            return [n for n in arr if n["date"] == self.day or (n.get("remind_at") or "")[:10] == self.day]
        fn = next((f[3] for f in FILTERS if f[0] == self.filter), None)
        return [n for n in arr if fn(n)] if fn else arr

    def view_title(self):
        if self.q:
            return f"Tìm “{self.q}”"
        if self.filter == "done" and self.done_scope is not None:
            return "✅ Đã xong · 📁 " + (self.done_scope or "Chưa gắn dự án")
        if self.filter == "proj":
            return "📁 " + self.proj if self.proj else "Chưa gắn dự án"
        if self.filter == "day":
            return day_label(self.day)
        return next((f"{f[1]} {f[2]}" for f in FILTERS if f[0] == self.filter), "Idea Note")

    @staticmethod
    def section(n):
        """Nhóm ưu tiên: gấp -> chờ verify -> có hẹn giờ -> đang làm -> chưa làm -> ý tưởng -> đã xong (chỉ khi tìm kiếm)."""
        if n["status"] == "done":
            return 6, "✅ Đã xong"
        if n["status"] == "verify":
            return 1, "⏳ Chờ verify"
        if is_urgent(n):
            return 0, "🔴 Gấp"
        if n.get("remind_at"):
            return 2, "⏰ Có hẹn giờ"
        if n["status"] == "doing":
            return 3, "🔄 Đang làm"
        if n["status"] == "todo":
            return 4, "⬜ Chưa làm"
        return 5, "💡 Ý tưởng / ghi chú"

    def render_list(self):
        arr = self.visible()
        by_day = self.filter in ("done", "trash") and not self.q
        if by_day:  # tab Hoàn thành: ngày xong mới nhất trên cùng · thùng rác: theo ngày
            dkey = (lambda n: (n.get("done_at") or n.get("updated") or n["date"])[:10]) if self.filter == "done" else (lambda n: n["date"])
            arr.sort(key=lambda n: n.get("done_at") or n.get("updated") or n.get("created") or "", reverse=True)
            arr.sort(key=dkey, reverse=True)
            key, label = dkey, day_label
        else:  # nhóm ưu tiên; trong nhóm: có giờ hẹn thì giờ gần nhất trước, còn lại mới nhất trước
            arr.sort(key=lambda n: n.get("created") or "", reverse=True)
            arr.sort(key=lambda n: n.get("remind_at") or "9999")
            arr.sort(key=lambda n: self.section(n)[0])
            key, label = (lambda n: self.section(n)[1]), (lambda k: k)
        overview = self.filter == "overview" and not self.q
        if overview:  # tổng quan: gom theo dự án (dự án mới động tới trên cùng), trong dự án vẫn theo ưu tiên
            rank = {p["name"]: i for i, p in enumerate(self.projects())}
            arr.sort(key=lambda n: rank.get(n.get("project") or "", len(rank)))
            per = {}
            for n in arr:
                c = per.setdefault(n.get("project") or "", {"task": 0, "done": 0, "verify": 0})
                c["task"] += is_task(n)
                c["done"] += n["status"] == "done"
                c["verify"] += n["status"] == "verify"

            def label(k):
                c = per[k]
                return ("📁 " + (k or "Chưa gắn dự án") + (f"   ·   xong {c['done']}/{c['task']} việc" if c["task"] else "")
                        + (f" · ⏳ {c['verify']} chờ verify" if c["verify"] else ""))
            key = lambda n: n.get("project") or ""
        self.order = [n["id"] for n in arr]
        imgs = sum(len(n.get("images", [])) for n in arr)
        self.title.setText(self.view_title())
        scope = [n for n in self.notes if not n.get("deleted") and n["status"] == "done"
                 and (self.filter != "proj" or (n.get("project") or "") == self.proj)]
        if overview:
            cnt = lambda fn: sum(1 for n in arr if fn(n))
            self.stats.setText(" · ".join(t for t in (
                f"{len(arr)} mục", f"🔴 {cnt(is_urgent)} gấp", f"⏰ {cnt(overdue)} trễ hẹn",
                f"🔄 {cnt(lambda n: n['status'] == 'doing')} đang làm", f"⬜ {cnt(lambda n: n['status'] == 'todo')} chưa làm",
                f"⏳ {cnt(lambda n: n['status'] == 'verify')} chờ verify", f"✅ {cnt(lambda n: n['status'] == 'done')} xong",
                f"💡 {cnt(lambda n: n['status'] == 'note')} ghi chú") if not t.startswith(("🔴 0", "⏰ 0", "⏳ 0"))))
        else:
            self.stats.setText(f"{len(arr)} mục · {imgs} ảnh")
        self.b_done.setText(f"✅ Đã xong ({len(scope)})")
        self.b_done.setVisible(self.filter == "proj" and not self.q and bool(scope))
        self.b_rename.setVisible(self.filter == "proj" and bool(self.proj) and not self.q)
        sb = self.list.verticalScrollBar().value()
        self.list.blockSignals(True)
        self.list.clear()
        groups = {}
        for n in arr:
            groups.setdefault(key(n), []).append(n)
        last, cur = None, None
        for n in arr:
            k = key(n)
            if k != last:
                g = groups[k]
                h = QListWidgetItem(f"{label(k)}   ·   {len(g)}")
                h.setData(Qt.ItemDataRole.UserRole + 1, "h")
                h.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(h)
                last = k
            it = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, n["id"])
            it.setData(Qt.ItemDataRole.UserRole + 1, "n")
            self.list.addItem(it)
            if n["id"] == self.sel:
                cur = it
        if not cur and arr:
            self.sel = arr[0]["id"]
            cur = self.list.item(1)
        if not arr:
            self.sel = None
        if cur:
            self.list.setCurrentItem(cur)
        self.list.verticalScrollBar().setValue(sb)
        self.list.blockSignals(False)
        self.list.setVisible(bool(arr))
        self.empty.setVisible(not arr)
        self.empty.setText("Không tìm thấy ghi chú nào." if self.q else
                           "Hôm nay chưa có gì — gõ vào ô phía trên rồi Ctrl+Enter." if self.filter == "today" else "Trống.")

    def mark_calendar(self):
        self.cal.setDateTextFormat(QDate(), QTextCharFormat())
        has, urg = QTextCharFormat(), QTextCharFormat()
        has.setFontWeight(QFont.Weight.Bold)
        has.setForeground(QColor(C["accent"]))
        urg.setFontWeight(QFont.Weight.Bold)
        urg.setForeground(QColor(C["red"]))
        days = {}
        for n in self.notes:
            if n.get("deleted"):
                continue
            for k in {n["date"], (n.get("remind_at") or "")[:10]}:
                if k:
                    days[k] = days.get(k) or bool(is_urgent(n))
        for k, u in days.items():
            self.cal.setDateTextFormat(QDate.fromString(k, "yyyy-MM-dd"), urg if u else has)

    # ----- chi tiết -----
    def cur(self):
        return self.by_id.get(self.sel)

    def fill_detail(self, force=False):
        n = self.cur()
        if not n:
            for w in self.detail_widgets:
                w.hide()
            self.d_empty.show()
            self.detail_loaded = None
            return
        stamp = (n["id"], json.dumps(n, sort_keys=True, ensure_ascii=False))
        if not force and stamp == self.detail_loaded:
            return
        for w in self.detail_widgets:
            w.show()
        self.d_empty.hide()
        same = self.detail_loaded and self.detail_loaded[0] == n["id"]
        self.detail_loaded = stamp
        self.loading = True
        for w in (self.d_status, self.d_urg, self.d_proj, self.d_date, self.d_rem, self.d_rem_on, self.d_prog):
            w.blockSignals(True)
        self.d_status.setCurrentIndex(list(STATUS).index(n["status"]))
        self.d_urg.setChecked(bool(n.get("urgent")))
        self.d_proj.setEditText(n.get("project") or "")
        self.d_date.setDate(QDate.fromString(n["date"], "yyyy-MM-dd"))
        if n.get("remind_at"):
            self.d_rem.setDateTime(QDateTime.fromString(n["remind_at"], "yyyy-MM-ddTHH:mm"))
        else:
            self.d_rem.setDateTime(QDateTime.currentDateTime().addSecs(3600))
        self.d_rem_on.setChecked(bool(n.get("remind_at")))
        self.d_rem.setEnabled(bool(n.get("remind_at")))
        self.d_repeat.blockSignals(True)
        self.d_repeat.setCurrentIndex(max(0, self.d_repeat.findData(n.get("repeat") or "")))
        self.d_repeat.setEnabled(bool(n.get("remind_at")))
        self.d_repeat.blockSignals(False)
        self.d_prog.setValue(n.get("progress") or 0)
        self.d_prog_l.setText(f"{n.get('progress') or 0}%")
        self.prog_row.setVisible(is_task(n))
        st = n["status"]
        self.verify_box.setVisible(st == "verify" or (st == "done" and bool(n.get("proof"))))
        at = n.get("did_at") if st == "verify" else n.get("done_at")
        when = f" lúc {at[11:16]} {at[8:10]}/{at[5:7]}" if at else ""
        self.d_vlabel.setText(f"⏳ <b>Đã làm{when}, chờ Verify.</b> Kiểm lại: việc này đã làm được thật chưa?"
                              if st == "verify" else f"✔ <b>Đã verify{when}.</b>")
        self.b_verify.setVisible(st == "verify")
        self.b_redo.setVisible(st == "verify")
        if not (same and self.d_proof.hasFocus()):
            self.d_proof.setText(n.get("proof") or "")
        for w in (self.d_status, self.d_urg, self.d_proj, self.d_date, self.d_rem, self.d_rem_on, self.d_prog):
            w.blockSignals(False)
        # đang gõ ở ô chi tiết thì không đè chữ
        if not (same and self.d_text.hasFocus()) and self.d_text.toPlainText() != (n.get("text") or ""):
            self.d_text.setPlainText(n.get("text") or "")
        self.update_spec_bar(self.d_text.toPlainText())
        if not (same and self.d_title.hasFocus()):
            self.d_title.setText(n.get("title") or "")
        auto = ((n.get("text") or "").strip().splitlines() or [""])[0][:60]
        self.d_title.setPlaceholderText("📌 Mục chính…" + (f"  (đang tự lấy: {auto})" if auto else ""))
        imgs = n.get("images", [])
        if [self.d_imgs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.d_imgs.count())] != imgs:
            self.d_imgs.clear()
            for f in imgs:
                it = QListWidgetItem(QIcon(QPixmap(img_path(f)).scaled(208, 208, Qt.AspectRatioMode.KeepAspectRatio,
                                                                       Qt.TransformationMode.SmoothTransformation)), "")
                it.setData(Qt.ItemDataRole.UserRole, f)
                self.d_imgs.addItem(it)
        for i in range(self.d_imgs.count()):  # di chuột lên ảnh: xem trước chữ máy đọc được
            t = A_K().ocr_text(imgs[i])
            self.d_imgs.item(i).setToolTip(("📝 " + t[:400] + ("…" if len(t) > 400 else "")) if t else "Bấm để xem to")
        self.d_imgs.setVisible(bool(imgs))
        self.d_imghead.setVisible(bool(imgs))
        self.d_imghead.setText(f"🖼 {len(imgs)} ảnh · bấm ảnh để xem to, ← → chuyển ảnh, lăn chuột phóng to · chuột phải: chép / xoá")
        # ghi chú chủ yếu là ảnh (lưu kiến thức bằng ảnh chụp) -> dành chỗ cho ảnh, ô chữ gọn lại
        short = len((n.get("text") or "").strip()) < 120
        self.dl.setStretchFactor(self.d_text, 1 if short and imgs else 3)
        self.dl.setStretchFactor(self.d_imgs, 5 if short and len(imgs) > 2 else 2)
        self.b_copy.setText("📋 Chép kèm ảnh" if imgs else "📋 Chép")
        created = n.get("created") or ""
        self.d_meta.setText(f"Tạo {created[11:16]} {created[8:10]}/{created[5:7]}"
                            + (" từ 📱 điện thoại" if n.get("from") == "phone" else "")
                            + (f"  ·  sửa {n['updated'][11:16]}" if n.get("updated") else ""))
        trash = bool(n.get("deleted"))
        self.b_restore.setVisible(trash)
        self.b_del.setText("❌ Xoá hẳn" if trash else "🗑 Xoá")
        self.loading = False

    def save_field(self, **body):
        if self.loading or not self.sel:
            return
        leaving = (body.get("status") == "done" and self.filter not in ("done", "overview") and not self.q
                   and not (self.cur() or {}).get("repeat"))  # nhắc lặp tự sang buổi sau, vẫn ở lại danh sách
        nid = self.sel
        n = update_note(nid, body)
        if n:
            self.flash("✅ Xong — đã chuyển sang tab Hoàn thành" if leaving else "Đã lưu", 2200 if leaving else 900)
        if leaving:
            self.sel = self._next_after(nid)
        self.refresh(force=True)
        self.fill_detail(force=True)

    def save_title(self):
        n = self.cur()
        if n and self.d_title.text().strip() != (n.get("title") or ""):
            self.save_field(title=self.d_title.text().strip())

    def save_proof(self):
        n = self.cur()
        if n and self.d_proof.text().strip() != (n.get("proof") or ""):
            self.save_field(proof=self.d_proof.text().strip())

    def verify_current(self):
        n = self.cur()
        if not n:
            return
        self.save_proof()
        n = self.cur()
        if not (n.get("proof") or n.get("images")):  # Verify mà không có gì để kiểm thì chỉ là tick lần 2
            self.d_proof.setFocus()
            self.flash("🔎 Ghi bằng chứng trước (link, số liệu, ai xác nhận) hoặc thêm ảnh, rồi mới Verify", 3500)
            return
        self.save_field(status="done")
        self.flash("✔ Đã verify, chuyển sang tab Hoàn thành", 2200)

    def update_spec_bar(self, text):
        """Dòng chấm spec: đủ 7 mục chưa, mục 4 có trống không, mục nào quá 5 dòng, còn mấy dấu ? chưa chốt."""
        c = S.spec7_check(text)
        n = self.cur()
        big = (not c and n and is_task(n) and n["status"] != "done" and len(text.strip()) >= SPEC7_SUGGEST)
        self.spec_row.setVisible(bool(c or big) and not self.d_empty.isVisible())
        self.b_sort.setVisible(not c)
        if big:  # gợi ý nhẹ: việc dài mà chưa có khung -> chỉ gợi ý, không tự sửa
            self.d_spec.setText("💡 Ghi chú này giống một việc lớn. Bấm 🪄 Sắp vào 7 mục để xếp lại cho rõ?")
            self.d_spec.setProperty("ok", False)
            self.d_spec.style().polish(self.d_spec)
            self.b_spec_tasks.hide()
        if not c:
            return
        warn = []
        if 4 in c["empty"]:
            warn.append("⚠ Mục 4 KHÔNG làm còn trống (việc sẽ tự phình ra)")
        others = [i for i in c["empty"] if i != 4]
        if others:
            warn.append("Mục " + ", ".join(map(str, others)) + " còn trống")
        warn += [f"Mục {i} dài {k} dòng (tối đa {S.SPEC7_MAX_LINES})" for i, k in c["long"]]
        if c["no_verify"]:
            warn.append(f"{c['no_verify']} dòng mục 6 chưa có Verify")
        if c["lines"] > 7 * S.SPEC7_MAX_LINES:
            warn.append("dài hơn 1 trang, nên tách 2 spec")
        head = f"📐 Spec {7 - len(c['empty'])}/7 mục"
        tail = f"❓ {c['open']} điểm chưa chốt" if c["open"] else ""
        good = not warn
        self.d_spec.setText(" · ".join(x for x in [head + (" ✔ đạt chuẩn" if good else ""), *warn, tail] if x))
        self.d_spec.setProperty("ok", good)
        self.d_spec.style().polish(self.d_spec)
        self.b_spec_tasks.setVisible(c["items"] > 0)
        self.b_spec_tasks.setText(f"➕ Tạo {c['items']} việc từ mục 6")

    def sort_spec7(self):
        n = self.cur()
        if not n:
            return
        self.save_text()
        text = self.d_text.toPlainText()
        if S.spec7_check(text):
            self.flash("Ghi chú này đã có khung 7 mục")
            return
        if not text.strip():
            self.flash("Ghi chú trống, chưa có gì để sắp")
            return
        self.b_sort.setEnabled(False)
        self.b_sort.setText("🪄 Đang sắp… (10 đến 30 giây)")

        def work(nid=n["id"]):
            try:
                self.bridge.sorted.emit(nid, text, A.spec7_sort(text), "")
            except Exception as e:
                S.log("sắp 7 mục lỗi:", repr(e))
                why = ("AI trong máy (Ollama) chưa chạy. Mở Ollama rồi bấm lại." if "refused" in str(e).lower() or "10061" in str(e)
                       else f"AI chưa sắp được: {type(e).__name__}. Bấm lại sau ít phút.")
                self.bridge.sorted.emit(nid, text, "", why)
        threading.Thread(target=work, daemon=True).start()

    def on_spec7_sorted(self, nid, before, after, err):
        self.b_sort.setEnabled(True)
        self.b_sort.setText("🪄 Sắp vào 7 mục")
        if err:
            self.flash("⚠ " + err, 4000)
            return
        live = find_live(nid)
        if not live or (live.get("text") or "") != before:  # đang chờ AI mà bạn sửa ghi chú -> không ghi đè
            self.flash("Ghi chú đã đổi trong lúc AI sắp, chưa ghi đè. Bấm 🪄 lại nếu vẫn muốn sắp.", 4000)
            return
        update_note(nid, {"text": after})
        self.refresh(force=True)
        self.fill_detail(force=True)
        self.flash("🪄 Đã sắp vào 7 mục. Đọc lại từng mục, chỗ \"?\" là còn thiếu. Nội dung gốc ở cuối.", 4000)

    def spec_to_tasks(self):
        n = self.cur()
        if not n:
            return
        self.save_text()
        items = S.spec7_items(self.d_text.toPlainText())
        if not items:
            return
        preview = "\n".join(f"• {it['date'][8:10] + '/' + it['date'][5:7] + ' · ' if it['date'] else ''}{it['title'][:70]}"
                            for it in items[:12]) + (f"\n… và {len(items) - 12} việc nữa" if len(items) > 12 else "")
        proj = n.get("project") or "chưa gắn dự án"
        if QMessageBox.question(self, "Idea Note", f"Tạo {len(items)} việc từ mục 6 vào dự án “{proj}”?\n"
                                "(việc nào đã có rồi sẽ bỏ qua)\n\n" + preview) != QMessageBox.StandardButton.Yes:
            return
        made, skip = S.spec7_tasks(n["id"])
        self.flash(f"➕ Đã tạo {made} việc" + (f", bỏ qua {skip} việc đã có" if skip else "") + f" trong 📁 {proj}", 3000)
        self.refresh(force=True)

    def insert_spec7(self):
        n = self.cur()
        if not n:
            return
        text = self.d_text.toPlainText()
        if SPEC7_HEAD in text:
            self.flash("Ghi chú này đã có khung 7 mục")
            return
        self.d_text.setPlainText(SPEC7 + ("\n" + text if text.strip() else ""))
        self.save_text()
        self.d_text.setFocus()
        self.flash("📐 Đã chèn khung 7 mục lên đầu, nội dung cũ vẫn ở bên dưới")

    def save_text(self):
        n = self.cur()
        if n and self.d_text.toPlainText() != (n.get("text") or ""):
            update_note(n["id"], {"text": self.d_text.toPlainText()})
            self.refresh(force=True)

    def save_repeat(self):
        key = self.d_repeat.currentData() or ""
        self.save_field(repeat=key)
        if key:
            n = self.cur()
            self.flash(f"🔁 Đã đặt lặp lại — buổi tới: {fmt_remind(n['remind_at'])}" if n and n.get("remind_at") else "🔁 Đã đặt lặp lại")

    def save_remind(self):
        on = self.d_rem_on.isChecked()
        self.d_rem.setEnabled(on)
        self.save_field(remind_at=self.d_rem.dateTime().toString("yyyy-MM-ddTHH:mm") if on else None)

    def add_detail_images(self, imgs):
        n = self.cur()
        if not n:
            return
        names = [qimage_to_file(i) for i in imgs]
        self.save_field(images=n.get("images", []) + names)
        self.flash(f"Đã thêm {len(names)} ảnh")

    def pick_detail_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn ảnh", "", "Ảnh (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        imgs = [QImage(f) for f in files]
        if imgs:
            self.add_detail_images([i for i in imgs if not i.isNull()])

    def img_menu(self, pos):
        it = self.d_imgs.itemAt(pos)
        n = self.cur()
        if not it or not n:
            return
        f = it.data(Qt.ItemDataRole.UserRole)
        m = QMenu(self)
        m.addAction("🔍 Xem to", lambda: self.view_images(n.get("images", []).index(f)))
        m.addAction("📝 Chép chữ trong ảnh", lambda: (QApplication.clipboard().setText(A_K().ocr_text(f)), self.flash("Đã chép chữ trong ảnh")))
        m.addAction("📋 Chép ảnh (dán vào Zalo / Claude)", lambda: (QApplication.clipboard().setImage(QImage(img_path(f))), self.flash("Đã chép ảnh — Ctrl+V để dán")))
        m.addAction("📄 Chép đường dẫn ảnh", lambda: (QApplication.clipboard().setText(img_path(f)), self.flash("Đã chép đường dẫn")))
        m.addAction("📂 Mở thư mục chứa ảnh", lambda: subprocess.Popen(["explorer", "/select,", img_path(f)]))
        m.addSeparator()
        m.addAction("🗑 Xoá ảnh này", lambda: self.save_field(images=[x for x in n.get("images", []) if x != f]))
        m.exec(self.d_imgs.mapToGlobal(pos))

    # ----- chống quên lưu -----
    def flush_detail(self):
        """Ghi ngay mọi chỗ đang sửa ở khung phải (chữ đang chờ 0,5 giây, tiêu đề, bằng chứng).
        Trước đây chuyển thư mục trong nửa giây sau khi gõ là mất lần sửa cuối."""
        if self.save_timer.isActive():
            self.save_timer.stop()
            self.save_text()
        self.save_title()
        self.save_proof()

    def leave_ok(self, quitting=False):
        """Trước khi chuyển thư mục / ngày / thoát: lưu chỗ đang sửa; ô Ghi nhanh còn chữ / ảnh chưa lưu thì hỏi.
        Trả về False nếu chọn Ở lại."""
        self.flush_detail()
        text, title = self.txt.toPlainText().strip(), self.ctitle.text().strip()
        if not (text or title or self.pend):
            return True
        head = (title or (text.splitlines()[0] if text else "") or f"{len(self.pend)} ảnh")[:70]
        where = self.cproj.currentText().strip() or "Chưa gắn dự án"
        box = QMessageBox(self)
        box.setWindowTitle("Idea Note · Chưa lưu")
        box.setIcon(QMessageBox.Icon.Warning)
        extra = f"  (+{len(self.pend)} ảnh)" if self.pend and (text or title) else ""
        box.setText(f"Ghi chú đang gõ ở ô Ghi nhanh CHƯA LƯU:\n\n“{head}”{extra}\n\nLưu vào 📁 {where} "
                    + ("trước khi thoát?" if quitting else "trước khi chuyển?"))
        b_save = box.addButton("💾 Lưu rồi " + ("thoát" if quitting else "chuyển"), QMessageBox.ButtonRole.AcceptRole)
        b_keep = box.addButton("Thoát, bỏ ghi chú này" if quitting else "Để trong ô, lưu sau", QMessageBox.ButtonRole.DestructiveRole)
        b_stay = box.addButton("Ở lại", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_save)
        box.setEscapeButton(b_stay)
        box.exec()
        c = box.clickedButton()
        if c is b_save:
            self.create()
            return True
        if c is b_keep:
            return True
        self.txt.setFocus()
        return False

    # ----- sự kiện -----
    def on_side(self, it):
        key = it.data(Qt.ItemDataRole.UserRole)
        if key is None:
            return
        if not self.leave_ok():
            self.refresh(force=True)  # vẽ lại cột trái để mục đang chọn về đúng chỗ cũ
            return
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.q = ""
        if key.startswith("proj:"):
            self.filter, self.proj = "proj", key[5:]
            self.cproj.setEditText(self.proj)
        else:
            self.filter, self.proj = key, None
        self.done_scope = None
        self.sel = None
        self.refresh(force=True)

    def show_project_done(self):
        if not self.leave_ok():
            return
        self.done_scope, self.filter, self.sel = self.proj or "", "done", None
        self.refresh(force=True)

    def _next_after(self, nid):
        """Dòng sẽ chọn tiếp khi dòng hiện tại rời khỏi danh sách (xong / xoá)."""
        if nid not in self.order:
            return self.sel
        i = self.order.index(nid)
        return self.order[i + 1] if i + 1 < len(self.order) else (self.order[i - 1] if i > 0 else None)

    def on_day(self, qd):
        if not self.leave_ok():
            return
        self.filter, self.day, self.q = "day", qd.toString("yyyy-MM-dd"), ""
        self.sel = None
        self.refresh(force=True)

    def on_search(self, t):
        self.flush_detail()  # gõ tìm kiếm: chỉ lưu chỗ đang sửa, không hỏi (ô Ghi nhanh vẫn giữ chữ)
        self.q = t.strip()
        self.sel = None
        self.refresh(force=True)

    def on_select(self, it, _prev=None):
        if it and it.data(Qt.ItemDataRole.UserRole + 1) == "n":
            self.flush_detail()  # lưu ghi chú cũ (chữ, tiêu đề, bằng chứng) trước khi mở ghi chú khác
            self.sel = it.data(Qt.ItemDataRole.UserRole)
            self.fill_detail()

    def toggle_done(self, nid):
        n = self.by_id.get(nid)
        if n:
            if n["status"] == "verify":  # Verify phải nhìn bằng chứng, không tick mù lần 2: mở hộp Verify bên phải
                self.sel = nid
                self.refresh(force=True)
                self.fill_detail(force=True)
                self.d_proof.setFocus()
                self.flash("⏳ Xem bằng chứng rồi bấm ✔ Verify ở khung bên phải", 2600)
                return
            done = n["status"] != "done"
            if n.get("repeat") and done:  # nhắc lặp: xong buổi này -> tự hẹn buổi sau, vẫn nằm trong danh sách
                nn = update_note(nid, {"status": "done"})
                self.flash(f"✅ Xong buổi này — 🔁 hẹn buổi sau: {fmt_remind(nn['remind_at'])}" if nn else "✅ Xong")
                self.refresh(force=True)
                return
            if done:  # việc thường: tick = "đã làm", chờ Verify
                update_note(nid, {"status": "verify"})
                self.sel = nid
                self.refresh(force=True)
                if self.sel == nid:  # còn trong danh sách đang xem: mở luôn ô bằng chứng
                    self.fill_detail(force=True)
                    self.d_proof.setFocus()
                    self.flash("☑ Đã làm. Ghi bằng chứng rồi bấm ✔ Verify để tính là xong", 3000)
                else:
                    self.flash("☑ Đã làm, chuyển sang tab ⏳ Chờ verify", 2600)
                return
            if self.sel == nid and not self.q and self.filter != "overview":
                self.sel = self._next_after(nid)
            update_note(nid, {"status": "done" if done else "todo"})
            self.flash("✅ Xong — đã chuyển sang tab Hoàn thành" if done else "↩ Đã đưa về Chưa làm")
            self.refresh(force=True)

    def list_menu(self, pos):
        it = self.list.itemAt(pos)
        if not it or it.data(Qt.ItemDataRole.UserRole + 1) != "n":
            return
        n = self.by_id[it.data(Qt.ItemDataRole.UserRole)]
        self.list.setCurrentItem(it)
        m = QMenu(self)
        m.addAction("📋 Chép", self.copy_note)
        st = m.addMenu("Trạng thái")
        for k, v in STATUS.items():
            st.addAction(v, lambda k=k: self.save_field(status=k))
        m.addAction("🔴 Bỏ gấp" if n.get("urgent") else "🔴 Đánh dấu gấp", lambda: self.save_field(urgent=not n.get("urgent")))
        m.addSeparator()
        m.addAction("❌ Xoá hẳn" if n.get("deleted") else "🗑 Xoá", self.delete_current)
        m.exec(self.list.mapToGlobal(pos))

    def delete_current(self):
        n = self.cur()
        if not n:
            return
        if n.get("deleted") and QMessageBox.question(self, "Idea Note", "Xoá hẳn ghi chú này? Không lấy lại được.") != QMessageBox.StandardButton.Yes:
            return
        i = self.order.index(n["id"]) if n["id"] in self.order else 0
        nxt = self.order[i + 1] if i + 1 < len(self.order) else (self.order[i - 1] if i > 0 else None)
        delete_note(n["id"])
        self.sel = nxt
        self.flash("Đã xoá hẳn" if n.get("deleted") else "Đã chuyển vào thùng rác (khôi phục được)")
        self.refresh(force=True)

    def jump_to(self, nid):
        n = self.by_id.get(nid) or next((x for x in snapshot() if x["id"] == nid), None)
        if not n:
            return
        self.filter, self.q, self.sel = ("trash" if n.get("deleted") else "all"), "", nid
        self.refresh(force=True)

    # ----- ô ghi nhanh -----
    def grow_capture(self, focus):
        self.txt.setFixedHeight(110 if focus or self.txt.toPlainText() or self.pend else 46)

    def add_pending(self, imgs):
        for img in imgs:
            self.pend.append(img)
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img).scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                                                      Qt.TransformationMode.SmoothTransformation)), "")
            self.pend_box.addItem(it)
        self.pend_box.setVisible(bool(self.pend))
        self.grow_capture(True)
        self.flash(f"Đã thêm {len(imgs)} ảnh — bấm Lưu")

    def pend_menu(self, pos):
        it = self.pend_box.itemAt(pos)
        if it:
            i = self.pend_box.row(it)
            m = QMenu(self)
            m.addAction("Bỏ ảnh này", lambda: (self.pend.pop(i), self.pend_box.takeItem(i),
                                                 self.pend_box.setVisible(bool(self.pend))))
            m.exec(self.pend_box.mapToGlobal(pos))

    def pick_images(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Chọn ảnh", "", "Ảnh (*.png *.jpg *.jpeg *.gif *.webp *.bmp)")
        imgs = [i for i in (QImage(f) for f in files) if not i.isNull()]
        if imgs:
            self.add_pending(imgs)

    def capture_spec7(self):
        text = self.txt.toPlainText().strip()
        if SPEC7_HEAD in text:
            self.flash("Ô ghi nhanh đã có khung 7 mục")
            return
        if text and "\n" not in text and not self.ctitle.text().strip():  # gõ 1 dòng tên trước -> thành tiêu đề
            self.ctitle.setText(text)
            text = ""
        self.txt.setPlainText(SPEC7 + ("\n" + text if text else ""))
        self.txt.setFocus()
        self.grow_capture(True)
        self.flash("📐 Đã điền khung 7 mục. Điền xong Ctrl+Enter để lưu")

    def set_remind(self, minutes=None, at=None, clear=False, value=None):
        if clear:
            self.remind = None
        elif value:
            self.remind = value
        elif minutes:
            self.remind = (dt.datetime.now() + dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M")
        elif at:
            d = dt.date.today() + dt.timedelta(days=at[0])
            self.remind = f"{d}T{at[1]:02d}:00"
        self.b_rem.setText(("🔁 " if getattr(self, "repeat", "") else "⏰ ") + fmt_remind(self.remind) if self.remind else "⏰ Nhắc hẹn")
        self.b_rem.setProperty("on", bool(self.remind))
        self.b_rem.style().unpolish(self.b_rem)
        self.b_rem.style().polish(self.b_rem)

    def set_repeat(self, key):
        self.repeat = key
        for k, a in self.rep_act.items():
            a.blockSignals(True)
            a.setChecked(k == key)
            a.blockSignals(False)
        if key and not self.remind:
            self.set_remind(minutes=60)  # lặp lại cần có giờ nhắc
        self.set_remind(value=self.remind) if self.remind else None

    def pick_remind(self):
        d = QDialog(self)
        d.setWindowTitle("Chọn giờ nhắc")
        e = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        e.setCalendarPopup(True)
        e.setDisplayFormat("HH:mm  dd/MM/yyyy")
        ok = QPushButton("Đặt nhắc")
        ok.setObjectName("primary")
        ok.clicked.connect(d.accept)
        lay = QVBoxLayout(d)
        lay.addWidget(e)
        lay.addWidget(ok)
        if d.exec():
            self.set_remind(value=e.dateTime().toString("yyyy-MM-ddTHH:mm"))

    def create(self):
        text = self.txt.toPlainText().strip()
        if not text and not self.pend and not self.ctitle.text().strip():
            self.txt.setFocus()
            return
        names = [qimage_to_file(i) for i in self.pend]
        date = self.day if self.filter == "day" and self.day else today()
        n = S.new_note({"title": self.ctitle.text().strip(), "text": text, "status": self.kind, "urgent": self.b_urg.isChecked(), "remind_at": self.remind,
                        "repeat": self.repeat if self.remind else "", "project": self.cproj.currentText().strip(), "date": date, "image_files": names})
        self.txt.clear()
        self.ctitle.clear()
        self.pend.clear()
        self.pend_box.clear()
        self.pend_box.hide()
        self.b_urg.setChecked(False)
        self.set_remind(clear=True)
        self.set_repeat("")
        self.sel = n["id"]
        self.refresh(force=True)
        if n["id"] not in self.order:
            if n.get("project"):
                self.filter, self.proj = "proj", n["project"]
            else:
                self.filter = "all"
            self.q = ""
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.refresh(force=True)
        self.flash("Đã lưu ✓")
        self.txt.setFocus()

    # ----- chép / xuất -----
    def copy_note(self):
        n = self.cur()
        if not n:
            return
        parts = [n.get("text") or ""] + ["Ảnh: " + img_path(f) for f in n.get("images", [])]
        QApplication.clipboard().setText("\n".join(parts).strip())
        self.flash("Đã chép chữ + đường dẫn ảnh — dán vào Claude" if n.get("images") else "Đã chép — dán vào Zalo/FB/Claude")

    def copy_claude(self):
        arr = [self.by_id[i] for i in self.order]
        if not arr:
            return
        QApplication.clipboard().setText(context_text(arr, self.view_title().lstrip("📁🗂📌⬜🔄🔴⏰✅💡🗑 ")))
        self.flash(f"Đã chép {len(arr)} mục cho Claude — dán vào là đủ ngữ cảnh")

    def export(self):
        arr = [self.by_id[i] for i in self.order]
        if arr:
            title = self.view_title().lstrip("📁🗂📌⬜🔄🔴⏰✅💡🗑 ")
            S.export_pack(title, context_text(arr, title))
            self.flash("Đã mở thư mục gói: kéo CONTEXT.md + ảnh vào Claude")

    def rename_project(self):
        if not self.proj:
            return
        nv, ok = QInputDialog.getText(self, "Đổi tên dự án", "Tên mới:", text=self.proj)
        if ok and nv.strip() and nv.strip() != self.proj:
            S.rename_project(self.proj, nv)
            self.proj = " ".join(nv.split())
            self.refresh(force=True)

    # ----- thông báo nhỏ trên tiêu đề -----
    def flash(self, msg, ms=2200):
        self.setWindowTitle(f"Idea Note — {msg}")
        QTimer.singleShot(ms, lambda: self.setWindowTitle("Idea Note"))
        if not self.isVisible() or self.isMinimized():
            self.tray.showMessage("Idea Note", msg, QIcon(ICON), 2500)

    # ----- nhắc hẹn -----
    def show_reminder(self, n):
        p = ReminderPopup(self, n)
        self.popups.append(p)
        p.show()
        p.raise_()
        self.refresh(force=True)

    # ----- cửa sổ / khay / bong bóng -----
    def show_main(self):
        bring_front(self)
        self.sync_bubble()

    def open_assistant(self):
        if not self.assist:
            self.assist = AssistantPanel(self)
        threading.Thread(target=A.Brain.warm, daemon=True).start()
        self.assist.show()
        self.assist.raise_()
        self.assist.activateWindow()
        self.assist.input.setFocus()

    def open_kb(self):
        if not getattr(self, "kb", None):
            self.kb = KnowledgePanel(self)
        self.kb.open()

    def view_images(self, i=0):
        n = self.cur()
        if n and n.get("images"):
            GalleryViewer(n["images"], i, note_title(n), self).exec()

    def toggle_side(self):
        self.side_frame.setVisible(not self.side_frame.isVisible())

    def fit_split(self):
        w = self.split.width()
        if w and not self.side_frame.isVisible():
            self.split.blockSignals(True)
            self.split.setSizes([0, int(w * 0.56), w - int(w * 0.56)])
            self.split.blockSignals(False)

    def eventFilter(self, obj, e):
        if obj is getattr(self, "mid", None) and e.type() == QEvent.Type.Resize and hasattr(self, "compact_btns"):
            small = obj.width() < 640
            if small != getattr(self, "_small", None):
                self._small = small
                for b, full, short in self.compact_btns:
                    b.setText(short if small else full)
        return super().eventFilter(obj, e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # cửa sổ hẹp: tự ẩn cột trái (bấm ☰ để hiện lại) · cửa sổ thấp: ẩn lịch tháng
        narrow = self.width() < 1000
        if narrow != getattr(self, "_narrow", None):
            self._narrow = narrow
            self.side_frame.setVisible(not narrow)
            self.split_user = False
        if narrow and not getattr(self, "split_user", False):  # cột giữa (danh sách) được nhiều chỗ hơn khung chi tiết
            QTimer.singleShot(0, self.fit_split)
        self.cal.setVisible(self.height() >= 640)

    def voice_hotkey(self):
        """Ctrl+Alt+V: đang dùng app -> đọc chữ vào ô ghi chú; đang làm việc khác -> nói qua bong bóng."""
        if self.voice.recording:
            self.voice.toggle(self.voice.origin)
        elif self.isVisible() and not self.isMinimized() and self.isActiveWindow():
            self.txt.setFocus()
            self.voice.toggle("capture")
        else:
            self.voice_bubble()

    def voice_bubble(self):
        """Nói qua bong bóng: trợ lý tự lưu ghi chú / đặt nhắc, kết quả hiện ngay cạnh bong bóng."""
        if not self.bubble.isVisible():
            self.bubble.show()
            self.bubble.raise_()
        self.voice.toggle("bubble")

    # ----- kết quả giọng nói -----
    def on_voice_level(self, lv, talking):
        self.bubble.set_voice(level=lv)
        self.vmeter.setValue(int(lv * 100))
        self.vmeter.setProperty("talk", talking)
        self.vmeter.style().polish(self.vmeter)

    def on_voice_state(self, s):
        o = self.voice.origin
        self.bubble.set_voice(state=s if o != "capture" else "idle", level=0)
        cap = o == "capture" and s != "idle"  # listen / loading / recognize
        self.vrow.setVisible(cap)
        self.vlabel.setText({"listen": "🎙 Đang nghe… nói xong ngừng 1 giây (hoặc bấm ⏹)", "recognize": "⏳ Đang chuyển giọng nói thành chữ…",
                             "loading": "⏳ Đang nạp bộ nghe (lâu không dùng nên nạp lại, chừng 20 giây)…"}.get(s, ""))
        self.b_mic.setText("⏹ Dừng" if cap and s == "listen" else "🎙 Nói")
        self.b_mic.setProperty("rec", cap and s == "listen")
        self.b_mic.style().polish(self.b_mic)
        if o == "bubble":
            msg = {"listen": "🎙 Đang nghe… nói xong ngừng 1 giây", "recognize": "⏳ Đang nhận dạng giọng nói…",
                   "loading": "⏳ Đang nạp bộ nghe (lâu không dùng nên nạp lại, chừng 20 giây)…",
                   "think": "🤖 Đang xử lý…"}.get(s)
            if msg:
                self.toast.say(msg, ms=0)

    def on_voice_heard(self, origin, text):
        if origin == "capture":
            cur = self.txt.toPlainText().rstrip()
            self.txt.setPlainText((cur + " " if cur else "") + text)
            self.txt.moveCursor(self.txt.textCursor().MoveOperation.End)
            self.grow_capture(True)
            self.txt.setFocus()
            self.flash("Đã chép lời nói vào ô ghi chú — sửa nếu cần rồi Ctrl+Enter để lưu")
        elif origin == "bubble":
            self.toast.say(f"🗣 “{text}”\n🤖 Đang xử lý…", ms=0)

    def on_voice_answer(self, origin, reply, acts):
        self.refresh(force=True)
        if origin == "bubble":
            lines, nid = actions_summary(self, acts)
            self.toast.say(("\n".join(lines) + "\n" if lines else "") + "🤖 " + reply, nid=nid, ms=9000)
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)

    def on_voice_fail(self, origin, msg):
        if origin == "bubble":
            self.toast.say("⚠️ " + msg, ms=8000)
        elif origin == "capture":
            self.flash(msg, 6000)
            self.vrow.show()
            self.vlabel.setText("⚠️ " + msg)
            QTimer.singleShot(6000, lambda: self.voice.busy or self.vrow.hide())

    def quick(self):
        self.show_main()
        self.txt.setFocus()

    def sync_bubble(self):
        want = not self.hide_bubble.isChecked() and (self.always.isChecked() or not self.isVisible() or self.isMinimized())
        if want and not self.bubble.isVisible():
            self.bubble.show()
            self.bubble.raise_()
        elif not want and self.bubble.isVisible():
            self.bubble.hide()

    def on_always(self, v):
        self.conf["always_bubble"] = v
        save_app_conf(self.conf)
        self.sync_bubble()

    def on_no_bubble(self, v):
        self.conf["no_bubble"] = v
        save_app_conf(self.conf)
        self.sync_bubble()
        if v:  # dễ bấm nhầm trong menu chuột phải -> nói rõ cách bật lại
            self.tray.showMessage("Đã tắt bong bóng nổi",
                                  "Bật lại: chuột phải icon Idea Note ở khay (góc phải dưới màn hình) → bỏ chọn “Tắt bong bóng”.",
                                  QIcon(ICON), 8000)

    def showEvent(self, e):
        super().showEvent(e)
        if getattr(self, "_stale", False):
            QTimer.singleShot(0, lambda: self.refresh(force=True))

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                # thu nhỏ = gom vào bong bóng + icon khay, không để nút trên thanh tác vụ
                QTimer.singleShot(0, self._tuck_away)
            else:
                QTimer.singleShot(50, self.sync_bubble)
        super().changeEvent(e)

    def _tuck_away(self):
        self.save_timer.isActive() and (self.save_timer.stop(), self.save_text())
        self.hide()
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.sync_bubble()

    def closeEvent(self, e):
        # đóng cửa sổ = ẩn xuống khay, vẫn chạy để nhắc hẹn
        e.ignore()
        self.save_geom()
        self.hide()
        self.sync_bubble()
        if not self.conf.get("told_tray"):
            self.tray.showMessage("Idea Note vẫn chạy ngầm", "Bấm bong bóng hoặc icon ở khay để mở lại. Thoát hẳn: chuột phải icon khay.",
                                  QIcon(ICON), 5000)
            self.conf["told_tray"] = True
            save_app_conf(self.conf)

    def save_geom(self):
        if not self.isMinimized() and self.isVisible():
            g = self.geometry()
            self.conf["geom"] = [g.x(), g.y(), g.width(), g.height()]
            save_app_conf(self.conf)

    def quit_all(self):
        if not self.isVisible():
            self.show_main()  # đang ẩn dưới khay mà ô Ghi nhanh còn chữ: mở ra để thấy câu hỏi
        if not self.leave_ok(quitting=True):
            return
        self.save_timer.stop()
        self.save_text()
        self.save_geom()
        self.tray.hide()
        S.flush()  # ghi nốt lần sửa cuối (bình thường luồng nền ghi sau 0,5 giây)
        QApplication.quit()

    # ----- hộp thoại -----
    def voice_from_phone(self, data, ext):
        """Chạy trong luồng máy chủ: ghi âm gửi từ điện thoại -> chữ -> tự lưu như mic trên bong bóng."""
        path = os.path.join(S.DATA, f"voice-phone.{ext}")
        with open(path, "wb") as f:
            f.write(data)
        text = self.ear.transcribe_file(path, hint=", ".join(p["name"] for p in self.projects()[:8]))
        S.log("điện thoại ghi âm:", ext, len(data), "byte ->", repr(text[:120]))
        if not text:
            return {"text": "", "reply": "Máy tính chưa nghe ra chữ nào — nói gần mic điện thoại hơn rồi thử lại."}
        reply, acts = V.quick(text)
        ids = [i for k, i in acts if k in ("create", "update")]
        saved = None
        with S.LOCK:
            live = next((x for x in S.NOTES if ids and x["id"] == ids[0]), None)
            if live and live.get("from") == "voice":  # ghi âm từ điện thoại -> đánh dấu nguồn 📱
                live["from"] = "phone"
                S.persist()
            saved = dict(live) if live else None
        extra = (" · ⏰ " + fmt_remind(saved["remind_at"])) if saved and saved.get("remind_at") else ""
        return {"text": text, "reply": reply + extra, "id": ids[0] if ids else None}

    def phone_dialog(self):
        import qrcode
        ips = S.lan_ips()
        urls = [f"https://{ip}:{S.PORT}/?k={S.CONF['key']}" for ip in ips]
        d = QDialog(self)
        d.setWindowTitle("📱 Mở trên điện thoại")
        lay = QVBoxLayout(d)
        info = QLabel("Điện thoại bắt <b>cùng Wi-Fi</b> với máy tính, quét mã này bằng camera / Zalo.<br>"
                      "<b>Lần đầu</b> điện thoại báo “Kết nối không riêng tư” (chứng chỉ do chính máy tính này tạo) → bấm "
                      "<b>Nâng cao → Tiếp tục truy cập</b>.<br>Sau đó chọn “Thêm vào màn hình chính”. Dùng địa chỉ "
                      "<b>https://</b> này thì nút 🎙 Ghi âm trên điện thoại mới dùng được mic.")
        info.setWordWrap(True)
        lay.addWidget(info)
        if urls:
            buf = io.BytesIO()
            qrcode.make(urls[0], border=2).save(buf, format="PNG")
            pm = QPixmap()
            pm.loadFromData(buf.getvalue())
            q = QLabel()
            q.setPixmap(pm.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
            q.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(q)
            u = QLabel("<br>".join(urls))
            u.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            u.setObjectName("muted")
            lay.addWidget(u)
        else:
            lay.addWidget(QLabel("Máy tính chưa kết nối Wi-Fi / mạng LAN."))
        lay.addWidget(QLabel("Điện thoại không vào được? Mở cổng tường lửa một lần (cần bấm <b>Yes</b> quyền Admin):"))
        fw = QPushButton("🛡 Mở tường lửa cho điện thoại")
        fw.clicked.connect(lambda: os.startfile(os.path.join(HOME, "mo-tuong-lua-cho-dien-thoai.cmd")))
        lay.addWidget(fw)
        d.exec()

    def settings_dialog(self):
        d = QDialog(self)
        d.setWindowTitle("⚙️ Cài đặt")
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel("Email nhận khi bấm “✉️ Gmail” (để trống = tự điền):"))
        em = QLineEdit(S.CONF.get("email", ""))
        lay.addWidget(em)
        auto = QCheckBox("Tự chạy ngầm khi bật máy (để nhắc hẹn luôn hoạt động)")
        auto.setChecked(os.path.exists(startup_link()))
        lay.addWidget(auto)
        lay.addWidget(QLabel("🎙 Micro dùng để nói ghi chú:"))
        mic = QComboBox()
        try:
            for idx, name in A.Ear.devices():
                mic.addItem(name, idx)
        except Exception as e:
            mic.addItem(f"Không đọc được mic: {e}", None)
        saved = self.conf.get("mic_name")
        if saved:
            i = next((i for i in range(mic.count()) if mic.itemText(i).startswith(saved)), -1)
            if i >= 0:
                mic.setCurrentIndex(i)
        lay.addWidget(mic)
        lay.addWidget(QLabel("Phím tắt toàn máy: <b>Ctrl+Alt+N</b> mở Idea Note để ghi · <b>Ctrl+Alt+V</b> nói ghi chú / giao việc."))
        row = QHBoxLayout()
        b1 = QPushButton("📂 Mở thư mục dữ liệu")
        b1.clicked.connect(lambda: os.startfile(S.DATA))
        row.addWidget(b1)
        b2 = QPushButton("🔔 Thử nhắc hẹn")
        b2.clicked.connect(lambda: self.show_reminder({"id": "", "remind_at": now_str(), "text": "Nhắc hẹn hoạt động tốt ✓"}))
        row.addWidget(b2)
        lay.addLayout(row)

        # ----- email + Google Lịch -----
        box = QFrame()
        box.setObjectName("card")
        bl = QVBoxLayout(box)
        t = QLabel("<b>📧 Nhắc hẹn qua Gmail + Google Lịch</b><br><span style='color:#6b6b72'>Đặt nhắc → email kèm lời mời "
                   "lịch (Google Lịch tự thêm, điện thoại tự báo). Tới giờ → email “⏰ ĐẾN GIỜ”. Xong / xoá → huỷ lịch.</span>")
        t.setWordWrap(True)
        bl.addWidget(t)
        gm = QLineEdit(S.CONF.get("gmail") or S.CONF.get("email", ""))
        gm.setPlaceholderText("ban@gmail.com")
        bl.addWidget(QLabel("Gmail dùng để GỬI thư (đăng nhập bằng Mật khẩu ứng dụng bên dưới):"))
        bl.addWidget(gm)
        bl.addWidget(QLabel("Gmail NHẬN nhắc hẹn + lời mời lịch (để trống = gửi cho chính Gmail trên):"))
        mto = QLineEdit(S.CONF.get("mail_to", ""))
        mto.setPlaceholderText("để trống = chính Gmail gửi")
        bl.addWidget(mto)
        bl.addWidget(QLabel("Tên người gửi hiện trong hộp thư:"))
        mname = QLineEdit(M.sender_name())
        mname.setPlaceholderText("Idea Note")
        bl.addWidget(mname)
        tip = QLabel("<span style='color:#6b6b72'>Gmail luôn ghi “tôi” khi thư đi từ chính địa chỉ đang đọc. Muốn thấy "
                     "tên ở trên thì gửi bằng một Gmail riêng (ô GỬI) và điền Gmail của anh/chị vào ô NHẬN.</span>")
        tip.setWordWrap(True)
        bl.addWidget(tip)
        bl.addWidget(QLabel("Mật khẩu ứng dụng Gmail (16 ký tự, không phải mật khẩu đăng nhập):"))
        pw = QLineEdit()
        pw.setEchoMode(QLineEdit.EchoMode.Password)
        pw.setPlaceholderText("đã lưu (mã hoá bằng Windows) — để trống nếu không đổi" if S.CONF.get("gmail_pw")
                              else "dán Mật khẩu ứng dụng vào đây")
        bl.addWidget(pw)
        howto = QPushButton("🔑 Mở trang tạo Mật khẩu ứng dụng (cần bật Xác minh 2 bước)")
        howto.clicked.connect(lambda: webbrowser.open("https://myaccount.google.com/apppasswords"))
        bl.addWidget(howto)
        inv = QCheckBox("Tự gửi lời mời Google Lịch khi đặt / đổi giờ nhắc")
        inv.setChecked(S.CONF.get("mail_invite", True))
        due = QCheckBox("Gửi email “⏰ ĐẾN GIỜ” lúc tới giờ hẹn")
        due.setChecked(S.CONF.get("mail_due", True))
        bl.addWidget(inv)
        bl.addWidget(due)
        mrow = QHBoxLayout()
        test = QPushButton("✉️ Lưu + gửi thử")
        mstat = QLabel()
        mstat.setWordWrap(True)
        mstat.setObjectName("muted")
        if M.LAST.get("error"):
            mstat.setText("⚠️ Lần gửi gần nhất lỗi: " + M.LAST["error"][:160])
        elif M.LAST.get("sent"):
            mstat.setText("✓ Gửi được lúc " + M.LAST["sent"])

        def save_mail():
            M.set_account(gm.text(), pw.text() or None, name=mname.text(), to=mto.text())
            pw.clear()
            pw.setPlaceholderText("đã lưu (mã hoá bằng Windows) — để trống nếu không đổi")
            S.CONF["mail_invite"], S.CONF["mail_due"] = inv.isChecked(), due.isChecked()
            S.save(S.CONF_F, S.CONF)

        def do_test():
            save_mail()
            if not M.configured():
                mstat.setText("⚠️ Cần điền Gmail và Mật khẩu ứng dụng trước.")
                return
            mstat.setText("⏳ Đang gửi thử…")
            QApplication.processEvents()
            err = M.send_test()
            mstat.setText("⚠️ " + err if err else f"✓ Đã gửi, mở Gmail ({M.recipient()}) xem thư “{M.sender_name()}”.")

        test.clicked.connect(do_test)
        mrow.addWidget(test)
        mrow.addWidget(mstat, 1)
        bl.addLayout(mrow)
        lay.addWidget(box)

        ok = QPushButton("Lưu")
        ok.setObjectName("primary")
        ok.clicked.connect(d.accept)
        lay.addWidget(ok)
        if d.exec():
            S.CONF["email"] = em.text().strip()
            S.save(S.CONF_F, S.CONF)
            save_mail()
            set_autostart(auto.isChecked())
            if mic.currentData() is not None:
                self.conf["mic_name"] = mic.currentText().replace(" (mặc định)", "")
                save_app_conf(self.conf)
            self.flash("Đã lưu cài đặt")


def startup_link():
    return os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup", "Idea Note.lnk")


def set_autostart(on):
    link = startup_link()
    if not on:
        if os.path.exists(link):
            os.remove(link)
        return
    target = sys.executable
    args = "--tray" if FROZEN else f'"{os.path.abspath(__file__)}" --tray'
    if not FROZEN:
        target = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{link}');$s.TargetPath='{target}';"
          f"$s.Arguments='{args}';$s.WorkingDirectory='{HOME}';$s.IconLocation='{ICON},0';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], creationflags=0x08000000)


STYLE = f"""
QWidget {{ font-family: 'Segoe UI'; font-size: 10pt; color: {C['ink']}; }}
Main, QSplitter, QDialog {{ background: {C['bg']}; }}
QFrame#side {{ background: {C['bg']}; }}
QFrame#card, QFrame#detail {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 14px; }}
QFrame#detail {{ margin: 12px 12px 12px 0; }}
QLabel#logo {{ font-size: 13pt; font-weight: 700; }}
QLabel#title {{ font-size: 14pt; font-weight: 700; }}
QLabel#muted {{ color: {C['muted']}; }}
QLineEdit, QComboBox, QDateEdit, QDateTimeEdit {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 5px 8px; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QDateTimeEdit:focus, QPlainTextEdit#dtext:focus {{ border-color: {C['accent']}; }}
QPlainTextEdit#capture {{ border: none; background: transparent; font-size: 11pt; }}
QPlainTextEdit#dtext {{ border: 1px solid {C['line']}; border-radius: 10px; padding: 6px; font-size: 11pt; background: {C['panel']}; }}
QPushButton, QToolButton {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 5px 11px; }}
QPushButton:hover, QToolButton:hover {{ background: {C['soft']}; }}
QPushButton:checked, QToolButton[on="true"] {{ background: {C['accent_soft']}; border-color: {C['accent']}; color: {C['accent']}; font-weight: 600; }}
QPushButton#urg:checked {{ background: {C['red_soft']}; border-color: {C['red']}; color: {C['red']}; }}
QPushButton#primary {{ background: {C['accent']}; color: white; border: none; font-weight: 600; padding: 6px 18px; }}
QPushButton#primary:hover {{ background: #c97a00; }}
QPushButton#claude {{ background: {C['claude']}; color: white; border: none; font-weight: 600; padding: 6px 14px; }}
QPushButton#claude:hover {{ background: #c4643f; }}
QPushButton#danger:hover {{ background: {C['red_soft']}; color: {C['red']}; }}
QPushButton#verify {{ background: {C['green']}; color: white; border: none; font-weight: 600; padding: 6px 16px; }}
QPushButton#verify:hover {{ background: #188547; }}
QLabel#specbar {{ background: {C['accent_soft']}; color: {C['ink']}; border-radius: 8px; padding: 5px 9px; font-size: 9pt; }}
QLabel#specbar[ok="true"] {{ background: {C['green_soft']}; color: {C['green']}; font-weight: 600; }}
QFrame#verifybox {{ background: {C['accent_soft']}; border: 1px solid {C['accent']}; border-radius: 10px; }}
QToolButton::menu-indicator {{ image: none; }}
QListWidget {{ border: none; outline: 0; background: transparent; }}
QListWidget#notes {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 14px; padding: 2px 0; }}
QCalendarWidget QWidget {{ alternate-background-color: {C['panel']}; }}
QCalendarWidget QAbstractItemView {{ background: {C['panel']}; selection-background-color: {C['accent']}; selection-color: white; border-radius: 10px; }}
QCalendarWidget QWidget#qt_calendar_navigationbar {{ background: {C['panel']}; border-top-left-radius: 10px; border-top-right-radius: 10px; }}
QCalendarWidget QToolButton {{ border: none; padding: 2px 6px; font-weight: 600; }}
QSlider::groove:horizontal {{ height: 6px; background: {C['soft']}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {C['blue']}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: white; border: 2px solid {C['blue']}; width: 14px; margin: -6px 0; border-radius: 9px; }}
QScrollBar:vertical {{ width: 8px; background: transparent; }}
QScrollBar::handle:vertical {{ background: #d6d2c8; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenu {{ background: {C['panel']}; border: 1px solid {C['line']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C['accent_soft']}; color: {C['ink']}; }}
QSplitter::handle {{ background: {C['bg']}; }}
QWidget#assist {{ background: {C['bg']}; }}
QTextBrowser {{ background: {C['panel']}; border: 1px solid {C['line']}; border-radius: 12px; padding: 4px; }}
QPushButton#mic {{ background: {C['accent']}; color: white; border: none; border-radius: 26px; min-width: 52px; max-width: 52px; min-height: 52px; max-height: 52px; font-size: 18pt; padding: 0; }}
QPushButton#mic:hover {{ background: #c97a00; }}
QPushButton#mic[rec="true"] {{ background: {C['red']}; }}
QLineEdit#ctitle {{ border: none; border-bottom: 1px solid {C['line']}; border-radius: 0; padding: 4px 2px; font-size: 11pt; font-weight: 600; background: transparent; }}
QLineEdit#dtitle {{ font-size: 13pt; font-weight: 700; padding: 6px 8px; }}
QPushButton#micsmall {{ color: {C['red']}; font-weight: 600; }}
QPushButton#micsmall[rec="true"] {{ background: {C['red']}; color: white; border-color: {C['red']}; }}
QPushButton#voice {{ background: {C['blue_soft']}; color: {C['blue']}; border-color: {C['blue_soft']}; font-weight: 600; }}
QProgressBar {{ background: {C['soft']}; border: none; border-radius: 3px; }}
QProgressBar::chunk {{ background: {C['muted']}; border-radius: 3px; }}
QProgressBar[talk="true"]::chunk {{ background: {C['green']}; }}
QPushButton#kb {{ background: #efe7fb; color: #6b3fb8; border-color: #efe7fb; font-weight: 600; }}
QScrollArea#detscroll, QScrollArea#detscroll > QWidget > QWidget {{ background: transparent; }}
QDialog#gallery {{ background: #111114; }}
QFrame#gtop {{ background: #1b1b1f; border: none; }}
QFrame#gtop QPushButton {{ background: #2a2a30; color: #eee; border: 1px solid #34343b; padding: 5px 10px; }}
QFrame#gtop QPushButton:hover {{ background: #3a3a42; }}
QFrame#gtop QPushButton:checked {{ background: {C['accent']}; color: white; border-color: {C['accent']}; }}
QLabel#gcount {{ color: white; font-weight: 700; font-size: 11pt; padding-right: 8px; }}
QLabel#gcap {{ color: #b9b9c0; }}
QPushButton#gnav {{ background: #111114; color: #ddd; border: none; border-radius: 0; font-size: 26pt; min-width: 46px; max-width: 46px; padding: 0; }}
QPushButton#gnav:hover {{ background: #26262c; color: white; }}
QFrame#gtext {{ background: #18181b; border: none; }}
QFrame#gtext QLabel {{ color: #eee; }}
QFrame#gtext QPlainTextEdit {{ background: #202024; color: #e6e6ea; border: 1px solid #2e2e34; border-radius: 8px; font-size: 10.5pt; padding: 6px; }}
QListWidget#gstrip {{ background: #1b1b1f; border: none; padding: 4px; }}
QListWidget#gstrip::item {{ border: 2px solid transparent; border-radius: 6px; margin: 1px; }}
QListWidget#gstrip::item:selected {{ border-color: {C['accent']}; background: #2a2a30; }}
QSplitter#gsplit, QWidget#gstage {{ background: #111114; }}
QSplitter#gsplit::handle {{ background: #2a2a30; }}
"""


def pin_in_ram(min_mb=300, max_mb=2048):
    """Giữ tối thiểu min_mb bộ nhớ của app luôn trong RAM. Bộ nhớ ảo (pagefile) máy này nằm trên ổ HDD E: hay nghẽn:
    để yên một lúc Windows đẩy app ra đó, cú bấm đầu tiên phải chờ đọc lại 5 đến 10 giây (đo được 26/09)."""
    try:
        k = ctypes.windll.kernel32
        k.SetProcessWorkingSetSizeEx.argtypes = [W.HANDLE, ctypes.c_size_t, ctypes.c_size_t, W.DWORD]
        ok = k.SetProcessWorkingSetSizeEx(k.GetCurrentProcess(), min_mb << 20, max_mb << 20, 0x1 | 0x8)  # MIN cứng, MAX mềm
        if not ok:
            S.log("không giữ được app trong RAM, mã lỗi", ctypes.GetLastError())
    except Exception as e:
        S.log("không giữ được app trong RAM:", repr(e))


def main():
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DG.IdeaNote")
    pin_in_ram()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(ICON))
    # chỉ 1 bản chạy: mở lần 2 thì gọi bản đang chạy hiện lên
    sock = QLocalSocket()
    sock.connectToServer("IdeaNoteApp")
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.waitForBytesWritten(300)
        return
    QLocalServer.removeServer("IdeaNoteApp")
    single = QLocalServer()
    single.listen("IdeaNoteApp")
    if not S.start():
        QMessageBox.warning(None, "Idea Note", "Cổng 41900 đang bị chiếm (có thể bản Idea Note cũ đang chạy).\n"
                            "App vẫn mở nhưng điện thoại / nhắc hẹn có thể không hoạt động.")
    win = Main()
    single.newConnection.connect(lambda: (single.nextPendingConnection(), win.show_main()))
    hk = HotkeyFilter({1: win.quick, 2: win.voice_hotkey})
    app.installNativeEventFilter(hk)
    taken = [name for hid, vk, name in ((1, ord("N"), "Ctrl+Alt+N"), (2, ord("V"), "Ctrl+Alt+V"))
             if not ctypes.windll.user32.RegisterHotKey(None, hid, 0x0002 | 0x0001 | 0x4000, vk)]
    if taken:  # phím đã bị app khác giữ -> báo thay vì im lặng
        S.log("phím tắt bị app khác giữ:", taken)
        QTimer.singleShot(3000, lambda: win.tray.showMessage("Idea Note", "Phím tắt " + ", ".join(taken) +
                                                            " đang bị app khác dùng — hãy mở bằng icon / bong bóng.", QIcon(ICON), 6000))
    if "--tray" in sys.argv:
        win.sync_bubble()
    else:
        win.show_main()
        win.txt.setFocus()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
