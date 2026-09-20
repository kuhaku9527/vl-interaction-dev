# llama.cpp 生态实战技术帖汇编 —— 为本项目 16GB 显存腾挪

- **端点身份**：检索端点（AFK，只读）
- **日期**：2026-09-20
- **目标**：为 1080p 窗口化游戏腾出显存，同时不破坏 1Hz 决策节拍（P99 ≤ 1200ms）
- **检索工具**：`anysearch-probe`（web_search 已 402）
- **用量**：11 次 search + 7 次 extract（其中 2 次 extract 因未重定向到文件而重复，见 §6）
- **性质**：本文只做检索与整理，**未改动任何源码**，未启动 llama-server，未下载模型

---

## 0. 一句话结论

**最值得做的两条：**

| # | 手段 | 预期省显存 | 代价 | 风险 |
|---|---|---|---|---|
| 1 | `--cache-type-k q8_0 --cache-type-v q8_0`（**前提：FA 确实开启**） | **~1,080–1,170 MiB** | decode 慢 ~5–10%；**prefill 基本不受影响**（见 §1.2） | 若 FA 被静默关闭 → V 量化直接抛异常启动失败；K/V 必须同量化 |
| 2 | mmproj F16 → **Q8_0**（用 `convert_hf_to_gguf.py --mmproj --outtype q8_0`，**不能用 llama-quantize**） | **~400–540 MiB** | 需重新转换一次；无运行时开销 | 视觉精度：**目前无任何"看图变差"的实测证据**，但同样**也无正面实测**，属未验证区 |

**两条合计 ≈ 1.5–1.7 GiB**，9,177 MiB → **~7,500–7,700 MiB**，且**两者都是加载期改动，不触碰视觉编码路径**（这正是 `--no-mmproj-offload` 被否决的原因——它省 1,570 MiB 但让 prompt 从 266ms 涨到 5,887ms）。

**明确不要做的**：`-ctv q4_0`（质量断崖，见 §1.3）；`--n-cpu-moe`（本模型非 MoE，不适用，见 §3.1）。

---

## 1. KV cache 量化

### 1.1 本机几何下的量化收益（复算，**与项目修正后的估算一致**）

几何：36 层 / 8 KV heads / head_dim 128 / n_ctx 16384。

```
bytes_per_token(f16) = 2(K,V) × 36 × 8 × 128 × 2B = 147,456 B = 144 KiB/token
KV_total(f16) = 144 KiB × 16384 = 2,304 MiB        # 项目实测 2,321 MiB，吻合
```

量化因子（每 32 个值一个 block）：

| dtype | bytes/32 values | 相对 f16 | 本项目 KV 实测/推算 | 相对 f16 省 |
|---|---|---|---|---|
| f16 | 64 | 1.0 | 2,321 MiB（实测基线） | — |
| **q8_0** | **34** | **0.53125** | **~1,224 MiB（推算）** | **~1,080 MiB** |
| q4_0 | 18 | 0.28125 | ~648 MiB | ~1,656 MiB |
| q8_0(K only，V 留 f16) | — | 0.765625 | ~1,764 MiB | ~540 MiB |

> **交叉验证**：S1（dev.to）独立给出的比例是"both halves quantized ≈ 53% of f16，K alone ≈ 77%"——与本表 0.53125 / 0.765625 **逐位吻合**。说明项目此前"只省 420 MiB"的旧结论确实是**用错了 28层/4KV头 的几何**。

**但实测比例可能比理论更有利**：S1 在同一台 24GB 机器上实测 f16→q8_0 的真实比值是 **2.02×**（理论 1.88×），作者归因于 FA 带来的 layout/padding 差异。若本项目也落在 2.02×，则 q8_0 ≈ 2,321/2.02 = **1,149 MiB，省 1,172 MiB**。

⇒ **报告区间取 ~1,080–1,170 MiB，保守规划按 1,080 MiB 计。**

**S1 给出的最重要方法论**（强烈建议本项目采纳）：**别用 metadata 算，直接读 llama.cpp 启动日志自己打印的那一行**：

```
llama_kv_cache: size =  160.00 MiB (  4096 cells,   8 layers,  1 seqs), K (f16): ...
```

用**你要发布的那组 flags**起两次小 ctx，读日志、相除。S1 作者正是没做这件事，先得到 368,640 B/token（默认 flags：f16 + FA off），而生产配置（q8_0 + FA on）是 182,784 B/token，**差 2.02×**——"我非常严谨地测量了一个我并不发布的配置"。

