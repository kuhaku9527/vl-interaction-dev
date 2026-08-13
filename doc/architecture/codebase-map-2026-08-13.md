# 代码库模块地图 + 巨石文件解耦计划

- 日期：2026-08-13
- 作者：软件工程师（寇豆码）｜只读盘点，未改动任何代码（`git status` 保持干净）
- 用途：作为「巨石拆解专项」的输入，覆盖 services/webinfer、services/webui、services/asr 及其它 service、前端 static
- 方法：全部数据来自 `wc -l` / `grep` / `sed` 实证，不凭记忆

---

## 0. 总体结论（TL;DR）

| 维度 | 数据 |
|---|---|
| 盘点目录 | webinfer(19 py / 7317 行)、webui(22 py / 14331 行)、asr、tts、memory-store、voice-clone、background-agent、common、kws-training、前端 static |
| 官方「4 巨石」 | jarvis_mode.py(2307) / live_mode.py(1479) / infer_loop.py(1500) / index.html(6996 行 / 335870 字符) |
| **额外发现巨石** | **server.py(2288 行)** —— 团队任务书未点名，但行数位列 webui 第二，同样需要进解耦计划 |
| 次大文件（可接受） | background_model.py(1348)、turn_controller.py(1045)、memory_summarizer.py(878)、vlm_service.py(774) |
| 已拆好的正面例子 | webinfer 9+1 门面拆分（live_adapter 73 行门面 / ADR-0007）、前端 9 个独立 JS + 5 个 `window.JoyXxx` 命名空间（D-033）、turn_controller 纯逻辑拆分、turn_streaming 共享消费器 |
| 测试基座 | 132 个测试文件（webinfer 28 + webui 64 py + 6 js + asr 1 + tts 1 + 其它）；巨石均有大量回归测试守护 |

**解耦总优先级**：`infer_loop`（收益/风险比最高）→ `index.html`（纯搬移零逻辑）→ `live_mode`（与 jarvis 共享 TTS 链路先去重）→ `server.py`（路由/会话/配置三分离）→ `jarvis_mode`（最复杂，最后动）。

---

## 1. 模块地图表

### 1.1 services/webinfer/（推理网关 :8070，19 文件 / 7317 行）

| 文件 | 行数 | 职责 | 拆解建议 |
|---|---|---|---|
| **infer_loop.py** | **1500** | 主推理循环 Mixin：text/chat + chat/completions 两个端点、frames 解析、NDJSON 流式协议、决策解析、chat payload 五步编排、主模型调用 | **巨石 → 可拆**（见 §2.3） |
| memory_summarizer.py | 878 | 长对话摘要（线程池并发、OpenAI 调用） | 保持（已是独立域） |
| app.py | 570 | aiohttp 应用工厂、参数解析、CLI 入口 | 保持 |
| session.py | 556 | 会话生命周期、输出持久化、debug 输入持久化 | 保持 |
| prompt_assembly.py | 502 | 角色档案缓存、system/memory 提示词、主消息组装（含 `_build_live_visual_messages`） | 保持（是消息组装的**正确归属地**） |
| memory_io.py | 442 | memory-store warmup/recall/push、qa_history 归档 | 保持 |
| memory_store_client.py | 439 | memory-store v0.2 客户端（熔断器） | 保持 |
| system_prompts.py | 368 | 角色提示词（persona）加载与组装 | 保持 |
| summarizer_routing.py | 358 | 摘要热切换端点、chunk flush、中/长期摘要、异步提交 | 保持 |
| response_format.py | 321 | 模型输出归一化、`parse_model_decision`（D-026 唯一实现）、响应 payload 格式化 | 保持 |
| adapter_types.py | 243 | AdapterConfig / SessionState 数据类（共享类型） | 保持 |
| io_utils.py | 197 | 文件系统/输出路径、URL→path 解析、base64/data_url 图像工具 | **保持 + 去重目标**（见 §4） |
| prompt_building.py | 186 | system-prompt 与消息构造助手、context 裁剪 | 保持 |
| request_parsing.py | 169 | 入站请求解析：文本/图像 ref（含 data_url）/时间范围 | **保持 + 去重目标**（见 §4） |
| time_ranges.py | 163 | 视频段时间范围解析/归一化 | 保持 |
| prompt_constants.py | 157 | 共享 prompt/格式常量（D-027 收敛点） | 保持 |
| adapter_core.py | 144 | StreamingInferAdapter 协调器/薄门面 | 保持 |
| **live_adapter.py** | **73** | **门面模块**（ADR-0007 拆分成果）：re-export 全部历史 import 面 | ✅ 已拆好 |
| config.py | 51 | 配置加载与 env 解析 | 保持 |

