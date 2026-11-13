"""本地 Web 服务：标准库 http.server，无框架。

接口（都给前端单页用）：
  GET  /api/stats                     库统计
  GET  /api/deals?sort=&q=&discount_only=&limit=
  GET  /api/game?appid=               详情 + 价格历史 + 相似
  GET  /api/epic                      Epic 免费游戏
  GET  /api/profile                   画像输入与 top tags
  POST /api/profile                   {kind, text, weight} 加一条
  POST /api/profile/delete            {id}
  GET  /api/rec?top=&discount_only=
  GET  /api/search?q=
"""
from __future__ import annotations

import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, db, recommend
from .sources import cheapshark, epic, steam

def _index():
    """统一走 `recommend.load_index()`：按 radar.db 的 mtime 失效，一落库就重建。

    以前这里是「按 COUNT(*)+MAX(fetched_at) 失效」，`set_game_tags` 补 tag 时改不到这两个值，
    于是界面会拿着旧向量继续推荐 —— 缓存就该只留一份实现。
    """
    return recommend.load_index()


def _entry_dict(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "text": row["text"],
        "appid": row["appid"],
        "weight": row["weight"],
        "created_at": row["created_at"],
    }


def _low_state(*a, **kw):
    """史低分级统一走 recommend.low_state，别在这儿留第二份实现。"""
    return recommend.low_state(*a, **kw)


def _bayes_score(score: float | None, count: int | None, prior: float = 75.0, strength: float = 200.0) -> float | None:
    """贝叶斯平均好评率。

    直接用好评率会害人：3 条好评就是 100%，压过 20 万条 96% 的 3A。
    这里把先验（75 分，权重相当于 200 条评价）混进去，评价少的分会被拉回中间。
    """
    if score is None:
        return None
    n = float(count or 0)
    return (score * n + prior * strength) / (n + strength)


def _worth(d: dict) -> float:
    """「值得买」综合分：口碑 45% + 折扣 30% + 史低信号 25%。

    为什么不是单纯按口碑：这是史低榜，不是排行榜 —— 原价 398 的神作对「今天买什么」没有意义。
    为什么不是随机：用户打开就要一个判断，随机等于把这个判断丢掉。
    """
    q = d.get("review_bayes")
    quality = (q / 100.0) if q is not None else 0.35      # 没口碑数据的给个中间偏低值，别让它冒头
    disc = min(1.0, (d.get("discount") or 0) / 90.0)
    if d.get("below_steam_low"):
        low_sig = 1.0
    elif d.get("at_low"):
        low_sig = 0.85
    elif d.get("near_low"):
        low_sig = 0.55
    else:
        low_sig = 0.15
    return round(0.45 * quality + 0.30 * disc + 0.25 * low_sig, 4)


def _itad_low(appid: int) -> dict | None:
    """ITAD 官方口径史低。存 meta（games 表那两列是 Steam 专用口径，别混进去）。"""
    raw = db.get_meta("itad_low", {}) or {}
    node = raw.get(str(appid))
    if not node or node.get("cents") is None:
        return None
    return {"price": node["cents"] / 100, "at": node.get("at"), "shop": node.get("shop")}


