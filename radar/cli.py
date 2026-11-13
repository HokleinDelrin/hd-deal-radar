"""命令行入口。

    python -m radar.cli init
    python -m radar.cli fetch --pages 2 --specials 20
    python -m radar.cli add "只狼" --why "喜欢招架和成就感，不要迷路"
    python -m radar.cli profile
    python -m radar.cli rec --top 15 --discount-only
    python -m radar.cli low 1245620
    python -m radar.cli serve
    python -m radar.cli daemon --at 09:30 [--once]
    python -m radar.cli selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, db, recommend, tags
from .sources import cheapshark, epic, itad, steam, steamspy

# ---------- 辅助 ----------
def _fmt_money(cents: int | None, currency: str = "CNY") -> str:
    if cents is None:
        return "—"
    sym = "¥" if currency == "CNY" else "$"
    return f"{sym}{cents / 100:.2f}"


def _load_index(verbose: bool = True):
    rows = db.all_games_with_tags()
    idx = recommend.load_index()
    if verbose:
        print(f"[index] {len(idx.vecs)} 个有 tag 的游戏 → 向量维度 {len(idx.idf)} 个 tag"
              f"（全库 {len(rows)} 条）", file=sys.stderr)
    return idx


def _entries_from_db(idx):
    out = []
    for row in db.profile_list():
        if row["kind"] == "note":
            continue
        out.append(recommend.resolve_entry(row["text"], idx, kind=row["kind"], weight=row["weight"]))
    return out


# ---------- 命令 ----------
def cmd_init(args) -> int:
    db.init()
    print(f"[ok] 数据库已初始化: {config.DB_PATH}")
    print(f"[ok] 缓存目录: {config.CACHE_DIR}")
    if not itad.enabled():
        print("[提示] 没有 itad_api_key，ITAD 源会跳过；如需覆盖更多商店，在 config.local.json 里填 itad_api_key")
    return 0


def cmd_fetch(args) -> int:
    db.init()
    total = {"games": 0, "snapshots": 0, "steam_details": 0}

    if args.specials:
        print(f"[steam] 拉特惠列表 ...", file=sys.stderr)
        appids = steam.featured_specials()[: args.specials]
        conc = max(1, int(args.concurrency or config.CFG.get("steam_concurrency") or 1))
        chunk_size = max(conc * 5, 10)
        for i in range(0, len(appids), chunk_size):
            chunk = appids[i:i + chunk_size]
            try:
                rows = steam.ingest_many(chunk, concurrency=conc)
            except Exception as exc:
                print(f"[steam] 批次失败: {exc}", file=sys.stderr)
                continue
            total["games"] += len(rows)
            total["steam_details"] += len(rows)
            total["snapshots"] += sum(1 for g in rows if g.get("price") is not None)
            print(f"[steam] {min(i + chunk_size, len(appids))}/{len(appids)} 并发={conc}",
                  file=sys.stderr)

    if args.pages:
        print(f"[cheapshark] 拉 {args.pages} 页 deals ...", file=sys.stderr)
        res = cheapshark.ingest_deal_pages(args.pages, seed_details=args.seed_details)
        total["games"] += res["games"]
        total["snapshots"] += res["snapshots"]
        total["steam_details"] += res["steam_details"]

    if args.epic:
        fg = epic.free_games()
        print(f"[epic] 免费游戏 {len(fg)} 个（当前 {sum(1 for x in fg if x['status']=='free_now')} 个可领）", file=sys.stderr)

    if args.heybox:
        from .sources import heybox
        if not heybox.enabled():
            print(f"[heybox] 跳过：{heybox.skip_reason()}", file=sys.stderr)
        else:
            with db.conn() as c:
                ids = [r[0] for r in c.execute(
                    "SELECT appid FROM games WHERE appid > 0 AND heybox_cents IS NULL LIMIT ?",
                    (args.heybox,)).fetchall()]
            if ids:
                print(f"[heybox] 补 {len(ids)} 个的小黑盒价", file=sys.stderr)
                hb = heybox.ingest_many(ids, chunk=20)
                total["heybox"] = hb["ok"]

    print(json.dumps(total, ensure_ascii=False))
    return 0


def cmd_add(args) -> int:
    db.init()
    idx = _load_index()
    text = args.text
    if args.why:
        text = f"{text} {args.why}"
    e = recommend.resolve_entry(text, idx, kind=args.kind, weight=args.weight)
    entry_id = db.profile_add(args.kind, text, e.appid, args.weight)
    print(f"[{args.kind}] #{entry_id} 「{text}」")
    if e.resolved_name:
        print(f"  → 解析为游戏: {e.resolved_name} (appid {e.appid})")
    else:
        print(f"  → 未匹配到具体游戏，按描述抽词处理")
    if e.vec:
        top = sorted(e.vec.items(), key=lambda kv: -kv[1])[:8]
        print("  → tag: " + ", ".join(f"{t}({w:.2f})" for t, w in top))
    if e.unmatched:
        print("  → 没听懂: " + ", ".join(e.unmatched[:8]))
    return 0


def cmd_profile(args) -> int:
    db.init()
    idx = _load_index()
    entries = _entries_from_db(idx)
    if not entries:
        print("画像为空。用 `add \"游戏名\" --why \"喜欢哪些地方\"` 加几条。")
        return 0
    vec, top = recommend.build_profile(entries)
    print(f"[entries] {len(entries)} 条输入，画像维度 {len(vec)}")
    for row in db.profile_list():
        if row["kind"] == "note":
            continue
        mark = "+" if row["kind"] == "liked" else "-"
        print(f"  {mark} {row['text']}  (w={row['weight']})")
    print("\n[top tags]")
    for t, w in top:
        aliases = tags.explain_mapping(t)[:3]
        hint = f"  ← {', '.join(aliases)}" if aliases else ""
        print(f"  {w:6.3f}  {t}{hint}")
    return 0


def cmd_rec(args) -> int:
    db.init()
    idx = _load_index()
    entries = _entries_from_db(idx)
    if not entries:
        print("画像为空，先加几条 liked。")
        return 1
    vec, _ = recommend.build_profile(entries)
    exclude = {e.appid for e in entries if e.appid}
    res = recommend.recommend(
        vec, idx, exclude=exclude, top=args.top,
        discount_boost=args.discount_boost, require_discount=args.discount_only,
        diversity=args.diversity,
    )
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    for i, r in enumerate(res, 1):
        flag = "史低!" if r["at_low"] else ("接近史低" if r["near_low"] else "")
        price = f"¥{r['price']:.2f} (-{r['discount']}%)" if r["price"] is not None else "—"
        print(f"{i:2d}. [{r['adj']:.3f}] {r['name']}  {price}  {flag}")
        if r["matched_tags"]:
            print("     tags: " + ", ".join(t for t, _ in r["matched_tags"][:5]))
    return 0


def cmd_low(args) -> int:
    db.init()
    target = args.query
    appid = int(target) if target.isdigit() else None
    if appid is None:
        found = db.find_games(target, limit=1)
        if not found:
            print(f"库里没有「{target}」，先用 search 或 fetch 补数据。")
            return 1
        appid = int(found[0]["appid"])
    g = db.get_game(appid)
    if not g:
        print(f"库里没有 appid={appid}")
        return 1
    cur = db.current_prices(appid)
    low = db.lowest(appid)
    print(f"{g['name']} (appid {appid})  {g['shop_url'] or ''}")
    print(f"  本地史低: {_fmt_money(low['cents'] if low else None)}   快照数: {low['n'] if low else 0}")
    if g["steam_lowest_cents"]:
        print(f"  Steam 史低（小黑盒口径）: {_fmt_money(g['steam_lowest_cents'])}")
    if g["heybox_cents"]:
        print(f"  小黑盒到手价: {_fmt_money(g['heybox_cents'])}  → https://www.xiaoheihe.cn/app/topic/game/pc/{appid}")
    ext = cheapshark.history_low(appid)
    if ext:
        print(f"  CheapShark 史低: ${ext['usd']:.2f}  ({ext['date']})")
    print("  当前各店:")
    for p in cur:
        print(f"    {p['shop']:<20} {_fmt_money(p['cents'], p['currency'])}  -{p['discount']}%  {p['captured_at'][:10]}")
    return 0


def cmd_search(args) -> int:
    db.init()
    local = db.find_games(args.term, limit=10)
    print(f"[本地库] {len(local)} 条")
    for g in local:
        print(f"  {g['appid']:<10} {g['name']}  mc={g['metacritic']}")
    print("[Steam 在线]")
    try:
        for it in steam.storesearch(args.term)[:10]:
            p = f"¥{it['price']:.2f} -{it['discount']}%" if it["price"] else "—"
            print(f"  {it['appid']:<10} {it['name']}  {p}")
    except Exception as exc:
        print(f"  在线搜索失败: {exc}")
    return 0


def cmd_enrich(args) -> int:
    """给库里缺 tag 的游戏补 SteamSpy tag。1.3s/个，用 --budget 控制一轮的量。"""
    db.init()
    with db.conn() as c:
        rows = c.execute(
            "SELECT appid, name FROM games WHERE tags IS NULL OR review_score IS NULL "
            "ORDER BY review_count DESC NULLS LAST, appid LIMIT ?", (args.budget,)
        ).fetchall()
    if not rows:
        print("[enrich] 没有缺 tag 的游戏")
        return 0
    print(f"[enrich] 待补 {len(rows)} 个，预计 {len(rows) * 1.4:.0f}s", file=sys.stderr)
    ok = 0
    scored = 0
    for i, r in enumerate(rows, 1):
        try:
            res = steamspy.enrich_appid(int(r["appid"]))
            if res["tags"]:
                ok += 1
            if res["score"] is not None:
                scored += 1
        except Exception as exc:
            print(f"[enrich] {r['appid']} 失败: {exc}", file=sys.stderr)
        if i % 10 == 0:
            print(f"[enrich] {i}/{len(rows)} tag {ok} 口碑 {scored}", file=sys.stderr)
    print(json.dumps({"tried": len(rows), "with_tags": ok, "with_reviews": scored}, ensure_ascii=False))
    return 0


def cmd_harvest(args) -> int:
    """从 SteamSpy 榜单铺候选池（只要名字/口碑，tag 之后用 enrich 补）。"""
    db.init()
    print(json.dumps(steamspy.harvest(pages=args.pages, source=args.source), ensure_ascii=False))
    return 0


def cmd_sync_owned(args) -> int:
    """把 Steam 库 / 愿望单导进画像（GetOwnedGames 需 steam_api_key + profile 公开）。"""
    from .sources import steamlib
    db.init()
    sid = (args.id or "").strip() or None
    try:
        if args.wishlist:
            res = steamlib.import_wishlist(limit=args.limit, steamid=sid, dry_run=args.dry_run)
        else:
            res = steamlib.import_to_profile(
                min_minutes=args.min_minutes, limit=args.limit, steamid=sid,
                dry_run=args.dry_run, skip_existing=not args.force,
            )
    except steamlib.SteamError as exc:
        print(f"[steam] {exc}")
        return 1
    print(json.dumps(res, ensure_ascii=False, indent=2 if args.dry_run else None))
    if res.get("status") == "ok" and not args.wishlist:
        print("\n下一步：python -m radar.cli profile  看画像有没有被拉偏；"
              "python -m radar.cli rec --top 15 看推荐。")
    return 0


def cmd_keys(args) -> int:
    """写 key / 校验 key。写完立刻联网验证 —— 别带着错 key 去跑一整晚采集。"""
    from .sources import steamlib
    db.init()
    updates = {}
    if args.itad:
        updates["itad_api_key"] = args.itad.strip()
    if args.steam_key:
        updates["steam_api_key"] = args.steam_key.strip()
    if args.steam_id:
        updates["steam_id"] = args.steam_id.strip()

    if updates and not args.check:
        path = config.save_local(updates)
        print(f"[ok] 已写入 {path}（只并入这几项，其它字段原样保留）")
    elif not updates and not args.check:
        print("""用法（可以一次给多项）：
  python -m radar.cli keys --itad <ITAD_KEY>            # https://isthereanydeal.com/apps/ 免费申请
  python -m radar.cli keys --steam-key <STEAM_KEY>      # https://steamcommunity.com/dev/apikey
  python -m radar.cli keys --steam-id <17位ID或主页链接>
  python -m radar.cli keys --check                      # 只校验，不写""")
        args.check = True

    # ---- 校验（--check 时用命令行传入的值临时校验，不落盘）----
    eff_itad = (args.itad or "").strip() or str(config.CFG.get("itad_api_key") or "")
    eff_ref = (args.steam_id or "").strip() or str(config.CFG.get("steam_id") or "")
    eff_skey = (args.steam_key or "").strip() or str(config.CFG.get("steam_api_key") or "")

    print("\n[校验]")
    iv = itad.verify_key(eff_itad)
    print(f"  ITAD key     {'OK  ' if iv['ok'] else 'FAIL'}  {iv['detail']}")
    if not iv["ok"]:
        print("               → 去 https://isthereanydeal.com/apps/ 注册拿 key（免费），"
              "然后 keys --itad <KEY>")

    if eff_ref:
        try:
            info = steamlib.profile_info(eff_ref)
            print(f"  Steam ID     OK    steamID64={info['steamid']}  昵称={info['nick']}  隐私={info['privacy']}")
            if not info["public"]:
                print("               → 不是 public：库和愿望单都会读成空。去 Steam「隐私设置」把"
                      "「游戏详情」设为公开")
        except Exception as exc:
            print(f"  Steam ID     FAIL  {exc}")
    else:
        print("  Steam ID     FAIL  没填（可以填 17 位 ID，也可以直接粘个人主页链接）")

    sv = steamlib.verify_key(eff_skey)
    print(f"  Steam key    {'OK  ' if sv['ok'] else 'FAIL'}  {sv['detail']}")
    if not sv["ok"]:
        print("               → 去 https://steamcommunity.com/dev/apikey 申请（域名随便填 localhost），"
              "然后 keys --steam-key <KEY>")
    return 0


