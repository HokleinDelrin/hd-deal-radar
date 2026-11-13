"""画像与推荐。

思路（见 QA.md 2026-11-09 第三条）：
- 每个游戏是一个 tag 上的稀疏 TF-IDF 向量（tag 票数取 log 作 tf，全库文档频率作 idf）；
- 用户画像 = 若干「喜欢」游戏向量 + 自由描述抽出的 tag 向量 − 若干「不喜欢」向量，加权求和后重新归一；
- 推荐 = 画像向量与候选向量的余弦，再乘上折扣/口碑的调节项。

这样「只给名字」和「说清喜欢哪儿」落在同一套空间里，可以混着加。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from . import db, tags as tagmod

# 折扣对推荐排序的加成系数：0 表示不偏好打折，0.3 表示 90% off 额外 ×1.27
DISCOUNT_BOOST = 0.30


@dataclass
class Index:
    games: dict[int, dict] = field(default_factory=dict)   # appid -> game dict
    vecs: dict[int, dict[str, float]] = field(default_factory=dict)
    idf: dict[str, float] = field(default_factory=dict)
    df: dict[str, int] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.vecs)


def _norm(v: dict[str, float]) -> dict[str, float]:
    n = math.sqrt(sum(x * x for x in v.values()))
    if n <= 0:
        return {}
    # 保留符号：负权重表示「不想要这个 tag」，用它把候选往下压
    return {k: x / n for k, x in v.items() if abs(x / n) > 1e-4}


def build_index(rows: Iterable | None = None) -> Index:
    if rows is None:
        rows = db.all_games_with_tags()
    games: dict[int, dict] = {}
    raw: dict[int, dict[str, float]] = {}
    df: dict[str, int] = {}

    for r in rows:
        tags = db.json_col(r, "tags", []) or []
        weights: dict[str, float] = {}
        for item in tags:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                name, votes = item[0], item[1]
            elif isinstance(item, dict):
                name, votes = item.get("name"), item.get("votes") or 1
            else:
                name, votes = item, 1
            if not name:
                continue
            weights[tagmod.canon(str(name))] = math.log1p(max(float(votes or 1), 1.0))
        if not weights:
            continue
        games[int(r["appid"])] = dict(r)
        raw[int(r["appid"])] = weights
        for t in weights:
            df[t] = df.get(t, 0) + 1

    n = max(len(raw), 1)
    idf = {t: math.log((1 + n) / (1 + c)) + 1.0 for t, c in df.items()}
    vecs = {appid: _norm({t: w * idf.get(t, 1.0) for t, w in ws.items()}) for appid, ws in raw.items()}
    return Index(games=games, vecs=vecs, idf=idf, df=df)


_CACHE: dict = {"mtime": -1.0, "index": None}


def load_index(force: bool = False) -> Index:
    """带缓存的索引。失效条件是 `radar.db` 的 mtime 变了。

    没有缓存时，每次 `rec` / 每个 Web 请求都要把全库 tag 重算一遍 TF-IDF；
    246 个游戏还行，扩到几千个就是秒级开销，不值得反复付。
    """
    mtime = db.db_mtime()
    if not force and _CACHE["index"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["index"]
    idx = build_index()
    _CACHE["mtime"] = mtime
    _CACHE["index"] = idx
    return idx


def text_vector(text: str, index: Index) -> tuple[dict[str, float], list[str]]:
    """一段话 → 向量。倾向的 tag 记正、被否定的 tag 记负。"""
    ex = tagmod.extract(text)
    vec: dict[str, float] = {}
    for t, w in ex["positive"].items():
        vec[t] = w * index.idf.get(t, 2.0)
    for t, w in ex["negative"].items():
        vec[t] = -w * index.idf.get(t, 2.0)
    return _norm(vec), ex["unmatched"]


@dataclass
class Entry:
    kind: str          # liked / disliked / note
    text: str
    appid: int | None = None
    weight: float = 1.0
    vec: dict[str, float] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)
    resolved_name: str | None = None


def resolve_entry(text: str, index: Index, kind: str = "liked", weight: float = 1.0) -> Entry:
    """一条输入 → 向量。能解析成游戏就用游戏向量，否则就走文本抽取。"""
    e = Entry(kind=kind, text=text, weight=weight)
    hit = _resolve_game(text, index)
    if hit is not None:
        appid, name = hit
        e.appid = appid
        e.resolved_name = name
        e.vec = index.vecs.get(appid, {})
        # 名字之外如果还写了描述，额外叠加
        extra = _strip_name(text, name)
        if extra:
            ev, un = text_vector(extra, index)
            e.unmatched = un
            e.vec = _norm({**{k: v for k, v in e.vec.items()}, **{k: v * 0.8 for k, v in ev.items()}})
    else:
        e.vec, e.unmatched = text_vector(text, index)
    return e


def _resolve_game(text: str, index: Index) -> tuple[int, str] | None:
    t = text.strip().lower()
    if not t:
        return None
    # 1) 完全等于名字
    for appid, g in index.games.items():
        names = [(g.get("name") or "").lower(), (g.get("name_cn") or "").lower()]
        if t in [n for n in names if n]:
            return appid, g.get("name") or g.get("name_cn")
    # 2) 包含名字（取最长匹配，避免 "Dark" 命中 "Dark Souls" 之类）
    best: tuple[int, str, int] | None = None
    for appid, g in index.games.items():
        for nm in (g.get("name"), g.get("name_cn")):
            if not nm:
                continue
            nl = nm.lower()
            if len(nl) >= 3 and nl in t:
                if best is None or len(nm) > best[2]:
                    best = (appid, nm, len(nm))
    if best:
        return best[0], best[1]
    return None


def _strip_name(text: str, name: str | None) -> str:
    if not name:
        return text
    return text.replace(name, " ").strip(" ,，。;；:：-—")


def build_profile(entries: Iterable[Entry]) -> tuple[dict[str, float], list[tuple[str, float]]]:
    acc: dict[str, float] = {}
    for e in entries:
        sign = -1.0 if e.kind == "disliked" else 1.0
        w = sign * float(e.weight or 1.0)
        for t, v in e.vec.items():
            acc[t] = acc.get(t, 0.0) + w * v
    # 保留负权重：「不想再要恐怖」这类会变成 −Horror，给主打恐怖的游戏降分
    acc = {t: v for t, v in acc.items() if abs(v) > 0.02}
    top = sorted(acc.items(), key=lambda kv: -kv[1])[:15]
    return _norm(acc), top


def low_state(cheapest: int | None, prev_min: int | None, steam_low: int | None,
              snap_n: int, lowest: int | None) -> str:
    """史低分级。比单一的「史低」有用得多 —— 用户要能一眼分出「破纪录」和「也就是老价」。

    super 超史低：实付比 Steam 官方史低还低（跨渠道破纪录，最值）
    new   星史低：刷了自己记录的最低（实付 < 之前的历史最低）
    equal 平史低：实付 == 之前的历史最低
    near  接近史低：<= 历史最低 +10%
    first 首采：快照不足两条，还说不上史低
    none  没有信号
    """
    if cheapest is None:
        return "none"
    if snap_n < 2:
        return "first"
    if steam_low and cheapest < steam_low:
        return "super"
    base = prev_min if prev_min is not None else lowest
    if base is None:
        return "none"
    if cheapest < base:
        return "new"
    if cheapest <= base:
        return "equal"
    if cheapest <= base * 1.10:
        return "near"
    return "none"


def itad_low_map() -> dict[int, dict]:
    """ITAD 官方口径史低（meta 里存的 {appid: {cents, at, shop}}）。读一次传下去，别在循环里反复查。"""
    raw = db.get_meta("itad_low", {}) or {}
    out: dict[int, dict] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
    return out


def _field(g, name: str, default=None):
    """同时吃 dict 与 sqlite3.Row（Row 没有 .get，dict 缺 key 会 KeyError）。"""
    try:
        v = g[name]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


def deal_view(appid: int, g: dict, *, best=None, lows=None, prev_map=None,
              itad_lows: dict[int, dict] | None = None) -> dict:
    """单个游戏的价格视图：现价、折扣、史低分级。

    抽出来是为了让 recommend 和 notify 用**同一套口径** —— 如果推送说「史低」而界面说「接近史低」，
    用户就再也不信了。`best/lows/prev_map` 由调用方一次查好传进来（逐条查会退化成 O(n) 次查询）。
    """
    low_pair = (lows or {}).get(appid)
    low = low_pair[0] if low_pair else None
    snap_n = low_pair[1] if low_pair else 0
    cur = int(best["cents"]) if best else None
    hb = _field(g, "heybox_cents")
    steam_low = _field(g, "steam_lowest_cents")
    cheapest = min(x for x in (cur, hb) if x is not None) if (cur is not None or hb is not None) else None

    # 折扣按「真正要付的钱」算：小黑盒 82% 的力度才会进排序，而不是只看 Steam 那档
    list_c = best["list_cents"] if best and best["list_cents"] else None
    if list_c and cheapest and list_c > cheapest:
        disc = int(round((1 - cheapest / list_c) * 100))
    else:
        disc = int(best["discount"]) if best else 0

    cheapest_shop = "小黑盒" if (hb is not None and (cur is None or hb <= cur)) else (best["shop"] if best else None)
    state = low_state(cheapest, (prev_map or {}).get((appid, cheapest_shop)), steam_low, snap_n, low)

    # ITAD 官方史低只当「裁判」用：现价低于它，说明这不是某家店的日常折扣，而是全网真破纪录
    ext = (itad_lows or {}).get(appid) or {}
    ext_cents = ext.get("cents")
    below_itad = bool(ext_cents and cheapest is not None and cheapest < ext_cents)
    if below_itad and state not in ("super", "new"):
        state = "new"   # 本地还没有足够快照时，至少能凭外部口径确认「现在是新低」

    return {
        "price": cheapest / 100 if cheapest is not None else None,
        "price_cents": cheapest,
        "list_price": (best["list_cents"] / 100) if best and best["list_cents"] else None,
        "discount": disc,
        "shop": cheapest_shop,
        "heybox": hb / 100 if hb is not None else None,
        "steam_price": cur / 100 if cur is not None else None,
        "steam_lowest": steam_low / 100 if steam_low else None,
        "itad_lowest": ext_cents / 100 if ext_cents else None,
        "below_steam_low": bool(steam_low and cheapest is not None and cheapest < steam_low),
        "below_itad_low": below_itad,
        "lowest": low / 100 if low is not None else None,
        "snapshots": snap_n,
        "state": state,
        "at_low": state in ("super", "new", "equal"),
        "near_low": state == "near",
    }


def _mmr_rerank(items: list[dict], index: Index, lam: float, limit: int) -> list[dict]:
    """最大边际相关（MMR）：既要有分，又别全是同一批 tag。

    不加这一步，`Souls-like + Difficult` 那一串会把整页占满 —— 每个都对，但看起来像只有一个游戏。
    lam=1 等于不重排；0.7 表示「相关性占七成，和已选的相似度扣三成」。
    """
    if not items or lam >= 1.0:
        return items[:limit]
    pool = list(items)
    top_adj = max((x["adj"] for x in pool), default=1.0) or 1.0
    selected: list[dict] = []
    while pool and len(selected) < limit:
        best_i, best_val = 0, None
        for i, it in enumerate(pool):
            rel = it["adj"] / top_adj
            if selected:
                vec = index.vecs.get(it["appid"], {})
                sim = max(_cosine(vec, index.vecs.get(s["appid"], {})) for s in selected)
            else:
                sim = 0.0
            val = lam * rel - (1.0 - lam) * sim
            if best_val is None or val > best_val:
                best_i, best_val = i, val
        selected.append(pool.pop(best_i))
    return selected


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def recommend(
    profile: dict[str, float],
    index: Index,
    *,
    exclude: set[int] | None = None,
    top: int = 30,
    discount_boost: float = DISCOUNT_BOOST,
    require_discount: bool = False,
    min_match_tags: int = 2,
    diversity: float = 0.0,
) -> list[dict]:
    """按画像余弦排序，叠加折扣与口碑；每个结果带命中理由。

    `diversity` > 0 时，排序后做 MMR 重排：0.3 表示「相似度扣 30%」，
    用来把「一串 Souls-like」打散。默认 0 保持旧行为。
    """
    if not profile:
        return []
    exclude = exclude or set()
    lows = db.lowest_map()
    now = db.best_now()
    prev_map = db.prev_min_map()
    itad_lows = itad_low_map()

    out: list[dict] = []
    for appid, vec in index.vecs.items():
        if appid in exclude:
            continue
        score = 0.0
        matched: list[tuple[str, float]] = []
        for t, w in vec.items():
            pw = profile.get(t)
            if pw:
                contrib = pw * w
                score += contrib
                matched.append((t, contrib))
        if score <= 0 or len(matched) < min_match_tags:
            continue
        matched.sort(key=lambda kv: -kv[1])

        g = index.games[appid]
        view = deal_view(appid, g, best=now.get(appid), lows=lows,
                         prev_map=prev_map, itad_lows=itad_lows)
        if require_discount and view["discount"] <= 0:
            continue
        adj = score * (1.0 + discount_boost * view["discount"] / 100.0)

        row: dict = {
            "appid": appid,
            "name": g.get("name"),
            "name_cn": g.get("name_cn"),
            "header_image": g.get("header_image"),
            "metacritic": g.get("metacritic"),
            "release_date": g.get("release_date"),
            "score": round(score, 4),
            "adj": round(adj, 4),
            "matched_tags": [[t, round(c, 3)] for t, c in matched[:8]],
            "review_score": g.get("review_score"),
            "review_count": g.get("review_count"),
            "heybox_deadline": g.get("heybox_deadline"),
        }
        row.update(view)
        out.append(row)

    out.sort(key=lambda d: -d["adj"])
    if diversity and diversity > 0:
        return _mmr_rerank(out, index, 1.0 - min(float(diversity), 0.95), top)
    return out[:top]


def similar(appid: int, index: Index, top: int = 10) -> list[dict]:
    base = index.vecs.get(appid)
    if not base:
        return []
    out = []
    for other, vec in index.vecs.items():
        if other == appid:
            continue
        s = sum(w * vec.get(t, 0.0) for t, w in base.items())
        if s > 0:
            out.append({"appid": other, "name": index.games[other].get("name"), "score": round(s, 4)})
    out.sort(key=lambda d: -d["score"])
    return out[:top]


def explain(profile: dict[str, float], top: int = 12) -> list[dict]:
    """画像解释：哪些 tag 撑起了这个画像，以及它们在库里有多常见。"""
    from . import db as _db
    total = max(len(_db.lowest_map()), 1)
    rows = []
    for t, w in sorted(profile.items(), key=lambda kv: -kv[1])[:top]:
        rows.append({"tag": t, "weight": round(w, 4)})
    return rows
