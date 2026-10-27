"""
Refresh the node registry the tests check workflows against.

    python tools/refresh_object_info.py [--url http://127.0.0.1:8188] [--from saved_object_info.json]

Reads GET /object_info from a running ComfyUI (with all KESHIKI node packs installed) and writes
  tests/fixtures/object_info.json.gz  - slim: node inputs/outputs, enum values, model file lists removed (committed)
  tests/_object_info_full.json        - the full answer incl. this machine's model files (not committed);
                                        enables test_template_models_installed_here.
"""
import argparse
import gzip
import json
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# lists of these are machine-specific (models, the user's input files, fonts) and are dropped from the fixture
FILE_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".sft", ".yaml", ".json",
            ".png", ".jpg", ".jpeg", ".webp", ".exr", ".mp4", ".mov", ".webm", ".mkv", ".wav", ".mp3", ".flac",
            ".ttf", ".otf")


def _combo(spec):
    """(is_combo, options) for both the old ([options], {...}) and new ("COMBO", {"options": [...]}) formats."""
    if isinstance(spec, list) and spec:
        if isinstance(spec[0], list):
            return True, spec[0]
        if spec[0] == "COMBO":
            meta = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            return True, meta.get("options") or []
    return False, None


def slim(full):
    out = {}
    for cls, info in full.items():
        inputs = {}
        req = (info.get("input") or {}).get("required") or {}
        opt = (info.get("input") or {}).get("optional") or {}
        for group, required in ((req, True), (opt, False)):
            for name, spec in group.items():
                is_combo, options = _combo(spec)
                if is_combo:
                    files = "font" in name.lower() or any(isinstance(o, str) and o.lower().endswith(FILE_EXT) for o in options)
                    item = {"type": "COMBO", "options": None if files else options, "required": required}
                    if files:
                        item["files"] = True
                else:
                    item = {"type": spec[0] if isinstance(spec, list) and spec else str(spec), "required": required}
                inputs[name] = item
        out[cls] = {"inputs": inputs, "outputs": list(info.get("output") or [])}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8188")
    ap.add_argument("--from", dest="src", default="")
    a = ap.parse_args()
    if a.src:
        full = json.load(open(a.src, encoding="utf-8"))
    else:
        with urllib.request.urlopen(a.url.rstrip("/") + "/object_info", timeout=120) as r:
            full = json.loads(r.read().decode("utf-8"))
    with open(os.path.join(ROOT, "tests", "_object_info_full.json"), "w", encoding="utf-8") as f:
        json.dump(full, f)
    with gzip.open(os.path.join(ROOT, "tests", "fixtures", "object_info.json.gz"), "wt", encoding="utf-8") as f:
        json.dump(slim(full), f, separators=(",", ":"))
    print(f"{len(full)} node classes written")


if __name__ == "__main__":
    main()