> 注：S1 提到 **PR #16812（2025-10）移除了 KV cache size padding**（此前 FA on 时按 256 对齐，off 时按 32）。b10155 应是该 PR 之后的版本，但**测的时候仍要取足够大的 ctx**，避开残留的 per-graph `n_kv` 256 对齐。

### 1.2 q8_0 的真实速度代价

**"q8_0 costs 9% of throughput" 这条标题：未能证实。** 我专门跑了 2 次查询定位它，**没有任何来源给出 "9%" 这个数字**。可查到的真实数字是：

| 来源 | f16 | q8_0 | q4_0 | 相对代价 | 性质 |
|---|---|---|---|---|---|
| S2 particula.tech 转载的表格 | 81.8 tok/s | **76.4 tok/s** | 80.3 tok/s | **q8_0 慢 6.6%，且是三者中最慢** | 二次引用，**原始为单人/12 prompt/无复现**的 harness（S2 自己明确标注了这些 caveat）；方向可信，绝对值不可移植 |

⇒ **可用的结论：q8_0 的 decode 代价量级在 5–10%，且 q8_0 往往比 q4_0 更慢**（q4_0 反直觉地更快，见下）。

**对本项目意味着什么**：本项目的 decode 输出只有 1–3 token（投机解码已实测 0.27×），**decode 占比极小**；1Hz 节拍的耗时几乎全在 **vision encode + prefill**。所以：

> **"q8_0 慢 7% decode" 对本项目基本无感。真正要确认的是 q8_0 对 prefill 的影响。**

**prefill 侧：那条"92.5% 吞吐崩塌"的传言已被原始作者本人证伪。** 这是本次检索最有价值的纠错：

- S7（OmniForge 博客）与 S6 的讨论串标题都传播着"**KV 量化在 64K 上下文吞吐慢到 92%/92.5% 崩塌**"。
- **S6（llama.cpp discussion #20969）里，原始测量者亲自回复**：
  > `"92.5% prompt throughput collapse at 64K" -- Wrong. I measured throughput from requests that failed silently. Prompt throughput is identical across all cache [types]`
- S2 也独立复核了这个数字，并指出原始帖子 **标题写了 q8_0 但根本没发布 q8_0 数字**、声称 128K 但只测到 64K。

⇒ **结论：不要因为"KV 量化让 prompt 处理崩塌"而否决 q8_0——那个数据是静默失败的请求统计出来的假象，已被撤回。** OmniForge 那篇是传播链条的下游。

### 1.3 q4_0 的质量断崖 —— 项目已有证据，外部独立证据同向

S2 转载的第三方表格（同一 harness）：

| dtype | 显存 | tok/s | 相对 f16 的输出相似度 |
|---|---|---|---|
| f16 | 4,899 MB | 81.8 | 100% |
| q8_0 | 4,691 MB | 76.4 | 81.6% |
| **q4_0** | 4,579 MB | 80.3 | **8.3%** |

> 原文措辞：**"Not a trade, a cliff, and the reason q4_0 is not a general-purpose setting."**

- **从 f16→q8_0 省 208 MB**（且 q8_0 唯一代价是慢一点）；
- **从 q8_0→q4_0 再省 112 MB，相似度从 81.6% 掉到 8.3%**。
- **关键陷阱**：速度列 81.8 / 76.4 / 80.3 **几乎持平**——"监控吞吐、TTFT、p99 的栈会显示一片绿灯，而输出质量已经塌了一个数量级"。

**这与项目自己记录的"K 侧 q4_0 有 375/500 答案抖动"完全同向，互为独立佐证。** 且注意：省下的增量只有 ~112 MB 量级——**用 1/9 的质量换 100 MB 显存，绝对不划算**。

⇒ **`-ctv q4_0` 明确否决。**

### 1.4 FA（flash attention）与 KV 量化的关系 —— 项目记录正确，且找到了根因

**项目的记录"FA 关闭时量化 V cache 会直接抛错"，被证实为真，且 S1 给出了源码级根因。**

三条错误信息（不同版本措辞）：

```
llama_context: quantized V cache requires flash_attn to be enabled
quantized V cache was requested, but this requires Flash Attention
V cache quantization requires flash_attn        # 旧版，早已不在树里，但搜索结果大多还是它
```

**根因（S1 逐层剥开，这是网上几乎没人写下来的部分）：**

经典（非 FA）attention 路径最后是 `ggml_mul_mat(ctx0, v, kq)`，它沿 `ne[0]` 归约，因此 **V 必须以 KV-position 轴为行轴到达 —— 即 V 需要转置存储**。代码里就是：

```cpp
bool v_trans = true;  // the value tensor is transposed
```

而这个 flag 在**每一处 cache 构造点**都被设成字面上的 `!cparams.flash_attn`。

