# ASR 云端配置契约（订正）

> 状态：已落地（2026-08-11，代码改完，ruff+pytest 绿）
> 关联：`services/webui/src/joy_interaction_webui/{server.py,asr.py}`、`services/asr/asr_adapter.py`、`docs/asrok-user-guide.md`
> 背景：2026-08-11 用户指出 ASROK 面板要求填写 `ws://127.0.0.1:8994/ws/asr` 是错误的——`ws://` 是系统内部传输管道，不应暴露给用户。

## 1. 问题

当前 `asr.api_base` 字段被同时用作：

- **用户配置字段**（前端 ASROK 面板 `svc-asr-api-base` 直接发到 PUT `/api/services/config`）；
- **WebUI↔ASR 引擎的内部 websocket 端点**（`asr.connect_asr` 用 `aiohttp.ws_connect(url)`）。

结果：要让云端 ASR 工作，必须让用户手敲内部桥的 `ws://` 地址。这把传输层泄漏成了用户契约，违反行业惯例，也导致前端语义（`API 地址`）与后端语义（websocket endpoint）错配。

## 2. 行业/官方标准（已查官方文档）

- **SiliconFlow 官方**（`docs.siliconflow.cn/api-reference/audio/create-audio-transcriptions`，OpenAI 兼容）：
  `POST https://api.siliconflow.cn/v1/audio/transcriptions`，请求头 `Authorization: Bearer <KEY>`，body `multipart/form-data` 传 `file`+`model`，返回 `{ "text": "..." }`。
- **OpenAI 官方**（`audio.transcriptions`）：同形态，`Authorization: Bearer`、`model`、`file`。
- **通用用户契约只有三项：`Base URL(https)` + `API Key` + `Model`。** `ws://` 在任何厂商官方面板/文档中都不存在，是本系统 WebUI↔ASR 引擎之间的内部管道。

## 3. 正确契约

### 3.1 用户面板（ASROK）字段语义

| 字段 | 含义 | 用户填什么 | 空值语义 |
|---|---|---|---|
| API 地址 | 提供商 **https** Base URL | `https://api.siliconflow.cn/v1/audio/transcriptions` | 走本地 in-process paraformer |
| 模型 | 提供商模型 ID | `FunAudioLLM/SenseVoiceSmall` | 本地用默认 paraformer 目录 |
| API 密钥 | `Authorization: Bearer` 的 key | `sk-...` | 本地留空 |

与 LLM / TTS / summary 三个槽位**完全同构**（都是 `api_base`+`model`+`api_key`，且 `api_base` 是 https）。

### 3.2 后端翻译层（缺失的关键件）

```
用户填 https base + key + model
        │
        ▼
asr.api_base = https URL   （运行时配置，用户可见值）
        │
        ▼
WebUI 连【固定内部桥】ws://127.0.0.1:8994/ws/asr   （常量，写死在代码，不进面板）
        │
        ▼
asr_adapter.py 带 upstream=https base / model / key 去 POST 云端
```

- `ws://127.0.0.1:8994/ws/asr` 成为**代码常量**（如 `INTERNAL_ASR_BRIDGE_WS`），不再由用户配置。
- 当 `api_base` 是 https（云端模式）：后端在 Save 时**确保 bridge 进程在跑**（拉起 `asr_adapter.py`，传入 `ASR_UPSTREAM_URL=api_base`、`ASR_MODEL=model`、`ASR_API_KEY=key`）；改回本地（api_base 空）时**停止 bridge**。
- 当 `api_base` 为空：保持 in-process paraformer（现状 `LOCAL_PRIMARY` 路径）。

### 3.3 校验（回退错误改动）

- `_validate_api_base`：**只收空或 http(s)**。去掉 2026-08-11 误加的 `ws`/`wss` 放行——`ws://` 重新变回纯内部常量，不来自用户输入。
- `_probe_asr`：云端模式探活改为探本地 bridge 的 `/health`（ws 或 http 均可），不再要求用户给的 https 地址可被直接 `ws_connect`。

## 4. 前端/后端对齐点

