# ---------------------------------------------------------------
# analyze_video_for_dubbing_prep.py
# ---------------------------------------------------------------
import os
import re
import json
import shutil
import logging
import traceback
import torch
import pathlib

import folder_paths

# ------------------------------------------------------------
# 3rd‑party библиотеки
# ------------------------------------------------------------
import numpy as np                 # используется в demucs‑функции
import soundfile as sf            # чтение/запись wav‑файлов
import torchaudio                 # <-- необходим для ресемплинга Demucs
from pydub import AudioSegment

# ------------------------------------------------------------
#   utils (ваши вспомогательные функции)
# ------------------------------------------------------------
from .utils import (
    ensure_dir,
    strip_quotes,
    extract_audio,
    detect_all_pauses,
    detect_long_pauses,
    build_chapters_from_long_pauses,
    get_whisper_model,
    get_faster_whisper_model,
    transcribe_whisper,
    save_json,
    fmt_sec,
    cleanup_dir,
    merge_whisper_segments,
)

logger = logging.getLogger("AnalyzeVideoForDubbingPrep")

# -----------------------------------------------------------------
#   Утилита: удаляем знаки пунктуации (используется в 2‑х местах)
# -----------------------------------------------------------------
def _remove_punct(text: str) -> str:
    cleaned = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    cleaned = " ".join(cleaned.split())
    return cleaned


# -----------------------------------------------------------------
#   Утилита: формат метки спикера (вне класса)
# -----------------------------------------------------------------
def format_speaker_label(speaker_id: str, style: str) -> str:
    """
    speaker_id – строка вида 'SPEAKER_00', 'SPEAKER_01' …
    Возвращает метку, заданную пользователем:
    'none' → '',
    '[1]'  → '[1] ',
    '{1}'  → '{1} ' и т.д.
    """
    if style == "none":
        return ""
    
    import re
    # Try to extract number from SPEAKER_01 or similar
    match = re.search(r'(\d+)', speaker_id)
    number = int(match.group(1)) + 1 if match else 1
    
    if style == "[1]":
        return f"[{number}] "
    if style == "{1}":
        return f"{{{number}}} "
    
    # fallback
    return f"[{number}] "


# -----------------------------------------------------------------
#   Утилита: привязка спикеров к сегментам Whisper
# -----------------------------------------------------------------
def assign_speakers(whisper_segments, speaker_turns):
    """
    Привязывает спикеров из диаризации к сегментам Whisper по времени.
    """
    for seg in whisper_segments:
        start, end = seg["start"], seg["end"]
        # Находим пересекающийся спикер
        assigned = "SPEAKER_00"
        for turn in speaker_turns:
            # Проверяем, пересекается ли сегмент с turn
            if turn["start"] <= end and turn["end"] >= start:
                assigned = turn["speaker"]
                break
        seg["speaker_id"] = assigned
    return whisper_segments


# -----------------------------------------------------------------
#   АУДИО‑ПРЕДОБРАБОТКА: разделение и шумоподавление
# -----------------------------------------------------------------
_demucs_model_cache = {}

