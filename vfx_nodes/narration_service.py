"""
Voice-over (narration) with paragraph cache
===========================================
Paste a long text, generate, listen, fix a paragraph, generate again: only the paragraphs whose text (or voice
settings) changed are re-voiced, the rest come from the cache, and everything is joined into one track.

Text: paragraphs are separated by an empty line. Voice: F5-TTS clone (the dubbing engines, own env) from a
<= 10 s fragment of a voice sample; the clone copies its timbre and delivery, its words go in ref_text
(typed or recognised by Whisper). Russian text can be prepared first: ё, numbers to words, stress marks (RUAccent).

Doc: projects/<id>/narration/doc.json; paragraph audio: projects/<id>/narration/parts/; result: audio_tts/.
"""
import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, List

try:
    from .project_manager import get_project_dir
    from . import dubbing_service as dub
    from .process_runner import ProcessError
    from .video_layers_service import resolve_media_path
    from .voice_board_service import _duration, _ffmpeg, _transcribe_file, _wait_dub, MAX_REF_S, LANG_NAMES
    from . import voice_board_service as vb
except ImportError:  # pragma: no cover
    from project_manager import get_project_dir
    import dubbing_service as dub
    from process_runner import ProcessError
    from video_layers_service import resolve_media_path
    from voice_board_service import _duration, _ffmpeg, _transcribe_file, _wait_dub, MAX_REF_S, LANG_NAMES
    import voice_board_service as vb


def _dir(pid: str) -> str:
    d = os.path.join(get_project_dir(pid), "narration")
    os.makedirs(os.path.join(d, "parts"), exist_ok=True)
    return d


def load_doc(pid: str) -> Dict[str, Any]:
    p = os.path.join(_dir(pid), "doc.json")
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                d.setdefault("cache", {})
                return d
        except (OSError, ValueError):
            pass
    return {"text": "", "language": "ru", "pause": 0.35, "seed": 985, "cache": {}, "parts": []}


