import os
import json
import pytest
from conftest import load


def test_delete_project_asset(tmp_path, monkeypatch):
    pm = load("project_manager")
    pid = "test_del_proj"
    p_dir = tmp_path / pid
    monkeypatch.setattr(pm, "get_project_dir", lambda _: str(p_dir))

    # Setup project with folders and project.json
    pm.create_or_open_project("Test Del Proj", project_id=pid)

    # Create test media files
    sb_dir = p_dir / "storyboard"
    sb_dir.mkdir(parents=True, exist_ok=True)
    img_file = sb_dir / "test_shot.png"
    img_file.write_bytes(b"dummy_png_bytes")

    aud_dir = p_dir / "audio"
    aud_dir.mkdir(parents=True, exist_ok=True)
    aud_file = aud_dir / "test_track.mp3"
    aud_file.write_bytes(b"dummy_audio_bytes")

    vid_dir = p_dir / "source_video"
    vid_dir.mkdir(parents=True, exist_ok=True)
    vid_file = vid_dir / "test_plate.mp4"
    vid_file.write_bytes(b"dummy_video_bytes")

    # Update project state with media lists
    pm.save_project_state(pid, {
        "mediaImages1D": [{"id": "img_1", "title": "Shot 1", "url": f"/vfx/project_file/{pid}/storyboard/test_shot.png"}],
        "mediaAudio": [{"id": "aud_1", "title": "Track 1", "url": f"/vfx/project_file/{pid}/audio/test_track.mp3"}],
        "mediaVideos2D": [{"id": "vid_1", "title": "Plate 1", "videoUrl": f"/vfx/project_file/{pid}/source_video/test_plate.mp4"}]
    })

    assert img_file.exists()
    assert aud_file.exists()
    assert vid_file.exists()

    # 1. Delete image asset
    res1 = pm.delete_project_asset(pid, asset_id="img_1", url=f"/vfx/project_file/{pid}/storyboard/test_shot.png")
    assert res1["success"] is True
    assert not img_file.exists()
    proj_after_1 = json.loads((p_dir / "project.json").read_text(encoding="utf-8"))
    assert len(proj_after_1.get("mediaImages1D", [])) == 0

    # 2. Delete audio asset
    res2 = pm.delete_project_asset(pid, asset_id="aud_1", filename="test_track.mp3", sub="audio")
    assert res2["success"] is True
    assert not aud_file.exists()
    proj_after_2 = json.loads((p_dir / "project.json").read_text(encoding="utf-8"))
    assert len(proj_after_2.get("mediaAudio", [])) == 0

    # 3. Delete video asset
    res3 = pm.delete_project_asset(pid, asset_id="vid_1", url=f"/vfx/project_file/{pid}/source_video/test_plate.mp4")
    assert res3["success"] is True
    assert not vid_file.exists()
    proj_after_3 = json.loads((p_dir / "project.json").read_text(encoding="utf-8"))
    assert len(proj_after_3.get("mediaVideos2D", [])) == 0


def test_delete_generated_comfy_asset(tmp_path, monkeypatch):
    pm = load("project_manager")
    pid = "test_gen_del"
    p_dir = tmp_path / pid
    comfy_out = tmp_path / "comfy_output"
    comfy_out.mkdir(parents=True, exist_ok=True)
    keshiki_out = comfy_out / "keshiki"
    keshiki_out.mkdir(parents=True, exist_ok=True)

    gen_vid = keshiki_out / "video_shot_099_00001_.mp4"
    gen_vid.write_bytes(b"dummy_mp4_content")

    monkeypatch.setattr(pm, "get_project_dir", lambda _: str(p_dir))
    monkeypatch.setattr(pm, "_get_comfyui_output_dir", lambda: str(comfy_out))

    pm.create_or_open_project("Test Gen Del", project_id=pid)
    url = "/view?filename=video_shot_099_00001_.mp4&type=output&subfolder=keshiki"
    pm.save_project_state(pid, {
        "mediaVideos2D": [{"id": "vid_gen_video_shot_099_00001__mp4", "title": "LTX Video: shot_099", "videoUrl": url}]
    })

    assert gen_vid.exists()

    res = pm.delete_project_asset(pid, asset_id="vid_gen_video_shot_099_00001__mp4", url=url, filename="video_shot_099_00001_.mp4", sub="keshiki")
    assert res["success"] is True
    assert not gen_vid.exists()

    proj = json.loads((p_dir / "project.json").read_text(encoding="utf-8"))
    assert len(proj.get("mediaVideos2D", [])) == 0


def test_delete_asset_path_traversal_blocked(tmp_path, monkeypatch):
    pm = load("project_manager")
    pid = "test_traversal"
    p_dir = tmp_path / pid
    monkeypatch.setattr(pm, "get_project_dir", lambda _: str(p_dir))
    pm.create_or_open_project("Test Traversal", project_id=pid)

    # File outside project directory
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("critical_secret")

    # Attempt path traversal deletion
    res = pm.delete_project_asset(pid, filename="../../secret.txt", sub="storyboard")
    assert res["success"] is True
    # The external secret file must NOT be deleted
    assert secret_file.exists()
    assert secret_file.read_text() == "critical_secret"
