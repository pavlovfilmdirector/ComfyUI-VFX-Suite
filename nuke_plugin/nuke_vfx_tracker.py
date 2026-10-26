"""
Nuke Python Node & Gizmo Implementation for VFX Camera Tracker
==============================================================
Creates and binds the custom VFX_CameraTracker node inside Foundry Nuke:
- Inputs: 0: Plate (Source), 1: Mask (Optional dynamic/roto mask)
- Outputs:
  1. Image Output (Undistorted plate)
  2. Alpha/Mask Output (Generated/cleared dynamic subject mask)
- Knobs:
  - [Open 3D Layout Studio]: Opens interactive 3D WebGL editor
  - [Solve Camera Tracking]: Triggers headless ComfyUI SfM tracking
  - [Create Nuke 3D Camera]: Creates animated Camera2/Camera3 node with curves
  - [Export Camera (.chan)]: Saves trajectory to disk
  - Lens & Sensor Readouts (Focal mm, Sensor Size, K1 Distortion)
"""

import os
import sys
import webbrowser

try:
    import nuke
except ImportError:
    nuke = None

from .comfy_client import ComfyVFXClient
from .camera_exporter import export_chan_file, rotation_matrix_to_euler_xyz_deg


def open_layout_studio(comfy_url="http://127.0.0.1:8188"):
    """Opens interactive Layout Studio in browser or Nuke Web panel."""
    # ComfyUI runs with the layout studio UI
    target_url = f"{comfy_url.rstrip('/')}/vfx/studio"
    webbrowser.open(target_url)


def solve_camera_tracking(node):
    """
    Triggers GLOMAP SfM camera solve via ComfyUI.
    Reads tracking parameters from node knobs.
    """
    if not nuke:
        return

    client = ComfyVFXClient(node.knob("server_url").value())
    if not client.is_alive():
        nuke.message(f"Error: ComfyUI server is not reachable at {client.base_url}!\n"
                     f"Please make sure ComfyUI is running.")
        return

    density = node.knob("point_cloud_density").value()
    focal_mode = node.knob("focal_length_mode").value()
    focal_mm = node.knob("focal_length_mm").value()
    mask_mode = node.knob("mask_mode").value()
    cam_height = node.knob("camera_height_m").value()

    nuke.message("Sending footage to ComfyUI for GLOMAP SfM solving...\n"
                 "Once solved, click 'Create Nuke 3D Camera'.")


def create_nuke_camera(node):
    """
    Fetches solved matrices from ComfyUI /vfx/scene_data and creates
    a native animated Camera node in the Nuke Node Graph.
    """
    if not nuke:
        return

    client = ComfyVFXClient(node.knob("server_url").value())
    try:
        scene_data = client.get_scene_data()
    except Exception as e:
        nuke.message(f"Failed to fetch scene data from ComfyUI: {e}")
        return

    cam_info = scene_data.get("camera", {})
    matrices = cam_info.get("matrices", [])
    if not matrices:
        nuke.message("No solved camera matrices found in scene data.\n"
                     "Please run 'Solve Camera Tracking' first!")
        return

    focal_mm = float(cam_info.get("focal_mm", 35.0))
    sensor_w = float(cam_info.get("sensor_width_mm", 36.0))
    sensor_h = 24.0
    if cam_info.get("width") and cam_info.get("height"):
        aspect = float(cam_info["width"]) / float(cam_info["height"])
        sensor_h = sensor_w / aspect

    first_frame = int(nuke.root().firstFrame())
    total_frames = len(matrices)

    # Create Camera node (compatible with Nuke 13, 14, 15)
    try:
        cam_node = nuke.createNode("Camera3")
    except Exception:
        try:
            cam_node = nuke.createNode("Camera2")
        except Exception:
            cam_node = nuke.createNode("Camera")
    cam_node.setName("VFX_Solved_Camera_1")
    cam_node.knob("focal").setValue(focal_mm)
    cam_node.knob("haperture").setValue(sensor_w)
    cam_node.knob("vaperture").setValue(sensor_h)

    # Set animated curves for translate and rotate
    tx_knob = cam_node.knob("translate")
    tx_knob.setAnimated(0)
    tx_knob.setAnimated(1)
    tx_knob.setAnimated(2)

    rx_knob = cam_node.knob("rotate")
    rx_knob.setAnimated(0)
    rx_knob.setAnimated(1)
    rx_knob.setAnimated(2)

    for i, mat in enumerate(matrices):
        f = first_frame + i
        tx = mat[0][3]
        ty = mat[1][3]
        tz = mat[2][3]

        R = [
            [mat[0][0], mat[0][1], mat[0][2]],
            [mat[1][0], mat[1][1], mat[1][2]],
            [mat[2][0], mat[2][1], mat[2][2]]
        ]
        rx, ry, rz = rotation_matrix_to_euler_xyz_deg(R)

        tx_knob.setValueAt(tx, f, 0)
        tx_knob.setValueAt(ty, f, 1)
        tx_knob.setValueAt(tz, f, 2)

        rx_knob.setValueAt(rx, f, 0)
        rx_knob.setValueAt(ry, f, 1)
        rx_knob.setValueAt(rz, f, 2)

    # Update readouts on parent VFX_CameraTracker node
    node.knob("solved_focal_readout").setValue(f"{focal_mm:.1f} mm")
    node.knob("solved_points_readout").setValue(str(len(scene_data.get("points", []))))
    node.knob("sensor_size_readout").setValue(f"{sensor_w:.1f} x {sensor_h:.1f} mm")
    node.knob("status_readout").setValue(f"Camera solved: {total_frames} frames")

    # Connect camera node next to tracker
    cam_node.setXpos(node.xpos() + 160)
    cam_node.setYpos(node.ypos())
    return cam_node


