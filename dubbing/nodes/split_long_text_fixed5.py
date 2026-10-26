# split_long_text_fixed5.py
# ---------------------------------------------------------------
# Узел, который делит длинный текст на ≤ 5 частей (по слову)
# и сразу отдаёт их как отдельные STRING‑выходы.
# ---------------------------------------------------------------
import re
import logging

logger = logging.getLogger("SplitLongTextFixed5")

class SplitLongTextFixed5:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                # Исходный текст (может быть многострочным)
                "input_text": ("STRING", {"default": "", "multiline": True}),
                # Максимальное количество слов **в одном** фрагменте
                "max_words_per_part": ("INT", {
                    "default": 5000,
                    "min": 1,
                    "max": 50000,
                    "step": 1
                }),
                # Флаг «запустить сейчас»
                "process_now": ("BOOLEAN", {"default": False}),
            }
        }
    CATEGORY = "VideoDubbing"
    
    # -----------------------------------------------------------
    #   Выходные типы: 5 строк + статус‑превью + количество частей
    # -----------------------------------------------------------
    RETURN_TYPES = (
        "STRING",   # part_1
        "STRING",   # part_2
        "STRING",   # part_3
        "STRING",   # part_4
        "STRING",   # part_5
        "INT",      # parts_count (сколько реально получено)
        "STRING",   # status / preview
    )
    RETURN_NAMES = (
        "part_1",
        "part_2",
        "part_3",
        "part_4",
        "part_5",
        "parts_count",
        "status",
    )
    FUNCTION = "split_text"
    CATEGORY = "text/manipulation"

    # -----------------------------------------------------------
    def split_text(self, input_text, max_words_per_part, process_now):
        # ----------------- 0️⃣ Флаг запуска -----------------
        if not process_now:
            return ("", "", "", "", "", 0,
                    "Set process_now=True to start processing")

        # ----------------- 1️⃣ Подготовка слов -----------------
        # Убираем лишние пробельные символы, разбиваем на отдельные "слова"
        words = re.findall(r'\S+', input_text.strip())
        total_words = len(words)

        log = [f"📦 SplitLongTextFixed5: {total_words} слов, "
               f"max {max_words_per_part} слов/часть"]

        if total_words == 0:
            log.append("⚠️ Input text is empty – all parts are empty.")
            return ("", "", "", "", "", 0, "\n".join(log))

        # ----------------- 2️⃣ Разбиение на куски -----------------
        parts = []
        for i in range(0, total_words, max_words_per_part):
            chunk = words[i:i + max_words_per_part]
            parts.append(" ".join(chunk))

        # Сколько частей получилось?
        parts_count = len(parts)

        # Если больше 5 – отбрасываем «лишние», но фиксируем в лог
        if parts_count > 5:
            dropped = parts_count - 5
            log.append(f"⚠️ Требуется 5 выходов, но обнаружено {parts_count} "
                       f"частей → последние {dropped} будут отброшены.")
            parts = parts[:5]          # оставляем только первые 5
            parts_count = 5
        else:
            log.append(f"✅ Успешно получено {parts_count} часть(ей).")

        # ----------------- 3️⃣ Формирование вывода -----------------
        # Заполняем пять позиций, недостающие – пустыми строками
        while len(parts) < 5:
            parts.append("")

        # Превью‑текст (короткая справка)
        preview = (f"Split result: {parts_count} part(s) "
                   f"(max {max_words_per_part} words each).")
        log.append(preview)

        # Возвращаем кортеж из 7‑ми элементов
        status = "\n".join(log)
        return (
            parts[0],   # part_1
            parts[1],   # part_2
            parts[2],   # part_3
            parts[3],   # part_4
            parts[4],   # part_5
            parts_count,
            status
        )


# ----------------------------------------------------------------
#   Регистрация узла
# ----------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "SplitLongTextFixed5": SplitLongTextFixed5
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "SplitLongTextFixed5": "Split Long Text → 5 Fixed Parts"
}
