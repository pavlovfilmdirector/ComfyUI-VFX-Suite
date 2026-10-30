# Changelog

All notable changes to **KESHIKI (景色) VFX Studio** (`ComfyUI-VFX-Suite`) are documented in this file.

## [0.1.0-preview] — 2026-10-30 (Technology Preview v0.1)

### Highlights
First public **Technology Preview (v0.1)** of **KESHIKI VFX Studio** — an end-to-end local VFX studio inside ComfyUI powered exclusively by open-weights models (LTX-2.5 22B + IC-LoRAs, FLUX.3 Klein, SAM 3.1 / SAM2, Depth Anything V2/3, Apple Depth Pro, MoGe-2, COLMAP + GLOMAP, TRELLIS.2, Whisper, F5-TTS, ACE-Step 1.5, and local Ollama AI Director).

### Added & Updated in v0.1
- **Unified Asset Gallery (Top Bar):**
  - Color-coded borders by media type (Images, 2D Videos, 3D Scenes & Meshes, Audio/Music) for instant visual recognition.
  - Strictly uniform `200×42 px` asset cards with right-aligned layout (`flex-end`) and static layer count badges.
- **3D Layout & Character Pipeline:**
  - Reorganized 3D scene toolbar into 3 logical rows:
    1. *Primitives:* Box, Sphere, Cylinder, Cone, Torus, Plane.
    2. *Scene & Lighting:* Free Camera, Sun Light, Point Light, 3-Point Ground Plane, and toggleable 3D Cursor (ON/OFF).
    3. *Characters & Passes:* Add Skeleton without mesh, Load FBX/Mixamo Animation, Show/Hide Bones, Export 3D Pose (OpenPose) to 2D Video, and Render 3D Characters through Shot Camera.
  - **Camera Sequence Render (`🎬 Render Camera`):** Added directly in the 3D **Monitor** header next to `Preview ON` with chunked upload, fast `libx264` MP4 compilation, live frame progress bar, and automatic addition to the Asset Gallery (`Baked 3D Layout`).
  - **Fast 3D-to-2D OpenPose & Clay Pass Export:** ROI-optimized `draw_openpose` (~12x faster on 4K plates), automatic 1920px long-edge capping, support for skeleton-only mannequins, and automatic registration in both 2D Video layers and the Top Asset Gallery.
  - **Skeleton Retargeting & Root Motion:** Fixed world-to-local scale conversion for FBX hip translation (`applyRetarget`), added `Hips: ON / LOCK` toggle and `📌 Root to Pivot` baking.
  - **Manual Actor Cutout Plane:** Dedicated dropzone supporting custom videos (`THREE.VideoTexture` synced to timeline) and images with one-click BiRefNet background removal (`✂️ Remove BG`).
- **2D Video & Paint Layers:**
  - **Cutout Alpha (`video_cutout`):** Extract transparent RGBA `.webm` video and PNG frame sequences from any mask or matte layer with checkerboard transparency preview.
  - **Eliminated Video Layer Flicker:** Instant layer state initialization from `videoLayersCache`, pre-decoded CSS `mask-image` updates during playback, and expanded 15-frame lookahead prefetch buffer.
  - **Timeline Keyframes & Ghost Mode (Onion-Skinning):** Brush strokes auto-key on the timeline with prev/next navigation, multi-select, mass move/delete, auto-clear of held frames on new strokes, and Ghost reference overlays for adjacent keyframes.
- **3D Mesh Generation (TRELLIS.2 / Hunyuan3D / Tripo):**
  - Real-time progress synchronization across both the 3D viewport overlay and the top-right global header chip (`#vfx-progress-chip`), with cancellation support and automatic sync to the Top Gallery and 3D Mesh Library whether triggered via UI button or AI Director.
- **My Tools (Custom ComfyUI Workflows) & AI Director:**
  - Automatic sanitization of orphan nodes and UI-to-API workflow conversion when importing `.json` workflows into **My Tools**.
  - Stage-by-stage confirmation and live action visualization for complex multi-step AI Director tasks, plus full MCP server parity (`keshiki_mcp.py`).
