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

/* ---------- 史低榜 ---------- */
let dealsTimer;
async function loadDeals() {
  const q = $("#q-deals").value.trim();
  const sort = $("#sort-deals").value;
  const only = $("#only-discount").checked ? "1" : "0";
  const cur = $("#cur-deals").value;
  const box = $("#deals");
  box.innerHTML = `<p class="hint">读取中…</p>`;
  const d = await api(`/api/deals?sort=${sort}&q=${encodeURIComponent(q)}&discount_only=${only}&currency=${cur}&limit=120`);
  box.innerHTML = "";
  $("#deals-hint").textContent = `命中 ${d.count} 个有价格记录的游戏（${cur === "USD" ? "美元 · 跨店比价" : "人民币 · Steam 国区"}）`;
  if (!d.items.length) {
    box.innerHTML = `<p class="hint">还没有数据。先在命令行跑一次采集：<code>python -m radar.cli fetch --pages 2 --specials 20 --epic</code></p>`;
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
$("#btn-refresh").addEventListener("click", () => {
  $("#deals-hint").textContent = "采集请在命令行跑：python -m radar.cli fetch --pages 2 --specials 20 --epic";
});

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

/* ---------- 启动 ---------- */
loadStats();
loadDeals();