**模块依赖**（单向无环，DAG）：`adapter_core → infer_loop/memory_io/session/...`；`infer_loop → prompt_assembly/prompt_building/request_parsing/response_format/time_ranges/config`；`live_adapter → 全部 re-export`。

### 1.2 services/webui/src/joy_interaction_webui/（WebUI 网关，22 文件 / 14331 行）

| 文件 | 行数 | 职责 | 拆解建议 |
|---|---|---|---|
| **jarvis_mode.py** | **2307** | BT-7274 Jarvis 状态机：KWS 链/ASR 对话/TTS 打断/LLM 流式/退出词/影子挂载/委托/日志 | **巨石 → 可拆**（见 §2.1） |
| **server.py** | **2288** | WebUI 网关：WS 主循环、会话管理、服务配置探测/持久化、ASR bridge、TTS 合成端点、路由装配 | **巨石（额外发现）→ 可拆**（见 §2.5） |
| **live_mode.py** | **1479** | Live 免唤醒状态机：VAD 门控/帧缓冲/proactive/对话提交/决策消费 | **巨石 → 可拆**（见 §2.2） |
| background_model.py | 1348 | 后台代理委派（长任务/高风险 VLM 问题）、帧缓冲、任务生命周期 | 保持（体量大但职责内聚，拆解优先级低） |
| turn_controller.py | 1045 | 统一 Turn Controller 纯逻辑（无 I/O） | ✅ 已拆好（纯逻辑可测） |
| vlm_service.py | 774 | VLM 推理服务封装（analyze_image/取消/后台交接） | 保持 |
| asr.py | 739 | 浏览器麦克风 ASR websocket 桥 | 保持 |
| jarvis_session.py | 558 | JarvisSession/LiveSession 会话管理器（桥接状态机到 server） | 保持 |
| video_processor.py | 446 | 视频处理（帧元数据/时间戳） | 保持 |
| turn_controller_shadow.py | 409 | Turn Controller 影子模式（Phase A 观察） | ✅ 已拆好 |
| tts.py | 397 | TTS 桥：流式 VLM 文本 → 浏览器可播 PCM | 保持 |
| turn_streaming.py | 337 | 共享 P0-A 流式 turn 消费器（webinfer NDJSON → 句子 TTS） | ✅ 已拆好 |
| rtsp_track.py | 294 | RTSP WebRTC 轨道 | 保持 |
| jarvis_routes.py | 278 | Jarvis HTTP 路由 | 保持 |
| addressee_detector.py | 262 | 呼称检测 Phase 1（CAM++ 声学预筛） | 保持 |
| local_file_server.py | 260 | 后台任务产物本地文件服务 | 保持 |
| live_routes.py | 259 | Live HTTP 路由 | 保持 |
| smart_turn_adapter.py | 224 | Smart Turn v3.2 语义端点检测适配 | 保持 |
| audio_processor.py | 219 | WebRTC 音频轨道（麦克风） | 保持 |
| turn_controller_delegate.py | 202 | Turn Controller 委托适配（Phase B 主动仲裁） | ✅ 已拆好 |
| vad_bypass.py | 179 | Jarvis VAD 旁路层（sherpa-onnx Silero VAD） | 保持 |
| __init__.py | 27 | 包初始化 | 保持 |

### 1.3 services/asr/ 及其它 service 目录

| 目录 | 文件 | 行数 | 职责 | 拆解建议 |
|---|---|---|---|---|
| asr/ | asr_adapter.py | ~300（13.2KB） | ASR 适配器 | 保持 |
| asr/jarvis/ | kws.py | 204 | Jarvis KWS（唤醒词） | 保持 |
| asr/jarvis/ | asr.py | 118 | Jarvis ASR（本地 paraformer promotion） | 保持 |
| tts/ | tts_adapter.py | 619 | TTS 适配器（句子级合成） | 保持 |
| tts/ | http_synthesizer.py | 193 | HTTP 合成器 | 保持 |
| memory-store/src/ | 13 文件 | app.py 502 / sqlite_backend 526 / embedder 292 等 | 记忆存储服务（backend 分离良好） | ✅ 已拆好 |
| voice-clone/ | 9 文件 | cloud_clone 657 / main 650 / models 140 | 声音克隆 API | ✅ 已拆好 |
| background-agent/ | codex_api/main.py | 424 | Codex 后台代理 | 保持 |
| background-agent/ | hermes_api/main.py | 535 | Hermes 后台代理 | 保持 |
| common/ | event_json.py / log_with_timestamp.py | 176 / 62 | 共享日志/事件工具 | ✅ 已拆好 |
| kws-training/ | icefall_src 等 | zipformer 2465 / scaling 1913 | KWS 训练（第三方 icefall 源码） | 保持（第三方依赖，不动） |

