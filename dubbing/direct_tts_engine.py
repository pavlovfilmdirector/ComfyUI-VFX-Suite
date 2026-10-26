import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        # Fallback for very old python or specific environments
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import argparse
import json
import logging
import subprocess
import os
import re

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Embed local FFmpeg by prepending to PATH
bin_dir = os.path.join(PROJECT_ROOT, "bin")
if os.path.exists(bin_dir):
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

from nodes.utils import get_whisper_model, transcribe_whisper
from tts_adapter import get_tts_adapter
from deep_translator import GoogleTranslator

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("VoiceClone")

def emit_status(progress: int, status: str, details=None):
    msg = {"progress": progress, "status": status, "details": details}
    print(f"STATUS_UPDATE:{json.dumps(msg, ensure_ascii=False)}", flush=True)

def get_ffmpeg_path():
    """Return the path to the local ffmpeg binary, or fallback to system ffmpeg."""
    local_ffmpeg = os.path.join(PROJECT_ROOT, "bin", "ffmpeg.exe")
    if os.path.exists(local_ffmpeg):
        return local_ffmpeg
    return "ffmpeg"

def extract_chunk(input_path, start_s, end_s, output_path):
    ffmpeg_exe = get_ffmpeg_path()
    cmd = [
        ffmpeg_exe, "-y",
        "-i", input_path,
        "-ss", str(start_s),
        "-to", str(end_s),
        "-vn",
        "-c:a", "pcm_s16le",
        "-ar", "24000",
        output_path
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg audio extraction failed: {e}")
        raise

def translate_long_text(translator, text, chunk_size=4500):
    """Splits text into chunks of chunk_size characters and translates each."""
    if not text.strip():
        return ""
        
    if len(text) <= chunk_size:
        return translator.translate(text)
        
    # Split by paragraphs or sentences if possible, or just by size
    chunks = []
    while text:
        if len(text) <= chunk_size:
            chunks.append(text)
            break
            
        # Try to find a good split point (period, newline, space)
        split_at = chunk_size
        search_range = text[chunk_size-500:chunk_size]
        match = re.search(r'[\n.!?]\s', search_range[::-1]) # Search backwards
        if match:
            split_at = chunk_size - match.start()
        else:
            # Fallback to last space
            match = re.search(r'\s', search_range[::-1])
            if match:
                split_at = chunk_size - match.start()
        
        chunks.append(text[:split_at])
        text = text[split_at:].strip()
        
    translated_chunks = []
    for i, chunk in enumerate(chunks):
        emit_status(80 + int(15 * (i/len(chunks))), f"Translating chunk {i+1}/{len(chunks)}...")
        translated_chunks.append(translator.translate(chunk))
        
    return " ".join(translated_chunks)

def run_transcribe(settings):
    emit_status(10, "Extracting audio chunk...")
    audio_load = settings.get("load_audio", {})
    audio_path = audio_load.get("file_path", "")
    start_s = audio_load.get("start_time", 0)
    end_s = audio_load.get("end_time", 999999)
    
    whisper_sett = settings.get("whisper", {})
    whisper_model_name = whisper_sett.get("model", "large-v3-turbo")
    whisper_lang = whisper_sett.get("language", "ru")
    
    # Validation
    if start_s >= end_s:
        logger.error("❌ End time must be greater than start time.")
        sys.exit(1)
        
    temp_dir = os.path.join(PROJECT_ROOT, "TempAudio")
    os.makedirs(temp_dir, exist_ok=True)
    temp_wav = os.path.join(temp_dir, "voice_clone_ref.wav")
    
    extract_chunk(audio_path, start_s, end_s, temp_wav)
    emit_status(30, "Audio chunk extracted successfully.")
    
    # Check if there is a 'speaker_ref' being passed instead (as from Voice Clone override)
    # BUG FIX: Only override if audio_path (primary source) is EMPTY
    tts_general = settings.get("tts_general", {})
    speaker_ref = tts_general.get("speaker_ref", "").strip()
    if not audio_path and speaker_ref and os.path.exists(speaker_ref):
        ref_start = tts_general.get("ref_start_time", 0.0)
        ref_end = tts_general.get("ref_end_time", 999999.0)
        emit_status(35, f"Using custom reference audio for transcription: {os.path.basename(speaker_ref)}")
        if ref_start > 0 or ref_end < 999999.0:
            emit_status(36, f"Cropping reference audio: {ref_start}s to {ref_end}s...")
            extract_chunk(speaker_ref, ref_start, ref_end, temp_wav)
        else:
            import shutil
            shutil.copy2(speaker_ref, temp_wav)
            
    emit_status(40, f"Loading Whisper ({whisper_model_name})...")
    
    # Load model
    device = "cuda" # Defaulting to CUDA if available
    model = get_whisper_model(whisper_model_name, device)
    
    emit_status(60, "Running transcription...")
    res = transcribe_whisper(model, temp_wav, whisper_lang.lower()[:2])
    
    text = res.get("text", "").strip()
    
    # Check if we need to translate (Target Language != None)
    target_lang_str = whisper_sett.get("target_language", "None")
    if target_lang_str != "None":
        lang_map = {
            "Russian": "ru",
            "English": "en",
            "Spanish": "es",
            "French": "fr",
            "German": "de"
        }
        target_lang_code = lang_map.get(target_lang_str, "ru")
        src_lang_code = lang_map.get(whisper_lang, "auto")
        
        # Don't translate if source and target are the same
        if src_lang_code != target_lang_code:
            emit_status(80, f"Translating text to {target_lang_str}...")
            try:
                translator = GoogleTranslator(source=src_lang_code, target=target_lang_code)
                translated_text = translate_long_text(translator, text)
                if translated_text:
                    text = translated_text
            except Exception as e:
                logger.error(f"Translation failed: {e}")
    
    emit_status(100, "Transcription completed!")
    
    # Special exact string prefix for main.py to intercept
    print(f"\nTRANSCRIBE_RESULT:{text}")

def run_tts(settings):
    emit_status(10, "Preparing for TTS generation...")
    audio_load = settings.get("load_audio", {})
    audio_path = audio_load.get("file_path", "")
    start_s = audio_load.get("start_time", 0)
    end_s = audio_load.get("end_time", 999999)

    # Decide where to put files
    out_dir = settings.get("output_dir")
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        temp_dir = out_dir
        base_name = settings.get("base_name", "voice_clone")
    else:
        temp_dir = os.path.join(PROJECT_ROOT, "TempAudio")
        out_dir = os.path.join(PROJECT_ROOT, "VoiceCloneOutputs")
        base_name = "output"
        
    os.makedirs(temp_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    
    temp_wav = os.path.join(temp_dir, "voice_clone_ref.wav")
    out_wav = os.path.join(out_dir, f"{base_name}.wav")
    
    # Priority: 1. Manual Reference -> 2. Video Chunk -> 3. Fallback stems
    tts_general = settings.get("tts_general", {})
    speaker_ref = tts_general.get("speaker_ref", "").strip()
    
    found_ref = False
    if speaker_ref and os.path.exists(speaker_ref):
        ref_start = tts_general.get("ref_start_time", 0.0)
        ref_end = tts_general.get("ref_end_time", 999999.0)
        if ref_start > 0 or ref_end < 999999.0:
            emit_status(30, f"Cropping custom reference: {os.path.basename(speaker_ref)}")
            try:
                extract_chunk(speaker_ref, ref_start, ref_end, temp_wav)
                found_ref = True
            except:
                logger.warning("Cropping failed, using full reference file.")
        
        if not found_ref:
            import shutil
            shutil.copy2(speaker_ref, temp_wav)
            found_ref = True
            emit_status(30, f"Using custom reference: {os.path.basename(speaker_ref)}")

    if not found_ref:
        if audio_path and os.path.exists(audio_path):
            emit_status(20, "Extracting audio chunk from source...")
            try:
                extract_chunk(audio_path, start_s, end_s, temp_wav)
                found_ref = True
            except Exception as e:
                logger.error(f"Failed to extract source chunk: {e}")
    
    if not found_ref or not os.path.exists(temp_wav):
        error_msg = "❌ Error: No valid reference audio found for cloning."
        emit_status(100, error_msg)
        logger.error(error_msg)
        sys.exit(1)
    
    engine_name = settings["tts_general"]["engine"].lower()
    emit_status(40, f"Loading {engine_name}...")
    adapter = get_tts_adapter(engine_name)
    
    # Mapping UI language names to codes
    lang_map = {
        "Russian": "ru",
        "English": "en",
        "Spanish": "es",
        "French": "fr",
        "German": "de"
    }
    
    # Determine the correct target language code
    target_lang_str = settings.get("whisper", {}).get("target_language", "Russian")
    target_lang_code = lang_map.get(target_lang_str, "ru")
    
    # Priority for reference text:
    # 1. tts_general["ref_text"] (Explicitly for the reference audio)
    # 2. settings["text_input"]["text"] (Fallback to main transcript ONLY if same file)
    ref_text = tts_general.get("ref_text", "").strip()
    if not ref_text:
        # If no ref_text is provided, we might be using the source video as ref.
        # In that case, we can try to use text_input, but ONLY as a fallback.
        ref_text = settings.get("text_input", {}).get("text", "")
    
    # Mapping voice clone settings to tts_adapter settings based on engine
    tts_settings = {
        "speaker_ref_text": ref_text, 
        "target_language": target_lang_code
    }
    
    if engine_name == "f5-tts":
        tts_settings.update({
            "seed": settings["f5_tts"]["seed"],
            "model": settings["f5_tts"]["model"],
            "vocoder": settings["f5_tts"]["vocoder"],
            "speed": settings["f5_tts"]["speed"],
            "model_type": settings["f5_tts"]["model_type"]
        })
    elif engine_name == "qwen":
        tts_settings.update({
            "model": settings["qwen"]["model"]
        })
    elif engine_name == "vibevoice":
        tts_settings.update({
            "model": settings["vibevoice"]["model"],
            "seed": settings["vibevoice"]["seed"]
        })
    
    target_text = settings["tts_general"]["target_text"]
    
    # Split text into manageable chunks (sentences) to avoid OOM or timeout crashes
    # Limit to roughly 500 characters per chunk for reliable synthesis
    chunks = []
    raw_sentences = re.split(r'(?<=[.!?])\s+', target_text)
    curr_chunk = ""
    for s in raw_sentences:
        if len(curr_chunk) + len(s) < 500:
            curr_chunk += (" " if curr_chunk else "") + s
        else:
            if curr_chunk: chunks.append(curr_chunk)
            curr_chunk = s
    if curr_chunk: chunks.append(curr_chunk)
    
    if not chunks:
        chunks = [target_text]

    chunk_files = []
    total_chunks = len(chunks)
    
    try:
        emit_status(60, f"Synthesizing voice (0/{total_chunks})...")
        for i, chunk in enumerate(chunks):
            # Show progress in terminal
            emit_status(60 + int(30 * (i/total_chunks)), f"Synthesizing part {i+1} of {total_chunks}...")
            
            chunk_wav = os.path.join(temp_dir, f"tts_part_{i:03d}.wav")
            # Add a small text buffer to prevent word clipping at the end of the chunk
            text_with_buffer = chunk + " ."
            adapter.synthesize(
                text=text_with_buffer,
                speaker_ref_path=temp_wav,
                output_path=chunk_wav,
                settings=tts_settings
            )
            
            # Add 1.0s of silence to prevent truncation at the end of chunks
            if os.path.exists(chunk_wav):
                import torch
                import torchaudio
                waveform, sr = torchaudio.load(chunk_wav)
                
                # Padding: 1.0s as requested by user for natural pause
                padding_samples = int(1.0 * sr) 
                padding = torch.zeros((waveform.shape[0], padding_samples), dtype=waveform.dtype)
                waveform_padded = torch.cat([waveform, padding], dim=1)
                
                # To implement "trimming" for natural feel, we can trim the very end 
                # but for now we just add the full 1s requested.
                torchaudio.save(chunk_wav, waveform_padded, sr)
                
            if os.path.exists(chunk_wav):
                chunk_files.append(chunk_wav)

        if not chunk_files:
            raise RuntimeError("Failed to generate any audio fragments.")

        # Merge segments using FFmpeg concat
        emit_status(90, "Merging audio fragments...")
        concat_list_path = os.path.join(temp_dir, "concat_list.txt")
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for cw in chunk_files:
                # FFmpeg requires escaping single quotes in file paths for concat
                safe_path = cw.replace("'", "'\\''")
                f.write(f"file '{safe_path}'\n")

        # Use FFmpeg to concatenate
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", concat_list_path, "-c", "copy", out_wav
        ]
        subprocess.run(cmd, check=True)
        
        emit_status(100, f"Voice Clone completed! Saved to {out_wav}", {"output_wav": out_wav})

    except Exception as e:
        logger.error(f"TTS Synthesis Failed: {e}")
        emit_status(100, f"❌ Error: {e}")
        sys.exit(1)
    finally:
        # Cleanup temporary chunks if needed, but keep final out_wav
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["transcribe", "tts"], required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    
    with open(args.config, 'r', encoding='utf-8') as f:
        settings = json.load(f)
        
    if args.mode == "transcribe":
        run_transcribe(settings)
    elif args.mode == "tts":
        run_tts(settings)
