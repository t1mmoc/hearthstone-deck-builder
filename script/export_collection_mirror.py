#!/usr/bin/env python3
"""
炉石传说收藏导出工具 — HearthMirror 方式（读游戏内存）

通过 HearthMirror.dll 直接从运行中的炉石传说客户端内存读取收藏数据，
再通过 HearthDb.dll 获取卡牌元数据，合并后导出为 CSV。

与 export_collection.py（走 HSReplay API）的区别：
  - 无需网络请求，纯本地读取
  - 需要：Windows + .NET Framework + 炉石客户端运行中
  - 数据来源更直接（游戏内存 → 本脚本），不经过 HSReplay 服务器

DLL 加载优先级：
  1. 本地 lib/ 目录（随脚本分发，无需装 HDT）
  2. --hdt-path 指定的 HDT 安装路径
  3. 自动检测 %LOCALAPPDATA%/HearthstoneDeckTracker/app-x.x.x

用法：
  python export_collection_mirror.py
  python export_collection_mirror.py --locale zhCN
  python export_collection_mirror.py --output my_collection.csv
  python export_collection_mirror.py --include-missing
  python export_collection_mirror.py --hdt-path "C:\\path\\to\\HDT"
  python export_collection_mirror.py --lib-dir .\\lib
"""

import argparse
import csv
import os
import sys
import glob

# ============================================================
# 强制 UTF-8 输出
# ============================================================
# 脚本会打印 ✓ 等 UTF-8 字符，中文 Windows 控制台默认 GBK 编码无法
# 编码这些字符，直接 print 会抛 UnicodeEncodeError。这里在启动时把
# 标准输出/错误固定为 UTF-8，避免依赖终端代码页。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass

# ============================================================
# 常量与映射表
# ============================================================

# PremiumType: 0=普通, 1=金色, 2=钻石, 3=签名
PREMIUM_NAMES = {0: "普通", 1: "金色", 2: "钻石", 3: "签名"}

# 稀有度 → 中文
RARITY_ZH = {
    "COMMON": "普通",
    "RARE": "稀有",
    "EPIC": "史诗",
    "LEGENDARY": "传说",
    "FREE": "免费",
}

# 类型 → 中文
TYPE_ZH = {
    "MINION": "随从",
    "SPELL": "法术",
    "WEAPON": "武器",
    "HERO": "英雄",
    "HERO_POWER": "英雄技能",
    "LOCATION": "地标",
    "BATTLEGROUND_SPELL": "酒馆法术",
    "MINION_HERO": "佣兵",
    "INVALID": "无效",
}

# 职业 → 中文
CLASS_ZH = {
    "NEUTRAL": "中立",
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
}

# 种族 → 中文
RACE_ZH = {
    "INVALID": "",
    "MURLOC": "鱼人",
    "DEMON": "恶魔",
    "MECHANICAL": "机械",
    "ELEMENTAL": "元素",
    "BEAST": "野兽",
    "TOTEM": "图腾",
    "PIRATE": "海盗",
    "DRAGON": "龙",
    "UNDEAD": "亡灵",
    "NAGA": "纳迦",
    "QUILBOAR": "野猪人",
}

# Locale → HearthDb.Enums.Locale 枚举名
LOCALE_MAP = {
    "zhCN": "zhCN",
    "zhTW": "zhTW",
    "enUS": "enUS",
    "jaJP": "jaJP",
    "koKR": "koKR",
    "frFR": "frFR",
    "deDE": "deDE",
    "esES": "esES",
    "esMX": "esMX",
    "ruRU": "ruRU",
    "itIT": "itIT",
    "ptBR": "ptBR",
    "plPL": "plPL",
    "ptPT": "ptPT",
    "thTH": "thTH",
}

# CSV 列头
CSV_HEADERS = [
    "dbfId", "卡牌ID", "名称", "法力值", "攻击力", "生命值", "耐久度",
    "类型", "类型_英", "稀有度", "稀有度_英", "职业", "职业_英", "卡组",
    "种族",
    "普通_拥有", "普通_上限", "金色_拥有", "金色_上限",
    "钻石_拥有", "钻石_上限", "签名_拥有", "签名_上限",
    "试用_拥有", "总拥有数", "是否集齐",
    "卡牌描述", "风味文字", "画师",
]


