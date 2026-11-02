"""First-run setup: which models KESHIKI can use, which are already on disk, and linking a folder that holds them.

The list comes from install_manifest.json (the same list install.bat uses). Downloads go through
model_download_service (into ComfyUI/models); a folder of models kept elsewhere is linked through ComfyUI's
extra_model_paths.yaml (section "keshiki_linked") and registered in the running ComfyUI right away.
"""
import json
import os
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(HERE, "install_manifest.json")
STATE = os.path.join(HERE, "output_cache", "setup_state.json")
LINK_SECTION = "keshiki_linked"
MODEL_KINDS = ("checkpoints", "diffusion_models", "unet", "text_encoders", "clip", "vae", "loras", "sam2", "sams",
               "geometry_estimation", "depthanything", "detection", "controlnet", "upscale_models", "LLM")

GROUP_INFO = {
    "core": ("Core: LTX-2.5 video, masks, matting", "Основа: видео LTX-2.5, маски, матты"),
    "iclora": ("LTX-2.5 IC-LoRAs (layout → render, refine, day → night…)", "IC-LoRA для LTX-2.5 (лейаут → рендер, рефайн, день → ночь…)"),
    "image": ("Images: FLUX.3 Klein (storyboard, edits)", "Изображения: FLUX.3 Klein (раскадровка, правки)"),
    "maps": ("Normal maps (MoGe-2)", "Карты нормалей (MoGe-2)"),
    "music": ("Music: ACE-Step 1.5", "Музыка: ACE-Step 1.5"),
    "auto": ("Downloaded automatically on first use", "Скачиваются сами при первом использовании"),
}


def _comfy_root() -> str:
    try:
        import folder_paths
        return os.path.dirname(os.path.abspath(folder_paths.models_dir))
    except Exception:
        return os.path.dirname(os.path.dirname(HERE))   # custom_nodes/<suite> -> ComfyUI


def _manifest() -> Dict[str, Any]:
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def _search_dirs(comfy: str, kind: str) -> List[str]:
    dirs = [os.path.join(comfy, "models", kind)]
    try:
        import folder_paths
        dirs += list(folder_paths.get_folder_paths(kind))
    except Exception:
        pass
    try:   # extra_model_paths.yaml, also before a restart
        import importlib.util
        spec = importlib.util.spec_from_file_location("keshiki_install", os.path.join(HERE, "install.py"))
        inst = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inst)
        dirs += inst.extra_model_dirs(comfy).get(kind, [])
    except Exception:
        pass
    out, seen = [], set()
    for d in dirs:
        k = os.path.normcase(os.path.abspath(d))
        if k not in seen and os.path.isdir(d):
            seen.add(k)
            out.append(d)
    return out


def _find(comfy: str, m: Dict[str, Any]) -> Optional[str]:
    d = str(m.get("dir") or "")
    fname = str(m.get("file") or "")
    if not fname or fname.startswith("("):
        return None
    if d.startswith("@"):
        p = os.path.join(comfy, d[1:], fname)
        return p if os.path.isfile(p) else None
    direct = os.path.join(comfy, "models", d, fname)
    if os.path.isfile(direct):
        return direct
    kind = d.split("/")[0]
    for top in _search_dirs(comfy, kind):
        for root, _, files in os.walk(top):
            if fname in files:
                return os.path.join(root, fname)
    return None


def is_done() -> bool:
    try:
        with open(STATE, encoding="utf-8") as f:
            return bool(json.load(f).get("done"))
    except Exception:
        return False


def mark_done(done: bool = True) -> Dict[str, Any]:
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump({"done": bool(done)}, f)
    return {"done": bool(done)}


def status() -> Dict[str, Any]:
    comfy = _comfy_root()
    models = []
    for i, m in enumerate(_manifest().get("models", [])):
        path = _find(comfy, m) if m.get("group") != "auto" else None
        d = str(m.get("dir") or "")
        downloadable = bool(m.get("url")) and not d.startswith("@") and not d.startswith("(")
        models.append({
            "key": f"m{i}", "group": m.get("group"), "file": m.get("file"), "dir": d, "size_gb": m.get("size_gb"),
            "url": m.get("url"), "page": m.get("page"), "note": m.get("note", ""),
            "installed": bool(path), "path": path, "downloadable": downloadable,
            "dest": f"{d}/{m.get('file')}" if downloadable else None,
        })
    groups = [{"id": g, "title_en": t[0], "title_ru": t[1]} for g, t in GROUP_INFO.items()]
    return {"done": is_done(), "comfy_root": comfy, "models_dir": os.path.join(comfy, "models"),
            "linked": linked_folders(), "groups": groups, "models": models}


def _yaml_path() -> str:
    return os.path.join(_comfy_root(), "extra_model_paths.yaml")


def linked_folders() -> List[str]:
    try:
        import yaml
        with open(_yaml_path(), encoding="utf-8") as f:
            conf = yaml.safe_load(f) or {}
    except Exception:
        return []
    sec = conf.get(LINK_SECTION) or {}
    return [sec["base_path"]] if isinstance(sec, dict) and sec.get("base_path") else []


def link_models_folder(path: str) -> Dict[str, Any]:
    """Use models kept in another folder (another ComfyUI's models/, a shared drive) without copying them."""
    path = os.path.abspath(os.path.expanduser(str(path or "").strip().strip('"')))
    if not os.path.isdir(path):
        raise ValueError(f"Folder not found: {path}")
    if os.path.basename(path).lower() != "models" and os.path.isdir(os.path.join(path, "models")):
        path = os.path.join(path, "models")         # a ComfyUI folder was given: use its models/
    kinds = [k for k in MODEL_KINDS if os.path.isdir(os.path.join(path, k))]
    if not kinds:
        raise ValueError("No model folders (diffusion_models, loras, vae, text_encoders…) inside " + path)
    import yaml
    yp = _yaml_path()
    conf = {}
    if os.path.isfile(yp):
        with open(yp, encoding="utf-8") as f:
            conf = yaml.safe_load(f) or {}
    conf[LINK_SECTION] = {"base_path": path, **{k: k for k in kinds}}
    with open(yp, "w", encoding="utf-8") as f:
        yaml.safe_dump(conf, f, allow_unicode=True, sort_keys=False)
    try:   # active now, without restarting ComfyUI
        import folder_paths
        for k in kinds:
            folder_paths.add_model_folder_path(k, os.path.join(path, k))
    except Exception:
        pass
    return {**status(), "linked_now": path, "kinds": kinds}