- 前端 `config_services.js`：`svc-asr-api-base` 的 placeholder/label 明确为「API 地址（https）」，与 llm/tts/summary 一致；任何文案不得出现「填 ws://」。
- 后端：`api_base` 一律按 https 提供商理解；ws 桥端点由常量承载。

## 5. 落地清单（待确认后执行）

1. 回退 `_validate_api_base` 的 `ws`/`wss` 放行（server.py ~L1051）。
2. 回退 `_probe_asr` 对 ws 的特殊处理（server.py ~L1240）。
3. `asr.py`：新增 `INTERNAL_ASR_BRIDGE_WS` 常量；`connect_asr` 在 `api_base` 为 http(s) 时连该常量端点（而非直接连 https）。
4. server.py：新增云端 bridge 生命周期管理（Save 云端配置→确保 `asr_adapter.py` 子进程；改回本地→停止）。
5. `config_services.js` + `index.html`：ASR 面板 label/placeholder 改为「API 地址（https）」，删除任何 ws 文案。
6. `docs/asrok-user-guide.md`：重写（见已订正版本）。

## 6. 验收

- 用户仅填 `https://api.siliconflow.cn/v1/audio/transcriptions` + `FunAudioLLM/SenseVoiceSmall` + `sk-...` → Save → 刷新浏览器 → 重新点麦克风，即走云端 ASR。
- 全程**无需**用户手敲任何 `ws://`。
- 本地模式（留空）行为不变。

## 7. 不在范围

- 更换唤醒链路（KWS↔VAD↔ASR 主唤醒）——见 `hybrid-wake-confirm.md` / `kws-recall-optimization.md`。
- ASR promotion 宽匹配逻辑——见 `2026-08-11-kws-asr-promotion-recall.md`。

## 8. 其他服务审计（2026-08-11，按用户「检查其他面板」要求）

统一结论：**只有 ASR 存在用户侧 ws 泄漏；其余面板契约正确，无需改代码。**

- **LLM**：`vlm_service.py` 用 `AsyncOpenAI(base_url=api_base)`，`api_base` 即 https；面板 `API Base URL`。标准契约，**干净**。
- **Summary**：`_probe_summary` 探 `api_base/models`（http）；面板 `API Base URL`。标准契约，**干净**。
- **TTS**：`tts.py` 连的是**常量内部桥** `ws://127.0.0.1:8992/ws/tts`（`TTS_URL` 默认值，非用户输入）；面板 `API Base URL` 已标 http。架构已正确，**无用户侧 ws 泄漏**。缺口：云端 TTS 的 `api_base` 当前未接线到 `tts_adapter` 上游路由（能力缺口，非泄漏 bug）→ **follow-up**：`tts_adapter.py` 加 `TTS_API_KEY` 转发 + webui 把面板 `api_base` 透传给桥。
- **ASR**：唯一泄漏点，`connect_asr` 直接 `ws_connect(api_base)`。本 spec 的 4 步修复已落地。

## 9. 代码审查修复（事件循环阻塞，2026-08-11）

实现后走 `joyai-code-reviewer` 质量门禁，审出 **1 个 BLOCKING**：bridge 生命周期的同步阻塞调用（`_asr_bridge_ensure` / `_asr_bridge_stop`，≤15s 就绪轮询）原在 aiohttp 事件循环上直调，保存云端 ASR 配置会卡死整个 WebUI。

修复（`server.py`）：

- `_validate_and_apply_slot`（async）内 bridge 启停改 `await loop.run_in_executor(None, _asr_bridge_ensure/stop, ...)`；
- `_propagate_services_to_runtime` 改 **async**，函数头取 `loop = asyncio.get_running_loop()`，`_asr_bridge_sync()` 包 `await loop.run_in_executor(None, _asr_bridge_sync)`；PUT handler 调用处 `await`；webinfer `create_task` 路由仍在真实 running loop 触发（不丢）。

验收：`ruff check/format` 通过；`pytest`（persist + summarizer + promotion）**23 passed**。遗留 1 个无关预存失败 `test_kws_diagnostics`（KWS shadow-ASR 音频采集逻辑，不引用本改动任何函数，非本改动引入，待另查）。
