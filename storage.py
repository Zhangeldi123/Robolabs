import sqlite3
from dataclasses import dataclass
from typing import Optional

DB_PATH = "bot.db"

@dataclass
class Lead:
    tg_id: int
    name: str
    age_group: str
    level: str
    goal: str
    schedule: str
    contact: str

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            tg_id INTEGER PRIMARY KEY,
            name TEXT,
            age_group TEXT,
            level TEXT,
            goal TEXT,
            schedule TEXT,
            contact TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        con.commit()

def upsert_lead(lead: Lead) -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute("""
        INSERT INTO leads (tg_id, name, age_group, level, goal, schedule, contact)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tg_id) DO UPDATE SET
            name=excluded.name,
            age_group=excluded.age_group,
            level=excluded.level,
            goal=excluded.goal,
            schedule=excluded.schedule,
            contact=excluded.contact
        """, (lead.tg_id, lead.name, lead.age_group, lead.level, lead.goal, lead.schedule, lead.contact))
        con.commit()

def count_leads() -> int:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT COUNT(*) FROM leads")
        return int(cur.fetchone()[0])

def get_lead(tg_id: int) -> Optional[Lead]:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("""
        SELECT tg_id, name, age_group, level, goal, schedule, contact
        FROM leads WHERE tg_id=?
        """, (tg_id,))
        row = cur.fetchone()
        if not row:
            return None
        return Lead(*row)
