const $ = (s) => document.querySelector(s);
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html != null) n.innerHTML = html;
  return n;
};
const money = (v, cur = "CNY") =>
  v == null ? "—" : (cur === "USD" ? "$" : "¥") + v.toFixed(2);
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
/** 券还有几天到期。返回 null 表示没有截止时间，<=0 表示已过期。 */
const daysLeft = (unix) => {
  if (!unix) return null;
  return Math.ceil((unix * 1000 - Date.now()) / 86400000);
};
const LOW_STATES = {
  super: ["超史低", "low super", "实付比 Steam 官方史低还低 —— 跨渠道破纪录"],
  new: ["★ 星史低", "low new", "比这台机器之前记录的任何价格都低"],
  equal: ["平史低", "low", "和之前记录的最低价格持平"],
  near: ["接近史低", "near", "离历史最低不到 10%"],
  first: ["首采", "", "只采到过一条价格，还谈不上史低"],
  none: ["未分级", "", "有价格但还构不成任何史低信号"],
};
// 筛选按钮的顺序和短名。后端 api_deals() 的 low_state 取值就是这几个 key。
const STATE_ORDER = ["super", "new", "equal", "near", "first", "none"];
const STATE_SHORT = {
  super: "超史低", new: "★星史低", equal: "平史低",
  near: "接近史低", first: "首采", none: "未分级",
};
const lowBadge = (item) => {
  const s = LOW_STATES[item.low_state];
  if (!s || item.low_state === "first") return "";
  return `<span class="badge ${s[1]}" title="${s[2]}">${s[0]}</span>`;
};
const reviewBadge = (item) => {
  if (item.review_score == null) return "";
  const n = item.review_count || 0;
  const cnt = n >= 10000 ? (n / 10000).toFixed(1) + "万" : String(n);
  return `<span class="badge" title="Steam 好评率 ${item.review_score}%（${n} 条评价）">好评 ${item.review_score}% · ${cnt}</span>`;
};

async function api(path, opt) {
  const r = await fetch(path, opt);
  return r.json();
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tabs button").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("#tab-" + b.dataset.tab).classList.add("active");
    if (b.dataset.tab === "taste") loadTaste();
    if (b.dataset.tab === "rec") loadRec();
    if (b.dataset.tab === "free") loadFree();
  });
});

/* ---------- stats ---------- */
async function loadStats() {
  const s = await api("/api/stats");
  $("#stats").textContent =
    `库内 ${s.games} 个游戏 · ${s.prices} 条价格快照 · ${s.snapshots} 次采集 · 画像 ${s.profile} 条`;
}

/* ---------- 卡片 ---------- */
function card(item, opts = {}) {
  const c = el("div", "card");
  c.dataset.appid = item.appid;
  const img = item.header_image || item.tiny_image || "";
  const dl = daysLeft(item.heybox_deadline);
  const couponTip = dl != null
    ? (dl <= 0
        ? `<span class="badge" style="color:#8b93a1">券已过期</span>`
        : dl <= 3
          ? `<span class="badge warn">券剩 ${dl} 天</span>`
          : `<span class="badge">券剩 ${dl} 天</span>`)
    : "";
  c.innerHTML = `
    ${img ? `<img class="img" loading="lazy" src="${esc(img)}" alt="">` : `<div class="img"></div>`}
    <div class="body">
      <div class="title">${esc(item.name_cn || item.name || item.title)}</div>
      <div class="meta">
        ${item.discount ? `<span class="badge off">-${item.discount}%</span>` : ""}
        ${lowBadge(item)}
        ${item.metacritic ? `<span class="badge">MC ${item.metacritic}</span>` : ""}
        ${reviewBadge(item)}
        ${item.adj != null ? `<span class="badge score">契合 ${item.adj.toFixed(3)}</span>` : ""}
        ${couponTip}
        ${item.shop ? `<span>${esc(item.shop)}</span>` : ""}
      </div>
      ${item.matched_tags?.length
        ? `<div class="tags">${item.matched_tags.slice(0, 5).map(([t]) => `<span class="t">${esc(t)}</span>`).join("")}</div>`
        : ""}
      <div class="price">
        ${item.price == null
          ? `<span class="meta">未采集价格</span>`
          : `<span class="now">${money(item.price, item.currency)}</span>
             ${item.list_price && item.list_price > item.price ? `<span class="was">${money(item.list_price, item.currency)}</span>` : ""}
             ${item.lowest != null && item.snapshots >= 2
               ? `<span class="meta">史低 ${money(item.lowest, item.currency)}</span>`
               : `<span class="meta" title="多跑几次采集后才会出现史低">首采</span>`}`}
      </div>
      ${item.heybox != null || item.steam_lowest != null
        ? `<div class="meta" style="font-size:11.5px">
             ${item.heybox != null ? `<span class="badge hb">小黑盒 ${money(item.heybox, item.currency)}</span>` : ""}
             ${item.steam_lowest != null ? `<span>Steam 史低 ${money(item.steam_lowest, item.currency)}</span>` : ""}
           </div>`
        : ""}
    </div>`;
  c.addEventListener("click", () => openGame(item.appid));
  return c;
}

