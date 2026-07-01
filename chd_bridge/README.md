# ChatHouseDiffusion bridge

`predict_floorplan.py` — мост между Construction AI Copilot и
[ChatHouseDiffusion](https://github.com/ChatHouseDiffusion/chathousediffusion)
(arXiv:2410.11908). Наш бэкенд не тянет их зависимости (PyTorch, DGL,
Graphormer, свой форк `denoising_diffusion_pytorch`) — вместо этого
адаптер (`src/floorplan/chathousediffusion_adapter.py`) вызывает этот
скрипт подпроцессом внутри вашего собственного checkout'а их репозитория.

## Настройка (на вашей стороне, не в этом репозитории)

1. Склонируйте `chathousediffusion` и поставьте их зависимости в
   отдельный venv (см. их `requirements.txt`) — Linux+CUDA рекомендуется
   их авторами; на Apple Silicon часть кода (CUDA-заточенные места в
   `denoising_diffusion_pytorch`) может потребовать правки.
2. Скачайте их чекпоинт (Tsinghua Cloud, ссылка в их README) и распакуйте
   в `predict_model/` внутри их checkout'а.
3. Создайте `api_info.json` в корне их checkout'а (или задайте переменные
   окружения `CHD_LLM_BASE_URL`/`CHD_LLM_API_KEY`/`CHD_LLM_MODEL` — удобно
   для локальных LLM вроде LM Studio/Ollama, где ключ не нужен):
   ```json
   {"api_key": "...", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"}
   ```
4. Скопируйте этот файл (`predict_floorplan.py`) в корень их checkout'а
   (туда же, где `predict.py`, `ui.py`, `prompt2json/`).
5. На стороне Construction AI Copilot задайте переменные окружения:
   ```
   CHD_PYTHON=/path/to/chathousediffusion/venv/bin/python
   CHD_BRIDGE_SCRIPT=/path/to/chathousediffusion/predict_floorplan.py
   ```
   Если не заданы (или файл не существует) — `floorplan_mode="chathousediffusion"`
   молча откатывается на детерминированный солвер, как и при недоступной
   локальной LLM в режиме `"neural"`.

## Известное ограничение: цветовые коллизии

Их `Trainer.predict()` возвращает уже готовое RGB-изображение (не индексы
классов) — см. подробный докстринг в начале `predict_floorplan.py`. Их
собственная палитра не инъективна: несколько классов комнат окрашены
одинаково (например LivingRoom/Entrance/Wall-in — все одним цветом).
Мост восстанавливает индексы классов по ближайшему цвету и разруливает
самую значимую коллизию (гостиная/прихожая) эвристикой по положению
входа и двери (`FrontDoor`); остальные коллизии безобидны для нашей
таксономии (см. `src/floorplan/vectorize.py:CHATHOUSEDIFFUSION_CLASS_TO_TYPE`
— несколько их классов и так сводятся к одному нашему типу).

Отладка: переменная окружения `CHD_DEBUG_DIR=/path/to/dir` сохранит туда
использованный текстовый промпт, JSON графа комнат от LLM и сырую карту
классов (`label_grid.npy`) для ручной проверки.
