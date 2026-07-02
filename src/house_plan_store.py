"""
Черновики планов (частный дом / многоквартирный дом) — SQLite, по образцу
src/pricing_db.py (та же схема соединения: sqlite3.connect на вызов,
init_db() при импорте).

Хранит генерацию из /api/house/plan между «сохранить» и «построить 3D»:
program_json/floorplan_json — сырые данные, достаточные, чтобы повторно
собрать IFC без пересчёта LLM (raw_program/raw_floorplan из ответа
/api/house/plan сохраняются как есть).
"""
import sqlite3
import os
import json
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "house_plans", "house_plans.db")
DB_PATH = os.path.normpath(DB_PATH)


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS house_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            building_kind TEXT NOT NULL CHECK(building_kind IN ('house', 'apartment')),
            name TEXT,
            description TEXT,
            program_json TEXT NOT NULL,
            floorplan_json TEXT NOT NULL,
            norms_issues_json TEXT,
            norms_citations TEXT,
            created_at TEXT
        );
    """)
    conn.commit()
    conn.close()


def create_house_plan(
    building_kind: str, name: str, description: str,
    program: dict, floorplan: dict,
    norms_issues: list | None = None, norms_citations: str = "",
) -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO house_plans
           (building_kind, name, description, program_json, floorplan_json,
            norms_issues_json, norms_citations, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (building_kind, name, description, json.dumps(program), json.dumps(floorplan),
         json.dumps(norms_issues or []), norms_citations, datetime.now().isoformat()),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def get_house_plan(plan_id: int) -> dict | None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM house_plans WHERE id = ?", (plan_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    plan = dict(row)
    plan["program"] = json.loads(plan.pop("program_json"))
    plan["floorplan"] = json.loads(plan.pop("floorplan_json"))
    plan["norms_issues"] = json.loads(plan.pop("norms_issues_json") or "[]")
    return plan


def list_house_plans() -> list[dict]:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id, building_kind, name, description, created_at FROM house_plans ORDER BY id DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def delete_house_plan(plan_id: int):
    conn = get_db()
    conn.execute("DELETE FROM house_plans WHERE id = ?", (plan_id,))
    conn.commit()
    conn.close()


# Инициализация при импорте
init_db()
