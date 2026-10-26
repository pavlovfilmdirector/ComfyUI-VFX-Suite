import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

import os
import json
import argparse
import logging
import re

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tts_adapter import get_tts_adapter

# Configure logging with explicit UTF-8 stream handler
logger = logging.getLogger("BookNarration")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter('%(message)s'))
if not logger.handlers:
    logger.addHandler(handler)

def emit_status(progress: int, status: str, details=None):
    msg = {"progress": progress, "status": status, "details": details}
    print(f"STATUS_UPDATE:{json.dumps(msg, ensure_ascii=False)}", flush=True)

def get_ffmpeg_path():
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
        import subprocess
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        logger.error(f"FFmpeg audio extraction failed: {e}")
        raise

def split_text(text, max_words=150):
    """Splits text into manageable chunks. Prioritizes punctuation if it exists."""
    # First, detect if there is any sentence-ending punctuation
    has_punctuation = bool(re.search(r'[.!?…]', text))
    
    if not has_punctuation:
        # Fallback: Strict word-count splitting
        words = text.split()
        chunks = []
        for i in range(0, len(words), max_words):
            chunks.append(" ".join(words[i : i + max_words]))
        return chunks

    # Primary logic: Splitting by punctuation but respecting the max_words limit
    paragraphs = re.split(r'\n\s*\n', text)
    chunks = []
    curr_chunk_words = []
    
    for p in paragraphs:
        # Split paragraph into sentences
        sentences = re.split(r'(?<=[.!?…])\s+', p)
        
        for s in sentences:
            s_words = s.split()
            if not s_words: continue
            
            # If a single sentence is still over the limit, it must be force-split by words
            if len(s_words) > max_words:
                # Flush current chunk first
                if curr_chunk_words:
                    chunks.append(" ".join(curr_chunk_words))
                    curr_chunk_words = []
                
                # Split this giant sentence
                for i in range(0, len(s_words), max_words):
                    chunk_slice = s_words[i : i + max_words]
                    if len(chunk_slice) == max_words:
                        chunks.append(" ".join(chunk_slice))
                    else:
                        curr_chunk_words = chunk_slice
            
            # Try to fit the sentence into the current chunk
            elif len(curr_chunk_words) + len(s_words) <= max_words:
                curr_chunk_words.extend(s_words)
            else:
                # Sentence doesn't fit, flush current chunk and start new one
                if curr_chunk_words:
                    chunks.append(" ".join(curr_chunk_words))
                curr_chunk_words = s_words
                
    if curr_chunk_words:
        chunks.append(" ".join(curr_chunk_words))
        
    return chunks

def run_narration(config_path):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            settings = json.load(f)
            
        text = settings["text"]
        out_dir = settings["output_dir"]
        ref_path = settings["speaker_ref"]
        ref_start = settings.get("ref_start_time", 0.0)
        ref_end = settings.get("ref_end_time", 999999.0)
        
        # Determine actual reference path to use (cropped or original)
        use_ref_path = ref_path
        if os.path.exists(ref_path) and (ref_start > 0 or ref_end < 999999.0):
            emit_status(2, f"Cropping reference audio from {ref_start}s to {ref_end}s...")
            temp_dir = os.path.join(PROJECT_ROOT, "TempAudio")
            os.makedirs(temp_dir, exist_ok=True)
            use_ref_path = os.path.join(temp_dir, "book_narration_ref.wav")
            extract_chunk(ref_path, ref_start, ref_end, use_ref_path)
        
        if not os.path.exists(out_dir):
            os.makedirs(out_dir, exist_ok=True)
            
        engine_name = settings["tts_general"]["engine"]
        adapter = get_tts_adapter(engine_name)
        
        chunks = split_text(text)
        total_chunks = len(chunks)
        
        emit_status(5, f"Text split into {total_chunks} chunks.")
        
        # Prepare adapter settings
        adapter_settings = {
            "speaker_ref_text": settings.get("tts_general", {}).get("ref_text", "")
        }
        if "f5" in engine_name.lower():
            adapter_settings.update({
                "seed": settings["f5_tts"]["seed"],
                "model": settings["f5_tts"]["model"],
                "vocoder": settings["f5_tts"]["vocoder"],
                "speed": settings["f5_tts"]["speed"],
                "model_type": settings["f5_tts"]["model_type"]
            })
        elif "vibe" in engine_name.lower():
            adapter_settings.update({
                "model": settings["vibevoice"]["model"],
                "seed": settings["vibevoice"]["seed"]
            })
        elif "qwen" in engine_name.lower():
            adapter_settings.update({
                "model": settings["qwen"]["model"]
            })

        for i, chunk in enumerate(chunks):
            chunk_num = i + 1
            progress = int((i / total_chunks) * 90) + 5
            emit_status(progress, f"Synthesizing chunk {chunk_num}/{total_chunks}...")
            
            # Show a snippet of the text in the log
            snippet = chunk[:50] + "..." if len(chunk) > 50 else chunk
            logger.info(f"Chunk {chunk_num}: {snippet}")
            
            output_file = os.path.join(out_dir, f"part_{chunk_num:03d}.wav")
            
            # Add a small text buffer to prevent word clipping at the end of the chunk
            text_with_buffer = chunk + " ."
            adapter.synthesize(
                text=text_with_buffer,
                speaker_ref_path=use_ref_path,
                output_path=output_file,
                settings=adapter_settings
            )
            
            # Add 1.0s of silence to provide a natural pause between chunks
            if os.path.exists(output_file):
                import torch
                import torchaudio
                waveform, sr = torchaudio.load(output_file)
                
                # Padding: 1.0s as requested for natural spacing
                padding_samples = int(1.0 * sr) 
                padding = torch.zeros((waveform.shape[0], padding_samples), dtype=waveform.dtype)
                waveform_padded = torch.cat([waveform, padding], dim=1)
                torchaudio.save(output_file, waveform_padded, sr)
            
        emit_status(95, "Merging audio chunks...")
        
        # Merge all chunks
        base_name = settings.get("base_name", "final_narration")
        final_file = os.path.join(out_dir, f"{base_name}.wav")
        concat_file = os.path.join(out_dir, "concat.txt")
        
        with open(concat_file, "w", encoding="utf-8") as f:
            for i in range(1, total_chunks + 1):
                part_file = f"part_{i:03d}.wav"
                # FFmpeg concat requires forward slashes or escaped backslashes
                f.write(f"file '{part_file}'\n")
                
        ffmpeg_exe = get_ffmpeg_path()
        cmd = [
            ffmpeg_exe, "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c", "copy",
            final_file
        ]
        
        try:
            import subprocess
            subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=out_dir)
            # Optional cleanup of concat list
            if os.path.exists(concat_file):
                os.remove(concat_file)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to merge audio chunks: {e}")
            raise

        emit_status(100, f"✅ Done! Audio saved to {final_file}", {"output_wav": final_file})
        
    except Exception as e:
        logger.error(f"❌ Error during narration: {str(e)}")
        emit_status(100, "Error", {"error": str(e)})
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    run_narration(args.config)
