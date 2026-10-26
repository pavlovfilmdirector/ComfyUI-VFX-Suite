# match_audio_duration_to_original_v2.py
# ---------------------------------------------------------------
# Узел, который подгоняет длительность синтезированного аудио
# к оригинальному, учитывая начальную паузу и количество межсегментных пауз.
# Если пауз в оригинальном видеоматериале нет (или пользователь выбрал
# policy "warning_only"), сразу применяется глобальный стретч.
# ---------------------------------------------------------------
import os
import json
import folder_paths
import numpy as np
import torch
import logging
import uuid
import subprocess
import traceback
from pydub import AudioSegment
from pydub.silence import detect_silence as pydub_detect_silence

logger = logging.getLogger("MatchAudioDurationToOriginalV2")

# --------------------------------------------------------------
# Вспомогательные функции (unchanged)
# --------------------------------------------------------------
STRETCH_METHOD_DEFAULT = "ffmpeg_atempo"
STRETCH_METHODS_AVAILABLE = ["ffmpeg_atempo"]   # можно добавить другие при необходимости


def stretch_audio_ffmpeg_atempo(audio_segment: AudioSegment,
                               target_duration_ms: int,
                               tmp_dir: str) -> AudioSegment:
    """Stretch audio using ffmpeg's atempo filter."""
    if len(audio_segment) == 0 or target_duration_ms == 0:
        return AudioSegment.silent(duration=target_duration_ms,
                                   frame_rate=audio_segment.frame_rate)

    rate = len(audio_segment) / target_duration_ms
    if rate <= 0.5 or rate >= 100.0:
        raise ValueError(f"FFmpeg atempo cannot handle rate {rate}")

    in_path = os.path.join(tmp_dir, f"atempo_{uuid.uuid4().hex}.wav")
    out_path = os.path.join(tmp_dir, f"atempo_{uuid.uuid4().hex}_out.wav")
    audio_segment.export(in_path, format="wav")

    # Формируем цепочку atempo
    chain = []
    while rate > 2.0:
        chain.append("atempo=2.0")
        rate /= 2.0
    while rate < 0.5:
        chain.append("atempo=0.5")
        rate *= 2.0
    chain.append(f"atempo={rate:.6f}")
    filter_str = ",".join(chain)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", in_path,
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

    # Очистка временных файлов
    for p in (in_path, out_path):
        try:
            os.remove(p)
        except OSError:
            pass
    return stretched


def match_audio_duration(audio_segment: AudioSegment,
                        target_duration_ms: int,
                        tmp_dir: str,
                        method: str = STRETCH_METHOD_DEFAULT) -> AudioSegment:
    """Растягивает AudioSegment до target_duration_ms."""
    if len(audio_segment) == 0:
        return AudioSegment.silent(duration=target_duration_ms,
                                   frame_rate=audio_segment.frame_rate)
    if target_duration_ms == 0:
        return AudioSegment.empty()
    if len(audio_segment) == target_duration_ms:
        return audio_segment

    if method == "ffmpeg_atempo":
        return stretch_audio_ffmpeg_atempo(audio_segment,
                                           target_duration_ms,
                                           tmp_dir)
    else:
        raise NotImplementedError(f"Stretch method '{method}' not implemented.")


