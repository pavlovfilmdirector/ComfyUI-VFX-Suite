# utils.py
# ---------------------------------------------------------------
# Универсальные вспомогательные функции для VideoDubbing‑pipelines.
# Поддерживают:
#   • извлечение аудио из video‑файла,
#   • детекцию коротких и длинных пауз,
#   • построение "глав" (big‑pauses),
#   • объединение Whisper‑сегментов,
#   • кеширование Whisper‑моделей,
#   • растяжку аудио (ffmpeg‑atempo, rubberband),
#   • формирование финального тайм‑лейна и запись JSON‑отчётов.
# ---------------------------------------------------------------

import os
import re
import json
import uuid
import logging
import subprocess
import hashlib
import traceback
from typing import List, Dict, Tuple, Any

import numpy as np
import torch
import torchaudio
import faster_whisper
import folder_paths
from pydub import AudioSegment
from pydub.silence import detect_silence as pydub_detect_silence
from typing import List, Dict, Tuple
import logging

logger = logging.getLogger("VideoDubbing.utils")

# -----------------------------------------------------------------
# 0️⃣  Общие файловые утилиты
# -----------------------------------------------------------------
def ensure_dir(path: str) -> None:
    """Создать каталог, если его нет (рекурсивно)."""
    os.makedirs(path, exist_ok=True)


def strip_quotes(s: str) -> str:
    """Удалить начинающие/заканчивающие кавычки и пробельные символы."""
    return s.strip().strip('"\'')   # удаляем пробелы и кавычки


def cleanup_dir(dir_path: str) -> None:
    """Рекурсивно удалить временную директорию (если существует)."""
    if os.path.isdir(dir_path):
        import shutil
        shutil.rmtree(dir_path, ignore_errors=True)


def save_json(data: dict, path: str) -> None:
    """Записать dict в JSON‑файл (pretty‑print, UTF‑8)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_json(path: str) -> dict:
    """Загрузить JSON‑файл и вернуть его содержимое."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_sec(sec: float) -> str:
    """Отформатировать секунды с 3‑мя знаками после запятой."""
    return f"{sec:.3f}"


# -----------------------------------------------------------------
# 0️⃣  Проверка наличия аудио‑потоков и поиск источника
# -----------------------------------------------------------------
def has_audio_stream(file_path: str) -> bool:
    """Возвращает True, если в файле есть хотя бы один аудио‑поток."""
    if not file_path or not os.path.exists(file_path):
        return False
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return "audio" in result.stdout.lower()
    except Exception:
        return False

def get_audio_source(video_path: str, manual_audio_path: str = None, log_list: list = None) -> str:
    """
    Пытается найти подходящий источник аудио для видео.
    1. Если задан manual_audio_path – использует его.
    2. Если само видео имеет звук – возвращает его.
    3. Иначе ищет альтернативы (_360 вместо _1080, .m4a, .mp3 и т.д.)
    """
    # 1. Ручной ввод
    manual = (manual_audio_path or "").strip()
    if manual and os.path.exists(manual) and has_audio_stream(manual):
        return manual

    # 2. Само видео
    if has_audio_stream(video_path):
        return video_path

    # 3. Авто‑поиск (fallback)
    if log_list is not None:
        log_list.append("⚠️ Video file has no audio stream. Searching for fallback...")
        
    v_dir = os.path.dirname(video_path)
    v_base = os.path.basename(video_path)
    
    fallbacks = []
    # Замена _1080 -> _360
    if "_1080" in v_base:
        fallbacks.append(os.path.join(v_dir, v_base.replace("_1080", "_360")))
    
    # Расширения
    v_name_no_ext = os.path.splitext(v_base)[0]
    for ext in [".m4a", ".mp3", ".wav", ".aac"]:
        fallbacks.append(os.path.join(v_dir, v_name_no_ext + ext))
        
    for fb in fallbacks:
        if os.path.exists(fb) and has_audio_stream(fb):
            if log_list is not None:
                log_list.append(f"🔍 Auto-fallback found audio in: {os.path.basename(fb)}")
            return fb
            
    return None


