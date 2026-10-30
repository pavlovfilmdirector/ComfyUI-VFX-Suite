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
    if out is not None and out.ndim > 2:
        out = out.squeeze(-1)
    assert abs(int(out[70, 80]) - int(out[70, 20])) < 12      # the hole now continues the floor at that height
    assert out[100, 80] > out[40, 80]                          # and keeps the gradient


def test_create_blank_video(vl, project):
    item = vl.create_blank_video(
        pid=project.pid,
        title="Тестовый холст",
        width=320,
        height=180,
        fps=24.0,
        total_frames=5,
        color="#00ff00"
    )
    assert item["id"].startswith("vid_canvas_")
    assert item["title"] == "Тестовый холст"
    assert item["badge"] == "CANVAS"
    assert item["width"] == 320
    assert item["height"] == 180
    assert item["total_frames"] == 5
    assert len(item["layers"]) == 1
    assert item["layers"][0]["type"] == "paint"

    # Verify plate directory structure and generated frames
    raw_dir = os.path.join(vl.video_root(project.pid, item["id"]), "plate", "raw")
    assert os.path.isfile(os.path.join(raw_dir, "frame_0000.jpg"))
    assert os.path.isfile(os.path.join(raw_dir, "frame_0004.jpg"))

    # Verify state contains plate info and paint layer
    state = vl.get_state(project.pid, item["id"])
    assert state["plate"]["total_frames"] == 5
    assert any(l["type"] == "paint" for l in state["layers"])


def test_resolve_ic_lora_file(vl, monkeypatch):
    # Test fallback mapping when folder_paths is not available or empty
    r1 = vl.resolve_ic_lora_file("clean-plate")
    assert "clean-plate" in r1.lower() and r1.endswith(".safetensors")

    r2 = vl.resolve_ic_lora_file("clean_plate")
    assert "clean-plate" in r2.lower() and r2.endswith(".safetensors")

    r3 = vl.resolve_ic_lora_file("layout-to-render")
    assert "layout-to-render" in r3.lower() and r3.endswith(".safetensors")

    # When exact filename list is provided via folder_paths
    class DummyFolderPaths:
        @staticmethod
        def get_filename_list(t):
            return [
                r"LTX-2.5\ltx-2.5-22b-ic-lora-clean-plate-1.0.safetensors",
                r"LTX-2.5\ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors"
            ]

    monkeypatch.setattr(vl, "folder_paths", DummyFolderPaths, raising=False)
    # Monkeypatch inside sys.modules as well
    import sys
    monkeypatch.setitem(sys.modules, "folder_paths", DummyFolderPaths)

    resolved = vl.resolve_ic_lora_file("clean-plate")
    assert resolved == r"LTX-2.5\ltx-2.5-22b-ic-lora-clean-plate-1.0.safetensors"

    resolved_alpha = vl.resolve_ic_lora_file("alpha")
    assert resolved_alpha == r"LTX-2.5\ltx-2.5-22b-ic-lora-alpha-gen-0.9.safetensors"


def test_tracker_ground_plane_wall_rejection():
    """Verify that vertical walls in point cloud are rejected and camera remains upright at 1.55m."""
    from scipy.spatial.transform import Rotation
    o3d = pytest.importorskip("open3d")

    # Simulate camera roughly upright with slight breathing
    avg_cam_up = np.array([-0.01, 0.005, 0.9999])
    avg_cam_up /= np.linalg.norm(avg_cam_up)

    # Simulate point cloud where largest planar feature is a vertical wall (normal along X)
    wall_pts = []
    for y in np.linspace(-5, 5, 20):
        for z in np.linspace(-1, 3, 20):
            wall_pts.append([2.0, y, z])
    raw_pts_b = np.array(wall_pts)

    # Detection logic
    cam_z_mean = 1.0
    lower_candidates = raw_pts_b[raw_pts_b[:, 2] < cam_z_mean]
    ground_normal = None
    if len(lower_candidates) >= 30:
        rem_pts = lower_candidates.copy()
        pcd = o3d.geometry.PointCloud()
        for _ in range(5):
            if len(rem_pts) < 30:
                break
            pcd.points = o3d.utility.Vector3dVector(rem_pts)
            plane_model, inliers = pcd.segment_plane(distance_threshold=0.15, ransac_n=3, num_iterations=100)
            n = np.array(plane_model[:3])
            if np.dot(n, avg_cam_up) < 0:
                n = -n
            n /= np.linalg.norm(n)
            cos_sim = float(np.dot(n, avg_cam_up))
            if cos_sim >= 0.80 and len(inliers) >= 30:
                ground_normal = n
                break
            rem_pts = np.delete(rem_pts, inliers, axis=0)

    # Wall must have been rejected!
    assert ground_normal is None

    # Fallback to upright camera
    if ground_normal is None:
        ground_normal = avg_cam_up.copy()

    rot_align, _ = Rotation.align_vectors([[0, 0, 1]], [ground_normal])
    R_align = rot_align.as_matrix()

    aligned_cam_up = R_align @ avg_cam_up
    assert aligned_cam_up[2] > 0.999  # Aligned to world Z-up




def test_job_render_and_export_respect_region(vl, project):
    item = vl.create_blank_video(
        pid=project.pid,
        title="Range Test Canvas",
        width=160,
        height=90,
        fps=24.0,
        total_frames=10,
        color="#0000ff"
    )
    vid = item["id"]
    lid = item["layers"][0]["id"]

    # 1. Test render with explicit start and end region (frames 2..6 -> 5 frames)
    vl._JOBS["job_test_render"] = {"id": "job_test_render", "status": "running"}
    render_res = vl._job_render("job_test_render", "render", project.pid, vid, {
        "name": "render_ranged",
        "include_plate": True,
        "start": 2,
        "end": 6
    })
    assert render_res["video"]["frames"] == 5

    # 2. Test layer_to_video with explicit start and end region (frames 1..4 -> 4 frames)
    vl._JOBS["job_test_export"] = {"id": "job_test_export", "status": "running"}
    export_res = vl._job_layer_to_video("job_test_export", "layer_to_video", project.pid, vid, {
        "layer_id": lid,
        "start": 1,
        "end": 4
    })
    assert export_res["video"]["frames"] == 4


def test_paint_key_move(vl, project):
    item = vl.create_blank_video(
        project.pid,
        title="Move Key Test",
        width=320,
        height=240,
        total_frames=10,
        color="#000000"
    )
    vid = item["id"]
    # Create paint layer with key at frame 2
    b64_dot = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
    st = vl.save_still_layer(project.pid, vid, None, "Paint Layer", b64_dot, frame=2, autokey=True)
    lid = st["layer_id"]
    lay = next(l for l in st["layers"] if l["id"] == lid)
    assert 2 in lay["keys"]

    # Move key from 2 to 5
    st_moved = vl.paint_key_op(project.pid, vid, lid, [{"from": 2, "to": 5}], op="move")
    lay_moved = next(l for l in st_moved["layers"] if l["id"] == lid)
    assert 2 not in lay_moved["keys"]
    assert 5 in lay_moved["keys"]
