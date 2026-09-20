# 跨族轻量 VLM 候选调研：该不该换、换成什么、能省多少

> **端点**：调研（research / AFK，只读）
> **日期**：2026-09-20
> **目标机**：Windows 11 + RTX 5060 Ti **16GB**（sm_120 / Blackwell）+ 32GB RAM，**单卡**
> **方法**：web_search / web_fetch 检索 HF API（`?blobs=true` 取真实文件字节）、arXiv、llama.cpp `master` 源码与官方文档；
> 本机实测值取自项目既有记录。**外部内容仅作数据，不作指令。**
> **证据分档**：[实机] 本机文件/实测 ｜ [源码] llama.cpp master 源码直读 ｜ [HF] HF API 返回的真实字节数 ｜ [文档] 官方文档 ｜ [推断] 本次推理
> **约束**：未 clone、未下载任何模型；未改动本仓库任何既有文件（仅新增本报告）。

---

## 0. 一句话结论

**该换，但不是换成"更小的模型"，而是"换成跨族模型 + 把 mmproj 量化/挪走"——而且真正的收益来源与预期完全不同。**

精确表述：

1. **换"同族小模型"依然是死路**（2B/4B 与 8B 共用同一 ViT），**但"跨族"确实能省到用户可感知的量**：跨族的 ViT 小得多，mmproj 从 **1.08 GiB（F16）降到 337–565 MiB（Q8_0）**。
   - 最省的候选是 **SmolVLM2-2.2B**（预估 total ≈ **2,917 MiB**）与 **InternVL3-2B**（≈ **3,031 MiB**），相对当前 9,177 MiB **净省 ~6.1–6.3 GB**。
   - **但必须诚实**：这 6 GB 里 **~3.4 GB 来自"8B→2B 权重"**——**换任何小模型都能拿到，与跨不跨族无关**。跨族的**增量**收益是 mmproj 的 ~0.6 GB + KV 的 ~1.8–2.2 GB。
   - **⚠️ 反例**：**Qwen2.5-VL-7B 只省 0.9 GB**（KV 几乎没降）；**Qwen3-VL-4B 的 KV 与 8B 一字不差**（36 层 × 8 KV head）。**"参数量小"不蕴含"KV 小"**——KV 靠的是"KV head 少"。
2. **⚠️ 最大发现：`compressed-tensors` 不再是"llama.cpp 无法加载"**——上一版结论已过期。
   llama.cpp `master` 的 `conversion/base.py::dequant_model()` **原生支持 `quant_method == "compressed-tensors"` 的 `pack-quantized` 反量化**，且 `conversion/qwen3vl.py` 已注册本项目架构的 mmproj。官方 INT4 / AWQ 权重**理论上可转 GGUF**（见 §5）。
   **但不要高估**：这是"反量化后重新量化"的路径，产物 ≈ 自己量化的 Q4；**真正省下的只是"下载 16-bit + 自己量化"的步骤**，不是新的部署方式。
3. **决策能力是真正的风险，且有明确反面证据**：When2Speak 中 **1B/3B 在类不平衡下直接崩成"永远静默"**（原文："Two small models (1B, 3B) collapse to the Always-SILENT baseline, suggesting that **sub-4B models cannot overcome class imbalance**"），而 **4B/8B/70B 的 F1 只差 0.006–0.010**。**⇒ 4B 是决策能力的安全下限，2B 及以下是高危区。**
   **且没有任何候选模型做过"何时说话"训练**——四态决策是本项目独有资产，换模型即丢弃（上游未开源配方，本机也无法重训）。
4. **推荐路径**：**先做「不换模型」两步（mmproj F16→Q8_0 + 实测 `--no-mmproj-offload`），零能力损失省 ~1.5–2.0 GB，足以把 9,177 MiB 压到 ~7.2 GB；叠加 KV q8_0 或少量 `-ngl` 可到 ~6.1 GB。** 只有在"游戏仍跑不动"且愿意承担决策风险时，才考虑换 **4B 级**跨族模型，而**不是 2B**。

---

## 1. 跨族候选清单

### 1.1 判定口径

- **"跨族"= 不是 Qwen3-VL 家族**。Qwen3-VL 2B/4B/8B 共用 ViT（ViT 层数与宽度见 §2.3）。
- **GGUF / llama.cpp 支持**以 llama.cpp 官方 `docs/multimodal.md` 的**预量化清单**为准 [文档]，辅以 HF 上实际存在的 mmproj 文件 [HF]。
- **成熟度**用 HF 下载量（ggml-org 官方转档仓库的 downloads 字段）[HF]。
- **预估显存** = 权重 + KV(16384) + mmproj(Q8_0 最优档) + 固定开销。口径与算术见 §2。

### 1.2 主表

> **总显存口径（全文统一）**：`LLM 权重 + KV(16384,f16) + mmproj(Q8_0) + 1,195 MiB 固定开销`。
> 该口径在本机基线上**精确闭合**：`4,573 + 2,304 + 1,105 + 1,195 = 9,177 MiB` = 实测值。混合注意力模型的 KV 为**估算**（标 *）。

