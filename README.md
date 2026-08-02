# Hearthstone Deck Builder

炉石传说组卡技能 — 按玩家实际拥有的卡牌组 30 张卡组，并生成可导入游戏的卡组代码。

## 功能

- **收藏导出**：通过 HSReplay API 或 HearthMirror（读游戏内存）导出玩家卡牌收藏为 CSV
- **收藏分析**：按职业/稀有度/系列统计，列出标准环境可用卡池
- **卡牌查询**：中英文卡名 → 卡牌 ID（内置索引，无需联网）
- **卡组代码**：生成/解码标准 base64 卡组代码，硬性校验"恰好 30 张 + 收藏足够"

## 快速开始

```bash
# 1. 导出收藏（需要炉石客户端运行中）
python script/export_collection_mirror.py --output data/collection_mirror.csv

# 2. 分析收藏，看看各职业有什么标准环境卡牌
python script/collection_report.py --csv data/collection_mirror.csv
python script/collection_report.py --csv data/collection_mirror.csv --class 死亡骑士 --with-text

# 3. 组好 30 张后生成卡组代码（卡牌可用 ID 或 中英文卡名）
python script/deck_builder.py --hero PALADIN \
    --cards "TLC_426:1,CORE_CS2_093:2,奉献:2" \
    --csv data/collection_mirror.csv
```

> 如果系统没有全局 `python`，用 uv 运行（脚本自带运行时检测）：
> `uv run --python 3.12 --no-project python script/xxx.py ...`

## 文件说明

| 路径 | 说明 |
|---|---|
| `SKILL.md` | 技能完整文档（AI 使用说明） |
| `script/*.py` | 核心脚本（见 SKILL.md 脚本一览） |
| `script/cards_index.json` | 中英文卡名 → ID 索引 |
| `script/lib/*.dll` | .NET DLL 依赖（需手动获取，见 script/lib/README.md） |
| `data/standard_sets.json` | 当前标准环境系列（每年轮换后更新） |
| `data/collection_mirror.csv` | 收藏数据（用户数据） |

## 许可

本技能脚本可自由使用。`script/lib/` 中 DLL 的许可详见 script/lib/README.md。
