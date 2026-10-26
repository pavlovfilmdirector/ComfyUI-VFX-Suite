"""Шаг 2. Консольное приложение кэш-агента (CLI).

Единая точка интерактива: `python main.py <команда> [аргументы]`.
Все команды работают поверх одного ядра — :class:`task_router.TaskRouter`
(собираемого лениво через :func:`task_router.default_router`) и используют:
  * шаг 1 (реестр)      → ``default_registry()`` из :mod:`workflow_registry`;
  * шаг 2 (база знаний) → ``default_kb()``     из :mod:`knowledge_base`.

Подкоманды:
    route       распознать интент по фразе → routing-решение + параметры + refs;
    workflows   напечатать каталог воркфлоу с параметрами/настройками/references;
    kb-search   полнотекстовый поиск по базе знаний.

Вывод: человекочитаемый (с ANSI-цветами, если stdout — tty) либо строгий JSON
(``--json`` на соответствующей подкоманде). Примеры запуска::

    python main.py route "выгрузи всё из кеша"
    python main.py workflows --category maintenance
    python main.py kb-search "hit rate" --top 3 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, List, Optional, Sequence

# --------------------------------------------------------------------------- #
# Безопасный вывод (Windows cp866 → UTF-8)                                     #
# --------------------------------------------------------------------------- #
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    try:  # pragma: no cover — defensive only
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — не трогаем stdout при ошибке
        pass

_IS_TTY = bool(getattr(sys.stdout, "isatty", lambda: False)())


def _color(text: str, code: int) -> str:
    """Обернуть текст в ANSI-256 цвет (безопасно, если не tty)."""
    if not _IS_TTY:
        return text
    return f"\x1b[38;5;{code}m{text}\x1b[0m"


# --------------------------------------------------------------------------- #
# Печать человекочитаемого результата роутинга                                 #
# --------------------------------------------------------------------------- #
_DECISION_COLOR = {
    "route": 46,       # зелёный — кейс выбран однозначно
    "ambiguous": 190,  # янтарный — уточнить у оператора
    "no_match": 197,   # красный — вне каталога
}


def _print_routing_human(result: Any) -> None:
    decision = str(getattr(result, "decision", "")).split(".")[-1] or "-"
    color = _DECISION_COLOR.get(decision, 248)

    print()
    print(f"  {_color('Решение:', 243)}  "
          f"{_color(decision.upper(), color)}"
          f"   (уверенность: {result.confidence:.3f})")

    if result.workflow_id:
        print(f"  {_color('Воркфлоу:', 243)}  {result.workflow_id}")

    # — Параметры с источником значения.
    params = dict(result.params or {})
    sources = dict(result.param_sources or {})
    if params:
        print(f"  {_color('Параметры:', 243)}")
        for name, val in params.items():
            src = sources.get(name, "-")
            if isinstance(src, str) and src.startswith("kb_setting:"):
                src_label = _color(src, 201)   # «из БЗ»
            elif src == "missing":
                src_label = _color(src, 249)   # не задано
            elif isinstance(src, str) and (src.startswith("invalid:")
                                           or src.startswith("unhandled:")):
                src_label = _color(src, 197)   # ошибка типизации
            else:
                src_label = _color(str(src), 248)
            print(f"    · {name} = {val!r}")
            print(f"        {_color('источник', 248)}: {src_label}")

    # — Кандидаты (виден распределённый score между кейсами).
    cands: List[Any] = list(result.candidates or [])
    if cands:
        print(f"  {_color('Кандидаты:', 243)}")
        for cand in cands[:5]:
            marker = "▶" if getattr(cand, "workflow_id", None) == result.workflow_id else "-"
            reason_str = getattr(cand, "reason", "") or ""
            suffix = f"   [{reason_str}]" if reason_str else ""
            print(f"    {marker} {cand.name:<30} ({cand.score:>7.2f}){suffix}")

    # — Ссылки/разделы БЗ (аудит).
    kb_refs = [r for r in (result.refs or []) if isinstance(r, dict) and r.get("kind")]
    if kb_refs:
        print(f"  {_color('Разделы БЗ (audit):', 243)}")
        for r in kb_refs:
            kind = str(r.get("kind"))
            rid = r.get("id", "?")
            heading = r.get("heading") or ""
            snippet_lines = [ln for ln in (r.get("snippet") or "").splitlines() if ln]
            first = snippet_lines[0].strip() if snippet_lines else ""
            extra = f" — {heading}" if heading else ""
            print(f"    · {kind:<10}  {_color(rid, 201)}{extra}")
            note = (r.get("note") or "")
            tag = "" if not note else "   " + str(r.get('kind',''))
            extra2 = f"  [{note[:60]}]" if note else ""
            if first:
                print(f"      {_color('» ' + first[:96], 248)}{_color(extra2, 253)}")

    if result.message:
        # — Финальное сообщение роутера (объяснение решения/примеры триггеров).
        print()
        for line in result.message.splitlines():
            print("  " + _color(line.rstrip()[:100], 250))


# --------------------------------------------------------------------------- #
# Команда route                                                                #
# --------------------------------------------------------------------------- #
def cmd_route(args: argparse.Namespace) -> int:
    """Распознать интент по фразе; вернуть воркфлоу + параметры + refs."""
    from task_router import default_router

    context: Optional[dict] = None
    if args.set:
        # Несколько --set "name=value"; каждое может содержать пары через запятую.
        pairs: dict[str, Any] = {}
        for item in args.set:
            piece = (item or "").strip()
            if not piece:
                continue
            sub_items = [p.strip() for p in piece.split(",")] if "," in piece else [piece]
            for si in sub_items:
                name, sep, val = si.partition("=")
                pairs[name.strip()] = val.strip() if (sep and val.strip()) else None
        context = pairs or None

    router = default_router()
    result = router.route(user_text=args.phrase, context=context)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    _print_routing_human(result)
    return 0


# --------------------------------------------------------------------------- #
# Команда workflows                                                            #
# --------------------------------------------------------------------------- #
def cmd_workflows(args: argparse.Namespace) -> int:
    """Каталог воркфлоу (с триггерами/параметрами/references)."""
    from workflow_registry import default_registry

    registry = default_registry()
    items = (registry.list(category=args.category)
             if args.category else list(registry.list()))

    if args.json:
        print(json.dumps([w.to_button_schema() for w in items], ensure_ascii=False, indent=2))
        return 0

    if not items:
        suffix = f" (в категории {args.category})." if args.category else "."
        print("Каталог воркфлоу пуст" + suffix)
        return 1

    cat_label = f"   [{args.category}]" if args.category else ""
    print()
    print(f"  {_color('Каталог воркфлоу', 81)} — всего {len(items)}{cat_label}")

    for wf in items:
        print()
        head = f"{wf.id}   ·   {wf.name}"
        print("  " + _color("─" * min(62, len(head) + 4), 237))
        print("  " + _color(head, 81))

        # — Описание.
        for para in (wf.description or "").splitlines():
            text = para.strip()
            while len(text) > 78:
                idx = text.rfind(" ", 0, 78)
                if idx <= 0:
                    idx = 78
                chunk, text = text[:idx], text[idx + 1:]
                print(f"      {_color(chunk.rstrip(), 249)}")
            if text.strip():
                print(f"      {_color(text.strip(), 249)}")

        # — Триггеры (топ-6).
        trig = list(wf.triggers or [])
        if trig:
            shown = ", ".join(_color(t[:38] + ("…" if len(t) > 38 else ""), 245) for t in trig[:6])
            more = f"   (+{len(trig) - 6})" if len(trig) > 6 else ""
            print(f"   {_color('триггеры:', 248)}  {shown}{more}")

        # — Параметры.
        params = list(wf.params or [])
        if params:
            print(f"   {_color('параметры:', 248)}")
            for p in params:
                flag = "*" if p.required else " "
                default_str = f"default={p.default!r}"
                enum_hint = ""
                if getattr(p, "options", None):
                    enum_hint = "   (enum: " + ", ".join(map(str, p.options)) + ")"
                print(f"     {flag} {p.name:<12} {_color(p.type.value, 247)}   {default_str}{enum_hint}")

        # — Связи с БЗ.
        sre = list(getattr(wf, "settings_refs", None) or ())
        if sre:
            pairs_txt = ", ".join(_color(f"{r.setting_id} → {r.param_name}", 201) for r in sre)
            print(f"   {_color('настройки БЗ:', 248)}  {pairs_txt}")

        kbs = list(getattr(wf, "kb_sections", None) or ())
        if kbs:
            kb_txt = ", ".join(_color(k, 201) for k in kbs)
            print(f"   {_color('разделы БЗ:', 248)}    {kb_txt}")

    return 0


# --------------------------------------------------------------------------- #
# Команда kb-search                                                            #
# --------------------------------------------------------------------------- #
def cmd_kb_search(args: argparse.Namespace) -> int:
    """Полнотекстовый поиск по БЗ + аудит индекса (секции/настройки)."""
    from knowledge_base import default_kb

    kb = default_kb()
    top_k = max(1, int(getattr(args, "top", 5) or 5))
    query = args.query or ""
    hits = list(kb.query(text=query, top_k=top_k)) if query else []

    payload = {
        "query": query,
        "top_k": top_k,
        "hits": [{"score": h.score, **h.section.to_dict()} for h in hits],
        "section_keys": list(kb.list_section_keys() or []),
        "setting_ids": list(kb.list_setting_ids() or []),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    n_sec = len(payload["section_keys"])
    n_set = len(payload["setting_ids"])
    print()
    print(f"  {_color('Поиск в БЗ:', 81)}  "
          f"{_color(repr(query) if query else '(пусто)', 201)}")
    print(f"     (секций: {n_sec}, настроек: {n_set})")

    if not payload["hits"]:
        print()
        print(f"  {_color('Совпадений нет.', 237)} Примеры ключей секций:")
        for key in payload["section_keys"][:10]:
            print("     · " + key)
        return 0

    for h in hits:
        sec = h.section
        snippet = (sec.text or "").replace("\n", " ").strip()
        if len(snippet) > 140:
            snippet = snippet[:137].rstrip() + "..."
        print(f"\n  ▶ [score {h.score:.3f}]  {_color(sec.heading, 117)}")
        print(f"      (документ: {_color(sec.doc, 248)} · ключ: {sec.key})")
        if snippet:
            print("      " + _color(snippet, 249))

    if args.list_all:
        # — Аудит-списки: все секции БЗ + все настройки (settings).
        keys = payload["section_keys"] or []
        settings = payload["setting_ids"] or []
        print(f"\n  {_color('Секции БЗ:', 248)}")
        for key in keys:
            print("     · " + key)
        if settings:
            print(f"\n  {_color('Настройки (settings):', 248)}")
            for sid in settings:
                meta = kb.get_setting(sid) or {}
                val_str = str(meta.get("value", "?"))[:50]
                note = meta.get("note") or ""
                tail = f"   ({note[:40]})" if note else ""
                print(f"     · {sid} = {_color(val_str, 201)}{_color(tail, 253)}")

    return 0


# --------------------------------------------------------------------------- #
# Parser / main                                                                #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kesh-agent",
        description="Кэш-агент (роутер кеш-задач) — консольное приложение.")

    sub = parser.add_subparsers(dest="cmd", required=True, metavar="<команда>")

    # -- route ----------------------
    p_route = sub.add_parser(
        "route", help="распознать интент по фразе → воркфлоу/параметры/refs")
    p_route.add_argument("phrase", help="фраза пользователя, напр. 'выгрузи всё из кеша'")
    p_route.add_argument("--set", dest="set", action="append", default=None,
                         metavar='"name=value[,...]"',
                         help="значения параметров (можно несколько раз)")
    p_route.add_argument("--json", action="store_true", help="вывод в JSON")
    p_route.set_defaults(func=cmd_route)

    # -- workflows ------------------
    p_wf = sub.add_parser(
        "workflows", help="печать каталога воркфлоу (параметры/настройки/references)")
    p_wf.add_argument("--category", default=None, choices=["maintenance", "performance", "analytics"])
    p_wf.add_argument("--json", action="store_true", help="вывод в JSON")
    p_wf.set_defaults(func=cmd_workflows)

    # -- kb-search ------------------
    p_kb = sub.add_parser(
        "kb-search", help="полнотекстовый поиск по базе знаний (token × idf)")
    p_kb.add_argument("query", help="поисковая строка")
    p_kb.add_argument("--top", default=5, type=int, help="сколько записей вернуть (def 5)")
    p_kb.add_argument("--json", action="store_true", help="вывод в JSON")
    p_kb.add_argument("--list-all", action="store_true",
                      help="печатать списки всех секций и настроек БЗ")
    p_kb.set_defaults(func=cmd_kb_search)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    func = getattr(args, "func", None)
    if func is None:  # pragma: no cover — argparse требует подкоманду (required=True)
        return 2
    try:
        return int(func(args))
    except KeyboardInterrupt:
        print("\nОтменено пользователем.", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover — entrypoint
    sys.exit(main())