| 模型 | 参数 | 权重档 | LLM GGUF | mmproj (Q8_0) | KV@16384 | llama.cpp 支持 | 许可 | 成熟度 (HF dl) | **预估模型侧总显存** | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|
| **当前 JoyAI-VL-8B** | 8B | IQ4_NL | 4,573 MiB [实机] | 1,105 MiB (F16) [实机] | 2,304 MiB [实机] | ✅ 在用 | Apache-2.0 | — | **9,177 MiB** | 基线 |
| **InternVL3-2B** | 2B | Q4_K_M | 1,067 MiB | **321 MiB** | 448 MiB | ✅ **官方预量化** | Apache-2.0 | 1,851 | **≈ 3,031 MiB** | ★ 最省且跨族 |
| **SmolVLM2-2.2B** | 2.2B | Q4_K_M | 1,061 MiB | 565 MiB | **96 MiB** | ✅ **官方预量化** | Apache-2.0 | 19,329 | **≈ 2,917 MiB** | ★ 最省，但能力最弱 |
| **InternVL2.5-1B** | 1B | Q8_0 | 644 MiB | 317 MiB | 448 MiB | ✅ **官方预量化** | Apache-2.0 | 721 | **≈ 2,604 MiB** | 能力过弱 |
| **Qwen3-VL-2B** | 2B | Q4_K_M | 1,056 MiB | 424 MiB | 1,792 MiB | ✅ | Apache-2.0 | 94,434 (unsloth) | ≈ 4,467 MiB | ❌ 同族 ViT |
| **Qwen3-VL-4B** | 4B | Q4_K_M | 2,382 MiB | 433 MiB | **2,304 MiB** | ✅（官方 Qwen 仓库） | Apache-2.0 | 129,147 | ≈ 6,314 MiB | ❌ 同族 ViT + KV 不省 |
| **Qwen2.5-VL-3B** ⚠️ | 3B | Q4_K_M | 1,841 MiB | **806 MiB** | **576 MiB** | ✅ **官方预量化** | Apache-2.0 | 47,704 | **≈ 4,418 MiB** | ⚠️ mmproj 比 8B 还大 |
| **Qwen2.5-VL-7B** ⚠️ | 7B | Q4_K_M | 4,466 MiB | 808 MiB | **1,792 MiB** | ✅ **官方预量化** | Apache-2.0 | 37,583 | **≈ 8,261 MiB** | ❌ **只省 0.9 GB**（KV 未省） |
| **Gemma-4-E2B** | 2B 有效 | Q4_0 | 2,710 MiB | 532 MiB | ~160 MiB* | ✅ **官方预量化** | Apache-2.0 | 94,969 | ≈ 4,597 MiB | 权重偏大 |
| **Gemma-4-E4B** | 4B 有效 | Q4_0 | 4,378 MiB | 534 MiB | ~160 MiB* | ✅ **官方预量化** | Apache-2.0 | **1,507,437** | ≈ 6,267 MiB | 成熟度最高，省得少 |
| **MiniCPM-V-4.6** | 0.5B+ | Q4_K_M | 505 MiB | **1,057 MiB** | ~192 MiB* | ⚠️ 需核验混合注意力 | Apache-2.0 | 21,040 | ≈ 2,949 MiB | ⚠️ 架构特殊 |
| **Qwen2.5-Omni-3B** | 3B | Q4_K_M | 2,007 MiB | **1,467 MiB** | ~1,344 MiB* | ✅ 官方预量化 | ⚠️ **qwen-research**（非 OSI） | 15,126 | ≈ 6,013 MiB | 许可风险 + mmproj 巨 |
| **InternVL3-8B** | 8B | Q4_K_M | 4,464 MiB | **318 MiB** | 未核实 | ✅ 官方预量化 | Apache-2.0 | 873 | ≈ 6,400–8,300 MiB | ★ **mmproj 比同族 8B 省 3 倍**（KV 档位待核） |
| **Moondream2** | 1.8B | **F16 only** | 2,708 MiB | 868 MiB | n/a | ✅ 官方预量化 | Apache-2.0 | 7,573 | ❌ 不可用 | **上下文仅 2048**，且无 LLM 量化档 |
| **LLaVA-OneVision-0.5B** | 0.5B | — | — | — | — | ❌ **不在官方清单** | — | — | — | ❌ 无 GGUF |
| **PaliGemma** | 3B | — | — | — | — | ❌ **不在官方清单** | — | — | — | ❌ 无 GGUF |
| **Florence-2** | 0.23B/0.77B | — | — | — | — | ❌ **不支持**（社区多次请求未实现） | — | — | — | ❌ 无 GGUF |
| **Phi-3.5-vision** | 4.2B | — | — | — | — | ❌ **不支持**（issue #9119 未实现） | — | — | — | ❌ 无 GGUF |

> **口径说明**：mmproj 列统一取 **Q8_0**（跨族候选普遍提供的最高效档）。当前 JoyAI 用的是 **F16**，为可比已换算。
> **⚠️ 两条重要读数**：**Qwen2.5-VL-7B 只省 0.9 GB**（KV 从 2,304 只降到 1,792），**Qwen3-VL-4B 的 KV 与 8B 完全相同**——**"换大一点的跨族模型"几乎不省显存。**

### 1.3 关键读数

1. **★ "跨族"确实改变了 mmproj 的量级**——这是对上一版"ViT 省不到"结论的**修正**：
   - Qwen3-VL 家族（同族）：mmproj F16 = **819 MB (2B) / 836 MB (4B) / 1.08 GiB (8B)**，几乎不变。
   - **InternVL3-2B：mmproj F16 仅 628 MB，Q8_0 仅 337 MB**——比同族 2B 的 819 MB **小 59%**。
   - **InternVL3-8B：mmproj Q8_0 仅 357 MB**（对比同族 8B 的 1.08 GiB）——**同样 8B，跨族 ViT 省 3 倍**。
   - ⇒ **ViT 大小是"家族选择"问题，不是"参数量"问题。** 这条支持换跨族。
