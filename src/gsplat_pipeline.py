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
import subprocess
import threading
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

def _run(job: dict, cmd: list, cwd: str = None, timeout: int = 7200) -> tuple[int, str]:
    """Запускает команду, стримит вывод в job["logs"]."""
    job["logs"].append(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.Popen(
            [str(c) for c in cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, cwd=cwd,
        )
        lines = []
        for line in proc.stdout:
            line = line.rstrip()
            lines.append(line)
            job["logs"].append(line)
        proc.wait(timeout=timeout)
        return proc.returncode, "\n".join(lines)
    except FileNotFoundError:
        msg = f"[Ошибка] Программа не найдена: {cmd[0]}. Убедитесь, что она установлена и доступна в PATH."
        job["logs"].append(msg)
        return -127, msg
    except Exception as e:
        msg = f"[Ошибка] {e}"
        job["logs"].append(msg)
        return -1, msg


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
        rc, _ = _run(job, [
            "ffmpeg", "-i", job["video_path"],
            "-vf", f"fps={fps}",
            "-q:v", "2",
            str(frames / "frame_%06d.jpg"),
            "-y",
        ])

        frame_count = len(list(frames.glob("*.jpg")))
        job["logs"].append(f"Извлечено кадров: {frame_count}")
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
Тип сцены: дорога, съёмка с движущегося автомобиля (forward-facing).

Оцени входные данные и дай рекомендации по 3 пунктам:
1. Достаточно ли кадров для качественной реконструкции?
2. Какие параметры COLMAP лучше для дорожной сцены?
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
        _run(job, [
            "colmap", "feature_extractor",
            "--database_path", db,
            "--image_path", str(frames),
            "--ImageReader.camera_model", "SIMPLE_RADIAL",
            "--ImageReader.single_camera", "1",
            "--SiftExtraction.use_gpu", "1",
            "--SiftExtraction.max_num_features", "8192",
        ])
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

        # Подсчёт зарегистрированных кадров
        registered = 0
        sparse_0 = sparse / "0"
        if sparse_0.exists():
            img_txt = sparse_0 / "images.txt"
            if img_txt.exists():
                content = img_txt.read_text()
                registered = sum(
                    1 for ln in content.splitlines()
                    if ln and not ln.startswith("#") and ".jpg" in ln
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
            f"""Результаты COLMAP для видео с дорожного регистратора:
- Кадров на входе: {frame_count}
- Зарегистрировано: {registered} ({pct}%)
- Тип сцены: forward-facing дорога

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

        # Попытка 1: Nerfstudio splatfacto (лучший для forward-facing)
        job["logs"].append("[Info] Попытка запуска через Nerfstudio (splatfacto)...")
        ns_rc, _ = _run(job, [
            "ns-train", "splatfacto",
            "--data", str(colmap),
            "--output-dir", str(output),
            "--max-num-iterations", "7000",
            "--viewer.quit-on-train-completion", "True",
        ], timeout=10800)

        if ns_rc == 0:
            plys = list(output.rglob("*.ply"))
            if plys:
                ply_path = str(max(plys, key=lambda p: p.stat().st_size))

        # Попытка 2: gsplat simple_trainer
        if not ply_path:
            job["logs"].append("[Info] Nerfstudio недоступен, пробую gsplat...")
            gs_rc, _ = _run(job, [
                "python", "-m", "gsplat.simple_trainer",
                "--data_dir", str(sparse_0),
                "--result_dir", str(output / "gsplat"),
                "--max_steps", "7000",
            ], timeout=10800)

            plys = list(output.rglob("*.ply"))
            if plys:
                ply_path = str(max(plys, key=lambda p: p.stat().st_size))

        if not ply_path:
            raise RuntimeError(
                "Обучение завершилось, но .ply файл не создан. "
                "Убедитесь, что Nerfstudio или gsplat установлены, "
                "и на сервере есть NVIDIA GPU с CUDA."
            )

        job["output_ply"] = ply_path
        job["progress"] = 95

        # LLM: финальный отчёт
        job["logs"].append("")
        job["logs"].append("[LLM] Генерирую финальный отчёт...")
        report = _llm(
            f"""Gaussian Splatting реконструкция завершена успешно!
Источник: видео с дорожного регистратора
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
