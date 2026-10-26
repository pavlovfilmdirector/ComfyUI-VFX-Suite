import os
import torch
import torchaudio
import argparse
import logging
import re
import json
from typing import List, Dict, Any
from pydub import AudioSegment
from tts_adapter import get_tts_adapter

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Narrator")

def transliterate_latin(text: str) -> str:
    """Фонетическая транслитерация латиницы в кириллицу (Apple -> аппле)"""
    chars = {
        'a': 'а', 'b': 'б', 'c': 'к', 'd': 'д', 'e': 'е', 'f': 'ф', 'g': 'г', 'h': 'х', 'i': 'и', 'j': 'дж', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о', 'p': 'п', 'q': 'кв', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'v': 'в', 'w': 'в', 'x': 'кс', 'y': 'й', 'z': 'з'
    }
    def replace(match):
        word = match.group(0).lower()
        return "".join(chars.get(c, c) for c in word)
    
    return re.sub(r'[a-zA-Z]+', replace, text)

def clean_text(text: str) -> str:
    """Агрессивная очистка: удаление скобок, замена дефисов, нижний регистр, сохранение '+'"""
    # Переводим в нижний регистр (F5-TTS ломается от заглавных)
    text = text.lower()
    # Удаляем скобки вообще
    text = re.sub(r'[\(\)\[\]\{\}]', ' ', text)
    # Заменяем дефисы и тире на пробелы
    text = re.sub(r'[\-\—\–]', ' ', text)
    # Оставляем ТОЛЬКО кириллицу, пробелы, стандартную пунктуацию и КЛЮЧЕВОЙ знак '+'
    text = re.sub(r'[^\u0400-\u04FF\s\.\!\?\,\+\:\;]', ' ', text)
    return " ".join(text.split())

def _fix_cyrillic_orthography(text: str) -> str:
    """Исправление латинских букв в кириллице и подстановка 'ё'"""
    import re
    # Замена латинских e/i на русские е/и
    text = re.sub(r"(?<=[\u0400-\u04FF])e(?=[\u0400-\u04FF])", "е", text)
    text = re.sub(r"(?<=[\u0400-\u04FF])i(?=[\u0400-\u04FF])", "и", text)
    # Здесь можно добавить расширенный список _COMMON_EYO_WORDS, если нужно
    return text

def split_into_segments(text: str, max_chars: int = 250) -> List[str]:
    """Разбиение текста на смысловые фрагменты"""
    # Сначала пробуем по точкам
    sentences = re.split(r'(?<=[.!?])\s+', text)
    segments = []
    current_seg = ""
    
    for sent in sentences:
        if len(current_seg) + len(sent) < max_chars:
            current_seg += " " + sent
        else:
            if current_seg:
                segments.append(current_seg.strip())
            current_seg = sent
            
    if current_seg:
        segments.append(current_seg.strip())
        
    return segments

def transcribe_reference(audio_path: str):
    """Эмуляция ноды 'Apply Whisper' со скриншота"""
    try:
        import whisper
        logger.info(f"Transcribing reference audio: {audio_path}...")
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, language="Russian")
        return result["text"].strip()
    except Exception as e:
        logger.warning(f"Whisper transcription failed: {e}. Proceeding without sample_text.")
        return ""

