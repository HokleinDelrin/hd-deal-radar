"""tag 词表与「自然语言 → tag」映射。

两层：
1. TAG_ALIASES —— 口语/中文说法 → 一个或多个 Steam tag。用户说「魂类」「肉鸽」「银河城」
   都要能落到 SteamSpy 的原生 tag 上，否则画像和候选不在同一个空间里。
2. 提取器 —— 没有分词器依赖：中文按 2~4 字窗口滑，英文按单词切，然后对着别名表做子串/整词命中。

未命中的关键词不丢掉，作为 unmatched 返回，界面会显示「这几个词我没听懂」，方便继续补表。
"""
from __future__ import annotations

import re
from typing import Iterable

# ---------- 别名表 ----------
# key: 归一化后的说法（小写）；value: Steam tag 列表（按可信度从高到低）
TAG_ALIASES: dict[str, list[str]] = {
    # 大类
    "魂类": ["Souls-like"],
    "魂系": ["Souls-like"],
    "魂like": ["Souls-like"],
    "souls": ["Souls-like"],
    "soulslike": ["Souls-like"],
    "soulslikes": ["Souls-like"],
    "类魂": ["Souls-like"],
    "肉鸽": ["Roguelike", "Roguelite"],
    "rogue": ["Roguelike", "Roguelite"],
    "roguelike": ["Roguelike"],
    "roguelite": ["Roguelite"],
    "roguelike卡牌": ["Roguelike Deckbuilder"],
    "肉鸽卡牌": ["Roguelike Deckbuilder"],
    "银河城": ["Metroidvania"],
    "metroidvania": ["Metroidvania"],
    "横版": ["2D Platformer", "Platformer"],
    "平台跳跃": ["Platformer"],
    "platformer": ["Platformer"],
    "开放世界": ["Open World"],
    "open world": ["Open World"],
    "沙盒": ["Sandbox"],
    "sandbox": ["Sandbox"],
    "生存": ["Survival"],
    "survival": ["Survival"],
    "建造": ["Building", "Base Building"],
    "基地建设": ["Base Building"],
    "自动化": ["Automation", "Factory"],
    "工厂": ["Factory", "Automation"],
    "种田": ["Farming Sim", "Life Sim"],
    "农场": ["Farming Sim"],
    "经营": ["Management"],
    "模拟经营": ["Management", "Simulation"],
    "模拟": ["Simulation"],
    "策略": ["Strategy"],
    "strategy": ["Strategy"],
    "战棋": ["Tactical RPG", "Turn-Based Tactics"],
    "回合制": ["Turn-Based", "Turn-Based Strategy", "Turn-Based Combat"],
    "即时战略": ["Real Time Strategy", "RTS"],
    "rts": ["Real Time Strategy"],
    "4x": ["4X"],
    "大战略": ["Grand Strategy"],
    "塔防": ["Tower Defense"],
    "自走棋": ["Auto Battler"],
    # 动作
    "动作": ["Action"],
    "act": ["Action"],
    "砍杀": ["Hack and Slash"],
    "割草": ["Hack and Slash", "Musou"],
    "角色动作": ["Character Action Game"],
    "招架": ["Character Action Game", "Souls-like", "Hack and Slash"],
    "弹反": ["Souls-like", "Character Action Game"],
    "防御反击": ["Souls-like"],
    "连招": ["Character Action Game", "Hack and Slash"],
    "手感": ["Character Action Game"],
    "打击感": ["Hack and Slash", "Character Action Game"],
    "潜行": ["Stealth"],
    "stealth": ["Stealth"],
    "跑酷": ["Parkour"],
    "格斗": ["Fighting"],
    "fighting": ["Fighting"],
    "清版": ["Beat 'em up"],
    # 射击
    "射击": ["Shooter"],
    "fps": ["FPS", "First-Person Shooter"],
    "第一人称": ["First-Person"],
    "第三人称": ["Third-Person"],
    "双摇杆": ["Twin Stick Shooter"],
    "弹幕": ["Bullet Hell"],
    "bullet hell": ["Bullet Hell"],
    "撤离": ["Extraction Shooter"],
    "刷宝": ["Looter Shooter", "Loot"],
    "looter": ["Looter Shooter"],
    # 角色扮演
    "rpg": ["RPG"],
    "角色扮演": ["RPG"],
    "jrpg": ["JRPG"],
    "日式rpg": ["JRPG"],
    "crpg": ["CRPG"],
    "美式rpg": ["CRPG"],
    "地牢": ["Dungeon Crawler"],
    "drpg": ["Dungeon Crawler"],
    # 叙事
    "剧情": ["Story Rich"],
    "story": ["Story Rich"],
    "叙事": ["Story Rich", "Narrative"],
    "分支": ["Choices Matter", "Multiple Endings"],
    "多结局": ["Multiple Endings"],
    "选择": ["Choices Matter"],
    "视觉小说": ["Visual Novel"],
    "visual novel": ["Visual Novel"],
    "galgame": ["Visual Novel", "Dating Sim"],
    "文字冒险": ["Visual Novel", "Text-Based"],
    "密室": ["Puzzle", "Point & Click"],
    "解谜": ["Puzzle"],
    "puzzle": ["Puzzle"],
    "推理": ["Detective", "Mystery"],
    "侦探": ["Detective"],
    "悬疑": ["Mystery"],
    # 氛围
    "恐怖": ["Horror"],
    "horror": ["Horror"],
    "生存恐怖": ["Survival Horror"],
    "心理恐怖": ["Psychological Horror"],
    "克苏鲁": ["Lovecraftian"],
    "氛围": ["Atmospheric"],
    "atmospheric": ["Atmospheric"],
    "沉浸": ["Immersive Sim", "Atmospheric"],
    "沉浸式模拟": ["Immersive Sim"],
    "走路模拟": ["Walking Simulator"],
    "治愈": ["Cozy", "Relaxing"],
    "放松": ["Relaxing", "Cozy"],
    "cozy": ["Cozy"],
    # 美术
    "像素": ["Pixel Graphics"],
    "pixel": ["Pixel Graphics"],
    "手绘": ["Hand-drawn"],
    "卡通": ["Cartoon", "Cute"],
    "二次元": ["Anime"],
    "anime": ["Anime"],
    "萌": ["Cute"],
    "3d": ["3D"],
    "2d": ["2D"],
    "低多边形": ["Low-Poly"],
    # 题材
    "奇幻": ["Fantasy"],
    "fantasy": ["Fantasy"],
    "科幻": ["Sci-fi"],
    "sci-fi": ["Sci-fi"],
    "赛博朋克": ["Cyberpunk"],
    "末日": ["Post-apocalyptic"],
    "废土": ["Post-apocalyptic"],
    "黑暗奇幻": ["Dark Fantasy"],
    "武侠": ["Martial Arts"],
    "忍者": ["Ninja"],
    "武士": ["Samurai"],
    "历史": ["Historical"],
    "二战": ["World War II"],
    "机甲": ["Mecha"],
    "太空": ["Space"],
    "魔法": ["Magic"],
    "僵尸": ["Zombies"],
    "吸血鬼": ["Vampires"],
    "政治": ["Political"],
    # 玩法属性
    "联机": ["Multiplayer", "Online Co-Op"],
    "多人": ["Multiplayer"],
    "合作": ["Co-op", "Online Co-Op"],
    "co-op": ["Co-op"],
    "双人": ["Local Co-Op", "Co-op"],
    "pvp": ["PvP"],
    "单人": ["Singleplayer"],
    "mmo": ["Massively Multiplayer", "MMORPG"],
    "难": ["Difficult", "Souls-like"],
    "硬核": ["Difficult"],
    "difficult": ["Difficult"],
    "永久死亡": ["Permadeath"],
    "随机生成": ["Procedural Generation"],
    "程序生成": ["Procedural Generation"],
    "音乐": ["Music", "Rhythm"],
    "音游": ["Rhythm"],
    "节奏": ["Rhythm"],
    "卡牌": ["Card Game"],
    "构筑": ["Deckbuilding"],
    "deckbuilding": ["Deckbuilding"],
    "派对": ["Party Game"],
    "独立": ["Indie"],
    "indie": ["Indie"],
    "女性主角": ["Female Protagonist"],
    "女主": ["Female Protagonist"],
    "养成": ["Life Sim", "Dating Sim"],
    "刷": ["Grinding"],
    "肝": ["Grinding"],
    "跑图": ["Open World"],
    "载具": ["Driving", "Automotive"],
    "竞速": ["Racing"],
    "赛车": ["Racing"],
    "飞行": ["Flight"],
    "海战": ["Naval"],
    "钓鱼": ["Fishing"],
    "牌组": ["Deckbuilding"],
}

