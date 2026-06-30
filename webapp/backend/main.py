"""
Construction AI Copilot — Web App Backend
FastAPI-сервер для трёх вкладок: Архив, Чат, Моделирование
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import csv
import json
import shutil
from pathlib import Path
from typing import Optional

app = FastAPI(title="Construction AI Copilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

from src.config import MODELS as LM_MODELS

# Белый список допустимых ID моделей
_ALLOWED_MODEL_IDS = {cfg["id"] for cfg in LM_MODELS.values()}

# ─── пути проекта ───
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SRC_DIR = Path(__file__).resolve().parent.parent.parent / "src"
OUTPUT_DIR = SRC_DIR.parent / "output"
SOURCES_CSV = SRC_DIR / "normbase" / "sources.csv"
CHROMA_DIR = DATA_DIR / "chroma_db"
OUTPUT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════
#  ВКЛАДКА 1 — АРХИВ НОРМАТИВОВ
# ═══════════════════════════════════════════

def _load_sources() -> list[dict]:
    """Загружает CSV нормативов."""
    if not SOURCES_CSV.exists():
        return []
    with open(SOURCES_CSV, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


@app.get("/api/archive/groups")
def archive_groups():
    """Группы нормативов (КМК, ШНК и т.д.)."""
    sources = _load_sources()
    groups = {}
    for r in sources:
        doc_type = r.get("doc_type", "Другое").strip()
        if doc_type not in groups:
            groups[doc_type] = {"type": doc_type, "count": 0}
        groups[doc_type]["count"] += 1
    return {"groups": list(groups.values())}


@app.get("/api/archive/docs")
def archive_docs(
    doc_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Список нормативов с фильтрацией."""
    sources = _load_sources()

    # Фильтры
    if doc_type:
        sources = [r for r in sources if r.get("doc_type", "").strip() == doc_type]
    if status:
        sources = [r for r in sources if r.get("status", "").strip() == status]
    if search:
        terms = search.lower().split()
        sources = [
            r for r in sources
            if all(
                term in f"{r.get('doc_type','')} {r.get('number','')} {r.get('title','')} {r.get('id','')}".lower()
                for term in terms
            )
        ]

    total = len(sources)
    total_pages = max(1, (total + page_size - 1) // page_size)
    start = (page - 1) * page_size
    items = sources[start:start + page_size]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


@app.get("/api/archive/doc/{doc_id}")
def archive_doc_detail(doc_id: str):
    """Детали норматива + текст."""
    sources = _load_sources()
    doc = None
    for r in sources:
        if r["id"] == doc_id:
            doc = r
            break
    if not doc:
        raise HTTPException(404, "Документ не найден")

    # Проверяем, есть ли текстовый кэш
    text = ""
    raw_path = DATA_DIR / "normbase_raw" / f"{doc_id}.pdf"
    text_path = DATA_DIR / "normbase_text" / f"{doc_id}.txt"
    if text_path.exists():
        text = text_path.read_text(encoding="utf-8", errors="replace")[:50000]

    chunk_ids = []
    try:
        import sqlite3
        db = CHROMA_DIR / "chroma.sqlite3"
        if db.exists():
            conn = sqlite3.connect(str(db))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT em.string_value
                FROM embedding_metadata em
                JOIN embeddings e ON e.id = em.id
                WHERE em.key = 'doc_id' AND em.string_value = ?
            """, (doc_id,))
            chunk_ids = [r[0] for r in cursor.fetchall()]
            conn.close()
    except Exception:
        pass

    return {
        "doc": doc,
        "text_preview": text[:3000],
        "chunks": len(chunk_ids),
        "has_text": bool(text),
    }


# ═══════════════════════════════════════════
#  ВКЛАДКА 2 — ЧАТ С БОТОМ
# ═══════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    model: str = "qwen/qwen3-14b"
    use_rag: bool = True
    system_prompt: Optional[str] = None


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Чат с RAG + локальной LLM."""
    if req.model not in _ALLOWED_MODEL_IDS:
        raise HTTPException(400, f"Недопустимая модель: {req.model!r}")
    try:
        from src.lmstudio_client import list_models, chat as lm_chat
        from src.rag_pipeline import NormbaseRAG

        try:
            list_models()
        except Exception:
            raise HTTPException(503, "LM Studio не отвечает. Запусти сервер и загрузи модель.")

        context = ""
        rag_chunks: list[dict] = []

        if req.use_rag:
            rag = NormbaseRAG()

            # Query expansion: 2 доп. подзапроса через LLM → лучший recall
            queries = [req.message]
            try:
                exp_prompt = (
                    "Сгенерируй 2 коротких поисковых запроса (2-5 слов каждый) "
                    "для поиска в базе строительных нормативов Узбекистана по вопросу:\n"
                    f"«{req.message}»\n"
                    "Только сами запросы, каждый с новой строки, без нумерации и пояснений."
                )
                expansion = lm_chat(
                    req.model,
                    [{"role": "user", "content": exp_prompt}],
                    stream=False,
                    max_tokens=80,
                )
                for line in expansion.strip().split("\n"):
                    line = line.strip().lstrip("-*•1234567890. \"'")
                    if line and len(line) > 3:
                        queries.append(line)
                queries = queries[:4]
            except Exception:
                pass  # fallback — только оригинальный запрос

            # Поиск по всем подзапросам с дедупликацией
            seen: set[str] = set()
            all_results: list[dict] = []
            for q in queries:
                for r in rag.search(q, top_k=5):
                    key = r["text"][:80]
                    if key not in seen:
                        seen.add(key)
                        all_results.append(r)

            # Фильтрация по порогу релевантности (cosine similarity ≥ 0.40)
            MIN_SCORE = 0.40
            relevant = [r for r in all_results if r.get("score", 0) >= MIN_SCORE]
            relevant.sort(key=lambda x: x.get("score", 0), reverse=True)
            relevant = relevant[:6]

            if relevant:
                context = "Контекст из нормативных документов Узбекистана:\n\n"
                for r in relevant:
                    meta = r.get("meta", {})
                    src = f"{meta.get('doc_type','')} {meta.get('number','')} — {meta.get('title','')}"
                    context += f"[{src}]\n{r['text'][:600]}\n\n"
                rag_chunks = [
                    {
                        "citation": r.get("citation", ""),
                        "score": round(r.get("score", 0), 2),
                        "doc_type": r.get("meta", {}).get("doc_type", ""),
                        "number": r.get("meta", {}).get("number", ""),
                        "title": r.get("meta", {}).get("title", ""),
                    }
                    for r in relevant
                ]

        # Системный промпт зависит от того, найден ли релевантный контекст
        if context:
            sys_prompt = req.system_prompt or (
                "Ты — Construction AI Copilot, ассистент по строительным нормам Узбекистана. "
                "Отвечай ТОЛЬКО на основе приведённых фрагментов нормативов. "
                "Обязательно ссылайся на документ и пункт. "
                "Не добавляй информацию из собственных знаний — только то, что есть в контексте. "
                "Если конкретного ответа в найденных фрагментах нет — прямо скажи об этом."
            )
        else:
            sys_prompt = (
                "Ты — Construction AI Copilot. "
                "По данному запросу в базе нормативов Узбекистана не найдено релевантных документов. "
                "Сообщи пользователю об этом честно. "
                "Не генерируй ответ из собственных знаний — только скажи, "
                "что данных в базе нет, и предложи уточнить или переформулировать запрос."
            )

        messages = [{"role": "system", "content": sys_prompt}]
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": req.message})

        response = lm_chat(req.model, messages, stream=False)
        return {
            "response": response,
            "model": req.model,
            "rag_used": bool(context),
            "rag_chunks": rag_chunks,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/chat/models")
def chat_models():
    """Список доступных моделей LM Studio."""
    try:
        from src.lmstudio_client import list_models
        models = list_models()
        return {"models": [m.id for m in models]}
    except Exception:
        return {"models": [], "error": "LM Studio недоступна"}


@app.get("/api/chat/status")
def chat_status():
    """Статус RAG-системы."""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        collections = client.list_collections()
        info = {}
        for c in collections:
            info[c.name] = c.count()
        return {"status": "ok", "collections": info}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ═══════════════════════════════════════════
#  ВКЛАДКА 3 — МОДЕЛИРОВАНИЕ
# ═══════════════════════════════════════════

class BuildingParams(BaseModel):
    name: str = "Building"
    length: float = 15.0
    width: float = 12.0
    height: float = 7.0
    num_floors: int = 2
    floor_height: float | None = None
    wall_thickness: float = 0.4
    slab_thickness: float = 0.2
    roof_type: str = "gable"
    add_internal_walls: bool = True
    add_windows: bool = True
    add_doors: bool = True
    add_columns: bool = True
    add_beams: bool = True
    add_stairs: bool = True
    add_balconies: bool = False
    add_foundation: bool = True
    windows_per_wall_long: int = 3
    windows_per_wall_short: int = 2
    window_width: float = 1.2
    window_height: float = 1.5
    window_sill: float = 0.9
    door_width: float = 0.9
    door_height: float = 2.1


@app.post("/api/model/generate")
def model_generate(params: BuildingParams):
    """Генерация IFC-модели."""
    try:
        from src.ifc_generator import create_max_building
        path, stats = create_max_building(
            name=params.name,
            length=params.length,
            width=params.width,
            height=params.height,
            num_floors=params.num_floors,
            floor_height=params.floor_height,
            wall_thickness=params.wall_thickness,
            slab_thickness=params.slab_thickness,
            roof_type=params.roof_type,
            add_internal_walls=params.add_internal_walls,
            add_windows=params.add_windows,
            add_doors=params.add_doors,
            add_columns=params.add_columns,
            add_beams=params.add_beams,
            add_stairs=params.add_stairs,
            add_balconies=params.add_balconies,
            add_foundation=params.add_foundation,
            windows_per_wall_long=params.windows_per_wall_long,
            windows_per_wall_short=params.windows_per_wall_short,
            window_width=params.window_width,
            window_height=params.window_height,
            window_sill=params.window_sill,
            door_width=params.door_width,
            door_height=params.door_height,
        )
        return {
            "path": path,
            "stats": stats,
            "filename": os.path.basename(path),
            "download_url": f"/api/model/download/{os.path.basename(path)}",
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/model/analyze-image")
async def model_analyze_image(
    file: UploadFile = File(None),
    description: str = Form(""),
):
    """Анализ плана/фасада через LLM → извлечение параметров здания."""
    import base64
    from src.config import LM_STUDIO_BASE_URL

    image_b64 = None
    media_type = "image/jpeg"

    if file and file.filename:
        content = await file.read()
        image_b64 = base64.b64encode(content).decode()
        media_type = file.content_type or "image/jpeg"

    system_prompt = (
        "Ты — BIM-инженер. Проанализируй изображение архитектурного плана или фасада здания "
        "и извлеки параметры. Верни ТОЛЬКО валидный JSON без markdown:\n"
        "{\n"
        '  "name": "Building",\n'
        '  "description": "краткое описание",\n'
        '  "length": 15.0,\n'
        '  "width": 12.0,\n'
        '  "floor_height": 3.0,\n'
        '  "num_floors": 2,\n'
        '  "roof_type": "gable",\n'
        '  "windows_per_wall_long": 3,\n'
        '  "windows_per_wall_short": 2,\n'
        '  "window_width": 1.2,\n'
        '  "window_height": 1.5,\n'
        '  "window_sill": 0.9,\n'
        '  "door_width": 0.9,\n'
        '  "door_height": 2.1,\n'
        '  "add_columns": false,\n'
        '  "add_balconies": false,\n'
        '  "notes": "краткий анализ изображения"\n'
        "}"
    )

    user_content: list = []
    if image_b64:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{image_b64}"}
        })
    text_part = description or "Проанализируй изображение и верни параметры здания."
    user_content.append({"type": "text", "text": text_part})

    try:
        import requests as req_lib
        resp = req_lib.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json={
                "model": "local-model",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content if image_b64 else text_part},
                ],
                "temperature": 0.1,
                "max_tokens": 600,
            },
            timeout=60,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # Извлечь JSON из ответа
        import re
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            raise ValueError("LLM не вернул JSON")
        extracted = json.loads(json_match.group())

        # Сформировать params для генерации
        floor_h = float(extracted.get("floor_height", 3.0))
        n_floors = int(extracted.get("num_floors", 2))
        result_params = {
            "name": extracted.get("name", "Building"),
            "length": float(extracted.get("length", 15.0)),
            "width": float(extracted.get("width", 12.0)),
            "height": floor_h * n_floors,
            "num_floors": n_floors,
            "roof_type": extracted.get("roof_type", "gable"),
            "windows_per_wall_long": int(extracted.get("windows_per_wall_long", 3)),
            "windows_per_wall_short": int(extracted.get("windows_per_wall_short", 2)),
            "window_width": float(extracted.get("window_width", 1.2)),
            "window_height": float(extracted.get("window_height", 1.5)),
            "window_sill": float(extracted.get("window_sill", 0.9)),
            "door_width": float(extracted.get("door_width", 0.9)),
            "door_height": float(extracted.get("door_height", 2.1)),
            "add_columns": bool(extracted.get("add_columns", False)),
            "add_balconies": bool(extracted.get("add_balconies", False)),
            "notes": extracted.get("notes", ""),
            "description": extracted.get("description", ""),
        }
        return {"ok": True, "params": result_params}
    except Exception as e:
        raise HTTPException(500, f"Ошибка анализа: {e}")


