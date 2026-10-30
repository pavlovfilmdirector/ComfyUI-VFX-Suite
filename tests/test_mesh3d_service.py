import os
import json
import time
import pytest
from unittest.mock import patch, MagicMock

from vfx_nodes.mesh3d_service import (
    list_mesh3d_library,
    check_comfy_trellis2_ready,
    start_3d_generation_job,
    get_3d_job_status,
    _run_comfy_3d_generation,
    _resolve_input_image,
    remove_background_image,
    _JOBS,
    _JOBS_LOCK
)


def test_list_mesh3d_library():
    res = list_mesh3d_library()
    assert "meshes" in res
    assert "engines" in res
    assert "trellis2" in res["engines"]
    assert "hunyuan_omni" in res["engines"]
    trellis = res["engines"]["trellis2"]
    assert "available" in trellis
    assert "weights_ready" in trellis
    assert trellis["available"] is True


def test_check_comfy_trellis2_ready():
    # Should run without error and return a boolean
    ready = check_comfy_trellis2_ready()
    assert isinstance(ready, bool)


def test_start_3d_generation_job_registration(tmp_path):
    test_img = str(tmp_path / "test.png")
    with open(test_img, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    params = {
        "engine": "comfy_trellis2",
        "name": "TestSword",
        "prompt": "An ancient glowing rune sword",
        "image_source": test_img,
        "bbox": [1.5, 0.4, 0.2],
        "texture_size": 2048,
        "seed": 1234
    }

    job = start_3d_generation_job(params)
    assert "job_id" in job
    assert job["engine"] == "comfy_trellis2"
    assert job["actor_name"] == "TestSword"
    assert job["status"] in ("running", "completed", "failed")

    # Status check
    st = get_3d_job_status(job["job_id"])
    assert st["job_id"] == job["job_id"]
    assert st["prompt"] == "An ancient glowing rune sword"


def test_run_comfy_3d_generation_mock(tmp_path):
    test_img = str(tmp_path / "source.png")
    with open(test_img, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    fake_glb = str(tmp_path / "mesh_fake.glb")
    with open(fake_glb, "wb") as f:
        f.write(b"glTF\x02\x00\x00\x00\x14\x00\x00\x00")

    mock_template = {
        "122": {"class_type": "LoadImage", "inputs": {"image": "default.png"}},
        "316": {"class_type": "PrimitiveBoolean", "inputs": {"value": False}},
        "288": {"class_type": "PrimitiveInt", "inputs": {"value": 1024}},
        "186": {"class_type": "DecimateMesh", "inputs": {"target_face_count": 100000}},
        "322": {"class_type": "Save3DAdvanced", "inputs": {"filename_prefix": "3d/default"}},
        "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "18": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "23": {"class_type": "KSampler", "inputs": {"seed": 1}},
        "12": {"class_type": "KSampler", "inputs": {"seed": 1}},
    }

    job_record = {
        "progress": 10,
        "message": "Starting",
        "logs": []
    }

    with patch("vfx_nodes.process_runner.load_template", return_value=mock_template), \
         patch("vfx_nodes.process_runner._post_json", return_value={"prompt_id": "fake_prompt_123"}), \
         patch("urllib.request.urlopen") as mock_urlopen, \
         patch("os.path.exists", side_effect=lambda p: True if p == fake_glb or p == test_img or "ComfyUI" in p else False):

        # Mock history response
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "fake_prompt_123": {
                "status": {"status_str": "success", "completed": True},
                "outputs": {
                    "322": {
                        "models": [{"filename": "mesh_fake.glb", "subfolder": "3d"}]
                    }
                }
            }
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with patch("shutil.copy2") as mock_copy:
            # Test running generator
            with patch("vfx_nodes.mesh3d_service.os.path.exists", return_value=True):
                res = _run_comfy_3d_generation(
                    job_id="test1234",
                    job_record=job_record,
                    input_img_path=test_img,
                    actor_name="TestObj",
                    safe_slug="TestObj",
                    seed=777,
                    texture_size=4096,
                    decim=300000,
                    use_trellis2=True
                )
                assert res.endswith(".glb")
                assert mock_template["316"]["inputs"]["value"] is True
                assert mock_template["288"]["inputs"]["value"] == 4096
                assert mock_template["186"]["inputs"]["target_face_count"] == 300000
                assert mock_template["3"]["inputs"]["seed"] == 777


def test_resolve_input_image_comfy_view(tmp_path):
    # Simulate a ComfyUI output image
    test_comfy_out = str(tmp_path / "comfy_test_surf.png")
    with open(test_comfy_out, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    # Patch ComfyUI output path check
    with patch("os.path.exists", side_effect=lambda p: True if "comfy_test_surf.png" in p else False):
        res = _resolve_input_image("/view?filename=comfy_test_surf.png&type=output")
        assert res.endswith(".png")
        assert "input_" in res


def test_remove_background_image_transparent(tmp_path):
    # Test image with existing transparency
    from PIL import Image
    test_img = str(tmp_path / "already_cutout.png")
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    # Draw a non-empty subject
    for x in range(30, 70):
        for y in range(30, 70):
            im.putpixel((x, y), (255, 0, 0, 255))
    im.save(test_img, "PNG")

    res = remove_background_image(test_img)
    assert res.get("status") == "ok"
    assert "cutout_url" in res
    assert "cutout_" in res["cutout_filename"]