**"这个 flag 就是整个故事的答案。"** FA 开 → V 自然布局存储（`ggml_flash_attn_ext` 要反向）；FA 关 → V 转置存储。

转置存储下追加一个 token，写入会**散开**（每个 head 一个元素散到 `n_embd_v_gqa` 个不同行，跨距 `kv_size`）。llama.cpp 用 `ggml_set_rows` 表达，并且转置分支做了看似疯狂的重塑：

```cpp
// in this branch the v_idxs are constructed in such a way that each row is a single head element
ggml_tensor * v_view = ggml_reshape_2d(ctx, v, 1, ggml_nelements(v));
v_cur = ggml_reshape_2d(ctx, v_cur, 1, ggml_nelements(v_cur));
return ggml_set_rows(ctx, v_view, v_cur, v_idxs);
```

而 **`ggml_set_rows` 一次量化"一整行"**——它调用该类型的 `from_float(src, dst, nc)`，`nc` 等于行长。**当 `nc == 1` 且 q8_0 是 32 元素一个 block 时，根本没有东西可量化**：`quantize_row_q8_0` 断言 count 必须是 32 的倍数；`ggml_set_rows` 还硬断言源必须是 F32/F16。

⇒ **不是"设计上的限制"，是"转置布局下量化在数学上无处落脚"。** 所以 FA 与量化 V 是焊死的。

**最阴险的一点（与本项目直接相关）**：报错**不告诉你原因，也经常不是你自己关的**：

- **后端替你关掉了**：S1 引用 LM Studio bug tracker #1943 —— Vulkan runtime 2.15.0 **静默强制关闭 FA**，而量化 KV 配置原样保留，"昨天能加载的配置今天失败"，回滚 2.14.4 即恢复。
- **llama.cpp 自己会在 graph resolution 阶段静默降级**：当 FA 节点落在不支持的设备上时，它打印 `... not supported, set to disabled`，**然后异常在事后才抛出**——这正是为什么两条错误串里有一条"在启动相当晚的时候才出现"。
- S1 引用 **ggml-org/llama.cpp#21450（Apple Metal）**：**混合量化 KV** 在 FA 不可用时失败，而**统一的 `q4_0/q4_0` 和 `f16/f16` 加载正常**。

### 1.5 "K 和 V 必须同量化吗？"（对称 vs 非对称）

**证据指向：保持对称是最安全的，但社区正在探索非对称。**

- **保持对称**：#21450 的证据是"uniform `q4_0`/`q4_0` 与 `f16`/`f16` 加载正常"，而 mixed 量化 KV 失败。S1 的总结原话：**"which is a good reminder to keep K and V symmetric"**。
- **非对称探索**：检索到两个明确针对该主题的条目——
  - `ggml-org/llama.cpp` **discussion #23470**「Asymmetric KV q8/q4 cache to preserve precision while reducing memory」
  - Reddit r/LocalLLaMA `1tkih6y`「[llama.cpp] Asymmetric KV q8/q4 cache: current caveats」（标题里就写着 "current caveats"）
  - **【未深读，extract 额度已耗尽】** —— 从标题判断方向是 `K=q8_0 / V=q4_0`（保住 K 的精度，因为项目自己的证据正是 K 侧 q4_0 最致命）。
- **一条来自红迪讨论串的实用档位建议**（S3 评论，作者是那个 PR 的实现者角色上的讨论者）：降级顺序应为
  > `BF16 → V 先降到 Q8_0 → K、V 都 Q8_0 → K=8 / V=4 → 最后才两边都 4，"but you're gonna have a bad time"`

- **另一个坑（S3 作者自述，与反量化有关）**：`q8 → q4 → q8` **不可能恢复原 q8**，最好情况也只是 q4。**不要自动往上抬精度。**

⇒ **本项目建议：`-ctk q8_0 -ctv q8_0`，严格对称。** 只有在实测发现 K 侧精度不足时，才考虑去读 #23470 走非对称，且要先验证 b10155 支持该组合。

### 1.6 一条值得关注的边缘路径（不改代码不可用）

S3（Reddit r/LocalLLaMA `1twwve0`）里有人实现了 **动态/按需 KV 量化：`POST /requantize_kvcache`（PR #24134）**，接受 `(ctk, ctv)`，把现有 KV 读出、量化、装进新 cache——**不用卸载重载模型，也不用重算整个 prompt**。作者在 5090 上实测，且明确说**只在部分架构上可用，Qwen3 是他测试用的模型**。

作者还列了三个 wishlist：单独 load/unload mmproj 的端点、`--fit` 自动动态量化 CLI flag、按需 prompt processing 端点。

