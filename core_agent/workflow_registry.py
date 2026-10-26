"""Шаг 1. Реестр воркфлоу — «костяк» кеш-агента.

Единый источник истины для всей связки:
  * агент сейчас использует его для роутинга задач;
  * UI позже рендерит каждый ``Workflow`` в кнопку со своей формой
    (см. :meth:`Workflow.to_button_schema`) — бизнес-логику трогать не придётся.

Модуль независимый (только stdlib). Ссылки на разделы БЗ хранятся строкой-slug,
чтобы реестр не тянул за собой инстанс ``KnowledgeBase``. Разрешение этих
ключей делает :mod:`task_router`.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Union

__all__ = [
    "ParamType",
    "WorkflowParam",
    "SettingRef",
    "Workflow",
    "WorkflowRegistry",
    "build_default_registry",
]


# --------------------------------------------------------------------------- #
# Типы входных параметров                                                      #
# --------------------------------------------------------------------------- #
class ParamType(enum.Enum):
    """Базовые типы полей формы с приведением значений (coerce)."""

    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    LIST = "list"
    ENUM = "enum"  # допустимые значения — в ``WorkflowParam.options``

    def coerce(self, value: Any) -> Any:
        """Привести произвольное значение (str / str-подобое) к типу поля."""
        if value is None:
            return None
        t = self.value

        if t == "bool":
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("1", "true", "yes", "y", "да", "on"):
                return True
            if s in ("0", "false", "no", "n", "нет", "off"):
                return False
            raise ValueError(f"Ожидалось bool, получено: {value!r}")

        if t == "int":
            try:
                return int(str(value).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Ожидалось int, получено: {value!r}") from exc

        if t == "float":
            try:
                return float(str(value).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Ожидалось float, получено: {value!r}") from exc

        if t == "list":
            if isinstance(value, (list, tuple)):
                return [str(x).strip() for x in value if str(x).strip()]
            parts = re.split(r"[,;]\s*", str(value))
            return [p.strip() for p in parts if p.strip()]

        # STRING / ENUM — возвращаем строку.
        return str(value).strip()

    def ui_type(self) -> str:
        """HTML-совместимый тип для рендера формы кнопки."""
        return {
            "string": "text",
            "int": "number",
            "float": "number",
            "bool": "checkbox",
            "list": "multi",
            "enum": "select",
        }[self.value]


# --------------------------------------------------------------------------- #
# Поля формы / ссылки на настройки                                             #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class WorkflowParam:
    """Одно входное поле воркфлоу (и, впоследствии, элемент формы кнопки)."""

    name: str
    type: ParamType = ParamType.STRING
    description: str = ""
    required: bool = True
    default: Any = None
    options: Tuple[str, ...] = ()  # допустимые значения для ``ParamType.ENUM``

    def coerce(self, value: Any) -> Any:
        """Приведение + проверка перечисления."""
        out = self.type.coerce(value)
        if self.type is ParamType.ENUM and self.options:
            if out is not None and str(out).strip() not in self.options:
                raise ValueError(
                    f"Параметр {self.name!r}: допустимые значения "
                    f"{list(self.options)}, получено {out!r}"
                )
        return out

    def to_schema(self) -> Dict[str, Any]:
        """Схема для генерации UI-формы (каждая запись = кнопка)."""
        return {
            "name": self.name,
            "label": self.description or self.name,
            "type": self.type.ui_type(),
            "required": self.required,
            "default": self.default,
            "options": list(self.options),
            "description": self.description,
        }


@dataclass(frozen=True)
class SettingRef:
    """Ссылка параметра на настройку из БЗ (связка шаг 1 ↔ шаг 2).

    :param setting_id:   ключ настройки в БЗ (напр. ``cache.default_ttl_seconds``);
    :param param_name:   название поля, которое привязано к этой настройке;
    :param description:  пояснение/заметка по настройке.
    """

    setting_id: str
    param_name: Optional[str] = None
    description: str = ""


# --------------------------------------------------------------------------- #
# Воркфлоу                                                                     #
# --------------------------------------------------------------------------- #
@dataclass
class Workflow:
    """Полная дефиниция одного кейса кеша.

    ``triggers``       — фразы-триггеры (RU/EN), по которым роутер определяет интент
    ``intents``        — канонические теги намерения (аналитика)
    ``params``         — шаблон входных параметров = будущее поле формы кнопки
    ``settings_refs``  — связанные с БЗ настройки (источник дефолтов)
    ``kb_sections``    — slug-идентификаторы разделов БЗ для данного кейса

    :param run: опциональный обработчик ``params -> result_dict``; если не задан,
        воркфлоу «плановый» (выполняет внешняя система).
    """

    id: str
    name: str
    description: str = ""
    category: str = "cache"
    triggers: Tuple[str, ...] = ()
    intents: Tuple[str, ...] = ()
    params: Tuple[WorkflowParam, ...] = ()
    settings_refs: Tuple[SettingRef, ...] = ()
    kb_sections: Tuple[str, ...] = ()
    run: Optional[Callable[[Mapping[str, Any]], Dict[str, Any]]] = None

    def get_param(self, name: str) -> Optional[WorkflowParam]:
        for p in self.params:
            if p.name == name:
                return p
        return None

    @property
    def param_names(self) -> List[str]:
        return [p.name for p in self.params]

    def setting_ref_for(self, param_name: str) -> Optional[SettingRef]:
        """Найти настройку БЗ, привязанную к входному полю."""
        for ref in self.settings_refs:
            if ref.param_name == param_name:
                return ref
        return None

    def defaults_map(self) -> Dict[str, Any]:
        return {p.name: p.default for p in self.params}

    # -- UI-рендер --------------------------------------------------------- #
    def to_button_schema(self) -> Dict[str, Any]:
        """Сгенерировать готовую «кнопку» для UI."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "params": [p.to_schema() for p in self.params],
            "settings_refs": [
                {"setting_id": r.setting_id, "param_name": r.param_name}
                for r in self.settings_refs
            ],
            "kb_sections": list(self.kb_sections),
        }

    # -- исполнение -------------------------------------------------------- #
    def execute(self, params: Mapping[str, Any]) -> Dict[str, Any]:
        """Выполнить воркфлоу через обработчик ``run`` (если задан)."""
        if self.run is None:
            raise RuntimeError(
                f"Воркфлоу {self.id!r} не имеет обработчика run() — декларативный кейс."
            )
        provided = {p.name: params.get(p.name, p.default) for p in self.params}
        result = self.run(provided) or {}
        return {"workflow": self.id, **result}


