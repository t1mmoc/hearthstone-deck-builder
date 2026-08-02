#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收藏分析报告：输入收藏 CSV，输出按职业 / 稀有度 / 系列 的统计，
以及指定职业的「标准环境可用卡池」清单，供 AI 组卡参考。

用法：
  # 总览：各职业拥有量与标准环境可用量
  python script/collection_report.py --csv data/collection_mirror.csv

  # 查看某职业的标准环境可用卡池（组卡时挑选卡牌用）
  python script/collection_report.py --csv data/collection_mirror.csv --class 死亡骑士
  python script/collection_report.py --csv data/collection_mirror.csv --class DEATHKNIGHT

  # 包含已退环境（狂野）的卡牌
  python script/collection_report.py --csv data/collection_mirror.csv --class 法师 --all

  # 卡池清单附带卡牌描述（AI 评估单卡用）
  python script/collection_report.py --csv data/collection_mirror.csv --class 死亡骑士 --with-text
"""

import argparse
import csv
import json
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import carddata

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

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

RARITY_ZH = {"COMMON": "普通", "RARE": "稀有", "EPIC": "史诗", "LEGENDARY": "传说", "FREE": "免费"}
DECK_TYPES = {"MINION", "SPELL", "WEAPON", "LOCATION"}
RUNE_ZH = {"blood": "血", "frost": "冰", "unholy": "邪"}


def normalize_class(text):
    """把职业输入（中文/英文代码/英文全称）归一化为英文代码，失败返回 None。"""
    t = (text or "").strip().upper()
    if t in CLASS_ZH:
        return t
    if text and text.strip() in CLASS_EN:
        return CLASS_EN[text.strip()]
    for en, zh in CLASS_ZH.items():
        if t == en or zh in text or en in text.upper():
            return en
    return None


def row_class_info(row, by_id):
    """返回 (英文职业代码, 多职业列表, 是否为英雄)。多职业卡以 'MULTI:...' 表示。"""
    card = by_id.get(row.get("卡牌ID", "")) or {}
    cc = card.get("cardClass")
    classes = card.get("classes") or []
    if cc and cc != "INVALID":
        return cc, [], (row.get("类型") == "英雄")
    if classes:
        return "MULTI:" + ",".join(sorted(classes)), classes, (row.get("类型") == "英雄")
    en = (row.get("职业_英") or "").upper()
    return (en if en else "未知"), [], (row.get("类型") == "英雄")


def format_class_key(key):
    """表格里显示职业名；多职业键显示为中文列表。"""
    if key.startswith("MULTI:"):
        classes = key.split(":", 1)[1].split(",")
        return "多职业(" + "、".join(CLASS_ZH.get(c, c) for c in classes) + ")"
    return CLASS_ZH.get(key, key)


def clean_card_text(text):
    """
    清理卡牌文本为可读单行：
    - 去掉（还剩X点/已经就绪）这类动态提示与 @ 分段（重复的变体文本）
    - 去掉 HTML 标签与 {0} 占位符
    - 删掉 $ / # 显示标记（后面的数字保留，如 "$25点伤害" → "25点伤害"）
    """
    if not text:
        return ""
    text = re.sub(r"<i>（[^<]*）</i>", "", text)
    text = text.split("@", 1)[0]
    text = re.sub(r"<[^>]*>", "", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\$[ad]", "", text)
    text = text.replace("$", "").replace("#", "")
    text = text.replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())


def main():
    parser = argparse.ArgumentParser(
        description="收藏分析报告：统计各职业/系列拥有量，列出标准环境可用卡池",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--csv", default=None, help="收藏 CSV 路径 (默认: data/collection_mirror.csv 或 ./collection_mirror.csv)")
    parser.add_argument("--class", dest="cls", help="只看某职业的可用卡池（职业中文名或英文代码）")
    parser.add_argument("--all", action="store_true", help="卡池清单包含已退环境（狂野）卡牌")
    parser.add_argument("--with-text", action="store_true", help="卡池清单附带卡牌描述（从完整缓存按需读取）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    args = parser.parse_args()

    csv_path = args.csv
    if not csv_path:
        for candidate in ("data/collection_mirror.csv", "collection_mirror.csv"):
            if os.path.exists(candidate):
                csv_path = candidate
                break
    if not csv_path or not os.path.exists(csv_path):
        print("错误: 找不到收藏 CSV。请用 --csv 指定，或先运行 export_collection_mirror.py --output data/collection_mirror.csv", file=sys.stderr)
        sys.exit(1)

    with open(csv_path, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    standard_sets, _ = carddata.load_standard_sets()
    index = carddata.load_index()
    by_id, _ = carddata.build_lookups(index)

    owned_rows = []
    for r in rows:
        if int(r.get("总拥有数", 0) or 0) > 0:
            owned_rows.append(r)

    # 每行补全职业/标准信息
    for r in owned_rows:
        r["_cls_en"], r["_classes"], r["_is_hero"] = row_class_info(r, by_id)
        r["_std"] = carddata.is_standard_set(r.get("卡组", ""), standard_sets)

    total = len(owned_rows)
    std_total = sum(1 for r in owned_rows if r["_std"])

    # ---- 按职业统计 ----
    per_class = {}
    for r in owned_rows:
        cls = r["_cls_en"]
        d = per_class.setdefault(cls, Counter())
        d["owned"] += 1
        if r["_std"]:
            d["std"] += 1
        rarity_en = {"普通": "COMMON", "稀有": "RARE", "史诗": "EPIC", "传说": "LEGENDARY", "免费": "FREE"}.get(r.get("稀有度", ""), "")
        if r["_std"] and rarity_en == "LEGENDARY":
            d["legendary"] += 1
        if r["_std"] and rarity_en == "EPIC":
            d["epic"] += 1

    # ---- 按系列统计 ----
    set_counter = Counter()
    for r in owned_rows:
        set_counter[r.get("卡组", "?")] += 1

    report = {
        "csv": os.path.abspath(csv_path),
        "total_owned": total,
        "standard_owned": std_total,
        "per_class": {k: dict(v) for k, v in sorted(per_class.items(), key=lambda x: -x[1]["owned"])},
        "per_set": dict(sorted(set_counter.items(), key=lambda x: -x[1])),
    }

    print(f"收藏 CSV: {os.path.abspath(csv_path)}")
    print(f"共拥有 {total} 张可收集卡牌，其中标准环境可用 {std_total} 张")
    print()
    print(f"{'职业':<8}{'总拥有':>6}{'标准':>6}{'标准传说':>8}{'标准史诗':>8}")
    for cls, d in report["per_class"].items():
        print(f"{format_class_key(cls):<10}{d['owned']:>6}{d.get('std', 0):>6}{d.get('legendary', 0):>8}{d.get('epic', 0):>8}")
    print()
    print("按系列统计（卡组=系列代码）:")
    for s, n in report["per_set"].items():
        mark = "标准" if carddata.is_standard_set(s, standard_sets) else "狂野"
        print(f"  {s:<35}{n:>5}  {mark}")
    print()

    # ---- 指定职业卡池 ----
    if args.cls:
        cls_en = normalize_class(args.cls)
        if cls_en is None:
            print(f"错误: 无法识别职业「{args.cls}」。可用: {', '.join(CLASS_ZH.values())}", file=sys.stderr)
            sys.exit(1)
        pool = [
            r for r in owned_rows
            if not r["_is_hero"]
            and (r["_cls_en"] == cls_en or cls_en in r["_classes"])
            and (args.all or r["_std"])
        ]
        pool.sort(key=lambda r: (int(r.get("法力值", 0) or 0), r.get("名称", "")))
        print(f"===== {CLASS_ZH.get(cls_en, cls_en)} 可用卡池（{'全部' if args.all else '仅标准环境'}，共 {len(pool)} 张）=====")
        text_by_id = {}
        if args.with_text:
            for c in carddata.load_cache("zhCN"):
                if c.get("id"):
                    text_by_id[c["id"]] = c
        ids_line = []
        for r in pool:
            type_zh = r.get("类型", "")
            mark = "" if args.all or r["_std"] else "  [已退环境]"
            owned = int(r.get("总拥有数", 0) or 0)
            extra = ""
            if r["_cls_en"].startswith("MULTI:"):
                extra = " [多职业: " + "、".join(CLASS_ZH.get(c, c) for c in r["_classes"]) + "]"
            rune = by_id.get(r.get("卡牌ID"), {}).get("runeCost") or {}
            rune_parts = [f"{RUNE_ZH[k]}×{v}" for k, v in sorted(rune.items()) if v]
            rune_str = (" 符文 " + " ".join(rune_parts)) if rune_parts else ""
            print(f"  [{r.get('卡牌ID')}] {r.get('名称')} ({r.get('法力值')}费) {r.get('稀有度')} x{owned} {r.get('卡组')}{rune_str}{mark}{extra}")
            if args.with_text:
                cc = text_by_id.get(r.get("卡牌ID"))
                if cc:
                    desc = clean_card_text(cc.get("text"))
                    if desc:
                        print(f"      {desc}")
            ids_line.append(f"{r.get('卡牌ID')}:{owned}")
        print()
        print("上述卡牌 ID 与拥有数（供组卡时引用）:")
        print(",".join(ids_line))

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
