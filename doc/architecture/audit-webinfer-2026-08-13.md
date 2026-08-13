# webinfer 面只读代码审查报告（audit-webinfer-2026-08-13）

- 日期：2026-08-13
- 范围：`services/webinfer/` 全目录（重点：`infer_loop.py` / `frame_parsing.py` / `stream_protocol.py` / `chat_payload.py` / `response_format.py` / `prompt_assembly.py` / `prompt_constants.py` / `io_utils.py` / `request_parsing.py` / `adapter_types.py` / `app.py` / `live_adapter.py` / `memory_io.py`，并交叉核对 `session.py` / `summarizer_routing.py` / `prompt_building.py` / `time_ranges.py` / `adapter_core.py` 与 webui 侧调用方 `live_llm.py` / `jarvis_mode.py`）
- 方式：只读；grep + 读码实证；未改动任何代码；未提交
- 背景：巨石解耦（infer_loop 拆 frame_parsing/stream_protocol/chat_payload + 去重 io_utils.normalize_image_b64 / prompt_assembly.compose_live_visual_messages）后的"再检查一遍"

## 严重度统计

| 级别 | 数量 | 说明 |
|---|---|---|
| P0（必修） | 0 | 未发现默认路径上会导致崩溃/数据损坏/服务不可用的直接 bug（如实说明，不凑数） |
| P1（建议修） | 4 | 有代码证据的真实缺陷 / 高影响遗留隐患 / 配置陷阱 |
| P2（可选） | 12 | 死代码、重复实现、健壮性、卫生问题 |

标注：①真 bug（有代码证据）；②过度设计（可拆回/合并）；③死代码；④前项目组遗留隐患。

---

## P1 发现

### P1-1 ①真bug：live visual 路径会静默丢失用户文本（当 message content 为 list 时）

- 位置：`infer_loop.py:326-330`（`_handle_text_payload`）、`infer_loop.py:518-523`（`_stream_text_payload_frames`），配合 `prompt_assembly.py:102-128`（`compose_live_visual_messages`）
- 问题：两条文本路径提取 `last_user_text` 时只接受 `isinstance(m.get("content"), str)`，忽略 OpenAI 多模态格式 `content: [{type: "text", text: "..."}]`。而 `compose_live_visual_messages` 会把 caller_messages 的**最后一条 user 消息整体丢弃**（`history_messages[:-1]`），改用 `last_user_text` + frames 重建视觉 user 消息。
- 影响：若调用方在带 `frames` 的 live round 里用标准 OpenAI list 格式发送提问（该端点本就接受 list content，见 `handle_text_chat` 校验逻辑），`last_user_text == ""` → 用户问题**整段消失**，模型只收到纯图像。属静默数据丢失，且与 `request_parsing._extract_user_prompt_text`（能处理 list）行为不一致。
- 建议修复：抽取一个 `_extract_last_user_text(messages)` 复用 `_extract_user_prompt_text` 的 list 分支；`compose_live_visual_messages` 的"丢弃尾 user"逻辑应只在"该尾 user 的文本已被 `last_user_text` 承载"时生效。

### P1-2 ④前项目组遗留隐患：多模态路径 qa_history 无界增长（qa_history_window 只约束了文本路径）

- 位置：`memory_io.py:426-428`（`_update_text_qa_history` 里唯一一处 `del qa_history[:...]` 裁剪）；`response_format.py:208-249`（`archive_chunk_response_records` 只 append 不裁剪）；`prompt_building.py:167-181`（`build_dynamic_system_content` 渲染**全部** `archived_in_chunk < current_chunk_index` 的条目）
- 问题：`qa_history_window`（默认 12，注释"root cause 1: 不裁剪会导致上下文溢出"）只被文本路径调用。视频/多模态路径每次 chunk 边界 `archive_chunk_response_records` 都向 `memory_state["qa_history"]` 追加，从不裁剪；且 qa_history 渲染在**系统消息内部**，而 `_trim_messages_to_ctx`（`prompt_building.py:82-93`）永远保留 `messages[:1]`（系统消息）——所以 prompt guard 也救不了它。
- 影响：长会话下 qa_history 无界增长 → 系统提示词越来越大 → 正是上游 PR #25 想修掉的上下文溢出问题，在多模态路径上依旧存在（文本路径修了，视频路径漏了）。同时内存持续增长。
- 建议修复：`archive_chunk_response_records` 追加后按 `qa_history_window` 裁剪（或把裁剪抽到 `memory_io` 的公共 helper 里，文本/视频两路径共用）；给该行为补视频路径回归测试（现有 `test_context_overflow_bounds.py` 只测了文本路径的 `_MemIO`）。

