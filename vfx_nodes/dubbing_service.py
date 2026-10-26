"""
Dubbing / voice service
=======================
Runs the KESHIKI dubbing engines (``<repo>/dubbing``, from the Video Dubbing Studio) in their own Python
environment. They need packages that clash with ComfyUI's (VibeVoice wants transformers<5, F5-TTS pins
pydantic), so they never import into this process: each run is a subprocess that prints
``STATUS_UPDATE:{json}`` lines, which become job progress here.

Modes
-----
- ``dub``        video -> transcription (Whisper) -> translation -> text prep -> voice synthesis
                 (F5-TTS / VibeVoice / Qwen3-TTS, voice cloned from the original) -> timing + final mix
                 (``dubbing_engine.py``, stages 0..4, ``start_at_stage`` resumes)
- ``voice``      text -> speech in a cloned voice (``direct_tts_engine.py --mode tts``)
- ``transcribe`` reference audio -> text (``direct_tts_engine.py --mode transcribe``)
- ``narration``  long text / book -> narration (``book_narration_engine.py``)

Machine settings (keshiki_local.json or KESHIKI_* env):
    dubbing_python  python.exe of the dubbing environment (e.g. the studio's python_env)
    dubbing_models  folder with F5-TTS / Whisper / vibevoice / Qwen / nllb models
"""
import json
import os
import subprocess
import threading
import time
import uuid
import shutil
from typing import Any, Dict, Optional

try:
    from .local_config import local_setting, cloud_services_allowed
except ImportError:  # pragma: no cover
    from local_config import local_setting, cloud_services_allowed

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DUB_DIR = os.path.join(BASE_DIR, "dubbing")
DATA_DIR = os.path.join(BASE_DIR, "output_cache", "dubbing")
SCRIPTS = {"dub": "dubbing_engine.py", "voice": "direct_tts_engine.py", "transcribe": "direct_tts_engine.py",
           "narration": "book_narration_engine.py"}

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


class DubbingError(RuntimeError):
    pass


def _python() -> str:
    return local_setting("dubbing_python")


def status() -> Dict[str, Any]:
    py = _python()
    models = local_setting("dubbing_models") or os.path.join(DUB_DIR, "models")
    found = {}
    for sub in ("F5-TTS", "Whisper", "vibevoice", "Qwen", "nllb"):
        d = os.path.join(models, sub)
        found[sub] = sorted(os.listdir(d))[:20] if os.path.isdir(d) else []
    return {
        "ready": bool(py) and os.path.isfile(py),
        "python": py,
        "models_dir": models,
        "models": found,
        "engines": ["f5tts", "vibevoice", "qwen"],
        "translators": ["ollama", "local"] + (["google", "yandex"] if cloud_services_allowed() else []),
        "allow_cloud": cloud_services_allowed(),
        "hint": "" if (py and os.path.isfile(py)) else
                "Set \"dubbing_python\" (and \"dubbing_models\") in keshiki_local.json - see dubbing/README.md",
    }


def defaults(mode: str = "dub") -> Dict[str, Any]:
    name = {"dub": "default_job_settings.json", "voice": "voice_clone_settings.json",
            "transcribe": "voice_clone_settings.json", "narration": "book_settings.json"}.get(mode, "default_job_settings.json")
    with open(os.path.join(DUB_DIR, "config", name), "r", encoding="utf-8") as f:
        cfg = json.load(f)
    if mode == "dub" and not cloud_services_allowed():
        cfg.setdefault("translation", {})["translator"] = "ollama"   # local LLM; "local" = NLLB (downloads once)
    return cfg


def _f5_model() -> str:
    """A fine-tuned F5-TTS checkpoint from the models folder if there is one, else the base F5 v1."""
    d = os.path.join(local_setting("dubbing_models") or os.path.join(DUB_DIR, "models"), "F5-TTS")
    files = sorted(f for f in os.listdir(d) if f.endswith((".pt", ".safetensors"))) if os.path.isdir(d) else []
    return f"model://{files[0]}" if files else "F5v1"


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _update(job_id: str, **kw):
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        j = _JOBS.get(job_id)
        return {k: v for k, v in j.items() if k != "proc"} if j else None


def cancel_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        j = _JOBS.get(job_id)
        proc = j.get("proc") if j else None
    if proc and proc.poll() is None:
        proc.terminate()
    _update(job_id, status="cancelled", message="Stopped")
    return get_job(job_id) or {}


