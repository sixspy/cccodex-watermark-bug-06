"""同秒两侧值班抢同一盖印席的互斥验收（watermark-bug-06）。

工程凭证：seat_occupancies.batch_id / formal_stamps.batch_id 唯一索引。
后到写入被索引挡住并整笔回滚，接口回 409 中文冲突；
库内一枚正式印记、一条占用，顺藤回看只亮一条。
"""
import sqlite3
import threading

import pytest

from app.db import get_db, init_db
from app.services.errors import ServiceError
from app.services.seat import list_seats, stamp_occupied
from app.services.trace import trace_downstream


@pytest.mark.asyncio
async def test_seat_directory_and_http_conflict(authed):
    batches = (await (await authed.get("/api/batches")).json())["items"]
    bid = next(b["id"] for b in batches if b["status"] == "待盖印")

    seats_before = await (await authed.get("/api/seats")).json()
    assert seats_before["mutex_token"] == "unique_index"

    first = await authed.post(
        f"/api/batches/{bid}/stamp",
        json={"watermark_value": "WM-HTTP-1", "owner": "a"},
    )
    assert first.status == 200
    assert (await first.json())["mutex_token"] == "unique_index"

    second = await authed.post(
        f"/api/batches/{bid}/stamp",
        json={"watermark_value": "WM-HTTP-2", "owner": "b"},
    )
    assert second.status == 409
    err = (await second.json())["error"]
    assert err and all("一" <= ch <= "鿿" or ch in "，。 " for ch in err)

    seats_after = await (await authed.get("/api/seats")).json()
    mine = [s for s in seats_after["items"] if s["batch_id"] == bid]
    assert len(mine) == 1
    assert mine[0]["owner"] == "a"

    trace = await authed.post("/api/trace", json={"watermark_value": "WM-HTTP-1"})
    body = await trace.json()
    assert trace.status == 200
    assert len(body["formal_stamps"]) == 1



def _pending_batch(db_path: str) -> int:
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM run_batches WHERE status='待盖印' ORDER BY id LIMIT 1"
        ).fetchone()
        return row["id"]


def test_parallel_claim_only_one_wins(tmp_path):
    db = str(tmp_path / "race.db")
    init_db(db)
    bid = _pending_batch(db)
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def worker(owner: str) -> None:
        barrier.wait()  # 两侧同秒发起
        try:
            batch = stamp_occupied(bid, f"WM-RACE-{owner}", owner=owner, db_path=db)
            results.append({"owner": owner, "ok": True, "batch": batch})
        except ServiceError as exc:
            results.append({"owner": owner, "ok": False, "msg": exc.message, "status": exc.status_code})

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    winners = [r for r in results if r["ok"]]
    losers = [r for r in results if not r["ok"]]
    assert len(winners) == 1
    assert len(losers) == 1
    loser = losers[0]
    assert loser["status"] == 409
    assert loser["msg"]  # 中文冲突短句，非裸数据库报错

    # 库里只落一枚正式印记、一条占用
    with get_db(db) as conn:
        seats = conn.execute(
            "SELECT * FROM seat_occupancies WHERE batch_id=?", (bid,)
        ).fetchall()
        stamps = conn.execute(
            "SELECT * FROM formal_stamps WHERE batch_id=?", (bid,)
        ).fetchall()
        batch = conn.execute(
            "SELECT * FROM run_batches WHERE id=?", (bid,)
        ).fetchone()
        meta_leak = conn.execute(
            "SELECT COUNT(*) AS c FROM meta WHERE key LIKE 'seat_%' OR key LIKE 'formal_wm_%'"
        ).fetchone()["c"]
    assert len(seats) == 1
    assert seats[0]["owner"] == winners[0]["owner"]
    assert len(stamps) == 1
    assert stamps[0]["watermark_value"] == winners[0]["batch"]["watermark_value"]
    assert batch["status"] == "已盖印"
    assert batch["watermark_value"] == stamps[0]["watermark_value"]  # 占用态与正式印记同源
    assert meta_leak == 0  # 旧 meta 双写已清除

    # 占用目录只有一条
    seats_view = list_seats(db_path=db)
    assert len([s for s in seats_view if s["batch_id"] == bid]) == 1

    # 顺藤回看只亮一条正式印记
    view = trace_downstream(stamps[0]["watermark_value"], db_path=db)
    assert len(view["formal_stamps"]) == 1
    assert view["formal_stamps"][0]["id"] == stamps[0]["id"]


def test_second_stamp_after_commit_is_conflict(tmp_path):
    db = str(tmp_path / "seq.db")
    init_db(db)
    bid = _pending_batch(db)
    first = stamp_occupied(bid, "WM-SEQ-1", owner="a", db_path=db)
    assert first["status"] == "已盖印"
    with pytest.raises(ServiceError) as exc:
        stamp_occupied(bid, "WM-SEQ-2", owner="b", db_path=db)
    assert exc.value.status_code == 409
    with get_db(db) as conn:
        stamps = conn.execute(
            "SELECT * FROM formal_stamps WHERE batch_id=?", (bid,)
        ).fetchall()
    assert len(stamps) == 1
    assert stamps[0]["watermark_value"] == "WM-SEQ-1"


def test_loser_rolls_back_partial_writes(tmp_path):
    db = str(tmp_path / "rollback.db")
    init_db(db)
    bid = _pending_batch(db)
    stamp_occupied(bid, "WM-FIRST", owner="a", db_path=db)
    # 后到者撞索引后，不得留下任何半笔
    with pytest.raises(ServiceError):
        stamp_occupied(bid, "WM-SECOND", owner="b", db_path=db)
    with get_db(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM seat_occupancies WHERE batch_id=?", (bid,)
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM formal_stamps WHERE batch_id=?", (bid,)
        ).fetchone()["c"] == 1
        assert conn.execute(
            "SELECT watermark_value FROM run_batches WHERE id=?", (bid,)
        ).fetchone()["watermark_value"] == "WM-FIRST"


def test_raw_sqlite_integrity_error_is_the_guard(tmp_path):
    """凭证本身核验：绕过服务层直接写第二次，唯一索引必须拒绝。"""
    db = str(tmp_path / "raw.db")
    init_db(db)
    bid = _pending_batch(db)
    stamp_occupied(bid, "WM-RAW", owner="a", db_path=db)
    with sqlite3.connect(db) as raw:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO formal_stamps(batch_id, watermark_value, owner) VALUES (?,?,?)",
                (bid, "WM-RAW-2", "b"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO seat_occupancies(batch_id, owner) VALUES (?,?)",
                (bid, "b"),
            )
