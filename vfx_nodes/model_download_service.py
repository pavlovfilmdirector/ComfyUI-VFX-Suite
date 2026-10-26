"""Background model downloads into ComfyUI/models (HF gated repos use the local HF token)."""
import os
import threading
import time
import uuid
import urllib.request

import folder_paths

_JOBS = {}
_LOCK = threading.Lock()


def _hf_token():
    for k in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k].strip()
    try:
        from huggingface_hub import get_token
        t = get_token()
        if t:
            return t
    except Exception:
        pass
    p = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "token")
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read().strip()
    return None


def _safe_dest(dest_rel):
    root = os.path.abspath(folder_paths.models_dir)
    dest = os.path.abspath(os.path.join(root, dest_rel))
    if not dest.startswith(root + os.sep):
        raise ValueError(f"destination outside models dir: {dest_rel}")
    return dest


def _download_one(job, item):
    url, dest = item["url"], _safe_dest(item["dest"])
    item["status"] = "running"
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        item.update(status="exists", done=os.path.getsize(dest), total=os.path.getsize(dest))
        return
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "KESHIKI-Studio/1.0"})
    if "huggingface.co" in url:
        tok = _hf_token()
        if tok:
            req.add_header("Authorization", f"Bearer {tok}")
    part = dest + ".part"
    start = os.path.getsize(part) if os.path.isfile(part) else 0
    if start:
        req.add_header("Range", f"bytes={start}-")
    with urllib.request.urlopen(req, timeout=60) as r:
        if start and r.status != 206:
            start = 0
        total = int(r.headers.get("Content-Length") or 0) + start
        item.update(total=total, done=start)
        with open(part, "ab" if start else "wb") as f:
            while True:
                if job.get("cancel"):
                    item["status"] = "cancelled"
                    return
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                item["done"] += len(chunk)
    os.replace(part, dest)
    item["status"] = "done"


def _worker(job):
    for item in job["items"]:
        if job.get("cancel"):
            item["status"] = "cancelled"
            continue
        try:
            _download_one(job, item)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg:
                msg += " — gated repo: open the model page on huggingface.co, accept the license, and log in with `hf auth login` (or set HF_TOKEN)."
            item.update(status="error", error=msg)
    job["status"] = "cancelled" if job.get("cancel") else (
        "error" if any(i["status"] == "error" for i in job["items"]) else "done")
    job["finished_at"] = time.time()


def start_download(items):
    clean = []
    for it in items:
        if not it.get("url") or not it.get("dest"):
            raise ValueError("each item needs url and dest")
        _safe_dest(it["dest"])
        clean.append({"url": it["url"], "dest": it["dest"], "status": "pending", "done": 0, "total": 0})
    job = {"id": uuid.uuid4().hex[:12], "status": "running", "items": clean, "started_at": time.time(),
           "has_hf_token": bool(_hf_token())}
    with _LOCK:
        _JOBS[job["id"]] = job
    threading.Thread(target=_worker, args=(job,), daemon=True).start()
    return job


def get_job(job_id):
    return _JOBS.get(job_id)


def cancel_job(job_id):
    job = _JOBS.get(job_id)
    if job:
        job["cancel"] = True
    return job