# -----------------------------------------------------------------
# 1️⃣  Работа с аудио‑файлом (ffmpeg)
# -----------------------------------------------------------------
def extract_audio(video_path: str, out_wav_path: str, trim_start_ms: int = 0, trim_end_ms: int = 0, sample_rate: int = 16000) -> AudioSegment:
    """
    Выполняет ffmpeg -> mono 16-bit PCM WAV.
    Использует метод 'Точного декодирования' (медленный поиск) для 100% надежности на битых файлах.
    """
    print(f"🛡️ Shield Extraction Active: Запуск точного декодирования FFmpeg (SR={sample_rate})...", flush=True)
    # ШАГ 1: Извлекаем ПОЛНЫЙ звук из видео (Защищенный режим)
    full_wav_path = out_wav_path + ".full.wav"
    cmd_full = [
        "ffmpeg",
        "-err_detect", "ignore_err",
        "-ignore_unknown",
        "-fflags", "+genpts",
        "-i", video_path,
        "-vn",
        "-af", "aresample=async=1",
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-y",
        full_wav_path
    ]
    
    print(f"🛡️ ЩИТ (Этап 1/2): Извлечение полного аудиопотока из {os.path.basename(video_path)}...", flush=True)
    result = subprocess.run(cmd_full, capture_output=True, text=True, timeout=900)
    
    if not os.path.exists(full_wav_path) or os.path.getsize(full_wav_path) < 1000:
        # Попытка быстрого фоллбэка, если щит не сработал
        print("⚠️ Полное извлечение со щитом не дало результата. Пробую упрощенный метод...", flush=True)
        subprocess.run(["ffmpeg", "-i", video_path, "-vn", "-acodec", "pcm_s16le", "-ar", str(sample_rate), "-ac", "1", "-y", full_wav_path], capture_output=True)

    if not os.path.exists(full_wav_path) or os.path.getsize(full_wav_path) < 1000:
        raise RuntimeError(f"❌ Не удалось извлечь звук: {result.stderr}")

    # ШАГ 2: Обрезаем чистый WAV через pydub
    full_audio = AudioSegment.from_wav(full_wav_path)
    print(f"✂️ ЩИТ (Этап 2/2): Обрезка WAV ({trim_start_ms}ms - {trim_end_ms}ms). Полный файл: {len(full_audio)/1000.0:.1f}с", flush=True)
    
    actual_end = trim_end_ms if trim_end_ms > trim_start_ms else len(full_audio)
    trimmed_audio = full_audio[trim_start_ms:actual_end]
    
    trimmed_audio.export(out_wav_path, format="wav")
    
    # Удаляем тяжелый временный файл
    try:
        os.remove(full_wav_path)
    except:
        pass
        
    return trimmed_audio


# -----------------------------------------------------------------
# 2️⃣  Поиск пауз (краткие и длинные)
# -----------------------------------------------------------------
def detect_all_pauses(audio: AudioSegment,
                      min_silence_s: float,
                      silence_thresh_db: float) -> List[Dict]:
    """
    Возвращает **все** паузы, обнаруженные pydub‑детектором,
    независимо от их длительности.
    """
    min_len_ms = int(min_silence_s * 1000)
    raw = pydub_detect_silence(
        audio,
        min_silence_len=min_len_ms,
        silence_thresh=silence_thresh_db,
    )
    pauses = []
    for start_ms, end_ms in raw:
        pauses.append({
            "start_s": round(start_ms / 1000.0, 3),
            "end_s":   round(end_ms   / 1000.0, 3),
            "duration_s": round((end_ms - start_ms) / 1000.0, 3)
        })
    return pauses


def detect_long_pauses(audio: AudioSegment,
                       min_pause_sec: float = 0.1,
                       silence_thresh_db: float = -40.0) -> List[Dict]:
    """
    Возвращает только **длинные** паузы (>= ``min_pause_sec``).
    """
    min_len_ms = int(min_pause_sec * 1000)
    raw = pydub_detect_silence(
        audio,
        min_silence_len=min_len_ms,
        silence_thresh=silence_thresh_db,
    )
    long_pauses = []
    for start_ms, end_ms in raw:
        dur = (end_ms - start_ms) / 1000.0
        if dur >= min_pause_sec:
            long_pauses.append({
                "start_s": round(start_ms / 1000.0, 3),
                "end_s":   round(end_ms   / 1000.0, 3),
                "duration_s": round(dur, 3)
            })
    return long_pauses


