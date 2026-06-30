"""
База расценок (SQLite).
Пустая — заполняется пользователем.
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "pricing", "pricing.db")


def get_db() -> sqlite3.Connection:
    """Возвращает соединение с БД (создаёт при первом вызове)."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицы, если их нет. Заполняет тестовыми данными."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT,
            region TEXT DEFAULT 'Ташкент',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS work_types (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            unit TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT,
            region TEXT DEFAULT 'Ташкент',
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            created_at TEXT,
            total_materials REAL DEFAULT 0,
            total_work REAL DEFAULT 0,
            total_overall REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS estimate_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            estimate_id INTEGER,
            item_type TEXT CHECK(item_type IN ('material', 'work')),
            item_name TEXT,
            unit TEXT,
            quantity REAL,
            unit_price REAL,
            total_price REAL,
            FOREIGN KEY (estimate_id) REFERENCES estimates(id)
        );
    """)

    # Тестовые материалы (пусто — пользователь заполнит)
    # cursor.execute("INSERT OR IGNORE INTO materials ...")

    conn.commit()
    conn.close()


def add_material(name: str, unit: str, price: float, category: str = "", region: str = "Ташкент") -> int:
    """Добавляет материал в базу."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO materials (name, unit, price, category, region, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, unit, price, category, region, datetime.now().isoformat()),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def add_work(name: str, unit: str, price: float, category: str = "", region: str = "Ташкент") -> int:
    """Добавляет тип работы в базу."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO work_types (name, unit, price, category, region, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, unit, price, category, region, datetime.now().isoformat()),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def search_materials(query: str, limit: int = 20) -> list[dict]:
    """Поиск материалов по названию."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM materials WHERE name LIKE ? ORDER BY name LIMIT ?",
        (f"%{query}%", limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def search_work(query: str, limit: int = 20) -> list[dict]:
    """Поиск типов работ по названию."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM work_types WHERE name LIKE ? ORDER BY name LIMIT ?",
        (f"%{query}%", limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def list_all_materials() -> list[dict]:
    """Все материалы."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM materials ORDER BY category, name")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def list_all_work() -> list[dict]:
    """Все типы работ."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM work_types ORDER BY category, name")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def create_estimate(project_name: str) -> int:
    """Создаёт новую смету."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO estimates (project_name, created_at) VALUES (?, ?)",
        (project_name, datetime.now().isoformat()),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def add_estimate_item(estimate_id: int, item_type: str, item_name: str,
                      unit: str, quantity: float, unit_price: float) -> int:
    """Добавляет позицию в смету."""
    total = round(quantity * unit_price, 2)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO estimate_items
           (estimate_id, item_type, item_name, unit, quantity, unit_price, total_price)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (estimate_id, item_type, item_name, unit, quantity, unit_price, total),
    )
    conn.commit()
    row_id = cursor.lastrowid

    # Обновляем итоги сметы
    cursor.execute("""
        UPDATE estimates SET
            total_materials = (SELECT COALESCE(SUM(total_price), 0) FROM estimate_items WHERE estimate_id = ? AND item_type = 'material'),
            total_work = (SELECT COALESCE(SUM(total_price), 0) FROM estimate_items WHERE estimate_id = ? AND item_type = 'work'),
            total_overall = (SELECT COALESCE(SUM(total_price), 0) FROM estimate_items WHERE estimate_id = ?)
        WHERE id = ?
    """, (estimate_id, estimate_id, estimate_id, estimate_id))
    conn.commit()
    conn.close()
    return row_id


def get_estimate(estimate_id: int) -> dict | None:
    """Возвращает смету с позициями."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM estimates WHERE id = ?", (estimate_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    estimate = dict(row)
    cursor.execute("SELECT * FROM estimate_items WHERE estimate_id = ?", (estimate_id,))
    estimate["items"] = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return estimate


def delete_estimate(estimate_id: int):
    """Удаляет смету."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM estimate_items WHERE estimate_id = ?", (estimate_id,))
    cursor.execute("DELETE FROM estimates WHERE id = ?", (estimate_id,))
    conn.commit()
    conn.close()


# Инициализация при импорте
init_db()