def separate_vocals_demucs(input_wav: pathlib.Path, out_dir: pathlib.Path, audio_segment=None) -> pathlib.Path:
    """
    Делит аудио на 4‑stem (drums, bass, other, vocals) с помощью Demucs‑v4.
    Возвращает путь к файлу `vocals.wav` (моно, 16 kHz) – готовый к Whisper.
    
    audio_segment: опционально pydub.AudioSegment, чтобы пропустить чтение с диска.
    """
    try:
        from demucs.pretrained import get_model
        from demucs.apply import apply_model
    except Exception as exc:
        raise ImportError(
            "Demucs is not installed. Install it with `pip install \"demucs>=4.0\"`."
        ) from exc

    ensure_dir(out_dir)

    # 1️⃣ Модель (htdemucs – хороший компромисс)
    model_name = "htdemucs"
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    if model_name not in _demucs_model_cache:
        logger.info(f"💾 Loading Demucs model \"{model_name}\" into memory...")
        _demucs_model_cache[model_name] = get_model(name=model_name)
        _demucs_model_cache[model_name].to(device)
    
    model = _demucs_model_cache[model_name]

    # 2️⃣ Получаем данные (из памяти или с диска)
    print(f"🔬 DEMUCS: Начало обработки. audio_segment={audio_segment is not None}", flush=True)
    if audio_segment:
        # Конвертируем AudioSegment -> numpy (channels, samples)
        wav_np = np.array(audio_segment.get_array_of_samples())
        if audio_segment.channels > 1:
            wav_np = wav_np.reshape((-1, audio_segment.channels)).T
        else:
            wav_np = wav_np[None, :]
        sr = audio_segment.frame_rate
        # Конвертируем int16 -> float32
        wav_np = wav_np.astype(np.float32) / 32768.0
    else:
        wav_np, sr = sf.read(str(input_wav))
        if wav_np.ndim == 1:            # моно → (samples, 1)
            wav_np = wav_np[:, None]
        wav_np = wav_np.T               # (channels, samples)

    # 3️⃣ При необходимости ресемплим до 44 kHz (требуется Demucs)
    target_sr = 44100
    if sr != target_sr:
        wav_tensor = torch.from_numpy(wav_np).float()
        resampler = torchaudio.transforms.Resample(orig_freq=sr, new_freq=target_sr)
        wav_tensor = resampler(wav_tensor)
        sr = target_sr
    else:
        wav_tensor = torch.from_numpy(wav_np).float()

    # 4️⃣ Demucs требует 2‑канального входа
    if wav_tensor.shape[0] == 1:
        wav_tensor = wav_tensor.repeat(2, 1)

    # 5️⃣ Переносим тензор на GPU (если модель уже там)
    wav_tensor = wav_tensor.to(device)

    # 6️⃣ Применяем модель (на GPU)
    estimates = apply_model(
        model,
        wav_tensor[None, ...],    # (1, 2, samples) – batch‑size 1
        shifts=1,
        split=True
    )[0]

    # 7️⃣ Вокальная дорожка – 3‑й стем
    vocals = estimates[3]          # (2, samples)

    # 8️⃣ Приводим к моно 16 kHz (формат Whisper)
    vocals_mono = torch.mean(vocals, dim=0, keepdim=True)   # (1, samples)
    # Ресемплинг обратно к 16 kHz (Demucs работает на 44100)
    if sr != 16000:
        resampler_back = torchaudio.transforms.Resample(orig_freq=sr, new_freq=16000)
        vocals_mono = resampler_back(vocals_mono.cpu())
    vocals_np = vocals_mono.squeeze().cpu().numpy()
    print(f"🔬 DEMUCS: Вокал извлечён. Длина: {len(vocals_np)/16000.0:.1f}с, min={vocals_np.min():.4f}, max={vocals_np.max():.4f}", flush=True)

    vocal_path = out_dir / "vocals.wav"
    sf.write(str(vocal_path), vocals_np, 16000)
    return vocal_path



def separate_vocals_spleeter(input_wav: pathlib.Path, out_dir: pathlib.Path) -> pathlib.Path:
    """Разделить .wav на два стема (vocals + accompaniment) с помощью Spleeter."""
    try:
        from spleeter.separator import Separator
    except Exception as exc:
        raise ImportError(
            "Spleeter is not installed. Install it with `pip install spleeter`."
        ) from exc

    ensure_dir(out_dir)
    separator = Separator("spleeter:2stems")
    separator.separate_to_file(str(input_wav), str(out_dir))

    vocal_path = out_dir / input_wav.stem / "vocals.wav"
    if not vocal_path.is_file():
        raise FileNotFoundError(f"Spleeter did not produce {vocal_path}")
    return vocal_path


def denoise_audio(input_wav: pathlib.Path, out_wav: pathlib.Path) -> pathlib.Path:
    """Шумоподавление с помощью библиотеки noisereduce."""
    try:
        import noisereduce as nr
    except Exception as exc:
        raise ImportError(
            "noisereduce is not installed. Install it with `pip install noisereduce`."
        ) from exc

    data, sr = sf.read(str(input_wav))
    # берём первые 0.5 сек как образец шума (если файл короче – берём всё)
    noise_len = int(0.5 * sr)
    noise_sample = data[:noise_len] if len(data) > noise_len else data
    reduced = nr.reduce_noise(y=data, sr=sr, y_noise=noise_sample, prop_decrease=1.0)
    sf.write(str(out_wav), reduced, sr)
    return out_wav


