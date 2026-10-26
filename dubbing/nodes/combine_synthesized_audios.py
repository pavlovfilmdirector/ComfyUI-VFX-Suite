# combine_synthesized_audios.py
# ---------------------------------------------------------------
# Узел, собирающий (до)5 аудио‑фрагментов в один файл.
# Вариант: 1) сразу склеить – будет единственный вывод (AUDIO)
#          2) дополнительно вернуть список всех входов (AUDIO_LIST)
# ---------------------------------------------------------------
import torch
import numpy as np
import logging
import folder_paths

logger = logging.getLogger("CombineSynthesizedAudios")

class CombineSynthesizedAudiosNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # Пять входов – каждый может быть пустым (если часть не нужна)
                "audio_1": ("AUDIO", {"tooltip": "First synthesized audio (or empty)"}),
                "audio_2": ("AUDIO", {"tooltip": "Second synthesized audio (or empty)"}),
                "audio_3": ("AUDIO", {"tooltip": "Third synthesized audio (or empty)"}),
                "audio_4": ("AUDIO", {"tooltip": "Fourth synthesized audio (or empty)"}),
                "audio_5": ("AUDIO", {"tooltip": "Fifth synthesized audio (or empty)"}),
                # Нужно ли склеивать? (True – получаем один WAV,
                # False – получаем только список без объединения)
                "concatenate": ("BOOLEAN", {"default": True, "label_on": "Concat", "label_off": "No concat"}),
                # Сохранять результат рядом с оригиналом? (по‑умолчанию – сохраняем в output‑folder)
                "save_output": ("BOOLEAN", {"default": True}),
                # Флаг запуска
                "process_now": ("BOOLEAN", {"default": False}),
            }
        }
    
    # -----------------------------------------------------------
    # 7 выходов:
    #   1‑5 – оригинальные аудио‑фрагменты (передаются «как есть», иногда удобно)
    #   6   – склеенный аудио‑слушок (если concatenate=True)
    #   7   – статус/лог
    # -----------------------------------------------------------
    RETURN_TYPES = (
        "AUDIO",   # audio_1 (pass‑through)
        "AUDIO",   # audio_2
        "AUDIO",   # audio_3
        "AUDIO",   # audio_4
        "AUDIO",   # audio_5
        "AUDIO",   # concatenated (may be empty)
        "STRING",  # status
    )
    RETURN_NAMES = (
        "audio_1",
        "audio_2",
        "audio_3",
        "audio_4",
        "audio_5",
        "combined_audio",
        "status",
    )
    FUNCTION = "combine"
    CATEGORY = "VideoDubbing"

    # -----------------------------------------------------------
    def combine(self,
                audio_1, audio_2, audio_3, audio_4, audio_5,
                concatenate,
                save_output,
                process_now):

        # ------------------- 0️⃣ Флаг запуска -------------------
        if not process_now:
            # Возвращаем «пустые» аудио‑словарики, чтобы граф не «сломался»
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, empty, empty, empty, empty, empty,
                    "Set process_now=True to start processing")

        log = ["🔧 Starting CombineSynthesizedAudiosNode..."]
        logger.info("\n".join(log))

        # ------------------- 1️⃣ Сбор всех входов в список -------------------
        inputs = [audio_1, audio_2, audio_3, audio_4, audio_5]

        # Функция‑помощник: проверка валидности аудио‑словаря
        def is_valid_audio(a):
            return (a is not None and
                    isinstance(a, dict) and
                    "waveform" in a and "sample_rate" in a)

        # ------------------- 2️⃣ Приводим каждый фрагмент к pydub.AudioSegment -------------------
        from pydub import AudioSegment

        pydub_segments = []          # список готовых AudioSegment‑ов
        for idx, a in enumerate(inputs, start=1):
            if not is_valid_audio(a):
                log.append(f"⚠️ audio_{idx} пустой/некорректный – будет пропущен.")
                pydub_segments.append(None)
                continue

            # waveform – Tensor [1,1,T]  (может быть float32 в диапазоне [-1,1])
            wav = a["waveform"].squeeze(0).squeeze(0).cpu().numpy()
            sr = a["sample_rate"]

            # Приводим к int16, как делают остальные узлы
            wav_int16 = (np.clip(wav, -1, 1) * 32767).astype(np.int16)

            seg = AudioSegment(
                data=wav_int16.tobytes(),
                sample_width=2,
                frame_rate=sr,
                channels=1,
            )
            pydub_segments.append(seg)
            log.append(f"✅ audio_{idx} – длина {len(seg) / 1000:.2f}s, SR={sr}")

        # ------------------- 3️⃣ Если требуется склейка -------------------
        combined_audio_dict = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
        combined_path = ""

        if concatenate:
            # Оставляем только НЕ‑None сегменты
            valid_segments = [s for s in pydub_segments if s is not None]

            if not valid_segments:
                log.append("⚠️ Нет ни одного валидного аудио‑фрагмента – combined_audio будет пустым.")
                combined_audio = AudioSegment.silent(duration=0, frame_rate=24000)
            else:
                # Склеиваем последовательно
                combined_audio = valid_segments[0]
                for seg in valid_segments[1:]:
                    combined_audio += seg

            # ------------------- 3.1 → Конверсия обратно к ComfyUI‑формату -------------------
            combined_samples = np.array(combined_audio.get_array_of_samples())
            # Приводим к float32 в диапазоне [-1, 1]
            combined_float = combined_samples.astype(np.float32) / 32768.0
            combined_waveform = torch.from_numpy(combined_float).unsqueeze(0).unsqueeze(0)

            combined_audio_dict = {
                "waveform": combined_waveform,
                "sample_rate": combined_audio.frame_rate,
            }

            log.append(f"✅ Combined audio length: {len(combined_audio) / 1000:.2f}s")

            # ------------------- 3.2 Сохранение (если включено) -------------------
            if save_output:
                # Сохраняем рядом с текущим проектом (в output‑folder)
                output_dir = folder_paths.get_output_directory()
                fname = f"combined_synth_{uuid.uuid4().hex[:8]}.wav"
                combined_path = os.path.join(output_dir, fname)
                try:
                    combined_audio.export(combined_path, format="wav")
                    log.append(f"💾 Combined audio saved to: {combined_path}")
                except Exception as e:
                    log.append(f"⚠️ Failed to save combined audio: {e}")

        else:
            log.append("ℹ️ concatenate=False – combined_audio оставляем пустым.")
            combined_path = ""

        # ------------------- 4️⃣ Формируем выходные переменные -------------------
        # Если входов было пусто, возвращаем «пустой» аудио‑словарик,
        # чтобы граф не «падал».
        def empty_audio():
            return {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}

        # Прокидываем оригинальные входы дальше (удобно, если кто‑то хочет их ещё использовать)
        outs = [
            inputs[0] if is_valid_audio(inputs[0]) else empty_audio(),
            inputs[1] if is_valid_audio(inputs[1]) else empty_audio(),
            inputs[2] if is_valid_audio(inputs[2]) else empty_audio(),
            inputs[3] if is_valid_audio(inputs[3]) else empty_audio(),
            inputs[4] if is_valid_audio(inputs[4]) else empty_audio(),
            combined_audio_dict,
            "\n".join(log)
        ]

        return tuple(outs)


# ----------------------------------------------------------------
# Регистрация узла
# ----------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "CombineSynthesizedAudiosNode": CombineSynthesizedAudiosNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CombineSynthesizedAudiosNode": "Combine Synthesized Audios (max 5 parts)"
}