class BimGenerateRequest(BaseModel):
    description: str


class ArchitectRequest(BaseModel):
    requirements: str
    model: str = "local-model"


@app.post("/api/model/architect")
def model_architect(req: ArchitectRequest):
    """
    LLM-архитектор двухшаговый пайплайн:
    1. Собирает нормы (ChromaDB если заполнена + статическая база КМК/ШНК)
    2. LLM изучает нормы и составляет план здания с цитированием
    3. Генерирует IFC-модель по плану
    """
    import requests as req_lib
    from src.config import LM_STUDIO_BASE_URL
    from src.normbase.norms_knowledge import get_relevant_norms, search_sources_csv
    import re

    if not req.requirements.strip():
        raise HTTPException(400, "requirements is required")

    # ─── ШАГ 1: Сбор норм ─────────────────────────────────────────────────────
    static_norms = get_relevant_norms(req.requirements)

    # Дополнительно ищем релевантные документы в ChromaDB
    chroma_context = ""
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        try:
            col = client.get_collection("uz_construction_norms")
            if col.count() > 0:
                results = col.query(
                    query_texts=[req.requirements],
                    n_results=min(8, col.count()),
                )
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                if docs:
                    chroma_context = "\n\nДОПОЛНИТЕЛЬНЫЕ ФРАГМЕНТЫ ИЗ БАЗЫ:\n"
                    for doc, meta in zip(docs, metas):
                        src = f"{meta.get('doc_type','')} {meta.get('number','')} п.{meta.get('clauses','')}".strip()
                        chroma_context += f"\n[{src}]\n{doc[:400]}\n"
        except Exception:
            pass
    except Exception:
        pass

    # Поиск релевантных документов в CSV (по ключевым словам)
    csv_path = str(SRC_DIR / "normbase" / "sources.csv")
    csv_hits = search_sources_csv(req.requirements[:80], csv_path, limit=5)
    csv_refs = ""
    if csv_hits:
        csv_refs = "\n\nРЕЛЕВАНТНЫЕ НОРМАТИВЫ В БАЗЕ:\n" + "\n".join(
            f"• {r.get('doc_type','')} {r.get('number','')} — {r.get('title','')}"
            for r in csv_hits
        )

    norms_block = static_norms + chroma_context + csv_refs

    # ─── ШАГ 2: LLM разрабатывает план ───────────────────────────────────────
    system_prompt = f"""Ты — опытный архитектор-проектировщик в Узбекистане с 20-летним стажем.

Перед тобой ДЕЙСТВУЮЩИЕ СТРОИТЕЛЬНЫЕ НОРМЫ (КМК/ШНК):
{norms_block}

ТВОЯ ЗАДАЧА:
1. Изучи требования заказчика
2. Применяя нормы выше, рассчитай все параметры здания:
   - Площадь: исходя из числа людей × норма на человека
   - Размеры плана: длина × ширина из площади и оптимальных пропорций
   - Высоту этажа: строго по нормам для данного типа здания
   - Толщину стен: по КМК 2.03.06-01 в зависимости от этажности
   - Количество и размеры окон: световой коэффициент 1:8 (жилые) или 1:6 (офис)
   - Подоконник: по норме для типа здания
   - Перемычки, колонны, фундамент: по нормам
3. Для каждого решения УКАЖИ норму-основание (например: «КМК 2.08.01-89 п.2.1»)

Верни ТОЛЬКО валидный JSON без markdown:
{{
  "name": "Название проекта",
  "building_type": "жилой/офис/торговый",
  "summary": "Описание проекта (2-3 предложения)",
  "norm_study": "Какие нормы применены и почему (200-400 символов)",
  "plan": {{
    "total_area_m2": 150.0,
    "persons": 6,
    "area_per_person": 25.0,
    "norm_ref_area": "КМК 2.08.01-89 п.2.1",
    "floor_count": 2,
    "floor_height_m": 3.0,
    "norm_ref_height": "КМК 2.08.01-89 п.2.1",
    "wall_material": "кирпич 1.5 кирпича",
    "wall_thickness_m": 0.38,
    "norm_ref_wall": "КМК 2.03.06-01 п.2.2",
    "slab_thickness_m": 0.20,
    "norm_ref_slab": "КМК 2.03.01-96 п.3.1",
    "window_sill_m": 0.9,
    "norm_ref_sill": "КМК 2.08.01-89 п.3.1",
    "lintel_height_m": 0.25,
    "norm_ref_lintel": "КМК 2.03.06-01 п.3.1",
    "foundation_depth_m": 0.6,
    "norm_ref_foundation": "КМК 2.02.01-98 п.4.1"
  }},
  "reasoning": {{
    "footprint": "Обоснование длины и ширины с ссылкой на нормы",
    "floors": "Обоснование этажности и высоты этажа",
    "facade": "Обоснование окон: кол-во, размер, подоконник (световой коэффициент)",
    "structure": "Стены, колонны, балконы, крыша — и почему",
    "layout": "Краткая планировка помещений"
  }},
  "params": {{
    "length": 18.0,
    "width": 12.0,
    "num_floors": 2,
    "floor_height": 3.0,
    "wall_thickness": 0.38,
    "roof_type": "gable",
    "windows_per_wall_long": 3,
    "windows_per_wall_short": 2,
    "window_width": 1.2,
    "window_height": 1.5,
    "window_sill": 0.9,
    "door_width": 0.9,
    "door_height": 2.1,
    "add_columns": false,
    "add_balconies": false,
    "add_internal_walls": true,
    "add_foundation": true
  }}
}}"""

    try:
        resp = req_lib.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json={
                "model": req.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Требования заказчика:\n{req.requirements}"},
                ],
                "temperature": 0.2,
                "max_tokens": 2000,
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # Извлечь JSON
        json_match = re.search(r'\{[\s\S]*\}', raw)
        if not json_match:
            raise ValueError("LLM не вернул JSON")
        data = json.loads(json_match.group())

        p = data.get("params", {})
        plan = data.get("plan", {})
        floor_h = float(p.get("floor_height", plan.get("floor_height_m", 3.0)))
        n_floors = int(p.get("num_floors", plan.get("floor_count", 2)))
        wall_t = float(p.get("wall_thickness", plan.get("wall_thickness_m", 0.38)))

        building_params = {
            "name": data.get("name", "Building"),
            "length": float(p.get("length", 15.0)),
            "width": float(p.get("width", 12.0)),
            "height": floor_h * n_floors,
            "num_floors": n_floors,
            "wall_thickness": wall_t,
            "slab_thickness": float(plan.get("slab_thickness_m", 0.20)),
            "roof_type": p.get("roof_type", "gable"),
            "add_internal_walls": bool(p.get("add_internal_walls", True)),
            "add_windows": True,
            "add_doors": True,
            "add_columns": bool(p.get("add_columns", False)),
            "add_beams": True,
            "add_stairs": bool(n_floors > 1),
            "add_balconies": bool(p.get("add_balconies", False)),
            "add_foundation": bool(p.get("add_foundation", True)),
            "windows_per_wall_long": int(p.get("windows_per_wall_long", 3)),
            "windows_per_wall_short": int(p.get("windows_per_wall_short", 2)),
            "window_width": float(p.get("window_width", 1.2)),
            "window_height": float(p.get("window_height", 1.5)),
            "window_sill": float(p.get("window_sill", plan.get("window_sill_m", 0.9))),
            "door_width": float(p.get("door_width", 0.9)),
            "door_height": float(p.get("door_height", 2.1)),
        }

        # ─── ШАГ 2.5: Детерминированная проверка норм (не доверяем LLM на слово) ──
        from src.normbase.validator import validate_and_fix_params
        building_type_str = data.get("building_type", "")
        building_params, norm_violations = validate_and_fix_params(building_params, building_type_str)

        # ─── ШАГ 3: Генерация IFC ─────────────────────────────────────────────
        from src.ifc_generator import create_max_building
        path, stats = create_max_building(**building_params)

        return {
            "ok": True,
            "name": data.get("name", "Building"),
            "building_type": data.get("building_type", ""),
            "summary": data.get("summary", ""),
            "norm_study": data.get("norm_study", ""),
            "plan": data.get("plan", {}),
            "reasoning": data.get("reasoning", {}),
            "params": building_params,
            "norm_violations_fixed": norm_violations,
            "stats": stats,
            "filename": os.path.basename(path),
            "download_url": f"/api/model/download/{os.path.basename(path)}",
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))