# 反向：Steam tag 的规范大小写，用于把用户/源给的 tag 归一
_CANON = {}
for _alts in TAG_ALIASES.values():
    for _t in _alts:
        _CANON.setdefault(_t.lower(), _t)
for _t in (
    "Action", "Adventure", "Indie", "RPG", "Strategy", "Simulation", "Early Access",
    "Free to Play", "Souls-like", "Metroidvania", "Roguelike", "Roguelite", "Bullet Hell",
    "Hack and Slash", "Character Action Game", "Deckbuilding", "Turn-Based Strategy",
    "Turn-Based Combat", "Turn-Based Tactics", "Real Time Strategy", "City Builder",
    "Colony Sim", "Survival", "Crafting", "Open World", "Sandbox", "Story Rich",
    "Choices Matter", "Multiple Endings", "Visual Novel", "Dating Sim", "JRPG", "CRPG",
    "Tactical RPG", "Dungeon Crawler", "Platformer", "2D Platformer", "3D Platformer",
    "Puzzle", "Point & Click", "Walking Simulator", "Horror", "Survival Horror",
    "Psychological Horror", "Atmospheric", "Immersive Sim", "Stealth", "Parkour",
    "Racing", "Flight", "Space Sim", "4X", "Grand Strategy", "Tower Defense",
    "Auto Battler", "MOBA", "Battle Royale", "Rhythm", "Music", "Fighting", "Beat 'em up",
    "Twin Stick Shooter", "Top-Down Shooter", "FPS", "Third-Person Shooter",
    "Extraction Shooter", "Looter Shooter", "Hero Shooter", "Arena Shooter",
    "Boomer Shooter", "Military", "Historical", "Fantasy", "Sci-fi", "Cyberpunk",
    "Post-apocalyptic", "Lovecraftian", "Anime", "Cute", "Pixel Graphics", "Hand-drawn",
    "Physics", "Destruction", "Building", "Base Building", "Automation", "Factory",
    "Farming Sim", "Life Sim", "Management", "Economy", "Card Game", "Board Game",
    "Party Game", "Co-op", "Online Co-Op", "Local Co-Op", "Multiplayer", "PvP",
    "Singleplayer", "Massively Multiplayer", "MMORPG", "Cozy", "Relaxing", "Difficult",
    "Permadeath", "Procedural Generation", "Great Soundtrack", "Female Protagonist",
    "Ninja", "Samurai", "Martial Arts", "Zombies", "Vampires", "Magic", "Space",
    "Mecha", "Naval", "World War II", "Detective", "Mystery", "Dystopia", "Political",
    "Dark Fantasy", "Gore", "Violent", "2D", "3D", "Low-Poly", "Cartoon", "Loot",
    "Grinding", "Driving", "Automotive", "Fishing", "Text-Based", "Narrative", "Musou",
    "First-Person", "Third-Person", "Shooter", "Adventure Game", "RTS",
):
    _CANON.setdefault(_t.lower(), _t)


