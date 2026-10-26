# finalize_dubbed_audio_node.py
# ---------------------------------------------------------------
# Узел FinalizeDubbedAudioNode – теперь:
#   • учитывает главы (chapters) и начальную паузу,
#   • подгоняет длительность каждого текста с ограничением растяжения,
#   • НОВОЕ: можно ОТКЛЮЧИТЬ растяжение (оставлять длинные сегменты как есть),
#           и тогда следующий сегмент вставляется в своё оригинальное время.
#   • сохраняет подробный JSON‑отчёт с тайм‑кодами,
#   • поддерживает два метода растяжки (ffmpeg_atempo / rubberband),
#   • даёт возможность отключить/модифицировать оригинальные паузы.
# ---------------------------------------------------------------
import os
import json
import uuid
import torch
import numpy as np
import logging
import traceback
import folder_paths
from typing import List, Dict, Tuple, Any

from pydub import AudioSegment

# -----------------------------------------------------------------
# Внешние утилиты (см. utils.py)
# -----------------------------------------------------------------
from .utils import (
    load_json,
    extract_audio_segment,
    stretch_audio_ffmpeg_atempo,
    stretch_audio_rubberband,
)

logger = logging.getLogger("FinalizeDubbedAudioNode")

# -----------------------------------------------------------------
def _stretch(
    segment: AudioSegment,
    target_ms: int,
    tmp_dir: str,
    method: str,
) -> AudioSegment:
    """Обёртка для выбора метода растяжки."""
    if method == "ffmpeg_atempo":
        return stretch_audio_ffmpeg_atempo(segment, target_ms, tmp_dir)
    elif method == "rubberband":
        return stretch_audio_rubberband(segment, target_ms, tmp_dir)
    else:
        raise ValueError(f"Unsupported stretch method: {method}")


