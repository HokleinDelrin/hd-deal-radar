"""CheapShark 源：免 key 的跨商店比价聚合，且带 cheapestPriceEver（历史最低）。

它是本项目「跨平台」能力的主力：一个接口就把 Steam / Fanatical / GreenManGaming /
GOG / Humble / WinGameStore 等几十家店的当前价和史低一起给你，还带 steamAppID 做锚点。
计价是美元，所以统一存 USD，和国区 CNY 分币种并列而不是硬换算。
"""
from __future__ import annotations

from typing import Any, Iterable

from .. import config, db, http

BASE = "https://www.cheapshark.com/api/1.0"


def stores() -> dict[str, str]:
    data = http.get_json(f"{BASE}/stores", ttl=7 * 24 * 3600)
    return {str(s["storeID"]): s["storeName"] for s in data if s.get("storeID")}


def deals(*, on_sale: bool = True, page: int = 0, upper_price: float | None = None,
          min_metacritic: int | None = None) -> list[dict]:
    params: dict[str, Any] = {"pageNumber": page, "storeID": 1}
    if on_sale:
        params["onSale"] = 1
    if upper_price:
        params["upperPrice"] = upper_price
    if min_metacritic:
        params["metacritic"] = min_metacritic
    return http.get_json(f"{BASE}/deals", params=params, ttl=1800)


def game_info(appid: int) -> dict | None:
    """单游戏：cheapest + deals + cheapestPriceEver。

    注意：`/games?id=` 要的是 CheapShark 自己的 gameID，不是 Steam appid ——
    传 Steam appid 会 404。正确路径是先 `/games?steamAppID=` 拿 gameID，再查详情。
    """
    rows = http.get_json(f"{BASE}/games", params={"steamAppID": appid}, ttl=3600)
    if not isinstance(rows, list) or not rows:
        return None
    gid = rows[0].get("gameID")
    if not gid:
        return None
    data = http.get_json(f"{BASE}/games", params={"id": gid}, ttl=3600)
    if not data or "info" not in data:
        return None
    return data


def history_low(appid: int) -> dict | None:
    """外部史低（美元）。仅作为对照列，不参与本项目史低计算。"""
    info = game_info(appid)
    if not info:
        return None
    ever = info.get("cheapestPriceEver") or {}
    if not ever.get("price"):
        return None
    return {
        "appid": appid,
        "title": (info.get("info") or {}).get("title"),
        "usd": float(ever["price"]),
        "date": ever.get("date"),
    }


def to_snapshots(appid: int, info: dict, store_names: dict[str, str]) -> list[dict]:
    out = []
    for d in info.get("deals") or []:
        price = d.get("price")
        retail = d.get("retailPrice")
        if price is None:
            continue
        out.append({
            "appid": appid,
            "source": "cheapshark",
            "shop": store_names.get(str(d.get("storeID")), f"store{d.get('storeID')}"),
            "currency": "USD",
            "price": float(price),
            "list_price": float(retail) if retail else None,
            "discount": int(round((1 - float(price) / float(retail)) * 100)) if retail and float(retail) > 0 else 0,
            "url": f"https://www.cheapshark.com/redirect?dealID={d.get('dealID')}" if d.get("dealID") else None,
        })
    return out


def ingest_deal_pages(pages: int = 3, *, seed_details: int = 0) -> dict:
    """跑几页 deals → 按 steamAppID 落价格快照。

    seed_details>0 时，顺带用 Steam 官方接口补前 N 个游戏的元数据（名字/图/国区价）。
    """
    from . import steam  # 避免循环导入

    names = stores()
    seen: set[int] = set()
    snaps = 0
    details = 0
    for page in range(pages):
        for d in deals(page=page):
            appid = d.get("steamAppID")
            if not appid:
                continue
            appid = int(appid)
            if appid in seen:
                continue
            info = game_info(appid)
            if not info:
                continue
            # 先建最小 games 行，否则这些 appid 只有价格没有名字，界面里查不到
            existing = db.get_game(appid)
            if existing is None:
                meta = info.get("info") or {}
                db.upsert_game({
                    "appid": appid,
                    "name": meta.get("title") or str(appid),
                    "short_desc": None,
                    "header_image": meta.get("thumb"),
                    "shop_url": f"https://store.steampowered.com/app/{appid}",
                })
            # 只有缺元数据的才补 Steam，省请求
            elif details < seed_details and not existing["tags"]:
                try:
                    if steam.ingest(appid):
                        details += 1
                except Exception:
                    pass
            elif details < seed_details and not existing["metacritic"]:
                try:
                    if steam.ingest(appid):
                        details += 1
                except Exception:
                    pass
            for s in to_snapshots(appid, info, names):
                db.add_price(s)
                snaps += 1
            seen.add(appid)
    return {"games": len(seen), "snapshots": snaps, "steam_details": details}
