#!/usr/bin/env python3
"""
HSReplay 卡牌收藏导出工具

从 HSReplay.net 的 Collection API + HearthstoneJSON 卡牌数据库
拉取你的炉石传说收藏，合并后导出为 CSV。

用法:
  # 标准用法 --url 指定收藏页面地址
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/"

  # 被 Cloudflare 拦截时传 Cookie
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --cookie "sessionid=xxx; csrftoken=yyy"

  # 切换卡牌语言
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --locale zhCN

  # 包含未拥有的卡
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --include-missing

URL 格式:
  https://hsreplay.net/collection/{region}/{account_lo}/
    region     = 1(欧洲) 2(美洲) 3(亚太) 5(中国)
    account_lo = 暴雪账号 ID

数据来源:
  1. https://hsreplay.net/api/v1/collection/?region=X&account_lo=Y&type=CONSTRUCTED
     → 返回 { "collection": { "<dbfId>": [normal, golden, diamond, signature, trial1-4] } }
  2. https://api.hearthstonejson.com/v1/latest/<locale>/cards.json
     → 返回全卡牌字典数组，含 name/cost/rarity/set/type 等字段
  合并方式: 用 dbfId 做 join，collection 提供"拥有几张"，cards.json 提供"卡叫什么"
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("缺少 requests 库，正在安装...")
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests


# ── 常量 ──────────────────────────────────────────────

HSREPLAY_COLLECTION_API = "https://hsreplay.net/api/v1/collection/"
HEARTHSTONEJSON_CARDS_API = "https://api.hearthstonejson.com/v1/latest/{locale}/cards.json"

# URL 解析正则: https://hsreplay.net/collection/{region}/{account_lo}/
URL_PATTERN = re.compile(
    r"hsreplay\.net/collection/(\d+)/(\d+)", re.IGNORECASE
)

# Collection 数组各索引含义 (从源码逆向得出)
COUNT_NAMES = ["normal", "golden", "diamond", "signature", "trial1", "trial2", "trial3", "trial4"]
COLLECTION_ARRAY_SIZE = 8

# 稀有度 → 最大拥有数
MAX_BY_RARITY = {
    "LEGENDARY": 1,
    "EPIC": 2,
    "RARE": 2,
    "COMMON": 2,
    "FREE": 2,
    "NONE": 1,
}

# 稀有度中文映射
RARITY_ZH = {
    "COMMON": "普通",
    "RARE": "稀有",
    "EPIC": "史诗",
    "LEGENDARY": "传说",
    "FREE": "免费",
}

# 职业中文映射
CLASS_ZH = {
    "NEUTRAL": "中立",
    "DRUID": "德鲁伊",
    "HUNTER": "猎人",
    "MAGE": "法师",
    "PALADIN": "圣骑士",
    "PRIEST": "牧师",
    "ROGUE": "潜行者",
    "SHAMAN": "萨满",
    "WARLOCK": "术士",
    "WARRIOR": "战士",
    "DEMONHUNTER": "恶魔猎手",
    "DEATHKNIGHT": "死亡骑士",
}

# 卡牌类型中文映射
TYPE_ZH = {
    "MINION": "随从",
    "SPELL": "法术",
    "WEAPON": "武器",
    "HERO": "英雄",
    "HERO_POWER": "英雄技能",
    "ENCHANTMENT": "附魔",
    "LOCATION": "地标",
    "BATTLEGROUND_SPELL": "酒馆法术",
}

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://hsreplay.net/",
    "Origin": "https://hsreplay.net",
}


# ── URL 解析 ──────────────────────────────────────────

def parse_collection_url(url: str) -> tuple[int, int]:
    """
    从 HSReplay 收藏页面 URL 中解析 region 和 account_lo。

    支持格式:
      https://hsreplay.net/collection/2/1234567890/
      hsreplay.net/collection/1/0987654321
    """
    match = URL_PATTERN.search(url)
    if not match:
        raise ValueError(
            f"无法从 URL 解析 region/account_lo: {url}\n"
            f"期望格式: https://hsreplay.net/collection/<region>/<account_lo>/"
        )
    region = int(match.group(1))
    account_lo = int(match.group(2))
    return region, account_lo


# ── 数据获取 ──────────────────────────────────────────

def fetch_collection(region: int, account_lo: int, cookie: str | None = None) -> dict:
    """
    调用 HSReplay Collection API。

    返回格式:
    {
      "collection": { "<dbfId>": [normal, golden, diamond, signature, ...] },
      "lastModified": "..."
    }

    注意: HSReplay 有 Cloudflare 保护，可能需要提供浏览器 Cookie。
    """
    params = {
        "region": region,
        "account_lo": account_lo,
        "type": "CONSTRUCTED",
    }
    headers = dict(BROWSER_HEADERS)
    if cookie:
        headers["Cookie"] = cookie

    print(f"[1/3] 正在从 HSReplay 获取收藏数据...")
    print(f"      URL: {HSREPLAY_COLLECTION_API}")
    print(f"      region={region}, account_lo={account_lo}")

    resp = requests.get(HSREPLAY_COLLECTION_API, params=params, headers=headers, timeout=30)

    if resp.status_code == 403:
        print("\n❌ 被 Cloudflare 拦截 (403)。")
        print("   请在浏览器中登录 hsreplay.net，然后复制 Cookie 传入 --cookie 参数。")
        print("   获取方式: F12 → Network → 找到 /api/v1/collection/ 请求 → 复制 Cookie 头")
        sys.exit(1)

    if resp.status_code == 401:
        print("\n❌ 需要登录 (401)。请提供有效的 Cookie。")
        sys.exit(1)

    resp.raise_for_status()
    data = resp.json()

    collection = data.get("collection", {})
    print(f"      ✅ 成功获取 {len(collection)} 张卡牌的收藏记录")

    return data


def fetch_cards(locale: str = "zhCN") -> list[dict]:
    """
    从 HearthstoneJSON 获取完整卡牌数据库。
    返回卡牌对象列表，每个对象含 dbfId, name, cost, rarity, set, type 等字段。
    """
    url = HEARTHSTONEJSON_CARDS_API.format(locale=locale)

    print(f"[2/3] 正在从 HearthstoneJSON 获取卡牌数据库...")
    print(f"      URL: {url}")

    resp = requests.get(url, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=60)
    resp.raise_for_status()
    cards = resp.json()

    print(f"      ✅ 成功获取 {len(cards)} 张卡牌数据")
    return cards


# ── 数据合并 ──────────────────────────────────────────

def normalize_collection_entry(entry) -> list[int]:
    """将 collection API 返回的数组规范化为固定 8 长度。"""
    if entry is None:
        return [0] * COLLECTION_ARRAY_SIZE
    if isinstance(entry, (int, float)):
        return [int(entry)] + [0] * (COLLECTION_ARRAY_SIZE - 1)
    if isinstance(entry, list):
        result = [int(x) if x is not None else 0 for x in entry]
        while len(result) < COLLECTION_ARRAY_SIZE:
            result.append(0)
        return result[:COLLECTION_ARRAY_SIZE]
    return [0] * COLLECTION_ARRAY_SIZE


def merge_data(collection_data: dict, cards: list[dict], include_missing: bool = False) -> list[dict]:
    """
    合并收藏数据与卡牌字典。

    逻辑 (与 HSReplay 前端一致):
    1. 遍历所有 collectible=true 的卡牌
    2. 用 card.dbfId 在 collection 中查出拥有数量
    3. 传说卡上限 1 张，其他上限 2 张
    4. 根据 include_missing 参数决定是否包含未拥有的卡
    """
    collection = collection_data.get("collection", {})

    # 构建 dbfId → 卡牌信息的索引
    cards_by_dbf = {}
    for card in cards:
        dbf_id = card.get("dbfId")
        if dbf_id is None:
            continue
        cards_by_dbf[str(dbf_id)] = card

    print(f"[3/3] 正在合并数据...")

    merged = []
    skipped_non_collectible = 0
    skipped_missing = 0

    for card in cards:
        # 只处理可收集的卡牌
        if not card.get("collectible", False):
            skipped_non_collectible += 1
            continue

        dbf_id = card.get("dbfId")
        if dbf_id is None:
            continue

        # 从收藏数据中获取数量
        counts_raw = collection.get(str(dbf_id))
        counts = normalize_collection_entry(counts_raw)

        normal = counts[0]
        golden = counts[1]
        diamond = counts[2]
        signature = counts[3]
        trial_total = sum(counts[4:8])

        total_owned = normal + golden + diamond + signature

        # 如果不包含缺失卡且没有拥有任何版本，跳过
        if not include_missing and total_owned == 0 and trial_total == 0:
            skipped_missing += 1
            continue

        rarity = card.get("rarity", "NONE")
        max_count = MAX_BY_RARITY.get(rarity, 2)

        row = {
            "dbfId": dbf_id,
            "卡牌ID": card.get("id", ""),
            "名称": card.get("name", ""),
            "法力值": card.get("cost", ""),
            "攻击力": card.get("attack", ""),
            "生命值": card.get("health", ""),
            "耐久度": card.get("durability", ""),
            "类型": TYPE_ZH.get(card.get("type", ""), card.get("type", "")),
            "类型_英": card.get("type", ""),
            "稀有度": RARITY_ZH.get(rarity, rarity),
            "稀有度_英": rarity,
            "职业": CLASS_ZH.get(card.get("cardClass", ""), card.get("cardClass", "")),
            "职业_英": card.get("cardClass", ""),
            "卡组": card.get("set", ""),
            "种族": card.get("race", card.get("races", [""])[0] if isinstance(card.get("races"), list) and card.get("races") else ""),
            "普通_拥有": normal,
            "普通_上限": max_count,
            "金色_拥有": golden,
            "金色_上限": max_count,
            "钻石_拥有": diamond,
            "钻石_上限": max_count,
            "签名_拥有": signature,
            "签名_上限": max_count,
            "试用_拥有": trial_total,
            "总拥有数": total_owned,
            "是否集齐": "是" if total_owned >= max_count else "否",
            "卡牌描述": (card.get("text", "") or "").replace("\n", " ").replace("$", ""),
            "风味文字": (card.get("flavor", "") or "").replace("\n", " "),
            "画师": card.get("artist", ""),
        }
        merged.append(row)

    print(f"      ✅ 合并完成: {len(merged)} 张卡牌")
    if skipped_non_collectible:
        print(f"      (跳过 {skipped_non_collectible} 张不可收集卡牌)")
    if skipped_missing:
        print(f"      (跳过 {skipped_missing} 张未拥有卡牌，使用 --include-missing 可包含)")

    return merged


# ── 导出 ──────────────────────────────────────────────

def export_csv(merged: list[dict], output_path: str):
    """将合并后的数据导出为 CSV 文件。"""
    if not merged:
        print("⚠️  没有数据可导出")
        return

    fieldnames = list(merged[0].keys())

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(merged)

    print(f"\n📄 CSV 已导出: {output_path}")
    print(f"   共 {len(merged)} 行，{len(fieldnames)} 列")


def export_json(merged: list[dict], output_path: str):
    """额外导出一份 JSON，方便后续处理。"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"📄 JSON 已导出: {output_path}")


