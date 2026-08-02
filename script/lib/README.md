# lib/ — .NET DLL 依赖

本目录应包含以下 5 个 DLL 文件（共约 42MB）：

| 文件 | 大小 | 用途 | 许可证 |
|---|---|---|---|
| `HearthMirror.dll` | ~321KB | 从游戏内存读取收藏数据 | All Rights Reserved (HearthSim) |
| `HearthDb.dll` | ~36MB | 卡牌元数据数据库（7367+ 张卡） | MIT |
| `Newtonsoft.Json.dll` | ~684KB | HearthMirror 依赖 | MIT |
| `System.Reflection.DispatchProxy.dll` | ~35KB | HearthMirror 依赖 | MIT |
| `untapped-scry-dotnet.dll` | ~4.3MB | HearthMirror 依赖 | 未知 |

## 为什么本仓库不提供这些 DLL

**许可限制**：`HearthMirror.dll` 和 `untapped-scry-dotnet.dll` 来自 Hearthstone Deck Tracker (HDT)，其许可证为 All Rights Reserved（保留所有权利），不允许重新分发。虽然 HDT 源码公开可读、免费下载使用，但未经许可不得将其组件打包进其他项目分发。

## 如何获取

### 方式一：安装 HDT（推荐）

1. 下载并安装 [Hearthstone Deck Tracker](https://hsreplay.net/downloads/)
2. 安装后 DLL 位于：`%LOCALAPPDATA%\HearthstoneDeckTracker\app-x.x.x\`
3. 脚本会自动检测该路径，或可通过 `--hdt-path` 指定

### 方式二：手动复制

如果你已安装 HDT，可以手动将上述 5 个 DLL 从 HDT 安装目录复制到本目录：

```powershell
$hdt = "$env:LOCALAPPDATA\HearthstoneDeckTracker\app-*"
$latest = (Get-Item $hdt | Sort-Object Name -Descending)[0].FullName
$dlls = @("HearthMirror.dll", "HearthDb.dll", "Newtonsoft.Json.dll",
          "System.Reflection.DispatchProxy.dll", "untapped-scry-dotnet.dll")
foreach ($dll in $dlls) { Copy-Item "$latest\$dll" . }
```

复制后脚本会自动从本目录加载，无需再指定 `--hdt-path`。