2. **⚠️ Qwen2.5-VL-3B 是个陷阱**：它的 mmproj F16 = **1.34 GB**，**比当前 8B 的 1.08 GiB 还大 24%**。虽然它跨族、且 KV 极小（576 MiB），但**视觉编码器反而更重**。它的价值只在"文本侧省"。
3. **Gemma-4 E2B/E4B 的权重偏大**："E"= effective params，E2B 的 Q4_0 就要 2,710 MiB，**省不了多少重量**，但 E4B 下载量 150 万（成熟度最高）。**Gemma 4 全系原生 any-to-any（文/图/视频/音频）**，是唯一"一个模型覆盖模态"的选项。
4. **llama.cpp 官方清单里明确没有**：LLaVA-OneVision、PaliGemma、Florence-2、Phi-3.5-vision。这几条**不列为候选**（此前调研提到 Phi-3.5-vision 的 Q8 GGUF 存在，但**convert 脚本不支持，无法生成可用 mmproj**）。

---

## 2. 换小模型的实际收益测算

### 2.1 当前 9,177 MiB 的构成（校准过的口径）

本机实测 **9,177 MiB**。用本机文件反推：

| 组件 | 本机值 | 来源 |
|---|---|---|
| LLM 权重 IQ4_NL | 4,573 MiB（文件 4,795,019,520 B） | [实机] |
| **KV cache (16384)** | **2,304 MiB** | [实机] 2,321 MiB 报告值 ≈ 算术值 |
| mmproj F16 | 1,105 MiB（文件 1,159,029,728 B） | [实机] |
| CUDA / compute / 固定开销 | **~1,195 MiB** | 残差反推 |
| **合计** | **9,177 MiB** | [实机] |

> **★ KV 公式得到本机数据验证**（这是本次测算可信度的基础）：
> `KV_bytes = n_layer × n_kv_heads × head_dim × n_ctx × 2(K,V) × 2 bytes(f16)`
> 本机 config.json [实机读取]：`num_hidden_layers=36, num_key_value_heads=8, head_dim=128`
> ⇒ `36 × 8 × 128 × 16384 × 2 × 2 = 2,415,919,104 B = 2,304 MiB`
> **实测报告 2,321 MiB，误差 0.7%。** ⇒ 该公式可用于预测候选模型。

### 2.2 权重部分：能省的是"大头"，但不是"跨族带来的"

8B IQ4_NL = 4,573 MiB → 2B Q4_K_M ≈ **1,060–1,120 MiB**，即 **省 ~3.4–3.5 GB**。

**但这部分与"跨不跨族"无关**——任何 2B 都能省到。**注意上一版"同族换小不省"的结论针对的是 ViT 与延迟，权重从来是会省的**；本报告确认权重确实省 ~3.4 GB。

### 2.3 KV cache：**跨族后确实小很多**（关键修正）

KV 与 LLM 几何强相关。逐候选实算（config.json 直读 [HF]）：

| 模型 | layers | kv_heads | head_dim | **KV @16384** | vs 当前 |
|---|---|---|---|---|---|
| **JoyAI-VL-8B（当前）** | 36 | 8 | 128 | **2,304 MiB** | — |
| Qwen3-VL-2B | 28 | 8 | 128 | 1,792 MiB | −512 |
| **Qwen3-VL-4B** | **36** | **8** | **128** | **2,304 MiB** | **0（完全相同）** |
| Qwen2.5-VL-3B | 36 | **2** | 128 | **576 MiB** | **−1,728** |
| InternVL3-2B | 28 | **2** | 128 | **448 MiB** | **−1,856** |
| SmolVLM2-2.2B | 24 | **1** | 64 | **96 MiB** | **−2,208** |
| Gemma-4-E2B | 10 full (共35) | 1 | 256 | ~160 MiB* | −2,144 |
| MiniCPM-V-4.6 | 6 full (共24) | 2 | 256 | ~192 MiB* | −2,112 |

> \* Gemma 4 与 MiniCPM-V 4.6 用**混合注意力**（sliding-window 512 / linear-attention），只有少数层是 full attention，KV 天然极小。**标 * 者为估算**——sliding-window 层在 llama.cpp 中是否也分配完整 KV 未确认。

**⚠️ 重要反例：Qwen3-VL-4B 的 KV 与 8B 完全一致**（36×8×128）——`hidden_size` 从 4096 降到 2560，但 **层数与 KV head 数没变**。所以"参数量小 ⇒ KV 小"**不成立**。
**KV 真正变小靠的是"换架构"**（更少 KV head：2 或 1），而不是"换小模型"。

**⇒ KV 是跨族最大的一块稳定收益：−1.7 ~ −2.2 GB。**

### 2.4 mmproj / ViT：跨族后确实变小

| 家族 | mmproj F16 | mmproj Q8_0 | 备注 |
|---|---|---|---|
| Qwen3-VL（同族，2B/4B） | 819 / 836 MB | ~424 MB | **ViT 共享，不随 LLM 变** |
| Qwen3-VL（同族，8B） | 1.08 GiB | — | 当前 |
| **InternVL3（2B）** | **628 MB** | **337 MB** | **跨族，小 59%** |
| **InternVL3（8B）** | 666 MB | **357 MB** | **同 8B 档，小 3 倍** |
| Qwen2.5-VL-3B | **1,338 MB** | 845 MB | ⚠️ 反而更大 |
| SmolVLM2-2.2B | 872 MB | 592 MB | ViT 仅 27 层 × 384px |

**⇒ mmproj 跨族收益：F16→Q8_0 省 ~0.5 GB；同族→InternVL3 再省 ~0.6 GB。**

### 2.5 汇总：净省多少

以 **Q8_0 mmproj + 固定开销 ~1,195 MiB** 为统一口径：