# ── 统计摘要 ──────────────────────────────────────────

def print_summary(merged: list[dict], collection_data: dict):
    """打印收藏统计摘要。"""
    print("\n" + "=" * 60)
    print("📊 收藏统计摘要")
    print("=" * 60)

    total_cards = len(merged)
    complete = sum(1 for r in merged if r["是否集齐"] == "是")

    by_rarity = {}
    for row in merged:
        rarity = row["稀有度_英"]
        if rarity not in by_rarity:
            by_rarity[rarity] = {"total": 0, "owned": 0, "complete": 0}
        by_rarity[rarity]["total"] += 1
        by_rarity[rarity]["owned"] += row["总拥有数"]
        if row["是否集齐"] == "是":
            by_rarity[rarity]["complete"] += 1

    print(f"总卡牌数 (已拥有): {total_cards}")
    print(f"已集齐: {complete} ({complete/total_cards*100:.1f}%)" if total_cards else "已集齐: 0")
    print()

    print(f"{'稀有度':<10} {'拥有':>6} {'集齐':>6} {'完成率':>8}")
    print("-" * 35)
    for rarity in ["COMMON", "RARE", "EPIC", "LEGENDARY"]:
        if rarity in by_rarity:
            d = by_rarity[rarity]
            pct = d["complete"] / d["total"] * 100 if d["total"] else 0
            print(f"{RARITY_ZH.get(rarity, rarity):<10} {d['total']:>6} {d['complete']:>6} {pct:>7.1f}%")

    # 按职业统计
    by_class = {}
    for row in merged:
        cls = row["职业_英"]
        if cls not in by_class:
            by_class[cls] = 0
        by_class[cls] += 1

    print(f"\n按职业分布:")
    for cls in sorted(by_class.keys()):
        print(f"  {CLASS_ZH.get(cls, cls):<10} {by_class[cls]:>5} 张")

    last_modified = collection_data.get("lastModified", "未知")
    print(f"\n收藏最后更新: {last_modified}")
    print("=" * 60)


