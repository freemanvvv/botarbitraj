"""
Construction AI Copilot — Web App Backend
FastAPI-сервер для трёх вкладок: Архив, Чат, Моделирование
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import csv
import json
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
        search_lower = search.lower()
        sources = [
            r for r in sources
            if search_lower in f"{r.get('doc_type','')} {r.get('number','')} {r.get('title','')} {r.get('id','')}".lower()
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
        import re

        # Проверка LM Studio
        try:
            models = list_models()
        except Exception:
            raise HTTPException(503, "LM Studio не отвечает. Запусти сервер и загрузи модель.")

        # RAG поиск
        context = ""
        if req.use_rag:
            rag = NormbaseRAG()
            results = rag.search(req.message, top_k=5)
            if results:
                context = "Контекст из нормативных документов Узбекистана:\n\n"
                for r in results:
                    meta = r.get("meta", {})
                    src = f"{meta.get('doc_type','')} {meta.get('number','')} — {meta.get('title','')}"
                    context += f"[{src}]\n{r['text'][:500]}\n\n"

        # Сборка сообщений
        sys_prompt = req.system_prompt or (
            "Ты — Construction AI Copilot, ассистент по строительным нормам Узбекистана. "
            "Отвечай на русском или узбекском (язык запроса). "
            "Используй контекст из нормативов и цитируй источник. "
            "Если в контексте нет точных данных — честно скажи об этом."
        )
        messages = [{"role": "system", "content": sys_prompt}]
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": req.message})

        response = lm_chat(req.model, messages, stream=False)
        return {"response": response, "model": req.model, "rag_used": bool(context)}
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
    """IFC → JSON-треугольники для Three.js. Парсит стены, окна, двери, крышу с поворотом."""
    try:
        import ifcopenshell
        import math
        filepath = OUTPUT_DIR / filename
        if not filepath.exists():
            raise HTTPException(404, "Файл не найден")
        ifc = ifcopenshell.open(str(filepath))

        def make_box(cx, cy, cz, w, d, h):
            """8 вершин (центрировано), 12 треугольников."""
            x0, y0, z0 = cx - w/2, cy - d/2, cz
            x1, y1, z1 = cx + w/2, cy + d/2, cz + h
            v = [x0,y0,z0, x1,y0,z0, x1,y1,z0, x0,y1,z0,
                 x0,y0,z1, x1,y0,z1, x1,y1,z1, x0,y1,z1]
            f = [0,1,2,0,2,3, 4,6,5,4,7,6,
                 0,4,5,0,5,1, 1,5,6,1,6,2,
                 2,6,7,2,7,3, 3,7,4,3,4,0]
            return v, f

        def make_box_at(px, py, pz, w, d, h, axis, rot_angle, centered=True):
            """Параллелепипед с центром в (px,py,pz), размер w×d×h, повёрнутый вокруг axis.
            Если centered=False — профиль от 0 до w/d (для крыши, только одна сторона от конька)."""
            if centered:
                hw, hd = w/2, d/2
                v = [-hw,-hd,0, hw,-hd,0, hw,hd,0, -hw,hd,0,
                     -hw,-hd,h, hw,-hd,h, hw,hd,h, -hw,hd,h]
            else:
                v = [0,0,0, w,0,0, w,d,0, 0,d,0,
                     0,0,h, w,0,h, w,d,h, 0,d,h]
            f = [0,1,2,0,2,3, 4,6,5,4,7,6,
                 0,4,5,0,5,1, 1,5,6,1,6,2,
                 2,6,7,2,7,3, 3,7,4,3,4,0]
            if abs(rot_angle) > 0.001:
                v = _rotate_verts(v, axis, rot_angle)
            out = []
            for i in range(0, len(v), 3):
                out.extend([v[i]+px, v[i+1]+py, v[i+2]+pz])
            return out, f

        def _rotate_verts(v, axis, angle):
            c, s = math.cos(angle), math.sin(angle)
            ax, ay, az = axis
            out = []
            for i in range(0, len(v), 3):
                x, y, z = v[i], v[i+1], v[i+2]
                rx = x*(c+ax*ax*(1-c)) + y*(ax*ay*(1-c)-az*s) + z*(ax*az*(1-c)+ay*s)
                ry = x*(ay*ax*(1-c)+az*s) + y*(c+ay*ay*(1-c)) + z*(ay*az*(1-c)-ax*s)
                rz = x*(az*ax*(1-c)-ay*s) + y*(az*ay*(1-c)+ax*s) + z*(c+az*az*(1-c))
                out.extend([rx, ry, rz])
            return out

        def get_placement_rp(product):
            placement = product.ObjectPlacement
            if placement and hasattr(placement, "RelativePlacement"):
                rp = placement.RelativePlacement
                if rp and hasattr(rp, "Location"):
                    c = rp.Location.Coordinates
                    return (float(c[0]), float(c[1]), float(c[2])), rp
            return (0, 0, 0), None

        def get_extrusion_dims(product):
            w, d, h = 0.3, 0.3, 2.8
            if not product.Representation:
                return w, d, h
            rep = product.Representation
            items = []
            if rep.is_a() == "IfcProductDefinitionShape":
                for r in (rep.Representations or []):
                    items.extend(r.Items or [])
            else:
                items = rep.Items or []
            for item in items:
                if item.is_a() == "IfcExtrudedAreaSolid":
                    sa = item.SweptArea
                    if sa and sa.is_a() == "IfcRectangleProfileDef":
                        w = float(sa.XDim)
                        d = float(sa.YDim)
                    if hasattr(item, "Depth"):
                        h = float(item.Depth)
            return w, d, h

        def get_rotation_angle(rp):
            """Извлекает угол поворота и ось из размещения IfcAxis2Placement3D."""
            if not rp:
                return (1, 0, 0), 0.0
            if not hasattr(rp, "Axis") or not rp.Axis:
                return (1, 0, 0), 0.0
            z_axis = [float(d) for d in rp.Axis.DirectionRatios]
            length = math.sqrt(sum(d*d for d in z_axis))
            if length < 0.001:
                return (1, 0, 0), 0.0
            z_axis = [d/length for d in z_axis]
            # угол между (0,0,1) и z_axis вокруг X
            # Используем atan2 для правильного знака по Y-компоненте
            angle = math.atan2(z_axis[1], z_axis[2])
            if abs(angle) < 0.001:
                return (1, 0, 0), 0.0
            return (1, 0, 0), angle

        geometry = []

        # ─── Стены ───
        for wall in ifc.by_type("IfcWall"):
            try:
                (px, py, pz), rp = get_placement_rp(wall)
                w, d_raw, h = get_extrusion_dims(wall)
                d = min(w, d_raw) if d_raw != 0.3 else d_raw
                w = max(w, d_raw)
                use_swapped = False
                if rp and hasattr(rp, "RefDirection") and rp.RefDirection:
                    dr = rp.RefDirection.DirectionRatios
                    if len(dr) >= 2 and abs(float(dr[0])) < 0.5 and abs(float(dr[1])) > 0.5:
                        use_swapped = True
                if use_swapped:
                    v, f = make_box(px + d/2, py + w/2, pz, d, w, h)
                else:
                    v, f = make_box(px + w/2, py + d/2, pz, w, d, h)
                geometry.append({"name": wall.Name, "type": "IfcWall", "vertices": v, "faces": f})
            except Exception:
                pass

        # ─── Плиты и крыша (с поворотом) ───
        for slab in ifc.by_type("IfcSlab"):
            try:
                (px, py, pz), rp = get_placement_rp(slab)
                w, d, h = get_extrusion_dims(slab)
                axis, angle = get_rotation_angle(rp) if hasattr(slab, "PredefinedType") and slab.PredefinedType == "ROOF" else ((1,0,0), 0.0)
                # Крыша: профиль только от конька до свеса (не центрированный)
                # Крыша: профиль от конька в сторону свеса
                # Крыша: поднимаем на высоту конька
                ridge_h = d * abs(math.sin(angle))  # вертикальная высота конька
                # От конька (вверху) до карниза (внизу)
                y_sign = -1 if angle >= 0 else 1
                v, f = make_box_at(px, py, pz + ridge_h, w, d * y_sign, h, axis, angle, centered=False)
                etype = "IfcRoof" if (hasattr(slab, "PredefinedType") and slab.PredefinedType == "ROOF") else "IfcSlab"
                geometry.append({"name": slab.Name, "type": etype, "vertices": v, "faces": f})
            except Exception:
                pass

        # ─── Окна ───
        for win in ifc.by_type("IfcWindow"):
            try:
                (px, py, pz), _ = get_placement_rp(win)
                w = float(win.OverallWidth) if win.OverallWidth else 1.2
                dh = float(win.OverallHeight) if win.OverallHeight else 1.5
                d = 0.05
                v, f = make_box(px, py + d/2, pz, w, d, dh)
                geometry.append({"name": win.Name, "type": "IfcWindow", "vertices": v, "faces": f})
            except Exception:
                pass

        # ─── Двери ───
        for door in ifc.by_type("IfcDoor"):
            try:
                (px, py, pz), _ = get_placement_rp(door)
                w = float(door.OverallWidth) if door.OverallWidth else 0.9
                dh = float(door.OverallHeight) if door.OverallHeight else 2.1
                d = 0.05
                v, f = make_box(px, py + d/2, pz, w, d, dh)
                geometry.append({"name": door.Name, "type": "IfcDoor", "vertices": v, "faces": f})
            except Exception:
                pass

        return {"elements": geometry}
    except Exception as e:
        raise HTTPException(500, f"Ошибка чтения IFC: {e}")


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
