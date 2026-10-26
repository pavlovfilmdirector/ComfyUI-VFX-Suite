import sys
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os
import json
import argparse
import re
import logging

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger("TextProcessor")

# Try to import RUAccent
RUAccent = None
try:
    from ruaccent import RUAccent
except ImportError:
    pass

try:
    from num2words import num2words
except ImportError:
    num2words = None

def emit_status(progress: int, status: str, details=None):
    msg = {"progress": progress, "status": status, "details": details}
    print(f"STATUS_UPDATE:{json.dumps(msg, ensure_ascii=False)}", flush=True)

# Regex-based fallback list for Ё fix (extracted from translate_segment_texts.py)
_COMMON_EYO_WORDS = {
    "моём","лёгкими","приобретёте","всё","своё","преобретёте","берёза", "берёзы", "берёзой", "берёзе", "берёзу", "берёз", "берёзами", "берёзах", "берёзка", "берёзонька",
    "берёзовый", "берёзовым", "берёзовыми", "берёзовом", "берёзовую", "берёзового", "берёзовом", "берёзовыми", "берёзов", "берёзовке",
    "ёлка", "ёлки", "ёлкой", "ёлке", "ёлку", "ёлок", "ёлками", "ёлках", "ёлочка", "ёлочке",
    "жёлтый", "жёлтого", "жёлтому", "лёд", "льда", "льду", "шёл", "шла", "шло", "шли", "шёпот", "плёнка"
    # ... abbreviated for the engine
}

def fix_cyrillic_orthography(text: str) -> str:
    text = re.sub(r"(?<=[\u0400-\u04FF])e(?=[\u0400-\u04FF])", "е", text)
    text = re.sub(r"(?<=[\u0400-\u04FF])i(?=[\u0400-\u04FF])", "и", text)

    def _replace_e_with_yo(word: str) -> str:
        if word.lower() in _COMMON_EYO_WORDS:
            return re.sub("е", "ё", word, count=1)
        return word

    tokens = re.split(r"(\W+)", text)
    tokens = [_replace_e_with_yo(tok) for tok in tokens]
    return "".join(tokens)

def convert_numerals_to_words(text: str, lang='ru') -> str:
    if not num2words:
        return text
        
    def replace_num(match):
        num_str = match.group(0)
        try:
            # Basic cardinal conversion
            return num2words(int(num_str), lang=lang)
        except Exception:
            return num_str

    # Find sequences of digits
    return re.sub(r'\b\d+\b', replace_num, text)

def process_text(config_path):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
            
        text = config.get("text", "")
        add_stress = config.get("add_stress", True)
        fix_ortho = config.get("fix_ortho", True)
        remove_punct = config.get("remove_punct", False)
        to_lowercase = config.get("to_lowercase", False)
        
        emit_status(10, "Starting text processing...")
        
        result = text
        
        if fix_ortho:
            emit_status(30, "Fixing orthography (Ё/Cyrillic)...")
            result = fix_cyrillic_orthography(result)
            
        # Convert numerals to words for Russian to ensure correct narration
        emit_status(40, "Converting numerals to Russian words...")
        result = convert_numerals_to_words(result, lang='ru')
            
        if remove_punct:
            emit_status(45, "Removing punctuation...")
            # Remove everything except alphanumeric and spaces
            result = re.sub(r'[^\w\s]', '', result)
            result = re.sub(r'\s+', ' ', result).strip()
            
        if add_stress:
            emit_status(60, "Adding stress marks (chunked)...")
            if RUAccent:
                accentizer = RUAccent()
                accentizer.load(omograph_model_size='turbo2')
                
                # Split text into segments to avoid DistilBERT limit (512 tokens)
                # 800 characters is a safe chunk size for context
                segments = []
                
                def split_to_safe_chunks(text, max_len=800):
                    """Recursively split text if it's too long."""
                    if len(text) <= max_len:
                        return [text]
                    
                    # Try splitting by space near the limit
                    split_pos = text.rfind(' ', 0, max_len)
                    if split_pos == -1:
                        split_pos = max_len # Force split if no space
                    
                    return [text[:split_pos]] + split_to_safe_chunks(text[split_pos:].strip(), max_len)

                raw_sentences = re.split(r'(?<=[.!?])\s+', result)
                current_chunk = ""
                
                for sentence in raw_sentences:
                    # If a single sentence is too long, we must split it hard
                    if len(sentence) > 800:
                        if current_chunk:
                            segments.append(current_chunk)
                            current_chunk = ""
                        segments.extend(split_to_safe_chunks(sentence))
                        continue

                    if len(current_chunk) + len(sentence) < 800:
                        current_chunk += (" " if current_chunk else "") + sentence
                    else:
                        if current_chunk:
                            segments.append(current_chunk)
                        current_chunk = sentence
                
                if current_chunk:
                    segments.append(current_chunk)

                processed_segments = []
                total_seg = len(segments)
                for i, seg in enumerate(segments):
                    if not seg.strip(): continue
                    emit_status(60 + int(35 * (i/total_seg)), f"Adding stress: part {i+1}/{total_seg}...")
                    try:
                        processed_segments.append(accentizer.process_all(seg))
                    except Exception as e:
                        logger.warning(f"⚠️ RUAccent failed on part {i+1}, skipping stress for this part: {e}")
                        processed_segments.append(seg)
                
                result = " ".join(processed_segments)
            else:
                logger.warning("RUAccent not installed. Skipping advanced stress marks.")
                # We could implement a very basic vowel-capitalization if needed, 
                # but usually + mark is what TTS engines like F5 expect.
                # RUAccent is the right way if available.
        
        if to_lowercase:
            emit_status(95, "Finalizing case conversion (lowercase)...")
            result = result.lower()

        emit_status(100, "✅ Processing complete", {"processed_text": result})
        
    except Exception as e:
        logger.error(f"❌ Error during text processing: {str(e)}")
        emit_status(100, "Error", {"error": str(e)})
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    process_text(args.config)
