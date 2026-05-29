import os
import aiosqlite
from contextlib import asynccontextmanager

DB_PATH = os.environ.get("OPENHOST_SQLITE_DASHBOARD", "dashboard.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

DEFAULTS = {
    "distance_unit": "mi",
    "elevation_unit": "ft",
    "temp_unit": "C",
    "hr_zone_1": "112",
    "hr_zone_2": "131",
    "hr_zone_3": "150",
    "hr_zone_4": "168",
    "hr_zone_5": "187",
}


async def init_db():
    async with connect() as db:
        await db.executescript(SCHEMA)
        await db.commit()


@asynccontextmanager
async def connect():
    db = await aiosqlite.connect(DB_PATH)
    try:
        yield db
    finally:
        await db.close()


async def get_settings() -> dict:
    result = dict(DEFAULTS)
    async with connect() as db:
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        for key, value in rows:
            result[key] = value
    return result


async def set_setting(key: str, value: str):
    async with connect() as db:
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()
