"""Тесты устойчивого извлечения JSON из ответов LLM (reasoning-модели)."""
import json

import pytest

from src.llm_json import extract_json_object


def test_plain_json():
    assert json.loads(extract_json_object('{"a": 1}')) == {"a": 1}


def test_think_block_with_braces_before_json():
    """qwen3 и подобные: <think> с фигурными скобками не должен ломать разбор."""
    raw = '<think>Прикинем комнаты: {спальня, кухня}. Площадь ~70.</think>\n{"project_name":"Дом","storeys":2}'
    assert json.loads(extract_json_object(raw))["project_name"] == "Дом"


def test_markdown_fence():
    raw = "Вот результат:\n```json\n{\"x\": {\"y\": 2}}\n```\nготово"
    assert json.loads(extract_json_object(raw)) == {"x": {"y": 2}}


def test_brace_inside_string_literal():
    raw = '{"note": "закрывающая } внутри строки", "x": 5}'
    assert json.loads(extract_json_object(raw))["x"] == 5


def test_text_around_object():
    raw = 'Ответ: {"ok": true}. Спасибо!'
    assert json.loads(extract_json_object(raw)) == {"ok": True}


def test_unclosed_think_no_json_raises():
    with pytest.raises(ValueError):
        extract_json_object("<think>рассуждаю без конца и без json")


def test_empty_raises():
    with pytest.raises(ValueError):
        extract_json_object("   ")


def test_truncated_object_raises():
    # обрезано по лимиту токенов — нет закрывающей скобки
    with pytest.raises(ValueError):
        extract_json_object('{"project_name": "Дом", "rooms": [{"id": "a"')