@app.post("/api/model/bim-generate")
def model_bim_generate(req: BimGenerateRequest):
    """BIM-пайплайн: текст → IFC (по ТЗ)."""
    if not req.description.strip():
        raise HTTPException(400, "description is required")
    try:
        description = req.description
        from src.bim_agents import run_pipeline
        path, stats = run_pipeline(req.description)
        return {
            "path": path,
            "stats": stats,
            "filename": os.path.basename(path),
            "download_url": f"/api/model/download/{os.path.basename(path)}",
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))


@app.get("/api/model/download/{filename}")
def model_download(filename: str):
    """Скачать IFC-файл."""
    filepath = (OUTPUT_DIR / filename).resolve()
    if not filepath.is_relative_to(OUTPUT_DIR.resolve()):
        raise HTTPException(400, "Недопустимое имя файла")
    if not filepath.exists():
        raise HTTPException(404, "Файл не найден")
    return FileResponse(str(filepath), media_type="application/ifc", filename=filename)


@app.get("/api/model/existing")
def model_existing():
    """Список сгенерированных IFC-файлов."""
    files = []
    for f in sorted(OUTPUT_DIR.glob("*.ifc"), key=os.path.getmtime, reverse=True):
        files.append({
            "name": f.name,
            "size_kb": f.stat().st_size // 1024,
            "created": os.path.getmtime(f),
            "url": f"/api/model/download/{f.name}",
        })
    return {"files": files}


