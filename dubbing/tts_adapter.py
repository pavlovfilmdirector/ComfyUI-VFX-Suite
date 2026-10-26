import os
import re
import torch
import numpy as np
import torchaudio
from typing import List, Dict, Any

def normalize_numbers(text: str, lang: str = 'ru') -> str:
    """Заменяет числа на слова, обрабатывая дефисы и точки (8-1 -> восемь один)"""
    # Сначала заменим дефисы между цифрами на пробелы
    text = re.sub(r'(\d)[\-\—\–\.](\d)', r'\1 \2', text)
    
    def replace(match):
        number = match.group(0)
        try:
            from num2words import num2words
            return num2words(int(number), lang=lang)
        except ImportError:
            import logging
            logging.getLogger("TTSAdapter").warning("⚠️ Library 'num2words' not found. Numbers will not be normalized!")
            return number
        except Exception:
            return number
    
    # Ищем последовательности цифр
    return re.sub(r'\d+', replace, text)

class BaseTTSAdapter:
    def synthesize(self, text: str, speaker_ref_path: str, output_path: str, settings: Dict[str, Any]):
        raise NotImplementedError

class VibeVoiceAdapter(BaseTTSAdapter):
    def __init__(self):
        # We import here to avoid dependency issues if not used
        from VibeVoice_ComfyUI.nodes.single_speaker_node import VibeVoiceSingleSpeakerNode
        self.node = VibeVoiceSingleSpeakerNode()
        
    def synthesize(self, text: str, speaker_ref_path: str, output_path: str, settings: Dict[str, Any]):
        # Mock ComfyUI specific inputs
        voice_to_clone = None
        if speaker_ref_path and os.path.exists(speaker_ref_path):
            waveform, sr = torchaudio.load(speaker_ref_path)
            voice_to_clone = {"waveform": waveform.unsqueeze(0), "sample_rate": sr}

        # Clean text from numeric prefixes like [1], 1., 1) etc.
        import re
        # This regex strips leading markers like [1], 1., 1), -, --, etc.
        text = re.sub(r'^([0-9\.\)\]\}\[\{\-\—\s]+)', '', text).strip()
        
        # Normalize remaining numbers to words
        text = normalize_numbers(text, lang='ru' if settings.get("target_language") == "ru" else 'en')

        # Call the node's generate_speech method
        results = self.node.generate_speech(
            text=text,
            model=settings.get("model", "VibeVoice-1.5B"),
            voice_to_clone=voice_to_clone,
            seed=settings.get("seed", 42),
            cfg_scale=settings.get("cfg_scale", 1.3),
            diffusion_steps=settings.get("steps", 20)
        )
        
        # Convert to 16-bit PCM for compatibility with pydub/wav
        waveform_int16 = (waveform.squeeze(0).clamp(-1, 1) * 32767).to(torch.int16)
        torchaudio.save(output_path, waveform_int16, sample_rate)
        return output_path

class F5TTSAdapter(BaseTTSAdapter):
    def __init__(self):
        from comfyui_f5_tts.F5TTS import F5TTSAudioInputs
        self.node = F5TTSAudioInputs()
        
    def synthesize(self, text: str, speaker_ref_path: str, output_path: str, settings: Dict[str, Any]):
        # Load ref audio
        if not speaker_ref_path or not os.path.exists(speaker_ref_path):
             logger.warning(f"⚠️ F5TTS: Missing speaker reference at {speaker_ref_path}. Using silent placeholder (Synthesis will likely fail).")
             waveform = torch.zeros(1, 1, 24000)
             sr = 24000
        else:
             waveform, sr = torchaudio.load(speaker_ref_path)
             # F5TTS expects (batch=1, channels, samples)
             if waveform.dim() == 2:
                 waveform = waveform.unsqueeze(0)
            
        sample_audio = {"waveform": waveform, "sample_rate": sr}
        
        # Clean text from numeric prefixes like [1], 1., 1) etc.
        import re
        # This regex strips leading markers like [1], 1., 1), -, --, etc.
        text = re.sub(r'^([0-9\.\)\]\}\[\{\-\—\s]+)', '', text).strip()
        
        # Normalize remaining numbers to words
        text = normalize_numbers(text, lang='ru' if settings.get("target_language") == "ru" else 'en')
        
        # Call the node's create method
        # Use sample_text from settings, but handle naming mismatch
        ref_text = settings.get("sample_text", "") or settings.get("speaker_ref_text", "")
        
        # Safety: if ref_text is the default Russian literary text but we are doing English or something else, 
        # or if it's just clearly a placeholder, clear it to let Whisper transcribe automatically.
        if "шляпу и длинное пальто" in ref_text:
             print("⚠️ Default Russian reference text detected. Clearing it to allow automatic transcription of reference audio.")
             ref_text = ""
             
        results = self.node.create(
            sample_audio=sample_audio,
            sample_text=ref_text, # Corrected mapping
            speech=text,
            seed=settings.get("seed", 42),
            model=settings.get("model", "F5v1"),
            vocoder=settings.get("vocoder", "vocos"),
            speed=settings.get("speed", 1.0),
            model_type=settings.get("model_type", "F5TTS_Base")
        )
        
        audio_dict = results[0]
        waveform = audio_dict["waveform"] # (1, channels, samples)
        sample_rate = audio_dict["sample_rate"]
        
        # Convert to 16-bit PCM for compatibility with pydub/wav
        waveform_int16 = (waveform.squeeze(0).clamp(-1, 1) * 32767).to(torch.int16)
        torchaudio.save(output_path, waveform_int16, sample_rate)
        return output_path

