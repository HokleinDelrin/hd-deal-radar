"""Steam 个人库：把「玩过什么、玩了多久、愿望单里有什么」变成画像与关注列表。

两条路，别混：

1. **官方 Web API（需 steam_api_key）**：`IPlayerService/GetOwnedGames` 给总时长 + 近两周时长；
   `IWishlistService/GetWishlist` 给愿望单。这是唯一能拿到「库内清单」的路。
2. **公开社区 XML（免 key）**：`/profiles/<steamid64>/?xml=1` 仍可用，给 steamID64 / 昵称 / 隐私状态。
   它的价值是「填 ID 时立刻验证、并提前告诉你 profile 会不会公开」，库数据它给不了。

实测记录（2026-11-12 复测）：
- `GetOwnedGames` 无 key → 401。
- `IWishlistService/GetWishlist` 无 key → 200，但 profile 非公开时返回 `{"response":{}}`。
- `community/profiles/<id>/games?tab=all&xml=1` 无 key → **302 到登录页**，旧的免 key 导出已废，别再试。
- `community/id/<vanity>/?xml=1` → 200，含 `<steamID64>` 与 `<privacyState>`（public / friendsonly / private）。
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import Any

from .. import config, db, http

BASE = "https://api.steampowered.com"
COMMUNITY = "https://steamcommunity.com"

_STEAMID_RE = re.compile(r"^\d{17}$")
_PROFILE_URL_RE = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
_VANITY_URL_RE = re.compile(r"steamcommunity\.com/id/([^/?#\s]+)")


class SteamError(RuntimeError):
    """Steam 侧的可解释错误（缺 key / 私密 / 无此账号），cli 直接打印 message。"""


# ---------- 身份解析 ----------
def _key() -> str:
    return str(config.CFG.get("steam_api_key") or "")


def parse_ref(raw: str) -> tuple[str | None, str | None]:
    """不联网解析：steamID64 / profiles 链接 / id 链接 / 自定义名 → (steamid64, vanity)。"""
    raw = (raw or "").strip().strip('"').strip("'")
    if not raw:
        return None, None
    if _STEAMID_RE.match(raw):
        return raw, None
    m = _PROFILE_URL_RE.search(raw)
    if m:
        return m.group(1), None
    m = _VANITY_URL_RE.search(raw)
    if m:
        return None, m.group(1)
    if "/" not in raw and " " not in raw:
        return None, raw  # 当自定义主页名处理
    return None, None


def _xml_get(url: str, ttl: int = 24 * 3600) -> ET.Element | None:
    text = http.get_text(url, ttl=ttl)
    if not text or text.lstrip().startswith("<!"):
        # 登录页 / 错误 HTML：Steam 会在这类情况下直接吐 HTML
        raise SteamError("Steam 返回的是 HTML（多半被登录墙拦住或链接写错了）")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:
        raise SteamError(f"profile XML 解析失败: {exc}") from exc


def profile_info(ref: str) -> dict:
    """用公开 profile XML 验证一个引用，顺便拿到隐私状态。免 key。"""
    steamid, vanity = parse_ref(ref)
    if steamid:
        url = f"{COMMUNITY}/profiles/{steamid}/?xml=1"
    elif vanity:
        url = f"{COMMUNITY}/id/{vanity}/?xml=1"
    else:
        raise SteamError("看不懂这个输入：给 17 位 steamID64、个人主页链接，或自定义主页名")

    root = _xml_get(url)
    if root is None:
        raise SteamError("Steam 没有返回 profile")
    if root.tag != "profile":
        err = root.findtext("error") or root.findtext(".//error") or "未知结构"
        raise SteamError(f"没找到这个 Steam 账号: {err}")

    def t(tag: str) -> str | None:
        el = root.find(tag)
        return (el.text or "").strip() if el is not None and el.text else None

    sid = t("steamID64") or steamid
    privacy = (t("privacyState") or "").lower()
    return {
        "steamid": sid,
        "vanity": t("steamID"),
        "nick": t("steamID"),
        "privacy": privacy or "unknown",
        "public": privacy == "public",
        "avatar": t("avatarFull") or t("avatarIcon"),
        "url": f"{COMMUNITY}/profiles/{sid}/",
    }


def current_steamid() -> str:
    """config 里的 steam_id，允许写链接或自定义名（能解析就联网解析）。"""
    raw = str(config.CFG.get("steam_id") or "").strip()
    if not raw:
        raise SteamError("没有 steam_id：跑 `python -m radar.cli keys --steam-id <你的主页链接或17位ID>`")
    steamid, _ = parse_ref(raw)
    if steamid:
        return steamid
    return str(profile_info(raw)["steamid"])


# ---------- 库数据 ----------
def owned_games(steamid: str | None = None) -> list[dict]:
    """[{appid, name, playtime_forever(分钟), playtime_2weeks}]。必须 key + profile 公开。"""
    if not _key():
        raise SteamError(
            "GetOwnedGames 必须带 key（2026-11-12 实测无 key → 401）；"
            "去 https://steamcommunity.com/dev/apikey 申请后跑 `python -m radar.cli keys --steam-key <KEY>`"
        )
    sid = steamid or current_steamid()
    data = http.get_json(
        f"{BASE}/IPlayerService/GetOwnedGames/v1/",
        params={
            "key": _key(),
            "steamid": sid,
            "include_appinfo": 1,
            "include_played_free_games": 1,
            "format": "json",
        },
        ttl=6 * 3600,
    )
    games = (data.get("response") or {}).get("games") or []
    if not games:
        raise SteamError(
            "库里读不到游戏：多半是 Steam「隐私设置 → 游戏详情」不是公开，"
            "或这个账号确实一个游戏都没有（试玩/免费游戏也算，但要 include_played_free_games）"
        )
    return games


def wishlist(steamid: str | None = None) -> list[dict]:
    """[{appid, priority, date_added}]。免 key，但 profile 的「游戏详情/愿望单」要公开。"""
    sid = steamid or current_steamid()
    data = http.get_json(
        f"{BASE}/IWishlistService/GetWishlist/v1/",
        params={"steamid": sid, "format": "json"},
        ttl=6 * 3600,
    )
    return (data.get("response") or {}).get("items") or []


def playtime_weight(minutes: Any = 0) -> float:
    """玩得越久权重越高，但封顶 —— 免得一个 3000 小时的游戏把画像压成它一个。"""
    m = float(minutes or 0)
    return round(min(3.0, 0.5 + math.log1p(m / 60.0) / 2.0), 2)


def _existing_liked_appids() -> set[int]:
    with db.conn() as c:
        return {int(r[0]) for r in c.execute(
            "SELECT appid FROM profile_entries WHERE kind='liked' AND appid IS NOT NULL").fetchall()}


def verify_key(key: str | None = None) -> dict:
    """在线校验 steam_api_key 本身。用 ResolveVanityURL 探一下：key 无效会 403。"""
    k = (key or _key()).strip()
    if not k:
        return {"ok": False, "status": "missing", "detail": "没有 steam_api_key"}
    try:
        import requests
        resp = requests.get(
            f"{BASE}/ISteamUser/ResolveVanityURL/v1/",
            params={"key": k, "vanityurl": "gabelogannewell", "format": "json"},
            timeout=int(config.CFG["timeout_sec"]),
            headers={"User-Agent": config.CFG["user_agent"]},
        )
    except Exception as exc:
        return {"ok": False, "status": "network", "detail": f"{type(exc).__name__}: {exc}"}
    if resp.status_code in (401, 403):
        return {"ok": False, "status": str(resp.status_code), "detail": "key 无效（403/401）"}
    if resp.status_code != 200:
        return {"ok": False, "status": str(resp.status_code), "detail": resp.text[:200]}
    try:
        body = (resp.json() or {}).get("response") or {}
    except ValueError:
        return {"ok": False, "status": "bad_json", "detail": resp.text[:200]}
    if body.get("success") != 1:
        return {"ok": False, "status": "denied", "detail": str(body)[:200]}
    return {"ok": True, "status": "200", "detail": f"可用，样例解析到 steamID64={body.get('steamid')}"}


def import_to_profile(
    *,
    min_minutes: int = 60,
    limit: int = 40,
    steamid: str | None = None,
    dry_run: bool = False,
    skip_existing: bool = True,
) -> dict:
    """按玩时降序把游戏导入画像（kind=liked，weight=时长权重）。

    只读不写当 dry_run=True 时返回将要导入的清单，方便先看一眼再落库。
    """
    games = owned_games(steamid)
    games = sorted(games, key=lambda g: -(g.get("playtime_forever") or 0))
    played = [g for g in games if (g.get("playtime_forever") or 0) >= min_minutes]
    picked = played[:limit]

    existing = _existing_liked_appids() if skip_existing else set()
    plan = []
    for g in picked:
        appid = int(g["appid"])
        minutes = int(g.get("playtime_forever") or 0)
        plan.append({
            "appid": appid,
            "name": g.get("name") or str(appid),
            "hours": round(minutes / 60.0, 1),
            "minutes_2weeks": int(g.get("playtime_2weeks") or 0),
            "weight": playtime_weight(minutes),
            "skip": appid in existing,
        })

    if dry_run:
        return {"status": "dry_run", "owned": len(games), "played": len(played), "plan": plan}

    added = 0
    for item in plan:
        if item["skip"]:
            continue
        appid = item["appid"]
        db.profile_add("liked", item["name"], appid, item["weight"])
        if db.get_game(appid) is None:
            db.upsert_game({
                "appid": appid,
                "name": item["name"],
                "header_image": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
                "shop_url": f"https://store.steampowered.com/app/{appid}",
            })
        added += 1
    return {
        "status": "ok",
        "owned": len(games),
        "played": len(played),
        "added": added,
        "skipped": sum(1 for x in plan if x["skip"]),
        "top": [x["name"] for x in plan[:5]],
    }


def import_wishlist(*, limit: int = 200, steamid: str | None = None, dry_run: bool = False) -> dict:
    """愿望单 → watch（关注列表）。愿望单本来就是「等它降价的游戏」，最适合当史低提醒的入口。"""
    items = wishlist(steamid)
    if not items:
        return {"status": "empty", "reason": "愿望单为空或不可见（profile 的游戏详情要公开）"}
    appids = [int(x["appid"]) for x in items if x.get("appid")][:limit]
    known = 0
    for appid in appids:
        if dry_run:
            known += 1 if db.get_game(appid) else 0
            continue
        if db.get_game(appid) is None:
            db.upsert_game({
                "appid": appid,
                "name": str(appid),
                "header_image": f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
                "shop_url": f"https://store.steampowered.com/app/{appid}",
            })
        db.watch_add(appid, "steam-wishlist")
    return {"status": "dry_run" if dry_run else "ok", "wishlist": len(items),
            "imported": len(appids), "already_in_library": known}