- **对本项目**：这是**最贴合"游戏时降精度、非游戏时保精度"**的方案——但**需要 llama.cpp 侧合入 PR #24134 并改本项目后端**，超出"只调参"范围。**记录为后续观察项，本轮不作为方案。**

---

## 2. mmproj 量化

### 2.1 关键操作陷阱（★ 这条最实用）

**不能用 `llama-quantize` 量化 mmproj —— 会直接失败：**

```
llama_model_quantize: failed to quantize: unknown model architecture: 'clip'
```

S4（llama.cpp discussion #15453）里 **maintainer CISC 给出了确切的根因与代码位置**：

- `llama_model_quantize_impl` 调用 `model.load_arch(ml)`（`src/llama-quant.cpp:604`）
- 它做 `arch = ml.get_arch(); if (arch == LLM_ARCH_UNKNOWN) throw ...`（`src/llama-model.cpp:447-451`）
- 而 `LLM_ARCH_NAMES`（`src/llama-arch.cpp:7-97`）**只登记文本模型架构**（llama / llama4 / qwen2 / qwen2vl / qwen3 / qwen3moe / gemma3 …），**根本没有 `clip`**。

⇒ **正确路径是转换期出量化：`convert_hf_to_gguf.py --mmproj --outtype q8_0`。** 转换脚本支持的 mmproj dtype 集合为 **`{f32, f16, bf16, q8_0, tq1_0, tq2_0}`**（S4 原文）——**注意：Q4_0 不在其中**。

⇒ 这条同时解释了为什么社区里 mmproj 普遍是 F16：**不是大家不想量化，是量化工具链默认走不通**。

### 2.2 精度影响：**这是本项目最关键、也最缺乏证据的一环**

诚实地讲清证据状态：

| 主张 | 证据强度 |
|---|---|
| mmproj Q8_0 ≈ f16 的 50% 体积；Q4_0 ≈ 25% | **提案里的估算**（S5，issue #18881），非实测 |
| 量化 mmproj "likely having minimal impact on vision encoding quality" | **S5 提案者的推测原话**——用了 "likely" 与 "estimated"，**不是实测结论** |
| 有人实测对比过量化 mmproj 对 VQA/OCR 的影响 | **本次检索未找到任何此类报告** ❌ |

**S5（issue #18881）提案原文的量化体积估算：**

| mmproj dtype | 体积 | 相对 f16 |
|---|---|---|
| f16 | 1.33 GB（Qwen2-VL-2B） | 100% |
| f32 | 2.66 GB | 200% |
| **Q8_0** | **~665 MB** | **~50%** |
| Q4_0 | ~333 MB | ~75% 缩减 |

**该 issue 的动机正是"主模型量化得很好（Q4_K_M ~1GB），但 mmproj 1.33GB 反而成了瓶颈"——与本项目 F16 mmproj 1.08GiB 的处境完全一致。**

- **一个方向性的间接证据**：S4 里有人用 **ExecuTorch 把 mmproj 量化成 Q4_0** 跟 llama.cpp（mmproj 保持未量化）在树莓派 5 上对比，结果是 ExecuTorch 的 prompt eval **2.55s vs llama.cpp 4.15s（更快）**。**但这是"不同引擎"的对比，不能归因于 mmproj 量化**，且没有记录任何质量指标。**不要拿这条当视觉质量的证据。**

### 2.3 本项目专属判断

- mmproj 量化后**仍然留在 GPU 上**（这就是它与 `--no-mmproj-offload` 的本质区别）——后者把视觉编码挪到 CPU，实测 prompt 266ms→5,887ms，**已否决**。**量化 mmproj 是唯一能"既省显存又不牺牲视觉路径"的手段。**
- **预期收益要打折算**：本项目 mmproj **实测占用 1,483 MiB，而文件只有 1.08 GiB**——差额 ~400 MiB 是 **compute buffer / 图执行缓冲，不会因为权重量化而缩小**。所以：
  - **乐观：~540 MiB**（1,080 MiB 文件砍半）
  - **保守：~400 MiB**
  - **建议按 ~400 MiB 规划**
- **验证方法（成本极低，强烈建议做）**：转换出 Q8_0 mmproj 后，**用同一张图跑同一组问题，对比输出**。因为本项目 mmproj 承担"看图"职责，这是**必须自测而非采信社区**的一环。
- **风险控制**：Q8_0 相对 F16 只多一个 32 值 block 的 scale（34/32 vs 2.0 = 1.0625 字节/值），量化误差远小于 Q4_0；**且视觉塔不参与自回归误差累积**（不像 KV cache 会被后续 token 反复读取放大）。**理论上这是本项目风险最低的一项**——但**仍属推断，需实测确认**。

