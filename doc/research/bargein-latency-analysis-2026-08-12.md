# 打断延迟链路分析（barge-in latency）

> 日期: 2026-08-12
> 触发: 用户实机体感"我在说的时候语音还在播放；说完字都显示出来了，上个语音还在播报；可能等新语音返回了才暂停"
> 结论先行: **用户体感推断完全正确**——当前 jarvis 对话路径根本没有"检测到说话即暂停 TTS"，打断依赖"新回复返回时前端切换音频"，属体感+速度优化范围

---

## 一、当前打断链路（jarvis 对话路径，证据链）

| # | 环节 | 现状 | 证据 |
|---|---|---|---|
| 1 | 用户说话（TTS 播放中） | 浏览器 `<audio>` 播放旧回复 | `jarvis_mode.py:1448-1452` 注释："browser plays TTS through `<audio>` via `playLlmReplyAudio`" |
| 2 | ASR 检测新 partial | `_handle_dialog` 收到 `text` | L1264-1276 |
| 3 | **打断判断** | `if self._tts_task and not done(): pause` —— **浏览器路径下 `_tts_task` 为 None → 不暂停** | L1297-1303 |
| 4 | 等 endpoint | 2s ASR 停滞才 commit（保守，防误打断） | L1308-1309 |
| 5 | LLM 推理 + TTS 合成 | GPU TTFT 200-500ms + MiniMax 云 150-250ms | 块3 延迟预算 |
| 6 | 新回复返回 | 前端 `playLlmReplyAudio` → **此时才切换音频** | L1760-1765 附近 |

**根因**：jarvis 的 TTS 播放归属是浏览器（stream_tts=False 为防 WebRTC 重复播放），但**打断机制仍假设进程内 `_tts_task`**——两者错位。用户说话 → 无任务可暂停 → 旧语音持续播放 → 等新回复整个 LLM+TTS 周期（≈2s endpoint + 300-750ms 推理合成）后才停。**总打断响应延迟 ≈ 3s 级，远超块3 报告硬中断 <150ms 目标**。

## 二、对照块3 报告（05_interruption_barge_in_mechanism.md）最佳实践

| 报告原则 | 我们现状 | 差距 |
|---|---|---|
| **客户端先行**：检测到打断立即本地静音，不等服务端确认 | 无——前端不主动停，等新回复 | **缺失（最核心）** |
| 硬中断 <150ms（VAD 20-40 + 确认 100-200 + TTS flush 60） | ~3s | 数量级差距 |
| "宁可慢打断，不可误打断"（false-barge-in 比 slow 更糟） | 2s 停滞=保守（误打断少） | 该原则下可保留部分保守 |
| Pattern 2：上下文精确截断（保留用户听到部分） | 未实现 | 后续 |
| 可取消：ASR/LLM/TTS/Audio Playout 全可取消 | TTS 播放不可取消（浏览器） | 缺失 |

## 三、优化方案（体感优先，分级）

### P0：前端先行停 TTS（体感立竿见影）
- **用户说话信号到达前端即停旧音频**：`playLlmReplyAudio` 播放新音频前先 `pause()` 旧；更佳——后端检测到用户说话（VAD/ASR 首个 partial）→ 经 WebRTC 数据通道/WS 通知前端 → `audio.pause()`。
- 实现：前端 `playLlmReplyAudio` 改为"停止当前 audio + 播新"（最小改动）；后端 partial 事件推前端停 TTS（完整）。
- 对照报告："客户端先行"原则直接落地。误打断风险低：仅当确实有 ASR partial 才停。

### P1：缩短 endpoint（速度）
- 当前 2s 停滞才 commit。块3 建议语义级端点（Smart Turn v3.2 **已有**，默认关）——开启后可提前 commit（语义完整即发），省 1-1.5s。
- 需验证 Smart Turn 在 jarvis 对话路径的误打断率（"宁可慢不可误"）。

### P2：架构级（Phase C 时接线）
- 统一 TurnController 的 `on_barge_in` → 前端停止信号（Phase C live 接入时一起做）。
- 打断后上下文截断（Pattern 2）+ interrupted flag 注入 LLM。

## 四、结论

**属体感优化 + 速度优化范围**（用户问的正是）。核心修 **P0 前端先行停 TTS**（当前链路压根没这环），P1 用已有 Smart Turn 提速度。方案待用户确认后写 spec 草稿（`draft-bargein-latency-optimization.md`）→ 实现 → 正式 spec。
