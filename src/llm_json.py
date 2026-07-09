"""Устойчивое извлечение JSON из ответа LLM.

Reasoning-модели (qwen3 и подобные) перед JSON выдают блок рассуждений
<think>…</think>, а иногда оборачивают ответ в ```json … ```. Наивный
`re.search(r'\\{[\\s\\S]*\\}', raw)` берёт от ПЕРВОЙ `{` до ПОСЛЕДНЕЙ `}` —
и ломается, если фигурная скобка попала в рассуждения или JSON обрезан по
лимиту токенов. Здесь — вырезаем <think>, снимаем обёртки и находим первый
СБАЛАНСИРОВАННЫЙ объект с учётом строковых литералов.
"""
import re

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_think(raw: str) -> str:
    """Убирает блоки рассуждений <think>…</think> из ответа reasoning-модели.
    Если тег открыт, но не закрыт (модель не успела) — отбрасывает всё после
    открытия. Для случаев, где нужен просто текст ответа без JSON."""
    if not raw:
        return ""
    text = _THINK_RE.sub("", raw)
    if "<think>" in text and "</think>" not in text:
        text = text.split("<think>", 1)[0]
    return text.replace("</think>", "").strip()


def extract_json_object(raw: str) -> str:
    """Возвращает строку первого валидного JSON-объекта из ответа модели.
    Бросает ValueError с понятным текстом, если объекта нет."""
    if not raw or not raw.strip():
        raise ValueError("модель вернула пустой ответ")

    text = _THINK_RE.sub("", raw)
    # незакрытый <think> (модель не успела закрыть тег) — отбрасываем всё после него
    if "<think>" in text and "</think>" not in text:
        text = text.split("<think>", 1)[0]
    text = text.replace("</think>", "")

    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        # от этой { нет закрытия (обрезано/битые скобки) — пробуем следующую
        start = text.find("{", start + 1)

    raise ValueError("в ответе модели нет валидного JSON-объекта "
                     "(возможно, обрезан по лимиту токенов или модель ушла в рассуждения)")