# --------------------------------------------------------------------------- #
# Реестр                                                                       #
# --------------------------------------------------------------------------- #
class WorkflowRegistry:
    """Контейнер воркфлоу с доступом по id и фильтрацией по категории."""

    def __init__(self) -> None:
        self._workflows: Dict[str, Workflow] = {}
        self._order: List[str] = []  # порядок регистрации (стабильный вывод)

    def register(self, wf: Workflow) -> "WorkflowRegistry":
        if wf.id in self._workflows:
            raise ValueError(f"Воркфлоу с id={wf.id!r} уже зарегистрирован")
        self._workflows[wf.id] = wf
        self._order.append(wf.id)
        return self

    def get(self, workflow_id: str) -> Workflow:
        try:
            return self._workflows[workflow_id]
        except KeyError as exc:
            raise KeyError(
                f"Воркфлоу {workflow_id!r} не найден. Известные: {self.all_ids()}"
            ) from exc

    def has(self, workflow_id: str) -> bool:
        return workflow_id in self._workflows

    def list(self, category: Optional[str] = None) -> List[Workflow]:
        items = [self._workflows[i] for i in self._order if i in self._workflows]
        if category is not None:
            items = [w for w in items if w.category == category]
        return items

    def all_ids(self) -> List[str]:
        return list(self._order)

    def __len__(self) -> int:
        return len(self._workflows)

    def __iter__(self):
        return iter(self.list())


