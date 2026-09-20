# 降低本地 LLM/VLM 显存占用 — 2026 前沿技术核实报告

> **端点身份**：研究端点（AFK，**只读**）。本报告不改任何源码。
> **日期**：2026（本轮）
> **目标**：只找「能省显存」的技术，**不研究准确率/幻觉/视觉精度**（用户明确要求）。
> **方法**：AnySearch 检索 + `gh_*` 插件 + **本机实测**（`llama-quantize --dry-run` 只读、二进制/GGUF 解析）。

---

## 0. 一句话结论

**还能再省约 1,730 MiB（保守）到 2,970 MiB（激进），JoyAI 合计从 8,082 MiB 降到 5,400–6,350 MiB。**

用户在 §1 的核心判断**是错的**：IQ4_NL 的 4,535 MiB **不是终点**。
实测本机模型，权重侧最高可再省 **约 2,200 MiB**（IQ2_XXS）或 **约 1,250 MiB**（IQ3_XXS）。

**最值得做的 3 条**：

| # | 措施 | 预期省 | 代价 |
|---|---|---|---|
| **1** | **权重 IQ4_NL → IQ3_XXS**（从 F16 源重转 + imatrix） | **−1,250 MiB** | 需下 16.34 GiB F16 源 + 重转；文本质量下降（对本项目「1–3 token 四态决策」影响小） |
| **2** | **mmproj F16 → Q8_0**（`convert_hf_to_gguf.py --mmproj --outtype q8_0`） | **−484 MiB** | ⚠️ **`llama-quantize` 做不到**（见 §2），必须走 convert 路径 |
| **3** | **KV 只压 V 到 q4_0（K 保持 q8_0）** | **−288 MiB** | 需用 `GGML_CUDA_FA_ALL_QUANTS=ON` 重编 llama.cpp；本机现有 build 不支持 |

---

## 1. 权重侧：用户认为「4,535 MiB 已无法再压」——**这个判断是错的**

### 1.1 实测证据（本机 `llama-quantize --dry-run`，只读、不需要 GPU）

在本机**真实模型** `joyai-vl-interaction-preview-iq4_nl-imat.gguf` 上跑完整量化阶梯：

| 类型 | 量化后 MiB | BPW | **相对现状节省** |
|---|---:|---:|---:|
| （当前文件，dry-run 读数） | 4,565.88 | 4.68 | — |
| Q4_K_M | 4,789.19 | 4.90 | −223（更大） |
| IQ4_NL | 4,589.88 | 4.70 | −24（更大） |
| Q4_K_S | 4,573.88 | 4.68 | −8（更大） |
| **IQ4_XS** | 4,374.83 | 4.48 | **+191** |
| Q3_K_M | 3,927.43 | 4.02 | +639 |
| IQ3_M | 3,710.43 | 3.80 | +856 |
| IQ3_S | 3,608.43 | 3.70 | +958 |
| Q3_K_S | 3,589.30 | 3.68 | +977 |
| IQ3_XS | 3,453.18 | 3.54 | +1,113 |
| **IQ3_XXS** | **3,314.35** | **3.39** | **+1,252** ⭐ |
| Q2_K | 3,124.02 | 3.20 | +1,442 |
| Q2_K_S | 2,935.02 | 3.01 | +1,631 |
| IQ2_M | 2,904.85 | 2.98 | +1,661 |
| IQ2_S | 2,726.35 | 2.79 | +1,840 |
| IQ2_XS | 2,565.57 | 2.63 | +2,000 |
| **IQ2_XXS** | **2,369.07** | **2.43** | **+2,197** ⭐⭐ |

> 命令：`llama-quantize.exe --dry-run --allow-requantize <model.gguf> <TYPE>`（秒级，只算尺寸不写文件、不碰 GPU）。

**结论**：IQ4_NL（4.70 bpw）根本不是 4-bit 里的下限，甚至**比 IQ4_XS（4.48）还大**。
再压一档到 **IQ3_XXS 直接省 1,252 MiB**，压到 **IQ2_XXS 省 2,197 MiB**。

### 1.2 质量代价（★ 关键：这是「实测帖」而不是论文）

- llama.cpp 官方 `tools/quantize/README.md` 的 Llama-3.1-8B 基准表：IQ3_XXS **3.04 GiB**、IQ2_XXS **2.23 GiB**（对 IQ4_NL 4.38 GiB）——与本机实测比例一致，说明本机模型不特殊。
- **r/LocalLLaMA 实测帖（2026-02-22，有人真跑过、给数字）**：有人用 **UD-IQ2_XXS on Qwen3-30B-A3B**，在高中/大学级别化学、数学、物理、相对论问题上「**找不到 IQ2 和 Q4 的有意义差别**」，只有极冷门学术题掉分（81/100 vs Q4 的 92/100）。
- **同一帖的反面证据**（要诚实列出）：评论区有实测者称 IQ2 用于**代码/长上下文**时「完全脑叶切除（lobotomized）」；另一位给出机制解释——**低 bpw 的典型故障模式是「相似 token / 相似概念互相混淆」和错别字**，且**小模型比大模型更容易被量化搞糊涂**。
- 另一条经验法则（评论区）：**低量化要跟「换更大模型」比**，而不是孤立看掉分。

