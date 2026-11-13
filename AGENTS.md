# game-deal-radar — 文档路由表

> 全局规则见 `~/.dsh/AGENTS.md`（每个会话自动加载，此处不重复）。
> 工作区规则见 `../AGENTS.md` —— **不会自动继承**，需要时自己去读。
> 建立：2026-11-09

## 这个项目是干什么的

跨平台 PC 游戏史低价格聚合 + 基于游玩画像的游戏推荐的本地应用。

## 按场景取文档

| 什么时候 | 读哪份 |
|---|---|
| 当前状态 / 待办 | `README.md` |
| 这件事为什么这么定 | `QA.md` |
| 发生过什么（时间线） | `LOG.md` |
| 数据源实测结论、端点与限速 | `docs/数据源.md` |
| 推荐算法怎么算的 | `docs/推荐算法.md` |

## 每次动手前必做

- 跑采集前先看 `docs/数据源.md` 里该源的状态，别对着已失效的端点写代码。
- 改 `radar/` 下的采集器后，至少跑一次 `python -m radar.cli selftest`，它会用离线快照断言解析逻辑。
- 任何新源都必须带 `cache/` 落盘，跑一次就有原始响应可复查；不允许只在内存里拼。
- 不要在代码里写死 API key，一律从 `config.local.json` 或环境变量读。
- **Windows 控制台是 GBK**：`python -m radar.cli` 已在 `main()` 里强制 stdout/stderr 转 UTF-8，
  但 `python -c "..."` 不经过这条路径，打印 ¥ 会 UnicodeEncodeError。跑 inline 脚本前先
  `$env:PYTHONIOENCODING='utf-8'`。
- 路径里的 `&`（`Softwares&Tools`）在 PowerShell 里必须引号包住，或者直接用工具的 workdir 参数。

## 已验证的接口形态（别再去踩）

- Steam `appdetails` **不支持批量**：`appids=1,2` 一律 400，只能逐个来。
- 小黑盒 `/game/get_game_infos/` 的参数名是 **`appids`**（写 `steam_appids` 会报缺失）；
  到手价 `final_price` 嵌在 `heybox_price.coupon_info` 里，不在顶层。
- CheapShark 的 `games?id=` 要它自己的 gameID，传 Steam appid 会 404。

## 不存在，别去找

- 本项目没有前端构建链（无 node_modules / 无打包），UI 是 `web/` 下的静态文件，由 Python 直接吐出。
- 本项目不是 Flask/Django 应用，Web 服务用的是标准库 `http.server`，别去找 `app.route`。
- 小黑盒**没有可用的折扣列表接口**（`/game/sale_list/`、`/game/deal_list/`、`/mall/game/list/` 等全 404），
  候选池从 Steam 特惠 + CheapShark 出，别去找"小黑盒特惠页"。