# --------------------------------------------------------------------------- #
# Встроенные обработчики (симуляции для демо; в проде — реальные SDK)          #
# --------------------------------------------------------------------------- #
def _exec_flush(params: Mapping[str, Any]) -> Dict[str, Any]:
    scope = params.get("scope", "all")
    keys = [k for k in (params.get("target_keys") or []) if k]
    simulated = {
        "mode": "simulate",
        "scope": scope,
        "affected_keys": len(keys) or -1,  # -1 → «все ключи» при scope=all
        "keys_sampled": keys[:5],
        "confirmed": bool(params.get("confirm", False)),
    }
    if simulated["scope"] != "all" and not keys:
        return {
            **simulated,
            "warning": "Scope не 'all', но target_keys пуст — ничего не тронуто",
        }
    return {"status": "ok", **simulated}


def _exec_prewarm(params: Mapping[str, Any]) -> Dict[str, Any]:
    keys = [k for k in (params.get("keys") or []) if k]
    batch = int(params.get("batch_size") or 0)
    batches = (len(keys) + batch - 1) // batch if batch and keys else 0
    return {
        "status": "ok",
        "mode": "simulate",
        "source": params.get("source", "redis"),
        "loaded_keys": len(keys),
        "batches": batches,
        "ttl_seconds": params.get("ttl_seconds"),
        "keys_sampled": keys[:5],
    }


def _exec_hit_rate(params: Mapping[str, Any]) -> Dict[str, Any]:
    # демо-входные (в реальном — из Prometheus/Grafana):
    hits, misses = 72, 28
    window = int(params.get("window_minutes") or 30)
    total = hits + misses
    rate = round(hits / total * 100, 2) if total else 0.0

    if params.get("format", "table") == "json":
        return {
            "status": "ok",
            "window_minutes": window,
            "hits": hits,
            "misses": misses,
            "hit_rate_pct": rate,
            "filter_prefix": params.get("filter_prefix"),
        }

    lines = [
        f"Окно: {window} мин | Hits: {hits} | Miss: {misses}",
        f"Hit-rate: {rate}%{'  (prefix=' + str(params.get('filter_prefix')) + ')' if params.get('filter_prefix') else ''}",
    ]
    return {"status": "ok", "format": "table", "text": "\n".join(lines)}


def _exec_invalidate(params: Mapping[str, Any]) -> Dict[str, Any]:
    pattern = params.get("pattern") or ""
    depth = int(params.get("depth") or 1)
    return {
        "status": "ok",
        "mode": "simulate",
        "pattern": pattern,
        "tree_depth": depth,
        "invalidated": -1 if pattern else 0,
        "warning": None if pattern else "pattern пуст — операция пропущена",
    }


def _exec_cleanup(params: Mapping[str, Any]) -> Dict[str, Any]:
    dry = bool(params.get("dry_run", True))
    limit = int(params.get("max_entries") or 0)
    return {
        "status": "ok",
        "mode": "simulate",
        "dry_run": dry,
        "max_entries_scanned": limit,
        "evicted_expired": -1,
    }


def _exec_top_keys(params: Mapping[str, Any]) -> Dict[str, Any]:
    n = int(params.get("limit") or 0)
    fmt = params.get("format", "table")
    demo = [
        ("user:1:profile", 1843),
        ("product:99:feed", 1577),
        ("cart:anonymous:17", 912),
    ][: max(1, min(n or 10, 10))]

    if fmt == "json":
        return {"status": "ok", "top_keys": demo}
    lines = ["TopKeys (демо):"] + [f"  {k}: {c}" for k, c in demo]
    return {"status": "ok", "format": "table", "text": "\n".join(lines)}


