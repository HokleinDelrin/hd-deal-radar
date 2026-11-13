# LOG — game-deal-radar

时间线（**只增不改**）。当前状态与待办见 `README.md`；决策理由见 `QA.md`。

---

## 当前状态
> 下次开工先看这段。

骨架已建（git init + 四件套 + `radar/`/`web/`/`docs/`），数据源实测与采集器正在落地。

## 2026-11-09

- 用户提出想法：跨平台史低聚合 + 基于玩过/喜欢游戏的推荐，两件事一起做。
- 在 项目目录 `game-deal-radar` 建项目目录，`git init`，从 `_template` 复制四件套并按本项目改写。
- 技术栈定为 Python 3.11 + requests + 标准库 `http.server` + SQLite；前端原生 JS 无构建（本机无 pnpm/flask，见 `QA.md`）。
- 起两个调研子代理：数据源实测（Steam/CheapShark/Epic/ITAD/SteamSpy/国内站）、画像与推荐方案（Steam API 元数据 + tag 向量 + 开源参考）。
- 决策：史低以自存价格快照为准，外部史低值仅作对照列（见 `QA.md` 2026-11-09 第一条）。
- 两个调研子代理回收：数据源实测结论 → `docs/数据源.md`；画像算法方案 → `docs/推荐算法.md`。
- 实测踩坑并修正：Steam `appids=1,2` 批量一律 400（网上说法已失效）→ 改串行 0.3s；CheapShark 的 `id` 不是 Steam appid → 改 `steamAppID` 两跳；SteamSpy 榜单接口不含 tags → tag 只能逐游戏 appdetails 补。
- 代码落地：`radar/{config,http,db,tags,recommend,server,cli}.py` + `sources/{steam,steamspy,cheapshark,epic,itad,steamlib}.py` + `web/{index.html,app.js,style.css}`。
- `selftest` 12 项断言全过，含「不要恐怖」这类否定识别。
- 真实数据跑通：SteamSpy 热门榜 100 个 + Steam 特惠 22 个入场；`enrich` 补 tag 110 个成功 100；画像 4 条 → 推荐出 Rust / The Forest / Don't Starve Together / Raft / Valheim 等，与「Terraria + Project Zomboid + Portal」的口味吻合。
- 修：只有 1 条快照时不再标「史低」（改显示「首采」），见 `QA.md`。
- 修：Windows 控制台 GBK 打 ¥ 会 UnicodeEncodeError，`cli.main` 里强制 stdout/stderr 转 UTF-8。
- 加币种切换：Steam 走国区 CNY、CheapShark 走 USD，币种分开存分开看，不做硬换算。
- 最终数据状态：181 个游戏 / 511 条价格快照 / 143 次采集 / 156 个带 tag 可参与画像 / 74 个有国区价。推荐已能排出 Rust、The Forest、Don't Starve Together、Valheim、Fallout 4 这类与画像吻合的结果。
- 项目体检 `project-hygiene` 乱度 0；`cache/` 300 个原始响应已进 .gitignore。

## 2026-11-09（第三轮）

- 用户提出四点：采集频率要不要改成每周、ITAD 是什么、券快过期提醒、小黑盒价进推荐折扣项、默认排序怎么定。
- **采集频率**：不降频，改根因 —— `db.add_price` 同价不写行（cents/list_cents/discount 三者全同才跳过），并刷新 `games.price_checked_at`。同时把调度分层：周一全量（harvest+fetch+enrich+heybox_all），其余日子轻量（fetch_light + heybox_all + heybox_deadlines）。理由记在 QA。
- **默认排序**：新增 `worth = 0.45×贝叶斯好评率 + 0.30×有效折扣 + 0.25×史低信号`，设为默认。实测三档差别极大：纯按好评率出传送门/星露谷/泰拉瑞亚（全 -0% 原价），纯按折扣出战锤混沌祸根（-95% 但好评 57.7%），混合后第一屏是 Dying Light / 红弦俱乐部 / 英雄连。
- 新增 `review_score` / `review_count` 两列，enrich 改用 `steamspy.enrich_appid` 一次同时落 tag 与口碑（217/246 拿到）。
- 新增 `heybox_deadline` 列与 `heybox --deadlines` 命令：**批量接口不给 `time_info`，只有单查 `get_game_detail` 才有**，所以单独一个限流 job（每日 80 个）。已补 84 个券截止时间，界面显示「券剩 N 天」，≤3 天转黄。
- 推荐排序的折扣项改成「有效折扣」= (原价 − 实付)/原价，这样小黑盒 82% 的力度会进排序（之前只看 Steam 那档，FF7RE 会被当成 -0%）。
- `heybox --force` 用于给已有价的重刷新字段。
- 当前库：246 个游戏 / 3182 条快照 / 250 次采集 / 217 个有 tag 与口碑 / 84 个有券倒计时。

