"""Тесты режима «Местность» (фотограмметрия OpenDroneMap) в gsplat-пайплайне:
определение способа запуска ODM, ветвление create_job/_pipeline на terrain,
запуск фейкового ODM (шелл-скрипт) → ортофото + меш, понятная ошибка если ODM
не установлен. Реальный OpenDroneMap/Docker не нужен — подменяем."""
import shlex
import stat
from pathlib import Path

import src.gsplat_pipeline as gp


def test_create_job_stores_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(gp, "GSPLAT_DATA_DIR", tmp_path)
    jid = gp.create_job("/v.mp4", "proj", mode="terrain")
    assert gp.get_job(jid)["mode"] == "terrain"
    jid2 = gp.create_job("/v.mp4", "proj2")  # default
    assert gp.get_job(jid2)["mode"] == "object"
    jid3 = gp.create_job("/v.mp4", "proj3", mode="bogus")  # невалидный → object
    assert gp.get_job(jid3)["mode"] == "object"


def test_find_odm_runner_prefers_custom(monkeypatch):
    monkeypatch.setenv("ODM_CMD", "myodm {datasets} {project}")
    assert gp._find_odm_runner() == ("custom", "myodm {datasets} {project}")


def test_find_odm_runner_docker(monkeypatch):
    monkeypatch.delenv("ODM_CMD", raising=False)
    monkeypatch.setattr(gp.shutil, "which",
                        lambda name: "/usr/bin/docker" if name == "docker" else None)
    assert gp._find_odm_runner() == ("docker", "opendronemap/odm")


def test_find_odm_runner_none(monkeypatch):
    monkeypatch.delenv("ODM_CMD", raising=False)
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)
    assert gp._find_odm_runner() is None


def test_run_terrain_missing_odm_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ODM_CMD", raising=False)
    monkeypatch.setattr(gp.shutil, "which", lambda name: None)
    frames = tmp_path / "frames"; frames.mkdir()
    (frames / "f.jpg").write_bytes(b"x")
    job = {"logs": [], "project_name": "p"}
    try:
        gp._run_terrain(job, tmp_path, frames)
        assert False, "должно было упасть"
    except RuntimeError as e:
        assert "OpenDroneMap" in str(e)
    assert any("docker pull opendronemap/odm" in l for l in job["logs"])


def test_run_terrain_runs_fake_odm_and_finds_outputs(tmp_path, monkeypatch):
    """Фейковый ODM (через ODM_CMD) создаёт ортофото и меш в ожидаемых папках —
    функция должна их найти, записать output_ortho и упаковать меш в zip."""
    frames = tmp_path / "frames"; frames.mkdir()
    for i in range(3):
        (frames / f"frame_{i:06d}.jpg").write_bytes(b"x")

    # скрипт создаёт reconstruction/odm_orthophoto/odm_orthophoto.png и
    # reconstruction/odm_texturing/odm_textured_model_geo.obj под {datasets}
    fake = tmp_path / "odm_fake.sh"
    fake.write_text(
        "#!/bin/bash\n"
        "ds=\"$1\"\n"
        "mkdir -p \"$ds/reconstruction/odm_orthophoto\"\n"
        "printf 'PNG' > \"$ds/reconstruction/odm_orthophoto/odm_orthophoto.png\"\n"
        "mkdir -p \"$ds/reconstruction/odm_texturing\"\n"
        "printf 'o mesh' > \"$ds/reconstruction/odm_texturing/odm_textured_model_geo.obj\"\n"
        "printf 'mtl' > \"$ds/reconstruction/odm_texturing/model.mtl\"\n"
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    monkeypatch.setenv("ODM_CMD", f"{fake} {{datasets}} {{project}}")
    monkeypatch.setenv("ODM_OPTS", "")  # без лишних флагов у фейка
    job = {"logs": [], "project_name": "proj", "progress": 0}
    gp._run_terrain(job, tmp_path, frames)

    assert job["output_ortho"] and Path(job["output_ortho"]).exists()
    assert job["output_ortho"].endswith(".png")
    assert job["output_mesh"] and Path(job["output_mesh"]).exists()
    assert job["output_mesh"].endswith(".zip")


def test_run_terrain_docker_command_shape(tmp_path, monkeypatch):
    """В docker-режиме команда должна монтировать datasets и передавать
    --project-path /datasets <project>. Перехватываем _run вместо запуска."""
    monkeypatch.delenv("ODM_CMD", raising=False)
    monkeypatch.setattr(gp.shutil, "which",
                        lambda name: "/usr/bin/docker" if name == "docker" else None)
    frames = tmp_path / "frames"; frames.mkdir()
    (frames / "frame_000001.jpg").write_bytes(b"x")

    captured = {}

    def fake_run(job, cmd, cwd=None, timeout=7200, stall_warn=20):
        captured["cmd"] = cmd
        return 0, ""

    monkeypatch.setattr(gp, "_run", fake_run)
    job = {"logs": [], "project_name": "proj", "progress": 0}
    try:
        gp._run_terrain(job, tmp_path, frames)
    except RuntimeError:
        pass  # ODM ничего не создал (фейковый _run) — ок, нам важна команда
    cmd = captured["cmd"]
    assert cmd[0] == "docker" and "run" in cmd
    assert "opendronemap/odm" in cmd
    assert "--project-path" in cmd and "/datasets" in cmd


# ── API: выдача файлов меша для браузерного 3D-вьюера ─────────

def test_mesh_file_endpoint_serves_and_guards(tmp_path, monkeypatch):
    """/api/gsplat/mesh-file отдаёт obj/mtl/текстуру из папки меша и режет
    path traversal. Задачу вставляем прямо в память пайплайна."""
    from fastapi.testclient import TestClient
    from webapp.backend import main

    monkeypatch.setattr(gp, "GSPLAT_DATA_DIR", tmp_path)
    tex = tmp_path / "job1" / "odm" / "reconstruction" / "odm_texturing"
    tex.mkdir(parents=True)
    (tex / "odm_textured_model_geo.obj").write_text("o mesh\nmtllib odm_textured_model_geo.mtl\n")
    (tex / "odm_textured_model_geo.mtl").write_text("newmtl m\n")
    (tex / "texture.png").write_bytes(b"\x89PNG")
    (tmp_path / "secret.txt").write_text("nope")

    gp._jobs["job1"] = {
        "id": "job1", "logs": [],
        "output_mesh_obj": str(tex / "odm_textured_model_geo.obj"),
    }
    try:
        c = TestClient(main.app)
        r = c.get("/api/gsplat/mesh-file/job1/odm_textured_model_geo.obj")
        assert r.status_code == 200 and "mtllib" in r.text
        r = c.get("/api/gsplat/mesh-file/job1/texture.png")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
        # path traversal → 404, а не выдача secret.txt
        r = c.get("/api/gsplat/mesh-file/job1/..%2f..%2f..%2fsecret.txt")
        assert r.status_code in (400, 404)
        assert "nope" not in r.text
    finally:
        gp._jobs.pop("job1", None)