/* ---------- 史低等级筛选 ----------
 * 默认全选（含「首采」）。理由：第一次装完只跑了半轮采集时，库里几乎全是「首采」，
 * 默认只勾前三级 → 打开就是空页面，看起来像坏了。宁可默认把话说全，
 * 再给「只看真史低 / 只看破纪录」两个一键预设。
 * 选择存 localStorage：下次打开还是你上次的口径。
 */
const LS_STATES = "gdr.states";
let selectedStates = new Set(STATE_ORDER);
try {
  const saved = JSON.parse(localStorage.getItem(LS_STATES) || "null");
  if (Array.isArray(saved) && saved.length) {
    selectedStates = new Set(saved.filter((s) => STATE_ORDER.includes(s)));
    if (!selectedStates.size) selectedStates = new Set(STATE_ORDER);
  }
} catch (_e) { /* localStorage 不可用就用默认值，别让整个页面挂掉 */ }

// ?states=super,new 深链：刷新/贴给别人也能复现同一套筛选（截图、报问题时用得上）
try {
  const qs = new URL(location.href).searchParams.get("states");
  const arr = (qs || "").split(",").filter((s) => STATE_ORDER.includes(s));
  if (arr.length) {
    selectedStates = new Set(arr);
    saveStates();
  }
} catch (_e) {}

function saveStates() {
  try { localStorage.setItem(LS_STATES, JSON.stringify([...selectedStates])); } catch (_e) {}
}

function renderStateChips(counts) {
  const box = $("#state-chips");
  box.innerHTML = "";
  STATE_ORDER.forEach((s) => {
    const n = (counts && counts[s]) || 0;
    const b = el("button", "chip" + (selectedStates.has(s) ? " on" : "") + (s === "none" || s === "first" ? " plain" : ""));
    b.innerHTML = `${STATE_SHORT[s]}<b>${n}</b>`;
    b.title = `${LOW_STATES[s][2]}｜当前其它筛选条件下共 ${n} 条`;
    b.addEventListener("click", () => {
      if (selectedStates.has(s)) {
        if (selectedStates.size === 1) return;   // 一个都不选等于空列表，没意义
        selectedStates.delete(s);
      } else {
        selectedStates.add(s);
      }
      saveStates();
      loadDeals();
    });
    box.appendChild(b);
  });
}

const allStatesSelected = () => selectedStates.size === STATE_ORDER.length;

document.querySelectorAll("[data-preset]").forEach((b) => {
  b.addEventListener("click", () => {
    const p = b.dataset.preset;
    selectedStates = new Set(
      p === "record" ? ["super", "new"]
      : p === "low" ? ["super", "new", "equal"]
      : STATE_ORDER,
    );
    saveStates();
    loadDeals();
  });
});

/* ---------- 史低榜 ---------- */
let dealsTimer;
async function loadDeals() {
  const q = $("#q-deals").value.trim();
  const sort = $("#sort-deals").value;
  const only = $("#only-discount").checked ? "1" : "0";
  const cur = $("#cur-deals").value;
  const states = allStatesSelected() ? "" : [...selectedStates].join(",");
  // 地址栏跟着筛选走：刷新还是这套口径，也能直接把链接发给别人复现
  try {
    const u = new URL(location.href);
    if (states) u.searchParams.set("states", states); else u.searchParams.delete("states");
    history.replaceState(null, "", u);
  } catch (_e) {}
  const box = $("#deals");
  box.innerHTML = `<p class="hint">读取中…</p>`;
  const d = await api(`/api/deals?sort=${sort}&q=${encodeURIComponent(q)}&discount_only=${only}` +
    `&currency=${cur}&states=${states}&limit=120`);
  renderStateChips(d.state_counts);
  box.innerHTML = "";
  const matched = d.matched != null ? d.matched : d.count;
  const curName = cur === "USD" ? "美元 · 跨店比价" : "人民币 · Steam 国区";
  const hidden = matched - d.count;
  $("#deals-hint").textContent =
    `命中 ${d.count} 个（${curName}）· 通过其它筛选的共 ${matched} 条` +
    (hidden > 0 ? ` · 等级筛选隐藏了 ${hidden} 条` : "");
  if (!d.items.length) {
    box.innerHTML = `<p class="hint">${matched
      ? "当前勾选的史低等级下没有游戏 —— 换个等级组合，或点上面的「全部」。"
      : "还没有数据。启动时会自动采集一轮，也可以点右上角「立即更新」。"}</p>`;
    return;
  }
  d.items.forEach((it) => box.appendChild(card(it)));
}
$("#q-deals").addEventListener("input", () => {
  clearTimeout(dealsTimer);
  dealsTimer = setTimeout(loadDeals, 250);
});
$("#sort-deals").addEventListener("change", loadDeals);
$("#cur-deals").addEventListener("change", loadDeals);
$("#only-discount").addEventListener("change", loadDeals);

