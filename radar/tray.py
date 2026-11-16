"""Windows 通知区托盘图标 —— ctypes 直调 Shell_NotifyIconW，零第三方依赖。

为什么不用 pystray：本项目不装第三方包（AGENTS.md 硬约束），pystray 自己还拉着 Pillow 生成图标。
这里只需要两个 Win32 能力：Shell_NotifyIconW（加/删图标）+ 一个隐藏消息窗口（接鼠标回调、弹菜单）。

线程约束（写错了会静默失效，别改）：
- `Tray.run()` **必须在主线程**调：GetMessage 循环 + TrackPopupMenu 都要求同一个线程。
- `Tray.stop()` / `Tray.set_tip()` 可以从任何线程调：内部走 PostMessageW 到那个隐藏窗口，
  由消息循环所在线程执行真正的 Shell_NotifyIcon 调用（跨线程直接调也能跑，但不保证顺序）。

非 Windows 平台：`available()` 返回 False，调用方退回「网页里点退出服务」。
"""
from __future__ import annotations

import ctypes
import math
import os
import struct
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# ---------- Win32 常量（数值照 SDK 抄，别手改） ----------
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007B
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1          # 托盘图标回调（Shell_NotifyIcon 的 uCallbackMessage）
WM_TIP = WM_APP + 2           # 「刷新 tooltip」请求，别的线程 Post 过来

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
MF_STRING, MF_SEPARATOR, MF_GRAYED = 0x0000, 0x0800, 0x0001
TPM_RETURNCMD, TPM_NONOTIFY, TPM_RIGHTBUTTON = 0x0100, 0x0080, 0x0002
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
IMAGE_ICON = 1
IDI_APPLICATION = 32512
MB_OK, MB_ICONINFORMATION, MB_ICONERROR = 0x0, 0x40, 0x10
MB_YESNO, MB_ICONQUESTION, IDYES = 0x4, 0x20, 6


def available() -> bool:
    return IS_WINDOWS


if IS_WINDOWS:                                    # pragma: no cover - 只在 Windows 上跑

    LRESULT = ctypes.c_ssize_t
    WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                 ctypes.c_size_t, ctypes.c_ssize_t)

    class WNDCLASSEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("style", wintypes.UINT),
            ("lpfnWndProc", ctypes.c_void_p),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
            ("hIconSm", wintypes.HICON),
        ]

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class NOTIFYICONDATAW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("hWnd", wintypes.HWND),
            ("uID", wintypes.UINT),
            ("uFlags", wintypes.UINT),
            ("uCallbackMessage", wintypes.UINT),
            ("hIcon", wintypes.HICON),
            ("szTip", wintypes.WCHAR * 128),
            ("dwState", wintypes.DWORD),
            ("dwStateMask", wintypes.DWORD),
            ("szInfo", wintypes.WCHAR * 256),
            ("uVersion", wintypes.UINT),
            ("szInfoTitle", wintypes.WCHAR * 64),
            ("dwInfoFlags", wintypes.DWORD),
            ("guidItem", GUID),
            ("hBalloonIcon", wintypes.HICON),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # argtypes 必须写全：不写的话 ctypes 把 Python int 按 32 位传，64 位下 HWND/句柄会被截断，
    # 表现是「函数返回 0，但 GetLastError=0」，查不出原因。
    user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
    user32.RegisterClassExW.restype = wintypes.WORD
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                      wintypes.HINSTANCE, wintypes.LPVOID]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadIconW.restype = wintypes.HICON
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                 ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.CreatePopupMenu.argtypes = []
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL
    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = wintypes.BOOL
    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = ctypes.c_int
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p]
    user32.SetTimer.restype = ctypes.c_size_t
    user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
    user32.KillTimer.restype = wintypes.BOOL
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterWindowMessageW.restype = wintypes.UINT
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL


def message_box(text: str, title: str = "史低雷达", error: bool = False) -> None:
    """没有窗口之后的唯一弹框（Win32 原生模态）。非 Windows 就打印，不抛异常。"""
    if not IS_WINDOWS:
        print(f"[{title}] {text}", file=sys.stderr)
        return
    flags = MB_OK | (MB_ICONERROR if error else MB_ICONINFORMATION)
    user32.MessageBoxW(None, text, title, flags)


def confirm_box(text: str, title: str = "史低雷达") -> bool:
    """是/否 确认框。非 Windows（没有原生弹框）就当作同意。"""
    if not IS_WINDOWS:
        return True
    return user32.MessageBoxW(None, text, title, MB_YESNO | MB_ICONQUESTION) == IDYES


def _draw_icon(path: Path, size: int = 32, color: tuple[int, int, int] = (0x4E, 0xA1, 0xFF)) -> Path | None:
    """画一个「◎」（外环 + 中心点）的 32x32 32bpp 图标写成 .ico。

    纯 struct 拼字节，不引入 Pillow —— 只用一次，没必要为它加依赖。
    4x4 超采样做抗锯齿，不然通知区里会是一圈锯齿。
    """
    cx = cy = (size - 1) / 2.0
    r_out = size * 0.46
    r_in = r_out - max(2.0, size * 0.10)
    r_dot = size * 0.12
    ss = 4
    r, g, b = color
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            cov = 0
            for sy in range(ss):
                for sx in range(ss):
                    dx = x + (sx + 0.5) / ss - cx
                    dy = y + (sy + 0.5) / ss - cy
                    d = math.hypot(dx, dy)
                    if (r_in <= d <= r_out) or d <= r_dot:
                        cov += 1
            row.append((b, g, r, int(255 * cov / (ss * ss))))
        rows.append(row)
    try:
        xor = bytearray()
        for y in range(size - 1, -1, -1):           # ico 里的位图像素是自下而上
            for px in rows[y]:
                xor += bytes(px)
        mask_row = ((size + 31) // 32) * 4
        and_mask = bytes(mask_row * size)           # 全 0：可见性交给 alpha 通道
        bmp = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                          len(xor) + len(and_mask), 0, 0, 0, 0)
        data = bmp + bytes(xor) + and_mask
        ico = struct.pack("<HHH", 0, 1, 1) + struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), 22)
        path.write_bytes(ico + data)
        return path
    except OSError:
        return None


