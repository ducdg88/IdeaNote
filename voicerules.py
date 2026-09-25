# -*- coding: utf-8 -*-
"""Ghi chú giọng nói bằng QUY TẮC tiếng Việt — tức thì, không cần AI / card đồ hoạ.

Lý do: qwen2.5:7b trên máy dùng chung với app khác (ctx 16384, GPU ~89%) nên mỗi câu phải chờ 40-150s.
Ghi chú giọng nói cần nhanh và chắc, nên giờ hẹn / gấp / dự án / "xong rồi" / câu hỏi được hiểu bằng quy tắc.
"""
import datetime as dt
import re
import unicodedata

import server as S

NUM_WORDS = {"một": 1, "hai": 2, "ba": 3, "bốn": 4, "tư": 4, "năm": 5, "sáu": 6, "bảy": 7, "tám": 8, "chín": 9,
             "mười": 10, "mười một": 11, "mười hai": 12, "mười lăm": 15, "hai mươi": 20, "ba mươi": 30,
             "bốn mươi lăm": 45}
WEEKDAY = {"hai": 0, "2": 0, "ba": 1, "3": 1, "tư": 2, "4": 2, "năm": 3, "5": 3, "sáu": 4, "6": 4, "bảy": 5, "7": 5}
URGENT_RE = re.compile(r"(?<!\w)(gấp|khẩn|khẩn cấp|quan trọng|ưu tiên|ngay lập tức)(?!\w)", re.I)
TODO_RE = re.compile(r"(?<!\w)(nhắc|hẹn|phải|cần phải|nhớ|deadline|hạn chót|việc cần)(?!\w)", re.I)
DONE_RE = re.compile(r"(xong rồi|đã xong|làm xong|hoàn thành|xong xuôi|xong nhé|xong nha)", re.I)
QUESTION_RE = re.compile(r"(\?\s*$|(có|còn) (việc|lịch|gì|hẹn)[^.]*(gì|không|nào|chưa)|lịch (hôm nay|ngày mai|mai)|"
                         r"^(hôm nay|ngày mai|mai|tuần này) (có|còn)|việc gì|bao nhiêu việc)", re.I)


REPEAT_WEEK_RE = re.compile(r"(hàng|hằng|mỗi)\s*tuần", re.I)
REPEAT_DAY_RE = re.compile(r"(hàng|hằng|mỗi)\s*(ngày|tối|sáng)", re.I)