## 2026-11-09（第二轮）

- 用户指出小黑盒是关键源：《最终幻想7 重制版》小黑盒 32 元，比 Steam 低 18 元。
- 重新实测小黑盒：网页是 3.8 KB SPA 空壳、无内嵌数据；API 无签名报 `缺少必要参数[_time]`、假签名报 `非法请求` → 必须实现签名。
- 查社区逆向资料，在 `radar/heybox_sign.py` 复刻 web 端 `hkey` 算法（三层字符表映射 + 交错拼接 + MD5 + 列混淆校验位）。关键细节：JS 里 `lv[i]=(e,t,n)=>ov(e,t+3,n)`，算签名用 `_time+3`。
- 实测签名生效：`/game/get_game_detail/?steam_appid=1462040` → 200，拿到 Steam 198 / Steam 史低 49.5 / 小黑盒券后 33（-82%），与用户看到的 32 元吻合。
- 找到批量接口 `/game/get_game_infos/?appids=1,2,3`（参数名必须是 `appids`，写 `steam_appids` 报缺失）。折扣列表接口试了 4 个候选全 404 → 候选池仍从 Steam 特惠出。
- 修一个解析坑：`final_price` 嵌在 `heybox_price.coupon_info` 里，不在 `heybox_price` 顶层，一开始全取成 None。
- `db` 加两列 `steam_lowest_cents` / `heybox_cents`（带迁移），新增 `set_game_external`。
- 全库补价：216 个游戏分 11 批，215 个成功；126 个拿到小黑盒价，170 个拿到 Steam 史低。
- 界面：卡片显示「小黑盒 ¥x」与「Steam 史低 ¥y」，加「按小黑盒价」排序，详情抽屉加小黑盒比价链接。
- 当前库：242 个游戏 / 1421 条价格快照 / 214 次采集。
- 加「破史低」标记：现价（含小黑盒）低于 Steam 官方史低时高亮 —— 这才是真正该出手的时候。
- 每日自动采集落地：`radar/scheduler.py`（纯标准库，分片 sleep 到点、单 job 异常只写日志不停机）+ `cli daemon --at/--once/--rounds` + `install-task.ps1`（-DryRun/-Unregister，任务名 game-deal-radar-daily）。实跑 `daemon --once` → 3/3 ok，`data/scheduler.log` 有记录。踩到一个并发坑并修掉：`cmd_fetch` 新增 `--heybox` 参数后，scheduler 里手写 `argparse.Namespace` 会 AttributeError 把整轮 fetch 跳过，改成复用 `cli.main([...])` 让 argparse 自己补默认值。

## 2026-11-12（ITAD 批量接入 + Steam 库导入收尾）

- 用户要求：继续做 ITAD key 接入或 Steam 个人库导入，并评估项目还能优化什么。
- 先复查两条路的前置条件，实测结论推翻了文档里的旧假设：
  - `community/profiles/<id>/games?tab=all&xml=1` 未登录 **302 到登录页**，免 key 导出游戏库这条路已废；
  - `GetOwnedGames` 无 key **401**；
  - 但 `community/profiles|id/<x>/?xml=1` 仍 200，含 `steamID64` 与 `privacyState` → 可以免 key 做「填 ID 时先验证 + 告知 profile 够不够公开」；
  - ITAD 无 key **403**（不是 401），字段 `status_code`/`reason_phrase`。
- 起子代理核对 ITAD 官方 OpenAPI，拿到三条关键修正：`prices/v3` 的 body 是**纯 UUID 数组**（≤200）、
  史低端点是 `POST /games/historylow/v1`（`/games/history/low/v2` 不存在）、
  `POST /lookup/id/shop/61/v1` 批量映射**匿名可调**。额度 1000 请求/5 分钟。
