"""小黑盒（Heybox）签名算法 —— Web 端 hkey。

为什么需要这个：小黑盒是国内最便宜的一档（第三方 key / 商城价），`get_game_detail`
一次就同时给出 Steam 史低价和小黑盒到手价，正是「跨平台史低」最缺的一块。
但它的 API 对每个请求都要签名，缺 `_time` 直接 `缺少必要参数`，签名错直接 `非法请求`。

算法来源：社区逆向（https://blog.sakurasen.cn/post/1778064732895/ 的 JS 化简版），
结构是三层字符表映射 + 交错拼接 + MD5 + 列混淆校验位。这里用 Python 等价实现。

注意一个容易看错的细节：JS 里 `lv[i] = (e,t,n) => ov(e, t+3, n)`，
传进 `ov` 的时间戳是 `_time + 3`，而 URL 上的 `_time` 还是原值。
"""
from __future__ import annotations

import hashlib
import random
import string
import time

R_CHARSET = "AB45STUVWZEFGJ6CH01D237IXYPQRKLMN89"
RND_KEY = "Z7mFG4tQp9Ws2LxB8H"


def _av(s: str, charset: str, end: int) -> str:
    """`av(e, t, n)`：取字符表前 n 个字符（n 为负则从尾部去掉），按 charCode % len 映射。"""
    table = charset[:end] if end >= 0 else charset[: len(charset) + end]
    if not table:
        return ""
    return "".join(table[ord(c) % len(table)] for c in s)


def _sv(s: str, charset: str) -> str:
    """`sv(e, t)`：整张字符表按 charCode % len 映射。"""
    return "".join(charset[ord(c) % len(charset)] for c in s)


def _create_new_str(a: str, b: str, c: str) -> str:
    """`CreateNewStr`：三段交错拼接。原实现用字符串比较排序，这里原样复刻（含它的怪癖）。"""
    arr: list[str] = []
    if len(a) < len(b):
        if len(c) < len(a):
            arr = [c, a, b]
        if len(c) > len(b):
            arr = [a, b, c]
        if c > a and c < b:
            arr = [a, c, b]
    if len(b) < len(a):
        if len(c) < len(a):
            arr = [c, b, a]
        if len(c) > len(b):
            arr = [b, a, c]
        if c > a and c < b:
            arr = [b, c, a]
    if not arr:            # 原 JS 在这种情况会直接抛错，这里兜底成顺序拼接
        arr = [a, b, c]

    out = ""
    for i in range(max(len(a), len(b), len(c))):
        if len(arr[0]) < i + 1 and len(arr[1]) > i:
            out += arr[1][i] + arr[2][i]
            continue
        if len(arr[1]) < i + 1:
            if len(arr[2]) > i:
                out += arr[2][i]
            continue
        out += arr[0][i]
        if len(arr[1]) > i:
            out += arr[1][i]
        if len(arr[2]) > i:
            out += arr[2][i]
    return out


def _vm(e: int) -> int:
    return 255 & ((e << 1) ^ 27) if e & 128 else e << 1


def _qm(e: int) -> int:
    return _vm(e) ^ e


def _sm(e: int) -> int:
    return _qm(_vm(e))


def _ym(e: int) -> int:
    return _sm(_qm(_vm(e)))


def _gm(e: int) -> int:
    return _ym(e) ^ _sm(e) ^ _qm(e)


def _km(e: list[int]) -> list[int]:
    """`Km`：4x4 列混淆，输入 6 个 ascii 码，输出被打乱的一串数。"""
    if len(e) < 4:
        e = e + [0] * (4 - len(e))
    t = [
        _gm(e[0]) ^ _ym(e[1]) ^ _sm(e[2]) ^ _qm(e[3]),
        _qm(e[0]) ^ _gm(e[1]) ^ _ym(e[2]) ^ _sm(e[3]),
        _sm(e[0]) ^ _qm(e[1]) ^ _gm(e[2]) ^ _ym(e[3]),
        _ym(e[0]) ^ _sm(e[1]) ^ _qm(e[2]) ^ _gm(e[3]),
    ]
    rest = e[4:]
    return t + rest


def make_nonce(ts: int | None = None) -> str:
    """32 位大写 MD5 当 nonce。原 JS 是 md5(time + Math.random(...))，随机性足够。"""
    ts = int(time.time()) if ts is None else ts
    seed = str(ts) + "".join(random.choices(string.ascii_letters + string.digits, k=16))
    return hashlib.md5(seed.encode()).hexdigest().upper()


def make_hkey(path: str, ts: int, nonce: str) -> str:
    """给定路径、时间戳、nonce → hkey。path 只传路径部分，不带 query。"""
    norm = "/" + "/".join(p for p in path.split("/") if p) + "/"
    str1 = _av(str(ts + 3), R_CHARSET, -2)   # 注意 +3：JS 里 lv[i] 转发时加了 3
    str2 = _sv(norm, R_CHARSET)
    str3 = _sv(nonce, R_CHARSET)
    combined = _create_new_str(str1, str2, str3)[:20]
    digest = hashlib.md5(combined.encode()).hexdigest()

    mixed = _km([ord(c) for c in digest[-6:]])
    a = str(sum(mixed) % 100).rjust(2, "0")
    s = _av(digest[:5], R_CHARSET, -4)
    return s + a


def signed_params(path: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """拼一套完整可用的查询参数。"""
    ts = int(time.time())
    nonce = make_nonce(ts)
    params = {
        "app": "heybox",
        "os_type": "web",
        "x_app": "heybox_website",
        "x_client_type": "web",
        "x_os_type": "Windows",
        "client_type": "web",
        "web_version": "3.0",
        "version": "999.0.4",
        "hkey": make_hkey(path, ts, nonce),
        "_time": str(ts),
        "nonce": nonce,
    }
    if extra:
        params.update({k: str(v) for k, v in extra.items()})
    return params
