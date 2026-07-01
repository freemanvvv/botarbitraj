# ChatHouseDiffusion bridge

`predict_floorplan.py` — мост между Construction AI Copilot и
[ChatHouseDiffusion](https://github.com/ChatHouseDiffusion/chathousediffusion)
(arXiv:2410.11908). Наш бэкенд не тянет их зависимости (PyTorch, DGL,
Graphormer, свой форк `denoising_diffusion_pytorch`) — вместо этого
адаптер (`src/floorplan/chathousediffusion_adapter.py`) вызывает этот
скрипт подпроцессом внутри вашего собственного checkout'а их репозитория.

## Статус

✅ Интеграция на `main` (ветка `claude/russian-greeting-frc3kn` смержена)
✅ Протестировано на Apple Silicon (MPS) — 50 шагов диффузии за ~4 секунды
✅ Патчи для MPS применены в `setup.sh`

## Быстрый старт

```bash
# 1. Запустить setup.sh из папки chd_bridge
bash chd_bridge/setup.sh

# 2. Выставить переменные окружения
export CHD_PYTHON=$HOME/Projects/chathousediffusion/venv311/bin/python
export CHD_BRIDGE_SCRIPT=$HOME/Projects/chathousediffusion/predict_floorplan.py

# 3. Перезапустить backend
```

В веб-интерфейсе (вкладка «Моделирование → Архитектор») появится селектор
«Планировка квартир» → выберите «ChatHouseDiffusion».

## Ручная настройка (если setup.sh не подходит)

1. Склонируйте `chathousediffusion` и поставьте зависимости в отдельный venv
   с Python 3.11 (требуется для DGL):
   ```bash
   git clone https://github.com/ChatHouseDiffusion/chathousediffusion.git
   cd chathousediffusion
   python3.11 -m venv venv
   source venv/bin/activate
   pip install "numpy<2" "torch==2.2.0" "torchvision==0.17.0" \
     openai Pillow requests tqdm "dgl==2.1.0" \
     ema_pytorch "transformers<4.36" pandas einops \
     fuzzywuzzy "langchain_core<0.3" opencv-python
   ```

2. Запатчить код под MPS (см. `setup.sh` — раздел "Patching...")

3. Скачайте чекпоинт (1.1 GB):
   ```
   https://cloud.tsinghua.edu.cn/f/a01a8205be55462685fd/
   ```
   Распакуйте RAR в `predict_model/`.

4. Создайте `api_info.json`:
   ```json
   {"api_key": "***", "base_url": "http://localhost:1234/v1", "model": "local-model"}
   ```

5. Скопируйте bridge-скрипт:
   ```bash
   cp chd_bridge/predict_floorplan.py /path/to/chathousediffusion/
   ```

## Проверка

```bash
cd /path/to/chathousediffusion
CHD_DEBUG_DIR=/tmp/chd_debug uv run --python venv311/bin/python \
  python3 predict_floorplan.py <<< '{"mask_path":"/tmp/test_mask.png",\
  "width_m":8,"depth_m":6,"px_per_meter":7,"entry_side":"west",\
  "room_program":{"facade":["living","kitchen"],"wet":["bathroom"]},\
  "description":"тест"}'
```

Маску можно создать: `python3 -c "from PIL import Image, ImageDraw; \
i=Image.new('L',(64,64),255);d=ImageDraw.Draw(i);d.rectangle([4,4,60,60],\
fill=0);i.save('/tmp/test_mask.png')"`

## Известное ограничение: цветовые коллизии

Их `Trainer.predict()` возвращает уже готовое RGB-изображение (не индексы
классов). Их палитра не инъективна: LivingRoom/Entrance/Wall-in — одним
цветом. Мост восстанавливает индексы по ближайшему цвету и эвристикой
(по положению FrontDoor) разруливает коллизию гостиная/прихожая.
Остальные коллизии безобидны (5/6/7/8 → bedroom, 16/17 → стена/дверь).

Отладка: `CHD_DEBUG_DIR=/tmp/chd_debug` сохранит промпт, JSON графа и
`label_grid.npy`.

## Переменные окружения

| Переменная | Описание | По умолчанию |
|---|---|---|
| `CHD_PYTHON` | Путь к python в venv CHD | — |
| `CHD_BRIDGE_SCRIPT` | Путь к predict_floorplan.py | — |
| `CHD_LLM_BASE_URL` | URL OpenAI-совместимого LLM | из api_info.json |
| `CHD_LLM_API_KEY` | API ключ | из api_info.json |
| `CHD_LLM_MODEL` | Модель LLM | из api_info.json |
| `CHD_DEBUG_DIR` | Директория для отладочных файлов | (отключено) |

## Промпт

Промпт для LLM (через `prompt2json`) обогащён нормами КМК 2.08.01-89:
минимальные площади, ширины, зонирование, требования к окнам для каждой
комнаты. Синхронизирован с `src/floorplan/norms.py`.
