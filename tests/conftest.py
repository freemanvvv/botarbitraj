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
