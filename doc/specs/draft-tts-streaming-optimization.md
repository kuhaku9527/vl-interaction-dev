# Spec Draft：TTS 链路流式化（LLM 流式 + Sentence Buffer 分句 TTS）

> 生命周期: **草稿**（2026-08-12）——P1 任务（用户反馈"打断后新回复 TTS 慢"）；走 草稿→实现→QA→真机 流程
> 上游: `doc/research/tts-latency-analysis-2026-08-12.md`（瓶颈定位）+ 块3 报告 Sentence Buffer（P0 原语）+ `turn_controller.py` SentenceBuffer 原型
> 状态: 设计草稿（未改代码）

---

## §1 因果链（Why）

- **用户体感**：打断 P0 后旧音频即时停，但**新回复 TTS 慢**（2.6-3.5s 才出第一声）。
- **瓶颈**（分析报告）：三处"完整"串行等待——LLM 非流式完整生成（300-1500ms）→ MiniMax 单次全量合成（300-2000ms）→ 前端等完整 WAV blob。无一流式、无分句。
- **业界对照**（块3）：流式并行 + Sentence Buffer = 755ms E2E（Salesforce 实测）。Sentence Buffer 是级联流水线最关键原语。

## §2 范围与负面约束

- **做**：LLM 流式出口（webinfer stream=True）→ jarvis 流式消费 → **Sentence Buffer 按句分句** → 分句 TTS 合成 → 前端逐句播放。
- **不做**（本草案）：MiniMax SSE 真流式（P0-B 后续）；首句预合成（P1）；前端 MediaSource 流式播放（P0-B 配套）；改动 decision token 语义；改 KWS/ASR。

## §3 方案

### P0-A：LLM 流式 + Sentence Buffer 分句 TTS

**链路（改后）**：
```
用户 utterance → jarvis ASR 2s endpoint → webinfer stream=True（decision token 先行）
→ jarvis 流式消费：先收 decision（silence/response/delegation），再收 content token
→ SentenceBuffer 按句边界（.!?。！？ + 17 缩写排除）flush 句子
→ 每句一次 MiniMax 合成（短句合成快，首音 150-250ms）
→ 前端逐句播放（音频队列，epoch 守卫复用）
```

**改动点**：
1. **webinfer**（`infer_loop.py`）：主对话 `_call_main_model`/`_handle_chat_payload` 增加 stream=True 路径——先流式收 decision token（首帧），再流式收 content；现有非流式路径保留（call 模式等）。
2. **jarvis**（`jarvis_mode.py` `_send_to_llm`）：消费流式——decision 先到（决定 silence/response），content 按句 flush；每句调 :8985 合成（短文本单次合成）→ 音频经 WS 推前端。
3. **Sentence Buffer**：复用 `turn_controller.py` 的 `SentenceBuffer`（min_sentence_length=10 / max_buffer_chars=500 / flush_on_timeout_ms=500）——jarvis 侧 import 使用（decision token strip 后按句）。
4. **前端**（`index.html`）：`playLlmReplyAudio` 改**逐句播放队列**（每句一个音频，顺序播放；`stopLlmReplyAudio`/epoch 守卫对队列整体生效——用户开口即停整队列）。

**风险**：
- webinfer 流式改动的回归面（既有非流式路径/测试）——流式作为新路径，非流式保留；
- decision token 流式时序（先 token 后 content）——解析逻辑复用 `parse_model_decision`（对完整 raw 有效；流式需"先收 decision 再 content"的协议约定，验证模型行为）；
- 前端队列播放的稳定性（断句失败/超时 flush）。

**验收**：首句 TTS 首音 ≤800ms（现 2.6s+）；打断后新回复体感明显变快；决策 token/退出词/打断行为零回归。

### P0-B（后续）：MiniMax SSE 流式
`cloud_clone.py` SSE 已实现（L423-439），仅需 server.py/jarvis 启用 streaming=true + 前端流式播放（MediaSource/WebAudio）——粒度更细，但前端复杂，排 P0-A 之后。

## §4 Harness / 验证

1. 单元：SentenceBuffer 复用测试（已有 64+）；webinfer 流式路径测试（decision 先到、content 流式、非流式回归）；
2. 集成：jarvis 流式消费 + 分句调 TTS 的 mock 测试；
3. 前端：逐句队列 + epoch 停止的静态断言；
4. 真机：你说"bt"→ 提问 → 计时首声（目标 <800ms）→ 打断后再问（旧声即停、新声快出）。

## §5 待明确

- webinfer 流式对 call 模式（无 decision token）的影响（call 保持非流式？）；
- 分句粒度（标点句 vs 固定时长 flush）对 TTS 自然度的影响；
- 前端队列 vs WebAudio 逐句拼接（先队列，够用即可）。

## §6 关联

- 分析：`doc/research/tts-latency-analysis-2026-08-12.md`
- SentenceBuffer 原型：`services/webui/src/joy_interaction_webui/turn_controller.py`
- 打断 P0：`fix(webui): barge-in P0`（前端 epoch 守卫复用）
- 块3：`final_report_unified_turn_controller.md` §6（Sentence Buffer 1500→755ms）
