"""
Generates the Master Cinematic Cocktail Workflow and API Prompt for ComfyUI-VFX-Suite.
Integrates:
1. Plate Prep (Undistort, SAM 2.1 Masking, Coarse Actor Hint)
2. CorridorKey (GreenFormer deep learning despill & fine edge matting)
3. Camera Tracker (GLOMAP Global SfM with metric scale & ground alignment)
4. 3D Layout Studio (Three.js WebGL, 26,010 calibration markers, 3D Depth Map Pass)
5. LTX-Video 2.3/2.5 DiT + IC-LoRA Union Control (Depth/Canny/Pose) + Distilled 8-Step LoRA
6. AI Director (Gemma 12B prompt conditioning)
7. VFX Harmonizer & Compositor (Reinhard color matching, Light Wrap)
8. Multi-pass Composite Export (.chan / EXR / WebGL passes)
"""

import json
import os

def build_workflow():
    workflow = {
        "id": "comfyui-vfx-suite-cocktail-master-v1",
        "revision": 4,
        "last_node_id": 120,
        "last_link_id": 250,
        "nodes": [
            # --- 1. Plate Prep ---
            {
                "id": 10,
                "type": "MarkdownNote",
                "pos": [40, 60],
                "size": [360, 220],
                "flags": {},
                "order": 1,
                "mode": 0,
                "inputs": [],
                "outputs": [],
                "title": "📌 Step 1: Plate Prep & Ingestion",
                "properties": {},
                "widgets_values": [
                    "### 🎞️ Step 1: Plate Prep\n\n"
                    "1. Загрузка исходной пластины (видео / EXR / DPX).\n"
                    "2. Автоматическая калибровка дисторсии объектива $k_1, k_2$.\n"
                    "3. SAM 2.1 / YOLO динамическое маскирование персонажа.\n"
                    "4. Формирование грубого альфа-хинта для CorridorKey."
                ],
                "color": "#1b2838",
                "bgcolor": "#101822"
            },
            {
                "id": 12,
                "type": "VFXPlatePrep",
                "pos": [40, 310],
                "size": [360, 340],
                "flags": {},
                "order": 2,
                "mode": 0,
                "inputs": [],
                "outputs": [
                    {"name": "undistorted_plate", "type": "IMAGE", "links": [101, 102]},
                    {"name": "tracking_mask", "type": "MASK", "links": [103]},
                    {"name": "rough_actor_matte", "type": "MASK", "links": [104]},
                    {"name": "prep_report", "type": "STRING", "links": []}
                ],
                "title": "🎞️ VFX Plate Prep & Masking",
                "properties": {"Node name for S&R": "VFXPlatePrep"},
                "widgets_values": [],
                "color": "#1a365d",
                "bgcolor": "#15243b"
            },

            # --- 2. CorridorKey (GreenFormer Matting) ---
            {
                "id": 13,
                "type": "MarkdownNote",
                "pos": [440, 60],
                "size": [380, 220],
                "flags": {},
                "order": 3,
                "mode": 0,
                "inputs": [],
                "outputs": [],
                "title": "📌 Step 2: CorridorKey Matting",
                "properties": {},
                "widgets_values": [
                    "### 🟩 Step 2: CorridorKey (GreenFormer)\n\n"
                    "1. Принимает недисторсированную пластину и грубый альфа-хинт из Step 1.\n"
                    "2. Извлекает тончайшие волоски, полупрозрачность и дым без зеленого рефлекса.\n"
                    "3. Выдает чистый деспиленный `fg` и субпиксельный `matte`."
                ],
                "color": "#1b3820",
                "bgcolor": "#102214"
            },
            {
                "id": 14,
                "type": "CorridorKey",
                "pos": [440, 310],
                "size": [380, 340],
                "flags": {},
                "order": 4,
                "mode": 0,
                "inputs": [
                    {"name": "image", "type": "IMAGE", "link": 101},
                    {"name": "mask", "type": "MASK", "link": 104}
                ],
                "outputs": [
                    {"name": "fg", "type": "IMAGE", "links": []},
                    {"name": "matte", "type": "MASK", "links": [105, 106]},
                    {"name": "processed", "type": "IMAGE", "links": [107]},
                    {"name": "QC", "type": "IMAGE", "links": []}
                ],
                "title": "🟩 CorridorKey (Neural Green Screen Matting)",
                "properties": {"Node name for S&R": "CorridorKey"},
                "widgets_values": [
                    "sRGB", # gamma_space
                    1.0,    # despill_strength
                    1.0,    # refiner_strength
                    "On",   # auto_despeckle
                    400     # despeckle_size
                ],
                "color": "#1b4d24",
                "bgcolor": "#112e16"
            },

            # --- 3. GLOMAP Camera Tracker ---
            {
                "id": 15,
                "type": "MarkdownNote",
                "pos": [860, 60],
                "size": [380, 220],
                "flags": {},
                "order": 5,
                "mode": 0,
                "inputs": [],
                "outputs": [],
                "title": "📌 Step 3: GLOMAP Global SfM",
                "properties": {},
                "widgets_values": [
                    "### 🎥 Step 3: GLOMAP SfM Tracking\n\n"
                    "1. Трекинг камеры по чистому фону (исключая маску персонажа).\n"
                    "2. Автоматическое выравнивание плоскости пола $Z = 0.000\\text{ m}$.\n"
                    "3. Построение разреженного облака точек для привязки 3D геометрии."
                ],
                "color": "#1b2838",
                "bgcolor": "#101822"
            },
            {
                "id": 16,
                "type": "CameraTrackerSfM",
                "pos": [860, 310],
                "size": [380, 340],
                "flags": {},
                "order": 6,
                "mode": 0,
                "inputs": [
                    {"name": "images", "type": "IMAGE", "link": 102},
                    {"name": "tracking_mask", "type": "MASK", "link": 103},
                    {"name": "manual_mask", "type": "MASK", "link": None}
                ],
                "outputs": [
                    {"name": "undistorted_images", "type": "IMAGE", "links": []},
                    {"name": "camera_data", "type": "CAMERA_DATA", "links": [108]},
                    {"name": "track_points", "type": "TRACK_POINTS", "links": [109]},
                    {"name": "tracking_mask", "type": "MASK", "links": []},
                    {"name": "summary_report", "type": "STRING", "links": []}
                ],
                "title": "🎥 Camera Tracker (GLOMAP SfM)",
                "properties": {"Node name for S&R": "CameraTrackerSfM"},
                "widgets_values": [
                    "Draft (2048 pts)",
                    "Auto Estimate (Refined)",
                    28.0,
                    "Exclude Masked (Dynamic Objects)"
                ],
                "color": "#1a365d",
                "bgcolor": "#15243b"
            },

            # --- 4. 3D Layout Studio & Markers ---
            {
                "id": 20,
                "type": "MarkdownNote",
                "pos": [1280, 60],
                "size": [440, 220],
                "flags": {},
                "order": 7,
                "mode": 0,
                "inputs": [],
                "outputs": [],
                "title": "📌 Step 4: 3D Layout & Markers",
                "properties": {},
                "widgets_values": [
                    "### 📐 Step 4: 3D Layout & Markers Studio\n\n"
                    "1. Загрузка 26,010 калибровочных маркеров из `Markers_3.blend`.\n"
                    "2. Биллборд живого актера в реальном масштабе сцены.\n"
                    "3. Аппаратный WebGL рендер мультипассов: 3D Depth Map, Shaded Markers, Wireframe.\n"
                    "4. Прямая передача Depth карты в IC-LoRA Union Control."
                ],
                "color": "#3b1e54",
                "bgcolor": "#261338"
            },
            {
                "id": 21,
                "type": "LayoutEditor3D",
                "pos": [1280, 310],
                "size": [440, 480],
                "flags": {},
                "order": 8,
                "mode": 0,
                "inputs": [
                    {"name": "camera_data", "type": "CAMERA_DATA", "link": 108},
                    {"name": "track_points", "type": "TRACK_POINTS", "link": 109},
                    {"name": "undistorted_images", "type": "IMAGE", "link": 102},
                    {"name": "rough_actor_matte", "type": "MASK", "link": 105},
                    {"name": "external_camera_file", "type": "STRING", "link": None}
                ],
                "outputs": [
                    {"name": "layout_render", "type": "IMAGE", "links": [110]},
                    {"name": "depth_map", "type": "IMAGE", "links": [111]},
                    {"name": "layout_mask", "type": "MASK", "links": []},
                    {"name": "camera_data", "type": "CAMERA_DATA", "links": []}
                ],
                "title": "📐 3D Layout Editor (Markers_3 & WebGL Depth)",
                "properties": {"Node name for S&R": "LayoutEditor3D"},
                "widgets_values": [
                    "Depth Map", # render_mode: Depth Map for IC-LoRA Union Control
                    0.0,         # camera_smoothing
                    "[]",        # layout_json
                    ""           # external_camera_file
                ],
                "color": "#4a154b",
                "bgcolor": "#2d0d2e"
            },

            # --- 5. Checkpoint & LoRA Loaders ---
            {
                "id": 50,
                "type": "CheckpointLoaderSimple",
                "pos": [1760, 60],
                "size": [400, 100],
                "flags": {},
                "order": 9,
                "mode": 0,
                "inputs": [],
                "outputs": [
                    {"name": "MODEL", "type": "MODEL", "links": [112]},
                    {"name": "CLIP", "type": "CLIP", "links": [113]},
                    {"name": "VAE", "type": "VAE", "links": [114, 115]}
                ],
                "title": "LTX-2.3 22B Base Model (FP8)",
                "properties": {"Node name for S&R": "CheckpointLoaderSimple"},
                "widgets_values": ["ltx-2.3-22b-dev-fp8.safetensors"]
            },
            {
                "id": 51,
                "type": "LoraLoader",
                "pos": [1760, 200],
                "size": [400, 140],
                "flags": {},
                "order": 10,
                "mode": 0,
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": 112},
                    {"name": "clip", "type": "CLIP", "link": 113}
                ],
                "outputs": [
                    {"name": "MODEL", "type": "MODEL", "links": [116]},
                    {"name": "CLIP", "type": "CLIP", "links": [117]}
                ],
                "title": "IC-LoRA Union Control (Depth / Canny / Pose)",
                "properties": {"Node name for S&R": "LoraLoader"},
                "widgets_values": [
                    "LTX-2.3\\ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
                    1.0, # strength_model
                    1.0  # strength_clip
                ]
            },
            {
                "id": 52,
                "type": "LoraLoader",
                "pos": [1760, 380],
                "size": [400, 140],
                "flags": {},
                "order": 11,
                "mode": 0,
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": 116},
                    {"name": "clip", "type": "CLIP", "link": 117}
                ],
                "outputs": [
                    {"name": "MODEL", "type": "MODEL", "links": [118]},
                    {"name": "CLIP", "type": "CLIP", "links": [119]}
                ],
                "title": "⚡ LTX-2.3 Distilled LoRA (8-Step Fast)",
                "properties": {"Node name for S&R": "LoraLoader"},
                "widgets_values": [
                    "LTX-2.3\\ltx-2.3-22b-distilled-1.1_lora-dynamic_fro09_avg_rank_111_bf16.safetensors",
                    1.0, # strength_model
                    1.0  # strength_clip
                ]
            },

            # --- 6. Prompt Conditioning (AI Director) ---
            {
                "id": 31,
                "type": "CLIPTextEncode",
                "pos": [1760, 560],
                "size": [400, 120],
                "flags": {},
                "order": 12,
                "mode": 0,
                "inputs": [
                    {"name": "clip", "type": "CLIP", "link": 119}
                ],
                "outputs": [
                    {"name": "CONDITIONING", "type": "CONDITIONING", "links": [120]}
                ],
                "title": "Positive Prompt (AI Director Gemma 12B)",
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
                "pos": [1760, 710],
                "size": [400, 90],
                "flags": {},
                "order": 13,
                "mode": 0,
                "inputs": [
                    {"name": "clip", "type": "CLIP", "link": 119}
                ],
                "outputs": [
                    {"name": "CONDITIONING", "type": "CONDITIONING", "links": [121]}
                ],
                "title": "Negative Prompt",
                "properties": {"Node name for S&R": "CLIPTextEncode"},
                "widgets_values": [
                    "blurry, low quality, artifacts, cartoon, 3d render plastic look, clay mockup, flat lighting, jitter"
                ],
                "color": "#532323",
                "bgcolor": "#331616"
            },

            # --- 7. VAE & Sampler ---
            {
                "id": 33,
                "type": "VAEEncode",
                "pos": [2200, 310],
                "size": [220, 90],
                "flags": {},
                "order": 14,
                "mode": 0,
                "inputs": [
                    {"name": "pixels", "type": "IMAGE", "link": 111},
                    {"name": "vae", "type": "VAE", "link": 114}
                ],
                "outputs": [
                    {"name": "LATENT", "type": "LATENT", "links": [122]}
                ],
                "title": "VAE Encode 3D Depth",
                "properties": {"Node name for S&R": "VAEEncode"}
            },
            {
                "id": 34,
                "type": "KSampler",
                "pos": [2460, 310],
                "size": [320, 340],
                "flags": {},
                "order": 15,
                "mode": 0,
                "inputs": [
                    {"name": "model", "type": "MODEL", "link": 118},
                    {"name": "positive", "type": "CONDITIONING", "link": 120},
                    {"name": "negative", "type": "CONDITIONING", "link": 121},
                    {"name": "latent_image", "type": "LATENT", "link": 122}
                ],
                "outputs": [
                    {"name": "LATENT", "type": "LATENT", "links": [123]}
                ],
                "title": "LTX 8-Step Sampler (IC-LoRA Union Control)",
                "properties": {"Node name for S&R": "KSampler"},
                "widgets_values": [
                    42,       # seed
                    "fixed",  # control_after_generate
                    8,        # steps
                    1.0,      # cfg
                    "euler",  # sampler_name
                    "normal", # scheduler
                    0.85      # denoise
                ],
                "color": "#664400",
                "bgcolor": "#442e00"
            },
            {
                "id": 35,
                "type": "VAEDecode",
                "pos": [2820, 310],
                "size": [210, 90],
                "flags": {},
                "order": 16,
                "mode": 0,
                "inputs": [
                    {"name": "samples", "type": "LATENT", "link": 123},
                    {"name": "vae", "type": "VAE", "link": 115}
                ],
                "outputs": [
                    {"name": "IMAGE", "type": "IMAGE", "links": [124]}
                ],
                "title": "VAE Decode Environment",
                "properties": {"Node name for S&R": "VAEDecode"}
            },

            # --- 8. Compositor & Harmonizer ---
            {
                "id": 41,
                "type": "VFXCompositor",
                "pos": [3070, 310],
                "size": [440, 360],
                "flags": {},
                "order": 17,
                "mode": 0,
                "inputs": [
                    {"name": "background_video", "type": "IMAGE", "link": 124},
                    {"name": "foreground_video", "type": "IMAGE", "link": 107},
                    {"name": "alpha_matte", "type": "MASK", "link": 106}
                ],
                "outputs": [
                    {"name": "composite_video", "type": "IMAGE", "links": [125]},
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

            # --- 9. Output ---
            {
                "id": 60,
                "type": "SaveImage",
                "pos": [3550, 310],
                "size": [380, 420],
                "flags": {},
                "order": 18,
                "mode": 0,
                "inputs": [
                    {"name": "images", "type": "IMAGE", "link": 125}
                ],
                "outputs": [],
                "title": "Final 4K VFX Composite Output",
                "properties": {"Node name for S&R": "SaveImage"},
                "widgets_values": ["VFX_Cocktail_Master"]
            }
        ],
        "links": [
            # [id, origin_id, origin_slot, target_id, target_slot, type]
            [101, 12, 0, 14, 0, "IMAGE"],         # VFXPlatePrep -> CorridorKey (image)
            [102, 12, 0, 16, 0, "IMAGE"],         # VFXPlatePrep -> CameraTrackerSfM (images)
            [103, 12, 1, 16, 1, "MASK"],          # VFXPlatePrep -> CameraTrackerSfM (tracking_mask)
            [104, 12, 2, 14, 1, "MASK"],          # VFXPlatePrep -> CorridorKey (mask)
            [105, 14, 1, 21, 3, "MASK"],          # CorridorKey (matte) -> LayoutEditor3D (rough_actor_matte)
            [106, 14, 1, 41, 2, "MASK"],          # CorridorKey (matte) -> VFXCompositor (alpha_matte)
            [107, 14, 2, 41, 1, "IMAGE"],         # CorridorKey (processed fg) -> VFXCompositor (foreground_video)
            [108, 16, 1, 21, 0, "CAMERA_DATA"],   # CameraTrackerSfM -> LayoutEditor3D (camera_data)
            [109, 16, 2, 21, 1, "TRACK_POINTS"],  # CameraTrackerSfM -> LayoutEditor3D (track_points)
            [110, 21, 0, 33, 0, "IMAGE"],         # LayoutEditor3D (layout_render)
            [111, 21, 1, 33, 0, "IMAGE"],         # LayoutEditor3D (depth_map) -> VAEEncode (pixels)
            [112, 50, 0, 51, 0, "MODEL"],         # Base Model -> IC-LoRA Union Control
            [113, 50, 1, 51, 1, "CLIP"],          # Base CLIP -> IC-LoRA Union Control
            [114, 50, 2, 33, 1, "VAE"],           # Base VAE -> VAEEncode
            [115, 50, 2, 35, 1, "VAE"],           # Base VAE -> VAEDecode
            [116, 51, 0, 52, 0, "MODEL"],         # IC-LoRA -> Distilled LoRA
            [117, 51, 1, 52, 1, "CLIP"],          # IC-LoRA -> Distilled LoRA
            [118, 52, 0, 34, 0, "MODEL"],         # Distilled LoRA -> KSampler (model)
            [119, 52, 1, 31, 0, "CLIP"],          # Distilled LoRA -> Positive CLIP
            [120, 31, 0, 34, 1, "CONDITIONING"],  # Positive Prompt -> KSampler
            [121, 32, 0, 34, 2, "CONDITIONING"],  # Negative Prompt -> KSampler
            [122, 33, 0, 34, 3, "LATENT"],        # VAEEncode -> KSampler (latent_image)
            [123, 34, 0, 35, 0, "LATENT"],        # KSampler -> VAEDecode (samples)
            [124, 35, 0, 41, 0, "IMAGE"],         # VAEDecode -> VFXCompositor (background_video)
            [125, 41, 0, 60, 0, "IMAGE"]          # VFXCompositor -> SaveImage
        ],
        "groups": [
            {
                "id": 1,
                "title": "Stage 1: Plate Prep & Ingestion",
                "bounding": [20, 10, 400, 680],
                "color": "#1a365d",
                "font_size": 24
            },
            {
                "id": 2,
                "title": "Stage 2: CorridorKey Neural Matting & Despill",
                "bounding": [420, 10, 420, 680],
                "color": "#1b4d24",
                "font_size": 24
            },
            {
                "id": 3,
                "title": "Stage 3: GLOMAP 3D Tracking & Ground Alignment",
                "bounding": [840, 10, 420, 680],
                "color": "#1a365d",
                "font_size": 24
            },
            {
                "id": 4,
                "title": "Stage 4: 3D Layout Studio & Markers Grid",
                "bounding": [1260, 10, 480, 800],
                "color": "#4a154b",
                "font_size": 24
            },
            {
                "id": 5,
                "title": "Stage 5: LTX-2.3 DiT + IC-LoRA Union Control (8-Step Fast)",
                "bounding": [1740, 10, 1300, 820],
                "color": "#5c3d10",
                "font_size": 24
            },
            {
                "id": 6,
                "title": "Stage 6: VFX Light Wrap & Color Harmonizer",
                "bounding": [3050, 10, 900, 740],
                "color": "#7b1113",
                "font_size": 24
            }
        ],
        "config": {},
        "extra": {
            "ds": {
                "scale": 0.75,
                "offset": [-20, 10]
            }
        },
        "version": 0.4
    }

    # API Prompt Format (for direct ComfyUI /prompt queue)
    api_prompt = {
        "12": {
            "class_type": "VFXPlatePrep",
            "inputs": {}
        },
        "14": {
            "class_type": "CorridorKey",
            "inputs": {
                "image": ["12", 0],
                "mask": ["12", 2],
                "gamma_space": "sRGB",
                "despill_strength": 1.0,
                "refiner_strength": 1.0,
                "auto_despeckle": "On",
                "despeckle_size": 400
            }
        },
        "16": {
            "class_type": "CameraTrackerSfM",
            "inputs": {
                "images": ["12", 0],
                "tracking_mask": ["12", 1],
                "point_cloud_density": "Draft (2048 pts)",
                "focal_length_mode": "Auto Estimate (Refined)",
                "focal_length_mm": 28.0,
                "mask_mode": "Exclude Masked (Dynamic Objects)"
            }
        },
        "21": {
            "class_type": "LayoutEditor3D",
            "inputs": {
                "camera_data": ["16", 1],
                "track_points": ["16", 2],
                "undistorted_images": ["12", 0],
                "rough_actor_matte": ["14", 1],
                "render_mode": "Depth Map",
                "camera_smoothing": 0.0,
                "layout_json": "[]",
                "external_camera_file": ""
            }
        },
        "50": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {
                "ckpt_name": "ltx-2.3-22b-dev-fp8.safetensors"
            }
        },
        "51": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["50", 0],
                "clip": ["50", 1],
                "lora_name": "LTX-2.3\\ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors",
                "strength_model": 1.0,
                "strength_clip": 1.0
            }
        },
        "52": {
            "class_type": "LoraLoader",
            "inputs": {
                "model": ["51", 0],
                "clip": ["51", 1],
                "lora_name": "LTX-2.3\\ltx-2.3-22b-distilled-1.1_lora-dynamic_fro09_avg_rank_111_bf16.safetensors",
                "strength_model": 1.0,
                "strength_clip": 1.0
            }
        },
        "31": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["52", 1],
                "text": "Cinematic photorealistic sci-fi citadel with massive weathered stone towers and glowing architectural panels replacing the proxy layout, volumetric golden hour sunlight, dust motes, 35mm film grain, 8k resolution"
            }
        },
        "32": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["52", 1],
                "text": "blurry, low quality, artifacts, cartoon, 3d render plastic look, clay mockup, flat lighting, jitter"
            }
        },
        "33": {
            "class_type": "VAEEncode",
            "inputs": {
                "pixels": ["21", 1],
                "vae": ["50", 2]
            }
        },
        "34": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["52", 0],
                "positive": ["31", 0],
                "negative": ["32", 0],
                "latent_image": ["33", 0],
                "seed": 42,
                "control_after_generate": "fixed",
                "steps": 8,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 0.85
            }
        },
        "35": {
            "class_type": "VAEDecode",
            "inputs": {
                "samples": ["34", 0],
                "vae": ["50", 2]
            }
        },
        "41": {
            "class_type": "VFXCompositor",
            "inputs": {
                "background_video": ["35", 0],
                "foreground_video": ["14", 2],
                "alpha_matte": ["14", 1],
                "edge_feather_px": 2.0,
                "light_wrap_strength": 0.20,
                "color_harmonize": True
            }
        },
        "60": {
            "class_type": "SaveImage",
            "inputs": {
                "filename_prefix": "VFX_Cocktail_Master",
                "images": ["41", 0]
            }
        }
    }

    return workflow, api_prompt

if __name__ == "__main__":
    base_dir = os.path.dirname(os.path.abspath(__file__))
    wf, api = build_workflow()

    wf_path = os.path.join(base_dir, "vfx_suite_master_workflow.json")
    with open(wf_path, "w", encoding="utf-8") as f:
        json.dump(wf, f, indent=2, ensure_ascii=False)
    print(f"[OK] Master UI Workflow written to: {wf_path}")

    cocktail_wf_path = os.path.join(base_dir, "master_cinematic_cocktail_workflow.json")
    with open(cocktail_wf_path, "w", encoding="utf-8") as f:
        json.dump(wf, f, indent=2, ensure_ascii=False)
    print(f"[OK] Cocktail UI Workflow written to: {cocktail_wf_path}")

    api_path = os.path.join(base_dir, "master_cocktail_api_prompt.json")
    with open(api_path, "w", encoding="utf-8") as f:
        json.dump(api, f, indent=2, ensure_ascii=False)
    print(f"[OK] Cocktail API Prompt written to: {api_path}")
