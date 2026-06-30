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
    wall_thickness: float = 0.4
    slab_thickness: float = 0.2
    roof_type: str = "gable"
    add_internal_walls: bool = True
    add_windows: bool = True
    add_doors: bool = True


@app.post("/api/model/generate")
def model_generate(params: BuildingParams):
    """Генерация IFC-модели (старый генератор)."""
    try:
        from src.ifc_generator import create_max_building
        path, stats = create_max_building(
            name=params.name,
            length=params.length,
            width=params.width,
            height=params.height,
            num_floors=params.num_floors,
            wall_thickness=params.wall_thickness,
            slab_thickness=params.slab_thickness,
            roof_type=params.roof_type,
            add_internal_walls=params.add_internal_walls,
            add_windows=params.add_windows,
            add_doors=params.add_doors,
        )
        return {
            "path": path,
            "stats": stats,
            "filename": os.path.basename(path),
            "download_url": f"/api/model/download/{os.path.basename(path)}",
        }
    except Exception as e:
        raise HTTPException(500, str(e))


class BimGenerateRequest(BaseModel):
    description: str


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
