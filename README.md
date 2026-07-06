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

## 3D-карты (Gaussian Splatting)

Пайплайн: `видео → кадры (ffmpeg) → позиции камер (COLMAP) → обучение 3DGS → .ply`
(вкладка «3D-карты»). Обучение выбирается по железу автоматически:

- **Mac / без NVIDIA** — трейнер **Brush** (Rust/wgpu, работает на Apple Silicon
  через Metal, БЕЗ CUDA). Это путь по умолчанию, когда `nvidia-smi` не найден.
- **Машина с NVIDIA GPU** — Nerfstudio (`ns-train splatfacto`) или gsplat (CUDA),
  а Brush остаётся запасным вариантом.

Установка зависимостей на Mac:

```bash
brew install ffmpeg colmap
# Brush — трейнер 3DGS на Metal (github.com/ArthurBrussee/brush).
# Готовых бинарей нет — собирается из исходников, нужен Rust 1.88+:
#   brew install rust        # или rustup
#   git clone https://github.com/ArthurBrussee/brush && cd brush
#   cargo build --release -p brush-cli
#   бинарь: target/release/brush-cli  (headless-вариант, без GUI)
```

Настройка (переменные окружения, необязательно):

- `BRUSH_BIN=/путь/к/target/release/brush-cli` — если бинаря нет в PATH.
- `BRUSH_CMD` — переопределить вызов Brush. Дефолт (флаги сверены по исходникам
  brush-cli):
  `{bin} {data} --total-train-iters {steps} --export-path {out_dir} --export-name model_{iter}.ply`.
  Плейсхолдеры подставляются простым replace: `{bin} {data} {steps} {ply} {out_dir}`
  (прочие фигурные скобки, напр. `{iter}`, уходят в Brush как есть).

Готовый `.ply` можно и не обучать здесь, а загрузить через «Загрузить готовый
.ply» — вьюер работает без GPU-обучения.

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

## Датасет планировок (RPLAN)

House-движок раскладывает комнаты подбором проверенной топологии из
`src/bim_agents/house_templates.json` (mosaic-формат: сетка разрезов +
комнаты-прямоугольники) и параметрической подгонкой под габариты. Базовая
библиотека — 6 отобранных вручную шаблонов; её можно расширить из
[RPLAN](http://staff.ustc.edu.cn/~fuxm/projects/DeepLayout/) (реальные планы
квартир) конвертером:

```bash
# 1) канонический RPLAN — каталог 4-канальных PNG:
python -m src.bim_agents.rplan_convert <каталог_с_png> \
    --append src/bim_agents/house_templates.json --limit 5000

# 2) зеркало Graph2Plan (.mat со struct-массивом data: боксы gtBoxNew + rType),
#    напр. Kaggle lkerkarabulut/rplan-dataset2025 → Network/data/data_train.mat:
python -m src.bim_agents.rplan_convert <data_train.mat> --graph2plan \
    --out rplan_templates.json --limit 5000
```

Конвертер отбирает только планы, выражающиеся чистой прямоугольной мозаикой
(slicing-раскладкой), L-образные/непрямоугольные отбрасывает, дедуплицирует
и мапит 18 классов RPLAN в наши категории. Оба входных формата (PNG-каналы и
Graph2Plan-боксы) идут через одно ядро сборки мозаики. Подробности — в
docstring `src/bim_agents/rplan_convert.py`.

## Статус

✅ Phase 0 — Инфраструктура (LM Studio, модели, окружение)  
✅ Phase 1 — RAG-ассистент по нормативам  
✅ Phase 3 — BIM/IFC генерация  
✅ Web App — все три вкладки  
⬜ Phase 2 — Сметы (в разработке)

---

*Built with local LLMs — no cloud API required.*
