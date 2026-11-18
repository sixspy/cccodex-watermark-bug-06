from app.db import get_db
from app.services.errors import ServiceError


def trace_downstream(watermark_value: str, db_path: str | None = None) -> dict:
    value = (watermark_value or "").strip()
    if not value:
        raise ServiceError("水印值不能为空")
    with get_db(db_path) as conn:
        # 正式印记只认 formal_stamps：一个批次一枚，回看只亮一条
        stamps = conn.execute(
            "SELECT f.* FROM formal_stamps f "
            "WHERE f.watermark_value=? ORDER BY f.id",
            (value,),
        ).fetchall()
        if not stamps:
            raise ServiceError("未找到该水印值的盖印批次", 404)
        batch_ids = [s["batch_id"] for s in stamps]
        ph_ids = ",".join("?" for _ in batch_ids)
        sources = conn.execute(
            f"SELECT * FROM run_batches WHERE id IN ({ph_ids}) ORDER BY id",
            batch_ids,
        ).fetchall()
        codes = [s["run_code"] for s in sources]
        placeholders = ",".join("?" for _ in codes)
        edges = conn.execute(
            f"SELECT * FROM lineage_edges WHERE upstream_run_code IN ({placeholders})",
            codes,
        ).fetchall()
        down_codes = sorted({e["downstream_run_code"] for e in edges})
        downstream = []
        if down_codes:
            ph2 = ",".join("?" for _ in down_codes)
            downstream = [
                dict(r)
                for r in conn.execute(
                    f"SELECT * FROM run_batches WHERE run_code IN ({ph2}) ORDER BY id",
                    down_codes,
                ).fetchall()
            ]
        return {
            "watermark_value": value,
            "sources": [dict(s) for s in sources],
            "formal_stamps": [dict(s) for s in stamps],
            "edges": [dict(e) for e in edges],
            "downstream": downstream,
        }
