"""
Gaussian Splatting pipeline с LLM-оркестратором.
Цепочка: загрузка видео → ffmpeg (кадры) → COLMAP → gsplat/nerfstudio → .ply

LLM (локальный через LM Studio) участвует в каждом шаге:
  - рекомендует параметры на основе характеристик видео
  - анализирует результаты COLMAP
  - диагностирует ошибки на русском языке
  - генерирует финальный отчёт о сцене
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from .config import LM_STUDIO_BASE_URL, MODELS

GSPLAT_DATA_DIR = Path(__file__).parent.parent / "data" / "gsplat_projects"
GSPLAT_DATA_DIR.mkdir(parents=True, exist_ok=True)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# ─── LLM helper ──────────────────────────────────────────────

def _llm(prompt: str, model_id: str, max_tokens: int = 400) -> str:
    try:
        payload = {
            "model": model_id,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        r = requests.post(
            f"{LM_STUDIO_BASE_URL}/chat/completions",
            json=payload, timeout=90,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"[LLM недоступен: {e}]"


# ─── Shell command helper ─────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _run(job: dict, cmd: list, cwd: str = None, timeout: int = 7200,
         stall_warn: int = 20) -> tuple[int, str]:
    """Запускает команду, стримит вывод в job["logs"] в реальном времени.

    Отличия от наивного варианта — специально, чтобы ЛОВИТЬ зависания (жалоба
    «доходит до 5% и висит»: 5% — это как раз старт ffmpeg-извлечения кадров):

      • stdin=DEVNULL — ffmpeg/COLMAP не наследуют stdin процесса-сервера и не
        блокируются в ожидании ввода. Наследованный stdin — частая причина
        зависшего ffmpeg: он трактует данные из stdin как интерактивные
        команды ('q' и т.п.) и может замереть;
      • вывод читается посимвольно и разбивается И по '\\n', И по '\\r':
        ffmpeg пишет прогресс через '\\r' (перезапись одной строки), поэтому
        обычный построчный итубор не отдавал НИ ОДНОЙ строки до конца
        кодирования — лог выглядел «застрявшим на 5%», хотя работа шла;
      • сторож простоя: если stall_warn секунд нет НИКАКОГО вывода — в лог
        падает отметка «жив, простой N c», так что сразу видно, на какой
        команде и как долго висим;
      • жёсткий таймаут реально убивает процесс. Раньше timeout применялся к
        wait() ПОСЛЕ вычитывания stdout — у зависшего процесса stdout не
        закрывается, цикл чтения не заканчивается, и до w() дело не доходило:
        зависание было вечным и бесследным.
    """
    printable = " ".join(str(c) for c in cmd)
    job["logs"].append(f"[{_ts()}] $ {printable}")
    try:
        # Читаем БАЙТАМИ (не text=True): в текстовом режиме Python включает
        # universal-newlines и превращает '\r' в '\n', из-за чего строку
        # прогресса ffmpeg нельзя отличить от обычной и не получается
        # тротлить её — лог захлебнётся сотнями строк прогресса.
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
        )
    except FileNotFoundError:
        msg = f"[Ошибка] Программа не найдена: {cmd[0]}. Убедитесь, что она установлена и доступна в PATH."
        job["logs"].append(f"[{_ts()}] {msg}")
        return -127, msg
    except Exception as e:
        msg = f"[Ошибка] {e}"
        job["logs"].append(f"[{_ts()}] {msg}")
        return -1, msg

    job["logs"].append(f"[{_ts()}] ▶ запущен pid={proc.pid}")
    start = time.monotonic()
    last_out = [start]
    lines: list[str] = []
    done = threading.Event()
    throttle = [0.0]  # '\r'-прогресс логируем не чаще раза в секунду

    def flush(text: str, overwrite: bool):
        text = text.rstrip()
        if not text:
            return
        now = time.monotonic()
        last_out[0] = now
        if overwrite:  # строка прогресса ffmpeg — не спамим лог
            if now - throttle[0] < 1.0:
                return
            throttle[0] = now
        lines.append(text)
        job["logs"].append(f"[{_ts()}] {text}")

    def reader():
        # Читаем ЧАНКАМИ через read1 (возвращает то, что уже доступно, без
        # ожидания заполнения буфера — прогресс виден сразу), а не по одному
        # байту: read(1) на бурстовом выводе COLMAP делал syscall+GIL на КАЖДЫЙ
        # байт и мог подвесить event-loop uvicorn (HTTP-опросы → «Failed to
        # fetch»). Разбиваем накопленный буфер по '\n' и '\r'.
        buf = bytearray()
        try:
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                buf += chunk
                start = 0
                for i, byte in enumerate(buf):
                    if byte == 10 or byte == 13:  # \n / \r
                        flush(bytes(buf[start:i]).decode("utf-8", "replace"), byte == 13)
                        start = i + 1
                if start:
                    del buf[:start]
        finally:
            flush(bytes(buf).decode("utf-8", "replace"), False)
            done.set()

    threading.Thread(target=reader, daemon=True).start()

    poll = max(1, min(stall_warn, 5))
    while not done.wait(timeout=poll):
        now = time.monotonic()
        if now - start > timeout:
            job["logs"].append(f"[{_ts()}] ⏱ таймаут {timeout} c — принудительно завершаю pid={proc.pid}")
            proc.kill()
            done.wait(timeout=10)
            return -9, "\n".join(lines)
        if now - last_out[0] >= stall_warn:
            job["logs"].append(
                f"[{_ts()}] ⏳ нет вывода {int(now - last_out[0])} c "
                f"(всего {int(now - start)} c), pid={proc.pid} — процесс ещё жив"
            )
            last_out[0] = now  # следующая отметка через stall_warn, а не спамом

    proc.wait()
    dur = time.monotonic() - start
    job["logs"].append(f"[{_ts()}] ✔ завершено rc={proc.returncode} за {dur:.1f} c")
    return proc.returncode, "\n".join(lines)


# ─── Обучение: выбор трейнера (CUDA vs Mac/Brush) ─────────────

def _has_nvidia_gpu() -> bool:
    """CUDA-трейнеры (Nerfstudio/gsplat) требуют NVIDIA GPU. Наличие nvidia-smi
    — надёжный признак. На Mac его нет → обучаем через Brush (Metal/wgpu)."""
    return shutil.which("nvidia-smi") is not None


def _find_brush() -> Optional[str]:
    """Путь к бинарю Brush: сперва BRUSH_BIN, затем PATH."""
    return os.environ.get("BRUSH_BIN") or shutil.which("brush")


def _latest_ply(output: Path) -> Optional[str]:
    plys = list(output.rglob("*.ply"))
    return str(max(plys, key=lambda p: p.stat().st_size)) if plys else None


def _prepare_brush_dataset(job_dir: Path, frames: Path, sparse: Path) -> Path:
    """Собирает COLMAP-датасет в раскладке, которую ждёт Brush: images/ и sparse/
    под одним корнем. Кадры не копируем — symlink (fallback на копию, если ФС
    без симлинков)."""
    root = job_dir / "brush_data"
    root.mkdir(exist_ok=True)
    for name, target in (("images", frames), ("sparse", sparse)):
        link = root / name
        if link.is_symlink() or link.exists():
            continue
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            shutil.copytree(target, link)
    return root


def _train_with_brush(job: dict, job_dir: Path, frames: Path, sparse: Path,
                      output: Path, steps: int = 7000) -> Optional[str]:
    """Обучение Gaussian Splatting через Brush — кросс-платформенный трейнер на
    Rust/wgpu, работает на Apple Silicon (Metal) БЕЗ NVIDIA/CUDA. Даёт .ply,
    совместимый с существующим вьюером.

    Флаги CLI Brush зависят от версии, поэтому команду можно переопределить
    переменной окружения BRUSH_CMD с плейсхолдерами {bin} {data} {steps} {ply}
    {out_dir} — так пользователь подстроит вызов под свою сборку без правки
    кода. Возвращает путь к .ply или None (тогда пайплайн отдаёт понятную
    ошибку с инструкцией по установке)."""
    brush = _find_brush()
    if not brush:
        job["logs"].append(
            f"[{_ts()}] [Brush] Бинарь brush не найден. Установите Brush — трейнер "
            "3DGS на Metal/wgpu, работает на Mac без NVIDIA:"
        )
        job["logs"].append("           • релиз: github.com/ArthurBrussee/brush (или cargo install)")
        job["logs"].append("           • путь можно задать: BRUSH_BIN=/путь/к/brush")
        return None

    data = _prepare_brush_dataset(job_dir, frames, sparse)
    out_ply = output / "brush" / "model.ply"
    out_ply.parent.mkdir(parents=True, exist_ok=True)

    template = os.environ.get(
        "BRUSH_CMD",
        "{bin} {data} --total-steps {steps} --export-path {ply}",
    )
    cmd = shlex.split(template.format(
        bin=brush, data=str(data), steps=steps,
        ply=str(out_ply), out_dir=str(out_ply.parent),
    ))
    job["logs"].append(f"[{_ts()}] [Brush] Обучение на Metal/wgpu (Mac-совместимо), шагов: {steps}")
    _run(job, cmd, timeout=10800)
    return _latest_ply(output)


# ─── Public API ───────────────────────────────────────────────

def create_job(video_path: str, project_name: str,
               fps: float = 1.0, model_id: str = None) -> str:
    job_id = str(uuid.uuid4())[:8]
    job_dir = GSPLAT_DATA_DIR / job_id
    job_dir.mkdir(parents=True)

    mid = model_id or list(MODELS.values())[0]["id"]
    job = {
        "id": job_id,
        "project_name": project_name,
        "video_path": video_path,
        "fps": fps,
        "model_id": mid,
        "status": "pending",
        "step": "Ожидание запуска",
        "progress": 0,
        "logs": [],
        "llm_analysis": {},
        "output_ply": None,
        "created_at": datetime.now().isoformat(),
        "job_dir": str(job_dir),
    }
    with _jobs_lock:
        _jobs[job_id] = job
    return job_id


def start_job(job_id: str):
    thread = threading.Thread(target=_pipeline, args=(job_id,), daemon=True)
    thread.start()


def get_job(job_id: str) -> Optional[dict]:
    return _jobs.get(job_id)


def list_jobs() -> list:
    with _jobs_lock:
        jobs = list(_jobs.values())
    return sorted(jobs, key=lambda j: j["created_at"], reverse=True)


def list_models() -> list[dict]:
    """Возвращает готовые .ply файлы из всех завершённых задач."""
    result = []
    for job in list_jobs():
        ply = job.get("output_ply")
        if ply and Path(ply).exists():
            result.append({
                "job_id": job["id"],
                "project_name": job["project_name"],
                "ply_path": ply,
                "ply_filename": Path(ply).name,
                "created_at": job["created_at"],
                "size_mb": round(Path(ply).stat().st_size / 1024 / 1024, 1),
            })
    return result


# ─── Pipeline ─────────────────────────────────────────────────

def _hdr(job, title):
    job["logs"].append("")
    job["logs"].append("=" * 55)
    job["logs"].append(title)
    job["logs"].append("=" * 55)


def _pipeline(job_id: str):
    job = _jobs[job_id]
    job_dir  = Path(job["job_dir"])
    frames   = job_dir / "frames"
    colmap   = job_dir / "colmap"
    sparse   = colmap / "sparse"
    output   = job_dir / "output"
    for d in (frames, colmap, sparse, output):
        d.mkdir(exist_ok=True)

    mid = job["model_id"]

    try:
        # ══════════════════════════════════════════════════
        # ШАГ 1 — Извлечение кадров через ffmpeg
        # ══════════════════════════════════════════════════
        job["status"] = "extracting"
        job["progress"] = 5
        _hdr(job, "ШАГ 1/3: Извлечение кадров (ffmpeg)")

        fps = job["fps"]
        vp = Path(job["video_path"])
        size_mb = vp.stat().st_size / 1024 / 1024 if vp.exists() else 0
        job["logs"].append(
            f"[{_ts()}] Видео: {vp.name} ({size_mb:.1f} МБ), существует={vp.exists()}, fps извлечения={fps}"
        )
        # -nostdin: не читать stdin (иначе ffmpeg может «зависнуть» на 5%,
        # трактуя унаследованный stdin как интерактивный ввод).
        rc, _ = _run(job, [
            "ffmpeg", "-nostdin",
            "-i", job["video_path"],
            "-vf", f"fps={fps}",
            "-q:v", "2",
            "-y",
            str(frames / "frame_%06d.jpg"),
        ])
        if rc != 0:
            job["logs"].append(f"[{_ts()}] ⚠ ffmpeg вернул код {rc} — кадры могли извлечься не полностью")

        frame_count = len(list(frames.glob("*.jpg")))
        job["logs"].append(f"[{_ts()}] Извлечено кадров: {frame_count}")
        job["progress"] = 20

        if frame_count < 10:
            raise RuntimeError(
                f"Слишком мало кадров ({frame_count}). "
                "Увеличьте FPS извлечения или проверьте видеофайл."
            )

        # LLM: оценка входных данных и рекомендации
        job["logs"].append("")
        job["logs"].append("[LLM] Анализирую входные данные...")
        analysis = _llm(
            f"""Ты — эксперт по фотограмметрии и 3D Gaussian Splatting.
