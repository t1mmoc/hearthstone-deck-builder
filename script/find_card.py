#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卡牌查询：按名称（中文/英文）或 ID 查找卡牌ID。

使用内置 script/cards_index.json（中英文索引），无需联网。
适用场景：
  - 网页上的英文卡组名 → ID（如 "Fire Fly" → RLK_503）
  - 用户用中文卡名提问 → ID（如 "火羽精灵" → RLK_503）
  - 评估单卡：--text 查看卡牌效果文本

用法：
  python script/find_card.py "Fire Fly"
  python script/find_card.py 火羽精灵 "墓地之力" --csv data/collection_mirror.csv
  python script/find_card.py 霜之哀伤 --text
"""

import argparse
import csv
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carddata

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

RUNE_ZH = {"blood": "血", "frost": "冰", "unholy": "邪"}


def normalize(text):
    return unicodedata.normalize("NFKC", (text or "")).strip().lower().replace(" ", "")


def search(index, query, include_all=False):
    """返回 (exact_matches, fuzzy_matches)；条目按相关度排序。"""
    q = normalize(query)
    if not q:
        return [], []

    exact = []
    prefix = []
    sub = []
    for c in index:
        if not include_all and not c.get("collectible"):
            continue
        ne = normalize(c.get("name_en"))
        nz = normalize(c.get("name_zh"))
        cid = (c.get("id") or "").upper()
        if ne == q or nz == q:
            exact.append(c)
        elif cid.startswith(q.upper()):
            prefix.append(c)
        elif len(q) >= 2 and (q in ne or q in nz or q in cid.lower()):
            sub.append(c)

    dedup = []
    seen = set()
    for c in exact + prefix + sub:
        if c["id"] not in seen:
            seen.add(c["id"])
            dedup.append(c)
    exact_ids = {c["id"] for c in exact}
    exact_list = [c for c in dedup if c["id"] in exact_ids]
    others = [c for c in dedup if c["id"] not in exact_ids]
    return exact_list[:15], others[:15]


def owned_counts(csv_path):
    if not csv_path:
        return {}
    owned = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = row.get("卡牌ID", "")
            if cid:
                owned[cid] = int(row.get("总拥有数", 0) or 0)
    return owned


def print_card(card, owned, standard_sets, show_text, locale):
    name_zh = card.get("name_zh") or card.get("name_en") or "?"
    name_en = card.get("name_en") or ""
    std = "标准" if carddata.is_standard_set(card.get("set"), standard_sets) else "狂野"
    cls = card.get("cardClass") or ""
    rune = card.get("runeCost") or {}
    rune_parts = [f"{RUNE_ZH[k]}×{v}" for k, v in sorted(rune.items()) if v]
    lines = [
        f"[{card.get('id')}] {name_zh}",
        f"   英文名: {name_en}",
        f"   dbfId: {card.get('dbfId')} | {card.get('cost', '?')}费 | {card.get('rarity') or '?'} | {cls} | {card.get('set')} | {std}",
    ]
    if rune_parts:
        lines.append(f"   符文: {' '.join(rune_parts)}")
    if owned:
        n = owned.get(card.get("id"), 0)
        lines.append(f"   收藏: {n} 张")
    if show_text:
        text = carddata.card_text(card.get("id"), locale)
        if text:
            lines.append(f"   效果: {text[:300]}")
    print("\n".join(lines))
    print()


def main():
    parser = argparse.ArgumentParser(
        description="卡牌查询：名称（中/英）或 ID → 卡牌ID",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("query", nargs="+", help="卡牌名称（中文/英文）或 ID/ID片段")
    parser.add_argument("--csv", help="收藏 CSV 路径，附带拥有数量")
    parser.add_argument("--text", action="store_true", help="显示卡牌效果文本")
    parser.add_argument("--locale", choices=["zhCN", "enUS"], default="zhCN", help="卡牌文本语言 (默认: zhCN)")
    parser.add_argument("--all", action="store_true", help="包含不可收集卡牌（如衍生物）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    index = carddata.load_index()
    standard_sets, _ = carddata.load_standard_sets()
    owned = owned_counts(args.csv)
    results = []

    for q in args.query:
        exact, fuzzy = search(index, q, include_all=args.all)
        hits = exact or fuzzy
        results.append({"query": q, "hits": hits})
        print(f"===== 查询: {q} =====")
        if not hits:
            print("  未找到匹配卡牌。可尝试更短的关键字片段。")
            print()
            continue
        for card in hits:
            print_card(card, owned, standard_sets, args.text, args.locale)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))

    any_hits = any(r["hits"] for r in results)
    sys.exit(0 if any_hits else 1)


if __name__ == "__main__":
    main()
