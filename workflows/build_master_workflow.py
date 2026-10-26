"""
Generates the Master Production Workflow JSON for ComfyUI-VFX-Suite
Modular VFX pipeline:
1. Plate Prep (Ingestion, Lens Undistortion, YOLO Dynamic Masking + Manual Mask, Rough Actor Matte)
2. Camera Tracker (GLOMAP Global SfM, Z=0 Ground Alignment, Point Cloud)
3. 3D Layout Editor (Three.js WebGL, Actor Projection Billboard, Keyframing, Clay/Color Render)
4. LTX-2.5 Layout-To-Render (Diffusion UNET + Distilled LoRA 450, 8 steps)
5. VFX Harmonizer & Compositor (Reinhard color matching, Light Wrap, Edge Feathering)
"""

import json
import os

workflow = {
    "id": "comfyui-vfx-suite-master-v2",
    "revision": 3,
    "last_node_id": 100,
    "last_link_id": 200,
    "nodes": [
        # --- Step 1: Plate Prep & Masking ---
        {
            "id": 10,
            "type": "MarkdownNote",
            "pos": [60, 80],
            "size": [360, 240],
            "flags": {},
            "order": 1,
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "title": "📌 Step 1: Plate Prep & Masking",
            "properties": {},
            "widgets_values": [
                "### 🎞️ Plate Prep Studio\n\n"
                "Все манипуляции выполняются внутри студии на ноде:\n"
                "1. **Загрузка шота**: Видео или сиквенс кадров загружаются через интерфейс Studio (Шаг 1).\n"
                "2. **Дисторсия линзы**: Интерактивная калибровка $k_1, k_2$ (ручная или ✨ Авто).\n"
                "3. **Маскирование**: AI динамические маски (YOLO), загрузка масок пользователя или ручная кисть с автосохранением.\n"
                "4. **Выходные тензоры**: `undistorted_plate`, `tracking_mask`, `rough_actor_matte` передаются далее по пайплайну."
            ],
            "color": "#1b2838",
            "bgcolor": "#101822"
        },
        {
            "id": 12,
            "type": "VFXPlatePrep",
            "pos": [60, 350],
            "size": [360, 330],
            "flags": {},
            "order": 2,
            "mode": 0,
            "inputs": [],
            "outputs": [
                {"name": "undistorted_plate", "type": "IMAGE", "links": [2, 10, 14]},
                {"name": "tracking_mask", "type": "MASK", "links": [3]},
                {"name": "rough_actor_matte", "type": "MASK", "links": [4, 7]},
                {"name": "prep_report", "type": "STRING", "links": []}
            ],
            "title": "🎞️ VFX Plate Prep & Masking",
            "properties": {"Node name for S&R": "VFXPlatePrep"},
            "widgets_values": [],
            "color": "#1a365d",
            "bgcolor": "#15243b"
        },

        # --- Step 2: GLOMAP Camera Tracker & Point Cloud ---
        {
            "id": 15,
            "type": "MarkdownNote",
            "pos": [870, 80],
            "size": [380, 200],
            "flags": {},
            "order": 4,
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "title": "📌 Step 2: GLOMAP SfM Tracking",
            "properties": {},
            "widgets_values": [
                "### 🎥 GLOMAP Global SfM\n\n"
                "1. Uses clean `undistorted_plate` and `tracking_mask` from Step 1 (bypasses redundant YOLO inference).\n"
                "2. Solves camera rotation and position with metric scale.\n"
                "3. Aligns ground floor plane automatically to $Z = 0.000\\text{ m}$.\n"
                "4. Generates sparse 3D point cloud for layout snapping."
            ],
            "color": "#1b2838",
            "bgcolor": "#101822"
        },
        {
            "id": 16,
            "type": "CameraTrackerSfM",
            "pos": [870, 310],
            "size": [380, 440],
            "flags": {},
            "order": 5,
            "mode": 0,
            "inputs": [
                {"name": "images", "type": "IMAGE", "link": 2},
                {"name": "tracking_mask", "type": "MASK", "link": 3},
                {"name": "manual_mask", "type": "MASK", "link": None}
            ],
            "outputs": [
                {"name": "undistorted_images", "type": "IMAGE", "links": []},
                {"name": "camera_data", "type": "CAMERA_DATA", "links": [5]},
                {"name": "track_points", "type": "TRACK_POINTS", "links": [6]},
                {"name": "tracking_mask", "type": "MASK", "links": []},
                {"name": "summary_report", "type": "STRING", "links": []}
            ],
            "title": "🎥 Camera Tracker (GLOMAP SfM)",
            "properties": {"Node name for S&R": "CameraTrackerSfM"},
            "widgets_values": [
                "Draft (2048 pts)",                     # point_cloud_density
                "Auto Estimate (Refined)",              # focal_length_mode
                28.0,                                   # focal_length_mm
                "Exclude Masked (Dynamic Objects)"      # mask_mode
            ],
            "color": "#1a365d",
            "bgcolor": "#15243b"
        },

        # --- Step 3: 3D Layout Editor with Actor Projection ---
        {
            "id": 20,
            "type": "MarkdownNote",
            "pos": [1290, 80],
            "size": [440, 200],
            "flags": {},
            "order": 6,
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "title": "📐 Step 3: 3D Layout Studio",
            "properties": {},
            "widgets_values": [
                "### Interactive 3D Studio & Actor Projection\n\n"
                "* Click **`⛶ Fullscreen 3D Viewport`** to expand.\n"
                "* **👤 Live Actor Billboard**: Live-action actor is projected directly in 3D to check scale and occlusion against 3D sets!\n"
                "* **Cinema Fly Navigation**: Hold **RMB + WASDQE** to flycam, wheel for speed.\n"
                "* **Hotkeys**: `Q` (Select), `W` (Move), `E` (Rotate), `R` (Scale).\n"
                "* **⚡ Save & Queue Render**: Bake layout and trigger generation right from the 3D viewport!"
            ],
            "color": "#3b1e54",
            "bgcolor": "#261338"
        },
        {
            "id": 21,
            "type": "LayoutEditor3D",
            "pos": [1290, 310],
            "size": [440, 480],
            "flags": {},
            "order": 7,
            "mode": 0,
            "inputs": [
                {"name": "camera_data", "type": "CAMERA_DATA", "link": 5},
                {"name": "track_points", "type": "TRACK_POINTS", "link": 6},
                {"name": "undistorted_images", "type": "IMAGE", "link": 10},
                {"name": "rough_actor_matte", "type": "MASK", "link": 4},
                {"name": "external_camera_file", "type": "STRING", "link": None}
            ],
            "outputs": [
                {"name": "layout_render", "type": "IMAGE", "links": [25]},
                {"name": "depth_map", "type": "IMAGE", "links": [26]},
                {"name": "layout_mask", "type": "MASK", "links": []},
                {"name": "camera_data", "type": "CAMERA_DATA", "links": []}
            ],
            "title": "📐 3D Layout Editor (WebGL / OBJ / FBX / GLB)",
            "properties": {"Node name for S&R": "LayoutEditor3D"},
            "widgets_values": [
                "Clay (Lambertian Studio)", # render_mode
                0.0,                        # camera_smoothing
                "[]",                       # layout_json
                ""                          # external_camera_file
            ],
            "color": "#4a154b",
            "bgcolor": "#2d0d2e"
        },

        # --- Step 4: Generative Set Extension (LTX-2.5) ---
        {
            "id": 30,
            "type": "MarkdownNote",
            "pos": [1770, 80],
            "size": [420, 160],
            "flags": {},
            "order": 8,
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "title": "🟡 Step 4: LTX-2.5 Layout-To-Render",
            "properties": {},
            "widgets_values": [
                "### Generative Set Extension\n\n"
                "The Clay / Semantic Color render provides exact perspective and camera parallax anchors.\n"
                "**LTX-2.5 IC-LoRA Layout-To-Render + Distilled LoRA 450** transforms proxy blocks into photorealistic environment in 8 fast steps."
            ],
            "color": "#5c3d10",
            "bgcolor": "#38250a"
        },
        {
            "id": 31,
            "type": "CLIPTextEncode",
            "pos": [1770, 260],
            "size": [420, 140],
            "flags": {},
            "order": 9,
            "mode": 0,
            "inputs": [
                {"name": "clip", "type": "CLIP", "link": 18}
            ],
            "outputs": [
                {"name": "CONDITIONING", "type": "CONDITIONING", "links": [8]}
            ],
            "title": "Positive Prompt (Environment Design)",
            "properties": {"Node name for S&R": "CLIPTextEncode"},
            "widgets_values": [
                "Cinematic photorealistic sci-fi citadel with massive weathered stone towers and glowing architectural panels replacing the proxy layout, volumetric golden hour sunlight, dust motes, 35mm film grain, 8k resolution"
            ],
            "color": "#235323",
            "bgcolor": "#163316"
        },
        {
            "id": 32,
            "type": "CLIPTextEncode",
            "pos": [1770, 430],
            "size": [420, 100],
            "flags": {},
            "order": 10,
            "mode": 0,
            "inputs": [
                {"name": "clip", "type": "CLIP", "link": 18}
            ],
            "outputs": [
                {"name": "CONDITIONING", "type": "CONDITIONING", "links": [9]}
            ],
            "title": "Negative Prompt",
            "properties": {"Node name for S&R": "CLIPTextEncode"},
            "widgets_values": [
                "blurry, low quality, artifacts, cartoon, 3d render plastic look, clay mockup, flat lighting, jitter"
            ],
            "color": "#532323",
            "bgcolor": "#331616"
        },
        {
            "id": 33,
            "type": "VAEEncode",
            "pos": [1770, 560],
            "size": [210, 80],
            "flags": {},
            "order": 11,
            "mode": 0,
            "inputs": [
                {"name": "pixels", "type": "IMAGE", "link": 25},
                {"name": "vae", "type": "VAE", "link": 19}
            ],
            "outputs": [
                {"name": "LATENT", "type": "LATENT", "links": [11]}
            ],
            "title": "VAE Encode Clay",
            "properties": {"Node name for S&R": "VAEEncode"}
        },
        {
            "id": 34,
            "type": "KSampler",
            "pos": [2010, 560],
            "size": [320, 340],
            "flags": {},
            "order": 12,
            "mode": 0,
            "inputs": [
                {"name": "model", "type": "MODEL", "link": 17},
                {"name": "positive", "type": "CONDITIONING", "link": 8},
                {"name": "negative", "type": "CONDITIONING", "link": 9},
                {"name": "latent_image", "type": "LATENT", "link": 11}
            ],
            "outputs": [
                {"name": "LATENT", "type": "LATENT", "links": [12]}
            ],
            "title": "LTX-2.5 Layout Sampler (8-Step)",
            "properties": {"Node name for S&R": "KSampler"},
            "widgets_values": [
                42,         # seed
                "fixed",    # control_after_generate
                8,          # steps (Distilled 8-Step)
                1.0,        # cfg (Optimal for distilled LoRA)
                "euler",    # sampler_name
                "normal",   # scheduler
                0.85        # denoise
            ],
            "color": "#664400",
            "bgcolor": "#442e00"
        },
        {
            "id": 35,
            "type": "VAEDecode",
            "pos": [2360, 560],
            "size": [210, 80],
            "flags": {},
            "order": 13,
            "mode": 0,
            "inputs": [
                {"name": "samples", "type": "LATENT", "link": 12},
                {"name": "vae", "type": "VAE", "link": 19}
            ],
            "outputs": [
                {"name": "IMAGE", "type": "IMAGE", "links": [13]}
            ],
            "title": "VAE Decode Environment",
            "properties": {"Node name for S&R": "VAEDecode"}
        },

        # --- Step 5: VFX Harmonizer & Compositor ---
        {
            "id": 40,
            "type": "MarkdownNote",
            "pos": [2610, 80],
            "size": [440, 160],
            "flags": {},
            "order": 14,
            "mode": 0,
            "inputs": [],
            "outputs": [],
            "title": "✨ Step 5: VFX Harmonizer & Compositing",
            "properties": {},
            "widgets_values": [
                "### Layer Harmonization & Light Wrap\n\n"
                "1. **Reinhard Color Tone Matching**: Seamlessly blends plate exposure and color temperature to match the newly generated world.\n"
                "2. **Light Wrap**: Bleeds ambient background luminance onto actor edge silhouettes for studio-quality integration.\n"
                "3. **Feathering**: Sub-pixel matte edge blur."
            ],
            "color": "#4a1c1c",
            "bgcolor": "#2b1010"
        },
        {
            "id": 41,
            "type": "VFXCompositor",
            "pos": [2610, 260],
            "size": [440, 360],
            "flags": {},
            "order": 15,
            "mode": 0,
            "inputs": [
                {"name": "background_video", "type": "IMAGE", "link": 13},
                {"name": "foreground_video", "type": "IMAGE", "link": 14},
                {"name": "alpha_matte", "type": "MASK", "link": 7}
            ],
            "outputs": [
                {"name": "composite_video", "type": "IMAGE", "links": [15]},
                {"name": "combined_mask", "type": "MASK", "links": []}
            ],
            "title": "✨ VFX Harmonizer & Compositor",
            "properties": {"Node name for S&R": "VFXCompositor"},
            "widgets_values": [
                2.0,  # edge_feather_px
                0.20, # light_wrap_strength
                True  # color_harmonize
            ],
            "color": "#7b1113",
            "bgcolor": "#470a0b"
        },

        # --- Model & LoRA Loaders ---
        {
            "id": 50,
            "type": "UNETLoader",
            "pos": [1290, 830],
            "size": [380, 100],
            "flags": {},
            "order": 16,
            "mode": 0,
            "inputs": [],
            "outputs": [
                {"name": "MODEL", "type": "MODEL", "links": [16]}
            ],
            "title": "LTX-2.5 Diffusion Model (UNET)",
            "properties": {"Node name for S&R": "UNETLoader"},
            "widgets_values": [
                "ltx-2.5\\ltx-2.5-22b-dev-transformer-bf16.safetensors",
                "default"
            ]
        },
        {
            "id": 53,
            "type": "CLIPLoader",
            "pos": [1290, 960],
            "size": [380, 100],
            "flags": {},
            "order": 17,
            "mode": 0,
            "inputs": [],
            "outputs": [
                {"name": "CLIP", "type": "CLIP", "links": [23]}
            ],
            "title": "Gemma4 Text Encoder (CLIP)",
            "properties": {"Node name for S&R": "CLIPLoader"},
            "widgets_values": [
                "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
                "ltxv"
            ]
        },
        {
            "id": 54,
            "type": "VAELoader",
            "pos": [1290, 1090],
            "size": [380, 80],
            "flags": {},
            "order": 18,
            "mode": 0,
            "inputs": [],
            "outputs": [
                {"name": "VAE", "type": "VAE", "links": [19]}
            ],
            "title": "LTX-2.5 Video VAE",
            "properties": {"Node name for S&R": "VAELoader"},
            "widgets_values": [
                "LTX-2.5\\ltx-2.5-video-vae-bf16.safetensors"
            ]
        },
        {
            "id": 51,
            "type": "LoraLoader",
            "pos": [1710, 830],
            "size": [360, 140],
            "flags": {},
            "order": 19,
            "mode": 0,
            "inputs": [
                {"name": "model", "type": "MODEL", "link": 16},
                {"name": "clip", "type": "CLIP", "link": 23}
            ],
            "outputs": [
                {"name": "MODEL", "type": "MODEL", "links": [21]},
                {"name": "CLIP", "type": "CLIP", "links": [22]}
            ],
            "title": "LTX-2.5 IC-LoRA (Layout-To-Render)",
            "properties": {"Node name for S&R": "LoraLoader"},
            "widgets_values": [
                "LTX-2.5\\ltx-2.5-22b-ic-lora-layout-to-render-1.0.safetensors",
                1.0, # strength_model
                1.0  # strength_clip
            ]
        },
        {
            "id": 52,
            "type": "LoraLoader",
            "pos": [2110, 830],
            "size": [360, 140],
            "flags": {},
            "order": 20,
            "mode": 0,
            "inputs": [
                {"name": "model", "type": "MODEL", "link": 21},
                {"name": "clip", "type": "CLIP", "link": 22}
            ],
            "outputs": [
                {"name": "MODEL", "type": "MODEL", "links": [17]},
                {"name": "CLIP", "type": "CLIP", "links": [18]}
            ],
            "title": "⚡ LTX-2.5 Distilled LoRA (8-Step Fast)",
            "properties": {"Node name for S&R": "LoraLoader"},
            "widgets_values": [
                "LTX-2.5\\ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
                1.0, # strength_model
                1.0  # strength_clip
            ]
        },

        # --- Output / Preview ---
        {
            "id": 60,
            "type": "SaveImage",
            "pos": [3090, 260],
            "size": [380, 420],
            "flags": {},
            "order": 21,
            "mode": 0,
            "inputs": [
                {"name": "images", "type": "IMAGE", "link": 15}
            ],
            "outputs": [],
            "title": "Final 4K VFX Composite Output",
            "properties": {"Node name for S&R": "SaveImage"},
            "widgets_values": ["VFX_Final_Shot"]
        }
    ],
    "links": [
        # [id, origin_id, origin_slot, target_id, target_slot, type]
        [2, 12, 0, 16, 0, "IMAGE"],          # VFXPlatePrep (undistorted_plate) -> CameraTrackerSfM (images)
        [3, 12, 1, 16, 1, "MASK"],           # VFXPlatePrep (tracking_mask) -> CameraTrackerSfM (tracking_mask)
        [4, 12, 2, 21, 3, "MASK"],           # VFXPlatePrep (rough_actor_matte) -> LayoutEditor3D (rough_actor_matte)
        [5, 16, 1, 21, 0, "CAMERA_DATA"],    # CameraTrackerSfM (camera_data) -> LayoutEditor3D (camera_data)
        [6, 16, 2, 21, 1, "TRACK_POINTS"],   # CameraTrackerSfM (track_points) -> LayoutEditor3D (track_points)
        [7, 12, 2, 41, 2, "MASK"],           # VFXPlatePrep (rough_actor_matte) -> VFXCompositor (alpha_matte)
        [8, 31, 0, 34, 1, "CONDITIONING"],   # Positive CLIP -> KSampler
        [9, 32, 0, 34, 2, "CONDITIONING"],   # Negative CLIP -> KSampler
        [10, 12, 0, 21, 2, "IMAGE"],         # VFXPlatePrep (undistorted_plate) -> LayoutEditor3D (undistorted_images)
        [11, 33, 0, 34, 3, "LATENT"],        # VAEEncode -> KSampler (latent_image)
        [12, 34, 0, 35, 0, "LATENT"],        # KSampler -> VAEDecode (samples)
        [13, 35, 0, 41, 0, "IMAGE"],         # VAEDecode -> VFXCompositor (background_video)
        [14, 12, 0, 41, 1, "IMAGE"],         # VFXPlatePrep (undistorted_plate) -> VFXCompositor (foreground_video)
        [15, 41, 0, 60, 0, "IMAGE"],         # VFXCompositor -> SaveImage
        [16, 50, 0, 51, 0, "MODEL"],         # UNET -> LoraLoader Layout
        [17, 52, 0, 34, 0, "MODEL"],         # Distilled LoRA -> KSampler (model)
        [18, 52, 1, 31, 0, "CLIP"],          # Distilled LoRA -> Positive CLIP
        [19, 54, 0, 33, 1, "VAE"],           # VAE -> VAEEncode
        [21, 51, 0, 52, 0, "MODEL"],         # LoRA Layout -> Distilled LoRA
        [22, 51, 1, 52, 1, "CLIP"],          # LoRA Layout -> Distilled LoRA
        [23, 53, 0, 51, 1, "CLIP"],          # CLIPLoader -> LoRA Layout
        [25, 21, 0, 33, 0, "IMAGE"],         # LayoutEditor3D (layout_render) -> VAEEncode (pixels)
        [26, 21, 1, 41, 2, "IMAGE"]          # LayoutEditor3D (depth_map)
    ],
    "groups": [
        {
            "id": 1,
            "title": "Stage 1: Plate Prep & AI Masking",
            "bounding": [40, 20, 800, 680],
            "color": "#1a365d",
            "font_size": 24
        },
        {
            "id": 2,
            "title": "Stage 2: GLOMAP 3D Tracking & Point Cloud",
            "bounding": [850, 20, 420, 750],
            "color": "#1a365d",
            "font_size": 24
        },
        {
            "id": 3,
            "title": "Stage 3: 3D Layout Studio & Live Actor Projection",
            "bounding": [1280, 20, 460, 790],
            "color": "#4a154b",
            "font_size": 24
        },
        {
            "id": 4,
            "title": "Stage 4: LTX-2.5 Generative Set Extension (8-Step Distilled)",
            "bounding": [1750, 20, 830, 920],
            "color": "#5c3d10",
            "font_size": 24
        },
        {
            "id": 5,
            "title": "Stage 5: VFX Light Wrap & Color Harmonizer",
            "bounding": [2590, 20, 900, 720],
            "color": "#7b1113",
            "font_size": 24
        }
    ],
    "config": {},
    "extra": {
        "ds": {
            "scale": 0.85,
            "offset": [-40, 20]
        }
    },
    "version": 0.4
}

out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vfx_suite_master_workflow.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(workflow, f, indent=2, ensure_ascii=False)

print(f"Master VFX Suite Workflow written successfully to: {out_path}")
print(f"Nodes: {len(workflow['nodes'])}, Links: {len(workflow['links'])}, Groups: {len(workflow['groups'])}")