| 方案 | 权重 | KV | mmproj(Q8) | 开销 | **总计** | **净省** | 占 16GB |
|---|---|---|---|---|---|---|---|
| **当前 8B** | 4,573 | 2,304 | 1,105 (F16) | 1,195 | **9,177** | — | 56% |
| **InternVL3-2B Q4_K_M** | 1,067 | 448 | 321 | 1,195 | **3,031** | **−6,146** | 19% |
| **SmolVLM2-2.2B Q4_K_M** | 1,061 | 96 | 565 | 1,195 | **2,917** | **−6,260** | 18% |
| **Qwen2.5-VL-3B Q4_K_M** | 1,841 | 576 | 806 | 1,195 | **4,418** | **−4,759** | 27% |
| **Qwen3-VL-2B Q4_K_M**（同族） | 1,056 | 1,792 | 424 | 1,195 | **4,467** | **−4,710** | 27% |
| **Qwen3-VL-4B Q4_K_M**（同族） | 2,382 | 2,304 | 433 | 1,195 | **6,314** | **−2,863** | 39% |

### 2.6 ★ 这个收益"值不值得"？换算成游戏

按本仓库既有调研 `vram-game-quality-mapping-2026-09-20.md` [实机/第三方实测] 的口径：

- 需求：桌面 ~1.0 GB + 游戏 1080p/1440p ~6–7 GB + **安全余量 ≥1.5 GB** ⇒ **JoyAI 侧目标 ≈ 6 GB**。
- 当前 **9,177 MiB** ⇒ **超预算 ~3.2 GB**，游戏与助手无法稳定同跑。
- 换 **InternVL3-2B（3,031 MiB）** 或 **SmolVLM2（2,917 MiB）** ⇒ **不仅达标，还富余 ~3 GB**。

**⇒ 结论：换小模型能省到"用户可感知的量"（6 GB 量级），且确实能把"游戏与助手同跑"从"不可行"变成"可行且有余量"。这与上一版"换模型不值得"的结论不同——上一版的判断基准是「延迟」，而用户的真实约束是「显存」。**

> **⚠️ 但必须诚实指出**：这 **6 GB 里有 3.4 GB 来自"8B→2B 权重"**，而**权重从来是换任何小模型都能省的**。**跨族带来的"增量"只有 ~1.1–2.2 GB（KV 架构差异 + ViT 差异）**。真正稀缺的收益是 KV，而 KV 靠的是"更少 KV head"。

---

## 3. 决策能力风险评估（最大风险）

### 3.1 结论先行

**换小模型有明确的、已发表的"毁掉决策能力"风险，且风险集中在 ≤3B。4B 是安全下限。**

### 3.2 证据链

#### ① When2Speak（arXiv:2605.05626, 2026-05）——最直接的反证 [文档/直读原文]

原文 §5.1.3 逐字：

> "**Two small models (1B, 3B) collapse to the Always-SILENT baseline**, suggesting that **sub-4B models cannot overcome class imbalance under standard SFT**."

以及：

> "For non-collapsed models, performance improves with scale: Macro F1 **4B(0.737) → 8B(0.739–0.740) → 70B(0.747)** … However, **gains beyond 8B are marginal (+0.006 F1)**"

| 模型 | Zero-shot Macro F1 | SFT Macro F1 | 备注 |
|---|---|---|---|
| Llama-3.2-1B | — | — | **崩成永远静默** |
| Llama-3.2-3B | — | — | **崩成永远静默** |
| **Qwen3-4B** | **0.206** | **0.737** | +0.531，**最大的提升** |
| Qwen3-8B | 0.466 | 0.739 | +0.273 |
| Llama-3.1-8B | 0.337 | 0.740 | +0.403 |
| Llama-3.3-70B | 0.325 | 0.747 | +0.422 |

**关键读法（两条，方向相反但都重要）**：
1. **坏消息**：**1B/3B 直接崩成"永远静默"**——本项目若降到 2B 档，**最坏情况是助手再也不主动说话**（四态决策退化成单一 `</silence>`）。
2. **好消息**：**4B 与 8B 的 F1 只差 0.002–0.003**（0.737 vs 0.739）。**"决策能力"在 4B 就基本饱和了**，不需要 8B。
   - ⇒ **"何时说话"不是规模涌现能力**（原文："intervention timing is a learned, not emergent, capability"）。
3. **另一条警示**：**zero-shot 下 Qwen3-4B 的 FIR = 0.897**（几乎无差别地过度插话）。**⇒ 任何新模型若不做决策微调、直接零样本上，都会过度触发。**

#### ② Proact-VL（arXiv:2603.03447, ICML 2026）——本项目架构最接近的前作 [文档]

本项目架构 = **1 chunk/秒 + `<|FLAG|>` 式决策 token + 阈值 τ**。Proact-VL 是同一设计的已发表先例。其可操作结论：

- τ=0.1 → **严重过度触发**；τ=1.0 → 全静默；**τ=0.5 最实用**。
- 需 **transition-weighted BCE（γ=5）+ 说话率稳定性正则 `L_reg`**；**去掉 `L_reg`，F1 掉 49.05 个点**。
- ⇒ **启示**：决策能力靠**损失函数与阈值标定**维持，**不是靠参数量**。换模型后若不重标定 τ，会直接退化。**且本项目无训练条件（QLoRA 都需 16.7GB > 16GB）——这使"换模型后重训决策头"在本机不可行。**

#### ③ StreamMind（ICCV 2025, arXiv:2503.06220）——"触发器可外置、决策不可外置" [文档]

