"""User tools: an exported ComfyUI workflow becomes a studio tool with an automatic passport."""
import json
import os

import pytest

from conftest import load

WF = {
    "1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}, "_meta": {"title": "IN: Plate"}},
    "2": {"class_type": "LoadImage", "inputs": {"image": "y.png"}, "_meta": {"title": "Load Image"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "a cat", "clip": ["9", 0]}, "_meta": {"title": "Positive"}},
    "4": {"class_type": "KSampler", "inputs": {"seed": 5, "steps": 20, "model": ["9", 0]}, "_meta": {"title": "Sampler"}},
    "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "t"}, "_meta": {"title": "Save"}},
}


@pytest.fixture
def ut(tmp_path, monkeypatch, project):
    m = load("user_tools_service")
    monkeypatch.setattr(m, "TOOLS_DIR", str(tmp_path / "tools"))
    return m


def test_passport_from_tagged_graph(ut):
    d = ut.describe(WF)
    assert [i["label"] for i in d["inputs"]] == ["Plate"], "with IN: tags only the tagged loaders are inputs"
    assert d["inputs"][0]["field"] == "image" and d["inputs"][0]["kind"] == "image"
    ids = {p["id"]: p for p in d["params"]}
    assert {p["field"] for p in d["params"]} == {"text", "seed"}, "untagged graphs expose prompts and seeds"
    assert [o["class_type"] for o in d["outputs"]] == ["SaveImage"]


def test_param_tags_win(ut):
    wf = json.loads(json.dumps(WF))
    wf["6"] = {"class_type": "PrimitiveFloat", "inputs": {"value": 0.7}, "_meta": {"title": "PARAM: Strength"}}
    d = ut.describe(wf)
    assert [(p["label"], p["type"], p["default"]) for p in d["params"]] == [("Strength", "float", 0.7)]


def test_ids_are_unique_and_readable(ut):
    wf = json.loads(json.dumps(WF))
    wf["6"] = {"class_type": "Canny", "inputs": {"low_threshold": 0.4, "high_threshold": 0.8}, "_meta": {"title": "PARAM: Порог"}}
    wf["7"] = {"class_type": "Canny", "inputs": {"low_threshold": 0.1, "high_threshold": 0.2}, "_meta": {"title": "PARAM: Порог"}}
    ids = [p["id"] for p in ut.describe(wf)["params"]]
    assert len(ids) == len(set(ids)) == 4
    assert ut._slug("Мой грейд") == "moy_greyd"


def test_untagged_loaders_are_all_inputs(ut):
    wf = {k: v for k, v in WF.items()}
    wf["1"] = {**WF["1"], "_meta": {"title": "Load Image"}}
    assert len(ut.describe(wf)["inputs"]) == 2


def test_import_rejects_editor_format_and_graph_without_output(ut):
    with pytest.raises(ut.ProcessError, match="Export \\(API\\)"):
        ut.import_tool({"nodes": [], "links": []}, "x")
    with pytest.raises(ut.ProcessError, match="Save / Preview"):
        ut.import_tool({"1": WF["1"]}, "x")


def test_import_list_edit_build_delete(ut, tmp_path):
    t = ut.import_tool(json.dumps(WF), "Мой грейд", "colour grade", "video")
    assert t["id"] and t["tab"] == "video" and ut.list_tools()[0]["name"] == "Мой грейд"
    t2 = ut.import_tool(WF, "Мой грейд")
    assert t2["id"] != t["id"], "same name must not overwrite"
    ut.save_passport({"id": t["id"], "description": "new"})
    assert ut.get_tool(t["id"])["description"] == "new" and ut.get_tool(t["id"])["inputs"]
    src = tmp_path / "plate.png"
    src.write_bytes(b"png")
    seen = []
    ut._to_input_dir = lambda p: seen.append(p) or "copied.png"
    seed_id = next(p["id"] for p in t["params"] if p["field"] == "seed")
    pr = ut.build_prompt(t, {"in_1": "/vfx/project_file/p/a.png", seed_id: "11"}, lambda u: str(src))
    assert pr["1"]["inputs"]["image"] == "copied.png" and pr["4"]["inputs"]["seed"] == 11
    with pytest.raises(ut.ProcessError, match="Plate"):
        ut.build_prompt(t, {}, lambda u: str(src))
    ut.delete_tool(t["id"])
    assert all(x["id"] != t["id"] for x in ut.list_tools())
