"""
Text-to-motion with NVIDIA Kimodo (https://github.com/nv-tlabs/kimodo, code Apache-2.0,
SOMA models under the NVIDIA Open Model License; its text encoder is Llama-3.1-8B + LLM2Vec, gated by Meta).

Kimodo lives in its own Python environment (it pins transformers 5.1 and needs ~17 GB of VRAM), so the studio
runs it as a separate process and reads back a BVH of the SOMA skeleton. The 3D editor retargets that BVH onto
any character (KESHIKI mannequin, UE / Mixamo rigs).

keshiki_local.json (or env KESHIKI_KIMODO_ROOT / KESHIKI_KIMODO_PYTHON):
    "kimodo_root":   "G:/AI/Antigravity/projects/kimodo"
    "kimodo_python": "G:/AI/Antigravity/projects/kimodo/venv/Scripts/python.exe"   (optional, found from root)
    "kimodo_model":  "kimodo-soma-rp"                                                (optional)
"""
import os
import re
import subprocess
import threading
import time
import uuid
from typing import Any, Dict, Optional

try:
    from .local_config import local_setting
    from .project_manager import get_project_dir
except ImportError:  # pragma: no cover
    from local_config import local_setting
    from project_manager import get_project_dir

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()          # one Kimodo process at a time (it fills most of a 24 GB card)


def kimodo_paths() -> Dict[str, str]:
    root = local_setting("kimodo_root", "")
    py = local_setting("kimodo_python", "")
    if root and not py:
        for cand in (os.path.join(root, "venv", "Scripts", "python.exe"), os.path.join(root, "venv", "bin", "python"),
                     os.path.join(root, ".venv", "Scripts", "python.exe"), os.path.join(root, ".venv", "bin", "python")):
            if os.path.isfile(cand):
                py = cand
                break
    return {"root": root, "python": py, "model": local_setting("kimodo_model", "")}


def kimodo_status() -> Dict[str, Any]:
    p = kimodo_paths()
    ok = bool(p["root"] and os.path.isdir(p["root"]) and p["python"] and os.path.isfile(p["python"]))
    return {"available": ok, **p,
            "hint": "" if ok else "Kimodo is not configured: set kimodo_root (and kimodo_python) in keshiki_local.json"}


def _safe_name(text: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", text.strip())[:40].strip("_")
    return s or "motion"


def start_motion(project_id: str, prompt: str, duration: float = 5.0, seed: Optional[int] = None,
                 steps: Optional[int] = None) -> Dict[str, Any]:
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("Describe the motion (prompt is empty)")
    st = kimodo_status()
    if not st["available"]:
        raise ValueError(st["hint"])
    duration = max(1.0, min(30.0, float(duration or 5.0)))
    job_id = f"kmo_{uuid.uuid4().hex[:10]}"
    out_dir = os.path.join(get_project_dir(project_id), "motions")
    os.makedirs(out_dir, exist_ok=True)
    stem = f"{time.strftime('%Y%m%d_%H%M%S')}_{_safe_name(prompt)}"
    job = {"id": job_id, "project_id": project_id, "prompt": prompt, "duration": duration, "status": "running",
           "message": "Starting Kimodo…", "progress": 2, "log": [], "bvh_url": None, "error": None,
           "start_time": time.time()}
    with _LOCK:
        _JOBS[job_id] = job
    threading.Thread(target=_run, args=(job_id, st, out_dir, stem, prompt, duration, seed, steps), daemon=True).start()
    return {"job_id": job_id}


def _set(job_id: str, **kw):
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id].update(kw)


def _run(job_id, st, out_dir, stem, prompt, duration, seed, steps):
    out_base = os.path.join(out_dir, stem)
    cmd = [st["python"], "-m", "kimodo.scripts.generate", prompt, "--duration", f"{duration:g}",
           "--output", out_base, "--bvh"]
    if st.get("model"):
        cmd += ["--model", st["model"]]
    if seed not in (None, ""):
        cmd += ["--seed", str(int(seed))]
    if steps not in (None, ""):
        cmd += ["--diffusion_steps", str(int(steps))]
    env = dict(os.environ)
    mc = os.path.join(st["root"], "MotionCorrection", "python")
    env["PYTHONPATH"] = os.pathsep.join(x for x in (mc if os.path.isdir(mc) else "", st["root"], env.get("PYTHONPATH", "")) if x)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        with _RUN_LOCK:
            _set(job_id, message="Freeing GPU memory…", progress=5)
            try:
                try:
                    from .process_runner import free_memory
                except ImportError:  # pragma: no cover
                    from process_runner import free_memory
                free_memory(wait_s=3.0)
            except Exception:  # noqa: BLE001
                pass
            _set(job_id, message="Kimodo: loading the text encoder and model…", progress=10)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(cmd, cwd=st["root"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace", creationflags=flags)
            with _LOCK:
                _JOBS[job_id]["pid"] = proc.pid
            tail = []
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                tail = (tail + [line])[-40:]
                m = re.search(r"(\d+)%\|", line)          # tqdm diffusion progress
                upd = {"log": tail}
                if m:
                    upd.update(progress=min(95, 15 + int(int(m.group(1)) * 0.8)), message=f"Kimodo: diffusion {m.group(1)}%")
                elif "Saving" in line:
                    upd.update(message="Kimodo: saving the motion…", progress=96)
                _set(job_id, **upd)
                with _LOCK:
                    if _JOBS[job_id].get("cancelled"):
                        proc.kill()
            code = proc.wait()
        with _LOCK:
            cancelled = _JOBS[job_id].get("cancelled")
        if cancelled:
            _set(job_id, status="cancelled", message="Stopped")
            return
        bvh = out_base + ".bvh"
        if code != 0 or not os.path.isfile(bvh):
            err = next((l for l in reversed(tail) if "Error" in l or "error" in l), tail[-1] if tail else f"exit code {code}")
            raise RuntimeError(err)
        pid = _JOBS[job_id]["project_id"]
        _set(job_id, status="done", progress=100, message="Motion ready",
             bvh_url=f"/vfx/project_file/{pid}/motions/{os.path.basename(bvh)}",
             npz_url=f"/vfx/project_file/{pid}/motions/{stem}.npz" if os.path.isfile(out_base + ".npz") else None)
    except Exception as e:  # noqa: BLE001
        _set(job_id, status="error", error=str(e), message=f"Kimodo error: {e}")


def get_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else {"status": "not_found"}


def cancel_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        if job_id in _JOBS:
            _JOBS[job_id]["cancelled"] = True
            return {"status": "ok"}
    return {"status": "not_found"}