Видеофайл: {Path(job['video_path']).name}
FPS извлечения: {fps}
Кадров извлечено: {frame_count}
Тип съёмки: видео (напр. облёт дрона вокруг объекта, обход здания/участка или проезд).

Оцени входные данные и дай рекомендации по 3 пунктам:
1. Достаточно ли кадров для качественной реконструкции?
2. Какие параметры COLMAP лучше под такой тип съёмки?
3. Чего ожидать от итоговой 3D-модели?
Ответ кратко по-русски (3-5 предложений).""", mid
        )
        job["llm_analysis"]["extraction"] = analysis
        job["logs"].append(f"[LLM] {analysis}")

        # ══════════════════════════════════════════════════
        # ШАГ 2 — COLMAP
        # ══════════════════════════════════════════════════
        job["status"] = "colmap"
        job["progress"] = 25
        _hdr(job, "ШАГ 2/3: COLMAP — позиции камер")

        db = str(colmap / "database.db")

        # Извлечение признаков
        job["logs"].append("[COLMAP] feature_extractor...")
        fe_cmd = [
            "colmap", "feature_extractor",
            "--database_path", db,
            "--image_path", str(frames),
            "--ImageReader.camera_model", "SIMPLE_RADIAL",
            "--ImageReader.single_camera", "1",
            "--SiftExtraction.max_num_features", "8192",
        ]
        # --SiftExtraction.use_gpu передаём ТОЛЬКО при NVIDIA: COLMAP на Mac
        # (Homebrew) собран без CUDA-SIFT, и эта опция там не распознаётся —
        # раньше это валило feature_extractor (rc=1, база пустая → «0 кадров»).
        # Без флага COLMAP сам выбирает доступный бэкенд (на Mac — SiftGPU через
        # OpenGL/Metal или CPU).
        if _has_nvidia_gpu():
            fe_cmd += ["--SiftExtraction.use_gpu", "1"]
        _run(job, fe_cmd)
        job["progress"] = 35

        # Матчинг: sequential лучше для видео
        job["logs"].append("[COLMAP] sequential_matcher...")
        _run(job, [
            "colmap", "sequential_matcher",
            "--database_path", db,
            "--SequentialMatching.overlap", "15",
            "--SequentialMatching.quadratic_overlap", "1",
        ])
        job["progress"] = 50

        # Разреженная реконструкция
        job["logs"].append("[COLMAP] mapper...")
        _run(job, [
            "colmap", "mapper",
            "--database_path", db,
            "--image_path", str(frames),
            "--output_path", str(sparse),
            "--Mapper.num_threads", "4",
            "--Mapper.init_min_tri_angle", "4",
        ])
        job["progress"] = 65

        # COLMAP пишет модель в БИНАРНОМ виде (cameras.bin/images.bin/points3D.bin),
        # а не в .txt — поэтому конвертируем в TXT, чтобы посчитать
        # зарегистрированные кадры (иначе images.txt нет и всегда «0 кадров»,
        # даже когда реконструкция удалась). Заодно .txt рядом не мешает.
        sparse_0 = sparse / "0"
        if sparse_0.exists() and not (sparse_0 / "images.txt").exists():
            _run(job, [
                "colmap", "model_converter",
                "--input_path", str(sparse_0),
                "--output_path", str(sparse_0),
                "--output_type", "TXT",
            ])

        # Подсчёт зарегистрированных кадров
        registered = 0
        if sparse_0.exists():
            img_txt = sparse_0 / "images.txt"
            if img_txt.exists():
                content = img_txt.read_text()
                # в images.txt на каждое изображение — 2 строки (вторая = точки);
                # строка регистрации оканчивается именем файла .jpg
                registered = sum(
                    1 for ln in content.splitlines()
                    if ln and not ln.startswith("#") and ln.rstrip().lower().endswith(".jpg")
                )

        pct = int(100 * registered / frame_count) if frame_count else 0
        job["logs"].append(f"COLMAP: зарегистрировано {registered}/{frame_count} кадров ({pct}%)")

        if registered < 5:
            raise RuntimeError(
                f"COLMAP зарегистрировал только {registered} кадров ({pct}%). "
                "Видео может быть слишком размытым, быстрым или сцена однородная (асфальт без ориентиров). "
                "Попробуйте снизить FPS и убедитесь, что в кадре есть статичные объекты."
            )

        # LLM: анализ COLMAP
        job["logs"].append("")
        job["logs"].append("[LLM] Анализирую результаты COLMAP...")
        colmap_analysis = _llm(
            f"""Результаты COLMAP для видео (напр. облёт дрона / съёмка объекта):
