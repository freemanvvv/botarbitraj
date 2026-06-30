# 🏗️ Construction AI Copilot

**AI-ассистент для строительного проектирования.**  
RAG по нормативам (КМК/ШНК/ГОСТ/СНиП), генерация IFC-моделей, сметы, документы.

Архитектура: LM Studio (локальные LLM) → Python Router → три фазы.

---

## Возможности

| Компонент | Описание |
|---|---|
| **RAG-ассистент** | Поиск по 92 000+ чанков нормативов. ChromaDB + LM Studio эмбеддинги |
| **Чат с ботом** | Выбор модели (Qwen3-14B, Qwen3-8B), вкл/выкл RAG, цитирование источников |
| **Генерация IFC** | Стены, окна, двери, скатная крыша, перегородки. IfcOpenShell + IFC4 |
| **Генерация документов** | Проектная документация на основе нормативов |
| **Сметы (BOQ)** | SQLite + openpyxl, расчёт стоимости |
| **Архив нормативов** | Библиотека ШНК/КМК с группами, фильтрами, поиском |

## Быстрый старт

```bash
# 1. Установить зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Запустить LM Studio (http://localhost:1234/v1)
#    Загрузить Qwen3-14B или Qwen3-8B

# 3. Запустить webapp
./start_webapp.sh
# Backend: http://localhost:8765
# Frontend: http://localhost:5173
```

Требования: Python 3.12, LM Studio v0.4+, Apple Metal (для Mac)

## Архитектура

```
LM Studio (локально)
  ├── Qwen3-14B — сметы, агент
  ├── Qwen3-8B  — быстрые ответы
  └── nomic-embed — эмбеддинги
        │
        ▼
Python Router
  ├── Phase 1: RAG (ChromaDB, LlamaIndex)
  ├── Phase 2: Сметы/BOQ (SQLite)
  └── Phase 3: BIM/IFC (IfcOpenShell)
        │
        ▼
Web App (FastAPI + React/Vite)
  ├── Архив нормативов
  ├── Чат с RAG
  └── Моделирование IFC
```

## Структура проекта

```
├── src/
│   ├── bim_agents/       # Агентная цепочка (Architect → FloorPlan → BIM)
│   ├── normbase/         # RAG-пайплайн (чанкинг, эмбеддинги, поиск)
│   ├── config.py         # Конфигурация моделей и API
│   ├── ifc_generator.py  # Генератор IFC-моделей
│   ├── rag_pipeline.py   # RAG с query expansion
│   └── main.py           # CLI-точка входа
├── webapp/
│   ├── backend/          # FastAPI (порт 8765)
│   └── frontend/         # React + Vite + Three.js (порт 5173)
├── data/
│   ├── normatives/       # КМК/ШНК в markdown
│   ├── external/         # Внешние источники (PjStroy)
│   └── pricing.db        # База цен
└── scripts/              # Индексация, обогащение метаданных
```

## Статус

✅ Phase 0 — Инфраструктура (LM Studio, модели, окружение)  
✅ Phase 1 — RAG-ассистент по нормативам  
✅ Phase 3 — BIM/IFC генерация  
✅ Web App — все три вкладки  
⬜ Phase 2 — Сметы (в разработке)

---

*Built with local LLMs — no cloud API required.*
