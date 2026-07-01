import os

import pytest


@pytest.fixture
def cleanup_ifc():
    """Отслеживает пути к сгенерированным IFC-файлам и удаляет их после теста,
    чтобы прогон тестов не засорял output/ реальными артефактами."""
    paths: list[str] = []
    yield paths
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


@pytest.fixture
def cleanup_estimates():
    """Удаляет тестовые сметы из общей SQLite-базы после теста (та же БД,
    что использует реальное приложение — тесты не должны там мусорить)."""
    from src.pricing_db import delete_estimate
    ids: list[int] = []
    yield ids
    for estimate_id in ids:
        try:
            delete_estimate(estimate_id)
        except Exception:
            pass


@pytest.fixture
def cleanup_pricing():
    """Удаляет тестовые позиции материалов/работ из общей БД расценок после теста."""
    from src.pricing_db import get_db
    entries: list[tuple[str, int]] = []  # [("materials"|"work_types", id), ...]
    yield entries
    conn = get_db()
    cur = conn.cursor()
    for table, row_id in entries:
        cur.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
    conn.commit()
    conn.close()