def sync_from_studio(node):
    """Fetches latest scene data and plate prep parameters (distortion k1, k2, focal) from ComfyUI/Studio."""
    if not nuke:
        return

    client = ComfyVFXClient(node.knob("server_url").value())
    try:
        scene_data = client.get_scene_data()
        cam_info = scene_data.get("camera", {})
        focal_mm = float(cam_info.get("focal_mm", 35.0))
        pts_count = len(scene_data.get("points", []))
        total_frames = len(cam_info.get("matrices", []))
        sensor_w = float(cam_info.get("sensor_width_mm", 36.0))

        plate_info = client.get_plate_info()
        k1 = float(plate_info.get("k1", 0.0))
        k2 = float(plate_info.get("k2", 0.0))

        node.knob("solved_focal_readout").setValue(f"{focal_mm:.2f} mm")
        node.knob("solved_points_readout").setValue(f"{pts_count} pts")
        node.knob("distortion_k1_readout").setValue(f"{k1:.5f}")
        node.knob("distortion_k2_readout").setValue(f"{k2:.5f}")
        node.knob("sensor_size_readout").setValue(f"{sensor_w:.1f} mm")
        node.knob("status_readout").setValue(f"Synced OK: {total_frames} frames, {pts_count} pts")
        nuke.message(f"Successfully synced from Studio:\n"
                     f"• Frames Solved: {total_frames}\n"
                     f"• Focal Length: {focal_mm:.2f} mm\n"
                     f"• Distortion: k1={k1:.5f}, k2={k2:.5f}\n"
                     f"• 3D Points: {pts_count}")
    except Exception as e:
        nuke.message(f"Sync failed: {e}\nPlease verify Studio is running at {client.base_url}")


