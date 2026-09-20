# 游戏 + JoyAI 显存实测清单（2026-09-20）

> 给用户的**逐步骤操作清单**。目标：测出「JoyAI 与游戏同时跑」时，
> 游戏能开到什么画质/分辨率，以及是否真的够用。

## 实测工具（已写好并验证）

`scripts/measure-game-vram.ps1` —— **已实测可用**，非管理员权限即可跑。
它输出：总显存时间序列 + **per-process 占用**（实测能列出 `dwm 197.6 MiB` 等）。

**已验证命令**：
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/measure-game-vram.ps1 -Mode baseline -Label verify-baseline -DurationSec 15
```

## 已确认的关键数字（实测）

| 项 | MiB |
|---|---|
| 显卡总显存 | 16,311 |
| **桌面基线**（Chrome/QQ/微信/DSH 等） | **446** |
| **JoyAI llama-server**（KV q8_0 已落地） | **7,664** |
| **游戏可用余量** | **8,201 MiB（8.0 GiB）** |

**⇒ 1080p 中高画质（~6,000）与 1440p 高画质（~7,000）均已达标。**

---

## 实测方案（三档，总计约 15 分钟）

### 准备
1. **先起 JoyAI**（若未起）：
   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File start-joyai.ps1
   ```
   等 7060 health 返回 ok（约 30 秒）。

2. **确认基线**（可选，快速）：
   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/measure-game-vram.ps1 -Mode baseline -Label joyai-idle -DurationSec 30
   ```
   预期：总量约 **8,100 MiB**（= 446 桌面 + 7,664 JoyAI）。

### ★ 主力游戏：**鸣潮**（`E:\Wuthering Waves\launcher.exe`，116GB，完整）

**为什么选它**：本机唯一完整且吃显存的 3A 级游戏。辐射4 也可用，但引擎老、显存需求低。

| 档 | 分辨率 | 画质预设 | 操作 |
|---|---|---|---|
| **A** | 1920×1080 | 低 | 启动 → 设置 → 图像 → 分辨率 1080p，预设「低」 |
| **B** | 1920×1080 | **中/高** | 同上，预设调到「中」或「高」 |
| **C** | 2560×1440 | 中/高 | 分辨率调 1440p（**你的显示器原生就是 1440p**） |

**每档怎么做**：
1. 改好设置后**进游戏、站定不动**（跑动会让画面变化、污染对比）
2. 另开一个终端跑：
   ```bash
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/measure-game-vram.ps1 -Mode session -Label wuthering-A-1080p-low -DurationSec 60
   ```
3. **跑够 60 秒**（脚本自己会采 60 秒）
4. **顺手截一张画质设置页的图**（证明档位）
5. 记录：**游戏内显示的帧率**（鸣潮有 FPS 显示，或看是否卡顿）

**然后重复 B、C 两档。**

### 验证项：哪一档开始出问题？

| 现象 | 含义 |
|---|---|
| 总显存 < 15,000 MiB | ✅ 安全 |
| 总显存 15,000–16,000 | ⚠️ 临界，可能卡顿 |
| 总显存触及 16,311 或游戏崩溃 | ❌ **超出，需降档或关 JoyAI** |

**同时观察**：JoyAI 是否还正常响应（改设置时问它一句话，看它能否回答）。

### 可选：辐射4（`E:\Games\Fallout4_984\Fallout4.exe`，130GB）
若有余力，按同样方法测一档（1080p 高），作为"老引擎"对照。

---

## 测完把结果给我

**要给我的信息**：
1. 三档各自的 `measure-out/*-summary.json`（脚本自动生成）
2. 三档的**游戏内帧率**
3. 三张画质设置页截图
4. 主观感受：**卡不卡**

**我会做的事**：把结果与调研的"1440p 需 ~7GB"对照，给出最终结论：
- 哪档是甜点
- 要不要再做 mmproj Q8_0（再省 ~400–540 MiB）
- 要不要给游戏留更多（如游戏时临时关掉某些服务）

---

## ⚠️ 注意事项

- **不要同时跑多个测量**（脚本 + 游戏 + 其他任务会互相干扰）
- **JoyAI 必须已启动**，否则测的不是"同时跑"
- 若游戏崩溃或系统卡死，**立即停脚本并告诉我**——那本身就是结论（说明那档不可行）
- 本清单**不涉及训练、不涉及模型修改**，纯测量
