# -*- coding: utf-8 -*-
"""Trợ lý giọng nói của Idea Note — chạy hoàn toàn trong máy.

Nghe:  faster-whisper (large-v3-turbo trên GPU, dự phòng small trên CPU) — không gửi giọng nói ra ngoài.
Hiểu:  Ollama (qwen2.5:7b) ở 127.0.0.1 + gọi công cụ: tạo / sửa / xem / xoá ghi chú, đặt nhắc hẹn.
"""
import datetime as dt
import json
import os
import re
import threading
import time
import urllib.request
import wave

import server as S

OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen2.5:7b"
# đo trên máy này (câu 8 giây): turbo GPU 0.5s · small CPU 2.5s · turbo CPU 12s -> GPU trước, đầy VRAM thì small CPU.
# Model để ngay trong E:\IdeaNote\models: 24/09 có công cụ dọn mất cả ~/.cache/huggingface/hub làm app hết nghe được.
MODELS = os.path.join(S.ROOT, "models")
WHISPER = [(os.path.join(MODELS, "large-v3-turbo"), "cuda", "int8_float16"),
           (os.path.join(MODELS, "small"), "cpu", "int8"),
           ("mobiuslabsgmbh/faster-whisper-large-v3-turbo", "cuda", "int8_float16"),
           ("Systran/faster-whisper-small", "cpu", "int8")]
EAR_IDLE_S = 15 * 60  # bộ nghe chiếm ~1,5 GB: không nói gì 15 phút thì nhả, lần nói sau tự nạp lại
THU = ["thứ hai", "thứ ba", "thứ tư", "thứ năm", "thứ sáu", "thứ bảy", "chủ nhật"]
STATUS_VI = {"note": "ý tưởng", "todo": "chưa làm", "doing": "đang làm", "verify": "đã làm, chờ verify", "done": "xong"}


HALLUCINATION = re.compile(r"(subscribe|đăng ký kênh|ghiền mì gõ|cảm ơn các bạn đã (xem|theo dõi)|hẹn gặp lại các bạn)", re.I)


