"""Шаг 3. «Роутер задач» — ориентация запроса к конкретному воркфлоу.

Связка (порядок внутри ``route``):

1) **Интент-классификация**: каждый ``Workflow`` из :class:`WorkflowRegistry`
   получает счёт по своим триггерам/интентам/id-бонусам.
2) **Решение** (порог + gap между лучшим и следующим кандидатом):
    - ``route``      → однозначно выбранная задача;
    - ``ambiguous``  → два сопоставимых кейса, уточнение у оператора;
    - ``no_match``   → запрос вне каталога.
3) **Контекст из БЗ**: по ``kb_sections`` + ``settings_refs`` выбранного кейса
   собираем ссылки/снимки — результат кладётся в ``refs`` (аудит).
4) **Заполнение параметров** в порядке приоритета:
       provided > setting из БЗ (по SettingRef) > статический default.
   Типизированное приведение + enum-валидация. Ошибки не роняют роутинг —
   они фиксируются в ``param_sources["<name>"] = "invalid:<msg>"``.
5) **План**: словарь вида ``{workflow, params, param_sources, refs, decision, ...}``.
"""

from __future__ import annotations

import os as _os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

# Windows console: ensure safe stdout.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:  # pragma: no cover - defensive only
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — не трогаем stdout при ошибке
        pass

# Зависимые модули (шаги 1 и 2) из той же папки.
_ROOT = _os.path.dirname(_os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from workflow_registry import Workflow, WorkflowRegistry  # noqa: E402
from knowledge_base import KnowledgeBase, KBSection  # noqa: E402

__all__ = [
    "TaskRouter",
    "RouteDecision",
    "Candidate",
    "RoutingResult",
]


# --------------------------------------------------------------------------- #
# Типы данных                                                                  #
# --------------------------------------------------------------------------- #
class RouteDecision(str):
    """Эндюмератор решения роутинга (стing-совместимый)."""

    ROUTE = "route"          # выбран один конкретный кейс
    AMBIGUOUS = "ambiguous"  # несколько сопоставимых — нужно уточнение
    NO_MATCH = "no_match"    # запрос не покрыт ни одним кейсом


@dataclass(frozen=True)
class Candidate:
    """Одна потенциальная кандидатура (workflow + score)."""

    workflow_id: str
    name: str
    score: float
    reason: Optional[str] = None


@dataclass
class RoutingResult:
    """Структурированный результат работы TaskRouter."""

    decision: str
    workflow_id: Optional[str] = None
    confidence: float = 0.0
    candidates: List[Candidate] = field(default_factory=list)
    params: Dict[str, Any] = field(default_factory=dict)
    param_sources: Dict[str, str] = field(default_factory=dict)
    refs: List[Dict[str, Any]] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "workflow": self.workflow_id,
            "confidence": self.confidence,
            "candidates": [
                {
                    "workflow_id": c.workflow_id,
                    "name": c.name,
                    "score": round(c.score, 3),
                    "reason": c.reason or "",
                }
                for c in self.candidates
            ],
            "params": self.params,
            "param_sources": self.param_sources,
            "refs": self.refs,
            "message": self.message,
        }

    def __str__(self) -> str:  # pragma: no cover — debug helper
        import json as _json
        return _json.dumps(self.to_dict(), ensure_ascii=False, indent=2, default=str)


# --------------------------------------------------------------------------- #
# Пороги (легко подкрутить на свой каталог)                                    #
# --------------------------------------------------------------------------- #
ROUTE_MIN_SCORE = 8.0         # минимально «полезный» score для route
ROUTE_GAP = 4.0               # требуемый отрыв лучшего от второго
FULL_TRIGGER_WEIGHT = 14.0    # полный hit фразы-триггера — сильный сигнал
PARTIAL_TRIGGER_WEIGHT = 5.0  # частичное покрытие — средний
INTENT_WEIGHT = 3.0           # совпавший tag из intents
IDENT_TOKEN_CAP = 2.5         # id/name бонус (мягко, чтобы не «поимка по ядру»)
NOISE_PENALTY = 18.0          # штраф за вообще нет совпадений — отбрасываем мусор
MIN_CANDIDATE_SCORE = 3.0     # ниже этого — кандидат в список не попадает