class Tray:
    """一个隐藏窗口 + 一个通知区图标 + 一个右键菜单。

    menu_provider: () -> list，元素是 (label, enabled, callback) 或 None（分隔线）。
        每次右键现算，所以菜单能显示「正在更新中…」这类实时状态。
    on_double_click: 双击图标时的动作（默认「打开界面」）。
    """

    ICON_ID = 1

    def __init__(self, tooltip: str, menu_provider, on_double_click=None,
                 on_refresh_tick=None, tip_every_ms: int = 5000, on_ready=None) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("托盘图标只在 Windows 上可用")
        self._tooltip = tooltip[:127]
        self._menu_provider = menu_provider
        self._on_double_click = on_double_click
        self._on_refresh_tick = on_refresh_tick
        self._on_ready = on_ready                  # 图标真的登记上去之后回调（打日志用）
        self._tip_every_ms = tip_every_ms
        self._hwnd = None
        self._hicon = None
        self._nid = None
        self._wndproc = None
        self._class_name = f"GameDealRadarTray_{os.getpid()}_{id(self)}"
        self._msg_taskbar_created = 0
        self._stopped = False
        self.registered = False              # Shell_NotifyIconW(NIM_ADD) 到底成没成
        self._ico_path = Path(tempfile.gettempdir()) / f"gdr-tray-{os.getpid()}.ico"

    # ---------- 生命周期 ----------
    def _create(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._on_message)     # 必须留引用，否则回调被 GC 掉直接崩
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = self._class_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:                          # 1410 = 类已存在，可以继续
                raise OSError(f"RegisterClassExW 失败: {err}")
        # 不填 WS_VISIBLE：这是个只用来收消息的窗口，不该出现在任务栏/桌面上
        self._hwnd = user32.CreateWindowExW(0, self._class_name, "史低雷达", 0, 0, 0, 0, 0,
                                            None, None, hinst, None)
        if not self._hwnd:
            raise OSError(f"CreateWindowExW 失败: {ctypes.get_last_error()}")
        self._msg_taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
        self._hicon = self._load_icon(hinst)
        self._add_icon()
        if self._on_ready:
            self._on_ready()
        if self._on_refresh_tick and self._tip_every_ms:
            user32.SetTimer(self._hwnd, 1, self._tip_every_ms, None)

    def _load_icon(self, hinst):
        """优先用自己画的 ◎；文件写不出来 / 加载失败就退回系统默认图标。"""
        try:
            if _draw_icon(self._ico_path):
                h = user32.LoadImageW(None, str(self._ico_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
                if h:
                    return h
        except Exception:
            pass
        return user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

    def _add_icon(self) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = self.ICON_ID
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._hicon
        nid.szTip = self._tooltip
        self._nid = nid
        ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self.registered = bool(ok)
        if not ok:
            raise OSError(f"Shell_NotifyIconW(NIM_ADD) 失败: {ctypes.get_last_error()}")

    def _remove_icon(self) -> None:
        if self._nid is not None:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None

    def run(self) -> None:
        """阻塞跑消息循环（主线程）。窗口被 WM_CLOSE 拆掉后返回。"""
        self._create()
        msg = wintypes.MSG()
        while True:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r == 0:            # WM_QUIT
                break
            if r == -1:           # 出错，别死循环
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._remove_icon()
        try:
            self._ico_path.unlink(missing_ok=True)
        except OSError:
            pass

    def stop(self) -> None:
        """线程安全：只往窗口 Post 一条 WM_CLOSE，真正的清理在消息循环线程里做。"""
        self._stopped = True
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    def set_tip(self, text: str) -> None:
        self._tooltip = (text or "")[:127]
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_TIP, 0, 0)

    # ---------- 消息处理 ----------
    def _apply_tip(self) -> None:
        if self._nid is None:
            return
        self._nid.uFlags = NIF_TIP
        self._nid.szTip = self._tooltip
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            ev = int(lparam) & 0xFFFF
            if ev in (WM_RBUTTONUP, WM_CONTEXTMENU):
                self._show_menu()
                return 0
            if ev == WM_LBUTTONDBLCLK:
                if self._on_double_click:
                    self._on_double_click()
                return 0
            if ev == WM_LBUTTONUP:
                return 0
            return 0
        if msg == WM_TIP:
            self._apply_tip()
            return 0
        if msg == 0x0113:                     # WM_TIMER
            if self._on_refresh_tick:
                try:
                    self._on_refresh_tick()
                except Exception:
                    pass
            self._apply_tip()
            return 0
        if self._msg_taskbar_created and msg == self._msg_taskbar_created:
            # explorer.exe 重启后图标会丢，得重新登记一次（这条不做的话「图标突然不见了」查不出来）
            try:
                self._add_icon()
            except OSError:
                pass
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.KillTimer(hwnd, 1)
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _show_menu(self) -> None:
        menu = user32.CreatePopupMenu()
        if not menu:
            return
        actions: dict[int, object] = {}
        i = 0
        for entry in self._menu_provider():
            if entry is None:
                user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            label, enabled, cb = entry
            i += 1
            user32.AppendMenuW(menu, MF_STRING | (0 if enabled else MF_GRAYED), i, label)
            actions[i] = cb
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self._hwnd)       # 不设前台，菜单点外面收不起来（Win32 老坑）
        cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON,
                                    pt.x, pt.y, 0, self._hwnd, None)
        user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)
        cb = actions.get(int(cmd))
        if cb:
            try:
                cb()
            except Exception as exc:                # 菜单里任何错误都得让用户看见，不能静默
                message_box(f"操作失败：{type(exc).__name__}: {exc}", "史低雷达", error=True)
