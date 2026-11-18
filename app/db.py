import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import Config


def connect(db_path: str | None = None) -> sqlite3.Connection:
    path = db_path or Config.database()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db(db_path: str | None = None):
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS watermark_templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL UNIQUE,
  column_name TEXT NOT NULL,
  version_no INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK (status IN ('现行','停用')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS run_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name TEXT NOT NULL,
  run_code TEXT NOT NULL UNIQUE,
  biz_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('待盖印','已盖印','已作废')),
  watermark_value TEXT,
  stamped_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS lineage_edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  upstream_run_code TEXT NOT NULL,
  downstream_run_code TEXT NOT NULL,
  UNIQUE(upstream_run_code, downstream_run_code)
);

CREATE TABLE IF NOT EXISTS gap_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of_date TEXT NOT NULL,
  expected_count INTEGER NOT NULL,
  stamped_count INTEGER NOT NULL,
  gap_count INTEGER NOT NULL,
  summary TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 盖印席位占用：每个批次至多一个占用者，batch_id 上的唯一索引即互斥凭证
CREATE TABLE IF NOT EXISTS seat_occupancies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL UNIQUE REFERENCES run_batches(id),
  owner TEXT NOT NULL,
  occupied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 正式印记：一个批次只落一枚，batch_id 唯一索引挡住后到写入
CREATE TABLE IF NOT EXISTS formal_stamps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id INTEGER NOT NULL UNIQUE REFERENCES run_batches(id),
  watermark_value TEXT NOT NULL,
  owner TEXT NOT NULL,
  stamped_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# 互斥凭证：唯一索引。并发抢席时后到的 INSERT 必然触发 IntegrityError，整笔回滚。
SEAT_BATCH_UNIQUE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_seat_occupancies_batch "
    "ON seat_occupancies(batch_id)"
)
FORMAL_BATCH_UNIQUE = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_formal_stamps_batch "
    "ON formal_stamps(batch_id)"
)

SEED_TEMPLATES = [
    ("dw_orders_d", "wm_batch", 1, "现行"),
    ("dw_payments_d", "wm_batch", 1, "现行"),
    ("dm_gmv_d", "wm_trace", 1, "现行"),
]

SEED_BATCHES = [
    ("dw_orders_d", "ord-20261114-01", "2026-11-14", "待盖印", None),
    ("dw_payments_d", "pay-20261114-01", "2026-11-14", "待盖印", None),
    ("dm_gmv_d", "gmv-20261114-01", "2026-11-14", "待盖印", None),
    ("dw_orders_d", "ord-20261113-01", "2026-11-13", "已盖印", "WM-ORD-131"),
]

SEED_EDGES = [
    ("ord-20261114-01", "gmv-20261114-01"),
    ("pay-20261114-01", "gmv-20261114-01"),
    ("ord-20261113-01", "gmv-20261114-01"),
]


def _backfill_formal_stamps(conn) -> None:
    """已盖印批次补登同源正式印记（老库迁移 / 种子后回填，同一逻辑）。"""
    rows = conn.execute(
        "SELECT b.id, b.watermark_value, b.stamped_at FROM run_batches b "
        "WHERE b.status='已盖印' AND b.watermark_value IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM formal_stamps f WHERE f.batch_id=b.id)"
    ).fetchall()
    for r in rows:
        conn.execute(
            "INSERT INTO formal_stamps(batch_id, watermark_value, owner, stamped_at) "
            "VALUES (?,?,?,?)",
            (r["id"], r["watermark_value"], "system", r["stamped_at"]),
        )
    # 清理旧版本散落 meta 的席位/印记双写痕迹，避免同源数据两处存放
    conn.execute(
        "DELETE FROM meta WHERE key LIKE 'seat\\_%' ESCAPE '\\' "
        "OR key LIKE 'formal_wm\\_%' ESCAPE '\\'"
    )


def init_db(db_path: str | None = None) -> None:
    with get_db(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.execute(SEAT_BATCH_UNIQUE)
        conn.execute(FORMAL_BATCH_UNIQUE)
        row = conn.execute("SELECT value FROM meta WHERE key='seeded'").fetchone()
        if row:
            _backfill_formal_stamps(conn)
            return
        for table, col, ver, status in SEED_TEMPLATES:
            conn.execute(
                "INSERT INTO watermark_templates(table_name, column_name, version_no, status) "
                "VALUES (?,?,?,?)",
                (table, col, ver, status),
            )
        for table, code, biz, status, wm in SEED_BATCHES:
            conn.execute(
                "INSERT INTO run_batches(table_name, run_code, biz_date, status, watermark_value, stamped_at) "
                "VALUES (?,?,?,?,?,?)",
                (
                    table,
                    code,
                    biz,
                    status,
                    wm,
                    "2026-11-13 18:00:00" if status == "已盖印" else None,
                ),
            )
        for up, down in SEED_EDGES:
            conn.execute(
                "INSERT INTO lineage_edges(upstream_run_code, downstream_run_code) VALUES (?,?)",
                (up, down),
            )
        conn.execute("INSERT INTO meta(key, value) VALUES ('seeded','1')")
        _backfill_formal_stamps(conn)
