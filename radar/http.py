"""带缓存 + 限速 + 落盘的 HTTP 层。

原则（见 AGENTS.md）：每个源的原始响应都要进 cache/，跑一次就有原文可复查。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import requests

from . import config

_local = threading.local()


def _cache_path(url: str) -> Path:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    slug = "".join(c if c.isalnum() else "_" for c in url.split("://", 1)[-1])[:60]
    return config.CACHE_DIR / f"{slug}-{h}.json"


def _throttle(host: str, rate: float | None = None) -> None:
    """按线程限速：同一个 host，每个线程之间间隔 `rate` 秒。

    计时器放线程本地（不是全局）是有意的：全局计时器会让 N 个线程互相等，
    并发就白加了；而单线程时两者行为完全一样，所以默认配置下没有任何变化。
    """
    gap = float(config.CFG["rate_limit_sec"] if rate is None else rate)
    last = getattr(_local, "last", None)
    if last is None:
        last = _local.last = {}
    prev = last.get(host, 0.0)
    wait = gap - (time.time() - prev)
    if wait > 0:
        time.sleep(wait)
    last[host] = time.time()


def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    ttl: int | None = None,
    allow_cache_only: bool = False,
    timeout: int | None = None,
    rate: float | None = None,
) -> Any:
    """GET 一个 JSON。缓存新鲜就直接用；否则联网，失败时退回旧缓存。"""
    ttl = config.CFG["cache_ttl_sec"] if ttl is None else ttl
    path = _cache_path(url if not params else url + "?" + json.dumps(params, sort_keys=True))

    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < ttl or allow_cache_only:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                path.unlink(missing_ok=True)

    if allow_cache_only:
        raise FileNotFoundError(f"无缓存且不允许联网: {url}")

    host = url.split("/")[2] if "//" in url else url
    _throttle(host, rate)
    sess_headers = {"User-Agent": config.CFG["user_agent"]}
    if headers:
        sess_headers.update(headers)
    resp = requests.get(
        url,
        params=params,
        headers=sess_headers,
        timeout=timeout or int(config.CFG["timeout_sec"]),
    )
    if resp.status_code != 200:
        if path.exists():  # 有旧的就先用旧的，别让一次 5xx 把界面搞空
            return json.loads(path.read_text(encoding="utf-8"))
        resp.raise_for_status()
    data = resp.json()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def get_text(url: str, **kw: Any) -> str:
    """取非 JSON 响应（HTML 之类），同样落盘。"""
    ttl = kw.pop("ttl", None)
    path = _cache_path(url).with_suffix(".txt")
    ttl = config.CFG["cache_ttl_sec"] if ttl is None else ttl
    if path.exists() and time.time() - path.stat().st_mtime < ttl:
        return path.read_text(encoding="utf-8")
    host = url.split("/")[2]
    _throttle(host)
    resp = requests.get(
        url,
        headers={"User-Agent": config.CFG["user_agent"]},
        timeout=int(config.CFG["timeout_sec"]),
    )
    resp.raise_for_status()
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def post_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    body: Any = None,
    ttl: int | None = None,
    timeout: int | None = None,
    rate: float | None = None,
) -> Any:
    """POST 一个 JSON 并落盘缓存（ITAD 的批量端点都是 POST，必须能缓存才能复查）。

    缓存键 = URL + 查询参数 + 请求体，三者任一不同就是不同条目。
    """
    ttl = config.CFG["cache_ttl_sec"] if ttl is None else ttl
    fp = url + "?" + json.dumps(params or {}, sort_keys=True) + "#" + json.dumps(body, sort_keys=True)
    path = _cache_path(fp)

    if path.exists():
        if time.time() - path.stat().st_mtime < ttl:
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                path.unlink(missing_ok=True)

    host = url.split("/")[2] if "//" in url else url
    _throttle(host, rate)
    resp = requests.post(
        url,
        params=params,
        json=body,
        headers={"User-Agent": config.CFG["user_agent"], "Content-Type": "application/json"},
        timeout=timeout or int(config.CFG["timeout_sec"]),
    )
    if resp.status_code != 200:
        if path.exists():  # 有旧缓存就先用旧的
            return json.loads(path.read_text(encoding="utf-8"))
        resp.raise_for_status()
    data = resp.json()
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data