# --------------------------------------------------------------
class MatchAudioDurationToOriginalV2:
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "segments_json_path": ("STRING", {"default": ""}),
                "generated_audio": ("AUDIO", {"tooltip": "Combined synthesized audio"}),
                "stretch_method": (STRETCH_METHODS_AVAILABLE,
                                   {"default": "ffmpeg_atempo"}),
                "process_now": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "target_language": ("STRING", {"default": ""}),
                "save_final_audio": ("BOOLEAN", {"default": True,
                                                "label_on": "Save",
                                                "label_off": "Don't Save"}),

                # Параметры детекции пауз в синтезированном аудио
                "synth_silence_threshold_db": ("FLOAT", {"default": -40.0,
                                                         "min": -100.0,
                                                         "max": 0.0,
                                                         "step": 1}),
                "synth_min_silence_duration_ms": ("INT", {"default": 300,
                                                         "min": 50,
                                                         "max": 5000,
                                                         "step": 10}),
                # Что делать, если количество пауз не совпало
                "pause_mismatch_policy": (["warning_only", "force_match", "abort"],
                                          {"default": "warning_only"}),
            }
        }

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("matched_audio", "saved_file_path", "status")
    FUNCTION = "match_duration"
    CATEGORY = "VideoDubbing"

    # --------------------------------------------------------------
    def match_duration(self,
                       segments_json_path,
                       generated_audio,
                       stretch_method,
                       process_now,
                       target_language="",
                       save_final_audio=True,
                       synth_silence_threshold_db=-40.0,
                       synth_min_silence_duration_ms=300,
                       pause_mismatch_policy="warning_only"):

        # ------------------- 0️⃣ Флаг запуска -------------------
        if not process_now:
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, "", "Set process_now=True to start")

        # ------------------- 1️⃣ Проверка входов ----------------
        if not os.path.isfile(segments_json_path):
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, "",
                    f"Error: segments JSON not found at {segments_json_path}")

        if not generated_audio or "waveform" not in generated_audio or "sample_rate" not in generated_audio:
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, "", "Error: Invalid generated audio input")

        log = [f"🔧 MatchAudioDurationToOriginalV2 started (stretch: {stretch_method})"]
        logger.info("\n".join(log))

        # --------------------------------------------------------------
        # 2️⃣ Читаем JSON → получаем исходную длительность,
        #    количество межсегментных пауз и начальную паузу.
        # --------------------------------------------------------------
        try:
            with open(segments_json_path, "r", encoding="utf-8") as f:
                project_info = json.load(f)

            if not isinstance(project_info, dict) or "segments" not in project_info:
                raise ValueError("segments.json does not contain a valid 'segments' list")

            segments = project_info["segments"]
            last_seg = segments[-1]
            orig_end_s = last_seg.get("original_end_s", last_seg.get("end", None))
            if orig_end_s is None:
                raise KeyError("Neither 'original_end_s' nor 'end' found in last segment")
            orig_len_ms = int(round(orig_end_s * 1000))

            orig_pauses = project_info.get("orig_pauses", [])
            orig_pause_cnt = len(orig_pauses)

            initial_pause_s = float(project_info.get("initial_pause_s", 0.0))

            log.append(f"✅ Original total duration: {orig_len_ms} ms")
            log.append(f"✅ Inter‑segment pauses count: {orig_pause_cnt}")
            log.append(f"✅ Initial pause (lead‑in): {initial_pause_s:.3f}s")
        except Exception as e:
            log.append(f"❌ Failed to read segments.json: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, "", "\n".join(log))

        # --------------------------------------------------------------
        # 3️⃣ Приводим полученный аудио‑словарь к pydub.AudioSegment
        # --------------------------------------------------------------
        try:
            wav = generated_audio["waveform"].squeeze(0).squeeze(0).cpu().numpy()
            sr = generated_audio["sample_rate"]
            wav_int16 = (np.clip(wav, -1, 1) * 32767).astype(np.int16)

            synth_seg = AudioSegment(
                data=wav_int16.tobytes(),
                sample_width=2,
                frame_rate=sr,
                channels=1,
            )
            log.append(f"✅ Generated audio length: {len(synth_seg) / 1000:.2f}s")
        except Exception as e:
            log.append(f"❌ Failed to convert generated audio to pydub: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, "", "\n".join(log))

        # --------------------------------------------------------------
        # 4️⃣ Детекция пауз в синтезированном аудио
        # --------------------------------------------------------------
        try:
            silent_ranges = pydub_detect_silence(
                synth_seg,
                min_silence_len=synth_min_silence_duration_ms,
                silence_thresh=synth_silence_threshold_db,
            )
            synth_pauses = [
                {"start_s": s / 1000.0, "end_s": e / 1000.0,
                 "duration_s": (e - s) / 1000.0}
                for s, e in silent_ranges
            ]
            synth_pause_cnt = len(synth_pauses)
            log.append(f"🔍 Detected {synth_pause_cnt} pauses in synthesized audio")
        except Exception as e:
            log.append(f"⚠️ Silence detection failed on synthesized audio: {e}")
            synth_pauses = []
            synth_pause_cnt = 0

        # --------------------------------------------------------------
        # 5️⃣ Выбираем стратегию растяжения
        # --------------------------------------------------------------
        # Если оригинальных межсегментных пауз **нет**, сразу используем глобальный стретч
        if orig_pause_cnt == 0:
            log.append("⚠️ No inter‑segment pauses in original → forcing **global stretch**.")
            use_global_stretch = True
        else:
            if synth_pause_cnt == orig_pause_cnt:
                log.append("✅ Pause count matches – will stretch per‑segment.")
                use_global_stretch = False
            else:
                log.append(
                    f"⚠️ Pause count mismatch (orig={orig_pause_cnt}, synth={synth_pause_cnt})"
                )
                if pause_mismatch_policy == "abort":
                    log.append("🛑 abort policy selected – returning empty result")
                    empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
                    return (empty, "", "\n".join(log))
                elif pause_mismatch_policy == "force_match":
                    log.append("🔧 force_match – will ignore per‑segment boundaries "
                               "and perform a **global stretch**.")
                    use_global_stretch = True
                else:   # warning_only
                    # Для safety в режиме warning_only тоже переходим к глобальному стретчу,
                    # иначе получаем очень низкие коэффициенты.
                    log.append("⚠️ warning_only – falling back to **global stretch** "
                               "(per‑segment stretch may produce unsupported rates).")
                    use_global_stretch = True

        # --------------------------------------------------------------
        # 6️⃣ Если выбран **per‑segment** режим
        # --------------------------------------------------------------
        if not use_global_stretch:
            try:
                segment_parts = []
                for idx, seg in enumerate(segments):
                    # Целевая длительность текущего сегмента
                    target_ms = int(round(
                        (seg["original_end_s"] - seg["original_start_s"]) * 1000
                    ))

                    # Границы в синтезированном аудио
                    if idx == 0:
                        seg_start_ms = 0
                    else:
                        seg_start_ms = int(round(synth_pauses[idx - 1]["end_s"] * 1000))

                    if idx < len(synth_pauses):
                        seg_end_ms = int(round(synth_pauses[idx]["start_s"] * 1000))
                    else:
                        seg_end_ms = len(synth_seg)

                    src_part = synth_seg[seg_start_ms:seg_end_ms]

                    # Если source уже нужной длины – оставляем как есть
                    if len(src_part) == target_ms:
                        stretched_part = src_part
                        log.append(
                            f"   Segment {idx+1}: lengths already match ({target_ms} ms)."
                        )
                    else:
                        # Попытка растянуть/сжать сегмент
                        stretched_part = match_audio_duration(
                            src_part,
                            target_ms,
                            folder_paths.get_temp_directory(),
                            method=stretch_method,
                        )
                        log.append(
                            f"   Segment {idx+1}: stretched {len(src_part)} ms → {target_ms} ms"
                        )
                    segment_parts.append(stretched_part)

                # Склейка всех частей
                combined = AudioSegment.empty()
                for part in segment_parts:
                    combined += part

                # Добавляем начальную паузу (если есть)
                if initial_pause_s > 0:
                    lead = AudioSegment.silent(
                        duration=int(round(initial_pause_s * 1000)),
                        frame_rate=combined.frame_rate,
                    )
                    combined = lead + combined
                    log.append(
                        f"✅ Prepended initial silence of {initial_pause_s:.3f}s to combined audio"
                    )

                final_seg = combined
                log.append("✅ Per‑segment stretch completed and re‑combined.")
            except Exception as e:
                log.append(f"❌ Per‑segment processing failed: {e}")
                log.append(traceback.format_exc())
                # Если что‑то пошло не так – переходим к глобальному стретчу
                use_global_stretch = True

        # --------------------------------------------------------------
        # 7️⃣ **Глобальный** стретч (если выбран или если per‑segment упал)
        # --------------------------------------------------------------
        if use_global_stretch:
            try:
                tmp_dir = folder_paths.get_temp_directory()
                final_seg = match_audio_duration(
                    synth_seg,
                    orig_len_ms,
                    tmp_dir,
                    method=stretch_method,
                )
                if initial_pause_s > 0:
                    lead = AudioSegment.silent(
                        duration=int(round(initial_pause_s * 1000)),
                        frame_rate=final_seg.frame_rate,
                    )
                    final_seg = lead + final_seg
                    log.append(
                        f"✅ (global) Prepended initial silence of {initial_pause_s:.3f}s"
                    )
                log.append(
                    f"✅ Global stretch completed: {len(synth_seg)} ms → {orig_len_ms} ms"
                )
            except Exception as e:
                log.append(f"❌ Global stretch failed: {e}")
                log.append(traceback.format_exc())
                empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
                return (empty, "", "\n".join(log))

        # --------------------------------------------------------------
        # 8️⃣ Преобразуем AudioSegment → ComfyUI‑audio dict
        # --------------------------------------------------------------
        try:
            samples = np.array(final_seg.get_array_of_samples())
            samples_f = samples.astype(np.float32) / 32768.0
            tensor = torch.from_numpy(samples_f).unsqueeze(0).unsqueeze(0)

            matched_audio = {
                "waveform": tensor,
                "sample_rate": final_seg.frame_rate,
            }
        except Exception as e:
            log.append(f"❌ Failed to convert final audio to ComfyUI dict: {e}")
            log.append(traceback.format_exc())
            empty = {"waveform": torch.zeros(1, 1, 1), "sample_rate": 24000}
            return (empty, "", "\n".join(log))

        # --------------------------------------------------------------
        # 9️⃣ Сохранение финального wav‑файла (если включено)
        # --------------------------------------------------------------
        saved_path = ""
        if save_final_audio:
            try:
                # Путь к оригинальному видео (может отсутствовать)
                original_video_path = project_info.get("original_video_path", "")
                original_video_path = original_video_path.strip().strip('"\'')
                if original_video_path and os.path.isfile(original_video_path):
                    src_dir = os.path.dirname(os.path.abspath(original_video_path))
                    base_name = os.path.splitext(os.path.basename(original_video_path))[0]
                    tgt_lang = target_language or project_info.get("target_language", "und")
                    final_fname = f"{base_name}_{tgt_lang}.wav"
                    saved_path = os.path.join(src_dir, final_fname)
                else:
                    proj_dir = project_info.get("project_dir",
                                               folder_paths.get_output_directory())
                    tgt_lang = target_language or project_info.get("target_language", "und")
                    final_fname = f"{tgt_lang}_{uuid.uuid4().hex[:8]}.wav"
                    saved_path = os.path.join(proj_dir, final_fname)

                final_seg.export(saved_path, format="wav")
                log.append(f"💾 Final audio saved to: {saved_path}")
            except Exception as e:
                log.append(f"⚠️ Failed to save final audio: {e}")

        log.append("🎉 MatchAudioDurationToOriginalV2 finished")
        status = "\n".join(log)
        return (matched_audio, saved_path, status)


# ----------------------------------------------------------------------
# Регистрация ноды
# ----------------------------------------------------------------------
NODE_CLASS_MAPPINGS = {
    "MatchAudioDurationToOriginalV2": MatchAudioDurationToOriginalV2
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "MatchAudioDurationToOriginalV2": "Match Audio Duration To Original (v2)"
}
