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

## 踩坑记录（犯一次就写，当场写）

**项目特定的坑写这里，不要往公共区搬。** 判定：换个项目还成立吗？成立 → `../_lessons/`；不成立 → 写这里。

格式固定，一行一条：

```markdown
### <一句话标题>
- 症状：<能被观察到的那种>
- 根因：<为什么会这么发生>
- 下次判据：<当场能执行的一句话，不是"要小心">
- 出处：<文件:行 或 提交>
```

写不出「下次判据」的说明还没想清楚，不收。

**同一个问题修到第三次**，改用 `../_lessons/07-重复失败.md` 的模板：先写「前三次各改了什么、是不是同一个假设、缺什么观测」，再动手。

> 2026-11-14 之前的坑已由 `_lessons` 统一归档，本节从 2026-11-14 起生效。

## 没修好的问题（2026-11-14 由总控会话统一归档）

### 通知到底弹没弹：三轮，前两轮的检测方式本身是错的
- 1：PowerShell 直调 WinRT，只检查退出码 0 → 报「发送成功」
- 2：查 `History.GetHistory()` 发现确实有 1 条 → 判定「自造 AUMID 不弹横幅」，换系统已注册的 AUMID
- 3：改成「攒着、每天 21:30 汇总」→ `notify --test` 改为比对 History 条数前后差
- **缺的观测**：第 1 轮只有退出码

### 小黑盒取价：换三条路线
- 1：复刻 web 端签名 → 可用但判定有踩线嫌疑
- 2：用户登录 + CDP 读 cookie → 未登录时跳过，仍要签名
- 3：CDP 打开商品页等 4 秒读 DOM → 一次拿全，不需要签名
- **缺的观测**：前两条只测 HTML 与 API，没测「渲染后的 DOM 里有什么」