---

## 3. 其他省显存手段（逐条：适用性 + 本项目能否用）

### 3.1 `--n-cpu-moe` —— ❌ **不适用**

**该参数的作用是把 MoE 的 expert 张量放到 CPU**（名字即 `n-cpu-moe` = number of CPU MoE layers）。本项目模型是 **Qwen3-8B 稠密（dense）+ Qwen3-VL ViT**，**没有 expert 张量**。⇒ **该 flag 对本项目无可 offload 的对象，不适用。**

### 3.2 `-ot` / `--override-tensor` —— ⚠️ **技术上可用，但对本项目是陷阱**

`-ot` 接受正则，可按张量名把**任意**张量（含稠密 FFN）钉到 CPU，**不限于 MoE**，所以对本项目**技术上可行**，例如把最后几层的 FFN 推到 CPU。

**但代价与 `--no-mmproj-offload` 是同一个性质的**：被推走的层每 token 都要过 PCIe 往返。本项目 1Hz 节拍预算 1000ms、P99 1200ms，**prompt 处理已经在关键路径上**。S3 里的相关讨论也印证：即使在 5090 上跑 27B，人们也在为"换配置要十几秒重载 + 重算 prompt"而痛苦。

⇒ **判定：不作为首选。** 仅当"KV q8_0 + mmproj Q8_0 仍不够"时才作为**最后手段**，且应**只推极少层并实测 P99**，优先推**不被视觉路径触发的那部分**。

### 3.3 `-ngl` 部分卸载 —— ⚠️ 定量备用

36 层、权重 ~4,535 MiB ⇒ **每层约 126 MiB**。若要硬性腾出 X MiB，可卸载 `ceil(X/126)` 层到 CPU。这是**最直接的杠杆，但线性伤 prefill**。作为 q8_0 + mmproj 之后的第三顺位。

### 3.4 `--swa` / 上下文滑窗 —— ❌ **对 Qwen3-8B 不适用**

S1 有一整段关于 **SWA 与 KV 计算**的重要论述：**Gemma 家族的 attention 是交错的**——少数层attend全长上下文，**其余层跑一个不随 `n_ctx` 增长的短滑窗**。后果是：

> "Metadata math doesn't know that. It multiplies one per-layer cost by every layer and confidently describes a model that does not exist." —— 在 24GB 机器上，**这个错误会让窗口估算偏差 4×**。

**关键区别**：
- **对 SWA 交错的模型（Gemma 系）**：KV 显存**本来就不随 n_ctx 全量增长**，滑窗层已经"免费"省了。而且 S1 用启动日志实测发现——**这种模型会打印多行 `llama_kv_cache:`，每行对应一个 cache，层数远少于总层数（例子里是 8 层），你必须把它们加起来**。
- **对 Qwen3-8B（本项目）**：**是全长 attention，不是 SWA 架构** ⇒ **`--swa` 类参数没有可作用的层**，不能指望它省显存。

⇒ **判定：不适用。** 但 §3.6 的"读启动日志"方法论**对本项目同样适用**——本项目也应确认 `llama_kv_cache:` 只有一行（若出现多行，说明几何假设有误，前面所有算术都要重做）。

### 3.5 `--cache-reuse` / `--ctx-checkpoints` —— 方向相反，注意别踩

- **`--cache-reuse`**：减少 prompt 重算，**省的是时间不是显存**。对本项目价值有限（1Hz 节拍下 prompt 每拍都在变）。
- **`--ctx-checkpoints`**：**这是省显存要"关掉"而不是打开的东西**——它保存多个上下文检查点，**每个都占一份 KV 状态显存**。**建议显式设为 `0`**（并确认 b10155 的参数名；较新版本已出现 `--swa-checkpoints` 变体）。
  - **【推断，非实测】** 具体省多少取决于配置，未找到带数字的帖子。
- 与之相关的一条 S2 结论值得记住：**"many small sliding-window attention layers"是"不要上量化 KV"的四个条件之一**——因为量化开销在滑窗层上摊不薄。本项目无滑窗层，**不触发这个例外**。

### 3.6 ★ 最有价值的方法论：**别信 metadata，读启动日志**（S1）

这是本次检索中**可直接落地、且能防止本项目再次"用错几何"**的一条：

> **"Metadata describes the model. I needed a number that describes the *allocation*. Those are different things, and only one of them gets printed at runtime."**

```
llama_kv_cache: size =  160.00 MiB (  4096 cells,   8 layers,  1 seqs), K (f16): ...
```

