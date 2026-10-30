import os
import json
import pytest
from vfx_nodes.telemetry_service import get_system_telemetry
from vfx_nodes.deconstruct_service import (
    get_project_deconstruction,
    save_project_deconstruction,
    toggle_deconstruction_item,
    remove_deconstruction_item,
    generate_nuke_export
)
from vfx_nodes.deconstruct_node import VFXDeconstructNukeNode

def test_telemetry_service():
    telem = get_system_telemetry()
    assert isinstance(telem, dict)
    assert "cpu_pct" in telem
    assert "ram_pct" in telem
    assert "ram_total_gb" in telem
    assert "gpu_util" in telem
    assert "vram_total_gb" in telem
    assert "disk_free_gb" in telem
    assert "io_read_mb_s" in telem
    assert "io_write_mb_s" in telem
    assert telem["cpu_pct"] >= 0
    assert telem["ram_pct"] >= 0

def test_deconstruction_service_crud(tmp_path):
    project_id = "test_deconstruct_proj"
    
    # 1. Toggle add item
    res = toggle_deconstruction_item(project_id, "mask_hero", enabled=True, item_data={
        "id": "mask_hero",
        "source_id": "mask_hero",
        "source_type": "mask",
        "name": "Hero Character Mask",
        "alias": "hero_mask",
        "data_type": "MASK"
    })
    assert res["status"] == "ok"
    assert res["enabled"] is True

    # 2. Get items
    dec_info = get_project_deconstruction(project_id)
    items = dec_info.get("items", [])
    assert len(items) >= 1
    found = next((x for x in items if x["source_id"] == "mask_hero"), None)
    assert found is not None
    assert found["alias"] == "hero_mask"
    assert found["enabled"] is True

    # 3. Toggle off
    res2 = toggle_deconstruction_item(project_id, "mask_hero", enabled=False)
    assert res2["enabled"] is False

    # 4. Remove item
    res3 = remove_deconstruction_item(project_id, "mask_hero")
    assert res3["status"] == "ok"
    dec_info_after = get_project_deconstruction(project_id)
    items_after = dec_info_after.get("items", [])
    assert not any(x["source_id"] == "mask_hero" for x in items_after)

def test_nuke_export(tmp_path):
    project_id = "test_nuke_export_proj"
    toggle_deconstruction_item(project_id, "shot_cam", enabled=True, item_data={
        "id": "shot_cam",
        "source_id": "shot_cam",
        "source_type": "camera_3d",
        "name": "Shot Camera",
        "alias": "cam_shot01",
        "data_type": "VFX_CAMERA"
    })
    toggle_deconstruction_item(project_id, "bg_plate", enabled=True, item_data={
        "id": "bg_plate",
        "source_id": "bg_plate",
        "source_type": "plate",
        "name": "Background Plate",
        "alias": "plate_clean",
        "data_type": "IMAGE"
    })

    export_res = generate_nuke_export(project_id)
    assert export_res["status"] == "ok"
    assert os.path.exists(export_res["nuke_script"])
    assert export_res["items_exported"] >= 2

    # Check script content
    with open(export_res["nuke_script"], "r", encoding="utf-8") as f:
        nk_text = f.read()
    assert "Root {" in nk_text
    assert "cam_shot01" in nk_text or "Camera2" in nk_text

def test_deconstruct_nuke_node():
    node = VFXDeconstructNukeNode()
    out = node.process(project_id="test_nuke_export_proj")
    assert isinstance(out, tuple)
    assert len(out) == 12
    # out[9] is nuke_script_path, out[10] is chan_path, out[11] is manifest dict
    nuke_path = out[9]
    manifest = out[11]
    assert isinstance(nuke_path, str)
    assert isinstance(manifest, dict)
    assert "items" in manifest