def fold(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


def _words_to_digits(t):
    # "ba giờ chiều" -> "3 giờ chiều" (chỉ khi chữ số đứng ngay trước giờ / phút / tiếng)
    for w in sorted(NUM_WORDS, key=len, reverse=True):
        t = re.sub(rf"(?<!\w){w}(?=\s+(giờ|tiếng|phút)(?!\w))", str(NUM_WORDS[w]), t, flags=re.I)
    return t


def parse_when(text, now=None):
    """Tìm giờ hẹn trong câu tiếng Việt. Trả về 'YYYY-MM-DDTHH:MM' hoặc None."""
    now = now or dt.datetime.now()
    t = _words_to_digits(text.lower())
    m = re.search(r"(\d+)\s*(phút|tiếng|giờ)\s*(nữa|sau)", t)
    if m:
        n = int(m.group(1))
        delta = dt.timedelta(minutes=n) if m.group(2) == "phút" else dt.timedelta(hours=n)
        return (now + delta).strftime("%Y-%m-%dT%H:%M")
    # ---- ngày ----
    day, explicit_day = now.date(), False
    if re.search(r"ngày kia|ngày mốt|(?<!\w)mốt(?!\w)", t):
        day, explicit_day = now.date() + dt.timedelta(days=2), True
    elif re.search(r"(?<!\w)mai(?!\w)", t):
        day, explicit_day = now.date() + dt.timedelta(days=1), True
    elif re.search(r"hôm nay|(sáng|trưa|chiều|tối|đêm) nay", t):
        explicit_day = True
    m = re.search(r"(thứ\s*(hai|ba|tư|năm|sáu|bảy|[2-7])|chủ nhật)(\s*tuần (sau|tới))?", t)
    if m:
        wd = 6 if m.group(1).startswith("chủ") else WEEKDAY[m.group(2)]
        ahead = (wd - now.weekday()) % 7 or 7
        if m.group(3):
            ahead = (wd - now.weekday()) % 7 + 7
        day, explicit_day = now.date() + dt.timedelta(days=ahead), True
    # "ngày 25 (tháng 9)" là ngày; còn "mỗi ngày 7 giờ" thì 7 là giờ, không phải mùng 7
    m = re.search(r"(?<!mỗi )(?<!hàng )(?<!hằng )ngày\s*(\d{1,2})(?!\s*(?:giờ|h\b|tiếng|phút|:|\d))"
                  r"(?:\s*(?:tháng|/)\s*(\d{1,2}))?", t)
    if m:
        d, mo = int(m.group(1)), int(m.group(2) or now.month)
        try:
            cand = dt.date(now.year, mo, d)
            if cand < now.date():
                cand = dt.date(now.year + 1, mo, d) if m.group(2) else dt.date(now.year + (mo == 12), mo % 12 + 1, d)
            day, explicit_day = cand, True
        except ValueError:
            pass
    # ---- giờ ----
    pm = re.search(r"(sáng|trưa|chiều|tối|đêm)", t)
    period = pm.group(1) if pm else None
    m = re.search(r"(?<![\d/])(\d{1,2})\s*(?:giờ|h|:)\s*(\d{1,2})?\s*(?:phút)?\s*(rưỡi|kém\s*(\d{1,2}))?(?!\s*(?:tiếng|phút))", t)
    if m:
        hh, mm = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) and m.group(3).startswith("rưỡi"):
            mm = 30
        elif m.group(4):
            hh, mm = hh - 1, 60 - int(m.group(4))
        if hh > 23 or mm > 59:
            return None
        if period in ("chiều", "tối", "đêm") and hh < 12:
            hh += 12
        elif period == "trưa" and hh < 11:
            hh += 12
        elif not period and 1 <= hh <= 6:
            hh += 12  # "3 giờ" không nói sáng / chiều -> hiểu là giờ làm việc buổi chiều
    elif period and explicit_day:
        hh, mm = {"sáng": 8, "trưa": 12, "chiều": 15, "tối": 20, "đêm": 21}[period], 0
    elif explicit_day and TODO_RE.search(t):
        hh, mm = 8, 0  # "nhắc anh ngày mai …" không có giờ -> 8:00 sáng
    else:
        return None
    when = dt.datetime.combine(day, dt.time(hh, mm))
    if when < now and not explicit_day:
        when += dt.timedelta(days=1)  # giờ đã qua trong hôm nay -> hiểu là ngày mai
    return when.strftime("%Y-%m-%dT%H:%M")


def parse_project(text):
    m = re.search(r"dự án\s+([^,.;!?\n]+)", text, re.I)
    if not m:
        return ""
    stop = {"nhé", "nha", "nhá", "ạ", "với", "lúc", "vào", "và", "gấp", "khẩn", "ngày", "mai", "sáng", "chiều", "tối",
            "hôm", "trước", "sau"}
    out = []
    for w in m.group(1).split()[:5]:
        if w.lower() in stop:
            break
        out.append(w)
    name = " ".join(out)
    return "" if name.lower() in {"này", "đó", "kia", "nọ", "mới", "đấy", "nào", "của", "cũ"} else name


SPEC7_RE = re.compile(r"(?:spec|khung|khuôn|mẫu)\s*(?:một trang\s*|1 trang\s*)?(?:7|bảy)\s*mục", re.I)
LEAD_RE = re.compile(r"^\s*(?:(?:hãy|giúp|em|anh|cho|tôi|mình|ơi|tạo|làm|viết|lên|mở|soạn)\s+)*(?:(?:cho|về|của)\s+)?[:\-,]?\s*", re.I)
TAIL_RE = re.compile(r"\s*(?:\s(?:đi|nhé|nha|nhá|giúp|với|ạ))+\s*[.!]*\s*$|[.!]+\s*$", re.I)


def spec7(text):
    """'tạo spec 7 mục cho app nhóm BYS' -> ghi chú có sẵn khung 7 mục, tiêu đề = phần còn lại. Không khớp -> None."""
    if not SPEC7_RE.search(text):
        return None
    rest = re.sub(r"\s*(?:trong|vào|ở)?\s*dự án\s.*$", "", SPEC7_RE.sub(" ", text), flags=re.I)  # tên dự án không vào tiêu đề
    title = TAIL_RE.sub("", LEAD_RE.sub("", " ".join(rest.split())))
    title = (title[:1].upper() + title[1:]) if title else "Spec mới"
    n = S.new_note({"title": title[:120], "text": S.SPEC7, "status": "note", "project": parse_project(text)}, "voice")
    return f"📐 Đã tạo ghi chú khung 7 mục: {n['title']}. Mở ra điền từng mục nhé.", [("create", n["id"])]


