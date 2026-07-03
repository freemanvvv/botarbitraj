#!/usr/bin/env python3
"""
Интеграция локального датасета в RAG систему.
"""

import os
import sys
from pathlib import Path

# Добавляем корень проекта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

def integrate_local_dataset(dataset_path: str):
    """Интегрировать локальный датасет в RAG."""
    try:
        from src.rag_pipeline import get_rag
    except ImportError as e:
        print(f"❌ Ошибка импорта: {e}")
        return False
    
    dataset_path = Path(dataset_path)
    print(f"🔄 Интегрирую датасет из: {dataset_path}")
    
    # Найти все текстовые файлы
    files = list(dataset_path.glob("*.txt")) + list(dataset_path.glob("*.md"))
    
    if not files:
        print(f"❌ Текстовые файлы не найдены в {dataset_path}")
        return False
    
    print(f"📁 Найдено файлов: {len(files)}")
    
    try:
        rag = get_rag("simple")
        total_chunks = 0
        processed = 0
        
        for filepath in files:
            try:
                print(f"   📄 Обработка: {filepath.name}")
                
                # Используем встроенный метод index_file
                chunks = rag.index_file(str(filepath))
                
                print(f"      ✅ Добавлено {chunks} чанков")
                total_chunks += chunks
                processed += 1
                
            except Exception as e:
                print(f"      ❌ Ошибка: {e}")
        
        count = rag.count()
        print(f"\n✅ Интегрировано файлов: {processed}/{len(files)}")
        print(f"📊 Чанков добавлено: {total_chunks}")
        print(f"📊 Всего документов в БД: {count}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка RAG интеграции: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    dataset_path = "data/datasets/demo_construction_data"
    success = integrate_local_dataset(dataset_path)
    sys.exit(0 if success else 1)
