#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
炉石传说卡组代码生成器 / 解码器。

用法：
  生成卡组代码（卡牌可用 ID 或 中/英文卡名，脚本自动解析）：
    python script/deck_builder.py --hero PALADIN --cards "TLC_426:1,CORE_CS2_093:2,..." \
        --csv data/collection_mirror.csv

  从 JSON 文件读取卡组（支持 {"id": ...} 或 {"name": ...}）：
    python script/deck_builder.py --hero PALADIN --cards-file deck.json --csv data/collection_mirror.csv

  带 sideboard（备牌/子卡）的卡组（如 E.T.C. / 奇利亚斯豪华版）：
    python script/deck_builder.py --hero PALADIN --cards "...30张..." \
        --sideboard "卡A:1,卡B:1,卡C:1" --csv data/collection_mirror.csv

  解码已有的卡组代码（例如从网页复制的 base64 代码）：
    python script/deck_builder.py --decode "AAECAZ8F..." --csv data/collection_mirror.csv

硬性规则（任一不满足即拒绝生成）：
  - 卡组必须恰好 30 张（不足或超出，炉石无法识别该卡组代码）；可用 --ignore-count 显式忽略张数限制
  - 每张传说卡最多 1 张，其余卡最多 2 张
  - 卡牌必须属于英雄职业或中立（多职业卡需包含英雄职业）
  - 标准模式只允许 标准环境系列 + 核心系列（见 data/standard_sets.json）
  - 死亡骑士卡组符文需求合计不得超过 3 个符文位
  - 传入 --csv 时校验收藏：任何一张数量不足即拒绝生成
  - 带 sideboard 的卡（E.T.C. / 奇利亚斯豪华版）会自动附带备牌/子卡：备牌不计入 30 张，
    但必须通过 --sideboard 显式给出且数量不超过该卡上限，否则生成的代码会被客户端拒绝
