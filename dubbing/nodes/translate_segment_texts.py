# ---------------------------------------------------------------
# translate_segment_texts.py
# Full offline-capable translation node with local (NLLB), Google, Yandex
# ---------------------------------------------------------------
import os
import json
import re
import logging
import traceback
import folder_paths
import time
import shutil
from deep_translator import GoogleTranslator, YandexTranslator

logger = logging.getLogger("TranslateSegmentFiles")

# ---------------------------------------------------------
# RUAccent – для ударений
# ---------------------------------------------------------
RUAccent = None

try:
    from ruaccent import RUAccent
    logger.info("RUAccent imported successfully.")
except Exception as e:
    logger.warning("⚠️ RUAccent not available: %s", e)

# ---------------------------------------------------------
# Локальный переводчик (NLLB via CTranslate2)
# ---------------------------------------------------------
LOCAL_TRANSLATOR_AVAILABLE = False
try:
    import ctranslate2
    from transformers import AutoTokenizer
    LOCAL_TRANSLATOR_AVAILABLE = True
    logger.info("✅ Local translator (ctranslate2 + transformers) available.")
except ImportError as e:
    logger.warning("⚠️ Local translator dependencies missing: %s", e)
except Exception as e:
    logger.error("❌ Error importing local translator: %s", e)