def start(mode: str, config: Dict[str, Any], start_at_stage: int = 0, project_id: str = "") -> Dict[str, Any]:
    if mode not in SCRIPTS:
        raise DubbingError(f"Unknown dubbing mode '{mode}'")
    py = _python()
    if not py or not os.path.isfile(py):
        raise DubbingError(status()["hint"])
    cfg = _deep_merge(defaults(mode), config or {})
    f5 = cfg.get("f5_tts")
    if isinstance(f5, dict) and f5.get("model") in (None, "", "auto"):
        f5["model"] = _f5_model()
    if mode == "dub":
        tr = cfg.get("translation", {})
        if tr.get("translator") in ("google", "yandex") and not cloud_services_allowed():
            raise DubbingError("Google/Yandex translation is a cloud service; use the local translator (ollama / NLLB) "
                               "or opt in with KESHIKI_ALLOW_CLOUD=1")
        cfg.setdefault("player", {})["projects_dir"] = os.path.join(DATA_DIR, "projects")
    job_id = f"dub_{uuid.uuid4().hex[:10]}"
    run_dir = os.path.join(DATA_DIR, "runs", job_id)
    os.makedirs(run_dir, exist_ok=True)
    if mode in ("voice", "narration"):
        cfg.setdefault("output_dir", os.path.join(DATA_DIR, "audio"))
    cfg_path = os.path.join(run_dir, "config.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    args = [py, os.path.join(DUB_DIR, SCRIPTS[mode]), "--config", cfg_path]
    if mode == "dub":
        args += ["--start_at_stage", str(int(start_at_stage))]
    elif mode in ("voice", "transcribe"):
        args += ["--mode", "tts" if mode == "voice" else "transcribe"]

    env = os.environ.copy()
    env["KESHIKI_DUBBING_DATA"] = DATA_DIR
    if local_setting("dubbing_models"):
        env["KESHIKI_DUBBING_MODELS"] = local_setting("dubbing_models")
    env["PYTHONIOENCODING"] = "utf-8"
    ff = shutil.which("ffmpeg")
    if ff:
        env["PATH"] = os.path.dirname(ff) + os.pathsep + env.get("PATH", "")

    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "mode": mode, "status": "running", "progress": 0, "message": "Starting…",
                         "started": time.time(), "log": [], "result": None, "stage": -1, "project_id": project_id}

    def runner():
        try:
            _release_gpu()
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.Popen(args, cwd=DUB_DIR, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags)
            _update(job_id, proc=proc)
            last = {}
            for line in iter(proc.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("STATUS_UPDATE:"):
                    try:
                        msg = json.loads(line[len("STATUS_UPDATE:"):])
                    except ValueError:
                        continue
                    last = msg
                    upd = {"progress": int(msg.get("progress", 0)), "message": str(msg.get("status", ""))}
                    if isinstance(msg.get("stage"), int) and msg["stage"] >= 0:
                        upd["stage"] = msg["stage"]
                    det = msg.get("details")
                    if isinstance(det, dict):
                        upd["details"] = det
                    _update(job_id, **upd)
                else:
                    with _LOCK:
                        log = _JOBS[job_id]["log"]
                        log.append(line[:500])
                        del log[:-200]
            code = proc.wait()
            if get_job(job_id).get("status") == "cancelled":
                return
            det = last.get("details") if isinstance(last.get("details"), dict) else {}
            wav = det.get("output_wav") or det.get("final_wav")
            if code != 0 or (det.get("error")):
                tail = "\n".join((get_job(job_id) or {}).get("log", [])[-8:])
                _update(job_id, status="error", error=det.get("error") or f"exit code {code}\n{tail}")
                return
            result = {"details": det}
            if wav and os.path.isfile(wav):
                result["audio"] = _publish_audio(wav, project_id)
            _update(job_id, status="done", progress=100, result=result, message=last.get("status") or "Done")
        except Exception as e:  # pragma: no cover
            _update(job_id, status="error", error=str(e))

    threading.Thread(target=runner, daemon=True).start()
    return get_job(job_id)


def _publish_audio(wav: str, project_id: str) -> Dict[str, str]:
    """Copy the result into the project (audio folder) so the timeline / LTX audio can use it."""
    name = f"{time.strftime('%Y%m%d_%H%M%S')}_{os.path.basename(wav)}"
    if project_id:
        try:
            from .project_manager import get_project_dir
        except ImportError:  # pragma: no cover
            from project_manager import get_project_dir
        d = os.path.join(get_project_dir(project_id), "audio_tts")
        os.makedirs(d, exist_ok=True)
        shutil.copy2(wav, os.path.join(d, name))
        return {"url": f"/vfx/project_file/{project_id}/audio_tts/{name}", "path": os.path.join(d, name)}
    return {"path": wav, "url": ""}


def _release_gpu():
    """The dubbing models run in another process: unload ComfyUI's models first (24 GB cards)."""
    try:
        import comfy.model_management as mm
        mm.unload_all_models()
        mm.soft_empty_cache()
    except Exception:
        pass
