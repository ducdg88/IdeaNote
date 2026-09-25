# -*- coding: utf-8 -*-
"""Nhắc hẹn qua Gmail + Google Lịch.

- Đặt / đổi giờ nhắc  -> gửi email kèm lời mời lịch (.ics METHOD:REQUEST) -> Google Lịch thêm sự kiện, điện thoại tự báo.
- Tới giờ             -> gửi email "⏰ ĐẾN GIỜ" -> app Gmail trên điện thoại báo ngay.
- Xong / xoá / bỏ nhắc -> gửi huỷ (METHOD:CANCEL) để sự kiện biến khỏi lịch.
Gửi bằng Gmail SMTP + Mật khẩu ứng dụng do người dùng tự dán vào Cài đặt; mật khẩu mã hoá bằng Windows DPAPI.
"""
import base64
import ctypes
import ctypes.wintypes as W
import datetime as dt
import html
import os
import smtplib
import threading
import time
import urllib.parse
import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import server as S

SETTLE_S = 20          # giờ nhắc đứng yên 20 giây mới gửi (đang chỉnh qua lại thì không gửi dồn)
LAST = {"error": "", "sent": ""}
_pending = {}          # id -> (remind_at, lần đầu thấy)
_fail = {}             # id -> (số lần lỗi, thời điểm được thử lại) — lỗi thì giãn dần 1 / 5 / 15 / 60 phút, khỏi bị Gmail chặn
BACKOFF = [60, 300, 900, 3600]
_lock = threading.Lock()


# ---------- mã hoá mật khẩu bằng Windows (DPAPI) ----------
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", W.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(b):
    buf = ctypes.create_string_buffer(b, len(b))
    return _Blob(len(b), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def protect(text):
    inp, _keep = _blob(text.encode("utf-8"))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), ctypes.c_wchar_p("IdeaNote"), None, None, None,
                                                  1, ctypes.byref(out)):
        raise OSError("Không mã hoá được mật khẩu")
    data = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return base64.b64encode(data).decode()


def unprotect(b64):
    inp, _keep = _blob(base64.b64decode(b64))
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out)):
        raise OSError("Không giải mã được mật khẩu (đã đổi tài khoản Windows?)")
    data = ctypes.string_at(out.pbData, out.cbData)
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return data.decode("utf-8")


# ---------- cấu hình ----------
def configured():
    c = S.CONF
    return bool(c.get("gmail") and c.get("gmail_pw"))


def set_account(gmail, app_password=None):
    S.CONF["gmail"] = (gmail or "").strip()
    if app_password:
        S.CONF["gmail_pw"] = protect(app_password.replace(" ", "").strip())
    S.save(S.CONF_F, S.CONF)


def _title(n):
    t = (n.get("title") or "").strip() or ((n.get("text") or "").strip().splitlines() or [""])[0]
    return (t or "Ghi chú Idea Note")[:90]


def _utc(local_str, minutes=0):
    t = dt.datetime.strptime(local_str, "%Y-%m-%dT%H:%M") + dt.timedelta(minutes=minutes)
    return t.astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # giờ máy (VN) -> UTC


