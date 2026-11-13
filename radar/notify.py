"""提醒层：把「跌到史低」推到眼前，而不是等你想起来打开界面。

三条通道，按可用性降级：

1. **Windows 通知**（默认，零依赖）：借 PowerShell 5.1 的 WinRT `ToastNotification`。
   不用装 BurntToast 之类的模块 —— 只为了弹一条通知而引入一个依赖不值得。
2. **Telegram**（配了 `telegram_bot_token` + `telegram_chat_id` 才用）：
   定时采集是后台跑的，手机上收到才算真的「雷达」。
3. **控制台**（`--dry-run`）：只打印，不弹也不发。

去重（`meta.notified`，{appid: {"cents", "at"}}）：同价不重复提醒 —— 否则一个常年平史低的游戏
会天天响；只有价格**更低**时才再提醒一次。这条规矩和界面上的「破史低 / 平史低」口径一致，
都走 `recommend.deal_view()`，避免推送说一套、界面说另一套。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from . import config, db, recommend

_NOTIFIED = "notified"
_APP_ID = "game-deal-radar"
_HUB = Path.home() / ".dsh" / "tools" / "notify-hub.py"

_STATE_CN = {"super": "破史低", "new": "新低", "equal": "平史低", "near": "接近史低"}

# WinRT toast：用 PowerShell 5.1（pwsh 7 默认加载不到这些 Windows 运行时类型）。
# 内容通过环境变量传，避免把这堆引号塞进命令串。
_PS_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($env:RADAR_TOAST_XML)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:RADAR_TOAST_APP).Show($toast)
"""