def cmd_itad(args) -> int:
    """给库里的游戏补 ITAD 多店价 + 官方史低（批量端点，一次 200 个）。"""
    db.init()

    if args.map_only:
        # 映射端点免 key，所以「等 key 到之前」就能先把这张表建好
        with db.conn() as c:
            ids = [r[0] for r in c.execute(
                "SELECT appid FROM games WHERE appid > 0 ORDER BY appid").fetchall()]
        mapping = itad.lookup_many(ids, verbose=True)
        print(json.dumps({"status": "ok", "mapped": len(mapping), "total": len(ids)}, ensure_ascii=False))
        return 0

    if not itad.enabled():
        v = itad.verify_key()
        print(f"[itad] 跳过：{v['detail']}。要启用就先 keys --itad <KEY>", file=sys.stderr)
        return 0
    with db.conn() as c:
        if args.appids:
            ids = [int(x) for x in args.appids.replace(",", " ").split()]
        else:
            cond = "1=1" if args.force else (
                "NOT EXISTS (SELECT 1 FROM prices p WHERE p.appid=g.appid AND p.source='itad')")
            ids = [r[0] for r in c.execute(
                f"SELECT appid FROM games g WHERE g.appid > 0 AND {cond} "
                "ORDER BY metacritic DESC NULLS LAST, appid LIMIT ?", (args.limit,)).fetchall()]
    if not ids:
        print("[itad] 没有要补的游戏（想全量重刷加 --force）")
        return 0
    print(f"[itad] 目标 {len(ids)} 个，批量 {args.batch} 一组", file=sys.stderr)
    total = {"games": 0, "new_snapshots": 0, "history_lows": 0, "skipped_non_cny": 0, "unresolved": 0}
    for i in range(0, len(ids), args.batch):
        chunk = ids[i:i + args.batch]
        res = itad.ingest_many(chunk, limit=len(chunk), with_lows=not args.no_lows)
        if res.get("status") != "ok":
            print(f"[itad] {res}", file=sys.stderr)
            break
        for k in total:
            total[k] += res.get(k, 0)
        print(f"[itad] {min(i + args.batch, len(ids))}/{len(ids)}  快照+{total['new_snapshots']}  "
              f"史低+{total['history_lows']}", file=sys.stderr)
    print(json.dumps(total, ensure_ascii=False))
    return 0


