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
import time
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
    try:
        with urllib.request.urlopen(f"{COMFY_URL}/queue", timeout=5) as resp:
            q = json.loads(resp.read().decode("utf-8"))
        running = [item[1] for item in q.get("queue_running", [])]
        pending = [item[1] for item in q.get("queue_pending", [])]
    except Exception:
        running, pending = [], []

    if pid and pid in pending:
        try:
            _post_json(f"{COMFY_URL}/queue", {"delete": [pid]})
        except Exception:
            pass
        return {"status": "removed_from_queue", "prompt_id": pid}

    # Always ensure targeted and global interrupt are fired, and free memory is signaled
    try:
        if pid:
            _post_json(f"{COMFY_URL}/interrupt", {"prompt_id": pid})
        _post_json(f"{COMFY_URL}/interrupt", {})
        _post_json(f"{COMFY_URL}/free", {"unload_models": True, "free_memory": True})
    except Exception:
        pass
    return {"status": "interrupted", "prompt_id": pid}


def _is_api_prompt(data: Any) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if "nodes" in data and "links" in data and isinstance(data["nodes"], list):
        return False
    valid = sum(1 for v in data.values() if isinstance(v, dict) and v.get("class_type"))
    return valid > 0 and valid >= (len(data) // 2)


def sanitize_api_prompt(data: Any) -> Dict[str, Any]:
    """Cleans an API prompt by filtering out non-dict entries or broken nodes missing class_type."""
    if not isinstance(data, dict):
        return {}
    return {
        str(k): v for k, v in data.items()
        if isinstance(v, dict) and v.get("class_type")
    }


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

    if prompt is not None and isinstance(prompt, dict):
        if "api_prompt" in prompt and isinstance(prompt["api_prompt"], dict) and _is_api_prompt(prompt["api_prompt"]):
            prompt = prompt["api_prompt"]
        elif "prompt" in prompt and isinstance(prompt["prompt"], dict) and _is_api_prompt(prompt["prompt"]):
            prompt = prompt["prompt"]

    if prompt is None:
        if mode == "custom_workflow" and pconf.get("custom_workflow_file") and not template:
            with open(pconf["custom_workflow_file"], "r", encoding="utf-8") as f:
                raw_wf = json.load(f)
            if isinstance(raw_wf, dict) and "api_prompt" in raw_wf and _is_api_prompt(raw_wf["api_prompt"]):
                prompt = raw_wf["api_prompt"]
            elif isinstance(raw_wf, dict) and "prompt" in raw_wf and _is_api_prompt(raw_wf["prompt"]):
                prompt = raw_wf["prompt"]
            elif _is_api_prompt(raw_wf):
                prompt = raw_wf
            else:
                raise ProcessError("Bound custom workflow is not in API format; re-export it with 'Save (API)'")
        elif template:
            prompt = load_template(process_id, template)
        else:
            raise ProcessError("No template or prompt given")
    elif not _is_api_prompt(prompt):
        raise ProcessError("`prompt` must be an API-format ComfyUI prompt")

    prompt = inject_params(prompt, params or {})

    # Auto-heal model names, quantized variants and LoRA slashes against installed ComfyUI models
    try:
        try:
            from .workflow_inspector import heal_and_validate_prompt
        except ImportError:
            from workflow_inspector import heal_and_validate_prompt
        prompt, _repairs = heal_and_validate_prompt(prompt)
    except Exception as heal_err:
        pass

    # VRAM rule: heavy local LLMs (agent "brain") leave the GPU before local diffusion / vision runs.
    if mode != "partner" and process_id != "agent":
        try:
            try:
                from .vfx_agent_service import unload_agent_from_vram
            except ImportError:  # pragma: no cover
                from vfx_agent_service import unload_agent_from_vram
            unload_agent_from_vram()
            apply_adaptive_vram_reserve(float(params.get('reserve_vram_gb', 0)) if params else None)
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
        from .progress_tracker import note_prompt
        note_prompt(resp.get("prompt_id"), prompt, process_id)
    except Exception:
        pass
    return {"status": "queued", "process_id": process_id, "mode": mode, "prompt_id": resp.get("prompt_id")}


def apply_adaptive_vram_reserve(custom_gb: Optional[float] = None) -> float:
    """Dynamically sets ComfyUI's EXTRA_RESERVED_VRAM headroom based on GPU memory.
    Prevents activations from spilling into slow Windows Shared GPU Memory across all workflows.
    - 24 GB cards (3090 / 4090): 5.0 GB reserve
    - 16 GB cards (4080 / 4070 Ti Super): 3.0 GB reserve
    - 12 GB cards (3060 / 4070): 2.0 GB reserve
    - <=8 GB cards: 1.0 GB reserve
    """
    try:
        import comfy.model_management as mm
        if custom_gb is not None and custom_gb > 0:
            target_bytes = int(custom_gb * (1024 ** 3))
        else:
            total_mb = getattr(mm, "total_vram", 0)
            if total_mb >= 22000:
                target_bytes = int(5.0 * (1024 ** 3))
            elif total_mb >= 15000:
                target_bytes = int(3.0 * (1024 ** 3))
            elif total_mb >= 11000:
                target_bytes = int(2.0 * (1024 ** 3))
            else:
                target_bytes = int(1.0 * (1024 ** 3))

        cur = getattr(mm, "EXTRA_RESERVED_VRAM", 0)
        if target_bytes > cur:
            mm.EXTRA_RESERVED_VRAM = target_bytes
        return mm.EXTRA_RESERVED_VRAM / (1024 ** 3)
    except Exception:
        return 0.0


def free_memory(wait_s: float = 3.0) -> None:
    """ComfyUI's /free: unload every model and drop the executor cache (RAM too), applied by the prompt worker
    between prompts, so a running prompt is never touched. Use before a big model of another family (LTX-2.3 DubIt
    after LTX-2.5, Qwen Edit after LTX...): offloading alone keeps the old weights in RAM."""
    try:
        from server import PromptServer
        q = PromptServer.instance.prompt_queue
        q.set_flag("unload_models", True)
        q.set_flag("free_memory", True)
    except Exception:  # noqa: BLE001 - outside ComfyUI
        return
    time.sleep(max(0.0, wait_s))


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
