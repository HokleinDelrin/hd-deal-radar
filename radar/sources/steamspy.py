"""SteamSpy 源：tag 与候选池。

为什么单独一个文件：Steam 官方的 appdetails 不给 tag，而 tag 是本项目画像的核心燃料。
SteamSpy 的 `request=appdetails` 固定返回 top-20 tag + 票数，票数正好当 tf 权重用。

限速教训（2026-11-09 实测）：单次 1.2–2.3s，不要并发；tag 一月一刷就够，所以全部走长 TTL 缓存。
另外 `average_forever` 现在全是 0（已停更），别拿它当玩时长信号。
"""
from __future__ import annotations

import re

from .. import config, db, http

API = "https://steamspy.com/api.php"
RATE = 1.3  # 秒/请求，实测这个节奏不会被踢


def appdetails(appid: int, *, ttl: int = 30 * 24 * 3600, cached_only: bool = False) -> dict:
    return http.get_json(
        API,
        params={"request": "appdetails", "appid": appid},
        ttl=ttl,
        allow_cache_only=cached_only,
        rate=RATE,
    )


def top100in2weeks() -> dict[str, dict]:
    """最近两周最热 100 个（一次请求拿 100 个游戏的 tag，性价比最高）。"""
    return http.get_json(API, params={"request": "top100in2weeks"}, ttl=12 * 3600, rate=RATE)


def top100forever() -> dict[str, dict]:
    return http.get_json(API, params={"request": "top100forever"}, ttl=7 * 24 * 3600, rate=RATE)


def all_page(page: int) -> dict[str, dict]:
    """全量 appid 分页，每页 1000。别一次跑完 —— 几百页。"""
    return http.get_json(API, params={"request": "all", "page": page}, ttl=7 * 24 * 3600, rate=RATE)


def tags_of(appid: int, *, cached_only: bool = False) -> list[list]:
    d = appdetails(appid, cached_only=cached_only)
    t = d.get("tags") or {}
    if not isinstance(t, dict):
        return []
    return sorted([[str(k), int(v)] for k, v in t.items() if isinstance(v, (int, float))],
                  key=lambda kv: -kv[1])[:20]


def _review(d: dict) -> tuple[float | None, int | None]:
    """好评率与评价数。分数没有评价数就没意义（3 条好评 = 100%），所以两个一起存。"""
    pos, neg = d.get("positive") or 0, d.get("negative") or 0
    total = int(pos) + int(neg)
    if total <= 0:
        return None, None
    return round(pos / total * 100, 1), total


def enrich_appid(appid: int, *, cached_only: bool = False) -> dict:
    """一次请求同时拿 tag 和口碑，直接落库。enrich 命令走这里。"""
    d = appdetails(appid, cached_only=cached_only)
    tags = sorted([[str(k), int(v)] for k, v in (d.get("tags") or {}).items()
                   if isinstance(v, (int, float))], key=lambda kv: -kv[1])[:20] if isinstance(d.get("tags"), dict) else []
    score, count = _review(d)
    if tags:
        db.set_game_tags(appid, tags)
    db.set_game_reviews(appid, score, count)
    return {"tags": len(tags), "score": score, "count": count}


def _meta(d: dict) -> dict:
    pos, neg = d.get("positive") or 0, d.get("negative") or 0
    total = pos + neg
    return {
        "owners": d.get("owners"),
        "ccu": d.get("ccu"),
        "recommendations": total,
        "review_score": round(pos / total * 100, 1) if total else None,
        "price_hint": d.get("price"),
        "genre": d.get("genre"),
    }


def ingest_from_steamspy(d: dict, *, want_tags: bool = True) -> dict:
    """SteamSpy 的记录 → games 行。名字/价格以 Steam 官方为准，这里只补 tag 与口碑。"""
    appid = int(d.get("appid") or 0)
    if not appid:
        return {}
    row = {
        "appid": appid,
        "name": d.get("name"),
        "tags": sorted([[k, int(v)] for k, v in (d.get("tags") or {}).items()], key=lambda kv: -kv[1])[:20]
        if want_tags and isinstance(d.get("tags"), dict) else None,
        "developers": [d["developer"]] if d.get("developer") else [],
        "publishers": [d["publisher"]] if d.get("publisher") else [],
        "metacritic": None,
        "short_desc": None,
        "header_image": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
        "shop_url": f"https://store.steampowered.com/app/{appid}",
    }
    db.upsert_game(row)
    score, count = _review(d)
    if score is not None:
        db.set_game_reviews(appid, score, count)
    return {**row, **_meta(d)}


def harvest(pages: int = 3, *, source: str = "top100in2weeks") -> dict:
    """拉热门榜单，把 tag 垫进库里。价格留空，等 Steam 那边补。"""
    pool: dict[str, dict] = {}
    if source == "top100in2weeks":
        pool.update(top100in2weeks())
    elif source == "top100forever":
        pool.update(top100forever())
    else:  # all page 模式
        for p in range(pages):
            pool.update(all_page(p))
    n = 0
    for key, rec in pool.items():
        rec.setdefault("appid", key)
        if ingest_from_steamspy(rec).get("tags"):
            n += 1
    return {"source": source, "games": len(pool), "with_tags": n}
