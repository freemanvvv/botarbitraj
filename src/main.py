"""
CLI-интерфейс Construction AI Copilot.
Точка входа: chat, search, index, generate, estimate.
"""
import sys
import os
from datetime import datetime

# Добавляем корень проекта в PYTHONPATH
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import MODELS, NORMATIVES_DIR, OUTPUT_DIR
from src.lmstudio_client import chat, list_models
from src.router import route, detect_language, estimate_complexity
from src.rag_pipeline import SimpleRAG, NormbaseRAG, get_rag
from src.document_generator import generate_docx, generate_pdf


def cmd_status():
    """Проверка статуса системы."""
    banner = """
╔══════════════════════════════════════╗
║   Construction AI Copilot ⚡         ║
║   Tron — твой архитектор            ║
╚══════════════════════════════════════╝
"""
    print(banner)

    # Проверка LM Studio
    try:
        models = list_models()
        print(f"✅ LM Studio API: {len(models)} моделей в системе")
        for m in models:
            print(f"   - {m.id}")
    except Exception as e:
        print(f"❌ LM Studio API: {e}")
        print("   (запусти LM Studio и загрузи модель для чата)")

    # Проверка RAG
    try:
        rag_simple = get_rag("simple")
        count = rag_simple.count()
        print(f"📚 ChromaDB (simple): {count} чанков")
        try:
            rag_nb = get_rag("normbase")
            count_nb = rag_nb.count()
            print(f"📚 ChromaDB (normbase): {count_nb} чанков")
        except Exception:
            pass
    except Exception as e:
        print(f"⚠️  ChromaDB: {e}")

    print(f"📁 Нормативы: {NORMATIVES_DIR}")
    print(f"📁 Вывод: {OUTPUT_DIR}")


def cmd_index():
    """Индексация нормативов."""
    print(f"📥 Индексирую нормативы из {NORMATIVES_DIR}...")
    try:
        rag = get_rag()
        results = rag.index_directory()
        total = sum(results.values())
        print(f"✅ Проиндексировано {len(results)} документов, {total} чанков")
        if not results:
            print("   (директория пуста или файлы не поддерживаются)")
    except Exception as e:
        print(f"❌ Ошибка индексации: {e}")


def cmd_search(query: str):
    """Поиск по нормативной базе (simple mode)."""
    rag = get_rag("simple")
    results = rag.search(query)

    if not results:
        print("🔍 Ничего не найдено. Возможно, база пуста (запусти 'index').")
        return

    print(f"🔍 Найдено {len(results)} результатов:\n")
    for i, r in enumerate(results, 1):
        print(f"--- Результат {i} (релевантность: {r['score']:.2f}) ---")
        print(f"📄 Источник: {r['source']}")
        print(f"📝 {r['text'][:500]}...")
        print()


def _expand_query(query: str, model_key: str) -> list[str]:
    """Query expansion: LLM генерирует 3-4 конкретных подзапроса."""
    prompt = (
        "Ты — ассистент-поисковик по строительным нормам Узбекистана (КМК/ШНК).\n"
        "Пользователь спросил: «" + query + "»\n"
        "Сгенерируй 3 коротких точных поисковых запроса, которые найдут конкретные цифры,\n"
        "нормы и пункты из нормативов по этой теме.\n"
        "Пиши коротко: 2-5 слов, без вводных фраз.\n"
        "Пример: «ширина марша лестницы норматив» «высота ступени»\n"
        "Никаких пояснений, только запросы, каждый с новой строки."
    )
    try:
        res = chat(model_key, [{"role": "user", "content": prompt}], stream=False)
        import re as _re
        queries = []
        for q in res.strip().split('\n'):
            q = q.strip().strip('"').lstrip('-*#—› ')
            q = _re.sub(r'^\d+\.\s*', '', q)  # убираем "1. ", "2. "
            if q and len(q) > 3 and not q.startswith(('Пример', 'Никаких', 'Пиши')):
                queries.append(q)
        queries.insert(0, query)
        print(f"🔍 Подзапросы: {len(queries)}", flush=True)
        for q in queries:
            print(f"   • {q[:80]}", flush=True)
        return queries[:5]
    except Exception:
        return [query]


