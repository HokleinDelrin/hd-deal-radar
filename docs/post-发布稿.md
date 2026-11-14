# Steam 挂 198、券后 33、官方史低 49.5 —— 到底哪个才算「史低」？

每次打折季我都在同一个问题上耗半小时：这游戏现在是真的史低，还是假打折？

SteamDB 给的是「相对原价的折扣百分比」，可我在国内，实际要付的是小黑盒领券后的到手价。这两件事经常不一致。11 月 9 日那天我测到：《最终幻想7 重制版》Steam 挂 198、官方史低 49.5、小黑盒券后 33。按百分比是 -75%；按我实际掏的钱，只剩标价的六分之一。差的这一截才决定买不买。（券有期限，这三个数只代表那天。）

所以我做了个跑在本地的小应用，只回答两件事：这游戏现在是不是史低；按我玩过的游戏，现在打折的里有什么值得买。数据躺在本机一个 SQLite 里，不上传、不注册。

数据源 8 个：Steam 现价、小黑盒券后价、CheapShark 第三方店、Epic 免费、ITAD（可选）、SteamSpy（画像 tag）、Steam storesearch、Steam profile XML。

小黑盒那档最有信息量：一个接口同时给 Steam 官方史低和券后到手价，不用申请 key。代价是它没有公开文档——签名是我照社区逆向出来的实现用 Python 等价复刻的（`radar/heybox_sign.py`，里面写了算法来源），所以只在你登录自己账号之后才取，频次也由你自己承担。不登录就整源跳过，其余照跑。这是把风险放回使用者身上，不是把它变合规；真正干净的做法只有一条：不用这个源——所以关掉它，采集、推荐、界面全都正常，只是少一档国内到手价。

真正省你时间的是踩坑记录。`docs/数据源.md` 128 行全是实测结论：

- Steam `appdetails` 不支持批量，传两个 appid 直接 400；
- ITAD 无 key 是 403 不是 401，史低批量端点要全小写的 `historylow/v1`——我一开始拿 GET 去打 `/games/history/low/v2`，收到 404 就记成「端点不存在」，其实它只收 POST，是方法用错了；
- CheapShark 的 `games?id=` 要它自己的 gameID，传 Steam appid 会 404；
- 小黑盒的到手价 `final_price` 嵌在 `coupon_info` 里，不在顶层。

还记了 12 个测过就放弃的源——SteamDB 直接 IP 级 ban、GG.deals 403、NGA 403——省得你重走。

另外 28 项离线自检，不联网、不写你的库，一条命令就知道有没有坏。

跑起来（Python 3.10+，依赖只有一个 `requests`）：

```powershell
git clone https://github.com/HokleinDelrin/hd-deal-radar.git && cd hd-deal-radar
pip install -r requirements.txt
python -m radar.cli init
python -m radar.cli fetch --pages 1
python -m radar.cli serve        # 然后开 http://127.0.0.1:8790
```

不填任何 API key 也能跑起来（ITAD / Steam 的 key 都是可选增强）。

已知限制：

- 候选池是我自己盯的那批，不是全网。我这台机器上 246 个；你 clone 下来第一次大约 60 个，要 `harvest` 或多跑几轮才铺满。
- 小黑盒源要登录你自己的账号，不登录就少一档国内到手价。
- Windows 优先，其它平台没测过。

仓库：https://github.com/HokleinDelrin/hd-deal-radar
