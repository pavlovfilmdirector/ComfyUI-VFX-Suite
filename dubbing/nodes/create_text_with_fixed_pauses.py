# create_text_with_fixed_pauses.py

import os
import json
import logging
import re

logger = logging.getLogger("CreateTextWithFixedPauses")

class CreateTextWithFixedPauses:
    """
    Узел, который загружает segments.json, извлекает переведённые тексты
    и объединяет их в один сплошной текст, вставляя фиксированное количество
    точек (например, 10) между сегментами для создания чётко идентифицируемых пауз.
    """

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "segments_json_path": ("STRING", {"default": "", "tooltip": "Path to the segments.json file after translation."}),
                "pause_marker": ("STRING", {"default": ".........", "tooltip": "String used to represent a pause in the continuous text."}),
                "process_now": ("BOOLEAN", {"default": False}),
            }
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")  # continuous_text, output_file_path, status
    RETURN_NAMES = ("continuous_text", "text_file_path", "status")
    FUNCTION = "create_text"
    CATEGORY = "VideoDubbing"

    def create_text(self, segments_json_path, pause_marker, process_now):
        if not process_now:
            return ("", "", "Set process_now=True to start processing")

        if not segments_json_path:
            error_msg = "Error: No segments JSON path provided"
            logger.error(error_msg)
            return ("", "", error_msg)

        if not os.path.exists(segments_json_path):
            error_msg = f"Error: Segments JSON file not found at {segments_json_path}"
            logger.error(error_msg)
            return ("", "", error_msg)

        log = [f"Starting creation of continuous text with fixed pauses from {segments_json_path}..."]
        logger.info("".join(log))

        try:
            log.append("🔧 Loading segments info from JSON...")
            with open(segments_json_path, 'r', encoding='utf-8') as f:
                project_info = json.load(f)

            if not isinstance(project_info, dict) or "segments" not in project_info:
                log.append("❌ Error: segments.json is not a valid JSON object or lacks 'segments' key")
                return ("", "", "".join(log))

            segments_data = project_info["segments"]
            if not segments_data or not isinstance(segments_data, list):
                log.append("❌ Error: segments.json 'segments' field is empty or not a list")
                return ("", "", "".join(log))

            log.append(f"✅ Loaded {len(segments_data)} segments from JSON.")

            # Определяем директорию проекта и путь для сохранения
            project_dir = os.path.dirname(segments_json_path)
            output_dir = os.path.join(project_dir, "ProcessedText")
            os.makedirs(output_dir, exist_ok=True)
            output_filename = "continuous_text_with_fixed_pauses.txt"
            output_path = os.path.join(output_dir, output_filename)

            log.append("📝 Creating continuous text with fixed pauses...")
            continuous_text_parts = []
            for idx, segment in enumerate(segments_data):
                # Выбираем текст для синтеза: сначала пытаемся использовать translated_text, затем original_text
                text_to_use = segment.get("translated_text", segment.get("original_text", "")).strip()

                if idx == 0:
                    # Для первого сегмента добавляем только его текст
                    continuous_text_parts.append(text_to_use)
                else:
                    # Для последующих сегментов: добавляем паузу (marker), затем текст
                    # Проверяем, была ли оригинальная пауза перед этим сегментом
                    # pause_before_s > 0 или просто добавляем marker всегда между сегментами?
                    # Решено: всегда добавляем marker между сегментами, соответствующими оригинальным сегментам.
                    # Количество marker будет len(segments_data) - 1
                    continuous_text_parts.append(pause_marker)
                    continuous_text_parts.append(text_to_use)

            continuous_text = "".join(continuous_text_parts)
            log.append(f"✅ Created continuous text with {len(segments_data)} segments and {len(segments_data) - 1} pause markers.")

            log.append(f"💾 Saving continuous text to: {output_path}")
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(continuous_text)

            log.append("🎉 Process completed successfully.")
            return (continuous_text, output_path, "".join(log))

        except Exception as e:
            import traceback
            error_msg = f"❌ Failed to create continuous text: {e}\n{traceback.format_exc()}"
            logger.error(error_msg)
            return ("", "", error_msg)


# Не забудьте зарегистрировать эту ноду в __init__.py
# NODE_CLASS_MAPPINGS["CreateTextWithFixedPauses"] = CreateTextWithFixedPauses
# NODE_DISPLAY_NAME_MAPPINGS["CreateTextWithFixedPauses"] = "Create Text With Fixed Pauses"