### 1.4 前端 static/（10 文件 / 12867 行）

| 文件 | 行数/大小 | 职责 | 拆解建议 |
|---|---|---|---|
| **index.html** | **6996 行 / 335870 字符** | 单页应用骨架 + 3 个内联 script 块（主逻辑 6161 行） | **巨石 → 可拆**（见 §2.4） |
| **styles.css** | **4005 行 / 117535 字符** | 全部样式（D-034 CSS 外置成果） | 保持（体量大但已是独立文件） |
| joy_ws.js | 414 | WebSocket/API 设置会话簇（从 index.html Block 4 抽出） | ✅ 已拆好（D-033） |
| wiki_frontend.js | 271 | Local Wiki 前端 F1-F4（window.JoyWiki） | ✅ 已拆好 |
| i18n_device_label.js | 236 | 运行时本地化（window.JoyI18n） | ✅ 已拆好 |
| screen_capture.js | 222 | 屏幕捕获 1fps JPEG → WS frame 管线 | ✅ 已拆好 |
| sanitize_static_html.js | 203 | 静态 HTML 消毒器（window.JoySanitize） | ✅ 已拆好 |
| render_markdown.js | 156 | Markdown/静态文本渲染（window.JoyRender） | ✅ 已拆好 |
| capture_webcam.js | 141 | 摄像头捕获 WebRTC | ✅ 已拆好 |
| capture_rtsp.js | 131 | RTSP 捕获 WebRTC | ✅ 已拆好 |
| config_services.js | 92 | 服务配置/API 表单簇（window.JoyConfig，Block 3） | ✅ 已拆好 |

**index.html 内联 script 块明细**（grep 实证）：
- **script#1（:95-114，约 20 行）**：Lucide 图标初始化 + i18n 应用（DOMContentLoaded）
- **script#2（:806-6966，约 6161 行，208 个命名函数）**：主应用逻辑，见 §2.4 分区
- **script#3（:6970-6996，约 22 行）**：sidebar 开关 IIFE

外部依赖 JS：lucide / katex / marked / dompurify（4 个 CDN）+ 9 个本地独立 JS。

---

## 2. 巨石文件解耦计划

### 2.1 jarvis_mode.py（2307 行）——最复杂，最后动

**内部职责分区清单**（grep 行号实证）：

| 区段 | 行号 | 职责 | 建议归属模块 |
|---|---|---|---|
| 工具函数 | 95-136 | `_is_garbage_text` / `_load_default_llm_system_prompt` | 抽到 `jarvis_text_utils.py` 或保留 |
| JarvisConfig | 137-425 | 配置 dataclass + from_env + 校验 | **`jarvis_config.py`**（与 jarvis_session 的 config 引用解耦） |
| 状态定义 | 450-483 | JarvisState enum + AsrPartial | `jarvis_state.py`（纯类型，零依赖） |
| 状态机骨架 | 484-671 | init、KWS/ASR/影子/委托/诊断初始化 | 保留核心类 |
| 引擎初始化 | 673-744 | prewarm_engines | 保留 |
| feed/run | 745-840 | feed_audio / run 主循环 | 保留 |
| **KWS 链** | 841-1001 | `_handle_kws` + 诊断 + PCM 捕获 + 写捕获 | **`jarvis_kws.py`**（含 908-1001 诊断/捕获 90 行） |
| KWS 补充 | 1002-1276 | fresh window / direct wake / shadow ASR / local promotion / confirm | 随 KWS 模块 |
| **ASR 对话** | 1277-1516 | `_handle_dialog`(legacy/delegated) + `_handle_dialog_commit` | **`jarvis_dialog.py`** |
| 状态迁移/音频 | 1517-1660 | `_transition_to` / `_reset_to_kws` / event wav 播放 | 保留核心 + `jarvis_sfx.py` |
| **TTS 打断** | 1661-1749 | TTS 状态、stop/pause | **`tts_turn_common.py`（共享，见 §4）** |
| **LLM 流式** | 1750-1969 | `_send_to_llm` 非流式/流式 | **`jarvis_llm.py`** |
| 句子 TTS | 1970-2087 | `_spawn_sentence_tts` / `_synthesize_tts_sentence` / `_fetch_tts_pcm` / `_wrap_pcm16_wav` | **`tts_turn_common.py`（与 live_mode 重复！）** |
| turn 收尾 | 2088-2214 | `_finish_llm_turn` / delegate TTS 通知 | 保留 |
| 流式/清理 | 2215-2277 | `_stream_tts` / `_cleanup` | 保留 |