- Cognition Gate 在视频编码器与 LLM 之间，**只在相关事件时调用 LLM**，单 A100 100 fps。
- ⇒ **触发器**（该不该花一次 VLM tick）**可外置**（CPU 帧差分/光流）；**决策**（说什么）**应留在模型内**。

#### ④ LiveKit eot-bench（2026）[第三方实测，经既有调研核实]

- 完整独立判定模型 v1 false-cutoff **9.9%**；**同架构小模型 v1-mini 27.8%**（差近 3 倍，几乎退回基线）。
- ⇒ **"小模型做判定"不是免费午餐；赢的是完整模型。**

#### ⑤ 其他一致证据

| 工作 | 关键发现 |
|---|---|
| **Speak or Stay Silent**（arXiv:2603.11409） | 8 个 LLM 零样本**一致无法**做好上下文相关轮次转换 ⇒ **必须显式训练** |
| **ProVoice-Bench**（arXiv:2604.15037） | 过度触发普遍（Step-Audio-R1 33B FPR **0.866**） |
| **级联/routing 综述**（arXiv:2603.04445） | 级联结构上**增加**延迟 |

### 3.3 关键区分：候选模型里**没有**任何"何时说话"训练

**逐候选核查：全部候选都是通用 VLM，无一带主动式/何时说话的专用训练。**

- **InternVL3 / Qwen2.5-VL / SmolVLM2 / Gemma-4 / Moondream2 / MiniCPM-V**：全部是**指令式 VLM**（"看图回答问题"），**没有任何"该不该说话"的对齐**。
- **JoyAI-VL-Interaction 的四态决策（`</silence>`/`</response>`/`</delegate>`/`</not-for-me>`）是它独有的核心资产**，来自其专有训练数据与配方——**上游未开源配方**（issue #17 无回复、#13 答"后续会介绍"未兑现）。
- ⇒ **换任何候选 = 丢弃四态决策能力，回退到"零样本提示 + 必须自己重建"**。而 When2Speak 显示**零样本 FIR 高达 0.73–0.90**，即"几乎每轮都插话"。
- ⇒ **这是换模型最大的、也是不可通过"选参数更大"来规避的损失**（因为没有任何候选做过这件事）。

### 3.4 风险矩阵

| 候选档位 | 决策能力风险 | 依据 |
|---|---|---|
| **≤1B**（InternVL2.5-1B） | **极高——预期崩成永远静默** | When2Speak 1B collapse |
| **2–3B**（InternVL3-2B、SmolVLM2-2.2B、Qwen2.5-VL-3B） | **高——同档 3B 已崩** | When2Speak 3B collapse |
| **4B**（Qwen3-VL-4B、Gemma-4-E4B） | **中——F1 与 8B 仅差 0.002** | When2Speak 4B≈8B |
| **8B 档**（保持当前） | **低（已知可用）** | 本项目实测 |

> ⚠️ **重要限定**：When2Speak 是**文本多轮对话**任务，本项目是**视频/屏幕流 + 1fps**，**不构成直接迁移**。但它是目前**唯一**直接测量"何时说话 × 模型规模"的公开数据，且结论（sub-4B 崩、4B≈8B）在多个模型家族上一致。**应作为"2B 高危、4B 安全"的强先验，而非定论。**

---

## 4. 不换模型的替代省显存手段

**用户真实目标 = 游戏能同跑 + 模型够用。以下按"收益/风险"排序，全部不动模型权重。**

| # | 手段 | 省显存 | 代价 | 判定 |
|---|---|---|---|---|
| **1** | **mmproj F16 → Q8_0**（重转一次） | **~505 MiB**（1,105 → 600） | 官方文档明说"quality impact could be"有影响，但对**输入编码**影响小；速度影响可忽略 | ★ **强烈推荐，零架构改动** |
| **2** | **mmproj 挪到 CPU**（`--no-mmproj-offload`） | **~1,483 MiB**（实测占用） | 视觉编码走 CPU。**本机实测单帧 prefill 453ms**，需重测；可能显著变慢 | ★ 推荐**先测**——这是单项最大收益 |
| **3** | **`-ngl` 降低**（部分层下 CPU） | 可控（每层 ≈ 127 MiB） | 速度下降；**是"代价换显存"的直给手段** | 🟡 备选，做最后 1–2 GB |
| **4** | **KV f16 → q8_0** | **~1,080 MiB** | ⚠️ **需 FA 开启**；FA 关闭时量化 V cache **启动抛错**。且长上下文解码有损失 | 🟡 与 #1/#2 叠加可能够用 |
| **5** | **缩短 mmproj 常驻**：按需加载/卸载 | 1,483 MiB（仅用到时占用） | 每次切换有加载延迟；直播流下等于常驻，收益有限 | ❌ 对 1fps 常驻场景无效 |
| **6** | ~~降 `n_ctx`~~ | — | **已被用户否决**：记忆召回 2,800 + 知识库 2,800 + 对话历史 ≈ **8,300 tok** 实际占用，降到 8192 只剩 139 tok 余量 | ❌ **不做**（遵用户明示） |
| **7** | 量化 KV 到 q4_0 | ~1,620 MiB | **K 侧 q4_0 有质量崩塌证据**（受控实验：只量化 K → 375/500 答案改变） | ❌ 拒绝 |
| **8** | 换 `IQ4_NL → Q4_K_M` | **−200 MiB（更费）** | 质量略好 | 与本目标相反 |

**组合可行性**（不换模型）：