### P1-3 ④前项目组遗留隐患：USE_PROMPT_AS_QUERY=0 + 默认 FORCE_SILENCE_BEFORE_QUERY=1 时，live 模式永远强制静音、永不推理

- 位置：`chat_payload.py:24-60`（`update_query_state` 在 `use_prompt_as_query=False` 时返回 None 且**不设置** `state.current_query_text`）、`chat_payload.py:63-78`（`is_forced_silence`：live + force + 无 query → True）、`infer_loop.py:934-960`（`_chat_payload_build_and_infer` 强制静音分支直接产出 `</silence>`、跳过模型调用）
- 问题：`USE_PROMPT_AS_QUERY=0` 是文档化的配置项，但关闭后 `current_query_text` 永远不会被设置；`force_silence_before_query` 默认开启。两者叠加 → `/v1/chat/completions` 在 live 模式下**每一轮都走强制静音分支，从不调用主模型**（`/v1/text/chat` 文本路径不做强制静音，不受影响，因此线上可能只表现为"视频路径哑火"）。
- 影响：配置陷阱，症状严重（助手永远不回答）且难排查。
- 建议修复：`is_forced_silence` 的判定应显式纳入 `use_prompt_as_query`（如 `not current_query_text and use_prompt_as_query`），或在 `update_query_state` 关闭 query 跟踪时同时把 `force_silence_before_query` 视为关闭；至少要在启动日志/文档中明确该组合的后果。

### P1-4 ①真bug：live visual frames 绕过 `max_pixels` 缩放（与视频路径不一致）

- 位置：`prompt_assembly.py:46-73`（`_build_live_visual_user_message` 直接把 `data:image/jpeg;base64,<b64>` 原样发出，无 resize）；对比视频路径 `io_utils.py:214-235`（`_internal_message_to_openai` → `_file_to_data_url(..., max_pixels=...)` 会缩放）
- 问题：`max_pixels`（默认 1048576 = 1MP）是全局图像预算配置；视频路径对 path/data_url 都会缩放，live visual 新路径（`/v1/text/chat` + frames）完全不缩放。同时 `_estimate_messages_chars`（`prompt_building.py:66-67`）对每张图只计 1024 字符占位，prompt guard 严重低估真实体积（4K 截图 base64 ≈ 10MB+）。
- 影响：屏幕捕获帧以全分辨率（可能数 MB/张、每轮最多 6 张）直达主模型 → 上下文/token 成本暴涨、长上下文溢出；`max_pixels` 配置项在该路径静默失效。
- 建议修复：在 `_parse_live_frames` 后或 `_build_live_visual_user_message` 内对 `image_b64` 应用与视频路径相同的 resize（复用 `_resize_data_url_if_needed` / `_image_to_data_url` 逻辑），并把 `_estimate_messages_chars` 的 image 占位与实际缩放后体积对齐。

---

## P2 发现

### P2-1 ②过度设计：流式帧协议逻辑存在两份实现（`build_stream_frames` 是纯测试预言机）

- 位置：`stream_protocol.py:69-161`（`build_stream_frames`） vs `infer_loop.py:495-675`（`_stream_text_payload_frames` 内联同款 decision-first + content + late-marker 逻辑）
- 问题：生产路径（`_stream_text_payload_frames`）并不调用 `build_stream_frames`，两者是**平行的两套实现**（后者多一个 `corrected: true` 帧且已发出 content 无法召回，前者直接重建帧列表）。全部 webinfer/webui 测试都在测 `build_stream_frames`，生产路径的帧逻辑只被 HTTP 级测试覆盖。
- 影响：两份实现极易漂移（注释已承认二者行为有差异）；对"拆分零行为变化"的承诺是持续的验证负担。
- 建议：让 `_stream_text_payload_frames` 显式复用 `build_stream_frames` 的判定（或至少抽一个共享的"单 delta 状态机"纯函数），把 `build_stream_frames` 收敛为唯一实现。

### P2-2 ②过度设计：`_handle_text_payload` 与 `_stream_text_payload_frames` 重复约 45 行

- 位置：`infer_loop.py:325-373` vs `infer_loop.py:518-561`（memory recall → last_user_text → composed_system → caller_messages → frames/compose_live_visual_messages → prompt guard）
- 问题：两条文本路径（流/非流）的前半段逐行相同，仅末尾 `stream` 参数不同。
- 影响：后续改 prompt 组装/guard 需要同步改两处（本次拆分本该消除的重复）。
- 建议：抽 `_compose_text_http_messages(state, payload, frames=...)` 公共方法。

### P2-3 ②过度设计：event_json 兜底导入块在 webinfer 内重复 4 份

