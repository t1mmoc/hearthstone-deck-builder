#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享卡牌数据模块（本技能各脚本共用）。

职责：
- 加载/生成 script/cards_index.json —— 中英文卡名 → 卡牌ID 的紧凑索引
  （由 script/cards_cache_enUS.json + cards_cache_zhCN.json 合并生成，按 dbfId 对齐）
- 读取 data/standard_sets.json —— 当前标准环境系列，仅用于合法性判断（不含任何 meta 信息）
- 按需从完整缓存读取卡牌文本（索引刻意不含 text，保持轻量）

用法：其他脚本先 `sys.path.insert(0, script目录)` 再 `import carddata`；
也可直接运行本文件重建索引：`python script/carddata.py`
"""

import json
import os
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(PROJECT_DIR, "data")

INDEX_PATH = os.path.join(SCRIPT_DIR, "cards_index.json")
CACHE_EN_PATH = os.path.join(SCRIPT_DIR, "cards_cache_enUS.json")
CACHE_ZH_PATH = os.path.join(SCRIPT_DIR, "cards_cache_zhCN.json")
STANDARD_SETS_PATH = os.path.join(DATA_DIR, "standard_sets.json")

CARDS_JSON_URL = "https://api.hearthstonejson.com/v1/latest/{locale}/cards.json"
USER_AGENT = "HearthstoneDeckBuilder/2.0"


def _cache_path(locale):
    return CACHE_EN_PATH if locale == "enUS" else CACHE_ZH_PATH


def download_cards(locale):
    """从 HearthstoneJSON 下载完整卡牌库并写入本地缓存。"""
    url = CARDS_JSON_URL.format(locale=locale)
    print(f"下载卡牌数据库 ({locale}) ...", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=240) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"下载失败: {exc}", file=sys.stderr)
        print(f"请检查网络，或手动放置 {os.path.basename(_cache_path(locale))}", file=sys.stderr)
        raise
    path = _cache_path(locale)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"已保存 {path}（{len(data)} 张卡牌）", file=sys.stderr)
    return data


def load_cache(locale):
    """读取完整卡牌缓存，缺失时尝试下载。locale: enUS / zhCN"""
    path = _cache_path(locale)
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return download_cards(locale)


def build_index():
    """用 enUS + zhCN 完整缓存合并生成紧凑索引。"""
    en = load_cache("enUS")
    zh = load_cache("zhCN")
    zh_by_dbf = {c["dbfId"]: c for c in zh if c.get("dbfId")}
    zh_by_id = {c["id"]: c for c in zh if c.get("id")}
    index = []
    seen = set()
    for c in en:
        dbf = c.get("dbfId")
        if dbf is None or dbf in seen:
            continue
        seen.add(dbf)
        z = zh_by_dbf.get(dbf) or zh_by_id.get(c.get("id")) or {}
        index.append({
            "id": c.get("id"),
            "dbfId": dbf,
            "name_en": c.get("name"),
            "name_zh": z.get("name") or c.get("name"),
            "set": c.get("set"),
            "cost": c.get("cost"),
            "rarity": c.get("rarity"),
            "cardClass": c.get("cardClass"),
            "classes": c.get("classes") or [],
            "type": c.get("type"),
            "collectible": bool(c.get("collectible")),
            "runeCost": c.get("runeCost") or {},
        })
    index.sort(key=lambda x: (x["dbfId"] or 0))
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))
    print(f"已生成 {INDEX_PATH}：{len(index)} 张卡牌", file=sys.stderr)
    return index


def load_index(rebuild=False):
    """加载索引；缺失时自动从缓存生成。"""
    if not rebuild and os.path.exists(INDEX_PATH):
        with open(INDEX_PATH, encoding="utf-8") as f:
            return json.load(f)
    return build_index()


def load_standard_sets():
    """
    返回 (标准系列代码集合, 元数据 dict)。
    未找到 data/standard_sets.json 时返回 (None, {})，表示不做标准合法性检查。
    """
    if not os.path.exists(STANDARD_SETS_PATH):
        return None, {}
    with open(STANDARD_SETS_PATH, encoding="utf-8") as f:
        meta = json.load(f)
    return set(meta.get("sets", [])), meta


def is_standard_set(set_code, standard_sets):
    """CORE（核心系列）永远标准合法；其余按 data/standard_sets.json 判断。"""
    if standard_sets is None:
        return True
    return set_code == "CORE" or set_code in standard_sets


def card_text(card_id, locale="zhCN"):
    """按需读取单张卡牌文本（索引不存 text）。"""
    cache = load_cache(locale)
    for c in cache:
        if c.get("id") == card_id:
            return c.get("text") or ""
    return ""


def build_lookups(index):
    """返回 (id → card, dbfId → card) 两个查找表。"""
    by_id = {}
    by_dbf = {}
    for c in index:
        if c.get("id"):
            by_id.setdefault(c["id"], c)
        if c.get("dbfId") is not None:
            by_dbf.setdefault(c["dbfId"], c)
    return by_id, by_dbf


if __name__ == "__main__":
    build_index()