# --------------------------------------------------------------------------- #
# Роутер                                                                       #
# --------------------------------------------------------------------------- #
class TaskRouter:
    """«Мозг» ориентации: phrase → (workflow, params, refs).

    :param registry: реестр воркфлоу (шаг 1) — обязателен.
    :param kb:       база знаний (шаг 2) — опционально; без неё degraded-режим
                     (refs пустые, дефолты только из статических полей).
    """

    def __init__(self, registry: WorkflowRegistry, kb: Optional[KnowledgeBase] = None):
        self.registry = registry
        self.kb = kb

    # ===================================================================== #
    # Публичное API                                                          #
    # ===================================================================== #
    def route(
        self,
        user_text: str,
        context: Optional[Mapping[str, Any]] = None,
    ) -> RoutingResult:
        """Классификация интента + план параметров.

        :param user_text: естественная фраза пользователя (RU/EN).
        :param context:   уже извлечённые поля вида ``{"keys": ["user:1"]}`` и т.п.
        """
        provided = dict(context or {})

        # 1) score по всем воркфлоу реестра → отсортированные кандидаты.
        candidates = self._score_all(user_text)

        if not candidates:
            return RoutingResult(
                decision=RouteDecision.NO_MATCH,
                workflow_id=None,
                confidence=0.0,
                candidates=[],
                params={},
                param_sources={},
                refs=self.list_registry_triggers_snippet()[:3],
                message=(
                    "Понимаю запрос как «вне каталога кейсов кеша». Уточните формулировку: "
                    + "; ".join(self._sample_triggers())
                    if self._sample_triggers()
                    else "каталог пуст"
                ),
            )

        # 2) ROUTE / AMBIGUOUS.
        best, second = candidates[0], (candidates[1] if len(candidates) > 1 else None)
        gap = best.score - (second.score if second else 0.0)

        decision = RouteDecision.ROUTE if self._route_best(candidates, best, second) \
                             else RouteDecision.AMBIGUOUS

        wf: Optional[Workflow] = None
        try:
            wf = self.registry.get(best.workflow_id)
        except KeyError:
            wf = None  # defensive — id не может быть неизвестен (мы сами его сгенерировали)

        # 3) Параметры по приоритету user > setting из БЗ > default.
        params: Dict[str, Any] = {}
        sources: Dict[str, str] = {}
        refs_settings: List[Dict[str, Any]] = []
        if wf is not None:
            params, sources, refs_settings = self._fill_params(wf, provided)

        # 4) Ссылки из БЗ (секции + настройки), использованные этим воркфлоу.
        kb_refs = self._kb_refs_for(wf)

        refs: List[Dict[str, Any]] = [dict(r) for r in list(refs_settings) + list(kb_refs)]
        # дедуп по («kind», «id») сохраняя порядок первого вхождения.
        seen = set()
        deduped_refs: List[Dict[str, Any]] = []
        for entry in refs:
            k2 = (entry.get("kind"), str(entry.get("id", "")))
            if k2 in seen:
                continue
            seen.add(k2)
            deduped_refs.append(entry)

        confidence = self._confidence(best, candidates)

        # 5) Сообщение.
        message = self._message(decision, best, second)

        return RoutingResult(
            decision=decision,
            workflow_id=(wf.id if wf else None),
            confidence=confidence,
            candidates=list(candidates),
            params=params,
            param_sources=sources,
            refs=deduped_refs,
            message=message,
        )

    # ===================================================================== #
    # Scoring                                                                #
    # ===================================================================== #
    def _score_all(self, user_text: str) -> List[Candidate]:
        text_low = (user_text or "").lower()
        words: Set[str] = set(_tokenize_ru_en(text_low))

        out: List[Candidate] = []
        for wf in self.registry.list():
            score, reason = self._score_one(wf, words, text_low)
            if score < MIN_CANDIDATE_SCORE:
                continue
            out.append(Candidate(workflow_id=wf.id, name=wf.name,
                                 score=round(score, 3), reason=reason or None))

        out.sort(key=lambda c: (-c.score, c.workflow_id))
        return out

    def _score_one(self, wf: Workflow, words: Set[str], text_low: str) -> Tuple[float, str]:
        reasons: List[str] = []
        score = 0.0

        # (a) Триггеры-фразы: полный hit — сильнее; частичное покрытие — мягче.
        full_hits = 0
        partial_score = 0.0
        for trig in wf.triggers or ():
            t_words = set(_tokenize_ru_en(trig))
            if not t_words:
                continue
            covered = [w for w in words if w in t_words]
            if len(covered) == len(t_words):
                full_hits += 1
            elif len(covered) > 0:
                partial_score += (len(covered) / len(t_words)) * PARTIAL_TRIGGER_WEIGHT

        if full_hits:
            score += FULL_TRIGGER_WEIGHT + (full_hits - 1) * 4.0  # бонус за второй+ полноты
            reasons.append(f"full-trigger×{full_hits}")
        if partial_score > 0:
            score += partial_score
            reasons.append(f"partial~{round(partial_score, 1)}")

        # (b) Интенты-теги — слабый сигнал; матчим ТОЛЬКО whole-token из тега,
        #     чтобы общие слова ("cache", "hits") не «подняли» чужие кейсы.
        matched_intent = 0
        for tag in wf.intents or ():
            pieces = {p.strip() for p in re.split(r"[.\-_]", tag.lower()) if len(p.strip()) >= 2}
            hits_tag = [w for w in words if any(p == w for p in pieces)]
            if hits_tag:
                matched_intent += INTENT_WEIGHT * min(1, len(hits_tag)) * min(1.0, 1.5)
        if matched_intent > 0:
            score += matched_intent
            reasons.append("intent")

        # (c) id/name tokens — небольшой бонус на «поимку по ядру» (мягко!).
        ident_tokens = self._ident_tokens(wf)
        hits_ident = [w for w in words if w in ident_tokens]
        if hits_ident:
            score += min(IDENT_TOKEN_CAP * len(hits_ident), IDENT_TOKEN_CAP * 1.5)

        # (d) Штраф за «мусорные» candidates — убираем тех, кто вообще не покрывает запрос.
        if full_hits == 0 and partial_score < PARTIAL_TRIGGER_WEIGHT * 0.30:
            score -= NOISE_PENALTY

        return max(0.0, score), ", ".join(reasons) or "generic"

    # ===================================================================== #
    # Параметры                                                              #
    # ===================================================================== #
    def _fill_params(
        self, wf: Workflow, provided: Mapping[str, Any]
    ) -> Tuple[Dict[str, Any], Dict[str, str], List[Dict[str, Any]]]:
        """Взять user-контекст → setting из БЗ → static default; типировать + валидировать."""

        params: Dict[str, Any] = {}
        sources: Dict[str, str] = {}
        refs_settings: List[Dict[str, Any]] = []

        for p in wf.params or ():
            # (1) user-provided.
            if p.name in provided and provided.get(p.name) is not None:
                raw, src_kind = provided[p.name], "user"
            else:
                # (2) setting из БЗ через SettingRef → параметр.
                ref = wf.setting_ref_for(p.name)
                value_kb = _lookup_setting(ref, self.kb) if (ref is not None and self.kb is not None) else None
                if value_kb is not None:
                    raw, src_kind = value_kb, f"kb_setting:{ref.setting_id}"
                    
                    # фиксируем ссылку для аудита.
                    entry = {
                        "kind": "setting",
                        "id": ref.setting_id,
                        "param_name": p.name,
                        "value": value_kb,
                    }
                    if entry not in refs_settings:
                        refs_settings.append(entry)
                elif p.default is not None:
                    raw, src_kind = p.default, "default"
                else:
                    # (3) ни один источник не дал.
                    raw, src_kind = None, "missing"

            # типизация + enum-валидация; ошибки — без роняния роутинга.
            try:
                coerced = p.coerce(raw) if raw is not None else (p.default if p.default is not None else None)
            except ValueError as exc:
                params[p.name] = None
                sources[p.name] = f"invalid:{exc}"
                continue
            except Exception as exc:  # pragma: no cover — defensive only
                params[p.name] = None
                sources[p.name] = f"unhandled:{exc.__class__.__name__}"
                continue

            # если raw был None, а default не-None и источник user/missing — добираем default.
            if coerced is None and p.default is not None:
                coerced = p.default
                src_kind = "default"

            params[p.name] = coerced

            # нормализуем label источника для читабельном param_sources.
            if src_kind in ("user", "missing"):
                sources[p.name] = src_kind
            elif src_kind and src_kind.startswith("kb_setting:"):
                sources[p.name] = src_kind
            else:
                sources[p.name] = (src_kind or "default")

        # завершающий прогон: required-поля без значения помечаем missing.
        for p in wf.params or ():
            if p.required and (p.name not in params or params.get(p.name) is None):
                sources.setdefault(p.name, "missing")

        return params, sources, refs_settings


    # ===================================================================== #
    # KB-ссылки                                                              #
    # ===================================================================== #
    def _kb_refs_for(self, wf: Optional[Workflow]) -> List[Dict[str, Any]]:
        """Собрать ссылки на разделы БЗ по ``wf.kb_sections`` (платно? нет — дёшево)."""
        if self.kb is None or wf is None:
            return []

        refs: List[Dict[str, Any]] = []
        ordered_keys = list(wf.kb_sections) + [wf.id]  # в конец добавим «себя» как hint-секцию.
        for slug in ordered_keys:
            key = (slug or "").strip()
            if not key:
                continue
            entry_hint = False
            try:
                sec = self.kb.get(key)
            except KeyError:
                sec = None
                entry_hint = True

            if sec is not None and isinstance(sec, KBSection):
                snippet = sec.text
                if len(snippet) > 240:
                    snippet = snippet[:240].rstrip() + " …"
                entry = {
                    "kind": "kb_section",
                    "id": sec.key,
                    "heading": sec.heading,
                    "doc": sec.doc,
                    "snippet": snippet,
                }
            else:
                entry = {
                    "kind": "kb_hint",
                    "id": key,
                    "note": "not found in kb. Available keys (sample): "
                            + ", ".join(self.kb.list_section_keys()[:8])
                            if self.kb is not None else "",
                }

            # дедуп (первый встреченный побеждает).
            exists = any(r.get("id") == entry["id"] and r.get("kind") == entry.get("kind") for r in refs)
            if not exists:
                refs.append(entry)

        return refs

    # ===================================================================== #
    # Helpers                                                                #
    # ===================================================================== #
    def _route_best(self, candidates: Sequence[Candidate], best: Candidate,
                    second: Optional[Candidate]) -> bool:
        if best.score < ROUTE_MIN_SCORE:
            return False
        if second is None:
            return True
        gap = best.score - second.score
        return gap >= ROUTE_GAP

    def _confidence(self, best: Candidate, candidates: Sequence[Candidate]) -> float:
        total = max(1e-6, sum(c.score for c in candidates))
        base = best.score / total
        # бонус за «разрыв» между лучшим и следующим (уверенный router → выше confidence).
        if len(candidates) >= 2:
            second_score = max((c.score for c in candidates[1:]), default=0.0)
            gap = best.score - second_score
            base += min(0.25, max(0.0, (gap - ROUTE_GAP) / (ROUTE_GAP * 4)))
        return round(min(1.0, max(0.0, base)), 3)

    def _ident_tokens(self, wf: Workflow) -> Set[str]:
        out = set()
        for field_name in ("id", "name"):
            value = getattr(wf, field_name, "") or ""
            parts = [w for w in re.split(r"[^a-zа-яё0-9]+", value.lower()) if len(w) >= 3]
            out.update(parts)
        return out

    def _message(self, decision: str, best: Candidate, second: Optional[Candidate]) -> str:
        if decision == RouteDecision.ROUTE:
            return f"Выбран воркфлоу «{best.name}» (id={best.workflow_id}). Параметры и refs зафиксированы в RoutingResult."
        if decision == RouteDecision.AMBIGUOUS:
            others = [c for c in ((best,) + ((second,) if second else ()))]
            snippet = "; ".join(f"{c.name} ({c.score})" for c in others[:3])
            return f"Пока распознал несколько сопоставимых кейсов: {snippet}. Уточните, какой хотите запустить."
        sample = self._sample_triggers()
        if not sample:
            return "Каталог воркфлоу пуст."
        return (
            "Точечно по пока — не уверен, к кому отнести запрос. Примеры триггеров в каталоге: "
            + "; ".join(sample[:5]) + "."
        )

    def _sample_triggers(self) -> List[str]:
        out = []
        for w in self.registry.list():
            if w.triggers and w.triggers[0] not in out:
                out.append(w.triggers[0])
            if len(out) >= 8:
                break
        return out

    def list_registry_triggers_snippet(self) -> List[Dict[str, Any]]:
        """Короткие «подсказки» из реестра для no_match-ветки (аудит UI)."""
        return [
            {
                "kind": "registry_hint",
                "id": w.id,
                "name": w.name,
                "triggers": list(w.triggers)[:3],
            }
            for w in self.registry.list()[:3]
        ]