# ============================================================
# DLL 搜索路径检测
# ============================================================

def find_dll_path(custom_hdt=None, custom_lib=None):
    """
    确定 .NET DLL 的搜索目录。
    优先级：--lib-dir > 本地 lib/ > --hdt-path > 自动检测 HDT 安装目录
    """
    # 1. 用户通过 --lib-dir 显式指定
    if custom_lib:
        if os.path.isfile(os.path.join(custom_lib, "HearthMirror.dll")):
            return custom_lib, "lib-dir"
        print(f"错误: --lib-dir 指定的目录下未找到 HearthMirror.dll: {custom_lib}", file=sys.stderr)
        sys.exit(1)

    # 2. 脚本同级的 lib/ 目录（内嵌 DLL）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    local_lib = os.path.join(script_dir, "lib")
    if os.path.isfile(os.path.join(local_lib, "HearthMirror.dll")):
        return local_lib, "local-lib"

    # 3. 用户通过 --hdt-path 显式指定
    if custom_hdt:
        if os.path.isfile(os.path.join(custom_hdt, "HearthMirror.dll")):
            return custom_hdt, "hdt-path"
        print(f"错误: --hdt-path 指定的目录下未找到 HearthMirror.dll: {custom_hdt}", file=sys.stderr)
        sys.exit(1)

    # 4. 自动检测 HDT 安装目录
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        base = os.path.join(local_appdata, "HearthstoneDeckTracker")
        if os.path.isdir(base):
            app_dirs = glob.glob(os.path.join(base, "app-*"))
            if app_dirs:
                app_dirs.sort(reverse=True)
                hdt_path = app_dirs[0]
                if os.path.isfile(os.path.join(hdt_path, "HearthMirror.dll")):
                    return hdt_path, "auto-hdt"

    print(
        "错误: 未找到 HearthMirror.dll。\n"
        "请通过以下方式之一解决：\n"
        "  1. 将 5 个 DLL 复制到脚本同级 lib/ 目录\n"
        "     (HearthMirror.dll, HearthDb.dll, Newtonsoft.Json.dll,\n"
        "      System.Reflection.DispatchProxy.dll, untapped-scry-dotnet.dll)\n"
        "  2. 安装 Hearthstone Deck Tracker: https://hsreplay.net/downloads/\n"
        "  3. 通过 --hdt-path 指定 HDT 安装路径\n"
        "  4. 通过 --lib-dir 指定 DLL 所在目录",
        file=sys.stderr,
    )
    sys.exit(1)


# ============================================================
# 加载 .NET DLL
# ============================================================

