(() => {
  const view = document.getElementById("view");
  const msgEl = document.getElementById("msg");
  let tab = "batches";
  let templates = [];
  let batches = [];
  let reports = [];
  let seats = [];
  let mutexToken = "unique_index";
  let selected = null;
  let stampValue = "";
  let stampOwner = "a";
  let traceValue = "WM-ORD-131";
  let traceResult = null;

  async function api(path, opts = {}) {
    const r = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (r.status === 401) {
      location.href = "/login";
      throw new Error("未登录");
    }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }

  function setMsg(t) {
    msgEl.textContent = t || "";
  }

  async function refresh() {
    const meta = await api("/api/meta");
    document.getElementById("lab-name").textContent = meta.lab_name || "血缘水印台";
    templates = (await api("/api/templates")).items || [];
    batches = (await api("/api/batches")).items || [];
    reports = (await api("/api/gap-reports")).items || [];
    const seatData = await api("/api/seats");
    seats = seatData.items || [];
    mutexToken = seatData.mutex_token || "unique_index";
  }

  function mutexLabel() {
    return mutexToken === "unique_index" ? "唯一索引" : "原子切换";
  }

  function seatOf(batchId) {
    return seats.find((s) => s.batch_id === batchId) || null;
  }

  function syncDock() {
    document.querySelectorAll(".dock [data-tab]").forEach((btn) => {
      btn.classList.toggle("on", btn.dataset.tab === tab);
    });
  }

  function renderTemplates() {
    return `
      <h2>水印模板</h2>
      <table>
        <thead><tr><th>表</th><th>列</th><th>版本</th><th>状态</th></tr></thead>
        <tbody>
          ${templates
            .map(
              (t) => `<tr>
            <td>${t.table_name}</td>
            <td>${t.column_name}</td>
            <td>${t.version_no}</td>
            <td><span class="chip">${t.status}</span></td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function renderBatches() {
    return `
      <h2>运行批次</h2>
      <p class="muted">主链：选批盖印 → 用水印追索下游 → 缺口对账</p>
      <table>
        <thead><tr><th>编号</th><th>表</th><th>运行码</th><th>业务日</th><th>状态</th><th>水印</th><th>席位</th><th></th></tr></thead>
        <tbody>
          ${batches
            .map(
              (b) => {
                const seat = seatOf(b.id);
                return `<tr>
            <td>${b.id}</td>
            <td>${b.table_name}</td>
            <td>${b.run_code}</td>
            <td>${b.biz_date}</td>
            <td><span class="chip">${b.status}</span></td>
            <td>${b.watermark_value || "-"}</td>
            <td>${
              seat
                ? `<span class="chip chip-seat">${mutexLabel()} · ${seat.owner} 值</span>`
                : `<span class="chip chip-idle">空闲</span>`
            }</td>
            <td>${
              b.status === "待盖印"
                ? `<button type="button" class="ghost" data-pick="${b.id}">盖印</button>`
                : ""
            }</td>
          </tr>`;
              }
            )
            .join("")}
        </tbody>
      </table>
      ${
        selected
          ? `<div class="cardish">
        <h3>盖印</h3>
        <p>${selected.run_code} · ${selected.table_name}</p>
        <p class="muted small">互斥凭证：<span class="chip chip-seat">${mutexLabel()}</span> 同秒两侧抢席时，后到写入被唯一索引挡回并整笔回滚</p>
        <label>值班
          <select id="stamp-owner">
            <option value="a"${stampOwner === "a" ? " selected" : ""}>左侧值班 a</option>
            <option value="b"${stampOwner === "b" ? " selected" : ""}>右侧值班 b</option>
          </select>
        </label>
        <label>水印值<input id="stamp-input" value="${stampValue}" /></label>
        <button type="button" id="btn-stamp">写入盖印</button>
      </div>`
          : ""
      }`;
  }

  function renderSeats() {
    return `
      <h2>占用目录</h2>
      <p class="muted small">互斥凭证：<span class="chip chip-seat">${mutexLabel()}</span>（seat_occupancies.batch_id 唯一索引，后到写入回中文冲突）</p>
      <table>
        <thead><tr><th>批次编号</th><th>运行码</th><th>表</th><th>占用值班</th><th>占用时间</th></tr></thead>
        <tbody>
          ${
            seats.length
              ? seats
                  .map(
                    (s) => `<tr>
              <td>${s.batch_id}</td>
              <td>${s.run_code}</td>
              <td>${s.table_name}</td>
              <td><span class="chip chip-seat">${s.owner} 值</span></td>
              <td>${s.occupied_at}</td>
            </tr>`
                  )
                  .join("")
              : `<tr><td colspan="5" class="muted">暂无占用，所有席位空闲</td></tr>`
          }
        </tbody>
      </table>`;
  }

  function renderTrace() {
    const src = (traceResult && traceResult.sources) || [];
    const stamps = (traceResult && traceResult.formal_stamps) || [];
    const down = (traceResult && traceResult.downstream) || [];
    return `
      <h2>下游追索</h2>
      <label>水印值<input id="trace-input" value="${traceValue}" placeholder="例如 WM-ORD-131" /></label>
      <div class="row"><button type="button" id="btn-trace">追索</button></div>
      ${
        traceResult
          ? `<div>
        <h3>正式印记（回看进度）</h3>
        <ul>${
          stamps.length
            ? stamps
                .map(
                  (s) =>
                    `<li><span class="chip chip-seat">印记 #${s.id}</span> 批次 ${s.batch_id} · ${s.watermark_value} · ${s.owner} 值 · ${s.stamped_at}</li>`
                )
                .join("")
            : `<li class="muted">无正式印记</li>`
        }</ul>
        <h3>源批次</h3>
        <ul>${src.map((s) => `<li>${s.run_code} · ${s.table_name}</li>`).join("")}</ul>
        <h3>下游</h3>
        <ul>${down
          .map(
            (d) =>
              `<li>${d.run_code} · ${d.status} · ${d.watermark_value || "未盖印"}</li>`
          )
          .join("")}</ul>
      </div>`
          : ""
      }`;
  }

  function renderGaps() {
    return `
      <h2>缺口对账</h2>
      <button type="button" id="btn-gap">生成对账</button>
      <table>
        <thead><tr><th>编号</th><th>截至</th><th>缺口</th><th>摘要</th></tr></thead>
        <tbody>
          ${reports
            .map(
              (r) => `<tr>
            <td>${r.id}</td>
            <td>${r.as_of_date}</td>
            <td>${r.gap_count}</td>
            <td>${r.summary}</td>
          </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  }

  function render() {
    if (tab === "templates") view.innerHTML = renderTemplates();
    else if (tab === "batches") view.innerHTML = renderBatches();
    else if (tab === "seats") view.innerHTML = renderSeats();
    else if (tab === "trace") view.innerHTML = renderTrace();
    else view.innerHTML = renderGaps();

    view.querySelectorAll("[data-pick]").forEach((btn) => {
      btn.onclick = () => {
        const id = Number(btn.dataset.pick);
        selected = batches.find((b) => b.id === id) || null;
        stampValue = selected ? "WM-" + selected.run_code.toUpperCase() : "";
        render();
      };
    });
    const stampInput = document.getElementById("stamp-input");
    if (stampInput) {
      stampInput.oninput = () => (stampValue = stampInput.value);
      const ownerSelect = document.getElementById("stamp-owner");
      if (ownerSelect) {
        ownerSelect.onchange = () => (stampOwner = ownerSelect.value);
      }
      document.getElementById("btn-stamp").onclick = async () => {
        setMsg("");
        try {
          await api("/api/batches/" + selected.id + "/stamp", {
            method: "POST",
            body: JSON.stringify({ watermark_value: stampValue, owner: stampOwner }),
          });
          selected = null;
          await refresh();
          setMsg("盖印完成");
          render();
        } catch (e) {
          // 后到抢席被唯一索引挡回：服务端短句直接上屏
          setMsg(e.message);
        }
      };
    }
    const traceInput = document.getElementById("trace-input");
    if (traceInput) {
      traceInput.oninput = () => (traceValue = traceInput.value);
      document.getElementById("btn-trace").onclick = async () => {
        setMsg("");
        try {
          traceResult = await api("/api/trace", {
            method: "POST",
            body: JSON.stringify({ watermark_value: traceValue }),
          });
          setMsg("追索完成");
          render();
        } catch (e) {
          setMsg(e.message);
        }
      };
    }
    const gapBtn = document.getElementById("btn-gap");
    if (gapBtn) {
      gapBtn.onclick = async () => {
        setMsg("");
        try {
          const r = await api("/api/gap-reports", {
            method: "POST",
            body: JSON.stringify({ as_of_date: "2026-11-16" }),
          });
          await refresh();
          setMsg(r.report.summary);
          render();
        } catch (e) {
          setMsg(e.message);
        }
      };
    }
  }

  document.querySelectorAll(".dock [data-tab]").forEach((btn) => {
    btn.onclick = () => {
      tab = btn.dataset.tab;
      syncDock();
      render();
    };
  });
  document.getElementById("btn-logout").onclick = () => {
    fetch("/api/logout", { method: "POST" }).then(() => (location.href = "/login"));
  };

  refresh()
    .then(() => {
      syncDock();
      render();
    })
    .catch((e) => setMsg(e.message));
})();
