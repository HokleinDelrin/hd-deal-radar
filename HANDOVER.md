# HANDOVER — game-deal-radar（史低雷达）

> 新会话先读这份，再读 `README.md`。本轮由 2026-11-12 会话更新（ITAD 批量接入 + Steam 库导入收尾）。

## 一句话现状

跑得起来的本地应用：**跨平台史低聚合（Steam + 小黑盒 + CheapShark + Epic）+ 按游玩画像推荐**。
Web UI 在 http://127.0.0.1:8790，Windows 计划任务 `game-deal-radar-daily` 已注册，每天 09:30 自动采集。
库内：246 个游戏 / 3182 条价格快照 / 220 个有 tag 与口碑 / 84 个有券倒计时 / 145 个有小黑盒价 /
**246 条 ITAD appid→uuid 映射（已提前建好，见下）**。

## 怎么跑（几条常用命令）

```powershell
cd game-deal-radar
python -m radar.cli selftest           # 离线自检（28 项断言，今天实测全通过；先跑这个确认没坏）
python -m radar.cli serve              # 起界面 → http://127.0.0.1:8790
python -m radar.cli rec --top 20       # 命令行看推荐
python -m radar.cli keys               # 看 key 该填什么、现在能不能用
python -m radar.cli itad --map-only    # 免 key 建 ITAD 映射（幂等，已跑过就不用再跑）
python -m radar.cli notify             # 把跌到史低的推出来（Windows 通知 / Telegram）
```

## 本轮（2026-11-12）新增

1. **`keys` 命令：一条命令接管所有凭据**。写入 `config.local.json` 后**立刻联网校验**，
   三项分别报 OK/FAIL：ITAD key、Steam ID（顺带报 profile 是否 public）、Steam key。
   `--check` 可以只校验不写。缺 key 时的提示直接带注册地址。
2. **ITAD 改成真批量**：`POST /games/prices/v3` + `POST /games/historylow/v1`，body 是**纯 UUID 数组**
   （≤200 个/次）。appid→uuid 用 `POST /lookup/id/shop/61/v1`，**这个端点免 key**，
   所以 246 条映射已经提前建好落在 `meta.itad_map` —— 等 key 到手只剩一步 `itad --limit 246`。
3. **Steam 库导入做完整**：`sync-owned` 支持 `--dry-run`（先看清单）、`--id`（临时传 ID/链接，不写 config）、
   `--wishlist`（愿望单进关注列表，这条**不需要 Steam key**）、`--force`。已存在的 liked 默认不重复加。
4. **steamID 解析**：接受 17 位 ID / `profiles/<id>` 链接 / `id/<vanity>` 链接 / 裸自定义名，
   用公开 profile XML（`?xml=1`，免 key）解析，并回报 `privacyState`。
5. **`cache-gc`**：`cache/` 现在 837 个文件 / 11 MB，默认只报告，`--apply` 才按 `--days`（默认 14）删。
6. **调度接上 ITAD**：`itad_refresh`（每日，`--limit 200`）+ `itad_refresh_force`（周一，`--limit 400 --force`）。
   没 key 时自己返回 0 跳过，不会让整轮日志变红。
7. **详情接口带 ITAD 史低**：`/api/game` 多了 `itad_lowest` 字段（`{price, at, shop}`），数据来自 `meta.itad_low`。
8. **自检 14 → 23 项**：新增 Steam 引用解析 5 项、ITAD 现价解析 3 项、ITAD 官方史低解析 2 项，
   全部读 `fixtures/*.json`，不联网。
9. **推送落地**（`radar/notify.py` + `cli notify`）：Windows 通知（PowerShell 5.1 调 WinRT toast，不装模块）
   + Telegram（配了 token/chat_id 才发）+ 控制台（`--dry-run`）。扫「关注列表 ∪ 画像 liked」，
   只提醒 `super/new/equal` 三档；去重记在 `meta.notified`，**同价不重复响，价格更低才再响**。
   已接进每日与每周任务末尾（`notify_deals`），实测弹窗成功、第二次跑正确判定「没有新的」。
10. **ITAD 史低当裁判**：`recommend.deal_view()` 里现价低于 ITAD 官方史低时把状态升到 `new`，
    界面与推送用的是同一个函数，不会出现「推送说史低、界面说接近史低」。
11. **MMR 去重**：`rec --diversity 0.3`、`/api/rec?div=0.3`。实测把与辐射4 高度相似的「腐蚀」
    从第 2 位压到第 5 位，换进更不一样的候选。默认 0（关闭），保持旧行为。