def cmd_heybox(args) -> int:
    """补小黑盒价 + Steam 史低。批量接口一次 20 个；--all 就扫全库还缺的。

    默认要求先登录（见 heybox.enabled()）—— 这个源走的是未公开接口，把身份摆在明处更稳妥。
    """
    from .sources import heybox
    db.init()
    if not heybox.enabled():
        print(f"[heybox] 跳过：{heybox.skip_reason()}", file=sys.stderr)
        return 0
    if getattr(args, "deadlines", False):
        res = heybox.refresh_deadlines(limit=args.limit)
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if args.all:
        cond = "1=1" if args.force else "heybox_cents IS NULL"
        with db.conn() as c:
            ids = [r[0] for r in c.execute(
                f"SELECT appid FROM games WHERE appid > 0 AND {cond} "
                "ORDER BY metacritic DESC NULLS LAST LIMIT ?", (args.limit,)).fetchall()]
    elif args.appids:
        ids = [int(x) for x in args.appids.replace(",", " ").split()]
    else:
        print("要么给 appid（--appids 1,2,3），要么 --all")
        return 1
    if not ids:
        print("[heybox] 没有需要补的游戏")
        return 0
    print(f"[heybox] 目标 {len(ids)} 个", file=sys.stderr)
    res = heybox.ingest_many(ids, chunk=args.chunk)
    print(json.dumps(res, ensure_ascii=False))
    return 0


