"""
Voice board ("холст озвучки")
=============================
A board of cards: each card is a project image or video. A card carries cues; a cue is one line of speech:
  * text + a voice sample; the clone copies timbre AND delivery of a <= 10 s fragment of it (ref_start/ref_end),
    whose words (ref_text) are typed or recognised by Whisper,
  * or a draft take (your own rough read): it gives the timing, its words when no text is typed, and can be the
    reference itself (ref_source = "draft") when the line must follow its intonation,
  * start (s) inside the card and an optional target duration (s).
Per cue:   voice     -> F5-TTS voice clone (the dubbing engines, own env), fitted to the duration
Per card:  lipsync   -> image card: LTX-2.5 audio-to-video, the lips follow the cue's voice (first frame = the image)
           mix       -> video card: the cues' voices are laid onto the video at their start times
           dubit     -> video card: LTX-2.3 DubIt IC-LoRA, the speaker says the cues' words (new lips + voice)
Board file: projects/<id>/voice_board/board.json. Results go to projects/<id>/audio_tts and source_video.
"""
import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import wave
from typing import Any, Dict, List, Optional

try:
    from .project_manager import get_project_dir
    from . import dubbing_service as dub
    from .process_runner import ProcessError, get_run_status, load_template, run_process
    from .video_layers_service import resolve_media_path
except ImportError:  # pragma: no cover
    from project_manager import get_project_dir
    import dubbing_service as dub
    from process_runner import ProcessError, get_run_status, load_template, run_process
    from video_layers_service import resolve_media_path

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------- board file
def _board_path(pid: str) -> str:
    d = os.path.join(get_project_dir(pid), "voice_board")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "board.json")


def load_board(pid: str) -> Dict[str, Any]:
    p = _board_path(pid)
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                b = json.load(f)
            if isinstance(b, dict):
                b.setdefault("cards", [])
                return b
        except (OSError, ValueError):
            pass
    return {"cards": [], "language": "ru"}


def save_board(pid: str, board: Dict[str, Any]) -> Dict[str, Any]:
    board = board if isinstance(board, dict) else {}
    board.setdefault("cards", [])
    p = _board_path(pid)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(board, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return board


def _find(board, card_id, cue_id=None):
    card = next((c for c in board["cards"] if c.get("id") == card_id), None)
    if card is None:
        raise ProcessError("Card not found")
    if cue_id is None:
        return card, None
    cue = next((q for q in card.get("cues", []) if q.get("id") == cue_id), None)
    if cue is None:
        raise ProcessError("Cue not found")
    return card, cue


# ---------------------------------------------------------------- helpers
def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return shutil.which("ffmpeg") or ""


def _duration(path: str) -> float:
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001 - not a wav: ask ffmpeg
        ff = _ffmpeg()
        if not ff:
            return 0.0
        out = subprocess.run([ff, "-i", path], capture_output=True, text=True, errors="replace").stderr
        import re
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def _fit(path: str, target: float) -> str:
    """Time-stretch a wav to `target` seconds (pitch kept; atempo 0.5..2 per stage)."""
    cur = _duration(path)
    ff = _ffmpeg()
    if not ff or cur <= 0 or target <= 0 or abs(cur - target) < 0.05:
        return path
    r = cur / target
    chain = []
    while r > 2.0:
        chain.append("atempo=2.0"); r /= 2.0
    while r < 0.5:
        chain.append("atempo=0.5"); r /= 0.5
    chain.append(f"atempo={r:.4f}")
    out = path[:-4] + f"_fit{target:.2f}s.wav"
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", path, "-filter:a", ",".join(chain), out], check=True)
    return out


def _wait_dub(job_id: str, parent: str, lo: int, hi: int) -> Dict[str, Any]:
    while True:
        j = dub.get_job(job_id) or {}
        if _cancelled(parent):
            dub.cancel_job(job_id)
            raise ProcessError("Stopped")
        _update(parent, progress=lo + int((hi - lo) * int(j.get("progress", 0)) / 100), message=str(j.get("message", ""))[:120])
        if j.get("status") in ("done", "error", "cancelled"):
            if j.get("status") != "done":
                raise ProcessError(str(j.get("error") or j.get("message") or j.get("status")))
            return j
        time.sleep(1.0)


def _update(job_id: str, **kw):
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def _cancelled(job_id: str) -> bool:
    with _LOCK:
        return bool(_JOBS.get(job_id, {}).get("cancel"))


def get_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else {"id": job_id, "status": "unknown"}


def cancel(job_id: str) -> Dict[str, Any]:
    _update(job_id, cancel=True)
    return get_job(job_id)


def _start(fn, *args) -> Dict[str, Any]:
    job_id = f"vbjob_{uuid.uuid4().hex[:10]}"
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "status": "running", "progress": 0, "message": "Starting…", "started": time.time()}

    def runner():
        try:
            res = fn(job_id, *args)
            _update(job_id, status="done", progress=100, message="Done", result=res)
        except Exception as e:  # noqa: BLE001
            stopped = str(e) == "Stopped"
            _update(job_id, status="cancelled" if stopped else "error", message="Stopped" if stopped else str(e))

    threading.Thread(target=runner, daemon=True).start()
    return get_job(job_id)