### 1.3 ★ 本项目特殊性判断（用户问的「能否更激进」）

**判断：可以，而且理由比通用聊天场景更充分。三条独立支撑：**

1. **本项目不是「自由文本生成」，是 1–3 token 的四态决策**。低 bpw 的主要伤害模式（长文本里相似概念漂移、代码标识符混淆、累积性错别字）在 1–3 token 输出上**几乎没有累积面**——没有长输出链条可以漂移。
2. **用户已明确设定风险偏好**：「回答错了无所谓，模型有纠错能力，因为有文本知识库；但跑不起来就是另一回事。」——这正是 IQ2/IQ3 的适用前提：**有外部纠错回路**。
3. **反方证据指向的是「代码/长上下文」，不是本项目负载**。上帖「lobotomized」的实测场景是 coding，其失效机制（长代码库里相似命名函数混淆）在本项目不存在。

**建议分级**：
- **先上 IQ3_XXS（−1,252 MiB）**——风险收益比最好，3.39 bpw 仍属「主流激进但不极端」区间，且本机实测尺寸已确认。
- **IQ2_XXS（−2,197 MiB）作为第二阶段**——省得最多，但必须先跑本项目的四态决策回归（不是通用 benchmark）。

### 1.4 ⚠️ 重转必须从 F16 源，不能从 IQ4_NL 直接 requantize

- `llama-quantize --allow-requantize` 官方警告原文：「**can severely reduce quality compared to quantizing from 16bit or 32bit**」。从 IQ4_NL 再压到 IQ3_XXS 是**二次量化**，误差叠加。
- **上游 F16 源存在且已核实**：`jdopensource/JoyAI-VL-Interaction-Preview`，4 个分片共 **16.34 GiB**（3,939 + 4,681 + 4,680 + 3,422 MiB），文件清单含 `model-0000{1..4}-of-00004.safetensors`。
- **imatrix 也已存在**：`Nasa1423/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF` 仓库里有 **`imatrix.dat`（5 MiB）**——即当前 IQ4_NL 就是用它做的。i-quant 家族（IQ2/IQ3）**必须**有 imatrix（dry-run 明确告警「actually completing this quantization will require an imatrix!」），这个 imatrix 可直接复用。
- 本机 HF cache 只有 `refs/main`（无权重文件）——**F16 源需要重新下载 16.34 GiB**。这是本方案的主要成本。

### 1.5 2026 新量化方法 / 更激进格式：核实结论

| 技术 | llama.cpp 支持？ | 对本项目有用？ | 证据 |
|---|---|---|---|
| **Q1_0（1.125 bpw）/ Q2_0（2.25 bpw）** | ✅ 本机 b10155 的 `llama-quantize --help` 已列出 | ⚠️ 未验证 CUDA 全算子覆盖，属实验性 | 本机实测列表 |
| **TQ1_0 / TQ2_0（三值化 1.69/2.06 bpw）** | ✅ 列表中有 | ❌ 三值化会摧毁文本能力；2026 新增的 TQ2_0 Metal 支持（PR #26980）与本机 CUDA 无关 | `gh_search` |
| **NVFP4** | ✅ 转换已合并（PR #21095 **merged 2026-05-25**；CUDA sm_120 POC PR #20247） | ❌ **本机已实测，反而更差**——见下 | 本机 `NVFP4-启动与运维手册.md` |
| **compressed-tensors / pack-quantized**（就是上游 `JoyAI-VL-Interaction-INT4`） | ✅ **PR #17069 MERGED 2025-11-09**（`convert : handle compressed-tensors quant method`，支持 pack-quantized 对称/非对称 + int/float/naive-quantized） | ❌ 对省显存无用 | `gh pr view 17069` |
| **AQLM / QuIP# / GPTQ 新变体 / 1.58-bit BitNet** | ❌ 无 GGUF 推理支持（BitNet 三值化 PR #8151 已 closed） | ❌ 不可用 | `gh_search` |
| **稀疏 KV / 分页 KV** | ❌ **未合并**——PR #18747「KV cache size limiting and block tracking」仍 **OPEN** | ❌ 当前 b10155 无此能力 | `gh pr view 18747` |

