"""Epic 源：免费游戏日历（免 key）。

Epic 每周送的免费游戏是「史低」的极端情形（0 元），单独占一栏才有意义。
"""
from __future__ import annotations

from datetime import datetime, timezone

from .. import config, db, http

BASE = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"


def raw() -> dict:
    return http.get_json(
        BASE,
        params={
            "locale": "zh-CN",
            "country": config.CFG["country"].upper(),
            "allowCountries": config.CFG["country"].upper(),
        },
        ttl=3600,
    )


def _promos(elem: dict) -> tuple[dict | None, dict | None]:
    promos = elem.get("promotions") or {}
    now = datetime.now(timezone.utc)
    current = upcoming = None
    for p in promos.get("promotionalOffers") or []:
        for o in p.get("promotionalOffers") or []:
            start = o.get("startDate")
            end = o.get("endDate")
            if not start or not end:
                continue
            s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            if s <= now <= e:
                current = o
            elif s > now:
                upcoming = o
    return current, upcoming


def free_games() -> list[dict]:
    data = raw()
    elements = (((data.get("data") or {}).get("Catalog") or {}).get("searchStore") or {}).get("elements") or []
    out = []
    for el in elements:
        cur, up = _promos(el)
        if not cur and not up:
            continue
        offer = cur or up
        price = ((el.get("price") or {}).get("totalPrice") or {})
        original = (price.get("originalPrice") or 0) / 100
        out.append({
            "title": el.get("title"),
            "id": el.get("id"),
            "product_slug": el.get("productSlug") or el.get("urlSlug"),
            "status": "free_now" if cur else "free_soon",
            "start": (offer or {}).get("startDate"),
            "end": (offer or {}).get("endDate"),
            "original_price": original,
            "currency": price.get("currencyCode") or "CNY",
            "description": (el.get("description") or "")[:300],
            "image": next(
                (i.get("url") for i in (el.get("keyImages") or []) if i.get("type") in ("Thumbnail", "OfferImageWide")),
                None,
            ),
        })
    return out