# ---------------------------------------------------------------- voice for one cue
LANG_NAMES = {"ru": "Russian", "en": "English", "es": "Spanish", "fr": "French", "de": "German"}


MAX_REF_S = 10.0   # F5-TTS clones best from a short, clean fragment; longer references get cut


def _ref_window(cue: Dict[str, Any], path: str) -> (float, float):
    """The reference fragment: [ref_start, ref_end] seconds, at most MAX_REF_S long."""
    a = max(0.0, float(cue.get("ref_start") or 0))
    b = float(cue.get("ref_end") or 0)
    if b <= a:
        b = a + min(MAX_REF_S, max(0.5, _duration(path) - a))
    return a, min(b, a + MAX_REF_S)


def _transcribe_file(job_id: str, pid: str, path: str, a: float, b: float, lang: str, lo: int, hi: int) -> str:
    j = dub.start("transcribe", {"load_audio": {"file_path": path, "start_time": a, "end_time": b},
                                 "whisper": {"language": lang, "target_language": "None"}}, project_id=pid)
    j = _wait_dub(j["id"], job_id, lo, hi)
    line = next((l for l in reversed(j.get("log", [])) if l.startswith("TRANSCRIBE_RESULT:")), "")
    return line[len("TRANSCRIBE_RESULT:"):].strip()


def _ref_of(cue: Dict[str, Any]) -> str:
    """Which take is the clone reference: the voice sample, or the draft (cue.ref_source == "draft")."""
    src = cue.get("draft_audio") if cue.get("ref_source") == "draft" else (cue.get("ref_audio") or cue.get("draft_audio"))
    return resolve_media_path(src) if src else ""


def _transcribe(job_id: str, pid: str, card_id: str, cue_id: str, which: str) -> Dict[str, Any]:
    """which = "ref": words of the reference fragment (needed by the clone); "draft": the line's text from the draft."""
    board = load_board(pid)
    card, cue = _find(board, card_id, cue_id)
    lang = str(cue.get("language") or board.get("language") or "ru")
    if which == "draft":
        path = resolve_media_path(cue["draft_audio"]) if cue.get("draft_audio") else ""
        if not path:
            raise ProcessError("No draft take on this line")
        text = _transcribe_file(job_id, pid, path, 0, 999999, lang, 5, 95)
        key = "text"
    else:
        path = _ref_of(cue)
        if not path:
            raise ProcessError("No voice sample on this line")
        a, b = _ref_window(cue, path)
        text = _transcribe_file(job_id, pid, path, a, b, lang, 5, 95)
        key = "ref_text"
    if not text:
        raise ProcessError("Whisper heard no words there")
    board = load_board(pid)
    card, cue = _find(board, card_id, cue_id)
    cue[key] = text
    save_board(pid, board)
    return {key: text}


def start_transcribe(pid: str, card_id: str, cue_id: str, which: str = "ref") -> Dict[str, Any]:
    return _start(_transcribe, pid, card_id, cue_id, which)