**拆分方案**：
```
jarvis_mode.py (骨架 2307→~700)
├── jarvis_state.py        （enum/dataclass，零依赖，~40 行）
├── jarvis_config.py       （JarvisConfig + env，~290 行）
├── jarvis_kws.py          （KWS 链 + 诊断 + shadow ASR + promotion，~440 行）
├── jarvis_dialog.py       （dialog 三态处理 + commit，~240 行）
├── jarvis_llm.py          （LLM 非流式/流式发送，~220 行）
└── tts_turn_common.py     （与 live_mode 共享的 TTS 链路，~150 行）
```

**依赖关系**：核心类保留在 jarvis_mode.py（或更名 `jarvis_machine.py`），依赖上述子模块；子模块间无环（state←config←kws←dialog←llm←tts）。

**风险标注**：
- 🔒 D-032（webui 单页）/D-033（前端模块化）只约束前端，jarvis 后端无直接 ADR 守护，但 KWS 链有 16 个测试文件覆盖（test_jarvis_*、test_kws_*、test_hybrid_*、test_wake_drain 等）
- 🔒 委托/影子（turn_controller_delegate/shadow）是 Phase A/B 渐进特性，jarvis_mode.py:573-622 有 env 门控 `JARVIS_TURN_SHADOW_ENABLED`/`JARVIS_TURN_DELEGATE_ENABLED`，拆解不能改变默认 OFF 行为
- 约法三章：所有被拆函数必须保持「必 log、不静默」语义
- **优先级：最后**（5）——状态机内 KWS→ASR→TTS→LLM 交错耦合最深，且测试最多，需在共享 TTS 层先落地后再动

### 2.2 live_mode.py（1479 行）

**内部职责分区清单**：

| 区段 | 行号 | 职责 | 建议归属模块 |
|---|---|---|---|
| 配置/工具 | 90-94 | `_env_flag` | `live_config.py` |
| LiveStateMachine 骨架 | 95-285 | init（VAD/ASR/addressee/turn_controller 装配） | 保留核心 |
| 状态访问器 | 286-349 | browser state / turn_state / addressee 状态 | 保留 |
| **VAD/ASR 初始化** | 350-422 | init_asr / prewarm / set_proactive | 保留核心 |
| **帧缓冲** | 423-456 | `handle_frame` / `recent_frames` / `_frames_payload` | **`live_frames.py`**（含 wire format 转换，与 webinfer frames 契约对接） |
| **注册流程** | 457-578 | enroll start/feed/finish/cancel + asr reset | **`live_enroll.py`** |
| **VAD 门控** | 579-849 | `feed_audio` 主入口 + vad_speech/asr_chunk/addressee gate/partial | 保留核心（VAD 门控是状态机心脏，动线最小化） |
| **对话提交** | 850-913 | drain/cooldown/commit/realign | 保留核心 |
| **LLM 发送** | 914-1041 | `_send_to_llm` 非流式/流式 | **`live_llm.py`** |
| **proactive** | 1042-1173 | `_proactive_loop` / `_send_proactive_prompt` / `_call_proactive_vlm` | **`live_proactive.py`** |
| turn 收尾 | 1174-1290 | `_finish_llm_turn` / `_wait_tts_turn_done` | 保留核心 |
| 句子 TTS | 1291-1406 | `_spawn_sentence_tts` / `_synthesize_tts_sentence` / `_fetch_tts_pcm` / `_wrap_pcm16_wav` | **`tts_turn_common.py`（与 jarvis_mode 重复！）** |
| 打断/停止 | 1407-1467 | `_handle_barge_in` / `stop` | 保留核心 |

**拆分方案**：
```
live_mode.py (骨架 1479→~700)
├── live_config.py      （env 配置，~10 行）
├── live_frames.py      （帧缓冲 + wire format，~35 行）
├── live_enroll.py      （注册流程，~100 行）
├── live_llm.py         （LLM 发送，~130 行）
├── live_proactive.py   （proactive 循环，~130 行）
└── tts_turn_common.py  （与 jarvis 共享 TTS，~150 行）
```

