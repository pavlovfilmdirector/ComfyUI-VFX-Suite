# Справочник процессов KESHIKI

Окна: **1D** — картинки; **2D** — видео со слоями (маски, позы, глубина, сгенерированные варианты); **3D** — сцена
Three.js (кубы, камеры, скелеты-манекены, облако точек трекинга); плюс Голос/Дубляж, Музыка, Мои пайплайны, Мои
инструменты. Всё локально на GPU (24 ГБ): тяжёлые процессы идут по очереди, по одному.

## Видео-процессы (2D): action `video_process` {kind, params} или шаг `run_plan`
Каждый процесс делает НОВЫЙ слой в видео. `start`/`end` — диапазон кадров (по умолчанию всё видео).
Перед любым процессом у видео должны быть разложены кадры (`prepare`; `run_plan` делает это сам).

| kind | Что делает | params |
|---|---|---|
| prepare | Раскладывает видео на кадры (нужно один раз) | url: videoUrl видео |
| text_mask | Маска по тексту (SAM 3.1): все объекты класса | text: англ. ("person", "woman", "red car"), start, end, max_objects |
| detect_all | Маска всех движущихся объектов (YOLO + SAM2) | classes: "person, car, ..." |
| corridor_key | Чистый кей волос/краёв из грубой маски → матт + вырезанный передний план | mask_layer_id |
| ltx_alpha | Альфа-матт переднего плана без хромакея (LTX Alpha IC-LoRA) | start, end |
| mask_grow | Расширить (+px) / сузить (−px) маску | layer_id, px, feather |
| merge_masks | Объединить несколько масок в одну | layer_ids: [...], name |
| depth | Карта глубины | engine: "v2" (по умолч.) / "da3" / "depth_pro" (точнее, медленнее); exclude_mask: id маски |
| normal | Нормали (MoGe-2) | convention: "directx" / "opengl" |
| canny | Контуры | — |
| pose | Поза людей (DWPose, OpenPose-скелет) | name |
| pose_cleanup | Чистка позы из 3D (убирает «длинные кости») | source_layer, max_bone_px |
| layout_cleanup | Чистка рендера лейаута + гайд первого кадра | layout_source, skeleton_source, matte_layer, pose_layer |
| track_camera | Трекинг камеры (GLOMAP): камера + облако точек → сцена 3D. Людей/машины исключай маской | start, end, mask_layers: [id маски], mask_mode: "layers"/"yolo"/"none", density: "draft"/"standard"/"dense", focal_fixed + focal_mm |
| undistort | Выпрямить дисторсию объектива (слой + lens.json + ST-maps) | auto / k1, k2 |
| redistort | Вернуть готовый слой в дисторсию объектива: слой «… · lens» | layer_id |
| first_frame | Первый кадр персонажа по 3D-гайду и позе (FLUX) | guide_layer, pose_layer, prompt (описание персонажа, англ.), scene, two_step |
| relight_frame | Переосветить кадр (Qwen Edit) | image (url) или layer + frame; azimuth 0–360, elevation, intensity, color "#hex"; или light_layer (взять свет с фона) |
| ltx_ic_lora | Видео LTX-2.5 по входному видео/слою + IC-LoRA (см. таблицу ниже). До 97 кадров за проход | lora, prompt (англ.), source_layer (вход/контроль; по умолч. исходное видео), ref_image (первый кадр), start, end, name; lora2 + source_layer2 — второй контроль (до 49 кадров) |
| composite | Вырезать объект маской и положить на другой фон | fg: "plate" или id слоя, mask: id маски, bg: id слоя или "plate", feather, light_wrap, harmonize |
| fill | Залить цветом внутри/снаружи маски | mask_layer_id, color, invert |
| video_cutout | Вырезать объект на прозрачный фон (PNG + WebM) | mask_layer_id, name |
| layer_to_video | Слой → отдельное видео | layer_id |
| render | Свести видимые слои в новое видео | start, end, include_plate |
| montage | Склейка нескольких видео с растворением (не над одним видео) | videos: [{url}], overlap |
| track_object | Маска объекта по клику (точки ставит пользователь в 2D) | points, frame |
| shot_task | Готовая задача на шот: маска → трекинг → поза/глубина → скелеты из 3D → варианты (фон / персонаж / оба) | track, subject, background, character, dancers{count,who,action}, variants [bg, char, both], start, end |

