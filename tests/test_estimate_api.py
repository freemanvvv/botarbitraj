"""Тесты вебхуков смет (BOQ) — генерация через LLM (мок), расчёт по базе
расценок, управление расценками, экспорт. Реальная LM Studio не нужна."""
import os

import requests
from fastapi.testclient import TestClient

from webapp.backend.main import app

client = TestClient(app)


def _mock_llm_response(monkeypatch, table_markdown: str):
    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": table_markdown}}]}

    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse())


def test_pricing_add_and_list_material(cleanup_pricing):
    r = client.post("/api/pricing/materials", json={
        "name": "Тестовый материал X", "unit": "шт", "price": 1000, "category": "Тест",
    })
    assert r.status_code == 200
    entry_id = r.json()["id"]
    cleanup_pricing.append(("materials", entry_id))

    r2 = client.get("/api/pricing/materials", params={"q": "Тестовый материал X"})
    assert r2.status_code == 200
    names = [m["name"] for m in r2.json()["materials"]]
    assert "Тестовый материал X" in names


def test_pricing_rejects_negative_price():
    r = client.post("/api/pricing/materials", json={"name": "X", "unit": "шт", "price": -1})
    assert r.status_code == 422


def test_pricing_rejects_huge_price():
    r = client.post("/api/pricing/materials", json={"name": "X", "unit": "шт", "price": 1e12})
    assert r.status_code == 422


def test_pricing_add_work(cleanup_pricing):
    r = client.post("/api/pricing/work", json={"name": "Тестовая работа Y", "unit": "м2", "price": 5000})
    assert r.status_code == 200
    cleanup_pricing.append(("work_types", r.json()["id"]))

    r2 = client.get("/api/pricing/work", params={"q": "Тестовая работа Y"})
    names = [w["name"] for w in r2.json()["work"]]
    assert "Тестовая работа Y" in names


def test_estimate_generate_rejects_empty_description():
    r = client.post("/api/estimate/generate", json={"description": ""})
    assert r.status_code == 400


def test_estimate_generate_matches_known_material(monkeypatch, cleanup_pricing, cleanup_estimates):
    # Заводим известный материал с предсказуемой ценой, чтобы не зависеть
    # от содержимого реальной (изменяемой) базы расценок.
    r = client.post("/api/pricing/materials", json={
        "name": "УникальныйТестМатериал777", "unit": "м3", "price": 100000,
    })
    cleanup_pricing.append(("materials", r.json()["id"]))

    table = (
        "## Ведомость работ\n"
        "| № | Наименование | Ед. изм. | Кол-во |\n"
        "|---|---|---|---|\n"
        "| 1 | УникальныйТестМатериал777 | м3 | 10 |\n"
    )
    _mock_llm_response(monkeypatch, table)

    r2 = client.post("/api/estimate/generate", json={"description": "Тестовый объект", "model": "local-model"})
    assert r2.status_code == 200
    data = r2.json()
    cleanup_estimates.append(data["estimate_id"])

    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["unit_price"] == 100000
    assert item["total"] == 1_000_000
    assert data["total"] == 1_000_000


def test_estimate_generate_marks_unknown_items(monkeypatch, cleanup_estimates):
    table = (
        "| № | Наименование | Ед. изм. | Кол-во |\n"
        "|---|---|---|---|\n"
        "| 1 | Абсолютно неизвестная позиция ZZZ999 | шт | 3 |\n"
    )
    _mock_llm_response(monkeypatch, table)

    r = client.post("/api/estimate/generate", json={"description": "Тест", "model": "local-model"})
    assert r.status_code == 200
    data = r.json()
    cleanup_estimates.append(data["estimate_id"])

    assert data["items"][0]["unit_price"] == 0.0
    assert "note" in data["items"][0]


def test_estimate_generate_returns_422_on_unparseable_response(monkeypatch):
    _mock_llm_response(monkeypatch, "Извините, не могу помочь с этим запросом.")
    r = client.post("/api/estimate/generate", json={"description": "Тест", "model": "local-model"})
    assert r.status_code == 422


def _exported_filename(response) -> str | None:
    """Имя файла может прийти как filename="..." или (для кириллицы,
    RFC 5987) filename*=utf-8''<url-encoded>."""
    import urllib.parse
    disposition = response.headers.get("content-disposition", "")
    if "filename*=" in disposition:
        encoded = disposition.split("filename*=", 1)[1].split("''", 1)[-1]
        return urllib.parse.unquote(encoded)
    if "filename=" in disposition:
        return disposition.split("filename=", 1)[1].strip('"')
    return None


def test_estimate_get_and_export(monkeypatch, cleanup_estimates):
    from webapp.backend.main import OUTPUT_DIR

    table = (
        "| № | Наименование | Ед. изм. | Кол-во |\n"
        "|---|---|---|---|\n"
        "| 1 | Тестовая позиция экспорта | шт | 2 |\n"
    )
    _mock_llm_response(monkeypatch, table)
    gen = client.post("/api/estimate/generate", json={"description": "Тест экспорта", "model": "local-model"})
    estimate_id = gen.json()["estimate_id"]
    cleanup_estimates.append(estimate_id)

    r = client.get(f"/api/estimate/{estimate_id}")
    assert r.status_code == 200
    assert r.json()["id"] == estimate_id

    r404 = client.get("/api/estimate/999999999")
    assert r404.status_code == 404

    exported_files = []
    try:
        r_xlsx = client.get(f"/api/estimate/{estimate_id}/export/xlsx")
        assert r_xlsx.status_code == 200
        assert r_xlsx.content[:2] == b"PK"  # xlsx это zip-контейнер
        if (name := _exported_filename(r_xlsx)):
            exported_files.append(name)

        r_pdf = client.get(f"/api/estimate/{estimate_id}/export/pdf")
        assert r_pdf.status_code == 200
        assert r_pdf.content[:4] == b"%PDF"
        if (name := _exported_filename(r_pdf)):
            exported_files.append(name)

        r_bad_fmt = client.get(f"/api/estimate/{estimate_id}/export/exe")
        assert r_bad_fmt.status_code == 400
    finally:
        for name in exported_files:
            try:
                os.remove(OUTPUT_DIR / name)
            except OSError:
                pass