# ---------- nghe ----------
class Ear:
    """Thu mic + nhận dạng tiếng Việt. Model chỉ nạp khi cần nghe (ensure), để lâu không dùng thì tự nhả bộ nhớ."""

    def __init__(self):
        self.model = None
        self.device = ""
        self.err = None
        self.ready = threading.Event()
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.last_stats = {}
        self.loading = False
        self.last_used = 0.0
        self._state = threading.Lock()
        self._watching = False

    def ensure(self):
        """Bắt đầu nạp model ở nền nếu chưa có (gọi lúc bắt đầu thu mic để vừa nói vừa nạp)."""
        self.last_used = time.time()
        with self._state:
            if self.model is not None or self.loading:
                return
            self.loading = True
            self.ready.clear()
            if not self._watching:
                self._watching = True
                threading.Thread(target=self._idle_loop, daemon=True).start()
        threading.Thread(target=self.load, daemon=True).start()

    def _idle_loop(self):
        while True:
            time.sleep(60)
            if self.model is None or self.loading or time.time() - self.last_used < EAR_IDLE_S:
                continue
            if not self.lock.acquire(blocking=False):  # đang nhận dạng thì để lượt sau
                continue
            try:
                with self._state:
                    self.model = None
                    self.ready.clear()
                import gc
                gc.collect()
                S.log("bộ nghe: nhả bộ nhớ sau", EAR_IDLE_S // 60, "phút không dùng")
            finally:
                self.lock.release()

    def load(self):
        # bản .exe không đóng gói torch (3.5 GB) — mượn cuBLAS trong torch của Python đã cài trên máy nếu có
        import glob
        import os
        for d in glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*\Lib\site-packages\torch\lib")):
            if os.path.exists(os.path.join(d, "cublas64_12.dll")):
                try:
                    os.add_dll_directory(d)
                    os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                except OSError:
                    pass
        from faster_whisper import WhisperModel
        # máy mới cài chưa có model -> lần đầu tự tải bản small (~480 MB) vào models\ rồi dùng luôn không cần mạng
        for name, dev, ct in WHISPER + [("small", "cpu", "int8")]:
            if os.path.isabs(name) and not os.path.exists(os.path.join(name, "model.bin")):
                continue
            download = name == "small"
            try:
                self.model = WhisperModel(name, device=dev, compute_type=ct, cpu_threads=6, local_files_only=not download,
                                          **({"download_root": MODELS} if download else {}))
                self.device = f"{os.path.basename(name).split('-')[-1]} · {dev}"
                break
            except Exception as e:  # thiếu VRAM / thiếu CUDA -> xuống CPU
                self.err = repr(e)
                S.log("bộ nghe không nạp được", name, dev, self.err[:200])
        S.log("bộ nghe:", self.device if self.model is not None else "KHÔNG NẠP ĐƯỢC")
        self.last_used = time.time()
        with self._state:
            self.loading = False
            self.ready.set()

    @staticmethod
    def devices():
        """Danh sách mic (MME) để chọn: [(index, tên)], mic mặc định của Windows đứng đầu."""
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            default = pa.get_default_input_device_info()["index"]
            out = []
            for i in range(pa.get_device_count()):
                d = pa.get_device_info_by_index(i)
                if d["maxInputChannels"] > 0 and pa.get_host_api_info_by_index(d["hostApi"])["name"] == "MME" \
                        and "Sound Mapper" not in d["name"]:
                    out.append((i, d["name"] + (" (mặc định)" if i == default else "")))
            return sorted(out, key=lambda x: x[0] != default)
        finally:
            pa.terminate()

    def record(self, on_level=None, device=None, max_s=40, silence_s=1.0, wait_s=8):
        """Thu tới khi nói xong rồi ngừng ~1.3s, hoặc bấm dừng. Luôn trả về audio đã thu (bộ nghe tự lọc im lặng).

        Ngưỡng 'đang nói' tự đo theo tiếng ồn nền 0.5s đầu — mic mỗi máy mỗi khác (BKD-11 ở máy này ồn nền ~400-1100)."""
        import audioop
        import pyaudio
        self.stop_flag.clear()
        pa = pyaudio.PyAudio()
        st = pa.open(format=pyaudio.paInt16, channels=1, rate=16000, input=True, frames_per_buffer=1600,
                     input_device_index=device)
        frames, levels, heard, quiet, start, talk_start = [], [], False, 0.0, time.time(), None
        floor, speech_seen, voice_lv = None, False, []
        try:  # nhận biết giọng người (không chỉ độ to) -> nói xong là dừng dù phòng ồn
            import numpy as np
            from faster_whisper.vad import VadOptions, get_speech_timestamps
            vad_opts = VadOptions(min_silence_duration_ms=300, speech_pad_ms=100)
            vad = lambda a, o: get_speech_timestamps(a, o)  # noqa: E731
        except Exception:
            vad = None
        try:
            while not self.stop_flag.is_set():
                buf = st.read(1600, exception_on_overflow=False)  # 0.1 s
                frames.append(buf)
                rms = audioop.rms(buf, 2)
                levels.append(rms)
                if len(levels) == 5:  # ồn nền = trung vị 0.5s đầu (mic ồn có tiếng lách tách vọt lên từng khung)
                    floor = max(80, sorted(levels)[2])
                elif floor is not None and not heard and rms < floor * 1.3:
                    floor = floor * 0.9 + rms * 0.1  # chưa nói thì cập nhật dần ồn nền
                smooth = sum(levels[-3:]) / len(levels[-3:])  # làm mượt 0.3s: tiếng lách tách 1 khung không tính là nói
                talking = floor is not None and smooth > floor * 1.8 + 120
                quiet_now = floor is not None and smooth < floor * 1.4 + 80
                if on_level:
                    on_level(min(1.0, rms / max(3000, (floor or 500) * 5)), talking)
                if talking:
                    heard, quiet = True, 0.0
                    talk_start = talk_start or len(frames)
                    voice_lv.append(smooth)
                elif heard:
                    # người nói sát mic to hơn hẳn video / loa trong phòng: tụt dưới 45% giọng mình = đã nói xong
                    mine = sorted(voice_lv)[len(voice_lv) // 2] if voice_lv else 0
                    low = quiet_now or (mine and smooth < mine * 0.45)
                    quiet = quiet + 0.1 if low else 0.0
                    if quiet >= silence_s:
                        break
                # và hỏi Silero VAD (nhận biết giọng người) xem 1 giây cuối còn ai nói không
                if heard and vad is not None and len(frames) % 5 == 0:
                    tail = np.frombuffer(b"".join(frames[-30:]), dtype=np.int16).astype(np.float32) / 32768.0
                    ts = vad(tail, vad_opts)
                    if ts:
                        speech_seen = True
                    if speech_seen and (not ts or len(tail) - ts[-1]["end"] > 16000 * silence_s):
                        break
                el = len(frames) / 10  # giây âm thanh đã thu
                if el > max_s or (not heard and el > wait_s) or (talk_start and (len(frames) - talk_start) / 10 > 30):
                    break
        finally:
            st.stop_stream()
            st.close()
            pa.terminate()
        self.last_stats = {"floor": floor, "max": max(levels or [0]), "heard": heard, "secs": round(len(frames) / 10, 1)}
        return b"".join(frames)

    def transcribe_file(self, path, hint=""):
        """File ghi âm từ điện thoại (m4a / webm / ogg / 3gp …) -> chữ."""
        import numpy as np
        from faster_whisper import decode_audio
        audio = decode_audio(path, sampling_rate=16000)
        return self.transcribe((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes(), hint)

    def transcribe(self, pcm, hint=""):
        with self.lock:  # mic máy tính + ghi âm điện thoại có thể tới cùng lúc
            return self._transcribe(pcm, hint)

    def _transcribe(self, pcm, hint=""):
        import numpy as np
        self.ensure()  # đang giữ self.lock nên luồng nhả bộ nhớ không chen vào giữa
        self.ready.wait()
        self.last_used = time.time()
        if not self.model:
            raise RuntimeError("Không nạp được model nghe: " + str(self.err))
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        prompt = ("Ghi chú công việc, nhắc hẹn. " + hint)[:220]
        segs, _ = self.model.transcribe(audio, language="vi", beam_size=1, vad_filter=True, initial_prompt=prompt)
        text = " ".join(s.text.strip() for s in segs).strip()
        if not text:  # nói nhỏ quá bộ lọc im lặng bỏ mất -> thử lại không lọc
            segs, _ = self.model.transcribe(audio, language="vi", beam_size=1, vad_filter=False, initial_prompt=prompt)
            text = " ".join(s.text.strip() for s in segs if s.no_speech_prob < 0.6).strip()
        if HALLUCINATION.search(text):  # câu Whisper hay tự bịa khi chỉ có tiếng ồn
            text = ""
        return text

    @staticmethod
    def save_wav(pcm, path):
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)


# ---------- công cụ trợ lý được phép dùng ----------
TOOLS = [
    {"type": "function", "function": {
        "name": "create_note",
        "description": "Tạo ghi chú / việc cần làm / nhắc hẹn mới. Dùng khi người dùng giao việc, nhờ nhắc, hoặc muốn ghi lại ý tưởng.",
        "parameters": {"type": "object", "required": ["text", "kind"], "properties": {
            "text": {"type": "string", "description": "Nội dung đầy đủ, viết lại gọn gàng, giữ nguyên tên người / số liệu"},
            "kind": {"type": "string", "enum": ["todo", "note"], "description": "todo = việc cần làm / cuộc hẹn; note = ý tưởng / ghi chú"},
            "project": {"type": "string", "description": "Tên dự án nếu người dùng nhắc tới (ưu tiên trùng tên dự án có sẵn)"},
            "urgent": {"type": "boolean", "description": "true nếu người dùng nói gấp / quan trọng / khẩn"},
            "remind_at": {"type": "string", "description": "Giờ nhắc dạng YYYY-MM-DDTHH:MM (giờ địa phương) nếu có hẹn giờ"},
            "date": {"type": "string", "description": "Ngày của việc YYYY-MM-DD nếu khác hôm nay"}}}}},
    {"type": "function", "function": {
        "name": "update_note",
        "description": "Sửa ghi chú có sẵn theo id: đánh dấu xong, đang làm, đổi giờ nhắc, bật/tắt gấp, đổi tiến độ, đổi dự án, sửa nội dung.",
        "parameters": {"type": "object", "required": ["id"], "properties": {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["todo", "doing", "done", "note"]},
            "urgent": {"type": "boolean"},
            "progress": {"type": "integer", "description": "0-100"},
            "remind_at": {"type": "string", "description": "YYYY-MM-DDTHH:MM, hoặc chuỗi rỗng để bỏ nhắc"},
            "project": {"type": "string"},
            "text": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "list_notes",
        "description": "Xem danh sách ghi chú để trả lời câu hỏi như 'hôm nay có việc gì', 'còn gì gấp', 'dự án X tới đâu rồi', 'lịch ngày mai'.",
        "parameters": {"type": "object", "properties": {
            "filter": {"type": "string", "enum": ["today", "tomorrow", "open", "urgent", "remind", "done", "ideas", "all"]},
            "project": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"}}}}},
    {"type": "function", "function": {
        "name": "search_notes",
        "description": "Tìm ghi chú theo từ khoá.",
        "parameters": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}}}}},
    {"type": "function", "function": {
        "name": "delete_note",
        "description": "Chuyển ghi chú vào thùng rác (khôi phục được). Chỉ dùng khi người dùng nói rõ muốn xoá.",
        "parameters": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}}},
]


def _brief(n):
    bits = [n["id"], STATUS_VI.get(n["status"], n["status"])]
    if n.get("urgent") and n["status"] != "done":
        bits.append("GẤP")
    if n.get("progress") and n["status"] == "doing":
        bits.append(f"{n['progress']}%")
    if n.get("project"):
        bits.append("dự án " + n["project"])
    if n.get("remind_at"):
        bits.append("nhắc " + n["remind_at"].replace("T", " "))
    bits.append("ngày " + n["date"])
    return " | ".join(bits) + " | " + " ".join((n.get("text") or "(ảnh)").split())[:90]


def _live():
    with S.LOCK:
        return [dict(n) for n in S.NOTES if not n.get("deleted")]


def _find_note(nid):
    return next((n for n in S.NOTES if n["id"] == nid), None)


def tool_create_note(a, actions):
    body = {"text": (a.get("text") or "").strip(), "status": "todo" if a.get("kind") == "todo" else "note",
            "urgent": bool(a.get("urgent")), "project": (a.get("project") or "").strip()}
    if a.get("remind_at"):
        body["remind_at"] = _norm_time(a["remind_at"])
        body["status"] = "todo" if body["status"] == "note" else body["status"]
    if a.get("date"):
        body["date"] = a["date"][:10]
    elif body.get("remind_at"):
        body["date"] = body["remind_at"][:10]
    if not body["text"]:
        return {"ok": False, "error": "thiếu nội dung"}
    said = fold(CURRENT_SAY[0])
    if body["project"] and "du an" not in said and fold(body["project"]) not in said:
        body["project"] = ""  # model tự bịa dự án người dùng không hề nói -> bỏ
    if not body["project"] and CURRENT_SAY[0]:  # model quên dự án -> bắt cụm "dự án X" trong lời nói
        m = re.search(r"dự án\s+([^,.;!?\n]+)", CURRENT_SAY[0], re.I)
        if m:
            body["project"] = " ".join(m.group(1).split()[:5]).strip()
    n = S.new_note(body, "voice")
    actions.append(("create", n["id"]))
    return {"ok": True, "id": n["id"], "saved": _brief(n)}


def tool_update_note(a, actions):
    with S.LOCK:
        n = _find_note(a.get("id", ""))
        if not n:
            return {"ok": False, "error": "không có ghi chú id này — hãy dùng list_notes / search_notes để lấy id đúng"}
        body = {k: a[k] for k in ("status", "urgent", "progress", "project", "text") if k in a and a[k] is not None}
        if body.get("status") == "done" and n["status"] in ("todo", "doing"):  # nói "xong" chỉ là đã làm, Verify bấm trong app
            body["status"] = S.finish_status(n)
        if "remind_at" in a:
            body["remind_at"] = _norm_time(a["remind_at"]) if a["remind_at"] else None
        S.apply(n, body)
        S.persist()
        actions.append(("update", n["id"]))
        return {"ok": True, "now": _brief(n)}


def tool_list_notes(a, actions):
    notes = _live()
    today = f"{dt.date.today()}"
    tomorrow = f"{dt.date.today() + dt.timedelta(days=1)}"
    f = a.get("filter") or "open"
    day = a.get("date") or (today if f == "today" else tomorrow if f == "tomorrow" else None)
    out = []
    for n in notes:
        if a.get("project") and a["project"].lower() not in (n.get("project") or "").lower():
            continue
        if day and not (n["date"] == day or (n.get("remind_at") or "")[:10] == day):
            continue
        if f == "open" and n["status"] not in ("todo", "doing"):
            continue
        if f == "urgent" and not (n.get("urgent") and n["status"] != "done"):
            continue
        if f == "remind" and not (n.get("remind_at") and n["status"] != "done"):
            continue
        if f == "done" and n["status"] != "done":
            continue
        if f == "ideas" and n["status"] != "note":
            continue
        out.append(n)
    out.sort(key=lambda n: (n.get("remind_at") or "9999", n.get("created") or ""))
    actions.append(("list", f))
    return {"count": len(out), "items": [_brief(n) for n in out[:30]]}


def tool_search_notes(a, actions):
    q = (a.get("query") or "").lower()
    hits = [n for n in _live() if q and q in ((n.get("text") or "") + " " + (n.get("project") or "")).lower()]
    return {"count": len(hits), "items": [_brief(n) for n in hits[:20]]}


def tool_delete_note(a, actions):
    with S.LOCK:
        n = _find_note(a.get("id", ""))
        if not n:
            return {"ok": False, "error": "không có id này"}
        n["deleted"] = True
        n["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        S.persist()
        actions.append(("delete", n["id"]))
        return {"ok": True, "deleted": _brief(n)}


CURRENT_SAY = [""]  # câu người dùng vừa nói (để bắt dự án khi model quên)

RUN = {"create_note": tool_create_note, "update_note": tool_update_note, "list_notes": tool_list_notes,
       "search_notes": tool_search_notes, "delete_note": tool_delete_note}


def _norm_time(s):
    s = (s or "").strip().replace(" ", "T")
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            v = dt.datetime.strptime(s[:19], fmt)
            if fmt == "%Y-%m-%d":
                v = v.replace(hour=8)
            return v.strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            pass
    return None


def system_prompt():
    now = dt.datetime.now()
    days = []
    for i in range(0, 14):
        d = now.date() + dt.timedelta(days=i)
        label = {0: "hôm nay", 1: "ngày mai", 2: "ngày kia"}.get(i, "")
        days.append(f"- {d:%Y-%m-%d} = {THU[d.weekday()]}" + (f" ({label})" if label else "") + (" (tuần sau)" if d.isocalendar()[1] != now.isocalendar()[1] else ""))
    notes = _live()
    open_ = [n for n in notes if n["status"] in ("todo", "doing")] + [n for n in notes if n["status"] == "note"][-10:]
    projects = sorted({n["project"] for n in notes if n.get("project")})
    return f"""Bạn là trợ lý công việc của app Idea Note, nói tiếng Việt, xưng "em", gọi người dùng là "anh".
Nhiệm vụ: nhận việc được giao bằng lời nói, tự ghi vào Idea Note, đặt nhắc hẹn, cập nhật tiến độ, và trả lời câu hỏi về công việc.

Quy tắc:
- Người dùng giao việc / nhờ nhắc / hẹn gặp -> gọi create_note với kind="todo". Ý tưởng, ghi nhớ -> kind="note".
- Đây là app GHI CHÚ BẰNG GIỌNG NÓI: câu nào không phải câu hỏi và không phải lệnh sửa việc cũ thì LUÔN lưu ngay bằng
  create_note(kind="note", text=gần nguyên văn lời nói, chỉ sửa chính tả / dấu câu). KHÔNG BAO GIỜ hỏi lại thêm thông tin trước khi lưu.
- Có giờ hẹn thì đặt remind_at. "sáng" không nói giờ = 08:00, "trưa" = 12:00, "chiều" = 15:00, "tối" = 20:00. "3 giờ chiều" = 15:00, "8 giờ tối" = 20:00.
- "X phút nữa / X tiếng nữa" phải tra BẢNG GIỜ bên dưới. Ngày tương đối phải tra BẢNG NGÀY. Không tự tính.
- Câu có "dự án X" -> điền project = X (bỏ chữ "dự án"; dự án mới chưa có cũng được). Nội dung text không cần lặp lại tên dự án.
- urgent = true CHỈ KHI người dùng nói "gấp", "khẩn", "quan trọng", "ưu tiên". Còn lại false.
- Người dùng báo đã xong / đang làm / đổi giờ một việc có sẵn -> tìm id trong DANH SÁCH rồi gọi update_note. Không chắc việc nào thì hỏi lại.
- Một câu có nhiều việc -> gọi create_note nhiều lần.
- Chỉ xoá khi người dùng nói rõ "xoá".
- MỌI thay đổi phải làm bằng cách GỌI CÔNG CỤ. Chưa gọi công cụ thì tuyệt đối không nói "đã tạo / đã lưu / đã cập nhật".
- Sau khi công cụ báo ok, trả lời NGẮN 1-2 câu xác nhận (nêu rõ giờ nhắc nếu có). Không hỏi thêm "anh còn việc gì không".
- Câu hỏi về lịch / việc -> gọi list_notes rồi tóm tắt ngắn gọn, ưu tiên việc gấp và giờ hẹn.

Ví dụ:
- "nhắc anh 9 giờ sáng mai họp team" -> create_note(text="Họp team", kind="todo", remind_at=<ngày mai>T09:00)
- "ý tưởng làm video gỗ óc chó cho dự án Kênh gỗ" -> create_note(text="Làm video gỗ óc chó", kind="note", project="Kênh gỗ")
- "việc họp team xong rồi" -> tìm dòng "Họp team" trong DANH SÁCH lấy id -> update_note(id=<id đó>, status="done")
- "việc thiết kế logo đang làm được một nửa" -> update_note(id=<id>, status="doing", progress=50)
- "dời việc gọi khách sang 4 giờ chiều" -> update_note(id=<id>, remind_at=<hôm nay>T16:00)

Bây giờ là {now:%H:%M} {THU[now.weekday()]} ngày {now:%d/%m/%Y}.
BẢNG GIỜ: {"; ".join(f"{k} nữa = {(now + dt.timedelta(minutes=m)):%Y-%m-%dT%H:%M}" for k, m in (("5 phút", 5), ("10 phút", 10), ("15 phút", 15), ("20 phút", 20), ("30 phút", 30), ("45 phút", 45), ("1 tiếng", 60), ("1 tiếng rưỡi", 90), ("2 tiếng", 120), ("3 tiếng", 180)))}
BẢNG NGÀY:
{chr(10).join(days)}

Dự án đang có: {", ".join(projects) or "(chưa có)"}
DANH SÁCH việc đang mở + ý tưởng gần đây (id | trạng thái | ... | nội dung):
{chr(10).join(_brief(n) for n in open_[-25:]) or "(trống)"}
"""


def fold(s):
    import unicodedata
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


def tidy(reply):
    """Bỏ câu hỏi đuôi ('Anh có muốn…?'), bỏ mã id lộ ra, xưng hô 'bạn' -> 'anh'."""
    out = re.sub(r"\s*\(?\b(id[: ]*)?\d{12}[0-9a-f]{4}\b\)?", "", reply.strip())
    sents = re.split(r"(?<=[.!?])\s+", out)
    while len(sents) > 1 and sents[-1].rstrip().endswith("?"):
        sents.pop()
    out = " ".join(sents) if "\n" not in out else out if not out.rstrip().endswith("?") else out.rsplit("\n", 1)[0]
    out = re.sub(r"\bBạn\b", "Anh", out)
    return re.sub(r"\bbạn\b", "anh", out)


CLAIM = re.compile(r"(đã|em) (tạo|ghi|lưu|đặt|cập nhật|đánh dấu|xoá|xóa|chuyển|thêm|sửa)", re.I)


def _ctx():
    """Khung ngữ cảnh của model ĐANG nạp sẵn. App khác trên máy cũng dùng qwen2.5:7b (ctx 16384) — xin khác cỡ là
    Ollama phải gỡ ra nạp lại mất 1-3 phút, nên cứ dùng đúng cỡ đang có; chưa nạp thì 4096 (vừa VRAM nhất)."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=3) as r:
            for m in json.loads(r.read()).get("models", []):
                if m.get("name", "").startswith(MODEL) and m.get("context_length"):
                    return int(m["context_length"])
    except Exception:
        pass
    return 4096


def _chat(messages):
    req = urllib.request.Request(OLLAMA + "/api/chat", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS, "stream": False,
                                                  "keep_alive": "60m", "options": {"temperature": 0.1, "num_ctx": _ctx()}}).encode())
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]


SORT_MAX = 6000  # ghi chú dài hơn chỉ sắp phần đầu (khung ngữ cảnh của model nhỏ), phần gốc vẫn giữ đủ ở cuối
SORT_PROMPT = """Bạn sắp một ghi chú tiếng Việt vào khung spec 7 mục. CHỈ dùng ý có trong ghi chú, không bịa thêm, giữ chữ của người viết.
Trả về JSON đúng dạng: {"1": [...], "2": [...], "3": [...], "4": [...], "5": [...], "6": [...], "7": [...]}
1 = Vấn đề: đang đau gì, ai đau, không làm thì mất gì
2 = Chân dung người dùng: ai dùng, họ cần gì, sợ gì
3 = Cách hoạt động: các bước, luồng làm
4 = KHÔNG làm: những gì ghi chú nói để sau, bỏ, không làm (câu có "chưa cần", "để sau", "không làm", "tạm gác" thuộc mục này)
5 = Dữ liệu: lưu gì, ở đâu, con số, số liệu
6 = Định nghĩa hoàn thành: mỗi phần tử là {"viec": "việc cần làm", "verify": "bằng chứng cho thấy xong, không rõ thì ?"}
7 = Dễ hỏng ở đâu: rủi ro, lỗi, chỗ dễ gãy
Mỗi ý chỉ đặt vào 1 mục đúng nghĩa nhất (rủi ro, lo sợ vào mục 7, không vào mục 2).
Mỗi mục tối đa 5 câu ngắn. Mục nào ghi chú không nói tới thì để danh sách rỗng []."""
NOT_RE = re.compile(r"(chưa cần|để sau|không làm|khỏi làm|tạm gác|gác lại|bỏ qua|không cần|chưa làm vội|tính sau)", re.I)


def spec7_sort(text):
    """Ghi chú tự do -> khung 7 mục bằng AI trong máy. Mục ghi chú chưa nói tới ghi "?", nội dung gốc giữ nguyên ở cuối."""
    src = (text or "").strip()
    req = urllib.request.Request(OLLAMA + "/api/chat", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": MODEL, "stream": False, "format": "json", "keep_alive": "60m",
                                                  "options": {"temperature": 0.1, "num_ctx": _ctx()},
                                                  "messages": [{"role": "system", "content": SORT_PROMPT},
                                                               {"role": "user", "content": src[:SORT_MAX]}]}).encode())
    with urllib.request.urlopen(req, timeout=240) as r:
        data = json.loads(json.loads(r.read())["message"]["content"] or "{}")

    def say(x):  # model đôi khi trả dict / số thay cho câu
        if isinstance(x, dict):
            x = " · ".join(str(v) for v in x.values() if v)
        return " ".join(str(x).split())

    names = S.SPEC7_HEAD, "2, CHÂN DUNG NGƯỜI DÙNG", "3, CÁCH HOẠT ĐỘNG", "4, KHÔNG LÀM", "5, DỮ LIỆU", \
        "6, ĐỊNH NGHĨA HOÀN THÀNH", "7, DỄ HỎNG Ở ĐÂU"
    out = []
    for i, name in enumerate(names, 1):
        raw = data.get(str(i)) or []
        items = [x for x in (raw if isinstance(raw, list) else [raw]) if say(x)][:10]  # trả 1 câu thay cho danh sách
        if i == 4:  # mục hay bị bỏ nhất: câu "chưa cần / để sau…" mà AI sót thì quy tắc tự bắt
            said = set(" ".join(say(x) for x in items).lower().split())
            for s in (" ".join(p.split()) for p in re.split(r"(?<=[.!?;])\s+|\n+", src[:SORT_MAX])):
                words = set(s.lower().rstrip(".").split())
                if NOT_RE.search(s) and len(words & said) < 0.6 * len(words) and len(items) < 8:  # AI đã ghi ý này thì thôi
                    items.append(s.rstrip("."))
                    said |= words
        out.append(name)
        if i == 6:
            for x in items:
                viec = say(x.get("viec") or x.get("việc") or "") if isinstance(x, dict) else say(x)
                ver = say(x.get("verify") or "?") if isinstance(x, dict) else "?"
                out += [f"[ ] Làm [ ] Verify · {viec or '?'}", f"    Verify: {ver or '?'}"]
            if not items:
                out += ["[ ] Làm [ ] Verify · ?", "    Verify: ?"]
        else:
            mark = "✕" if i == 4 else "!" if i == 7 else "•"
            out += [f"{mark} {say(x)}" for x in items] or [f"{mark} ?"]
        out.append("")
    cut = f"  (AI chỉ sắp {SORT_MAX} ký tự đầu)" if len(src) > SORT_MAX else ""
    return "\n".join(out) + "━━━━━━━━━━━━━━━━━━━━\nGHI CHÚ GỐC" + cut + "\n━━━━━━━━━━━━━━━━━━━━\n" + src


class Brain:
    def __init__(self):
        self.history = []  # các lượt hội thoại gần đây (chỉ user/assistant)

    def ask(self, text):
        """Trả về (câu trả lời, danh sách thao tác đã làm). AI lỗi thì vẫn lưu nguyên lời nói — không bao giờ mất."""
        import voicerules  # "tạo spec 7 mục cho X": làm ngay theo quy tắc, không chờ AI
        made = voicerules.spec7(text)
        if made:
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": made[0]}]
            return made
        try:
            return self._ask(text)
        except Exception as e:
            n = S.new_note({"text": text, "status": "todo"}, "voice")
            return (f"Em chưa hiểu kịp (AI trong máy đang bận: {type(e).__name__}), nên đã lưu nguyên lời anh "
                    f"thành việc cần làm để không bị mất.", [("create", n["id"])])

    def _ask(self, text):
        CURRENT_SAY[0] = text
        actions = []
        turn = [{"role": "user", "content": text}]
        reply, nudged = "", False
        for _ in range(6):
            m = _chat([{"role": "system", "content": system_prompt()}] + self.history + turn)
            calls = m.get("tool_calls") or []
            turn.append({k: m[k] for k in ("role", "content", "tool_calls") if k in m})
            if not calls:
                reply = (m.get("content") or "").strip()
                # chống "nói đã làm mà không làm": bắt gọi công cụ thật một lần
                if not actions and not nudged and CLAIM.search(reply):
                    nudged = True
                    turn.pop()
                    turn.append({"role": "system", "content": "Bạn CHƯA gọi công cụ nào nên chưa có gì được lưu. "
                                                              "Hãy gọi đúng công cụ (create_note / update_note / ...) ngay bây giờ."})
                    continue
                break
            for c in calls:
                fn = c.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                try:
                    res = RUN[fn["name"]](args, actions) if fn.get("name") in RUN else {"error": "không có công cụ này"}
                except Exception as e:
                    res = {"ok": False, "error": repr(e)}
                turn.append({"role": "tool", "content": json.dumps(res, ensure_ascii=False)})
        if CLAIM.search(reply) and not any(a[0] in ("create", "update", "delete") for a in actions):
            reply = "Em chưa lưu được gì — anh nói lại rõ hơn giúp em nhé."
        reply = tidy(reply)
        # giữ nguyên cả lượt gọi công cụ trong lịch sử để model học đúng cách làm
        self.history = (self.history + [x for x in turn if x.get("role") != "system"])[-16:]
        while self.history and self.history[0]["role"] != "user":
            self.history.pop(0)
        return reply or "Em đã làm xong.", actions

    @staticmethod
    def warm():
        """Nạp sẵn model Ollama vào bộ nhớ để lần hỏi đầu không phải chờ (đã nạp rồi thì thôi)."""
        if loaded():
            return True
        try:
            req = urllib.request.Request(OLLAMA + "/api/generate", method="POST", headers={"Content-Type": "application/json"},
                                         data=json.dumps({"model": MODEL, "prompt": "", "keep_alive": "60m", "options": {"num_ctx": _ctx()}}).encode())
            urllib.request.urlopen(req, timeout=240).close()
            return True
        except Exception:
            return False


def loaded():
    """Model AI đã nằm sẵn trong bộ nhớ chưa (để báo người dùng nếu phải chờ khởi động)."""
    try:
        with urllib.request.urlopen(OLLAMA + "/api/ps", timeout=3) as r:
            return any(m.get("name", "").startswith(MODEL) for m in json.loads(r.read()).get("models", []))
    except Exception:
        return False
