# cache_synthesized_audio.py
# ---------------------------------------------------------------
# Узел кеширования синтезированного аудио.
# Если enable_cache=True и аудио закешировано, возвращает путь к файлу.
# В противном случае, возвращает пустой путь, сигнализируя о необходимости генерации.
# ---------------------------------------------------------------
import os
import hashlib
import json
import uuid
import torch
import numpy as np
import logging
import folder_paths
from pydub import AudioSegment
import traceback

logger = logging.getLogger("CacheSynthesizedAudio")

class CacheSynthesizedAudioNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "input_text": ("STRING", {"default": "", "multiline": True}),
                "enable_cache": ("BOOLEAN", {"default": True,
                                            "label_on": "Cache ON",
                                            "label_off": "Cache OFF"}),
                "process_now": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                # Необязательный вход для уже сгенерированного аудио (если нужно закешировать)
                "generated_audio_to_cache": ("AUDIO", {"tooltip": "Audio output of VibeVoice to cache"}),
            }
        }
        
    # -----------------------------------------------------------
    # 5 выходов:
    #   1. cached_audio (если есть) – словарь с аудио из кеша
    #   2. cached_file_path – путь к wav-файлу в кеше (или пустая строка)
    #   3. text_hash – SHA-256 хеш исходного текста
    #   4. status – статус-лог
    #   5. needs_generation – BOOLEAN, True если кэш не найден и нужно генерировать
    # -----------------------------------------------------------
    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "STRING", "BOOLEAN")
    RETURN_NAMES = ("cached_audio", "cached_file_path", "text_hash", "status", "needs_generation")
    FUNCTION = "run"
    CATEGORY = "VideoDubbing"

    # -----------------------------------------------------------
    def run(self, input_text, enable_cache, process_now, generated_audio_to_cache=None):
        # 0️⃣ Флаг запуска
        if not process_now:
            empty_audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty_audio, "", "", "Set process_now=True to start", False)

        # 1️⃣ Вычисляем хеш текста (единственный ключ кеша)
        text_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()

        # Папка кеша
        cache_dir = os.getenv("AUDIO_CACHE_DIR",
                              os.path.join(folder_paths.get_input_directory(),
                                           "AudioCache"))
        os.makedirs(cache_dir, exist_ok=True)

        cache_path = os.path.join(cache_dir, f"{text_hash}.wav")

        # -------------------------------------------------------
        # 2️⃣ Если кеш выключен – всегда генерируем
        # -------------------------------------------------------
        if not enable_cache:
            logger.info("Cache is disabled. Process will proceed to generation.")
            empty_audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty_audio, "", text_hash, "Cache disabled – proceed to generation.", True)

        # -------------------------------------------------------
        # 3️⃣ Кеш включён – проверяем, есть ли уже файл
        # -------------------------------------------------------
        if os.path.isfile(cache_path):
            try:
                # Читаем wav-файл из кеша
                cached_seg = AudioSegment.from_wav(cache_path)
                wav = np.array(cached_seg.get_array_of_samples())
                wav_f = wav.astype(np.float32) / 32768.0
                tensor = torch.from_numpy(wav_f).unsqueeze(0).unsqueeze(0)

                cached_audio = {
                    "waveform": tensor,
                    "sample_rate": cached_seg.frame_rate,
                }
                logger.info(f"Cache hit for text hash {text_hash}. Loading from {cache_path}")
                # Возвращаем кешированное аудио и False (не нужно генерировать)
                return (cached_audio, cache_path, text_hash, f"✅ Loaded from cache: {cache_path}", False)
            except Exception as e:
                logger.warning(f"Failed to read cached file {cache_path}: {e}")
                # Если чтение не удалось – будем создавать новый кеш или генерировать

        # -------------------------------------------------------
        # 4️⃣ Нет кеша или не удалось прочитать.
        #    Проверяем, нужно ли кешировать новое аудио.
        #    Возвращаем False для cached_audio (или пустой), True для needs_generation.
        # -------------------------------------------------------

        # Если на вход подано generated_audio_to_cache, и кеш не нашелся, значит его нужно сохранить
        if generated_audio_to_cache and "waveform" in generated_audio_to_cache and "sample_rate" in generated_audio_to_cache:
            # Это вызов происходит *после* генерации, и мы кешируем результат
            try:
                wav = generated_audio_to_cache["waveform"].squeeze(0).squeeze(0).cpu().numpy()
                sr = generated_audio_to_cache["sample_rate"]
                wav_int16 = (np.clip(wav, -1, 1) * 32767).astype(np.int16)

                seg = AudioSegment(
                    data=wav_int16.tobytes(),
                    sample_width=2,
                    frame_rate=sr,
                    channels=1,
                )
                seg.export(cache_path, format="wav")
                logger.info(f"Caching generated audio to {cache_path}")
                # Возвращаем кешированное аудио (то же, что и на входе) и False (т.к. теперь оно закешировано)
                # Это позволяет использовать кешированное аудио в следующем вызове
                return (generated_audio_to_cache, cache_path, text_hash, f"✅ Cached new audio: {cache_path}", False)
            except Exception as e:
                logger.error(f"Failed to cache new audio: {e}")
                # Даже если кеширование не удалось, мы всё равно говорим, что генерация была
                # и результат нужно использовать (но не кешировать)
                empty_audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
                return (empty_audio, "", text_hash, f"❌ Failed to cache: {e}", False) # Или True, если следующий шаг всё равно нужен?
        else:
            # Кеш не найден, и на вход не подано аудио для кеширования.
            # Это означает, что вызов происходит *до* генерации.
            # Возвращаем пустое аудио, путь к кешу (пустой), хеш, статус и True (нужно генерировать)
            logger.info(f"No cache found for text hash {text_hash}. Process needs to generate audio.")
            empty_audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty_audio, "", text_hash, f"Cache miss for {text_hash}. Needs generation.", True)


# ----------------------------------------------------------------------
# Регистрация ноды
# ----------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "CacheSynthesizedAudioNode": CacheSynthesizedAudioNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CacheSynthesizedAudioNode": "Cache Synthesized Audio (Conditional)"
}