# --------------------------------------------------------------------------- #
# Сборка реестра из встроенных кейсов                                          #
# --------------------------------------------------------------------------- #
def build_default_registry() -> WorkflowRegistry:
    """Развёртывание встроенного каталога кейсов кеш-агента (6 шт.)."""

    reg = WorkflowRegistry()

    reg.register(
        Workflow(
            id="flush-cache",
            name="Полный сброс кеша",
            description=(
                "Эвакуирует/очищает кэш по выбранному scope. Требует подтверждения "
                "при полном сбросе."
            ),
            category="maintenance",
            triggers=(
                "flush cache",
                "побей кеш",
                "очисти кеш",
                "скинь кеш полностью",
                "сбросить кэш",
                "выгрузить всё из кеша",
                "invalidate all",
            ),
            intents=("cache.flush", "maintenance.reset"),
            params=(
                WorkflowParam(
                    name="scope",
                    type=ParamType.ENUM,
                    required=True,
                    default="all",
                    options=("all", "key", "prefix"),
                    description="Область сброса: все ключи / конкретные / по префиксу.",
                ),
                WorkflowParam(
                    name="target_keys",
                    type=ParamType.LIST,
                    required=False,
                    default=None,
                    description="Список целевых ключей (обязателен при scope=key/prefix).",
                ),
                WorkflowParam(
                    name="confirm",
                    type=ParamType.BOOL,
                    required=True,
                    default=False,
                    description="Подтверждение рискованной операции сброса.",
                ),
            ),
            settings_refs=(SettingRef(
                setting_id="cache.flush_scope", param_name="scope"
            ),),
                # slug реальные разделы БЗ (knowledge/*.md); task_router их решает.
                kb_sections=("полный-сброс-кеша-flush", "scope-область-сброса"),
            run=_exec_flush,
        )
    )

    reg.register(
        Workflow(
            id="prewarm",
            name="Погревание кеша (prewarm)",
            description="Предзагружает «горячие» ключи из source в кэш с заданным TTL.",
            category="performance",
            triggers=(
                "prewarm cache",
                "прогреть кеш",
                "предзагрузить в кеш",
                "разогреть кеш по ключам",
                "погрей кеш",
                "prefill hot keys",
            ),
            intents=("cache.prewarm", "performance.warmup"),
            params=(
                WorkflowParam(
                    name="keys", type=ParamType.LIST, required=True,
                    description="Список горячих ключей для предзагрузки.",
                ),
                WorkflowParam(
                    name="ttl_seconds", type=ParamType.INT, required=False, default=None,
                    description="TTL (секунды) прогруженных записей. Если не указано — "
                                "берётся из БЗ `cache.default_ttl_seconds`.",
                ),
                WorkflowParam(
                    name="batch_size", type=ParamType.INT, required=False, default=512,
                    description="Размер батча при параллельной предзагрузке.",
                ),
                WorkflowParam(
                    name="source", type=ParamType.ENUM, required=False, default="redis",
                    options=("redis", "api", "file"),
                    description="Источник данных для предзагрузки.",
                ),
            ),
            settings_refs=(SettingRef(
                setting_id="cache.default_ttl_seconds", param_name="ttl_seconds"
            ),),
            kb_sections=("погревание-кеша-prewarm", "ttl-time-to-live"),
            run=_exec_prewarm,
        )
    )

    reg.register(
        Workflow(
            id="cache-hit-rate",
            name="Анализ hit-rate кеша",
            description="Считает hit/miss метрики за заданное окно по произвольному префиксу.",
            category="analytics",
            triggers=(
                "hit rate of cache",
                "hit-rate кеша",
                "какой hit-rate",
                "процент попадания в кеш",
                "разбор метрик кеша",
                "посчитай hit/miss",
            ),
            intents=("cache.hits", "analytics.metrics"),
            params=(
                WorkflowParam(
                    name="window_minutes", type=ParamType.INT, required=False, default=None,
                    description="Окно анализа в минутах. По-умолчанию — из БЗ "
                                "`cache.metric_window_minutes`.",
                ),
                WorkflowParam(
                    name="filter_prefix", type=ParamType.STRING, required=False, default=None,
                    description="Фильтр по префиксу ключей.",
                ),
                WorkflowParam(
                    name="format", type=ParamType.ENUM, required=False, default="table",
                    options=("table", "json"),
                    description="Формат вывода (таблица/JSON).",
                ),
            ),
            settings_refs=(SettingRef(
                setting_id="cache.metric_window_minutes", param_name="window_minutes"
            ),),
            kb_sections=("анализ-hit-rate-кеша", "hit-rate"),
            run=_exec_hit_rate,
        )
    )

    reg.register(
        Workflow(
            id="invalidate-pattern",
            name="Инвалидация по паттерну",
            description="Удаляет дерево ключей по glob-паттерну на заданную глубину.",
            category="maintenance",
            triggers=(
                "invalidate by pattern",
                "удалить по маске",
                "очисти ветку ключей",
                "снести keys tree by prefix pattern",
                "обнулить паттерн в кеш",
            ),
            intents=("cache.invalidate_pattern",),
            params=(
                WorkflowParam(
                    name="pattern", type=ParamType.STRING, required=True,
                    description="Паттерн/glob для поиска ключей.",
                ),
                WorkflowParam(
                    name="depth", type=ParamType.INT, required=False, default=1,
                    description="Глубина обхода дерева ключей от корня паттерна.",
                ),
            ),
            kb_sections=("инвалидация-по-паттерну",),
            run=_exec_invalidate,
        )
    )

    reg.register(
        Workflow(
            id="cleanup-expired",
            name="Очистка истёкших записей",
            description="Удаляет записи с протухшим TTL; поддерживается dry-run.",
            category="maintenance",
            triggers=(
                "clean expired entries in cache",
                "убери протухшие из кеша",
                "очисти просроченные ключи",
                "удали истёкшие записи кеша",
                "purge expired from cache",
            ),
            intents=("cache.cleanup_expired",),
            params=(
                WorkflowParam(
                    name="dry_run", type=ParamType.BOOL, required=False, default=True,
                    description="Dry-run: только посчитать объём, ничего не удалять.",
                ),
                WorkflowParam(
                    name="max_entries", type=ParamType.INT, required=False, default=None,
                    description="Максимум записей в одном проходе. Дефолт из БЗ "
                                "`cache.max_entries`.",
                ),
            ),
            settings_refs=(SettingRef(
                setting_id="cache.max_entries", param_name="max_entries"
            ),),
            kb_sections=("очистка-истёкших-cleanup-expired", "dry-run"),
            run=_exec_cleanup,
        )
    )

    reg.register(
        Workflow(
            id="top-keys-report",
            name="Топ ключей по доступам",
            description="Выдаёт топ N самых «горячих» ключей за окно с форматированием.",
            category="analytics",
            triggers=(
                "top keys of cache",
                "самые горячие ключи кеша",
                "топ ключей по hit-ам",
                "какие ключи бьют чаще всего",
            ),
            intents=("cache.top_keys", "analytics.report"),
            params=(
                WorkflowParam(
                    name="limit", type=ParamType.INT, required=False, default=None,
                    description="Сколько записей выдать в отчёте. Дефолт из БЗ "
                                "`cache.top_keys_limit`.",
                ),
                WorkflowParam(
                    name="format", type=ParamType.ENUM, required=False, default="table",
                    options=("table", "json"),
                    description="Формат вывода отчёта.",
                ),
            ),
            settings_refs=(SettingRef(
                setting_id="cache.top_keys_limit", param_name="limit"
            ),),
            kb_sections=("топ-ключей-top-keys-report", "hit-rate"),
            run=_exec_top_keys,
        )
    )

    return reg


# --------------------------------------------------------------------------- #
# Ленивый глобальный реестр                                                    #
# --------------------------------------------------------------------------- #
_DEFAULT_REGISTRY: Optional[WorkflowRegistry] = None


def default_registry() -> WorkflowRegistry:
    """Вернуть заранее инициализированный реестр встроенных кейсов (лениво-единственно)."""
    global _DEFAULT_REGISTRY
    if _DEFAULT_REGISTRY is None:
        _DEFAULT_REGISTRY = build_default_registry()  # type: ignore[assignment]
    return _DEFAULT_REGISTRY
