# --------------------------------------------------------------
# finalize_dubbed_audio_from_files.py
# --------------------------------------------------------------
import os
import json
import uuid
import torch
import numpy as np
import logging
import traceback
import folder_paths
import re
from typing import List, Dict, Tuple, Any

from pydub import AudioSegment

# --------------------------------------------------------------
# Вспомогательные функции для обрезки тишины (новые)
# --------------------------------------------------------------
def _detect_leading_silence(sound: AudioSegment,
                           silence_thresh: float = -50.0,
                           min_silence_len: int = 200) -> int:
    """Возвращает количество миллисекунд тишины в начале аудио."""
    trim_ms = 0
    while trim_ms < len(sound):
        segment = sound[trim_ms:trim_ms + min_silence_len]
        if segment.dBFS > silence_thresh:            # звук уже слышен
            break
        trim_ms += min_silence_len
    return trim_ms


def _trim_start(sound: AudioSegment,
                silence_thresh: float = -50.0,
                min_silence_len: int = 200) -> AudioSegment:
    """Обрезает тишину **только в начале** дорожки."""
    start = _detect_leading_silence(sound, silence_thresh, min_silence_len)
    if start == 0:
        return sound
    return sound[start:]


def _trim_end(sound: AudioSegment,
              silence_thresh: float = -50.0,
              min_silence_len: int = 200) -> AudioSegment:
    """Обрезает тишину **только в конце** дорожки."""
    # Аналогично ищем тишину в начале реверса и отрезаем её в оригинале.
    end = _detect_leading_silence(sound.reverse(), silence_thresh, min_silence_len)
    if end == 0:
        return sound
    return sound[:len(sound) - end]


# --------------------------------------------------------------
# utils (оставляем без изменений)
# --------------------------------------------------------------
from .utils import (
    load_json,
    stretch_audio_ffmpeg_atempo,
    extract_audio,  # <-- добавлен
)

logger = logging.getLogger("FinalizeDubbedAudioFromFiles")

def _stretch(segment: AudioSegment, target_ms: int, tmp_dir: str, method: str) -> AudioSegment:
    """Обёртка для выбора метода растяжки."""
    if method == "ffmpeg_atempo":
        return stretch_audio_ffmpeg_atempo(segment, target_ms, tmp_dir)
    elif method == "rubberband":
        from .utils import stretch_audio_rubberband
        return stretch_audio_rubberband(segment, target_ms, tmp_dir)
    else:
        logger.warning(f"Unknown stretch method '{method}', using 'ffmpeg_atempo'.")
        return stretch_audio_ffmpeg_atempo(segment, target_ms, tmp_dir)


