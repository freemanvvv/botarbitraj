"""Тесты helper'а _run из gsplat-пайплайна — конкретно поведение, из-за
которого «загрузка видео доходит до 5% и висит»: наследованный stdin,
'\\r'-прогресс ffmpeg, отсутствие реального таймаута."""
import sys
import time

from src.gsplat_pipeline import _run


def _job() -> dict:
    return {"logs": []}


def test_run_captures_newline_output():
    job = _job()
    rc, out = _run(job, [sys.executable, "-c", "print('строка1'); print('строка2')"])
    assert rc == 0
    assert "строка1" in out and "строка2" in out
    joined = "\n".join(job["logs"])
    assert "строка1" in joined and "✔ завершено rc=0" in joined


def test_run_does_not_hang_on_inherited_stdin():
    """Команда, читающая stdin до конца, НЕ должна зависать: _run отдаёт
    stdin=DEVNULL, поэтому read() сразу получает EOF. Это и есть корневая
    причина зависания ffmpeg на 5% (наследованный stdin)."""
    job = _job()
    start = time.monotonic()
    rc, out = _run(job, [sys.executable, "-c", "import sys; sys.stdin.read(); print('ok')"], timeout=15)
    assert rc == 0
    assert "ok" in out
    assert time.monotonic() - start < 10  # не завис


def test_run_hard_timeout_kills_hung_process():
    """Зависший (молчащий, не закрывающий stdout) процесс должен быть убит по
    таймауту, а не висеть вечно — раньше timeout к такому процессу не
    применялся вовсе."""
    job = _job()
    start = time.monotonic()
    rc, _ = _run(job, [sys.executable, "-c", "import time; time.sleep(30)"], timeout=2, stall_warn=1)
    elapsed = time.monotonic() - start
    assert rc == -9
    assert elapsed < 15
    joined = "\n".join(job["logs"])
    assert "таймаут" in joined
    assert "нет вывода" in joined  # сторож простоя успел отметиться


def test_run_reports_missing_binary():
    job = _job()
    rc, msg = _run(job, ["definitely-not-a-real-binary-xyz"])
    assert rc == -127
    assert "не найдена" in msg
