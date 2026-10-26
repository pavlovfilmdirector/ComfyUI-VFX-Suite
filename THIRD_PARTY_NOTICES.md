# Third-party notices

KESHIKI VFX Studio is GPL-3.0 (see `LICENSE`). It bundles a few web libraries and relies on separately
installed tools, node packs and models that keep their own licenses. Nothing below is relicensed by this project.
Check each upstream page for the current terms before commercial use.

## Bundled in this repository (`web/lib`)

| component | license |
|---|---|
| three.js core + example loaders/controls (FBXLoader, GLTFLoader, OBJLoader, OrbitControls, TransformControls) | MIT, (c) three.js authors |
| fflate | MIT |
| lucide icons | ISC |

## Installed separately by `install.py`

| component | source | license |
|---|---|---|
| COLMAP (incl. the GLOMAP global mapper) | github.com/colmap/colmap | BSD (new BSD); binaries include third-party libraries, e.g. CUDA runtime |
| GLOMAP (optional legacy build) | github.com/colmap/glomap | BSD-3-Clause |
| ComfyUI-LTXVideo | github.com/Lightricks/ComfyUI-LTXVideo | see upstream |
| comfyui_controlnet_aux | github.com/Fannovel16/comfyui_controlnet_aux | see upstream |
| ComfyUI-DepthAnythingV2 | github.com/kijai/ComfyUI-DepthAnythingV2 | see upstream |
| ComfyUI-VideoHelperSuite | github.com/Kosinkadink/ComfyUI-VideoHelperSuite | see upstream |
| ComfyUI-segment-anything-2 | github.com/kijai/ComfyUI-segment-anything-2 | see upstream |
| ComfyUI-CorridorKey | github.com/SeanBRVFX/ComfyUI-CorridorKey | follows CorridorKey's own terms (below) |
| Relight prompt formula | adapted from ComfyUI-qwenmultianglelight (aiwood, wallen) | MIT |
| Python packages | `requirements.txt` | each package's own license (e.g. Ultralytics is AGPL-3.0) |

## Models (downloaded by the user)

| model | license notes |
|---|---|
| LTX-2.5 + IC-LoRAs (Lightricks) | open weights under Lightricks' own license - see the model cards |
| FLUX.3 Klein (Black Forest Labs, 5.6B) + Qwen3-VL-4B text encoder | FLUX Kommunity License v1.0: open weights; non-commercial use, and commercial output for users under US$5M annual revenue - see the model card |
| Qwen-Image-Edit-2511 (optional) + Lightning LoRA | Apache-2.0 |
| CorridorKey (nikopueringer) | custom terms described by the author as close to CC BY-NC-SA 4.0: no resale or paid API, keep the "CorridorKey" name |
| SAM 2 (Meta) | see the model card |
| SAM 3.1, SAM 3D Body (Meta) | SAM License - see the model cards |
| RT-DETR v4 person detector (used by SAM 3D Body) | see the model card |
| Depth Anything 3 Mono-Large | Apache-2.0 (Comfy-Org repack) |
| Apple Depth Pro (optional, own repo + checkpoint) | Apple Sample Code License (code and weights; redistribution keeps the notice) |
| Depth Anything V2 | license depends on the model size - see the model card |
| Optional 3D generators: TRELLIS.2 (Microsoft, code MIT), Hunyuan3D-Omni (Tencent Hunyuan 3D community license, not licensed in the EU, UK and South Korea) | see the model cards |
| Optional Gaussian splats: Apple SHARP (ml-sharp) | Apple research-only model license (non-commercial) |

## Cloud services

KESHIKI runs on local open-weights models. Code paths for cloud APIs (Comfy partner nodes, per-process cloud
providers, cloud LLMs for the agent, edge-tts) are disabled unless the user opts in with `KESHIKI_ALLOW_CLOUD=1`
or `"allow_cloud": true` in `keshiki_local.json`.