def run_pyannote_diarization(vocal_path: pathlib.Path) -> list:
    """
    Выполняет speaker‑diarization с помощью готового пайплайна.
    Использует Pipeline.from_pretrained с use_auth_token.
    """
    # ------------------------------------------------------------------
    # 1️⃣  Импортируем нужные классы
    # ------------------------------------------------------------------
    try:
        from pyannote.audio import Pipeline
    except Exception as exc:
        raise ImportError(
            "pyannote.audio is not installed. Install it with `pip install pyannote.audio`."
        ) from exc

    # ------------------------------------------------------------------
    # 2️⃣  GatedRepoError (разные версии huggingface_hub)
    # ------------------------------------------------------------------
    try:
        from huggingface_hub.errors import GatedRepoError
    except Exception:
        from huggingface_hub import HfHubHTTPError as GatedRepoError  # type: ignore

    # ------------------------------------------------------------------
    # 3️⃣  Токен HF_TOKEN
    # ------------------------------------------------------------------
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        token_path = pathlib.Path.home() / ".huggingface" / "token"
        if token_path.is_file():
            hf_token = token_path.read_text().strip()
        else:
            raise RuntimeError(
                "HF_TOKEN не найден. Установите переменную окружения "
                "(Settings → Access Tokens на HuggingFace) "
                "или сохраните токен в ~/.huggingface/token."
            )

    # ------------------------------------------------------------------
    # 4️⃣  Загружаем готовый пайплайн (с use_auth_token)
    # ------------------------------------------------------------------
    try:
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=hf_token  # ✅ Вот так нужно!
        )
    except GatedRepoError as e:
        # Нет доступа к модели – мягко отключаем диаризацию
        raise RuntimeError("DIARIZATION_GATED") from e
    except Exception as exc:
        raise RuntimeError(f"Не удалось загрузить готовый пайплайн диаризации: {exc}") from exc

    # ------------------------------------------------------------------
    # 5️⃣  Запуск диаризации (на GPU, если доступна)
    # ------------------------------------------------------------------
    try:
        output = pipeline(str(vocal_path))
    except GatedRepoError as e:
        raise RuntimeError("DIARIZATION_GATED") from e
    except Exception as exc:
        raise RuntimeError(f"Ошибка во время диаризации: {exc}") from exc

    # ------------------------------------------------------------------
    # 6️⃣  Формируем список turn‑ов
    # ------------------------------------------------------------------
    speaker_turns = []
    for turn, _, speaker in output.itertracks(yield_label=True):
        speaker_turns.append(
            {"start": turn.start, "end": turn.end, "speaker": speaker}
        )
    return speaker_turns