def cmd_notify(args) -> int:
    """把关注范围里跌到史低的推出来（默认弹 Windows 通知；配了 TG 就同时发手机）。"""
    from . import notify
    db.init()
    if args.test:
        print(json.dumps(notify.test_channels(), ensure_ascii=False, indent=2))
        print("\n判读：landed=true 说明系统确实收下了这条通知；但「收下」不等于「弹横幅」——"
              "Win10 上未注册的 AUMID 常常只进通知中心。无论如何网页端的提醒中心一定有记录。")
        return 0
    if args.mark_seen:
        db.notify_mark_seen()
        print("[ok] 提醒已全部标记为已读")
        return 0
    res = notify.run(dry_run=args.dry_run, limit=args.limit, quiet=args.quiet, instant=args.now)
    print(json.dumps(res, ensure_ascii=False))
    if not notify.tracked_appids():
        print("关注范围为空：先用 `add` 加几条 liked，或 `sync-owned --wishlist` 把 Steam 愿望单导进来。")
    return 0


def cmd_heybox_login(args) -> int:
    """登录小黑盒，把会话存下来（之后的采集会带上它）。"""
    from . import heybox_login as hl
    db.init()
    if args.status:
        print(json.dumps(hl.status(), ensure_ascii=False, indent=2))
        return 0
    if args.clear:
        print("[ok] 已清除登录态" if hl.clear() else "[i] 本来就没有登录态")
        return 0
    if args.paste:
        try:
            print(json.dumps(hl.paste(args.paste), ensure_ascii=False, indent=2))
        except RuntimeError as exc:
            print(f"[heybox-login] {exc}")
            return 1
        return 0

    try:
        created = hl.start_browser(args.port)
    except RuntimeError as exc:
        print(f"[heybox-login] {exc}")
        return 1
    if created is None:
        print("[heybox-login] 复用已经打开的登录浏览器")
    print("""
在弹出的 Edge 窗口里登录小黑盒（扫码或账号密码都行，验证码你自己过）。
登录成功后回到这个窗口按回车，我读取登录态并存下来。

这个窗口用的是独立 profile（data/edge-profile），不会碰你日常浏览器的数据；
它也会保留，下次通常不用重登。不想开浏览器就：
  python -m radar.cli heybox-login --paste "<F12 里复制的 Cookie 头>"
""")
    try:
        input("登录完成后按回车…")
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return 1
    try:
        info = hl.save(hl.grab_cookies(args.port))
    except Exception as exc:
        print(f"[heybox-login] 失败: {exc}")
        return 1
    print(f"[ok] 已保存 {info['count']} 条 cookie → {hl.COOKIE_FILE}")
    if not info.get("looks_logged_in"):
        print("[注意] 这些 cookie 里没有看到会话/用户标识（pkey / heybox_id 之类），"
              "很可能只是未登录时的匿名 cookie。请在浏览器里确认已登录，再点一次「读取登录态」。")
    else:
        print("采集会自动带上登录态。清除：heybox-login --clear")
    return 0


