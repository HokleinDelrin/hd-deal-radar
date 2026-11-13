"""全局配置。密钥一律从 config.local.json 或环境变量读，不写进代码。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 打包成 EXE 后：代码资源在解压目录（只读），数据必须落到用户目录，
# 否则写入会被 UAC/只读目录挡掉，而且升级换 EXE 会把库存一起弄丢。
_FROZEN = bool(getattr(sys, "frozen", False))
_BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

if _FROZEN:
    ROOT = _BUNDLE
    DATA_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "GameDealRadar"
    DATA_DIR = DATA_HOME / "data"
    CACHE_DIR = DATA_HOME / "cache"
    DB_PATH = DATA_DIR / "radar.db"
    CONFIG_LOCAL = DATA_HOME / "config.local.json"
else:
    ROOT = Path(__file__).resolve().parent.parent
    DATA_HOME = ROOT
    DATA_DIR = ROOT / "data"
    CACHE_DIR = ROOT / "cache"
    DB_PATH = DATA_DIR / "radar.db"
    CONFIG_LOCAL = ROOT / "config.local.json"

WEB_DIR = _BUNDLE / "web"
FIXTURE_DIR = _BUNDLE / "fixtures"

DEFAULTS = {
    # 国区价，人民币
    "country": "cn",
    "currency": "CNY",
    "language": "schinese",
    # 采集礼貌值
    "rate_limit_sec": 1.2,
    "timeout_sec": 20,
    # appdetails 的并发线程数。1 = 老行为（串行 0.3s/个）；>1 时每线程各自限速，
    # 总速率≈concurrency/0.3，实测 4 线程抓 30 个无 429（见 docs/数据源.md）。
    "steam_concurrency": 1,
    "user_agent": "game-deal-radar/0.1 (+local personal use)",
    "cache_ttl_sec": 6 * 3600,
    # 服务
    "host": "127.0.0.1",
    "port": 8790,
    # 可选 key
    "itad_api_key": "",
    "steam_api_key": "",
    "steam_id": "",
    # 提醒（telegram 两项留空就只用 Windows 本地通知）
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    # 小黑盒：默认「必须先登录才能用」。它的价格走的是未公开接口 + 签名，
    # 登录态把身份摆正（以自己账号、按自己频率取）；不想用就保持 false，其它源照样工作。
    "heybox_require_login": True,
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_LOCAL.exists():
        try:
            cfg.update(json.loads(CONFIG_LOCAL.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:  # 报错要响，不要静默
            raise SystemExit(f"config.local.json 解析失败: {exc}") from exc
    # 环境变量优先
    for key, env in (
        ("itad_api_key", "ITAD_API_KEY"),
        ("steam_api_key", "STEAM_API_KEY"),
        ("steam_id", "STEAM_ID"),
        ("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
        ("telegram_chat_id", "TELEGRAM_CHAT_ID"),
    ):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    for d in (DATA_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
    return cfg


CFG = load()


def save_local(updates: dict) -> Path:
    """把若干字段并进 config.local.json，保留文件里已有的其它字段。

    只写「用户自己填的东西」——不要在这里落采集统计之类会频繁变化的值。
    """
    data: dict = {}
    if CONFIG_LOCAL.exists():
        try:
            data = json.loads(CONFIG_LOCAL.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data.update({k: v for k, v in updates.items() if v is not None})
    CONFIG_LOCAL.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CFG.update(data)  # 让当前进程立刻用上，不必重启
    return CONFIG_LOCAL

