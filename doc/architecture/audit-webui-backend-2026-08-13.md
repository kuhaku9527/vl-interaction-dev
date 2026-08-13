# WebUI 后端只读代码审查报告（2026-08-13）

- 审查范围：`services/webui/src/joy_interaction_webui/`
- 审查方式：只读，未修改任何代码、未提交
- 审查重点：巨石解耦后（server 拆 8 模块 / jarvis_mode 拆 6 模块 / live_mode 拆 4 模块 / tts_turn_common 首批共享）的行为漂移、循环 import 残留、过度拆分、死代码、前项目组遗留隐患
- 结论速览：**P0 = 0，P1 = 3，P2 = 12，P3/信息 = 8**

---

## 一、P0（无）

未发现默认路径上必然导致崩溃 / 数据损坏 / 会话错乱的真 P0。

---

## 二、P1（高优先级，建议尽快修复）

### P1-1 任意文件写入（路径穿越）：`jarvis_routes.py:256-278 diagnostic_save_wav`

- 位置：`jarvis_routes.py:269-271`
  ```python
  filename = part.filename or "mic.wav"
  out_path = out_dir / filename
  ```
- 问题：`part.filename` 完全由客户端控制，未做任何 `..` / 绝对路径 / 分隔符清洗，直接与 `out_dir` 拼接。`Path("D:/AI/data/kws/mic_captures") / "../../foo"` 会被 pathlib 归一化为 `D:/AI/data/foo`，实现目录穿越写文件。该接口**无鉴权、无大小限制**，且注册为普通 HTTP 路由（`setup_jarvis_routes` 中 `add_post("/api/diagnostic/save_wav", ...)`）。
- 影响：恶意网页（CSRF，浏览器可向 localhost 发 multipart POST）或同一局域网内的攻击者可在主机任意可写路径落盘任意字节；默认 `--host 127.0.0.1` 时主要是本地/CSRF 风险，一旦以 `--host 0.0.0.0` 暴露即为远程任意文件写入。
- 建议：`filename` 仅取 `Path(filename).name`（basename），拒绝含分隔符/`..`/绝对路径的输入；加大小上限（如 20MB）；如需保持诊断用途可加 `Content-Length`/Origin 校验。
- 标注：**真 bug（安全）**。相对面：读取侧 `_is_allowed_diagnostic_wav`（`jarvis_routes.py:106-112`）有白名单校验，写入侧却完全裸奔——明显的前项目组遗留隐患。

### P1-2 ASR bridge 未在服务启动时与持久化配置对齐（重启后云端 ASR 失效）

- 位置：`asr_bridge.py:115-124 _asr_bridge_sync` 唯一调用点 `admin_endpoints.py:443`（PUT 传播路径）；`server.py:463-493 on_startup` 未调用。
- 问题：`connect_asr`（`asr.py:316-317`）对 `http(s)://` 配置一律改写为固定内部桥 `INTERNAL_ASR_BRIDGE_WS`。但桥进程只在 `PUT /api/services/config` 触发 `_propagate_services_to_runtime → _asr_bridge_sync` 时拉起。若 `config/services.json` 持久化了云端 ASR `api_base` 而 webui 重启，桥未启动 → 浏览器 ASR 连接 8994 失败 → `_resolve_asr_failure_mode`（`asr.py:239-258`）判定 `ERROR_NO_FALLBACK`（除非 `ASR_ALLOW_LOCAL_FAILOVER=1`）→ 浏览器拿到错误，本地兜底也不启用，**直到操作员再手动 PUT 一次配置才恢复**。
- 影响：持久化云端 ASR 的场景，每次重启 webui 后浏览器 ASR 不可用（静默功能性回归；旧代码直连 upstream 反而可用）。
- 建议：在 `on_startup` 中（executor 内）调用一次 `_asr_bridge_sync()`（`server.py` 已 re-export `_asr_bridge_sync`，代价是启动阻塞 ≤15s，可接受）。
- 标注：**真 bug（解耦回归）**。

### P1-3 `session_cleanup` 不销毁 jarvis/live 会话（状态机 + 引擎泄漏）

