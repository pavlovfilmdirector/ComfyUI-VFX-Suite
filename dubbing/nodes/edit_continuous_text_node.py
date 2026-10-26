# ---------------------------------------------------------------
# edit_continuous_text_node.py
# ---------------------------------------------------------------
import os
import json
import logging
import traceback

logger = logging.getLogger("EditContinuousTextNode")


class EditContinuousTextNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # Текст, который будет показан в UI. Если к нему подключён
                # другой узел, значение будет записано в поле, а пользователь
                # всё равно сможет его поправить.
                "continuous_text": ("STRING", {
                    "multiline": True,
                    "default": "",          # будет заменено при первом соединении
                    # Важно НЕ ставить forceInput=True – иначе значение из
                    # соединения будет «запрещено» для редактирования.
                    "forceInput": False,
                }),
                "segments_json_path": ("STRING", {"default": ""}),
            },
            "optional": {
                # Альтернативный стартовый текст (если пользователь хочет
                # написать что‑то с нуля). Не имеет forceInput – поле
                # останется редактируемым в UI.
                "editable_text": ("STRING", {
                    "multiline": True,
                    "default": "",
                }),
            },
        }

    # --------------------------------------------------------------
    #   Три выхода:
    #   1) edited_text          – текущий (может быть отредактирован)
    #   2) updated_segments_json_path – путь к JSON‑файлу (для цепочек)
    #   3) status               – отладочный лог
    #   4) display_text         – **дублирует** edited_text и предназначен
    #                              для удобного «цикл‑подключения» к вводу.
    # --------------------------------------------------------------
    RETURN_TYPES = ("STRING", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("edited_text",
                    "updated_segments_json_path",
                    "status",
                    "display_text")      # ← новый выход
    FUNCTION = "edit"
    CATEGORY = "VideoDubbing"

    # --------------------------------------------------------------
    def edit(self,
             continuous_text: str,
             segments_json_path: str,
             editable_text: str = "") -> tuple:
        """
        1️⃣ Выбирает стартовый текст:
            • если `continuous_text` получен от другого узла – используем его;
            • иначе – берём значение `editable_text`;
            • если оба пусты – пустая строка.
        2️⃣ Сохраняет (или обновляет) поле `continuous_text` в `segments.json`.
        3️⃣ Возвращает отредактированный текст, путь к JSON, статус‑сообщение
           и **display_text**, который удобно подключить обратно к входу
           `continuous_text`.
        """
        # ------------------- 0️⃣ Базовые проверки -------------------
        if not segments_json_path:
            status = "❌ Error: No segments_json_path supplied."
            logger.error(status)
            return ("", "", status, "")

        if not os.path.isfile(segments_json_path):
            status = f"❌ Error: segments JSON not found at {segments_json_path}"
            logger.error(status)
            return ("", "", status, "")

        # ------------------- 1️⃣ Выбор стартового текста -------------
        start_text = continuous_text or editable_text or ""

        # ------------------- 2️⃣ Загрузка JSON ----------------------
        try:
            with open(segments_json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            status = f"❌ Failed to load JSON: {e}"
            logger.error(status)
            return (start_text, "", status, start_text)

        # ------------------- 3️⃣ Сохранение текста в JSON -----------
        data["continuous_text"] = start_text
        try:
            with open(segments_json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            save_msg = "✅ Continuous text saved to segments.json"
        except Exception as e:
            save_msg = f"⚠️ Failed to write JSON: {e}"
            logger.error(save_msg)

        # ------------------- 4️⃣ Формируем статус -------------------
        status = (f"{save_msg}\n"
                  f"📝 Length of text: {len(start_text)} chars.\n"
                  f"🔗 JSON path: {segments_json_path}")

        logger.info(status)

        # ------------------- 5️⃣ Возврат ---------------------------
        # `display_text` – копия `start_text`, чтобы пользователь мог
        # подключить её к входу `continuous_text` и увидеть текст в UI.
        return (start_text, segments_json_path, status, start_text)


# ----------------------------------------------------------------------
# Регистрация ноды
# ----------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "EditContinuousTextNode": EditContinuousTextNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "EditContinuousTextNode": "Edit Continuous Text (Manual Edit UI)"
}