# ── 主入口 ────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="HSReplay 炉石传说卡牌收藏导出工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 标准用法
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/"

  # 被 Cloudflare 拦截时传 Cookie
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --cookie "sessionid=xxx"

  # 包含未拥有的卡
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --include-missing

  # 英文卡牌名
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --locale enUS

  # 同时导出 JSON
  python export_collection.py --url "https://hsreplay.net/collection/2/1234567890/" --json
        """,
    )
    parser.add_argument(
        "--url",
        type=str,
        required=True,
        help='HSReplay 收藏页面 URL，格式: https://hsreplay.net/collection/<region>/<account_lo>/',
    )
    parser.add_argument("--cookie", type=str, default=None, help="浏览器 Cookie (用于绕过 Cloudflare/认证)")
    parser.add_argument("--locale", type=str, default="zhCN", choices=["zhCN", "zhTW", "enUS", "jaJP", "koKR", "deDE", "esES", "frFR", "itIT", "plPL", "ptBR", "ruRU", "thTH"], help="卡牌语言")
    parser.add_argument("--output", "-o", type=str, default=None, help="输出 CSV 文件路径 (默认: collection_<account_lo>.csv)")
    parser.add_argument("--include-missing", action="store_true", help="包含未拥有的卡牌 (默认只导出已拥有的)")
    parser.add_argument("--json", action="store_true", help="同时导出 JSON 格式")
    parser.add_argument("--no-summary", action="store_true", help="不打印统计摘要")

    args = parser.parse_args()

    # 从 URL 解析 region 和 account_lo
    region, account_lo = parse_collection_url(args.url)

    output_csv = args.output or f"collection_{account_lo}.csv"

    print("=" * 60)
    print("🗡️  HSReplay 炉石卡牌收藏导出工具")
    print("=" * 60)
    print(f"  URL:   {args.url}")
    print(f"  地区:  {region}")
    print(f"  账号:  {account_lo}")
    print(f"  语言:  {args.locale}")
    print(f"  包含缺失: {'是' if args.include_missing else '否'}")
    print()

    # Step 1: 获取收藏数据
    collection_data = fetch_collection(region, account_lo, args.cookie)

    # Step 2: 获取卡牌数据库
    cards = fetch_cards(args.locale)

    # Step 3: 合并数据
    merged = merge_data(collection_data, cards, include_missing=args.include_missing)

    # Step 4: 导出
    export_csv(merged, output_csv)
    if args.json:
        json_path = output_csv.replace(".csv", ".json")
        export_json(merged, json_path)

    # 统计摘要
    if not args.no_summary:
        print_summary(merged, collection_data)

    print(f"\n✅ 完成! 输出文件: {Path(output_csv).absolute()}")


if __name__ == "__main__":
    main()