- 位置：`server.py:311-372 session_cleanup`（`/api/session/cleanup`）。
- 问题：该端点只清理 `sessions`（VLM 会话 dict）、WS 注册表、RTSP、PeerConnection，**从不调用 `manager.remove_session/remove_live_session`**（`jarvis_session.py:531-543`）。前端断连/页面卸载后，`JarvisStateMachine`（含 KWS + ASR ~200MB 模型、KWS 诊断线程、可能的 TTS/LLM 任务）和 `LiveStateMachine`（ASR 引擎、可能存在的 proactive/cooldown 任务）继续驻留 `JarvisSessionManager._sessions/_live_sessions`，直到进程退出。
- 影响：多次进出会话 → 引擎/任务累积；KWS 持续监听死会话；`session_websockets` 为空后 `notify_*` 全部打 INFO drop 日志，掩盖真实流量。
- 建议：`session_cleanup` 中按 session_id 同时调用 `manager.remove_session()` 与 `manager.remove_live_session()`（注意双开场景两者都要删）；或在前端 WS 断开时注册清理回调。
- 标注：**真 bug / 生命周期隐患**。

---

## 三、P2（中优先级）

### P2-1 `_validate_api_base` 与文档/`_probe_asr` 行为矛盾（ws:// 配置被 400 拒绝）
- 位置：`services_config.py:144-169`。
- 问题：docstring 明说“ws(s):// is allowed because the external ASR may be a websocket bridge”，但 `parsed.scheme not in ("http","https")` 直接拒绝；而 `service_probe.py:202-203` 明确支持 `ws://` 覆盖（“not probed, treated ok”）。两处对同一字段的契约不一致 → 通过 PUT UI 保存 ws 覆盖必得 400，只能靠环境变量 `ASR_URL` 才能用 ws。
- 建议：统一三处语义（docstring/校验/探针），校验放行 `ws/wss`。
- 标注：**真 bug（契约不一致，解耦前遗留）**。

### P2-2 `ws_notify.handle_background_handoff_for_interaction` 空函数体（死代码）
- 位置：`ws_notify.py:166-173`。
- 问题：`notify_session_json`（`:94`）每次调用都会先走它；函数在 guard 之后查到 `session["vlm_service"]` 便结束，**没有任何行为**。疑似拆分 server.py 时的残留（原来可能负责 `background_result_ready` 的转发/重定向）。
- 影响：无功能影响，但每次 `notify_session_json` 都多一次无意义的 server 惰性 import + dict 查询；且误导后来者以为有 handoff 逻辑。
- 建议：删除函数与调用，或在 `background_result_ready` 真正需要转发到后台时补全实现。
- 标注：**死代码**。

### P2-3 `jarvis_routes.py:252-253` 重复 return（死代码）
- 位置：`jarvis_feed_wav` 末尾两行完全相同的 `return web.json_response({"session_id": session_id, "fed": result})`。
- 建议：删除第二行。
- 标注：**死代码（拆分/复制粘贴残留）**。

### P2-4 `session_cleanup` 响应字段 `cancelled_background_tasks` 恒为 0
- 位置：`server.py:344, 370`。
- 问题：变量初始化后从未累加，接口对前端承诺了该统计但恒为 0。
- 建议：要么统计 `background_service` 的实际取消任务数，要么从响应中移除。
- 标注：**死代码/误导性接口**。

### P2-5 LLM 配置 PUT 不传播到 jarvis 语音路径
- 位置：`admin_endpoints.py:389-458 _propagate_services_to_runtime`。
- 问题：LLM 传播只更新 `server.sessions` 的 VLMService 与 `default_vlm_config`；TTS 传播写 `JARVIS_TTS_API_URL`、ASR 传播写 `ASR_MODEL_DIR`，唯独**不写 `JARVIS_LLM_API_URL`**（`jarvis_config.from_env:358` 只读 env）。jarvis 语音 LLM 端点（`llm_api_url`）在 PUT 后仍指向旧值/默认 8070。
- 影响：操作员在 UI 改 LLM 端点后，视频/文字路径生效、语音路径不生效，属隐性不一致。
- 建议：与 TTS 相同，把 `llm.api_base` 写入 `JARVIS_LLM_API_URL` env（对后续 `from_env()` 会话生效）。
- 标注：**真 bug（传播不完整）**。

