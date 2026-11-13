"""IsThereAnyDeal 源（可选，需要 API key）。

ITAD 是覆盖最广的一家：几十家店的当前价 + 官方口径的历史最低（all / y1 / m3 三档）。
三条设计原则：

1. **没 key 就安静跳过**，绝不能让缺 key 把整条采集链挂掉。
2. **一律走批量端点**：`POST /games/prices/v3` 与 `POST /games/historylow/v1` 都收**纯 UUID 数组**
   （单次上限 200，`200` 很稳，`201` 没验证过），逐个查等于把「1000 请求 / 5 分钟」的额度烧在握手上。
3. **appid → uuid 的映射可以提前建**：`POST /lookup/id/shop/61/v1` 匿名可调（无需 key），
   所以等 key 到手时只剩 prices + historylow 两步，不用再排队解析。

实测（2026-11-12，官方 OpenAPI 仓库 github.com/IsThereAnyDeal/API + 本机 curl）：

- 无 key → **403** `{"status_code":403,"reason_phrase":"Missing api key"}`；坏 key 同样是 403 但 reason 是
  `Invalid or expired api key`。**不是 401**，字段名是 `reason_phrase`。
- `/games/prices/v3`：query 里 `country` / `deals` / `vouchers`；`shops` 必须写成 `61,35`（form+explode=false）；
  **body 是纯数组** `["uuid1","uuid2"]`，不是 `{"ids":[...]}`。
- `/games/historylow/v1`（全小写）：body 同样是纯数组，返回 `[{id, low:{shop, price, regular, cut, timestamp}}]`。
  `/games/history/low/v2` **不存在**（GET 打过去是 404，很容易误判成端点下线）。
- `/games/lookup/v1` 一次只能一个 appid，查不到是 **200 + `{"found":false}`**；
  批量映射要用 `POST /lookup/id/shop/61/v1`，body `["app/220","app/620"]`，返回 `{"app/220": uuid|null}`。
- 额度：**1000 请求 / 5 分钟**（免费、需验证邮箱，无付费层级）。响应里没有 `x-ratelimit` 头，只能客户端自己控。
  注册页：https://isthereanydeal.com/apps/my/

货币口径要盯住：国区拿到的应该是 CNY。ITAD 若给了别的币种（某些店只报 USD），
一律**不入库**只计数 —— 混币会把「本地史低」按美元数字压成假史低。
"""
from __future__ import annotations

import time
from typing import Any, Iterable

import requests

from .. import config, db, http

BASE = "https://api.isthereanydeal.com"
MAX_IDS = 200          # 批量端点单次上限（201 未验证，按 200 切最稳）
STEAM_SHOP_ID = 61     # ITAD 里 Steam 的 shop id
_LOOKUP_META = "itad_map"   # {steam_appid: itad_uuid}
_LOW_META = "itad_low"      # {steam_appid: {"cents": int, "at": str, "shop": str}}


def enabled() -> bool:
    return bool(config.CFG.get("itad_api_key"))


def _key() -> str:
    return str(config.CFG.get("itad_api_key") or "")


def _country() -> str:
    return str(config.CFG["country"]).upper()


# ---------- 底层请求 ----------
def _post(endpoint: str, body: Any, *, params: dict | None = None, ttl: int = 6 * 3600) -> Any:
    """带 429 退避的 POST。ITAD 限流时给 429 + Retry-After。"""
    q = {"key": _key()}
    if params:
        q.update(params)
    for attempt in (1, 2):
        try:
            return http.post_json(f"{BASE}{endpoint}", params=q, body=body, ttl=ttl)
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", 0)
            if status == 429 and attempt == 1:
                wait = int(exc.response.headers.get("Retry-After", "5") or 5)
                time.sleep(min(30, max(1, wait)))
                continue
            raise
    return None


def _amount(node: dict | None) -> float | None:
    """ITAD 的金额节点：{"amount": 9.99, "amountInt": 999, "currency": "CNY"}。"""
    if not isinstance(node, dict):
        return None
    v = node.get("amount")
    return float(v) if isinstance(v, (int, float)) else None


def _load_map() -> dict[int, str]:
    raw = db.get_meta(_LOOKUP_META, {}) or {}
    return {int(k): v for k, v in raw.items() if v}


def _save_map(mapping: dict[int, str]) -> None:
    cur = _load_map()
    cur.update(mapping)
    db.set_meta(_LOOKUP_META, {str(k): v for k, v in cur.items()})


# ---------- ID 映射 ----------
def lookup_steam(appid: int) -> str | None:
    """单个 Steam appid → ITAD uuid。走批量端点，所以它也免 key。"""
    return lookup_many([int(appid)]).get(int(appid))


