---
name: hearthstone-deck-builder
description: |
  炉石传说卡牌工具集：导出玩家收藏、分析收藏、按玩家实际拥有的卡牌组 30 张卡组、
  生成/解码标准 base64 卡组代码。适用于"我有哪些卡，帮我组一套卡组"、
  把网上卡组（英文卡名或卡组代码）转成可导入代码、校验收藏能否组出某套牌等场景。
---

# 炉石传说组卡技能 (Hearthstone Deck Builder)

## 核心定义

- **收藏 (Collection)** = 玩家已拥有的全部卡牌；**卡组 (Deck)** = 从收藏中选出的恰好 30 张牌。
- 标准模式卡组代码**必须恰好 30 张**；不足或超出，炉石客户端无法识别。

## 组卡原则（必须遵守）

1. **以玩家收藏为唯一依据，用你自己的判断组卡。** 不要照搬主流/meta 卡组，不要引用胜率、Tier 榜或"当前最强"之类的信息。
2. **卡牌合法性以本技能内置数据为准，不要凭记忆判断环境。** 标准环境系列见 `data/standard_sets.json`（每年轮换后必须更新）；卡牌属性（费用/稀有度/职业/符文/系列）以 `script/cards_index.json` 为准。
3. 组卡前先运行收藏分析，确认玩家实际拥有哪些卡；每张卡只能用收藏中拥有的数量。
4. 最终必须生成**恰好 30 张**的卡组代码，并传入 `--csv` 通过收藏校验。

## 工作流

### 步骤 1：导出收藏（二选一，已有 CSV 则跳过）

方式 A（炉石运行中，读内存）：

```bash
python script/export_collection_mirror.py --output data/collection_mirror.csv
```

方式 B（HSReplay 网页，需网络）：

```bash
python script/export_collection.py --url "https://hsreplay.net/collection/{region}/{account_lo}/" --output data/collection_mirror.csv
```

### 步骤 2：分析收藏

```bash
# 总览：各职业拥有量、标准环境可用量、按系列统计
python script/collection_report.py --csv data/collection_mirror.csv

# 查看某职业的标准环境可用卡池（AI 组卡时从这里挑选）
python script/collection_report.py --csv data/collection_mirror.csv --class 死亡骑士

# 附带每张卡的完整描述（评估单卡效果时加 --with-text）
python script/collection_report.py --csv data/collection_mirror.csv --class 死亡骑士 --with-text
```

### 步骤 3：组卡（AI 判断）

- 根据收藏分析 + 用户偏好（职业/思路/玩法），从玩家拥有的标准环境卡牌中选 30 张。
- 需要看单卡效果时：`python script/find_card.py "卡名" --text --csv data/collection_mirror.csv`
- 网上英文卡组名 → ID：`python script/find_card.py "Fire Fly" "Grave Strength"`（中英文都支持）
- 组卡时注意：传说卡最多 1 张、其余最多 2 张；卡牌必须属于所选职业或中立；死亡骑士注意符文需求合计 ≤ 3。

### 步骤 4：生成并校验卡组代码

```bash
# 卡牌可用 ID 或 中/英文卡名，脚本自动解析
python script/deck_builder.py --hero DEATHKNIGHT \
    --cards "CORE_RLK_061:2,墓地之力:2,火羽精灵:2,..." \
    --csv data/collection_mirror.csv
```

`deck_builder.py` 硬性校验（任一不满足即拒绝生成）：
- 恰好 30 张卡牌（刻意少于 30 张等场景可用 `--ignore-count` 显式忽略张数限制）
- 传说 ≤ 1 张、其余 ≤ 2 张
- 职业合法、标准环境系列合法
- 死亡骑士符文需求 ≤ 3
- 收藏数量足够（传 `--csv` 时）
- 带 sideboard（备牌/子卡）的卡（如 `ETC_080` 乐队经理、`TOY_330` 奇利亚斯豪华版）必须用 `--sideboard` 显式给出备牌：备牌不计入 30 张，但数量必须恰好等于该卡上限（3 张），否则拒绝生成