def extract_text_from_pdf(pdf_path: str) -> str:
    """Извлечение текста из PDF (цифровой слой или OCR сканов)"""
    import fitz  # PyMuPDF
    import numpy as np
    from PIL import Image
    
    logger.info(f"Opening PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    full_text = []
    
    # Инициализируем OCR только при необходимости
    reader = None

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text().strip()
        
        if len(text) > 50: # Если на странице есть текстовый слой
            logger.info(f"Page {page_num+1}: Text layer found")
            full_text.append(text)
        else: # Если текста мало (вероятно, скан/картинка)
            logger.info(f"Page {page_num+1}: Scanning image (OCR)...")
            if reader is None:
                import easyocr
                reader = easyocr.Reader(['ru', 'en'])
            
            # Рендерим страницу в картинку
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            
            # Распознаем
            result = reader.readtext(np.array(img), detail=0)
            full_text.append(" ".join(result))
            
    doc.close()
    return "\n\n".join(full_text)

def main():
    parser = argparse.ArgumentParser(description="Text Narration Tool (Screenshot Replica)")
    parser.add_argument("--text_file", required=True, help="Path to .txt file")
    parser.add_argument("--ref_audio", required=True, help="Reference audio (Demo_NST_01.mp3)")
    parser.add_argument("--output", default="output/narration_final.wav", help="Output path")
    parser.add_argument("--engine", default="f5tts", help="TTS Engine")
    
    parser.add_argument("--max_chars", type=int, default=35000, help="Maximum characters per TTS block (default: 35000)")
    
    args = parser.parse_args()
    
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    temp_dir = "temp_narration"
    os.makedirs(temp_dir, exist_ok=True)

    # 1. Чтение текста
    if args.text_file.lower().endswith(".pdf"):
        text = extract_text_from_pdf(args.text_file)
    else:
        with open(args.text_file, "r", encoding="utf-8") as f:
            text = f.read()
    
    if not text.strip():
        logger.error("No text found to process!")
        return

    # 2. Нормализация и транслитерация
    from tts_adapter import normalize_numbers
    text = transliterate_latin(text)
    text = normalize_numbers(text, lang='ru')
    text = _fix_cyrillic_orthography(text)

    # 3. RUAccent (как на ноде RUAccent Stress & E Fix)
    if args.engine.lower() in ["f5tts", "f5-tts"]:
        try:
            from ruaccent import RUAccent
            logger.info("Processing RUAccent (turbo3.1 + Dictionary)...")
            accentizer = RUAccent()
            accentizer.load(omograph_model_size='turbo3.1', use_dictionary=True)
            text = accentizer.process_all(text)
            logger.info(f"Text processed. Sample: {text[:100]}...")
        except Exception as e:
            logger.warning(f"RUAccent error: {e}")
    
    # 4. Транскрипция референса
    ref_text = transcribe_reference(args.ref_audio)

    # 5. Подготовка сегментов
    text = clean_text(text)
    segments = split_into_segments(text, max_chars=args.max_chars)
    logger.info(f"Total segments to process: {len(segments)} (Max block size: {args.max_chars} chars)")

    # 6. TTS Setup & Model Selection
    adapter = get_tts_adapter(args.engine)
    engine_name = args.engine.lower()
    
    selected_model = ""
    settings = {
        "speed": 1.0,
        "seed": 42,
        "target_language": "ru"
    }

    if "f5" in engine_name:
        selected_model = "model://Misha.pt"
        settings.update({
            "model": selected_model,
            "seed": 985,
            "vocoder": "auto",
            "model_type": "F5TTS_v1_Base",
            "speaker_ref_text": ref_text
        })
    elif "vibe" in engine_name:
        import folder_paths
        v_models = folder_paths.get_folder_paths("vibevoice")
        local_v = []
        if v_models:
            v_dir = v_models[0]
            if os.path.exists(v_dir):
                local_v = [f for f in os.listdir(v_dir) if os.path.isdir(os.path.join(v_dir, f)) and f not in ["loras", "tokenizer"]]
        
        selected_model = "VibeVoice-7B" if "VibeVoice-7B" in local_v else "VibeVoice-1.5B"
        settings.update({
            "model": selected_model,
            "seed": 42,
            "steps": 20,
            "cfg_scale": 1.3
        })
    elif "qwen" in engine_name:
        import folder_paths
        qwen_dirs = folder_paths.get_folder_paths("qwen")
        local_q = []
        if qwen_dirs:
            q_dir = qwen_dirs[0]
            if os.path.exists(q_dir):
                local_q = [f for f in os.listdir(q_dir) if os.path.isdir(os.path.join(q_dir, f))]
        
        # Priority: Base model for voice cloning
        if "Qwen3-TTS-12Hz-1.7B-Base" in local_q:
            selected_model = "Qwen3-TTS-12Hz-1.7B-Base"
        elif local_q:
            selected_model = local_q[0]
        else:
            selected_model = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
            
        settings.update({
            "model": selected_model
        })

    # 7. Синтез
    generated_files = []
    for i, seg in enumerate(segments):
        logger.info(f"[{i+1}/{len(segments)}] Synthesizing with {args.engine}...")
        seg_out = os.path.join(temp_dir, f"part_{i:03d}.wav")
        adapter.synthesize(seg, args.ref_audio, seg_out, settings)
        generated_files.append(seg_out)

    # 7. Финальная склейка
    if generated_files:
        logger.info("Merging into final audio...")
        combined = AudioSegment.empty()
        for f in generated_files:
            combined += AudioSegment.from_wav(f)
            combined += AudioSegment.silent(duration=400) # Пауза между блоками
        
        combined.export(args.output, format="wav")
        logger.info(f"SUCCESS! Narration saved to: {args.output}")

if __name__ == "__main__":
    main()