**⚠️ 重要澄清（用户要求核实的最新状态）**：`compressed-tensors` 的 `pack-quantized` 反量化**确实已经在 llama.cpp master 落地**（PR #17069，2025-11-09 合并）。
但——**这条路对省显存没有意义**：上游 `JoyAI-VL-Interaction-INT4` 的 `model.safetensors` 是 **7,200 MiB**，NVFP4 版是 **7,201 MiB**。转成 GGUF 后仍是 4-bit+ 量级（≈7 GiB 权重），**比现有 IQ4_NL 的 4,535 MiB 大一倍**。它是「换更高质量」的路，不是「省显存」的路。

**❌ 本地已实测否定：NVFP4 走 vLLM（不要重复建议）**
本机 `D:\AI\models\main\JoyAI-VL-Interaction-NVFP4\NVFP4-启动与运维手册.md` 记录了 2026-08-04 的实测：

| 项 | MiB |
|---|---:|
| NVFP4 权重（4-bit + FP8 scales） | **7,201** |
| CUDA 图 + 激活 + 框架开销 | 2,975 |
| 小计（不含 KV 池） | 10,176 |
| 预分配 KV 池（25,248 tokens） | 3,551 |
| **nvidia-smi 合计** | **13,727** |

对比现有 llama.cpp 方案 JoyAI 合计 **8,082 MiB** —— **NVFP4/vLLM 多占 5,645 MiB，是明确的倒退**。手册自己也写明「NVFP4 是并行可选后端，不是替代」。**这条路已经被人替我们试过了，结论是否定的。**

---

## 2. 视觉侧（mmproj）

### 2.1 ★ 实测：`llama-quantize` **无法**量化 mmproj

本机直接实测（只读 dry-run）：

```
llama_model_quantize: failed to quantize: unsupported model architecture: 'clip'
```

**这是本报告最重要的操作性发现之一**：mmproj 的 `general.architecture = clip`，`llama-quantize` 不支持该架构。
所以**必须走转换路径**，不能在现有 GGUF 上做二次量化：

```bash
python convert_hf_to_gguf.py --mmproj --outtype q8_0 --outfile mmproj-...-q8_0.gguf <F16 HF 源目录>
```

### 2.2 mmproj 的真实结构与可省空间（本机解析 GGUF 实测）

| 项 | 实测值 |
|---|---|
| 参数量 | **576M**（`general.size_label = 576M`） |
| 张量数 | 352 |
| 权重精度分布 | **F16: 573,272,064 元素**（118 张量）+ **F32: 3,116,272 元素**（234 张量，bias/norm） |
| 文件大小 | **1,105 MiB**（F16 权重 1,093 + F32 11.9） |
| 视觉塔结构 | 27 层 / hidden 1152 / FFN 4304 / 16 heads / patch 16 / image_size 768 / `spatial_merge_size=2` |
| projector 类型 | `qwen3vl_merger`，`projection_dim=4096` |
| deepstack | `is_deepstack_layers` 在 layer 8 与 16 为 true；含 `v.deepstack.16.fc1.weight [4608,4608]` |
| **VRAM 占用（用户实测）** | **1,483 MiB** = 1,105 权重 + **378 MiB 缓冲区** |

**Q8_0 后**：573.27M × 8.5/8 = 609 MiB + F32 11.9 = **621 MiB** → **节省 484 MiB**。
（注意：F32 的 234 个 bias/norm 张量在 q8_0 转换下保持不变，所以省的是 1,093 → 609 这部分。）

### 2.3 mmproj 还有更激进的选项吗？——**基本没有**

`convert_hf_to_gguf.py --outtype` 的**全部允许值**（本机实测）：
`f32, f16, bf16, q8_0, tq1_0, tq2_0, auto`

- **q8_0 是唯一合理的压缩档位**。
- `tq1_0` / `tq2_0` 是**三值化**——用在视觉编码器上等于摧毁视觉，不可行。
- **没有 q4_0/q6_K 之类的中间档**。想要 q4_0 mmproj 只能自己改 convert 脚本（超范围，不建议）。
- llama.cpp 官方 `tools/quantize/README.md` 明确说明为何 mmproj 通常保持高档位：「multimodal components ... their quality has a direct impact on the quality of LLM generations ... The impact on speed and memory from using a smaller quant is negligible, but overall quality could be impacted.」

### 2.4 部分 offload / 替代方案

| 手段 | 结论 |
|---|---|
| `--no-mmproj-offload`（已有实测） | ❌ 已否定：省 1,570 MiB 但 prompt 266ms → 5,887ms（22×） |
| **部分层 offload（`-ot` 只把部分视觉张量放 CPU）** | ⚠️ 本机有 `-ot, --override-tensor <name>=<buftype>`，理论上可逐个张量指定 buffer。**但**：视觉塔是**逐层顺序前向**，任何一层在 CPU 都会让每帧都付一次 PCIe 往返——因为 `--no-mmproj-offload` 全量 offload 已经是 22× 惩罚，**部分 offload 大概率同样致命**（成本按层数线性分摊）。**不建议**，除非只挑 1–2 个非瓶颈张量试。 |
| `--mtmd-batch-max-tokens`（默认 1024） | ⚠️ 减小可**压缩视觉编码的临时缓冲区**（那 378 MiB 里的一部分），但不减权重。属「零成本可试」。 |
| **视觉 token 压缩（间接省）** | ✅ 见 §3——`--image-min-tokens` / `--image-max-tokens` |