def build_chapters_from_long_pauses(long_pauses: List[Dict],
                                   audio_duration_s: float) -> List[Dict]:
    """
    На основе списка ``long_pauses`` формирует «главы» (chapters).
    Если длинных пауз нет – создаётся одна глава, охватывающая всё аудио.
    Вернёт список словарей:
        {"index": int, "start_s": float, "end_s": float,
         "duration_s": float, "long_pause_before_s": float}
    """
    if not long_pauses:
        return [{
            "index": 1,
            "start_s": 0.0,
            "end_s": audio_duration_s,
            "duration_s": audio_duration_s,
            "long_pause_before_s": 0.0,
        }]

    chapters = []
    chapter_start = 0.0
    for i, pause in enumerate(long_pauses):
        chapter_end = pause["start_s"]
        chapters.append({
            "index": i + 1,
            "start_s": round(chapter_start, 3),
            "end_s":   round(chapter_end,   3),
            "duration_s": round(chapter_end - chapter_start, 3),
            "long_pause_before_s": pause["duration_s"] if i > 0 else 0.0,
        })
        chapter_start = pause["end_s"]

    # Последняя глава (после последней длинной паузы)
    chapters.append({
        "index": len(long_pauses) + 1,
        "start_s": round(chapter_start, 3),
        "end_s": round(audio_duration_s, 3),
        "duration_s": round(audio_duration_s - chapter_start, 3),
        "long_pause_before_s": long_pauses[-1]["duration_s"],
    })
    return chapters


# -----------------------------------------------------------------
# 3️⃣  Whisper‑модель (кеширование)
# -----------------------------------------------------------------
_model_cache: Dict[str, Any] = {}
_fw_model_cache: Dict[str, Any] = {}

def get_whisper_model(name: str, device: str):
    """
    Возвращает объект Whisper‑модели (стандартный openai-whisper).
    """
    import whisper
    key = f"{name}_{device}"
    if key not in _model_cache:
        paths = folder_paths.get_folder_paths("whisper")
        models_dir = paths[0] if paths else os.path.join(folder_paths.MODELS_DIR, "Whisper")
        os.makedirs(models_dir, exist_ok=True)
        local_path = os.path.join(models_dir, f"{name}.pt")
        
        if os.path.exists(local_path):
            logger.info(f"Loading Whisper model from local file: {local_path}")
            try:
                _model_cache[key] = whisper.load_model(local_path, device=device)
            except Exception as e:
                logger.warning(f"Failed to load local model {local_path}: {str(e)}")
                _model_cache[key] = whisper.load_model(name, device=device, download_root=models_dir)
        else:
            _model_cache[key] = whisper.load_model(name, device=device, download_root=models_dir)
            
    return _model_cache[key]

def get_faster_whisper_model(name: str, device: str, compute_type: str = "float16"):
    """
    Возвращает объект FasterWhisper (ctranslate2).
    """
    key = f"{name}_{device}_{compute_type}"
    if key not in _fw_model_cache:
        # Пытаемся найти в Whisper папку или скачать
        paths = folder_paths.get_folder_paths("whisper")
        models_dir = paths[0] if paths else os.path.join(folder_paths.MODELS_DIR, "Whisper")
        
        # Если это GPU, то по умолчанию float16, если CPU – int8
        if device == "cpu":
            compute_type = "int8"
            
        logger.info(f"Loading Faster-Whisper model: {name} ({compute_type}) on {device}")
        _fw_model_cache[key] = faster_whisper.WhisperModel(
            name, 
            device=device, 
            compute_type=compute_type, 
            download_root=models_dir
        )
    return _fw_model_cache[key]


def transcribe_whisper(model,
                      wav_path: str,
                      language: str,
                      use_faster: bool = False,
                      vad_filter: bool = False,
                      vad_threshold: float = 0.3) -> dict:
    """
    Универсальная обёртка для Whisper и Faster-Whisper.
    """
    if use_faster and hasattr(model, "transcribe"):
        # Если модель - FasterWhisper, у неё есть свой transcribe
        # Но у OpenAI Whisper тоже есть .transcribe, проверим тип прямо
        if isinstance(model, faster_whisper.WhisperModel):
            logger.info(f"Transcribing with Faster-Whisper (VAD={vad_filter}, Threshold={vad_threshold})")
            segments, info = model.transcribe(
                wav_path,
                language=language,
                beam_size=5,
                word_timestamps=True,
                vad_filter=vad_filter,
                vad_parameters=dict(min_silence_duration_ms=500, threshold=vad_threshold) if vad_filter else None
            )
            
            # Конвертируем формат Faster-Whisper в стандартный дикт Whisper
            final_segments = []
            full_text = ""
            for s in segments:
                # s: Segment(start, end, text, words=None, ...)
                seg_dict = {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text,
                    "avg_logprob": getattr(s, "avg_logprob", 0.0),
                    "no_speech_prob": getattr(s, "no_speech_prob", 0.0),
                }
                if s.words:
                    seg_dict["words"] = [{"start": w.start, "end": w.end, "word": w.word, "probability": w.probability} for w in s.words]
                
                final_segments.append(seg_dict)
                full_text += s.text + " "
                
            return {
                "text": full_text.strip(),
                "segments": final_segments,
                "language": info.language
            }

    # Стандартный Whisper
    logger.info("Transcribing with Standard Whisper")
    return model.transcribe(
        wav_path,
        language=language,
        word_timestamps=True,
    )


