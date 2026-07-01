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
from pydantic import BaseModel, Field
import csv
import json
import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger("construction_copilot")
logging.basicConfig(level=logging.INFO)


def _server_error(e: Exception, client_message: str) -> HTTPException:
    """
    Логирует полную трассировку на сервере и возвращает клиенту короткое
    сообщение без внутренних путей/деталей реализации. Использовать вместо
    `HTTPException(500, str(e))`, который дословно пересылает исключение
    (включая абсолютные пути и внутренние детали библиотек) в ответ клиенту.
    """
    logger.exception(client_message)
    return HTTPException(500, client_message)


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
        raise _server_error(e, "Ошибка обработки запроса чата")


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
    # Верхние границы — защита от случайного/намеренного запроса, который
    # заставит генератор построить неограниченно большую IFC-модель (DoS
    # через диск/память/CPU). Числа выбраны с запасом для реальных зданий.
    name: str = "Building"
    length: float = Field(15.0, gt=0, le=500)
    width: float = Field(12.0, gt=0, le=500)
    height: float = Field(7.0, gt=0, le=500)
    num_floors: int = Field(2, gt=0, le=120)
    floor_height: float | None = Field(None, gt=0, le=10)
    wall_thickness: float = Field(0.4, gt=0, le=2.0)
    slab_thickness: float = Field(0.2, gt=0, le=1.0)
    roof_type: str = "gable"
    add_internal_walls: bool = True
    add_windows: bool = True
    add_doors: bool = True
    add_columns: bool = True
    add_beams: bool = True
    add_stairs: bool = True
    add_balconies: bool = False
    add_foundation: bool = True
    windows_per_wall_long: int = Field(3, ge=0, le=50)
    windows_per_wall_short: int = Field(2, ge=0, le=50)
    window_width: float = Field(1.2, gt=0, le=10)
    window_height: float = Field(1.5, gt=0, le=10)
    window_sill: float = Field(0.9, ge=0, le=5)
    door_width: float = Field(0.9, gt=0, le=5)
    door_height: float = Field(2.1, gt=0, le=5)


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
        raise _server_error(e, "Ошибка генерации модели")


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
        raise _server_error(e, "Ошибка анализа изображения")


class BimGenerateRequest(BaseModel):
    description: str


class ArchitectRequest(BaseModel):
    requirements: str
    model: str = "local-model"
    floorplan_mode: str = "solver"  # "solver" | "neural" (LM Studio) | "chathousediffusion" (внешний CLI-мост)
    building_pattern: str = "row"    # "row" | "corner" | "duplex" — тип секции


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

ТВОЯ ЗАДАЧА — спроектировать здание ПО ЭТАПАМ СТРОИТЕЛЬСТВА, снизу вверх:
1. Котлован и фундамент — глубина заложения, тип фундамента (по этажности и грунту)
2. Типовой этаж — кол-во подъездов (секций), квартир на лестничной площадке,
   площади и состав помещений каждой квартиры
3. Лестнично-лифтовой узел — ширина марша, нужен ли лифт (обязателен от 5 этажей),
   сколько лифтов на подъезд, грузоподъёмность, размер шахты лифта
4. Инженерные сети — расположение шахты ВК (вода/канализация), шахты вентиляции,
   этажных электрощитов; все они должны быть строго друг над другом по вертикали
5. Кровля — тип, уклон, парапет/свес

На каждом этапе ОБЯЗАТЕЛЬНО применяй нормы выше и УКАЗЫВАЙ норму-основание
(например: «КМК 2.08.01-89 п.2.1»). Если в требованиях заказчика указано число
подъездов и квартир на площадке — используй именно их, посчитав лифты/лестницы
по нормам для этой конфигурации.