def lookup_many(appids: Iterable[int], *, verbose: bool = False) -> dict[int, str]:
    """批量 appid → uuid。`POST /lookup/id/shop/61/v1`，body 是 `["app/220", ...]`。

    这个端点**匿名可调**（不需要 key）：所以映射表可以在没 key 的时候就建好。
    只对 meta 里没有的发请求；结果长期复用（同一款游戏的 uuid 不会变）。
    """
    want = [int(a) for a in appids if a]
    have = _load_map()
    missing = [a for a in want if a not in have]
    out = {a: have[a] for a in want if a in have}
    if not missing:
        return out
    for i in range(0, len(missing), MAX_IDS):
        chunk = missing[i:i + MAX_IDS]
        data = http.post_json(
            f"{BASE}/lookup/id/shop/{STEAM_SHOP_ID}/v1",
            body=[f"app/{a}" for a in chunk],
            ttl=30 * 24 * 3600,
        )
        got: dict[int, str] = {}
        if isinstance(data, dict):
            for key, uuid in data.items():
                if not uuid:
                    continue
                try:
                    appid = int(str(key).split("/")[-1])
                except ValueError:
                    continue
                got[appid] = str(uuid)
        if got:
            _save_map(got)
        out.update(got)
        if verbose:
            print(f"[itad] lookup {min(i + MAX_IDS, len(missing))}/{len(missing)} 命中 {len(got)}", flush=True)
    return out


# ---------- 价格 ----------
def prices_batch(itad_ids: list[str], *, country: str | None = None) -> dict[str, dict]:
    """各店当前价，按 uuid 返回原始节点。批量 200 个一次，body 是纯数组。"""
    if not enabled() or not itad_ids:
        return {}
    out: dict[str, dict] = {}
    ids = list(itad_ids)
    for i in range(0, len(ids), MAX_IDS):
        chunk = ids[i:i + MAX_IDS]
        data = _post(
            "/games/prices/v3",
            chunk,
            params={"country": (country or _country()), "deals": "true", "vouchers": "true"},
            ttl=3600,
        )
        for node in data or []:
            if isinstance(node, dict) and node.get("id"):
                out[node["id"]] = node
    return out


def low_row(node: dict) -> dict | None:
    """把 `historylow/v1` 的单个元素转成 {amount, currency, at, shop}。纯函数，selftest 用。

    响应形态是 `{id, low:{shop:{id,name}, price:{amount,amountInt,currency}, regular, cut, timestamp}}`：
    价格在 `low.price.amount`，不是扁平的 `low.amount`。
    """
    if not isinstance(node, dict) or not node.get("id"):
        return None
    low = node.get("low") or {}
    price = low.get("price") or {}
    amount = _amount(price)
    if amount is None:
        return None
    return {
        "amount": amount,
        "currency": (price.get("currency") or "").upper(),
        "at": low.get("timestamp"),
        "shop": (low.get("shop") or {}).get("name"),
    }


def lows_batch(itad_ids: list[str], *, country: str | None = None) -> dict[str, dict]:
    """官方口径历史最低（`POST /games/historylow/v1`，全小写、批量）。

    返回 {uuid: {"amount", "currency", "at", "shop"}}。
    """
    if not enabled() or not itad_ids:
        return {}
    out: dict[str, dict] = {}
    ids = list(itad_ids)
    for i in range(0, len(ids), MAX_IDS):
        chunk = ids[i:i + MAX_IDS]
        data = _post(
            "/games/historylow/v1",
            chunk,
            params={"country": (country or _country())},
            ttl=12 * 3600,
        )
        for node in data or []:
            row = low_row(node)
            if row:
                out[node["id"]] = row
    return out


def history_low(itad_id: str, country: str | None = None) -> dict | None:
    """单个 uuid 的官方史低；内部走批量端点，保持旧签名给命令行用。"""
    got = lows_batch([itad_id], country=country).get(itad_id)
    if not got:
        return None
    return {"amount": got["amount"], "currency": got["currency"],
            "timestamp": got["at"], "shop": got["shop"]}


def prices(itad_id: str, country: str | None = None) -> list[dict]:
    """单个 uuid 的各店价格，拍平成入库用的行。"""
    node = prices_batch([itad_id], country=country).get(itad_id) or {}
    rows = []
    for d in node.get("deals") or []:
        amount = _amount(d.get("price"))
        if amount is None:
            continue
        rows.append({
            "shop": (d.get("shop") or {}).get("name") or "unknown",
            "price": amount,
            "currency": ((d.get("price") or {}).get("currency") or "").upper(),
            "discount": d.get("cut") or 0,
            "url": d.get("url"),
            "regular": _amount(d.get("regular")),
        })
    return rows


# ---------- 入库 ----------
def _save_lows(appid: int, low: dict | None, currency: str) -> bool:
    """官方史低存 meta（games 表那两列是 Steam 专用口径，别混）。"""
    if not low or low.get("currency") != currency:
        return False
    cents = int(round(float(low["amount"]) * 100))
    cur = db.get_meta(_LOW_META, {}) or {}
    old = cur.get(str(appid)) or {}
    # 同一口径下只保留更低的那条
    if old.get("cents") is not None and old["cents"] <= cents:
        return False
    cur[str(appid)] = {"cents": cents, "at": low.get("at"), "shop": low.get("shop")}
    db.set_meta(_LOW_META, cur)
    return True