- 位置：`infer_loop.py:91-127`、`memory_io.py:21-57`、`session.py:31-67`、`memory_store_client.py:43-79`（同一段 `_ensure_event_json_importable` + try/except + no-op `emit_event` 逐字复制；memory-store 服务里还有 2 份）
- 影响：DRY 违规；将来改兜底策略要改 4+ 处。
- 建议：把这段收敛到 `services/common/event_json.py` 自身（或 webinfer 内一个 `_event_compat.py`），各模块直接 import。

### P2-4 ③死代码：拆分后遗留孤儿函数/状态

- `request_parsing.py:58-81` `_extract_first_image_ref`：全仓（排除 build/）无调用者。
- `prompt_assembly.py:431-433` `_build_main_api_messages`：无调用者（`_build_cached_api_messages` 才是实际路径）。
- `time_ranges.py:146-149` `_format_seconds`：无调用者（与 `_format_seconds_words` 并存）。
- `adapter_types.py:223` + `frame_parsing.py:154` `session_frame_counter`：生产代码只增不减、无人读取（写后即弃的遗留计数）。
- 建议：删除或标注 deprecated；`session_frame_counter` 若确无用途直接移除（其 bump 行为有测试锁定，需同步改测试）。

### P2-5 ③死代码/重复常量：`DEFAULT_SYSTEM_PROMPT` 与 `DEFAULT_SYSTEM_PROMPT_EN` 逐字相同

- 位置：`prompt_constants.py:58-69` 与 `70-81`（两份完全一致的三态 prompt；后者仅被测试 `test_qa_4state_supplement.py` 引用）
- 影响：恰好违背该文件自己的"centralized to prevent drift"目标；后续改 prompt 容易只改一份。
- 建议：删除 `DEFAULT_SYSTEM_PROMPT`，测试改用 `DEFAULT_SYSTEM_PROMPT_EN`。

### P2-6 ①真bug（校验缝隙）：`frames` 为 falsy 的非法类型会被静默当作"无 frames"

- 位置：`infer_loop.py:203` `if payload.get("frames"):`
- 问题：`frames: ""` / `frames: 0` 等 falsy 值跳过 `_parse_live_frames` 校验，直接走纯文本路径；而 `frames: "not-a-list"`（truthy）会被 400。与"约法三章 — invalid frames 必须显式 400，绝不静默吞掉"的注释相悖。
- 建议：改为 `if "frames" in payload and payload.get("frames") is not None:` 先过校验，或对非 list/非空值显式 400。

### P2-7 ①真bug（健壮性）：`normalize_image_b64` 边界 + 导入期 env 崩溃风险

- 位置：`io_utils.py:82-112`、`io_utils.py:19`
- 问题：
  - `base64.b64decode(padded, validate=True)` 对 URL-safe base64（`-_`）与 MIME 折行（内嵌 `\n`）直接 400；`_parse_live_frames` 对每个 frame 的 `image_b64` 无字节数上限（仅受 app 128MB body 限制，6 帧可到 ~120MB，解码内存翻倍）→ 潜在 DoS/内存峰值。
  - `_DEFAULT_JPEG_QUALITY = int(os.getenv("JOYAI_JPEG_QUALITY", "92"))` 在模块导入期执行，env 值非法时整个服务起不来，且无日志上下文。
- 建议：b64decode 前 strip 空白/容忍 `-_`；给 live frames 加每帧字节上限（与 `client_max_size` 联动）；env 读取加 try/except 回退默认值。

### P2-8 ④前项目组遗留隐患：`_file_to_data_url_cached` 对"同路径覆盖写入"返回陈旧帧

- 位置：`io_utils.py:121-154`（lru_cache 以 `(path, max_pixels)` 为键，docstring 已承认"覆盖同一路径不自动失效"）
- 问题：视频路径若以固定路径（如 `x-local-image-path: /tmp/frame.jpg` 每帧覆盖）引用图像，缓存会一直返回**第一帧**的 base64，直到 LRU 逐出；而旧的 `cache_clear()` 已被移除。
- 影响：当前 webui 主要传 data URL（`jarvis_mode.py:1381`、`live_frames.py`），暂未踩中；但该路径一旦被复用即出现"模型永远看旧画面"的隐蔽 bug。
- 建议：至少把该风险写进 README；或在 path 键中加入 `(st_mtime_ns, size)`。

### P2-9 ④前项目组遗留/一致性：`_forward_text_only` 完全绕过系统 prompt / memory / qa_history，且 content 携带原始 decision token

