"""
Every API workflow the studio sends to ComfyUI is checked against ComfyUI's node registry
(tests/fixtures/object_info.json.gz = GET /object_info of the reference install, model file lists removed):
known node classes, existing input names, required inputs present, links pointing to real outputs,
valid enum values. Refresh the fixture with tools/refresh_object_info.py after updating ComfyUI / node packs.
"""
import glob
import gzip
import json
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = sorted(glob.glob(os.path.join(ROOT, "workflows", "api", "**", "*.json"), recursive=True))
NODES = json.load(gzip.open(os.path.join(ROOT, "tests", "fixtures", "object_info.json.gz"), "rt", encoding="utf-8"))
# inputs ComfyUI accepts that object_info does not list (hidden / UI-only)
HIDDEN = {"unique_id", "prompt", "extra_pnginfo", "dynprompt"}


def _rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("path", TEMPLATES, ids=_rel)
def test_template_is_api_format(path):
    d = _load(path)
    assert isinstance(d, dict) and d, "empty template"
    for nid, n in d.items():
        assert isinstance(n, dict) and "class_type" in n and isinstance(n.get("inputs"), dict), f"node {nid} is not API format"


@pytest.mark.parametrize("path", TEMPLATES, ids=_rel)
def test_template_nodes_exist(path):
    missing = sorted({n["class_type"] for n in _load(path).values() if n["class_type"] not in NODES})
    assert not missing, f"unknown node classes: {missing}"


@pytest.mark.parametrize("path", TEMPLATES, ids=_rel)
def test_template_inputs_and_links(path):
    d = _load(path)
    problems = []
    for nid, n in d.items():
        spec = NODES.get(n["class_type"])
        if not spec:
            continue
        names = spec["inputs"]
        for k, v in n["inputs"].items():
            base = k.split(".")[0]
            if k not in names and base not in names and k not in HIDDEN:
                problems.append(f"{nid} {n['class_type']}: unknown input '{k}'")
                continue
            if isinstance(v, list) and len(v) == 2 and isinstance(v[1], int) and isinstance(v[0], str):
                src = d.get(v[0])
                if src is None:
                    problems.append(f"{nid}.{k} -> missing node {v[0]}")
                    continue
                outs = NODES.get(src["class_type"], {}).get("outputs")
                if outs is not None and v[1] >= len(outs):
                    problems.append(f"{nid}.{k} -> {v[0]} output {v[1]} (node has {len(outs)})")
            else:
                s = names.get(k) or names.get(base) or {}
                opts = s.get("options")
                if s.get("type") == "COMBO" and opts and isinstance(v, str) and k == base and v not in opts:
                    problems.append(f"{nid} {n['class_type']}.{k} = {v!r} not in {opts[:8]}")
        for k, s in names.items():
            if s.get("required") and k not in n["inputs"] and not any(x.startswith(k + ".") for x in n["inputs"]):
                problems.append(f"{nid} {n['class_type']}: required input '{k}' missing")
    assert not problems, "\n".join(problems)


def _template_refs():
    """(process_id, template) pairs the Python code loads."""
    refs = set()
    for f in glob.glob(os.path.join(ROOT, "vfx_nodes", "*.py")):
        src = open(f, encoding="utf-8").read()
        for m in re.finditer(r'load_template\(\s*(?:PROCESS_ID|"([\w-]+)")\s*,\s*f?"([\w\-{}]+)"', src):
            proc = m.group(1)
            if proc is None:
                pm = re.search(r'^PROCESS_ID\s*=\s*"([\w-]+)"', src, re.M)
                proc = pm.group(1) if pm else None
            if proc and "{" not in m.group(2):
                refs.add((proc, m.group(2)))
    return sorted(refs)


@pytest.mark.parametrize("proc,name", _template_refs())
def test_templates_used_by_code_exist(proc, name):
    assert os.path.isfile(os.path.join(ROOT, "workflows", "api", proc, name + ".json")), f"workflows/api/{proc}/{name}.json"


# ---------------------------------------------------------------- this machine's models (opt-in)
# tools/refresh_object_info.py also writes tests/_object_info_full.json (with model file lists, not committed).
# When it is present, every model file a template names must be installed here.
FULL = os.path.join(ROOT, "tests", "_object_info_full.json")
MODEL_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft")


