"""
User tools - your own ComfyUI workflows as studio tools
=======================================================
Build a graph in ComfyUI, export it with "Save (API)" (or send it to the agent) and import it here. The studio then
runs it from code: a form in "My tools", an action for the studio agent and a tool for MCP agents.

The tool's passport (user_tools/<id>/tool.json) is made automatically from the graph:
  inputs  - load nodes (LoadImage, LoadVideo, VHS_LoadVideo, LoadAudio...). Title one "IN: Plate" to name it.
  params  - nodes titled "PARAM: Strength" expose their values; without PARAM nodes the prompt texts and seeds are exposed.
  outputs - whatever the Save / Preview nodes produce (images, video, audio) is copied into the project.
The passport can be edited (name, description, tab, labels, ranges) - the graph stays as exported.
"""
import copy
import json
import os
import re
import shutil
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from .process_runner import ProcessError, get_run_status, inject_params, run_process, _is_api_prompt, sanitize_api_prompt
    from .project_manager import get_project_dir
    from .workflow_converter import convert_ui_workflow_to_api_prompt
except ImportError:  # pragma: no cover
    from process_runner import ProcessError, get_run_status, inject_params, run_process, _is_api_prompt, sanitize_api_prompt
    from project_manager import get_project_dir
    from workflow_converter import convert_ui_workflow_to_api_prompt

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(BASE_DIR, "user_tools")
PROCESS_ID = "user_tools"
TABS = ("image", "video", "voice", "music", "any")

# class_type -> (media kind, input that takes the file name)
LOADERS = {
    "LoadImage": ("image", "image"), "LoadImageMask": ("image", "image"), "LoadImageOutput": ("image", "image"),
    "LoadVideo": ("video", "file"), "VHS_LoadVideo": ("video", "video"), "VHS_LoadVideoFFmpeg": ("video", "video"),
    "LoadAudio": ("audio", "audio"), "VHS_LoadAudioUpload": ("audio", "audio"),
}
AUTO_PARAM_INPUTS = ("text", "prompt", "positive", "seed", "noise_seed", "lora_name", "strength_model", "strength_clip")

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------- passport
_RU = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
                "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def _slug(s: str) -> str:
    s = "".join(_RU.get(c, c) for c in (s or "").lower())      # Cyrillic names give readable ids, not "tool"
    return re.sub(r"[^a-z0-9_]+", "_", s).strip("_")[:40] or "tool"


def _title(node: Dict[str, Any]) -> str:
    return str((node.get("_meta") or {}).get("title") or "")


def _tagged(node, tag):
    t = _title(node)
    return t[len(tag):].strip() if t.upper().startswith(tag) else None


def _kind_of(v, field: str = ""):
    if "lora" in field.lower():
        return "lora"
    return "bool" if isinstance(v, bool) else "int" if isinstance(v, int) else "float" if isinstance(v, float) else "text"


def describe(prompt: Dict[str, Any]) -> Dict[str, Any]:
    """Inputs / params / outputs of an API prompt (the automatic passport)."""
    if isinstance(prompt, dict):
        if "prompt" in prompt and isinstance(prompt["prompt"], dict) and not ("nodes" in prompt["prompt"]):
            prompt = prompt["prompt"]
        elif "workflow" in prompt and isinstance(prompt["workflow"], dict):
            prompt = prompt["workflow"]
    if isinstance(prompt, dict) and "nodes" in prompt and "links" in prompt:
        try:
            prompt = convert_ui_workflow_to_api_prompt(prompt)
        except Exception as e:
            raise ProcessError(f"Could not convert editor workflow to API prompt: {e}")

    prompt = sanitize_api_prompt(prompt)
    if not _is_api_prompt(prompt):
        raise ProcessError("This is not an API-format workflow. In ComfyUI use Workflow → Export (API) / 'Save (API)'.")
    inputs, params, outputs = [], [], []
    tagged_in = any(_tagged(n, "IN:") is not None for n in prompt.values())
    for nid, n in sorted(prompt.items(), key=lambda kv: kv[0]):
        ct, ins = n["class_type"], n.get("inputs", {})
        label_in = _tagged(n, "IN:")
        if ct in LOADERS and (label_in is not None or not tagged_in):
            kind, field = LOADERS[ct]
            inputs.append({"id": f"in_{nid}", "node": nid, "field": field, "kind": kind,
                           "label": label_in or _title(n) or f"{kind} {len(inputs) + 1}", "required": True})
        elif label_in is not None:
            field = next((k for k in ("image", "video", "audio", "file") if k in ins), None)
            if field:
                inputs.append({"id": f"in_{nid}", "node": nid, "field": field, "kind": field if field != "file" else "video",
                               "label": label_in, "required": True})
        if ct.startswith(("Save", "Preview", "VHS_VideoCombine")) or "Save" in ct:
            outputs.append({"node": nid, "class_type": ct, "label": _title(n) or ct})
    tagged_params = [(nid, n) for nid, n in prompt.items() if _tagged(n, "PARAM:") is not None]
    if tagged_params:
        for nid, n in tagged_params:
            label = _tagged(n, "PARAM:")
            widgets = [(k, v) for k, v in n.get("inputs", {}).items() if not isinstance(v, list)]
            for k, v in widgets:
                params.append({"id": f"p{nid}_{k}", "node": nid, "field": k,
                               "label": label if len(widgets) == 1 else f"{label} · {k}", "type": _kind_of(v, k), "default": v})
    else:
        for nid, n in sorted(prompt.items(), key=lambda kv: kv[0]):
            for k, v in n.get("inputs", {}).items():
                if k in AUTO_PARAM_INPUTS and not isinstance(v, list) and n["class_type"] not in LOADERS:
                    params.append({"id": f"p{nid}_{k}", "node": nid, "field": k,
                                   "label": f"{_title(n) or n['class_type']} · {k}", "type": _kind_of(v, k), "default": v})
    return {"inputs": inputs, "params": params, "outputs": outputs}