def cmd_cache_gc(args) -> int:
    """清理 cache/ 里的旧原始响应。默认只报告，--apply 才删。

    cache/ 是「跑一次就有原文可复查」的凭据，但它不是数据——真正的数据在 SQLite。
    留着太旧的原响应只会让目录几千个文件、grep 变慢，所以按天清理，默认保留 14 天。
    """
    import time as _t
    now = _t.time()
    cutoff = now - args.days * 86400
    files = [p for p in config.CACHE_DIR.glob("*") if p.is_file()]
    stale = [p for p in files if p.stat().st_mtime < cutoff]
    total_mb = sum(p.stat().st_size for p in files) / 1e6
    stale_mb = sum(p.stat().st_size for p in stale) / 1e6
    print(f"[cache] {len(files)} 个文件 / {total_mb:.1f} MB；"
          f"超过 {args.days} 天的 {len(stale)} 个 / {stale_mb:.1f} MB")
    if not args.apply:
        print("（只报告。确认要删就加 --apply）")
        return 0
    for p in stale:
        try:
            p.unlink()
        except OSError as exc:
            print(f"[cache] 删不掉 {p.name}: {exc}", file=sys.stderr)
    print(f"[cache] 已删 {len(stale)} 个，释放约 {stale_mb:.1f} MB")
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    db.init()
    serve(host=args.host or config.CFG["host"], port=args.port or config.CFG["port"])
    return 0


def cmd_daemon(args) -> int:
    """常驻调度：每天 --at 时刻跑一轮 harvest → fetch → enrich，Ctrl+C 退出。

    只做转发：时刻计算、异常隔离、日志都在 scheduler 里，这里不复制逻辑。
    延迟 import 是为了避免 cli ↔ scheduler 循环导入（scheduler 需要 cmd_* 函数）。
    """
    from . import scheduler

    return scheduler.run_daily(at=args.at, once=args.once, max_rounds=args.rounds or None)