**探针做法（S1 原文，约 40 行 bash）**：用**一个小 ctx + 你要发布的完整 flags** 启动引擎，**解析它自己打印的日志**，相除，杀掉。"两秒，无推理，无下载。"

**S1 用血泪换来的两条纪律**：
1. **必须传你要发布的 flags**——他家探针用默认 flags 量出 368,640 B/token，生产配置实际 182,784 B/token，**差 2.02×**。
2. **ctx 要取在 padding floor 之上**——否则你量到的是"一条舍入规则，而不是模型"。

**本项目应立即执行**：在 `-ctk q8_0 -ctv q8_0 --flash-attn on` 下重启一次，抓 `llama_kv_cache:` 行，**用实数替换 §1.1 的推算**。

### 3.7 Windows / CUDA 特有项

**本次检索未能找到带数字的 Windows 专属显存帖子。** 检索命中的是（见 §5 S-W1..W6）：一条 Reddit「llama.cpp - how to free up even more space on your GPU」、一条「Why does it take the same amount of RAM as VRAM?」（discussion #19883）、一份 Windows+NVIDIA 运行指南、一份 Performance Tuning 官方文档。**这些均未深读（extract 额度耗尽），以下为推断，请勿当实测采信：**

| 项 | 判断 | 依据强度 |
|---|---|---|
| `--no-mmap` | **不省显存**，只改权重加载方式（全量进 RAM 而非 mmap）。对 32GB RAM 机器可能反而挤压系统内存。S3 里一位用户的命令行确实带了 `--no-mmap` 且实测 29,244 MiB / 32GB，但那是 5090 场景，不能证明 `--no-mmap` 省了显存 | 【推断】 |
| 显存碎片 | **未证实**。检索到 r/LocalLLaMA「llama.cpp - how to free up even more space on your GPU」标题高度相关，**但未 extract** | 【未验证】 |
| `GGML_CUDA_NO_PINNED=1` | 影响的是**锁页主机内存**，不是 VRAM。32GB RAM 场景下收益存疑 | 【推断】 |
| `--batch-size` / `--ubatch-size` 调小 | 可降低**计算缓冲峰值**。S3 那位用户用的是 `--batch-size 6144 --ubatch-size 1024`。对本项目 VL 场景**可能有用**（图像编码的图可能很大），但**未经实测** | 【推断】 |

> **建议本项目**：这些项在 KV q8_0 + mmproj Q8_0 落地后**有余量再逐项 A/B**，每项都测 P99，别批量上。

---

## 4. 可直接抄的配置

### 4.1 推荐基线（两条主要手段）

```bash
llama-server \
  -m <Qwen3-8B-IQ4_NL.gguf> \
  --mmproj <Qwen3-VL-mmproj-Q8_0.gguf> \
  --n-gpu-layers 999 \
  --parallel 1 \
  --ctx-size 16384 \
  --flash-attn on \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  --ctx-checkpoints 0 \
  --no-mmproj-offload=false
```

`--flash-attn on` 与 `-ctv q8_0` **必须同时出现**（§1.4）。`--ctx-checkpoints 0` 为省显存项（§3.5，【推断】）。

### 4.2 mmproj Q8_0 的生成（**不要用 llama-quantize**）

```bash
# 正确：转换期直接出 q8_0
py convert_hf_to_gguf.py <Qwen3-VL-hf-dir> \
  --mmproj \
  --outtype q8_0 \
  --outfile Qwen3-VL-mmproj-Q8_0.gguf

# 错误（必然失败，见 §2.1）
# llama-quantize mmproj-F16.gguf out.gguf Q8_0
#   → llama_model_quantize: failed to quantize: unknown model architecture: 'clip'
```

`--outtype` 支持的 mmproj dtype：**`{f32, f16, bf16, q8_0, tq1_0, tq2_0}`** —— **Q4_0 不在其中**，别试。

### 4.3 上线前必做：**先量，再信**

```bash
# 用生产 flags 起一个小 ctx，抓 llama_kv_cache 行，算出真实 bytes/token
# 然后 × 16384 与项目实测 2,321 MiB 对账（S1 的方法论，§3.6）
```

对齐后再启动正式服务。**若启动日志出现 `not supported, set to disabled`（FA 被静默降级），立即中止——此时 `-ctv q8_0` 会启动失败。**

### 4.4 明确不要用的

```bash
# ❌ 质量断崖：q8_0→q4_0 只多省 ~112 MB，相似度 81.6% → 8.3%
-ctv q4_0

# ❌ 非 MoE 模型，不适用
--n-cpu-moe N

# ❌ 已实测否决：prompt 266ms → 5,887ms
--no-mmproj-offload
```

---