Верни ТОЛЬКО валидный JSON без markdown:
{{
  "name": "Название проекта",
  "building_type": "жилой/офис/торговый",
  "summary": "Описание проекта (2-3 предложения)",
  "norm_study": "Какие нормы применены и почему (200-400 символов)",
  "stages": [
    {{
      "stage": "Котлован и фундамент",
      "description": "Глубина заложения, тип фундамента и почему",
      "norm_refs": ["КМК 2.02.01-98 п.4.1"]
    }},
    {{
      "stage": "Типовой этаж",
      "description": "Подъезды, квартиры на площадке, состав и площади помещений квартиры",
      "norm_refs": ["КМК 2.08.01-89 п.2.1"]
    }},
    {{
      "stage": "Лестнично-лифтовой узел",
      "description": "Марш, лифт(ы), грузоподъёмность, шахта",
      "norm_refs": ["КМК 2.08.01-89 п.6.1"]
    }},
    {{
      "stage": "Инженерные сети",
      "description": "Шахта ВК, вентиляция, этажные электрощиты — расположение",
      "norm_refs": []
    }},
    {{
      "stage": "Кровля",
      "description": "Тип, уклон, парапет/свес",
      "norm_refs": []
    }}
  ],
  "building": {{
    "entrances": 1,
    "apartments_per_landing": 1,
    "apartment_rooms": 2,
    "has_elevator": false,
    "elevators_per_entrance": 0,
    "elevator_capacity_kg": 400,
    "elevator_shaft_m": "1.8x1.8",
    "stair_width_m": 1.2,
    "riser_shaft_m": "0.4x0.6",
    "electrical_niche_m": "0.6x0.9x0.2"
  }},
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
        from src.normbase.validator import validate_and_fix_params, validate_building_meta
        building_type_str = data.get("building_type", "")
        building_params, norm_violations = validate_and_fix_params(building_params, building_type_str)
        n_floors = int(building_params["num_floors"])  # используем уже подрезанное значение
        building_meta, building_violations = validate_building_meta(data.get("building", {}), n_floors)
        norm_violations = norm_violations + building_violations

        # ─── ШАГ 3: Генерация IFC ─────────────────────────────────────────────
        from src.ifc_generator import create_max_building, create_apartment_building
        n_entrances = int(building_meta.get("entrances", 1) or 1)
        n_apt = int(building_meta.get("apartments_per_landing", 1) or 1)
        if n_entrances > 1 or n_apt > 1:
            path, stats = create_apartment_building(
                name=building_params["name"],
                num_floors=building_params["num_floors"],
                floor_height=building_params["height"] / building_params["num_floors"],
                entrances=n_entrances,
                apartments_per_landing=n_apt,
                apartment_rooms=int(building_meta.get("apartment_rooms", 2) or 2),
                floorplan_mode=req.floorplan_mode if req.floorplan_mode in ("solver", "neural", "chathousediffusion") else "solver",
                llm_model=req.model,
                building_pattern=req.building_pattern,
                has_elevator=bool(building_meta.get("has_elevator", False)),
                elevators_per_entrance=int(building_meta.get("elevators_per_entrance", 1) or 1),
                elevator_capacity_kg=float(building_meta.get("elevator_capacity_kg", 400) or 400),
                elevator_shaft_m=str(building_meta.get("elevator_shaft_m", "1.8x1.8") or "1.8x1.8"),
                stair_width_m=float(building_meta.get("stair_width_m", 1.2) or 1.2),
                riser_shaft_m=str(building_meta.get("riser_shaft_m", "0.4x0.6") or "0.4x0.6"),
                electrical_niche_m=str(building_meta.get("electrical_niche_m", "0.6x0.9x0.2") or "0.6x0.9x0.2"),
                wall_thickness=building_params["wall_thickness"],
                slab_thickness=building_params["slab_thickness"],
                roof_type=building_params["roof_type"],
                add_windows=building_params.get("add_windows", True),
                add_doors=building_params.get("add_doors", True),
                add_columns=building_params.get("add_columns", True),
                add_beams=building_params.get("add_beams", True),
                add_foundation=building_params.get("add_foundation", True),
                window_width=building_params.get("window_width", 1.2),
                window_height=building_params.get("window_height", 1.5),
                window_sill=building_params.get("window_sill", 0.9),
                door_width=building_params.get("door_width", 0.9),
                door_height=building_params.get("door_height", 2.1),
            )
        else:
            path, stats = create_max_building(**building_params)

        # ─── ШАГ 4: Проверка целостности IFC ─────────────────────────────────
        from src.integrity_checker import validate_model_integrity
        integrity = validate_model_integrity(path)

        # Нарушения норм планировки квартир (Путь C, фазы 0-2) — тот же формат
        # issues, что и integrity_checker; объединяем в одну панель для UI.
        fp_issues = stats.pop("floorplan_issues", [])
        if fp_issues:
            integrity["issues"] = fp_issues + integrity["issues"]
            errors = sum(1 for i in integrity["issues"] if i["severity"] == "error")
            warnings = sum(1 for i in integrity["issues"] if i["severity"] == "warning")
            integrity["ok"] = errors == 0
            integrity["summary"] = f"{errors} ошибок, {warnings} предупреждений"
            integrity["counts"]["errors"] = errors
            integrity["counts"]["warnings"] = warnings

        return {
            "ok": True,
            "name": data.get("name", "Building"),
            "building_type": data.get("building_type", ""),
            "summary": data.get("summary", ""),
            "norm_study": data.get("norm_study", ""),
            "stages": data.get("stages", []),
            "building": building_meta,
            "plan": data.get("plan", {}),
            "reasoning": data.get("reasoning", {}),
            "params": building_params,
            "norm_violations_fixed": norm_violations,
            "integrity": integrity,
            "stats": stats,
            "filename": os.path.basename(path),
            "download_url": f"/api/model/download/{os.path.basename(path)}",
        }
    except Exception as e:
        raise _server_error(e, "Ошибка работы AI-архитектора")


# ─── MEP-раскладки (электрика, трубы, слаботочка) ─────────────────────────
class MepRequest(BaseModel):
    filename: str
    model: str = "local-model"
    systems: list[str] = ["electrical", "plumbing", "low_current"]
    building_type: str = "row"


@app.post("/api/model/mep-generate")
def model_mep_generate(req: MepRequest):
    """
    Генерирует MEP-раскладку для готового IFC-файла.
    Использует координаты комнат из floorplan-кэша (если есть)
    или генерирует новую планировку через generate_floorplan_llm.
    """
    from src.floorplan import (
        generate_floorplan_llm,
        generate_floorplan,
        generate_mep_layout,
        MepLayout,
    )

    # Пробуем найти кэшированную планировку. Если нет — генерируем базовую
    # (для демо, если IFC был создан через ручные параметры без floorplan)
    import os
    filepath = (OUTPUT_DIR / req.filename).resolve()
    if not filepath.is_relative_to(OUTPUT_DIR.resolve()) or not filepath.exists():
        raise HTTPException(404, "IFC-файл не найден")

    # Пытаемся получить габариты из IFC (хотя бы примерные)
    width = 15.0
    depth = 9.0
    room_count = 2
    try:
        import ifcopenshell
        ifc = ifcopenshell.open(str(filepath))
        # Пытаемся найти размеры из IFC
        for storey in ifc.by_type("IfcBuildingStorey")[:1]:
            for rel in ifc.get_inverse(storey):
                if rel.is_a("IfcRelContainedInSpatialStructure"):
                    for el in rel.RelatedElements:
                        if el.is_a("IfcSlab"):
                            try:
                                rep = el.Representation.Representations[0]
                                poly = rep.Items[0]
                                if poly.is_a("IfcPolyline"):
                                    pts = [(p.Coordinates[0], p.Coordinates[1]) for p in poly.Points]
                                    xs = [p[0] for p in pts]
                                    ys = [p[1] for p in pts]
                                    width = max(xs) - min(xs)
                                    depth = max(ys) - min(ys)
                            except Exception:
                                pass
                        break
                    break
    except Exception:
        pass

    # Генерируем/загружаем планировку
    fp = generate_floorplan_llm(
        width=width,
        depth=depth,
        room_count=room_count,
        building_pattern=req.building_type,
        model=req.model,
    ) or generate_floorplan(
        width=width,
        depth=depth,
        room_count=room_count,
    )

    layout = generate_mep_layout(
        fp=fp,
        systems=req.systems,
        model=req.model,
    )

    return {
        "ok": True,
        "source": layout.source,
        "floorplan_source": fp.source,
        "width": round(width, 2),
        "depth": round(depth, 2),
        "room_count": room_count,
        "electrical": [e.__dict__ for e in layout.electrical],
        "plumbing": [p.__dict__ for p in layout.plumbing],
        "low_current": [l.__dict__ for l in layout.low_current],
    }


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
        raise _server_error(e, "Ошибка генерации модели по описанию")


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
        filepath = (OUTPUT_DIR / filename).resolve()
        if not filepath.is_relative_to(OUTPUT_DIR.resolve()):
            raise HTTPException(400, "Недопустимое имя файла")
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
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e, "Ошибка чтения информации о модели")


@app.get("/api/model/view/{filename}")
def model_view(filename: str):
    """IFC → JSON-треугольники для Three.js.
    Использует ifcopenshell.geom для корректной тесселяции с булевыми операциями.
    IFC Z-up → Three.js Y-up: переставляем (x,y,z) → (x,z,y) и инвертируем обход треугольников.
    """
    try:
        import ifcopenshell
        import ifcopenshell.geom

        filepath = (OUTPUT_DIR / filename).resolve()
        if not filepath.is_relative_to(OUTPUT_DIR.resolve()):
            raise HTTPException(400, "Недопустимое имя файла")
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
            "IfcTransportElement": "IfcTransportElement",
            "IfcFlowSegment": "IfcFlowSegment",
            "IfcBuildingElementProxy": "IfcBuildingElementProxy",
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
    except HTTPException:
        raise
    except Exception as e:
        raise _server_error(e, "Ошибка чтения геометрии модели")


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
        raise _server_error(e, "Ошибка запуска пайплайна 3D-реконструкции")


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
        raise _server_error(e, "Ошибка получения списка задач")


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
        raise _server_error(e, "Ошибка получения статуса задачи")


@app.get("/api/gsplat/models")
def gsplat_models():
    """Список готовых .ply моделей."""
    try:
        from src.gsplat_pipeline import list_models
        return {"models": list_models()}
    except Exception as e:
        raise _server_error(e, "Ошибка получения списка моделей")


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
        raise _server_error(e, "Ошибка выдачи файла модели")


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
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in Path(file.filename).name)
        ply_path = job_dir / "output" / safe_name
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
        raise _server_error(e, "Ошибка загрузки PLY-файла")


