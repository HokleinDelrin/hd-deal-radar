"""每日定时采集的轻量调度器（只用标准库）。

为什么这么做：
- 项目约定不引第三方框架，`time.sleep` 到点足够；不装 APScheduler/celery。
- 采集逻辑已经在 `radar/cli.py` 的 `cmd_*` 里实现，这里只负责「什么时候跑、跑挂了怎么办」。
  用 `argparse.Namespace` 直接喂参数去调那些函数，而不是把采集步骤抄一份——
  抄一份第二天就会和 cli 漂移。
- 单个 job 抛异常必须只写日志、不中断当天剩余 job，也不影响明天那一轮；
  常驻进程最怕「半夜某个源 502，第二天整天不采了」。

实测踩坑：
- Windows 控制台默认 GBK，往日志/管道里打中文会 UnicodeEncodeError，所以打印前统一
  `reconfigure(encoding="utf-8")`（cli.py 里同样的处理，直跑 `python -m radar.scheduler` 时这里也要）。
- 一次 `time.sleep(到明天)` 遇上系统休眠/校时/长 sleep 漂移，会睡过头错过时刻；改成按
  SLICE_SEC 分片睡、每次醒来用 datetime 重算剩余秒数，顺带让 Ctrl+C 一秒内生效。
- 计划任务里不用常驻进程，用 `python -m radar.cli fetch ...` 单次命令（见 install-task.ps1）：
  任务计划程序会把「跟它预期不符的常驻进程」当异常，单次命令的退出码才是它认的信号。
- 日志必须 `flush` + 追加模式：进程被强杀时缓冲区里的最后几行最值钱。
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timedelta
from typing import Callable, Sequence

from . import cli, config

# 日志放 data/ 下（.gitignore 已忽略 data/），追加写、UTF-8。
LOG_PATH = config.DATA_DIR / "scheduler.log"

# 等待切片：越大越省电，越小 Ctrl+C 响应越快。30s 在「立刻响应」和「不空转」之间够用。
SLICE_SEC = 30.0


# ---------- 基础设施 ----------
def _fix_console() -> None:
    """Windows 控制台 GBK → UTF-8。重复调用无害。"""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def log(msg: str) -> None:
    """追加一行到 data/scheduler.log，同时打到 stdout，方便前台调试。"""
    _fix_console()
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now().astimezone().isoformat(timespec='seconds')}] {msg}"
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
    print(line, flush=True)


def _parse_at(at: str) -> tuple[int, int, int]:
    """解析 "09:30" / "9:30" / "09:30:00"。"""
    parts = str(at).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"--at 需要 HH:MM 格式，收到 {at!r}")
    try:
        hh, mm = int(parts[0]), int(parts[1])
        ss = int(parts[2]) if len(parts) > 2 else 0
    except ValueError as exc:
        raise ValueError(f"--at 需要 HH:MM 格式，收到 {at!r}") from exc
    if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
        raise ValueError(f"--at 时刻越界，收到 {at!r}")
    return hh, mm, ss


def next_run_at(at: str, now: datetime | None = None) -> datetime:
    """下一次 at 时刻（本地时区）。今天已过就顺延到明天。"""
    now = now or datetime.now().astimezone()
    hh, mm, ss = _parse_at(at)
    target = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return target


def seconds_until(at: str, now: datetime | None = None) -> float:
    now = now or datetime.now().astimezone()
    return max(0.0, (next_run_at(at, now) - now).total_seconds())


def sleep_until(target: datetime) -> None:
    """分片睡到 target。每片醒来重算，避免休眠/校时导致睡过头。"""
    while True:
        left = (target - datetime.now().astimezone()).total_seconds()
        if left <= 0:
            return
        time.sleep(min(left, SLICE_SEC))


# ---------- 每日任务序列 ----------
# 每个 job 是不带参数的 callable，返回 cli 的退出码；名字（__name__）直接进日志。
def _cli(*argv: str) -> int:
    """按完整命令行跑一次 cli：参数默认值交给 argparse，绝不手工拼 Namespace。

    实测踩坑（第一版就是这么炸的）：手写 `argparse.Namespace(pages=2, specials=30, ...)`
    去调 `cmd_fetch`，第二天别人给 `cmd_fetch` 加了 `args.heybox`，daemon 一跑就是
    `AttributeError: 'Namespace' object has no attribute 'heybox'`，fetch 整个跳过。
    走 `cli.main(argv)` 就永远不会缺字段 —— 新增的 argparse 参数自动带默认值。
    """
    return cli.main(list(argv))


def harvest() -> int:
    """铺候选池：SteamSpy top100in2weeks（只要名字/口碑，tag 交给 enrich）。"""
    return _cli("harvest", "--source", "top100in2weeks")


def fetch() -> int:
    """抓国区现价 + 跨店比价 + Epic 免费，参数与 README「怎么跑」保持一致。"""
    return _cli("fetch", "--pages", "2", "--specials", "30", "--epic")


def fetch_light() -> int:
    """每日轻量：只刷特惠区那一批 + 顺带补 20 个缺小黑盒价的。

    为什么不全量：价格一周内基本不变，而 `db.add_price` 现在同价不写行，
    所以轻量刷的价值在「发现新上架的折扣」，不在「把每个游戏都确认一遍」。
    """
    return _cli("fetch", "--specials", "20", "--pages", "0", "--heybox", "20")


def enrich() -> int:
    """补 SteamSpy tag 与好评率。1.4s/个，按 budget 排队，跑不完下次接着补。"""
    return _cli("enrich", "--budget", "200")


def heybox_all() -> int:
    """全量补小黑盒价 + Steam 官方史低。批量接口一次 20 个，200 个游戏十几批就完。"""
    return _cli("heybox", "--all", "--limit", "400")


def heybox_deadlines() -> int:
    """补优惠截止时间（券倒计时）。批量接口不给 `time_info`，只能单查，按天限流 80 个。"""
    return _cli("heybox", "--deadlines", "--limit", "80")


def itad_refresh() -> int:
    """补 ITAD 多店价 + 官方史低。没配 key 时自己跳过（返回 0），不会把整轮日志打红。"""
    return _cli("itad", "--limit", "200")


def itad_refresh_force() -> int:
    """周一全量重刷 ITAD：已采过的也刷一遍，抓「这周又降了」的店。"""
    return _cli("itad", "--limit", "400", "--force")


def notify_deals() -> int:
    """采集跑完后把「跌到史低」的推出去。放在最后一个 job，拿到的是本轮最新价。

    没新提醒时 `--quiet` 让日志保持干净；弹窗失败只写 warn，不影响整轮结果。
    """
    return _cli("notify", "--limit", "5", "--quiet")


# 每天跑：轻量刷新，一两分钟量级。
DAILY_JOBS: list[Callable[[], int]] = [fetch_light, heybox_all, heybox_deadlines,
                                      itad_refresh, notify_deals]

# 每周跑（周一）：铺池子 + 全量抓 + 补 tag/口碑 + 全量小黑盒 + 全量 ITAD 重刷。
# 选周一是因为 Steam 的周中特惠常在周二凌晨换档，周一跑完正好覆盖「上一周结束、下一周开始」。
WEEKLY_JOBS: list[Callable[[], int]] = [harvest, fetch, enrich, heybox_all,
                                        itad_refresh_force, notify_deals]

WEEKLY_WEEKDAY = 0  # 0 = 周一


def jobs_for(now: datetime | None = None) -> tuple[list[Callable[[], int]], str]:
    """今天该跑哪一档。周一全量，其余日子轻量。"""
    now = now or datetime.now().astimezone()
    if now.weekday() == WEEKLY_WEEKDAY:
        return WEEKLY_JOBS, "weekly"
    return DAILY_JOBS, "daily"


# ---------- 跑一轮 / 常驻 ----------
def run_round(jobs: Sequence[Callable[[], int]] | None = None, label: str = "") -> int:
    """依次跑一遍 jobs。单 job 异常只记日志，绝不外抛（KeyboardInterrupt/SystemExit 除外）。"""
    jobs = list(DAILY_JOBS if jobs is None else jobs)
    log(f"round start [{label}] ({len(jobs)} jobs)")
    ok = 0
    for fn in jobs:
        name = getattr(fn, "__name__", repr(fn))
        t0 = time.monotonic()
        log(f"START {name}")
        try:
            rc = fn()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:  # noqa: BLE001 —— 这里就是要兜住一切，包括 OSError/MemoryError
            cost = time.monotonic() - t0
            log(f"ERR   {name} {cost:.1f}s {type(exc).__name__}: {exc}")
            log("traceback:\n" + traceback.format_exc())
            continue
        cost = time.monotonic() - t0
        ok += 1
        log(f"END   {name} {cost:.1f}s rc={rc}")
    log(f"round done: {ok}/{len(jobs)} ok")
    return ok


def run_daily(
    at: str = "09:30",
    jobs: Sequence[Callable[[], int]] | None = None,
    once: bool = False,
    max_rounds: int | None = None,
) -> int:
    """常驻调度：每天 at 时刻跑一轮。周一跑全量（WEEKLY_JOBS），其余日子跑轻量（DAILY_JOBS）。

    jobs 传了就固定用这一组（自测用），不传就按当天星期自动选。
    once=True：先立刻跑一轮，再进入循环（调试用）。
    max_rounds：只给自测用，跑够几轮就返回，不暴露到 CLI —— 否则「常驻」这个语义就没了。
    """
    _fix_console()
    fixed = list(jobs) if jobs is not None else None
    log(f"daemon start at={at} fixed_jobs={[getattr(f, '__name__', f) for f in fixed] if fixed else None} "
        f"once={once} max_rounds={max_rounds}")

    rounds = 0

    def one_round() -> None:
        if fixed is not None:
            run_round(fixed, label="fixed")
        else:
            js, kind = jobs_for()
            log(f"today is {kind} round（周{'一二三四五六日'[datetime.now().weekday()]}）")
            run_round(js, label=kind)

    if once:
        try:
            one_round()
        except KeyboardInterrupt:
            log("interrupted during round, exit")
            return 0
        rounds += 1
        if max_rounds is not None and rounds >= max_rounds:
            log(f"reached max_rounds={max_rounds}, exit")
            return 0

    while True:
        target = next_run_at(at)
        left = max(0.0, (target - datetime.now().astimezone()).total_seconds())
        log(f"next run at {target.isoformat(timespec='seconds')}（{left / 3600:.2f} 小时后）")
        try:
            sleep_until(target)
        except KeyboardInterrupt:
            log("interrupted while waiting, exit")
            return 0
        try:
            one_round()
        except KeyboardInterrupt:
            log("interrupted during round, exit")
            return 0
        rounds += 1
        if max_rounds is not None and rounds >= max_rounds:
            log(f"reached max_rounds={max_rounds}, exit")
            return 0


if __name__ == "__main__":  # 直跑方便手测：python -m radar.scheduler --once
    ap = argparse.ArgumentParser(prog="radar.scheduler", description="史低雷达每日调度器")
    ap.add_argument("--at", default="09:30", help="每天几点跑，HH:MM")
    ap.add_argument("--once", action="store_true", help="先立刻跑一轮再进入每日循环")
    args = ap.parse_args()
    raise SystemExit(run_daily(at=args.at, once=args.once))
