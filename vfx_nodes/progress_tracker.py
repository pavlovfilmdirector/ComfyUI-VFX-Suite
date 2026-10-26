"""Live progress of ComfyUI runs for the studio UI.

Wraps PromptServer.send_sync once, so every prompt (ours or one queued from the ComfyUI UI)
reports: current node + title, step progress, node counter, ETA and the latest sampler preview.
"""
import re
import io
import threading
import time

_LOCK = threading.Lock()
_STATE = {}          # prompt_id -> dict
_ORDER = []          # most recent last
_LABELS = {}         # prompt_id -> process label (set by process_runner)
_PREVIEWS = {}       # prompt_id -> (PIL image or bytes, mimetype)
_CURRENT = {"prompt_id": None}
_INSTALLED_FLAG = "_keshiki_progress_wrapped"


def note_label(prompt_id, label):
    if prompt_id:
        _LABELS[prompt_id] = label


def _prompt_from_queue(prompt_id):
    try:
        from server import PromptServer
        q = PromptServer.instance.prompt_queue
        # No q.mutex here: send_sync may be called while the queue holds it (lock-order inversion).
        running = list(dict(q.currently_running).values())
        for item in running:
            if len(item) > 2 and item[1] == prompt_id:
                return item[2]
    except Exception:
        pass
    return None


def _title(st, node_id):
    if node_id is None:
        return ""
    node = (st.get("prompt") or {}).get(str(node_id)) or {}
    return (node.get("_meta") or {}).get("title") or node.get("class_type") or str(node_id)


def _get(pid):
    st = _STATE.get(pid)
    if st is None:
        prompt = _prompt_from_queue(pid)
        st = {"prompt_id": pid, "status": "running", "started": time.time(), "node": None, "node_title": "",
              "value": 0, "max": 0, "done_nodes": 0, "cached_nodes": 0,
              "total_nodes": len(prompt) if prompt else 0, "prompt": prompt or {}, "step_t0": None,
              "eta": None, "updated": time.time(), "has_preview": False}
        _STATE[pid] = st
        _ORDER.append(pid)
        while len(_ORDER) > 30:
            old = _ORDER.pop(0)
            _STATE.pop(old, None)
            _PREVIEWS.pop(old, None)
    return st


_LAST_EVICT = [0.0]


def evict_heavy_llm_async():
    """A generation is starting: push the big local thinking LLM out of VRAM (the small
    always-on dispatcher model stays). Runs in a thread so ComfyUI messaging never waits."""
    if time.time() - _LAST_EVICT[0] < 5:
        return
    _LAST_EVICT[0] = time.time()

    def _run():
        try:
            from .vfx_agent_service import unload_agent_from_vram
            unload_agent_from_vram(None, preserve_micro=False, kill_llama_server=False)
        except Exception as e:
            print(f"[VFX-Suite] LLM eviction skipped: {e}")
    threading.Thread(target=_run, daemon=True).start()


def warm_micro_async():
    """Load the small dispatcher model once at startup and pin it (keep_alive -1)."""
    def _run():
        try:
            time.sleep(8)
            import json, urllib.request, sys, os
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if root not in sys.path:
                sys.path.insert(0, root)
            from core_agent import studio_agent as sa
            cfg = {}
            try:
                from .vfx_agent_service import load_agent_config
                cfg = load_agent_config() or {}
            except Exception:
                pass
            if str(cfg.get("provider") or "ollama") not in sa.LOCAL_PROVIDERS:
                return
            m = sa.micro_model(cfg)
            if not m:
                return
            url = re.sub(r"/(v1|api)$", "", (cfg.get("api_url") or "http://127.0.0.1:11434").rstrip("/"))
            req = urllib.request.Request(f"{url}/api/generate", data=json.dumps(
                {"model": m, "prompt": "", "keep_alive": -1}).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=60).read()
            print(f"[VFX-Suite] micro dispatcher '{m}' pinned in VRAM")
        except Exception as e:
            print(f"[VFX-Suite] micro warm-up skipped: {e}")
    threading.Thread(target=_run, daemon=True).start()