def cmd_chat(query: str):
    """Диалог с ассистентом через RAG + LLM."""
    # Маршрутизация
    routing = route(query)
    print(f"🧠 Маршрут: {routing['model_key']} [{routing['model_id']}]")
    print(f"🌐 Язык: {routing['language']}")
    print(f"📊 Сложность: {routing['complexity']}\n")

    # Query expansion — генерируем подзапросы
    print("⏳ Query expansion...")
    sys.stdout.flush()
    subqueries = _expand_query(query, routing['model_key'])

    # Поиск по каждому подзапросу
    rag = get_rag("normbase")
    seen_texts = set()
    all_results = []
    for sq in subqueries:
        results = rag.search(sq, top_k=3)
        for r in results:
            # Дедупликация по тексту
            text_hash = r['text'][:100]
            if text_hash not in seen_texts:
                seen_texts.add(text_hash)
                all_results.append(r)

    # Сортируем по релевантности и берём топ-7
    all_results.sort(key=lambda x: x['score'], reverse=True)
    all_results = all_results[:7]

    print(f"📚 Уникальных чанков: {len(all_results)}", flush=True)

    # Сборка контекста с цитированием
    context = "Контекст из нормативных документов Узбекистана:\n\n"
    for r in all_results:
        meta = r.get('meta', {})
        src = f"{meta.get('doc_type','')} {meta.get('number','')} — {meta.get('title','')}"
        context += f"[{src}]\n{r['text'][:600]}\n\n"

    messages = [
        {"role": "system", "content": (
            "Ты — Construction AI Copilot, ассистент по строительным нормам Узбекистана. "
            "Отвечай на русском или узбекском (язык запроса). "
            "Используй контекст из нормативов и обязательно цитируй источник (название документа и пункт). "
            "Если в контексте нет конкретных цифр или пунктов — честно скажи, что в базе нет точных данных. "
            "Не выдумывай нормативы."
        )},
        {"role": "system", "content": context},
        {"role": "user", "content": query},
    ]

    try:
        print("⏳ Qwen3-14B генерирует ответ...")
        sys.stdout.flush()
        response = chat(routing["model_key"], messages, stream=False)
        print(f"\n🤖 Ответ:\n{response}\n")
        sys.stdout.flush()
        return response
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        if "connect" in str(e).lower() or "refused" in str(e).lower():
            print("\n💡 Запусти LM Studio и загрузи модель для чата.")
        return None


def cmd_generate(title: str, text: str):
    """Генерация документа."""
    print("📄 Генерирую документы...")

    # DOCX
    docx_path = generate_docx(text, title)
    print(f"✅ DOCX: {docx_path}")

    # PDF
    pdf_path = generate_pdf(text, title)
    print(f"✅ PDF: {pdf_path}")

    return docx_path, pdf_path


def cmd_estimate(query: str):
    """
    Сметный расчёт: LLM → структура работ → расчёт кодом → XLSX/PDF.
    """
    from src.estimate_engine import parse_boq_from_llm, calculate_estimate, export_to_xlsx, export_to_pdf

    print("💰 Генерация сметы...\n")

    routing = route(query)
    messages = [
        {
            "role": "system",
            "content": (
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
            ),
        },
        {"role": "user", "content": query},
    ]

    try:
        print("⏳ LLM генерирует состав работ...")
        sys.stdout.flush()
        response = chat(routing["model_key"], messages)
        print(f"📋 Состав работ:\n{response}\n")

        # Парсим ответ LLM в структуру
        items = parse_boq_from_llm(response)
        if not items:
            print("⚠️  Не удалось распарсить ответ LLM. Показываю сырой ответ:")
            print(response)
            return

        print(f"📊 Распознано {len(items)} позиций")
        for item in items:
            print(f"   - {item['name']}: {item['quantity']} {item['unit']}")

        # Расчёт
        print("\n⏳ Рассчитываю смету...")
        estimate = calculate_estimate(f"Смета: {query[:30]}...", items)

        print(f"\n💰 Итог по смете:")
        print(f"   Материалы: {estimate['total_materials']:,.2f} сум")
        print(f"   Работы:    {estimate['total_work']:,.2f} сум")
        print(f"   ВСЕГО:     {estimate['total']:,.2f} сум")

        # Экспорт
        xlsx_path = export_to_xlsx(estimate)
        pdf_path = export_to_pdf(estimate)
        print(f"\n📄 XLSX: {xlsx_path}")
        print(f"📄 PDF:  {pdf_path}")

        return estimate

    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return None