def _voice(job_id: str, pid: str, card_id: str, cue_id: str) -> Dict[str, Any]:
    board = load_board(pid)
    card, cue = _find(board, card_id, cue_id)
    if cue.get("speech"):   # a ready recording is the line itself: no synthesis, only the timing fit
        path = resolve_media_path(cue["speech"])
        url = cue["speech"]
        target = float(cue.get("duration") or 0)
        if target > 0:
            fitted = _fit(path, target)
            if fitted != path:
                d = os.path.join(get_project_dir(pid), "audio_tts"); os.makedirs(d, exist_ok=True)
                name = os.path.basename(fitted); shutil.copy2(fitted, os.path.join(d, name))
                path, url = os.path.join(d, name), f"/vfx/project_file/{pid}/audio_tts/{name}"
        board = load_board(pid); card, cue2 = _find(board, card_id, cue_id)
        cue2["audio"] = url; cue2["audio_seconds"] = round(_duration(path), 2); save_board(pid, board)
        return {"audio": url, "text": cue.get("text", ""), "seconds": cue2["audio_seconds"]}
    lang = str(cue.get("language") or board.get("language") or "ru")
    draft = resolve_media_path(cue["draft_audio"]) if cue.get("draft_audio") else ""
    ref = _ref_of(cue)
    text = str(cue.get("text") or "").strip()
    if not ref:
        raise ProcessError("Add a voice sample (or at least a draft take) to this line")
    if not text and draft:   # the line's words from the draft take
        _update(job_id, message="Transcribing the draft take…", progress=5)
        text = _transcribe_file(job_id, pid, draft, 0, 999999, lang, 5, 20)
        if not text:
            raise ProcessError("Could not read words from the draft take; type the text")
    if not text:
        raise ProcessError("Type the text of the line (or record a draft take)")
    # the clone copies timbre AND delivery (intonation, pace, emotion) of the reference fragment, so pick a
    # fragment (<= 10 s) said the way the line should sound; its exact words make the clone much cleaner
    ra, rb = _ref_window(cue, ref)
    ref_text = str(cue.get("ref_text") or "").strip()
    if not ref_text:
        _update(job_id, message="Transcribing the reference fragment…", progress=22)
        ref_text = _transcribe_file(job_id, pid, ref, ra, rb, lang, 22, 30)
    _update(job_id, message="Cloning the voice (F5-TTS)…", progress=30)
    cfg = {"load_audio": {"file_path": ref, "start_time": ra, "end_time": rb},
           "tts_general": {"engine": "F5-TTS", "speaker_ref": ref, "ref_text": ref_text,
                           "target_text": text, "ref_start_time": ra, "ref_end_time": rb},
           "whisper": {"language": lang, "target_language": LANG_NAMES.get(lang, "Russian")},
           "base_name": f"cue_{cue_id}_{int(time.time())}"}
    j = dub.start("voice", cfg, project_id=pid)
    j = _wait_dub(j["id"], job_id, 30, 90)
    audio = (j.get("result") or {}).get("audio") or {}
    path = audio.get("path")
    if not path or not os.path.isfile(path):
        raise ProcessError("The voice engine produced no audio")
    target = float(cue.get("duration") or 0)
    if target <= 0 and draft:    # the draft take sets the timing
        target = _duration(draft)
    if target > 0:
        _update(job_id, message="Fitting the line to its duration…", progress=92)
        fitted = _fit(path, target)
        if fitted != path:
            name = os.path.basename(fitted)
            path = fitted
            audio = {"url": f"/vfx/project_file/{pid}/audio_tts/{name}", "path": fitted}
    board = load_board(pid)   # reload: the user may have edited other cards meanwhile
    card, cue2 = _find(board, card_id, cue_id)
    cue2["text"] = text
    cue2["ref_text"] = ref_text
    cue2["audio"] = audio["url"]
    cue2["audio_seconds"] = round(_duration(path), 2)
    save_board(pid, board)
    return {"audio": audio["url"], "text": text, "seconds": cue2["audio_seconds"]}


def start_voice(pid: str, card_id: str, cue_id: str) -> Dict[str, Any]:
    return _start(_voice, pid, card_id, cue_id)


# ---------------------------------------------------------------- lipsync (image card) with LTX-2.5 A2V
def _to_input(src_path: str, tag: str) -> str:
    import folder_paths
    ext = os.path.splitext(src_path)[1].lower() or ".bin"
    name = f"keshiki_{tag}_{uuid.uuid4().hex[:8]}{ext}"
    shutil.copy2(src_path, os.path.join(folder_paths.get_input_directory(), name))
    return name


