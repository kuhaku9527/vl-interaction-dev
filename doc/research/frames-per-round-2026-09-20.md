# 每轮实际帧数（专家 §5 #12 的答案，2026-09-20）

> 决定训练时的 seq 长度；「错了全盘重来」。

## 实测（源码核实，非推断）

| 路径 | 帧数 | 代码位置 |
|---|---|---|
| **用户轮**（说话提交后） | **最多 6 帧**（整个环形缓冲） | `live_mode.py` `_send_to_llm(..., frames=self._frames_payload(self._recent_frames))` |
| **proactive 轮** | **仅 1 帧**（最新帧） | `live_proactive.py` `frames_payload([recent_frames[-1]])` |

`LIVE_FRAME_WINDOW` 默认 **6**（`live_frames.py`，env 可覆盖）。

## 对训练 seq 长度的影响

- 每帧 768×576 ≈ **448 prompt token**（实测）
- 用户轮图像部分 ≈ 6 × 448 = **2,688 token**
- 加上：system prompt ~845 + 记忆召回 ~2,800 + Wiki ~2,800 + history ~960 ≈ **7,400 token**
- **⇒ 单轮总 prompt ≈ 10,000 token 量级**

**⇒ 训练时 seq 至少需要 4096；若要贴合生产应取 8192。**
专家方案用的 seq 若小于此值，训练的输入分布与生产不一致。

## 待确认
- 生产实际是否真的每次都填满 6 帧（取决于用户说话间隔与 1 fps 采集）
- 主动搭话轮（1 帧）与用户轮（最多 6 帧）的**分布差异**是否需要在训练数据里体现