/* ---------- 口味 ---------- */
async function loadTaste() {
  const p = await api("/api/profile");
  const list = $("#taste-list");
  list.innerHTML = "";
  if (!p.entries.length) {
    list.innerHTML = `<p class="hint">还没有输入。上面加一条试试，比如「只狼 喜欢招架手感」。</p>`;
  }
  p.entries.forEach((e) => {
    const row = el("div", "entry");
    row.innerHTML = `
      <span class="k ${esc(e.kind)}">${e.kind === "liked" ? "喜欢" : "不喜欢"}</span>
      <span class="txt">${esc(e.text)}</span>
      <span class="hint">w=${e.weight}</span>
      <button class="del" title="删除">×</button>`;
    row.querySelector(".del").addEventListener("click", async () => {
      await api("/api/profile/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: e.id }),
      });
      loadTaste();
      loadStats();
    });
    list.appendChild(row);
  });

  const tagBox = $("#taste-tags");
  tagBox.innerHTML = "";
  if (!p.top_tags.length) {
    tagBox.innerHTML = `<p class="hint">画像为空。至少加一条「喜欢」。</p>`;
  }
  p.top_tags.forEach(([t, w]) => {
    const n = el("span", "tag", `${esc(t)} <b>${w.toFixed(3)}</b>`);
    tagBox.appendChild(n);
  });

  const res = $("#taste-resolved");
  res.innerHTML = p.resolved.length
    ? `<h2 style="margin-top:14px">每条输入解析成了什么</h2>` +
      p.resolved
        .map(
          (r) => `<div class="entry">
        <span class="k ${esc(r.kind)}">${r.kind === "liked" ? "+" : "−"}</span>
        <span class="txt">${esc(r.text)}</span>
        <span class="hint">${r.name ? "→ " + esc(r.name) : "→ " + r.top_tags.map(([t]) => esc(t)).join(", ")}</span>
      </div>` +
            (r.unmatched.length ? `<div class="hint" style="padding-left:26px">没听懂：${r.unmatched.map(esc).join(" / ")}</div>` : ""),
        )
        .join("")
    : "";
}

$("#taste-add").addEventListener("click", async () => {
  const text = $("#taste-text").value.trim();
  if (!text) return;
  const body = {
    text,
    kind: $("#taste-kind").value,
    weight: parseFloat($("#taste-weight").value || "1"),
  };
  const r = await api("/api/profile", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.error) {
    $("#taste-msg").textContent = "没加上：" + r.error;
    return;
  }
  $("#taste-msg").textContent = r.resolved
    ? `已加入，识别为《${r.resolved}》`
    : `已加入，抽到 tag：${r.top_tags.map(([t]) => t).join(", ") || "（没抽到，补一下描述？）"}`;
  $("#taste-text").value = "";
  loadTaste();
  loadStats();
});
$("#taste-text").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("#taste-add").click();
});

/* ---------- 推荐 ---------- */
async function loadRec() {
  const only = $("#rec-only-discount").checked ? "1" : "0";
  const top = $("#rec-top").value || 30;
  const box = $("#rec");
  box.innerHTML = `<p class="hint">计算中…</p>`;
  const r = await api(`/api/rec?top=${top}&discount_only=${only}`);
  box.innerHTML = "";
  if (r.error === "empty_profile") {
    box.innerHTML = `<p class="hint">先到「我的口味」加几条你玩过/喜欢的游戏。</p>`;
    return;
  }
  $("#rec-hint").textContent = `${r.count} 条候选`;
  r.items.forEach((it) => box.appendChild(card(it)));
}
$("#rec-run").addEventListener("click", loadRec);
$("#rec-only-discount").addEventListener("change", loadRec);