### 2.5 一个本地「免下载」可能性（需验证，未证实）

本机 `D:\AI\models\main\JoyAI-VL-Interaction-NVFP4\recipe.yaml` 的量化配方是：
```yaml
ignore: ['re:.*lm_head', 're:.*visual.*', 're:model.visual.*', 're:.*mlp.gate$']
```
即 **NVFP4 那份 safetensors 里的视觉塔是 BF16 原样的**。理论上它可能作为 `convert_hf_to_gguf.py --mmproj` 的输入，**避免为此下载 16.34 GiB F16 源**。

⚠️ **但不确定**：(1) 本机 vendored `convert_hf_to_gguf.py` 对 `compressed-tensors` 的支持度实测为 **0 处匹配**（grep `compressed.tensors|pack.quantized|nvfp4` 无命中）；(2) 转换器可能因文件里存在量化张量而整体失败，即使我们只想要 `visual.*`。
**判定**：值得花 10 分钟试一次（只读、CPU），成功就白省 16 GiB 下载；失败就老老实实下 F16 源。

---

## 3. 上下文侧（保持功能前提下降低占用）

### 3.1 先把 KV 数学钉死（本机实测校准）

用户实测：**f16 = 2,321 MiB，q8_0 = 1,224 MiB**。
几何（36 层 / 8 KV heads / head_dim 128 / n_ctx 16384）理论值：

| 精度 | K 单独 | **K+V 合计** |
|---|---:|---:|
| f16 / bf16 | 1,152 MiB | **2,304 MiB** ✅ 与实测 2,321 吻合 |
| **q8_0** | 612 MiB | **1,224 MiB** ✅ 与实测 1,224 **完全一致** |
| q5_0 | 396 MiB | 792 MiB |
| q4_0 | 324 MiB | 648 MiB |

**理论值与实测值吻合到 1% 以内** → 下表的预测可信。

### 3.2 ★ 这颠覆了用户对 KV q4_0 的否定结论

用户实测：「KV q4_0：**仅多省 112 MiB** 而相似度 81.6%→8.3%」。

**⚠️ 这里有一个 5 倍的数学矛盾，值得用一次便宜的重测解决：**
按几何，`-ctk q4_0 -ctv q4_0` 应为 **648 MiB**，相对 q8_0 的 1,224 应省 **576 MiB**——不是 112 MiB。
112 MiB 这个数字**暗示那次 q4_0 测试实际上只有一半（或更少）的 cache 真的被降精度**（例如 K/V 只降了一个、或 fallback 到了非 FA 路径）。

**而 llama.cpp 官方 discussion #23470 有实测数据直接支持「非对称 KV」**（有人用 `llama-perplexity` + KLD 跑过 qwen2.5-7b）：

| flags | KLD | Same top-p | 备注 |
|---|---:|---:|---|
| `-ctk q4_0 -ctv q4_0` | 5.5089 | 11.6% | 完全崩（**对应本项目用户的失败结果**） |
| `-ctk q4_0 -ctv f16` | 5.4874 | 11.8% | 崩——**问题全在 K** |
| `-ctk f16 -ctv q4_0` | 0.0040 | **96.9%** | **V 降到 4-bit 几乎零代价** |
| `-ctk q8_0 -ctv q4_0` | 0.0048 | **96.7%** | 损失极低 |
| `-ctk q8_0 -ctv q8_0` | 0.0018 | 98.0% | 接近完美 |

**结论**：用户当年把 **K 也降到 q4_0** 才导致 81.6%→8.3% 的灾难。**K 必须保持高精度，V 可以砍到 q4_0**。
`-ctk q8_0 -ctv q4_0` → KV = 612 + 324 = **936 MiB**，比现状 q8_0 **再省 288 MiB**，且官方实测精度损失极小（96.7% same top-p）。

**⚠️ 但有前置条件（本机实测）**：非对称 KV 需要 flash-attention 支持该组合，而 CUDA 后端用 `GGML_CUDA_FA_ALL_QUANTS` 编译开关控制哪些组合可用。
本机 `ggml-cuda.dll` 扫描出的全部 `GGML_CUDA_*` 变量为：

