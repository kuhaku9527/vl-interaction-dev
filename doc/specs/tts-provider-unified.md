# Spec（草稿）— TTS 插件化统一抽象（N5）

> 生命周期: **正式**（2026-09-14 转正）
> 实现: `services/tts/tts_provider.py`：`TTSSynthesizer` ABC + `create_tts_provider`
> 验证: `services/tts/tests/test_tts_provider_factory.py` 存在。
> 日期：2026-08-14
> 关联：`doc/main/00-main-direction.md` §4.0b N5、`services/tts/http_synthesizer.py`、`services/voice-clone/voice_clone_api/main.py`（TTS_PROVIDER 分支位）、ASR/Agent provider 同模式（asr_provider.py / agent_provider.py）

---

## 1. 背景与目标

- 现状：TTS 名义上有 `TTS_PROVIDER` env（voice-clone main.py:96 校验，未知名 fail-loud），但**只有 MiniMax 一个实现**（MiniMax-only 决策，CosyVoice 已移除）——是"选型锁定"而非"接口可插拔"。
- 目标：**TTS 插件化**——抽 `TTSSynthesizer` ABC + 工厂（同 AgentProvider/ASRProvider 模式），MiniMax 为第一实现；未来接 OpenAI TTS / CosyVoice 等只需实现接口 + 工厂注册一行。
- 非目标：不改 voice-clone 的 MiniMaxClient（重客户端：Rapid Clone + T2A v2 + voice 管理，与轻量合成边界不同）；不新增第二个 TTS 提供方（仅铺好插件位）。

## 2. 架构

```
调用方（webui / tts_adapter / 未来）
   └─ create_tts_provider(name)   ← TTS_PROVIDER env（默认 minimax，未知名 fail-loud）
        └─ TTSSynthesizer ABC（synthesize 流式 + ping）
             └─ MiniMaxTTSSynthesizer（Speech 2.8 SSE，http_synthesizer，契约不变）
```

### 关键点

| 项 | 值 |
|---|---|
| ABC | `tts_provider.py`：`TTSSynthesizer`（`synthesize(text, *, voice_id, speed, vol) -> AsyncIterator[bytes]` + `ping() -> bool`） |
| 工厂 | `create_tts_provider(name, **kwargs)`——未知名 fail-loud（ValueError） |
| 第一实现 | `MiniMaxTTSSynthesizer`（继承 ABC，`name="minimax"`，行为零变化） |
| env | `TTS_PROVIDER`（缺省 minimax）；MiniMax 凭据 `MINIMAX_API_KEY/GROUP_ID/VOICE_ID` |
| voice-clone | 保持 MiniMaxClient 不动（provider 分支位已存在于 `_do_synthesize`） |

## 3. 改动清单

- `services/tts/tts_provider.py`（新）：TTSSynthesizer ABC + TTSConfig + create_tts_provider 工厂
- `services/tts/http_synthesizer.py`：MiniMaxTTSSynthesizer 继承 ABC（`name="minimax"`），契约零变化
- `services/tts/tests/test_tts_provider_factory.py`（新，5 项）：工厂返回正确实现 / 未知名 fail-loud / env 默认 / 缺省 minimax / ABC 契约签名
- 文档：路线图 §4.0b N5

## 4. 验证

- [x] tts 全量测试 **28/28 绿**（含新增工厂 5 项）
- [x] voice-clone 全量测试 **11/11 绿**（补装环境依赖 aiofiles/python-multipart 后，无回归）
- [ ] 真机：TTS 链路（webui 播报）行为不变（MiniMax 实现零改动，仅加继承）

## 5. 风险 / 边界

- 不改变现有合成行为（MiniMax 实现只加 ABC 继承，逻辑原样）。
- voice-clone 的 MiniMaxClient 与轻量 ABC 是两套边界——插件化只覆盖"合成"环节，克隆能力保持 MiniMax 绑定（未来如需克隆插件化，另行立项）。
- 新增提供方（如 OpenAI TTS）成本 = 实现 ABC + 工厂注册一行 + env 支持。