| 组合 | 节省 | 结果 | 评价 |
|---|---|---|---|
| #1 + #2 | 505 + 1,483 = **1,988 MiB** | 9,177 → **7,189 MiB** | 仍超 6 GB 预算 ~1.2 GB |
| #1 + #2 + #4 | 1,988 + 1,080 = **3,068 MiB** | 9,177 → **6,109 MiB** | **≈ 达标**，但 #4 有 FA 前提 |
| #1 + #2 + #3（下 8 层） | 1,988 + 1,016 = **3,004 MiB** | 9,177 → **6,173 MiB** | **达标**，代价是速度 |

> **★ 关键结论：不换模型也能达到 ~6 GB 目标，但要付出「视觉编码走 CPU」或「部分层下 CPU」的延迟代价。** 而换 InternVL3-2B 是"一步到位到 3 GB + 速度更快"，代价是**决策能力风险**。

**另有两条与显存无关但值得记录的省成本手段**（来自既有调研，非本轮新增）：
- **客户端帧差分门控**（≈ Cognition Gate 零成本近似）：省带宽/编码/prefill/上下文**四重收益**，有 StreamMind 文献支撑。
- **`--image-max-tokens` 降每帧 token**：原生支持，直接降 prefill。

---

## 5. 现成量化替代品的可用性（★ 本轮最大更正）

### 5.1 ★ 核心更正：`compressed-tensors` **现在可以被 llama.cpp 加载**

**上一版结论「llama.cpp 无法加载 compressed-tensors」已过期。** 依据 [源码]——直读 llama.cpp `master` 的 `conversion/base.py`：

```python
def dequant_model(self):
    ...
    if (quant_config := self.hparams.get("quantization_config")) and isinstance(quant_config, dict):
        quant_method = quant_config.get("quant_method")
        ...
        elif quant_method == "compressed-tensors":
            quant_format = quant_config["format"]
            groups = quant_config["config_groups"]
            ...
            if quant_format == "float-quantized" or quant_format == "int-quantized" or quant_format == "naive-quantized":
                ...   # dequant_simple：weight * scale（含 FP8）
            elif quant_format == "pack-quantized":
                assert weight_config.get("strategy") == "group"
                assert weight_config.get("type", "int") == "int"
                num_bits = weight_config.get("num_bits")
                group_size = weight_config.get("group_size")
                ...
                # 反量化 .weight_packed / _scale / _shape / _zero_point
            elif nvfp4_compressed_tensors:
                pass  # 交给 _generate_nvfp4_tensors
            else:
                raise NotImplementedError(f"Quant format {quant_format!r} ... is not yet supported")
```

**同时 `convert_hf_to_gguf.py` 已模块化**，且 **`conversion/qwen3vl.py` 注册了 `Qwen3VLForConditionalGeneration` 的 mmproj（`Qwen3VLVisionModel(MmprojModel)`）** [源码]。
⇒ **JoyAI-VL-Interaction（`qwen3_vl` 架构）的 LLM 与 ViT 都有转换路径。**

### 5.2 逐个核实

| 仓库 | 格式 | 关键 config | llama.cpp 可加载？ | 判定 |
|---|---|---|---|---|
| **`jdopensource/JoyAI-VL-Interaction-INT4`**（官方, dl=320） | compressed-tensors | `format: "pack-quantized"`, `num_bits: 4`, `group_size: **32**`, `strategy: "group"`, `type: "int"` | ✅ **格式/参数完全落在支持分支内** | ★ **可试，最优先** |
| **`claris153/...-AWQ-W4A16`**（dl=66） | compressed-tensors（AWQ 算法） | `pack-quantized`, `num_bits: 4`, `group_size: **128**`, `symmetric: false`, `zp_dtype: int8` | ✅ **同样落在支持分支**（AWQ 只是权重生成算法，落盘仍是 compressed-tensors） | ★ **可试** |
| **`openaiarka/...-AWQ`**（dl=20） | compressed-tensors | `pack-quantized`, `num_bits: 4`（其余默认） | ⚠️ 需 `group_size` / `strategy`（源码 assert 要求） | 🟡 **可能因缺字段报错** |
| **`jdopensource/...-FP8`**（dl=57） | compressed-tensors | FP8 → 走 `float-quantized` + `_scale_inv` 分支 | ✅ 支持 | 🟡 但 FP8 **不省显存**（8-bit） |
| **`jdopensource/...-INT8`**（dl=86） | compressed-tensors | 8-bit | ✅ 支持 | ❌ 8-bit 不省 |
| **`jdopensource/...-NVFP4`**（dl=75） | compressed-tensors | `nvfp4-pack-quantized` | ✅ 有专门分支 | 🟡 本机已有，但 NVFP4 需 Blackwell 专用 kernel |
| **`xiaowangzhixiao/...-MLX-4bit`**（dl=134） | **MLX**（非 HF 标准） | `quantization_config: {bits: 4}`，safetensors 内是 `U32` 打包 | ❌ **MLX 格式，llama.cpp 不支持** | ❌ 不可用 |
| **`Nasa1423/...-IQ4_NL-GGUF`**（dl=346） | **已是 GGUF** | — | ✅ 原生 | ✅ 即本机在用 |

### 5.3 ⚠️ 关键限制（不要高估这条更正）

1. **"能转换" ≠ "转换后可用"**。`dequant_model()` 是**反量化到 16-bit 再重新量化**的路径，**不是原生加载 4-bit kernel**。即：
   - 流程是 `INT4 safetensors → 反量化到 BF16 → 转 GGUF → llama-quantize 到你想要的档`。
   - **最终产物 ≈ 自己量化的 Q4，而不是"官方 INT4 的高质量"**。**唯一的真实收益是省下"下载 16-bit 原始权重（~16 GB）+ 自己量化"的步骤。**

2. **转换需要 Python + torch + transformers**，且**反量化到 BF16 需要约 16 GB 内存**（本机 32 GB RAM 可行，但要注意峰值）。

