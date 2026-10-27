"""
Test harness for KESHIKI (runs without ComfyUI, without a GPU).

* ComfyUI-only modules (folder_paths, comfy.model_management, torch when absent) are replaced by small stubs, so
  every vfx_nodes service imports in plain Python: `python -m pytest tests` from the repo root.
* `project` fixture: a temporary project folder; every service's get_project_dir points there.
* `comfy_dirs` fixture: temporary ComfyUI input / output folders.
* GPU / live-server tests live in tools/smoke_test.py (opt-in), not here.
"""
import importlib
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _stub(name, **attrs):
    m = types.ModuleType(name)
    m.__dict__.update(attrs)
    sys.modules[name] = m
    return m


# --- folder_paths (ComfyUI) -------------------------------------------------------------------------------
_FP_STATE = {"input": os.path.join(ROOT, "tests", "_tmp_input"), "output": os.path.join(ROOT, "tests", "_tmp_output"),
             "files": {}}
if "folder_paths" not in sys.modules:
    _stub("folder_paths",
          get_input_directory=lambda: _FP_STATE["input"],
          get_output_directory=lambda: _FP_STATE["output"],
          get_temp_directory=lambda: _FP_STATE["output"],
          get_filename_list=lambda kind: list(_FP_STATE["files"].get(kind, [])),
          get_full_path=lambda kind, name: None,
          models_dir=os.path.join(ROOT, "tests", "_tmp_models"),
          folder_names_and_paths={})

# --- comfy.model_management ---------------------------------------------------------------------------------
if "comfy" not in sys.modules:
    comfy = _stub("comfy")
    mm = _stub("comfy.model_management", unload_all_models=lambda: None, soft_empty_cache=lambda *a, **k: None,
               EXTRA_RESERVED_VRAM=0, get_torch_device=lambda: "cpu")
    comfy.model_management = mm

# --- torch (only when it is not installed: the services import it for tensors they never touch in tests) -----
try:
    import torch  # noqa: F401
except ImportError:
    t = _stub("torch", Tensor=object, float32="float32", cuda=types.SimpleNamespace(is_available=lambda: False,
                                                                                     empty_cache=lambda: None))
    t.from_numpy = lambda a: a
    t.no_grad = lambda: types.SimpleNamespace(__enter__=lambda s: None, __exit__=lambda *a: False)


@pytest.fixture
def comfy_dirs(tmp_path):
    _FP_STATE["input"] = str(tmp_path / "input")
    _FP_STATE["output"] = str(tmp_path / "output")
    os.makedirs(_FP_STATE["input"], exist_ok=True)
    os.makedirs(_FP_STATE["output"], exist_ok=True)
    _FP_STATE["files"] = {}
    yield _FP_STATE


SERVICES = ["project_manager", "process_runner", "local_config", "video_layers_service", "image_generate_service",
            "dubbing_service", "voice_board_service", "narration_service", "music_service", "layers_service", "user_tools_service"]


def load(name):
    return importlib.import_module(f"vfx_nodes.{name}")


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throw-away project folder; all services resolve projects there."""
    root = tmp_path / "proj"
    root.mkdir()

    def gpd(pid):
        d = root / (pid or "sandbox")
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    for name in SERVICES:
        try:
            mod = load(name)
        except Exception:  # noqa: BLE001 - a module that cannot import is reported by test_imports
            continue
        if hasattr(mod, "get_project_dir"):
            monkeypatch.setattr(mod, "get_project_dir", gpd)
    return types.SimpleNamespace(pid="testproj", dir=gpd("testproj"), gpd=gpd, root=root)


# --- a fake ComfyUI queue -----------------------------------------------------------------------------------
class FakeComfy:
    """Stands in for ComfyUI's /prompt + /history. Every queued prompt goes through the real template loader and
    the strict param injector (unknown "Title.input" keys raise), then each Save* node yields a real small file."""

    def __init__(self, dirs):
        self.dirs = dirs
        self.runs = []

    def run_process(self, process_id, template=None, params=None, prompt=None, client_id="", run_tag=None):
        pr = load("process_runner")
        if prompt is None:
            prompt = pr.load_template(process_id, template)
        prompt = pr.inject_params(prompt, params or {})
        pid = f"p{len(self.runs)}"
        self.runs.append({"process": process_id, "template": template, "prompt": prompt, "params": params or {}, "id": pid})
        return {"prompt_id": pid}

    def _outputs(self, prompt):
        import cv2
        import numpy as np
        outs = []
        os.makedirs(self.dirs["output"], exist_ok=True)
        for nid, n in prompt.items():
            ct = n["class_type"]
            if ct in ("SaveAudio", "SaveAudioMP3", "SaveAudioOpus"):
                name = f"{nid}.mp3"
                open(os.path.join(self.dirs["output"], name), "wb").write(b"ID3fake")
                outs.append({"node": nid, "kind": "audio", "filename": name, "subfolder": "", "type": "output"})
            elif ct == "SaveVideo":
                name = f"{nid}.mp4"
                make_video(os.path.join(self.dirs["output"], name), frames=9)
                outs.append({"node": nid, "kind": "images", "filename": name, "subfolder": "", "type": "output"})
            elif ct in ("SaveImage", "PreviewImage"):
                name = f"{nid}.png"
                cv2.imwrite(os.path.join(self.dirs["output"], name), np.full((64, 96, 3), 128, np.uint8))
                outs.append({"node": nid, "kind": "images", "filename": name, "subfolder": "", "type": "output"})
        return outs

    def get_run_status(self, prompt_id):
        run = next(r for r in self.runs if r["id"] == prompt_id)
        return {"prompt_id": prompt_id, "status": "done", "outputs": self._outputs(run["prompt"]), "errors": []}

    def patch(self, monkeypatch, module):
        monkeypatch.setattr(module, "run_process", self.run_process, raising=False)
        monkeypatch.setattr(module, "get_run_status", self.get_run_status, raising=False)
        return self


def make_video(path, frames=24, w=96, h=64, fps=24, color=None):
    import cv2
    import numpy as np
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(frames):
        img = np.zeros((h, w, 3), np.uint8)
        img[:] = color if color is not None else (i * 10 % 255, 80, 160)
        vw.write(img)
    vw.release()
    return path


def make_wav(path, seconds=1.0, sr=16000, freq=440.0):
    import wave
    import numpy as np
    t = np.arange(int(seconds * sr)) / sr
    data = (np.sin(2 * np.pi * freq * t) * 12000).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(data)
    return path


@pytest.fixture
def fake_comfy(comfy_dirs):
    return FakeComfy(comfy_dirs)
