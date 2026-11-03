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
    pytest.importorskip("scipy")
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


# ---------- hand-drawn mask layers are keyed like the paint brush (held until the next key)
def _png(color, size=(48, 32), box=None):
    img = np.zeros((size[1], size[0], 4), np.uint8)
    if box:
        x0, y0, x1, y1 = box
        img[y0:y1, x0:x1] = color
    return cv2.imencode(".png", img)[1].tobytes()


def test_hand_mask_layer_holds_last_key(vl, project):
    vid = vl.create_blank_video(project.pid, title="Mask hold", width=48, height=32, total_frames=10)["id"]
    lid = vl._add_layer(project.pid, vid, "mask", "Hand mask")["id"]
    st = vl.save_layer_frame(project.pid, vid, lid, 2, _png((92, 59, 255, 240), box=(4, 4, 20, 20)))
    lay = next(l for l in st["layers"] if l["id"] == lid)
    assert lay["keys"] == [2]
    held = vl.frame_path(project.pid, vid, lid, 7)
    assert held and held.endswith("key_0002.png")          # last key carried forward
    vl.save_layer_frame(project.pid, vid, lid, 6, _png((92, 59, 255, 240), box=(30, 10, 44, 28)))
    assert vl.frame_path(project.pid, vid, lid, 8).endswith("key_0006.png")
    assert vl.frame_path(project.pid, vid, lid, 4).endswith("key_0002.png")


def test_generated_mask_layer_is_still_edited_per_frame(vl, project):
    vid = vl.create_blank_video(project.pid, title="Mask gen", width=48, height=32, total_frames=6)["id"]
    lay = vl._add_layer(project.pid, vid, "mask", "SAM")
    d = vl._layer_dir(project.pid, vid, lay["id"])
    for i in range(6):
        cv2.imwrite(os.path.join(d, f"frame_{i:04d}.png"), np.zeros((32, 48, 4), np.uint8))
    vl.save_layer_frame(project.pid, vid, lay["id"], 3, _png((255, 255, 255, 255), box=(0, 0, 8, 8)))
    assert not any(n.startswith("key_") for n in os.listdir(d))


# ---------- morph smoothing
def test_smooth_inbetween_rounds_jagged_edges(vl):
    img = np.zeros((40, 40, 4), np.uint8)
    img[10:30, 10:30] = (0, 0, 255, 255)
    img[10:30:2, 30] = (0, 0, 255, 255)                    # a comb of 1-px teeth on the edge
    out = vl._smooth_inbetween(img, 4)
    assert out.shape == img.shape
    assert out[20, 20, 3] == 255 and out[2, 2, 3] == 0     # inside stays solid, far outside stays empty
    assert out[11:29, 31, 3].std() < img[11:29, 30, 3].std()   # the teeth are smoothed out


# ---------- composite with a gallery picture as the background
def test_composite_with_gallery_image_background(vl, project, monkeypatch, tmp_path):
    vid = vl.create_blank_video(project.pid, title="Comp", width=48, height=32, total_frames=4, color="#ff0000")["id"]
    mlid = vl._add_layer(project.pid, vid, "mask", "M")["id"]
    vl.save_layer_frame(project.pid, vid, mlid, 0, _png((255, 255, 255, 255), box=(0, 0, 24, 32)))
    bg = str(tmp_path / "bg.png"); cv2.imwrite(bg, np.full((64, 96, 3), (255, 0, 0), np.uint8))   # blue
    monkeypatch.setattr(vl, "resolve_media_path", lambda u: bg)
    r = vl._job_composite("t", "composite", project.pid, vid, {"fg": "plate", "mask": mlid, "bg": "/vfx/project_file/p/storyboard/bg.png",
                                                               "erode": 0, "feather": 0})
    out = cv2.imread(vl.frame_path(project.pid, vid, r["layer"]["id"], 2))
    assert out[16, 40, 0] > 200 and out[16, 40, 2] < 60     # right half: the gallery background