def cmd_selftest(args) -> int:
    """离线自检：不联网，检查画像/推荐/史低计算的关键逻辑。"""
    db.init()
    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{'  ' + detail if detail else ''}")
        ok = ok and cond

    # 1) tag 抽取
    hits, un = tags.extract_tags("喜欢只狼的招架手感，肉鸽卡牌也能接受，不要恐怖")
    check("中文描述 → tag", "Souls-like" in hits and "Roguelike Deckbuilder" in hits,
          f"命中 {len(hits)}: {', '.join(list(hits)[:5])}")
    check("「不要恐怖」不进正向", "Horror" not in hits, f"正向={list(hits)}")
    neg = tags.extract("喜欢只狼的招架手感，不要恐怖")["negative"]
    check("否定 → 负向 tag", "Horror" in neg, f"负向={list(neg)}")
    check("否定词进 unmatched 不丢信息", isinstance(un, list))

    hits2, _ = tags.extract_tags("hades is a roguelite with great story")
    check("英文描述 → tag", "Roguelite" in hits2 and "Story Rich" in hits2)

    # 2) 索引与余弦（构造两条假数据）
    import math
    fake = [
        {"appid": -1, "name": "FakeSouls", "name_cn": "假魂", "tags": json.dumps([["Souls-like", 400], ["Difficult", 300]]), "metacritic": 90},
        {"appid": -2, "name": "FakeFarm", "name_cn": "假农场", "tags": json.dumps([["Farming Sim", 400], ["Cozy", 300]]), "metacritic": 80},
    ]
    idx = recommend.build_index(fake)
    check("索引建立", idx.size == 2)
    v, _ = recommend.text_vector("魂类 难", idx)
    check("文本向量非空", bool(v), str({k: round(x, 2) for k, x in v.items()}))

    e = recommend.resolve_entry("假魂", idx, "liked", 1.0)
    check("名字能解析到游戏", e.appid == -1, f"resolved={e.resolved_name}")
    pvec, top = recommend.build_profile([e])
    check("画像向量归一", abs(math.sqrt(sum(x * x for x in pvec.values())) - 1.0) < 1e-6)

    # 2.5) Steam 引用解析（纯离线）
    from .sources import steamlib as _sl
    check("steamID64 直认", _sl.parse_ref("76561197960287930") == ("76561197960287930", None))
    check("profiles 链接 → ID",
          _sl.parse_ref("https://steamcommunity.com/profiles/76561197960287930/games/?tab=all")[0]
          == "76561197960287930")
    check("vanity 链接 → 自定义名",
          _sl.parse_ref("https://steamcommunity.com/id/gabelogannewell/") == (None, "gabelogannewell"))
    check("裸自定义名也能认", _sl.parse_ref("gabelogannewell") == (None, "gabelogannewell"))
    check("玩时长权重：单调且封顶",
          _sl.playtime_weight(9999999) == 3.0
          and _sl.playtime_weight(600) > _sl.playtime_weight(60) > _sl.playtime_weight(0) > 0)

    # 2.6) ITAD 解析（读 fixtures 里的响应样本，不联网）
    from .sources import itad as _itad
    fx = config.FIXTURE_DIR / "itad_prices_v3.json"
    if fx.exists():
        node = json.loads(fx.read_text(encoding="utf-8"))[0]
        rows, skipped = _itad.deal_rows(node, "CNY")
        check("ITAD 多店价解析", len(rows) == 2 and rows[0]["price"] == 99.0,
              f"rows={len(rows)} 首行={rows[0] if rows else None}")
        check("ITAD 非 CNY 行被丢", skipped == 1, f"skipped={skipped}")
        check("ITAD 金额节点容错",
              _itad._amount(None) is None and _itad._amount({"amount": 9.99}) == 9.99
              and _itad._amount({"amount": "x"}) is None)
    else:
        print(f"  SKIP  ITAD fixture 不存在: {fx}")

    fx2 = config.FIXTURE_DIR / "itad_historylow_v1.json"
    if fx2.exists():
        lows = json.loads(fx2.read_text(encoding="utf-8"))
        row = _itad.low_row(lows[0])
        check("ITAD 官方史低解析（low.price.amount）",
              row is not None and row["amount"] == 79.2 and row["currency"] == "CNY"
              and row["shop"] == "Steam",
              f"row={row}")
        check("ITAD 史低空值不报错", _itad.low_row(lows[2]) is None)
    else:
        print(f"  SKIP  ITAD 史低 fixture 不存在: {fx2}")

    # 3) 史低计算与快照去重：用临时 appid 验证，测完清掉
    TEST_ID = -999999
    try:
        with db.conn() as c:
            c.execute("DELETE FROM prices WHERE appid=?", (TEST_ID,))
        p = {"appid": TEST_ID, "source": "test", "shop": "T", "currency": "CNY",
             "price": 10.0, "list_price": 20.0, "discount": 50}
        w1 = db.add_price(p, captured_at="2026-01-01T00:00:00+08:00")
        w2 = db.add_price(p, captured_at="2026-01-02T00:00:00+08:00")   # 同价
        w3 = db.add_price(p, captured_at="2026-01-03T00:00:00+08:00")   # 同价
        n = db.lowest(TEST_ID)["n"]
        check("同价不重复写快照", w1 is True and w2 is False and w3 is False and n == 1,
              f"wrote={[w1, w2, w3]} rows={n}")
        p2 = dict(p, price=8.0, discount=60)
        w4 = db.add_price(p2, captured_at="2026-01-04T00:00:00+08:00")
        low = db.lowest(TEST_ID)
        check("降价会写新快照且史低跟着降", w4 is True and low["n"] == 2 and low["cents"] == 800,
              f"lowest={low['cents']} rows={low['n']}")
        with db.conn() as c:
            c.execute("DELETE FROM prices WHERE appid=?", (TEST_ID,))
        check("测试数据已清理", db.lowest(TEST_ID)["n"] == 0)
    except Exception as exc:
        check("史低与去重逻辑", False, str(exc))

    # 4) 元数据软字段：不带 tags 的 upsert 不能抹掉已有 tag（2026-11-12 真踩过：217 行 tag 被打回 1 行）
    SOFT_ID = -999998
    try:
        db.upsert_game({"appid": SOFT_ID, "name": "SoftFieldTest", "tags": [["Souls-like", 100]]})
        db.upsert_game({"appid": SOFT_ID, "name": "SoftFieldTest", "short_desc": "只给描述，不带 tags"})
        kept = db.json_col(db.get_game(SOFT_ID), "tags", [])
        check("不带 tags 的 upsert 保留已有 tag", bool(kept) and kept[0][0] == "Souls-like", f"tags={kept}")
    except Exception as exc:
        check("软字段保留", False, str(exc))
    finally:
        with db.conn() as c:
            c.execute("DELETE FROM games WHERE appid=?", (SOFT_ID,))

    # 5) MMR：相似的往后压，不一样的顶上来
    mmr_idx = recommend.Index(
        games={-1: {}, -2: {}, -3: {}},
        vecs={-1: {"A": 1.0}, -2: {"A": 1.0}, -3: {"B": 1.0}},
        idf={"A": 1.0, "B": 1.0}, df={"A": 2, "B": 1},
    )
    mmr_in = [{"appid": -1, "adj": 1.0}, {"appid": -2, "adj": 0.95}, {"appid": -3, "adj": 0.5}]
    order = [x["appid"] for x in recommend._mmr_rerank(list(mmr_in), mmr_idx, 0.6, 3)]
    check("MMR 把重复的压到后面", order == [-1, -3, -2], f"order={order}")
    check("MMR 不重排时保持原序",
          [x["appid"] for x in recommend._mmr_rerank(list(mmr_in), mmr_idx, 1.0, 3)] == [-1, -2, -3])

    # 6) 价格视图：Row 与 dict 都能吃；ITAD 官方史低能把状态升级
    import sqlite3 as _sq
    _mem = _sq.connect(":memory:")
    _mem.row_factory = _sq.Row
    row_obj = _mem.execute("select 888 as heybox_cents, 999 as steam_lowest_cents").fetchone()
    v_row = recommend.deal_view(1, row_obj, best=None, lows={}, prev_map={}, itad_lows={})
    check("deal_view 能吃 sqlite3.Row", v_row["heybox"] == 8.88, f"heybox={v_row['heybox']}")
    v_hit = recommend.deal_view(
        1, {"heybox_cents": None, "steam_lowest_cents": None},
        best={"cents": 5000, "list_cents": 10000, "discount": 50, "shop": "Steam"},
        lows={1: (5000, 3)}, prev_map={(1, "Steam"): 6000},
        itad_lows={1: {"cents": 5500}},
    )
    check("现价低于 ITAD 官方史低 → 判新低",
          v_hit["state"] == "new" and v_hit["below_itad_low"] is True,
          f"state={v_hit['state']} itad_low={v_hit['itad_lowest']}")

    # 7) 索引缓存：同一进程内不重复重建
    check("索引命中缓存", recommend.load_index() is recommend.load_index())

    print("\n[selftest]", "全部通过" if ok else "有失败项")
    return 0 if ok else 1