12. **修掉一个真 bug（重点）**：`upsert_game` 以前是 `tags=excluded.tags`，而 steam/cheapshark/heybox
    三条采集链都不带 tags —— 每天 `fetch` 会把 SteamSpy 补好的 tag 全部抹成 NULL。
    实测症状：`review_score` 有 217 行、`tags` 只剩 1 行，推荐退化成 1 条结果。
    现在软字段（tags/简介/封面/开发商等）一律 `COALESCE`，并加了回归断言；随后 `enrich --budget 250` 补回 219 个。
13. **索引缓存统一**：`recommend.load_index()` 按 `radar.db` 的 mtime 失效，CLI 与 Web 共用一份；
    顺手修掉 server 里「按 COUNT+MAX(fetched_at) 失效」漏掉 `set_game_tags` 的问题。
14. **appdetails 并发**：`--concurrency N`（config `steam_concurrency`，默认 1）。限速计时器改成线程本地，
    实测 30 个请求 41.1s → 12.0s（3.4×），真实 appid 12 个 5.5s 全部成功，无 429。
15. **复合索引** `idx_prices_latest(appid, currency, shop, captured_at DESC)`。
16. **提醒改成「攒着、定点一次」**：`notify` 默认**不再当场弹**，而是入队到全局汇总器
    `~/.dsh/tools/notify-hub.py`；它每天 21:30（计划任务 `dsh-notify-digest`）把所有应用的积压
    合并成一条系统通知，并生成明细页 `~/.dsh/notify/today.html`。`notify --now` 才是立刻弹。
    理由：一个应用一天一条能忍，五六个应用就变成「谁都能随时打断我」。
17. **网页提醒中心**：右上角「提醒」+ 未读红点，后端 `/api/notifications`、`POST /api/notifications/seen`。
    Toast 会被系统吞，但 `notifications` 表一定有 —— 这才是「一条一条看」的入口。
18. **通道自测** `notify --test`：分别用两个 AUMID 发一条，并比 `History` 条数前后差，回答「系统到底收没收到」。
    踩坑：自造 AUMID（`game-deal-radar`）在 Win10 只进通知中心、不弹横幅（History 有记录但屏幕啥也没有），
    换成 PowerShell 已注册的 AUMID 才会真弹。
19. **前端坑**：铃铛按钮不能放进 `<nav class="tabs">` —— app.js 把 `.tabs button` 全部当页签，
    点一下会把整个主体切空（截图验证过：`main` 高度从 9597 塌到 98）。
20. **打包成桌面应用**（`app.py` + PyInstaller）：`dist/史低雷达.exe` 17.4 MB 单文件，
    双击起服务 → 自动开浏览器 → 留一个 tkinter 小窗（打开界面 / 更新数据 / 退出）。
    `config.py` 加了 frozen 分支：代码资源走 `_MEIPASS`，**数据与配置落到 `%LOCALAPPDATA%\GameDealRadar`**；
    `app._migrate_install()` 首次运行会从 EXE 同级/上一级的 `data/radar.db` 自动迁移。
    实测：EXE 起来后 246 游戏 / 3182 快照 / 176 条折扣正常，`dist/ build/ *.spec` 已进 `.gitignore`。
21. **小黑盒登录**（`radar/heybox_login.py` + `cli heybox-login`）：起**独立 profile 的 Edge**
    （`data/edge-profile`），用户自己登录，程序经 CDP `Storage.getCookies` 取 xiaoheihe 域 cookie，
    存 `data/heybox_cookies.json`；`heybox.py` 之后所有请求自动带 `Cookie`。
    零依赖：WebSocket 客户端是手写的（不装 selenium / playwright）。支持 `--paste` 兜底。
22. **小黑盒改成「登录才用」**：`heybox.enabled()` 受 `config.heybox_require_login`（默认 true）控制，
    未登录时 `fetch --heybox` 与 `heybox` 命令直接跳过并说明原因，其它源不受影响。
23. **登录判定做过一次纠错**：最初把 `token` 也算进"看起来已登录"，结果匿名 cookie `x_xhh_tokenid`
    让它误判为 true。改成只认 `pkey / heybox_id / user_heybox` 三个名字；未登录首页的
    `lang / smidV2 / .thumbcache_* / x_xhh_tokenid` 都不算。

**仍未做**：小黑盒登录后接口行为还没实测（要用户真登录一次）：登录态是否还需要 hkey 签名、
登录后能否拿到比匿名更多的字段，都要跑过才知道。