### 步骤 5（可选）：解码网上卡组代码

许多卡组网站的代码是 JS 渲染的，抓不到文本。让用户直接粘贴卡组代码或卡名列表：

```bash
python script/deck_builder.py --decode "AAECAZ8F..." --csv data/collection_mirror.csv
```

## 组卡自检清单（生成代码前逐项确认）

- 恰好 30 张，传说 ≤ 1、其余 ≤ 2
- 1–2 费曲线至少约 8 张，有明确的终结/制胜手段
- 有解场手段和过牌（各 4 张以上为宜）
- 死亡骑士：符文需求合计 ≤ 3，`collection_report.py --class 死亡骑士` 会直接显示每张卡的符文
- 全部卡牌均来自标准环境系列（`data/standard_sets.json`）且收藏数量足够
- 若选了带 sideboard 的卡（乐队经理 `ETC_080` / 奇利亚斯豪华版 `TOY_330`），必须另外用 `--sideboard` 给出恰好 3 张备牌列表（不计入 30 张），否则脚本会报错拒绝生成

## 运行环境

- 若本机无全局 `python`，所有脚本可用 `uv run --python 3.12 --no-project python script/xxx.py ...` 运行
- 脚本输出为 UTF-8，Windows 终端乱码时先执行 `chcp 65001`

## 脚本一览

| 脚本 | 功能 |
|---|---|
| `script/export_collection.py` | HSReplay API 导出收藏 → CSV |
| `script/export_collection_mirror.py` | HearthMirror 读游戏内存导出收藏 → CSV（需炉石运行中 + `script/lib/` DLL） |
| `script/collection_report.py` | 收藏分析：职业/稀有度/系列统计 + 标准环境可用卡池清单 |
| `script/find_card.py` | 卡名（中/英）或 ID → 卡牌ID，支持模糊匹配与卡牌效果查看 |
| `script/deck_builder.py` | 生成/解码 30 张卡组代码，硬性校验收藏与合法性；支持 sideboard 备牌（`--sideboard`）与忽略张数限制（`--ignore-count`） |
| `script/carddata.py` | 共享数据模块：加载/重建 `cards_index.json`、读取 `standard_sets.json` |

## 文件结构

```
hearthstone_deckbuilder/
├── SKILL.md                  # 本文件（技能定义）
├── README.md                 # 人读说明
├── data/
│   ├── standard_sets.json    # 当前标准环境系列（轮换时更新，仅合法性，无 meta）
│   └── collection_mirror.csv # 收藏数据（用户数据，勿提交）
└── script/
    ├── carddata.py           # 共享数据加载
    ├── cards_index.json      # 中英文卡名 → ID 紧凑索引（内置查找功能）
    ├── cards_cache_enUS.json # 英文完整卡牌库缓存
    ├── cards_cache_zhCN.json # 中文完整卡牌库缓存
    ├── export_collection.py
    ├── export_collection_mirror.py
    ├── collection_report.py
    ├── find_card.py
    ├── deck_builder.py
    └── lib/                  # HearthMirror 依赖 DLL（许可见 lib/README.md）
```

## 维护事项

- **每年 3 月轮换后更新 `data/standard_sets.json`**，否则标准合法性判断会过期。
- 卡牌索引缺失或过期时重建：`python script/carddata.py`（从两张完整缓存合并生成）。
- 收藏 CSV 是用户数据，不要提交到版本库。

## 许可

- `HearthDb.dll`: MIT License
- `HearthMirror.dll` / `untapped-scry-dotnet.dll`: All Rights Reserved（来自 HDT，仅本地使用）
- `Newtonsoft.Json.dll` / `System.Reflection.DispatchProxy.dll`: MIT License
- 本技能脚本: 可自由使用
