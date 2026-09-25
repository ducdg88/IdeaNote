# -*- coding: utf-8 -*-
"""Idea Note — ghi chú nhanh trên PC (chữ + ảnh, nhắc hẹn, tiến độ).

Chạy nền ở cổng 41900. Máy tính mở http://127.0.0.1:41900,
điện thoại cùng Wi-Fi quét mã QR trong app (có khóa riêng).
Dữ liệu nằm trong thư mục data/ cạnh file này, tự sao lưu mỗi ngày.
"""
import base64
import datetime as dt
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

# app .exe đặt IDEANOTE_HOME để dùng chung thư mục dữ liệu với bản chạy bằng Python
ROOT = os.environ.get("IDEANOTE_HOME") or os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")
IMG = os.path.join(DATA, "img")
BACKUP = os.path.join(DATA, "backup")
NOTES_F = os.path.join(DATA, "notes.json")
CONF_F = os.path.join(DATA, "config.json")
PORT = int(os.environ.get("IDEANOTE_PORT") or 41900)
VERSION = "1.1.0"
LOCK = threading.RLock()
STATIC = {"/icon.png": "icon.png", "/manifest.webmanifest": "manifest.webmanifest"}

for d in (DATA, IMG, BACKUP):
    os.makedirs(d, exist_ok=True)

# pythonw không có console: dồn mọi lỗi vào file log thay vì văng mất
if sys.stderr is None or sys.stdout is None:
    _logf = open(os.path.join(DATA, "server.log"), "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = _logf


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, file=sys.stderr, flush=True)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        bad = path + ".hong-" + time.strftime("%Y%m%d%H%M%S")
        shutil.copy2(path, bad)
        log("file hỏng, đã giữ bản sao ở", bad)
        return default


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


NOTES = load(NOTES_F, [])
CONF = load(CONF_F, {})
CONF.setdefault("key", secrets.token_urlsafe(9))
CONF.setdefault("email", "")
save(CONF_F, CONF)
BOOT = time.strftime("%Y%m%d%H%M%S")  # đổi mỗi lần server khởi động -> trang tự tải lại bản mới


REV = [0]      # tăng mỗi lần dữ liệu đổi -> app biết mà vẽ lại
ON_DUE = None  # app gán hàm(note) để tự hiện cửa sổ nhắc thay cho toast
ON_VOICE = None  # app gán hàm(bytes, đuôi file) -> dict: nhận ghi âm từ điện thoại, nghe bằng Whisper rồi lưu


def persist():
    REV[0] += 1
    save(NOTES_F, NOTES)
    day = os.path.join(BACKUP, f"notes-{dt.date.today():%Y-%m-%d}.json")
    if not os.path.exists(day):
        shutil.copy2(NOTES_F, day)
        olds = sorted(f for f in os.listdir(BACKUP) if f.startswith("notes-"))
        for f in olds[:-30]:
            os.remove(os.path.join(BACKUP, f))


def now_local():
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M")


DATA_URL = re.compile(r"^data:image/(png|jpe?g|gif|webp);base64,(.+)$", re.S)


def store_images(items):
    names = []
    for s in items or []:
        m = DATA_URL.match(s or "")
        if not m:
            continue
        ext = "jpg" if m.group(1).startswith("jp") else m.group(1)
        name = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.{ext}"
        with open(os.path.join(IMG, name), "wb") as f:
            f.write(base64.b64decode(m.group(2)))
        names.append(name)
    return names


def drop_images(names):
    for n in names:
        try:
            os.remove(os.path.join(IMG, os.path.basename(n)))
        except OSError:
            pass


FIELDS = {"title": str, "text": str, "date": str, "status": str, "urgent": bool, "project": str, "repeat": str,
          "progress": int, "remind_at": (str, type(None)), "deleted": bool, "proof": str}
STATUSES = {"note", "todo", "doing", "verify", "done"}  # verify = đã làm, chờ kiểm chứng làm được thật chưa
REPEATS = {"": 0, "daily": 1, "weekly": 7}  # nhắc lặp lại: số ngày giữa 2 lần