# --------------------------------------------------------------------------- #
# Токенизация RU/EN                                                            #
# --------------------------------------------------------------------------- #
_TOKEN_RU_EN_RE = re.compile(r"[a-zA-Z0-9а-яёА-ЯЁ]+")


def _tokenize_ru_en(text: str) -> List[str]:
    """Лечкая токенизr — small enough для scoring'а, без лемматизации."""
    return [t.lower() for t in _TOKEN_RU_EN_RE.findall(text or "")]


# --------------------------------------------------------------------------- #
# Чтение настроек из БЗ (общий помощник)                                       #
# --------------------------------------------------------------------------- #
def _lookup_setting(setting_ref: Any, kb: Optional[KnowledgeBase]) -> Optional[Any]:
    """Читает настройку по ref-объекту через KB; если нет — None."""
    if setting_ref is None or kb is None:
        return None
    sid = getattr(setting_ref, "setting_id", None)
    if not sid:
        return None
    hit = kb.get_setting(sid)  # type: ignore[arg-type]
    if not isinstance(hit, dict):
        return None
    v = hit.get("value")
    # Если значение — строка (например, 'all') и мы знаем, что поле enum/строка — возвращаем как есть.
    # Это не валидатор: роутер сам типизирует в WorkflowParam.coerce().
    return v


# --------------------------------------------------------------------------- #
# Ленивый глобальный инстанс                                                   #
# --------------------------------------------------------------------------- #
_default_router: Optional[TaskRouter] = None


def default_router() -> TaskRouter:
    """Собрать роутер поверх встроенных шагов 1-2 (лениво, единожды)."""
    global _default_router
    if _default_router is None:
        # импорт через локальный модуль; KnowledgeBase берётся из корневого knowledge/
        from workflow_registry import default_registry as _reg
        kb_instance: Optional[KnowledgeBase] = None
        try:
            kb_instance = KnowledgeBase(root=None)
        except Exception:  # noqa: BLE001 — работаем в degraded-режиме, без падений.
            kb_instance = None
        _default_router = TaskRouter(registry=_reg(), kb=kb_instance)
    return _default_router


__all__ += ["_tokenize_ru_en", "_lookup_setting", "default_router"]
