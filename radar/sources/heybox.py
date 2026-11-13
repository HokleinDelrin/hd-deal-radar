"""小黑盒源：国内最便宜的一档（第三方 key / 商城价），同时给 Steam 史低。

为什么值得单独做一个源：
1. 它给的 `price.lowest_price` 是 **Steam 官方史低**（不用 key，比 ITAD 还省事）；
2. 它给的 `heybox_price.final_price` 是 **小黑盒到手价**（含券），常年低于 Steam 国区价 ——
   实测《最终幻想7 重制版》Steam 198 / 史低 49.5，小黑盒 33（-82%）。

签名见 `radar/heybox_sign.py`。请求要带 `Referer: https://www.xiaoheihe.cn/`，
不带也能通，但带上更像正常流量。

**登录态优先**（2026-11-12）：配置了小黑盒登录（`radar/heybox_login.py`，cookie 存
`data/heybox_cookies.json`）后，请求会带上你自己的会话 cookie。签名仍然要带 —— 它防爬不防鉴权，
两件事不是一回事；带登录态的意义是「以自己账号的身份、按自己的频率取」。
"""
from __future__ import annotations

from typing import Any

from .. import config, db, heybox_login, heybox_sign, http

API = "https://api.xiaoheihe.cn"
REFERER = {"Referer": "https://www.xiaoheihe.cn/", "Origin": "https://www.xiaoheihe.cn"}


def headers() -> dict[str, str]:
    """基础头 + 登录态（有就带上）。"""
    h = dict(REFERER)
    cookie = heybox_login.load()
    if cookie:
        h["Cookie"] = cookie
    return h


def logged_in() -> bool:
    return bool(heybox_login.load())


def enabled() -> bool:
    """要不要用这个源。默认**必须先登录**（config.heybox_require_login）。

    这样「小黑盒数据」跟「你自己的账号」绑在一起：没登录就只有 Steam / ITAD / CheapShark / Epic。
    想彻底不用，把 heybox_require_login 和采集参数都关掉即可，其它源不依赖它。
    """
    if not bool(config.CFG.get("heybox_require_login", True)):
        return True
    return logged_in()


def skip_reason() -> str:
    if bool(config.CFG.get("heybox_require_login", True)) and not logged_in():
        return "没有登录小黑盒：跑 `heybox-login`（或桌面窗口里点「登录小黑盒」）后再试"
    return ""


def _get(path: str, extra: dict[str, Any] | None = None, ttl: int = 3600,
         cached_only: bool = False) -> dict:
    params = heybox_sign.signed_params(path, {k: str(v) for k, v in (extra or {}).items()})
    return http.get_json(
        API + path, params=params, headers=headers(),
        ttl=ttl, allow_cache_only=cached_only, rate=0.8,
    )


def game_detail(steam_appid: int, *, cached_only: bool = False) -> dict | None:
    d = _get("/game/get_game_detail/", {"steam_appid": steam_appid},
             ttl=3600, cached_only=cached_only)
    if (d.get("status") or "") != "ok":
        return None
    return d.get("result")


def game_infos(appids: list[int], *, cached_only: bool = False) -> list[dict]:
    """批量接口：一次问多个 appid，返回结构和 `get_game_detail` 的 result 一致（含 price）。

    实测参数名是 `appids`（逗号分隔），叫 `steam_appids` 会报「参数 appids 缺失」。
    """
    if not appids:
        return []
    d = _get("/game/get_game_infos/", {"appids": ",".join(str(a) for a in appids)},
             ttl=3600, cached_only=cached_only)
    if (d.get("status") or "") != "ok":
        return []
    return (d.get("result") or {}).get("base_infos") or []


