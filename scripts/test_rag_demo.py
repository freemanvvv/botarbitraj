#!/usr/bin/env python3
"""
Тестирование RAG системы с интегрированным датасетом.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.rag_pipeline import get_rag

def test_rag():
    """Тестировать RAG с демо-запросами."""
    
    # Инициализируем RAG
    rag = get_rag("simple")
    
    print("🔍 Тестирование RAG системы")
    print("=" * 70)
    print(f"📊 Всего документов в БД: {rag.count()}\n")
    
    # Примеры запросов
    queries = [
        "Какие стандартные размеры жилых комнат?",
        "Какова минимальная высота потолков?",
        "Сколько стоит кирпичная кладка?",
        "Какие типовые квартиры?",
    ]
    
    for query in queries:
        print(f"\n❓ Запрос: {query}")
        print("-" * 70)
        
        try:
            # Прямой запрос к ChromaDB
            results = rag.collection.query(
                query_texts=[query],
                n_results=2
            )
            
            if results['ids'] and results['ids'][0]:
                for i in range(len(results['ids'][0])):
                    print(f"\n  📄 Результат {i+1}:")
                    print(f"     Источник: {results['metadatas'][0][i].get('source', 'unknown')}")
                    if results.get('distances'):
                        score = 1 - results['distances'][0][i]
                        print(f"     Релевантность: {score:.2%}")
                    print(f"     Текст: {results['documents'][0][i][:120]}...")
            else:
                print("  ❌ Результатов не найдено")
                
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
    
    print("\n" + "=" * 70)
    print("✅ Тестирование завершено!")

if __name__ == "__main__":
    test_rag()
