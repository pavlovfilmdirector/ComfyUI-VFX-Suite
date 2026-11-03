# Рецепты (проверенные цепочки)

Формат: что просит пользователь → шаги (kind) → важное. Цепочку из 2+ шагов запускай одним `run_plan`.
Сначала смотри, какие слои у видео уже есть, и пропускай готовые шаги.

## 1. Заменить фон, персонаж тот же
«замени фон на …», «перенеси героиню в …»
1. `pose` — движение персонажа.
2. `image_edit` (frame = start): "Keep the <subject> exactly as is - same face, hair, clothes, pose, position, size. Replace everything behind with <new place>. Same camera angle and framing."
3. `ltx_ic_lora` lora=union-control, source_layer=$поза, ref_image=$кадр, prompt = персонаж + новое место + движение камеры.
Альтернатива с точным персонажем: `text_mask` → `ltx_ic_lora` clean-plate … → `composite` (fg=plate, mask, bg=новый фон) → `ltx_ic_lora` refine-details.

## 2. Заменить персонажа, фон тот же
1. `depth` (держит геометрию сцены и силуэт) или `pose` (если фигура сильно другая).
2. `image_edit`: "Replace the <subject> with <new character>, same pose, position and size. Keep the background exactly the same."
3. `ltx_ic_lora` union-control, source_layer=$глубина (или $поза), ref_image=$кадр.

## 3. И фон, и персонаж
`pose` → `image_edit` (заменить и персонажа, и всё за ним) → `ltx_ic_lora` union-control по позе.

## 4. Трекинг шота + массовка/персонажи из 3D
«сделай трекинг и добавь на фон N человек, которые …»
1. `text_mask` (кто в кадре) → 2. `track_camera` (mask_layers=[$маска], start/end ≤ 97 кадров) →
3. `pose` → 4. `wait_3d_pose` (ask: «Во вкладке 3D добавьте N скелетов с анимацией, поставьте за персонажем в кадр
трекинг-камеры и нажмите Pose to Gen») → 5. `merge_pose` [$поза, $3d] → 6. `image_edit` (фон + люди) →
7. `ltx_ic_lora` union-control по $слитой позе.
Готовый вариант этой цепочки — kind `shot_task` (variants bg / char / both).

## 5. Убрать людей / чистый фон (clean plate)
`ltx_ic_lora` lora=clean-plate, prompt "An empty <place>, nobody, same light". Длинное видео — куски по 97 кадров.

## 6. Вырезать персонажа на прозрачный фон
`text_mask` (или `detect_all`) → `corridor_key` (mask_layer_id = $маска; волосы и края) → `video_cutout`.
Быстро и грубо: `text_mask` → `video_cutout`. Без маски вообще: `ltx_alpha`.

## 7. Персонаж в новом месте по фото-референсу
Пользователь дал фото персонажа: `pose` → `image_edit` с описанием персонажа с фото → `ltx_ic_lora` union-control.
Для 3D-скелета (без исходного видео): 3D → скелет + анимация → Pose to Gen → `first_frame` (guide_layer, pose_layer,
prompt = описание персонажа) → `ltx_ic_lora` union-control.

## 8. 3D-лейаут → кино-шот (layout to render)
`setup_3d_layout` (кубы, пол, камера облёта) → пользователь жмёт «рендер камер в слои» (Layout to Gen / Pose to Gen /
Depth) → `first_frame` по слою Layout to Gen → `ltx_ic_lora` layout-to-render, ref_image = первый кадр
(+ lora2 union-control по Pose to Gen, до 49 кадров). Подсказка: в промпте не противоречь первому кадру.

## 9. Свет, время суток, качество
- Ночь: `ltx_ic_lora` day-to-night. Ч/б → цвет: colorization. Шум/царапины: restore. Смаз: deblur. ×2: pixel-spatial-upscaler.
- Свет первого кадра под новый фон: `relight_frame` (light_layer = фон) → дальше как в рецепте 1.

## 10. Объектив с дисторсией (для композа в Nuke)
`undistort` (кадры выпрямлены, lens.json + ST-maps) → работа на выпрямленном плейте → `redistort` готового слоя.

## 11. Звук
- Дубляж видео на другой язык: `dubbing` mode=dub. Клон голоса / реплика: mode=voice. Длинный закадр: `voice` или mode=narration.
- Липсинк под новое аудио: ltx_ic_lora dubit (LTX-2.3). Музыка: `music` (tags по-английски, lyrics с [verse]/[chorus]).

## Типичные ошибки (не делай так)
- Не запускай видео LTX без первого кадра, если LoRA его требует (layout-to-render, union-control в рецептах 1–4).
- Не давай LTX больше 97 кадров за раз; длина 8n+1.
- Не запускай `track_camera` без маски людей, если в кадре есть движущиеся персонажи.
- Не путай картинки (1D, generate_image / edit_image) и видео (2D, video_process / run_plan).