# -----------------------------------------------------------------
# 4️⃣  Объединение (merge) Whisper‑сегментов
# -----------------------------------------------------------------
def merge_whisper_segments(whisper_segments: List[Dict],
                          original_pauses: List[Dict],
                          factor: int = 1,
                          max_pause_duration_s: float = 1.0 # <-- НОВЫЙ ПАРАМЕТР
                          ) -> Tuple[List[Dict], List[Dict]]:
    """
    Объединяет Whisper-сегменты в группы по factor штук, но НЕ объединяет,
    если пауза между двумя сегментами превышает max_pause_duration_s.
    Возвращает:
    * список новых (смёрженных или нет) сегментов,
    * список новых пауз между ними (корректные по длительности).
    Если factor <= 1 или max_pause_duration_s <= 0, просто копирует входные списки.
    """
    if factor <= 1 or max_pause_duration_s <= 0:
        return [s.copy() for s in whisper_segments], [p.copy() for p in original_pauses]

    merged_segments = []
    merged_pauses = []
    i = 0
    while i < len(whisper_segments):
        # Начинаем новую группу с текущего сегмента
        group_start_idx = i
        group = [whisper_segments[i]]
        group_start_s = whisper_segments[i]["start"]
        group_end_s = whisper_segments[i]["end"]

        # Пробуем добавить до (factor - 1) следующих сегментов
        j = i + 1
        while j < len(whisper_segments) and len(group) < factor:
            next_seg = whisper_segments[j]
            next_start_s = next_seg["start"]
            next_end_s = next_seg["end"]

            # Проверяем паузу между текущей группой и следующим сегментом
            # Считаем паузу от group_end_s до next_start_s
            pause_between_s = next_start_s - group_end_s

            # Проверяем, превышает ли пауза порог
            if pause_between_s > max_pause_duration_s:
                logger.info(
                    f"merge_whisper_segments: Pause ({pause_between_s:.3f}s) between segments "
                    f"{j-1} and {j} exceeds threshold ({max_pause_duration_s}s). "
                    "Stopping merge for current group."
                )
                break

            # Пауза в пределах нормы, добавляем сегмент в группу
            group.append(next_seg)
            group_end_s = next_end_s # Обновляем конец группы
            j += 1 # Переходим к следующему потенциальному сегменту для объединения

        # Объединяем текущую группу
        txt = " ".join(seg["text"].strip() for seg in group)
        # pause_before берём у первого сегмента группы (если он есть)
        pause_before = float(group[0].get("pause_before_s", 0.0))

        merged_segment = {
            "start": round(group_start_s, 3),
            "end": round(group_end_s, 3),
            "text": txt,
            "pause_before_s": round(pause_before, 3),
        }
        merged_segments.append(merged_segment)

        # Рассчитываем и добавляем паузу *после* этой объединённой группы
        # Это пауза между group_end_s и началом следующего сегмента (если он есть)
        # nxt - индекс первого сегмента *после* текущей группы
        nxt = j # j указывает на первый сегмент, который НЕ вошёл в группу, или на len(whisper_segments)
        if nxt < len(whisper_segments):
            next_start = whisper_segments[nxt]["start"]
            pause_dur = max(0.0, next_start - group_end_s)

            # Если в оригинальном списке пауз уже есть такая пауза – используем её длительность
            # Ищем паузу, которая начинается примерно в group_end_s и заканчивается примерно в next_start
            matching = next(
                (p for p in original_pauses
                 if abs(p["start_s"] - group_end_s) < 0.001 and abs(p["end_s"] - next_start) < 0.001),
                None
            )
            if matching:
                pause_dur = matching["duration_s"]

            merged_pauses.append({
                "start_s": round(group_end_s, 3),
                "end_s": round(next_start, 3),
                "duration_s": round(pause_dur, 3),
            })

        # Переходим к следующей группе, начиная с индекса j
        i = j

    return merged_segments, merged_pauses