def _check_nodes(prompt: Dict[str, Any]) -> List[str]:
    prompt = sanitize_api_prompt(prompt)
    try:
        import nodes  # ComfyUI's registry (in-process)
        known = set(nodes.NODE_CLASS_MAPPINGS)
    except Exception:  # noqa: BLE001 - outside ComfyUI (tests): skip
        return []
    return sorted({n["class_type"] for n in prompt.values() if n["class_type"] not in known})


def _tool_dir(tid: str) -> str:
    return os.path.join(TOOLS_DIR, _slug(tid))


def list_tools() -> List[Dict[str, Any]]:
    out = []
    if not os.path.isdir(TOOLS_DIR):
        return out
    for d in sorted(os.listdir(TOOLS_DIR)):
        p = os.path.join(TOOLS_DIR, d, "tool.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    out.append(json.load(f))
            except (OSError, ValueError):
                continue
    return out


def get_tool(tid: str) -> Dict[str, Any]:
    p = os.path.join(_tool_dir(tid), "tool.json")
    if not os.path.isfile(p):
        raise ProcessError(f"No tool '{tid}'")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def import_tool(workflow: Any, name: str = "", description: str = "", tab: str = "any", tool_id: str = "") -> Dict[str, Any]:
    if isinstance(workflow, str):
        try:
            workflow = json.loads(workflow)
        except ValueError as e:
            raise ProcessError(f"Not JSON: {e}")
    if isinstance(workflow, dict):
        if "prompt" in workflow and isinstance(workflow["prompt"], dict) and not ("nodes" in workflow["prompt"]):
            workflow = workflow["prompt"]
        elif "workflow" in workflow and isinstance(workflow["workflow"], dict):
            workflow = workflow["workflow"]
    if isinstance(workflow, dict) and "nodes" in workflow and "links" in workflow:
        try:
            workflow = convert_ui_workflow_to_api_prompt(workflow)
        except Exception as e:
            raise ProcessError(f"Could not convert editor workflow to API prompt: {e}")
    workflow = sanitize_api_prompt(workflow)
    passport = describe(workflow)
    missing = _check_nodes(workflow)
    if missing:
        raise ProcessError("Nodes not installed in this ComfyUI: " + ", ".join(missing))
    if not passport["outputs"]:
        raise ProcessError("The workflow has no Save / Preview node - nothing would come back to the studio")
    name = (name or "").strip() or "My tool"
    tid = _slug(tool_id or name)
    base, n = tid, 2
    while os.path.isdir(_tool_dir(tid)) and not tool_id:
        tid, n = f"{base}_{n}", n + 1
    d = _tool_dir(tid)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "workflow.json"), "w", encoding="utf-8") as f:
        json.dump(workflow, f, ensure_ascii=False, indent=1)
    tool = {"id": tid, "name": name, "description": (description or "").strip(), "tab": tab if tab in TABS else "any",
            "created": time.time(), **passport}
    return save_passport(tool)


