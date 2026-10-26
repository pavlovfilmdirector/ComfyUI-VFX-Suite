"""
Music generation - ACE-Step 1.5 (MIT) through ComfyUI's native nodes
====================================================================
Song = style tags + lyrics (with [verse] / [chorus] structure tags) + BPM, key, time signature, language and length.
The turbo DiT runs 8 steps; the LM (Qwen 0.6B + 4B) writes the audio codes first ("generate_audio_codes").
Template: workflows/api/music/ace_step15_t2a.json (built from ComfyUI's "Text to Audio (ACE-Step 1.5)" blueprint).

Every track is saved as an mp3 in projects/<id>/music/ with a .json sidecar (all settings), so the library
can show, replay and re-run it. Jobs run in a background thread; the UI polls /vfx/music/job/<id>.
"""
import json
import os
import random
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List

try:
    from .process_runner import ProcessError, get_run_status, load_template, run_process
    from .project_manager import get_project_dir
except ImportError:  # pragma: no cover
    from process_runner import ProcessError, get_run_status, load_template, run_process
    from project_manager import get_project_dir

PROCESS_ID = "music"
MODEL_FILES = {
    "diffusion_models": ["acestep_v1.5_turbo.safetensors"],
    "text_encoders": ["qwen_0.6b_ace15.safetensors", "qwen_4b_ace15.safetensors"],
    "vae": ["ace_1.5_vae.safetensors"],
}
KEYS = [f"{r} {q}" for q in ("major", "minor") for r in ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B")]

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def status() -> Dict[str, Any]:
    """Which ACE-Step 1.5 files ComfyUI can see (any folder registered for that model type)."""
    import folder_paths
    missing = []
    for kind, files in MODEL_FILES.items():
        have = set(os.path.basename(f) for f in folder_paths.get_filename_list(kind))
        missing += [f"{kind}/{f}" for f in files if f not in have]
    return {"ready": not missing, "missing": missing, "keys": KEYS}


def _music_dir(pid: str) -> str:
    d = os.path.join(get_project_dir(pid), "music")
    os.makedirs(d, exist_ok=True)
    return d


def list_tracks(pid: str) -> List[Dict[str, Any]]:
    d = _music_dir(pid)
    out = []
    for f in sorted(os.listdir(d), reverse=True):
        if not f.endswith(".mp3"):
            continue
        meta = {}
        side = os.path.join(d, f[:-4] + ".json")
        if os.path.isfile(side):
            try:
                with open(side, "r", encoding="utf-8") as fh:
                    meta = json.load(fh)
            except (OSError, ValueError):
                meta = {}
        out.append({**meta, "file": f, "url": f"/vfx/project_file/{pid}/music/{f}"})
    return out


def _update(job_id: str, **kw):
    with _LOCK:
        _JOBS[job_id].update(kw)


def get_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else {"id": job_id, "status": "unknown"}


def _run_one(pid: str, p: Dict[str, Any], seed: int, run_tag: str, timeout_s: float = 1800.0) -> Dict[str, Any]:
    import folder_paths
    duration = max(10.0, min(600.0, float(p.get("duration", 60))))
    params = {
        "Song.tags": str(p.get("tags") or "").strip(),
        "Song.lyrics": str(p.get("lyrics") or "").strip() or "[instrumental]",
        "Song.seed": seed,
        "Song.bpm": int(p.get("bpm") or 120),
        "Song.duration": duration,
        "Song.timesignature": str(p.get("timesignature") or "4"),
        "Song.language": str(p.get("language") or "en"),
        "Song.keyscale": str(p.get("keyscale") or "E minor") if str(p.get("keyscale") or "E minor") in KEYS else "E minor",
        "Song.generate_audio_codes": bool(p.get("audio_codes", True)),
        "Length.seconds": duration,
        "Sampler.seed": seed,
        "Sampler.steps": int(p.get("steps") or 8),
    }
    q = run_process(PROCESS_ID, template="ace_step15_t2a", params=params, client_id="keshiki_music", run_tag=run_tag)
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = get_run_status(q["prompt_id"])
        if st["status"] == "cancelled":
            raise ProcessError("Stopped")
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError("Music generation failed: " + ("; ".join(m for m in msgs if m) or str(st.get("errors"))))
        if st["status"] == "done":
            outs = [o for o in st["outputs"] if o.get("kind") == "audio"]
            if not outs:
                raise ProcessError("The workflow finished without audio")
            o = outs[-1]
            src = os.path.join(folder_paths.get_output_directory(), o.get("subfolder", ""), o["filename"])
            name = f"{time.strftime('%Y%m%d_%H%M%S')}_{seed}.mp3"
            dst = os.path.join(_music_dir(pid), name)
            shutil.copy2(src, dst)
            meta = {"title": str(p.get("title") or "").strip() or (params["Song.tags"][:40] or "Track"),
                    "tags": params["Song.tags"], "lyrics": params["Song.lyrics"], "bpm": params["Song.bpm"],
                    "duration": duration, "keyscale": params["Song.keyscale"], "timesignature": params["Song.timesignature"],
                    "language": params["Song.language"], "seed": seed, "seconds": round(time.time() - t0, 1),
                    "created": time.time(), "model": "ACE-Step 1.5 turbo"}
            with open(dst[:-4] + ".json", "w", encoding="utf-8") as fh:
                json.dump(meta, fh, ensure_ascii=False, indent=1)
            return {**meta, "file": name, "url": f"/vfx/project_file/{pid}/music/{name}"}
        time.sleep(1.0)
    raise ProcessError(f"Timed out after {int(timeout_s)} s")


def start(pid: str, p: Dict[str, Any]) -> Dict[str, Any]:
    st = status()
    if not st["ready"]:
        raise ProcessError("ACE-Step 1.5 is not installed: missing " + ", ".join(st["missing"]))
    if not str(p.get("tags") or "").strip() and not str(p.get("lyrics") or "").strip():
        raise ProcessError("Describe the style (tags) or write lyrics")
    job_id = f"mjob_{uuid.uuid4().hex[:10]}"
    variants = max(1, min(4, int(p.get("variants") or 1)))
    base_seed = int(p.get("seed") or random.randint(1, 2**31 - 1))
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "status": "running", "progress": 0, "message": "Starting…", "tracks": [], "started": time.time()}

    def runner():
        try:
            for k in range(variants):
                if get_job(job_id).get("cancel"):
                    raise ProcessError("Stopped")
                _update(job_id, message=f"Track {k + 1}/{variants} (first run loads the models)", progress=int(100 * k / variants))
                tr = _run_one(pid, p, base_seed + k, run_tag=job_id)
                with _LOCK:
                    _JOBS[job_id]["tracks"].append(tr)
            _update(job_id, status="done", progress=100, message="Done")
        except Exception as e:  # noqa: BLE001
            stopped = str(e) == "Stopped"
            _update(job_id, status="cancelled" if stopped else "error", message="Stopped" if stopped else str(e))

    threading.Thread(target=runner, daemon=True).start()
    return get_job(job_id)


def cancel(job_id: str) -> Dict[str, Any]:
    _update(job_id, cancel=True)
    try:
        try:
            from .process_runner import cancel_run
        except ImportError:  # pragma: no cover
            from process_runner import cancel_run
        cancel_run(run_tag=job_id)
    except Exception:  # noqa: BLE001 - the flag above still stops the next variant
        pass
    return get_job(job_id)