def _cue_track(pid: str, card: Dict[str, Any], fps: float) -> (str, float):
    """All voiced cues of a card on one wav, each at its start time; returns (path, seconds)."""
    ff = _ffmpeg()
    cues = [q for q in card.get("cues", []) if q.get("audio")]
    if not cues:
        raise ProcessError("Generate the voice of at least one line first")
    paths = [resolve_media_path(q["audio"]) for q in cues]
    if len(cues) == 1 and float(cues[0].get("start") or 0) <= 0:
        return paths[0], _duration(paths[0])
    if not ff:
        raise ProcessError("ffmpeg not found")
    os.makedirs(os.path.join(get_project_dir(pid), "audio_tts"), exist_ok=True)   # a card with ready speech only has no folder yet
    out = os.path.join(get_project_dir(pid), "audio_tts", f"card_{card['id']}_{int(time.time())}.wav")
    args, mix = [ff, "-y", "-loglevel", "error"], []
    for i, (q, p) in enumerate(zip(cues, paths)):
        args += ["-i", p]
        ms = int(max(0.0, float(q.get("start") or 0)) * 1000)
        mix.append(f"[{i}]adelay={ms}|{ms}[a{i}]")
    mix.append("".join(f"[a{i}]" for i in range(len(cues))) + f"amix=inputs={len(cues)}:normalize=0[out]")
    subprocess.run(args + ["-filter_complex", ";".join(mix), "-map", "[out]", out], check=True)
    return out, _duration(out)