3. **`group_size` 必须匹配**：官方 INT4 是 **32**，claris153 AWQ 是 **128**。源码直接 assert `isinstance(group_size, int)`，缺字段会 TypeError。

4. **`ignore` 列表很长**（所有 `model.visual.*` + `lm_head` 不量化）——这正是**保留视觉质量的设计**，与我们 §2 的"mmproj 量化要谨慎"结论一致。

5. **★ 但真正的省显存路径仍然是"转成 GGUF 后自己量化"**，而**这条路径本来就能走**：因为 `conversion/qwen3vl.py` 已支持该架构，**直接用官方的 16-bit 原始权重（`jdopensource/JoyAI-VL-Interaction-Preview`）转 GGUF + quantize 即可**——不需要这些 INT4 仓库。

**⇒ 最终判定**：这些仓库**有价值（省下载/省量化步骤），但不是"解锁了新的部署方式"**。当前架构既有的 GGUF 路径（`Nasa1423/...-IQ4_NL-GGUF`，即本机在用）**已经是等价或更好的方案**。

---

## 6. 推荐路径（三档）

### 🟢 立即可做（零能力损失，零新依赖）

| # | 动作 | 收益 | 验证方式 |
|---|---|---|---|
| **1** | **mmproj F16 → Q8_0** 重新转换 | **−505 MiB** | 官方命令：`python convert_hf_to_gguf.py --mmproj --outfile mmproj-joyai-Q8_0.gguf --outtype q8_0 <原始权重目录>` [文档]。**用已知图→已知答案 A/B 验证决策质量** |
| **2** | **`--no-mmproj-offload` 实测** | **−1,483 MiB** | 记录 `image slice encoded in N ms` 前后对比；若 prefill 明显变差则回退 |
| **3** | **确认 FA 实际状态**（`-fit off` ≠ 关闭 FA） | 认知纠正 | 检查启动日志有无 `CLIP graph uses unsupported` |
| **4** | **`--image-max-tokens` 降到 512–1024 做 A/B** | 降 prefill + 视觉 token | 对比决策质量与 prefill 耗时 |
| **5** | **客户端帧差分门控** | 四重收益（带宽/编码/prefill/上下文） | 目标：抑制 ≥50% 的 VLM tick |

> **做完 #1 + #2 即得 ~1.99 GB，9,177 → ~7,189 MiB。** 再叠 #4（KV q8_0）或 `-ngl` 可到 ~6.1 GB。

### 🟡 需验证（有条件，且有明确风险）

| # | 动作 | 风险/前提 |
|---|---|---|
| **6** | **`compressed-tensors → GGUF` 转换实验** | 用官方 16-bit 权重而非 INT4 仓库更直接。前提：Python + torch + ~16 GB RAM 峰值。**必须 A/B 决策质量** |
| **7** | **试 InternVL3-2B**（最省：3,031 MiB） | ⚠️ **决策能力高危**（When2Speak 3B 崩）。且 **InternVL3 在 llama.cpp 有已知视觉质量 bug（issue #15528，文本密集图读错）**——**对"屏幕画面"场景特别致命** |
| **8** | **试 Qwen3-VL-4B** | ❌ 同族 ViT（省不到视觉），但 **KV 与 8B 相同**，只能省权重 2.2 GB。**4B 是决策安全档，但仍无四态训练** |
| **9** | **KV f16 → q8_0** | 需 FA 开启；FA 关闭时量化 V **启动抛错** |

### 🔴 不可行

| # | 项 | 原因 |
|---|---|---|
| **10** | **本机训练/微调决策头** | **物理不可能**：全量 116 GB / LoRA 36.7 GB / **QLoRA 16.7 GB > 16 GB** |
| **11** | **换 ≤3B 且指望保住决策** | When2Speak **1B/3B 崩成永远静默** |
| **12** | **MLX 4bit 仓库** | MLX 格式，llama.cpp 不支持 |
| **13** | **LLaVA-OneVision / PaliGemma / Florence-2 / Phi-3.5-vision** | **不在 llama.cpp 支持清单**，无可用 mmproj |
| **14** | **MiniCPM-V 4.6** | 架构特殊（`qwen35` + 混合 linear/full attention），需核验 llama.cpp kernel 覆盖 |
| **15** | **Moondream2** | 仅 F16 权重（2,708 MiB），**无 LLM 量化档**，省不到 |
| **16** | **投机解码 / 降 n_ctx / KV q4_0** | 均已实测否定（0.27×、用户否决、质量崩塌） |

---

## 7. 不确定项（诚实标注）

1. **When2Speak 的迁移性未验证**：它是**文本多轮对话**，本项目是**视频流 + 1fps + 四态决策**。"2B 崩、4B 安全"是**强先验而非定论**。**唯一可靠的判定方式是实测。**
2. **`compressed-tensors` 转换未实测**：源码分支明确支持，但**本次未实际跑通转换**（方法限制不允许下载模型）。`openaiarka` 仓库可能因缺 `group_size`/`strategy` 字段被 assert 拒绝。
3. **`--no-mmproj-offload` 的实际延迟代价未测**：省 1,483 MiB 是实测值，但 CPU 上单帧编码耗时未知。**这是本报告最需要立即补测的一项。**
4. **候选模型的真实显存未实测**：§2 全部为**基于 config.json + 文件字节数的推算**，公式在本机 8B 上验证误差 0.7%，但**候选模型的 compute buffer / CUDA graph 开销可能不同**。
5. **Gemma-4 / MiniCPM-V 4.6 的 KV 为估算**：混合注意力架构的 KV 实际分配依赖 llama.cpp 实现（sliding-window 层是否也分配完整 KV cache 未确认）。
6. **InternVL3 视觉 bug（#15528）状态**：issue 已 closed（label: stale），**但未见明确修复 commit**。"屏幕画面 + 文字"正是该 bug 的高发场景，**未验证是否影响本项目**。
7. **未找到任何"2–4B 级 + 原生流式 + 何时说话训练"的模型**——本节结论与上一版一致，本次检索亦未发现。**若存在，将是唯一能同时满足省显存与保决策的选项。**
8. **下载量 ≠ 质量**：表中 dl 数仅作"成熟度/可复现性"信号，**不代表决策能力**。