_PS_HISTORY = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotificationManager]::History.GetHistory($env:RADAR_TOAST_APP).Count
"""


def _history_count(app_id: str = _APP_ID) -> int | None:
    """查通知中心里这个 AppID 的历史条数。用来判断「系统到底收没收到」，查不到就返回 None。

    这是对上一轮错误检测的修正：以前只看 PowerShell 退出码，那只能证明命令跑通了，
    不能证明通知进了系统 —— 用户没看到横幅时，这两种情况看起来一模一样。
    """
    env = dict(os.environ)
    env["RADAR_TOAST_APP"] = app_id
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_HISTORY],
            env=env, capture_output=True, timeout=20, text=True,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode != 0:
        return None
    try:
        return int((res.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def beep() -> bool:
    """响一声。Toast 会被系统吞，声音不会 —— 这是最便宜的兜底。"""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
        return True
    except Exception:
        try:
            print("\a", end="", flush=True)
            return True
        except Exception:
            return False


# ---------- 通道 ----------
def windows_toast(title: str, body: str, app_id: str | None = None) -> bool:
    """弹一条 Windows 通知。失败不抛异常 —— 提醒层坏掉不该把采集链带崩。

    `app_id` 是通知来源的 AUMID。未在开始菜单注册过的 AUMID（比如本项目的 `game-deal-radar`）
    在 Win10 上往往只进通知中心、不弹横幅；已知已注册的是 PowerShell 自己那个。
    """
    xml = (
        '<toast><visual><binding template="ToastGeneric">'
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual>"
        '<audio src="ms-winsoundevent:Notification.Default"/>'
        "</toast>"
    )
    env = dict(os.environ)
    env["RADAR_TOAST_XML"] = xml
    env["RADAR_TOAST_APP"] = app_id or _APP_ID
    try:
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _PS_TOAST],
            env=env, capture_output=True, timeout=25, text=True,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[notify] toast 失败: {exc}", file=sys.stderr)
        return False
    if res.returncode != 0:
        print(f"[notify] toast 失败: {(res.stderr or '').strip()[:200]}", file=sys.stderr)
        return False
    return True


def telegram(text: str) -> bool:
    """发 Telegram。没配 token/chat_id 就直接返回 False（不是错误）。"""
    token = str(config.CFG.get("telegram_bot_token") or "").strip()
    chat = str(config.CFG.get("telegram_chat_id") or "").strip()
    if not token or not chat:
        return False
    import requests
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=int(config.CFG["timeout_sec"]),
        )
    except Exception as exc:
        print(f"[notify] telegram 失败: {exc}", file=sys.stderr)
        return False
    if resp.status_code != 200:
        print(f"[notify] telegram 失败: {resp.status_code} {resp.text[:200]}", file=sys.stderr)
        return False
    return True


# ---------- 选什么提醒 ----------
def tracked_appids() -> list[int]:
    """关注列表 + 画像里点名的游戏。没有这两样，就没有「该提醒谁」的依据。"""
    ids: set[int] = set()
    try:
        for r in db.watch_list():
            if r["appid"]:
                ids.add(int(r["appid"]))
    except Exception:
        pass
    for r in db.profile_list("liked"):
        if r["appid"]:
            ids.add(int(r["appid"]))
    return sorted(ids)


def scan(limit: int = 10) -> list[dict]:
    """扫一遍关注范围，挑出「现在就是史低级」的。按价格从低到高排。"""
    ids = tracked_appids()
    if not ids:
        return []
    lows = db.lowest_map()
    now = db.best_now()
    prev = db.prev_min_map()
    itad_lows = recommend.itad_low_map()

    rows: list[dict] = []
    for appid in ids:
        g = db.get_game(appid)
        if not g:
            continue
        v = recommend.deal_view(appid, g, best=now.get(appid), lows=lows,
                                prev_map=prev, itad_lows=itad_lows)
        if v["state"] not in ("super", "new", "equal") or v["price"] is None:
            continue
        rows.append({
            "appid": appid,
            "name": g["name"] or g["name_cn"] or str(appid),
            "url": g["shop_url"] or f"https://store.steampowered.com/app/{appid}",
            **v,
        })
    rows.sort(key=lambda r: (r["price"] or 9e9))
    return rows[:limit]


def _seen() -> dict:
    return db.get_meta(_NOTIFIED, {}) or {}


def _is_fresh(seen: dict, row: dict) -> bool:
    old = seen.get(str(row["appid"])) or {}
    old_cents = old.get("cents")
    # 没提醒过，或者比上次提醒的价更低 —— 只有这两种情况值得再响一次
    return old_cents is None or int(row["price_cents"]) < int(old_cents)


def _mark(rows: list[dict]) -> None:
    seen = _seen()
    for r in rows:
        seen[str(r["appid"])] = {"cents": int(r["price_cents"]), "at": db.now()}
    db.set_meta(_NOTIFIED, seen)


def _fmt_line(row: dict) -> str:
    price = f"¥{row['price']:.2f}"
    tag = _STATE_CN.get(row["state"], row["state"])
    disc = f" -{row['discount']}%" if row.get("discount") else ""
    return f"{row['name']} {price}{disc}（{tag}）"


def push_to_hub(rows: list[dict]) -> bool:
    """把这一批交给全局汇总器（`~/.dsh/tools/notify-hub.py`），由它在固定时间合并成一条通知。

    为什么默认走它而不是自己弹：一个项目一天弹一条还能忍，等有五六个工具都这么做，
    就成了「谁都可以随时打断我」。把「什么时候打扰」这件事收到一个地方统一管，
    每个工具只负责「攒着」，这才是可持续的。
    """
    if not _HUB.exists():
        return False
    body = "；".join(f"{r['name']} ¥{r['price']:.2f}" for r in rows[:6])
    title = (f"{len(rows)} 个游戏跌到史低" if len(rows) > 1
             else f"{rows[0]['name']} {_STATE_CN.get(rows[0]['state'], '跌到史低')}")
    cmd = [sys.executable, str(_HUB), "push", "--app", "史低雷达", "--title", title,
           "--body", body, "--url", f"http://127.0.0.1:{config.CFG.get('port', 8790)}"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=30, text=True, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"[notify] 入队失败: {exc}", file=sys.stderr)
        return False
    return res.returncode == 0


def run(*, dry_run: bool = False, limit: int = 5, quiet: bool = False,
        instant: bool = False) -> dict:
    """扫一遍 → 记账 → 发出去。返回统计，供 cli/scheduler 打日志。

    顺序很重要：**先写 `notifications` 表，再尝试弹窗**。Toast 在 Win10 上可能只进通知中心
    不弹横幅（未注册 AUMID 时就是这样），如果以「弹没弹」为准，用户会永远收不到；
    写库则是确定的，网页端的提醒中心一定看得到。
    """
    rows = scan(limit=limit)
    seen = _seen()
    fresh = [r for r in rows if _is_fresh(seen, r)]

    result = {"scanned": len(rows), "fresh": len(fresh), "toast": False, "beep": False,
              "telegram": False, "stored": 0}
    if not fresh:
        if not quiet:
            print(f"[notify] 关注范围里 {len(rows)} 个处于史低级，但没有新的（同价不重复提醒）")
        return result

    lines = [_fmt_line(r) for r in fresh]
    if not quiet:
        print(f"[notify] 新提醒 {len(fresh)} 条：")
        for ln in lines:
            print("  - " + ln)

    if dry_run:
        result["dry_run"] = True
        return result

    for r in fresh:
        db.notify_add(r["appid"], r["name"], r["price_cents"],
                      discount=r.get("discount") or 0, state=r.get("state") or "",
                      shop=r.get("shop") or "")
    result["stored"] = len(fresh)

    headline = f"史低雷达 · {len(fresh)} 个跌到史低" if len(fresh) > 1 else "史低雷达 · 跌到史低了"
    body = "；".join(lines[:4]) + ("…" if len(lines) > 4 else "")
    if instant:
        # 只有显式 --now 才当场打断你（调试、或者某次真的想立刻知道）
        result["toast"] = windows_toast(headline, body)
        result["beep"] = beep()
        result["telegram"] = telegram(
            f"<b>{escape(headline)}</b>\n" + "\n".join(f"• {escape(ln)}" for ln in lines[:10])
        )
    else:
        result["queued"] = push_to_hub(fresh)
    # 提醒已经记进库里了，所以即使弹窗被系统吞掉也要标记已读，否则明天会再记一遍同样的价
    _mark(fresh)
    if not instant and not result.get("queued"):
        print("[notify] 已写进提醒中心，但没能入队到全局汇总器（检查 ~/.dsh/tools/notify-hub.py）",
              file=sys.stderr)
    return result


def test_channels() -> dict:
    """一条命令把所有通道走一遍，并告诉你「系统到底收没收到」。

    判定用 `History` 条数的前后差，不看退出码 —— 退出码只能说明命令跑通了。
    """
    app_ids = [(_APP_ID, "game-deal-radar（未注册，通常不弹横幅）"),
               ("{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe",
                "Windows PowerShell（已注册，一般会弹）")]
    report = {}
    for app_id, label in app_ids:
        before = _history_count(app_id)
        sent = windows_toast("史低雷达 · 通道测试", f"{label}：看到这条说明横幅可用", app_id=app_id)
        after = _history_count(app_id)
        landed = None if (before is None or after is None) else after > before
        report[label] = {"sent": sent, "history_before": before, "history_after": after,
                         "landed": landed}
    report["beep"] = beep()
    report["telegram"] = telegram("<b>史低雷达 · 通道测试</b>\n收到说明 Telegram 通了")
    db.notify_add(None, "通道测试", 0, state="test", shop="test")   # 网页提醒中心一定能看到
    return report