def parse(res: dict) -> dict:
    """把小黑盒的响应摊平成我们自己的字段。

    金额换算：`*_coin` 是「币」，1000 币 = 1 元（原价 198000 币 = 198 元，据此验证）。
    `final_price` 已经是字符串元，优先用它。
    """
    price = res.get("price") or {}
    hb = res.get("heybox_price") or {}
    coupon = hb.get("coupon_info") or {}

    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    # 注意：`final_price`（用券后到手价）是嵌在 coupon_info 里的，不在 heybox_price 顶层。
    # 顶层对应的是 cost_coin/1000（原价直接买）。实测 FF7RE：coin 35600→35.6，用券 final 33。
    final = f(coupon.get("final_price")) or f(hb.get("final_price"))
    coin_yuan = (f(hb.get("cost_coin")) or 0) / 1000 or None

    return {
        "appid": int(res.get("steam_appid") or res.get("appid") or 0),
        "name": res.get("name"),
        "name_en": res.get("name_en"),
        "image": res.get("image"),
        "score": f(res.get("score")),
        "platforms": [p.get("platf") for p in res.get("platforms_list") or []],
        # Steam 官方口径
        "steam_current": f(price.get("current")),
        "steam_initial": f(price.get("initial")),
        "steam_discount": price.get("discount") or 0,
        "steam_lowest": f(price.get("lowest_price")),
        "steam_lowest_discount": price.get("lowest_discount") or 0,
        "steam_is_lowest": bool(price.get("is_lowest")),
        # 小黑盒商城
        "hb_final": final,
        "hb_coin": coin_yuan,
        "hb_original": (f(hb.get("original_coin")) or 0) / 1000 or None,
        "hb_discount": hb.get("discount") or 0,
        "hb_is_lowest": bool(hb.get("is_lowest") or hb.get("new_lowest")),
        "hb_super_lowest": bool(hb.get("super_lowest")),
        "hb_coupon": {
            "desc": coupon.get("coupon_desc"),
            "max_reduce": (f(coupon.get("max_reduce")) or 0) / 100 or None,
        } if coupon else None,
        "has_price": bool(hb.get("is_game_price")),
        "deadline": (hb.get("time_info") or {}).get("end_time"),
        "sku_id": hb.get("sku_id"),
    }


def ingest(appid: int, *, cached_only: bool = False) -> dict | None:
    """抓一个 Steam appid 的小黑盒数据，落库。"""
    res = game_detail(appid, cached_only=cached_only)
    if not res:
        return None
    return _store(appid, res)


def _store(appid: int, res: dict) -> dict | None:
    p = parse(res)
    if not p["name"]:
        return None

    db.upsert_game({
        "appid": appid,
        "name": p["name"],
        "name_cn": p["name"],
        "header_image": p["image"],
        "shop_url": f"https://www.xiaoheihe.cn/app/topic/game/pc/{appid}",
    })
    # 小黑盒到手价进价格表，独立 shop，参与比价但用 CNY
    if p["hb_final"] or p["hb_coin"]:
        db.add_price({
            "appid": appid,
            "source": "heybox",
            "shop": "小黑盒",
            "currency": "CNY",
            "price": p["hb_final"] or p["hb_coin"],
            "list_price": p["hb_original"] or p["steam_initial"],
            "discount": p["hb_discount"],
            "url": f"https://www.xiaoheihe.cn/app/topic/game/pc/{appid}",
        })
    db.set_game_external(appid, steam_lowest=p["steam_lowest"],
                         heybox_final=p["hb_final"] or p["hb_coin"],
                         heybox_deadline=p["deadline"])
    return p


def ingest_many(appids: list[int], *, chunk: int = 20, cached_only: bool = False) -> dict:
    """批量补小黑盒价。用 `get_game_infos` 一次问 chunk 个，失败再退回单个。"""
    ok = 0
    for i in range(0, len(appids), chunk):
        part = appids[i : i + chunk]
        try:
            infos = game_infos(part, cached_only=cached_only)
        except Exception:
            infos = []
        got = set()
        for res in infos:
            aid = int(res.get("steam_appid") or 0)
            if not aid:
                continue
            if _store(aid, res):
                ok += 1
                got.add(aid)
        # 批量没覆盖到的，逐个补一次
        for a in part:
            if a in got:
                continue
            try:
                if ingest(a, cached_only=cached_only):
                    ok += 1
            except Exception:
                pass
    return {"tried": len(appids), "ok": ok}


def batch(appids: list[int], *, cached_only: bool = False) -> dict:
    return ingest_many(appids, cached_only=cached_only)


def refresh_deadlines(appids: list[int] | None = None, limit: int = 200) -> dict:
    """只补「优惠截止时间」。

    实测（2026-11-09）：批量接口 `get_game_infos` **不返回** `time_info`，
    只有单查 `get_game_detail` 才有 —— 所以券倒计时只能逐个问，成本 0.8s/个。
    因此这里单独一个函数，由调度器按天限流调用，别混进批量流程里拖慢采集。
    """
    if appids is None:
        with db.conn() as c:
            appids = [r[0] for r in c.execute(
                "SELECT appid FROM games WHERE heybox_cents IS NOT NULL "
                "ORDER BY heybox_cents ASC LIMIT ?", (limit,)).fetchall()]
    ok = 0
    for a in appids:
        try:
            res = game_detail(a)
            if not res:
                continue
            deadline = ((res.get("heybox_price") or {}).get("time_info") or {}).get("end_time")
            if deadline:
                db.set_game_external(a, heybox_deadline=int(deadline))
                ok += 1
        except Exception:
            continue
    return {"tried": len(appids), "with_deadline": ok}