def load_dotnet_assemblies(dll_dir):
    """加载 HearthMirror.dll 和 HearthDb.dll"""
    import clr

    # 将 DLL 目录加入 .NET 程序集搜索路径
    sys.path.insert(0, dll_dir)

    try:
        clr.AddReference("HearthMirror")
    except Exception as e:
        print(f"错误: 无法加载 HearthMirror.dll: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        clr.AddReference("HearthDb")
    except Exception as e:
        print(f"错误: 无法加载 HearthDb.dll: {e}", file=sys.stderr)
        sys.exit(1)

    from HearthMirror import Reflection, Status
    from HearthMirror.Enums import MirrorStatus
    from HearthDb import Cards
    from HearthDb.Enums import Locale

    return Reflection, Status, MirrorStatus, Cards, Locale


# ============================================================
# 从游戏内存读取收藏
# ============================================================

def read_collection(Reflection, Status, MirrorStatus):
    """调用 HearthMirror 从游戏内存读取收藏"""
    # 检查游戏状态
    status = Status.GetStatus()
    mirror_status = status.MirrorStatus

    if mirror_status == MirrorStatus.ProcNotFound:
        print(
            "错误: 未找到炉石传说进程。请先启动炉石传说客户端，\n"
            "     进入主菜单（收藏页面）后重试。",
            file=sys.stderr,
        )
        sys.exit(1)
    elif mirror_status == MirrorStatus.Error:
        print(
            "错误: 读取游戏进程时出错。请尝试：\n"
            "     1. 以管理员权限运行炉石传说\n"
            "     2. 确保炉石客户端在主菜单或收藏页面",
            file=sys.stderr,
        )
        sys.exit(1)
    elif mirror_status != MirrorStatus.Ok:
        print(f"错误: 游戏状态异常 (MirrorStatus={mirror_status})", file=sys.stderr)
        sys.exit(1)

    print("✓ 炉石传说客户端已连接，正在读取收藏...")

    # 读取收藏
    raw_collection = Reflection.Client.GetCollection()
    print(f"  原始条目数: {raw_collection.Count}")

    # 构建查找表: {card_id: {premium_type: count, ..., 'trial': trial_count}}
    collection_map = {}
    for i in range(raw_collection.Count):
        item = raw_collection[i]
        card_id = item.Id
        premium = item.PremiumType
        count = item.Count
        trial = item.TrialCount

        if card_id not in collection_map:
            collection_map[card_id] = {"trial": 0}
        collection_map[card_id][premium] = count
        if trial > 0:
            collection_map[card_id]["trial"] = max(
                collection_map[card_id].get("trial", 0), trial
            )

    return collection_map


# ============================================================
# 获取卡牌元数据并合并
# ============================================================

def build_card_list(Cards, Locale, locale_name, collection_map, include_missing):
    """从 HearthDb 获取可收集卡牌列表，合并收藏数据"""
    locale = getattr(Locale, locale_name)

    # 获取所有可收集卡牌
    collectible = Cards.Collectible
    print(f"  HearthDb 可收集卡牌数: {collectible.Count}")

    results = []
    owned_count = 0
    complete_count = 0

    for pair in collectible:
        card = pair.Value
        card_id = pair.Key

        # 获取收藏数据
        coll = collection_map.get(card_id, {})
        normal = coll.get(0, 0)
        golden = coll.get(1, 0)
        diamond = coll.get(2, 0)
        signature = coll.get(3, 0)
        trial = coll.get("trial", 0)

        total_owned = normal + golden + diamond + signature

        # 跳过未拥有的卡（除非 --include-missing）
        if total_owned == 0 and trial == 0 and not include_missing:
            continue

        if total_owned > 0:
            owned_count += 1

        # 稀有度决定上限
        rarity_str = str(card.Rarity)
        max_copies = 1 if rarity_str == "LEGENDARY" else 2

        # 是否集齐（总拥有数达到上限即算集齐，含金色/钻石/签名）
        is_complete = total_owned >= max_copies

        if is_complete:
            complete_count += 1

        # 获取属性
        name = card.GetLocName(locale) or card.Name or ""
        text = card.GetLocText(locale) or card.Text or ""
        flavor = card.GetLocFlavorText(locale) or card.FlavorText or ""
        artist = card.ArtistName or ""

        type_en = str(card.Type)
        rarity_en = rarity_str
        class_en = str(card.Class)
        race_en = str(card.Race)
        set_en = str(card.Set)

        results.append({
            "dbfId": card.DbfId,
            "卡牌ID": card_id,
            "名称": name,
            "法力值": card.Cost,
            "攻击力": card.Attack if card.Attack > 0 else "",
            "生命值": card.Health if card.Health > 0 else "",
            "耐久度": card.Durability if card.Durability > 0 else "",
            "类型": TYPE_ZH.get(type_en, type_en),
            "类型_英": type_en,
            "稀有度": RARITY_ZH.get(rarity_en, rarity_en),
            "稀有度_英": rarity_en,
            "职业": CLASS_ZH.get(class_en, class_en),
            "职业_英": class_en,
            "卡组": set_en,
            "种族": RACE_ZH.get(race_en, race_en),
            "普通_拥有": normal,
            "普通_上限": max_copies,
            "金色_拥有": golden,
            "金色_上限": max_copies,
            "钻石_拥有": diamond,
            "钻石_上限": max_copies,
            "签名_拥有": signature,
            "签名_上限": max_copies,
            "试用_拥有": trial,
            "总拥有数": total_owned,
            "是否集齐": "是" if is_complete else "否",
            "卡牌描述": text,
            "风味文字": flavor,
            "画师": artist,
        })

    return results, owned_count, complete_count


# ============================================================
# 导出 CSV
# ============================================================

def export_csv(results, output_path):
    """导出为 CSV"""
    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    print(f"\n✓ 已导出 {len(results)} 张卡牌到: {output_path}")


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="炉石传说收藏导出 — HearthMirror 方式（读游戏内存）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
要求：
  1. Windows + .NET Framework 4.5+
  2. 炉石传说客户端正在运行（在主菜单或收藏页面）
  3. 以下任一方式提供 DLL：
     a) 脚本同级 lib/ 目录内嵌 DLL（推荐，随脚本分发）
     b) 已安装 Hearthstone Deck Tracker (HDT)
     c) 通过 --hdt-path 或 --lib-dir 指定

