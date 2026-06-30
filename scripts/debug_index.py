"""Debug script for RAG indexing"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

print("Starting debug...")
sys.stdout.flush()

from src.rag_pipeline import SimpleRAG

print("Creating SimpleRAG...")
sys.stdout.flush()
rag = SimpleRAG()
print(f"Collection ready, count: {rag.count()}")
sys.stdout.flush()

print("Indexing directory...")
sys.stdout.flush()
results = rag.index_directory()
print(f"Results: {results}")
print(f"Final count: {rag.count()}")
