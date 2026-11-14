# Release Notes · v1.0.0

> 发布日期：2026-11-14
> 说明方式：按「你能感知到的东西」写，不按 commit 写。每条后面跟一个当场能复现的验证方式。
> 本文所有数字均来自 2026-11-14 本机实测，命令与输出摘要见每节末尾；没有实测过的数字不写。

---

## 这是个什么东西

一个跑在你自己电脑上的本地应用：盯住一批 PC 游戏，回答两个问题 ——
**「这个游戏现在是史低吗，该今天买吗」** 和 **「按我玩过的游戏，现在打折的里有什么值得买」**。

数据存在你本机的一个 SQLite 文件里，不上传，不需要注册账号。

---

## 1. 打开就有一个填好数据的界面，不是空壳

第一次跑完采集后，界面直接是满的：**246 个游戏、3182 条价格记录、250 条史低快照**。

- 四个页签：史低榜 / 我的口味 / 推荐 / 免费
- 默认排序是「值得买」：贝叶斯好评率 45% + 有效折扣 30% + 史低信号 25%
- 点任一游戏开详情抽屉：价格曲线、各店比价、相似游戏

**验证**：`python -m radar.cli serve` 后打开 `http://127.0.0.1:8790`，
或直接打接口 `curl http://127.0.0.1:8790/api/stats`。

> 实测（2026-11-14）：`/api/stats` 返回
> `{"games": 246, "prices": 3182, "profile": 4, "watch": 0, "snapshots": 250}`
> 首页 `GET /` → HTTP 200，4443 字节 HTML。

---

## 2. 算的不是「折扣百分比」，是「券后到手价」和「破史低」

多数比价站告诉你「-75%」。这个项目多算两件国内玩家真正在意的事：

- **小黑盒券后到手价**：把券抵扣算完之后的实际付款额，作为独立商店进价格表，跟 Steam 国区价直接比。
- **「超史低」标记**：只有现价**低于 Steam 官方历史最低价**时才高亮。其余老实标「史低」/「平史低」/「首采」。

**验证**：详情接口 `GET /api/game?appid=1462040` 里 `itad_lowest` 字段与卡片状态用的是同一个函数
`recommend.deal_view()`，所以网页和命令行不会各说各话。

> 这条的自检覆盖：**#26 `deal_view` 能吃 sqlite3.Row**、**#27 现价低于 ITAD 官方史低 → 判新低**。

---

## 3. 历史最低是本地自建的，第一天不会骗你

Steam 不提供历史价格。所以第一天跑完，库里「当前价 == 最低价」——这时界面标 **`首采`**，不标「史低」。
跑满两轮采集之后才开始有真实的历史最低。

**验证**：`python -m radar.cli low <appid>` 看单个游戏的价格序列。

> 自检覆盖：**#20 同价不重复写快照**（`wrote=[True, False, False] rows=1`）、
> **#21 降价会写新快照且史低跟着降**（`lowest=800 rows=2`）。

---

## 4. 按你玩过的游戏推荐正在打折的，不是「猜你喜欢」

给一句人话就行：`python -m radar.cli add "只狼" --why "喜欢招架手感，不要迷路"`。
它会解析成 tag 向量，再和库里正在打折的游戏算相似度。

- 只说名字和「名字 + 说明喜欢什么」两种粒度都支持
- 否认识别：「不要恐怖」不会把恐怖类推进正向
- 已拥有的自动排除，折扣参与加权，每条推荐带「为什么推它」

**验证**：`python -m radar.cli profile` 看你的话被解释成了哪些 tag；`python -m radar.cli rec --top 20` 看结果。

> 自检覆盖 14 项：**#1–#9**（中文/英文描述→tag、否定分离、索引与非空向量、画像向量归一）、
> **#10–#13**（steamID64 / profiles 链接 / vanity 链接 / 裸自定义名四种写法都能解析）、
> **#14 玩时长权重：单调且封顶**。

---

## 5. 28 项离线自检：不联网、不碰你的库，一条命令知道有没有坏

```powershell
python -m radar.cli selftest
```

不联网、不写你的数据库（测完自动清理），覆盖 tag 解析 / 画像 / 去重 / 史低判定 / ITAD 与 Steam 响应解析。

**实测输出（2026-11-14）**：28 行 `PASS`，末行 `[selftest] 全部通过`，退出码 `0`。