@pytest.mark.skipif(not os.path.isfile(FULL), reason="no local object_info (run tools/refresh_object_info.py)")
@pytest.mark.parametrize("path", TEMPLATES, ids=_rel)
def test_template_models_installed_here(path):
    full = _load(FULL)
    missing = []
    for nid, n in _load(path).items():
        spec = (full.get(n["class_type"]) or {}).get("input", {})
        allin = {**spec.get("required", {}), **spec.get("optional", {})}
        for k, v in n["inputs"].items():
            if not (isinstance(v, str) and v.lower().endswith(MODEL_EXT)):
                continue
            s = allin.get(k)
            opts = s[0] if isinstance(s, list) and s and isinstance(s[0], list) else \
                (s[1].get("options") if isinstance(s, list) and len(s) > 1 and isinstance(s[1], dict) else None)
            if opts and v not in opts and v.replace("/", "\\") not in opts and v.replace("\\", "/") not in opts:
                missing.append(f"{n['class_type']}.{k} = {v}")
    assert not missing, "models not installed on this machine:\n" + "\n".join(missing)


def test_inject_style_lora_inactive_when_empty():
    from vfx_nodes.server_api import inject_style_lora
    t2v_path = os.path.join(ROOT, "workflows", "api", "video_gen", "ltx25_t2v.json")
    with open(t2v_path, encoding="utf-8") as f:
        wf = json.load(f)

    # Empty string or None -> leaves workflow untouched
    wf_none = inject_style_lora(dict(wf), None)
    assert "lora_style" not in wf_none
    assert wf_none["guider"]["inputs"]["model"] == ["unet", 0]

    wf_empty = inject_style_lora(dict(wf), "")
    assert "lora_style" not in wf_empty
    assert wf_empty["guider"]["inputs"]["model"] == ["unet", 0]


def test_inject_style_lora_active_t2v():
    from vfx_nodes.server_api import inject_style_lora
    t2v_path = os.path.join(ROOT, "workflows", "api", "video_gen", "ltx25_t2v.json")
    with open(t2v_path, encoding="utf-8") as f:
        wf = json.load(f)

    wf_mod = inject_style_lora(json.loads(json.dumps(wf)), "cinematic_style.safetensors", strength_model=0.75, strength_clip=0.85)
    assert "lora_style" in wf_mod
    lora = wf_mod["lora_style"]
    assert lora["class_type"] == "LoraLoader"
    assert lora["inputs"]["lora_name"] == "cinematic_style.safetensors"
    assert lora["inputs"]["strength_model"] == 0.75
    assert lora["inputs"]["strength_clip"] == 0.85
    assert lora["inputs"]["model"] == ["unet", 0]
    assert lora["inputs"]["clip"] == ["clip", 0]

    # Downstream rewiring
    assert wf_mod["guider"]["inputs"]["model"] == ["lora_style", 0]
    assert wf_mod["pos"]["inputs"]["clip"] == ["lora_style", 1]
    assert wf_mod["neg"]["inputs"]["clip"] == ["lora_style", 1]


def test_inject_style_lora_active_i2v():
    from vfx_nodes.server_api import inject_style_lora
    i2v_path = os.path.join(ROOT, "workflows", "api", "video_gen", "ltx25_i2v.json")
    with open(i2v_path, encoding="utf-8") as f:
        wf = json.load(f)

    wf_mod = inject_style_lora(json.loads(json.dumps(wf)), "anime_look.safetensors", strength_model=0.9, strength_clip=0.9)
    assert "lora_style" in wf_mod
    lora = wf_mod["lora_style"]
    assert lora["class_type"] == "LoraLoader"
    assert lora["inputs"]["lora_name"] == "anime_look.safetensors"
    assert lora["inputs"]["model"] == ["unet", 0]
    assert lora["inputs"]["clip"] == ["clip", 0]

    assert wf_mod["guider"]["inputs"]["model"] == ["lora_style", 0]
    assert wf_mod["pos"]["inputs"]["clip"] == ["lora_style", 1]
    assert wf_mod["neg"]["inputs"]["clip"] == ["lora_style", 1]

