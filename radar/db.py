"""SQLite 存取层。

设计要点（见 QA.md）：
- 价格一律存整数「分」，避免浮点比较误差；
- 每次采集都追加 prices 快照，史低由 MIN(cents) 现算，不依赖外部站点口径；
- games.tags 存 [[tag, weight], ...] 的 JSON，供画像向量使用。
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    appid        INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    name_cn      TEXT,
    type         TEXT DEFAULT 'game',
    release_date TEXT,
    metacritic   INTEGER,
    developers   TEXT,           -- json [str]
    publishers   TEXT,           -- json [str]
    genres       TEXT,           -- json [str]
    tags         TEXT,           -- json [[name, weight], ...]
    short_desc   TEXT,
    header_image TEXT,
    shop_url     TEXT,
    fetched_at   TEXT,
    steam_lowest_cents INTEGER,  -- 外部口径的 Steam 史低（小黑盒/ITAD 提供），只作对照
    heybox_cents       INTEGER,  -- 小黑盒到手价（含券），比价时单独一档
    heybox_deadline    INTEGER,  -- 小黑盒这一档优惠的截止时间（unix 秒）
    review_score       REAL,     -- Steam 好评率 0-100（SteamSpy 的 positive/(positive+negative)）
    review_count       INTEGER,  -- 评价总数，用来判断这个分数可不可信
    price_checked_at   TEXT      -- 这个游戏最后一次确认价格的时间（价格没变也会更新）
);

CREATE TABLE IF NOT EXISTS prices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    appid       INTEGER NOT NULL,
    source      TEXT NOT NULL,   -- steam / cheapshark / itad / epic
    shop        TEXT NOT NULL,   -- Steam / GreenManGaming / Epic ...
    currency    TEXT NOT NULL,
    cents       INTEGER NOT NULL,
    list_cents  INTEGER,
    discount    INTEGER DEFAULT 0,
    url         TEXT,
    captured_at TEXT NOT NULL,
    UNIQUE(appid, shop, currency, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_prices_appid ON prices(appid, currency);
-- 「每家店取最新一条」是最高频的查询，给它的排序键建复合索引
CREATE INDEX IF NOT EXISTS idx_prices_latest ON prices(appid, currency, shop, captured_at DESC);

CREATE TABLE IF NOT EXISTS watch (
    appid    INTEGER PRIMARY KEY,
    note     TEXT,
    added_at TEXT NOT NULL
);

-- 用户画像输入：一句话 / 一个名字 / 一段描述
CREATE TABLE IF NOT EXISTS profile_entries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,      -- liked / disliked / note
    appid      INTEGER,            -- 能解析到游戏就有
    text       TEXT NOT NULL,      -- 原始输入
    weight     REAL DEFAULT 1.0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- 提醒记录：Toast 可能被系统吞掉，所以「有没有提醒过」以这张表为准
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    appid       INTEGER,
    name        TEXT,
    price_cents INTEGER,
    discount    INTEGER DEFAULT 0,
    state       TEXT,
    shop        TEXT,
    created_at  TEXT NOT NULL,
    seen        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notif_time ON notifications(created_at DESC);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def db_mtime() -> float:
    """数据库文件的修改时间。用来让内存里的索引「一落库就失效」，不用重启进程。"""
    try:
        return config.DB_PATH.stat().st_mtime
    except OSError:
        return 0.0


@contextmanager
def conn():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)


def _migrate(c: sqlite3.Connection) -> None:
    """给老库补列。SQLite 没有 ADD COLUMN IF NOT EXISTS，只能自己查。"""
    have = {r["name"] for r in c.execute("PRAGMA table_info(games)").fetchall()}
    for col, decl in (
        ("steam_lowest_cents", "INTEGER"),
        ("heybox_cents", "INTEGER"),
        ("heybox_deadline", "INTEGER"),
        ("review_score", "REAL"),
        ("review_count", "INTEGER"),
        ("price_checked_at", "TEXT"),
    ):
        if col not in have:
            c.execute(f"ALTER TABLE games ADD COLUMN {col} {decl}")


def set_game_external(appid: int, *, steam_lowest: float | None = None,
                      heybox_final: float | None = None,
                      heybox_deadline: int | None = None) -> None:
    """写外部口径的价格（史低 / 小黑盒到手价 / 券截止）。传 None 表示不动这一列。"""
    sets, vals = [], []
    if steam_lowest is not None:
        sets.append("steam_lowest_cents=?")
        vals.append(int(round(steam_lowest * 100)))
    if heybox_final is not None:
        sets.append("heybox_cents=?")
        vals.append(int(round(heybox_final * 100)))
    if heybox_deadline is not None:
        sets.append("heybox_deadline=?")
        vals.append(int(heybox_deadline))
    if not sets:
        return
    vals.append(appid)
    with conn() as c:
        c.execute(f"UPDATE games SET {', '.join(sets)} WHERE appid=?", vals)


def set_game_reviews(appid: int, score: float | None, count: int | None) -> None:
    if score is None and count is None:
        return
    with conn() as c:
        c.execute("UPDATE games SET review_score=?, review_count=? WHERE appid=?",
                  (score, count, appid))


def set_game_tags(appid: int, tags: list) -> None:
    with conn() as c:
        c.execute("UPDATE games SET tags=? WHERE appid=?",
                  (json.dumps(tags, ensure_ascii=False), appid))


def _j(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


_SOFT_COLS = ("name_cn", "release_date", "metacritic", "developers", "publishers",
              "genres", "tags", "short_desc", "header_image", "shop_url")


def upsert_game(g: dict) -> None:
    """插入或更新元数据。

    **软字段用 COALESCE**：调用方没给（None）就保留库里已有的值。
    这个不是洁癖 —— steam/cheapshark/heybox 三条采集链都不带 `tags`（tag 只来自 SteamSpy `enrich`），
    早先 `tags=excluded.tags` 的写法让每天的 `fetch` 把补好的 tag 全抹成 NULL：
    2026-11-12 实测 `review_score` 还有 217 行、`tags` 只剩 1 行，推荐因此退化成 1 条结果。
    """
    row = {
        "appid": g["appid"],
        "name": g.get("name") or "",
        "name_cn": g.get("name_cn"),
        "type": g.get("type") or "game",
        "release_date": g.get("release_date"),
        "metacritic": g.get("metacritic"),
        "developers": _j(g.get("developers")),
        "publishers": _j(g.get("publishers")),
        "genres": _j(g.get("genres")),
        "tags": _j(g.get("tags")),
        "short_desc": g.get("short_desc"),
        "header_image": g.get("header_image"),
        "shop_url": g.get("shop_url"),
        "fetched_at": g.get("fetched_at") or now(),
    }
    cols = ", ".join(row)
    placeholders = ", ".join(f":{k}" for k in row)
    updates = ", ".join(
        f"{k}=COALESCE(excluded.{k}, games.{k})" if k in _SOFT_COLS else f"{k}=excluded.{k}"
        for k in row if k != "appid"
    )
    with conn() as c:
        c.execute(
            f"INSERT INTO games ({cols}) VALUES ({placeholders}) "
            f"ON CONFLICT(appid) DO UPDATE SET {updates}",
            row,
        )


def add_price(p: dict, captured_at: str | None = None) -> bool:
    """写一条价格快照。**价格和上次一样就不写** —— 见 QA「为什么价格没变不记快照」。

    返回 True 表示真的落了一行新快照，False 表示与上一条相同、跳过。
    同时刷新 games.price_checked_at，让界面能显示「数据截至」。
    """
    appid = int(p["appid"])
    shop = p["shop"]
    currency = p.get("currency") or config.CFG["currency"]
    cents = int(round(float(p["price"]) * 100))
    list_cents = int(round(float(p["list_price"]) * 100)) if p.get("list_price") else None
    discount = int(p.get("discount") or 0)
    ts = captured_at or p.get("captured_at") or now()

    with conn() as c:
        last = c.execute(
            """SELECT cents, list_cents, discount FROM prices
               WHERE appid=? AND shop=? AND currency=?
               ORDER BY captured_at DESC LIMIT 1""",
            (appid, shop, currency),
        ).fetchone()
        wrote = False
        if not (last and last["cents"] == cents
                and (last["list_cents"] or 0) == (list_cents or 0)
                and (last["discount"] or 0) == discount):
            c.execute(
                """INSERT INTO prices
                   (appid, source, shop, currency, cents, list_cents, discount, url, captured_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(appid, shop, currency, captured_at) DO UPDATE SET
                     cents=excluded.cents, list_cents=excluded.list_cents,
                     discount=excluded.discount, url=excluded.url""",
                (appid, p["source"], shop, currency, cents, list_cents, discount, p.get("url"), ts),
            )
            wrote = True
        c.execute("UPDATE games SET price_checked_at=? WHERE appid=?", (ts, appid))
    return wrote


def get_game(appid: int) -> sqlite3.Row | None:
    with conn() as c:
        return c.execute("SELECT * FROM games WHERE appid=?", (appid,)).fetchone()


def find_games(term: str, limit: int = 8) -> list[sqlite3.Row]:
    """本地模糊查：英文名、中文名都查。"""
    like = f"%{term}%"
    with conn() as c:
        return c.execute(
            "SELECT * FROM games WHERE name LIKE ? OR name_cn LIKE ? ORDER BY metacritic DESC NULLS LAST LIMIT ?",
            (like, like, limit),
        ).fetchall()


def current_prices(appid: int, currency: str | None = None) -> list[sqlite3.Row]:
    """每个商店取最新一条快照。"""
    cur = currency or config.CFG["currency"]
    with conn() as c:
        return c.execute(
            """SELECT p.* FROM prices p
               JOIN (SELECT shop, MAX(captured_at) m FROM prices
                     WHERE appid=? AND currency=? GROUP BY shop) t
                 ON p.shop=t.shop AND p.captured_at=t.m
               WHERE p.appid=? AND p.currency=?
               ORDER BY p.cents ASC""",
            (appid, cur, appid, cur),
        ).fetchall()


def lowest(appid: int, currency: str | None = None) -> sqlite3.Row | None:
    cur = currency or config.CFG["currency"]
    with conn() as c:
        return c.execute(
            """SELECT MIN(cents) cents, COUNT(*) n, MIN(captured_at) first_at
               FROM prices WHERE appid=? AND currency=?""",
            (appid, cur),
        ).fetchone()


def lowest_map(currency: str | None = None) -> dict[int, tuple[int, int]]:
    """appid -> (最低分, 快照条数)。

    快照条数很重要：只有一条快照时「当前价 == 史低」是废话，界面上不能标史低，
    所以调用方必须用条数判断，别只看 min。
    """
    cur = currency or config.CFG["currency"]
    with conn() as c:
        rows = c.execute(
            "SELECT appid, MIN(cents) m, COUNT(*) n FROM prices WHERE currency=? GROUP BY appid", (cur,)
        ).fetchall()
    return {r["appid"]: (r["m"], r["n"]) for r in rows}


def low_before(appid: int, shop: str, currency: str | None = None) -> int | None:
    """这个商店**除最新一条之外**的历史最低价。

    用来区分「刷新史低」和「打平史低」：含最新一条的 MIN 永远等于当前价（当前就是最低），
    所以必须把最新那行排掉才能判断这次是破纪录还是持平。
    """
    cur = currency or config.CFG["currency"]
    with conn() as c:
        row = c.execute(
            """SELECT MIN(cents) m FROM prices
               WHERE appid=? AND shop=? AND currency=? AND rowid <> (
                   SELECT rowid FROM prices WHERE appid=? AND shop=? AND currency=?
                   ORDER BY captured_at DESC LIMIT 1)""",
            (appid, shop, cur, appid, shop, cur),
        ).fetchone()
    return row["m"] if row else None


def prev_min_map(currency: str | None = None) -> dict[tuple[int, str], int | None]:
    """(appid, shop) -> 该店除最新一条外的最低价。一次性算出全库，别逐条查。

    为什么需要它：含最新一条的 MIN 恒等于当前价（当前就是最低），
    没法区分「这次破了纪录」和「一直就是这个价」。排掉最新那条才看得出。
    """
    cur = currency or config.CFG["currency"]
    with conn() as c:
        rows = c.execute(
            "SELECT appid, shop, cents FROM prices WHERE currency=? ORDER BY appid, shop, captured_at",
            (cur,),
        ).fetchall()
    groups: dict[tuple[int, str], list[int]] = {}
    for r in rows:
        groups.setdefault((r["appid"], r["shop"]), []).append(int(r["cents"]))
    return {k: (min(v[:-1]) if len(v) > 1 else None) for k, v in groups.items()}


def best_now(currency: str | None = None) -> dict[int, sqlite3.Row]:
    """每个游戏当前最便宜的那家店。"""
    cur = currency or config.CFG["currency"]
    with conn() as c:
        rows = c.execute(
            """SELECT p.* FROM prices p
               JOIN (SELECT appid, MIN(cents) mc FROM prices p2
                     WHERE currency=? AND captured_at = (
                        SELECT MAX(captured_at) FROM prices p3
                        WHERE p3.appid=p2.appid AND p3.shop=p2.shop AND p3.currency=p2.currency)
                     GROUP BY appid) t
                 ON p.appid=t.appid AND p.cents=t.mc
               WHERE p.currency=? GROUP BY p.appid""",
            (cur, cur),
        ).fetchall()
    return {r["appid"]: r for r in rows}


def price_history(appid: int, currency: str | None = None) -> list[sqlite3.Row]:
    cur = currency or config.CFG["currency"]
    with conn() as c:
        return c.execute(
            """SELECT captured_at, shop, MIN(cents) cents FROM prices
               WHERE appid=? AND currency=? GROUP BY captured_at, shop
               ORDER BY captured_at ASC""",
            (appid, cur),
        ).fetchall()


def all_games_with_tags() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM games WHERE tags IS NOT NULL").fetchall()


def notify_add(appid: int, name: str, price_cents: int, *, discount: int = 0,
               state: str = "", shop: str = "") -> int:
    """记一条提醒。**这才是「提醒过」的权威记录** —— Toast 弹没弹都不影响它。"""
    with conn() as c:
        cur = c.execute(
            "INSERT INTO notifications (appid, name, price_cents, discount, state, shop, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (appid, name, int(price_cents), int(discount or 0), state, shop, now()),
        )
        return int(cur.lastrowid)


def notify_list(limit: int = 20, unseen_only: bool = False) -> list[sqlite3.Row]:
    with conn() as c:
        where = "WHERE seen=0" if unseen_only else ""
        return c.execute(
            f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def notify_unread() -> int:
    with conn() as c:
        return int(c.execute("SELECT COUNT(*) n FROM notifications WHERE seen=0").fetchone()["n"])


def notify_mark_seen() -> None:
    with conn() as c:
        c.execute("UPDATE notifications SET seen=1 WHERE seen=0")


def watch_add(appid: int, note: str = "") -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO watch (appid, note, added_at) VALUES (?,?,?)",
            (appid, note, now()),
        )


def watch_list() -> list[sqlite3.Row]:
    with conn() as c:
        return c.execute(
            "SELECT w.*, g.name, g.name_cn FROM watch w LEFT JOIN games g USING(appid) ORDER BY w.added_at DESC"
        ).fetchall()


def profile_add(kind: str, text: str, appid: int | None, weight: float) -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO profile_entries (kind, appid, text, weight, created_at) VALUES (?,?,?,?,?)",
            (kind, appid, text, weight, now()),
        )
        return int(cur.lastrowid)


def profile_list(kind: str | None = None) -> list[sqlite3.Row]:
    with conn() as c:
        if kind:
            return c.execute(
                "SELECT * FROM profile_entries WHERE kind=? ORDER BY id DESC", (kind,)
            ).fetchall()
        return c.execute("SELECT * FROM profile_entries ORDER BY id DESC").fetchall()


def profile_delete(entry_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM profile_entries WHERE id=?", (entry_id,))


def set_meta(key: str, value: Any) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def get_meta(key: str, default: Any = None) -> Any:
    with conn() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def stats() -> dict:
    with conn() as c:
        return {
            "games": c.execute("SELECT COUNT(*) n FROM games").fetchone()["n"],
            "prices": c.execute("SELECT COUNT(*) n FROM prices").fetchone()["n"],
            "profile": c.execute("SELECT COUNT(*) n FROM profile_entries").fetchone()["n"],
            "watch": c.execute("SELECT COUNT(*) n FROM watch").fetchone()["n"],
            "snapshots": c.execute(
                "SELECT COUNT(DISTINCT captured_at) n FROM prices"
            ).fetchone()["n"],
        }


def json_col(row: sqlite3.Row | None, col: str, default: Any = None) -> Any:
    if row is None:
        return default
    v = row[col]
    if not v:
        return default
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return default


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