@app.delete("/api/model/delete/{filename}")
def model_delete(filename: str):
    """Удалить IFC-файл."""
    filepath = (OUTPUT_DIR / filename).resolve()
    if not filepath.is_relative_to(OUTPUT_DIR.resolve()):
        raise HTTPException(400, "Недопустимое имя файла")
    if not filepath.exists():
        raise HTTPException(404, "Файл не найден")
    os.remove(str(filepath))
    return {"deleted": filename, "ok": True}


@app.get("/api/model/info/{filename}")
def model_info(filename: str):
    """Информация об IFC-файле."""
    try:
        import ifcopenshell
        filepath = OUTPUT_DIR / filename
        if not filepath.exists():
            raise HTTPException(404, "Файл не найден")
        ifc = ifcopenshell.open(str(filepath))
        return {
            "filename": filename,
            "schema": ifc.wrapped_data.schema,
            "project": ifc.by_type("IfcProject")[0].Name,
            "walls": len(ifc.by_type("IfcWall")),
            "slabs": len(ifc.by_type("IfcSlab")),
            "windows": len(ifc.by_type("IfcWindow")),
            "doors": len(ifc.by_type("IfcDoor")),
            "openings": len(ifc.by_type("IfcOpeningElement")),
            "storeys": len(ifc.by_type("IfcBuildingStorey")),
            "materials": len(ifc.by_type("IfcMaterial")),
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/model/view/{filename}")
def model_view(filename: str):
    """IFC → JSON-треугольники для Three.js.
    Использует ifcopenshell.geom для корректной тесселяции с булевыми операциями.
    IFC Z-up → Three.js Y-up: переставляем (x,y,z) → (x,z,y) и инвертируем обход треугольников.
    """
    try:
        import ifcopenshell
        import ifcopenshell.geom

        filepath = OUTPUT_DIR / filename
        if not filepath.exists():
            raise HTTPException(404, "Файл не найден")
        ifc = ifcopenshell.open(str(filepath))

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)

        def ifc_to_three(verts_flat):
            """IFC (x,y,z) → Three.js (x,z,y)  (Z-up → Y-up swap)."""
            out = []
            for i in range(0, len(verts_flat), 3):
                out.extend([verts_flat[i], verts_flat[i + 2], verts_flat[i + 1]])
            return out

        def reverse_winding(faces_flat):
            """Swap v1↔v2 per triangle to compensate for the reflection."""
            out = []
            for i in range(0, len(faces_flat), 3):
                out.extend([faces_flat[i], faces_flat[i + 2], faces_flat[i + 1]])
            return out

        geometry = []
        DISPLAY_TYPES = {
            "IfcWall": "IfcWall",
            "IfcSlab": "IfcSlab",
            "IfcWindow": "IfcWindow",
            "IfcDoor": "IfcDoor",
            "IfcColumn": "IfcColumn",
            "IfcBeam": "IfcBeam",
            "IfcStairFlight": "IfcStairFlight",
            "IfcFooting": "IfcFooting",
            "IfcRailing": "IfcRailing",
        }

        for ifc_type, display_type in DISPLAY_TYPES.items():
            for product in ifc.by_type(ifc_type):
                try:
                    shape = ifcopenshell.geom.create_shape(settings, product)
                    verts = list(shape.geometry.verts)
                    faces = list(shape.geometry.faces)
                    if not verts or not faces:
                        continue

                    # Slabs with ROOF predefined type → different colour in viewer
                    etype = display_type
                    if ifc_type == "IfcSlab" and getattr(product, "PredefinedType", None) == "ROOF":
                        etype = "IfcRoof"

                    geometry.append({
                        "name": product.Name or ifc_type,
                        "type": etype,
                        "vertices": ifc_to_three(verts),
                        "faces": reverse_winding(faces),
                    })
                except Exception:
                    pass

        return {"elements": geometry}
    except Exception as e:
        raise HTTPException(500, f"Ошибка чтения IFC: {e}")


