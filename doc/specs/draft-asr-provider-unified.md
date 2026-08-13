# Spec：ASR 统一抽象（ASRProvider，本地/云端可插拔）

> 生命周期: **草稿 v1**（2026-08-13）——用户决策"现在做"：jarvis/live 对话 ASR 也支持云端（提识别率，云端延迟实测不高、兼容 OpenAI 协议可换提供方）
> 上游: `决策/服务-语音栈.md`（D-045/D-080）+ `services/asr/jarvis/asr.py`（JarvisASR）+ `services/webui/.../asr.py`（call 云端路径）
> 问题: ASR 双路径分裂——call 可配置云端上游，jarvis/live 硬编码本地 sherpa（换提供方无插槽）→ 同一能力两套实现，识别率差距（用户实测）

---

## §1 目标

1. **统一 ASR 抽象**：`ASRProvider` 接口，jarvis/live/call 共用；提供方可配置（local 本地 sherpa / cloud 云端），换提供方=改配置不改代码。
2. **jarvis/live 支持云端**：对话 ASR 可选云端（提识别率），默认仍本地（保回归）。
3. **统一健康探测**：provider 级可达性探测（本地=模型可用；云端=上游可达，含鉴权）。

## §2 ASRProvider 接口

```python
class ASRProvider:
    def start(self) -> None: ...          # 会话开始
    def feed_chunk(self, pcm: bytes) -> str: ...  # 喂 PCM，返回 partial 文本（本地真流式；云端无 partial 返回 ""）
    def stop(self) -> str: ...            # 结束，返回 final 文本（本地=last_text；云端=积累后整段识别）
    def reset(self) -> None: ...          # 重置会话
    @property
    def available(self) -> bool: ...      # 提供方可用性（加载/探测结果）
    @property
    def streaming(self) -> bool: ...      # True=流式（有 partial）；False=整段（准流式）
```

实现：
- **LocalStreamingProvider**：包装现有 `JarvisASR`（sherpa streaming-paraformer，真流式）——现状行为逐字节不变；
- **CloudBatchProvider**：积累 PCM → 端点触发 `stop()` 整段 POST（复用 call 的桥/上游客户端逻辑，OpenAI `/v1/audio/transcriptions` 兼容——SiliconFlow 等可换）。

## §3 jarvis/live 接入（行为兼容设计）

- **env 配置**：`JARVIS_ASR_PROVIDER=local|cloud`（默认 local=现状零变化）；云端时复用 `ASR_UPSTREAM_URL`/`ASR_API_KEY`（已配 SiliconFlow）。
- **jarvis_mode/live_mode `_init_asr`**：按 env 创建 provider（本地=现有 JarvisASR；云端=CloudBatchProvider）；`_feed_asr_chunk` 调 `provider.feed_chunk`（云端返回 "" → partial 字幕为空，但不阻塞流程）。
- **endpoint 语义**：
  - 本地：sherpa endpoint + 2s 停滞（现状）；
  - 云端：**积累策略**——静默 2s（沿用现有计时）后调 `provider.stop()` 拿 final（整段识别延迟 = 云端 1 次往返，用户实测不高）；无 partial 阶段。
- **退出词/打断影响（关键语义变化）**：jarvis EXIT_WORDS 检查基于 partial——云端无 partial 时，退出词检测延迟到 final（结束后再判，语义略变但可用）；live 无退出词不受影响。**记录该差异，真机验证时确认可接受**。
- **addressee 声学门控**（live）：门控在 ASR 前（VAD 段 classify）——与 provider 无关，不变。

## §4 健康探测

- `_probe_asr`（service_probe）扩展：provider=cloud 时探测上游可达（复用现有桥探测逻辑）；provider=local 时现状（模型存在性）。
- 云端不可达：jarvis/live 显式 error（仿 D-080 语义，不静默降级本地——**除非** `JARVIS_ASR_ALLOW_LOCAL_FAILOVER=1` 显式 opt-in）。

## §5 实现分层（团队）

1. `services/asr/jarvis/asr_provider.py`（新）：ASRProvider 接口 + LocalStreamingProvider（包装 JarvisASR）+ CloudBatchProvider（复用 asr.py 的云端客户端/桥逻辑——**抽公共**，消除 call 与对话的重复）；
2. `jarvis_mode.py` / `live_mode.py`：`_init_asr` 按 env 选 provider；feed 链路适配（partial 可空）；
3. `asr.py`（webui）：云端客户端逻辑抽为共享（CloudBatchProvider 复用）；
4. env 文档（run-windows.env.example 补 JARVIS_ASR_PROVIDER）+ 语音栈决策更新（走审查组）；
5. 测试：LocalStreamingProvider 与现状等价（jarvis 全链路回归）；CloudBatchProvider mock（积累→POST→final）；provider 切换（env local/cloud）；健康探测；jarvis 全量回归（默认 local 零变化）。

## §6 风险

| 风险 | 缓解 |
|---|---|
| 云端无 partial → jarvis 字幕/退出词语义变化 | 记录差异；默认 local 保现状；云端为 opt-in，真机验证后定 |
| 云端整段延迟（vs 本地流式） | 用户实测不高；静默 2s + 1 次往返 |
| call 云端客户端逻辑复用侵入 | 抽公共函数，call 行为零变化（回归测试） |
| 云端挂时 jarvis/live 静默 | 显式 error（D-080 语义），failover 需显式 opt-in |

## §7 关联

- `决策/服务-语音栈.md`（D-045 ASR :8993 云 opt-in 落地 / D-080 显式报错）
- `services/webui/src/joy_interaction_webui/asr.py`（call 云端路径，抽公共）
- `services/asr/jarvis/asr.py`（JarvisASR 本地流式）
- 用户观察：云端 ASR 延迟不高、兼容性高可换 API → 支持云端作为对话识别选项
