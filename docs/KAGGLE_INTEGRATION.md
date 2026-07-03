# Kaggle Dataset Integration Guide

**Версия:** 1.0  
**Дата:** 3 июля 2026

---

## 📚 Обзор

Construction AI Copilot поддерживает интеграцию датасетов с Kaggle для расширения базы знаний RAG системы. Это позволяет обогатить индексированный контент архитектурными чертежами, планами этажей, справочниками и другими строительными ресурсами.

## 🚀 Быстрый старт

### 1. Установка kagglehub

```bash
pip install kagglehub
```

### 2. Аутентификация

Создайте API token на [Kaggle Settings](https://www.kaggle.com/settings/account):

```bash
# Интерактивная аутентификация
kagglehub auth login

# Или установить переменные окружения
export KAGGLE_USERNAME=<your_username>
export KAGGLE_KEY=<your_api_key>
```

### 3. Загрузка датасета

```bash
# Основной скрипт с полным функционалом
python scripts/download_kaggle_dataset.py \
  --dataset "lkerkarabulut/rplan-dataset2025" \
  --integrate-rag \
  --output data/datasets

# Списки доступных датасетов
python scripts/download_kaggle_dataset.py --list
```

---

## 📦 Поддерживаемые форматы

| Формат | Статус | Примечание |
|---|---|---|
| **PDF** | ✅ | Используется pdfplumber для экстракции текста |
| **TXT** | ✅ | Обработка как есть |
| **Markdown (.md)** | ✅ | Сохраняет структуру форматирования |
| **PNG/JPG** | 🔄 | Требуется OCR интеграция (pytesseract) |
| **CSV** | ⚠️ | Поддержка планируется |
| **DXF** | ⚠️ | Требуется специальный парсер |

---

## 🎯 Рекомендуемые датасеты

### 🏢 Архитектура и планы этажей

| Датасет | Размер | Описание |
|---|---|---|
| `lkerkarabulut/rplan-dataset2025` | ~500 MB | Floor plans, DXF файлы, 10K+ примеров |
| `jkuler/lol-buildings` | ~200 MB | Архитектурные стили, фасады, 5K+ изображений |
| `titir2/architectural-heritage-elements` | ~100 MB | Элементы наследия, орнаменты |

### 🏗️ Строительство

| Датасет | Размер | Описание |
|---|---|---|
| `devanshkhandelwal/construction-site-images` | ~1 GB | Фото площадок, документация |
| `zahidshakoor/construction-cost-dataset` | ~50 MB | Данные о стоимости работ |

### 📐 Инженерные справочники

| Датасет | Размер | Описание |
|---|---|---|
| `rtatman/earthquake-database` | ~100 MB | Сейсмические нормы |
| `russellyates/weather-as-dataframes` | ~500 MB | Климатические данные |

---

## 💻 Использование скриптов

### Основной скрипт: `download_kaggle_dataset.py`

```bash
# Базовое использование
python scripts/download_kaggle_dataset.py

# С параметрами
python scripts/download_kaggle_dataset.py \
  --dataset "username/dataset-name" \
  --output /path/to/output \
  --integrate-rag \
  --no-normalize

# Список датасетов
python scripts/download_kaggle_dataset.py --list
```

**Параметры:**

| Флаг | Тип | По умолчанию | Описание |
|---|---|---|---|
| `--dataset` | str | `lkerkarabulut/rplan-dataset2025` | ID датасета на Kaggle |
| `--output` | str | `data/datasets` | Директория для сохранения |
| `--integrate-rag` | bool | `True` | Индексировать в ChromaDB |
| `--no-normalize` | bool | `False` | Пропустить нормализацию текста |
| `--list` | bool | `False` | Показать популярные датасеты |

### Быстрый пример: `examples/quick_kaggle_load.py`

```bash
python examples/quick_kaggle_load.py
```

Загружает датасет и показывает содержимое.

---

## 🔍 Как это работает

### Поток обработки

```
Kaggle Dataset
    ↓
[download_kaggle_dataset.py]
    ├── Скачивание файлов
    ├── Организация по типам (PDF, TXT, MD)
    └── Сохранение в data/datasets/
         ↓
[integrate_dataset_into_rag()]
    ├── Парсинг файлов (pdfplumber, read)
    ├── Нормализация текста (remove extra spaces, special chars)
    ├── Добавление метаданных (source, file_type, timestamp)
    └── Индексация в ChromaDB
         ↓
✅ Доступно через RAG чат

```

### Интеграция в RAG пайплайн

Датасеты добавляются в существующую ChromaDB коллекцию:

```python
# src/rag_pipeline.py
rag = get_rag("simple")  # или "normbase"

# Документы из Kaggle автоматически добавляются
rag.add_document(
    content=text_from_dataset,
    metadata={
        "source": "kaggle_rplan_dataset2025",
        "file": "floor_plan_123.pdf",
        "date_added": "2026-07-03T12:30:00",
        "file_type": ".pdf"
    }
)
```

### Нормализация текста

Текст нормализуется перед индексацией:

1. **Удаление лишних пробелов** — `\s+` → ` `
2. **Удаление спецсимволов** — контрольные символы и NULL байты
3. **Trimming** — удаление пробелов в начале/конце

Это улучшает качество поиска в RAG.

---

## 📊 Примеры использования

### Пример 1: Загрузить датасет полов в план

```bash
# Скачать и индексировать планы этажей
python scripts/download_kaggle_dataset.py \
  --dataset "lkerkarabulut/rplan-dataset2025" \
  --integrate-rag

# В чате RAG теперь доступны запросы вроде:
# "Какие стандартные размеры комнат в жилых зданиях?"
# "Покажи примеры планов двухкомнатных квартир"
```

### Пример 2: Загрузить датасет со стоимостью

```bash
python scripts/download_kaggle_dataset.py \
  --dataset "zahidshakoor/construction-cost-dataset" \
  --integrate-rag

# Используется для обогащения фазы 2 (Сметы)
# Запросы: "Какова стоимость кирпичной кладки в Ташкенте?"
```

### Пример 3: Собственный датасет

```bash
# 1. Создайте датасет на Kaggle
# 2. Загрузите через скрипт
python scripts/download_kaggle_dataset.py \
  --dataset "your_username/your_dataset" \
  --integrate-rag

# 3. Проверьте через веб-интерфейс
# ./start_webapp.sh
# Перейдите на http://localhost:5173 → Чат → включите RAG
```

---

## ⚙️ Конфигурация

### Переменные окружения

```bash
# Kaggle API credentials
export KAGGLE_USERNAME=your_username
export KAGGLE_KEY=your_api_key

# Путь к ChromaDB
export CHROMA_DB_PATH=./data/chroma_db

# Путь к нормативам
export NORMATIVES_DIR=./data/normatives

# LM Studio URL для эмбеддингов
export LM_STUDIO_URL=http://localhost:1234/v1
```

### Размеры чанков в RAG

```python
# src/rag_pipeline.py
CHUNK_SIZE = 600  # символов
CHUNK_OVERLAP = 200  # для контекста
```

Рекомендуется не менять, если вы не переиндексируете всю БД.

---

## 🐛 Troubleshooting

### Проблема: "kagglehub not installed"

**Решение:**
```bash
pip install kagglehub --upgrade
```

### Проблема: "Authentication failed"

**Решение:**
```bash
# Удалить старую конфигурацию
rm ~/.kaggle/kaggle.json

# Пересоздать
kagglehub auth login
```

### Проблема: "Dataset download hangs"

**Решение:**
```bash
# Используйте timeout
timeout 300 python scripts/download_kaggle_dataset.py --dataset "..."

# Или загрузите файлы вручную с kaggle.com
```

### Проблема: "ChromaDB не индексирует файлы"

**Решение:**
```bash
# Проверьте логи
python scripts/download_kaggle_dataset.py \
  --dataset "your_dataset" \
  --integrate-rag  2>&1 | tee logs/kaggle_integration.log

# Проверьте формат файлов (*.pdf, *.txt, *.md)
# Проверьте права доступа на data/datasets/
```

---

## 📈 Оптимизация

### Кэширование

Kagglehub кэширует загруженные датасеты в `~/.cache/kagglehub/`. Повторная загрузка будет быстрой.

```bash
# Очистить кэш
rm -rf ~/.cache/kagglehub/
```

### Параллельная загрузка

Для больших датасетов используйте параллельную обработку:

```bash
# Модифицируйте скрипт для использования multiprocessing
# (рекомендуется только для >1GB датасетов)
```

### Контроль памяти

Для большых датасетов загружайте и индексируйте порциями:

```python
# Модифицируйте integrate_dataset_into_rag()
for file_path in files_to_process:
    # Обработка одного файла за раз
    # ChromaDB автоматически управляет памятью
    pass
```

---

## 🔐 Безопасность

### Проверка источников

Перед загрузкой датасета проверьте:

1. ✅ Авторство датасета (проверить профиль автора)
2. ✅ Лицензию (обычно CC0, CC-BY, CC-BY-SA)
3. ✅ Размер файлов (не превышает доступное место)
4. ✅ Структуру данных (соответствует ожиданиям)

### Изоляция данных

Все датасеты сохраняются в `data/datasets/` отдельно от исходных нормативов.

```bash
# Структура
data/
├── normatives/     # Исходные КМК/ШНК (не трогать)
└── datasets/       # Kaggle датасеты (можно удалить)
```

---

## 📝 FAQ

**Q: Могу ли я удалить датасет после индексации?**  
A: Да, файлы можно удалить. Вектора остаются в ChromaDB. Но если понадобятся исходные файлы — скачивайте заново.

**Q: Сколько датасетов я могу загрузить?**  
A: Столько, сколько вместится в ChromaDB (~10-50 GB на SSD). Помните о лимите памяти (20 GB на M3).

**Q: Как обновить датасет?**  
A: Удалите старые файлы, переиндексируйте ChromaDB, загрузите заново.

**Q: Поддерживаются ли OCR-документы (изображения)?**  
A: Пока нет встроенной поддержки, но pytesseract установлен. Требуется доработка.

**Q: Как использовать датасет в фазе 2 (Сметы)?**  
A: Загрузите датасет с расценками, модифицируйте `pricing_db.py` для использования новых данных.

---

## 🚀 Будущие улучшения

- [ ] Поддержка OCR для сканов и изображений
- [ ] Параллельная загрузка больших датасетов
- [ ] Web UI для управления датасетами (загрузка, удаление, переиндексация)
- [ ] Автоматическое обновление датасетов по расписанию
- [ ] Поддержка приватных датасетов Kaggle
- [ ] Интеграция с другими источниками (GitHub, Google Drive, S3)

---

*Документ актуален на 3 июля 2026*