def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，打 ¥ 会直接 UnicodeEncodeError，这里强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    p = argparse.ArgumentParser(prog="radar", description="史低雷达：跨平台史低 + 画像推荐")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="初始化数据库")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("fetch", help="采集一轮")
    s.add_argument("--pages", type=int, default=0, help="CheapShark deals 页数（每页 60 条）")
    s.add_argument("--specials", type=int, default=0, help="Steam 特惠区抓多少个 appid")
    s.add_argument("--seed-details", type=int, default=20, help="顺带补多少游戏的 Steam 元数据")
    s.add_argument("--epic", action="store_true", help="顺带拉 Epic 免费游戏")
    s.add_argument("--heybox", type=int, default=0, help="顺带给多少个缺价游戏补小黑盒价")
    s.add_argument("--concurrency", type=int, default=0,
                   help="appdetails 并发线程数（0=用 config.steam_concurrency，默认 1；实测 4 安全）")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("add", help="加一条画像输入")
    s.add_argument("text", help="游戏名，或一段描述")
    s.add_argument("--why", default="", help="为什么喜欢，具体到哪些地方")
    s.add_argument("--kind", choices=["liked", "disliked"], default="liked")
    s.add_argument("--weight", type=float, default=1.0)
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("profile", help="看画像")
    s.set_defaults(func=cmd_profile)

    s = sub.add_parser("rec", help="推荐")
    s.add_argument("--top", type=int, default=20)
    s.add_argument("--discount-only", action="store_true", help="只要正在打折的")
    s.add_argument("--discount-boost", type=float, default=recommend.DISCOUNT_BOOST)
    s.add_argument("--diversity", type=float, default=0.0,
                   help="多样度 0-0.9：0.3 表示把「同一批 tag 扎堆」的结果压掉三成")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_rec)

    s = sub.add_parser("low", help="看某游戏的价格与史低")
    s.add_argument("query", help="appid 或名字")
    s.set_defaults(func=cmd_low)

    s = sub.add_parser("search", help="搜游戏（本地 + Steam 在线）")
    s.add_argument("term")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("harvest", help="从 SteamSpy 榜单铺候选池")
    s.add_argument("--source", default="top100in2weeks",
                   choices=["top100in2weeks", "top100forever", "all"])
    s.add_argument("--pages", type=int, default=1, help="source=all 时的页数，每页 1000")
    s.set_defaults(func=cmd_harvest)

    s = sub.add_parser("enrich", help="给缺 tag 的游戏补 SteamSpy tag")
    s.add_argument("--budget", type=int, default=100)
    s.set_defaults(func=cmd_enrich)

    s = sub.add_parser("sync-owned", help="把 Steam 库 / 愿望单导进画像")
    s.add_argument("--min-minutes", type=int, default=60, help="至少玩了多少分钟才算数")
    s.add_argument("--limit", type=int, default=40, help="最多导入多少个（按玩时长降序）")
    s.add_argument("--id", default="", help="临时指定 steamID64 / 主页链接，不写进 config")
    s.add_argument("--wishlist", action="store_true", help="改成导愿望单（进关注列表，免 key）")
    s.add_argument("--dry-run", action="store_true", help="只看会导入什么，不落库")
    s.add_argument("--force", action="store_true", help="已存在的 liked 也再加一条")
    s.set_defaults(func=cmd_sync_owned)

    s = sub.add_parser("keys", help="写入 / 校验 ITAD 与 Steam 的 key")
    s.add_argument("--itad", default="", help="ITAD API key")
    s.add_argument("--steam-key", default="", help="Steam Web API key")
    s.add_argument("--steam-id", default="", help="steamID64 或个人主页链接")
    s.add_argument("--check", action="store_true", help="只校验现有配置，不写入")
    s.set_defaults(func=cmd_keys)

    s = sub.add_parser("itad", help="补 ITAD 多店价与官方史低（需 itad_api_key）")
    s.add_argument("--appids", default="", help="逗号分隔的 Steam appid")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--batch", type=int, default=50, help="每组多少个，组间有打印")
    s.add_argument("--force", action="store_true", help="已经采过 ITAD 的也重刷")
    s.add_argument("--no-lows", action="store_true", help="只要当前价，不要官方史低")
    s.add_argument("--map-only", action="store_true",
                   help="只建 appid→uuid 映射（这个端点免 key，可以提前跑）")
    s.set_defaults(func=cmd_itad)

    s = sub.add_parser("heybox", help="补小黑盒价与 Steam 史低")
    s.add_argument("--appids", default="", help="逗号分隔的 Steam appid")
    s.add_argument("--all", action="store_true", help="扫全库还缺小黑盒价的")
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--force", action="store_true", help="连已有价的也重刷（拿券截止时间等新字段用）")
    s.add_argument("--deadlines", action="store_true", help="只补优惠截止时间（单查接口才有，0.8s/个）")
    s.add_argument("--chunk", type=int, default=20)
    s.set_defaults(func=cmd_heybox)

    s = sub.add_parser("notify", help="把跌到史低的游戏推出来（Windows 通知 / Telegram）")
    s.add_argument("--limit", type=int, default=5, help="最多提醒几个")
    s.add_argument("--dry-run", action="store_true", help="只打印，不弹不发")
    s.add_argument("--quiet", action="store_true", help="没新的就别说话（daemon 用）")
    s.add_argument("--test", action="store_true", help="逐条通道自测：弹窗 + 声音 + 查系统是否真的收到")
    s.add_argument("--mark-seen", action="store_true", help="把提醒中心里的都标记已读")
    s.add_argument("--now", action="store_true",
                   help="立刻弹（默认只入队到全局汇总器，等它按你定的时间统一提醒）")
    s.set_defaults(func=cmd_notify)

    s = sub.add_parser("heybox-login", help="登录小黑盒（之后采集带登录态）")
    s.add_argument("--paste", default="", help="直接粘贴 Cookie 头（不想开浏览器时用）")
    s.add_argument("--status", action="store_true", help="看现在存了什么")
    s.add_argument("--clear", action="store_true", help="清除登录态")
    s.add_argument("--port", type=int, default=9223, help="CDP 调试端口")
    s.set_defaults(func=cmd_heybox_login)

    s = sub.add_parser("cache-gc", help="清理 cache/ 里的旧原始响应（默认只报告）")
    s.add_argument("--days", type=int, default=14, help="保留多少天内的原始响应")
    s.add_argument("--apply", action="store_true", help="真的删")
    s.set_defaults(func=cmd_cache_gc)

    s = sub.add_parser("serve", help="起本地 UI")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("daemon", help="常驻：每天定时采集一轮（Ctrl+C 退出）")
    s.add_argument("--at", default="09:30", help="每天几点跑，HH:MM")
    s.add_argument("--once", action="store_true", help="先立刻跑一轮，再进入每日循环")
    s.add_argument("--rounds", type=int, default=0, help="跑 N 轮就退出（0=常驻）；验收/调试用")
    s.set_defaults(func=cmd_daemon)

    s = sub.add_parser("selftest", help="离线自检")
    s.set_defaults(func=cmd_selftest)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
