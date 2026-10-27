"""Video view processes that run without a GPU: clip join with dissolve, background-only depth, relight prompts."""
import os

import cv2
import numpy as np
import pytest
from conftest import load, make_video


@pytest.fixture
def vl(project, monkeypatch):
    m = load("video_layers_service")
    m._JOBS["t"] = {"id": "t", "status": "running"}
    return m


# ---------- relight prompt (Qwen Multiangle Lightning formula)
@pytest.mark.parametrize("az,el,expect", [(0, 0, "in front"), (90, 30, "from the right"), (180, 70, "from behind"),
                                          (270, -50, "from the left"), (350, 10, "in front"), (45, 0, "front-right")])
def test_lighting_prompt_direction(vl, az, el, expect):
    assert expect in vl.lighting_prompt(az, el, 5, "#ffffff")


def test_lighting_prompt_strength_and_height(vl):
    assert "soft" in vl.lighting_prompt(0, 0, 1) and "intense" in vl.lighting_prompt(0, 0, 9)
    assert "overhead" in vl.lighting_prompt(0, 80) and "uplighting" in vl.lighting_prompt(0, -60)
    assert vl.lighting_prompt(0, 0).startswith("SCENE LOCK")


def test_directional_relight_calls_qwen_without_lora(vl, project, monkeypatch, tmp_path):
    calls = []
    img = str(tmp_path / "ff.png"); cv2.imwrite(img, np.full((32, 32, 3), 128, np.uint8))
    import vfx_nodes.image_generate_service as ig
    monkeypatch.setattr(ig, "run_qwen_edit", lambda imgs, prompt, **k: calls.append((imgs, prompt, k)) or
                        {"image": "data:image/png;base64," + __import__("base64").b64encode(cv2.imencode(".png", np.zeros((8, 8, 3), np.uint8))[1].tobytes()).decode(), "seed": 1, "seconds": 0.1})
    monkeypatch.setattr(vl, "resolve_media_path", lambda u: img)
    r = vl._job_relight_frame("t", "relight_frame", project.pid, "v1", {"image": "/ff.png", "azimuth": 90, "elevation": 30, "color": "#ffaa00"})
    imgs, prompt, k = calls[0]
    assert len(imgs) == 1 and "from the right" in prompt and "#ffaa00" in prompt and not k.get("loras")
    assert r["image"]["url"].endswith(".png")


# ---------- montage: dissolve
def test_montage_joins_with_dissolve(vl, project, monkeypatch, tmp_path):
    a = make_video(str(tmp_path / "a.mp4"), frames=30, color=(0, 0, 255))
    b = make_video(str(tmp_path / "b.mp4"), frames=20, color=(255, 0, 0))
    monkeypatch.setattr(vl, "resolve_media_path", lambda u: {"/a": a, "/b": b}[u])
    r = vl._job_montage("t", "montage", project.pid, "", {"videos": [{"url": "/a"}, {"url": "/b"}], "overlap": 8})
    assert r["video"]["frames"] == 30 + 20 - 8
    cap = cv2.VideoCapture(os.path.join(project.dir, "source_video", os.path.basename(r["video"]["url"])))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f.mean(axis=(0, 1)))
    mid = frames[30 - 4]                 # inside the dissolve: both colours present
    assert mid[0] > 40 and mid[2] > 40


def test_montage_needs_two_clips(vl, project):
    with pytest.raises(Exception, match="two"):
        vl._job_montage("t", "montage", project.pid, "", {"videos": [{"url": "/a"}]})


# ---------- background-only depth
def test_depth_without_the_character(vl, project):
    vid = "vtest"
    vl.save_manifest(project.pid, vid, {"layers": []}) if hasattr(vl, "save_manifest") else None
    mask = vl._add_layer(project.pid, vid, "mask", "person")
    depth = vl._add_layer(project.pid, vid, "depth", "depth")
    H, W = 120, 160
    for i in range(3):
        d = np.tile(np.linspace(30, 220, H, dtype=np.float32)[:, None], (1, W)).astype(np.uint8)   # floor gradient
        d[30:110, 60:100] = 250                                                                   # the person (near)
        cv2.imwrite(os.path.join(vl._layer_dir(project.pid, vid, depth["id"]), f"frame_{i:04d}.png"), d)
        m = np.zeros((H, W), np.uint8); m[30:110, 60:100] = 255
        cv2.imwrite(os.path.join(vl._layer_dir(project.pid, vid, mask["id"]), f"frame_{i:04d}.png"), m)
    vl._depth_exclude_subject("t", project.pid, vid, vl._layer_dir(project.pid, vid, depth["id"]), mask["id"])
    out = cv2.imread(os.path.join(vl._layer_dir(project.pid, vid, depth["id"]), "frame_0001.png"), 0)
    assert abs(int(out[70, 80]) - int(out[70, 20])) < 12      # the hole now continues the floor at that height
    assert out[100, 80] > out[40, 80]                          # and keeps the gradient
