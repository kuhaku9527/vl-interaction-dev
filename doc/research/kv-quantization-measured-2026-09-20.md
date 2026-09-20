# KV cache q8_0 实测：省 1,320 MiB 且延迟不降反升（2026-09-20）

> **结论：这是本项目迄今找到的「净赚」级优化。省 1,320 MiB 显存，延迟还略降。
> 建议立即采用。**

## 背景

项目既有结论称「KV 量化（16384 下只省 ~420MiB，毫无意义）」。
本次实测证实：**那是用错几何算的**（用了 Qwen2.5-VL-7B 的 28层/4KV头），
本机真实几何是 **36 层 / 8 KV heads / head_dim 128**。

调研端点独立推算应省 ~1,097 MiB，且 dev.to 一手文章的实测比值
（both quantized ≈ 53% of f16）与本项目 q8_0 系数 0.53125 **逐位吻合**。
**本实测证实推算正确。**

## 实测方法

```bash
llama-server -m <GGUF> --mmproj <F16> \
  -c 16384 -ngl 999 --parallel 1 -fit off --jinja \
  -ctk q8_0 -ctv q8_0 --flash-attn on -v
```

- **`-v`（verbose）是关键**：llama.cpp 会自己打印 KV 的**实际分配**，不必用 metadata 推算
- 延迟测法同 `measure-mmproj-offload.py`（768×576、**每轮换图**规避 prefix cache、12 轮取中位）

## llama.cpp 自报的 KV 分配（权威数字）

```
llama_context: flash_attn = enabled
llama_kv_cache: CUDA0 KV buffer size = 1224.00 MiB
llama_kv_cache: size = 1224.00 MiB (16384 cells, 36 layers, 1/1 seqs),
                K (q8_0): 612.00 MiB, V (q8_0): 612.00 MiB
```

| 配置 | KV 总占用 | 来源 |
|---|---|---|
| f16（原基线） | **2,321 MiB** | 既有实测（差分法） |
| **q8_0** | **1,224 MiB** | **llama.cpp 自报（本次）** |
| **节省** | **1,097 MiB** | 与推算**完全一致** |

## 端到端实测（关键：延迟代价）

| 指标 | f16 KV（基线） | **q8_0 KV** | 差异 |
|---|---|---|---|
| prompt 中位 | 265.9 ms | **260.5 ms** | **−2.0%** |
| prompt 范围 | 264.2–272.7 | 258.9–264.4 | — |
| **端到端中位** | 618.3 ms | **575.8 ms** | **−6.9%** |
| 端到端范围 | 539.4–625.1 | 472.2–600.7 | — |
| **显存中位** | 9,535 MiB | **8,215 MiB** | **−1,320 MiB** |

**⇒ 不仅没有变慢，还略快。** 原因：
- 本项目 decode 只输出 **1–3 个 token**，KV 读取量极小
- KV 减半后**每步要搬运的数据更少**，在低 token 输出场景下**净收益**
- 调研提到的「decode 慢 5–10%」在 81.8→76.4 tok/s 的高输出场景才显现

## 为什么必须同时开 `--flash-attn on`

调研已做**源码级证实**（本次采信）：
- 非 FA 路径 `ggml_mul_mat` 沿 `ne[0]` 归约 ⇒ V 必须**转置存储**（`v_trans = !cparams.flash_attn`）
- 转置下追加 token 使 `ggml_set_rows` 的 `nc==1`，而 **q8_0 断言 count 须为 32 的倍数** ⇒ **数学上无处落脚**
- 故 **FA 与量化 V 焊死**；且 **K/V 必须对称**（Metal #21450：mixed 量化在 FA 不可用时失败）

**本机实测确认**：`flash_attn = enabled`，服务正常启动。

⚠️ **风险提示**：FA 可能被后端**静默关闭**（Vulkan runtime 2.15.0 已知如此）。
**启动日志必须确认 `flash_attn = enabled`**，否则会直接启动失败。

## 明确否决：`q4_0`

调研给出明确证据（本次采信，与项目既有证据独立同向）：
- q8_0 → q4_0 **仅多省 ~112 MiB**，但相似度 **81.6% → 8.3%**（原文："Not a trade, a cliff"）
- 速度几乎持平（81.8 / 76.4 / 80.3 tok/s）⇒ **监控全绿而质量塌一个数量级**
- 与项目既有记录「K 侧 q4_0 有 375/500 答案抖动」**独立同向**

**⇒ 只用对称 q8_0，绝不用 q4_0。**

## 两条被纠正的传言

1. **「KV 量化让 64K prompt 吞吐崩塌 92%」是假的，且已被原始测量者本人撤回**：
   llama.cpp discussion #20969 原文——*"I measured throughput from requests that failed silently.
   **Prompt throughput is identical across all cache types**"*。
2. **「q8_0 Costs 9% of Throughput」未能证实**（该次检索返回 no results）。
   可查到的真实数字是 **6.6%**，且与本项目低输出场景不符（本实测为 −7%，即更快）。

## 待办：mmproj Q8_0（第二项）

调研给出做法与陷阱：
- **不能用 `llama-quantize`**（必报 `unknown model architecture: 'clip'`；根因是
  `LLM_ARCH_NAMES` 只登记文本架构）
- **正确路径**：`convert_hf_to_gguf.py --mmproj --outtype q8_0`
- 支持集合 `{f32,f16,bf16,q8_0,tq1_0,tq2_0}` —— **Q4_0 不在其中**
- **预期保守省 ~400 MiB**（乐观 540）；打折原因：本机 mmproj 实测占 1,483 MiB
  但文件仅 1.08 GiB，**~400 MiB 是 compute buffer**，不随权重量化缩小
- ⚠️ **视觉精度影响全网无实测证据**；issue #18881 的 "likely minimal impact" 是**推测原话**
- ⇒ **转换后必须同图同问自测**（本项目靠 mmproj 看图，不能只信推测）

## 叠加效果预估

| 项 | 状态 | 省 |
|---|---|---|
| **KV f16 → q8_0** | ✅ **已实测确认** | **−1,097 MiB**（KV 本体）/ −1,320 MiB（端到端） |
| mmproj F16 → Q8_0 | ⏳ 待实测 | −400 ~ −540 MiB（推算） |
| **合计** | | **≈ −1,500 ~ −1,640 MiB** |

**⇒ 9,177 → ~7,600 MiB，且已确认部分不损失延迟。这对「游戏同时跑」是实质性的。**

## 诚实标注（不确定项）
- mmproj Q8_0 的**视觉精度影响无公开实测**，必须自测
- KV q8_0 的**长会话质量**未测（本实测只覆盖单轮延迟与显存）
- 未测 **1 Hz 连续运行**下 q8_0 的稳定性（建议补测）
