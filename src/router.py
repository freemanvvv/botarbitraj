"""
Роутер моделей и языков.
"""
import re
from .config import MODELS


def detect_language(text: str) -> str:
    """
    Детект языка запроса.
    Возвращает 'uz' (узбекский), 'ru' (русский), 'en' (английский).
    """
    # Простая эвристика: узбекская латиница с характерными буквами
    uz_chars = set("o'g'chshq'")
    text_lower = text.lower()

    # Счёт характерных узбекских букв
    uz_score = sum(1 for c in text_lower if c in uz_chars)

    # Если много русских букв
    ru_pattern = re.compile(r'[а-яё]')
    ru_count = len(ru_pattern.findall(text_lower))

    # Если много латиницы
    en_pattern = re.compile(r'[a-z]')
    en_count = len(en_pattern.findall(text_lower))

    if uz_score >= 3 and uz_score > ru_count * 0.3:
        return "uz"

    if ru_count > en_count and ru_count > 5:
        return "ru"

    if en_count > ru_count and en_count > 3:
        return "en"

    return "ru"  # дефолт — русский


def estimate_complexity(text: str) -> str:
    """
    Оценка сложности задачи: 'simple' | 'complex'.
    """
    # Ключевые слова, указывающие на сложную задачу
    complex_keywords = [
        "смет", "boq", "стоимост", "расценк", "цена", "бюджет",
        "ifc", "bim", "план", "чертеж", "проект",
        "скрипт", "код", "python",
        "smeta", "xarajat", "narx", "byudjet",
        "specification", "estimate",
        "расчёт", "расчет", "подсчёт", "подсчет",
        "кмк", "снип", "гост", "норматив", "норма",
        "пункт", "параграф", "требован",
        "соответств", "проверк",
        "лестниц", "ступен", "высот",
        "бетон", "арматур", "перекрыт",
        "стандарт", "регламент",
        "qmk", "snip", "gost", "norma", "talab",
    ]

    text_lower = text.lower()
    for kw in complex_keywords:
        if kw in text_lower:
            return "complex"

    # Длинные запросы — сложные
    if len(text) > 200:
        return "complex"

    return "simple"


def select_model(text: str) -> str:
    """
    Выбирает модель для запроса.
    Возвращает ключ модели из config.MODELS.
    """
    lang = detect_language(text)
    complexity = estimate_complexity(text)

    # Узбекский — всегда на Llama-Uz (если доступна)
    if lang == "uz":
        return "llama-uz"

    # Сложные задачи — на 14B
    if complexity == "complex":
        return "qwen3-14b"

    # Простые — на 8B (быстро)
    return "qwen3-8b"


def route(text: str) -> dict:
    """
    Полный роутинг: выбор модели + мета-информация.
    """
    lang = detect_language(text)
    complexity = estimate_complexity(text)
    model_key = select_model(text)

    return {
        "model_key": model_key,
        "model_id": MODELS[model_key]["id"],
        "language": lang,
        "complexity": complexity,
    }