### P2-6 turn-delegate 在 `on_speech_stopped` 抛 `TurnStateError` 时被永久禁用
- 位置：`jarvis_mode.py:838-846`（`_handle_dialog` 的 catch-all）+ `turn_controller_delegate.py:114-116`。
- 问题：`_handle_dialog_delegated` 中任何 `TurnStateError`（例如控制器处于 LISTENING/ENDED/ERROR 时 `delegate.on_speech_stopped()` 会 raise，见 `turn_controller.py:784-787`）都会触发 `self._turn_delegate = None`，**本会话剩余时间永久退回 legacy**。虽然 fail-open 设计如此，但单次状态竞态（如 forced-state 后残留 stale 文本、或 `on_dialog_enter` 对齐失败）会静默关闭 Phase B，且没有任何告警统计。
- 建议：将“控制器不在可接收状态”视为可忽略事件（在 delegate 内部 log+吞掉，或对 `TurnStateError` 计数并降级为仅本轮 legacy，而非永久置 None）。
- 标注：**真 bug（健壮性/可恢复性）**。

### P2-7 失败的 ASR PUT 仍会拉起桥进程（配置未提交、桥已启动）
- 位置：`services_config.py:285-290` + `:296-314`。
- 问题：`_asr_bridge_ensure` 在探针**之前**执行；探针失败（422）后配置未应用，但桥已带着新 upstream 运行，`_last_asr_propagated` 也未更新。另外 http(s) ASR 的“可达性探针”打的是桥自身 `/health`（`service_probe.py:193-199`），upstream 不可达时探针仍可能通过 → 不可达 URL 会被持久化。
- 建议：探针通过后再拉起桥；桥健康只代表桥自身，upstream 可达性应在桥内转发首包时判定或探针打到 upstream 路径。
- 标注：**真 bug（状态不一致）**。

### P2-8 addressee 门控路径下旧文本 endpoint 可在新 segment 缓冲期误触发
- 位置：`live_mode.py:618-674`（`feed_audio` step 1-3）。
- 问题：门控段缓冲期间（`_addressee_in_seg=True`）ASR 不被喂入，`_last_speech_time` 不更新。若上一段文本已 stale>2s 且此刻用户开始新 segment，step 3 的 endpoint 判断仍成立 → `on_speech_stopped` 提交**上一段旧文本**，而此时用户还在说话；随后新 segment 释放又会产生第二次提交。非门控路径因 ASR 持续喂入而部分缓解。
- 影响：门控开启 + 用户连续说话时可能重复/串话提交。
- 建议：在 `_addressee_in_seg` 为 True 时跳过 step 3 endpoint 判断（或把门控段起点视为 `_last_speech_time` 刷新点）。
- 标注：**真 bug（窄竞态）**。

### P2-9 `_cleanup` 未取消 `_confirm_task` / `_promo_task`
- 位置：`jarvis_mode.py:1712-1724`。
- 问题：`run()` 被取消时只清 KWS/ASR/`_tts_task`/句子任务，`_confirm_task`（WAIT_ASR_CONFIRM 超时任务）与 `_promo_task`（ASR 提升任务）可能残留；取消后 `_wait_asr_confirm_timeout` 的 `reset_to_kws` 可能在已清理的实例上继续执行。
- 建议：`_cleanup` 中一并 cancel + await。
- 标注：**真 bug（清理遗漏，生命周期）**。

### P2-10 `wait_asr_confirm_timeout` 用状态快照判断，存在取消竞态窗口
- 位置：`jarvis_kws.py:134-139`。
- 问题：`state` 是调用时快照。`_promote_from_confirm` 会取消 `_confirm_task`，但若取消恰在 `asyncio.sleep` 完成之后到达，快照仍为 `WAIT_ASR_CONFIRM` → 继续跑 fresh-window probe → 可能 `direct_wake` 把已处于 DIALOG_ACTIVE 的机器再打回 WAKE_DETECTED/重启 ASR。
- 建议：probe 前用回调/闭包读取 `self.state` 实时值（如 `state_reader: Callable[[], JarvisState]`）。
- 标注：**真 bug（窄竞态，拆分前已有）**。