/* ---------- 免费 ---------- */
async function loadFree() {
  const box = $("#free");
  box.innerHTML = `<p class="hint">读取中…</p>`;
  const d = await api("/api/epic");
  box.innerHTML = "";
  if (!d.items?.length) {
    box.innerHTML = `<p class="hint">没拿到免费游戏列表${d.detail ? "：" + esc(d.detail) : ""}</p>`;
    return;
  }
  d.items.forEach((it) => {
    const c = el("div", "card");
    c.innerHTML = `
      ${it.image ? `<img class="img" loading="lazy" src="${esc(it.image)}" alt="">` : `<div class="img"></div>`}
      <div class="body">
        <div class="title">${esc(it.title)}</div>
        <div class="meta">
          <span class="badge ${it.status === "free_now" ? "low" : ""}">${it.status === "free_now" ? "现在可领" : "即将免费"}</span>
          ${it.end ? `<span>截止 ${it.end.slice(0, 10)}</span>` : ""}
        </div>
        <div class="price"><span class="now">免费</span>
          ${it.original_price ? `<span class="was">${money(it.original_price, it.currency)}</span>` : ""}</div>
      </div>`;
    if (it.product_slug) {
      c.addEventListener("click", () =>
        window.open(`https://store.epicgames.com/zh-CN/p/${it.product_slug}`, "_blank"));
    }
    box.appendChild(c);
  });
}