# --------------------------------------------------------------
# Основная нода
# --------------------------------------------------------------
class FinalizeDubbedAudioFromFiles:
    # ----------  Параметры, возвращаемые нодой  ----------
    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("final_audio", "saved_wav_path", "finalized_json_path", "status")
    FUNCTION = "finalize_dubbed_audio_from_files"
    CATEGORY = "VideoDubbing"

    @classmethod
    def INPUT_TYPES(s):
        """
        Обязательные параметры идут в фиксированном порядке.
        Новые переключатели помещены в `optional`, чтобы не смещать порядок.
        """
        return {
            "required": {
                "segments_json_path": ("STRING", {
                    "default": "", "tooltip": "Path to the original segments.json file."
                }),
                "synthesized_audio_folder": ("STRING", {
                    "default": "", "tooltip": "Folder with synthesized audio files."
                }),
                "process_now": ("BOOLEAN", {
                    "default": False, "tooltip": "Start processing."
                }),
                "stretch_method": (["ffmpeg_atempo", "rubberband"], {
                    "default": "ffmpeg_atempo", "tooltip": "Stretch method."
                }),
                "max_stretch_factor": ("FLOAT", {
                    "default": 2.0, "min": 1.0, "max": 10.0, "step": 0.1,
                    "tooltip": "Maximum stretch factor."
                }),
                "allow_stretching": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Allow stretching (if off – only compression)."
                }),
                "use_original_pauses": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Insert original pauses if available."
                }),
                "filename_prefix": ("STRING", {
                    "default": "final_dubbed",
                    "tooltip": "Prefix for saved final file."
                }),
                "save_final_audio": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Save the final wav file."
                }),
                "save_final_json": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Save detailed JSON report."
                }),
            },
            "optional": {                               # <<< NEW >>>
                "trim_start_silence": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Trim leading silence of each synthesized chunk."
                }),
                "trim_end_silence": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Trim trailing silence of each synthesized chunk."
                }),
                "silence_thresh": ("FLOAT", {
                    "default": -50.0,
                    "min": -100.0,
                    "max": 0.0,
                    "step": 0.5,
                    "tooltip": "Silence threshold in dBFS for trimming."
                }),
                "min_silence_len": ("INT", {
                    "default": 200,
                    "min": 10,
                    "max": 5000,
                    "step": 10,
                    "tooltip": "Minimum length (ms) of a silent segment to be trimmed."
                }),
                # <<< Два новых переключателя >>>
                "add_original_background": ("BOOLEAN", {
                    "default": True,
                    "tooltip": "Add original background music / accompaniment under the dub."
                }),
                "add_original_noise": ("BOOLEAN", {
                    "default": False,
                    "tooltip": "Add original background noise (high‑frequency hiss) under the dub."
                }),
                # <<< НОВЫЙ ПАРАМЕТР: ГРОМКОСТЬ ОРИГИНАЛА (%) >>>
                "original_audio_volume_percent": ("FLOAT", {
                    "default": 0.0,
                    "min": 0.0,
                    "max": 100.0,
                    "step": 1.0,
                    "tooltip": "Add original audio (full mix) at specified percentage of original volume."
                }),
            },
        }

    # --------------------------------------------------------------
    # Функция, вызываемая нодой.
    # Параметры из `optional` идут **последними**.
    # --------------------------------------------------------------
    def finalize_dubbed_audio_from_files(
        self,
        segments_json_path: str,
        synthesized_audio_folder: str,
        process_now: bool,
        stretch_method: str = "ffmpeg_atempo",
        max_stretch_factor: float = 2.0,
        allow_stretching: bool = True,
        use_original_pauses: bool = True,
        filename_prefix: str = "final_dubbed",
        save_final_audio: bool = True,
        save_final_json: bool = True,
        # ----- параметры optional -----
        trim_start_silence: bool = False,
        trim_end_silence: bool = False,
        silence_thresh: float = -50.0,
        min_silence_len: int = 200,
        # новые переключатели
        add_original_background: bool = True,
        add_original_noise: bool = False,
        original_audio_volume_percent: float = 0.0,
    ):
        # -----------------------------------------------------------------
        # 0️⃣ Флаг запуска
        # -----------------------------------------------------------------
        if not process_now:
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "Set process_now=True to start"

        # -----------------------------------------------------------------
        # 1️⃣ Проверка входов
        # -----------------------------------------------------------------
        if not os.path.isfile(segments_json_path):
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", f"Error: Original JSON not found at {segments_json_path}"

        if not os.path.isdir(synthesized_audio_folder):
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", f"Error: Synthesized audio folder not found at {synthesized_audio_folder}"

        log = ["🔧 FinalizeDubbedAudioFromFiles started"]
        logger.info("".join(log))

        try:
            # -----------------------------------------------------------------
            # 2️⃣ Загрузка оригинального JSON
            # -----------------------------------------------------------------
            with open(segments_json_path, "r", encoding="utf-8") as f:
                project_info = json.load(f)

            orig_segments = project_info["segments"]
            if not isinstance(orig_segments, list):
                raise ValueError("segments.json 'segments' field is not a list")

            initial_pause_s = project_info.get("initial_pause_s", 0.0)
            orig_pauses = project_info.get("orig_pauses", [])
            original_video_path = project_info.get("original_video_path", "")
            original_video_dir = os.path.dirname(original_video_path) if original_video_path else ""

            # получаем информацию о том, как в первой ноде обрабатывался звук
            audio_preprocess = project_info.get("audio_preprocess", {})
            separation_method = audio_preprocess.get("separation_method", "none")

            log.append(f"✅ Loaded {len(orig_segments)} original segments, initial pause {initial_pause_s}s")
            log.append(f"ℹ️ Original video path: {original_video_path}")

            # -----------------------------------------------------------------
            # 3️⃣ Сопоставление синтезированных аудио по индексу сегмента
            # -----------------------------------------------------------------
            # Ищем файлы по шаблону: <prefix>_<001>.wav
            # Мы делаем поиск гибким: либо четкое совпадение с префиксом, либо просто окончание _001.wav
            audio_files_by_index = {}
            # escape prefix for regex
            esc_prefix = re.escape(filename_prefix)
            
            for f in os.listdir(synthesized_audio_folder):
                full_path = os.path.join(synthesized_audio_folder, f)
                if not os.path.isfile(full_path):
                    continue
                
                # Пробуем найти индекс в конце файла: _001.wav или _001.mp3
                # Либо если есть четкое совпадение с префиксом
                match = re.search(rf"({esc_prefix}|txtsegments_[a-zA-Z]+)?_?(\d{{3}})\.(\w+)", f)
                if match:
                    # Индекс это вторая группа (\d{3})
                    idx = int(match.group(2))
                    # Если у нас уже есть файл для этого индекса, приоритет тому, что больше похож на префикс
                    if idx in audio_files_by_index:
                        if filename_prefix in f:
                             audio_files_by_index[idx] = full_path
                    else:
                        audio_files_by_index[idx] = full_path

            if len(audio_files_by_index) != len(orig_segments):
                log.append(f"⚠️ Warning: Mismatch - Found {len(audio_files_by_index)} audio files vs {len(orig_segments)} segments. Attempting to proceed with available files.")
            else:
                log.append(f"✅ Found {len(audio_files_by_index)} synthesized audio files matching segments.")

            # -----------------------------------------------------------------
            # 4️⃣ Загрузка, (опциональная) обрезка и выравнивание длительности
            # -----------------------------------------------------------------
            aligned_segments = []
            aligned_info = []
            tmp_dir = folder_paths.get_temp_directory()

            for orig_seg in orig_segments:
                idx = orig_seg.get("index")
                synth_audio_seg = None
                
                if idx in audio_files_by_index:
                    synth_file_path = audio_files_by_index[idx]
                    try:
                        synth_audio_seg = AudioSegment.from_file(synth_file_path)
                    except Exception as e:
                        log.append(f"⚠️ Failed to load {synth_file_path}: {e}")
                
                if synth_audio_seg is None:
                    # Fallback to silence if file missing - use the target duration from segment
                    target_duration_s = orig_seg["original_end_s"] - orig_seg["original_start_s"]
                    synth_audio_seg = AudioSegment.silent(duration=int(round(target_duration_s * 1000)), frame_rate=24000)
                    log.append(f"⚠️ Segment {idx}: Audio missing. Using silence ({target_duration_s:.3f}s).")

                # ---------- Обрезка начала ----------
                if trim_start_silence:
                    before_len = len(synth_audio_seg)
                    synth_audio_seg = _trim_start(
                        synth_audio_seg,
                        silence_thresh=silence_thresh,
                        min_silence_len=min_silence_len,
                    )
                    trimmed = before_len - len(synth_audio_seg)
                    if trimmed > 0:
                        log.append(f"✂️ Segment {idx}: trimmed {trimmed} ms leading silence.")
                # ---------- Обрезка конца ----------
                if trim_end_silence:
                    before_len = len(synth_audio_seg)
                    synth_audio_seg = _trim_end(
                        synth_audio_seg,
                        silence_thresh=silence_thresh,
                        min_silence_len=min_silence_len,
                    )
                    trimmed = before_len - len(synth_audio_seg)
                    if trimmed > 0:
                        log.append(f"✂️ Segment {idx}: trimmed {trimmed} ms trailing silence.")

                target_start_s = orig_seg["original_start_s"]
                target_end_s   = orig_seg["original_end_s"]
                target_duration_s = target_end_s - target_start_s
                target_ms = int(round(target_duration_s * 1000))
                current_ms = len(synth_audio_seg)

                factor = target_ms / max(current_ms, 1)

                # ------------------ растяжка/сжатие ------------------
                if not allow_stretching:
                    # только сжатие, растяжка запрещена
                    if factor < 1.0:                     # нужно сжать
                        if factor < 1.0 / max_stretch_factor:
                            log.append(
                                f"⚠️ Segment {idx}: compression {factor:.2f} < 1/{max_stretch_factor}. "
                                f"Keeping original length ({current_ms} ms)."
                            )
                            aligned = synth_audio_seg
                            factor_used = 1.0
                            final_ms = current_ms
                        else:
                            aligned = _stretch(synth_audio_seg, target_ms, tmp_dir, stretch_method)
                            factor_used = factor
                            final_ms = target_ms
                    else:
                        log.append(
                            f"ℹ️ Segment {idx}: stretching disabled, original length ({current_ms} ms) <= target ({target_ms} ms)."
                        )
                        aligned = synth_audio_seg
                        factor_used = 1.0
                        final_ms = current_ms
                else:
                    # обычный режим (сжатие + растяжка)
                    if factor < 1.0:                     # сжатие
                        if factor < 1.0 / max_stretch_factor:
                            log.append(
                                f"⚠️ Segment {idx}: compression {factor:.2f} < 1/{max_stretch_factor}. "
                                f"Keeping original length ({current_ms} ms)."
                            )
                            aligned = synth_audio_seg
                            factor_used = 1.0
                            final_ms = current_ms
                        else:
                            aligned = _stretch(synth_audio_seg, target_ms, tmp_dir, stretch_method)
                            factor_used = factor
                            final_ms = target_ms
                    elif factor > 1.0:                   # растяжка
                        if factor > max_stretch_factor:
                            log.append(
                                f"⚠️ Segment {idx}: stretch {factor:.2f} > {max_stretch_factor}. "
                                f"Keeping original length ({current_ms} ms)."
                            )
                            aligned = synth_audio_seg
                            factor_used = 1.0
                            final_ms = current_ms
                        else:
                            aligned = _stretch(synth_audio_seg, target_ms, tmp_dir, stretch_method)
                            factor_used = factor
                            final_ms = target_ms
                    else:                                 # factor == 1.0
                        aligned = synth_audio_seg
                        factor_used = 1.0
                        final_ms = current_ms

                aligned_segments.append(aligned)
                aligned_info.append({
                    "index": idx,
                    "original_file": synth_file_path,
                    "original_duration_ms": current_ms,
                    "target_duration_ms": target_ms,
                    "final_duration_ms": final_ms,
                    "stretch_factor": factor_used,
                    "original_start_s": target_start_s,
                    "original_end_s": target_end_s,
                })
                log.append(f"✅ Aligned segment {idx}: {current_ms} ms -> {final_ms} ms (factor {factor_used:.2f})")

            # -----------------------------------------------------------------
            # 5️⃣ Сборка финального аудио и тайм‑лайна (фон пока не добавлен)
            # -----------------------------------------------------------------
            # final_audio = AudioSegment.empty()  # <-- УДАЛЯЕМ ЭТУ СТРОКУ
            timeline = []
            cursor = 0.0

            # --- НОВОЕ: собираем список фрагментов ---
            audio_chunks_to_concat = []
            
            # --- FIXED: собрать список фрагментов без ложного смещения trim_video_start_ms ---
            audio_chunks_to_concat = []
            
            # Мы НЕ добавляем trim_video_start_ms здесь, потому что Whisper уже работает 
            # по обрезанному видео, и его тайминги (0.0, 1.2...) уже соответствуют началу Trim.

            # Начальная пауза (если есть)
            if initial_pause_s > 0:
                lead = AudioSegment.silent(
                    duration=int(round(initial_pause_s * 1000)),
                    frame_rate=aligned_segments[0].frame_rate if aligned_segments else 24000
                )
                audio_chunks_to_concat.append(lead)  # <-- Добавляем в список
                timeline.append({
                    "type": "pause",
                    "content": f"initial {initial_pause_s:.3f}s",
                    "start_s": round(cursor, 3),
                    "end_s": round(cursor + initial_pause_s, 3),
                    "duration_s": round(initial_pause_s, 3),
                })
                cursor += initial_pause_s
            else:
                # Если начальной паузы нет, всё равно добавляем время в таймлайн, если нужно
                # (например, если cursor != 0.0 из-за других причин)
                pass

            # Основные сегменты + паузы
            for i, (aligned, orig_seg, aligned_det) in enumerate(zip(aligned_segments, orig_segments, aligned_info)):
                idx = orig_seg["index"]
                
                # Тайминги Whisper (original_start_s) уже относительные для обрезанного видео
                target_start_s = orig_seg["original_start_s"]

                # Пауза‑ожидание, если курсор отстаёт от планируемого начала
                if cursor < target_start_s:
                    wait_duration_s = target_start_s - cursor
                    wait_pause_seg = AudioSegment.silent(
                        duration=int(round(wait_duration_s * 1000)),
                        frame_rate=aligned.frame_rate
                    )
                    audio_chunks_to_concat.append(wait_pause_seg)  # <-- Добавляем в список
                    timeline.append({
                        "type": "pause",
                        "content": f"wait_gap {wait_duration_s:.3f}s",
                        "start_s": round(cursor, 3),
                        "end_s": round(cursor + wait_duration_s, 3),
                        "duration_s": round(wait_duration_s, 3),
                    })
                    log.append(f"  Added WAIT_GAP before segment {idx}: {wait_duration_s:.3f}s")
                    cursor += wait_duration_s

                # Текстовый кусок
                seg_dur_s = aligned_det["final_duration_ms"] / 1000.0
                audio_chunks_to_concat.append(aligned)  # <-- Добавляем в список
                timeline.append({
                    "type": "text",
                    "index": idx,
                    "original_file": aligned_det["original_file"],
                    "start_s": round(cursor, 3),
                    "end_s": round(cursor + seg_dur_s, 3),
                    "duration_s": round(seg_dur_s, 3),
                    "stretch_factor": aligned_det["stretch_factor"],
                })
                cursor += seg_dur_s

                # Оригинальная пауза после сегмента (если включена)
                if use_original_pauses and i < len(orig_pauses):
                    pause_info = orig_pauses[i]
                    pause_sec = pause_info["duration_s"]
                    pause_seg = AudioSegment.silent(
                        duration=int(round(pause_sec * 1000)),
                        frame_rate=aligned.frame_rate
                    )
                    audio_chunks_to_concat.append(pause_seg)  # <-- Добавляем в список
                    timeline.append({
                        "type": "pause",
                        "content": f"orig {pause_sec:.3f}s",
                        "start_s": round(cursor, 3),
                        "end_s": round(cursor + pause_sec, 3),
                        "duration_s": round(pause_sec, 3),
                    })
                    cursor += pause_sec
                    log.append(f"  Added ORIG_PAUSE after segment {idx}: {pause_sec:.3f}s")

            # --- FIXED: эффективное соединение ---
            if audio_chunks_to_concat:
                # В pydub sum() может вести себя странно с большими списками
                # Используем AudioSegment.empty().append() для скорости
                final_audio = audio_chunks_to_concat[0]
                for chunk in audio_chunks_to_concat[1:]:
                    final_audio = final_audio + chunk
            else:
                # Если фрагментов нет, создаём пустой сегмент
                final_audio = AudioSegment.silent(duration=0, frame_rate=24000)

            # -----------------------------------------------------------------
            # 6️⃣ Восстановление фоновой дорожки (по переключателям)
            # -----------------------------------------------------------------
            # Получаем оригинальный микс (полный звук видео)
            tmp_full_wav = os.path.join(folder_paths.get_temp_directory(),
                                        f"{uuid.uuid4().hex}_full.wav")
            trim_video_start_ms = project_info.get("trim_video_start_ms", 0)

            try:
                # ВАЖНО: извлекаем фон С ТЕМ ЖЕ ТРИММОМ, что и основной проект
                trim_end = project_info.get("trim_video_end_ms", 0)
                
                # Если видео немое, ищем источник звука (fallback)
                from .utils import get_audio_source
                audio_source = get_audio_source(original_video_path, log_list=log)
                
                if audio_source:
                    extract_audio(audio_source, tmp_full_wav, trim_video_start_ms, trim_end)
                    full_original = AudioSegment.from_wav(tmp_full_wav)
                else:
                    raise RuntimeError("No audio source found for background extraction")
            except Exception as e:
                log.append(f"⚠️ Не удалось извлечь оригинальный микс (trim={trim_video_start_ms}-{trim_end}): {e}")
                full_original = None

            if full_original is not None:
                # Если в проекте использовалось разделение, убираем из оригинального
                # микса все оригинальные голосовые отрезки, заменяя их тишиной.
                # Если разделения НЕ было – просто берём оригинальный микс как есть.
                if separation_method != "none":
                    bg_silent = full_original
                    for seg in orig_segments:
                        # Так как full_original ТЕПЕРЬ УЖЕ ОБРЕЗАН в extract_audio,
                        # нам нужно считать тайминги сегмента ОТНОСИТЕЛЬНО начала этого куска.
                        # Но в orig_segments хранятся тайминги относительно ОРИГИНАЛЬНОГО видео (0.0).
                        # Поэтому вычитаем смещение (которое было 0 если trim_start_ms=0).
                        
                        start_ms = int(round(seg["original_start_s"] * 1000))
                        end_ms   = int(round(seg["original_end_s"] * 1000))
                        
                        # Если сегмент выходит за рамки текущего куска, пропускаем или обрезаем
                        if end_ms < 0 or start_ms >= len(bg_silent):
                            continue
                            
                        start_ms = max(0, start_ms)
                        end_ms   = min(len(bg_silent), end_ms)
                        
                        seg_len  = end_ms - start_ms
                        silence  = AudioSegment.silent(duration=seg_len,
                                                       frame_rate=bg_silent.frame_rate)
                        bg_silent = bg_silent[:start_ms] + silence + bg_silent[end_ms:]
                else:
                    bg_silent = full_original

                # ------------------- Добавляем фон (музыку) -------------------
                if add_original_background:
                    # Overlay at the correct offset (0.0 if background already trimmed)
                    final_audio = final_audio.overlay(bg_silent, position=0)
                    log.append(f"✅ Background music restored.")

                # ------------------- Добавляем шум (high‑freq hiss) -------------------
                if add_original_noise:
                    # High-frequency noise restore
                    noise_track = bg_silent.high_pass_filter(3000).apply_gain(-20)
                    final_audio = final_audio.overlay(noise_track, position=0)
                    log.append("✅ Noise track added.")

                # ------------------- Добавляем оригинальное аудио на X% громкости -------------------
                if original_audio_volume_percent > 0:
                    import math
                    gain = 20 * math.log10(max(original_audio_volume_percent, 0.001) / 100.0)
                    lowered_original = full_original.apply_gain(gain)
                    final_audio = final_audio.overlay(lowered_original, position=0)
                    log.append(f"✅ Original audio mix (at {original_audio_volume_percent}%) restored.")

            else:
                log.append("⚠️ Не удалось загрузить оригинальный микс – фон не будет добавлен.")

            # ------------------- Удаляем временный файл микса -------------------
            try:
                os.remove(tmp_full_wav)
            except Exception:
                pass

            # -----------------------------------------------------------------
            # 7️⃣ Преобразование AudioSegment → ComfyUI AUDIO
            # -----------------------------------------------------------------
            # --- FIXED: Normalization to [-1.0, 1.0] for ComfyUI ---
            final_audio_np = np.array(final_audio.get_array_of_samples())
            
            # Guard for empty audio
            if final_audio_np.size == 0:
                final_audio_np = np.zeros(1, dtype=np.float32)
                
            if final_audio.channels > 1:
                final_audio_np = final_audio_np.reshape((-1, final_audio.channels)).mean(axis=1)

            # Normalize 16-bit PCM (pydub default) to float32
            final_audio_norm = final_audio_np.astype(np.float32) / 32768.0
            
            final_audio_tensor = torch.from_numpy(final_audio_norm).unsqueeze(0).unsqueeze(0)
            final_audio_dict = {
                "waveform": final_audio_tensor,
                "sample_rate": final_audio.frame_rate
            }
            log.append(f"✅ Final audio assembled (normalized float32) → {len(final_audio)/1000:.2f}s")

            # -----------------------------------------------------------------
            # 8️⃣ Сохранение WAV‑файла
            # -----------------------------------------------------------------
            saved_wav_path = ""
            if save_final_audio:
                try:
                    out_dir = original_video_dir if original_video_dir else os.path.dirname(segments_json_path)
                    base_name = os.path.splitext(os.path.basename(project_info.get("original_video_name", "final_dub")))[0]
                    saved_wav_filename = f"{base_name}_{filename_prefix}.wav"
                    saved_wav_path = os.path.join(out_dir, saved_wav_filename)
                    final_audio.export(saved_wav_path, format="wav")
                    log.append(f"🗂️ Final WAV saved → {saved_wav_path}")
                except Exception as e:
                    log.append(f"⚠️ Failed to write final wav: {e}")

            # -----------------------------------------------------------------
            # 9️⃣ Сохранение JSON‑отчёта
            # -----------------------------------------------------------------
            finalized_json_path = ""
            if save_final_json:
                try:
                    out_data = dict(project_info)   # shallow copy
                    out_data["finalized_segments_timeline"] = timeline
                    out_data["aligned_text_segments_details"] = aligned_info
                    out_data["final_audio_path"] = saved_wav_path
                    out_data["final_audio_duration_s"] = len(final_audio) / 1000.0

                    # Записываем, какие фоновые элементы мы добавили
                    out_data["add_original_background"] = add_original_background
                    out_data["add_original_noise"] = add_original_noise
                    out_data["original_audio_volume_percent"] = original_audio_volume_percent

                    out_dir = os.path.dirname(segments_json_path)
                    finalized_json_filename = os.path.join(out_dir, "finalized_from_files.json")
                    with open(finalized_json_filename, "w", encoding="utf-8") as jf:
                        json.dump(out_data, jf, ensure_ascii=False, indent=2)
                    log.append(f"🗂️ Finalized JSON written → {finalized_json_filename}")
                    finalized_json_path = finalized_json_filename
                except Exception as e:
                    log.append(f"⚠️ Failed to write finalized JSON: {e}")

            log.append("🎉 FinalizeDubbedAudioFromFiles finished")
            status = "\n".join(log)
            return final_audio_dict, saved_wav_path, finalized_json_path, status

        except Exception as e:
            log.append(f"❌ Critical failure: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "\n".join(log)


# --------------------------------------------------------------
# Регистрация ноды
# --------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "FinalizeDubbedAudioFromFiles": FinalizeDubbedAudioFromFiles
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FinalizeDubbedAudioFromFiles": "Finalize Dubbed Audio From Files (Align & Save)"
}