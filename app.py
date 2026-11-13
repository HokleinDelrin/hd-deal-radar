"""史低雷达 · 桌面启动器（打包成 EXE 后的入口）。

双击就干三件事：起本地服务 → 自动打开界面 → 留一个小窗口告诉你它在跑、以及怎么退出。

为什么不用「只有后台进程」：`--noconsole` 打包后没有控制台，用户看不见任何东西，
也没法 Ctrl+C。留一个 tkinter 小窗（标准库，不额外增体积）比托盘图标省事，
还能顺手放一个「更新数据」按钮 —— 不然每次采集都要去翻命令行。
"""
from __future__ import annotations

import socket
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import messagebox, ttk
import tkinter as tk

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
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


class App:
    def __init__(self, port: int) -> None:
        self.port = port
        self.url = f"http://127.0.0.1:{port}"
        self.root = tk.Tk()
        self.root.title("史低雷达")
        self.root.geometry("460x300")
        self.root.minsize(420, 280)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

        tk.Label(self.root, text="史低雷达", font=("Microsoft YaHei UI", 16, "bold")).pack(pady=(16, 2))
        tk.Label(self.root, text="正在本机运行，不需要联网以外的权限",
                 font=("Microsoft YaHei UI", 9), fg="#666").pack()

        self.stat = tk.Label(self.root, text="读取中…", font=("Microsoft YaHei UI", 9))
        self.stat.pack(pady=(8, 4))

        self.link = tk.Label(self.root, text=self.url, fg="#1a6fd4", cursor="hand2",
                             font=("Consolas", 10, "underline"))
        self.link.pack()
        self.link.bind("<Button-1>", lambda _e: self.open_ui())

        btns = tk.Frame(self.root)
        btns.pack(pady=10)
        ttk.Button(btns, text="打开界面", command=self.open_ui).grid(row=0, column=0, padx=4)
        ttk.Button(btns, text="更新数据", command=self.refresh).grid(row=0, column=1, padx=4)

        btns2 = tk.Frame(self.root)
        btns2.pack(pady=2)
        ttk.Button(btns2, text="登录小黑盒", command=self.heybox_open).grid(row=0, column=0, padx=4)
        ttk.Button(btns2, text="读取登录态", command=self.heybox_grab).grid(row=0, column=1, padx=4)
        ttk.Button(btns2, text="退出", command=self.quit).grid(row=0, column=2, padx=4)

        self.hint = tk.Label(self.root, text="关这个窗口 = 停止服务", fg="#888",
                             font=("Microsoft YaHei UI", 8))
        self.hint.pack(side="bottom", pady=8)

        self._refresh_stat()

    # ---------- 动作 ----------
    def open_ui(self) -> None:
        webbrowser.open(self.url)

    def refresh(self) -> None:
        if getattr(self, "_busy", False):
            return
        self._busy = True
        self.hint.config(text="正在更新，几分钟内别关窗口…", fg="#b06a00")

        def work() -> None:
            code = 0
            err = ""
            try:
                from radar import cli
                code = cli.main(["fetch", "--specials", "20", "--pages", "0", "--heybox", "20"])
            except BaseException as exc:      # noqa: BLE001 — 界面上要能看见任何失败
                err = f"{type(exc).__name__}: {exc}"
            self.root.after(0, lambda: self._after_refresh(code, err))

        threading.Thread(target=work, daemon=True).start()

    def _after_refresh(self, code: int, err: str) -> None:
        self._busy = False
        self.hint.config(text="关这个窗口 = 停止服务", fg="#888")
        self._refresh_stat()
        if err:
            messagebox.showerror("更新失败", err)
        elif code != 0:
            messagebox.showwarning("更新结束", f"采集返回码 {code}，可能有个别源失败了（界面里仍可看已有数据）")
        else:
            messagebox.showinfo("更新完成", "数据已更新，界面上刷新一下就能看到。")

    def heybox_open(self) -> None:
        """起一个独立 profile 的 Edge 让用户自己登录。不碰日常浏览器的数据。"""
        from radar import heybox_login as hl
        try:
            created = hl.start_browser()
        except Exception as exc:
            messagebox.showerror("打开登录窗口失败", str(exc))
            return
        self.hint.config(text="在刚弹出的 Edge 里登录小黑盒，登录好后点「读取登录态」", fg="#b06a00")
        if created is None:
            self.hint.config(text="登录窗口已经开着，登录好后点「读取登录态」", fg="#b06a00")

    def heybox_grab(self) -> None:
        from radar import heybox_login as hl
        try:
            info = hl.save(hl.grab_cookies())
        except Exception as exc:
            messagebox.showerror("读取失败", f"{exc}\n\n先在「登录小黑盒」打开的窗口里完成登录，再点这里。")
            return
        self.hint.config(text="关这个窗口 = 停止服务", fg="#888")
        messagebox.showinfo("已登录", f"已保存 {info['count']} 条 cookie。之后的采集会带上登录态。")
        self._refresh_stat()

    def _refresh_stat(self) -> None:
        try:
            s = db.stats()
            from radar import heybox_login as hl
            hb = "已登录" if hl.status().get("logged_in") else "未登录"
            self.stat.config(text=f"游戏 {s.get('games', 0)} · 快照 {s.get('prices', 0)} · "
                                  f"画像 {s.get('profile', 0)} 条 · 小黑盒{hb}")
        except Exception as exc:
            self.stat.config(text=f"数据库读取失败：{exc}")

    def quit(self) -> None:
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


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


def main() -> int:
    moved = _migrate_install()
    if moved:
        print(f"已从 {moved} 迁移数据库", file=sys.stderr)
    db.init()
    port = _free_port(int(config.CFG.get("port") or 8790))
    from radar.server import serve
    threading.Thread(target=serve, kwargs={"host": "127.0.0.1", "port": port},
                     daemon=True).start()
    if not _wait_ready(port):
        print("服务没起来", file=sys.stderr)
        return 1
    webbrowser.open(f"http://127.0.0.1:{port}")
    App(port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