**依赖关系**：核心类保留，依赖子模块；`live_proactive → live_frames + live_llm`；`tts_turn_common` 独立被两模式共用。

**风险标注**：
- C.B live 视觉（doc/specs/draft-live-visual-cb.md）层 1-3 刚落地（git log: a536ef3/d9736ee/80caf37/08ab0c4，604 测试 QA 通过），proactive 默认关（`LIVE_PROACTIVE_ENABLED`）——拆解**不得**改变默认关与帧不进历史语义
- 9 个测试文件覆盖（test_live_mode、test_live_proactive、test_live_visual、test_qa_live_*、test_webui_bargein_qa 等）
- **优先级：3**——先抽 `tts_turn_common`（与 jarvis 同步去重）再做 frames/proactive 抽离

### 2.3 infer_loop.py（1500 行）——收益/风险比最高，先动

**内部职责分区清单**（grep 行号实证）：

| 区段 | 行号 | 职责 | 建议归属模块 |
|---|---|---|---|
| **frames 解析** | 82-143 | `_parse_live_frames`（base64/data URL 校验归一化） | **`frame_parsing.py`**（与 request_parsing/io_utils 去重） |
| 模式归一化 | 144-174 | `_normalize_interaction_mode` | `frame_parsing.py` 或 request_parsing |
| 决策 marker/stream 提取 | 175-207 | `_find_first_decision_marker` / `_extract_stream_delta` / `_extract_stream_usage` | **`stream_protocol.py`** |
| **流式协议组装** | 208-305 | `build_stream_frames`（NDJSON 四态帧组装） | **`stream_protocol.py`** |
| emit_event 兜底 | 306-341 | event json 可导入性 + 兜底 | 保留或抽 `event_emit.py` |
| Mixin 骨架 | 342-349 | `InferLoopMixin` + `_resolve_backend` | 保留 |
| text/chat 端点 | 350-516 | `handle_text_chat` | 保留（端点薄层） |
| **text payload 处理** | 517-628 | `_handle_text_payload`（含 :562-576 重复消息组装） | 保留 + 去重 |
| **流式端点** | 629-896 | `_handle_text_chat_streaming` + `_stream_text_payload_frames`（含 :757-769 重复消息组装） | 保留 + 去重 |
| chat/completions 端点 | 897-937 | `handle_chat_completions` | 保留（D-029 守护） |
| **chat payload 五步编排** | 938-970 | `_handle_chat_payload` 编排 | **`chat_payload.py`**（已有清晰的 5 个 `_chat_payload_*` 子步，抽出即收益） |
| chat payload 子步 | 971-1370 | resolve_frames / advance_chunk / append_turn / build_and_infer / finalize / forced_silence | **`chat_payload.py`** |
| 纯文本转发 | 1371-1398 | `_forward_text_only` | 随 chat_payload |
| **帧引用解析** | 1399-1441 | `_time_range_for_frame` / `_resolve_frame_ref` / `_save_base64_frame` / `_validate_local_image_path` | **`frame_parsing.py`**（与 :82-143 同域） |
| 查询状态 | 1442-1472 | `_update_query_state` | 随 chat_payload |
| 主模型调用 | 1473-1500 | `_call_main_model` | 保留 |

**拆分方案**：
```
infer_loop.py (Mixin 骨架 1500→~900)
├── frame_parsing.py    （frames 解析 + 帧引用 + data_url 归一化，~120 行）
├── stream_protocol.py  （NDJSON 四态流协议 + build_stream_frames，~130 行）
├── chat_payload.py     （chat payload 五步编排，~430 行）
└── （去重）prompt_assembly.py 增加 compose_live_visual_messages helper（见 §4）
```

**依赖关系**：`infer_loop` 继续作为 Mixin 宿主 import 三个新模块；`chat_payload → frame_parsing + stream_protocol + prompt_assembly`。

**风险标注**：
- 🔒 **D-029 视频端点决策回归测试**：chat/completions 视频 QA 路径被 `test_decision_token_isolation.py` 等守护（doc/specs/draft-live-visual-cb.md:23 明确「触碰 D-029 守护的视频 QA 路径回归风险大」）。**chat/completions 路径保持不动**，只做机械搬移，不改任何解析/组装逻辑
- 🔒 D-026 `parse_model_decision` 唯一实现（response_format.py:50）——infer_loop 只能 import，不许复制
- 🔒 D-028 state.lock 并发守卫——所有抽出的函数保持原锁语义
- 10 个测试文件覆盖（test_text_chat_*、test_live_visual、test_decision_*、test_adapter_core_split 等）
- **优先级：1**——`frame_parsing` 与 `stream_protocol` 是纯函数（无 I/O、无状态），机械搬移 + 测试即绿，收益（-270 行 + 去重 2 处）风险最小

