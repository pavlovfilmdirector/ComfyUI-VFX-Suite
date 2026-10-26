from __future__ import annotations

from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

# Импортируем готовый роутер и типы из текущего воркспейса
from task_router import default_router, TaskRouter, RoutingResult

app = FastAPI(
    title="LiteRT Local Cache Agent API",
    description="Гибридная оркестрация: FastAPI-обертка над локальным TaskRouter и базой знаний.",
    version="1.0.0",
)


# --- Pydantic Схемы для валидации запросов и ответов ---

class RouteRequest(BaseModel):
    user_text: str = Field(..., description="Естественная фраза пользователя (RU/EN)", example="очисти кеш полностью")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Дополнительный контекст или параметры")


class KBQueryRequest(BaseModel):
    query: str = Field(..., description="Поисковый запрос по базе знаний", example="hit rate")
    top_k: int = Field(default=5, description="Максимальное количество результатов")


# --- Эндпоинты ---

@app.get("/health", summary="Проверка работоспособности сервиса")
def health_check() -> Dict[str, str]:
    return {"status": "ok", "service": "litert-local-cache-agent"}


@app.post("/route", summary="Распознать интент и получить план выполнения")
def route_task(payload: RouteRequest) -> Dict[str, Any]:
    """Маршрутизирует запрос пользователя через TaskRouter, сопоставляя его с воркфлоу и базой знаний."""
    try:
        router: TaskRouter = default_router()
        result: RoutingResult = router.route(payload.user_text, context=payload.context)
        return result.to_dict()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка роутинга: {exc}")


@app.get("/workflows", summary="Каталог доступных воркфлоу")
def list_workflows(category: Optional[str] = Query(default=None, description="Фильтр по категории")) -> List[Dict[str, Any]]:
    """Возвращает список всех зарегистрированных воркфлоу (и кнопок для UI) с учетом категории."""
    router = default_router()
    wfs = router.registry.list(category=category)
    return [wf.to_button_schema() for wf in wfs]


@app.get("/kb/sections", summary="Список доступных разделов базы знаний")
def list_kb_sections() -> List[str]:
    """Возвращает список всех доступных ключей (slug) разделов базы знаний."""
    router = default_router()
    if router.kb is None:
        return []
    return router.kb.list_section_keys()


@app.post("/kb/search", summary="Полнотекстовый поиск по базе знаний")
def search_kb(payload: KBQueryRequest) -> List[Dict[str, Any]]:
    """Ищет релевантные разделы в Markdown-документах базы знаний."""
    router = default_router()
    if router.kb is None:
        raise HTTPException(status_code=503, detail="База знаний (KnowledgeBase) не инициализирована")
    
    hits = router.kb.query(payload.query, top_k=payload.top_k)
    return [
        {
            "score": hit.score,
            "section": hit.section.to_dict()
        }
        for hit in hits
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)