class QwenTTSAdapter(BaseTTSAdapter):
    def __init__(self):
        try:
            from qwen_tts import Qwen3TTSModel
            self.model_wrapper_class = Qwen3TTSModel
        except ImportError:
            raise ImportError("QwenTTS requires qwen-tts package. Please install: pip install qwen-tts")
        
        self.model = None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"

    def _load_model(self, model_name):
        if self.model is None:
            import folder_paths
            # Try to find the model in the qwen folder
            model_path = folder_paths.get_full_path("qwen", model_name)
            if not os.path.exists(model_path):
                qwen_dirs = folder_paths.get_folder_paths("qwen")
                for d in qwen_dirs:
                    full = os.path.join(d, model_name)
                    if os.path.exists(full):
                        model_path = full
                        break
            
            load_path = model_path if os.path.exists(model_path) else model_name
            print(f"Loading Qwen-TTS from {load_path}...")
            
            # Use the official wrapper for loading
            self.model = self.model_wrapper_class.from_pretrained(
                load_path,
                device_map=self.device,
                dtype=torch.bfloat16 if "cuda" in self.device else torch.float32
            )

    def synthesize(self, text: str, speaker_ref_path: str, output_path: str, settings: Dict[str, Any]):
        model_name = settings.get("model", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
        self._load_model(model_name)
        
        # Determine mode
        manual_ref = settings.get("manual_speaker_ref")
        is_custom_voice_model = "CustomVoice" in model_name
        
        target_lang = settings.get("target_language", "Auto")
        # Capitalize language name for Qwen (e.g., 'Russian')
        if target_lang and target_lang != "Auto":
            lang_map = {"ru": "Russian", "en": "English", "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese", "it": "Italian"}
            target_lang = lang_map.get(target_lang.lower(), target_lang.capitalize())

        if is_custom_voice_model:
            if manual_ref:
                print(f"Warning: Model {model_name} does not support cloning. Using custom voice '{settings.get('speaker', 'Serena')}' instead.")
            
            wavs, sr = self.model.generate_custom_voice(
                text=text,
                language=target_lang,
                speaker=settings.get("speaker", "Serena"),
                instruct=settings.get("instruct", "")
            )
        else:
            # Assume Base model support cloning
            wavs, sr = self.model.generate_voice_clone(
                text=text,
                language=target_lang,
                ref_audio=speaker_ref_path,
                x_vector_only_mode=True 
            )
            
        # Convert to 16-bit PCM
        import numpy as np
        wav_norm = np.clip(wavs[0], -1, 1)
        wav_int16 = (wav_norm * 32767).astype(np.int16)
        
        import soundfile as sf
        sf.write(output_path, wav_int16, sr)
        return output_path

def get_tts_adapter(engine_name: str):
    engine_name = engine_name.lower().replace("-", "").replace("_", "")
    if engine_name == "vibevoice":
        return VibeVoiceAdapter()
    elif engine_name == "f5tts":
        return F5TTSAdapter()
    elif engine_name == "qwen":
        return QwenTTSAdapter()
    else:
        raise ValueError(f"Unknown TTS engine: {engine_name}")