### P2-11 `ws_handler.update_frames_per_batch` 是 no-op（假更新）
- 位置：`ws_handler.py:110-118`。
- 问题：消息类型叫 `update_frames_per_batch`，但 handler 只是把 `VideoProcessorTrack.frames_per_batch` 原值回显，**不修改任何东西**。前端若依赖该消息改帧批大小会静默无效。
- 建议：删除该分支，或真正实现 setter 并回写。
- 标注：**死代码/误导**。

### P2-12 jarvis/live 之间仍有可合并的重复实现
- 位置：
  - `jarvis_mode.py:1349-1435 _send_to_llm_non_streaming` vs `live_llm.py:107-164 send_to_llm_non_streaming`：历史快照 + httpx POST + streamingharness 解析 + `_finish_llm_turn`，逻辑几乎逐行一致（仅 frames 差异）。
  - `jarvis_llm.py:23-113 send_to_llm_streaming` vs `live_llm.py:23-104 send_to_llm`：同为“consumer 构造 + consume + fail-open retry + finish”，参数传递风格不同但语义重复。
- 建议：第二批共享可把“非流式单轮”抽为 `llm_turn_common.send_to_llm_single_shot(...)`，streaming 侧只保留 frames/epoch 差异。
- 标注：**重复实现（过度设计残留）**。

---

## 四、P3 / 信息（低优先级）

- P3-1 `jarvis_session.py:553-558 get_global_manager` 单例从未被调用 —— **死代码**。
- P3-2 `jarvis_session.py:97-103 check_exit_words` 从未被调用（退出词检测在状态机内部 `jarvis_dialog.exit_word_detected`）—— **死代码**。
- P3-3 `vad_bypass.py:151-170 pop_segment/current_segment` 为文档标注的“future follow-up”，生产路径未使用 —— **预留死代码**。
- P3-4 `turn_controller.py` 的 7 个事件 dataclass（`TurnEvent/SpeechStartedEvent/SpeechStoppedEvent/PartialTranscriptEvent/TurnDecisionEvent/LLMTokenEvent/TTSAudioEvent`）+ `TURN_DECISION_ACTIONS` + `on_resume_speaking/on_llm_ttft_timed_out/on_llm_total_timed_out/on_max_utterance_timed_out` 在生产路径（jarvis delegate / live）均未使用，属原型保留面 —— **死代码（原型残留）**。
- P3-5 `turn_controller_shadow.py`（17KB，环境门默认关）整个 Phase A 影子控制器仅用于对齐日志 —— **过度设计候选**（若 Phase A 已验收完毕可归档；至少可注明“仅 acceptance 用”）。
- P3-6 `live_enroll.py`（93 行 4 个纯函数）与 `live_frames.py`（77 行）、`jarvis_dialog.py`（56 行）拆分颗粒过细；三者均只被单个调用方使用，直接内联回 `live_mode.py`/`jarvis_mode.py` 也可 —— **过度拆分候选（低优先级）**。
- P3-7 `jarvis_llm.py` 是 18 个关键字参数的薄门面，抽象收益低，可并回 `jarvis_mode._send_to_llm_streaming` —— **过度设计候选**。
- P3-8 常量重复：`asr.py:145-146 ASR_BRIDGE_PORT/INTERNAL_ASR_BRIDGE_WS` 与 `asr_bridge.py:20-22 ASR_BRIDGE_PORT/WS/HTTP` 定义同一组值（同 env），建议 asr.py 从 asr_bridge 导入。
- P3-9 `turn_controller_shadow.py:66` 注释“<_SHADOW_BARGE_CONF”说 `_SHADOW_SPEECH_CONF=0.8` 应 `< barge_in_threshold 0.75`，与实值矛盾（功能无影响，LISTENING→USER_SPEAKING 不检查阈值）—— 注释陈旧。
- P3-10 `admin_endpoints.py:465-467` 在协程内 `try: asyncio.get_running_loop() except RuntimeError` 是死分支（协程内必成功）—— 死代码。
- P3-11 `server.py` on_startup 的 `browser_asr_warmup_task`（`:490`）从未在 shutdown 时取消 —— 进程退出前任务残留（低风险）。
- P3-12 `_services_status_handler`（`admin_endpoints.py:171`）在 `api_base` 为空时展示 `"/models"` 端点串 —— 展示瑕疵。

