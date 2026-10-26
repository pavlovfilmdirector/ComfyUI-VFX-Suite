# detect_and_split_synth_audio.py
# ---------------------------------------------------------------
# Узел, который
#   1) ищет паузы в синтезированном аудио,
#   2) сравнивает их количество с оригинальными паузами,
#   3) (по желанию) корректирует несовпадения,
#   4) разбивает аудио на сегменты и отдаёт их дальше.
# ---------------------------------------------------------------
import os
import json
import uuid
import torch
import numpy as np
import logging
import folder_paths
from pydub import AudioSegment
from pydub.silence import detect_silence

logger = logging.getLogger("DetectAndSplitSynthAudio")

class DetectAndSplitSynthAudioNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # Исходный комбинированный WAV‑словарь, полученный от CombineSynthesizedAudiosNode
                "combined_audio": ("AUDIO", {"tooltip": "Combined synthesized audio"}),

                # Путь к JSON‑файлу, где хранится массив оригинальных пауз
                "segments_json_path": ("STRING", {"default": ""}),

                # Параметры детекции пауз (можно экспериментировать)
                "silence_threshold_db": ("FLOAT", {"default": -40.0, "min": -100, "max": 0, "step": 1}),
                "min_silence_duration_ms": ("INT", {"default": 300, "min": 50, "max": 5000, "step": 10}),

                # Как реагировать, если количество найденных пауз НЕ равно оригинальному?
                #   warning_only – только предупредить, но всё‑равно делить.
                #   force_match  – попытаться скорректировать (добавить/удалить паузы‑заполнители).
                #   abort        – остановить работу и вернуть пустой список.
                "mismatch_policy": (["warning_only", "force_match", "abort"],
                                    {"default": "warning_only"}),

                "process_now": ("BOOLEAN", {"default": False}),
            }
        }
        
    # -----------------------------------------------------------
    # Выходы
    # -----------------------------------------------------------
    # 1‑5 – отдельные аудио‑сегменты (AUDIO) – если их меньше 5 – возвращаем пустой токен
    # 6 – список всех сегментов (STRING_LIST) – путь к временным wav‑файлам (для отладки)
    # 7 – статус‑лог
    # -----------------------------------------------------------
    RETURN_TYPES = ("AUDIO", "AUDIO", "AUDIO", "AUDIO", "AUDIO",
                    "STRING_LIST", "STRING")
    RETURN_NAMES = ("seg_1", "seg_2", "seg_3", "seg_4", "seg_5",
                    "seg_files", "status")
    FUNCTION = "detect_and_split"
    CATEGORY = "VideoDubbing"

    # -----------------------------------------------------------
    def detect_and_split(self,
                         combined_audio,
                         segments_json_path,
                         silence_threshold_db,
                         min_silence_duration_ms,
                         mismatch_policy,
                         process_now):

        if not process_now:
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, empty, empty, empty, empty, [], "Set process_now=True to start")

        log = ["🔧 DetectAndSplitSynthAudioNode started."]
        logger.info("\n".join(log))

        # ------------------- 1️⃣ Проверка входов -----------------
        if not os.path.exists(segments_json_path):
            log.append(f"❌ segments_json_path does not exist: {segments_json_path}")
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, empty, empty, empty, empty, [], "\n".join(log))

        # ------------------- 2️⃣ Структура original pauses ----------
        try:
            with open(segments_json_path, "r", encoding="utf-8") as f:
                proj = json.load(f)
            orig_pauses = proj.get("orig_pauses", [])
            if not isinstance(orig_pauses, list):
                raise ValueError("'orig_pauses' must be a list")
            orig_pause_cnt = len(orig_pauses)
            log.append(f"✅ Loaded original pauses count: {orig_pause_cnt}")
        except Exception as e:
            log.append(f"❌ Failed to read original pauses: {e}")
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, empty, empty, empty, empty, [], "\n".join(log))

        # ------------------- 3️⃣ Трансформируем ComfyUI‑audio → pydub ----------
        try:
            wav = combined_audio["waveform"].squeeze(0).squeeze(0).cpu().numpy()
            sr = combined_audio["sample_rate"]

            wav_int16 = (np.clip(wav, -1, 1) * 32767).astype(np.int16)
            synth_seg = AudioSegment(
                data=wav_int16.tobytes(),
                sample_width=2,
                frame_rate=sr,
                channels=1,
            )
            log.append(f"✅ Combined audio length: {len(synth_seg) / 1000:.2f}s")
        except Exception as e:
            log.append(f"❌ Failed to convert combined audio to pydub: {e}")
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, empty, empty, empty, empty, [], "\n".join(log))

        # ------------------- 4️⃣ Поиск пауз в синтезе ----------
        try:
            silent_ranges = detect_silence(
                synth_seg,
                min_silence_len=min_silence_duration_ms,
                silence_thresh=silence_threshold_db,
            )
            synth_pauses = [
                {"start_s": s / 1000.0, "end_s": e / 1000.0,
                 "duration_s": (e - s) / 1000.0}
                for s, e in silent_ranges
            ]
            synth_pause_cnt = len(synth_pauses)
            log.append(f"🔍 Detected {synth_pause_cnt} pauses in synthesized audio.")
        except Exception as e:
            log.append(f"❌ Silence detection failed on synthesized audio: {e}")
            synth_pauses = []
            synth_pause_cnt = 0

        # ------------------- 5️⃣ Сравнение количества -----------------
        if synth_pause_cnt != orig_pause_cnt:
            msg = (f"⚠️ Pause count mismatch: original={orig_pause_cnt} "
                   f"vs synthesized={synth_pause_cnt}.")
            log.append(msg)

            if mismatch_policy == "abort":
                log.append("🛑 abort policy selected – returning empty segments.")
                empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
                return (empty, empty, empty, empty, empty, [], "\n".join(log))

            if mismatch_policy == "force_match":
                log.append("🔧 force_match policy – will adjust pauses to match original count.")
                # -----------------------------------------------------------
                #   Алгоритм «force_match»:
                #   • Если найдено меньше пауз, добавляем искусственные тишины
                #     (по 300 мс) до тех пор, пока их количество не сравняется.
                #   • Если найдено больше пауз, отбрасываем лишние в конце.
                # -----------------------------------------------------------
                if synth_pause_cnt < orig_pause_cnt:
                    # Добавляем недостающие паузы в конец синтезированного аудио
                    needed = orig_pause_cnt - synth_pause_cnt
                    log.append(f"   Adding {needed} artificial pauses (300 ms each) at the end.")
                    filler = AudioSegment.silent(duration=300 * needed,
                                                frame_rate=synth_seg.frame_rate)
                    synth_seg += filler
                    # Пересчитываем паузы
                    silent_ranges = detect_silence(
                        synth_seg,
                        min_silence_len=min_silence_duration_ms,
                        silence_thresh=silence_threshold_db,
                    )
                    synth_pauses = [
                        {"start_s": s / 1000.0, "end_s": e / 1000.0,
                         "duration_s": (e - s) / 1000.0}
                        for s, e in silent_ranges
                    ]
                    synth_pause_cnt = len(synth_pauses)
                    log.append(f"   After filler: now {synth_pause_cnt} pauses.")
                elif synth_pause_cnt > orig_pause_cnt:
                    # Обрезаем лишние паузы в конце (просто их игнорируем)
                    excess = synth_pause_cnt - orig_pause_cnt
                    log.append(f"   Truncating {excess} extra pauses from the end.")
                    # Пересчитываем границы: берём только первые orig_pause_cnt пауз
                    cut_point_ms = int(synth_pauses[orig_pause_cnt - 1]["end_s"] * 1000)
                    synth_seg = synth_seg[:cut_point_ms]
                    # Обновляем список пауз (только нужное количество)
                    synth_pauses = synth_pauses[:orig_pause_cnt]
                    synth_pause_cnt = len(synth_pauses)
                    log.append(f"   After truncation: now {synth_pause_cnt} pauses.")
                else:
                    # Должно быть равно – ничего не делаем
                    pass
            else:
                # mismatch_policy == "warning_only"
                log.append("⚠️ Continuing with the detected pauses (warning only).")
        else:
            log.append("✅ Pause count matches original – good to go.")

        # ---------------------------------------------------------------
        # 6️⃣ Разбиваем аудио‑файл по найденным паузам
        # ---------------------------------------------------------------
        # Формируем границы: от начала до первой паузы, затем между паузами,
        # и окончание после последней паузы.
        bounds_ms = []

        # Начало → первая пауза
        if synth_pauses:
            first_start = int(synth_pauses[0]["start_s"] * 1000)
            bounds_ms.append((0, first_start))
        else:
            # нет ни одной паузы – просто один сегмент (весь файл)
            bounds_ms.append((0, len(synth_seg)))

        # Промежутки между паузами
        for i in range(len(synth_pauses) - 1):
            seg_start = int(synth_pauses[i]["end_s"] * 1000)
            seg_end   = int(synth_pauses[i + 1]["start_s"] * 1000)
            bounds_ms.append((seg_start, seg_end))

        # Последний сегмент → конец аудио
        if synth_pauses:
            last_end = int(synth_pauses[-1]["end_s"] * 1000)
            bounds_ms.append((last_end, len(synth_seg)))

        log.append(f"🔪 Determined {len(bounds_ms)} audio chunks (bounds in ms).")

        # ---------------------------------------------------------------
        # 7️⃣ Преобразуем каждый кусок обратно в ComfyUI‑audio и сохраняем файл‑превью
        # ---------------------------------------------------------------
        segment_audios = []   # список словарей {"waveform":…, "sample_rate":…}
        segment_files  = []   # пути к временным wav‑файлам (для отладки)

        tmp_dir = folder_paths.get_temp_directory()
        os.makedirs(tmp_dir, exist_ok=True)

        for idx, (start_ms, end_ms) in enumerate(bounds_ms, start=1):
            chunk = synth_seg[start_ms:end_ms]

            # Convert to torch tensor (float32, [-1, 1])
            raw_samples = np.array(chunk.get_array_of_samples())
            chunk_f = raw_samples.astype(np.float32) / 32768.0
            tensor = torch.from_numpy(chunk_f).unsqueeze(0).unsqueeze(0)

            segment_audios.append({
                "waveform": tensor,
                "sample_rate": chunk.frame_rate
            })

            # Сохраняем файл‑превью (можно удалить позже)
            fname = f"synth_part_{idx:03d}_{uuid.uuid4().hex}.wav"
            fpath = os.path.join(tmp_dir, fname)
            chunk.export(fpath, format="wav")
            segment_files.append(fpath)

            log.append(f"   Part {idx}: {len(chunk)/1000:.2f}s → {fpath}")

        # ---------------------------------------------------------------
        # 8️⃣ Формируем 5‑ти позиционный вывод (падежи, если <5 – пустой токен)
        # ---------------------------------------------------------------
        empty_audio = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
        # Заполняем пять позиций, а если частей больше 5 – отбрасываем лишнее (уже записано в preview)
        while len(segment_audios) < 5:
            segment_audios.append(empty_audio)
            segment_files.append("")

        # Если частей >5 – просто логируем, но в выходе оставляем первые 5
        if len(bounds_ms) > 5:
            log.append(f"⚠️ More than 5 audio parts detected ({len(bounds_ms)}). "
                       "Only the first 5 are returned as outputs; all paths are listed in seg_files.")

        # ---------------------------------------------------------------
        # 9️⃣ Финальный статус и возврат
        # ---------------------------------------------------------------
        status = "\n".join(log)
        return (
            segment_audios[0],   # seg_1
            segment_audios[1],   # seg_2
            segment_audios[2],   # seg_3
            segment_audios[3],   # seg_4
            segment_audios[4],   # seg_5
            segment_files,       # seg_files (STRING_LIST)
            status
        )


# ----------------------------------------------------------------
# Регистрация узла
# ----------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "DetectAndSplitSynthAudioNode": DetectAndSplitSynthAudioNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "DetectAndSplitSynthAudioNode": "Detect & Split Synthesized Audio"
}