SPEC7_HEAD = "1, VẤN ĐỀ"
HINT = "↳"  # dòng câu hỏi gợi ý trong khung: điền đè lên, khi chấm spec thì bỏ qua
SPEC7 = """1, VẤN ĐỀ
↳ Đang đau ở đâu? Ai đau? Không làm thì mất gì?
• 

2, CHÂN DUNG NGƯỜI DÙNG
↳ Ai dùng? Họ đang dùng gì thay thế? Họ sợ gì?
• 

3, CÁCH HOẠT ĐỘNG
↳ Viết mục 4 trước. Người dùng làm bước 1, 2, 3 thế nào?
• 

4, KHÔNG LÀM
↳ Cái gì hấp dẫn nhưng để sau? Cái gì tuyệt đối không đụng?
✕ 

5, DỮ LIỆU
↳ Lưu gì, ở đâu, ai xem, ai sửa? Con số mốc hiện tại là bao nhiêu?
• 

6, ĐỊNH NGHĨA HOÀN THÀNH
↳ Mỗi dòng là 1 việc: làm gì, và bằng chứng nào cho thấy xong. Có ngày thì ghi dd/mm.
[ ] Làm [ ] Verify · 
    Verify: 

7, DỄ HỎNG Ở ĐÂU
↳ Chỗ nào hay gãy? Dấu hiệu nhận ra sớm? Chặn trước thế nào?
! 

↳ Mỗi mục 1 đến 5 dòng. Chưa biết thì ghi "?" để nhớ còn phải chốt.
"""  # khung spec 1 trang: mục 4 là mục hay bị bỏ, và là thứ làm việc tự phình ra
SPEC7_NAMES = ["van de", "chan dung", "cach hoat dong", "khong lam", "du lieu", "dinh nghia hoan thanh", "de hong"]
SPEC7_MAX_LINES = 5
_SPEC_HEAD_RE = re.compile(r"^\s*([1-7])\s*[,.:)]\s*(.+?)\s*$")
_SPEC_RULE_RE = re.compile(r"^\s*[━─=_\-]{5,}\s*$")
_SPEC_MARK_RE = re.compile(r"^[\s•✕!\-*·]*(?:\[[ xX]\]\s*(?:Làm|Verify)?\s*)*(?:Verify\s*:)?[\s·]*", re.I)
_SPEC_OPEN_RE = re.compile(r"(?:^|[\s:(])\?(?:\s|$)")
_SPEC_DATE_RE = re.compile(r"\s*(?:(?:T[2-7]|CN|Thứ\s*\S+)\s+)?(\d{1,2})/(\d{1,2})(?!\d)", re.I)


