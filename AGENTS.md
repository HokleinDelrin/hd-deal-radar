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
- **改了 `app.py` / 任何启动路径上的东西，必须重新打包 `dist\史低雷达.exe` 并实测那个 exe** ——
  用户双击的是 exe，不是 `.py`。只跑 `python app.py` 不算交付（见下面踩坑区第一条）。

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

### 改了源码没重新打包 = 用户什么都没看到
- 症状：用户双击 `dist\史低雷达.exe`，反馈「问题一个都没修，还是一进去弹小窗」；而 `python app.py` 明明已经没窗口了
- 根因：交付物是 exe，源码只是原料。`dist/` 在 `.gitignore` 里，打包不是自动的，改完不打包，exe 的 mtime 还停在三天前
- 下次判据：只要动了 `app.py` / `radar/` 里启动路径上的模块，收尾前必须
  `python -m PyInstaller --noconfirm --clean "史低雷达.spec"`，然后**启动 dist 里那个 exe** 看四件事
  （无窗口 / 网页自动开 / 自动开始采集 / 通知区有 ◎），回报里写明「跑过 dist 的 exe，mtime=…」
- 出处：2026-11-15 第一次交付只改了源码，`dist\史低雷达.exe` 仍是 11-12 的旧包

### 打包版是窗口模式，没有 stdout —— 只能从外部观测
- 症状：exe 起来后看不到任何日志，`print` 全部无声，怀疑「到底跑没跑」
- 根因：spec 里 `console=False`，`sys.stdout` 是 `None`，`print` 变成空操作（`cli.main` 的 reconfigure 也被 `hasattr` 挡掉了）
- 下次判据：验收 exe 只认外部可观测的三样：`GET /api/update` 的 `running/last_at`、
  `EnumWindows` 里有没有 `GameDealRadarTray_*` 隐藏窗口类（有 = 托盘 `run()` 走到了 `_create()`，
  且 `NIM_ADD` 失败会抛异常把进程带走）、`dist` 那侧 DB 的 mtime。别指望日志
- 出处：`_probe/exe_probe.ps1`（2026-11-15）

### Updater 在 Lock 里调 status() → 第二次点「立即更新」把线程锁死
- 症状：`POST /api/update` 第二次请求永不返回，其它接口正常；采集明明跑完了 `meta.fetch_state` 却不更新
- 根因：`with self._lock:` 里调了 `self.status()`，而 `status()` 内部也要拿同一把 `threading.Lock`（非重入）
- 下次判据：写完 `with self._lock` 后 grep 一遍块内每个方法调用，凡是自己也会 `with self._lock` 的一律挪到块外
- 出处：`radar/updater.py` `start()`（2026-11-15）

### 托盘/窗口 API 的参数不声明 argtypes，64 位句柄会被截断
- 症状：ctypes 调用返回 0，但 `ctypes.get_last_error()` 也是 0，什么都看不出来
- 根因：不设 `argtypes` 时 Python int 按 `c_int` 传，HWND/HICON 的高 32 位丢失
- 下次判据：每加一个 user32/shell32 函数，先写 `argtypes/restype` 再调；句柄一律走 `wintypes.*`
- 出处：`radar/tray.py` 顶部那一串 argtypes（2026-11-15）

### 验收「双击会开浏览器」时，别真的把浏览器开起来
- 症状：为验证 `app.py` 双击路径，浏览器窗口直接盖住用户正在跑的全屏游戏
- 根因：`webbrowser.open()` 会真的拉起默认浏览器，没人能保证桌面此刻是空的
- 下次判据：`$env:BROWSER='python X:/path/fake_browser.py %s'`（**必须正斜杠**：GenericBrowser 用
  shlex 切命令，反斜杠会被当转义吃掉、路径拼成一坨），让它走假命令，只断言「调用了、URL 正确」
- 出处：`_probe/fake_browser.py`（2026-11-15）

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