- 重写 `radar/sources/itad.py`：批量 prices + 批量 historylow + 免 key 批量映射 + 429 退避 + 币种过滤（非 CNY 丢行并计数）+ `meta.itad_map` / `meta.itad_low`。`http.py` 补 `post_json`（POST 也要落盘缓存，否则无法复查）。
- **实测免 key 建映射**：246 个 appid 分两批、两次请求全命中；appid 220 → `018d937f-012f-73b8-ab2c-898516969e6a`，与官方样例一致。等 key 到手只剩 `itad --limit 246` 一步。
- 重写 `radar/sources/steamlib.py`：`parse_ref`/`profile_info`（免 key 预检）、`owned_games`、`wishlist`、`verify_key`、`import_to_profile(--dry-run/--force/去重)`、`import_wishlist`（→ watch）。修掉尾部重复 `from typing import Any`。
- 新增命令：`keys`（写入并在线校验三项，失败直说原因）、`itad`（批量补价，`--map-only` 免 key）、`cache-gc`（cache 837 个文件只报告，`--apply` 才删）。`sync-owned` 扩到 `--id/--dry-run/--wishlist/--force`。
- 调度：`itad_refresh`（每日 200）/ `itad_refresh_force`（周一 400 --force），没 key 返回 0 跳过。
- `server.py` 的 `/api/game` 加 `itad_lowest` 字段（读 `meta.itad_low`，与 games 表那两个 Steam 专用列分开）。
- `selftest` 14 → 23 项：Steam 引用解析 5 项、ITAD 现价解析 3 项、ITAD 史低解析 2 项，全部读 `fixtures/*.json`，不联网。新增 `fixtures/itad_prices_v3.json`、`fixtures/itad_historylow_v1.json`。
- 冒烟：`serve` + `/api/game?appid=1245620` 正常（艾尔登法环 ¥288，Steam 史低 ¥178.8，ITAD 史低待 key）。
- 卡点：prices / historylow / GetOwnedGames 三项都要用户自己的 key，无法代做。已在 README/HANDOVER 写清「一条命令接 key」。

## 2026-11-12（第二轮：推送 + 工程优化 + 修 tag 被抹的 bug）

- 用户拍板：推送做；工程四项做；ITAD 的定位要按「实际买不买得到」重新想（结论：只当史低裁判，不做购买入口）。
- 新增 `radar/notify.py`：Windows 通知（PowerShell 5.1 直调 WinRT，不装 BurntToast）+ Telegram + `--dry-run`。
  实测弹窗成功；第二次跑正确判定「没有新的」→ 去重生效。
- 抽 `recommend.deal_view()`：推荐、推送、详情共用一套价格/史低口径。`itad_low_map()` 让 ITAD 官方史低
  成为「裁判」——现价低于它就把状态升到 `new`。
- MMR：`rec --diversity 0.3` / `/api/rec?div=0.3`，实测把「腐蚀」从第 2 位压到第 5 位。
- **发现并修掉真 bug**：`upsert_game` 的 `tags=excluded.tags` 让每天的 fetch 把 SteamSpy tag 抹成 NULL
  （`review_score` 217 行 vs `tags` 1 行），推荐退化成 1 条结果。软字段改 COALESCE + 回归断言 + `enrich` 补回 219 个。
- 工程：`recommend.load_index()` 按 db mtime 缓存（CLI/Web 共用，修掉 server 漏掉 set_game_tags 的失效条件）、
  `appdetails` 并发（30 请求 41.1s → 12.0s，限速计时器改 thread-local）、`prices` 复合索引。
- `selftest` 23 → 29 项：软字段保留、MMR 两条、Row/dict 双兼容、ITAD 史低升级、索引缓存。
- 冒烟：`/api/rec?div=` 生效，`/api/game` 正常，`notify` 实弹成功。

## 2026-11-12（第三轮：通知改成全局汇总 + 网页提醒中心）

- 用户反馈：通知收到了，但**不应该让每个应用随时弹**；希望一天定个时间（21:00/21:30/22:00）集中看一遍。
- 诊断「为什么之前没看到」：`History.GetHistory()` 里确实有 1 条记录 —— 说明通知进了系统，但**自造 AUMID 在 Win10 不弹横幅**。
  换成 PowerShell 已注册的 AUMID 后用户确认收到。上一轮的「检测」只看退出码，是不够的，已改成对比 History 条数。
