import aiosqlite
from datetime import date

DB_PATH = "bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id      INTEGER PRIMARY KEY,
    username     TEXT,
    first_seen   TEXT NOT NULL,
    last_seen    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    src_fmt      TEXT,
    dst_fmt      TEXT,
    bytes_in     INTEGER,
    bytes_out    INTEGER,
    ok           INTEGER NOT NULL,
    ts           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_usage (
    user_id      INTEGER NOT NULL,
    day          TEXT NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def upsert_user(user_id: int, username: str | None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO users(user_id, username, first_seen, last_seen)
               VALUES(?,?,datetime('now'),datetime('now'))
               ON CONFLICT(user_id) DO UPDATE
                 SET last_seen=datetime('now'), username=excluded.username""",
            (user_id, username),
        )
        await db.commit()

async def record_conversion(user_id, src, dst, size_in, size_out, ok: bool):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO conversions
               (user_id, src_fmt, dst_fmt, bytes_in, bytes_out, ok, ts)
               VALUES (?,?,?,?,?,?,datetime('now'))""",
            (user_id, src, dst, size_in, size_out, int(ok)),
        )
        await db.commit()

async def bump_daily(user_id: int) -> int:
    today = date.today().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO daily_usage(user_id, day, count) VALUES(?,?,1)
               ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1""",
            (user_id, today),
        )
        await db.commit()
        async with db.execute(
            "SELECT count FROM daily_usage WHERE user_id=? AND day=?",
            (user_id, today),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0

async def set_kv(k: str, v: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
        await db.commit()

async def get_kv(k: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT v FROM kv WHERE k=?", (k,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

async def stats_snapshot() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async def one(q, *p):
            async with db.execute(q, p) as c:
                r = await c.fetchone()
                return r[0] if r else 0
        return {
            "users":        await one("SELECT COUNT(*) FROM users"),
            "conversions":  await one("SELECT COUNT(*) FROM conversions WHERE ok=1"),
            "failures":     await one("SELECT COUNT(*) FROM conversions WHERE ok=0"),
            "today":        await one("SELECT COUNT(*) FROM conversions WHERE ok=1 AND date(ts)=date('now')"),
            "top_dst":      await one("SELECT dst_fmt FROM conversions WHERE ok=1 GROUP BY dst_fmt ORDER BY COUNT(*) DESC LIMIT 1") or "-",
        }