def _live():
    with S.LOCK:
        return [dict(n) for n in S.NOTES if not n.get("deleted")]


def _match_open_task(text):
    """'việc gọi anh Tú xong rồi' -> việc chưa xong khớp nhiều chữ nhất."""
    said = set(fold(DONE_RE.sub(" ", text)).split()) - {"viec", "cai", "da", "roi", "nhe", "nha", "la", "cua", "anh",
                                                        "em", "toi", "minh", "xong", "do", "nay"}
    best, score = None, 0
    for n in _live():
        if n["status"] in ("done", "verify"):
            continue
        s = len(said & set(fold(n.get("text") or "").split()))
        if s > score or (s == score and s and (n.get("created") or "") > (best.get("created") or "")):
            best, score = n, s
    return best if score >= max(1, min(2, len(said) // 2)) else None


def _answer_question(text):
    t = fold(text)
    today, tom = f"{dt.date.today()}", f"{dt.date.today() + dt.timedelta(days=1)}"
    notes = [n for n in _live() if n["status"] != "done"]
    if re.search(r"(gap|khan|quan trong)(?! (anh|chi|em|ong|ba|khach))", t):
        pick, label = [n for n in notes if n.get("urgent")], "việc gấp chưa xong"
    elif re.search(r"(?<!\w)mai(?!\w)", t):
        pick, label = [n for n in notes if tom in (n["date"], (n.get("remind_at") or "")[:10])], "việc ngày mai"
    else:
        pick, label = [n for n in notes if today in (n["date"], (n.get("remind_at") or "")[:10])], "việc hôm nay"
    pick.sort(key=lambda n: (n.get("remind_at") or "9999", not n.get("urgent")))
    if not pick:
        return f"Không có {label} nào."
    lines = [f"Có {len(pick)} {label}:"]
    for n in pick[:6]:
        extra = (" · ⏰ " + n["remind_at"][11:16]) if n.get("remind_at") else ""
        extra += " · 🔴" if n.get("urgent") else ""
        lines.append("• " + " ".join((n.get("text") or "(ảnh)").split())[:70] + extra)
    if len(pick) > 6:
        lines.append(f"… và {len(pick) - 6} việc nữa.")
    return "\n".join(lines)


def quick(text):
    """Ghi chú giọng nói tức thì. Trả về (câu trả lời, [(kiểu, id)]) — cùng dạng với Brain.ask."""
    text = " ".join(text.split()).strip()
    if not text:
        return "Chưa nghe thấy gì.", []
    made = spec7(text)
    if made:
        return made
    if QUESTION_RE.search(text) and not TODO_RE.search(text):
        return _answer_question(text), [("list", "")]
    if DONE_RE.search(text) and not parse_when(text):
        n = _match_open_task(text)
        if n:
            with S.LOCK:
                live = next((x for x in S.NOTES if x["id"] == n["id"]), None)
                S.apply(live, {"status": S.finish_status(live)})
                S.persist()
            return ("Đã đánh dấu xong." if live["status"] == "done" else "Đã làm, chờ Verify: mở app ghi bằng chứng."), [("update", n["id"])]
    when = parse_when(text)
    # kể chuyện có nhiều mốc giờ ("máy chạy 7 giờ tới 8 giờ 30 mới lên") mà không nói nhắc / hẹn -> không đặt nhắc
    if when and not TODO_RE.search(text) and len(re.findall(r"\d{1,2}\s*(?:giờ|h)(?!\w)", _words_to_digits(text.lower()))) > 1:
        when = None
    rep = "weekly" if REPEAT_WEEK_RE.search(text) else "daily" if REPEAT_DAY_RE.search(text) else ""
    body = {"text": text[0].upper() + text[1:], "status": "todo" if (when or TODO_RE.search(text)) else "note",
            "urgent": bool(URGENT_RE.search(text)), "project": parse_project(text), "remind_at": when,
            "repeat": rep if when else ""}
    if when:
        body["date"] = when[:10]
    n = S.new_note(body, "voice")
    if when and rep:
        return ("🔁 Đã đặt nhắc lặp " + ("hàng tuần." if rep == "weekly" else "hàng ngày.")), [("create", n["id"])]
    return ("Đã đặt nhắc." if when else "Đã lưu."), [("create", n["id"])]
