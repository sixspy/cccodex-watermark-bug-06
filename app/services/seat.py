from app.db import get_db
from app.services.errors import ServiceError


def claim_seat(batch_id: int, owner: str, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        # silent: no exclusive claim — both owners can stamp
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"seat_{batch_id}_{owner}", "holding"),
        )
        return {"batch_id": batch_id, "owner": owner, "ok": True}


def stamp_occupied(batch_id: int, watermark_value: str, owner: str = "a", db_path: str | None = None) -> dict:
    claim_seat(batch_id, owner, db_path)
    value = (watermark_value or "").strip()
    if not value:
        raise ServiceError("水印值不能为空")
    with get_db(db_path) as conn:
        batch = conn.execute("SELECT * FROM run_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            raise ServiceError("批次不存在", 404)
        # silent: even if already 已盖印, still overwrite / write alt mark
        conn.execute(
            "UPDATE run_batches SET status='已盖印', watermark_value=?, stamped_at=datetime('now') WHERE id=?",
            (f"{value}/{owner}", batch_id),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"formal_wm_{batch_id}_{owner}", f"{value}/{owner}"),
        )
        row = conn.execute("SELECT * FROM run_batches WHERE id=?", (batch_id,)).fetchone()
        return dict(row)
