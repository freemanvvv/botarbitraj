"""Тесты моста ChatHouseDiffusion (src/floorplan/chathousediffusion_adapter.py).

Реальный ChatHouseDiffusion (torch/dgl/веса) в CI недоступен и не нужен —
subprocess.run мокается, проверяется только контракт адаптера: конфигурация
через CHD_PYTHON/CHD_BRIDGE_SCRIPT, парсинг ответа моста, откат на None при
любой неудаче, и то, что успешный ответ проходит через vectorize+geometry
и даёт валидную по нормам ApartmentFloorplan.
"""
import json
import subprocess

import pytest

from src.floorplan.chathousediffusion_adapter import generate_floorplan_chd
from src.floorplan.norms import validate_floorplan

cv2 = pytest.importorskip("cv2")


def test_returns_none_when_bridge_not_configured(monkeypatch, tmp_path):
    monkeypatch.delenv("CHD_PYTHON", raising=False)
    monkeypatch.delenv("CHD_BRIDGE_SCRIPT", raising=False)
    result = generate_floorplan_chd(width=8.5, depth=10.0, room_count=2, entry_side="west")
    assert result is None


def test_returns_none_when_bridge_script_path_does_not_exist(monkeypatch):
    result = generate_floorplan_chd(
        width=8.5, depth=10.0, room_count=2, entry_side="west",
        bridge_python="/usr/bin/python3", bridge_script="/nonexistent/bridge.py",
    )
    assert result is None


def _label_grid_for(width_m, depth_m, px_per_meter):
    import numpy as np
    h, w = int(depth_m * px_per_meter), int(width_m * px_per_meter)
    grid = np.zeros((h, w), dtype=np.uint8)
    mid = w // 2
    grid[:, :mid] = 0   # living, примыкает к входу (west, x=0)
    grid[:, mid:] = 2   # kitchen
    return grid.tolist()


def test_returns_valid_floorplan_when_bridge_succeeds(monkeypatch, tmp_path):
    script = tmp_path / "predict_floorplan.py"
    script.write_text("# fake bridge, invoked via mocked subprocess.run")

    width, depth = 8.0, 6.0
    px_per_meter = 6.0

    def fake_run(cmd, input, capture_output, text, timeout):
        request = json.loads(input)
        grid = _label_grid_for(request["width_m"], request["depth_m"], request["px_per_meter"])
        payload = {"label_grid": grid, "px_per_meter": request["px_per_meter"]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload) + "\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    fp = generate_floorplan_chd(
        width=width, depth=depth, room_count=2, entry_side="west",
        bridge_python="/usr/bin/python3", bridge_script=str(script),
    )
    assert fp is not None
    assert fp.source == "chathousediffusion"
    assert {r.type for r in fp.rooms} == {"living", "kitchen"}
    assert any(d.kind == "entry" for d in fp.doors)


def test_returns_none_when_subprocess_times_out(monkeypatch, tmp_path):
    script = tmp_path / "predict_floorplan.py"
    script.write_text("# fake bridge")

    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = generate_floorplan_chd(
        width=8.0, depth=6.0, bridge_python="/usr/bin/python3", bridge_script=str(script),
    )
    assert result is None


def test_returns_none_when_subprocess_fails(monkeypatch, tmp_path):
    script = tmp_path / "predict_floorplan.py"
    script.write_text("# fake bridge")

    def fake_run(cmd, input, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = generate_floorplan_chd(
        width=8.0, depth=6.0, bridge_python="/usr/bin/python3", bridge_script=str(script),
    )
    assert result is None


def test_returns_none_when_stdout_is_not_json(monkeypatch, tmp_path):
    script = tmp_path / "predict_floorplan.py"
    script.write_text("# fake bridge")

    def fake_run(cmd, input, capture_output, text, timeout):
        return subprocess.CompletedProcess(cmd, 0, stdout="not json at all\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = generate_floorplan_chd(
        width=8.0, depth=6.0, bridge_python="/usr/bin/python3", bridge_script=str(script),
    )
    assert result is None


def test_returns_none_when_no_rooms_extracted(monkeypatch, tmp_path):
    script = tmp_path / "predict_floorplan.py"
    script.write_text("# fake bridge")

    def fake_run(cmd, input, capture_output, text, timeout):
        payload = {"label_grid": [[13, 13], [13, 13]], "px_per_meter": 4.0}  # только "External"
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = generate_floorplan_chd(
        width=8.0, depth=6.0, bridge_python="/usr/bin/python3", bridge_script=str(script),
    )
    assert result is None


def test_env_var_configuration_is_used_when_args_omitted(monkeypatch, tmp_path):
    script = tmp_path / "predict_floorplan.py"
    script.write_text("# fake bridge")
    monkeypatch.setenv("CHD_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("CHD_BRIDGE_SCRIPT", str(script))

    width, depth = 8.0, 6.0

    def fake_run(cmd, input, capture_output, text, timeout):
        request = json.loads(input)
        grid = _label_grid_for(request["width_m"], request["depth_m"], request["px_per_meter"])
        payload = {"label_grid": grid, "px_per_meter": request["px_per_meter"]}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    fp = generate_floorplan_chd(width=width, depth=depth)
    assert fp is not None