/* ---------- 详情抽屉 ---------- */
function sparkline(points, w = 480, h = 90) {
  if (!points.length) return `<p class="hint">还没有价格历史，多跑几次采集就有了。</p>`;
  const xs = points.map((p) => +new Date(p.at));
  const ys = points.map((p) => p.price);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sx = (v) => ((v - x0) / (x1 - x0 || 1)) * (w - 16) + 8;
  const sy = (v) => h - 12 - ((v - y0) / (y1 - y0 || 1)) * (h - 26);
  const d = points.map((p, i) => `${i ? "L" : "M"}${sx(xs[i]).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <path d="${d}" fill="none" stroke="#4ea1ff" stroke-width="1.6"/>
    <text x="8" y="12" fill="#8b93a1" font-size="10">最高 ${y1.toFixed(2)}</text>
    <text x="8" y="${h - 2}" fill="#8b93a1" font-size="10">最低 ${y0.toFixed(2)}</text>
  </svg>`;
}

async function openGame(appid) {
  const cur = $("#cur-deals")?.value || "CNY";
  const d = await api(`/api/game?appid=${appid}&currency=${cur}`);
  const drawer = $("#drawer");
  const body = $("#drawer-body");
  if (d.error) {
    body.innerHTML = `<p class="hint">没找到这个游戏的数据。</p>`;
  } else {
    const g = d.game;
    body.innerHTML = `
      <h3>${esc(g.name_cn || g.name)}</h3>
      <p class="hint">${esc(g.name)} · ${esc(g.release_date || "")} · MC ${g.metacritic ?? "—"}</p>
      ${g.header_image ? `<img class="shot" src="${esc(g.header_image)}" alt="">` : ""}
      <p class="hint">本地史低 <b style="color:#35d07f">${money(d.lowest, cur)}</b> · ${d.snapshots} 条快照 · ${esc((g.genres || []).join(" / "))}</p>
      ${d.heybox != null || d.steam_lowest != null
        ? `<p class="hint">
             ${d.heybox != null ? `小黑盒到手价 <b style="color:#ff8fa3">${money(d.heybox, cur)}</b>` : ""}
             ${d.steam_lowest != null ? ` · Steam 史低 <b>${money(d.steam_lowest, cur)}</b>` : ""}
           </p>`
        : ""}
      <h2 style="font-size:13px;color:#8b93a1">价格曲线</h2>
      ${sparkline(d.history)}
      <h2 style="font-size:13px;color:#8b93a1">当前各店</h2>
      <table><tr><th>商店</th><th>现价</th><th>折扣</th><th>采集于</th></tr>
        ${d.current.map((p) => `<tr><td>${esc(p.shop)}</td><td>${money(p.price, p.currency)}</td><td>${p.discount ? "-" + p.discount + "%" : "—"}</td><td>${esc(p.at.slice(0, 10))}</td></tr>`).join("") || `<tr><td colspan="4" class="hint">暂无</td></tr>`}
      </table>
      <p class="hint">${esc(g.short_desc || "")}</p>
      ${(g.tags || []).length ? `<div class="tags">${g.tags.map(([t, v]) => `<span class="t">${esc(t)} ${v}</span>`).join("")}</div>` : ""}
      <h2 style="font-size:13px;color:#8b93a1;margin-top:14px">口味相近的</h2>
      <div class="tags">${(d.similar || []).map((s) => `<span class="t">${esc(s.name)} ${s.score.toFixed(2)}</span>`).join("") || '<span class="hint">—</span>'}</div>
      <p style="margin-top:16px">
        <a href="${esc(g.shop_url || "#")}" target="_blank" style="color:#4ea1ff">Steam 商店 →</a>
        <span style="color:#242a34"> | </span>
        <a href="${esc(g.heybox_url || "#")}" target="_blank" style="color:#ff8fa3">小黑盒比价 →</a>
      </p>`;
  }
  drawer.classList.add("open");
}
$("#drawer-close").addEventListener("click", () => $("#drawer").classList.remove("open"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#drawer").classList.remove("open");
});

/* ---------- 采集状态（启动自动更新 / 手动补一刀） ---------- */
let updTimer = null;
let wasRunning = false;

function paintUpdate(u) {
  const dot = $("#upd-dot");
  const txt = $("#upd-text");
  dot.className = "dot" + (u.running ? " busy" : (u.last_ok === false ? " err" : u.last_ok ? " ok" : ""));
  // 这两句是用户直接看的状态，别改成别的口径
  txt.textContent = u.running
    ? (u.last_at ? `上次更新 于 ${u.last_at} · 正在更新中…` : "首次采集进行中…（还没有跑完过一轮）")
    : (u.last_at ? `上次更新 于 ${u.last_at}` : "还没采集过");
  const bits = [];
  if (u.argv?.length) bits.push(`采集参数：${u.argv.join(" ")}`);
  if (u.gap_min) bits.push(`自动更新最小间隔：${u.gap_min} 分钟`);
  if (u.last_at && !u.running) bits.push(`上一轮耗时 ${u.last_seconds ?? "?"} 秒（${u.last_reason === "manual" ? "手动" : "自动"}触发）`);
  if (u.last_ok === false) bits.push(`上一轮失败：${u.last_error || "返回码 " + u.last_code}`);
  if (!u.running && u.cooldown_left_min > 0) bits.push(`冷却中：还要 ${u.cooldown_left_min} 分钟才会自动更新`);
  txt.title = bits.join("\n");
  const btn = $("#btn-update");
  btn.disabled = !!u.running;
  btn.textContent = u.running ? "更新中…" : "立即更新";
}

async function pollUpdate() {
  let u;
  try {
    u = await api("/api/update");
  } catch (_e) {
    return;                     // 服务正在退出时这里会失败，不要当成错误刷屏
  }
  paintUpdate(u);
  const flippedOff = wasRunning && !u.running;
  const flippedOn = !wasRunning && u.running;
  wasRunning = !!u.running;
  if (flippedOn) loadStats();
  if (flippedOff) { loadStats(); loadDeals(); }   // 新价格已落库，重拉一遍
  clearTimeout(updTimer);
  updTimer = setTimeout(pollUpdate, u.running ? 1500 : 15000);
}

$("#btn-update").addEventListener("click", async () => {
  const r = await api("/api/update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (r.started) {
    wasRunning = true;
    paintUpdate(r.status || { running: true });
    clearTimeout(updTimer);
    updTimer = setTimeout(pollUpdate, 800);
  } else if (r.why === "running") {
    $("#deals-hint").textContent = "已经在采集了，等这一轮跑完。";
  } else {
    $("#deals-hint").textContent = `这轮没起来（${r.why}）。`;
  }
});

$("#btn-quit").addEventListener("click", async () => {
  if (!confirm("退出史低雷达？本地服务会停止，这个页面随即失效。\n（下次双击图标再开）")) return;
  const r = await api("/api/quit", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Radar-Quit": "1" },
    body: "{}",
  });
  if (r.ok) {
    document.body.insertAdjacentHTML(
      "afterbegin",
      `<div class="bye">服务已退出，可以关掉这个标签页。</div>`,
    );
    clearTimeout(updTimer);
  }
});

/* ---------- 启动 ---------- */
loadStats();
loadDeals();
pollUpdate();