"""

import argparse
import base64
import csv
import json
import os
import re
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carddata

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ============================================================
# 常量
# ============================================================

FORMAT_MAP = {"wild": 1, "standard": 2, "classic": 3, "twist": 4}
FORMAT_ZH = {1: "狂野模式", 2: "标准模式", 3: "经典模式", 4: "幻变模式"}

# 炉石年份映射（每年 3 月左右轮换）
HS_YEAR_MAP = {
    2026: "甲虫年",
    2025: "猛龙年",
    2024: "天马年",
    2023: "啸狼年",
    2022: "多头蛇年",
    2021: "狮鹫年",
    2020: "凤凰年",
    2019: "巨龙年",
    2018: "乌鸦年",
    2017: "猛犸年",
    2016: "海怪年",
}


def get_hearthstone_year():
    """根据当前日期推断炉石年名称（炉石年通常从 3 月开始）。"""
    from datetime import datetime
    now = datetime.now()
    year = now.year if now.month >= 3 else now.year - 1
    return HS_YEAR_MAP.get(year, f"{year}年")


# 带 sideboard（备牌/子卡）的主卡：卡牌ID → sideboard 卡数（必须恰好等于该值）。
# 这些卡加入主卡组后，客户端会在卡组里额外附带恰好 N 张备牌（不计入 30 张主卡），
# 必须显式给出恰好 N 张备牌并编码进卡组代码的 sideboard 段，否则代码无效。
SIDEBOARD_LIMITS = {
    "ETC_080": 3,  # 乐队经理精英牛头人酋长 E.T.C., Band Manager（恰好 3 张自选备牌）
    "TOY_330": 3,  # 奇利亚斯豪华版3000型 Zilliax Deluxe 3000（恰好 3 张：2 功能模块 + 1 外观模块）
}


# 职业名 → 默认英雄卡牌ID
DEFAULT_HEROES = {
    "WARRIOR": "HERO_01",
    "SHAMAN": "HERO_02",
    "ROGUE": "HERO_03",
    "PALADIN": "HERO_04",
    "HUNTER": "HERO_05",
    "DRUID": "HERO_06",
    "WARLOCK": "HERO_07",
    "MAGE": "HERO_08",
    "PRIEST": "HERO_09",
    "DEMONHUNTER": "HERO_10",
    "DEATHKNIGHT": "HERO_11",
}

# 职业代码 ↔ 中文
CLASS_ZH = {
    "MAGE": "法师",
    "PALADIN": "圣骑士",
    "WARRIOR": "战士",
    "HUNTER": "猎人",
    "DRUID": "德鲁伊",
    "PRIEST": "牧师",
    "ROGUE": "潜行者",
    "SHAMAN": "萨满祭司",
    "WARLOCK": "术士",
    "DEMONHUNTER": "恶魔猎手",
    "DEATHKNIGHT": "死亡骑士",
    "NEUTRAL": "中立",
}
CLASS_EN = {v: k for k, v in CLASS_ZH.items()}


def normalize(text):
    """名称归一化：NFKC + 小写 + 去空白，用于模糊匹配。"""
    return unicodedata.normalize("NFKC", (text or "")).strip().lower().replace(" ", "")


# ============================================================
# 卡名 → 卡牌解析
# ============================================================

def find_by_name(index, token):
    """
    按名称查找卡牌：先精确（中/英），再唯一子串匹配。
    返回 (card, candidates)：命中唯一时 candidates 为空列表；
    无法唯一确定时返回 (None, 候选列表)。
    """
    tok = normalize(token)
    if not tok:
        return None, []

    def names(c):
        return [normalize(c.get("name_en")), normalize(c.get("name_zh"))]

    exact = [c for c in index if tok in names(c)]
    if exact:
        collectible_exact = [c for c in exact if c.get("collectible")]
        pool = collectible_exact if collectible_exact else exact
        unique = {c["id"]: c for c in pool}
        if len(unique) == 1:
            return next(iter(unique.values())), []
        # 多个精确命中（如 原版 与 CORE 版 同名）：优先核心系列
        core = [c for c in unique.values() if c.get("set") == "CORE"]
        if len({c["id"] for c in core}) == 1:
            return core[0], []
        return None, list(unique.values())[:15]

    if len(tok) < 2:
        return None, []
    subs = [c for c in index if c.get("collectible") and (tok in names(c)[0] or tok in names(c)[1])]
    unique = {c["id"]: c for c in subs}
    if len(unique) == 1:
        return next(iter(unique.values())), []
    return None, list(unique.values())[:15]


def parse_cards_arg(cards_str):
    """解析逗号分隔的 `卡牌:数量` 对；未写数量默认 1。卡牌可为 ID 或中/英文名。"""
    cards = []
    for item in cards_str.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            token, count = item.rsplit(":", 1)
            cards.append((token.strip(), int(count.strip())))
        else:
            cards.append((item, 1))
    return cards


def parse_cards_file(file_path):
    """从 JSON 读取卡组：支持 [{"id": "...", "count": n}] 或 [{"name": "...", "count": n}]。"""
    with open(file_path, encoding="utf-8") as f:
        data = json.load(f)
    cards = []
    for item in data:
        token = item.get("id") or item.get("name")
        if not token:
            raise ValueError(f"卡牌条目缺少 id/name: {item}")
        cards.append((token, int(item.get("count", 1))))
    return cards


def resolve_deck_cards(tokens, index, by_id, allow_token=False):
    """
    把 token 列表（ID 或名称）解析为 [(card, count), ...]；无法解析的返回错误。

    allow_token=True 时允许不可收藏的衍生物卡（如奇利亚斯模块），供 sideboard 解析使用。
    """
    resolved = []
    errors = []
    for token, count in tokens:
        card = by_id.get(token)
        if card is None and token.startswith("CORE_"):
            # 旧版 CORE_ 前缀（2023 年前）已并入原 ID，自动降级解析
            card = by_id.get(token[5:])
        if card is None:
            card, candidates = find_by_name(index, token)
            if card is None:
                if candidates:
                    errors.append((token, "无法唯一确定，候选：" +
                                   " / ".join(f"{c.get('name_zh') or c.get('name_en')} [{c['id']}]" for c in candidates)))
                else:
                    errors.append((token, "未找到该卡牌（试试 script/find_card.py 模糊查询）"))
                continue
        if not allow_token and not card.get("collectible") and card.get("type") != "HERO":
            errors.append((token, "该卡牌不可用于组卡（非收藏卡）"))
            continue
        resolved.append((card, count))
    return resolved, errors


# ============================================================
# 收藏 CSV
# ============================================================

def load_collection(csv_path):
    """读取收藏 CSV，返回 卡牌ID → 总拥有数。"""
    collection = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = row.get("卡牌ID", "")
            if not cid:
                continue
            collection[cid] = int(row.get("总拥有数", 0) or 0)
    return collection


# ============================================================
# 卡组校验
# ============================================================

def validate_deck(cards, hero_card, format_name, standard_sets, collection=None,
                  ignore_count=False, sideboard_cards=None):
    """
    校验卡组。cards: [(card, count)] 主卡组；sideboard_cards: [(card, count)] 备牌/子卡（可选）。
    返回错误列表（空 = 通过）。
    硬性规则：恰好 30 张（--ignore-count 可忽略）、传说 ≤1 / 其他 ≤2、职业合法、
    标准环境合法、DK 符文 ≤3、收藏数量足够；sideboard 卡不计入 30 张但校验其上限。
    """
    errors = []
    hero_class = hero_card.get("cardClass")

    total = sum(count for _, count in cards)
    if not ignore_count and total != 30:
        errors.append(
            f"卡组共有 {total} 张卡牌，必须恰好 30 张（不足或超出都无法被炉石识别，"
            f"请填满/调整到 30 张，或用 --ignore-count 显式忽略张数限制）")

    runes = {"blood": 0, "frost": 0, "unholy": 0}

    def check_card(card, count, count_runes=True):
        cid = card.get("id")
        name = card.get("name_zh") or card.get("name_en") or cid
        is_token = not card.get("collectible")

        max_copies = 1 if card.get("rarity") == "LEGENDARY" else 2
        if count > max_copies:
            errors.append(f"{cid} {name}：数量 {count} 超过上限 {max_copies}（传说最多 1 张，其余最多 2 张）")

        cls = card.get("cardClass")
        classes = card.get("classes") or []
        if cls != "NEUTRAL" and cls != hero_class and hero_class not in classes:
            errors.append(f"{cid} {name}：属于 {CLASS_ZH.get(cls, cls)}，不能放入 {CLASS_ZH.get(hero_class, hero_class)} 卡组")

        # 衍生物（token）随主卡走，不校验退环境与收藏
        if not is_token and format_name == "standard" and not carddata.is_standard_set(card.get("set"), standard_sets):
            errors.append(f"{cid} {name}：所属系列 {card.get('set')} 已退环境，标准模式不可用")

        if collection is not None and not is_token:
            owned = collection.get(cid, 0)
            if owned < count:
                errors.append(f"{cid} {name}：需要 {count} 张，收藏只有 {owned} 张")

        if count_runes and hero_class == "DEATHKNIGHT":
            rc = card.get("runeCost") or {}
            for k in runes:
                runes[k] = max(runes[k], int(rc.get(k, 0) or 0))

    for card, count in cards:
        check_card(card, count)

    # sideboard（备牌/子卡）校验：不计入 30 张主卡，但必须有对应主卡且数量恰好等于该卡上限
    sb_owners = [card for card, _ in cards if card.get("id") in SIDEBOARD_LIMITS]
    sb_total = sum(count for _, count in (sideboard_cards or []))
    if len(sb_owners) > 1:
        errors.append("主卡组含多张可携带备牌的卡，暂不支持一次指定多张备牌主卡；请分别生成")
    elif sb_owners:
        owner = sb_owners[0]
        limit = SIDEBOARD_LIMITS[owner.get("id")]
        owner_name = owner.get("name_zh") or owner.get("name_en") or owner["id"]
        if sb_total != limit:
            errors.append(f"「{owner_name}」需要恰好 {limit} 张备牌/子卡，当前给出 {sb_total} 张（请用 --sideboard 补齐）")
    elif sideboard_cards:
        errors.append(
            f"提供了 {sb_total} 张备牌/子卡，但主卡组中没有可携带备牌的卡"
            f"（{', '.join(SIDEBOARD_LIMITS)}）")
    for card, count in (sideboard_cards or []):
        check_card(card, count, count_runes=False)

    if hero_class == "DEATHKNIGHT" and sum(runes.values()) > 3:
        errors.append(f"死亡骑士符文需求 {runes}（血/冰/邪）合计超过 3 个符文位，无法构建")
    return errors


# ============================================================
# 卡组代码 编解码
# ============================================================

def encode_varint(value):
    """整数 → varint 字节（同 Protobuf）。"""
    if value < 0:
        raise ValueError(f"varint 不支持负数: {value}")
    result = bytearray()
    while value > 0x7F:
        result.append((value & 0x7F) | 0x80)
        value >>= 7
    result.append(value & 0x7F)
    return bytes(result)


def decode_varint(data, pos):
    """从 data[pos:] 解码 varint，返回 (value, new_pos)。"""
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("卡组代码结构不完整")
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def encode_deck_code(format_num, hero_dbf_id, cards, sideboards=None):
    """
    编码卡组为 base64 字符串。
    cards: [(dbfId, count)] 主卡组；sideboards: [(dbfId, count, owner_dbfId)] 备牌/子卡。
    1-copy / 2-copy / n-copy 三块内的 dbfId 均升序排列，保证输出稳定且与游戏内导出一致。
    主卡组块之后：无 sideboard 写 0x00 标志；有 sideboard 写 0x01 + sideboard 三块
    （每张备牌编码为 dbfId + owner_dbfId，n-copy 块额外带 count）。
    """
    singles = sorted(d for d, count in cards if count == 1)
    doubles = sorted(d for d, count in cards if count == 2)
    n_copies = sorted(((d, count) for d, count in cards if count not in (1, 2)), key=lambda x: x[0])

    data = bytearray()
    data.append(0)  # reserved
    data.append(1)  # version
    data.extend(encode_varint(format_num))

    data.extend(encode_varint(1))  # 英雄数
    data.extend(encode_varint(hero_dbf_id))

    data.extend(encode_varint(len(singles)))
    for d in singles:
        data.extend(encode_varint(d))
    data.extend(encode_varint(len(doubles)))
    for d in doubles:
        data.extend(encode_varint(d))
    data.extend(encode_varint(len(n_copies)))
    for d, count in n_copies:
        data.extend(encode_varint(d))
        data.extend(encode_varint(count))

    sideboards = sideboards or []
    if sideboards:
        data.append(1)  # sideboard 标志：有
        sb_singles = sorted((d, o) for d, c, o in sideboards if c == 1)
        sb_doubles = sorted((d, o) for d, c, o in sideboards if c == 2)
        sb_n = sorted((d, c, o) for d, c, o in sideboards if c not in (1, 2))
        # sideboard 卡按 (owner_dbfId, dbfId) 升序，与游戏内导出一致
        sb_singles.sort(key=lambda x: (x[1], x[0]))
        sb_doubles.sort(key=lambda x: (x[1], x[0]))
        sb_n.sort(key=lambda x: (x[2], x[0]))
        data.extend(encode_varint(len(sb_singles)))
        for d, o in sb_singles:
            data.extend(encode_varint(d))
            data.extend(encode_varint(o))
        data.extend(encode_varint(len(sb_doubles)))
        for d, o in sb_doubles:
            data.extend(encode_varint(d))
            data.extend(encode_varint(o))
        data.extend(encode_varint(len(sb_n)))
        for d, c, o in sb_n:
            data.extend(encode_varint(d))
            data.extend(encode_varint(c))
            data.extend(encode_varint(o))
    else:
        data.append(0)  # sideboard 标志：无
    return base64.b64encode(bytes(data)).decode("ascii")


def decode_deck_code(code):
    """
    解码 base64 卡组代码，返回 dict:
    {"format": int, "hero_dbf": int, "cards": [(dbfId, count), ...],
     "sideboards": [(dbfId, count, owner_dbfId), ...]}
    """
    raw = base64.b64decode(code)
    pos = 0
    if len(raw) < 3:
        raise ValueError("卡组代码太短")
    reserved = raw[pos]; pos += 1
    version = raw[pos]; pos += 1
    if version != 1:
        raise ValueError(f"不支持的卡组代码版本: {version}")
    fmt, pos = decode_varint(raw, pos)
    hero_count, pos = decode_varint(raw, pos)
    hero_dbf = None
    for _ in range(hero_count):
        d, pos = decode_varint(raw, pos)
        if hero_dbf is None:
            hero_dbf = d
    n1, pos = decode_varint(raw, pos)
    singles = []
    for _ in range(n1):
        d, pos = decode_varint(raw, pos)
        singles.append(d)
    n2, pos = decode_varint(raw, pos)
    doubles = []
    for _ in range(n2):
        d, pos = decode_varint(raw, pos)
        doubles.append(d)
    n3, pos = decode_varint(raw, pos)
    n_copies = []
    for _ in range(n3):
        d, pos = decode_varint(raw, pos)
        c, pos = decode_varint(raw, pos)
        n_copies.append((d, c))
    cards = [(d, 1) for d in singles] + [(d, 2) for d in doubles] + n_copies

    # sideboard 段（可选，跟在主卡组块之后的 0x01 标志 + 三块备牌）
    sideboards = []
    if pos < len(raw) and raw[pos] == 1:
        pos += 1
        ns1, pos = decode_varint(raw, pos)
        for _ in range(ns1):
            d, pos = decode_varint(raw, pos)
            o, pos = decode_varint(raw, pos)
            sideboards.append((d, 1, o))
        ns2, pos = decode_varint(raw, pos)
        for _ in range(ns2):
            d, pos = decode_varint(raw, pos)
            o, pos = decode_varint(raw, pos)
            sideboards.append((d, 2, o))
        ns3, pos = decode_varint(raw, pos)
        for _ in range(ns3):
            d, pos = decode_varint(raw, pos)
            c, pos = decode_varint(raw, pos)
            o, pos = decode_varint(raw, pos)
            sideboards.append((d, c, o))
    return {"format": fmt, "hero_dbf": hero_dbf, "cards": cards, "sideboards": sideboards}


def deck_display_name(card, locale="zhCN"):
    return card.get("name_zh") if locale == "zhCN" else card.get("name_en")


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="炉石传说卡组代码生成器/解码器 — 恰好 30 张，可校验收藏",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 生成标准模式卡组代码（ID 或 卡名均可）
  python script/deck_builder.py --hero PALADIN \\
      --cards "TLC_426:1,CORE_CS2_093:2,奉献:2" --csv data/collection_mirror.csv

  # 从 JSON 读取卡组
  python script/deck_builder.py --hero PALADIN --cards-file deck.json --csv data/collection_mirror.csv

  # 带 sideboard 备牌的卡组（E.T.C. / 奇利亚斯豪华版）
  python script/deck_builder.py --hero PALADIN --cards "...30张含 ETC_080..." \\
      --sideboard "卡A:1,卡B:1,卡C:1" --csv data/collection_mirror.csv

  # 解码网上复制的卡组代码
  python script/deck_builder.py --decode "AAECAZ8F..." --csv data/collection_mirror.csv

注意：
  - 必须恰好 30 张卡牌，否则拒绝生成（炉石无法识别非 30 张卡组代码）；
    刻意少于 30 张等场景可用 --ignore-count 显式忽略张数限制
  - 传说卡最多 1 张，其余卡最多 2 张
  - 带 sideboard 的卡（E.T.C. / 奇利亚斯豪华版）需用 --sideboard 给出备牌，备牌不计入 30 张
  - 卡名查找使用内置 script/cards_index.json（中英文）
""",
    )
    parser.add_argument("--decode", metavar="DECK_CODE", help="解码已有卡组代码（不生成）")
    parser.add_argument("--format", choices=list(FORMAT_MAP), default="standard", help="游戏模式 (默认: standard)")
    parser.add_argument("--hero", help="英雄：职业名 (如 PALADIN/法师) 或英雄卡牌ID (如 HERO_04)")
    parser.add_argument("--cards", help="卡牌列表，逗号分隔的 `卡牌:数量` 对；卡牌可为 ID 或 中/英文名")
    parser.add_argument("--cards-file", help="从 JSON 文件读取卡组：支持 {\"id\": ...} 或 {\"name\": ...}")
    parser.add_argument("--sideboard", help="备牌/子卡列表（E.T.C. / 奇利亚斯豪华版等自动附带的 sideboard），逗号分隔的 `卡牌:数量` 对")
    parser.add_argument("--ignore-count", action="store_true", help="显式忽略卡牌张数限制（默认必须恰好 30 张）")
    parser.add_argument("--csv", help="收藏 CSV 路径；传入则启用收藏校验")
    parser.add_argument("--name", default="自定义卡组", help="卡组名称 (默认: 自定义卡组)")
    parser.add_argument("--locale", choices=["zhCN", "enUS"], default="zhCN", help="输出卡名语言 (默认: zhCN)")
    parser.add_argument("--year", default=None, help="炉石年份名称 (默认: 自动检测，如 甲虫年)")
    parser.add_argument("--rebuild-index", action="store_true", help="重建 cards_index.json 后继续")
    args = parser.parse_args()

    index = carddata.load_index(rebuild=args.rebuild_index)
    by_id, by_dbf = carddata.build_lookups(index)
    standard_sets, std_meta = carddata.load_standard_sets()

    collection = None
    if args.csv:
        if not os.path.exists(args.csv):
            print(f"错误: 收藏 CSV 不存在: {args.csv}", file=sys.stderr)
            print("请先导出收藏（export_collection_mirror.py 或 export_collection.py），或确认 --csv 路径", file=sys.stderr)
            sys.exit(1)
        collection = load_collection(args.csv)

    # ---------- 解码模式 ----------
    if args.decode:
        try:
            decoded = decode_deck_code(args.decode)
        except Exception as exc:
            print(f"解码失败: {exc}", file=sys.stderr)
            sys.exit(1)
        hero = by_dbf.get(decoded["hero_dbf"]) or {}
        fmt_zh = FORMAT_ZH.get(decoded["format"], f"格式{decoded['format']}")
        cards_info = []
        total = 0
        for dbf, count in decoded["cards"]:
            card = by_dbf.get(dbf) or {}
            cards_info.append((dbf, card, count))
            total += count
        print(f"=== 卡组代码解码 ===")
        print(f"模式: {fmt_zh}")
        print(f"英雄: {deck_display_name(hero) or '未知'} [{hero.get('id') or decoded['hero_dbf']}]")
        print(f"卡牌数: {total}")
        print()
        missing_any = False
        cards_info.sort(key=lambda x: (x[1].get("cost", 0) if x[1] else 0, x[1].get("name_zh", "") if x[1] else ""))
        for dbf, card, count in cards_info:
            if not card:
                print(f"  dbf={dbf}（未知卡牌，卡牌库可能过期）")
                continue
            name = deck_display_name(card, args.locale)
            std_mark = "标准" if carddata.is_standard_set(card.get("set"), standard_sets) else "狂野"
            owned = ""
            if collection is not None:
                have = collection.get(card["id"], 0)
                owned = f"  收藏 {have}/{count}"
                if have < count:
                    missing_any = True
            print(f"  {count}x ({card.get('cost', '?')}) {name} [{card['id']}] {card.get('set')} {std_mark}{owned}")
        if decoded.get("sideboards"):
            print()
            print("=== 备牌/子卡 (sideboard) ===")
            for dbf, count, owner_dbf in decoded["sideboards"]:
                card = by_dbf.get(dbf) or {}
                owner = by_dbf.get(owner_dbf) or {}
                name = deck_display_name(card, args.locale) if card else f"dbf={dbf}"
                owner_name = deck_display_name(owner, args.locale) if owner else f"dbf={owner_dbf}"
                std_mark = "标准" if (card and carddata.is_standard_set(card.get("set"), standard_sets)) else "狂野"
                owned = ""
                if collection is not None and card:
                    have = collection.get(card["id"], 0)
                    owned = f"  收藏 {have}/{count}"
                    if have < count:
                        missing_any = True
                print(f"  {count}x {name} [{card.get('id') if card else dbf}] {std_mark}  归属: {owner_name}{owned}")
        if collection is not None and missing_any:
            print("\n⚠ 部分卡牌收藏数量不足（详见上方 收藏 列）", file=sys.stderr)
            sys.exit(1)
        return

    # ---------- 生成模式 ----------
    if args.hero is None:
        print("错误: 必须指定 --hero（职业名或英雄卡牌ID）", file=sys.stderr)
        sys.exit(1)
    if not args.cards and not args.cards_file:
        print("错误: 必须指定 --cards 或 --cards-file", file=sys.stderr)
        sys.exit(1)

    tokens = parse_cards_arg(args.cards) if args.cards else parse_cards_file(args.cards_file)
    if not tokens:
        print("错误: 卡牌列表为空", file=sys.stderr)
        sys.exit(1)

    deck_cards, resolve_errors = resolve_deck_cards(tokens, index, by_id)
    if resolve_errors:
        print("错误: 以下卡牌无法解析:", file=sys.stderr)
        for token, msg in resolve_errors:
            print(f"  「{token}」 {msg}", file=sys.stderr)
        sys.exit(1)

    sideboard_cards = []
    if args.sideboard:
        sb_tokens = parse_cards_arg(args.sideboard)
        sideboard_cards, sb_errors = resolve_deck_cards(sb_tokens, index, by_id, allow_token=True)
        if sb_errors:
            print("错误: 以下备牌/子卡无法解析:", file=sys.stderr)
            for token, msg in sb_errors:
                print(f"  「{token}」 {msg}", file=sys.stderr)
            sys.exit(1)

    hero_input = args.hero.strip()
    hero_key = hero_input.upper()
    if hero_key in DEFAULT_HEROES:
        hero_card_id = DEFAULT_HEROES[hero_key]
    else:
        hero_card_id = hero_input
    hero_card = by_id.get(hero_card_id)
    if hero_card is None:
        # 允许中文职业名
        en_key = CLASS_EN.get(hero_input)
        if en_key in DEFAULT_HEROES:
            hero_card_id = DEFAULT_HEROES[en_key]
            hero_card = by_id.get(hero_card_id)
    if hero_card is None:
        print(f"错误: 找不到英雄卡牌: {args.hero}", file=sys.stderr)
        print(f"可用职业: {', '.join(DEFAULT_HEROES.keys())} / {', '.join(CLASS_ZH.values())}", file=sys.stderr)
        sys.exit(1)

    hero_class = hero_card.get("cardClass")
    errors = validate_deck(deck_cards, hero_card, args.format, standard_sets, collection,
                           ignore_count=args.ignore_count, sideboard_cards=sideboard_cards)
    if errors:
        print("❌ 卡组校验失败:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print("\n拒绝生成卡组代码。请修正后重试。", file=sys.stderr)
        sys.exit(1)

    total = sum(count for _, count in deck_cards)
    count_note = f"{total} 张卡牌" if args.ignore_count else "30 张卡牌"
    sb_note = f" + {sum(c for _, c in sideboard_cards)} 张备牌" if sideboard_cards else ""
    if hero_class == "DEATHKNIGHT":
        runes = {"blood": 0, "frost": 0, "unholy": 0}
        for card, _ in deck_cards:
            rc = card.get("runeCost") or {}
            for k in runes:
                runes[k] = max(runes[k], int(rc.get(k, 0) or 0))
        rune_zh = {"blood": "血", "frost": "冰", "unholy": "邪"}
        rune_line = "，".join(f"{rune_zh[k]}×{v}" for k, v in runes.items() if v) or "无符文需求"
        print(f"✓ 校验通过：{count_note}{sb_note}，收藏满足，符文配置 {rune_line}", file=sys.stderr)
    else:
        print(f"✓ 校验通过：{count_note}{sb_note}，收藏满足", file=sys.stderr)

    # ---------- 编码 ----------
    hero_dbf = hero_card.get("dbfId")
    dbf_cards = [(card["dbfId"], count) for card, count in deck_cards]
    sideboard_owner_dbf = None
    for card, _ in deck_cards:
        if card.get("id") in SIDEBOARD_LIMITS:
            sideboard_owner_dbf = card["dbfId"]
    dbf_sideboards = [(card["dbfId"], count, sideboard_owner_dbf) for card, count in sideboard_cards]
    deck_code = encode_deck_code(FORMAT_MAP[args.format], hero_dbf, dbf_cards, dbf_sideboards)

    hs_year = args.year or get_hearthstone_year()
    lines = [f"### {args.name}",
             f"# 职业：{CLASS_ZH.get(hero_class, hero_class)}",
             f"# 模式：{FORMAT_ZH.get(FORMAT_MAP[args.format], args.format)}",
             f"# {hs_year}",
             "#"]
    info = [(card, count) for card, count in deck_cards]
    info.sort(key=lambda x: (x[0].get("cost", 0) or 0, deck_display_name(x[0], args.locale) or ""))
    for card, count in info:
        prefix = f"{count}x" if count > 1 else "1x"
        lines.append(f"# {prefix} ({card.get('cost', '?')}) {deck_display_name(card, args.locale)}")
    if sideboard_cards:
        lines.append("# ")
        lines.append("# ---- 备牌/子卡 (sideboard) ----")
        sb_info = [(card, count) for card, count in sideboard_cards]
        sb_info.sort(key=lambda x: (x[0].get("cost", 0) or 0, deck_display_name(x[0], args.locale) or ""))
        for card, count in sb_info:
            prefix = f"{count}x" if count > 1 else "1x"
            lines.append(f"# {prefix} ({card.get('cost', '?')}) {deck_display_name(card, args.locale)}")
    lines += ["# ", deck_code, "# ", "# 想要使用这副套牌，请先复制到剪贴板，然后在游戏中点击“新套牌”进行粘贴。"]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