# ... остальной код utils.py ...


# -----------------------------------------------------------------
# 5️⃣  Инструменты растяжки/сжатия аудио
# -----------------------------------------------------------------
def stretch_audio_ffmpeg_atempo(audio_segment: AudioSegment,
                               target_duration_ms: int,
                               tmp_dir: str) -> AudioSegment:
    """
    Растягивает/сжимает звук с помощью ffmpeg‑фильтра ``atempo``.
    Поддерживаемый диапазон коэффициента : 0.5 – 2.0 за один фильтр,
    поэтому формируется цепочка, если нужен более сильный эффект.
    """
    if len(audio_segment) == 0 or target_duration_ms == 0:
        return AudioSegment.silent(duration=target_duration_ms,
                                   frame_rate=audio_segment.frame_rate)

    rate = len(audio_segment) / target_duration_ms
    if abs(rate - 1.0) < 0.001:
        return audio_segment

    # Фильтры atempo работают только в диапазоне [0.5, 2.0]
    chain = []
    while rate > 2.0:
        chain.append("atempo=2.0")
        rate /= 2.0
    while rate < 0.5:
        chain.append("atempo=0.5")
        rate *= 2.0
    chain.append(f"atempo={rate:.6f}")
    filter_str = ",".join(chain)

    in_path = os.path.join(tmp_dir, f"stretch_{uuid.uuid4().hex}.wav")
    out_path = os.path.join(tmp_dir, f"stretch_{uuid.uuid4().hex}_out.wav")
    audio_segment.export(in_path, format="wav")
    cmd = [
        "ffmpeg", "-y", "-i", in_path,
        "-filter:a", filter_str,
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)

    stretched = AudioSegment.from_wav(out_path)

    # Обрезаем/добавляем тишину до точного target_duration_ms
    if len(stretched) > target_duration_ms:
        stretched = stretched[:target_duration_ms]
    elif len(stretched) < target_duration_ms:
        stretched = stretched + AudioSegment.silent(
            duration=target_duration_ms - len(stretched),
            frame_rate=stretched.frame_rate,
        )

    # Убираем временные файлы
    for p in (in_path, out_path):
        try: os.remove(p)
        except OSError: pass
    return stretched