def canon(tag: str) -> str:
    t = tag.strip()
    return _CANON.get(t.lower(), t)


# ---------- 提取 ----------
_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9 '\-&+]{1,40}", re.I)

# 否定词：出现在别名前面 12 字内 / 后面 6 字内，就把这个 tag 判为「不想要」。
# 「不」单字太容易误伤（不但/不过/不够），只认带宾语的组合。
_NEG_PRE = ("不要", "不想", "不喜欢", "不爱", "讨厌", "受不了", "避开", "厌倦", "拒绝",
            "别再", "不需要", "无感", "不感冒", "晕", "受不了", "不太喜欢", "玩腻")
_NEG_POST = ("不要", "算了", "免了", "受够了", "腻了", "不喜欢", "滚")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def _is_negated(low: str, start: int, end: int) -> bool:
    pre = low[max(0, start - 12): start]
    post = low[end: end + 6]
    return any(n in pre for n in _NEG_PRE) or any(n in post for n in _NEG_POST)


def extract(text: str) -> dict:
    """把自由描述拆成 {正向 tag} / {负向 tag} / 没听懂的关键词。"""
    low = _norm(text)
    pos: dict[str, float] = {}
    neg: dict[str, float] = {}
    used: list[tuple[int, int]] = []

    # 长别名优先，避免「肉鸽卡牌」被「肉鸽」抢先
    for alias in sorted(TAG_ALIASES, key=len, reverse=True):
        start = 0
        while True:
            i = low.find(alias, start)
            if i < 0:
                break
            span = (i, i + len(alias))
            if not any(a <= span[0] < b or a < span[1] <= b for a, b in used):
                used.append(span)
                w = 1.0 if re.search(r"[\u4e00-\u9fff]", alias) else 0.9
                bucket = neg if _is_negated(low, *span) else pos
                for tag in TAG_ALIASES[alias]:
                    bucket[tag] = max(bucket.get(tag, 0.0), w)
            start = i + len(alias)

    # 英文单词直接命中规范 tag
    for m in _ASCII_TOKEN.finditer(low):
        tok = m.group(0).strip()
        if tok in _CANON:
            tag = _CANON[tok]
            bucket = neg if _is_negated(low, m.start(), m.end()) else pos
            bucket.setdefault(tag, 0.8)

    unmatched = _residual_tokens(low, used)
    unmatched += [t for t in re.findall(r"[a-z]{4,}", low) if t not in _CANON and t not in unmatched]
    # 同一个 tag 两边都出现时，否定优先
    for t in list(pos):
        if t in neg:
            pos.pop(t)
    return {"positive": pos, "negative": neg, "unmatched": unmatched[:20]}