def deal_rows(node: dict, currency: str | None = None) -> tuple[list[dict], int]:
    """把 prices/v3 单个元素转成入库行。纯函数，selftest 用 fixture 断言的就是它。

    返回 (rows, skipped_non_currency)。币种对不上的行直接丢 —— 混币会造出假史低。
    """
    want = (currency or str(config.CFG["currency"])).upper()
    rows: list[dict] = []
    skipped = 0
    for d in node.get("deals") or []:
        amount = _amount(d.get("price"))
        if amount is None:
            continue
        cur = ((d.get("price") or {}).get("currency") or "").upper()
        if cur and cur != want:
            skipped += 1
            continue
        rows.append({
            "shop": (d.get("shop") or {}).get("name") or "itad",
            "currency": want,
            "price": amount,
            "list_price": _amount(d.get("regular")),
            "discount": d.get("cut") or 0,
            "url": d.get("url"),
        })
    return rows, skipped


def ingest_many(appids: Iterable[int], *, limit: int = 100, with_lows: bool = True) -> dict:
    """把一批 Steam 游戏在 ITAD 上的多店价落库。没 key 时返回 skipped。"""
    if not enabled():
        return {"status": "skipped", "reason": "no itad_api_key"}
    ids = [int(a) for a in appids if a and int(a) > 0][:limit]
    mapping = lookup_many(ids)
    if not mapping:
        return {"status": "empty", "reason": "一个 appid 都没解析到 ITAD id"}
    by_id = {v: k for k, v in mapping.items()}
    price_nodes = prices_batch(list(mapping.values()))
    lows = lows_batch(list(mapping.values())) if with_lows else {}

    currency = str(config.CFG["currency"]).upper()
    written = skipped_currency = low_hits = 0
    for iid, node in price_nodes.items():
        appid = by_id.get(iid)
        if not appid:
            continue
        rows, skipped = deal_rows(node, currency)
        skipped_currency += skipped
        for row in rows:
            if db.add_price({"appid": appid, "source": "itad", **row}):
                written += 1
    for iid, low in lows.items():
        appid = by_id.get(iid)
        if appid and _save_lows(appid, low, currency):
            low_hits += 1
    return {
        "status": "ok",
        "games": len(mapping),
        "new_snapshots": written,
        "history_lows": low_hits,
        "skipped_non_cny": skipped_currency,
        "unresolved": len(ids) - len(mapping),
    }


def ingest(appid: int) -> dict:
    """单个游戏（兼容旧调用）。"""
    res = ingest_many([appid], limit=1)
    if res.get("status") != "ok":
        return res
    iid = lookup_steam(int(appid))
    return {"status": "ok", "itad_id": iid, "deals": res["new_snapshots"],
            "history_low": history_low(iid) if iid else None}


# ---------- 自检 ----------
def verify_key(key: str | None = None) -> dict:
    """在线校验 key 是否可用。返回 {ok, status, detail}，不抛异常。

    ITAD 的错误口径（实测）：403 + `{"status_code":403,"reason_phrase":"..."}`，
    无 key 是 `Missing api key`，坏 key 是 `Invalid or expired api key`。不是 401。
    """
    k = (key or _key()).strip()
    if not k:
        return {"ok": False, "status": "missing",
                "detail": "没有 itad_api_key（去 https://isthereanydeal.com/apps/my/ 免费拿，需验证邮箱）"}
    try:
        resp = requests.get(
            f"{BASE}/games/lookup/v1",
            params={"key": k, "appid": "1245620"},   # 只狼，稳定存在的 appid
            timeout=int(config.CFG["timeout_sec"]),
            headers={"User-Agent": config.CFG["user_agent"]},
        )
    except Exception as exc:
        return {"ok": False, "status": "network", "detail": f"{type(exc).__name__}: {exc}"}
    if resp.status_code == 403:
        try:
            reason = (resp.json() or {}).get("reason_phrase") or ""
        except ValueError:
            reason = resp.text[:120]
        return {"ok": False, "status": "403", "detail": f"key 被拒：{reason or 'Missing/Invalid api key'}"}
    if resp.status_code != 200:
        return {"ok": False, "status": str(resp.status_code), "detail": resp.text[:200]}
    try:
        body = resp.json() or {}
    except ValueError:
        return {"ok": False, "status": "bad_json", "detail": resp.text[:200]}
    game = body.get("game") or {}
    if game.get("id"):
        return {"ok": True, "status": "200",
                "detail": f"可用，样例 id={game.get('id')} title={game.get('title')}"}
    if body.get("found") is False:
        return {"ok": False, "status": "no_game", "detail": "接口通了但查不到样例游戏，key 可能受限"}
    return {"ok": True, "status": "200", "detail": f"可用（响应 {str(body)[:80]}）"}
