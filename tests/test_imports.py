"""Every studio service must import without ComfyUI running (catches syntax errors and broken imports)."""
import importlib
import os
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "vfx_nodes"))
                 if f.endswith(".py") and f not in ("__init__.py",))
# nodes that need a real ComfyUI / torch / Blender runtime are only syntax-checked
RUNTIME_ONLY = {"composite_node", "layout_editor_node", "plate_prep_node", "tracker_node", "server_api"}


@pytest.mark.parametrize("name", MODULES)
def test_module_compiles(name):
    src = open(os.path.join(ROOT, "vfx_nodes", name + ".py"), encoding="utf-8").read()
    compile(src, name, "exec")


@pytest.mark.parametrize("name", [m for m in MODULES if m not in RUNTIME_ONLY])
def test_service_imports(name):
    importlib.import_module(f"vfx_nodes.{name}")
