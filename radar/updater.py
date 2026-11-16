"""采集的自动调度与状态。

三件事，别混在一起：
1. **什么时候跑**：启动时自动跑一轮；距上次成功采集不足 `auto_fetch_gap_min` 分钟就跳过。
2. **跑什么**：`FETCH_ARGV`，和旧版 app.py 里「更新数据」按钮调的那组参数一模一样 —— 不另发明一套。
3. **跑到哪了**：`status()` 给前端轮询（`GET /api/update`），也顺手写进 `meta.fetch_state`，
   这样关掉应用再打开还知道「上次更新 于 …」。

为什么要有间隔：`docs/数据源.md` 记的限速是给「一天一两轮」用的。自动更新一旦没有下限，
用户反复双击图标就等于在对 Steam / 小黑盒做压力测试。手动点「立即更新」不受这个间隔限制。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from . import config, db

# 跟 app.py 原来那句一字不差，别改（改了就变成「自动采集比手动更狠」）
FETCH_ARGV = ["fetch", "--specials", "20", "--pages", "0", "--heybox", "20"]
META_KEY = "fetch_state"
DEFAULT_GAP_MIN = 30


def _fmt(ts: float | None) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class Updater:
    def __init__(self, gap_min: int | None = None, argv: list[str] | None = None) -> None:
        if gap_min is None:
            gap_min = int(config.CFG.get("auto_fetch_gap_min") or DEFAULT_GAP_MIN)
        self.gap_min = max(0, int(gap_min))
        self.argv = list(argv or FETCH_ARGV)
        self._lock = threading.Lock()
        self._running = False
        self._started_ts: float | None = None
        self._reason = ""
        self._last: dict = db.get_meta(META_KEY, {}) or {}
        self._last_error = ""

    # ---------- 状态 ----------
    def status(self) -> dict:
        with self._lock:
            running = self._running
            started = self._started_ts
            reason = self._reason
            last = dict(self._last or {})
            err = self._last_error
        last_ts = last.get("ts")
        next_allowed = (last_ts + self.gap_min * 60) if last_ts else None
        left = 0
        if next_allowed:
            left = max(0, int(round((next_allowed - time.time()) / 60)))
        return {
            "running": running,
            "started_at": _fmt(started),
            "started_ts": started,
            "running_for_s": int(time.time() - started) if (running and started) else 0,
            "reason": reason,
            "last_at": _fmt(last_ts),
            "last_ts": last_ts,
            "last_ok": last.get("ok"),
            "last_code": last.get("code"),
            "last_reason": last.get("reason"),
            "last_seconds": last.get("seconds"),
            "last_error": err or last.get("error") or "",
            "gap_min": self.gap_min,
            "cooldown_left_min": left,
            "argv": self.argv,
        }

    # ---------- 触发 ----------
    def start(self, reason: str = "manual", force: bool = False) -> dict:
        """起一轮采集。force=True 是手动触发：跳过间隔限制，但不允许两轮并行。

        注意：`status()` 自己也拿这把锁，所以判定只能在锁内做、拼返回值必须在锁外拼 ——
        之前把 `self.status()` 写在 with 里面，第二个「立即更新」请求直接把线程锁死，
        表现是「点了没反应、服务还在」，很难看出是自锁（见 QA.md 2026-11-15）。
        """
        why = "ok"
        left_min = 0
        with self._lock:
            if self._running:
                why = "running"
            else:
                last_ts = (self._last or {}).get("ts")
                if not force and last_ts:
                    left = (last_ts + self.gap_min * 60) - time.time()
                    if left > 0:
                        why = "cooldown"
                        left_min = max(1, int(left // 60) + 1)
                if why == "ok":
                    self._running = True
                    self._started_ts = time.time()
                    self._reason = reason
                    self._last_error = ""
        if why == "ok":
            threading.Thread(target=self._run, name="radar-updater", daemon=True).start()
            return {"started": True, "why": "ok", "status": self.status()}
        out = {"started": False, "why": why, "status": self.status()}
        if why == "cooldown":
            out["cooldown_left_min"] = left_min
        return out

    def _run(self) -> None:
        t0 = time.time()
        code, err = 0, ""
        try:
            from . import cli
            code = cli.main(self.argv)
        except BaseException as exc:      # noqa: BLE001 —— 状态面板上要能看见任何失败
            code = -1
            err = f"{type(exc).__name__}: {exc}"
        seconds = round(time.time() - t0, 1)
        with self._lock:
            self._running = False
            self._started_ts = None
            self._last_error = err
            self._last = {
                "ts": time.time(),
                "ok": code == 0 and not err,
                "code": code,
                "seconds": seconds,
                "reason": self._reason,
                "error": err,
            }
            state = dict(self._last)
        try:
            db.set_meta(META_KEY, state)
        except Exception:
            pass      # 写状态失败不能把采集结果一起吞掉


_UPDATER: Updater | None = None


def get() -> Updater:
    """进程内单例。懒加载：模块 import 时数据库可能还没建。"""
    global _UPDATER
    if _UPDATER is None:
        _UPDATER = Updater()
    return _UPDATER