### IC-LoRA для `ltx_ic_lora` (params.lora — имя файла или ключ)
| ключ | что делает | вход (source_layer) / нужно |
|---|---|---|
| union-control | Новое видео по контролю: поза / глубина / контуры | слой pose, depth или canny + ref_image (первый кадр) + prompt |
| layout-to-render | Серый 3D-лейаут → финальный шот | слой рендера лейаута + ref_image (первый кадр) |
| clean-plate | Убирает людей/объекты, восстанавливает фон | исходное видео (source_layer не нужен) |
| alpha-gen | Матт переднего плана | — |
| refine-details | Дорисовывает детали, сглаживает швы после композита | слой композита |
| restore / deblur / decompression | Чистка шума / смаза / артефактов кодека | — |
| day-to-night, colorization, sdr-to-hdr, water-simulation | Ночь, раскраска, HDR, вода | — |
| pixel-spatial-upscaler | Апскейл ×2 | — |
| ingredients | Видео по референс-листу (персонажи/предметы на чёрном) | ref_image = лист; промпт «Reference sheet: … / Generated video: …» |
| motion-track, dubit (LTX-2.3) | Движение по треку; липсинк под аудио | — |

Правило кадров LTX: длина = 8n+1 (9, 17, 25, 33, 49, 65, 81, 97). 97 кадров ≈ 3–5 мин на 24 ГБ.

## Дополнительные шаги, которые есть только в `run_plan`
| kind | Что делает | params | результат |
|---|---|---|---|
| image_edit | Правка кадра FLUX по тексту: новый фон, новый персонаж, стиль | prompt (англ., «Keep … exactly, replace …»), frame (кадр видео) или image (url), layer | `$id.image` |
| wait_3d_pose | Ждёт, пока пользователь в 3D поставит скелеты и нажмёт **Pose to Gen** | ask: что сказать пользователю, wait_min | `$id.layer` |
| merge_pose | Сложить несколько слоёв позы в один (персонаж + массовка) | layers: [...], name | `$id.layer` |

## Как писать `run_plan`
```json
{"action": "run_plan", "params": {"video_id": "<id или название>", "title": "Замена фона",
  "steps": [
    {"id": "m", "kind": "text_mask", "title": "Маска героини", "params": {"text": "woman"}},
    {"id": "p", "kind": "pose", "title": "Поза", "params": {}},
    {"id": "f", "kind": "image_edit", "title": "Первый кадр с новым фоном", "params": {"frame": 0,
      "prompt": "Keep the woman exactly as is. Replace everything behind her with a neon cyberpunk street at night."}},
    {"id": "v", "kind": "ltx_ic_lora", "title": "Видео", "params": {"lora": "union-control", "source_layer": "$p.layer",
      "ref_image": "$f.image", "prompt": "...", "start": 0, "end": 96, "name": "Cyberpunk"}}
  ]}, "confirm": false}
```
- Ссылки только на шаги ВЫШЕ: `$m.layer` — id слоя шага m, `$f.image` — url кадра шага f.
- Слои, которые УЖЕ есть в видео, передавай их id напрямую, не делай заново.
- Длинное видео: делай куски по ≤97 кадров (`start`/`end`).

## Другие действия (не видео-слои)
generate_image (картинка FLUX/Qwen), edit_image, generate_video (окно настроек LTX: t2v, i2v, flf2v, layout_to_render),
mesh3d (3D-модель из картинки, TRELLIS.2), setup_3d_layout (кубы + пол + камера облёта), add_skeleton, project_pose
(поза из 3D в 2D-слой), dubbing (дубляж / клон голоса / закадровый текст), voice, music (ACE-Step), run_tool
(«Мои инструменты»), switch_dimension / switch_tab (показать окно). Полный список с параметрами — «Каталог действий».

## Где что в интерфейсе (если спрашивают «где кнопка»)
- «✨ Генерация» — центр вверху: Изображение, Видео, Голос, 3D модель, Музыка, Мои инструменты.
- 2D: тулбар над видео — Маски, Матт, Карты, Линза, Камера; под таймлайном «Блокнот» с кнопкой ✨.
- 3D: тулбар вьюпорта — кубы, камера, скелет, пресеты облёта, Pose to Gen (иконка картинки), рендер камер в слои.
- «🔀 Мои пайплайны» — сохранённые цепочки; «🕘 Генерации» — список запусков внизу слева.
