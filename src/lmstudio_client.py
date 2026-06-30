"""
Клиент для LM Studio (OpenAI-совместимый API)
"""
from openai import OpenAI
from .config import LM_STUDIO_BASE_URL, MODELS

_client = None


def get_client() -> OpenAI:
    """
    Возвращает OpenAI-клиент для LM Studio.
    Создаёт при первом вызове (лениво, не при импорте).
    """
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=LM_STUDIO_BASE_URL,
            api_key="lm-studio",
            timeout=180.0,  # таймаут 3 минуты на генерацию
            max_retries=0,
        )
    return _client


def chat(
    model_key: str,
    messages: list[dict],
    temperature: float | None = None,
    max_tokens: int = 4096,
    stream: bool = False,
) -> str:
    """
    Отправляет запрос к модели через LM Studio.
    Автоматический fallback: если модель не загружена — пробуем любую загруженную.
    """
    cfg = MODELS.get(model_key, MODELS["qwen3-8b"])
    model_id = cfg["id"]
    temp = temperature if temperature is not None else cfg["temp"]

    client = get_client()

    # Пробуем запрошенную модель
    try:
        response = client.chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=temp,
            top_p=cfg["top_p"],
            max_tokens=max_tokens,
            stream=stream,
        )

        if stream:
            return response
        return response.choices[0].message.content

    except Exception as e:
        error_text = str(e)
        # Если модель не загружена — пробуем любую другую доступную
        if "Failed to load model" in error_text or "model" in error_text.lower():
            # Ищем загруженную модель
            available = client.models.list().data
            for m in available:
                try:
                    alt_response = client.chat.completions.create(
                        model=m.id,
                        messages=messages,
                        temperature=temp,
                        top_p=cfg.get("top_p", 0.9),
                        max_tokens=max_tokens,
                        stream=stream,
                    )
                    if stream:
                        return alt_response
                    return alt_response.choices[0].message.content
                except Exception:
                    continue

        # Ничего не помогло — пробрасываем оригинальную ошибку
        raise


def embed(text: str, model: str | None = None) -> list[float]:
    """
    Получает эмбеддинг текста через LM Studio.
    """
    from .config import EMBEDDING_MODEL

    model = model or EMBEDDING_MODEL
    client = get_client()
    response = client.embeddings.create(model=model, input=text)
    return response.data[0].embedding


def list_models() -> list[dict]:
    """Возвращает список доступных в LM Studio моделей."""
    client = get_client()
    return client.models.list().data
