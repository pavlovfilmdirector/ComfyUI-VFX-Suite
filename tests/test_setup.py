"""First-run setup: model list from install_manifest.json, installed detection, linking a models folder."""
import json
import os

import pytest
from conftest import load


@pytest.fixture
def ss(tmp_path, monkeypatch):
    m = load("setup_service")
    comfy = tmp_path / "ComfyUI"
    (comfy / "models" / "vae" / "LTX-2.5").mkdir(parents=True)
    (comfy / "models" / "vae" / "LTX-2.5" / "v.safetensors").write_bytes(b"x")
    man = tmp_path / "install_manifest.json"
    man.write_text(json.dumps({"models": [
        {"group": "core", "file": "v.safetensors", "dir": "vae/LTX-2.5", "size_gb": 1, "url": "https://h/v"},
        {"group": "core", "file": "t.safetensors", "dir": "diffusion_models/ltx-2.5", "size_gb": 2, "url": "https://h/t"},
        {"group": "core", "file": "ck.pth", "dir": "@custom_nodes/X/models", "url": "https://h/ck"},
        {"group": "auto", "file": "yolo.pt", "dir": "(ultralytics)"}]}))
    monkeypatch.setattr(m, "MANIFEST", str(man))
    monkeypatch.setattr(m, "STATE", str(tmp_path / "state.json"))
    monkeypatch.setattr(m, "_comfy_root", lambda: str(comfy))
    return m, comfy, tmp_path


def test_status_marks_installed_and_downloadable(ss):
    m, comfy, _ = ss
    st = m.status()
    by = {x["file"]: x for x in st["models"]}
    assert by["v.safetensors"]["installed"] and not by["t.safetensors"]["installed"]
    assert by["t.safetensors"]["downloadable"] and by["t.safetensors"]["dest"] == "diffusion_models/ltx-2.5/t.safetensors"
    assert not by["ck.pth"]["downloadable"]          # outside ComfyUI/models: page link only
    assert st["done"] is False


def test_done_flag(ss):
    m, _, _ = ss
    m.mark_done(True)
    assert m.is_done()


def test_link_models_folder_finds_models_elsewhere(ss):
    m, comfy, tmp = ss
    other = tmp / "D" / "ComfyUI" / "models" / "diffusion_models" / "ltx-2.5"
    other.mkdir(parents=True)
    (other / "t.safetensors").write_bytes(b"x")
    r = m.link_models_folder(str(tmp / "D" / "ComfyUI"))          # the ComfyUI folder: its models/ is used
    assert r["linked_now"].endswith("models") and "diffusion_models" in r["kinds"]
    assert {x["file"]: x for x in r["models"]}["t.safetensors"]["installed"]
    assert "keshiki_linked" in (comfy / "extra_model_paths.yaml").read_text(encoding="utf-8")


def test_link_rejects_folder_without_models(ss, tmp_path):
    m, _, _ = ss
    with pytest.raises(ValueError):
        m.link_models_folder(str(tmp_path))
