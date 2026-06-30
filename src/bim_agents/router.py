"""
Фаза 0 — Роутер моделей для LM Studio.
Управляет загрузкой/выгрузкой моделей, соблюдает лимит 20GB памяти.
"""
from typing import Optional
import requests

LMSTUDIO_URL = "http://localhost:1234"

# Приоритеты: меньше = выше приоритет
MODEL_REGISTRY = {
    "qwen/qwen3-14b": {
        "name": "Qwen3-14B Q4_K_M",
        "role": "bim, architecture, engineering",
        "size_gb": 8.5,
        "priority": 1,
    },
    "behbudiy/Llama-3.1-8B-Instruct-Uz": {
        "name": "Llama-3.1-8B Uzbek",
        "role": "uzbek, kmk, client comms",
        "size_gb": 4.5,
        "priority": 2,
    },
    "qwen/qwen3-8b": {
        "name": "Qwen3-8B",
        "role": "fast tasks, dispatch",
        "size_gb": 4.8,
        "priority": 0,  # резидентная
    },
}

class ModelRouter:
    _loaded_model: Optional[str] = None

    def list_models(self) -> list[str]:
        try:
            r = requests.get(f"{LMSTUDIO_URL}/v1/models", timeout=5)
            return [m["id"] for m in r.json()["data"]]
        except Exception:
            return []

    def is_loaded(self, model_id: str) -> bool:
        return model_id in self.list_models()

    def load(self, model_id: str) -> bool:
        """Загружает модель, выгружая предыдущую."""
        if self.is_loaded(model_id):
            self._loaded_model = model_id
            return True
        # Выгружаем предыдущую
        if self._loaded_model and self._loaded_model != model_id:
            self._unload(self._loaded_model)
        # Загружаем
        try:
            r = requests.post(
                f"{LMSTUDIO_URL}/v1/models/load",
                json={"model": model_id},
                timeout=60,
            )
            if r.status_code == 200:
                self._loaded_model = model_id
                return True
        except Exception:
            pass
        return False

    def _unload(self, model_id: str):
        try:
            requests.post(
                f"{LMSTUDIO_URL}/v1/models/unload",
                json={"model": model_id},
                timeout=10,
            )
        except Exception:
            pass

    def chat(self, model_id: str, messages: list, stream: bool = False) -> str:
        """Через LM Studio."""
        if self.is_loaded(model_id):
            # Модель уже загружена
            pass
        elif model_id == self._loaded_model:
            pass
        else:
            self.load(model_id)

        try:
            r = requests.post(
                f"{LMSTUDIO_URL}/v1/chat/completions",
                json={
                    "model": model_id,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "stream": stream,
                },
                timeout=600,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as e:
            raise RuntimeError(f"LM Studio error: {e}")

    def router_for(self, query: str) -> str:
        """Определяет модель по запросу через быструю эвристику."""
        q = query.lower()
        if any(w in q for w in ["узбек", "ўзбек", "kmk", "кмк", "shnk", "шнк", "норматив"]):
            return "behbudiy/Llama-3.1-8B-Instruct-Uz"
        if any(w in q for w in ["стена", "этаж", "plan", "ifc", "bim", "дом", "здание", "проект"]):
            return "qwen/qwen3-14b"
        return "qwen/qwen3-8b"


router = ModelRouter()