```
GGML_CUDA_ALLREDUCE, GGML_CUDA_AR_BF16_THRESHOLD, GGML_CUDA_AR_COPY_CHUNK_BYTES,
GGML_CUDA_AR_COPY_MAX_CHUNKS, GGML_CUDA_AR_COPY_THRESHOLD, GGML_CUDA_CUBLAS_COMPUTE_TYPE,
GGML_CUDA_DEVICES, GGML_CUDA_DISABLE_FUSION, GGML_CUDA_DISABLE_GRAPHS,
GGML_CUDA_ENABLE_UNIFIED_MEMORY, GGML_CUDA_FORCE_MMQ, GGML_CUDA_GRAPH_OPT,
GGML_CUDA_MAX_DEVICES, GGML_CUDA_NCCL, GGML_CUDA_NO_PINNED, GGML_CUDA_P2P,
GGML_CUDA_PDL, GGML_CUDA_REGISTER_HOST
```

**没有 `GGML_CUDA_FA_ALL_QUANTS`** —— 它是**编译期**选项（不是运行时 env），所以现有 b10155 二进制**不支持**非对称 KV 的 FA 路径（缺省 CUDA 只含 f16/q4_0/q8_0 的对称组合）。
**要做 §3.2 必须自己用 `-DGGML_CUDA_FA_ALL_QUANTS=ON` 重编 llama.cpp。**

### 3.3 上下文缩短（用户说「不要简单建议降 n_ctx」——所以给的是保功能路径）

用户当前上下文 ≈ 10,000 token：system 845 + 记忆召回 2,800 + Wiki 2,800 + history 960 + 图像 2,700。n_ctx = 16,384。

| 手段 | 省显存 | 是否保功能 |
|---|---|---|
| **削减图像 token**（`--image-max-tokens`） | 每 1,000 token ≈ 37 MiB KV(q8_0) | ✅ 保功能——6 帧 × 448 = 2,688 token 若有冗余可砍 |
| **记忆召回去重 / Wiki 按需注入** | 每 1,000 token ≈ 37 MiB | ✅ 保功能（属功能优化，不是砍功能） |
| 若把 n_ctx 从 16,384 降到 12,288 | 306 MiB | ⚠️ 需先确认 10,000 token 峰值留足余量 |

> 换算：KV q8_0 每 token = 1,224 MiB / 16,384 = **0.0747 MiB/token ≈ 37 MiB / 1,000 token**。
> 所以上下文侧是**线性、按需**的省法：真把上下文压到 10,000 token 以内，KV 可省 ~470 MiB。但这是「功能优化换显存」，优先级低于权重侧（权重侧一刀 1,252 MiB 立竿见影）。

### 3.4 稀疏 / 分页 KV（用户问的新特性）

**❌ 当前不可用。** `gh pr view 18747` 核实：PR #18747「ggml, llama : add KV cache size limiting and block tracking infrastructure」**仍是 OPEN，未合并**。
llama.cpp 截至 b10155 **没有** PagedAttention / 稀疏 KV 的用户可用特性。不要指望这条路。
（另有若干 TurboQuant / PolarQuant KV 压缩 PR #21062/#21241/#21307，**全部因 AI policy violation 被关闭**，不可用。）

---

## 4. 其他（Windows / CUDA / 多进程）

### 4.1 固定开销 840 MiB（CUDA context / buffer）的压缩空间

本机核实的可用 env（`ggml-cuda.dll` 扫描）：

| env / flag | 作用 | 预期 |
|---|---|---|
| `GGML_CUDA_DISABLE_GRAPHS=1` | 关闭 CUDA graph 捕获 | 省 CUDA graph 预留显存。**参考**：NVFP4 手册实测 vLLM 的 CUDA 图占 **~2,975 MiB** 残差，`--enforce-eager` 可省 ~2GB。llama.cpp 的图占用小得多，**预期只能省几十~一两百 MiB**，属「可试」 |
| **减小 `-ub`（ubatch，物理批）** | 计算缓冲区随 ubatch 线性缩放 | 用户跑 `--parallel 1`，`-ub` 默认 512。**这是 840 MiB 里最可能有弹性的一项**，可试 `-ub 256` |
| `GGML_CUDA_FORCE_MMQ=1` | 强制 MMQ kernel | 不省显存，仅影响速度 |
| `GGML_CUDA_NO_PINNED=1` | 不用 pinned host memory | 省**主机**内存，**不省 VRAM** |
| `--load-mode` / `--no-mmap` / `--mlock` | mmap/锁定加载 | **不改变 VRAM 占用**（模型权重本来就要全部驻留 GPU）。只影响主机 RAM 与页换出行为。**对省显存无帮助** |
| `-ot` 把非热张量丢 CPU | 逐张量 buffer 指定 | 理论上可行但会拖慢，且 GPU 侧仍有上下文开销 |

### 4.2 多进程共享（最重要的「其他」项）

本项目还有 webui / webinfer / memory-store 等服务。**若其中任何一个持有 CUDA 上下文（例如任何 PyTorch / onnxruntime-gpu 依赖），每个进程会独立吃掉约 300–500 MiB 的 CUDA context**——这是**白送的 300–500 MiB**，而且和 llama-server 无关。

