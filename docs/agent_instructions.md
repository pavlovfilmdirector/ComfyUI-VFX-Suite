# ComfyUI-VFX-Suite: AI Agent & MCP Operator Manual

This document provides system instructions for autonomous AI agents (ComfyUI Agent, Claude Desktop, Cursor, and custom MCP clients) operating the `ComfyUI-VFX-Suite` pipeline.

---

## 1. Pipeline Overview

The suite bridges live-action matchmoving (GLOMAP SfM), interactive 3D layout blocking, and generative set extension (`LTX-2.5-22b` with IC-LoRAs).

```
[Raw Video / Sequence] 
       │
       ├──► [CameraTrackerSfM] ──► CAMERA_DATA + TRACK_POINTS + Undistorted Plate
       │         ▲                        │
       │   [Manual Mask]                  ▼
       │                          [LayoutEditor3D] ◄── [Agent layout_json]
       │                                  │
       │                                  ├──► Clay / Semantic Color ──► [LTX-2.5 Layout-To-Render]
       │                                  └──► Layout Mask                      │
       │                                                                        ▼
       ├──► [LTX-2.5 Alpha-Gen] ──► Actor RGBA ──────────────────────► [VFXCompositor]
                                                                                │
                                                                                ▼
                                                                     [4K Refine & Details]
```

---

## 2. Node Specifications & Tool Invocations

### 2.1 `CameraTrackerSfM`
* **Inputs**:
  * `images` (optional IMAGE tensor, e.g. `undistorted_plate` from Plate Prep).
  * `tracking_mask` (optional MASK tensor, pre-computed dynamic movers mask from Plate Prep).
  * `manual_mask` (optional MASK from MaskEditor).
  * `input_path` (optional direct path to video or image sequence).
* **Parameters**:
  * `point_cloud_density`: `"Draft (2048 pts)"`, `"Standard (4096 pts)"`, `"Dense (8192 pts)"`, `"Ultra (16384 pts)"`.
  * `focal_length_mode`: `"Auto Estimate (Refined)"`, `"Fixed / Known Focal"`.
  * `focal_mm`: Focal length in 35mm equivalent (FLOAT, default 28.0).
  * `camera_height_m`: Approximate camera height above ground for 1:1 real-world metric scaling (FLOAT, default 1.55m).
  * `smooth_camera`: Trajectory jitter smoothing (BOOLEAN, default False to retain 100% physical micro-shakes).
* **Outputs**:
  * `undistorted_images`: IMAGE tensor.
  * `camera_data`: Solved trajectory dictionary (`matrices`, `focal_mm`, `positions`).
  * `track_points`: 3D sparse point cloud list `[[x, y, z], ...]`.
  * `tracking_mask`: Dynamic mask tensor.
  * `summary_report`: Text status report.

### 2.2 `LayoutEditor3D`
* **Inputs**:
  * `camera_data` (from tracker or external file).
  * `track_points` (sparse point cloud).
  * `layout_json`: String manifest describing 3D proxy objects, colors, and animated keyframes.
  * `render_mode`: `"Clay (Lambertian Studio)"`, `"Semantic Color (Color Blocking)"`, `"Flat Color Segmentation"`, `"Depth Map"`, `"Surface Normals"`, `"Layout Mask"`.
* **Outputs**:
  * `layout_render`: IMAGE (Clay or Semantic Color pass for LTX Layout-To-Render).
  * `depth_map`: Normalized 16-bit depth map.
  * `layout_mask`: Binary silhouette mask.
  * `camera_data`: Evaluated camera trajectory.

### 2.3 `VFXCompositor`
* **Inputs**:
  * `background_image`: Generated background from LTX.
  * `foreground_image`: Actor plate or alpha cutout.
  * `foreground_mask`: Matte from Alpha-Gen.
  * `light_wrap_intensity`: 0.0 – 1.0 (ambient color bleeding around actor edges).
  * `harmonize_reinhard`: Boolean (matches color tone and exposure).
* **Output**:
  * `composite`: Final seamless VFX composite.

---

## 3. Autonomous Layout Manipulation (`layout_json` Schema)

An AI Agent can construct or alter 3D sets by injecting JSON into `LayoutEditor3D.layout_json`:

```json
[
  {
    "id": 1001,
    "type": "cube",
    "position": [-2.5, 5.0, 0.0],
    "rotation": [0.0, 0.0, 0.785],
    "scale": [2.0, 2.0, 4.0],
    "keyframes": [
      {
        "frame": 0,
        "position": [-2.5, 5.0, 0.0],
        "rotation": [0.0, 0.0, 0.0],
        "scale": [2.0, 2.0, 4.0],
        "interp": "bezier"
      },
      {
        "frame": 60,
        "position": [-2.5, 8.0, 0.0],
        "rotation": [0.0, 0.0, 1.57],
        "scale": [2.0, 2.0, 4.0],
        "interp": "bezier"
      }
    ]
  },
  {
    "id": 1002,
    "type": "cylinder",
    "position": [3.0, 6.0, 0.0],
    "rotation": [0.0, 0.0, 0.0],
    "scale": [1.5, 1.5, 6.0]
  }
]
```

### Interpolation Modes:
* `"bezier"`: Cubic Hermite / Smoothstep curve (smooth easing).
* `"linear"`: Constant velocity between keys.
* `"step"`: Instant jump on keyframe (stepped/cut animation).

---

## 4. MCP Agent Instructions (Best Practices)

1. **Camera Scale & Coordinate System**:
   * All coordinates are metric (1 unit = 1 meter).
   * Floor is aligned at $Z = 0$.
   * Camera trajectory is $+Z$ up, looking along $-Y$ or $-Z$ depending on convention.
2. **Generative Prompt Engineering for LTX**:
   * When passing the Clay pass into `LTX-2.5-22b-ic-lora-layout-to-render`, always describe the geometry in the prompt:
     * *Example*: `"Cinematic sci-fi ruins, towering weathered stone pillars and futuristic monoliths where the gray blocks stand, realistic 35mm film grain, atmospheric dust, volumetric sunlight"`.
3. **Refine & Upscale Pass**:
   * Run the initial generation at 720p for fast turnaround (15–25 sec), then feed the composite through `LTX-2.5-22b-ic-lora-refine-details` (8 distilled steps) at 1080p or 4K.