### 2.4 index.html（6996 行 / 335870 字符）

**内部职责分区清单**（script#2 内 208 个命名函数，按功能聚类）：

| 聚类 | 行号（script#2 内偏移） | 代表函数 | 建议归属 JS 文件 |
|---|---|---|---|
| ASR 常量/元素引用 | 1-115 | ASR_* 常量、DOM 元素 | `app_state.js` 或并入主入口 |
| **llm_reply 处理 + ASR draft** | 118-280 | `installLlmReplyHandler` / `appendJarvisToResult` / `renderAsrDraft` | **`llm_reply_ui.js`** |
| 摄像头/屏幕捕获 handler | 327-660 | `enumerateCameras` / `switchCameraByFacing` | **`capture_ui.js`**（复用已拆 capture_webcam） |
| 服务配置表单 | 639-768 | （注释：body 已抽到 config_services.js） | ✅ 已抽 |
| **决策 token 剥离/渲染** | 769-1110 | `getVlmDisplayText` / `stripInlineStructuredJsonFromText` / `renderTextIntoElement` | **`vlm_render.js`** |
| 背景富内容解析 | 1047-1570 | `parseBackgroundChart` / `extractBackgroundHtml` / `scoreHtmlCandidate` | **`background_rich.js`** |
| **TTS 播放链路** | 1590-1911 | `stopTtsPlayback` / `getTtsWebSocket` / `pcm16ToAudioBuffer` / `queueTtsPcmChunk` / `speakVlmText` | **`tts_player.js`** |
| **VLM 历史渲染** | 1912-2674 | `createJarvisDialogNode` / `renderVlmHistory` / `appendOrUpdateBackgroundEntry` | **`vlm_history.js`** |
| 背景摘要/跳转 | 2527-2674 | `updateBackgroundSummaryEntry` / `jumpToBackgroundResult` | 随 vlm_history |
| 结果更新/延迟 | 2675-2814 | `updateResultText` / `logLatencyBreakdown` | **`result_panel.js`** |
| 主题/布局/全屏 | 2815-3318 | `applyTheme` / `applyOverlayPosition` / `toggleFullscreen` | **`layout_theme.js`** |
| 服务检测/模型 | 3319-3478 | `detectServices` / `fetchModels` | 随 config_services |
| 提示词面板 | 3447-3498 | `applyPromptSettings` / `resizePromptInput` | **`prompt_panel.js`** |
| 直播 UI | 3566-3993 | `setLiveModeActive` / `startLiveMode` / `stopLiveMode` / `setLiveProactiveUiState` | **`live_ui.js`** |
| BT 监听 | 3994-4145 | `startBtListening` / `stopBtListening` | **`bt_listen_ui.js`** |
| 语音输入 | 4146-4830 | `startSpeech` / `stopSpeech` / `prepareSpeechMic` / `connectAsrWebSocket` | **`speech_input.js`** |
| 模式选择/发送 | 4830-5001 | `selectBtListenMode` / `selectLiveMode` / `captureBtFrameB64` / `sendBtPrompt` | 随对应 UI |
| **LLM reply 播放队列** | 5002-5413 | `enqueueLlmReplySentence` / `processLlmReplyQueue` | **`llm_reply_audio.js`**（P0-A 队列） |
| **WS 连接/消息分发** | 5480-5741 | `connectWebSocket` / `dispatchServerMessage` | **`ws_dispatcher.js`** |
| 会话重置 | 5742-5835 | `resetSession` | 随 ws_dispatcher |
| 服务状态轮询 | 5836-6141 | `pollServiceStatus` / `pollJarvisStatus` / `pollLiveStatus` / `pollExtendedStatus` | **`status_poll.js`** |
| WrappedWS/llm_reply 安装 | 6142-6161 | `WrappedWS` IIFE | 随 ws_dispatcher |

**拆分方案**：按 D-033 既有模式（`window.JoyXxx` 命名空间 + `<script src>`），把 script#2 拆成 8-10 个独立 JS：
```
js/llm_reply_ui.js / vlm_render.js / background_rich.js / tts_player.js /
vlm_history.js / speech_input.js / live_ui.js / llm_reply_audio.js /
ws_dispatcher.js / status_poll.js
```
（每个 300-800 行，依赖关系通过 window 命名空间与调用顺序 `defer`/文档尾部加载保持）

