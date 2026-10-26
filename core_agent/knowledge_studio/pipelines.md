# Рецепты (проверены на реальных клипах)

Каждый шаг — отдельное действие video_process. Делай по одному шагу: дождись слоя, возьми его id и только потом следующий шаг.

## Новый фон, персонаж прежний

1. text_mask {text: "person"} → маска персонажа.
2. depth {} → глубина клипа (несёт движение камеры).
3. Первый кадр нового фона: edit_image на кадре 0 с промптом «replace the background with …, keep the person exactly unchanged» (или пользователь даёт свою картинку).
4. ltx_ic_lora {lora: union-control, source_layer: <depth>, needs: "first_frame", ref_image: <первый кадр>, prompt: описание новой сцены} → новый фон с тем же движением камеры.
5. composite {fg: "plate", mask: <маска>, bg: <слой из шага 4>, harmonize: 0.4, light_wrap: 0.3} → оригинальный персонаж поверх.
6. По желанию: ltx_ic_lora {lora: refine-details, source_layer: <composite>, overlap: 16} — сглаживает края и детали. Без first_frame (иначе первый кадр «прыгает»).

## Новый персонаж, фон прежний

1. ltx_ic_lora {lora: clean-plate, prompt: "An empty clean plate of the exact same location: … with no people, no person, no body parts."} → фон без человека.
2. pose {} → поза DWPose (или поза из 3D).
3. first_frame {guide_layer: "plate" кадр 0 / рендер, pose_layer: <поза>, ref_image: <персонаж>} — рецепт «сцена + скелет + персонаж» держит облик лучше всего.
4. ltx_ic_lora {lora: union-control, source_layer: <поза>, needs: "first_frame", ref_image: <первый кадр>, prompt: персонаж + действие + сцена}.
5. text_mask {text: "person", source_layer: <слой из шага 4>} → маска нового персонажа.
6. composite {fg: <слой шага 4>, mask: <маска шага 5>, bg: <clean plate>} → точный исходный фон.
7. По желанию refine-details по composite.

## 3D-анимация → видео (персонаж по анимации)

1. В 3D: персонаж/скелет + анимация (FBX, Mixamo), окружение из примитивов, камера (облёт).
2. Экспорт из 3D: слой позы и слой окружения (только примитивы) в 2D-видео.
3. first_frame {guide_layer: <окружение>, pose_layer: <поза>, ref_image: <персонаж>, scene: "wooden bench, oak tree, lawn"}.
4. ltx_ic_lora {lora: layout-to-render, source_layer: <окружение>, lora2: union-control, source_layer2: <поза>, lora2_attention: 0.8, needs: "first_frame", ref_image: <кадр>}.

## Дубляж

action dubbing {mode: "dub", config: {player: {video_path, target_lang: "ru"}, translation: {source_language: "en", target_language: "ru", translator: "ollama"}}}.
Клон голоса: {mode: "voice", config: {tts_general: {engine: "F5-TTS", speaker_ref: <путь к образцу>, target_text: <текст>}}}.

## Частые проблемы

- «Слоя нет / Prepare the plate first» → сначала prepare.
- Видно склейки → шот до 97 кадров идёт одним проходом; для длинных overlap 16.
- Персонаж «копирует» референс вместо позы → используй first_frame с pose_layer (скелет отдельной картинкой), а не наложенный на сцену.
- Тёплый персонаж на холодном фоне → composite с harmonize 0.4–0.5.
- Ollama не отвечает → проверь, что Ollama запущена и есть модель (qwen / gemma).
- Две LoRA (lora2) на 24 ГБ — не больше 49 кадров (end-start ≤ 48), иначе 30+ мин и зависание декодера.
- Полная перегенерация по скелету с новым первым кадром — лицо «плывёт», на динамике сильнее. Если важен исходный персонаж — предлагай рецепт «Новый фон» со сведением.
- Глубина (любая: V2, DA3, depth_pro) не держит лицо. Узнаваемый персонаж → только поза (без lora2 глубины). Глубину — только если персонаж неважен или для фона/камеры.