def _esc(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def ics(n, method, seq, to):
    title, text = _title(n), (n.get("text") or "").strip()
    lines = ["BEGIN:VCALENDAR", "PRODID:-//IdeaNote//VN", "VERSION:2.0", "CALSCALE:GREGORIAN", f"METHOD:{method}",
             "BEGIN:VEVENT", f"UID:{n['id']}@ideanote", f"SEQUENCE:{seq}",
             f"DTSTAMP:{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}",
             f"DTSTART:{_utc(n['remind_at'])}", f"DTEND:{_utc(n['remind_at'], 30)}",
             *([f"RRULE:FREQ={'WEEKLY' if n['repeat'] == 'weekly' else 'DAILY'}"] if n.get("repeat") else []),
             f"SUMMARY:{_esc(('🔴 ' if n.get('urgent') else '') + title)}",
             f"DESCRIPTION:{_esc(text + (chr(10) + 'Dự án: ' + n['project'] if n.get('project') else ''))}",
             "ORGANIZER;CN=Idea Note:mailto:idea-note@invalid",
             f"ATTENDEE;CN={to};ROLE=REQ-PARTICIPANT;PARTSTAT=ACCEPTED;RSVP=FALSE:mailto:{to}",
             f"STATUS:{'CANCELLED' if method == 'CANCEL' else 'CONFIRMED'}", "TRANSP:OPAQUE",
             "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(title)}", "TRIGGER:-PT10M", "END:VALARM",
             "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_esc(title)}", "TRIGGER:PT0M", "END:VALARM",
             "END:VEVENT", "END:VCALENDAR"]
    # dòng .ics dài quá 75 byte phải gập lại
    out = []
    for ln in lines:
        b = ln.encode("utf-8")
        while len(b) > 74:
            cut = 74
            while cut and (b[cut] & 0xC0) == 0x80:
                cut -= 1
            out.append(b[:cut].decode("utf-8"))
            b = b" " + b[cut:]
        out.append(b.decode("utf-8"))
    return "\r\n".join(out) + "\r\n"


def gcal_link(n):
    return ("https://calendar.google.com/calendar/render?action=TEMPLATE&text=" + urllib.parse.quote(_title(n))
            + f"&dates={_utc(n['remind_at'])}/{_utc(n['remind_at'], 30)}"
            + "&details=" + urllib.parse.quote((n.get("text") or "")[:1500])
            + ("&recur=" + urllib.parse.quote("RRULE:FREQ=" + ("WEEKLY" if n["repeat"] == "weekly" else "DAILY"))
               if n.get("repeat") else ""))


def _when_vi(n):
    t = dt.datetime.strptime(n["remind_at"], "%Y-%m-%dT%H:%M")
    thu = ["Thứ hai", "Thứ ba", "Thứ tư", "Thứ năm", "Thứ sáu", "Thứ bảy", "Chủ nhật"][t.weekday()]
    return f"{t:%H:%M} · {thu} {t:%d/%m/%Y}"


KINDS = {  # màu dải đầu thư + nhãn
    "invite": ("#2f6fdb", "📅 LỊCH HẸN MỚI", "📅"),
    "preview": ("#2f6fdb", "📅 LỊCH HẸN MỚI", "📅"),
    "due": ("#d93636", "⏰ ĐẾN GIỜ RỒI", "⏰"),
    "cancel": ("#8a8a93", "❌ ĐÃ HUỶ LỊCH", "❌"),
    "test": ("#1f9d55", "✅ KẾT NỐI THÀNH CÔNG", "✅"),
}
STATUS_VI = {"note": "💡 Ý tưởng / ghi chú", "todo": "⬜ Chưa làm", "doing": "🔄 Đang làm", "verify": "⏳ Chờ verify",
             "done": "✅ Hoàn thành"}


def repeat_vi(n):
    t = dt.datetime.strptime(n["remind_at"], "%Y-%m-%dT%H:%M")
    if n.get("repeat") == "daily":
        return f"Hàng ngày lúc {t:%H:%M}"
    thu = ["thứ hai", "thứ ba", "thứ tư", "thứ năm", "thứ sáu", "thứ bảy", "chủ nhật"][t.weekday()]
    return f"Hàng tuần · {thu} lúc {t:%H:%M}"


def _left(n):
    """'còn 2 giờ 15 phút nữa' / 'đã quá 5 phút'."""
    t = dt.datetime.strptime(n["remind_at"], "%Y-%m-%dT%H:%M")
    mins = int((t - dt.datetime.now()).total_seconds() // 60)
    a = abs(mins)
    txt = (f"{a // 1440} ngày " if a >= 1440 else "") + (f"{a % 1440 // 60} giờ " if a >= 60 else "") + f"{a % 60} phút"
    return ("còn " + txt + " nữa") if mins > 0 else ("vừa tới giờ" if mins > -2 else "đã quá " + txt)


def _open_link(n):
    ips = S.lan_ips()
    return f"https://{ips[0]}:{S.PORT}/#n={n['id']}" if ips else ""


def _button(href, label, bg, fg="#ffffff", border=None):
    b = border or bg
    return (f'<a href="{html.escape(href)}" style="display:inline-block;background:{bg};color:{fg};border:1px solid {b};'
            f'padding:11px 18px;border-radius:9px;text-decoration:none;font-weight:600;font-size:14px;margin:0 8px 8px 0">'
            f'{html.escape(label)}</a>')


def _html(n, kind, cids):
    color, badge, _ = KINDS[kind]
    E = html.escape
    title = _title(n)
    rows = []
    if n.get("remind_at"):
        when = f"<b style='font-size:16px'>{E(_when_vi(n))}</b>"
        if kind in ("invite", "preview", "due"):
            when += f"<br><span style='color:{color};font-size:13px'>{E(_left(n))}</span>"
        rows.append(("🕒 Thời gian", when))
    if n.get("repeat") and n.get("remind_at"):
        rows.append(("🔁 Lặp lại", E(repeat_vi(n))))
    if n.get("project"):
        rows.append(("📁 Dự án", f"<span style='background:#e6eefc;color:#2f6fdb;border-radius:6px;padding:3px 9px;"
                                 f"font-weight:600;font-size:13px'>{E(n['project'])}</span>"))
    st = STATUS_VI.get(n.get("status") or "", "")
    if n.get("status") == "doing":
        st += f" · {n.get('progress', 0)}%"
    if n.get("urgent"):
        st += (" &nbsp;<span style='background:#fde8e8;color:#d93636;border-radius:6px;padding:2px 8px;"
               "font-weight:700;font-size:12px'>🔴 GẤP</span>")
    if st:
        rows.append(("📌 Trạng thái", st))
    table = "".join(
        f"<tr><td style='padding:7px 0;color:#6b6b72;font-size:13px;width:118px;vertical-align:top'>{k}</td>"
        f"<td style='padding:7px 0;color:#1d1d1f;font-size:14px'>{v}</td></tr>" for k, v in rows)
    text = (n.get("text") or "").strip()
    body = ""
    if text and text != title:
        body = (f"<div style='color:#6b6b72;font-size:12px;text-transform:uppercase;letter-spacing:.5px;margin:0 0 6px'>"
                f"Nội dung ghi chú</div><div style='background:#faf8f4;border-left:4px solid {color};border-radius:8px;"
                f"padding:12px 14px;color:#1d1d1f;font-size:14px;line-height:1.55;white-space:pre-wrap'>{E(text)}</div>")
    imgs = "".join(f"<img src='cid:{c}' alt='ảnh ghi chú' style='display:block;max-width:100%;max-height:280px;width:auto;"
                   f"height:auto;border-radius:10px;border:1px solid #e6e3dc;margin-top:12px'>" for c in cids)
    buttons = ""
    if kind in ("invite", "preview", "due") and n.get("remind_at"):
        buttons += _button(gcal_link(n), "📅 Thêm vào Google Lịch", "#e08a00")
    link = _open_link(n)
    if link and kind != "test":
        buttons += _button(link, "📝 Mở trong Idea Note", "#ffffff", "#1d1d1f", "#e6e3dc")
    intro = {
        "invite": "Lịch hẹn này đã được thêm vào Google Lịch — điện thoại sẽ tự báo trước giờ hẹn.",
        "preview": "Mẫu thư nhắc hẹn Idea Note gửi cho anh (bản xem thử, không thêm vào lịch).",
        "due": "Đã tới giờ hẹn cho việc dưới đây.",
        "cancel": "Việc này đã xong / đã bỏ nhắc nên sự kiện được gỡ khỏi Google Lịch.",
        "test": "Idea Note đã gửi được email. Từ giờ mỗi lần đặt nhắc hẹn sẽ có thư như thế này + lời mời Google Lịch.",
    }[kind]
    return f"""<!doctype html><html><body style="margin:0;padding:0;background:#f6f5f2">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6f5f2">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#ffffff;
 border:1px solid #e6e3dc;border-radius:14px;overflow:hidden;font-family:'Segoe UI',Roboto,Arial,sans-serif">
<tr><td style="background:{color};padding:13px 22px">
 <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
 <td style="color:#ffffff;font-weight:700;font-size:13px;letter-spacing:1px">IDEA NOTE</td>
 <td align="right" style="color:#ffffff;font-weight:700;font-size:13px">{E(badge)}</td></tr></table>
</td></tr>
<tr><td style="padding:22px 22px 4px">
 <div style="color:#6b6b72;font-size:12px;text-transform:uppercase;letter-spacing:.5px">Mục chính</div>
 <div style="color:#1d1d1f;font-size:22px;font-weight:700;line-height:1.3;margin-top:4px">{E(title)}</div>
 <div style="color:#6b6b72;font-size:13px;margin-top:6px">{E(intro)}</div>
</td></tr>
<tr><td style="padding:8px 22px 6px"><table role="presentation" width="100%" cellspacing="0" cellpadding="0"
 style="border-top:1px solid #f1efe9;border-bottom:1px solid #f1efe9">{table}</table></td></tr>
<tr><td style="padding:12px 22px 4px">{body}{imgs}</td></tr>
<tr><td style="padding:14px 22px 14px">{buttons}</td></tr>
<tr><td style="padding:12px 22px 18px;border-top:1px solid #f1efe9;color:#9a9aa3;font-size:12px">
 Gửi tự động từ Idea Note trên máy tính · Tắt trong ⚙️ Cài đặt của app.</td></tr>
</table></td></tr></table></body></html>"""


def _plain(n, kind):
    _, badge, _ = KINDS[kind]
    out = [badge, "", "Mục chính: " + _title(n)]
    if n.get("remind_at"):
        out.append("Thời gian: " + _when_vi(n) + (f" ({_left(n)})" if kind != "cancel" else ""))
    if n.get("repeat") and n.get("remind_at"):
        out.append("Lặp lại: " + repeat_vi(n))
    if n.get("project"):
        out.append("Dự án: " + n["project"])
    if n.get("status"):
        out.append("Trạng thái: " + STATUS_VI.get(n["status"], "") + (" · GẤP" if n.get("urgent") else ""))
    text = (n.get("text") or "").strip()
    if text and text != _title(n):
        out += ["", "Nội dung:", text]
    if n.get("remind_at") and kind != "cancel":
        out += ["", "Thêm vào Google Lịch: " + gcal_link(n)]
    out += ["", "— Gửi tự động từ Idea Note trên máy tính"]
    return "\n".join(out)


def build(n, kind, seq):
    """kind: invite | cancel | due | test | preview"""
    to = S.CONF["gmail"]
    m = EmailMessage()
    m["From"] = f"Idea Note <{to}>"
    m["To"] = to
    m["Message-ID"] = f"<{uuid.uuid4().hex}@ideanote>"
    title = _title(n)
    icon = KINDS[kind][2]
    urgent = "🔴 " if n.get("urgent") else ""
    short = ""
    if n.get("remind_at"):
        t = dt.datetime.strptime(n["remind_at"], "%Y-%m-%dT%H:%M")
        short = f" · {t:%H:%M} {['T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'CN'][t.weekday()]} {t:%d/%m}"
    m["Subject"] = {
        "invite": f"{icon} {urgent}{title}{short}",
        "preview": f"{icon} [Mẫu] {urgent}{title}{short}",
        "due": f"{icon} ĐẾN GIỜ: {urgent}{title}{short}",
        "cancel": f"{icon} Huỷ lịch: {title}{short}",
        "test": "✅ Idea Note: email nhắc hẹn hoạt động tốt",
    }[kind]
    m.set_content(_plain(n, kind))
    # ảnh của ghi chú hiện ngay trong thư (tối đa 3 ảnh, mỗi ảnh ≤ 4 MB)
    imgs = []
    for f in (n.get("images") or [])[:3]:
        path = os.path.join(S.IMG, os.path.basename(f))
        if os.path.isfile(path) and os.path.getsize(path) <= 4 * 1024 * 1024:
            imgs.append((make_msgid(domain="ideanote")[1:-1], path))
    m.add_alternative(_html(n, kind, [c for c, _ in imgs]), subtype="html")
    html_part = m.get_payload()[1]
    for cid, path in imgs:
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        with open(path, "rb") as fh:
            html_part.add_related(fh.read(), maintype="image", subtype="jpeg" if ext == "jpg" else ext, cid=f"<{cid}>")
    if kind in ("invite", "cancel"):
        method = "REQUEST" if kind == "invite" else "CANCEL"
        cal = ics(n, method, seq, to)
        m.add_alternative(cal, subtype="calendar", params={"method": method})  # Gmail đọc phần này thành lời mời lịch
        m.add_attachment(cal.encode("utf-8"), maintype="application", subtype="ics", filename="invite.ics")
    return m


def send_preview(n):
    """Gửi 1 thư MẪU (không kèm lời mời lịch) để xem bố cục."""
    try:
        send(build(n, "preview", 0))
        return ""
    except Exception as e:
        return str(e)


def send(msg):
    pw = unprotect(S.CONF["gmail_pw"])
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=25) as s:
        s.login(S.CONF["gmail"], pw)
        s.send_message(msg)


def send_test():
    """Gửi 1 email thử vào chính Gmail đã cài. Trả về '' nếu được, hoặc lý do lỗi dễ hiểu."""
    try:
        send(build({"id": "test"}, "test", 0))
        LAST.update(error="", sent=time.strftime("%H:%M:%S"))
        return ""
    except smtplib.SMTPAuthenticationError:
        return "Gmail từ chối đăng nhập — kiểm tra lại địa chỉ Gmail và Mật khẩu ứng dụng (16 ký tự)."
    except Exception as e:
        return f"Không gửi được: {e}"


def _deliver(n, kind, seq):
    try:
        send(build(n, kind, seq))
        LAST.update(error="", sent=time.strftime("%H:%M:%S"))
        S.log("đã gửi email", kind, n["id"], n.get("remind_at"))
        return True
    except Exception as e:
        LAST["error"] = f"{time.strftime('%H:%M')} {e}"
        S.log("gửi email lỗi:", kind, n["id"], repr(e))
        return False


def on_due(n):
    """Luồng nhắc gọi khi tới giờ."""
    if configured() and S.CONF.get("mail_due", True):
        threading.Thread(target=_deliver, args=(dict(n), "due", 0), daemon=True).start()


def series_key(n):
    """Nhắc thường: chính giờ nhắc. Nhắc lặp: 'kiểu lặp|thứ|giờ' — sang tuần mới không đổi key nên không gửi lại lời mời."""
    r = n.get("remind_at")
    if not r or not n.get("repeat"):
        return r
    t = dt.datetime.strptime(r, "%Y-%m-%dT%H:%M")
    return f"R|{n['repeat']}|{t.weekday() if n['repeat'] == 'weekly' else '*'}|{t:%H:%M}"


def tick():
    """Đồng bộ lời mời lịch với các nhắc hẹn: mới / đổi giờ -> mời, xong / xoá / bỏ nhắc -> huỷ."""
    if not configured() or not S.CONF.get("mail_invite", True):
        return
    now, now_s = time.time(), S.now_local()
    jobs = []
    with S.LOCK:
        for n in S.NOTES:
            want = series_key(n) if (not n.get("deleted") and n.get("status") != "done") else None
            have = n.get("mail_for")
            if want and want != have:
                if n["remind_at"] < now_s:
                    continue  # giờ đã qua thì khỏi mời
                first = _pending.get(n["id"])
                if not first or first[0] != want:
                    _pending[n["id"]] = (want, now)
                    continue
                if now - first[1] >= SETTLE_S:
                    jobs.append((dict(n), "invite", int(n.get("mail_seq") or 0) + (1 if have else 0)))
            elif have and not want:  # huỷ đúng sự kiện đã mời (giờ bắt đầu lúc mời lưu ở mail_at)
                jobs.append((dict(n, remind_at=n.get("mail_at") or have, repeat=n.get("mail_repeat", "")), "cancel",
                             int(n.get("mail_seq") or 0) + 1))
    for n, kind, seq in jobs:
        cnt, wait_until = _fail.get(n["id"], (0, 0))
        if now < wait_until:
            continue
        if not _deliver(n, kind, seq):
            _fail[n["id"]] = (cnt + 1, now + BACKOFF[min(cnt, len(BACKOFF) - 1)])
            continue
        _fail.pop(n["id"], None)
        with S.LOCK:
            live = next((x for x in S.NOTES if x["id"] == n["id"]), None)
            if live:
                live["mail_seq"] = seq
                live["mail_for"] = series_key(n) if kind == "invite" else None
                live["mail_at"] = n["remind_at"] if kind == "invite" else None
                live["mail_repeat"] = n.get("repeat", "") if kind == "invite" else ""
                S.persist()
        _pending.pop(n["id"], None)


def loop():
    while True:
        try:
            tick()
        except Exception as e:
            S.log("lỗi luồng email:", repr(e))
        time.sleep(10)


def start():
    threading.Thread(target=loop, daemon=True).start()
