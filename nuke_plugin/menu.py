"""
Nuke VFX Suite - Menu Registration
==================================
Adds the VFX Suite toolbar and node creation commands to Foundry Nuke.
"""

import os
import sys

# Ensure nuke_plugin is discoverable
plugin_dir = os.path.dirname(os.path.abspath(__file__))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

parent_dir = os.path.dirname(plugin_dir)
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

try:
    import nuke
    from .nuke_vfx_tracker import create_vfx_tracker_node, open_layout_studio

    # Add to Nuke Nodes Toolbar
    toolbar = nuke.menu("Nodes")
    vfx_menu = toolbar.addMenu("VFX Suite", icon="Camera.png")

    vfx_menu.addCommand("VFX Camera Tracker (GLOMAP)",
                        "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.create_vfx_tracker_node()",
                        icon="Camera.png")

    vfx_menu.addCommand("Open 3D Layout Studio",
                        "import nuke_plugin.nuke_vfx_tracker as nvt; nvt.open_layout_studio()",
                        icon="Axis.png")

    print("[VFX Suite] Loaded Foundry Nuke VFX Bridge Plugin successfully.")

except ImportError:
    pass