def _cmd_plan(query: str):
    """Генерация SVG-плана."""
    from src.svg_plans import SVGPlanGenerator

    plan = SVGPlanGenerator(scale=60)

    if query:
        params = query.split(",")
        try:
            w = float(params[0]) if len(params) > 0 else 12
            h = float(params[0 + 1]) if len(params) > 1 else 10
        except (ValueError, IndexError):
            w, h = 12, 10
    else:
        w, h = 12, 10

    plan.add_outer_walls(w, h)
    plan.add_room("Гостиная", 1, h/2, w/2 - 1, h/2 - 1)
    plan.add_room("Кухня", w/2 + 0.5, h/2, w/2 - 1, h/2 - 1)
    plan.add_room("Спальня 1", 1, 0.5, w/3, h/2 - 1)
    plan.add_room("Спальня 2", w/3 + 1, 0.5, w/3, h/2 - 1)

    path = plan.save()
    print(f"✅ SVG-план: {path}")


def _cmd_ifc(args: list[str]):
    """Генерация максимальной IFC-модели."""
    from src.ifc_generator import create_max_building

    name = args[0] if args else "Building"
    params = " ".join(args[1:]) if len(args) > 1 else ""

    kw = {}
    if params:
        for p in params.split():
            if "=" in p:
                k, v = p.split("=", 1)
                try:
                    v = float(v) if "." in v else int(v)
                except ValueError:
                    pass
                kw[k] = v

    try:
        path, stats = create_max_building(
            name=name,
            length=kw.get("length", 15.0),
            width=kw.get("width", 12.0),
            height=kw.get("height", 7.0),
            num_floors=kw.get("floors", 2),
            wall_thickness=kw.get("wt", 0.4),
            slab_thickness=kw.get("st", 0.2),
            roof_type=kw.get("roof", "gable"),
            add_internal_walls=kw.get("intwalls", True),
            add_windows=kw.get("windows", True),
            add_doors=kw.get("doors", True),
        )
        for k, v in stats.items():
            print(f"   {k}: {v}")
        print(f"\n✅ IFC: {path}")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


def _cmd_nb_index(args: list[str]):
    """Индексация через NormBase (с OCR, чанкинг по пунктам, статусами)."""
    from src.rag_pipeline import NormbaseRAG

    nb = NormbaseRAG()
    only = None
    limit = None
    mode = "csv"  # csv or dir

    for arg in args:
        if arg.startswith("--only="):
            only = arg.split("=", 1)[1]
        elif arg.startswith("--limit="):
            limit = int(arg.split("=", 1)[1])
        elif arg == "--dir":
            mode = "dir"

    if mode == "dir":
        print(f"📥 NormBase: индексирую файлы из {NORMATIVES_DIR}")
        results = nb.index_directory()
        total = sum(results.values())
        print(f"✅ Обработано {len(results)} файлов, {total} чанков")
    else:
        print(f"📥 NormBase: индексирую из sources.csv")
        total = nb.index_from_csv(limit=limit, only=only)


def _cmd_nb_search(query: str, status_mode: str = "default"):
    """Поиск через NormBase (с фильтрацией статусов)."""
    from src.rag_pipeline import NormbaseRAG

    nb = NormbaseRAG()
    results = nb.search(query, top_k=5, status_mode=status_mode)

    if not results:
        print("🔍 Ничего не найдено.")
        return

    print(f"\n🔍 Найдено {len(results)} результатов:\n" + "=" * 60)
    for i, r in enumerate(results, 1):
        score = r["score"]
        print(f"\n{i}. {r['citation']}  (сходство {score:.2f})")
        snippet = r["text"].strip().replace("\n", " ")[:400]
        print(f"   {snippet}{'…' if len(r['text']) > 400 else ''}")


