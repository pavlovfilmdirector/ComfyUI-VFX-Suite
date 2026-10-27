"""
1D Image Layers Storage
=======================
Every image in a project keeps its layers as separate PNG files:

    projects/<project>/layers/<image_id>/layers.json
    projects/<project>/layers/<image_id>/<layer_id>.png

layers.json holds the ordered stack (bottom -> top) with metadata only:
    {"image_id": ..., "active_layer_id": ..., "layers": [
        {"id", "name", "type", "visible", "opacity", "color", "file", "updated_at"}
    ]}

Layer types: paint | mask | depth | canny | pose
"""

import base64
import json
import os
import re
import time
from typing import Any, Dict, List

try:
    from .project_manager import get_project_dir
except ImportError:  # pragma: no cover
    from project_manager import get_project_dir

LAYER_TYPES = ("paint", "mask", "depth", "normal", "canny", "pose")
_ID_RE = re.compile(r"[^A-Za-z0-9_\-]")
_MAX_LAYER_BYTES = 64 * 1024 * 1024


def _safe_id(value: str, fallback: str) -> str:
    clean = _ID_RE.sub("_", str(value or "").strip())[:96]
    return clean or fallback


def layers_dir(project_id: str, image_id: str) -> str:
    return os.path.join(get_project_dir(project_id), "layers", _safe_id(image_id, "image"))


def _file_url(project_id: str, image_id: str, filename: str, mtime: float) -> str:
    return f"/vfx/project_file/{project_id}/layers/{_safe_id(image_id, 'image')}/{filename}?v={int(mtime * 1000)}"


def _decode_data_url(data_url: str) -> bytes:
    if not data_url.startswith("data:image/png;base64,"):
        raise ValueError("Layer data must be a PNG data URL")
    raw = base64.b64decode(data_url.split(",", 1)[1])
    if len(raw) > _MAX_LAYER_BYTES:
        raise ValueError("Layer PNG is too large")
    return raw


def save_layers(project_id: str, image_id: str, layers: List[Dict[str, Any]], active_layer_id: str = "") -> Dict[str, Any]:
    """Writes the layer stack. A layer with `data` (PNG data URL) gets its file (re)written;
    a layer without `data` keeps its existing file. Files of removed layers are deleted."""
    d = layers_dir(project_id, image_id)
    os.makedirs(d, exist_ok=True)
    out: List[Dict[str, Any]] = []
    keep_files = set()
    for i, layer in enumerate(layers or []):
        lid = _safe_id(layer.get("id"), f"layer_{i}")
        ltype = layer.get("type") if layer.get("type") in LAYER_TYPES else "paint"
        filename = f"{lid}.png"
        path = os.path.join(d, filename)
        data = layer.get("data")
        if isinstance(data, str) and data:
            with open(path, "wb") as f:
                f.write(_decode_data_url(data))
        has_file = os.path.isfile(path)
        if has_file:
            keep_files.add(filename)
        out.append({
            "id": lid,
            "name": str(layer.get("name") or f"Layer {i + 1}")[:80],
            "type": ltype,
            "visible": bool(layer.get("visible", True)),
            "opacity": max(0.0, min(1.0, float(layer.get("opacity", 1.0)))),
            "color": str(layer.get("color") or "")[:16],
            "file": filename if has_file else "",
            "skeleton": layer.get("skeleton") if isinstance(layer.get("skeleton"), list) and len(layer.get("skeleton")) == 18 else None,
            "transparentBg": bool(layer.get("transparentBg")),
            "inverted": bool(layer.get("inverted")),
            "comment": str(layer.get("comment") or "")[:4000],
            "updated_at": os.path.getmtime(path) if has_file else 0,
        })
    for fn in os.listdir(d):
        if fn.endswith(".png") and fn not in keep_files:
            try:
                os.remove(os.path.join(d, fn))
            except OSError:
                pass
    manifest = {"image_id": image_id, "active_layer_id": _safe_id(active_layer_id, ""), "layers": out}
    with open(os.path.join(d, "layers.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    return _with_urls(project_id, image_id, manifest)


def load_layers(project_id: str, image_id: str) -> Dict[str, Any]:
    path = os.path.join(layers_dir(project_id, image_id), "layers.json")
    if not os.path.isfile(path):
        return {"image_id": image_id, "active_layer_id": "", "layers": []}
    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    return _with_urls(project_id, image_id, manifest)


def _with_urls(project_id: str, image_id: str, manifest: Dict[str, Any]) -> Dict[str, Any]:
    res = dict(manifest)
    res["layers"] = []
    for layer in manifest.get("layers", []):
        item = dict(layer)
        item["src"] = _file_url(project_id, image_id, layer["file"], layer.get("updated_at") or time.time()) if layer.get("file") else ""
        res["layers"].append(item)
    return res