# -----------------------------------------------------------------
class FinalizeDubbedAudioNode:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "segments_json_path": ("STRING", {"default": ""}),
                "synthesized_audio": ("AUDIO", {"tooltip": "Audio dict from VibeVoice"}),
                "synthesized_segments_json_path": ("STRING", {"default": ""}),
                "process_now": ("BOOLEAN", {"default": False}),
                "stretch_method": (["ffmpeg_atempo", "rubberband"], {"default": "ffmpeg_atempo"}),
                "max_stretch_factor": ("FLOAT", {
                    "default": 2.0, "min": 1.0, "max": 4.0, "step": 0.1,
                    "tooltip": "Maximum allowed stretch/compression factor."
                }),
                # --- НОВЫЙ ПАРАМЕТР ---
                "allow_stretching": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Allow Stretch",
                    "label_off": "No Stretch",
                    "tooltip": "If OFF, segments longer than original will be compressed, but shorter ones will NOT be stretched. Next segment will play at its original time."
                }),
                "use_original_pauses": ("BOOLEAN", {"default": True}),
                "target_language": ("STRING", {"default": ""}),
                "save_final_audio": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Save",
                    "label_off": "Don't Save",
                }),
                "save_final_json": ("BOOLEAN", {
                    "default": True,
                    "label_on": "Save JSON",
                    "label_off": "Skip JSON",
                }),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING", "STRING")
    RETURN_NAMES = ("final_audio", "saved_wav_path", "finalized_json_path", "status")
    FUNCTION = "finalize_dubbed_audio"
    CATEGORY = "VideoDubbing"

    # -----------------------------------------------------------------
    def finalize_dubbed_audio(self,
                              segments_json_path: str,
                              synthesized_audio: dict,
                              synthesized_segments_json_path: str,
                              process_now: bool,
                              stretch_method: str = "ffmpeg_atempo",
                              max_stretch_factor: float = 2.0,
                              # --- НОВЫЙ ПАРАМЕТР ---
                              allow_stretching: bool = True, # <-- НОВЫЙ ПАРАМЕТР
                              use_original_pauses: bool = True,
                              target_language: str = "",
                              save_final_audio: bool = True,
                              save_final_json: bool = True):

        # ---------------------------------------------------------
        # 0️⃣ Флаг запуска
        # ---------------------------------------------------------
        if not process_now:
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "Set process_now=True to start"

        # ---------------------------------------------------------
        # 1️⃣ Проверка входов
        # ---------------------------------------------------------
        if not os.path.isfile(segments_json_path):
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", f"Error: Original JSON not found at {segments_json_path}"
        if not os.path.isfile(synthesized_segments_json_path):
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", f"Error: Synth JSON not found at {synthesized_segments_json_path}"
        if not synthesized_audio or "waveform" not in synthesized_audio or "sample_rate" not in synthesized_audio:
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "Error: Invalid synthesized audio input"

        log = ["🔧 FinalizeDubbedAudioNode started"]
        logger.info("\n".join(log))

        # ---------------------------------------------------------
        # 2️⃣ Загрузка оригинального JSON
        # ---------------------------------------------------------
        try:
            project_info = load_json(segments_json_path)
            orig_segments = project_info["segments"]
            orig_pauses   = project_info.get("orig_pauses", [])
            initial_pause_s = float(project_info.get("initial_pause_s", 0.0))
            chapters = project_info.get("chapters", [])
            log.append(f"✅ Original: {len(orig_segments)} seg., "
                       f"{len(orig_pauses)} pauses, {len(chapters)} chapters, "
                       f"initial pause {initial_pause_s:.3f}s")
        except Exception as e:
            log.append(f"❌ Failed to read original JSON: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "\n".join(log)

        # ---------------------------------------------------------
        # 3️⃣ Загрузка синтезированного JSON
        # ---------------------------------------------------------
        try:
            synth_info = load_json(synthesized_segments_json_path)
            synth_segments = synth_info["segments"]
            text_synth_segments = [s for s in synth_segments if s.get("type") == "text"]
            log.append(f"✅ Synth JSON: {len(synth_segments)} total, "
                       f"{len(text_synth_segments)} text segments")
        except Exception as e:
            log.append(f"❌ Failed to read synth JSON: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "\n".join(log)

        # ---------------------------------------------------------
        # 4️⃣ Количественная проверка
        # ---------------------------------------------------------
        if len(orig_segments) != len(text_synth_segments):
            log.append(
                f"❌ Мismatch: оригинал {len(orig_segments)} vs синтез {len(text_synth_segments)}"
            )
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "\n".join(log)

        # Проверка количества пауз (может немного отличаться – выводим предупреждение)
        # ИСПРАВЛЕНО: expected_pauses = max(len(orig_segments) - 1, 0)
        expected_pauses = max(len(orig_segments) - 1, 0)
        if use_original_pauses and len(orig_pauses) < expected_pauses:
            log.append(
                f"⚠️ Недостаточно оригинальных пауз: найдено {len(orig_pauses)}, "
                f"но ожидается минимум {expected_pauses}. Будут добавлены тишины."
            )

        # ---------------------------------------------------------
        # 5️⃣ Перевод Comfy‑Audio → pydub
        # ---------------------------------------------------------
        try:
            wav = synthesized_audio["waveform"].squeeze(0).squeeze(0).cpu().numpy()
            sr = synthesized_audio["sample_rate"]
            wav_i16 = (np.clip(wav, -1, 1) * 32767).astype(np.int16)

            synth_audio_seg = AudioSegment(
                data=wav_i16.tobytes(),
                sample_width=2,
                frame_rate=sr,
                channels=1,
            )
            log.append(f"✅ Synth audio loaded: {len(synth_audio_seg)/1000:.2f}s")
        except Exception as e:
            log.append(f"❌ Failed to convert synth audio: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "\n".join(log)

        # ---------------------------------------------------------
        # 6️⃣ Выделяем отдельные синтезированные текстовые чанки
        # ---------------------------------------------------------
        extracted_text_segments = []
        for i, seg in enumerate(text_synth_segments):
            part = extract_audio_segment(synth_audio_seg,
                                         seg["start_time_s"],
                                         seg["end_time_s"])
            extracted_text_segments.append(part)
            log.append(
                f"   Extracted synth text {i+1}: {len(part)/1000:.2f}s "
                f"({seg['start_time_s']:.3f}s–{seg['end_time_s']:.3f}s)"
            )

        # ---------------------------------------------------------
        # 7️⃣ Подгонка длительности (с ограничением растяжения и новой логикой)
        # ---------------------------------------------------------
        aligned_segments = []
        aligned_info = []          # будет записан в итоговый JSON
        tmp_dir = folder_paths.get_temp_directory()

        for i, (synth_seg, orig_seg) in enumerate(zip(extracted_text_segments, orig_segments)):
            target_ms = int(round(
                (orig_seg["original_end_s"] - orig_seg["original_start_s"]) * 1000
            ))
            current_ms = len(synth_seg)

            # Вычисляем коэффициент растяжения
            factor = target_ms / max(current_ms, 1)

            # --- НОВАЯ ЛОГИКА ---
            if factor < 1.0:
                # Нужно сжать (сегмент длиннее оригинала) -> разрешаем сжатие
                if factor < 1.0 / max_stretch_factor:
                    log.append(
                        f"⚠️ Segment {i+1}: required compression {factor:.2f} < 1/{max_stretch_factor}. "
                        f"Leaving original length ({current_ms} ms)."
                    )
                    aligned = synth_seg
                    factor = 1.0
                    final_ms = current_ms
                else:
                    # Сжимаем в пределах допустимого
                    aligned = _stretch(synth_seg, target_ms, tmp_dir, stretch_method)
                    final_ms = target_ms
            else:
                # Нужно растянуть (сегмент короче оригинала)
                if not allow_stretching:
                    # Растяжение запрещено -> оставляем как есть
                    log.append(
                        f"ℹ️ Segment {i+1}: required stretch {factor:.2f} > 1. "
                        f"No stretching allowed. Leaving original length ({current_ms} ms)."
                    )
                    aligned = synth_seg
                    factor = 1.0
                    final_ms = current_ms
                else:
                    # Растяжение разрешено
                    if factor > max_stretch_factor:
                        log.append(
                            f"⚠️ Segment {i+1}: required stretch {factor:.2f} > max {max_stretch_factor}. "
                            f"Leaving original length ({current_ms} ms)."
                        )
                        aligned = synth_seg
                        factor = 1.0
                        final_ms = current_ms
                    else:
                        aligned = _stretch(synth_seg, target_ms, tmp_dir, stretch_method)
                        final_ms = target_ms

            aligned_segments.append(aligned)
            aligned_info.append({
                "index": i + 1,
                "original_duration_ms": current_ms,
                "target_duration_ms": target_ms,
                "final_duration_ms": final_ms,
                "stretch_factor": round(factor, 3),
            })
            log.append(
                f"   Segment {i+1}: {current_ms} ms → {final_ms} ms "
                f"(factor {factor:.3f})"
            )

        # ---------------------------------------------------------
        # 8️⃣ Сборка финального тайм‑лайна (текст в оригинальное время)
        # ---------------------------------------------------------
        final_audio = AudioSegment.empty()
        timeline = []                     # будет записан в JSON
        cursor = 0.0

        # ---- Начальная пауза ----
        if initial_pause_s > 0:
            lead = AudioSegment.silent(
                duration=int(round(initial_pause_s * 1000)),
                frame_rate=final_audio.frame_rate if final_audio else aligned_segments[0].frame_rate,
            )
            final_audio += lead
            timeline.append({
                "type": "pause",
                "content": f"initial {initial_pause_s:.3f}s",
                "start_s": round(cursor, 3),
                "end_s":   round(cursor + initial_pause_s, 3),
                "duration_s": round(initial_pause_s, 3),
            })
            cursor += initial_pause_s

        # ---- Текстовые сегменты + паузы ожидания ----
        for i, (aligned, orig_seg) in enumerate(zip(aligned_segments, orig_segments)):
            # Вычисляем, в какое время должен начинаться этот сегмент
            target_start_s = orig_seg["original_start_s"]
            
            # Если текущий курсор "позади" времени начала сегмента, вставляем паузу ожидания
            if cursor < target_start_s:
                wait_duration_s = target_start_s - cursor
                wait_pause_seg = AudioSegment.silent(
                    duration=int(round(wait_duration_s * 1000)),
                    frame_rate=aligned.frame_rate,
                )
                final_audio += wait_pause_seg
                timeline.append({
                    "type": "pause",
                    "content": f"wait_gap {wait_duration_s:.3f}s",
                    "start_s": round(cursor, 3),
                    "end_s":   round(cursor + wait_duration_s, 3),
                    "duration_s": round(wait_duration_s, 3),
                })
                log.append(f"   Added WAIT_GAP before segment {i+1}: {wait_duration_s:.3f}s")
                cursor += wait_duration_s

            # Добавляем сам сегмент
            seg_dur = len(aligned) / 1000.0
            final_audio += aligned
            timeline.append({
                "type": "text",
                "index": i + 1,
                "start_s": round(cursor, 3),
                "end_s":   round(cursor + seg_dur, 3),
                "duration_s": round(seg_dur, 3),
                "stretch_factor": aligned_info[i]["stretch_factor"],
            })
            cursor += seg_dur

            # --- Вставка обычной паузы (если разрешено) ---
            # (Опционально, если вы хотите вставлять оригинальные паузы между сегментами)
            # if use_original_pauses and i < len(aligned_segments) - 1:
            #     if i < len(orig_pauses):
            #         pause_sec = orig_pauses[i]["duration_s"]
            #     else:
            #         pause_sec = 0.5
            #     pause_seg = AudioSegment.silent(
            #         duration=int(round(pause_sec * 1000)),
            #         frame_rate=aligned.frame_rate,
            #     )
            #     final_audio += pause_seg
            #     timeline.append({
            #         "type": "pause",
            #         "content": f"orig {pause_sec:.3f}s",
            #         "start_s": round(cursor, 3),
            #         "end_s":   round(cursor + pause_sec, 3),
            #         "duration_s": round(pause_sec, 3),
            #     })
            #     cursor += pause_sec

        log.append(f"✅ Final audio assembled – {len(final_audio)/1000:.2f}s total")
        log.append(f"✅ Timeline entries: {len(timeline)}")

        # ---------------------------------------------------------
        # 9️⃣ Преобразуем AudioSegment → ComfyUI‑dict
        # ---------------------------------------------------------
        try:
            samples = np.array(final_audio.get_array_of_samples()).astype(np.float32) / 32768.0
            tensor = torch.from_numpy(samples).unsqueeze(0).unsqueeze(0)   # (1,1,N)
            final_audio_dict = {"waveform": tensor, "sample_rate": final_audio.frame_rate}
        except Exception as e:
            log.append(f"❌ Failed to convert final audio to dict: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return empty, "", "", "\n".join(log)

        # ---------------------------------------------------------
        # 10️⃣ Сохранение WAV‑файла (если включено)
        # ---------------------------------------------------------
        saved_wav_path = ""
        if save_final_audio:
            try:
                # Путь рядом с оригинальным видео, если он известен
                if project_info.get("original_video_path"):
                    base_dir = os.path.dirname(os.path.abspath(project_info["original_video_path"]))
                    base_name = os.path.splitext(os.path.basename(project_info["original_video_path"]))[0]
                else:
                    base_dir = folder_paths.get_output_directory()
                    base_name = f"dub_{uuid.uuid4().hex[:8]}"
                tgt_lang = target_language or project_info.get("target_language", "und")
                wav_name = f"{base_name}_{tgt_lang}.wav"
                saved_wav_path = os.path.join(base_dir, wav_name)
                final_audio.export(saved_wav_path, format="wav")
                log.append(f"💾 Final WAV saved → {saved_wav_path}")
            except Exception as e:
                log.append(f"⚠️ Failed to write final wav: {e}")

        # ---------------------------------------------------------
        # 11️⃣ Сохранение подробного JSON‑отчёта (если включено)
        # ---------------------------------------------------------
        finalized_json_path = ""
        if save_final_json:
            try:
                # Добавляем полученную информацию в копию оригинального JSON
                out_data = dict(project_info)   # shallow copy
                out_data["finalized_segments"] = timeline
                out_data["aligned_text_segments"] = aligned_info
                out_data["final_audio_path"] = saved_wav_path
                out_dir = os.path.dirname(segments_json_path)
                finalized_json_path = os.path.join(out_dir, "finalized_segments.json")
                with open(finalized_json_path, "w", encoding="utf-8") as jf:
                    json.dump(out_data, jf, ensure_ascii=False, indent=2)
                log.append(f"🗂️ Finalized JSON written → {finalized_json_path}")
            except Exception as e:
                log.append(f"⚠️ Failed to write finalized JSON: {e}")

        log.append("🎉 FinalizeDubbedAudioNode finished")
        status = "\n".join(log)

        return final_audio_dict, saved_wav_path, finalized_json_path, status


# ----------------------------------------------------------------------
# Регистрация ноды
# ----------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "FinalizeDubbedAudioNode": FinalizeDubbedAudioNode
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "FinalizeDubbedAudioNode": "Finalize Dubbed Audio (Align & Save)"
}