def cmd_pricing(action: str = "list"):
    """Управление расценками."""
    from src.pricing_db import list_all_materials, list_all_work, search_materials, search_work

    if action == "list_materials":
        materials = list_all_materials()
        if not materials:
            print("📦 База материалов пуста. Добавь через API.")
            return
        print(f"\n📦 Материалы ({len(materials)}):")
        for m in materials:
            print(f"   {m['name']:35s} | {m['unit']:15s} | {m['price']:>10,.0f} сум | {m['category']}")

    elif action == "list_work":
        work = list_all_work()
        if not work:
            print("👷 База работ пуста. Добавь через API.")
            return
        print(f"\n👷 Типы работ ({len(work)}):")
        for w in work:
            print(f"   {w['name']:35s} | {w['unit']:15s} | {w['price']:>10,.0f} сум | {w['category']}")

    elif action.startswith("search_material "):
        query = action[16:]
        results = search_materials(query)
        if not results:
            print(f"❌ Ничего не найдено по запросу: {query}")
            return
        print(f"\n🔍 Материалы по запросу '{query}':")
        for r in results:
            print(f"   {r['name']:35s} | {r['unit']:15s} | {r['price']:>10,.0f} сум")

    elif action.startswith("search_work "):
        query = action[12:]
        results = search_work(query)
        if not results:
            print(f"❌ Ничего не найдено по запросу: {query}")
            return
        print(f"\n🔍 Работы по запросу '{query}':")
        for r in results:
            print(f"   {r['name']:35s} | {r['unit']:15s} | {r['price']:>10,.0f} сум")

    else:
        print("""Управление расценками:
  pricing list_materials        — все материалы
  pricing list_work             — все виды работ
  pricing search_material <текст> — поиск материалов
  pricing search_work <текст>    — поиск работ
""")



def print_help():
    print("""Construction AI Copilot ⚡ — команды:

  status                     — проверка системы
  index                      — индексация нормативов
  search <запрос>            — поиск по нормативам
  chat <запрос>              — диалог с ассистентом
  gen <заголовок>|<текст>    — генерация PDF/DOCX
  estimate <описание>        — расчёт сметы (LLM → XLSX/PDF)
  pricing [list/search...]   — управление расценками
  plan [W,H]                 — SVG-план этажа (ширина, высота)
  ifc <name> [length=15 width=12 height=7 floors=2 roof=gable wt=0.4 st=0.2 windows=true doors=true intwalls=true]  — IFC-модель (окна, двери, перегородки, крыша)
  nb-index [--only=X] [--limit=N] [--dir]  — NormBase индексация
  nb-search <запрос> [--active] [--all]    — NormBase поиск с фильтром
  help                       — справка
""")


def main():
    if len(sys.argv) < 2:
        print_help()
        return

    command = sys.argv[1]
    args = sys.argv[2:]

    if command == "status":
        cmd_status()
    elif command == "index":
        cmd_index()
    elif command == "search":
        query = " ".join(args)
        cmd_search(query)
    elif command == "chat":
        query = " ".join(args)
        cmd_chat(query)
    elif command == "gen":
        if not args or "|" not in " ".join(args):
            print("❌ Формат: gen <заголовок>|<текст>")
            return
        full = " ".join(args)
        title, text = full.split("|", 1)
        cmd_generate(title.strip(), text.strip())
    elif command == "estimate":
        query = " ".join(args)
        cmd_estimate(query)
    elif command == "pricing":
        action = " ".join(args) if args else "help"
        cmd_pricing(action)
    elif command == "plan":
        if args:
            _cmd_plan(" ".join(args))
        else:
            _cmd_plan("")
    elif command == "ifc":
        _cmd_ifc(args)
    elif command == "nb-index":
        _cmd_nb_index(args)
    elif command == "nb-search":
        query_parts = []
        status_mode = "default"
        for arg in args:
            if arg == "--active":
                status_mode = "active"
            elif arg == "--all":
                status_mode = "all"
            else:
                query_parts.append(arg)
        query = " ".join(query_parts)
        _cmd_nb_search(query, status_mode)
    elif command == "help":
        print_help()
    else:
        print(f"❌ Неизвестная команда: {command}")
        print_help()


if __name__ == "__main__":
    main()
