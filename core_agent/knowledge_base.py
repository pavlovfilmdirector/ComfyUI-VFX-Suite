"""Шаг 2. База знаний («вечно-доступная шпаргалка» для агента).

Задачи модуля:
* собрать документы из ``knowledge/`` (.md / .txt + JSON с числовыми настройками);
* нарезать Markdown на секции по заголовкам;
* построить обратный индекс (лексемы × idf) для полнотекстового поиска;
* дать два канала доступа — ищет (:meth:`KnowledgeBase.query`) и точечный
  доступ (:meth:`KnowledgeBase.get`, :meth:`KnowledgeBase.get_setting`).

Модуль намеренно не знает про реестр или роутер — это отдельный «доменный» слой,
который легко переиспользовать в UI/CLI.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set, Union

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:  # pragma: no cover - defensive, Windows cp866 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — не падаем при ошибке
        pass

__all__ = ["KnowledgeBase", "KBSection", "KBHit"]


# --------------------------------------------------------------------------- #
# Константы                                                                    #
# --------------------------------------------------------------------------- #
DEFAULT_KNOWLEDGE_ROOT: Path = Path(__file__).resolve().parent / "knowledge"

_TOKEN_RE = re.compile(r"[a-zA-Z0-9а-яёА-ЯЁ]+")
_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")


def _tokenize(text: str) -> List[str]:
    """Лечкое токенизr (lowercase, буквы/цифры RU/EN)."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "")]


def _slugify(text: str) -> str:
    """Сделать id секции из заголовка. Не латинизируем — кириллица остаётся."""
    t = (text or "").strip().lower()
    # всё, что не буква/цифра → дефис; сжимаем подряд идущие
    t = re.sub(r"[^0-9a-zа-яё]+", "-", t)
    t = re.sub(r"-{2,}", "-", t).strip("-")
    return t or "section"


def _try_number(s: str) -> Union[int, float, str]:
    """Привести строку к int/float, если возможно; иначе вернуть строку."""
    s_trim = (s or "").strip()
    try:
        return int(s_trim, 10)
    except ValueError:
        pass
    try:
        f = float(s_trim)
        if f.is_integer():
            return int(f)
        return f
    except ValueError:
        return s


# --------------------------------------------------------------------------- #
# DTO                                                                          #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class KBSection:
    """Секция документа (заголовок + тело)."""

    key: str       # slug — стабильный ident для kb.get(key)
    doc: str       # имя файла относительно корня БЗ (для аудита)
    heading: str   # оригинальный заголовок markdown
    text: str      # тело секции (без заголовка)

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "doc": self.doc, "heading": self.heading, "text": self.text}


@dataclass(frozen=True)
class KBHit:
    """Результат одной заявки из полнотекстового поиска."""

    section: KBSection
    score: float