# ═══════════════════════════════════════════
#  ВКЛАДКА 4 — 3D-КАРТЫ (GAUSSIAN SPLATTING)
# ═══════════════════════════════════════════

GSPLAT_UPLOAD_DIR = SRC_DIR.parent / "data" / "gsplat_uploads"
GSPLAT_UPLOAD_DIR.mkdir(exist_ok=True)


@app.post("/api/gsplat/upload")
async def gsplat_upload(
    file: UploadFile = File(...),
    project_name: str = Form("project"),
    fps: float = Form(1.0),
    model: str = Form(""),
):
    """Загружает видеофайл и создаёт задачу пайплайна."""
    allowed_ext = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed_ext:
        raise HTTPException(400, f"Поддерживаемые форматы: {', '.join(allowed_ext)}")

    # Сохраняем файл
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file.filename)
    dest = GSPLAT_UPLOAD_DIR / safe_name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        from src.gsplat_pipeline import create_job, start_job
        model_id = model if model in _ALLOWED_MODEL_IDS else list(_ALLOWED_MODEL_IDS)[0]
        job_id = create_job(
            video_path=str(dest),
            project_name=project_name or Path(file.filename).stem,
            fps=max(0.1, min(fps, 10.0)),
            model_id=model_id,
        )
        start_job(job_id)
        return {"job_id": job_id, "message": "Пайплайн запущен"}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/gsplat/jobs")