## 5. 来源清单（按证据强度分组）

### 【有人实测过 · 一手数据】

| ID | 来源 | 命中内容 |
|---|---|---|
| **S1** | [dev.to/dreamdeck — "V cache quantization requires flash_attn"](https://dev.to/dreamdeck/v-cache-quantization-requires-flashattn-the-llamacpp-error-that-quietly-halves-your-context-1kdb) | **★ 最有价值**。源码级解释 FA↔量化V 的焊死关系（`v_trans = !cparams.flash_attn` + `ggml_set_rows` 的 `nc==1` 断言）；实测 f16 368,640 B/token vs q8_0+FA 182,784 B/token（**比值 2.02×**）；指出 PR #16812 移除 padding；"读启动日志而非算 metadata"的方法论；FA 被后端/框架静默关闭的三种路径 |
| **S2** | [particula.tech — KV Cache Quantization: What FP8 and q8_0 Cost in Accuracy](https://particula.tech/blog/kv-cache-quantization-accuracy-loss-benchmarks) | f16/q8_0/q4_0 的显存+tok/s+相似度三联表（q8_0 **76.4** vs f16 81.8；q4_0 相似度 **8.3%**）；**复核并质疑了 92.5% 崩塌数据**；vLLM 的"stay on BF16"四条件。⚠️ 表格本身是**二次引用**，原始为单人/12 prompt/无复现 |
| **S3** | [Reddit r/LocalLLaMA — Dynamic KV cache quantization and load-on-demand mmproj/MTP](https://www.reddit.com/r/LocalLLaMA/comments/1twwve0/dynamic_kv_cache_quantization_and_loadondemand/) | RTX 5090 实测：Q6_K + mmproj on + **q8_0 kvcache** + 150k ctx = **29/32 GB**；另一个用户的完整命令行（`--flash-attn on --cache-type-k q8_0 --cache-type-v q8_0 --kv-unified --no-mmap`，**实测 29,244 MiB**）；降级档位顺序建议；**PR #24134 动态量化**（含 Qwen3 实测） |
| **S4** | [llama.cpp discussion #15453 — Q4_0 quantization support for mmproj](https://github.com/ggml-org/llama.cpp/discussions/15453) | **maintainer CISC 逐行定位 `unknown model architecture: 'clip'` 的根因**（`llama-arch.cpp:7-97` 只登记文本架构）；mmproj 支持的 dtype 集合；用户实测 ExecuTorch(Q4_0 mmproj) vs llama.cpp 树莓派对比 |
| **S9** | ggml-org/llama.cpp **#21450**（经 S1 引用） | Metal 上**混合量化 KV 在 FA 不可用时失败，而统一 q4_0/q4_0 与 f16/f16 正常** → K/V 对称的依据 |

### 【已证伪 —— 不要采信】

| ID | 来源 | 内容 |
|---|---|---|
| **S6** | [llama.cpp discussion #20969 — TurboQuant](https://github.com/ggml-org/llama.cpp/discussions/20969) | **原始测量者本人回复**：「"92.5% prompt throughput collapse at 64K" -- **Wrong.** I measured throughput from requests that failed silently. **Prompt throughput is identical across all cache [types]**」 |

### 【未证实 / 传闻传播链】

| ID | 来源 | 内容 |
|---|---|---|
| **S7** | [OmniForge — Why Your Local LLM Is Slow](https://omniforge.online/blog/your-local-llm-is-slow-because-of-five-config-flags) | 复述"KV 量化在 64K 慢到 92%"——**即 S6 已被撤回的那个数字**，属传播下游。**该博客的 llama.cpp 配置建议整体需谨慎对待** |

### 【提案 / 推测 —— 无实测支撑】

| ID | 来源 | 内容 |
|---|---|---|
| **S5** | [Issue #18881 — Support for quantized mmproj files](https://github.com/ggml-org/llama.cpp/issues/18881) | mmproj Q8_0≈50%、Q4_0≈25% 的**估算**；"likely minimal impact on vision encoding quality" 是**推测原话** |

### 【未深读 —— extract 额度耗尽，仅标题级线索】

| ID | 来源 | 为何值得后续读 |
|---|---|---|
| S8a | [llama.cpp discussion #23470](https://github.com/ggml-org/llama.cpp/discussions/23470) | **Asymmetric KV q8/q4**：非对称 K/V 量化的精度-显存权衡 |
| S8b | [Reddit 1tkih6y](https://www.reddit.com/r/LocalLLaMA/comments/1tkih6y/llamacpp_asymmetric_kv_q8q4_cache_current_caveats/) | 标题即含 **"current caveats"** ——非对称量化的坑 |
| S8c | [Reddit 1mhlj69 — What's the verdict on using quantized KV cache?](https://www.reddit.com/r/LocalLLaMA/comments/1mhlj69/whats_the_verdict_on_using_quantized_kv_cache/) | 综合讨论；摘要含「if it frees memory for a slightly less quantized model…worse performance on smaller context」 |
| S8d | Reddit 1dalkm8 — Memory Tests using llama.cpp KV cache quantization | 内存实测 |
| S8e | [llama.cpp #5932 — 4-bit KV Cache](https://github.com/ggml-org/llama.cpp/discussions/5932) | q4_0 KV 的原始讨论 |
| S8f | [NVIDIA 论坛 — KV Cache Quantization Benchmarks on DGX Spark](https://forums.developer.nvidia.com/t/kv-cache-quantization-benchmarks-on-dgx-spark-q4-0-vs-q8-0-vs-f16-llama-cpp-nemotron-30b-128k-context/365138) | 标题含 q4_0/q8_0/f16 三方对比（S2 已指出其"标题写 q8_0 但没发 q8_0 数字"） |
| S8g | [Reddit 1suur3s — Qwen3.6 27B's surprising KV cache quantization test results](https://www.reddit.com/r/LocalLLaMA/comments/1suur3s/qwen36_27bs_surprising_kv_cache_quantization_test/) | **Qwen 系**模型的 KV 量化实测 |
| S8h | [llama.cpp #17200 — Qwen3-VL on llama-server fails on second…](https://github.com/ggml-org/llama.cpp/issues/17200) | **Qwen3-VL 相关 bug**，与本项目直接同栈 |
| S8i | [Reddit 1u8i79d — llama.cpp: how to free up even more space on your GPU](https://www.reddit.com/r/LocalLLaMA/comments/1u8i79d/llamacpp_how_to_free_up_even_more_space_on_your/) | **§3.7 显存碎片/Windows 问题的最可能答案所在** |
| S8j | [Performance Tuning — llama.cpp 官方文档](https://ggml-org-llama-cpp.mintlify.app/advanced/performance-tuning) | 官方参数权威说明 |
| S8k | [Qwen 官方文档 — llama.cpp 量化](https://qwen.readthedocs.io/en/latest/quantization/llama.cpp.html) | Qwen 系 mmproj 转换的官方做法 |
| S8l | [llama.cpp quantization README](https://github.com/ggml-org/llama.cpp/blob/master/tools/quantize/README.md) | 官方量化表 |

### 【中文社区】

| ID | 来源 | 备注 |
|---|---|---|
| S-C1 | [知乎 — llama.cpp 量化和部署 qwen2.5 7b/QWQ/QVQ](https://zhuanlan.zhihu.com/p/16964017115) | QVQ = 视觉模型，可能含 mmproj 做法 |
| S-C2 | [博客园 — llama.cpp 载入 Qwen3 30B VL 模型（linux MI50 下）](https://www.cnblogs.com/taozebra/p/19486686) | **Qwen3-VL + llama.cpp 实操** |
| S-C3 | Reddit 1twwve0 中文版（`?tl=zh-hans`） | 同 S3 |

> **中文社区检索结论**：命中量明显少于英文，且**未发现带显存数字的 Qwen3-VL mmproj 量化实测**。本项目这块基本要自己测。

---

## 6. 实际用量统计（如实报告）

| 项 | 限额 | 实际 | 说明 |
|---|---|---|---|
| **search** | ≤12 | **11** | 其中第 11 次返回 "No relevant results found"（专门定位"9% throughput"这个数字，未命中） |
| **extract** | ≤6 | **7** | **超 1 次** |
| **合计** | 18 | **18** | 总调用数与上限持平 |

**extract 超支原因（如实说明）**：
1. 第 1 次对 Reddit `1twwve0` 用了 `--format compact`，**只返回标题+URL 无正文**——该格式对 extract 不产出内容，属无效调用；随即用 `--format full` 重取。
2. 对 dev.to 那篇先取 `--max-chars 7000`，**输出被 `head -60` 截断**；为拿到 FA 根因的完整中间段，**重定向到文件再取一次**。

⇒ **两次浪费均为「未预估输出长度 / 未重定向到文件」造成**，非检索目标发散。**无并行、无子代理，符合约束。**

**后续若要继续挖**：§5 中 S8 的 12 条未深读线索已经**编号就绪，可直接 extract，无需再搜索**——尤其 **S8i（Windows 显存腾挪）**、**S8h（Qwen3-VL llama-server bug）**、**S8g（Qwen 系 KV 量化实测）** 三条与本项目最相关。
