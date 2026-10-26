"""
Process Runner — single entry point for executing studio processes
==================================================================
Every studio process is executed the same way: an API-format ComfyUI prompt
(JSON) is loaded from a template, user parameters are injected, and the prompt
is queued on the local ComfyUI server via POST /prompt.

The node graph shown in the UI is only a preview of that JSON; it never owns
a ComfyUI canvas/tab, so runs do not pile up as open workflows.

Execution modes per process (see pipeline_config_service):
  local            -> template from workflows/api/<process>/<name>.json
  partner          -> same, but the template uses Comfy partner (API) nodes;
                      the Comfy API key is passed as extra_data.api_key_comfy_org
  custom_workflow  -> the user's bound API JSON
  cloud            -> reserved for third-party HTTP APIs (not used by the runner)

Parameter injection
-------------------
`params` maps "<node title or id>.<input name>" -> value, e.g.
    {"Positive Prompt.text": "a red car", "Sampler.seed": 42}
Node titles come from the API prompt's "_meta": {"title": ...}.
"""

import copy
import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

try:
    from .pipeline_config_service import (
        BASE_DIR,
        get_comfy_api_key,
        is_process_enabled,
        load_processes_config,
    )
except ImportError:  # pragma: no cover - direct execution
    from pipeline_config_service import BASE_DIR, get_comfy_api_key, is_process_enabled, load_processes_config

def _detect_comfy_url() -> str:
    """URL of the ComfyUI server this package runs inside (its own --port / --listen), not a fixed 8188."""
    env = os.environ.get("KESHIKI_COMFY_URL", "").strip()
    if env:
        return env.rstrip("/")
    port, host = 8188, "127.0.0.1"
    try:
        from comfy.cli_args import args as _comfy_args
        port = int(getattr(_comfy_args, "port", 8188) or 8188)
        listen = str(getattr(_comfy_args, "listen", "") or "").split(",")[0].strip()
        if listen and listen not in ("0.0.0.0", "::", "[::]"):
            host = listen
    except Exception:
        pass
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


COMFY_URL = _detect_comfy_url()
TEMPLATES_DIR = os.path.join(BASE_DIR, "workflows", "api")


class ProcessError(Exception):
    pass


# run_tag (chosen by the UI) -> prompt_id, so a run can be cancelled while the HTTP call is still waiting
_ACTIVE_RUNS: Dict[str, str] = {}


def register_run(run_tag: Optional[str], prompt_id: str) -> None:
    if run_tag:
        _ACTIVE_RUNS[str(run_tag)] = prompt_id


def cancel_run(run_tag: Optional[str] = None, prompt_id: Optional[str] = None) -> Dict[str, Any]:
    """Removes a pending prompt from the queue or interrupts it if it is running."""
    pid = prompt_id or _ACTIVE_RUNS.get(str(run_tag or ""))
    if not pid:
        return {"status": "not_found"}
    with urllib.request.urlopen(f"{COMFY_URL}/queue", timeout=10) as resp:
        q = json.loads(resp.read().decode("utf-8"))
    running = [item[1] for item in q.get("queue_running", [])]
    pending = [item[1] for item in q.get("queue_pending", [])]
    if pid in pending:
        _post_json(f"{COMFY_URL}/queue", {"delete": [pid]})
        return {"status": "removed_from_queue", "prompt_id": pid}
    if pid in running:
        _post_json(f"{COMFY_URL}/interrupt", {"prompt_id": pid})
        return {"status": "interrupted", "prompt_id": pid}
    return {"status": "not_running", "prompt_id": pid}


def _is_api_prompt(data: Any) -> bool:
    return isinstance(data, dict) and bool(data) and all(
        isinstance(v, dict) and "class_type" in v for v in data.values()
    )


def load_template(process_id: str, template: str) -> Dict[str, Any]:
    safe = os.path.basename(template)
    if not safe.endswith(".json"):
        safe += ".json"
    path = os.path.join(TEMPLATES_DIR, process_id, safe)
    if not os.path.isfile(path):
        raise ProcessError(f"Template not found: workflows/api/{process_id}/{safe}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not _is_api_prompt(data):
        raise ProcessError(f"{safe} is not an API-format prompt (export with 'Save (API)' in ComfyUI)")
    return data


def list_templates(process_id: Optional[str] = None) -> Dict[str, list]:
    out: Dict[str, list] = {}
    if not os.path.isdir(TEMPLATES_DIR):
        return out
    for pid in sorted(os.listdir(TEMPLATES_DIR)):
        if process_id and pid != process_id:
            continue
        d = os.path.join(TEMPLATES_DIR, pid)
        if os.path.isdir(d):
            out[pid] = sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))
    return out


