# -*- coding: utf-8 -*-
"""Kho kiến thức của Idea Note: đọc chữ trong ảnh, tìm theo từ khoá + theo nghĩa, hỏi AI có trích nguồn.

Chạy hoàn toàn trong máy:
- Đọc chữ trong ảnh (OCR): Tesseract có gói tiếng Việt (vie.traineddata) nếu có, không thì OCR sẵn trong Windows.
- Tìm theo nghĩa: Ollama bge-m3 (không có thì chỉ tìm theo từ khoá, bỏ dấu).
- Trả lời: Ollama qwen2.5, chỉ dựa trên các ghi chú tìm được, ghi rõ nguồn [1] [2]…
Kết quả đọc ảnh lưu ở data/kb-ocr.json, vector ở data/kb-vec.json (xoá đi là máy tự làm lại).
"""
import base64
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import urllib.request

import server as S  # server.start() gán lại S = module đang chạy (khi chạy python server.py)

OLLAMA = "http://127.0.0.1:11434"
EMBED = "bge-m3"
CHAT = ("qwen2.5:7b", "qwen2.5:3b")
NOWIN = 0x08000000
LOCK = threading.RLock()
OCR = {}     # tên ảnh -> {"text", "engine", "q" (độ tốt của cách đọc), "at"}
VEC = {}     # sha1(đoạn chữ) -> vector bge-m3
STATE = {"engine": "", "q": 0, "busy": "", "embed": None, "embed_at": 0, "err": "", "started": False}
STATUS_VI = {"note": "Ghi chú", "todo": "Chưa làm", "doing": "Đang làm", "verify": "Chờ verify", "done": "Hoàn thành"}


def _ocr_f():
    return os.path.join(S.DATA, "kb-ocr.json")


def _vec_f():
    return os.path.join(S.DATA, "kb-vec.json")


def _write(path, body):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, path)


def fold(s):
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").replace("Đ", "D").lower()


# ---------- đọc chữ trong ảnh ----------
def _tesseract():
    """(tesseract.exe, thư mục tessdata) khi có gói tiếng Việt, không thì None."""
    exe = shutil.which("tesseract") or next((p for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                                                         r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")
                                             if os.path.exists(p)), None)
    if not exe:
        return None
    for d in (os.path.join(S.ROOT, "models", "tessdata"), os.path.join(os.path.dirname(exe), "tessdata")):
        if os.path.exists(os.path.join(d, "vie.traineddata")):
            return exe, d
    return None


