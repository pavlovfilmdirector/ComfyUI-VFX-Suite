import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os
import json
import logging
import argparse
from typing import Dict, Any

# Add relevant paths to sys.path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Embed local FFmpeg by prepending to PATH
bin_dir = os.path.join(PROJECT_ROOT, "bin")
if os.path.exists(bin_dir):
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
else:
    # Fallback to check if ffmpeg is available in system path
    import shutil
    if not shutil.which("ffmpeg"):
        logger.warning("⚠️ FFmpeg not found in local 'bin' or system PATH. Audio processing may fail.")

from nodes.analyze_video_for_dubbing_prep import AnalyzeVideoForDubbingPrep
from nodes.translate_segment_texts import TranslateSegmentFiles
from nodes.prepare_continuous_text_with_pauses import PrepareContinuousTextWithPauses
from nodes.FinalizeDubbedAudioFromFiles import FinalizeDubbedAudioFromFiles
from nodes.utils import extract_audio

class DubbingEngine:
    def __init__(self):
        self.project_data = {}

    def emit_status(self, progress: int, status: str, details: Any = None, stage: int = -1):
        msg = {"progress": progress, "status": status, "details": details, "stage": stage}
        print(f"STATUS_UPDATE:{json.dumps(msg, ensure_ascii=False)}", flush=True)

    def extract_chunk(self, input_path, start_s, end_s, output_path):
        """Извлекает фрагмент аудио через защищенную функцию из utils.py."""
        # Конвертируем секунды в миллисекунды для extract_audio
        start_ms = int(start_s * 1000)
        end_ms = int(end_s * 1000)
        extract_audio(input_path, output_path, start_ms, end_ms, sample_rate=24000)

    def run_analysis(self, video_path: str, settings: dict):
        import re
        import folder_paths

        # Determine stable project directory
        base_name = os.path.basename(video_path)
        name_no_ext = os.path.splitext(base_name)[0]
        
        # Name based strictly on input file name as requested
        safe_name = re.sub(r'[<>:"/\\|?*]', '_', name_no_ext)
        custom_projects_dir = settings.get("projects_dir", "")
        
        if custom_projects_dir and os.path.exists(custom_projects_dir):
            project_dir = os.path.join(custom_projects_dir, safe_name)
        else:
            input_dir = folder_paths.get_input_directory()
            project_dir = os.path.join(input_dir, "VideoDubbingProjects", safe_name)
            
        expected_json = os.path.join(project_dir, "segments.json")
        self.project_data["segments_json"] = expected_json

        # If analysis is not requested, check if JSON exists
        if not settings.get("process_now", True):
            if os.path.exists(expected_json):
                self.emit_status(25, "Analysis skipped (found existing project)", {"json": expected_json})
                return expected_json
            else:
                self.emit_status(25, "Error: process_now=False but no existing project found at location.")
                return None

        self.emit_status(5, f"Analyzing video: {base_name}...", stage=0)
        node = AnalyzeVideoForDubbingPrep()
        
        # Use the correct key from settings
        enable_diarization = settings.get("enable_speaker_diarization", settings.get("enable_diarization", False))
        
        params = {
            "video_path": video_path,
            "source_language": settings.get("source_language", "en"),
            "whisper_model": settings.get("whisper_model", "large"),
            "use_gpu": settings.get("use_gpu", True),
            "detect_silence": settings.get("detect_silence", True),
            "silence_threshold_db": settings.get("silence_threshold_db", -40.0),
            "min_silence_duration_s": settings.get("min_silence_duration_s", 0.20),
            "fallback_initial_pause_ms": settings.get("fallback_initial_pause_ms", 0),
            "long_pause_min_duration_s": settings.get("long_pause_min_duration_s", 0.5),
            "merge_segments_factor": settings.get("merge_segments_factor", 3.0),
            "max_pause_duration_for_merge_s": settings.get("max_pause_duration_for_merge_s", 0.5),
            "clean_temp": settings.get("clean_temp", True),
            "clear_cache": settings.get("clear_cache", True),
            "remove_punctuation": settings.get("remove_punctuation", False),
            "process_now": True, # Force True here because we already checked
            "separation_method": settings.get("separation_method", "demucs"),
            "skip_noise_reduction": settings.get("skip_noise_reduction", False),
            "min_segment_duration_s": settings.get("min_segment_duration_s", 0.30),
            "enable_speaker_diarization": enable_diarization,
            "speaker_label_style": settings.get("speaker_label_style", "none"),
            "use_faster_whisper": settings.get("use_faster_whisper", True),
            "enable_vad": settings.get("enable_vad", True),
            "vad_threshold": settings.get("vad_threshold", 0.3),
            "repetitive_filter": settings.get("repetitive_filter", True),
            "custom_projects_dir": settings.get("projects_dir", ""),
            "trim_video_start_ms": settings.get("trim_video_start_ms", 0),
            "trim_video_end_ms": settings.get("trim_video_end_ms", 0),
            "audio_path": settings.get("original_video_audio_path", "")
        }
        
        results = node.process(**params)
        segments_json_path = results[2]
        analysis_status = results[5]
        
        if not segments_json_path or not os.path.exists(segments_json_path):
             self.emit_status(25, "Analysis failed (No output JSON)")
             # Print the full analysis log to help debug
             print("--- ANALYSIS FAILED LOG ---")
             print(analysis_status)
             print("---------------------------")
             return None

        with open(segments_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not data.get("segments"):
                self.emit_status(25, "Analysis complete (Warning: No speech segments found)")
                logger.warning("No speech segments detected in video. Stopping.")
                return None

        self.project_data["segments_json"] = segments_json_path
        self.emit_status(25, "Analysis complete", {"json": segments_json_path})
        return segments_json_path

    def run_translation(self, segments_json: str, settings: Dict[str, Any]):
        if not segments_json: return None
        if not settings.get("process_now_trans", True):
            self.emit_status(50, "Translation skipped (using existing data)")
            return segments_json

        self.emit_status(30, "Translating segments...", stage=1)
        node = TranslateSegmentFiles()
        
        params = {
            "segments_json_path": segments_json,
            "source_language": settings.get("trans_source_language", "en"),
            "target_language": settings.get("trans_target_language", "ru"),
            "translator": settings.get("translator", "google"),
            "remove_punctuation_before_translation": settings.get("remove_punctuation_before_translation", True),
            "remove_punctuation_after_translation": settings.get("remove_punctuation_after_translation", False),
            "retain_translation_punctuation": settings.get("retain_translation_punctuation", True),
            "fix_cyrillic_orthography": settings.get("fix_cyrillic_orthography", True),
            "add_stress": settings.get("add_stress", True),
            "stress_engine": settings.get("stress_engine", "RUAccent"),
            "process_now": settings.get("process_now_trans", True),
            "original_video_audio_path": settings.get("original_video_audio_path", "")
        }
        
        results = node.translate_files(**params)
        
        # Display the formatted internal logs from the translation node in the UI terminal
        if len(results) >= 5 and results[4]:
            print(results[4])
            
        self.emit_status(50, "Translation complete", {"full_text": results[1]})
        return segments_json

    def run_preparation(self, segments_json: str, settings: Dict[str, Any]):
        if not segments_json: return None
        if not settings.get("process_now_prep", True):
            self.emit_status(70, "Text preparation skipped")
            return segments_json

        self.emit_status(60, "Preparing continuous text...", stage=2)
        node = PrepareContinuousTextWithPauses()
        
        params = {
            "segments_json_path": segments_json,
            "boundary_pause_marker": settings.get("boundary_pause_marker", "."),
            "initial_pause_duration_ms": settings.get("initial_pause_duration_ms", 0),
            "inter_segment_pause_duration_ms": settings.get("inter_segment_pause_duration_ms", 0),
            "final_pause_duration_ms": settings.get("final_pause_duration_ms", 0),
            "remove_punctuation": settings.get("remove_punctuation_prep", False),
            "process_now": settings.get("process_now_prep", True),
            "original_video_audio_path": settings.get("original_video_audio_path", "")
        }
        
        results = node.prepare_text(**params)
        
        if len(results) >= 6 and results[5]:
            print(results[5]) # Show logs in terminal
            
        self.emit_status(70, "Text preparation complete")
        
        # Check if we should stop for manual editing
        if settings.get("stop_for_editing", False):
            self.emit_status(70, "PROCESS_PAUSED_FOR_EDITING", {"segments_json": segments_json}, stage=2)
            logger.info("Pipeline paused for manual text editing as requested.")
            sys.exit(0) # Exit cleanly, UI will handle resume.

        return segments_json

    def run_tts(self, segments_json: str, engine_name: str, settings: Dict[str, Any]):
        with open(segments_json, "r", encoding="utf-8") as f:
            project_info = json.load(f)
        
        project_dir = project_info.get("project_dir")
        tts_dir = os.path.join(project_dir, "cache_audio")
        
        if not settings.get("process_now_tts", True):
            self.emit_status(80, "TTS Synthesis skipped (using existing cache)")
            return tts_dir

        self.emit_status(75, f"Loading TTS engine: {engine_name}...", stage=3)
        from tts_adapter import get_tts_adapter
        adapter = get_tts_adapter(engine_name)
        
        segments = project_info.get("segments", [])
        os.makedirs(tts_dir, exist_ok=True)
        
        # Clear existing files in the TTS directory to avoid index mismatches from previous runs
        for f in os.listdir(tts_dir):
            if f.endswith(".wav"):
                try:
                    os.remove(os.path.join(tts_dir, f))
                except:
                    pass
        
        # Find reliable reference audio for cloning
        # Priority: 1. Manual Ref, 2. Separated Vocals, 3. Original Audio, 4. Any wav in Temp
        vocal_audio_path = settings.get("manual_speaker_ref")

        if not vocal_audio_path or not os.path.exists(vocal_audio_path):
            audio_preprocess = project_info.get("audio_preprocess", {})
            vocal_audio_path = audio_preprocess.get("separated_audio_path")

            if not vocal_audio_path or not os.path.exists(str(vocal_audio_path)):
                vocal_audio_path = audio_preprocess.get("final_audio_path")

            if not vocal_audio_path or not os.path.exists(str(vocal_audio_path)):
                # Manual search in folders for resilience
                sep_dir = os.path.join(project_dir, "SeparatedAudio")
                options = [
                    os.path.join(sep_dir, "vocals_denoised.wav"),
                    os.path.join(sep_dir, "vocals.wav"),
                    os.path.join(sep_dir, "original_audio.wav")
                ]
                for opt in options:
                    if os.path.exists(opt):
                        vocal_audio_path = opt
                        break
        
        if not vocal_audio_path or not os.path.exists(str(vocal_audio_path)):
             # Absolute fallback: check TempAudio
             temp_dir = os.path.join(project_dir, "TempAudio")
             if os.path.exists(temp_dir):
                 wavs = [f for f in os.listdir(temp_dir) if f.endswith(".wav")]
                 if wavs:
                     vocal_audio_path = os.path.join(temp_dir, wavs[0])

        if not vocal_audio_path or not os.path.exists(str(vocal_audio_path)):
            error_msg = f"❌ Error: Cannot find reference audio in {project_dir}. Synthesis impossible."
            self.emit_status(80, error_msg)
            logger.error(error_msg)
            return None

        # Check if reference trimming is requested
        ref_start = settings.get("ref_start_time", 0.0)
        ref_end = settings.get("ref_end_time", 999999.0)
        if (ref_start > 0 or ref_end < 999999.0):
            self.emit_status(76, f"✂️ Cropping reference: {ref_start}s to {ref_end}s", stage=3)
            cropped_ref_path = os.path.join(tts_dir, "speaker_ref_cropped.wav")
            try:
                self.extract_chunk(vocal_audio_path, ref_start, ref_end, cropped_ref_path)
                if os.path.exists(cropped_ref_path) and os.path.getsize(cropped_ref_path) > 1000:
                    vocal_audio_path = cropped_ref_path
                else:
                    logger.warning("⚠️ Cropped reference is too small or missing. Falling back to full vocal file.")
            except Exception as e:
                logger.error(f"⚠️ Reference cropping failed: {e}. Using full vocal file.")

        total = len(segments)
        for i, seg in enumerate(segments, 1):
            idx = seg.get("index")
            text = seg.get("translated_text", "")
            if not text:
                continue
            
            self.emit_status(75 + int(15 * (i/total)), f"Synthesizing segment {i}/{total}...", stage=3)
            
            # TODO: Better speaker ref selection
            # For now, if we have speaker diarization, we could find a clean clip.
            # But the simplest is to use the full vocal path if the model supports it, 
            # or a specific segment.
            
            # output_filename logic
            prefix = settings.get("filename_prefix", "ru")
            output_filename = f"{prefix}_{idx:03d}.wav"
            output_path = os.path.join(tts_dir, output_filename)
            
            # Use sample_text if provided, otherwise seg text
            sample_text = settings.get("sample_text", "")
            if sample_text and i == 1: # Maybe only for first? No, usually text is per segment.
                 # But if the user provided one big sample text, how to map?
                 # ComfyUI usually expects 1-to-1 or a specific flow.
                 # For now, we use the translated_text from segment.
                 pass
            
            # Many TTS models need a 5-10s reference.
            adapter.synthesize(text, vocal_audio_path, output_path, settings)
            
            seg["generated_audio_file_path"] = output_path

        # Update JSON with audio paths
        with open(segments_json, "w", encoding="utf-8") as f:
            json.dump(project_info, f, ensure_ascii=False, indent=2)
            
        # Emulate ComfyUI Node outputs for UI logs
        self.emit_status(78, f"💾 [Save Audio MP3 To Project Cache] -> {tts_dir}")
        self.emit_status(79, f"📝 [Compare and Store Text] >>\n{tts_dir}\n{tts_dir}\n{tts_dir}")
            
        self.emit_status(80, "TTS Synthesized", {"folder": tts_dir})
        return tts_dir

    def run_finalization(self, segments_json: str, tts_folder: str, settings: Dict[str, Any]):
        if not settings.get("process_now_fin", True):
            self.emit_status(100, "Assembly skipped")
            return "Skipped"

        self.emit_status(90, "Assembling final video...", stage=4)
        node = FinalizeDubbedAudioFromFiles()
        
        params = {
            "segments_json_path": segments_json,
            "synthesized_audio_folder": tts_folder,
            "process_now": True,
            "stretch_method": settings.get("stretch_method", "ffmpeg_atempo"),
            "max_stretch_factor": settings.get("max_stretch_factor", 2.0),
            "allow_stretching": settings.get("allow_stretching", False),
            "use_original_pauses": settings.get("use_original_pauses", True),
            "filename_prefix": settings.get("filename_prefix", "ru"),
            "save_final_audio": settings.get("save_final_audio", True),
            "save_final_json": settings.get("save_final_json", True),
            "trim_start_silence": settings.get("trim_start_silence", True),
            "trim_end_silence": settings.get("trim_end_silence", False),
            "silence_thresh": settings.get("silence_thresh", -50.0),
            "min_silence_len": settings.get("min_silence_len", 200),
            "add_original_background": settings.get("add_original_background", False),
            "add_original_noise": settings.get("add_original_noise", False),
            "original_audio_volume_percent": float(settings.get("original_audio_volume_percent", 0.0))
        }
        
        results = node.finalize_dubbed_audio_from_files(**params)
        final_wav = results[1]
        log_status = results[3]
        
        if not final_wav:
             logger.error(f"FINALIZATION_FAILED:\n{log_status}")
             self.emit_status(100, "Dubbing failed during assembly", {"log": log_status})
        else:
             self.emit_status(100, "Dubbing completed!", {"output_wav": final_wav})
        
        return final_wav

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone Dubbing Engine")
    parser.add_argument("--config", help="Path to JSON config file from UI")
    parser.add_argument("--video", help="Path to video file")
    parser.add_argument("--target_lang", default="ru", help="Target language")
    parser.add_argument("--tts_engine", default="f5-tts", help="TTS Engine to use")
    parser.add_argument("--gpu", action="store_true", default=True, help="Use GPU")
    parser.add_argument("--speaker_ref", default=None, help="Manual speaker reference for cloning")
    parser.add_argument("--start_at_stage", type=int, default=0, help="Stage index to start from (0-4)")
    
    args = parser.parse_args()
    
    ui_config = {}
    if args.config and os.path.exists(args.config):
        with open(args.config, 'r', encoding='utf-8') as f:
            ui_config = json.load(f)
            
    # Extract player values if config provided, else fallback to args
    player_conf = ui_config.get("player", {})
    video_path = player_conf.get("video_path", args.video)
    target_lang = player_conf.get("target_lang", args.target_lang)
    tts_engine = player_conf.get("tts_engine", args.tts_engine)
    
    if not video_path:
        logger.error("No video specified!")
        sys.exit(1)
    
    engine = DubbingEngine()
    engine_name = tts_engine.lower()
    
    # UI Node blocks
    analysis_conf = ui_config.get("analysis", {})
    trans_conf = ui_config.get("translation", {})
    prep_conf = ui_config.get("prepare_text", {})
    tts_conf = ui_config.get("tts", {})
    fin_conf = ui_config.get("finalize", {})
    
    # New engine-specific blocks
    f5_conf = ui_config.get("f5_tts", {})
    vibe_conf = ui_config.get("vibevoice", {})
    qwen_conf = ui_config.get("qwen", {})
    
    # Resolve Model Type for UI
    ui_model_type = tts_conf.get("model_type", "auto")
    
    if engine_name == "qwen":
        if args.speaker_ref or tts_conf.get("speaker_ref"):
            selected_model = "Qwen3-TTS-12Hz-1.7B-Base"
        else:
            selected_model = "Qwen3-TTS-12Hz-1.7B-CustomVoice"
    elif engine_name == "f5tts" or engine_name == "f5-tts":
        if ui_model_type != "auto" and ui_model_type.startswith("F5"):
            selected_model = "F5v1" # Simplification, logic from before can also be kept
        else:
            # Look for local models first
            import folder_paths
            local_models = []
            for p in folder_paths.get_folder_paths("checkpoints"):
                f5_path = os.path.join(p, "F5-TTS")
                if os.path.exists(f5_path):
                    local_models += [f for f in os.listdir(f5_path) if f.endswith((".safetensors", ".pt"))]
            
            if local_models:
                if "Misha.pt" in local_models:
                    selected_model = "model://Misha.pt"
                elif "Rus_model.safetensors" in local_models:
                    selected_model = "model://Rus_model.safetensors"
                elif "Rus_model.pt" in local_models:
                    selected_model = "model://Rus_model.pt"
                elif "Misha.safetensors" in local_models:
                    selected_model = "model://Misha.safetensors"
                else:
                    selected_model = f"model://{local_models[0]}"
            else:
                selected_model = "F5v1"
    else:
        # VibeVoice
        if ui_model_type != "auto" and "VibeVoice" in ui_model_type:
            selected_model = ui_model_type
        else:
            import folder_paths
            v_models = folder_paths.get_folder_paths("vibevoice")
            local_v = []
            if v_models:
                v_dir = v_models[0]
                if os.path.exists(v_dir):
                    local_v = [f for f in os.listdir(v_dir) if os.path.isdir(os.path.join(v_dir, f)) and f != "loras" and f != "tokenizer"]
            
            if "VibeVoice-7B" in local_v:
                selected_model = "VibeVoice-7B"
            elif "VibeVoice-1.5B" in local_v:
                selected_model = "VibeVoice-1.5B"
            elif local_v:
                selected_model = local_v[0]
            else:
                selected_model = "VibeVoice-1.5B"
    
    try:
        settings = {
            "source_language": analysis_conf.get("source_language", "en"),
            "target_language": target_lang,
            "whisper_model": analysis_conf.get("whisper_model", "large"),
            "use_gpu": analysis_conf.get("use_gpu", args.gpu),
            "detect_silence": analysis_conf.get("detect_silence", True),
            "silence_threshold_db": analysis_conf.get("silence_threshold_db", -40.0),
            "min_silence_duration_s": analysis_conf.get("min_silence_duration_s", 0.20),
            "fallback_initial_pause_ms": analysis_conf.get("fallback_initial_pause_ms", 0),
            "long_pause_min_duration_s": analysis_conf.get("long_pause_min_duration_s", 0.5),
            "merge_segments_factor": analysis_conf.get("merge_segments_factor", 3.0),
            "max_pause_duration_for_merge_s": analysis_conf.get("max_pause_duration_for_merge_s", 0.5),
            "clean_temp": analysis_conf.get("clean_temp", True),
            "clear_cache": analysis_conf.get("clear_cache", True),
            "remove_punctuation": analysis_conf.get("remove_punctuation", False),
            "process_now": analysis_conf.get("process_now", True),
            "separation_method": analysis_conf.get("separation_method", "demucs"),
            "skip_noise_reduction": analysis_conf.get("skip_noise_reduction", False),
            "min_segment_duration_s": analysis_conf.get("min_segment_duration_s", 0.30),
            "enable_speaker_diarization": analysis_conf.get("enable_speaker_diarization", analysis_conf.get("enable_diarization", False)), 
            "speaker_label_style": analysis_conf.get("speaker_label_style", "none"),
            "use_faster_whisper": analysis_conf.get("use_faster_whisper", True),
            "enable_vad": analysis_conf.get("enable_vad", True),
            "vad_threshold": analysis_conf.get("vad_threshold", 0.3),
            "repetitive_filter": analysis_conf.get("repetitive_filter", True),
            "projects_dir": player_conf.get("projects_dir", ""),
            "trim_video_start_ms": player_conf.get("trim_video_start_ms", 0),
            "trim_video_end_ms": player_conf.get("trim_video_end_ms", 0),
            
            "trans_source_language": trans_conf.get("source_language", "en"),
            "trans_target_language": trans_conf.get("target_language", target_lang),
            "translator": trans_conf.get("translator", "google"),
            "remove_punctuation_before_translation": trans_conf.get("remove_punctuation_before_translation", True),
            "remove_punctuation_after_translation": trans_conf.get("remove_punctuation_after_translation", False),
            "retain_translation_punctuation": trans_conf.get("retain_translation_punctuation", True),
            "fix_cyrillic_orthography": trans_conf.get("fix_cyrillic_orthography", True),
            "add_stress": trans_conf.get("add_stress", True),
            "stress_engine": trans_conf.get("stress_engine", "RUAccent"),
            "process_now_trans": trans_conf.get("process_now", True),
            "original_video_audio_path": trans_conf.get("original_video_audio_path", ""),
            
            "boundary_pause_marker": prep_conf.get("boundary_pause_marker", "."),
            "initial_pause_duration_ms": prep_conf.get("initial_pause_duration_ms", 0),
            "inter_segment_pause_duration_ms": prep_conf.get("inter_segment_pause_duration_ms", 0),
            "final_pause_duration_ms": prep_conf.get("final_pause_duration_ms", 0),
            "remove_punctuation_prep": prep_conf.get("remove_punctuation", False),
            "process_now_prep": prep_conf.get("process_now", True),
            
            "stretch_method": fin_conf.get("stretch_method", "ffmpeg_atempo"),
            "max_stretch_factor": fin_conf.get("max_stretch_factor", 2.0),
            "allow_stretching": fin_conf.get("allow_stretching", False),
            "use_original_pauses": fin_conf.get("use_original_pauses", True),
            "filename_prefix": fin_conf.get("filename_prefix", "ru"),
            "save_final_audio": fin_conf.get("save_final_audio", True),
            "save_final_json": fin_conf.get("save_final_json", True),
            "trim_start_silence": fin_conf.get("trim_start_silence", True),
            "trim_end_silence": fin_conf.get("trim_end_silence", False),
            "silence_thresh": fin_conf.get("silence_thresh", -50.0),
            "min_silence_len": fin_conf.get("min_silence_len", 200),
            "add_original_background": fin_conf.get("add_original_background", False),
            "add_original_noise": fin_conf.get("add_original_noise", False),
            "original_audio_volume_percent": fin_conf.get("original_audio_volume_percent", 0.0),
            "stop_for_editing": prep_conf.get("stop_for_editing", False),
            "process_now_tts": tts_conf.get("process_now", True),
            "process_now_fin": fin_conf.get("process_now", True),
            "projects_dir": player_conf.get("projects_dir", "")
        }
            
        # Custom logic for engine-specific parameters
        if "vibe" in engine_name:
            settings["seed"] = vibe_conf.get("seed", 42)
            selected_model = vibe_conf.get("model", selected_model)
        elif "f5" in engine_name:
            settings["seed"] = f5_conf.get("seed", 985)
            settings["vocoder"] = f5_conf.get("vocoder", "auto")
            settings["speed"] = f5_conf.get("speed", 1.0)
            settings["model_type"] = f5_conf.get("model_type", "auto")
        elif "qwen" in engine_name:
            selected_model = qwen_conf.get("model", selected_model)

        settings["model"] = selected_model
        settings["manual_speaker_ref"] = tts_conf.get("speaker_ref", args.speaker_ref)
        settings["sample_text"] = tts_conf.get("sample_text", "")
        
        # Use current or passed segments_json
        seg_json = engine.project_data.get("segments_json") or analysis_conf.get("segments_json_path")
        if not seg_json and video_path:
            # Fallback for CLI when skipping stage 0
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            proj_dir = settings.get("projects_dir") or "input/VideoDubbingProjects"
            seg_json = os.path.join(proj_dir, base_name, "segments.json")
        settings["segments_json_path"] = seg_json
        
        # 1. Analyze
        if args.start_at_stage <= 0:
            seg_json = engine.run_analysis(video_path, settings)
            if not seg_json:
                 logger.error("❌ Analysis failed to produce segments.json")
                 sys.exit(1)
        
        if not seg_json or not os.path.exists(seg_json):
            logger.error(f"❌ Error: segments.json not found at {seg_json}. Run Stage 1 (Analysis) first!")
            sys.exit(1)
        
        # 2. Translate
        if args.start_at_stage <= 1:
            engine.run_translation(seg_json, settings)
        
        # 3. Prepare Text
        if args.start_at_stage <= 2:
            engine.run_preparation(seg_json, settings)
        
        # 4. TTS
        tts_dir = None
        if args.start_at_stage <= 3:
            tts_dir = engine.run_tts(seg_json, engine_name, settings)
        else:
             # If we start at 4, we need tts_dir
             tts_dir = fin_conf.get("synthesized_audio_folder") or os.path.join(os.path.dirname(seg_json), "cache_audio")
        
        # 5. Finalize
        if args.start_at_stage <= 4:
            output_wav = engine.run_finalization(seg_json, tts_dir, settings)
            if not output_wav or (isinstance(output_wav, str) and not os.path.exists(output_wav)):
                 if isinstance(output_wav, str) and "Error" in output_wav:
                     logger.error(f"❌ Finalization failed: {output_wav}")
                 else:
                     logger.error("❌ Finalization failed to produce a valid audio file.")
                 sys.exit(1)
            
            print(f"SUCCESS: Final audio at {output_wav}")
            
        # Hard exit to prevent native DLL crashes (like CTranslate2/PyTorch) during Python GC teardown
        os._exit(0)
        
    except Exception as e:
        logger.error(f"ENGINE_ERROR:{str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)