- 新建全局工具 `~/.dsh/tools/notify-hub.py`：push/flush/list/install/uninstall/where。
  队列 `~/.dsh/notify/queue.jsonl`（只追加），flush 用「改名再处理」保证与 push 并发不丢消息；
  投递过进 `history.jsonl`（留 500 条），明细页 `~/.dsh/notify/today.html` 按应用分组。
- 注册计划任务 `dsh-notify-digest`：每天 **21:30** 汇总投递（`install --at` 可改）。
- 本项目 `notify` 默认改为「写 `notifications` 表 + 入队」，不再当场弹；`--now` 才立刻弹。
- 网页提醒中心：`/api/notifications`、`POST /api/notifications/seen`、`web/bell.js`（右上角「提醒」+ 未读红点，样式自包含）。
- 前端踩坑：铃铛一开始放进 `<nav class="tabs">`，被 app.js 当成页签，点一下主体高度从 9597 塌到 98（截图复现），挪出 nav 修好。
- 截图验证：右上角铃铛（未读 2）、点开面板、主体高度 9597（修复后），三张截图留在 browser-ops 工作区，未进项目仓库。
- 测试数据已清理（`notifications` 清空，hub 队列为空）。

## 2026-11-12（第四轮：打包 EXE + 小黑盒登录）

- 用户提出两点：① 做成应用给 EXE；② 小黑盒那套「自实现签名 + 匿名请求」有踩线嫌疑，改成「先登录再取」。
- EXE：写 `app.py`（tkinter 小窗 + 后台服务 + 自动开浏览器 + 「更新数据」按钮），
  PyInstaller `--onefile --noconsole` 打包，17.4 MB。`config.py` 加 frozen 分支把数据落到 `%LOCALAPPDATA%`；
  `_migrate_install()` 首次运行自动搬老库。实测 246 游戏 / 3182 快照 / 176 条折扣正常。
- 登录：`radar/heybox_login.py`，起独立 profile 的 Edge（`data/edge-profile`），经 CDP `Storage.getCookies`
  取 cookie；WebSocket 客户端手写（零依赖）。`heybox.py` 所有请求自动带 Cookie。
- 定性纠错：一开始把 `token` 当登录标志，匿名 cookie `x_xhh_tokenid` 直接误判成已登录；
  收紧为只认 `pkey / heybox_id / user_heybox`。实测当前 profile 只有 `lang / smidV2 / .thumbcache_* / x_xhh_tokenid`，
  判定为**未登录**，符合实际。
- 门禁：`heybox_require_login` 默认 true，未登录时 `heybox` / `fetch --heybox` 直接跳过并说明原因；
  其它源（Steam / ITAD / CheapShark / Epic）不受影响。
- 待办：用户真登录一次后，验证「登录态是否还需要签名」「登录后字段是否有增加」。

## 2026-11-12（第五轮：小黑盒改用「浏览器读页面」，实测成功）

- 用户指出：「我登录后本来就能在小黑盒看到价格，你直接接官方、从我的账号里取不就行了」——方向正确。
- 实测一：商品页 `app/topic/game/pc/1245620` 与 `games/detail/1245620` 的 HTML 都是 **3796 字节 SPA 空壳**，
  `¥` / `price` / `lowest_price` / `__NUXT__` 全无命中 → 纯 HTTP 拿不到价格。
- 实测二：**用 CDP 打开页面等 4 秒渲染，DOM 里数据齐全**（艾尔登法环）：`¥298` 原价、`领券减10`、
  `￥178.8 史低价格`、`9.6 分`、`6.3万评价`、`93% 全语言好评率`、`2.6万 昨日峰值在线`、
  `440.0万 小黑盒玩家数`、`128.5h 平均游戏时间`。
- 结论：**这条路不需要 hkey 签名** —— 签名由页面自己的 JS 生成，我们只是打开页面读内容。
  性质是「像用户一样看网页」，与「Python 伪造客户端请求」是两件事，正好对上用户的诉求。
- 探针归档到 `_probe/_probe_page.py`；下一步把它做成 `radar/sources/heybox_browser.py` 替换签名路径
  （单页约 4 秒，246 个约 16 分钟 → 周一全量；每日只抓打折/关注的那几十个）。
- 交接：HANDOVER 第 24 条已写清实测数据与下一步；README 顶部仍指向 `HANDOVER.md`。