def _on_event(event, data):
    from server import BinaryEventTypes
    now = time.time()
    with _LOCK:
        if isinstance(event, str) and isinstance(data, dict):
            pid = data.get("prompt_id")
            if event == "execution_start" and pid:
                _CURRENT["prompt_id"] = pid
                _get(pid)
                evict_heavy_llm_async()
            elif event == "execution_cached" and pid:
                _get(pid)["cached_nodes"] = len(data.get("nodes") or [])
            elif event == "executing" and pid:
                st = _get(pid)
                if st["node"] is not None:
                    st["done_nodes"] += 1
                st.update(node=data.get("node"), node_title=_title(st, data.get("node")),
                          value=0, max=0, step_t0=None, eta=None, updated=now)
                if data.get("node") is None:
                    st["status"] = "done"
            elif event == "progress" and pid:
                st = _get(pid)
                v, m = data.get("value", 0), data.get("max", 0)
                if st["step_t0"] is None or v < st["value"]:
                    st["step_t0"] = (now, v)
                t0, v0 = st["step_t0"]
                if v > v0 and m:
                    st["eta"] = round((now - t0) / (v - v0) * (m - v), 1)
                st.update(value=v, max=m, updated=now)
                if data.get("node") is not None and str(data.get("node")) != str(st["node"]):
                    st["node_title"] = _title(st, data.get("node"))
            elif event in ("execution_success", "execution_error", "execution_interrupted") and pid:
                st = _get(pid)
                st["status"] = {"execution_success": "done", "execution_error": "error",
                                "execution_interrupted": "cancelled"}[event]
                if event == "execution_error":
                    st["error"] = str(data.get("exception_message", ""))[:500]
                st["updated"] = now
                if _CURRENT["prompt_id"] == pid:
                    _CURRENT["prompt_id"] = None
        elif event == BinaryEventTypes.PREVIEW_IMAGE_WITH_METADATA and isinstance(data, tuple):
            img, meta = data
            pid = (meta or {}).get("prompt_id") or _CURRENT["prompt_id"]
            if pid:
                _PREVIEWS[pid] = img
                _get(pid)["has_preview"] = True
        elif event == BinaryEventTypes.UNENCODED_PREVIEW_IMAGE:
            pid = _CURRENT["prompt_id"]
            if pid:
                _PREVIEWS[pid] = data
                _get(pid)["has_preview"] = True


def install(enable_previews=True):
    from server import PromptServer
    ps = PromptServer.instance
    if not getattr(ps, _INSTALLED_FLAG, False):
        orig = ps.send_sync

        def wrapped(event, data, sid=None):
            try:
                import sys
                mod = sys.modules.get(__name__)
                (getattr(mod, "_on_event", None) or _on_event)(event, data)
            except Exception as e:  # never break ComfyUI messaging
                print(f"[VFX-Suite] progress tracker: {e}")
            return orig(event, data, sid)

        ps.send_sync = wrapped
        setattr(ps, _INSTALLED_FLAG, True)
    if enable_previews:
        try:
            from comfy.cli_args import args, LatentPreviewMethod
            if args.preview_method == LatentPreviewMethod.NoPreviews:
                args.preview_method = LatentPreviewMethod.Auto
        except Exception as e:
            print(f"[VFX-Suite] previews not enabled: {e}")


def _public(st):
    out = {k: v for k, v in st.items() if k not in ("prompt", "step_t0")}
    out["label"] = _LABELS.get(st["prompt_id"], "")
    total = max(1, st["total_nodes"] - st["cached_nodes"])
    frac = (st["value"] / st["max"]) if st["max"] else 0
    out["percent"] = 100 if st["status"] == "done" else round(min(99, 100 * (st["done_nodes"] + frac) / total), 1)
    out["step_percent"] = round(100 * frac, 1)
    out["elapsed"] = round(time.time() - st["started"], 1)
    return out


def snapshot():
    """Running prompt (if any) plus the last few finished ones."""
    import sys
    tracker = sys.modules.get(__name__)
    with _LOCK:
        items = [_public(_STATE[p]) for p in reversed(_ORDER) if p in _STATE]
    running = [i for i in items if i["status"] == "running"]
    # Stale guard: a prompt with no events for 30 min is considered gone.
    running = [i for i in running if time.time() - i["updated"] < 1800]
    pending = 0
    try:
        from server import PromptServer
        q = PromptServer.instance.prompt_queue
        pending = q.get_tasks_remaining() - len(running)
    except Exception:
        pass
    return {"running": running[0] if running else None, "recent": items[:6], "pending": max(0, pending)}


def preview_bytes(prompt_id):
    data = _PREVIEWS.get(prompt_id)
    if data is None:
        return None, None
    try:
        # (image_type, PIL.Image, max_size)
        img = data[1] if isinstance(data, tuple) else data
        if hasattr(img, "save"):
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=85)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        pass
    return None, None