def extract_tags(text: str) -> tuple[dict[str, float], list[str]]:
    """兼容用：只要正向 tag + 未匹配词。"""
    r = extract(text)
    return r["positive"], r["unmatched"]


_STOP = {
    "喜欢", "不喜欢", "想要", "一个", "那种", "这种", "感觉", "但是", "但是不", "不要",
    "可以", "一些", "有点", "特别", "非常", "很", "的", "了", "和", "与", "或", "在",
    "the", "and", "but", "with", "for", "like", "want", "game", "play",
}


def _residual_tokens(low: str, used: list[tuple[int, int]]) -> list[str]:
    """把已被别名吃掉的中文片段挖掉，剩下的按 2~3 字窗口收词。"""
    masked = list(low)
    for a, b in used:
        for i in range(a, min(b, len(masked))):
            masked[i] = " "
    rest = "".join(masked)
    out: list[str] = []
    for chunk in re.split(r"[^\u4e00-\u9fff]+", rest):
        chunk = chunk.strip()
        if len(chunk) < 2:
            continue
        for size in (3, 2):
            for i in range(0, max(0, len(chunk) - size + 1)):
                frag = chunk[i : i + size]
                if frag not in _STOP and frag not in out:
                    out.append(frag)
    return out


def explain_mapping(tag: str) -> list[str]:
    """给界面用：某个 tag 是被哪些说法命中的。"""
    return [a for a, ts in TAG_ALIASES.items() if tag in ts]