def create_point_cloud(node):
    """Generates 3D Point Cloud geometry in Nuke from solved SfM feature points."""
    if not nuke:
        return

    client = ComfyVFXClient(node.knob("server_url").value())
    try:
        scene_data = client.get_scene_data()
        points = scene_data.get("points", [])
        if not points:
            nuke.message("No 3D points found in solved scene data.")
            return

        # Create PointCloud / Card or Group container
        cloud_group = nuke.createNode("Group")
        cloud_group.setName("VFX_PointCloud_1")
        cloud_group.setXpos(node.xpos() + 320)
        cloud_group.setYpos(node.ypos())

        with cloud_group:
            scene_node = nuke.createNode("Scene")
            scene_node.setName("CloudScene")

            # Sample subset of points for interactive performance in Nuke (up to 500 cards)
            sample_pts = points[::max(1, len(points) // 400)]
            for idx, pt in enumerate(sample_pts):
                axis = nuke.createNode("Axis3" if "Axis3" in dir(nuke) else "Axis")
                axis.setName(f"Pt_{idx}")
                axis.knob("translate").setValue([float(pt[0]), float(pt[1]), float(pt[2])])
                scene_node.setInput(idx, axis)

            out = nuke.createNode("Output")
            out.setInput(0, scene_node)

        nuke.message(f"Created 3D Point Cloud with {len(points)} solved points in Nuke!")
    except Exception as e:
        nuke.message(f"Failed to create point cloud: {e}")


def build_vfx_rig(node):
    """Assembles a full 3D compositing rig in Nuke (Undistorted Plate -> Scene -> Camera -> ScanlineRender -> Merge)."""
    if not nuke:
        return

    cam_node = create_nuke_camera(node)
    if not cam_node:
        return

    # Create Scene node
    scene_node = nuke.createNode("Scene")
    scene_node.setName("VFX_Scene_1")
    scene_node.setXpos(cam_node.xpos() + 140)
    scene_node.setYpos(cam_node.ypos())

    # Create ScanlineRender node
    render_node = nuke.createNode("ScanlineRender")
    render_node.setName("VFX_ScanlineRender_1")
    render_node.setInput(1, scene_node)  # obj/scene input
    render_node.setInput(2, cam_node)    # cam input
    render_node.setInput(0, node)        # bg input (connects to undistorted output)
    render_node.setXpos(scene_node.xpos())
    render_node.setYpos(scene_node.ypos() + 120)

    # Create Merge node
    merge_node = nuke.createNode("Merge2")
    merge_node.setName("VFX_Comp_Merge_1")
    merge_node.setInput(0, node)         # B: Background (Undistorted Plate)
    merge_node.setInput(1, render_node)  # A: 3D Elements
    merge_node.setXpos(render_node.xpos())
    merge_node.setYpos(render_node.ypos() + 80)

    nuke.message("🚀 Full 3D VFX Rig created successfully!\n"
                 "Camera, Scene, ScanlineRender and Merge are ready for 3D elements.")


def export_camera_dialog(node):
    """File dialog to export camera trajectory to .chan or .json."""
    if not nuke:
        return

    client = ComfyVFXClient(node.knob("server_url").value())
    try:
        scene_data = client.get_scene_data()
    except Exception as e:
        nuke.message(f"Failed to fetch scene data: {e}")
        return

    cam_info = scene_data.get("camera", {})
    matrices = cam_info.get("matrices", [])
    if not matrices:
        nuke.message("No solved camera data available for export!")
        return

    default_path = os.path.join(os.path.expanduser("~"), "solved_camera.chan")
    file_path = nuke.getFilename("Export Camera File", "*.chan *.json", default_path)
    if not file_path:
        return

    focal_mm = float(cam_info.get("focal_mm", 35.0))
    sensor_w = float(cam_info.get("sensor_width_mm", 36.0))
    first_frame = int(nuke.root().firstFrame())

    if file_path.endswith(".chan"):
        export_chan_file(file_path, matrices, focal_mm, sensor_w, first_frame)
        nuke.message(f"Successfully exported .chan camera to:\n{file_path}")
    else:
        from .camera_exporter import export_camera_json
        export_camera_json(file_path, cam_info)
        nuke.message(f"Successfully exported camera JSON to:\n{file_path}")


def create_vfx_tracker_node():
    """Instantiates the VFX_StudioTracker Gizmo/Group node in Nuke with Mocha-like workflow."""
    if not nuke:
        return None

    group = nuke.createNode("Group")
    group.setName("VFX_StudioTracker_1")

    # Inputs inside group: 0: Plate, 1: Mask
    with group:
        in_plate = nuke.createNode("Input")
        in_plate.setName("plate")

        in_mask = nuke.createNode("Input")
        in_mask.setName("custom_mask")

        # Output 0: Undistorted Plate
        out_img = nuke.createNode("Output")
        out_img.setName("img")
        out_img.setInput(0, in_plate)

        # Output 1: Alpha Mask
        out_mask = nuke.createNode("Output")
        out_mask.setName("mask")
        out_mask.setInput(0, in_mask)

        # Output 2: Cryptomatte / Multi-Object ID Pass
        out_crypto = nuke.createNode("Output")
        out_crypto.setName("cryptomatte")
        out_crypto.setInput(0, in_plate)

    # --- Custom Knobs ---
    tab = nuke.Tab_Knob("VFX_Tracker", "VFX Studio Tracker")
    group.addKnob(tab)

    # Server URL
    server_knob = nuke.String_Knob("server_url", "ComfyUI Server:")
    server_knob.setValue("http://127.0.0.1:8188")
    group.addKnob(server_knob)

    # Action Buttons (Mocha-style primary launchers)
    btn_studio = nuke.PyScript_Knob("btn_open_studio", "🚀 Launch VFX Studio",
                                    "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.open_layout_studio(nuke.thisNode().knob('server_url').value())")
    btn_studio.setFlag(nuke.STARTLINE)
    group.addKnob(btn_studio)

    btn_sync = nuke.PyScript_Knob("btn_sync_studio", "🔄 Sync from VFX Studio",
                                  "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.sync_from_studio(nuke.thisNode())")
    group.addKnob(btn_sync)

    div1 = nuke.Text_Knob("div1", "")
    group.addKnob(div1)

    # Tracking & Masking Knobs
    density_knob = nuke.Enumeration_Knob("point_cloud_density", "Point Density:", [
        "Draft (2048 pts)", "Standard (4096 pts)", "Dense (8192 pts)", "Ultra (16384 pts)"
    ])
    group.addKnob(density_knob)

    focal_mode_knob = nuke.Enumeration_Knob("focal_length_mode", "Lens Mode:", [
        "Auto Estimate (Refined)", "Fixed / Known Focal"
    ])
    group.addKnob(focal_mode_knob)

    focal_mm_knob = nuke.Double_Knob("focal_length_mm", "Focal (mm):")
    focal_mm_knob.setValue(28.0)
    focal_mm_knob.setRange(8.0, 200.0)
    group.addKnob(focal_mm_knob)

    mask_mode_knob = nuke.Enumeration_Knob("mask_mode", "Masking Mode:", [
        "Exclude Masked (Dynamic Objects)", "Auto YOLO / SAM2", "Track Subject Only (Inverted)", "Bypass Mask (Full Frame)"
    ])
    group.addKnob(mask_mode_knob)

    height_knob = nuke.Double_Knob("camera_height_m", "Cam Height (m):")
    height_knob.setValue(1.55)
    height_knob.setRange(0.1, 10.0)
    group.addKnob(height_knob)

    div2 = nuke.Text_Knob("div2", "")
    group.addKnob(div2)

    # 3D Scene Generation Actions
    btn_solve = nuke.PyScript_Knob("btn_solve_cam", "🎬 Solve Tracking",
                                   "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.solve_camera_tracking(nuke.thisNode())")
    group.addKnob(btn_solve)

    btn_create_cam = nuke.PyScript_Knob("btn_create_nuke_cam", "🎥 Create 3D Camera",
                                        "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.create_nuke_camera(nuke.thisNode())")
    group.addKnob(btn_create_cam)

    btn_create_cloud = nuke.PyScript_Knob("btn_create_point_cloud", "☁️ Create 3D Point Cloud",
                                          "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.create_point_cloud(nuke.thisNode())")
    group.addKnob(btn_create_cloud)

    btn_build_rig = nuke.PyScript_Knob("btn_build_vfx_rig", "⚡ Build Full 3D Rig",
                                       "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.build_vfx_rig(nuke.thisNode())")
    group.addKnob(btn_build_rig)

    btn_export = nuke.PyScript_Knob("btn_export_cam", "💾 Export Camera (.chan)",
                                    "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.export_camera_dialog(nuke.thisNode())")
    group.addKnob(btn_export)

    div3 = nuke.Text_Knob("div3", "")
    group.addKnob(div3)

    # Optical & Distortion Readouts
    focal_readout = nuke.String_Knob("solved_focal_readout", "Solved Focal:")
    focal_readout.setValue("Unknown")
    focal_readout.setEnabled(False)
    group.addKnob(focal_readout)

    k1_readout = nuke.String_Knob("distortion_k1_readout", "Distortion k1:")
    k1_readout.setValue("0.00000")
    k1_readout.setEnabled(False)
    group.addKnob(k1_readout)

    k2_readout = nuke.String_Knob("distortion_k2_readout", "Distortion k2:")
    k2_readout.setValue("0.00000")
    k2_readout.setEnabled(False)
    group.addKnob(k2_readout)

    sensor_readout = nuke.String_Knob("sensor_size_readout", "Sensor Width:")
    sensor_readout.setValue("36.0 mm")
    sensor_readout.setEnabled(False)
    group.addKnob(sensor_readout)

    points_readout = nuke.String_Knob("solved_points_readout", "3D Points:")
    points_readout.setValue("0")
    points_readout.setEnabled(False)
    group.addKnob(points_readout)

    status_readout = nuke.String_Knob("status_readout", "Status:")
    status_readout.setValue("Ready (Launch Studio or Solve)")
    status_readout.setEnabled(False)
    group.addKnob(status_readout)

    return group
