"""小黑盒登录：用真实账号的登录态去读数据，而不是伪造匿名签名。

为什么这么做
------------
原来那套是「自实现 web 签名 + 匿名请求」，等于假装成网页端去打别人的接口 —— 签名只是反爬
门槛，绕过它并不让你更「正当」。**登录**则把关系摆正：以你自己的账号、自己的会话、只取你看得到的东西，
频次也压得住。这不是「技术上绕过」，而是「用回正常用户的路径」。

怎么拿到登录态
--------------
零依赖，不装 selenium / playwright：

1. 起一个**独立 profile** 的 Edge（`--user-data-dir=data/edge-profile`），不影响你日常浏览器的数据；
2. 你在那个窗口里正常登录小黑盒（扫码或账号密码，验证码你自己过）；
3. 程序通过 CDP（`--remote-debugging-port`）读 `Storage.getCookies`，把 xiaoheihe 域的 cookie 存到
   `data/heybox_cookies.json`。这个 profile 会保留，所以下次通常不用重登。

不想开浏览器也行：`--paste "<从 F12 里复制的 Cookie 头>"` 直接存。
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import config

COOKIE_FILE = config.DATA_DIR / "heybox_cookies.json"
PROFILE_DIR = config.DATA_DIR / "edge-profile"
DEFAULT_PORT = 9223
LOGIN_URL = "https://www.xiaoheihe.cn/"
DOMAIN_HINTS = ("xiaoheihe", "heybox")
# 未登录时站点也会种 `lang`、`smidV2`、`.thumbcache_*`、`x_xhh_tokenid` 这类匿名/设备 cookie，
# 光看「抓到几条」或含不含 token 都会误判。只有这几个名字才说明真的登录了。
LOGIN_HINTS = ("pkey", "heybox_id", "user_heybox")

EDGE_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


# ---------- 极简 WebSocket 客户端（只为 CDP 服务，够用就行） ----------
class _WS:
    def __init__(self, url: str, timeout: float = 15.0):
        parsed = urllib.parse.urlparse(url)
        host, port = parsed.hostname or "127.0.0.1", parsed.port or 80
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手没响应")
            buf += chunk
        head, self._buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise RuntimeError(f"WebSocket 握手失败: {head[:120]!r}")

    def send(self, obj: dict) -> None:
        payload = json.dumps(obj).encode("utf-8")
        frame = bytearray([0x81])          # FIN + text
        n = len(payload)
        if n < 126:
            frame.append(0x80 | n)
        elif n < 65536:
            frame.append(0x80 | 126)
            frame += struct.pack(">H", n)
        else:
            frame.append(0x80 | 127)
            frame += struct.pack(">Q", n)
        mask = os.urandom(4)
        frame += mask
        frame += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(frame))

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("连接被关闭")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv(self) -> str:
        parts: list[bytes] = []
        while True:
            b1, b2 = self._read(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            masked, length = b2 & 0x80, b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else None
            payload = self._read(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise RuntimeError("服务端关闭了连接")
            if opcode == 0x9:      # ping → 忽略
                continue
            parts.append(payload)
            if fin:
                break
        return b"".join(parts).decode("utf-8", "replace")

    def call(self, method: str, params: dict | None = None, timeout: float = 20.0) -> dict:
        self.send({"id": 1, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(f"CDP 错误: {msg['error']}")
                return msg.get("result") or {}
        raise RuntimeError(f"CDP 调用超时: {method}")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


# ---------- 浏览器 ----------
def find_edge() -> str | None:
    for p in EDGE_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _cdp_ws(port: int) -> str | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
            return json.loads(r.read().decode("utf-8")).get("webSocketDebuggerUrl")
    except Exception:
        return None


def start_browser(port: int = DEFAULT_PORT, url: str = LOGIN_URL) -> subprocess.Popen | None:
    """起一个独立 profile 的 Edge。已经在跑就直接复用（避免端口冲突）。"""
    if _cdp_ws(port):
        return None
    edge = find_edge()
    if not edge:
        raise RuntimeError("没找到 msedge.exe（Windows 10/11 自带；装在别处就手动改 EDGE_CANDIDATES）")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([
        edge,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ])
    for _ in range(40):
        if _cdp_ws(port):
            return proc
        time.sleep(0.25)
    raise RuntimeError("Edge 起来了但调试端口没通（可能被安全软件拦）")


def grab_cookies(port: int = DEFAULT_PORT) -> list[dict]:
    ws_url = _cdp_ws(port)
    if not ws_url:
        raise RuntimeError(f"连不上 CDP 端口 {port}，先跑登录流程把浏览器开起来")
    ws = _WS(ws_url)
    try:
        result = ws.call("Storage.getCookies")
    finally:
        ws.close()
    cookies = result.get("cookies") or []
    out = []
    for c in cookies:
        domain = (c.get("domain") or "").lower()
        if any(h in domain for h in DOMAIN_HINTS):
            out.append(c)
    return out


# ---------- 存取 ----------
def looks_logged_in(cookies: list[dict]) -> bool:
    """粗判：cookie 名里有没有小黑盒的用户标识（`pkey` / `heybox_id` / `user_heybox`）。

    实测未登录的首页会种 `lang`、`smidV2`、`.thumbcache_*`、`x_xhh_tokenid` —— 这些**都不是**登录态，
    所以这里刻意只认那几个明确的名字。它只用来提示，不阻断；真正的判据是采集能不能取到价。
    """
    names = " ".join((c.get("name") or "").lower() for c in cookies)
    return any(h in names for h in LOGIN_HINTS)


def save(cookies: list[dict]) -> dict:
    if not cookies:
        raise RuntimeError("一个 xiaoheihe 的 cookie 都没抓到 —— 多半是那个窗口里还没登录成功")
    pairs = [f"{c['name']}={c['value']}" for c in cookies if c.get("name")]
    payload = {
        "cookie": "; ".join(pairs),
        "count": len(pairs),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "names": [c.get("name") for c in cookies],
        "looks_logged_in": looks_logged_in(cookies),
    }
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load() -> str:
    """返回可用的 Cookie 头；没有登录过就返回空串（调用方据此降级）。"""
    if not COOKIE_FILE.exists():
        return ""
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    return str(data.get("cookie") or "")


def status() -> dict:
    if not COOKIE_FILE.exists():
        return {"logged_in": False, "reason": f"没有 {COOKIE_FILE.name}", "file": str(COOKIE_FILE)}
    try:
        data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {"logged_in": False, "reason": f"文件坏了: {exc}", "file": str(COOKIE_FILE)}
    return {"logged_in": bool(data.get("cookie")), "count": data.get("count"),
            "saved_at": data.get("saved_at"), "names": data.get("names"),
            "looks_logged_in": data.get("looks_logged_in"),
            "file": str(COOKIE_FILE)}


def clear() -> bool:
    if COOKIE_FILE.exists():
        COOKIE_FILE.unlink()
        return True
    return False


def paste(raw: str) -> dict:
    """从 F12 里复制的 Cookie 头直接存（不想开浏览器时的兜底）。"""
    text = (raw or "").strip().strip('"').strip("'")
    if ";" not in text and "=" not in text:
        raise RuntimeError("这不像 Cookie 头（应该形如 `a=1; b=2`）")
    pairs = [p.strip() for p in text.split(";") if "=" in p]
    payload = {
        "cookie": "; ".join(pairs),
        "count": len(pairs),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "names": [p.split("=", 1)[0] for p in pairs],
        "source": "paste",
    }
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