def _fold(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


def spec7_parse(text):
    """Tách 7 mục -> {số mục: [các dòng]}. Dòng kẻ ━━━ kết thúc mục đang có nội dung (phần phụ lục sau đó không tính)."""
    out, cur = {}, None
    for line in (text or "").splitlines():
        m = _SPEC_HEAD_RE.match(line)
        if m and int(m.group(1)) not in out and _fold(m.group(2)).startswith(SPEC7_NAMES[int(m.group(1)) - 1]):
            cur = int(m.group(1))
            out[cur] = []
        elif cur and _SPEC_RULE_RE.match(line):
            if any(spec7_content(x) for x in out[cur]):
                cur = None
        elif cur:
            out[cur].append(line)
    return out


def spec7_content(line):
    """Phần chữ thật của 1 dòng (bỏ câu gợi ý ↳, dấu đầu dòng •, ✕, !, ô [ ] Làm [ ] Verify, chữ Verify:)."""
    t = line.strip()
    return "" if t.startswith(HINT) else _SPEC_MARK_RE.sub("", t).strip()


def spec7_check(text):
    """Chấm spec theo các mẹo: đủ 7 mục, mục 4 không trống, mỗi mục tối đa 5 dòng, dấu ? chưa chốt,
    mỗi dòng mục 6 có Verify. Không phải spec (ít hơn 4 mục) -> None."""
    secs = spec7_parse(text)
    if len(secs) < 4:
        return None
    filled = {i: [x for x in secs.get(i, []) if spec7_content(x)] for i in range(1, 8)}
    items = spec7_items(text)
    return {"found": sorted(secs), "empty": [i for i in range(1, 8) if not filled[i]],
            # mục 6 đếm theo số việc: dòng "Verify:" đi kèm mỗi việc không tính là dòng riêng
            "long": [(i, len(items) if i == 6 else len(v)) for i, v in filled.items()
                     if (len(items) if i == 6 else len(v)) > SPEC7_MAX_LINES],
            "open": sum(1 for v in filled.values() for x in v if _SPEC_OPEN_RE.search(spec7_content(x))),
            "items": len(items), "no_verify": sum(1 for it in items if not it["verify"]),
            "lines": sum(len(v) for v in filled.values())}


def spec7_items(text):
    """Các dòng '[ ] Làm ... ' ở mục 6 -> [{title, verify, date, who}] (dòng 'Verify: ...' ngay dưới là bằng chứng)."""
    out = []
    for line in spec7_parse(text).get(6, []):
        body = spec7_content(line)
        if re.match(r"^\s*[•✕!\-*·]*\s*\[[ xX]\]", line) and body:
            out.append({"title": body, "verify": "", "date": "", "who": ""})
        elif out and re.match(r"^\s*Verify\s*:", line, re.I) and body and not out[-1]["verify"]:
            out[-1]["verify"] = body
    today = dt.date.today()
    for it in out:
        t = it["title"]
        m = re.search(r"\(([^()]{1,40})\)\s*$", t)  # "(Leader)" cuối dòng = người làm
        if m:
            it["who"], t = m.group(1).strip(), t[:m.start()].strip()
        d = _SPEC_DATE_RE.match(t)  # chỉ ngày ở đầu dòng mới là hạn ("T6 25/09 · Gửi tin"), ngày giữa câu là nội dung
        if d:
            try:
                day = dt.date(today.year, int(d.group(2)), int(d.group(1)))
                if (today - day).days > 180:  # 05/01 ghi vào tháng 12 -> năm sau
                    day = day.replace(year=today.year + 1)
                it["date"] = f"{day}"
                t = t[d.end():].lstrip(" ·:-") or t
            except ValueError:
                pass
        it["title"] = " ".join(t.strip(" ·").split())
    return out


def spec7_tasks(nid):
    """Tạo việc từ mục 6 của ghi chú spec: mỗi dòng 1 việc (cùng dự án, có dòng Verify). Bấm lại không tạo trùng.
    Trả về (số đã tạo, số bỏ qua vì đã có)."""
    with LOCK:
        src = next((n for n in NOTES if n["id"] == nid), None)
        if not src:
            return 0, 0
        have = {n.get("spec_src") for n in NOTES if not n.get("deleted")}
        have |= {(n.get("project") or "", _fold(n.get("title") or "")) for n in NOTES if not n.get("deleted")}
        head = (src.get("title") or (src.get("text") or "").strip().split("\n")[0])[:80]
        made = skip = 0
        for it in spec7_items(src.get("text")):
            key = f"{nid}:{_fold(it['title'])}"
            if key in have or ((src.get("project") or ""), _fold(it["title"])) in have:
                skip += 1
                continue
            body = {"title": it["title"][:120], "status": "todo", "project": src.get("project") or "",
                    "text": (f"Người làm: {it['who']}\n" if it["who"] else "") + f"Verify: {it['verify'] or '?'}\n\nTừ spec: {head}"}
            if it["date"]:
                body["date"] = it["date"]
            n = new_note(body)
            n["spec_src"] = key
            made += 1
        if made:
            persist()
        return made, skip


def finish_status(note):
    """Tick xong: việc thường sang "chờ verify" (có bằng chứng mới tính là xong); nhắc lặp thì xong luôn để hẹn buổi sau."""
    return "done" if note.get("repeat") else "verify"


def next_occurrence(remind_at, repeat, after=None):
    """Lần nhắc kế tiếp (sau `after`, mặc định là bây giờ) của một nhắc lặp lại."""
    step = dt.timedelta(days=REPEATS.get(repeat or "", 0))
    t = dt.datetime.strptime(remind_at, "%Y-%m-%dT%H:%M")
    after = after or dt.datetime.now()
    if not step:
        return remind_at
    while t <= after:
        t += step
    return t.strftime("%Y-%m-%dT%H:%M")


def roll_forward(note, after=None):
    """Nhắc lặp: chuyển sang buổi kế tiếp, mở lại việc (không bao giờ nằm ở tab Hoàn thành)."""
    note["remind_at"] = next_occurrence(note["remind_at"], note.get("repeat"), after)
    note["date"] = note["remind_at"][:10]
    note["status"] = "todo"
    note["progress"] = 0
    note["reminded"] = False


def apply(note, body):
    old_remind = note.get("remind_at")
    for k, t in FIELDS.items():
        if k in body and isinstance(body[k], t):
            note[k] = body[k]
    if not note.get("remind_at"):
        note["remind_at"] = None
    note["project"] = " ".join((note.get("project") or "").split())[:60]
    note["title"] = " ".join((note.get("title") or "").split())[:120]  # mục chính của ghi chú
    if note.get("status") not in STATUSES:
        note["status"] = "note"
    note["progress"] = max(0, min(100, int(note.get("progress") or 0)))
    if note["remind_at"] != old_remind:
        note["reminded"] = False  # đổi giờ nhắc thì nhắc lại
    if "status" in body:
        if note["status"] == "done":
            note["progress"] = 100
            note["done_at"] = dt.datetime.now().isoformat(timespec="seconds")  # để tab Hoàn thành xếp theo ngày xong
        elif note["status"] == "verify":
            note["progress"] = 100
            note["did_at"] = dt.datetime.now().isoformat(timespec="seconds")  # lúc tick "đã làm"
        elif note["progress"] == 100:
            note["progress"] = 0
    elif "progress" in body and note["progress"] == 100 and note["status"] in ("todo", "doing"):
        note["status"] = finish_status(note)
        note["did_at" if note["status"] == "verify" else "done_at"] = dt.datetime.now().isoformat(timespec="seconds")
    elif "progress" in body and 0 < note["progress"] < 100 and note["status"] == "todo":
        note["status"] = "doing"
    if note.get("repeat") not in REPEATS:
        note["repeat"] = ""
    if note.get("repeat") and not note.get("remind_at"):
        note["repeat"] = ""  # bỏ giờ nhắc thì bỏ luôn lặp lại
    if (note.get("repeat") and ("remind_at" in body or "repeat" in body)
            and note["remind_at"] < now_local() and note["status"] != "done"):
        note["remind_at"] = next_occurrence(note["remind_at"], note["repeat"])  # vừa đặt mà giờ đã qua -> buổi kế tiếp
        note["date"] = note["remind_at"][:10]
        note["reminded"] = False
    if note.get("repeat") and note["status"] == "done":  # xong buổi này -> hẹn buổi SAU buổi đang hẹn
        note["last_done"] = dt.datetime.now().isoformat(timespec="seconds")
        note.pop("done_at", None)
        cur = dt.datetime.strptime(note["remind_at"], "%Y-%m-%dT%H:%M")
        roll_forward(note, after=max(cur, dt.datetime.now()))
    note["updated"] = dt.datetime.now().isoformat(timespec="seconds")


def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips


# ---------- thông báo Windows ----------
PS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$d = New-Object Windows.Data.Xml.Dom.XmlDocument
$d.LoadXml(@'
__XML__
'@)
$t = [Windows.UI.Notifications.ToastNotification]::new($d)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($t)
"""


def toast(title, body, note_id=""):
    url = f"http://127.0.0.1:{PORT}/#n={note_id}"
    q = lambda s: escape(" ".join(str(s).split())[:220], {'"': "&quot;"})
    xml = (f'<toast launch="{q(url)}" activationType="protocol" scenario="reminder">'
           f'<visual><binding template="ToastGeneric"><text>{q(title)}</text><text>{q(body)}</text></binding></visual>'
           f'<actions><action content="Mở ghi chú" activationType="protocol" arguments="{q(url)}"/>'
           f'<action content="" activationType="system" arguments="dismiss"/></actions>'
           f'<audio src="ms-winsoundevent:Notification.Reminder"/></toast>')
    script = PS_TOAST.replace("__XML__", xml)
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                      "-EncodedCommand", enc], creationflags=0x08000000)


def reminder_loop():
    while True:
        try:
            due = []
            with LOCK:
                now = now_local()
                late = (dt.datetime.now() - dt.timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
                rolled = False
                for n in NOTES:  # nhắc lặp quá giờ 3 tiếng mà chưa tick xong -> sang buổi kế tiếp
                    if n.get("repeat") and n.get("remind_at") and not n.get("deleted") and n["remind_at"] < late:
                        roll_forward(n)
                        rolled = True
                if rolled:
                    persist()
                for n in NOTES:
                    if (n.get("remind_at") and not n.get("reminded") and not n.get("deleted")
                            and n.get("status") not in ("done", "verify") and n["remind_at"] <= now):
                        n["reminded"] = True
                        due.append(n)
                if due:
                    persist()
            for n in due:
                head = ("🔴 GẤP · " if n.get("urgent") else "") + "⏰ Nhắc hẹn " + n["remind_at"][11:16]
                if ON_DUE:
                    ON_DUE(n)
                else:
                    toast(head, n.get("text") or "(ghi chú có ảnh)", n["id"])
                try:  # email "ĐẾN GIỜ" -> app Gmail trên điện thoại báo
                    import mailer
                    mailer.on_due(n)
                except Exception as e:
                    log("email đến giờ lỗi:", repr(e))
                log("đã nhắc", n["id"])
        except Exception as e:  # không để luồng nhắc chết
            log("lỗi luồng nhắc:", repr(e))
        time.sleep(15)


def ics_for(n):
    def esc(s):
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    text = n.get("text") or "Ghi chú"
    title = esc(text.strip().split("\n")[0][:80])
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//IdeaNote//VN", "BEGIN:VEVENT",
             f"UID:{n['id']}@ideanote", f"DTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}"]
    if n.get("remind_at"):
        start = dt.datetime.strptime(n["remind_at"], "%Y-%m-%dT%H:%M")
        lines += [f"DTSTART:{start:%Y%m%dT%H%M%S}", f"DTEND:{start + dt.timedelta(minutes=30):%Y%m%dT%H%M%S}"]
    else:
        d = dt.datetime.strptime(n.get("date") or f"{dt.date.today()}", "%Y-%m-%d")
        lines += [f"DTSTART;VALUE=DATE:{d:%Y%m%d}", f"DTEND;VALUE=DATE:{d + dt.timedelta(days=1):%Y%m%d}"]
    lines += [f"SUMMARY:{title}", f"DESCRIPTION:{esc(text)}",
              "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{title}", "TRIGGER:-PT0M", "END:VALARM",
              "END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


# ---------- HTTP ----------
class H(BaseHTTPRequestHandler):
    server_version = "IdeaNote"

    def log_message(self, *a):
        pass

    def local(self):
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    def authed(self):
        if self.local():
            return True
        if parse_qs(urlparse(self.path).query).get("k", [""])[0] == CONF["key"]:
            return True
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return "ink" in c and c["ink"].value == CONF["key"]

    def send(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None, cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def note_id(self, prefix):
        return urlparse(self.path).path[len(prefix):].strip("/")

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p in ("/", "/index.html"):
            hdr = {}
            if parse_qs(u.query).get("k", [""])[0] == CONF["key"]:
                hdr["Set-Cookie"] = f"ink={CONF['key']}; Max-Age=31536000; Path=/; HttpOnly; SameSite=Lax"
            with open(os.path.join(ROOT, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8", hdr)
        if p in STATIC:
            fp = os.path.join(ROOT, STATIC[p])
            with open(fp, "rb") as f:
                return self.send(200, f.read(), mimetypes.guess_type(fp)[0] or "application/manifest+json")
        if not self.authed():
            return self.send(401, {"error": "Quét mã QR trên máy tính để mở"})
        if p == "/api/notes":
            import knowledge as K
            with LOCK:
                return self.send(200, {"notes": NOTES, "now": now_local(), "boot": BOOT, "ocr": K.ocr_map()})
        if p == "/api/kb":  # tình trạng kho kiến thức: đã đọc chữ bao nhiêu ảnh, có tìm theo nghĩa không
            import knowledge as K
            return self.send(200, K.status())
        if p == "/api/info":
            info = {"port": PORT, "email": CONF["email"], "local": self.local(), "boot": BOOT, "version": VERSION,
                    "voice": bool(ON_VOICE)}
            if self.local():
                info.update(ips=lan_ips(), key=CONF["key"], data=DATA)
            return self.send(200, info)
        if p.startswith("/img/"):
            fp = os.path.join(IMG, os.path.basename(p[5:]))
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    return self.send(200, f.read(), mimetypes.guess_type(fp)[0] or "image/png", cache=True)
        if p.startswith("/api/ics/"):
            with LOCK:
                n = next((x for x in NOTES if x["id"] == self.note_id("/api/ics/")), None)
            if n:
                return self.send(200, ics_for(n), "text/calendar; charset=utf-8",
                                 {"Content-Disposition": 'attachment; filename="ideanote.ics"'})
        self.send(404, {"error": "không thấy"})

    def do_POST(self):
        if not self.authed():
            return self.send(401, {"error": "Quét mã QR trên máy tính để mở"})
        p = urlparse(self.path).path
        if p == "/api/voice":  # ghi âm từ điện thoại -> máy tính nghe (Whisper trong app) rồi tự lưu
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 30 * 1024 * 1024:
                return self.send(400, {"error": "Đoạn ghi âm trống hoặc quá dài (tối đa ~30 MB)."})
            if not ON_VOICE:
                return self.send(503, {"error": "Mở app Idea Note trên máy tính để nhận dạng giọng nói."})
            ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
            ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
                   "audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/3gpp": "3gp",
                   "audio/amr": "amr"}.get(ctype, "webm")
            try:
                return self.send(200, ON_VOICE(self.rfile.read(n), ext))
            except Exception as e:
                log("ghi âm điện thoại lỗi:", repr(e))
                return self.send(500, {"error": f"Máy tính chưa nghe được đoạn này: {e}"})
        b = self.body()
        if p == "/api/notes":
            return self.send(200, new_note(b, "pc" if self.local() else "phone"))
        if p == "/api/ask":  # hỏi kho kiến thức: trả lời + ghi chú nguồn
            import knowledge as K
            return self.send(200, K.ask(b.get("q") or "", b.get("project") if isinstance(b.get("project"), str) else None))
        if p == "/api/config":
            if isinstance(b.get("email"), str):
                CONF["email"] = b["email"].strip()
            save(CONF_F, CONF)
            return self.send(200, {"ok": True})
        if p == "/api/rename-project":
            rename_project(b.get("old") or "", b.get("new") or "")
            return self.send(200, {"ok": True})
        if p == "/api/export" and self.local():
            return self.send(200, {"ok": True, "folder": export_pack(b.get("title"), b.get("text") or "")})
        if p == "/api/test-toast" and self.local():
            toast("✅ Idea Note", "Thông báo nhắc hẹn hoạt động tốt!")
            return self.send(200, {"ok": True})
        if p == "/api/open-folder" and self.local():
            os.startfile(DATA)
            return self.send(200, {"ok": True})
        self.send(404, {"error": "không thấy"})

    def do_PUT(self):
        if not self.authed():
            return self.send(401, {"error": "Quét mã QR trên máy tính để mở"})
        nid = self.note_id("/api/notes/")
        b = self.body()
        with LOCK:
            n = next((x for x in NOTES if x["id"] == nid), None)
            if not n:
                return self.send(404, {"error": "không thấy"})
            apply(n, b)
            if isinstance(b.get("images"), list):
                keep = [x for x in n.get("images", []) if x in b["images"]]
                drop_images([x for x in n.get("images", []) if x not in keep])
                n["images"] = keep
            n["images"] = n.get("images", []) + store_images(b.get("add_images"))
            persist()
            return self.send(200, n)

    def do_DELETE(self):
        if not self.authed():
            return self.send(401, {"error": "Quét mã QR trên máy tính để mở"})
        nid = self.note_id("/api/notes/")
        with LOCK:
            n = next((x for x in NOTES if x["id"] == nid), None)
            if not n:
                return self.send(404, {"error": "không thấy"})
            if n.get("deleted"):  # đã ở thùng rác -> xóa hẳn
                NOTES.remove(n)
                drop_images(n.get("images", []))
            else:
                n["deleted"] = True
                n["updated"] = dt.datetime.now().isoformat(timespec="seconds")
            persist()
            return self.send(200, {"ok": True})


# ---------- thao tác dữ liệu dùng chung cho web + app ----------
def new_note(b, source="pc"):
    now = dt.datetime.now()
    n = {"id": now.strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:4], "text": "",
         "date": f"{now.date()}", "status": "note", "urgent": False, "progress": 0, "project": "",
         "remind_at": None, "reminded": False, "deleted": False,
         "created": now.isoformat(timespec="seconds"), "from": source}
    apply(n, b)
    n["images"] = store_images(b.get("add_images")) + [x for x in b.get("image_files", []) if x]
    with LOCK:
        NOTES.append(n)
        persist()
    return n


def save_image_bytes(data, ext="png"):
    """Lưu ảnh (bytes) vào data/img, trả về tên file — app dùng khi dán ảnh."""
    name = f"{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.{ext}"
    with open(os.path.join(IMG, name), "wb") as f:
        f.write(data)
    return name


def rename_project(old, new):
    old, new = old.strip(), " ".join(new.split())[:60]
    with LOCK:
        for n in NOTES:
            if n.get("project", "") == old:
                n["project"] = new
        persist()


def export_pack(title, text):
    """Gói ngữ cảnh: CONTEXT.md + ảnh chép kèm, mở sẵn thư mục để kéo thả vào Claude."""
    title = re.sub(r'[\\/:*?"<>|]+', " -", title or "Idea Note").strip()[:60] or "Idea Note"
    folder = os.path.join(DATA, "export", f"{title} {dt.datetime.now():%Y-%m-%d %H%M}")
    os.makedirs(folder, exist_ok=True)
    for name in re.findall(r"[\w\-]+\.(?:png|jpg|gif|webp)", text):
        src = os.path.join(IMG, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(folder, name))
    with open(os.path.join(folder, "CONTEXT.md"), "w", encoding="utf-8") as f:
        f.write(text.replace(IMG + os.sep, ""))
    os.startfile(folder)
    return folder


HTTPS_DIR = os.path.join(DATA, "https")


def ensure_cert():
    """Chứng chỉ HTTPS tự ký cho mạng nhà. Trình duyệt điện thoại chỉ cho dùng mic khi trang chạy HTTPS."""
    import ipaddress
    cert, key = os.path.join(HTTPS_DIR, "cert.pem"), os.path.join(HTTPS_DIR, "key.pem")
    ips = sorted(set(lan_ips() + ["127.0.0.1"]))
    stamp = os.path.join(HTTPS_DIR, "ips.txt")
    if os.path.exists(cert) and os.path.exists(stamp) and open(stamp).read() == ",".join(ips):
        return cert, key
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    os.makedirs(HTTPS_DIR, exist_ok=True)
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Idea Note (may tinh nha)")])
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName("localhost")] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=3650))
         .add_extension(x509.SubjectAlternativeName(san), critical=False).sign(k, hashes.SHA256()))
    with open(key, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                serialization.NoEncryption()))
    with open(cert, "wb") as f:
        f.write(c.public_bytes(serialization.Encoding.PEM))
    with open(stamp, "w") as f:
        f.write(",".join(ips))
    log("đã tạo chứng chỉ HTTPS cho", ips)
    return cert, key


class DualServer(ThreadingHTTPServer):
    """Một cổng 41900 phục vụ cả http:// lẫn https:// (nhìn byte đầu: 0x16 = bắt tay TLS) — khỏi mở thêm cổng tường lửa."""
    ssl_ctx = None

    def process_request_thread(self, request, client_address):
        try:
            if self.ssl_ctx:
                request.settimeout(10)
                first = request.recv(1, socket.MSG_PEEK)
                if first == b"\x16":
                    request = self.ssl_ctx.wrap_socket(request, server_side=True)
                request.settimeout(None)
        except Exception:
            self.shutdown_request(request)
            return
        super().process_request_thread(request, client_address)


def start():
    """Bật máy chủ (cho điện thoại) + luồng nhắc ở nền. Trả về False nếu cổng đã có bản khác chạy."""
    try:
        srv = DualServer(("0.0.0.0", PORT), H)
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*ensure_cert())
            srv.ssl_ctx = ctx
        except Exception as e:  # không có HTTPS thì vẫn chạy http như cũ
            log("không bật được HTTPS:", repr(e))
    except OSError:
        log("cổng", PORT, "đang bận — Idea Note đã chạy sẵn")
        return False
    threading.Thread(target=reminder_loop, daemon=True).start()
    try:  # kho kiến thức: đọc chữ trong ảnh + tìm theo nghĩa, chạy nền
        import knowledge
        knowledge.S = sys.modules[__name__]
        knowledge.start()
    except Exception as e:
        log("không bật được kho kiến thức:", repr(e))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:  # email + lời mời Google Lịch cho nhắc hẹn
        import mailer
        mailer.S = sys.modules[__name__]
        mailer.start()
    except Exception as e:
        log("không bật được email nhắc hẹn:", repr(e))
    log("Idea Note chạy ở cổng", PORT)
    return True


def main():
    if start():
        while True:
            time.sleep(3600)


if __name__ == "__main__":
    main()
