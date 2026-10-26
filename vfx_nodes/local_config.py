"""
Machine-specific paths (keshiki_local.json in the repo root, written by install.py; never committed).

Optional external tools live outside the repo and differ per machine, e.g.:
    {"hunyuan3d_root": "D:/Hunyuan3D-Omni", "hunyuan3d_python": "D:/envs/h3d/python.exe",
     "trellis_root": "D:/TRELLIS.2", "trellis_python": "D:/envs/trellis/python.exe",
     "ollama_models": "D:/Ollama/models"}
Each key can also come from an environment variable KESHIKI_<KEY upper-case>.
"""
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(BASE_DIR, "keshiki_local.json")


def local_setting(key: str, default: str = "") -> str:
    env = os.environ.get("KESHIKI_" + key.upper())
    if env:
        return env
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            val = json.load(f).get(key)
        if val:
            return str(val)
    except (OSError, ValueError):
        pass
    return default


def cloud_services_allowed() -> bool:
    """KESHIKI runs on local open-weights models. Cloud APIs (partner nodes, Replicate/Runway/Kling/ElevenLabs...,
    OpenAI/Claude/Gemini as the agent, edge-tts) stay off unless the user opts in: KESHIKI_ALLOW_CLOUD=1
    or "allow_cloud": true in keshiki_local.json."""
    return local_setting("allow_cloud", "").strip().lower() in ("1", "true", "yes")