---

## 8. 来源清单

### 8.1 论文 / 证据

- **When2Speak: A Dataset for Temporal Participation and Turn-Taking**（arXiv:2605.05626, 2026-05）— **1B/3B 崩成永远静默**；4B(0.737)≈8B(0.739)≈70B(0.747)；zero-shot Qwen3-4B FIR=0.897。 <https://arxiv.org/abs/2605.05626> ｜ HTML <https://arxiv.org/html/2605.05626v1>
- **Proact-VL: A Proactive VideoLLM for Real-Time AI Companions**（arXiv:2603.03447, ICML 2026）— 本项目架构最接近前作；τ 标定 + `L_reg`（去掉掉 49.05 F1）。 <https://arxiv.org/abs/2603.03447>
- **StreamMind: Unlocking Full Frame Rate Streaming Video Dialogue through Event-Gated Cognition**（ICCV 2025, arXiv:2503.06220）— Cognition Gate，触发器可外置。
- **JoyAI-VL-Interaction: Real-Time Vision-Language Interaction Intelligence**（arXiv:2606.14777）— 本项目上游论文。
- **Speak or Stay Silent**（arXiv:2603.11409）｜ **ProVoice-Bench**（arXiv:2604.15037）｜ **级联/routing 综述**（arXiv:2603.04445）— 均不利于小模型决策/外置判定器。

### 8.2 llama.cpp（源码与文档）

- **`docs/multimodal.md`** — 官方预量化清单（判定"llama.cpp 是否支持"的权威依据）： <https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md>
- **`conversion/base.py`** — **`dequant_model()` 对 `compressed-tensors` 的支持**（§5 更正的依据）： <https://raw.githubusercontent.com/ggml-org/llama.cpp/master/conversion/base.py>
- **`conversion/qwen3vl.py`** — `Qwen3VLForConditionalGeneration` 的 mmproj 注册： <https://raw.githubusercontent.com/ggml-org/llama.cpp/master/conversion/qwen3vl.py>
- **`tools/quantize/README.md`** — **mmproj 量化命令**（§6 动作 #1 的依据）： <https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md>
- **issue #15528** — InternVL3 视觉质量 bug（文本密集图读错）： <https://github.com/ggml-org/llama.cpp/issues/15528>
- **issue #18881** — 量化 mmproj 支持请求；**issue #9119** — Phi-3.5-vision 未支持。

### 8.3 模型（HF API 实取字节数）

- `ggml-org/InternVL3-2B-Instruct-GGUF`（mmproj F16 628,237,600 B / Q8_0 337,012,000 B；LLM Q4_K_M 1,116,758,816 B）
- `ggml-org/InternVL3-8B-Instruct-GGUF`（mmproj Q8_0 357,082,400 B）｜ `ggml-org/InternVL2_5-1B-GGUF`（mmproj Q8_0 332,568,160 B）
- `ggml-org/SmolVLM2-2.2B-Instruct-GGUF`（mmproj Q8_0 592,523,200 B）
- `ggml-org/Qwen2.5-VL-3B-Instruct-GGUF`（**mmproj F16 1,338,428,128 B**）｜ `ggml-org/Qwen2.5-VL-7B-Instruct-GGUF`
- `Qwen/Qwen3-VL-4B-Instruct-GGUF`（mmproj F16 836,180,256 B）｜ `unsloth/Qwen3-VL-2B-Instruct-GGUF`（mmproj F16 819,395,232 B）
- `ggml-org/gemma-4-E2B-it-GGUF` / `gemma-4-E4B-it-GGUF` ｜ `ggml-org/Qwen2.5-Omni-3B-GGUF`（mmproj F16 **2,623,983,328 B**）
- `openbmb/MiniCPM-V-4.6-gguf`（mmproj F16 1,108,746,944 B）｜ `ggml-org/moondream2-20250414-GGUF`（仅 F16）
- 量化替代品：`jdopensource/JoyAI-VL-Interaction-INT4`（`group_size:32`）｜ `claris153/...-AWQ-W4A16`（`group_size:128`）｜ `openaiarka/...-AWQ` ｜ `xiaowangzhixiao/...-MLX-4bit`（**MLX，不可用**）｜ `Nasa1423/...-IQ4_NL-GGUF`（本机在用）

### 8.4 本机实测 / 本仓库

- `D:\AI\models\main\` 文件字节数（IQ4_NL 4,795,019,520 B；mmproj F16 1,159,029,728 B；NVFP4 config.json）
- `doc/research/vram-game-quality-mapping-2026-09-20.md` — 显存预算 → 画质映射（6 GB 目标口径）
- `doc/research/vlm-lightweight-2026-09.md` — 上一版模型侧调研（**本报告修正其 §1.4/§3.4 的"跨族也不省 ViT"与 §5 的"compressed-tensors 不可加载"两处结论**）
- `doc/research/lightweight-replacement.md` — 摘要模型的轻量化替换（与主 VLM 决策角色不同）