- Кадров на входе: {frame_count}
- Зарегистрировано: {registered} ({pct}%)
- Тип съёмки: видео с непрерывной траектории (облёт/обход/проезд)

Дай оценку и рекомендации для gsplat:
1. Насколько хорошо прошла реконструкция ({pct}% — это много или мало)?
2. Рекомендуемое число итераций обучения gsplat (диапазон 3000–30000)?
3. Стоит ли беспокоиться о качестве?
Ответ по-русски, 3-4 предложения.""", mid
        )
        job["llm_analysis"]["colmap"] = colmap_analysis
        job["logs"].append(f"[LLM] {colmap_analysis}")

        # ══════════════════════════════════════════════════
        # ШАГ 3 — Обучение Gaussian Splatting
        # ══════════════════════════════════════════════════
        job["status"] = "training"
        job["progress"] = 70
        _hdr(job, "ШАГ 3/3: Обучение Gaussian Splatting")

        ply_path = None
        has_gpu = _has_nvidia_gpu()

        if has_gpu:
            # На NVIDIA/CUDA — привычные трейнеры (качество/скорость выше).
            job["logs"].append(f"[{_ts()}] [Info] Обнаружен NVIDIA GPU. Пробую Nerfstudio (splatfacto)...")
            ns_rc, _ = _run(job, [
                "ns-train", "splatfacto",
                "--data", str(colmap),
                "--output-dir", str(output),
                "--max-num-iterations", "7000",
                "--viewer.quit-on-train-completion", "True",
            ], timeout=10800)
            if ns_rc == 0:
                ply_path = _latest_ply(output)

            if not ply_path:
                job["logs"].append(f"[{_ts()}] [Info] Nerfstudio недоступен, пробую gsplat...")
                _run(job, [
                    "python", "-m", "gsplat.simple_trainer",
                    "--data_dir", str(sparse_0),
                    "--result_dir", str(output / "gsplat"),
                    "--max_steps", "7000",
                ], timeout=10800)
                ply_path = _latest_ply(output)
        else:
            job["logs"].append(
                f"[{_ts()}] [Info] NVIDIA GPU не найден (напр. Mac) — CUDA-трейнеры "
                "(Nerfstudio/gsplat) недоступны, обучаю через Brush (Metal/wgpu)."
            )

        # Brush — трейнер на Metal/wgpu: основной путь на Mac и запасной на любой
        # машине, если CUDA-трейнеры не дали .ply.
        if not ply_path:
            job["logs"].append(f"[{_ts()}] [Info] Запускаю Brush (кросс-платформенный трейнер 3DGS)...")
            ply_path = _train_with_brush(job, job_dir, frames, sparse, output)

        if not ply_path:
            raise RuntimeError(
                "Обучение не создало .ply. На Mac (без NVIDIA) установите Brush "
                "(github.com/ArthurBrussee/brush) и COLMAP; на машине с NVIDIA — "
                "Nerfstudio или gsplat (CUDA). Подробности в логе выше."
            )

        job["output_ply"] = ply_path
        job["progress"] = 95

        # LLM: финальный отчёт
        job["logs"].append("")
        job["logs"].append("[LLM] Генерирую финальный отчёт...")
        report = _llm(
            f"""Gaussian Splatting реконструкция завершена успешно!