**风险标注**：
- 🔒 D-032（webui 单页）/D-033（前端模块化，5 个 window.JoyXxx）/D-034（Vitest + CSS 外置 + a11y）——已有 6 个 js 测试 + 20 个 static 契约测试（test_webui_static_contract 等）守护，说明**搬移路线已被验证**
- 已有先例：joy_ws.js/config_services.js 等 9 个文件就是从 index.html 抽出的（文件头注释实证「Extracted from index.html so the monolith shrinks」）
- 浏览器全局作用域共享：拆分时需保持函数在全局命名空间可见（不能改 export/import 机制，除非引入构建步骤——**建议维持无构建**，D-032 单页原生 HTML/JS）
- **优先级：2**——纯搬移零逻辑改动，复用已验证的 D-033 模式；唯一注意是 208 个函数间的隐式全局依赖，需按聚类一次性搬完整块

### 2.5 server.py（2288 行）——额外发现，需纳入专项

**内部职责分区清单**：

| 区段 | 行号 | 职责 | 建议归属模块 |
|---|---|---|---|
| WS/会话 helper | 67-306 | `_is_heartbeat_path` / `_spawn_bg` / `send_to_session` / `notify_session_*` | **`ws_notify.py`**（被 jarvis_session 反向 import，需保持契约） |
| **WS 主循环** | 307-525 | `websocket_handler`（协议分发） | **`ws_handler.py`** |
| 服务探测 | 526-650 | `_probe_llm` / `_probe_tts` / `_probe_kws` / `_resolve_service_targets` | **`service_probe.py`** |
| **TTS 合成端点** | 651-806 | `build_tts_synthesize_payload` / `_tts_synthesize_handler` | **`tts_endpoint.py`** |
| 会话管理 | 806-887 | `get_or_create_session` / `session_cleanup` | 保留核心 |
| WebRTC/offer | 888-975 | mic 音频 / `offer` | **`webrtc_offer.py`** |
| 生命周期 | 976-1052 | startup/shutdown | 保留核心 |
| **服务配置管理** | 1053-1363 | `_merge_services_config_file` / `_validate_and_apply_slot` / `_probe_slot` | **`services_config.py`**（最大块 ~310 行） |
| **ASR bridge** | 1364-1460 | `_asr_bridge_ensure` / `_asr_bridge_stop` / `_asr_bridge_sync` | **`asr_bridge.py`** |
| 配置变更事件 | 1461-1571 | `_log_config_change` / probe summary/asr | 随 services_config |
| 状态/代理端点 | 1572-1810 | `_services_config_handler` / `_screen_latency_handler` / `_proxy_to_memory_store` | **`admin_endpoints.py`** |
| 扩展状态/ingest | 1810-1993 | `extended_status` / `_ingest_text_handler` / `_propagate_services_to_runtime` | **`admin_endpoints.py`** |
| webinfer 摘要代理 | 1994-2073 | `_webinfer_proxy_summarizer_routing` / `_webinfer_summarizer_route_handler` | **`webinfer_proxy.py`** |
| main/中间件/路由 | 2074-2288 | `main` / security/access_log 中间件 / `_health_handler` / `_index_handler` / rtsp stubs | 保留核心 |

**拆分方案**：
```
server.py (核心 2288→~500)
├── ws_notify.py        （notify 契约层，jarvis_session 依赖，~240 行）
├── ws_handler.py       （WS 主循环，~220 行）
├── service_probe.py    （探测，~125 行）
├── tts_endpoint.py     （TTS 合成，~155 行）
├── services_config.py  （配置管理，~310 行）
├── asr_bridge.py       （ASR bridge，~100 行）
├── admin_endpoints.py  （状态/代理端点，~280 行）
└── webinfer_proxy.py   （webinfer 代理，~80 行）
```

**风险标注**：
- ⚠️ `ws_notify` 被 jarvis_session.py 以 `from .server import notify_session_*` 反向 import（server.py:98-102 有双模块加载 bug 的专门注释）——**拆解必须先解决这个循环/别名问题**，否则搬移后 import 面断裂
- 多个测试直接 import server（test_send_to_session_actually_awaits、test_llm_reply_broadcast、test_services_*、test_webui_summarizer_proxy 等）——保持 `server.py` 作为 re-export 门面可平滑过渡
- **优先级：4**——依赖 `ws_notify` 循环问题需先设计（或沿用 ADR-0007 live_adapter 门面模式）

---

## 3. 共享实现去重清单

