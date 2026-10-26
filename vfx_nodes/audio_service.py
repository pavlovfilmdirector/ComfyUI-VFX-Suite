"""
VFX Studio Audio & Neural TTS Synthesis Service
================================================
Provides synchronous voice generation, translation, and lip-sync alignment:
1. High-fidelity neural voice synthesis (Russian / English).
2. Translation between Russian and English.
3. Automatic duration calculation (seconds -> frames at project FPS).
4. Audio waveform extraction for timeline scrubbing in 3D Layout Studio.
5. Export to WAV for LTX-Video Audio-VAE and Wav2Lip conditioning.
"""

import os
import sys
import json
import asyncio
import logging
try:
    import torchaudio  # optional: only needed for TTS dialogue export
except ImportError:
    torchaudio = None
import numpy as np

LOGGER = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output_cache")
AUDIO_DIR = os.path.join(CACHE_DIR, "audio")
os.makedirs(AUDIO_DIR, exist_ok=True)

VOICE_MAP = {
    "dmitry": "ru-RU-DmitryNeural",
    "svetlana": "ru-RU-SvetlanaNeural",
    "guy": "en-US-GuyNeural",
    "christopher": "en-US-ChristopherNeural",
    "jenny": "en-US-JennyNeural",
    "brian": "en-GB-BrianNeural"
}


async def _synthesize_edge_tts(text: str, voice_code: str, out_mp3: str):
    import edge_tts
    communicate = edge_tts.Communicate(text, voice_code)
    await communicate.save(out_mp3)


try:
    from .local_config import cloud_services_allowed   # edge-tts / Google translate are cloud services
except ImportError:  # pragma: no cover
    from local_config import cloud_services_allowed


def generate_speech(text: str, voice: str = "dmitry", auto_translate: bool = False, target_lang: str = "en", fps: float = 24.0) -> dict:
    """
    Synthesizes speech from text, optionally translates, and saves WAV for LTX & Wav2Lip.
    """
    if not cloud_services_allowed():
        raise RuntimeError("Voice synthesis uses a cloud service (edge-tts) and is off by default. "
                           "LTX-2.5 generates the soundtrack itself; to enable edge-tts anyway set KESHIKI_ALLOW_CLOUD=1.")
    clean_text = text.strip()
    if not clean_text:
        raise ValueError("Text cannot be empty.")

    translated_text = clean_text
    if auto_translate:
        try:
            from .vfx_agent_service import generate_director_plan
            # Use director/Gemma or simple mapping
            if target_lang == "en":
                # Quick translation via deep_translator or fallback
                try:
                    from deep_translator import GoogleTranslator
                    translated_text = GoogleTranslator(source="auto", target="en").translate(clean_text)
                except Exception:
                    translated_text = clean_text
        except Exception as e:
            LOGGER.warning(f"Translation skipped: {e}")

    voice_code = VOICE_MAP.get(voice.lower(), VOICE_MAP["dmitry"])
    if auto_translate and target_lang == "en" and voice.lower() in ["dmitry", "svetlana"]:
        voice_code = VOICE_MAP["christopher"]

    out_mp3 = os.path.join(AUDIO_DIR, "current_dialogue.mp3")
    out_wav = os.path.join(AUDIO_DIR, "current_dialogue.wav")

    # Run synthesis
    asyncio.run(_synthesize_edge_tts(translated_text, voice_code, out_mp3))

    # Convert to standard 16-bit 24000/44100Hz WAV
    if torchaudio is None:
        raise RuntimeError("torchaudio is not installed: pip install torchaudio")
    sig, sr = torchaudio.load(out_mp3)
    torchaudio.save(out_wav, sig, sr)

    duration_sec = float(sig.shape[-1]) / float(sr)
    total_frames = max(1, int(round(duration_sec * fps)))

    # Generate waveform preview (64 points)
    waveform = []
    step = max(1, sig.shape[-1] // 64)
    mono = sig[0].cpu().numpy()
    for i in range(0, sig.shape[-1], step):
        chunk = mono[i:i + step]
        if len(chunk) > 0:
            waveform.append(float(np.abs(chunk).max()))
    if len(waveform) > 64:
        waveform = waveform[:64]

    info = {
        "text": clean_text,
        "translated_text": translated_text,
        "voice": voice_code,
        "duration_sec": round(duration_sec, 3),
        "total_frames": total_frames,
        "fps": fps,
        "wav_path": out_wav,
        "mp3_path": out_mp3,
        "waveform": waveform
    }

    info_path = os.path.join(AUDIO_DIR, "info.json")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)

    return info


def get_current_audio_info() -> dict:
    info_path = os.path.join(AUDIO_DIR, "info.json")
    if os.path.exists(info_path):
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"has_audio": False}