def save_doc(pid: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    old = load_doc(pid)
    doc = {**old, **(doc or {})}
    doc["cache"] = old.get("cache", {})      # the cache is server-owned
    p = os.path.join(_dir(pid), "doc.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)
    return doc


def paragraphs(text: str) -> List[str]:
    out, cur = [], []
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if line.strip():
            cur.append(line.strip())
        elif cur:
            out.append(" ".join(cur)); cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def _key(par: str, doc: Dict[str, Any]) -> str:
    sig = json.dumps([par, doc.get("ref_audio"), doc.get("ref_start"), doc.get("ref_end"), doc.get("ref_text"),
                      doc.get("language"), doc.get("speed", 1.0)], ensure_ascii=False)
    return hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]


def status_parts(pid: str) -> Dict[str, Any]:
    """Which paragraphs are already voiced with the current settings (for the editor's list)."""
    doc = load_doc(pid)
    parts = []
    for i, par in enumerate(paragraphs(doc.get("text", ""))):
        k = _key(par, doc)
        c = doc["cache"].get(k)
        parts.append({"i": i, "text": par, "key": k, "audio": c["url"] if c else None, "seconds": c.get("seconds") if c else None})
    return {**{k: v for k, v in doc.items() if k != "cache"}, "parts": parts}


# ---------------------------------------------------------------- jobs (reuse the voice-board job table)
def _generate(job_id: str, pid: str, only: List[int]) -> Dict[str, Any]:
    doc = load_doc(pid)
    pars = paragraphs(doc.get("text", ""))
    if not pars:
        raise ProcessError("Paste the text first")
    ref = resolve_media_path(doc["ref_audio"]) if doc.get("ref_audio") else ""
    if not ref or not os.path.isfile(ref):
        raise ProcessError("Choose a voice sample")
    lang = str(doc.get("language") or "ru")
    a = max(0.0, float(doc.get("ref_start") or 0))
    b = float(doc.get("ref_end") or 0)
    if b <= a:
        b = a + min(MAX_REF_S, max(0.5, _duration(ref) - a))
    b = min(b, a + MAX_REF_S)
    ref_text = str(doc.get("ref_text") or "").strip()
    if not ref_text:
        vb._update(job_id, message="Transcribing the reference fragment…", progress=2)
        ref_text = _transcribe_file(job_id, pid, ref, a, b, lang, 2, 6)
        doc = load_doc(pid); doc["ref_text"] = ref_text; save_doc(pid, doc); doc = load_doc(pid)
    todo = [i for i, p in enumerate(pars) if (not only or i in only) and (_key(p, doc) not in doc["cache"] or i in (only or []))]
    for n, i in enumerate(todo):
        if vb._cancelled(job_id):
            raise ProcessError("Stopped")
        par = pars[i]
        lo, hi = 6 + int(84 * n / max(1, len(todo))), 6 + int(84 * (n + 1) / max(1, len(todo)))
        vb._update(job_id, message=f"Paragraph {i + 1}/{len(pars)}…", progress=lo)
        cfg = {"load_audio": {"file_path": ref, "start_time": a, "end_time": b},
               "tts_general": {"engine": "F5-TTS", "speaker_ref": ref, "ref_text": ref_text, "target_text": par,
                               "ref_start_time": a, "ref_end_time": b},
               # a forced re-take of one paragraph gets a fresh seed (a new reading), a first voicing uses the doc seed
               "f5_tts": {"seed": (int(time.time() * 1000) % 2**31) if i in (only or []) else int(doc.get("seed") or 985),
                          "speed": float(doc.get("speed") or 1.0)},
               "whisper": {"language": lang, "target_language": LANG_NAMES.get(lang, "Russian")},
               "base_name": f"narr_{i}_{int(time.time())}"}
        j = dub.start("voice", cfg, project_id=pid)
        j = _wait_dub(j["id"], job_id, lo, hi)
        src = ((j.get("result") or {}).get("audio") or {}).get("path")
        if not src or not os.path.isfile(src):
            raise ProcessError(f"Paragraph {i + 1}: the voice engine produced no audio")
        k = _key(par, doc)
        name = f"{k}.wav"
        shutil.copy2(src, os.path.join(_dir(pid), "parts", name))
        doc = load_doc(pid)
        doc["cache"][k] = {"url": f"/vfx/project_file/{pid}/narration/parts/{name}", "seconds": round(_duration(src), 2)}
        p = os.path.join(_dir(pid), "doc.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
    # join all paragraphs (cached + new) with a short pause
    vb._update(job_id, message="Joining the paragraphs…", progress=92)
    doc = load_doc(pid)
    files = []
    for p_ in pars:
        c = doc["cache"].get(_key(p_, doc))
        if c:
            files.append(os.path.join(_dir(pid), "parts", os.path.basename(c["url"])))
    if not files:
        raise ProcessError("Nothing voiced yet")
    ff = _ffmpeg()
    out_dir = os.path.join(get_project_dir(pid), "audio_tts")
    os.makedirs(out_dir, exist_ok=True)
    name = f"voiceover_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    out = os.path.join(out_dir, name)
    pause = max(0.0, float(doc.get("pause") or 0.35))
    args, chain = [ff, "-y", "-loglevel", "error"], []
    for k, f in enumerate(files):
        args += ["-i", f]
        chain.append(f"[{k}]aresample=44100,aformat=channel_layouts=mono,apad=pad_dur={pause:.2f}[p{k}]")
    chain.append("".join(f"[p{k}]" for k in range(len(files))) + f"concat=n={len(files)}:v=0:a=1[out]")
    subprocess.run(args + ["-filter_complex", ";".join(chain), "-map", "[out]", out], check=True)
    url = f"/vfx/project_file/{pid}/audio_tts/{name}"
    doc["result"] = url
    p = os.path.join(_dir(pid), "doc.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    return {"audio": url, "seconds": round(_duration(out), 1), "voiced": len(todo), "cached": len(pars) - len(todo)}


def start_generate(pid: str, only: List[int] = None) -> Dict[str, Any]:
    return vb._start(_generate, pid, list(only or []))


def _prepare(job_id: str, pid: str, add_stress: bool) -> Dict[str, Any]:
    """Russian text preparation: ё, numbers to words, optional stress marks (RUAccent) -> back into the doc."""
    doc = load_doc(pid)
    j = dub.start("text", {"text": doc.get("text", ""), "add_stress": bool(add_stress), "fix_ortho": True}, project_id=pid)
    j = _wait_dub(j["id"], job_id, 5, 95)
    txt = ((j.get("details") or {}).get("processed_text")) or ""
    if not txt:
        raise ProcessError("Text preparation returned nothing")
    # keep the paragraph breaks: the engine works on the whole text, so prepare paragraph by paragraph if lost
    if "\n\n" in doc.get("text", "") and "\n\n" not in txt:
        out = []
        for par in paragraphs(doc.get("text", "")):
            jj = dub.start("text", {"text": par, "add_stress": bool(add_stress), "fix_ortho": True}, project_id=pid)
            jj = _wait_dub(jj["id"], job_id, 50, 95)
            out.append(((jj.get("details") or {}).get("processed_text")) or par)
        txt = "\n\n".join(out)
    doc = load_doc(pid)
    doc["text"] = txt
    save_doc(pid, doc)
    return {"text": txt}


def start_prepare(pid: str, add_stress: bool = True) -> Dict[str, Any]:
    return vb._start(_prepare, pid, add_stress)


def _transcribe_ref(job_id: str, pid: str) -> Dict[str, Any]:
    doc = load_doc(pid)
    ref = resolve_media_path(doc["ref_audio"]) if doc.get("ref_audio") else ""
    if not ref:
        raise ProcessError("Choose a voice sample")
    a = max(0.0, float(doc.get("ref_start") or 0))
    b = float(doc.get("ref_end") or 0)
    if b <= a:
        b = a + MAX_REF_S
    txt = _transcribe_file(job_id, pid, ref, a, min(b, a + MAX_REF_S), str(doc.get("language") or "ru"), 5, 95)
    doc = load_doc(pid); doc["ref_text"] = txt; save_doc(pid, doc)
    return {"ref_text": txt}


def start_transcribe_ref(pid: str) -> Dict[str, Any]:
    return vb._start(_transcribe_ref, pid)