def api_deals(q: dict) -> dict:
    sort = (q.get("sort") or ["discount"])[0]
    term = (q.get("q") or [""])[0].strip().lower()
    discount_only = (q.get("discount_only") or ["0"])[0] == "1"
    limit = int((q.get("limit") or ["80"])[0])
    currency = (q.get("currency") or [config.CFG["currency"]])[0]

    lows = db.lowest_map(currency)
    now = db.best_now(currency)
    prev_map = db.prev_min_map(currency)
    with db.conn() as c:
        rows = c.execute("SELECT * FROM games").fetchall()

    out = []
    for g in rows:
        best = now.get(g["appid"])
        if not best:
            continue
        if discount_only and not best["discount"]:
            continue
        name = g["name"] or ""
        if term and term not in name.lower() and term not in (g["name_cn"] or "").lower():
            continue
        low_pair = lows.get(g["appid"])
        low = low_pair[0] if low_pair else None
        snap_n = low_pair[1] if low_pair else 0
        cur = int(best["cents"])
        hb = g["heybox_cents"]
        # 小黑盒价可能比 Steam 现价更低，选真正最便宜的那档展示
        cheapest = min(x for x in (cur, hb) if x is not None)
        cheapest_shop = "小黑盒" if (hb is not None and hb <= cur) else best["shop"]
        steam_low = g["steam_lowest_cents"]
        bayes = _bayes_score(g["review_score"], g["review_count"])
        # 折扣也按「真正要付的钱」重算：小黑盒价往往比 Steam 折扣更狠（FF7RE 是 -82% 而不是 -0%）
        list_c = best["list_cents"]
        eff_disc = (int(round((1 - cheapest / list_c) * 100))
                    if list_c and list_c > cheapest else int(best["discount"] or 0))
        state = _low_state(cheapest, prev_map.get((g["appid"], cheapest_shop)), steam_low, snap_n, low)
        item = {
            "appid": g["appid"],
            "name": name,
            "name_cn": g["name_cn"],
            "header_image": g["header_image"],
            "metacritic": g["metacritic"],
            "price": cheapest / 100,
            "list_price": (best["list_cents"] / 100) if best["list_cents"] else None,
            "discount": eff_disc,
            "shop": cheapest_shop,
            "url": best["url"],
            "lowest": low / 100 if low is not None else None,
            "snapshots": snap_n,
            "low_state": state,
            "at_low": state in ("super", "new", "equal"),
            "near_low": state == "near",
            "heybox": hb / 100 if hb is not None else None,
            "steam_price": cur / 100,
            "steam_lowest": (steam_low / 100) if steam_low else None,
            # 现价（含小黑盒）低于 Steam 官方史低 —— 这才叫真的「破史低」
            "below_steam_low": bool(steam_low and cheapest < steam_low),
            "currency": currency,
            "review_score": g["review_score"],
            "review_count": g["review_count"],
            "review_bayes": round(bayes, 1) if bayes is not None else None,
            "heybox_deadline": g["heybox_deadline"],
            "checked_at": g["price_checked_at"],
        }
        item["worth"] = _worth(item)
        out.append(item)

    if sort == "price":
        out.sort(key=lambda d: (d["price"] is None, d["price"]))
    elif sort == "metacritic":
        out.sort(key=lambda d: -(d["metacritic"] or 0))
    elif sort == "review":
        out.sort(key=lambda d: -(d["review_bayes"] or 0))
    elif sort == "at_low":
        out.sort(key=lambda d: (not d["at_low"], -(d["discount"] or 0)))
    elif sort == "heybox":
        out.sort(key=lambda d: (d["heybox"] is None, d["heybox"] or 1e9))
    elif sort == "discount":
        out.sort(key=lambda d: -(d["discount"] or 0))
    else:  # worth：默认
        out.sort(key=lambda d: -d["worth"])
    return {"count": len(out), "items": out[:limit]}


def api_game(q: dict) -> dict:
    appid = int((q.get("appid") or ["0"])[0])
    currency = (q.get("currency") or [config.CFG["currency"]])[0]
    g = db.get_game(appid)
    if not g:
        return {"error": "not_found", "appid": appid}
    hist = db.price_history(appid, currency)
    cur = db.current_prices(appid, currency)
    low = db.lowest(appid, currency)
    idx = _index()
    sim = recommend.similar(appid, idx, top=8) if appid in idx.vecs else []
    return {
        "game": {
            "appid": appid,
            "name": g["name"],
            "name_cn": g["name_cn"],
            "type": g["type"],
            "release_date": g["release_date"],
            "metacritic": g["metacritic"],
            "developers": db.json_col(g, "developers", []),
            "publishers": db.json_col(g, "publishers", []),
            "genres": db.json_col(g, "genres", []),
            "tags": (db.json_col(g, "tags", []) or [])[:15],
            "short_desc": g["short_desc"],
            "header_image": g["header_image"],
            "shop_url": g["shop_url"],
            "heybox_url": f"https://www.xiaoheihe.cn/app/topic/game/pc/{appid}",
        },
        "current": [
            {"shop": r["shop"], "price": r["cents"] / 100, "list_price": (r["list_cents"] or 0) / 100,
             "discount": r["discount"], "currency": r["currency"], "url": r["url"], "at": r["captured_at"]}
            for r in cur
        ],
        "history": [
            {"at": r["captured_at"], "shop": r["shop"], "price": r["cents"] / 100} for r in hist
        ],
        "lowest": (low["cents"] / 100) if low else None,
        "steam_lowest": (g["steam_lowest_cents"] / 100) if g["steam_lowest_cents"] else None,
        "itad_lowest": _itad_low(appid),
        "heybox": (g["heybox_cents"] / 100) if g["heybox_cents"] else None,
        "snapshots": low["n"] if low else 0,
        "similar": sim,
    }


def api_epic(_q: dict) -> dict:
    try:
        fg = epic.free_games()
    except Exception as exc:
        return {"error": "fetch_failed", "detail": str(exc), "items": []}
    return {"count": len(fg), "items": fg}


def api_profile(_q: dict) -> dict:
    idx = _index()
    rows = db.profile_list()
    entries = []
    for r in rows:
        if r["kind"] == "note":
            continue
        e = recommend.resolve_entry(r["text"], idx, r["kind"], r["weight"])
        entries.append(e)
    vec, top = recommend.build_profile(entries) if entries else ({}, [])
    return {
        "entries": [_entry_dict(r) for r in rows],
        "resolved": [
            {"text": e.text, "kind": e.kind, "appid": e.appid, "name": e.resolved_name,
             "top_tags": sorted(e.vec.items(), key=lambda kv: -kv[1])[:6], "unmatched": e.unmatched[:8]}
            for e in entries
        ],
        "top_tags": [[t, round(w, 4)] for t, w in top],
        "dim": len(vec),
        "index": {"games": idx.size, "tags": len(idx.idf)},
    }