def inject_params(prompt: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """Returns a copy of `prompt` with params applied. Unknown keys raise, so typos never pass silently."""
    result = copy.deepcopy(prompt)
    by_title = {}
    for nid, node in result.items():
        title = (node.get("_meta") or {}).get("title")
        if title:
            by_title.setdefault(title, nid)
    for key, value in (params or {}).items():
        if "." not in key:
            raise ProcessError(f"Bad param key '{key}', expected '<node>.<input>'")
        node_ref, input_name = key.rsplit(".", 1)
        nid = node_ref if node_ref in result else by_title.get(node_ref)
        if nid is None:
            raise ProcessError(f"Node '{node_ref}' not found in template")
        inputs = result[nid].setdefault("inputs", {})
        if input_name not in inputs:
            raise ProcessError(f"Input '{input_name}' not found on node '{node_ref}'")
        if isinstance(inputs[input_name], list):
            raise ProcessError(f"Input '{node_ref}.{input_name}' is a link, not a widget value")
        inputs[input_name] = value
    return result


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 15.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise ProcessError(f"ComfyUI rejected the prompt ({e.code}): {body[:2000]}")


def run_process(
    process_id: str,
    template: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
    prompt: Optional[Dict[str, Any]] = None,
    client_id: str = "keshiki_studio",
    run_tag: Optional[str] = None,
) -> Dict[str, Any]:
    """Queues a process run. Pass either `template` (file in workflows/api/<process_id>/) or a full `prompt`."""
    if not is_process_enabled(process_id):
        raise ProcessError(f"Process '{process_id}' is disabled in Settings")

    pconf = load_processes_config().get("processes", {}).get(process_id, {})
    mode = pconf.get("execution_mode", "local")

    if prompt is None:
        if mode == "custom_workflow" and pconf.get("custom_workflow_file") and not template:
            with open(pconf["custom_workflow_file"], "r", encoding="utf-8") as f:
                prompt = json.load(f)
            if not _is_api_prompt(prompt):
                raise ProcessError("Bound custom workflow is not in API format; re-export it with 'Save (API)'")
        elif template:
            prompt = load_template(process_id, template)
        else:
            raise ProcessError("No template or prompt given")
    elif not _is_api_prompt(prompt):
        raise ProcessError("`prompt` must be an API-format ComfyUI prompt")

    prompt = inject_params(prompt, params or {})

    # VRAM rule: heavy local LLMs (agent "brain") leave the GPU before local diffusion / vision runs.
    if mode != "partner" and process_id != "agent":
        try:
            try:
                from .vfx_agent_service import unload_agent_from_vram
            except ImportError:  # pragma: no cover
                from vfx_agent_service import unload_agent_from_vram
            unload_agent_from_vram()
        except Exception as e:  # never block a run because of eviction
            print(f"[VFX-Suite] VRAM eviction skipped: {e}")

    extra_data: Dict[str, Any] = {}
    uses_partner = mode == "partner" or any(
        n.get("class_type", "") in _partner_class_types() for n in prompt.values()
    )
    if uses_partner:
        try:
            from .local_config import cloud_services_allowed
        except ImportError:  # pragma: no cover
            from local_config import cloud_services_allowed
        if not cloud_services_allowed():
            raise ProcessError("This workflow uses Comfy partner (cloud API) nodes; KESHIKI runs local models only. "
                               "Opt in with KESHIKI_ALLOW_CLOUD=1 if you really want them.")
        key = get_comfy_api_key()
        if not key:
            raise ProcessError("This run uses Comfy partner nodes: set the Comfy API key in Settings")
        extra_data["api_key_comfy_org"] = key

    resp = _post_json(f"{COMFY_URL}/prompt", {"prompt": prompt, "client_id": client_id, "extra_data": extra_data})
    if resp.get("node_errors"):
        raise ProcessError(f"Node errors: {json.dumps(resp['node_errors'])[:2000]}")
    register_run(run_tag, resp.get("prompt_id"))
    try:
        from .progress_tracker import note_label
        note_label(resp.get("prompt_id"), process_id)
    except Exception:
        pass
    return {"status": "queued", "process_id": process_id, "mode": mode, "prompt_id": resp.get("prompt_id")}


def _partner_class_types() -> set:
    try:
        from .pipeline_config_service import PROCESS_EXTRAS
    except ImportError:  # pragma: no cover
        from pipeline_config_service import PROCESS_EXTRAS
    return {n for ex in PROCESS_EXTRAS.values() for n in ex.get("partner_nodes", [])}


def get_run_status(prompt_id: str) -> Dict[str, Any]:
    """Reads /history/<id>. Returns status and output file descriptors (images, video, audio, 3D)."""
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/history/{prompt_id}", timeout=10) as resp:
            hist = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise ProcessError(f"Cannot read ComfyUI history: {e}")
    entry = hist.get(prompt_id)
    if not entry:
        return {"prompt_id": prompt_id, "status": "pending"}
    st = entry.get("status", {})
    outputs = []
    for nid, out in (entry.get("outputs") or {}).items():
        for kind, items in out.items():
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict) and "filename" in it:
                        outputs.append({"node": nid, "kind": kind, **it})
    status = "error" if st.get("status_str") == "error" else ("done" if st.get("completed") else "running")
    messages = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
    if any(m and m[0] == "execution_interrupted" for m in st.get("messages", [])):
        status = "cancelled"
    return {"prompt_id": prompt_id, "status": status, "outputs": outputs, "errors": [m[1] for m in messages]}
