# 盖印席互斥复现与二验说明（watermark-bug-06）

## 背景缺陷

同秒两侧值班（owner=a / owner=b）抢同一盖印席时，旧实现 `app/services/seat.py`：

- `claim_seat` 用 `INSERT OR REPLACE INTO meta` 按 `seat_{批次}_{值班}` 各写各的 key，无互斥；
- `stamp_occupied` 不查批次状态，直接 UPDATE 覆盖水印，并向 meta 再写一枚 `formal_wm_{批次}_{值班}`。

后果：库里落下两枚正式印记、批次水印被覆盖成 `值/owner` 形式翻倍，顺藤回看出现重复来源。

## 选边结论（工程凭证二选一）

采用 **唯一索引凭证**（未采用旁路占用表原子切换）：

- `seat_occupancies.batch_id`、`formal_stamps.batch_id` 各建 UNIQUE 索引；
- 占席、正式印记、批次状态在**同一事务**内一次落档，同源提交；
- 后到请求撞 `sqlite3.IntegrityError`，整笔回滚，接口回 **409 中文冲突短句**；
- 后到请求的命运：失败、无半笔写入、库内仍只有一枚正式印记。

## 一验：功能验收（串行 + 页面四处核查点）

```bash
pip install -r requirements.txt
pytest -q
python run.py        # http://127.0.0.1:7000 ，admin / 123456
```

| 核查点 | 位置 | 预期 |
|---|---|---|
| 占用目录 | 底栏「席位」页 | 亮「唯一索引」芯片，每批次至多一条占用，含值班与占用时间 |
| 打标列表 | 批次页表格「席位」列 | 占用亮「唯一索引 · a 值」，未占用亮「空闲」 |
| 批次卡片 | 点「盖印」后的卡片 | 标注互斥凭证「唯一索引」，可选左侧 a / 右侧 b 值班 |
| 回看进度 | 追索页「正式印记」区 | 同一水印值只亮一条正式印记（来源 formal_stamps） |

接口：`GET /api/seats` 返回 `{items, mutex_token:"unique_index"}`；
盖印失败响应 `{"ok":false,"error":"<中文短句>"}`，HTTP 409。

## 二验：同秒并发抢席（真互斥证据）

### 方式 A：自动化（推荐）

```bash
pytest tests/test_seat_mutex.py -q
```

含 5 项：线程屏障同秒并发仅一胜一负（409 中文）、胜方水印与正式印记同源、
占用目录一条、回看一条、绕过服务层直写第二次必撞 IntegrityError。

### 方式 B：HTTP 手工复现

```bash
# 1. 登录
curl -c c.txt -X POST http://127.0.0.1:7000/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"123456"}'

# 2. 取一个待盖印批次 id
curl -s -b c.txt http://127.0.0.1:7000/api/batches

# 3. 两侧同秒抢同一席（BID 替换为上一步的批次编号）
curl -s -b c.txt -X POST http://127.0.0.1:7000/api/batches/$BID/stamp \
  -H 'Content-Type: application/json' \
  -d '{"watermark_value":"WM-CONC-A","owner":"a"}' -w " a:%{http_code}\n" &
curl -s -b c.txt -X POST http://127.0.0.1:7000/api/batches/$BID/stamp \
  -H 'Content-Type: application/json' \
  -d '{"watermark_value":"WM-CONC-B","owner":"b"}' -w " b:%{http_code}\n" &
wait

# 4. 库内核验：一条占用、一枚印记、无 meta 双写残留
sqlite3 instance/watermark_lab.db \
  "SELECT * FROM seat_occupancies WHERE batch_id=$BID;
   SELECT * FROM formal_stamps WHERE batch_id=$BID;
   SELECT key FROM meta WHERE key LIKE 'seat_%' OR key LIKE 'formal_wm_%';"
```

预期：一侧 200、另一侧 409；两条查询各只回一行；第三条查询无行。
追索 `WM-CONC-B`（败方水印）应 404「未找到该水印值的盖印批次」。

### 本机库快速核验

```bash
sqlite3 instance/watermark_lab.db ".indexes"   # 见 idx_seat_occupancies_batch / idx_formal_stamps_batch
```

## 同源与双写红线

- 正式印记唯一来源：`formal_stamps` 表；`run_batches.watermark_value` 与它同事务更新。
- 旧 `meta` 中的 `seat_*` / `formal_wm_*` 键在 `init_db` 启动时自动清理，老库无需手工迁移。
- 只改前端提示不改互斥写库 = 验收失败；互斥以数据库唯一索引为准，不依赖应用层判断顺序。