def save_passport(tool: Dict[str, Any]) -> Dict[str, Any]:
    d = _tool_dir(tool["id"])
    if not os.path.isfile(os.path.join(d, "workflow.json")):
        raise ProcessError(f"No tool '{tool['id']}'")
    old = {}
    if os.path.isfile(os.path.join(d, "tool.json")):
        old = get_tool(tool["id"])
    merged = {**old, **tool, "id": old.get("id", tool["id"])}
    with open(os.path.join(d, "tool.json"), "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
    return merged


def delete_tool(tid: str) -> Dict[str, Any]:
    d = _tool_dir(tid)
    if os.path.isdir(d):
        shutil.rmtree(d)
    return {"deleted": tid}


# ---------------------------------------------------------------- run
def _to_input_dir(path: str) -> str:
    import folder_paths
    name = f"keshiki_tool_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
    shutil.copy2(path, os.path.join(folder_paths.get_input_directory(), name))
    return name


def build_prompt(tool: Dict[str, Any], values: Dict[str, Any], resolve) -> Dict[str, Any]:
    with open(os.path.join(_tool_dir(tool["id"]), "workflow.json"), encoding="utf-8") as f:
        prompt = json.load(f)
    params = {}
    for i in tool.get("inputs", []):
        v = values.get(i["id"])
        if not v:
            if i.get("required", True):
                raise ProcessError(f"Choose the input '{i['label']}'")
            continue
        params[f"{i['node']}.{i['field']}"] = _to_input_dir(resolve(v))
    for p in tool.get("params", []):
        if p["id"] in values and values[p["id"]] is not None:
            v = values[p["id"]]
            t = p.get("type")
            v = bool(v) if t == "bool" else int(v) if t == "int" else float(v) if t == "float" else str(v)
            params[f"{p['node']}.{p['field']}"] = v
    return inject_params(prompt, params)


def _run(job_id: str, pid: str, tid: str, values: Dict[str, Any]) -> Dict[str, Any]:
    import folder_paths
    try:
        from .video_layers_service import resolve_media_path
    except ImportError:  # pragma: no cover
        from video_layers_service import resolve_media_path
    tool = get_tool(tid)
    prompt = build_prompt(tool, values, resolve_media_path)
    q = run_process(PROCESS_ID, prompt=prompt, client_id="keshiki_tools", run_tag=job_id)
    t0 = time.time()
    while True:
        if _JOBS[job_id].get("cancel"):
            raise ProcessError("Stopped")
        st = get_run_status(q["prompt_id"])
        if st["status"] == "error":
            msgs = [e.get("exception_message", "").strip() for e in (st.get("errors") or []) if isinstance(e, dict)]
            raise ProcessError("; ".join(m for m in msgs if m) or "The workflow failed")
        if st["status"] == "cancelled":
            raise ProcessError("Stopped")
        if st["status"] == "done":
            break
        _update(job_id, message=f"Running… {int(time.time() - t0)} s")
        time.sleep(1.5)
    out_dir = os.path.join(get_project_dir(pid), "user_tools", tool["id"])
    os.makedirs(out_dir, exist_ok=True)
    files = []
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for o in st["outputs"]:
        base = {"temp": folder_paths.get_temp_directory(), "input": folder_paths.get_input_directory()}.get(
            o.get("type", "output"), folder_paths.get_output_directory())
        src = os.path.join(base, o.get("subfolder", ""), o["filename"])
        if not os.path.isfile(src):
            continue
        name = f"{stamp}_{o['filename']}"
        shutil.copy2(src, os.path.join(out_dir, name))
        ext = os.path.splitext(name)[1].lower()
        kind = "video" if ext in (".mp4", ".webm", ".mov", ".gif") else "audio" if ext in (".wav", ".mp3", ".flac", ".ogg") else "image"
        files.append({"kind": kind, "url": f"/vfx/project_file/{pid}/user_tools/{tool['id']}/{name}", "name": name})
    if not files:
        raise ProcessError("The workflow finished but produced no files")
    return {"files": files, "seconds": round(time.time() - t0, 1)}


def _update(job_id, **kw):
    with _LOCK:
        _JOBS[job_id].update(kw)


def get_job(job_id: str) -> Dict[str, Any]:
    with _LOCK:
        j = _JOBS.get(job_id)
        return dict(j) if j else {"id": job_id, "status": "unknown"}


def start(pid: str, tid: str, values: Dict[str, Any]) -> Dict[str, Any]:
    get_tool(tid)
    job_id = f"tjob_{uuid.uuid4().hex[:10]}"
    with _LOCK:
        _JOBS[job_id] = {"id": job_id, "tool": tid, "status": "running", "message": "Starting…", "started": time.time()}

    def runner():
        try:
            res = _run(job_id, pid, tid, values or {})
            _update(job_id, status="done", message="Done", result=res)
        except Exception as e:  # noqa: BLE001
            _update(job_id, status="cancelled" if str(e) == "Stopped" else "error", message=str(e))

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
    except Exception:  # noqa: BLE001
        pass
    return get_job(job_id)