所需 DLL（共 5 个，约 42MB）：
  HearthMirror.dll, HearthDb.dll, Newtonsoft.Json.dll,
  System.Reflection.DispatchProxy.dll, untapped-scry-dotnet.dll

示例：
  python export_collection_mirror.py
  python export_collection_mirror.py --locale zhCN
  python export_collection_mirror.py --include-missing
  python export_collection_mirror.py --hdt-path "C:\\HDT"
  python export_collection_mirror.py --lib-dir .\\lib
""",
    )
    parser.add_argument(
        "--locale",
        default="zhCN",
        choices=list(LOCALE_MAP.keys()),
        help="卡牌语言 (默认: zhCN)",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出 CSV 文件路径 (默认: collection_mirror.csv)",
    )
    parser.add_argument(
        "--include-missing",
        action="store_true",
        help="包含未拥有的卡牌",
    )
    parser.add_argument(
        "--hdt-path",
        default=None,
        help="HDT 安装路径 (fallback: 当 lib/ 不存在时使用)",
    )
    parser.add_argument(
        "--lib-dir",
        default=None,
        help="DLL 所在目录 (默认: 脚本同级 lib/)",
    )

    args = parser.parse_args()

    # 输出路径
    output_path = args.output or "collection_mirror.csv"

    print("=" * 60)
    print("  炉石传说收藏导出 — HearthMirror 方式")
    print("=" * 60)

    # 1. 检测 DLL 路径
    print("\n[1/4] 检测 DLL 路径...")
    dll_dir, source = find_dll_path(args.hdt_path, args.lib_dir)
    print(f"  DLL 来源: {source}")
    print(f"  路径: {dll_dir}")

    # 2. 加载 .NET DLL
    print("\n[2/4] 加载 .NET 程序集...")
    Reflection, Status, MirrorStatus, Cards, Locale = load_dotnet_assemblies(dll_dir)
    print("  ✓ HearthMirror.dll 已加载")
    print("  ✓ HearthDb.dll 已加载")

    # 3. 从游戏内存读取收藏
    print("\n[3/4] 读取游戏内存收藏数据...")
    collection_map = read_collection(Reflection, Status, MirrorStatus)

    unique_cards = len(collection_map)
    owned_entries = sum(
        1 for v in collection_map.values()
        if any(v.get(k, 0) > 0 for k in [0, 1, 2, 3])
    )
    print(f"  唯一卡牌ID数: {unique_cards}")
    print(f"  拥有的卡牌ID数: {owned_entries}")

    # 4. 合并卡牌元数据并导出
    print("\n[4/4] 合并卡牌数据并导出...")
    results, owned_count, complete_count = build_card_list(
        Cards, Locale, args.locale, collection_map, args.include_missing
    )

    export_csv(results, output_path)

    # 统计摘要
    print(f"\n{'=' * 60}")
    print(f"  导出完成！")
    print(f"{'=' * 60}")
    print(f"  已拥有卡牌: {owned_count} 张")
    print(f"  已集齐卡牌: {complete_count} 张")
    print(f"  导出总条目: {len(results)} 张")
    print(f"  CSV 文件: {os.path.abspath(output_path)}")


if __name__ == "__main__":
    main()
