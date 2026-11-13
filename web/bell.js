/* 提醒中心：右上角铃铛 + 未读红点 + 展开列表。
 *
 * 为什么需要它：Windows 的 Toast 在 Win10 上可能只进通知中心、不弹横幅（未注册 AUMID 时尤其如此），
 * 所以「有没有提醒过」不能以弹窗为准。真正的记录在 SQLite 的 notifications 表里，这里就是它的界面。
 * 样式自包含（在这里注入 <style>），避免为了一个小部件去动全局样式表。
 */
(function () {
  "use strict";

  const CSS = `
  .bell{position:relative;margin-left:10px;background:#1f242b;border:1px solid #2c333c;color:#e6e6e6;
        border-radius:9px;padding:7px 12px;cursor:pointer;font:inherit;font-size:13px}
  .bell:hover{background:#262d36;border-color:#3a424c}
  .bell .badge{position:absolute;top:-6px;right:-6px;min-width:17px;height:17px;line-height:17px;
        border-radius:9px;background:#e5484d;color:#fff;font-size:11px;padding:0 4px;text-align:center}
  .bell-panel{position:fixed;top:64px;right:22px;width:340px;max-height:70vh;overflow:auto;z-index:60;
        background:#171b20;border:1px solid #2c333c;border-radius:12px;box-shadow:0 12px 40px #0009;padding:12px 14px}
  .bell-panel[hidden]{display:none}
  .bell-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;font-size:14px}
  .bell-head button{background:none;border:0;color:#8b939e;font-size:18px;cursor:pointer;line-height:1}
  .bell-item{padding:9px 0;border-bottom:1px solid #232931}
  .bell-item:last-child{border-bottom:0}
  .bell-t{display:flex;justify-content:space-between;gap:8px;font-size:13px}
  .bell-p{color:#7fd18c;white-space:nowrap}
  .bell-m{color:#79818c;font-size:11px;margin-top:3px}
  .bell-empty{color:#79818c;font-size:13px;padding:8px 0}
  .bell-foot{color:#6b7280;font-size:11px;margin-top:10px;line-height:1.5}
  `;

  const style = document.createElement("style");
  style.textContent = CSS;
  document.head.appendChild(style);

  const esc = (s) => String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const STATE_CN = { super: "破史低", new: "新低", equal: "平史低", near: "接近史低", test: "通道测试" };

  const badge = document.getElementById("bell-badge");
  const panel = document.getElementById("bell-panel");
  const listEl = document.getElementById("bell-list");
  const btn = document.getElementById("bell");
  if (!btn || !panel || !listEl) return;

  let loaded = 0;

  function render(data) {
    loaded = data.unread || 0;
    if (loaded > 0) {
      badge.textContent = loaded > 99 ? "99+" : String(loaded);
      badge.hidden = false;
    } else {
      badge.hidden = true;
    }
    const items = data.items || [];
    if (!items.length) {
      listEl.innerHTML = '<div class="bell-empty">还没有提醒。采集跑起来、有游戏跌到史低时，这里会出现记录。</div>';
      return;
    }
    listEl.innerHTML = items.map((it) => {
      const price = it.price != null ? "¥" + Number(it.price).toFixed(2) : "";
      const disc = it.discount ? " -" + it.discount + "%" : "";
      const tag = STATE_CN[it.state] || "";
      const shop = it.shop && it.state !== "test" ? " · " + it.shop : "";
      const when = (it.created_at || "").replace("T", " ").slice(0, 16);
      return '<div class="bell-item"><div class="bell-t"><span>' + esc(it.name || "") +
        '</span><span class="bell-p">' + esc(price) + esc(disc) + '</span></div>' +
        '<div class="bell-m">' + esc(tag + shop) + " · " + esc(when) + '</div></div>';
    }).join("");
  }

  async function load() {
    try {
      const res = await fetch("/api/notifications?limit=30", { cache: "no-store" });
      render(await res.json());
    } catch (e) {
      /* 服务没起来时不打扰用户 */
    }
  }

  async function markSeen() {
    if (!loaded) return;
    try {
      await fetch("/api/notifications/seen", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
      });
      badge.hidden = true;
    } catch (e) { /* 忽略 */ }
  }

  btn.addEventListener("click", () => {
    const willOpen = panel.hidden;
    panel.hidden = !willOpen;
    if (willOpen) { load(); markSeen(); }
  });
  document.getElementById("bell-close").addEventListener("click", () => { panel.hidden = true; });

  load();
  setInterval(load, 60000);
})();
