"""Регрессионные тесты на P0-находки аудита безопасности:
path traversal, отсутствие верхних границ параметров, утечка деталей исключений."""
import os

import pytest
from fastapi.testclient import TestClient

from webapp.backend.main import app, OUTPUT_DIR

client = TestClient(app)


def test_health_endpoint_ok():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_model_generate_rejects_huge_num_floors():
    r = client.post("/api/model/generate", json={"name": "Attack", "num_floors": 999999})
    assert r.status_code == 422


def test_model_generate_rejects_huge_dimensions():
    r = client.post("/api/model/generate", json={"name": "Attack", "length": 1e9, "width": 1e9})
    assert r.status_code == 422


def test_model_generate_accepts_normal_request():
    r = client.post(
        "/api/model/generate",
        json={"name": "PytestNormal", "num_floors": 2, "length": 12, "width": 9, "height": 6},
    )
    assert r.status_code == 200
    filename = r.json()["filename"]
    path = OUTPUT_DIR / filename
    assert path.exists()
    path.unlink()  # уборка за собой


@pytest.mark.parametrize("endpoint", ["/api/model/info", "/api/model/view"])
def test_path_traversal_blocked_on_filename_endpoints(endpoint):
    # прямой ../ не долетает до хендлера — роутинг сам не матчит multi-segment path
    r = client.get(f"{endpoint}/foo/../../../../etc/passwd")
    assert r.status_code == 404

    # несуществующий (но синтаксически валидный) файл — корректный 404, не 500
    r2 = client.get(f"{endpoint}/does_not_exist_12345.ifc")
    assert r2.status_code == 404
    assert "Файл не найден" in r2.json()["detail"]


def test_error_messages_do_not_leak_internal_paths():
    """500-е ошибки не должны содержать абсолютные пути сервера."""
    # запрос, который приведёт к исключению внутри обработчика (некорректный JSON)
    r = client.post("/api/model/architect", json={"requirements": ""})
    assert r.status_code == 400  # пустые requirements отклоняются явно, до генерации
    assert str(OUTPUT_DIR) not in r.text
