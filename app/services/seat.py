import sqlite3

from app.db import get_db
from app.services.errors import ServiceError

# 互斥凭证：seat_occupancies.batch_id / formal_stamps.batch_id 的唯一索引。
# 两侧值班同秒抢席时，后到的 INSERT 必撞 IntegrityError，整笔事务回滚。
_CONFLICT_MESSAGES = {
    "seat_occupancies": "盖印席已被占用，请稍后再试",
    "formal_stamps": "该批次已存在正式印记，禁止重复盖印",
}


def _conflict_message(exc: sqlite3.IntegrityError) -> str:
    text = str(exc)
    for table, message in _CONFLICT_MESSAGES.items():
        if table in text:
            return message
    return "盖印冲突，本次写入已回滚"


def list_seats(db_path: str | None = None) -> list[dict]:
    """占用目录：当前所有席位占用情况（页面占用目录数据源）。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT s.batch_id, b.run_code, b.table_name, s.owner, s.occupied_at "
            "FROM seat_occupancies s JOIN run_batches b ON b.id=s.batch_id "
            "ORDER BY s.batch_id"
        ).fetchall()
        return [dict(r) for r in rows]


def stamp_occupied(
    batch_id: int,
    watermark_value: str,
    owner: str = "a",
    db_path: str | None = None,
) -> dict:
    """按唯一索引凭证一次落档：占席、正式印记、批次状态在同一事务内同源提交。

    后到的抢席写入被唯一索引挡住时抛 IntegrityError，get_db 回滚整笔，
    对外转成 409 中文冲突短句，库内不留半笔、不产生第二枚印记。
    """
    value = (watermark_value or "").strip()
    if not value:
        raise ServiceError("水印值不能为空")
    owner = (owner or "").strip() or "a"
    with get_db(db_path) as conn:
        batch = conn.execute(
            "SELECT * FROM run_batches WHERE id=?", (batch_id,)
        ).fetchone()
        if not batch:
            raise ServiceError("批次不存在", 404)
        if batch["status"] == "已盖印":
            raise ServiceError("批次已盖印", 409)
        if batch["status"] != "待盖印":
            raise ServiceError("仅待盖印批次可盖印", 409)
        tpl = conn.execute(
            "SELECT * FROM watermark_templates WHERE table_name=? AND status='现行'",
            (batch["table_name"],),
        ).fetchone()
        if not tpl:
            raise ServiceError("无现行水印模板", 409)
        try:
            # 1) 抢席：batch_id 唯一索引，后到者在此被挡
            conn.execute(
                "INSERT INTO seat_occupancies(batch_id, owner) VALUES (?, ?)",
                (batch_id, owner),
            )
            # 2) 正式印记：同源同事务，batch_id 唯一索引兜底重复盖印
            conn.execute(
                "INSERT INTO formal_stamps(batch_id, watermark_value, owner) "
                "VALUES (?, ?, ?)",
                (batch_id, value, owner),
            )
            # 3) 批次状态与印记同源更新，不再散落 meta 双写
            conn.execute(
                "UPDATE run_batches SET status='已盖印', watermark_value=?, "
                "stamped_at=datetime('now') WHERE id=?",
                (value, batch_id),
            )
        except sqlite3.IntegrityError as exc:
            raise ServiceError(_conflict_message(exc), 409) from exc
        row = conn.execute(
            "SELECT * FROM run_batches WHERE id=?", (batch_id,)
        ).fetchone()
        return dict(row)


# 旧调用点兼容：claim_seat 不再独立静默占席，占席只在盖印事务内发生
def claim_seat(batch_id: int, owner: str, db_path: str | None = None) -> dict:
    raise ServiceError("席位仅在盖印时按凭证一次性占用", 409)