- 位置：`infer_loop.py:1094-1120`
- 问题：`/v1/chat/completions` 无图像时直接透传 `payload["messages"]` 给主模型——不组装四态/角色/记忆 system prompt，不更新 qa_history，`content` 返回原始 `</response>`/`</delegation>` token（测试已锁定该行为为"by design"，ADR 0008 §9）。与文本路径（剥离 token、记录 history、注入记忆）行为不一致。
- 影响：同一网关两条路径对同一模型输出呈现给用户的内容格式不同（视频透传路径会把 `</silence>` 暴露给前端），跨路径 QA history 也会断裂。
- 建议：短期在 README/API 文档标注；中期让无图 chat/completions 也走 `_handle_text_payload` 的组装管线。

### P2-10 ①真bug（边界）：委派轮次原始文本污染 qa_history，带 `</delegation>` 标签回流到后续 prompt

- 位置：`response_format.py:56-62`（`extract_response_payload` 不识别 delegation）、`infer_loop.py:1033-1035`（`_chat_payload_finalize` 把 `extract_response_payload(generated_text)` 存入 response_records）、`response_format.py:208-249`（归档进 qa_history）、`prompt_building.py:178-179`（渲染到后续 prompt）
- 问题：`</response> note </delegation> question` 经 `normalize_model_output`/`extract_response_payload` 得到 `note </delegation> question`，作为 response 归档；后续轮次渲染 qa_history 时，模型会在历史里看到裸的 `</delegation>` 控制标签（`skip_special_tokens=False` 下同样会回灌主模型）。
- 影响：决策框架被历史污染，可能诱发模型乱用标签；用户侧 content 因 `strip_decision_tokens` 不受影响。
- 建议：`extract_response_payload` 对 delegation/not-for-me 输出返回 None（或先 `strip_decision_tokens` 再入库）。

### P2-11 ①真bug（小）/一致性：`handle_chat_completions` 的 JSON 解析 400 不是 OpenAI 错误格式

- 位置：`infer_loop.py:677-716`（`payload = await _read_json(request)` 无 try/except；对比 `handle_text_chat:144-147` 有包装）
- 附带：`request_parsing.py:96-113` `_extract_all_image_refs` 在**第一个含图消息**处 break，若历史消息里有多轮含图只取最后一条（与"仅当前轮"设计一致，但若最后一条是 assistant 消息含图会漏当前 user 轮）。
- 建议：统一错误包装；`_extract_all_image_refs` 明确只扫最后一条 user 消息。

### P2-12 ④遗留/卫生：内存画像与本地 build 产物

- `session.py:410` `state.predictions` 列表会话期无界增长，且 `prediction.input.model_input.messages` 对 data_url 帧携带完整 base64（`chat_payload.py:81-98` + `response_format.py:198-205`）；`current_chunk["summarizer_frame_cache"]`（`infer_loop.py:870`）同样存完整 data URL 并在 `_flush_chunk` 时深拷贝——长会话 + 多图轮次内存峰值可达 GB 级。
- 本地 `services/webinfer/build/`（git-ignored，未入库）是拆分前快照：`build/bdist.win-amd64/wheel/adapter_types.py` 仍是旧 `max_pixels=262144`（现行 1048576）。若有人误装该 wheel 会得到旧行为。
- 建议：确认 data_url 帧在 chunk/预测记录中是否必须全量保存（可只存路径/摘要）；提醒删除或重建本地 build 产物。

---

## 拆分本身的质量结论（对本次重构的直接评价）

- 门面 re-export 无行为漂移：`_parse_live_frames is frame_parsing._parse_live_frames` 等恒等性有测试锁定（`test_qa_batch2_split_boundary.py:307-316`），`_LIVE_FRAMES_MAX`、`build_stream_frames` 等 re-export 均保真。
- 共享状态传递无遗漏：`ctx: SimpleNamespace` 在 `_chat_payload_*` 五步间传递完整，未发现丢字段；`chat_payload.py` 的纯函数全部显式传参，无 `self` 隐式耦合。
- 主要问题集中在**拆分后暴露出的历史遗留**（qa_history 裁剪只修了一半、配置组合陷阱、`session_frame_counter` 孤儿状态）与**新 live visual 路径自身的缺口**（list-content 丢文本、max_pixels 失效、falsy frames 校验缝隙），而非拆分本身引入的回归。

## 最高优先级 3 条（供决策）

1. **P1-1** live visual 路径 list-content 用户文本丢失（静默数据丢失，有代码证据）。
2. **P1-2** 多模态路径 qa_history 无界增长（内存泄漏 + 系统提示词溢出，prompt guard 无法兜底）。
3. **P1-3** `USE_PROMPT_AS_QUERY=0` + 默认强制静音 → live 视频路径永远不推理（配置陷阱）。