def api_notifications(q: dict) -> dict:
    """提醒中心。Toast 会被系统吞，但这里一定有 —— 界面上的红点以它为准。"""
    limit = int((q.get("limit") or ["20"])[0])
    items = [dict(r) for r in db.notify_list(limit=limit)]
    for it in items:
        it["price"] = (it["price_cents"] / 100) if it.get("price_cents") else None
    return {"unread": db.notify_unread(), "items": items}


def api_rec(q: dict) -> dict:
    idx = _index()
    rows = db.profile_list()
    entries = [recommend.resolve_entry(r["text"], idx, r["kind"], r["weight"]) for r in rows if r["kind"] != "note"]
    if not entries:
        return {"error": "empty_profile", "items": []}
    vec, _ = recommend.build_profile(entries)
    exclude = {e.appid for e in entries if e.appid}
    top = int((q.get("top") or ["30"])[0])
    only = (q.get("discount_only") or ["0"])[0] == "1"
    items = recommend.recommend(vec, idx, exclude=exclude, top=top, require_discount=only,
                               diversity=float((q.get("div") or ["0"])[0] or 0))
    return {"count": len(items), "items": items}


def api_search(q: dict) -> dict:
    term = (q.get("q") or [""])[0].strip()
    if not term:
        return {"local": [], "online": []}
    local = [dict(r) for r in db.find_games(term, limit=10)]
    for r in local:
        r["tags"] = (db.json_col(r, "tags", []) or [])[:8]
    online = []
    try:
        online = steam.storesearch(term)[:10]
    except Exception:
        pass
    return {"local": local, "online": online}


def api_add_profile(body: dict) -> dict:
    idx = _index()
    text = (body.get("text") or "").strip()
    if not text:
        return {"error": "empty_text"}
    kind = body.get("kind") or "liked"
    weight = float(body.get("weight") or 1.0)
    e = recommend.resolve_entry(text, idx, kind, weight)
    entry_id = db.profile_add(kind, text, e.appid, weight)
    return {"ok": True, "id": entry_id, "resolved": e.resolved_name, "appid": e.appid,
            "top_tags": sorted(e.vec.items(), key=lambda kv: -kv[1])[:8], "unmatched": e.unmatched[:10]}


class Handler(BaseHTTPRequestHandler):
    server_version = "GameDealRadar/0.1"

    def log_message(self, fmt, *args):  # 静音默认日志，保留错误
        pass

    # --- 工具 ---
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _static(self, rel: str) -> None:
        path = (config.WEB_DIR / rel).resolve()
        if not str(path).startswith(str(config.WEB_DIR.resolve())) or not path.is_file():
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, path.read_bytes(), ctype)

    # --- 路由 ---
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        q = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                routes = {
                    "/api/stats": lambda: {**db.stats(), "db": str(config.DB_PATH)},
                    "/api/deals": lambda: api_deals(q),
                    "/api/game": lambda: api_game(q),
                    "/api/epic": lambda: api_epic(q),
                    "/api/profile": lambda: api_profile(q),
                    "/api/rec": lambda: api_rec(q),
                    "/api/search": lambda: api_search(q),
                    "/api/notifications": lambda: api_notifications(q),
                }
                fn = routes.get(path)
                if fn is None:
                    self._json({"error": "no_route", "path": path}, 404)
                    return
                self._json(fn())
                return
            if path in ("/", "/index.html"):
                self._static("index.html")
                return
            self._static(path.lstrip("/"))
        except Exception as exc:
            self._json({"error": "exception", "detail": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._json({"error": "bad_json"}, 400)
            return
        try:
            if parsed.path == "/api/profile":
                self._json(api_add_profile(body))
            elif parsed.path == "/api/profile/delete":
                db.profile_delete(int(body.get("id")))
                self._json({"ok": True})
            elif parsed.path == "/api/watch":
                db.watch_add(int(body["appid"]), body.get("note") or "")
                self._json({"ok": True})
            elif parsed.path == "/api/notifications/seen":
                db.notify_mark_seen()
                self._json({"ok": True, "unread": db.notify_unread()})
            else:
                self._json({"error": "no_route", "path": parsed.path}, 404)
        except Exception as exc:
            self._json({"error": "exception", "detail": f"{type(exc).__name__}: {exc}"}, 500)


def serve(host: str = "127.0.0.1", port: int = 8790) -> None:
    db.init()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"史低雷达 → http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭")
    finally:
        srv.server_close()