PS_OCR = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$lang = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | Where-Object { $_.LanguageTag -like 'vi*' } | Select-Object -First 1
if ($lang) { $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang) } else { $e = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
$res = @{}
foreach ($p in (Get-Content -LiteralPath '__IN__' -Encoding UTF8)) {
  if (-not $p) { continue }
  try {
    $f = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($p)) ([Windows.Storage.StorageFile])
    $s = Await ($f.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $d = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($s)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $b = Await ($d.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $r = Await ($e.RecognizeAsync($b)) ([Windows.Media.Ocr.OcrResult])
    $res[$p] = (($r.Lines | ForEach-Object { $_.Text }) -join "`n")
    $s.Dispose()
  } catch { $res[$p] = $null }
}
@{ lang = $e.RecognizerLanguage.LanguageTag; items = $res } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath '__OUT__' -Encoding UTF8
"""


def engine():
    """Cách đọc chữ tốt nhất đang có: (tên, điểm). Điểm cao hơn -> ảnh đã đọc bằng cách kém sẽ được đọc lại."""
    if _tesseract():
        return "tesseract-vie", 3
    return "windows", 1  # điểm thật (có tiếng Việt hay không) biết sau lần chạy đầu


def _prep(path, tmpdir, i):
    """Ảnh quá to (Windows OCR giới hạn ~2600px) hoặc chữ quá nhỏ -> đổi cỡ ra file tạm. Không có PIL thì dùng ảnh gốc."""
    try:
        from PIL import Image
        im = Image.open(path)
        w, h = im.size
        k = min(1.0, 2500 / max(w, h)) if max(w, h) > 2500 else (2.0 if max(w, h) < 900 else 1.0)
        if k == 1.0 and im.format in ("PNG", "JPEG", "BMP"):
            return path
        im = im.convert("RGB").resize((max(1, int(w * k)), max(1, int(h * k))))
        out = os.path.join(tmpdir, f"ocr-{i}.png")
        im.save(out)
        return out
    except Exception:
        return path


def _ocr_windows(paths):
    tmp = os.path.join(S.DATA, "kb-tmp")
    os.makedirs(tmp, exist_ok=True)
    real = {_prep(p, tmp, i): p for i, p in enumerate(paths)}
    fin, fout = os.path.join(tmp, "in.txt"), os.path.join(tmp, "out.json")
    with open(fin, "w", encoding="utf-8") as f:
        f.write("\n".join(real))
    if os.path.exists(fout):
        os.remove(fout)
    script = PS_OCR.replace("__IN__", fin.replace("'", "''")).replace("__OUT__", fout.replace("'", "''"))
    enc = base64.b64encode(script.encode("utf-16-le")).decode()
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                   capture_output=True, timeout=60 + 15 * len(paths), creationflags=NOWIN)
    with open(fout, encoding="utf-8-sig") as f:
        j = json.load(f)
    lang = (j.get("lang") or "").lower()
    STATE["engine"], STATE["q"] = f"windows-{lang or '?'}", 3 if lang.startswith("vi") else 1
    items = j.get("items") or {}
    shutil.rmtree(tmp, ignore_errors=True)
    return {real[k]: v for k, v in items.items() if k in real}, STATE["engine"], STATE["q"]


def _ocr_tesseract(paths):
    exe, d = _tesseract()
    langs = "vie+eng" if os.path.exists(os.path.join(d, "eng.traineddata")) else "vie"
    env = dict(os.environ, TESSDATA_PREFIX=d)
    out = {}
    for p in paths:
        try:
            r = subprocess.run([exe, p, "stdout", "-l", langs, "--psm", "3"], capture_output=True, timeout=120,
                               env=env, creationflags=NOWIN)
            out[p] = r.stdout.decode("utf-8", "replace")
        except Exception as e:
            S.log("đọc chữ ảnh lỗi:", p, repr(e))
            out[p] = None
    STATE["engine"], STATE["q"] = "tesseract-vie", 3
    return out, "tesseract-vie", 3


def clean_ocr(t):
    lines = [" ".join(x.split()) for x in (t or "").splitlines()]
    return "\n".join(x for x in lines if len(re.sub(r"[\W_]", "", x)) >= 2)  # bỏ dòng rác 1 ký tự (icon, viền)


def ocr_batch(names):
    paths = [os.path.join(S.IMG, f) for f in names]
    name, _ = engine()
    got, eng, q = (_ocr_tesseract if name.startswith("tesseract") else _ocr_windows)(paths)
    now = dt.datetime.now().isoformat(timespec="seconds")
    with LOCK:
        for f, p in zip(names, paths):
            if got.get(p) is not None:
                OCR[f] = {"text": clean_ocr(got[p]), "engine": eng, "q": q, "at": now}
            else:
                OCR[f] = {"text": "", "engine": eng, "q": q, "at": now, "fail": True}
        body = json.dumps(OCR, ensure_ascii=False, indent=1)
    _write(_ocr_f(), body)  # ghi ngoài LOCK: ổ HDD nghẽn thì chỉ luồng này chờ
    S.REV[0] += 1  # trang điện thoại / app vẽ lại để thấy chữ trong ảnh


def ocr_text(name):
    return (OCR.get(name) or {}).get("text", "")


def ocr_map():
    with LOCK:
        return {k: v.get("text", "") for k, v in OCR.items() if v.get("text")}


def note_ocr(n):
    return "\n".join(ocr_text(f) for f in n.get("images") or [])


# ---------- chia đoạn ----------
def title_of(n):
    t = (n.get("title") or "").strip()
    return t or ((n.get("text") or "").strip().split("\n") or [""])[0][:80] or ("(ảnh)" if n.get("images") else "(trống)")


def _split(text, size=900):
    text = (text or "").strip()
    if len(text) <= size:
        return [text] if text else []
    out, cur = [], ""
    for para in re.split(r"\n\s*\n|\n(?=\S)", text):
        if len(cur) + len(para) > size and cur:
            out.append(cur.strip())
            cur = ""
        while len(para) > size:
            out.append(para[:size])
            para = para[size - 100:]  # chồng 100 ký tự để không cắt mất ý
        cur += para + "\n"
    if cur.strip():
        out.append(cur.strip())
    return out


def chunks(notes=None):
    """Mỗi ghi chú -> các đoạn chữ (tiêu đề + nội dung) + mỗi ảnh có chữ -> 1 đoạn."""
    if notes is None:
        with S.LOCK:
            notes = [dict(n) for n in S.NOTES if not n.get("deleted")]
    out = []
    for n in notes:
        head = title_of(n)
        tag = f"[{n['project']}] " if n.get("project") else ""
        body = (n.get("text") or "").strip()
        if n.get("title") or len(body) > 80:
            parts = _split(body) or [""]
        else:
            parts = [""]  # chữ ngắn đã nằm trọn trong tiêu đề
        for i, p in enumerate(parts):
            out.append({"nid": n["id"], "img": None, "idx": -1, "text": f"{tag}{head}\n{p}".strip()})
        for k, f in enumerate(n.get("images") or []):
            t = ocr_text(f)
            if len(t) >= 8:
                for p in _split(t):
                    out.append({"nid": n["id"], "img": f, "idx": k, "text": f"{tag}{head} (ảnh {k + 1})\n{p}"})
    return out


def _key(text):
    return hashlib.sha1((EMBED + "\n" + text).encode("utf-8")).hexdigest()[:20]


# ---------- Ollama ----------
def _post(path, body, timeout):
    req = urllib.request.Request(OLLAMA + path, method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _models():
    try:
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=3) as r:
            return [m.get("name", "") for m in json.loads(r.read()).get("models", [])]
    except Exception:
        return None


def embed_ready():
    """Có Ollama + bge-m3 không (hỏi lại mỗi 2 phút)."""
    if time.time() - STATE["embed_at"] > 120:
        ms = _models()
        STATE["embed"] = bool(ms) and any(m.split(":")[0] == EMBED for m in ms)
        STATE["embed_at"] = time.time()
    return STATE["embed"]


def embed(texts):
    # num_gpu 0: tính bằng CPU, không chen vào card đồ hoạ (card hay đầy vì Verba / bot khác -> cả máy giật)
    j = _post("/api/embed", {"model": EMBED, "input": [t[:2000] for t in texts], "keep_alive": "10m",
                             "options": {"num_gpu": 0}}, 180)
    return j["embeddings"]


def _cos(a, b):
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb) if na and nb else 0.0


# ---------- chạy nền: đọc ảnh mới + tính vector ----------
def index_once():
    """Làm 1 lượt việc còn thiếu. Trả về True nếu còn việc (để lượt sau chạy ngay)."""
    with S.LOCK:
        names = [f for n in S.NOTES if not n.get("deleted") for f in n.get("images") or []]
    _, best = engine()
    if STATE["q"] and not best > 1:
        best = STATE["q"]
    todo = [f for f in dict.fromkeys(names) if os.path.isfile(os.path.join(S.IMG, f))
            and (f not in OCR or (OCR[f].get("q", 0) < best and not OCR[f].get("fail")))]
    if todo:
        batch = todo[:12]
        STATE["busy"] = f"Đang đọc chữ trong ảnh ({len(todo)} ảnh còn lại)…"
        try:
            ocr_batch(batch)
        except Exception as e:
            STATE["err"] = f"Đọc chữ trong ảnh lỗi: {e!r}"[:300]
            S.log(STATE["err"])
            return False
        return True
    if embed_ready():
        cs = chunks()
        need = [c["text"] for c in cs if _key(c["text"]) not in VEC]
        need = list(dict.fromkeys(need))
        if need:
            STATE["busy"] = f"Đang học nghĩa các ghi chú ({len(need)} đoạn còn lại)…"
            try:
                batch = need[:16]
                vs = embed(batch)
                with LOCK:
                    for t, v in zip(batch, vs):
                        VEC[_key(t)] = [round(x, 5) for x in v]
                    if len(need) <= 16:  # xong hết -> bỏ vector của đoạn đã sửa / đã xoá rồi lưu
                        keep = {_key(c["text"]) for c in cs}
                        for k in [k for k in VEC if k not in keep]:
                            del VEC[k]
                    body = json.dumps(VEC, ensure_ascii=False)
                _write(_vec_f(), body)
                return True
            except Exception as e:
                STATE["err"] = f"Ollama chưa tính được vector: {e!r}"[:300]
                STATE["embed_at"] = time.time()
                STATE["embed"] = False
    STATE["busy"] = ""
    return False


QUIET_S = 30  # đang gõ / đang thêm ghi chú thì chờ yên 30 giây mới đọc ảnh + học nghĩa, khỏi chạy theo từng phím


def _loop():
    seen, changed = -1, 0.0
    while True:
        try:
            if S.REV[0] != seen:
                seen, changed = S.REV[0], time.time()
            if changed and (time.time() - changed >= QUIET_S or STATE["busy"]):
                changed = 0.0 if not STATE["busy"] else changed
                if index_once():
                    time.sleep(0.5)
                    continue
        except Exception as e:
            S.log("kho kiến thức lỗi:", repr(e))
        time.sleep(5)


def start():
    if STATE["started"]:
        return
    STATE["started"] = True
    OCR.update(S.load(_ocr_f(), {}))
    VEC.update(S.load(_vec_f(), {}))
    threading.Thread(target=_loop, daemon=True).start()


def status():
    with S.LOCK:
        names = {f for n in S.NOTES if not n.get("deleted") for f in n.get("images") or []}
    done = sum(1 for f in names if f in OCR)
    with_text = sum(1 for f in names if ocr_text(f))
    return {"images": len(names), "ocr_done": done, "ocr_text": with_text, "engine": STATE["engine"] or engine()[0],
            "vietnamese_ocr": (STATE["q"] or engine()[1]) >= 3, "semantic": bool(STATE["embed"]),
            "vectors": len(VEC), "busy": STATE["busy"], "err": STATE["err"]}


# ---------- tìm ----------
STOP = set("""la cua va cac nhung co khong gi nao ve cho toi minh ban hay mot nhu the thi o trong de duoc voi kien thuc
hoi tim bao nhieu sao lam nay do ay a oi nhe nha xem giup muon biet noi dau dang gom tat ca nao khi nen phai
cai con ra vao len di lai da se dang roi thoi vay the""".split())
WORD = re.compile(r"[0-9a-z]+")


def _terms(q):
    toks = WORD.findall(fold(q))
    keep = [t for t in toks if t not in STOP]
    return keep or toks


def _acronyms(text):
    """Chữ viết tắt từ cụm viết hoa: "Chief of Staff" -> cos, "Build to Own" -> bto."""
    words = re.findall(r"[^\W_]+", text or "")
    out = set()
    for i in range(len(words)):
        if not words[i][:1].isupper():
            continue
        for j in range(i + 1, min(i + 5, len(words))):
            if words[j][:1].isupper():
                out.add(fold("".join(w[0] for w in words[i:j + 1])))
    return out


def _keyword_scores(q, cs):
    terms = _terms(q)
    if not terms:
        return {}
    docs = [WORD.findall(fold(c["text"])) for c in cs]
    acr = [_acronyms(c["text"]) for c in cs]
    N = len(cs) or 1
    avg = sum(len(d) for d in docs) / N or 1
    df = {t: sum(1 for d, a in zip(docs, acr) if t in d or t in a) for t in set(terms)}
    pairs = list(zip(terms, terms[1:]))
    out = {}
    for i, (d, a) in enumerate(zip(docs, acr)):
        s = 0.0
        for t in set(terms):
            tf = d.count(t) + (2 if t in a else 0)
            if not tf:
                continue
            idf = math.log(1 + (N - df[t] + 0.5) / (df[t] + 0.5))
            s += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(d) / avg))
        if s and pairs:  # tiếng Việt ghép 2 tiếng mới thành từ: "chỉ huy", "kiến thức"
            joined = " " + " ".join(d) + " "
            s += sum(1.5 for x, y in pairs if f" {x} {y} " in joined)
        if s:
            out[i] = s
    return out


def search(q, project=None, limit=8):
    """-> [{"note", "score", "hits": [đoạn khớp nhất…]}] theo thứ tự liên quan."""
    with S.LOCK:
        notes = [dict(n) for n in S.NOTES if not n.get("deleted")]
    if project is not None:
        notes = [n for n in notes if (n.get("project") or "") == project]
    cs = chunks(notes)
    if not cs:
        return []
    kw = _keyword_scores(q, cs)
    sem = {}
    if embed_ready():
        try:
            qv = embed([q])[0]
            for i, c in enumerate(cs):
                v = VEC.get(_key(c["text"]))
                if v:
                    sem[i] = _cos(qv, v)
        except Exception as e:
            STATE["err"] = f"Tìm theo nghĩa lỗi: {e!r}"[:300]
    score = {}
    kmax = max(kw.values(), default=0) or 1
    for i, s in kw.items():
        score[i] = 0.6 * s / kmax
    if sem:
        top = sorted(sem.values(), reverse=True)
        floor = max(0.42, top[min(len(top) - 1, 12)])  # chỉ tính các đoạn gần nghĩa nhất
        for i, c in sem.items():
            if c > floor:
                score[i] = score.get(i, 0) + 0.8 * (c - floor) / max(1e-6, top[0] - floor) * min(1.0, (top[0] - 0.35) / 0.25)
    by_note = {}
    for i, s in sorted(score.items(), key=lambda x: -x[1]):
        c = cs[i]
        g = by_note.setdefault(c["nid"], {"score": 0.0, "hits": []})
        if len(g["hits"]) < 3:  # điểm = đoạn khớp nhất + chút thưởng cho đoạn 2, 3 (ghi chú dài không tự cộng dồn)
            g["score"] += s * (1, 0.15, 0.08)[len(g["hits"])]
            g["hits"].append(dict(c, score=round(s, 3), kw=i in kw))
    best = max((g["score"] for g in by_note.values()), default=0)
    notes_by_id = {n["id"]: n for n in notes}
    out = [dict(g, note=notes_by_id[nid]) for nid, g in by_note.items() if g["score"] >= best * 0.25]
    out.sort(key=lambda g: -g["score"])
    return out[:limit]


def snippet(text, q, size=240):
    """Đoạn ngắn quanh chỗ khớp đầu tiên (so bỏ dấu), bỏ dòng tiêu đề đầu."""
    body = text.split("\n", 1)[1] if "\n" in text else text
    flat = " ".join(body.split())
    f = fold(flat)
    pos = -1
    for t in sorted(_terms(q), key=len, reverse=True):
        m = re.search(r"\b" + re.escape(t) + r"\b", f)
        if m:
            pos = m.start()
            break
    start = max(0, pos - size // 3) if pos > 0 else 0
    s = flat[start:start + size]
    return ("…" if start else "") + s + ("…" if start + size < len(flat) else "")


# ---------- hỏi ----------
SYSTEM = """Bạn là trợ lý tra cứu kho ghi chú Idea Note của anh (ghi chú chữ + chữ đọc từ ảnh chụp slide / màn hình).
Quy tắc:
- CHỈ dùng thông tin trong các NGUỒN bên dưới. Không bịa, không thêm kiến thức ngoài.
- Trả lời tiếng Việt, xưng "em", gọi "anh". Ngắn gọn, ưu tiên gạch đầu dòng "- ".
- Mỗi ý phải ghi nguồn ngay cuối câu dạng [1] hoặc [2][3].
- Chữ đọc từ ảnh do máy đọc nên có thể sai dấu hoặc dính chữ: hãy hiểu theo ngữ cảnh và viết lại cho đúng tiếng Việt.
- Mở đầu bằng 1 câu trả lời thẳng vào câu hỏi, sau đó các ý chi tiết. Nguồn nào có nhắc tới chủ đề thì tóm tắt đúng những gì nguồn đó nói.
- Chỉ khi KHÔNG nguồn nào liên quan mới nói "Kho ghi chú chưa có thông tin này". Không vừa nói "chưa có" vừa trả lời.
- Không dùng dấu gạch dài."""


def _chat_model():
    ms = _models() or []
    for want in CHAT:
        if any(m == want or m.startswith(want + "-") for m in ms):
            return want
    return None


def _ctx():
    try:
        import assistant
        return assistant._ctx()
    except Exception:
        return 4096


def ask(q, project=None):
    """Câu hỏi -> {"answer", "sources": [...], "ai": bool, "error"}. AI không chạy thì vẫn trả các ghi chú liên quan."""
    q = " ".join((q or "").split())[:500]
    t0 = time.time()
    if not q:
        return {"answer": "", "sources": [], "ai": False, "error": "Câu hỏi trống"}
    groups = search(q, project, limit=6)
    sources = []
    for g in groups:
        n = g["note"]
        for h in g["hits"][:2]:
            if len(sources) >= 8:
                break
            sources.append({"n": len(sources) + 1, "id": n["id"], "title": title_of(n), "project": n.get("project") or "",
                            "date": n.get("date") or "", "status": STATUS_VI.get(n.get("status"), ""),
                            "img": h["img"], "idx": h["idx"], "images": len(n.get("images") or []),
                            "snippet": snippet(h["text"], q), "text": h["text"]})
    out = {"q": q, "answer": "", "sources": sources, "ai": False, "error": "", "status": status()}
    if not sources:
        out["answer"] = ("Kho ghi chú chưa có gì khớp với câu hỏi này." +
                         (" Máy vẫn đang đọc chữ trong ảnh, hỏi lại sau ít phút nhé." if status()["busy"] else ""))
        return _done(out, t0)
    model = _chat_model()
    if not model:
        out["error"] = "AI trong máy (Ollama + qwen2.5) chưa chạy nên em chỉ liệt kê ghi chú liên quan."
        return _done(out, t0)
    budget, blocks = 5200, []
    for s in sources:
        where = f"chữ trong ảnh {s['idx'] + 1}/{s['images']}" if s["img"] else "nội dung ghi chú"
        head = f"[{s['n']}] Ghi chú “{s['title']}”" + (f" · dự án {s['project']}" if s["project"] else "") + \
               f" · ngày {s['date'][8:10]}/{s['date'][5:7]} · {where}"
        body = s["text"].split("\n", 1)[1] if "\n" in s["text"] else s["text"]
        body = body[:max(200, budget // len(sources))]
        blocks.append(head + "\n" + body)
    msg = [{"role": "system", "content": SYSTEM},
           {"role": "user", "content": "NGUỒN:\n\n" + "\n\n".join(blocks) + f"\n\nCÂU HỎI: {q}"}]
    try:
        j = _post("/api/chat", {"model": model, "messages": msg, "stream": False, "keep_alive": "30m",
                                "options": {"temperature": 0.2, "num_ctx": max(4096, _ctx())}}, 240)
        ans = (j.get("message") or {}).get("content", "").strip()
        out["answer"] = re.sub(r"\s*[—–]\s*", ", ", ans)
        out["ai"] = True
    except Exception as e:
        S.log("hỏi kho kiến thức lỗi:", repr(e))
        out["error"] = f"AI trong máy chưa trả lời được ({type(e).__name__}), em liệt kê ghi chú liên quan bên dưới."
    return _done(out, t0)


def _done(out, t0):
    out["took"] = round(time.time() - t0, 1)
    for s in out["sources"]:
        s.pop("text", None)
    return out