# --------------------------------------------------------------------------- #
# Основной класс                                                               #
# --------------------------------------------------------------------------- #
class KnowledgeBase:
    """Доменный слой БЗ.

    Использование::

        kb = KnowledgeBase("path/to/knowledge")
        kb.load()  # идемпотентно; при повторном вызове индексы пересобираются
        for hit in kb.query("hit rate"):
            print(hit.score, "->", hit.section.heading)
        sec = kb.get("flush-cache")
        v   = kb.get_setting("cache.default_ttl_seconds")
    """

    def __init__(self, root: Optional[Union[str, Path]] = None) -> None:
        self.root = Path(root) if root else DEFAULT_KNOWLEDGE_ROOT
        self._sections: Dict[str, KBSection] = {}
        self._section_tokens: Dict[str, Set[str]] = {}
        self._idf: Dict[str, float] = {}
        self._settings: Dict[str, Dict[str, Any]] = {}
        self._loaded: bool = False

    # -- загрузка ----------------------------------------------------------- #
    def load(self) -> "KnowledgeBase":
        """Загрузить + индексировать. Идемпотентно."""
        if not self.root.exists():
            raise FileNotFoundError(
                f"Каталог БЗ не найден: {self.root}. "
                "Создайте там *.md секции (и опционально JSON-настройки), либо укажите другой корень."
            )

        # сбрасываем индексы (идемпотентность)
        self._sections.clear()
        self._section_tokens.clear()
        self._idf.clear()
        self._settings.clear()

        # 1) JSON-настройки (если есть).
        for jf in sorted(self.root.glob("*.json")):
            self._merge_json_settings(jf)

        # 2) Markdown / Plaintext — секции по заголовкам + строки "key = value".
        files = list(self.root.rglob("*.md")) + list(self.root.rglob("*.txt"))
        for path in sorted(files, key=lambda p: str(p)):
            rel = path.relative_to(self.root).as_posix()
            try:
                raw = path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001 — скачем с пометкой, без поломки всего БЗ
                print(f"[KnowledgeBase] Пропущен {rel}: {exc}", file=sys.stderr)
                continue
            self._parse_document(rel=rel, body=raw)

        # 3) idf-вектор по всему корпусу секций.
        n = max(1, len(self._section_tokens))
        df: Dict[str, int] = {}
        for tokens in self._section_tokens.values():
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1
        for term, freq in df.items():
            # log-idf (классика); +1 в числителе/знаменателе — smoothing, чтобы не делить на 0.
            self._idf[term] = math.log((n + 1) / (freq + 1))

        self._loaded = True
        return self

    # -- парсинг документов ------------------------------------------------- #
    def _merge_json_settings(self, path: Path) -> None:
        """Развернуть JSON в виде ``cache.default_ttl_seconds`` → значение."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — некорректный JSON прощаемся с сообщением
            print(f"[KnowledgeBase] Не удалось прочитать {path.name}: {exc}", file=sys.stderr)
            return

        def walk(node: Any, prefix: str = "") -> None:
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        walk(v, prefix=f"{prefix}{k}.")
                    else:
                        self._settings[f"{prefix}{k}"] = {
                            "value": v, "note": "", "doc": path.name,
                        }
            elif isinstance(node, list):
                for i, item in enumerate(node, start=1):
                    walk(item, prefix=f"{prefix}{i}.")

        if isinstance(data, dict):
            walk(data)

    def _parse_document(self, rel: str, body: str) -> None:
        """Разрезать документ на секции; собрать строки с настройками ``id = value``."""

        # 1) Настройки (строка вида ``cache.x = 300 # note``).
        for line in body.splitlines():
            m = self._SETTING_RE.match(line.strip())
            if not m:
                continue
            sid, sval, snote = m.group("id"), m.group("value"), (m.group("note") or "").strip()
            self._settings[sid] = {"value": _try_number(sval), "note": snote, "doc": rel}

        # 2) Секции по заголовкам. «Буквирный» контент до первого ## превращаем в overview.
        lines = body.splitlines()
        current: Optional[str] = None
        buffer: List[str] = []
        preamble: List[str] = []

        def flush(key: Optional[str], buf: List[str]) -> None:
            body_text = "\n".join(buf).strip()
            if not body_text:
                return
            heading_final = (key or "overview").strip() or "overview"
            slug = _slugify(heading_final) if key else "overview"
            # уникальность: если slug уже занят другим doc'ом — суффикс по имени файла.
            if slug in self._sections and self._sections[slug].doc != rel:
                stem = Path(rel).stem
                base_slug, suffix_n = _slugify(stem) + "-2", 2
                candidate = f"{base_slug}{suffix_n}"
                while candidate in self._sections and self._sections[candidate].doc != rel:
                    suffix_n += 1
                    candidate = f"{base_slug}{suffix_n}"
                slug = candidate

            section = KBSection(
                key=slug, doc=rel, heading=heading_final, text=body_text
            )
            self._sections[slug] = section
            toks = _tokenize(body_text) + _tokenize(heading_final)
            self._section_tokens[slug] = set(toks)

        for line in lines:
            hm = _HEADING_RE.match(line)
            if hm:
                flush(current, buffer)
                current = hm.group("title").strip()
                buffer = []
            else:
                (buffer if current is not None else preamble).append(line)

        if preamble:
            flush(None, preamble)  # содержимое до первого ## → overview
        if current is not None or buffer:
            flush(current, buffer)

    _SETTING_RE = re.compile(
        r"^\s*(?P<id>[A-Za-z_][\w\-]*(?:[.-][\w\-]+)+)\s*=\s*"
        r"(?P<value>[^\s#]+)(?:\s*#\s*(?P<note>.*))?$"
    )

    # -- поиск ------------------------------------------------------------- #
    def query(
        self, text: str, top_k: int = 5, min_score: float = 0.25
    ) -> List[KBHit]:
        """Полнотекстовый поиск по корпусу БЗ (token × idf → суммарный score)."""
        self._ensure_loaded()
        qtokens = [t for t in _tokenize(text) if t not in _STOPWORDS] or _tokenize(text)

        scored: List[KBHit] = []
        for slug, sec in self._sections.items():
            sec_tokens = self._section_tokens.get(slug, set())
            if not sec_tokens:
                continue
            matched = {t for t in qtokens if t in sec_tokens}
            if len(matched) == 0 and not (self._slug_hit(slug, qtokens)):
                continue
            base = sum(self._idf.get(t, 0.5) for t in matched) or 0.0
            # бонус: совпало ≥2 токена — это «фразирование», сигнал чуть выше
            if len(matched) >= 2:
                base += 0.25 * (len(matched) - 1)
            # бонус: id секции содержит один из query-токенов — сильный match
            slug_hit = 1.0 if self._slug_hit(slug, qtokens) else 0.0
            total_score = base + slug_hit

            if total_score < min_score:
                continue
            scored.append(KBHit(section=sec, score=round(total_score, 4)))

        scored.sort(key=lambda h: (-h.score, h.section.key))
        return scored[: max(0, top_k)]

    def _slug_hit(self, slug: str, qtokens: List[str]) -> bool:
        """Совпал ли slug-составной секции с одним из query-токенов."""
        parts = {w for w in slug.split("-") if len(w) >= 3}
        return bool(parts & set(qtokens))

    # -- точечный доступ --------------------------------------------------- #
    def get(self, key: Union[str, KBSection]) -> KBSection:
        """Точечное получение по slug. Падает с понятным KeyError при miss."""
        self._ensure_loaded()
        if isinstance(key, KBSection):
            return key

        k = (key or "").strip().lower()
        # 1) прямой slug;
        if k in self._sections:
            return self._sections[k]
        # 2) по оригинальному заголовку через _slugify.
        slug_by_title = _slugify(k)
        if slug_by_title in self._sections:
            return self._sections[slug_by_title]

        # 3) частичное совпадение: содержимое запроса входит в key/heading, или наоборот.
        best: Optional[Any] = None
        for s in self._sections.values():
            if not k:
                continue
            hay_in_key = k in s.key or (s.heading.lower() and k in s.heading.lower())
            key_in_hay = s.heading.lower() and s.heading.lower() in k
            if hay_in_key or key_in_hay:
                ratio = min(len(k), len(s.key)) / max(1, len(k), len(s.key))
                cur_score = ratio + (0.35 if (s.heading and s.heading.lower() == k) else 0.0)
                if best is None or cur_score > best[0]:
                    best = (cur_score, s)

        if best is not None:
            return best[1]

        known_keys = ", ".join(sorted(self._sections.keys())[:8]) or "(пусто)"
        raise KeyError(f"Секция {k!r} не найдена. Доступные ключи: {known_keys}")

    def get_setting(self, setting_id: str) -> Optional[Dict[str, Any]]:
        """Чтение отдельной настройки (id → {value, note, doc})."""
        self._ensure_loaded()
        sid_norm = (setting_id or "").strip().lower().replace(" ", "")
        if not sid_norm:
            return None

        cand = sid_norm
        if cand in self._settings:
            return dict(self._settings[cand])

        # гибкий поиск: совпадает хвост id или полное под-строковое включение.
        candidates: List[tuple] = []  # (len, v) — берём самое длинное совпадение
        for k, v in self._settings.items():
            lk = (k or "").lower().replace(" ", "")
            if not lk:
                continue
            if sid_norm == lk or lk.startswith(sid_norm) or sid_norm.endswith(lk):
                candidates.append((len(lk), dict(v)))
        if candidates:
            candidates.sort(key=lambda x: -x[0])
            return candidates[0][1]
        return None

    # -- служебное --------------------------------------------------------- #
    def list_section_keys(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self._sections.keys())

    def list_setting_ids(self) -> List[str]:
        self._ensure_loaded()
        return sorted(self._settings.keys())

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            try:
                self.load()
            except FileNotFoundError:
                # degraded-режим: БЗ не найдена — продолжаем с пустыми индексами,
                # чтобы роутер мог работать только на статических дефолтах из реестра.
                self._loaded = True


# --------------------------------------------------------------------------- #
# Стоп-слова (RU+EN мелкие частицы)                                            #
# --------------------------------------------------------------------------- #
_STOPWORDS: Set[str] = {
    "и", "в", "на", "по", "из", "для", "чтобы", "этот", "эта", "это", "не", "как",
    "the", "a", "an", "of", "in", "on", "to", "and", "or", "is", "are", "with",
    "какие", "сделать", "написать", "проанализировать", "получить", "разобрать",
    "выдачi", "выдать", "посчитай",
}


# --------------------------------------------------------------------------- #
# Ленивый глобальный инстанс                                                   #
# --------------------------------------------------------------------------- #
_DEFAULT_KB: Optional[KnowledgeBase] = None


def default_kb() -> KnowledgeBase:
    """Вернуть общий для программы инстанс БЗ (лениво, единожды)."""
    global _DEFAULT_KB
    if _DEFAULT_KB is None:
        _DEFAULT_KB = KnowledgeBase(root=None)
    return _DEFAULT_KB  # type: ignore[return-value]