# ---------- a prompt that vanished from the ComfyUI queue is an error, not "pending" forever
def test_run_status_lost_prompt(monkeypatch):
    pr = load("process_runner")

    class R:
        def __init__(self, body): self.body = body
        def read(self): return __import__("json").dumps(self.body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(url, timeout=10):
        return R({"queue_running": [], "queue_pending": []} if url.endswith("/queue") else {})
    monkeypatch.setattr(pr.urllib.request, "urlopen", fake)
    assert pr.get_run_status("abc")["status"] == "error"

    def fake2(url, timeout=10):
        return R({"queue_running": [[0, "abc", {}, {}, []]], "queue_pending": []} if url.endswith("/queue") else {})
    monkeypatch.setattr(pr.urllib.request, "urlopen", fake2)
    assert pr.get_run_status("abc")["status"] == "pending"


# ---------- layer transform (Move tool): saved in the manifest, applied when the layer is used
def test_layer_xform_saved_and_applied(vl, project):
    vid = vl.create_blank_video(project.pid, title="Xf", width=40, height=20, total_frames=3)["id"]
    lid = vl._add_layer(project.pid, vid, "mask", "M")["id"]
    vl.save_layer_frame(project.pid, vid, lid, 0, _png((255, 255, 255, 255), size=(40, 20), box=(0, 0, 10, 20)))
    st = vl.update_layers_meta(project.pid, vid, [{"id": lid, "name": "M", "xform": {"x": 0.5, "y": 0, "s": 1, "r": 0}}])
    lay = next(l for l in vl.load_manifest(project.pid, vid)["layers"] if l["id"] == lid)
    assert lay["xform"]["x"] == 0.5
    a, _ = vl._layer_alpha(project.pid, vid, lay, 1, 20, 40)
    assert a[10, 25] > 0.9 and a[10, 5] < 0.1           # the white strip moved half a frame to the right
    vl.update_layers_meta(project.pid, vid, [{"id": lid, "name": "M", "xform": {"x": 0, "y": 0, "s": 1, "r": 0}}])
    assert "xform" not in next(l for l in vl.load_manifest(project.pid, vid)["layers"] if l["id"] == lid)


def test_component_morph_keeps_strokes_whole(vl):
    A = np.zeros((120, 200, 4), np.uint8); B = np.zeros((120, 200, 4), np.uint8)
    cv2.line(A, (20, 30), (70, 40), (255, 255, 255, 255), 9)
    cv2.line(B, (130, 90), (180, 60), (255, 255, 255, 255), 9)
    mid = vl._component_morph(A, B, 0.5)
    area = lambda im: int((im[..., 3] > 127).sum())
    assert area(mid) > 0.6 * min(area(A), area(B))      # the SDF blend of far-apart strokes shrank to a dot
    ys, xs = np.nonzero(mid[..., 3] > 127)
    assert 80 < xs.mean() < 120                            # half-way between the two strokes


def test_thick_line_moves_without_flipping(vl):
    A = np.zeros((400, 700, 4), np.uint8); B = np.zeros((400, 700, 4), np.uint8)
    cv2.line(A, (80, 300), (300, 220), (0, 200, 255, 255), 30)
    cv2.line(B, (380, 150), (620, 110), (0, 200, 255, 255), 30)
    area = lambda im: int((im[..., 3] > 100).sum())
    for t in (0.25, 0.5, 0.75):
        mid = vl._component_morph(A, B, t)
        assert area(mid) > 0.85 * min(area(A), area(B))   # it used to turn over and shrink half-way
        ys, xs = np.nonzero(mid[..., 3] > 100)
        assert xs.max() - xs.min() > 180                    # still a long line, not a short blob


def test_outline_loop_morph_stays_closed(vl):
    A = np.zeros((300, 300, 4), np.uint8); B = np.zeros((300, 300, 4), np.uint8)
    cv2.ellipse(A, (120, 150), (60, 100), 30, 0, 360, (255, 255, 255, 255), 6)
    cv2.ellipse(B, (180, 150), (70, 90), 10, 0, 360, (255, 255, 255, 255), 6)
    mid = vl._component_morph(A, B, 0.5)
    n, _ = cv2.connectedComponents((mid[..., 3] > 100).astype(np.uint8))
    assert n - 1 == 1                                          # one clean loop, not dashes


def test_duplicate_video_copies_layers(vl, project):
    vid = vl.create_blank_video(project.pid, title="Dup", width=48, height=32, total_frames=4)["id"]
    lid = vl._add_layer(project.pid, vid, "mask", "M")["id"]
    vl.save_layer_frame(project.pid, vid, lid, 1, _png((255, 255, 255, 255), box=(0, 0, 8, 8)))
    st = vl.duplicate_video(project.pid, vid, vid + "_copy")
    assert any(l["id"] == lid and l.get("keys") == [1] for l in st["layers"])
    with pytest.raises(vl.ProcessError):
        vl.duplicate_video(project.pid, vid, vid + "_copy")


def test_layer_xform_non_uniform_scale(vl):
    img = np.zeros((40, 80), np.uint8); img[15:25, 35:45] = 255          # a 10x10 square in the middle
    out = vl._apply_xform(img, {"x": 0, "y": 0, "s": 1, "r": 0, "sx": 3, "sy": 1})
    ys, xs = np.nonzero(out > 127)
    assert 27 <= xs.max() - xs.min() + 1 <= 33 and 9 <= ys.max() - ys.min() + 1 <= 11   # wider only
    out = vl._apply_xform(img, {"x": 0.25, "y": 0, "s": 1, "r": 90})
    ys, xs = np.nonzero(out > 127)
    assert 55 < xs.mean() < 65                                             # moved a quarter of the width


def test_place_in_frame_keeps_original_size(vl):
    tall = np.zeros((100, 40, 4), np.uint8); tall[..., 3] = 255
    out = vl._place_in_frame(tall, (40, 100), (200, 120))
    ys, xs = np.nonzero(out[..., 3])
    assert out.shape[:2] == (120, 200) and xs.max() - xs.min() + 1 == 40 and ys.max() - ys.min() + 1 == 100
    stretched = np.zeros((120, 200, 4), np.uint8); stretched[..., 3] = 255   # a vertical layer stored at canvas size
    out = vl._place_in_frame(stretched, (40, 100), (200, 120))
    ys, xs = np.nonzero(out[..., 3])
    assert xs.max() - xs.min() + 1 == 40


def test_import_media_as_layer_keeps_size(tmp_path, monkeypatch):
    import cv2, numpy as np
    from vfx_nodes import video_layers_service as vls
    monkeypatch.setattr(vls, "PROJECTS_ROOT", str(tmp_path), raising=False)
    img = np.full((200, 100, 3), 255, np.uint8)            # a vertical picture
    p = tmp_path / "pic.png"; cv2.imwrite(str(p), img)
    monkeypatch.setattr(vls, "resolve_media_path", lambda u: str(p))
    monkeypatch.setattr(vls, "ensure_plate_registered", lambda *a, **k: None)
    monkeypatch.setattr(vls.pps, "get_info", lambda *a, **k: {})
    store = {"layers": []}
    monkeypatch.setattr(vls, "load_manifest", lambda pid, vid: store)
    monkeypatch.setattr(vls, "save_manifest", lambda pid, vid, m: store.update(m))
    monkeypatch.setattr(vls, "_add_layer", lambda pid, vid, t, n, extra=None, **k: (store["layers"].append({"id": "vl_x", "type": t, "name": n}) or store["layers"][-1]))
    out = tmp_path / "L"; monkeypatch.setattr(vls, "_layer_dir", lambda pid, vid, lid: str(out))
    r = vls.import_media_as_layer("p", "v", "/vfx/project_file/p/pic.png", "", [400, 200], 0, 3)
    assert r["frames"] == 3
    fr = cv2.imread(str(out / "frame_0002.png"), cv2.IMREAD_UNCHANGED)
    assert fr.shape == (200, 400, 4)
    assert fr[100, 200, 3] == 255 and fr[100, 10, 3] == 0   # centred at original size, transparent around


def test_publish_undistort_writes_layer_lens_and_stmaps(tmp_path, monkeypatch):
    import json as _json, cv2
    from vfx_nodes import video_layers_service as vls
    monkeypatch.setattr(vls, "video_root", lambda pid, vid: str(tmp_path / vid))
    monkeypatch.setattr(vls.pps, "get_info", lambda *a, **k: {"width": 64, "height": 48, "total_frames": 5})
    store = {"layers": []}
    monkeypatch.setattr(vls, "load_manifest", lambda pid, vid: store)
    monkeypatch.setattr(vls, "save_manifest", lambda pid, vid, m: store.update(m))
    lens = vls._publish_undistort("p", "v", -0.12, 0.03)
    assert lens["k1"] == -0.12 and lens["width"] == 64
    lay = store["layers"][0]
    assert lay["id"] == "plate_undist" and lay["frames"] == 5 and "k1 -0.120" in lay["name"]
    st = cv2.imread(str(tmp_path / "v" / "lens" / "stmap_undistort.png"), cv2.IMREAD_UNCHANGED)
    assert st.shape == (48, 64, 3) and st.dtype.name == "uint16"
    # the centre pixel maps (almost) to itself
    assert abs(st[24, 32, 2] / 65535 * 64 - 32.5) < 1.0
    assert _json.load(open(tmp_path / "v" / "lens" / "lens.json"))["stmap_convention"].startswith("Nuke")
    vls._publish_undistort("p", "v", 0.05, 0.0)   # again: the same layer is updated, not duplicated
    assert len(store["layers"]) == 1


def test_redistort_bends_a_layer_back(tmp_path, monkeypatch):
    import cv2, numpy as np
    from vfx_nodes import video_layers_service as vls
    monkeypatch.setattr(vls.pps, "get_info", lambda *a, **k: {"width": 64, "height": 48, "total_frames": 2})
    src = tmp_path / "src"; src.mkdir()
    for i in range(2):
        cv2.imwrite(str(src / f"frame_{i:04d}.png"), np.full((48, 64, 3), 200, np.uint8))
    store = {"layers": [{"id": "vl_a", "type": "rgb", "name": "Shot"},
                        {"id": "plate_undist", "type": "rgb", "name": "Undistort", "lens": {"k1": -0.2, "k2": 0.0}}]}
    monkeypatch.setattr(vls, "load_manifest", lambda pid, vid: store)
    monkeypatch.setattr(vls, "save_manifest", lambda pid, vid, m: store.update(m))
    out = tmp_path / "out"
    def add_layer(pid, vid, t, n, extra=None, **k):
        store["layers"].append({"id": "vl_new", "type": t, "name": n}); return store["layers"][-1]
    monkeypatch.setattr(vls, "_add_layer", add_layer)
    monkeypatch.setattr(vls, "_layer_dir", lambda pid, vid, lid: str(out if lid == "vl_new" else src))
    monkeypatch.setattr(vls, "frame_path", lambda pid, vid, lid, i: str(src / f"frame_{i:04d}.png"))
    monkeypatch.setattr(vls, "_cancelled", lambda j: False)
    monkeypatch.setattr(vls, "_job_update", lambda *a, **k: None)
    out.mkdir()
    r = vls._job_redistort("j", "redistort", "p", "v", {"layer_id": "vl_a"})
    assert r["frames"] == 2 and r["layer"]["name"] == "Shot · lens"
    im = cv2.imread(str(out / "frame_0000.png"))
    assert im.shape == (48, 64, 3) and im[24, 32, 0] == 200


def test_shot_planner_reads_a_plain_text_task():
    import importlib, sys, os
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core_agent"))
    sp = importlib.import_module("shot_planner")
    msg = ("давай сделаем ему трекинг заменим фон на городскую среду типа гето. добавим на фоны пару скелетов пусть это будут "
           "афроамериканцы танцевать капоэйру. и пусть будет три варианта финального шота с заменой фона с заменой персонажа "
           "и с заменой фона и персонажа.")
    assert sp.looks_like_shot_task(msg)
    p = sp.build_plan(msg)
    assert p["track"] and p["variants"] == ["bg", "char", "both"]
    assert "ghetto" in p["background"] and p["dancers"]["count"] == 2 and "capoeira" in p["dancers"]["action"]
    assert not sp.looks_like_shot_task("сделай глубину")
    assert sp.build_plan("замени персонажа на рыцаря в доспехах и сделай трекинг")["character"] == "рыцаря в доспехах"