def _lipsync(job_id: str, pid: str, card_id: str, prompt: str, seed: Optional[int]) -> Dict[str, Any]:
    import folder_paths
    board = load_board(pid)
    card, _ = _find(board, card_id)
    if card.get("kind") != "image":
        raise ProcessError("Lip-sync from a still works on image cards; for a video card use Mix")
    img = resolve_media_path(card.get("media", ""))
    if not img or not os.path.isfile(img):
        raise ProcessError("The card's image was not found")
    fps = 24.0
    track, secs = _cue_track(pid, card, fps)
    secs = max(1.0, min(secs + 0.3, 20.0))
    frames = 1 + int(secs * fps // 8) * 8
    _update(job_id, message=f"LTX-2.5 audio-to-video: {frames} frames (first run loads the model)", progress=10)
    try:   # free VRAM held by other models
        import comfy.model_management as mm
        mm.unload_all_models(); mm.soft_empty_cache()
    except Exception:  # noqa: BLE001
        pass
    params = {
        "Image.image": _to_input(img, "a2v_img"), "Audio.audio": _to_input(track, "a2v_aud"),
        "Trim.duration": secs, "Length.length": frames, "Conditioning.frame_rate": fps, "Video.fps": fps,
        "Prompt.text": prompt or str(card.get("prompt") or "") or
        "The person in the image speaks naturally to the camera, lips precisely synced to the speech, subtle head and eye motion, realistic.",
        "Seed.noise_seed": int(seed or int(time.time()) % 2**31),
    }
    q = run_process("video_fx", prompt=load_template("voice", "ltx25_a2v"), params=params,
                    client_id="keshiki_voice", run_tag=job_id)
    t0 = time.time()
    while True:
        if _cancelled(job_id):
            try:
                from .process_runner import cancel_run
            except ImportError:  # pragma: no cover
                from process_runner import cancel_run
            cancel_run(run_tag=job_id)
            raise ProcessError("Stopped")
        st = get_run_status(q["prompt_id"])
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError("Lip-sync failed: " + ("; ".join(m for m in msgs if m) or str(st.get("errors"))))
        if st["status"] == "cancelled":
            raise ProcessError("Stopped")
        if st["status"] == "done":
            outs = [o for o in st["outputs"] if o.get("kind") in ("images", "videos", "gifs") and str(o.get("filename", "")).endswith(".mp4")]
            if not outs:
                raise ProcessError("The workflow finished without a video")
            o = outs[-1]
            src = os.path.join(folder_paths.get_output_directory(), o.get("subfolder", ""), o["filename"])
            break
        _update(job_id, message=f"LTX-2.5 lip-sync… {int(time.time() - t0)} s", progress=min(95, 10 + int((time.time() - t0) / 3)))
        time.sleep(2.0)
    folder = os.path.join(get_project_dir(pid), "source_video")
    os.makedirs(folder, exist_ok=True)
    name = f"lipsync_{card_id}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    shutil.copy2(src, os.path.join(folder, name))
    url = f"/vfx/project_file/{pid}/source_video/{name}"
    board = load_board(pid)
    card, _ = _find(board, card_id)
    card["result_video"] = url
    save_board(pid, board)
    return {"video": url, "frames": frames, "seconds": round(secs, 2)}


def start_lipsync(pid: str, card_id: str, prompt: str = "", seed: Optional[int] = None) -> Dict[str, Any]:
    return _start(_lipsync, pid, card_id, prompt, seed)


# ---------------------------------------------------------------- mix (video card): voices onto the video
def _mix(job_id: str, pid: str, card_id: str, keep_original: bool) -> Dict[str, Any]:
    board = load_board(pid)
    card, _ = _find(board, card_id)
    vid = resolve_media_path(card.get("media", ""))
    if not vid or not os.path.isfile(vid):
        raise ProcessError("The card's video was not found")
    ff = _ffmpeg()
    if not ff:
        raise ProcessError("ffmpeg not found")
    track, _secs = _cue_track(pid, card, 24.0)
    _update(job_id, message="Mixing the voices onto the video…", progress=40)
    folder = os.path.join(get_project_dir(pid), "source_video")
    os.makedirs(folder, exist_ok=True)
    name = f"voiced_{card_id}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    out = os.path.join(folder, name)
    has_audio = "Audio:" in subprocess.run([ff, "-i", vid], capture_output=True, text=True, errors="replace").stderr
    if keep_original and has_audio:
        fc = "[0:a]volume=0.35[bg];[bg][1:a]amix=inputs=2:normalize=0:duration=first[a]"
        args = [ff, "-y", "-loglevel", "error", "-i", vid, "-i", track, "-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
    else:
        args = [ff, "-y", "-loglevel", "error", "-i", vid, "-i", track, "-map", "0:v", "-map", "1:a"]
    subprocess.run(args + ["-c:v", "copy", "-c:a", "aac", out], check=True)
    url = f"/vfx/project_file/{pid}/source_video/{name}"
    board = load_board(pid)
    card, _ = _find(board, card_id)
    card["result_video"] = url
    save_board(pid, board)
    return {"video": url}


def start_mix(pid: str, card_id: str, keep_original: bool = True) -> Dict[str, Any]:
    return _start(_mix, pid, card_id, keep_original)


# ---------------------------------------------------------------- DubIt (video card): new words, new lips
LANG_SAY = {"ru": "Russian", "en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian",
            "pt": "Portuguese", "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "uk": "Ukrainian"}


def _dubit(job_id: str, pid: str, card_id: str, desc: str, start: float, max_s: float, seed: Optional[int]) -> Dict[str, Any]:
    """LTX-2.3 DubIt IC-LoRA: the speaker in the clip says the card's lines instead (translation or rephrasing);
    lips and voice are generated together, the voice stays close to the original speaker. One speaker; the new
    text should be about as long as the original speech. Single stage (no upscaler) to fit 24 GB."""
    import folder_paths
    import cv2
    board = load_board(pid)
    card, _ = _find(board, card_id)
    if card.get("kind") != "video":
        raise ProcessError("DubIt works on video cards")
    vid = resolve_media_path(card.get("media", ""))
    if not vid or not os.path.isfile(vid):
        raise ProcessError("The card's video was not found")
    text = " ".join(str(q.get("text") or "").strip() for q in card.get("cues", []) if str(q.get("text") or "").strip())
    if not text:
        raise ProcessError("Type the new words in the card's lines (in the target language's own script)")
    lang = str(board.get("language") or "ru")
    cap = cv2.VideoCapture(vid)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 24) or 24
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    start = max(0.0, float(start or 0))
    avail = max(0.0, total / fps - start) if total else max_s
    secs = max(1.0, min(float(max_s or 5.0), avail))
    frames = 1 + int(secs * fps - 1) // 8 * 8
    secs = frames / fps
    ff = _ffmpeg()
    if not ff:
        raise ProcessError("ffmpeg not found")
    name = f"keshiki_dubit_{uuid.uuid4().hex[:8]}.mp4"
    clip = os.path.join(folder_paths.get_input_directory(), name)
    subprocess.run([ff, "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", vid, "-t", f"{secs + 0.05:.3f}",
                    "-c:v", "libx264", "-crf", "16", "-c:a", "aac", clip], check=True)
    say = LANG_SAY.get(lang, "Russian")
    prompt = f'{(desc or card.get("prompt") or "The person in the video").strip()}, speaking in {say} saying: "{text}"'
    _update(job_id, message=f"DubIt (LTX-2.3): {frames} frames (first run loads the model)", progress=10)
    try:
        import comfy.model_management as mm
        mm.unload_all_models(); mm.soft_empty_cache()
    except Exception:  # noqa: BLE001
        pass
    params = {"Source Video.file": name, "Prompt.text": prompt, "Length.length": frames, "Audio length.frames_number": frames,
              "Seed.noise_seed": int(seed or int(time.time()) % 2**31)}
    q = run_process("video_fx", prompt=load_template("voice", "ltx23_dubit"), params=params, client_id="keshiki_voice", run_tag=job_id)
    src = _wait_video(job_id, q["prompt_id"], "DubIt")
    folder = os.path.join(get_project_dir(pid), "source_video")
    os.makedirs(folder, exist_ok=True)
    out = f"dubit_{card_id}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
    shutil.copy2(src, os.path.join(folder, out))
    url = f"/vfx/project_file/{pid}/source_video/{out}"
    board = load_board(pid)
    card, _ = _find(board, card_id)
    card["result_video"] = url
    save_board(pid, board)
    return {"video": url, "frames": frames, "prompt": prompt}


def _wait_video(job_id: str, prompt_id: str, what: str) -> str:
    import folder_paths
    t0 = time.time()
    while True:
        if _cancelled(job_id):
            try:
                from .process_runner import cancel_run
            except ImportError:  # pragma: no cover
                from process_runner import cancel_run
            cancel_run(run_tag=job_id)
            raise ProcessError("Stopped")
        st = get_run_status(prompt_id)
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError(f"{what} failed: " + ("; ".join(m for m in msgs if m) or str(st.get("errors"))))
        if st["status"] == "cancelled":
            raise ProcessError("Stopped")
        if st["status"] == "done":
            outs = [o for o in st["outputs"] if str(o.get("filename", "")).lower().endswith(".mp4")]
            if not outs:
                raise ProcessError("The workflow finished without a video")
            o = outs[-1]
            return os.path.join(folder_paths.get_output_directory(), o.get("subfolder", ""), o["filename"])
        _update(job_id, message=f"{what}… {int(time.time() - t0)} s", progress=min(95, 10 + int((time.time() - t0) / 3)))
        time.sleep(2.0)


def start_dubit(pid: str, card_id: str, desc: str = "", start: float = 0.0, max_s: float = 5.0, seed: Optional[int] = None) -> Dict[str, Any]:
    return _start(_dubit, pid, card_id, desc, start, max_s, seed)


# ---------------------------------------------------------------- words of any audio fragment (canvas audio nodes)
def _asr(job_id: str, pid: str, url: str, a: float, b: float, lang: str) -> Dict[str, Any]:
    path = resolve_media_path(url)
    if not path or not os.path.isfile(path):
        raise ProcessError("Audio not found")
    a = max(0.0, float(a or 0))
    b = float(b or 0)
    if b <= a:
        b = a + MAX_REF_S
    text = _transcribe_file(job_id, pid, path, a, min(b, a + MAX_REF_S) if b - a > 0 else b, lang or "ru", 5, 95)
    if not text:
        raise ProcessError("Whisper heard no words there")
    return {"text": text}


def start_asr(pid: str, url: str, a: float = 0, b: float = 0, lang: str = "ru", whole: bool = False) -> Dict[str, Any]:
    if whole:
        return _start(lambda job_id, *x: {"text": _transcribe_file(job_id, pid, resolve_media_path(url), 0, 999999, lang, 5, 95)})
    return _start(_asr, pid, url, a, b, lang)