| # | 重复点 | 位置（实证） | 建议 |
|---|---|---|---|
| 1 | **Live visual 消息组装块**（完全相同 ~20 行） | infer_loop.py:562-576 与 :757-769（`_handle_text_payload` 与 `_stream_text_payload_frames` 内） | 抽 `prompt_assembly.compose_live_visual_messages(composed_system, last_user_text, frames, history_messages)` helper——prompt_assembly 已是 `_build_live_visual_messages` 归属地 |
| 2 | **base64/data_url 归一化**（4 处语义重叠） | ① infer_loop._parse_live_frames:100-113（data URL 前缀剥离）；② request_parsing._extract_first_image_ref/_extract_all_image_refs:76-80（data_url 识别）；③ infer_loop._save_base64_frame:1414-1419（re.match data URL）；④ io_utils._file_to_data_url:77（生成 data URL） | 抽 `io_utils.normalize_image_b64()`（输入 data URL 或 raw base64 → raw base64）与 `io_utils.is_data_url()`；frames 解析与 request_parsing 共用 |
| 3 | **TTS 句子链路**（5 个同名方法完全重复） | jarvis_mode.py:1970-2087 与 live_mode.py:1291-1406：`_spawn_sentence_tts` / `_synthesize_tts_sentence` / `_fetch_tts_pcm` / `_wrap_pcm16_wav` / 相关 state | 抽 `webui/tts_turn_common.py`（复用 tts.py 的合成客户端），两模式改为继承/组合 |
| 4 | **frames wire format 组装** | live_mode._frames_payload:447-456 生成 `{image_b64, ts_ms}`；infer_loop._parse_live_frames 消费同结构 | 以 `frame_parsing.py` 为契约层，两端共用类型/校验 |
| 5 | **notify_session_* 契约**（双模块加载别名） | server.py:98-102 注释 + notify 函数族；jarvis_session.py 反向 import | 拆到 `ws_notify.py` 单一定义，server re-export（门面模式，仿 live_adapter） |
| 6 | **决策 token 剥离** | 前端 index.html `getVlmDisplayText`/`stripInlineStructuredJsonFromText`（:769-1025） vs 后端 response_format.strip_decision_tokens | 两端协议需保持一致；前端抽 `vlm_render.js` 时锁一个 token 正则常量（跨端不能直接共用 JS/Python，但需在 spec 固化） |

---

## 4. 总结

### 解耦总优先级

| 序 | 巨石 | 行数 | 拆后骨架 | 预计工作量 | 收益 | 风险 |
|---|---|---|---|---|---|---|
| 1 | **infer_loop.py** | 1500 | ~900 | 1.5-2 天 | -600 行 + 去重 2 处（纯函数） | 低（D-029 路径不动） |
| 2 | **index.html** | 6996 | ~800 | 2-3 天 | -6000 行（纯搬移，模式已验证） | 低（D-033 先例） |
| 3 | **live_mode.py** | 1479 | ~700 | 2 天 | -780 行 + TTS 去重 | 中（C.B 新逻辑，测试充分） |
| 4 | **server.py**（额外发现） | 2288 | ~500 | 2-3 天 | -1800 行 | 中高（ws_notify 循环依赖） |
| 5 | **jarvis_mode.py** | 2307 | ~700 | 3-4 天 | -1600 行 + TTS 去重 | 高（状态机耦合最深，最后动） |

**合计预计工作量：约 10-14 人日**（含每步回归测试）。建议分 5 个独立 PR，每 PR 遵循约法三章 + ADR-0007 门面模式 + 全量回归。

### 关键原则（专项执行时）
1. **机械搬移优先，逻辑零改动**：所有拆解先纯搬移 + 测试绿，再谈重构
2. **门面 re-export 平滑过渡**：仿 live_adapter.py（73 行门面）——原文件保留 re-export，避免 import 面断裂
3. **D-029 / D-026 / D-028 / D-032~034 守护路径**：chat/completions 视频 QA、parse_model_decision 唯一实现、state.lock 并发、前端命名空间契约——拆解时逐项回归
4. **共享实现先去重后拆分**：`tts_turn_common` / `frame_parsing` / `compose_live_visual_messages` 是三个去重点，抽 helper 的 PR 应排在对应巨石拆分之前
5. **约法三章**：被拆函数保持「必 log、不静默、增新删旧」

### 只读确认
- ✅ 未创建/修改任何代码文件（唯一写入：本报告 + doc/architecture/ 目录）
- ✅ `git status --short` 为空（0 变更）
- ✅ 全部数据 `wc -l` / `grep` / `sed` 实证
