"""
Поиск по нормативной базе с цитированием.

Запуск:
    python query.py "минимальная высота ступени лестницы"
    python query.py "перила лестницы" --n 8
    python query.py "приёмка объекта в эксплуатацию" --status all
    python query.py "высота ступени лестницы" --ask   # + ответ LLM по найденному

По умолчанию отменённые нормы (status=superseded) исключаются из выдачи.
"""
from __future__ import annotations

import argparse
from typing import Dict, List

import requests

import config
import embed
import store


def format_citation(meta: Dict) -> str:
    parts = [f"{meta.get('doc_type','')} {meta.get('number','')}".strip()]
    clauses = (meta.get("clauses") or "").strip()
    if clauses:
        parts.append(f"п. {clauses}")
    ps, pe = meta.get("page_start"), meta.get("page_end")
    if ps:
        parts.append(f"стр. {ps}" if ps == pe else f"стр. {ps}-{pe}")
    cite = ", ".join(p for p in parts if p)
    status = meta.get("status", "")
    if status and status != "active":
        cite += f"  [статус: {status}]"
    return cite


def build_where(status_mode: str):
    if status_mode == "all":
        return None
    if status_mode == "active":
        return {"status": "active"}
    # default: всё, кроме отменённого
    return {"status": {"$ne": config.DEFAULT_EXCLUDE_STATUS}}


def retrieve(question: str, n: int, status_mode: str) -> List[Dict]:
    q_emb = embed.embed_query(question)
    collection = store.get_collection()
    return store.query(collection, q_emb, n_results=n, where=build_where(status_mode))


def ask_llm(question: str, hits: List[Dict]) -> str:
    context = "\n\n".join(
        f"[{format_citation(h['meta'])}]\n{h['text']}" for h in hits
    )
    system = (
        "Ты — консультант по строительным нормам Узбекистана. Отвечай ТОЛЬКО "
        "на основе приведённых фрагментов нормативов. Обязательно ссылайся на "
        "документ и пункт. Если ответа в фрагментах нет — так и скажи, не "
        "выдумывай."
    )
    payload = {
        "model": config.CHAT_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",
             "content": f"Фрагменты нормативов:\n\n{context}\n\nВопрос: {question}"},
        ],
    }
    r = requests.post(f"{config.LMSTUDIO_BASE_URL}/chat/completions",
                     json=payload, timeout=config.REQUEST_TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("question", help="вопрос / поисковый запрос")
    ap.add_argument("--n", type=int, default=5, help="сколько фрагментов вернуть")
    ap.add_argument("--status", choices=["default", "active", "all"],
                    default="default", help="фильтр по статусу нормы")
    ap.add_argument("--ask", action="store_true",
                    help="дополнительно сгенерировать ответ LLM по найденному")
    args = ap.parse_args()

    if not embed.check_connection():
        return

    hits = retrieve(args.question, args.n, args.status)
    if not hits:
        print("Ничего не найдено. Проверь, что база заполнена (python ingest.py).")
        return

    print(f"\nНайдено фрагментов: {len(hits)}\n" + "=" * 60)
    for i, h in enumerate(hits, 1):
        sim = 1 - h["distance"] if h["distance"] is not None else None
        head = format_citation(h["meta"])
        score = f"  (сходство {sim:.2f})" if sim is not None else ""
        print(f"\n{i}. {head}{score}")
        snippet = h["text"].strip().replace("\n", " ")
        print(f"   {snippet[:400]}{'…' if len(snippet) > 400 else ''}")

    if args.ask:
        print("\n" + "=" * 60 + "\nОТВЕТ (на основе найденного):\n")
        print(ask_llm(args.question, hits))


if __name__ == "__main__":
    main()
