"""Тесты Mac-пути обучения (Brush) в gsplat-пайплайне: выбор трейнера без
NVIDIA, сборка COLMAP-датасета под Brush, запуск фейкового brush и понятное
сообщение, если brush не установлен. Реальный Brush не нужен — подменяем
бинарь скриптом."""
import os
import stat
from pathlib import Path

import src.gsplat_pipeline as gp


def test_find_brush_prefers_env(monkeypatch):
    monkeypatch.setenv("BRUSH_BIN", "/opt/brush/brush")
    assert gp._find_brush() == "/opt/brush/brush"


def test_find_brush_none_when_absent(monkeypatch):
    monkeypatch.delenv("BRUSH_BIN", raising=False)
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)
    assert gp._find_brush() is None


def test_prepare_brush_dataset_links_images_and_sparse(tmp_path):
    job_dir = tmp_path / "job"
    frames = job_dir / "frames"
    sparse = job_dir / "colmap" / "sparse"
    (sparse / "0").mkdir(parents=True)
    frames.mkdir(parents=True)
    (frames / "frame_000001.jpg").write_bytes(b"x")

    root = gp._prepare_brush_dataset(job_dir, frames, sparse)
    assert (root / "images").exists()
    assert (root / "sparse" / "0").exists()
    # images указывает на кадры (через symlink или копию)
    assert (root / "images" / "frame_000001.jpg").exists()


def test_train_with_brush_missing_binary_logs_install_hint(tmp_path, monkeypatch):
    monkeypatch.delenv("BRUSH_BIN", raising=False)
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)
    job = {"logs": []}
    out = gp._train_with_brush(job, tmp_path, tmp_path / "frames", tmp_path / "sparse", tmp_path / "out")
    assert out is None
    joined = "\n".join(job["logs"])
    assert "brush" in joined.lower() and "BRUSH_BIN" in joined


def test_train_with_brush_runs_fake_binary_and_returns_ply(tmp_path, monkeypatch):
    """Фейковый brush (шелл-скрипт) пишет .ply в out_dir — функция должна его
    найти и вернуть. Заодно проверяем, что команда собирается из BRUSH_CMD."""
    job_dir = tmp_path / "job"
    frames = job_dir / "frames"; frames.mkdir(parents=True)
    (frames / "f.jpg").write_bytes(b"x")
    sparse = job_dir / "colmap" / "sparse" / "0"; sparse.mkdir(parents=True)
    output = job_dir / "output"; output.mkdir()

    fake = tmp_path / "brush_fake.sh"
    # печатает прогресс через \r (проверяем заодно стриминг), создаёт .ply по --export-path
    fake.write_text(
        "#!/bin/bash\n"
        "printf 'step 100\\rstep 200\\n'\n"
        "ply=\"\"\n"
        "while [ $# -gt 0 ]; do if [ \"$1\" = \"--export-path\" ]; then ply=\"$2\"; fi; shift; done\n"
        "mkdir -p \"$(dirname \"$ply\")\"\n"
        "printf 'ply\\nformat ascii 1.0\\n' > \"$ply\"\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("BRUSH_BIN", str(fake))
    monkeypatch.setenv("BRUSH_CMD", "{bin} {data} --steps {steps} --export-path {ply}")
    job = {"logs": []}
    out = gp._train_with_brush(job, job_dir, frames,
                               job_dir / "colmap" / "sparse", output, steps=200)
    assert out is not None
    assert Path(out).exists() and out.endswith(".ply")
    joined = "\n".join(job["logs"])
    assert "Brush" in joined and "✔ завершено rc=0" in joined


def test_has_nvidia_gpu_reflects_nvidia_smi(monkeypatch):
    monkeypatch.setattr(gp.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None)
    assert gp._has_nvidia_gpu() is True
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)
    assert gp._has_nvidia_gpu() is False