**建议动作**：跑一次 `nvidia-smi`，看除 llama-server 外还有哪些进程占显存。用户「桌面基线 1,080 MiB」这个数偏高（典型 DWM + 浏览器 + 桌面约 400–700 MiB），**这 1,080 MiB 里可能就藏着一个多余的 CUDA 上下文**。

> ⚠️ 注意用户给的实测里 `doc/research/data/vram_diagnosis.json` 显示桌面基线中位数 **457 MiB**（4 次采样 458/458/456/446），而任务书里写的是 1,080 MiB。**这两个数字矛盾**，说明「桌面基线」在不同时刻测量差异很大（可能含浏览器/Electron/其他服务）。值得重测一次以确定真实可回收量。

### 4.3 显存碎片

Windows WDDM 下 CUDA 显存碎片确实会浪费空间。可用手段有限：启动顺序（先起 llama-server 再起其他 GPU 服务）、避免反复重启。llama.cpp 的 `--defrag-thold` 是针对 **KV cache** 的（且本机 help 标注 **DEPRECATED**），不是通用碎片整理，对本项目无用（`--parallel 1` 下 KV 几乎不碎片）。

---

## 5. 三档清单

### 🟢 可立即做

| 措施 | 预期省 MiB | 代价 / 风险 | 依据 |
|---|---:|---|---|
| **KV 现状保持 q8_0**（已落地，确认不回退） | —（已省 1,320） | 无 | 用户实测 |
| **重测 KV q4_0 的 112 MiB 异常** | 0（诊断） | 一次启动，10 分钟 | 数学 576 vs 实测 112 矛盾 |
| `GGML_CUDA_DISABLE_GRAPHS=1` 试跑 | 数十~150 | 可能轻微变慢 | 本机 env 存在 |
| 减小 `-ub`（512 → 256）试跑 | 未知（在 840 MiB 内） | 可能变慢 | 本机 flag 存在 |
| **查 `nvidia-smi`：是否有其他进程白占 CUDA context** | **0 ~ 500** | 纯诊断，零成本 | §4.2 |
| **削减图像 token（`--image-max-tokens`）** | ~37 / 1,000 token | 需确认视觉不退化 | §3.3 |
| 记忆召回去重 / Wiki 按需注入 | ~37 / 1,000 token | 功能优化 | §3.3 |

### 🟡 需验证（本报告的核心建议都在这一档）

| 措施 | 预期省 MiB | 代价 / 前置条件 | 依据 |
|---|---:|---|---|
| **权重 → IQ3_XXS** ⭐ | **−1,252** | 下 16.34 GiB F16 源（+5 MiB imatrix.dat）+ 重转 + 跑本项目四态决策回归 | **本机 dry-run 实测**（3,314.35 MiB） |
| **权重 → IQ2_XXS** | **−2,197** | 同上，但质量风险更高 | **本机 dry-run 实测**（2,369.07 MiB） |
| **mmproj → Q8_0** ⭐ | **−484** | `convert_hf_to_gguf.py --mmproj --outtype q8_0`（**llama-quantize 不行**）；需 F16 源 or 试 NVFP4 里的 BF16 视觉塔 | **本机 GGUF 解析实测**（1,105 → 621 MiB） |
| **KV `-ctk q8_0 -ctv q4_0`（非对称）** | **−288** | **需用 `-DGGML_CUDA_FA_ALL_QUANTS=ON` 重编 llama.cpp** | llama.cpp discussion #23470 **官方有人实测 KLD**（96.7% same top-p） |
| 用 NVFP4 目录的 BF16 视觉塔免下载做 mmproj | 省下载（0 MiB 显存） | 本机 converter 无 compressed-tensors 支持，成功率未知 | §2.5 |
| `--mtmd-batch-max-tokens` 调小 | 未知（378 MiB 缓冲内一部分） | 零风险可试 | 本机 flag 存在 |

### 🔴 不可行（不要重复建议）