# ═══════════════════════════════════════════
#  ВКЛАДКА 5 — СМЕТЫ (BOQ)
# ═══════════════════════════════════════════

class EstimateGenerateRequest(BaseModel):
    description: str
    model: str = "local-model"


class PriceEntryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    unit: str = Field(..., min_length=1, max_length=20)
    price: float = Field(..., ge=0, le=1_000_000_000)
    category: str = Field("", max_length=100)
    region: str = Field("Ташкент", max_length=100)


@app.post("/api/estimate/generate")
def estimate_generate(req: EstimateGenerateRequest):
    """
    LLM структурирует состав работ/материалов по описанию объекта →
    парсинг в позиции (кодом) → расчёт по базе расценок (кодом, не LLM) →
    сохранение сметы в SQLite.
    """
    if not req.description.strip():
        raise HTTPException(400, "description is required")

    from src.config import LM_STUDIO_BASE_URL
    from src.estimate_engine import parse_boq_from_llm, calculate_estimate
    import requests as req_lib

    sys_prompt = (
        "Ты — сметчик по строительству в Узбекистане. "
        "Структурируй состав работ и материалов для указанного объекта. "
        "Выдай строго в формате Markdown-таблицы без лишнего текста.\n\n"
        "## Ведомость работ\n"
        "| № | Наименование | Ед. изм. | Кол-во |\n"
        "|---|---|---|---|\n"
        "| 1 | Название материала/работы | м3 | 12 |\n\n"
        "ВАЖНО: используй только точные числа, не диапазоны. "
        "Единицы измерения: м3, м2, м, шт, точка, мешок, т, кг. "
        "Не указывай цены — только объёмы. Не добавляй пояснений и примечаний."
    )

    try:
        resp = req_lib.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json={
                "model": req.model,
                "messages": [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": req.description},
                ],
                "temperature": 0.1,
                "max_tokens": 1500,
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        raise _server_error(e, "Ошибка обращения к локальной модели")

    items = parse_boq_from_llm(raw)
    if not items:
        raise HTTPException(422, "Не удалось распознать состав работ в ответе модели")

    # Инженерный предел — та же защита от DoS, что и в build-параметрах:
    # не даём одной LLM-генерацией создать неограниченную смету.
    MAX_ITEMS = 300
    if len(items) > MAX_ITEMS:
        items = items[:MAX_ITEMS]

    estimate = calculate_estimate(f"Смета: {req.description[:60]}", items)
    estimate["raw_llm_response"] = raw
    return estimate


@app.get("/api/estimate/{estimate_id}")
def estimate_get(estimate_id: int):
    from src.pricing_db import get_estimate
    estimate = get_estimate(estimate_id)
    if not estimate:
        raise HTTPException(404, "Смета не найдена")
    return estimate


@app.get("/api/estimate/{estimate_id}/export/{fmt}")
def estimate_export(estimate_id: int, fmt: str):
    """Экспорт сметы в xlsx или pdf."""
    if fmt not in ("xlsx", "pdf"):
        raise HTTPException(400, "Формат должен быть xlsx или pdf")

    from src.pricing_db import get_estimate
    raw = get_estimate(estimate_id)
    if not raw:
        raise HTTPException(404, "Смета не найдена")

    # calculate_estimate()'s item shape (type/name/unit/quantity/unit_price/total)
    # отличается от сырых строк estimate_items в БД — приводим к нему для экспорта.
    estimate_data = {
        "project_name": raw["project_name"],
        "items": [
            {
                "type": it["item_type"],
                "name": it["item_name"],
                "unit": it["unit"],
                "quantity": it["quantity"],
                "unit_price": it["unit_price"],
                "total": it["total_price"],
            }
            for it in raw["items"]
        ],
        "total_materials": raw["total_materials"],
        "total_work": raw["total_work"],
        "total": raw["total_overall"],
    }

    try:
        from src.estimate_engine import export_to_xlsx, export_to_pdf
        path = export_to_xlsx(estimate_data) if fmt == "xlsx" else export_to_pdf(estimate_data)
    except Exception as e:
        raise _server_error(e, "Ошибка экспорта сметы")

    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "application/pdf"
    return FileResponse(path, media_type=media, filename=os.path.basename(path))


@app.get("/api/pricing/materials")
def pricing_materials(q: Optional[str] = Query(None)):
    from src.pricing_db import search_materials, list_all_materials
    return {"materials": search_materials(q, limit=100) if q else list_all_materials()}


@app.get("/api/pricing/work")
def pricing_work(q: Optional[str] = Query(None)):
    from src.pricing_db import search_work, list_all_work
    return {"work": search_work(q, limit=100) if q else list_all_work()}


@app.post("/api/pricing/materials")
def pricing_add_material(entry: PriceEntryCreate):
    from src.pricing_db import add_material
    row_id = add_material(entry.name, entry.unit, entry.price, entry.category, entry.region)
    return {"id": row_id, "ok": True}


@app.post("/api/pricing/work")
def pricing_add_work(entry: PriceEntryCreate):
    from src.pricing_db import add_work
    row_id = add_work(entry.name, entry.unit, entry.price, entry.category, entry.region)
    return {"id": row_id, "ok": True}


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