---

## 五、拆分专项核查结论（团队重点问题）

1. **门面 re-export 行为漂移**：未发现行为漂移。`server.py`/`jarvis_mode.py` 的 `x as x` re-export 都是同一对象（dict/set 共享引用），monkeypatch 契约成立。唯一漂移风险点是 P2-5（LLM 配置传播遗漏）这类“拆走但没拆干净”的传播逻辑。
2. **ws_notify 惰性 `from . import server` 循环 import**：**现状安全**——所有 `from . import server` 都在函数调用期解析，模块加载图无环（server→ws_notify→(调用期)server 不会在加载期回环）。但该模式脆弱：任何人在模块级调用 `notify_session_*`/`_spawn_bg` 都会立即死锁。建议在 `ws_notify.py` 顶部注释中固化“禁止模块级调用”约束，或把 `_server_send_to_session` 改为注入式回调（由 server 启动时 `set_send_facade()`）。
3. **services_config 合并边界**：`_merge_services_config_file` 只合并已知 slot+已知字段，边界正确；非字符串值静默忽略属可接受降级。风险集中在 P2-7（桥启动早于探针）。
4. **live_enroll 是否值得独立**：见 P3-6，属低价值拆分，不构成 bug。
5. **turn_controller 链**：FSM 转移合法、`SentenceBuffer` 边界（缩写/逗号切分/超长强刷）实现正确；风险集中在 P2-6（delegate 永久降级）与 P2-10（快照竞态）。
6. **addressee 链**：门控 fail-open 正确（无检测器/未 enroll/VAD 不可用全部透传）；风险集中在 P2-8 窄竞态。enroll 状态机（start/pcm/finish/cancel）无绕过路径：`feed_audio` 在 `_enroll_phase` 直接短路，`/api/live/enroll` 有 session 存在性检查。
7. **tts_turn_common**：jarvis/live 调用一致性良好（epoch 语义：spawn 时捕获、合成前后双检）；`seq`/`reply_session` 语义无漂移。
8. **VAD/音频/asr**：`VadBypass` fail-open 正确；`MicAudioTrack` 重采样链正确。asr.py 的 inproc 兜底与 bridge 路径见 P1-2。

---

## 六、总体评估

- 拆分的机械正确性（import 图、re-export、monkeypatch 契约）整体良好，**未发现因拆分本身导致的 P0**。
- 主要问题集中在三处：**安全（P1-1 路径穿越）、重启后配置对齐（P1-2 桥未同步）、生命周期（P1-3 会话泄漏）**——后两者是典型的“拆走逻辑但拆丢了初始化/清理挂点”的解耦回归模式。
- 死代码/过度设计总量中等偏高（P3 列 12 条），多为原型保留面（turn_controller 事件面、shadow、vad 预留钩子）与拆分颗粒过细的小模块，建议在下次清理窗口一并收敛。

## 附：文件行号索引（核对用）

| 编号 | 文件:行 |
|---|---|
| P1-1 | jarvis_routes.py:256-278（写路径 269-271） |
| P1-2 | asr_bridge.py:115-124；admin_endpoints.py:443；server.py:463-493；asr.py:316-317 |
| P1-3 | server.py:311-372；jarvis_session.py:531-543 |
| P2-1 | services_config.py:144-169；service_probe.py:202-203 |
| P2-2 | ws_notify.py:166-173, 94 |
| P2-3 | jarvis_routes.py:252-253 |
| P2-4 | server.py:344, 370 |
| P2-5 | admin_endpoints.py:389-458；jarvis_config.py:358 |
| P2-6 | jarvis_mode.py:838-846；turn_controller_delegate.py:114-116；turn_controller.py:784-787 |
| P2-7 | services_config.py:285-314；service_probe.py:193-199 |
| P2-8 | live_mode.py:618-674 |
| P2-9 | jarvis_mode.py:1712-1724 |
| P2-10 | jarvis_kws.py:134-139 |
| P2-11 | ws_handler.py:110-118 |
| P2-12 | jarvis_mode.py:1349-1435；live_llm.py:107-164；jarvis_llm.py:23-113 |