| 措施 | 为何不行 | 依据 |
|---|---|---|
| **NVFP4 / vLLM** | 本机实测 **13,727 MiB**，比现状 8,082 多 5,645 | 本机 `NVFP4-启动与运维手册.md` |
| **compressed-tensors / INT4 上游权重** | 转 GGUF 后仍 ≈7 GiB，**比 IQ4_NL 大一倍**；PR #17069 已合并但方向是「提质」不是「省显存」 | HF API + `gh pr view 17069` |
| **`--no-mmproj-offload`** | 已实测否定：prompt 266ms → 5,887ms（22×） | 用户实测 |
| **KV 整体 q4_0（K 也降）** | 已实测否定（相似度 8.3%）；官方 KLD 也确认 q4_0-K 是灾难 | 用户实测 + discussion #23470 |
| **投机解码** | 输出仅 1–3 token，0.27× 反而慢 | 用户实测 |
| **AQLM / QuIP# / GPTQ / 1.58-bit** | llama.cpp 无 GGUF 推理支持 | `gh_search` |
| **TQ1_0 / TQ2_0 用于 mmproj 或 LLM** | 三值化摧毁文本/视觉能力 | 官方 outtype 列表 + 判断 |
| **稀疏 KV / PagedAttention** | PR #18747 仍 OPEN，未合并 | `gh pr view 18747` |
| **TurboQuant / PolarQuant KV 压缩** | PR #21062/#21241/#21307 **全部被关闭**（AI policy violation） | `gh_search` |
| **`--no-mmap` / `--mlock` 省 VRAM** | 只影响主机 RAM/换页，不改变 VRAM | 本机 help 文本 |

---

## 6. 来源清单（明确标注「有人实测」vs「推测」）

### ✅ 有人实测过（有数字）