# ---------------------------------------------------------
# Утилиты
# ---------------------------------------------------------
def _remove_punct(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(cleaned.split())

def load_yo_words():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "yo_words.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                words = json.load(f)
                return set(words)
        except Exception as e:
            logger.error("Error loading yo_words.json: %s", e)
    
    # Fallback to a minimal set if file missing
    return {"всё", "моём", "самолёт", "берёза", "ёлка"}

_COMMON_EYO_WORDS = load_yo_words()

def _fix_cyrillic_orthography(text: str) -> str:
    text = re.sub(r"(?<=[\u0400-\u04FF])e(?=[\u0400-\u04FF])", "е", text)
    text = re.sub(r"(?<=[\u0400-\u04FF])i(?=[\u0400-\u04FF])", "и", text)

    def _replace_e_with_yo(word: str) -> str:
        if word.lower() in _COMMON_EYO_WORDS:
            return re.sub("е", "ё", word, count=1)
        return word

    tokens = re.split(r"(\W+)", text)
    tokens = [_replace_e_with_yo(tok) for tok in tokens]
    return "".join(tokens)


# ---------------------------------------------------------
# Класс ноды
# ---------------------------------------------------------
_LANG_NAMES = {"en": "English", "ru": "Russian", "es": "Spanish", "fr": "French", "de": "German", "zh": "Chinese"}


def _ollama_base():
    base = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    return base if base.startswith("http") else "http://" + base


def _ollama_pick_model():
    """KESHIKI_TRANSLATE_MODEL, else the first non-embedding model Ollama has."""
    import urllib.request
    want = os.environ.get("KESHIKI_TRANSLATE_MODEL", "")
    if want:
        return want
    try:
        with urllib.request.urlopen(_ollama_base() + "/api/tags", timeout=3) as r:
            names = [m["name"] for m in json.load(r).get("models", [])]
    except Exception:
        return ""
    names = [n for n in names if "embed" not in n.lower()]
    big = [n for n in names if not any(t in n.lower() for t in ("0.5b", "0.8b", "1b", "1.5b"))]
    return (big or names or [""])[0]


def _ollama_translate(model, text, src, tgt):
    import urllib.request
    prompt = (f"Translate this film dialogue line from {_LANG_NAMES.get(src, src)} to {_LANG_NAMES.get(tgt, tgt)}. "
              "Keep the meaning, tone and roughly the same length so it can be dubbed. "
              "Answer with the translation only, no quotes, no notes.\n\n" + text)
    body = json.dumps({"model": model, "stream": False, "think": False, "options": {"temperature": 0.2},
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(_ollama_base() + "/api/chat", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        out = json.load(r).get("message", {}).get("content", "")
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"«»')
    return out or text


class TranslateSegmentFiles:
    @classmethod
    def INPUT_TYPES(s):
        translator_options = ["ollama", "google", "yandex"]
        if LOCAL_TRANSLATOR_AVAILABLE:
            translator_options.append("local")

        return {
            "required": {
                "segments_json_path": ("STRING", {"default": ""}),
                "source_language": (["en", "ru", "es", "fr", "de", "zh"], {"default": "en"}),
                "target_language": (["ru", "en", "es", "fr", "de", "zh"], {"default": "ru"}),
                "translator": (translator_options, {"default": "local"}),
                "remove_punctuation_before_translation": ("BOOLEAN", {"default": False}),
                "remove_punctuation_after_translation": ("BOOLEAN", {"default": False}),
                "retain_translation_punctuation": ("BOOLEAN", {"default": True}),
                "fix_cyrillic_orthography": ("BOOLEAN", {"default": False}),
                "add_stress": ("BOOLEAN", {"default": False}),
                "stress_engine": (["RUAccent"], {"default": "RUAccent"}),
                "process_now": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "original_video_audio_path": ("STRING", {"default": ""}),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = (
        "translated_text_file_paths",
        "full_translation_path",
        "updated_segments_json_path",
        "project_root_dir",
        "status",
        "item_list",
        "content_list",
        "translated_segments_dir",
    )
    FUNCTION = "translate_files"
    CATEGORY = "audio/video/dubbing"

    def translate_files(
        self,
        segments_json_path,
        source_language,
        target_language,
        translator,
        remove_punctuation_before_translation,
        remove_punctuation_after_translation,
        retain_translation_punctuation,
        fix_cyrillic_orthography,
        add_stress,
        stress_engine,
        process_now,
        original_video_audio_path="",
    ):
        if not process_now:
            return ([], "", "", "", "Set process_now=True to start processing", [], [], "")

        log = [f"🔧 Starting translation ({translator}) {source_language} → {target_language}"]
        if not segments_json_path or not os.path.isfile(segments_json_path):
            return ([], "", "", "", f"Error: segments JSON not found: {segments_json_path}", [], [], "")

        # --- Загрузка JSON ---
        try:
            with open(segments_json_path, "r", encoding="utf-8") as f:
                project_info = json.load(f)
            segments_data = project_info["segments"]
            if not isinstance(segments_data, list):
                raise ValueError("'segments' is not a list")
        except Exception as e:
            log.append(f"❌ JSON error: {e}")
            return ([], "", "", "", "\n".join(log), [], [], "")

        diarization_info = project_info.get("diarization", {})
        speaker_label_style = diarization_info.get("speaker_label_style", "none")

        project_dir = os.path.dirname(segments_json_path)
        text_segments_dir = os.path.join(project_dir, "TextSegments")
        translate_dir = os.path.join(text_segments_dir, "translate")
        
        if os.path.exists(translate_dir):
            try:
                shutil.rmtree(translate_dir)
                log.append(f"🗑️ Cleared previous translations in: {translate_dir}")
            except Exception as e:
                log.append(f"⚠️ Failed to clear translate_dir: {e}")
                
        os.makedirs(translate_dir, exist_ok=True)

        # --- Инициализация переводчика ---
        local_translator = None
        tokenizer = None
        ct_translator = None
        cloud_translator = None

        if translator == "local":
            if not LOCAL_TRANSLATOR_AVAILABLE:
                return ([], "", "", "", "❌ Local translator not available – install ctranslate2 & transformers", [], [], "")

            model_name = "facebook/nllb-200-distilled-600M"
            paths = folder_paths.get_folder_paths("nllb")
            model_cache_dir = paths[0] if paths else os.path.join(folder_paths.MODELS_DIR, "nllb")
            ct_model_path = os.path.join(model_cache_dir, "nllb-200-ct2")

            if not os.path.exists(ct_model_path):
                log.append(f"📥 Converting NLLB model to CTranslate2 format… (one-time)")
                try:
                    from ctranslate2.converters import TransformersConverter
                    converter = TransformersConverter(model_name)
                    converter.convert(ct_model_path, quantization="float16", force=True)
                    log.append("✅ Model converted and cached.")
                except Exception as e:
                    log.append(f"❌ Failed to convert model: {e}")
                    return ([], "", "", "", "\n".join(log), [], [], "")

            try:
                tokenizer = AutoTokenizer.from_pretrained(model_name)
                ct_translator = ctranslate2.Translator(ct_model_path, device="cuda", compute_type="float16")
                log.append("✅ Local NLLB translator ready (GPU, float16)")
            except Exception as e:
                log.append(f"❌ Failed to load local translator: {e}")
                return ([], "", "", "", "\n".join(log), [], [], "")

        elif translator == "ollama":
            # Local LLM through Ollama (KESHIKI default: no cloud, no extra model download)
            ollama_model = _ollama_pick_model()
            if not ollama_model:
                return ([], "", "", "", "❌ Ollama is not running or has no models (ollama pull qwen3:8b)", [], [], "")
            log.append(f"✅ Ollama translator ready ({ollama_model})")

        else:
            # Google / Yandex
            try:
                if translator == "google":
                    cloud_translator = GoogleTranslator(source=source_language, target=target_language)
                else:  # yandex
                    token = os.getenv("YANDEX_TRANSLATE_TOKEN")
                    folder = os.getenv("YANDEX_TRANSLATE_FOLDER")
                    if not token or not folder:
                        raise RuntimeError("Yandex env vars not set")
                    cloud_translator = YandexTranslator(
                        source=source_language,
                        target=target_language,
                        api_key=token,
                        region=folder,
                    )
            except Exception as e:
                log.append(f"❌ Cloud translator init failed: {e}")
                return ([], "", "", "", "\n".join(log), [], [], "")

        # --- Инициализация ударений ---
        accent_obj = None
        accentizer_obj = None
        stress_engine_used = None

        if add_stress and target_language == "ru":
            if stress_engine == "RUAccent" and RUAccent is not None:
                try:
                    accentizer_obj = RUAccent()
                    accentizer_obj.load(omograph_model_size='turbo3.1', use_dictionary=True, tiny_mode=False)
                    stress_engine_used = "RUAccent"
                    log.append("✅ RUAccent loaded")
                except Exception as e:
                    log.append(f"⚠️ RUAccent init failed: {e}")

        # --- Перевод всех сегментов ---
        translation_cache = {}
        translated_paths = []
        full_translation_text = ""
        item_list = []
        content_list = []

        # NLLB language codes
        nllb_lang_map = {
            "en": "eng_Latn",
            "ru": "rus_Cyrl",
            "es": "spa_Latn",
            "fr": "fra_Latn",
            "de": "deu_Latn",
            "zh": "zho_Hans",
        }
        src_lang_nllb = nllb_lang_map.get(source_language, "eng_Latn")
        tgt_lang_nllb = nllb_lang_map.get(target_language, "rus_Cyrl")

        for i, segment in enumerate(segments_data, start=1):
            if not isinstance(segment, dict):
                log.append(f"⚠️ Segment {i} invalid – skipping")
                translated_paths.append(""); item_list.append(""); content_list.append("")
                continue

            idx = segment.get("index", i)
            original_text_with_prefix = segment.get("original_text_with_speaker_prefix", "")
            original_text = segment.get("original_text", "").strip()

            speaker_id = segment.get("speaker_id", "SPEAKER_00")
            
            # If style is 'none', we explicitly want no prefix
            if speaker_label_style == "none":
                speaker_prefix = ""
                text_for_translation = original_text
            elif original_text_with_prefix:
                match = re.search(r'^(\[[0-9]+\]|\{[0-9]+\})\s*', original_text_with_prefix)
                if match:
                    speaker_prefix = match.group(0)
                    text_for_translation = original_text_with_prefix[len(speaker_prefix):].strip()
                else:
                    speaker_prefix = ""
                    text_for_translation = original_text
            else:
                speaker_prefix = ""
                text_for_translation = original_text

            if not text_for_translation:
                translated_paths.append(""); item_list.append(""); content_list.append("")
                full_translation_text += " "
                continue

            source_for_translation = _remove_punct(text_for_translation) if remove_punctuation_before_translation else text_for_translation

            if source_for_translation in translation_cache:
                raw_translation = translation_cache[source_for_translation]
                log.append(f"   Cache hit for segment {idx}")
            else:
                try:
                    if translator == "local":
                        tokenizer.src_lang = src_lang_nllb
                        inputs = tokenizer([source_for_translation], return_tensors="pt", padding=True)
                        
                        # --- КЛЮЧЕВОЕ ИЗМЕНЕНИЕ ---
                        input_ids = inputs.input_ids[0]
                        input_tokens = tokenizer.convert_ids_to_tokens(input_ids)
                        # Удаляем pad-токены (если есть)
                        input_tokens = [t for t in input_tokens if t != tokenizer.pad_token]

                        results = ct_translator.translate_batch(
                            [input_tokens],  # ← List[List[str]]
                            target_prefix=[[tgt_lang_nllb]],
                            beam_size=4,
                            length_penalty=0.1,
                        )
                        translated_tokens = results[0].hypotheses[0]

                        # Собираем обратно в строку
                        raw_translation = tokenizer.convert_tokens_to_string(translated_tokens)

                        # --- УДАЛЯЕМ ТОКЕН ЯЗЫКА ИЗ НАЧАЛА ---
                        if raw_translation.startswith(tgt_lang_nllb):
                            raw_translation = raw_translation[len(tgt_lang_nllb):].strip()

                    elif translator == "ollama":
                        raw_translation = _ollama_translate(ollama_model, source_for_translation, source_language, target_language)
                    else:
                        max_retries = 3
                        for attempt in range(max_retries):
                            try:
                                raw_translation = cloud_translator.translate(source_for_translation)
                                break
                            except Exception as e:
                                if attempt < max_retries - 1:
                                    log.append(f"   ⚠️ Cloud translation retry {attempt+1}/{max_retries} due to: {e}")
                                    time.sleep(2.0)
                                else:
                                    raise e
                    
                    translation_cache[source_for_translation] = raw_translation
                    log.append(f"   ✅ Translated segment {idx}")
                except Exception as e:
                    log.append(f"⚠️ Translation failed for segment {idx}: {e}")
                    raw_translation = source_for_translation

            if fix_cyrillic_orthography:
                raw_translation = _fix_cyrillic_orthography(raw_translation)

            if stress_engine_used == "RUAccent" and accentizer_obj:
                try:
                    raw_translation = accentizer_obj.process_all(raw_translation)
                except Exception as e:
                    log.append(f"⚠️ RUAccent failed on segment {idx}: {e}")

            # --- Пунктуация после перевода ---
            translated_text = raw_translation
            if remove_punctuation_after_translation:
                translated_text = _remove_punct(translated_text)
            if not retain_translation_punctuation:
                translated_text = _remove_punct(translated_text)

            # --- Сохранение ---
            translated_filename = f"txtsegments_{target_language}_{idx:03d}.txt"
            translated_file_path = os.path.join(translate_dir, translated_filename)
            full_translated_with_prefix = speaker_prefix + translated_text

            try:
                with open(translated_file_path, "w", encoding="utf-8") as f:
                    f.write(full_translated_with_prefix)
            except Exception as e:
                log.append(f"⚠️ Write failed for segment {idx}: {e}")

            translated_paths.append(translated_file_path)
            item_list.append(translated_file_path)
            content_list.append(translated_text)
            full_translation_text += full_translated_with_prefix + " "

            segment["translated_text"] = full_translated_with_prefix
            segment["translated_text_file_path"] = translated_file_path

        # --- Full translation file ---
        full_translation_path = ""
        if full_translation_text.strip():
            full_translation_path = os.path.join(project_dir, f"full_translation_{target_language}.txt")
            try:
                with open(full_translation_path, "w", encoding="utf-8") as f:
                    f.write(full_translation_text.strip())
                log.append("✅ Full translation saved")
            except Exception as e:
                log.append(f"⚠️ Full translation write failed: {e}")

        # --- Save updated JSON ---
        project_info["target_language"] = target_language
        try:
            with open(segments_json_path, "w", encoding="utf-8") as f:
                json.dump(project_info, f, ensure_ascii=False, indent=2)
            log.append("✅ segments.json updated")
        except Exception as e:
            log.append(f"⚠️ segments.json write failed: {e}")

        log.append(f"✅ Translation complete – {len(translated_paths)} segments")
        status = "\n".join(log)

        return (
            translated_paths,
            full_translation_path,
            segments_json_path,
            project_dir,
            status,
            item_list,
            content_list,
            translate_dir,
        )


NODE_CLASS_MAPPINGS = {"TranslateSegmentFiles": TranslateSegmentFiles}
NODE_DISPLAY_NAME_MAPPINGS = {"TranslateSegmentFiles": "Translate Segment Files"}