# -----------------------------------------------------------------
#   Основная нода
# -----------------------------------------------------------------
class AnalyzeVideoForDubbingPrep:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "video_path": ("STRING", {"default": ""}),
                "source_language": (["en", "ru", "es", "fr", "de"], {"default": "en"}),
                "whisper_model": (["tiny", "base", "small", "medium", "large", "turbo", "large-v3"], {"default": "base"}),
                "use_gpu": ("BOOLEAN", {"default": True}),
                "detect_silence": ("BOOLEAN", {"default": True}),
                "silence_threshold_db": ("FLOAT", {"default": -40.0, "min": -100.0, "max": 0.0, "step": 1.0}),
                "min_silence_duration_s": ("FLOAT", {"default": 0.2, "min": 0.05, "max": 5.0, "step": 0.05}),
                "fallback_initial_pause_ms": ("INT", {"default": 0, "min": 0, "max": 5000, "step": 10}),
                "long_pause_min_duration_s": ("FLOAT", {"default": 1.0, "min": 0.5, "max": 10.0, "step": 0.1}),
                "merge_segments_factor": ("INT", {"default": 1, "min": 1, "max": 1000, "step": 1}),
                "max_pause_duration_for_merge_s": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 5.0, "step": 0.1}),
                "clean_temp": ("BOOLEAN", {"default": True}),
                "clear_cache": ("BOOLEAN", {"default": False}),
                "remove_punctuation": ("BOOLEAN", {"default": False, "tooltip": "Remove punctuation from the original text before segmentation."}),
                "process_now": ("BOOLEAN", {"default": False}),
                # ★ NEW ★ -------------------------------------------------
                "separation_method": (["none", "demucs"], {"default": "demucs"}),
                "skip_noise_reduction": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Отключить шумоподавление (по умолчанию включено, т.к. ломает звук, если диктор говорит с 1-й секунды)."
                }),
                "min_segment_duration_s": ("FLOAT", {
                    "default": 0.3,
                    "min": 0.0,
                    "max": 5.0,
                    "step": 0.05,
                    "tooltip": "Отбрасывать сегменты короче указанного порога (сек)."
                }),
                # ----------- DIARIZATION SETTINGS --------------------
                "enable_speaker_diarization": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Разделить речь по спикерам (speaker diarization)."
                }),
                "speaker_label_style": (["none", "[1]", "{1}"], {
                    "default": "none",
                    "tooltip": "Формат метки спикера в тексте сегмента: none, [1], {1}."
                }),
                # ----------- WHISPER X / FASTER SETTINGS -----------
                "use_faster_whisper": ("BOOLEAN", {"default": True, "tooltip": "Использовать Faster-Whisper (намного быстрее и стабильнее на Windows)."}),
                "enable_vad": ("BOOLEAN", {"default": True, "tooltip": "Включить VAD-фильтрацию (голос/тишина) для предотвращения галлюцинаций в тишине."}),
                "vad_threshold": ("FLOAT", {"default": 0.3, "min": 0.0, "max": 1.0, "step": 0.05, "tooltip": "Порог VAD для Faster-Whisper. 0.5 - строго, 0.2 - мягко."}),
                "repetitive_filter": ("BOOLEAN", {"default": True, "tooltip": "Удалять сегменты с повторяющимся текстом (галлюцинации Whisper)."}),
                # ---------------------------------------------------------
                "audio_path": ("STRING", {"default": "", "tooltip": "Путь к отдельному аудио‑файлу (если видео без звука)."}),
            },
        }

    RETURN_TYPES = (
        "STRING",  # original_full_text_path
        "STRING",  # original_full_text_no_punct_path
        "STRING",  # segments_json_path
        "STRING",  # translated_segments_dir
        "STRING",  # project_root_dir
        "STRING",  # status
        "STRING",  # segments_summary
        "STRING",  # segments_json_preview
    )
    RETURN_NAMES = (
        "original_full_text_path",
        "original_full_text_no_punct_path",
        "segments_json_path",
        "translated_segments_dir",
        "project_root_dir",
        "status",
        "segments_summary",
        "segments_json_preview",
    )
    FUNCTION = "process"
    CATEGORY = "VideoDubbing"

    # -----------------------------------------------------------------
    #   Основная функция
    # -----------------------------------------------------------------
    def process(self,
                video_path,
                source_language,
                whisper_model,
                use_gpu,
                detect_silence,
                silence_threshold_db,
                min_silence_duration_s,
                fallback_initial_pause_ms,
                long_pause_min_duration_s,
                merge_segments_factor,
                max_pause_duration_for_merge_s,
                clean_temp,
                clear_cache,
                remove_punctuation,
                process_now,
                separation_method,
                skip_noise_reduction,
                min_segment_duration_s,
                enable_speaker_diarization,
                speaker_label_style,
                use_faster_whisper=True,
                enable_vad=True,
                vad_threshold=0.3,
                repetitive_filter=True,
                custom_projects_dir="",
                trim_video_start_ms=0,
                trim_video_end_ms=0,
                audio_path=""):
        # -------------------------------------------------------------
        # 0️⃣  Быстрый выход, если процесс не активирован
        # -------------------------------------------------------------
        if not process_now:
            return ("", "", "", "", "", "Set process_now=True to start processing", "", "")

        # -------------------------------------------------------------
        # 1️⃣  Очистка пути к видео
        # -------------------------------------------------------------
        video_path = strip_quotes(video_path)
        if not video_path:
            return ("", "", "", "", "", "Error: No input video path provided", "", "")
        if not os.path.isfile(video_path):
            return ("", "", "", "", "", f"Error: Video file not found at {video_path}", "", "")

        device_str = "cuda" if use_gpu and torch.cuda.is_available() else "cpu"
        log = [f"🔧 Device: {device_str}", f"🧠 PyTorch: {torch.__version__}"]

        try:
            # ---------------------------------------------------------
            # 2️⃣  Формируем структуру проекта
            # ---------------------------------------------------------
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            
            # Включаем обрезку в имя папки для изоляции проектов
            if trim_video_start_ms > 0 or trim_video_end_ms > 0:
                base_name += f"_{trim_video_start_ms}_{trim_video_end_ms}"
                
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', base_name)

            if custom_projects_dir:
                # Use user-specified path if provided
                project_dir = os.path.join(custom_projects_dir, safe_name)
            else:
                # Default behavior
                input_dir = folder_paths.get_input_directory()
                project_dir = os.path.join(input_dir, "VideoDubbingProjects", safe_name)

            # ---------- Очистка кэша, если пользователь просил ----------
            if clear_cache and os.path.isdir(project_dir):
                log.append("⚠️ clear_cache=True – wiping project directory")
                try:
                    shutil.rmtree(project_dir)
                except Exception as e:
                    log.append(f"❌ Failed to clear project dir: {e}")
                    log.append(traceback.format_exc())
                    return ("", "", "", "", "", "\n".join(log), "", "")

            # ---------- Создаём нужные директории ----------
            ensure_dir(project_dir)

            temp_audio_dir   = os.path.join(project_dir, "TempAudio")
            text_segments_dir = os.path.join(project_dir, "TextSegments")
            origin_text_dir   = os.path.join(text_segments_dir, "origin")
            translate_dir     = os.path.join(text_segments_dir, "translate")
            separation_dir    = os.path.join(project_dir, "SeparatedAudio")
            speaker_audio_dir = os.path.join(project_dir, "SpeakerAudio")

            for d in (temp_audio_dir, origin_text_dir, translate_dir, separation_dir):
                ensure_dir(d)

            # ---------------------------------------------------------
            # 3️⃣  Пути к файлам полного текста
            # ---------------------------------------------------------
            original_full_txt_path          = os.path.join(project_dir, "original_full_text.txt")
            original_full_txt_no_punct_path = os.path.join(project_dir, "original_full_text_no_punct.txt")

            # ---------------------------------------------------------
            # 4️⃣  Извлекаем аудио (полный оригинал или по Range)
            # ---------------------------------------------------------
            from .utils import get_audio_source
            
            audio_source = get_audio_source(video_path, audio_path, log_list=log)
            if not audio_source:
                log.append("❌ Error: No audio source found (video is silent and no fallback exists)")
                return ("", "", "", "", "", "\n".join(log), "", "")

            temp_wav_path = os.path.join(temp_audio_dir, f"{safe_name}_tmp.wav")
            log.append(f"🎧 Extracting audio from {os.path.basename(audio_source)} (trim: {trim_video_start_ms}ms to {trim_video_end_ms}ms)…")
            
            try:
                original_audio = extract_audio(audio_source, temp_wav_path, trim_video_start_ms, trim_video_end_ms)
            except Exception as e:
                log.append(f"❌ Failed to extract audio: {e}")
                return ("", "", "", "", "", "\n".join(log), "", "")
                
            total_duration_s = len(original_audio) / 1000.0
            log.append(f"✅ Audio extracted – duration {fmt_sec(total_duration_s)} s")

            # ---------------------------------------------------------
            # 5️⃣  Выделяем чистый голос (по выбранному методу)
            # ---------------------------------------------------------
            # По‑умолчанию будем использовать оригинальный микс для Whisper
            processed_wav_path = temp_wav_path
            audio_preprocess_info = {
                "separation_method": "none",
                "noise_reduction_applied": False,
                "final_audio_path": str(temp_wav_path),
            }

            if separation_method == "demucs":
                log.append("🔊 Выделяем голос c помощью Demucs (Optimized)…")
                
                vocal_path = separate_vocals_demucs(
                    pathlib.Path(temp_wav_path), pathlib.Path(separation_dir),
                    audio_segment=original_audio
                )
                log.append(f"✅ Вокальная дорожка получена → {vocal_path}")

                # Шумоподавление – **по желанию**
                if not skip_noise_reduction:
                    denoise_path = pathlib.Path(separation_dir) / "vocals_denoised.wav"
                    denoise_audio(vocal_path, denoise_path)
                    log.append("✅ Шумоподавление выполнено")
                    final_vocal_path = str(denoise_path)
                    nr_applied = True
                else:
                    final_vocal_path = str(vocal_path)
                    log.append("⚡ Шумоподавление **ОТКЛЮЧЕНО** (skip_noise_reduction=True)")
                    nr_applied = False

                # Теперь используем чистый вокал для Whisper и Silence Detection!
                processed_wav_path = final_vocal_path

                audio_preprocess_info = {
                    "separation_method": str(separation_method),
                    "separated_audio_path": str(vocal_path),
                    "noise_reduction_applied": nr_applied,
                    "final_audio_path": final_vocal_path,
                }

            else:
                log.append("🔇 Separation disabled – копируем оригинальное аудио для сохранности")
                final_vocal_path = os.path.join(separation_dir, "original_audio.wav")
                shutil.copy2(temp_wav_path, final_vocal_path)
                processed_wav_path = final_vocal_path
                audio_preprocess_info["final_audio_path"] = final_vocal_path
                audio_preprocess_info["separated_audio_path"] = final_vocal_path

            # ---------------------------------------------------------
            # Загружаем аудио для детекции тишины (оригинал или вокал)
            # ---------------------------------------------------------
            if processed_wav_path != temp_wav_path:
                audio_for_silence = AudioSegment.from_wav(processed_wav_path)
            else:
                audio_for_silence = original_audio

            # ---------------------------------------------------------
            # 6️⃣  Поиск пауз (если включено)
            # ---------------------------------------------------------
            all_pauses = []
            initial_pause_s = 0.0

            if detect_silence:
                log.append(
                    f"🔎 Detecting all silences (threshold={silence_threshold_db} dBFS, min={min_silence_duration_s}s)…"
                )
                all_pauses = detect_all_pauses(
                    audio_for_silence,
                    min_silence_s=min_silence_duration_s,
                    silence_thresh_db=silence_threshold_db,
                )
                if all_pauses and all_pauses[0]["start_s"] == 0.0:
                    initial_pause_s = all_pauses[0]["duration_s"]
                    all_pauses = all_pauses[1:]
                log.append(
                    f"✅ Detected {len(all_pauses)} inter‑segment pauses + initial {fmt_sec(initial_pause_s)} s"
                )
            else:
                log.append("⚠️ Silence detection disabled – assuming no pauses")

            # ---------------------------------------------------------
            # 7️⃣  Fallback‑начальная пауза
            # ---------------------------------------------------------
            if initial_pause_s == 0.0 and fallback_initial_pause_ms > 0:
                initial_pause_s = fallback_initial_pause_ms / 1000.0
                log.append(
                    f"⚙️ No initial silence – using fallback {fallback_initial_pause_ms} ms ({fmt_sec(initial_pause_s)} s)"
                )

            # ---------------------------------------------------------
            # 8️⃣  Длинные паузы → главы
            # ---------------------------------------------------------
            long_pauses = []
            if detect_silence:
                log.append(f"🔎 Detecting LONG pauses (min {long_pause_min_duration_s}s)…")
                long_pauses = detect_long_pauses(
                    audio_for_silence,
                    min_pause_sec=long_pause_min_duration_s,
                    silence_thresh_db=silence_threshold_db,
                )
                log.append(f"✅ Detected {len(long_pauses)} long pauses")
            else:
                log.append("⚠️ Silence detection disabled – chapters will be 1 whole audio")

            chapters = build_chapters_from_long_pauses(long_pauses, total_duration_s)
            log.append(f"✅ Built {len(chapters)} chapter(s)")

            # ---------------------------------------------------------
            # 9️⃣  Whisper‑транскрипция (полный текст + сегменты)
            # ---------------------------------------------------------
            if use_faster_whisper:
                log.append(f"🔧 Loading Faster-Whisper model \"{whisper_model}\" on {device_str}")
                model = get_faster_whisper_model(whisper_model, device_str)
            else:
                log.append(f"🔧 Loading Standard Whisper model \"{whisper_model}\" on {device_str}")
                model = get_whisper_model(whisper_model, device_str)

            log.append(f"🗣 Transcribing whole audio (Faster={use_faster_whisper}, VAD={enable_vad})…")
            wh_res = transcribe_whisper(
                model,
                processed_wav_path,
                source_language,
                use_faster=use_faster_whisper,
                vad_filter=enable_vad,
                vad_threshold=vad_threshold
            )
            full_original_text = wh_res.get("text", "")
            whisper_segments   = wh_res.get("segments", [])

            # ---------------------------------------------------------
            # 🔀  При необходимости склеиваем сегменты Whisper
            # ---------------------------------------------------------
            if merge_segments_factor > 1:
                log.append(
                    f"🔀 Merging Whisper segments (factor={merge_segments_factor}, max_pause={max_pause_duration_for_merge_s}s)…"
                )
                whisper_segments, all_pauses = merge_whisper_segments(
                    whisper_segments,
                    all_pauses,
                    factor=merge_segments_factor,
                    max_pause_duration_s=max_pause_duration_for_merge_s,
                )
                log.append(
                    f"✅ Merged → {len(whisper_segments)} segments, {len(all_pauses)} pauses"
                )
            else:
                log.append("⚙️ No merging requested (merge_segments_factor=1)")

            # ---------------------------------------------------------
            # 10️⃣  Диаризация, если включена
            # ---------------------------------------------------------
            speaker_turns = []
            if enable_speaker_diarization:
                # Для диаризации нужен чистый вокал – используем тот, который уже
                # создали в блоке separation_method (или будем повторно достать, если separation=none)
                vocal_for_diarization = (
                    pathlib.Path(separation_dir) / "vocals.wav"
                    if (pathlib.Path(separation_dir) / "vocals.wav").exists()
                    else pathlib.Path(temp_wav_path)   # fallback – весь микс
                )
                log.append(f"🔎 Running speaker diarization on {vocal_for_diarization}")
                try:
                    speaker_turns = run_pyannote_diarization(vocal_for_diarization)
                except RuntimeError as e:
                    # Специальный маркер, который мы выбрасываем из run_pyannote_diarization
                    if str(e) == "DIARIZATION_GATED":
                        log.append(
                            "⚠️ Diarization model is gated – skipping speaker diarisation. "
                            "Set a valid HF_TOKEN with access or disable the option."
                        )
                        enable_speaker_diarization = False
                        speaker_turns = []
                    else:
                        raise
                else:
                    log.append(f"✅ Diarization produced {len(speaker_turns)} speaker turns")
                    # Присваиваем спикеров каждому Whisper‑сегменту
                    whisper_segments = assign_speakers(whisper_segments, speaker_turns)
                    log.append("✅ Speaker IDs added to Whisper segments")
                    # Папка для аудио‑файлов отдельных спикеров (удобно для отладки)
                    ensure_dir(speaker_audio_dir)

            # ---------------------------------------------------------
            # 11️⃣  Сохраняем два варианта полного текста
            # ---------------------------------------------------------
            with open(original_full_txt_path, "w", encoding="utf-8") as f:
                f.write(full_original_text)

            full_original_no_punct = _remove_punct(full_original_text)
            with open(original_full_txt_no_punct_path, "w", encoding="utf-8") as f:
                f.write(full_original_no_punct)

            log.append("✅ Full original texts saved (with & without punctuation)")

            # ---------------------------------------------------------
            # 12️⃣  Формируем однородные сегменты (с учётом диаризации)
            # ---------------------------------------------------------
            enriched_segments = []
            tolerance_s = 0.15

            for idx, seg in enumerate(whisper_segments, start=1):
                start_s = seg["start"]
                end_s = seg["end"]
                duration_s = end_s - start_s

                txt_original = seg["text"].strip()
                txt_clean = _remove_punct(txt_original)

                # ------------------- Фильтр галлюцинаций (повторы) --------------
                if repetitive_filter and len(txt_original.split()) > 3:
                    # Если текст состоит из одного и того же слова, повторенного много раз
                    words = txt_original.lower().split()
                    unique_words = set(words)
                    if len(unique_words) == 1 or (len(words) > 5 and len(unique_words) < 3):
                        log.append(f"⚠️ Skipping repetitive hallucination {idx}: '{txt_original}'")
                        continue

                txt_for_segment = txt_clean if remove_punctuation else txt_original

                # ------------------- Фильтр коротких сегментов -------------------
                if duration_s < min_segment_duration_s:
                    log.append(f"⚠️ Skipping short segment {idx} ({duration_s:.3f}s)")
                    continue

                # ------------------- Фильтр «только цифры» --------------------
                if txt_for_segment.isdigit():
                    log.append(f"⚠️ Skipping digit‑only segment {idx} ('{txt_for_segment}')")
                    continue

                # ------------------- Пропуск полностью пустого текста -----------
                if not txt_for_segment:
                    log.append(f"⚠️ Skipping empty segment {idx}")
                    continue

                # ------------------- Счёт слов и WPS -------------------------
                word_cnt = len(txt_for_segment.split())
                wps = word_cnt / duration_s if duration_s > 0 else 0.0

                # ------------------- Спикер (если есть) -------------------------
                speaker_id = seg.get("speaker_id", "SPEAKER_00")
                speaker_prefix = format_speaker_label(speaker_id, speaker_label_style) if enable_speaker_diarization else ""

                # Записываем в файл
                seg_fname = f"txtsegments_{source_language}_{idx:03d}.txt"
                seg_path = os.path.join(origin_text_dir, seg_fname)
                with open(seg_path, "w", encoding="utf-8") as f:
                    f.write(speaker_prefix + txt_for_segment)

                # Пауза перед сегментом
                pause_before_s = 0.0
                for p in all_pauses:
                    if abs(p["end_s"] - start_s) <= tolerance_s:
                        pause_before_s = p["duration_s"]
                        break
                    if p["start_s"] <= start_s <= p["end_s"]:
                        pause_before_s = p["end_s"] - start_s
                        break

                # ------------------- Текст с префиксом -------------------------
                original_text_with_prefix = speaker_prefix + (txt_for_segment if remove_punctuation else txt_original)

                enriched_segments.append({
                    "index": idx,
                    "original_start_s": round(start_s, 3),
                    "original_end_s":   round(end_s, 3),
                    "original_duration_s": round(duration_s, 3),

                    "original_text": txt_for_segment
                    if remove_punctuation
                    else txt_original,

                    # добавляем текст с префиксом спикера
                    "original_text_with_speaker_prefix": original_text_with_prefix,

                    # добавляем speaker_id только если диаризация включена
                    **({"speaker_id": speaker_id} if enable_speaker_diarization else {}),

                    "original_word_count": word_cnt,
                    "original_words_per_second": round(wps, 2),
                    "pause_before_s": round(pause_before_s, 3),

                    "text_file_path": seg_path,
                    "translated_text": "",
                    "translated_text_file_path": "",
                    "generated_audio_file_path": ""
                })

                log.append(
                    f"📦 segment {idx}: dur={fmt_sec(duration_s)}s, words={word_cnt}, wps={wps:.2f}, pause={fmt_sec(pause_before_s)}s, speaker={speaker_id if enable_speaker_diarization else 'N/A'}"
                )

                # ------------------- Сохраняем отдельные аудио‑файлы спикера (опция) -------------------
                if enable_speaker_diarization:
                    try:
                        # Источник для вырезки – уже полученный вокал (если был) иначе оригинал
                        if separation_method == "demucs":
                            source_vocal = pathlib.Path(separation_dir) / "vocals.wav"
                        else:
                            source_vocal = pathlib.Path(temp_wav_path)   # fallback – весь микс

                        src_data, src_sr = sf.read(str(source_vocal))
                        start_idx = int(start_s * src_sr)
                        end_idx = int(end_s * src_sr)
                        seg_audio = src_data[start_idx:end_idx]

                        speaker_file = pathlib.Path(speaker_audio_dir) / f"{speaker_id}_{idx:03d}.wav"
                        sf.write(str(speaker_file), seg_audio, src_sr)
                    except Exception as e:
                        log.append(f"⚠️ Failed to save speaker audio for segment {idx}: {e}")

            # ---------------------------------------------------------
            # 13️⃣  Убираем «концевые» паузы после последнего сегмента
            # ---------------------------------------------------------
            if enriched_segments:
                speech_end = enriched_segments[-1]["original_end_s"]
                filtered_pauses = [
                    p for p in all_pauses if p["start_s"] <= speech_end
                ]
                if len(filtered_pauses) != len(all_pauses):
                    removed = len(all_pauses) - len(filtered_pauses)
                    log.append(f"⚠️ Removed {removed} trailing pause(s) after speech end")
            else:
                speech_end = 0.0
                filtered_pauses = []
                log.append("⚠️ No speech segments detected – JSON will contain 0 segments")

            # ---------------------------------------------------------
            # 14️⃣  Сохраняем JSON‑проект (добавляем info о диаризации)
            # ---------------------------------------------------------
            project_info = {
                "original_video_path": video_path,
                "original_video_name": os.path.basename(video_path),
                "project_dir": project_dir,
                "source_language": source_language,
                "target_language": "",
                "original_total_duration_s": round(speech_end, 3), # This is duration of the extracted chunk
                "trim_video_start_ms": trim_video_start_ms,
                "trim_video_end_ms": trim_video_end_ms,
                "initial_pause_s": round(initial_pause_s, 3),
                "orig_pauses": filtered_pauses,
                "chapters": chapters,
                "segments": enriched_segments,
                "original_full_text": full_original_text,
                "original_full_text_no_punct": full_original_no_punct,
                "audio_preprocess": audio_preprocess_info,
                "diarization": {
                    "enabled": enable_speaker_diarization,
                    "method": "pyannote/speaker-diarization" if enable_speaker_diarization else None,
                    "speaker_label_style": speaker_label_style,
                    "turns": speaker_turns,   # может быть пустым, но полезно для отладки
                },
            }

            seg_json_path = os.path.join(project_dir, "segments.json")
            save_json(project_info, seg_json_path)
            log.append(f"✅ Segments JSON saved → {seg_json_path}")

            # ---------------------------------------------------------
            # 15️⃣  Очистка временных аудио‑файлов
            # ---------------------------------------------------------
            if clean_temp:
                cleanup_dir(temp_audio_dir)
                log.append("🧹 TempAudio folder deleted (clean_temp=True)")

            # ---------------------------------------------------------
            # 16️⃣  Финальная сводка и preview
            # ---------------------------------------------------------
            summary = (
                f"Segments: {len(enriched_segments)}, "
                f"Pauses: {len(filtered_pauses)}, "
                f"Chapters: {len(chapters)}"
            )
            preview = json.dumps(enriched_segments[:2], ensure_ascii=False, indent=2)
            if len(enriched_segments) > 2:
                preview += "\n... (more segments)"

            log.append("🎉 Analysis completed successfully")
            status = "\n".join(log)

            return (
                original_full_txt_path,
                original_full_txt_no_punct_path,
                seg_json_path,
                translate_dir,
                project_dir,
                status,
                summary,
                preview,
            )

        except Exception as e:
            log.append(f"❌ Critical failure: {e}")
            log.append(traceback.format_exc())
            return ("", "", "", "", "", "\n".join(log), "", "")


# ----------------------------------------------------------------------
# Регистрация ноды
# ----------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "AnalyzeVideoForDubbingPrep": AnalyzeVideoForDubbingPrep
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AnalyzeVideoForDubbingPrep": "Analyze Video for Dubbing Prep"
}