def stretch_audio_rubberband(audio_segment: AudioSegment,
                            target_duration_ms: int,
                            tmp_dir: str) -> AudioSegment:
    """
    Альтернатива через external‑utility ``rubberband-cli``.
    Позволяет задать произвольный коэффициент скорости.
    Требует, чтобы ``rubberband`` был установлен в системе.
    """
    if len(audio_segment) == 0 or target_duration_ms == 0:
        return AudioSegment.silent(duration=target_duration_ms,
                                   frame_rate=audio_segment.frame_rate)

    rate = len(audio_segment) / target_duration_ms
    speed = 1.0 / rate
    in_path = os.path.join(tmp_dir, f"rb_{uuid.uuid4().hex}.wav")
    out_path = os.path.join(tmp_dir, f"rb_{uuid.uuid4().hex}_out.wav")
    audio_segment.export(in_path, format="wav")
    cmd = [
        "rubberband",
        "-t", f"{speed:.6f}",   # target speed (1 = original)
        in_path,
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    stretched = AudioSegment.from_wav(out_path)

    # Точно подгоняем длительность
    if len(stretched) > target_duration_ms:
        stretched = stretched[:target_duration_ms]
    elif len(stretched) < target_duration_ms:
        stretched = stretched + AudioSegment.silent(
            duration=target_duration_ms - len(stretched),
            frame_rate=stretched.frame_rate,
        )
    for p in (in_path, out_path):
        try: os.remove(p)
        except OSError: pass
    return stretched


# -----------------------------------------------------------------
# 6️⃣  Утилиты для FinalizeDubbedAudioNode
# -----------------------------------------------------------------
def extract_audio_segment(audio: AudioSegment,
                          start_s: float,
                          end_s: float) -> AudioSegment:
    """
    Отрезать часть из ``audio`` по времени (секунды). Возвращает AudioSegment.
    """
    start_ms = int(round(start_s * 1000))
    end_ms   = int(round(end_s   * 1000))
    return audio[start_ms:end_ms]


def build_final_timeline(initial_pause_s: float,
                         aligned_text_segments: List[AudioSegment],
                         orig_pauses: List[Dict],
                         chapters: List[Dict]) -> Tuple[AudioSegment, List[Dict]]:
    """
    Собирает окончательный AudioSegment и список метаданных (timeline).
    ``aligned_text_segments`` – уже растянутые/сжатые текстовые части.
    ``orig_pauses`` – список оригинальных пауз (может включать длинные).
    ``chapters`` – список глав (для справки, но в текущем тайм‑лайне
    паузы уже присутствуют в ``orig_pauses``).

    Возврат: (final AudioSegment, timeline‑list), где каждый элемент:
        {"type": "text"/"pause",
         "content": <строка или длительность>,
         "start_s": float,
         "end_s":   float,
         "duration_s": float, …}
    """
    timeline: List[Dict] = []
    final = AudioSegment.empty()
    sr = aligned_text_segments[0].frame_rate if aligned_text_segments else 24000
    cursor = 0.0

    # ---- Начальная пауза
    if initial_pause_s > 0:
        lead = AudioSegment.silent(
            duration=int(round(initial_pause_s * 1000)),
            frame_rate=sr,
        )
        final += lead
        timeline.append({
            "type": "pause",
            "content": f"initial {initial_pause_s:.3f}s",
            "start_s": round(cursor, 3),
            "end_s":   round(cursor + initial_pause_s, 3),
            "duration_s": round(initial_pause_s, 3),
        })
        cursor += initial_pause_s

    # ---- Текст + паузы
    for i, seg in enumerate(aligned_text_segments):
        seg_dur = len(seg) / 1000.0
        final += seg
        timeline.append({
            "type": "text",
            "index": i + 1,
            "start_s": round(cursor, 3),
            "end_s":   round(cursor + seg_dur, 3),
            "duration_s": round(seg_dur, 3),
        })
        cursor += seg_dur

        # пауза после текста (если это не последний сегмент)
        if i < len(aligned_text_segments) - 1:
            if i < len(orig_pauses):
                pause_sec = orig_pauses[i]["duration_s"]
            else:
                # fallback – 0.5 s тишины
                pause_sec = 0.5
            pause_seg = AudioSegment.silent(
                duration=int(round(pause_sec * 1000)),
                frame_rate=sr,
            )
            final += pause_seg
            timeline.append({
                "type": "pause",
                "content": f"orig {pause_sec:.3f}s",
                "start_s": round(cursor, 3),
                "end_s":   round(cursor + pause_sec, 3),
                "duration_s": round(pause_sec, 3),
            })
            cursor += pause_sec

    return final, timeline


# -----------------------------------------------------------------
# 7️⃣  Хеш‑функция для IS_CHANGED (детерминистичный)
# -----------------------------------------------------------------
def stable_hash(*values) -> str:
    """
    Deterministic hash (MD5) от произвольного набора значений.
    Используется в `IS_CHANGED`, чтобы кеш в ComfyUI был предсказуем.
    """
    m = hashlib.md5()
    for v in values:
        m.update(str(v).encode("utf-8"))
    return m.hexdigest()


# -----------------------------------------------------------------
# 8️⃣  Пример функции‑обёртки для сохранения JSON‑отчётов
# -----------------------------------------------------------------
def write_finalized_report(base_dir: str,
                           base_name: str,
                           project_info: dict,
                           timeline: List[Dict],
                           aligned_info: List[Dict],
                           final_wav_path: str) -> str:
    """
    Записывает ``project_info`` с добавленными полями:
        * `finalized_segments` – тайм‑лайн,
        * `aligned_text_segments` – детали растяжения,
        * `final_audio_path`   – путь к wav‑файлу.
    Возвращает полный путь к записанному JSON.
    """
    out = dict(project_info)          # shallow copy
    out["finalized_segments"] = timeline
    out["aligned_text_segments"] = aligned_info
    out["final_audio_path"] = final_wav_path

    out_path = os.path.join(base_dir, f"{base_name}_finalized.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out_path


# -----------------------------------------------------------------
# 9️⃣  Минимальная «заглушка» для тестов (если вдруг понадобится)
# -----------------------------------------------------------------
if __name__ == "__main__":
    # Быстренький sanity‑check
    print("utils.py loaded – available helpers:")
    for name in sorted([n for n in globals() if not n.startswith("_")]):
        print("  •", name)
