"""Steam 源：官方 Store API（免 key）+ SteamSpy（拿 tag）。

Steam 官方没有「历史最低价」这个东西 —— 它只告诉你当前折扣。所以这里的职责是：
拿到当前价、原价、折扣比例、以及元数据（类别/开发商/发售日/口碑），价格快照由 db 层累积。
"""
from __future__ import annotations

import re
from typing import Any

from .. import config, db, http

STORE = "https://store.steampowered.com"
SPY = "https://steamspy.com/api.php"


def app_details(appid: int, *, cached_only: bool = False) -> dict[str, Any] | None:
    node = app_details_many([appid], cached_only=cached_only).get(appid)
    return node


def app_details_many(appids: list[int], *, cached_only: bool = False,
                     concurrency: int = 1) -> dict[int, dict[str, Any]]:
    """逐个取详情。

    实测（2026-11-09）：`appids=1,2` 一律 400，Steam 这个端点不接受批量，
    所以这里只能一个个来；好在它单请求 17KB、0.3s 间隔连发 20 次都 200。

    `concurrency > 1` 时开线程池：每个线程各自按 0.3s 限速（`http._throttle` 的计时器是线程本地的），
    所以总速率 ≈ concurrency / 0.3。默认 1 = 老行为，别在没实测过的情况下调高。
    """
    out: dict[int, dict[str, Any]] = {}
    url = f"{STORE}/api/appdetails"

    def fetch(appid: int) -> tuple[int, dict[str, Any] | None]:
        try:
            data = http.get_json(
                url,
                params={"appids": appid, "cc": config.CFG["country"], "l": config.CFG["language"]},
                allow_cache_only=cached_only,
                rate=0.3,
            )
        except Exception:
            return appid, None
        node = data.get(str(appid)) or {}
        if node.get("success") and node.get("data"):
            return appid, node["data"]
        return appid, None

    if concurrency and concurrency > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=int(concurrency)) as ex:
            for appid, detail in ex.map(fetch, appids):
                if detail:
                    out[appid] = detail
        return out

    for appid in appids:
        appid, detail = fetch(appid)
        if detail:
            out[appid] = detail
    return out


def to_game(detail: dict[str, Any], appid: int) -> dict:
    """Steam appdetails → 本项目 games 行。"""
    price = detail.get("price_overview") or {}
    return {
        "appid": appid,
        "name": detail.get("name"),
        "name_cn": detail.get("name"),  # 国区 l=schinese 时 name 已经是中文名
        "type": detail.get("type"),
        "release_date": (detail.get("release_date") or {}).get("date"),
        "metacritic": (detail.get("metacritic") or {}).get("score"),
        "developers": detail.get("developers") or [],
        "publishers": detail.get("publishers") or [],
        "genres": [g.get("description") for g in detail.get("genres") or [] if g.get("description")],
        "tags": None,
        "short_desc": re.sub(r"<[^>]+>", "", detail.get("short_description") or "")[:600],
        "header_image": detail.get("header_image"),
        "shop_url": f"{STORE}/app/{appid}",
        "price": (price.get("final") or 0) / 100 if price else None,
        "list_price": (price.get("initial") or 0) / 100 if price else None,
        "discount": price.get("discount_percent") or 0,
        "currency": price.get("currency"),
    }


def spy_tags(appid: int, *, cached_only: bool = False) -> dict[str, int]:
    """SteamSpy 的 user tag → 票数。速限很紧，调用方要自己控频。"""
    data = http.get_json(
        SPY,
        params={"request": "appdetails", "appid": appid},
        ttl=30 * 24 * 3600,  # tag 基本不变，缓存一个月
        allow_cache_only=cached_only,
    )
    tags = data.get("tags") or {}
    if isinstance(tags, dict):
        return {str(k): int(v) for k, v in tags.items() if isinstance(v, (int, float))}
    return {}


def spy_meta(appid: int) -> dict:
    data = http.get_json(
        SPY,
        params={"request": "appdetails", "appid": appid},
        ttl=7 * 24 * 3600,
        allow_cache_only=True,
    )
    return {
        "owners": data.get("owners"),
        "ccu": data.get("ccu"),
        "average_forever": data.get("average_forever"),
        "positive": data.get("positive"),
        "negative": data.get("negative"),
    }


def featured_specials() -> list[int]:
    """Steam 首页「特惠」列表 —— 拿当前在打折的一批 appid，免 key。"""
    data = http.get_json(
        f"{STORE}/api/featuredcategories",
        params={"cc": config.CFG["country"], "l": config.CFG["language"]},
        ttl=3600,
    )
    appids: set[int] = set()
    for key in ("specials", "top_sellers", "new_releases"):
        block = data.get(key) or {}
        for item in block.get("items") or []:
            aid = item.get("id")
            if isinstance(aid, int):
                appids.add(aid)
    return sorted(appids)


def storesearch(term: str) -> list[dict]:
    """名字 → appid。给「只输入一个名字」这条路径用。"""
    data = http.get_json(
        f"{STORE}/api/storesearch",
        params={"term": term, "cc": config.CFG["country"], "l": config.CFG["language"]},
        ttl=7 * 24 * 3600,
    )
    out = []
    for it in data.get("items") or []:
        p = it.get("price") or {}
        out.append({
            "appid": it.get("id"),
            "name": it.get("name"),
            "tiny": it.get("tiny_image"),
            "price": (p.get("final") or 0) / 100 if p else None,
            "list_price": (p.get("initial") or 0) / 100 if p else None,
            "discount": p.get("discount_percent") or 0,
            "currency": p.get("currency"),
        })
    return out


def ingest(appid: int, *, with_tags: bool = True, cached_only: bool = False) -> dict | None:
    """抓一个游戏并落库（元数据 + 一条价格快照）。"""
    res = ingest_many([appid], with_tags=with_tags, cached_only=cached_only)
    return res[0] if res else None


def ingest_many(appids: list[int], *, with_tags: bool = True, cached_only: bool = False,
                tag_budget: int | None = None, concurrency: int = 1) -> list[dict]:
    """批量落库。SteamSpy 的 tag 是 1 请求/游戏，用 tag_budget 控制这一轮最多补几个。

    `concurrency` 只影响 appdetails 的抓取（那一步是大头）；tag 仍是逐个抓。
    """
    details = app_details_many(appids, cached_only=cached_only, concurrency=concurrency)
    out: list[dict] = []
    tags_left = len(appids) if tag_budget is None else tag_budget
    for appid in appids:
        detail = details.get(appid)
        if not detail:
            continue
        g = to_game(detail, appid)
        if with_tags and tags_left > 0:
            try:
                t = spy_tags(appid, cached_only=cached_only)
                if t:
                    g["tags"] = sorted(t.items(), key=lambda kv: -kv[1])[:20]
                    tags_left -= 1
            except Exception:
                tags_left -= 1  # 失败也算花掉一次预算，别把这一轮卡死
        db.upsert_game(g)
        if g.get("price") is not None:
            db.add_price({
                "appid": appid,
                "source": "steam",
                "shop": "Steam",
                "currency": g.get("currency") or config.CFG["currency"],
                "price": g["price"],
                "list_price": g.get("list_price"),
                "discount": g.get("discount"),
                "url": g["shop_url"],
            })
        out.append(g)
    return out
