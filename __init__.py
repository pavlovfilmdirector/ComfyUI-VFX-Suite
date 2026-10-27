"""
ComfyUI-VFX-Suite
=================
Autonomous End-to-End VFX Suite for ComfyUI.

Nodes:
1. VFXPlatePrep: Ingestion, Lens Undistortion (k1, k2), YOLOv8 Auto-Masking, Boolean Manual Masking.
2. CameraTrackerSfM: GLOMAP Global SfM + Z=0 Ground Alignment.
3. LayoutEditor3D: Interactive WebGL 3D Viewport with Actor Projection, Timeline & Auto-Keying.
4. VFXCompositor: Layer Harmonization, Alpha Matting & Edge Blending for LTX-Video.
"""

import os
import sys

PACKAGE_ROOT = os.path.dirname(os.path.abspath(__file__))
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

# 1. Plate Prep & Undistortion
try:
    try:
        from .vfx_nodes.plate_prep_node import VFXPlatePrepNode
    except (ImportError, ValueError):
        from vfx_nodes.plate_prep_node import VFXPlatePrepNode
    NODE_CLASS_MAPPINGS["VFXPlatePrep"] = VFXPlatePrepNode
    NODE_DISPLAY_NAME_MAPPINGS["VFXPlatePrep"] = "🎞️ VFX Plate Prep & Masking"
except Exception as e:
    print(f"[ComfyUI-VFX-Suite] Error loading VFXPlatePrep: {e}")

# 2. Camera Tracker SfM
try:
    try:
        from .vfx_nodes.tracker_node import CameraTrackerNode
    except (ImportError, ValueError):
        from vfx_nodes.tracker_node import CameraTrackerNode
    NODE_CLASS_MAPPINGS["CameraTrackerSfM"] = CameraTrackerNode
    NODE_DISPLAY_NAME_MAPPINGS["CameraTrackerSfM"] = "🎥 Camera Tracker (GLOMAP SfM)"
except Exception as e:
    print(f"[ComfyUI-VFX-Suite] Error loading CameraTrackerSfM: {e}")

# 3. 3D Layout Editor
try:
    try:
        from .vfx_nodes.layout_editor_node import LayoutEditorNode
    except (ImportError, ValueError):
        from vfx_nodes.layout_editor_node import LayoutEditorNode
    NODE_CLASS_MAPPINGS["LayoutEditor3D"] = LayoutEditorNode
    NODE_DISPLAY_NAME_MAPPINGS["LayoutEditor3D"] = "📐 3D Layout Editor (WebGL / OBJ / FBX / GLB)"
except Exception as e:
    print(f"[ComfyUI-VFX-Suite] Error loading LayoutEditor3D: {e}")

# 4. VFX Compositor & Harmonizer
try:
    try:
        from .vfx_nodes.composite_node import VFXCompositorNode
    except (ImportError, ValueError):
        from vfx_nodes.composite_node import VFXCompositorNode
    NODE_CLASS_MAPPINGS["VFXCompositor"] = VFXCompositorNode
    NODE_DISPLAY_NAME_MAPPINGS["VFXCompositor"] = "✨ VFX Harmonizer & Compositor"
except Exception as e:
    print(f"[ComfyUI-VFX-Suite] Error loading VFXCompositor: {e}")

# 5. Loaders (text encoders with an explicit dtype - ACE-Step's LM crashes in fp16)
try:
    try:
        from .vfx_nodes import loader_nodes as _ln
    except (ImportError, ValueError):
        from vfx_nodes import loader_nodes as _ln
    NODE_CLASS_MAPPINGS.update(_ln.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_ln.NODE_DISPLAY_NAME_MAPPINGS)
except Exception as e:
    print(f"[ComfyUI-VFX-Suite] Error loading loader nodes: {e}")

# Register Server Endpoints for 3D Viewport
try:
    try:
        from .vfx_nodes.server_api import register_server_routes
    except (ImportError, ValueError):
        from vfx_nodes.server_api import register_server_routes
    register_server_routes()
except Exception as e:
    print(f"[ComfyUI-VFX-Suite] Server routes initialization note: {e}")

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