Источник: видео (облёт дрона / съёмка объекта)
Использовано кадров: {registered}
Файл модели: {Path(ply_path).name} ({Path(ply_path).stat().st_size // 1024 // 1024} МБ)

Напиши профессиональный итоговый отчёт по-русски (4-6 предложений):
1. Что представляет собой реконструированная сцена?
2. Оценка качества (на основе % зарегистрированных кадров)
3. Как работать с .ply файлом дальше (очистка в SuperSplat и т.д.)
4. Практические рекомендации""", mid, max_tokens=600
        )
        job["llm_analysis"]["report"] = report
        job["logs"].append("")
        job["logs"].append("[LLM] ФИНАЛЬНЫЙ ОТЧЁТ:")
        job["logs"].append(report)

        job["status"] = "done"
        job["step"] = "Готово"
        job["progress"] = 100
        job["logs"].append("")
        job["logs"].append("✅ Пайплайн завершён успешно!")

    except Exception as exc:
        err = str(exc)
        job["logs"].append("")
        job["logs"].append(f"❌ ОШИБКА: {err}")

        # LLM диагностирует ошибку
        try:
            diag = _llm(
                f"""В пайплайне Gaussian Splatting возникла ошибка:
{err}

Объясни причину и дай конкретные рекомендации по исправлению на русском языке.
Будь практичным — 2-4 предложения.""", mid
            )
            job["llm_analysis"]["error_diagnosis"] = diag
            job["logs"].append("")
            job["logs"].append(f"[LLM] Диагностика: {diag}")
        except Exception:
            pass

        job["status"] = "error"
        job["step"] = f"Ошибка: {err[:120]}"
        job["progress"] = 0