def gsplat_jobs():
    """Список всех задач."""
    try:
        from src.gsplat_pipeline import list_jobs
        jobs = list_jobs()
        return {"jobs": [
            {k: v for k, v in j.items() if k != "logs"}
            for j in jobs
        ]}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/gsplat/jobs/{job_id}")
def gsplat_job_status(job_id: str, log_offset: int = Query(0)):
    """Статус задачи + логи с заданного смещения (для polling)."""
    try:
        from src.gsplat_pipeline import get_job
        job = get_job(job_id)
        if not job:
            raise HTTPException(404, "Задача не найдена")
        all_logs = job["logs"]
        return {
            "id": job["id"],
            "project_name": job["project_name"],
            "status": job["status"],
            "step": job["step"],
            "progress": job["progress"],
            "llm_analysis": job["llm_analysis"],
            "output_ply": job.get("output_ply"),
            "created_at": job["created_at"],
            "logs": all_logs[log_offset:],
            "log_total": len(all_logs),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/gsplat/models")
def gsplat_models():
    """Список готовых .ply моделей."""
    try:
        from src.gsplat_pipeline import list_models
        return {"models": list_models()}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/gsplat/ply/{job_id}/{filename}")
def gsplat_serve_ply(job_id: str, filename: str):
    """Отдаёт .ply файл для вьюера."""
    try:
        from src.gsplat_pipeline import get_job, GSPLAT_DATA_DIR
        job = get_job(job_id)
        if not job:
            raise HTTPException(404, "Задача не найдена")
        ply = job.get("output_ply")
        if not ply or not Path(ply).exists():
            raise HTTPException(404, ".ply файл не найден")
        # Проверка path traversal
        ply_path = Path(ply).resolve()
        if not str(ply_path).startswith(str(GSPLAT_DATA_DIR.resolve())):
            raise HTTPException(400, "Недопустимый путь")
        return FileResponse(str(ply_path), media_type="application/octet-stream",
                            filename=ply_path.name)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/api/gsplat/upload-ply")
async def gsplat_upload_ply(
    file: UploadFile = File(...),
    project_name: str = Form("uploaded"),
):
    """Загрузка готового .ply файла напрямую (без пайплайна)."""
    if not file.filename.endswith(".ply"):
        raise HTTPException(400, "Ожидается файл .ply")
    try:
        from src.gsplat_pipeline import create_job, get_job, GSPLAT_DATA_DIR
        import uuid
        job_id = str(uuid.uuid4())[:8]
        job_dir = GSPLAT_DATA_DIR / job_id
        job_dir.mkdir(parents=True)
        ply_path = job_dir / "output" / file.filename
        ply_path.parent.mkdir(exist_ok=True)
        with open(ply_path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        from src.gsplat_pipeline import _jobs
        from datetime import datetime
        _jobs[job_id] = {
            "id": job_id,
            "project_name": project_name,
            "video_path": "",
            "fps": 0,
            "model_id": "",
            "status": "done",
            "step": "Загружен вручную",
            "progress": 100,
            "logs": [f"Файл загружен: {file.filename}"],
            "llm_analysis": {},
            "output_ply": str(ply_path),
            "created_at": datetime.now().isoformat(),
            "job_dir": str(job_dir),
        }
        return {"job_id": job_id, "message": "PLY загружен"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


# ─── Здоровье ───

@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "lmstudio": check_lmstudio(),
        "chroma": check_chroma(),
        "ifcopenshell": check_ifc(),
    }


def check_lmstudio():
    try:
        from src.lmstudio_client import list_models
        return [m.id for m in list_models()[:3]]
    except Exception:
        return False


def check_chroma():
    try:
        import chromadb
        client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        return {c.name: c.count() for c in client.list_collections()}
    except Exception:
        return False


def check_ifc():
    try:
        import ifcopenshell
        return ifcopenshell.version
    except Exception:
        return False


# Serve production frontend (built React app)
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765)
