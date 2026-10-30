# -*- coding: utf-8 -*-
"""
Tests for user_pipelines_service and pipeline REST endpoints.
"""

import os
import pytest
from vfx_nodes import user_pipelines_service as ups


def test_list_builtin_stages():
    stages = ups.list_builtin_stages()
    assert isinstance(stages, list)
    assert len(stages) >= 5
    stage_ids = [s["id"] for s in stages]
    assert "flux_t2i" in stage_ids
    assert "ltx25_i2v" in stage_ids
    assert "ltx23_dubit" in stage_ids
    assert "ltx25_a2v" in stage_ids


def test_pipeline_crud(tmp_path, monkeypatch):
    monkeypatch.setattr(ups, "PIPELINES_DIR", str(tmp_path))

    # List default presets
    pipes = ups.list_pipelines()
    assert len(pipes) >= 3
    preset_ids = [p["id"] for p in pipes]
    assert "preset_flux_to_ltx" in preset_ids

    # Get pipeline
    p1 = ups.get_pipeline("preset_flux_to_ltx")
    assert p1 is not None
    assert p1["id"] == "preset_flux_to_ltx"
    assert len(p1["stages"]) == 2

    # Save custom pipeline
    custom = {
        "id": "my_custom_pipe",
        "name": "Custom Test Pipeline",
        "stages": [
            {
                "id": "s1",
                "title": "Stage 1",
                "stage_type": "flux_t2i",
                "inputs": {"prompt": "Test prompt"}
            },
            {
                "id": "s2",
                "title": "Stage 2",
                "stage_type": "ltx25_i2v",
                "inputs": {
                    "start_image": {"from_stage": "s1", "output_key": "image"},
                    "prompt": "Test motion"
                }
            }
        ]
    }
    res = ups.save_pipeline(custom)
    assert res["status"] == "ok"
    assert res["id"] == "my_custom_pipe"

    # Verify retrieved
    p2 = ups.get_pipeline("my_custom_pipe")
    assert p2 is not None
    assert p2["name"] == "Custom Test Pipeline"
    assert p2["stages"][1]["inputs"]["start_image"]["from_stage"] == "s1"

    # Delete pipeline
    del_res = ups.delete_pipeline("my_custom_pipe")
    assert del_res["status"] == "ok"
    assert ups.get_pipeline("my_custom_pipe") is None


def test_resolve_asset_path(tmp_path, monkeypatch):
    monkeypatch.setattr(ups, "get_project_dir", lambda pid: str(tmp_path))
    test_file = tmp_path / "sample.png"
    test_file.write_text("dummy")

    # Absolute path
    assert ups._resolve_asset_path(str(test_file), "p1") == str(test_file)

    # Relative to project
    assert ups._resolve_asset_path("sample.png", "p1") == str(test_file)

    # Web URL path
    assert ups._resolve_asset_path("/vfx/project_file/p1/sample.png", "p1") == str(test_file)
