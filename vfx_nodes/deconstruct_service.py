"""
Deconstruction & Nuke Bridge Service
====================================
Manages the project's Deconstruction export stack and generates Foundry Nuke scripts (.nk + .chan)
as well as dynamic output feeds for the ComfyUI VFXDeconstructNuke node.
"""

import os
import sys
import json
import time
import math
import shutil
from typing import Dict, Any, List, Optional

try:
    from .project_manager import get_project_dir
except ImportError:
    from project_manager import get_project_dir

try:
    from nuke_plugin.camera_exporter import export_chan_file
except ImportError:
    try:
        from ..nuke_plugin.camera_exporter import export_chan_file
    except Exception:
        export_chan_file = None


def _load_project_json(project_id: str) -> tuple[str, dict]:
    p_dir = get_project_dir(project_id)
    p_file = os.path.join(p_dir, "project.json")
    data = {}
    if os.path.exists(p_file):
        try:
            with open(p_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    return p_file, data


def _save_project_json(p_file: str, data: dict) -> None:
    os.makedirs(os.path.dirname(p_file), exist_ok=True)
    with open(p_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_project_deconstruction(project_id: str) -> Dict[str, Any]:
    """Returns the list of items configured for deconstruction export."""
    p_file, p_data = _load_project_json(project_id)
    items = p_data.get("deconstruction_items")
    if items is None:
        items = []
        p_data["deconstruction_items"] = items
        _save_project_json(p_file, p_data)
    return {
        "project_id": project_id,
        "items": items,
        "count": len(items)
    }


def save_project_deconstruction(project_id: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Replaces or saves the full deconstruction items list."""
    p_file, p_data = _load_project_json(project_id)
    p_data["deconstruction_items"] = items
    _save_project_json(p_file, p_data)
    return {
        "status": "ok",
        "project_id": project_id,
        "items": items,
        "count": len(items)
    }


def toggle_deconstruction_item(project_id: str, item_data_or_id: Any, enabled: Optional[bool] = None, item_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Adds, updates, or toggles an item in the project's deconstruction list."""
    if isinstance(item_data_or_id, dict):
        base_item = dict(item_data_or_id)
    else:
        base_item = dict(item_data or {})
        if item_data_or_id:
            base_item.setdefault("id", str(item_data_or_id))
            base_item.setdefault("source_id", str(item_data_or_id))
    if enabled is not None:
        base_item["enabled"] = bool(enabled)

    p_file, p_data = _load_project_json(project_id)
    items = p_data.get("deconstruction_items", [])

    item_id = str(base_item.get("id") or "").strip()
    if not item_id:
        source_id = str(base_item.get("source_id") or "item")
        item_id = f"dec_{source_id}_{int(time.time() * 1000) % 10000}"

    existing_idx = next((i for i, it in enumerate(items) if it.get("id") == item_id or (it.get("source_id") and it.get("source_id") == base_item.get("source_id"))), -1)

    if existing_idx >= 0:
        # Update existing
        target_enabled = base_item.get("enabled")
        if target_enabled is None:
            target_enabled = not items[existing_idx].get("enabled", True)
        items[existing_idx].update(base_item)
        items[existing_idx]["enabled"] = bool(target_enabled)
        updated_item = items[existing_idx]
    else:
        # Create new
        new_item = {
            "id": item_id,
            "source_type": base_item.get("source_type", "pass"),
            "source_id": base_item.get("source_id", item_id),
            "alias": base_item.get("alias") or base_item.get("name") or "pass_out",
            "name": base_item.get("name") or "Unnamed Pass",
            "data_type": base_item.get("data_type", "IMAGE"),
            "file_path": base_item.get("file_path", ""),
            "url": base_item.get("url", ""),
            "enabled": bool(base_item.get("enabled", True)),
            "meta": base_item.get("meta", {})
        }
        items.append(new_item)
        updated_item = new_item

    p_data["deconstruction_items"] = items
    _save_project_json(p_file, p_data)
    return {
        "status": "ok",
        "enabled": updated_item.get("enabled", True),
        "item": updated_item,
        "items": items,
        "count": len(items)
    }


def remove_deconstruction_item(project_id: str, item_id: str) -> Dict[str, Any]:
    """Removes an item from the deconstruction list."""
    p_file, p_data = _load_project_json(project_id)
    items = p_data.get("deconstruction_items", [])
    items = [it for it in items if it.get("id") != item_id and it.get("source_id") != item_id]
    p_data["deconstruction_items"] = items
    _save_project_json(p_file, p_data)
    return {
        "status": "ok",
        "items": items,
        "count": len(items)
    }


def generate_nuke_export(project_id: str, custom_items: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Generates a complete Foundry Nuke script (.nk) and camera .chan files
    containing all enabled deconstruction layers, masks, and 3D camera tracks.
    """
    p_file, p_data = _load_project_json(project_id)
    items = custom_items if custom_items is not None else p_data.get("deconstruction_items", [])
    enabled_items = [it for it in items if it.get("enabled", True)]

    p_dir = get_project_dir(project_id)
    exports_dir = os.path.join(p_dir, "exports", "nuke")
    os.makedirs(exports_dir, exist_ok=True)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    script_filename = f"deconstruction_{stamp}.nk"
    script_path = os.path.join(exports_dir, script_filename)

    fps = float(p_data.get("fps") or 24.0)
    width = int(p_data.get("plateWidth") or p_data.get("width") or 1920)
    height = int(p_data.get("plateHeight") or p_data.get("height") or 1080)
    frames_count = int(p_data.get("frames_count") or 49)

    nk_lines = [
        "#! /usr/local/Nuke -nx",
        "# VFX Studio Deconstruction & Nuke Bridge Export",
        f"# Project: {project_id} | Date: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        "version 14.0 v5",
        "Root {",
        " first_frame 1",
        f" last_frame {frames_count}",
        f" fps {fps}",
        f' format "{width} {height} 0 0 {width} {height} 1.0 HD_{width}"',
        " colorManagement Nuke",
        "}\n"
    ]

    x_offset = 0
    y_offset = 0
    read_nodes = []
    cam_node_name = None
    chan_path = ""

    for idx, it in enumerate(enabled_items):
        alias = it.get("alias") or f"pass_{idx+1}"
        safe_alias = "".join(c if c.isalnum() or c == "_" else "_" for c in alias)
        data_type = it.get("data_type", "IMAGE").upper()
        raw_fp = it.get("file_path") or it.get("url") or ""

        # Normalize absolute path with forward slashes for Nuke
        fp = os.path.abspath(raw_fp).replace("\\", "/") if raw_fp and os.path.exists(raw_fp) else raw_fp.replace("\\", "/")

        if data_type == "VFX_CAMERA" or it.get("source_type") == "camera_3d":
            # Export .chan file if camera matrices exist
            cam_chan_file = os.path.join(exports_dir, f"{safe_alias}_{stamp}.chan")
            meta = it.get("meta") or {}
            matrices = meta.get("matrices") or p_data.get("cameraMatrices") or []
            focal_mm = float(meta.get("focalMm") or meta.get("focal_mm") or 35.0)
            sensor_w = float(meta.get("sensorWidthMm") or meta.get("sensor_width_mm") or 36.0)

            if matrices and export_chan_file:
                try:
                    export_chan_file(cam_chan_file, matrices, focal_mm=focal_mm, sensor_width_mm=sensor_w, start_frame=1)
                    chan_path = cam_chan_file.replace("\\", "/")
                except Exception:
                    pass

            nk_lines.extend([
                f"# --- 3D Camera Track: {safe_alias} ---",
                "Camera3 {",
                " inputs 0",
                f' name Camera_{safe_alias}',
                f' label "{alias} [Solved 3D Track]"',
                f' focal {focal_mm:.2f}',
                f' haperture {sensor_w:.2f}',
                f' vaperture {(sensor_w / (width / height)):.2f}',
                f' file "{chan_path}"' if chan_path else "",
                f' read_from_file {"true" if chan_path else "false"}',
                f" xpos {x_offset}",
                f" ypos {y_offset - 150}",
                "}\n"
            ])
            cam_node_name = f"Camera_{safe_alias}"

        elif data_type == "MASK":
            node_name = f"Read_Mask_{safe_alias}"
            read_nodes.append(node_name)
            nk_lines.extend([
                f"# --- Alpha / Mask Pass: {safe_alias} ---",
                "Read {",
                " inputs 0",
                f' file "{fp}"',
                f' name {node_name}',
                f' label "Mask: {alias}"',
                " first 1",
                f" last {frames_count}",
                f" xpos {x_offset}",
                f" ypos {y_offset}",
                "}\n"
            ])
            x_offset += 150

        else:
            node_name = f"Read_{safe_alias}"
            read_nodes.append(node_name)
            nk_lines.extend([
                f"# --- Footage / Pass Layer: {safe_alias} ---",
                "Read {",
                " inputs 0",
                f' file "{fp}"',
                f' name {node_name}',
                f' label "{alias}"',
                " first 1",
                f" last {frames_count}",
                f" xpos {x_offset}",
                f" ypos {y_offset}",
                "}\n"
            ])
            x_offset += 150

    # If camera and read nodes exist, construct a Scene and ScanlineRender block
    if cam_node_name:
        nk_lines.extend([
            "# --- 3D Scene Assembly ---",
            "Scene {",
            f" inputs 0",
            f" name Scene_Deconstruct",
            f" xpos {max(0, x_offset // 2)}",
            f" ypos {y_offset + 120}",
            "}",
            "ScanlineRender {",
            f" inputs 2",
            f" name ScanlineRender_Deconstruct",
            f" xpos {max(0, x_offset // 2)}",
            f" ypos {y_offset + 220}",
            "}\n"
        ])

    with open(script_path, "w", encoding="utf-8") as f:
        f.write("\n".join(nk_lines))

    return {
        "status": "ok",
        "script_filename": script_filename,
        "nuke_script_path": script_path,
        "nuke_script": script_path,
        "chan_path": chan_path,
        "chan_file": chan_path,
        "items_count": len(enabled_items),
        "items_exported": len(enabled_items),
        "exported_nodes": len(read_nodes) + (1 if cam_node_name else 0)
    }
