import os
import json

# Root directory of the dubbing engine code (KESHIKI: <repo>/dubbing)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Working data (projects, temp) and the models can live elsewhere: KESHIKI passes them in.
DATA_DIR = os.environ.get("KESHIKI_DUBBING_DATA") or BASE_DIR

def get_config():
    """Load app_config.json if it exists."""
    config_path = os.path.join(BASE_DIR, "config", "app_config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def get_input_directory():
    path = os.path.join(DATA_DIR, "input")
    os.makedirs(path, exist_ok=True)
    return path

def get_output_directory():
    path = os.path.join(DATA_DIR, "output")
    os.makedirs(path, exist_ok=True)
    return path

def get_temp_directory():
    path = os.path.join(DATA_DIR, "temp")
    os.makedirs(path, exist_ok=True)
    return path

# In ComfyUI, models_dir is often used as a base path for models
MODELS_DIR = os.environ.get("KESHIKI_DUBBING_MODELS") or os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)

# LOAD CUSTOM PATHS
_config = get_config()
_custom_model_paths = _config.get("model_paths", {})

# Helper to get path (prioritizing custom)
def _get_p(key, default_subfolder):
    custom = _custom_model_paths.get(key)
    if custom and os.path.isdir(custom):
        return custom
    return os.path.join(MODELS_DIR, default_subfolder)

# Common ComfyUI folder mappings
_folder_mappings = {
    "checkpoints": [_get_p("f5-tts", "F5-TTS"), MODELS_DIR], # F5-TTS looks for 'F5-TTS' subfolder here
    "vibevoice": [_get_p("vibevoice", "vibevoice")],
    "f5-tts": [_get_p("f5-tts", "F5-TTS")],
    "qwen": [_get_p("qwen", "Qwen")],
    "whisper": [_get_p("whisper", "Whisper")],
    "nllb": [_get_p("nllb", "nllb")],
}

def get_folder_paths(name):
    # Special handle for 'Whisper' (case sensitivity)
    if name.lower() == "whisper":
        return _folder_mappings.get("whisper", [])
    return _folder_mappings.get(name, [])

def get_full_path(folder_name, filename):
    paths = get_folder_paths(folder_name)
    for p in paths:
        full = os.path.join(p, filename)
        if os.path.exists(full):
            return full
    
    # Fallback default
    base = _get_p(folder_name.lower(), folder_name)
    return os.path.join(base, filename)

def get_annotated_filepath(filename):
    # If it's already an absolute path, return it
    if os.path.isabs(filename):
        return filename
    return os.path.join(get_input_directory(), filename)

supported_pt_extensions = {".ckpt", ".pt", ".bin", ".pth", ".safetensors"}

def filter_files_content_types(files, types):
    # Simplified filter for the mock
    return files