| 组 | 自检项 |
|---|---|
| tag 与画像（9 项） | #1 中文描述→tag 命中 4 个 · #2 否定不进正向 · #3 否定进负向 · #4 否定词不丢信息 · #5 英文描述→tag · #6 索引建立 · #7 文本向量非空 · #8 名字解析到游戏 · #9 画像向量归一 |
| Steam 引用解析（4 项） | #10 steamID64 直认 · #11 profiles 链接 · #12 vanity 链接 · #13 裸自定义名 |
| 权重（1 项） | #14 玩时长权重单调且封顶 |
| ITAD 响应解析（5 项） | #15 多店价解析 · #16 非 CNY 行丢弃 · #17 金额节点容错 · #18 官方史低解析 · #19 史低空值不报错 |
| 快照与去重（3 项） | #20 同价不重复写 · #21 降价写新快照且史低跟降 · #22 测试数据清理 |
| 数据完整性（1 项） | #23 不带 tags 的 upsert 保留已有 tag |
| 推荐与排序（3 项） | #24 MMR 压重复 · #25 MMR 不重排保持原序 · #26 deal_view 吃 Row |
| 史低判定与缓存（2 项） | #27 现价低于官方史低判新低 · #28 索引命中缓存 |

> 这 28 项里 #15–#19 读 `fixtures/*.json` 离线快照，其余为内联构造；全程不发一个网络请求。

---

## 6. 8 个数据源的实测结论写成了文档，包括「哪个已经死了」

`docs/数据源.md`（128 行）不是 API 罗列，是逐条实测记录：端点、要不要 key、限速、能不能给史低、以及**踩过的坑**。

覆盖：Steam Store / Steam storesearch / SteamSpy / CheapShark / Epic / ITAD / 小黑盒 / Steam profile XML，
外加 12 个**测过但放弃**的源（SteamDB、GG.deals、NGA、淘宝、AllKeyShop…）及放弃原因。

几个能省你半天时间的结论：

- Steam `appdetails` **不支持批量**，`appids=1,2` 一律 400，只能逐个来
- ITAD 无 key 是 **403 不是 401**；`/games/history/low/v2` 用 GET 打是 404 —— 它要 POST，别把 404 当成「端点不存在」（全小写 `historylow/v1` 才对）
- CheapShark 的 `games?id=` 要它自己的 gameID，传 Steam appid 会 404
- 小黑盒到手价 `final_price` 嵌在 `heybox_price.coupon_info` **里面**，不在顶层
- SteamSpy `average_forever` 已全是 0，别用
- 免 key 导出 Steam 游戏库这条路已经死了（302 跳登录页）

**验证**：直接读 `docs/数据源.md`，每条都写了实测方式。

---

## 7. 可选增强：不填 key 也能跑，填了更准

| 增强 | 需要什么 | 没有会怎样 |
|---|---|---|
| ITAD 多店价 + 官方史低 | 免费 key（https://isthereanydeal.com/apps/my/） | 自动跳过，本地快照继续算 |
| Steam 库导入画像 | Steam API key + profile 公开 | 手动 `add` 一样能建画像 |
| Telegram 提醒 | bot token + chat_id | 只用网页提醒中心 |
| 小黑盒券后价 | **登录你自己的小黑盒账号** | 跳过该源；其余价格源（Steam 特惠 / CheapShark / Epic）照常 |

**验证**：`python -m radar.cli keys --check` 只校验不写入，三项分别报 OK/FAIL 并说明原因。

---

## 8. 已知限制（写清楚，不藏）

- **候选池 246 个游戏，不是全网**。定位是「盯住你在乎的那批」，不是「搜什么都有」。
- **Windows 优先**：小黑盒登录走 Edge 的 CDP、通知走 Windows Toast，这两块换系统不可用。
  采集、推荐、Web UI 本身是跨平台的。
- **ITAD 这条链路本机没有 key，未做端到端实跑**。解析逻辑有 5 项离线自检覆盖，但真实响应只验证过 fixtures。
- **桌面版 EXE 不在仓库里**（`dist/` 已 gitignore）。要桌面版就自己打包，命令在 README。
- **提醒的「定点汇总」那一步依赖 `~/.dsh/tools/notify-hub.py`**（作者本机的多应用汇总脚本，不在本仓库）。
  没有它时提醒照样写进网页面板，只是入队跳过并打一行提示。

---

## 9. 一条命令确认你能跑起来

```powershell
git clone https://github.com/HokleinDelrin/hd-deal-radar.git
cd hd-deal-radar
pip install -r requirements.txt      # 只有 requests 一个包
python -m radar.cli selftest         # 期望：28 行 PASS + "[selftest] 全部通过"
python -m radar.cli init
python -m radar.cli fetch --pages 1
python -m radar.cli serve            # → http://127.0.0.1:8790
```

> 实测环境：Windows 10 专业版 / Python 3.11.9 / requests 2.32.3，2026-11-14。
> 依赖声明：`radar/http.py` 顶层 `import requests`，**没有它第一条命令就 ImportError** ——
> 这是本版修正的一处文档错误（此前 README 写「零第三方依赖」）。

---

MIT License。