| # | 来源 | 内容 | 可信度 |
|---|---|---|---|
| 1 | **本机 `llama-quantize --dry-run`**（b10155） | 完整量化阶梯 17 档的实际尺寸（§1.1） | ★★★★★ 一手实测，本机、本项目模型 |
| 2 | **本机 GGUF 二进制解析** | mmproj 576M / F16 573.27M 元素 / F32 3.12M 元素 / 1,105 MiB（§2.2） | ★★★★★ 一手实测 |
| 3 | **本机 `llama-quantize` 试跑** | `unsupported model architecture: 'clip'`（§2.1） | ★★★★★ 一手实测 |
| 4 | **本机 `ggml-cuda.dll` / `llama-server --help` 扫描** | 无 `GGML_CUDA_FA_ALL_QUANTS`；ctk/ctv 允许值；`-ot`/`-ub`/`--load-mode` 存在（§2.3/§3.2/§4.1） | ★★★★★ 一手实测 |
| 5 | **本机 `NVFP4-启动与运维手册.md`**（2026-08-04 workbuddy 实测） | NVFP4/vLLM 显存 13,727 MiB 分解（§1.5） | ★★★★★ 本机前人实测 |
| 6 | [llama.cpp discussion #23470](https://github.com/ggml-org/llama.cpp/discussions/23470) | 非对称 KV 的 KLD/same-top-p 对照表，`llama-perplexity` 实跑（§3.2） | ★★★★★ 官方 discussion，附完整命令与数字 |
| 7 | [r/LocalLLaMA 1rbio4h](https://www.reddit.com/r/LocalLLaMA/comments/1rbio4h/has_anyone_else_tried_iq2_quantization_im/)（2026-02） | IQ2_XXS 在 Qwen3-30B-A3B 上实测，含正反双方经验（§1.2） | ★★★★ 真人实测，但为个例 |
| 8 | [r/LocalLLaMA 1sd2zer](https://www.reddit.com/r/LocalLLaMA/comments/1sd2zer/advice_ask_be_carefull_with_qwen_35_vision/)（2026-04） | Qwen 视觉服务的 `--image-min-tokens`、`-ub 16384` 浪费显存、图像 token 数≈缩放尺寸（§2.4/§3.3） | ★★★★ 真人实测 |
| 9 | [llama.cpp discussion #26736](https://github.com/ggml-org/llama.cpp/discussions/26736) | `--no-kv-offload` 后失败的真因是 **compute buffer** 而非 KV（§4.1） | ★★★★ 附日志 |
| 10 | [r/LocalLLaMA 1u8i79d](https://www.reddit.com/r/LocalLLaMA/comments/1u8i79d/llamacpp_how_to_free_up_even_more_space_on_your/) | `--no-mmap --mlock` 实践（**省的是主机 RAM，不是 VRAM**） | ★★★ 与省显存无关，仅用于证否 |
| 11 | **HF API 查询** | 上游仓库文件清单与精确字节数：F16 源 16.34 GiB/4 分片、`imatrix.dat` 5 MiB、INT4 7,200 MiB（§1.4/§1.5） | ★★★★★ 官方 API |
| 12 | **llama.cpp 官方 `tools/quantize/README.md`** | 官方量化对照表、mmproj 转换命令、`--allow-requantize` 警告、mmproj 保高精度理由（§1.2/§1.4/§2.3） | ★★★★★ 官方文档 |
| 13 | **`gh pr view` 核实** | #17069 MERGED 2025-11-09、#21095 MERGED 2026-05-25、#18747 OPEN（§1.5/§3.4） | ★★★★★ 官方 API |

### ⚠️ 推测 / 未验证（**不要当成结论**）

| # | 项 | 不确定性 |
|---|---|---|
| 14 | §2.5「用 NVFP4 目录的 BF16 视觉塔做 mmproj」 | recipe.yaml 语义支持，但本机 converter 无 compressed-tensors 支持（grep 0 命中）→ **成功率未知** |
| 15 | §4.1 `GGML_CUDA_DISABLE_GRAPHS` / `-ub` 的真实省量 | 本机 env/flag 存在，但**没有实测数字**；量级是外推 |
| 16 | §4.2「多进程白占 CUDA context」 | 结构性推理；需 `nvidia-smi` 实测确认。且任务书 1,080 MiB 与 `vram_diagnosis.json` 的 457 MiB **互相矛盾**，真实基线待重测 |
| 17 | §1.3「本项目可用更激进量化」 | **这是我的判断**，依据是「1–3 token 无累积漂移面」+ 用户风险偏好 + 反方证据场景为 coding。**必须以本项目四态决策回归验证** |
| 18 | §3.2 KV 非对称 KV 在本项目 8B 模型上的实际省量 | 288 MiB 是几何外推（KV 数学已与实测吻合到 1%，可信度高）；但 discussion #23470 用的是 qwen2.5-7b，**架构不同** |
| 19 | Q1_0/Q2_0（1.125/2.25 bpw） | 本机 quantize 列表中有，但**未验证 CUDA 算子覆盖**，也**未在 dry-run 中测试尺寸** |

---

## 7. 用量统计 + 诚实标注

### 用量（严格遵守预算）

| 工具 | 用 | 上限 | 状态 |
|---|---:|---:|---|
| AnySearch `search` | **8** | 10 | ✅ 未超 |
| AnySearch `extract` | **5** | 5 | ✅ 刚好用满 |
| 子代理 | **0** | 禁止 | ✅ 未派 |
| 启动 llama-server / GPU 任务 | **0** | 禁止 | ✅ 未跑 |
| 下载模型 | **0** | 禁止 | ✅ 未下 |
| 写源码 | **0** | 只读 | ✅ 只写了本报告 |

**预算外补充（未消耗 AnySearch 额度）**：`gh_search` / `gh pr view`（GitHub 插件，独立配额）、HuggingFace 公开 API（免鉴权）、本机二进制/GGUF 解析与 `llama-quantize --dry-run`（**只读，不写文件、不碰 GPU**）。

### 诚实标注

1. **本报告最强的部分是 §1.1 与 §2.2** —— 那是**在你本机的真实模型上实测**出来的数字，不是检索来的。
2. **`llama-quantize --dry-run` 是从 IQ4_NL 二次量化算出的尺寸**。从 F16 源正式量化后，**尺寸可能略有差异**（估计 ±20~30 MiB，因为 dry-run 报 IQ4_NL 为 4,589.88 MiB 而实际文件是 4,571.6 MiB）。**但「IQ3_XXS ≈ 3.3 GiB / IQ2_XXS ≈ 2.4 GiB」这个量级是可靠的。**
3. **§1.3 的「本项目可以更激进」是我的判断，不是实测。** 它建立在「1–3 token 输出没有累积漂移面」这个推理上，以及用户自己的风险偏好。**必须用本项目的四态决策回归来验证，而不是通用 benchmark。**
4. **§3.2 推翻用户「KV q4_0 只能省 112 MiB」这个结论的建议是「重测」而不是「断言」。** 数学与实测差 5 倍，可能是我漏掉了某个约束（例如那次测试 K/V 只降了一半，或 fallback 到非 FA 路径）。但 discussion #23470 的官方 KLD 数据强烈支持「K 保精度、V 可砍」，而且**用户当年的失败恰恰源自把 K 也砍了**。
5. **没能验证的事**：(a) 非对称 KV 在本机需重编 llama.cpp，**我没跑**（禁止 GPU 任务）；(b) NVFP4 目录能否直接产出 mmproj，**我没试**（会启动 python 转换）；(c) `nvidia-smi` 当前态**我没看**（用户机器正在恢复，避免干扰）。
6. **本报告完全不评估准确率/视觉精度**（用户明确要求）。§1.2/§1.3 提到质量，唯一目的是**估算量化的代价**，不是评价模型答得对不对。

### 最终账（如果三条全做）

| 场景 | JoyAI 合计 | 给游戏剩 | 相对现在 |
|---|---:|---:|---:|
| **现状** | 8,082 MiB | 7,149 MiB | — |
| **保守（IQ3_XXS + mmproj Q8_0）** | **6,346 MiB** | **8,885 MiB** | **多省 1,736 MiB** |
| **激进（+ KV V-q4_0）** | **6,058 MiB** | **9,173 MiB** | **多省 2,024 MiB** |
| **极限（IQ2_XXS + mmproj Q8_0 + KV V-q4_0）** | **5,401 MiB** | **9,830 MiB** | **多省 2,681 MiB** |

> 即：**给游戏的可支配显存最多可以从 7.1 GiB 提到 9.8 GiB。**
