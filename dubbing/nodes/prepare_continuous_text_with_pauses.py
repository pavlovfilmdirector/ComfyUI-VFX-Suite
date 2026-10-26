# ---------------------------------------------------------------
# PrepareContinuousTextWithPauses
# ---------------------------------------------------------------
# Узел формирует один длинный (или один выбранный) текст из сегментов,
# добавляя начальные, меж‑сегментные и конечные паузы.
# Добавлен:
#   • вход `segment_filename` – выбирает один конкретный сегмент
#   • переключатель `remove_punctuation` – управляет очисткой от знаков
# ---------------------------------------------------------------

import os
import json
import folder_paths
import logging
import re
import traceback

logger = logging.getLogger("PrepareContinuousTextWithPauses")


class PrepareContinuousTextWithPauses:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "segments_json_path": ("STRING", {"default": ""}),
                "boundary_pause_marker": (
                    ["pause", "pause_ms", ".", "[pause]"],
                    {"default": "pause"},
                ),
                "initial_pause_duration_ms": ("INT", {"default": 0, "min": 0, "max": 10000, "step": 10}),
                "inter_segment_pause_duration_ms": ("INT", {"default": 0, "min": 0, "max": 5000, "step": 10}),
                "final_pause_duration_ms": ("INT", {"default": 0, "min": 0, "max": 10000, "step": 10}),
                "remove_punctuation": ("BOOLEAN", {"default": False, "tooltip": "Удалять знаки препинания из текста сегментов"}),
                "process_now": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "original_video_audio_path": ("STRING", {"default": ""}),
                # ---> Новый вход: имя файла‑сегмента, которое требуется извлечь
                "segment_filename": ("STRING", {"default": "", "tooltip": "Если указано, вернётся только текст данного сегмента (по имени файла)."}),
            },
        }

    # -----------------------------------------------------------------
    #   Выходные типы (не менялись)
    # -----------------------------------------------------------------
    RETURN_TYPES = (
        "STRING",   # continuous_text
        "FLOAT",    # avg_voice_speed_factor
        "STRING",   # original_audio_path
        "STRING",   # original_segments_json_path
        "STRING",   # original_segments_json_dir
        "STRING",   # status
    )
    RETURN_NAMES = (
        "continuous_text",
        "avg_voice_speed_factor",
        "original_audio_path",
        "original_segments_json_path",
        "original_segments_json_dir",
        "status",
    )
    FUNCTION = "prepare_text"
    CATEGORY = "VideoDubbing"

    # -----------------------------------------------------------------
    def prepare_text(
        self,
        segments_json_path,
        boundary_pause_marker,
        initial_pause_duration_ms,
        inter_segment_pause_duration_ms,
        final_pause_duration_ms,
        remove_punctuation,
        process_now,
        original_video_audio_path="",
        segment_filename="",
    ):
        # ---------------------------------------------------------
        # 0️⃣  Быстрый выход, если процесс не запущен
        # ---------------------------------------------------------
        if not process_now:
            return ("", 1.0, "", "", "", "Set process_now=True to start")
        if not segments_json_path:
            return ("", 1.0, original_video_audio_path, "", "", "Error: No segments JSON path provided")
        if not os.path.exists(segments_json_path):
            return ("", 1.0, original_video_audio_path, "", "", f"Error: Segments JSON file not found at {segments_json_path}")

        log = [f"🚀 Preparing continuous text from {segments_json_path}..."]
        logger.info("\n".join(log))

        # ---------------------------------------------------------
        # 1️⃣  Читаем segments.json
        # ---------------------------------------------------------
        try:
            with open(segments_json_path, "r", encoding="utf-8") as f:
                project_info = json.load(f)

            if not isinstance(project_info, dict) or "segments" not in project_info:
                raise ValueError("segments.json does not contain a valid 'segments' list")
            segments = project_info["segments"]
            if not isinstance(segments, list):
                raise ValueError("'segments' field is not a list")
            if not segments:
                log.append("⚠️ No segments found in JSON.")
                json_dir = os.path.dirname(segments_json_path)
                return ("", 1.0, original_video_audio_path, segments_json_path, json_dir, "\n".join(log))
        except Exception as e:
            log.append(f"❌ Failed to load segment JSON: {e}")
            log.append(traceback.format_exc())
            json_dir = (
                os.path.dirname(segments_json_path)
                if os.path.isdir(os.path.dirname(segments_json_path))
                else ""
            )
            return ("", 1.0, original_video_audio_path, segments_json_path, json_dir, "\n".join(log))

        # ---------------------------------------------------------
        # 2️⃣  Если указан `segment_filename` – ищем **один** нужный сегмент
        # ---------------------------------------------------------
        single_segment_mode = bool(segment_filename.strip())
        selected_segments = segments  # по умолчанию – все

        if single_segment_mode:
            target_name = os.path.basename(segment_filename.strip())
            log.append(f"🔎 Looking for segment with file name '{target_name}'")

            found = None
            for seg in segments:
                # Возможные поля, где хранится путь к файлу текста
                for key in ("text_file_path", "translated_text_file_path", "original_text_file_path", "clean_text_file_path"):
                    p = seg.get(key, "")
                    if p and os.path.basename(p) == target_name:
                        found = seg
                        break
                if found:
                    break

            if not found:
                log.append(f"⚠️ Segment file '{target_name}' not found in JSON – will return empty result.")
                json_dir = os.path.dirname(segments_json_path)
                return ("", 1.0, original_video_audio_path, segments_json_path, json_dir, "\n".join(log))

            # Оставляем в списке ровно один найденный сегмент
            selected_segments = [found]
            log.append(f"✅ Segment '{target_name}' found – processing ONLY this segment.")

        # ---------------------------------------------------------
        # 3️⃣  Формируем начальную паузу (если требуется)
        # ---------------------------------------------------------
        text_parts = []

        if initial_pause_duration_ms > 0:
            if boundary_pause_marker == ".":
                dots = max(1, initial_pause_duration_ms // 100)
                text_parts.append("." * dots)
                log.append(f"   Added initial pause using {dots} dots (≈{initial_pause_duration_ms} ms)")
            elif boundary_pause_marker == "[pause]":
                text_parts.append(f"[pause:{initial_pause_duration_ms}]")
            elif boundary_pause_marker == "pause":
                text_parts.append(f"pause:{initial_pause_duration_ms}")
            elif boundary_pause_marker == "pause_ms":
                ms_str = f"{initial_pause_duration_ms // 1000},{initial_pause_duration_ms % 1000:03d}"
                text_parts.append(f"pause:{ms_str}")
            log.append(f"   Added initial pause: {initial_pause_duration_ms} ms (marker '{boundary_pause_marker}')")

        # ---------------------------------------------------------
        # 4️⃣  Обходим выбранные сегменты (один или все)
        # ---------------------------------------------------------
        total_original_duration = 0.0
        total_words = 0
        total_pause_duration = 0.0  # только для статистики, сейчас не используется

        for i, segment in enumerate(selected_segments):
            # ------------------- Текст (перевод, если есть) -------------------
            seg_text = segment.get("translated_text", "").strip() or segment.get("original_text", "").strip()
            if not seg_text:
                log.append(f"   Skipping segment {i+1}: empty text.")
                continue

            # ------------------- Удаляем пунктуацию (по флагу) -------------------
            if remove_punctuation:
                cleaned_text = re.sub(r"[^\w\s]", " ", seg_text)
                cleaned_text = " ".join(cleaned_text.split())
            else:
                cleaned_text = seg_text  # оставляем как есть

            # ------------------- Добавляем собственно реплику -------------------
            text_parts.append(cleaned_text)

            # ------------------- Меж‑сегментная пауза (если это НЕ последний и не в режиме single‑segment) -------------------
            if not single_segment_mode and i < len(selected_segments) - 1 and inter_segment_pause_duration_ms > 0:
                if boundary_pause_marker == ".":
                    dots = max(1, inter_segment_pause_duration_ms // 100)
                    text_parts.append("." * dots)
                    log.append(f"   Added inter‑segment pause using {dots} dots (≈{inter_segment_pause_duration_ms} ms)")
                elif boundary_pause_marker == "[pause]":
                    text_parts.append(f"[pause:{inter_segment_pause_duration_ms}]")
                    log.append(f"   Added inter‑segment pause: [pause:{inter_segment_pause_duration_ms}]")
                elif boundary_pause_marker == "pause":
                    text_parts.append(f"pause:{inter_segment_pause_duration_ms}")
                    log.append(f"   Added inter‑segment pause: pause:{inter_segment_pause_duration_ms}")
                elif boundary_pause_marker == "pause_ms":
                    ms_str = f"{inter_segment_pause_duration_ms // 1000},{inter_segment_pause_duration_ms % 1000:03d}"
                    text_parts.append(f"pause:{ms_str}")
                    log.append(f"   Added inter‑segment pause: pause:{ms_str}")

            # ------------------- Метрики для speed‑factor -------------------
            total_words += segment.get(
                "original_word_count", len(cleaned_text.split())
            )
            total_original_duration += segment.get("original_duration_s", 0.0)

        # ---------------------------------------------------------
        # 5️⃣  Конечная пауза (если требуется)
        # ---------------------------------------------------------
        if final_pause_duration_ms > 0:
            if boundary_pause_marker == ".":
                dots = max(1, final_pause_duration_ms // 100)
                text_parts.append("." * dots)
                log.append(f"   Added final pause using {dots} dots (≈{final_pause_duration_ms} ms)")
            elif boundary_pause_marker == "[pause]":
                text_parts.append(f"[pause:{final_pause_duration_ms}]")
            elif boundary_pause_marker == "pause":
                text_parts.append(f"pause:{final_pause_duration_ms}")
            elif boundary_pause_marker == "pause_ms":
                ms_str = f"{final_pause_duration_ms // 1000},{final_pause_duration_ms % 1000:03d}"
                text_parts.append(f"pause:{ms_str}")
            log.append(f"   Added final pause: {final_pause_duration_ms} ms (marker '{boundary_pause_marker}')")

        # ---------------------------------------------------------
        # 6️⃣  Сборка финального текста
        # ---------------------------------------------------------
        continuous_text = " ".join(text_parts)

        # ---------------------------------------------------------
        # 7️⃣  Средний speed‑factor (по выбранным сегментам)
        # ---------------------------------------------------------
        avg_voice_speed_factor = 1.0
        if total_original_duration > 0:
            avg_wps_original = total_words / total_original_duration
            VIBEVOICE_BASELINE_WPS = 2.75
            avg_voice_speed_factor = VIBEVOICE_BASELINE_WPS / avg_wps_original
            avg_voice_speed_factor = max(0.5, min(2.0, avg_voice_speed_factor))
            log.append(f"📊 Original avg WPS = {avg_wps_original:.2f}")
            log.append(f"🔧 Computed avg_voice_speed_factor = {avg_voice_speed_factor:.3f}")
        else:
            log.append("⚠️ Original duration is zero – using default speed factor 1.0")

        # ---------------------------------------------------------
        # 8️⃣  Статус + preview
        # ---------------------------------------------------------
        preview = continuous_text[:200] + ("…" if len(continuous_text) > 200 else "")
        log.append(f"📄 Preview (first 200 chars): {preview}")

        json_dir = os.path.dirname(segments_json_path)

        summary = [
            f"Total original duration: {total_original_duration:.2f}s",
            f"Total words: {total_words}",
            f"Avg voice speed factor: {avg_voice_speed_factor:.3f}",
            f"Initial pause: {initial_pause_duration_ms} ms",
            f"Inter‑segment pause: {inter_segment_pause_duration_ms} ms",
            f"Final pause: {final_pause_duration_ms} ms",
            f"Segment‑filter mode: {'single segment' if single_segment_mode else 'full'}",
            f"Original segments JSON dir: {json_dir}",
        ]
        log.append("\n".join(summary))
        log.append("✅ Continuous text preparation completed")
        status = "\n".join(log)

        # ---------------------------------------------------------
        # 9️⃣  Возврат
        # ---------------------------------------------------------
        return (
            continuous_text,
            avg_voice_speed_factor,
            original_video_audio_path,
            segments_json_path,
            json_dir,
            status,
        )


# ----------------------------------------------------------------------
# Регистрация ноды
# ----------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "PrepareContinuousTextWithPauses": PrepareContinuousTextWithPauses
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PrepareContinuousTextWithPauses": "Prepare Continuous Text With Pauses"
}