24. **重大发现：小黑盒可以「浏览器读页面」取数，不需要签名**（2026-11-12 夜，用户提出的方向，已实测）
    - 商品页 `https://www.xiaoheihe.cn/app/topic/game/pc/<appid>` 的 HTML 是 **3796 字节的 SPA 空壳**
      （`¥` / `price` / `lowest_price` / `__NUXT__` 全无命中）——纯 HTTP 抓不到任何价格。
    - **但用 CDP 打开页面、等 4 秒渲染后，DOM 里什么都有**。实测艾尔登法环（未登录状态）：
      `¥298` 原价、`领券减10`、`￥178.8 史低价格`、`9.6 分`、`6.3万评价`、`93% 全语言好评率`、
      `2.6万 昨日峰值在线`、`440.0万 小黑盒玩家数`、`128.5h 平均游戏时间`。
    - **这条路不需要 hkey 签名**：签名是页面自己的 JS 算的，我们只是「打开页面读内容」，
      性质是「像用户一样看网页」，跟用 Python 伪造客户端请求完全是两件事。这就是用户想要的「登录后从自己账号取」。
    - 探针脚本：`_probe/` 下的 `_probe_page.py`（用 `heybox_login._WS` + `/json/new` + `Runtime.evaluate`）。
    - **下一步（新会话直接干这个）**：新建 `radar/sources/heybox_browser.py`，把「CDP 打开页面 → 等渲染 →
      读 DOM → 正则/选择器提价格」做成采集器，取代 `heybox_sign.py` 那条路。性能：单页约 4 秒，
      246 个约 16 分钟 → 放周一全量；每日只抓「正在打折/关注列表」的那几十个。
      需要把「原价 / 券 / 史低 / 好评率 / 在线」几个字段从 `body.innerText` 里切出来（先用文本+正则，
      稳定后再改选择器）。

## 还没做的（按价值排）

1. **ITAD prices 还没跑过一次**：映射和解析都通了，但 `prices/v3` / `historylow/v1` 必须带 key。
   拿到 key 后：`python -m radar.cli keys --itad <KEY>` → `python -m radar.cli itad --limit 246`。
2. **Steam 库还没导入**：同上，`keys --steam-key <KEY> --steam-id <主页链接>` → `sync-owned --dry-run` 看一眼再真跑。
   愿望单那条可以先跑（免 key，只要 profile 公开）。
3. **Telegram 通道没验过**：代码在，但需要 `telegram_bot_token` + `telegram_chat_id` 才能实测。
4. **候选池还小（246 个）**。要扩就跑 `python -m radar.cli harvest --source all --pages 20`（SteamSpy 全量，慢）。
   注意 `fetch` 现在会并发抓 appdetails，扩池后单轮时间主要花在 SteamSpy 的 tag 上（1 请求/游戏）。


## 别踩的坑（验证过的）

- Steam `appdetails` **不支持批量**，`appids=1,2` 一律 400，只能逐个 0.3s。
- `GetOwnedGames` 无 key → **401**；`community/profiles/<id>/games?tab=all&xml=1` 无登录 → **302 到登录页**，
  免 key 导出游戏库这条路已经废了，别再试。免 key 还活的只有 profile `?xml=1`（ID + 隐私状态）
  和 `IWishlistService/GetWishlist`（profile 公开才给数据）。
- ITAD 无 key 是 **403**（不是 401），字段是 `status_code` / `reason_phrase`；
  `/games/history/low/v2` **不存在**（GET 过去 404，容易误判），史低批量端点是 `/games/historylow/v1`（全小写）；
  `prices/v3` 的 body 是**纯数组**，写成 `{"ids":[...]}` 会被 400。
- ITAD 币种要过滤：`country=CN` 下个别店只报 USD，混着入库会把 `¥9` 当史低。现在不符就丢行并计 `skipped_non_cny`。
- 小黑盒 `/game/get_game_infos/` 参数名是 `appids`；批量不给 `time_info`。
- CheapShark `games?id=` 要它自己的 gameID，传 Steam appid 404。
- Windows 控制台 GBK：`python -c` 打印 ¥ 前先 `$env:PYTHONIOENCODING='utf-8'`。
- 路径含 `&`（`Softwares&Tools`），PowerShell 里必须引号或走 workdir 参数。
- 已 `git commit`（初始版本，remote 指向 `HokleinDelrin/hd-deal-radar`）；**push 前必须先跑
  `open-source-lab/tools/preflight-scan.ps1`**，退出码 0 才发。

## 文档地图

| 想干什么 | 读哪份 |
|---|---|
| 改采集器 | `docs/数据源.md`（20 个源的实测结论 + 小黑盒签名详解 + ITAD 端点口径） |
| 改推荐算法 | `docs/推荐算法.md` |
| 想知道为什么这么定 | `QA.md`（只增不改，每条都有实测依据） |
| 时间线 | `LOG.md` |
| 代码入口 | `radar/cli.py`（所有命令）、`radar/server.py`（API）、`web/app.js`（前端） |
