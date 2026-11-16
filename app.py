"""史低雷达 · 桌面启动器（打包成 EXE 后的入口）。

双击只做三件事：**起本地服务 → 自动打开界面 → 在通知区放一个托盘图标**。
不再弹 tkinter 小窗：旧版那个窗上写着「关这个窗口 = 停止服务」，
用户一关就变成「网页还开着、服务已经死了」—— 那是设计错误，不是用户操作错误。

服务生命周期现在归托盘：
- 右键菜单：打开界面 / 立即更新数据 / 小黑盒（登录、读取登录态） / 退出
- 双击图标 = 打开界面
- 退出 = 删图标 → 消息循环结束 → 进程退出（HTTP 服务是守护线程，随之结束）
另留一条兜底：网页右上角「退出服务」（`POST /api/quit`）—— 托盘被系统折叠进溢出区、
或者跑在非 Windows 上时，仍然关得掉。

采集不再问用户：启动就在后台跑一轮（`radar/updater.py`，距上次不足 30 分钟则跳过），
托盘菜单和网页里的「立即更新」都只是手动补一刀。
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from radar import config, db


def _free_port(preferred: int = 8790) -> int:
    for port in range(preferred, preferred + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("8790 附近找不到可用端口")


def _wait_ready(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def _migrate_install() -> str | None:
    """首次运行：用户目录还没有库时，看看 EXE 旁边有没有一份可以直接搬过来的库。

    打包版把数据放在 `%LOCALAPPDATA%\\GameDealRadar`（EXE 所在目录可能不可写，升级换 EXE 也不该丢库存）。
    所以第一次跑的时候，如果 EXE 同级或上一级有 `data/radar.db`，就复制一份过去 —— 老用户重装不丢数据。
    """
    import shutil
    if config.DB_PATH.exists():
        return None
    exe_dir = Path(sys.executable).parent
    for cand in (exe_dir / "data" / "radar.db", exe_dir.parent / "data" / "radar.db",
                 Path.cwd() / "data" / "radar.db"):
        if cand.is_file():
            config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cand, config.DB_PATH)
            return str(cand)
    return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="史低雷达", description="双击即用的桌面入口；命令行参数只给调试用")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器（调试 / 前台跑服务时用）")
    p.add_argument("--port", type=int, default=0, help="指定端口（0 = 8790 起找空闲端口）")
    p.add_argument("--no-update", action="store_true", help="启动时不自动采集一轮（只看已有数据）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    moved = _migrate_install()
    if moved:
        print(f"已从 {moved} 迁移数据库", file=sys.stderr)
    db.init()
    port = args.port or _free_port(int(config.CFG.get("port") or 8790))
    url = f"http://127.0.0.1:{port}"

    from radar import server, updater
    from radar import tray as tray_mod

    # 服务跑在守护线程：主线程要留给托盘消息循环（TrackPopupMenu 要求同线程）
    stopped = threading.Event()
    threading.Thread(
        target=lambda: (server.serve(host="127.0.0.1", port=port), stopped.set()),
        name="radar-http", daemon=True,
    ).start()
    if not _wait_ready(port):
        print("服务没起来", file=sys.stderr)
        return 1

    up = updater.get()
    if not args.no_update:
        first = up.start("startup")           # 后台线程，不挡开网页
        if not first.get("started"):
            st = first.get("status") or {}
            left = first.get("cooldown_left_min")
            print(f"[auto-fetch] 跳过（{first.get('why')}"
                  f"{f'，还要等 {left} 分钟' if left else ''}）；上次更新 {st.get('last_at')}", file=sys.stderr)

    if not args.no_browser:
        webbrowser.open(url)

    # ---------- 没有托盘（非 Windows）：靠网页里的「退出服务」或 Ctrl+C ----------
    if not tray_mod.available():
        print("当前系统没有通知区托盘，退出请按 Ctrl+C，或在网页里点「退出服务」", file=sys.stderr)
        try:
            while not stopped.wait(0.5):
                pass
        except KeyboardInterrupt:
            pass
        return 0

    # ---------- 动作 ----------
    def open_ui() -> None:
        webbrowser.open(url)

    def do_update() -> None:
        r = up.start("manual", force=True)
        if not r.get("started"):
            if r.get("why") == "running":
                tray_mod.message_box("已经在采集了，等这一轮跑完。")
            else:
                tray_mod.message_box(f"这轮没起来（{r.get('why')}）。")

    def heybox_login() -> None:
        from radar import heybox_login as hl
        try:
            created = hl.start_browser()
        except Exception as exc:                       # noqa: BLE001
            tray_mod.message_box(f"打开登录窗口失败：{exc}", error=True)
            return
        tray_mod.message_box(
            "已打开独立的 Edge 窗口，登录小黑盒后，右键托盘 →「小黑盒：读取登录态」。"
            if created is not None else
            "登录窗口已经开着。登录好后，右键托盘 →「小黑盒：读取登录态」。")

    def heybox_grab() -> None:
        def work() -> None:
            from radar import heybox_login as hl
            try:
                info = hl.save(hl.grab_cookies())
                tray_mod.message_box(f"已保存 {info['count']} 条 cookie，之后的采集会带上登录态。")
            except Exception as exc:                   # noqa: BLE001
                tray_mod.message_box(f"读取失败：{exc}\n\n先在「小黑盒：登录…」打开的窗口里完成登录。",
                                     error=True)
        threading.Thread(target=work, name="radar-heybox", daemon=True).start()

    def quit_app() -> None:
        st = up.status()
        if st.get("running") and not tray_mod.confirm_box(
                "正在更新数据，现在退出会中断这一轮采集。仍要退出？", "史低雷达"):
            return
        t.stop()

    def menu():
        st = up.status()
        running = st.get("running")
        last = st.get("last_at")
        if running:
            status_line = f"正在更新…（{st.get('running_for_s', 0)} 秒）"
        elif last:
            status_line = f"上次更新 {last}"
        else:
            status_line = "还没采集过"
        return [
            ("打开界面", True, open_ui),
            ("立即更新数据" if not running else "正在更新数据…", not running, do_update),
            None,
            ("小黑盒：登录…", True, heybox_login),
            ("小黑盒：读取登录态", True, heybox_grab),
            None,
            (f"退出　（{status_line}）", True, quit_app),
        ]

    def tick() -> None:
        """托盘 tooltip 每 5 秒跟着采集状态走 —— 不打开网页也能知道跑到哪了。"""
        st = up.status()
        if st.get("running"):
            t.set_tip(f"史低雷达 · 正在更新数据…（{st.get('running_for_s', 0)} 秒）")
        elif st.get("last_at"):
            t.set_tip(f"史低雷达 · 上次更新 {st['last_at']}")
        else:
            t.set_tip("史低雷达 · 还没采集过")

    t = tray_mod.Tray(tooltip=f"史低雷达 · {url}", menu_provider=menu,
                      on_double_click=open_ui, on_refresh_tick=tick,
                      on_ready=lambda: print("[tray] 通知区图标已登记", file=sys.stderr))
    # 网页里点「退出服务」→ server.serve() 返回 → 这里顺手把托盘也收掉，别留个死图标
    threading.Thread(target=lambda: (stopped.wait(), t.stop()),
                     name="radar-watch", daemon=True).start()
    t.run()          # 阻塞：消息循环，直到托盘「退出」或网页点了「退出服务」
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
