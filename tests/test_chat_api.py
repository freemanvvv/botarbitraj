"""Тесты /api/chat: RAG-фолбэк на архив, уточняющий вопрос, чистка расширения
запроса от <think>. LM Studio и векторный поиск мокаются."""
import src.lmstudio_client as lm
import src.rag_pipeline as ragmod
from fastapi.testclient import TestClient

from webapp.backend import main

client = TestClient(main.app)
MID = sorted(main._ALLOWED_MODEL_IDS)[0]


def _setup(monkeypatch, search_impl, chat_reply="ответ"):
    monkeypatch.setattr(lm, "list_models", lambda: [MID])
    monkeypatch.setattr(lm, "chat", lambda *a, **k: chat_reply)

    class FakeRAG:
        def __init__(self, *a, **k):
            pass

        def search(self, q, top_k=8):
            return search_impl(q)

    monkeypatch.setattr(ragmod, "NormbaseRAG", FakeRAG)


def test_clarify_when_nothing_found(monkeypatch):
    """Ничего не нашлось (ни вектор, ни архив) → clarify=True, а не «данных нет»."""
    _setup(monkeypatch, lambda q: [], chat_reply="Уточните: о каком объекте речь?")
    d = client.post("/api/chat", json={"message": "zzz qqq wwww", "model": MID, "use_rag": True}).json()
    assert d["clarify"] is True
    assert d["rag_used"] is False
    assert d["rag_chunks"] == []


def test_archive_fallback_surfaces_docs(monkeypatch):
    """Вектор пуст, но в архиве есть документы по слову «пожарная» → показываем
    их (rag_used=True, score=None), clarify=False."""
    _setup(monkeypatch, lambda q: [])
    d = client.post("/api/chat", json={"message": "пожарная безопасность", "model": MID, "use_rag": True}).json()
    assert d["clarify"] is False
    assert d["rag_used"] is True
    assert any("Пожарн" in (c["title"] or "") for c in d["rag_chunks"])
    assert all(c["score"] is None for c in d["rag_chunks"])


def test_stem_rerank_promotes_on_topic(monkeypatch):
    """Профильный чанк с меньшим score поднимается выше мусора за счёт совпадения
    стемма запроса («пожарн»)."""
    def search(q):
        return [
            {"text": "Аэродромы полосы", "score": 0.90, "meta": {"doc_type": "КМК", "number": "2.05.08-97", "title": "Аэродромы"}},
            {"text": "пожарные извещатели ставят...", "score": 0.82, "meta": {"doc_type": "ШНК", "number": "2.04.09-07", "title": "Пожарная автоматика зданий"}},
        ]
    _setup(monkeypatch, search)
    d = client.post("/api/chat", json={"message": "какие пожарные датчики нужны", "model": MID, "use_rag": True}).json()
    assert d["rag_chunks"][0]["number"] == "2.04.09-07"  # профильный — первым


def test_rag_disabled_no_clarify(monkeypatch):
    _setup(monkeypatch, lambda q: [])
    d = client.post("/api/chat", json={"message": "привет", "model": MID, "use_rag": False}).json()
    assert d["clarify"] is False
    assert d["rag_used"] is False


def test_broad_query_asks_to_narrow_instead_of_summary(monkeypatch):
    """Запрос совпал с МНОГИМИ разными нормативами без явного лидера →
    clarify=True (просим сузить), но источники всё равно отданы списком, чтобы
    пользователь мог выбрать документ."""
    def search(q):
        # 5 разных документов, близкие score, ни один не доминирует
        return [
            {"text": "требования к зданиям 1", "score": 0.72, "meta": {"doc_type": "ШНК", "number": "2.01.01-11", "title": "Общие", "page_start": 5, "page_end": 5}},
            {"text": "требования к зданиям 2", "score": 0.71, "meta": {"doc_type": "КМК", "number": "2.08.01-89", "title": "Жилые здания", "page_start": 3, "page_end": 3}},
            {"text": "требования к зданиям 3", "score": 0.70, "meta": {"doc_type": "ШНК", "number": "2.04.09-07", "title": "Пожарная автоматика", "page_start": 8, "page_end": 8}},
            {"text": "требования к зданиям 4", "score": 0.69, "meta": {"doc_type": "КМК", "number": "2.01.07-96", "title": "Нагрузки", "page_start": 2, "page_end": 2}},
            {"text": "требования к зданиям 5", "score": 0.68, "meta": {"doc_type": "ШНК", "number": "2.07.01-03", "title": "Планировка", "page_start": 1, "page_end": 1}},
        ]
    _setup(monkeypatch, search, chat_reply="Уточните: жилое или общественное здание?")
    d = client.post("/api/chat", json={"message": "требования к зданиям", "model": MID, "use_rag": True}).json()
    assert d["clarify"] is True            # просим сузить, а не «выжимка»
    assert d["rag_used"] is True
    assert len(d["rag_chunks"]) >= 4       # список документов для выбора отдан


def test_focused_query_answers_without_clarify(monkeypatch):
    """Есть явный лидер (один документ с большим отрывом) → отвечаем, без
    уточнения, даже если всплыли пара других документов."""
    def search(q):
        return [
            {"text": "минимальная площадь кухни 8 м2", "score": 0.93, "meta": {"doc_type": "КМК", "number": "2.08.01-89", "title": "Жилые здания", "page_start": 12, "page_end": 12}},
            {"text": "кухня освещение", "score": 0.90, "meta": {"doc_type": "КМК", "number": "2.08.01-89", "title": "Жилые здания", "page_start": 13, "page_end": 13}},
            {"text": "прочее", "score": 0.55, "meta": {"doc_type": "ШНК", "number": "2.07.01-03", "title": "Планировка", "page_start": 1, "page_end": 1}},
        ]
    _setup(monkeypatch, search)
    d = client.post("/api/chat", json={"message": "минимальная площадь кухни", "model": MID, "use_rag": True}).json()
    assert d["clarify"] is False
    assert d["rag_used"] is True
