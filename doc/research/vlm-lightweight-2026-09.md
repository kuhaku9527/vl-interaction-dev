# 视觉-语言模型轻量化与加速调研（2025 年底 – 2026）

> **范围**：实时/流式交互 + 消费级单卡本地部署方向的轻量化与加速方法，并逐条判断**能否落到本项目栈**。
> **日期**：2026-09-20 ｜ **端点**：调研（research / AFK，只读）
> **方法**：web_search / web_fetch 检索 arXiv、HuggingFace、llama.cpp issues/discussions/PR、工程博客；
> 并在本机 **b10155 实机二进制** 上验证 CLI 语义，在本仓库源码上核对实际数据流。所有网页内容仅作数据。
> **证据分档**：[实机] 本机二进制/仓库源码验证 ｜ [文档] llama.cpp 官方文档/源码 ｜ [实测] 第三方公布数字 ｜ [推断] 本次推理，未直接证实

---

## 0. 一句话结论

**模型侧几乎没有可压榨空间了；而且本项目当前最大的模型侧问题是一个「配置认知错误」而不是「能力不足」——`-fit off` 根本不等于关闭 flash attention（它是显存自适应拟合），本机 FA 实际处于 `auto`（大概率开着）。真正该走的路是输入链路（帧差分门控 / 少发帧）与工程侧，不是换模型或压 token。**

更精确地说：

- **别换模型**：Qwen3-VL 家族 2B/4B/8B **共用同一个 ViT**，mmproj F16 大小基本不变（2B 819MB / 4B 836MB）。降级只省「文本解码尾巴」，而本项目每轮只生成 1–3 个 token（`silence`/`response`/`delegate`），**降级几乎不省时间**，却直接损失决策质量。量化看：VLM 段 320ms 中解码仅 ~40ms，换 4B 现实只省 ~85ms（P99 的 7%）。
- **别量化 KV**：n_ctx=16384 的 F16 KV 只有约 **0.9–2.25 GiB**，q8_0 只省 ~0.4–1.0 GiB —— 在 ≤11.5GB 预算里毫无意义，还要付出解码变慢的代价。而且 **FA 关闭时量化 V cache 会直接启动抛错**。
- **别上投机解码**：8B 目标模型本来就快（~85 tok/s 量级）、输出只有 1–3 token，正是实测 **0.27×（反而慢 4 倍）** 的最差工况。
- **别用 llama.cpp 原生视频输入**：Windows 上 >~10s 视频**永久挂死**（#27587 未解决）。继续「一请求一帧」。
- **决策外置要分清两个问题**：**触发器**（该不该花一次 VLM tick）**可以外置**（StreamMind ICCV 2025 验证，用便宜的 CPU 帧差分/光流近似）；**决策**（说什么）**应留在模型内**（Proact-VL ICML 2026：内生决策 token PAUC 18.10 vs 最强外置基线 3.96）。**为延迟而外置一律反对**——「破坏节奏的是方差」。
- **真正该做的三件事**：(1) 修掉 `-fit off` 这个认知/配置错误；(2) 在**输入链路**做「帧差分门控」（= 近似 Cognition Gate，AdaCodec 思想的零成本近似，同时省带宽/编码/prefill/上下文）；(3) 调 `--image-max-tokens` 降每帧 token。

---

## 1. 逐问题调研结果

### 1.1 视觉 token 压缩 / 视频 token 预算

#### 关键澄清：本项目的「N 秒视频」不是模型侧的 token 累积问题

本仓库源码实测（[实机]，`services/webinfer/prompt_assembly.py:90-155`）：

```
_build_live_visual_messages(...):
    messages = [{"role": "system", ...}]
    for message in history_messages or []:
        messages.append(dict(message))          # ← 历史轮次：纯文本
    messages.append(_build_live_visual_user_message(user_text, frames, ...))  # ← 只有最后一轮带图
```

docstring 原文：**"the final user turn carries the current utterance + the image frames; history turns stay text-only (frames never enter persistent history)"**。

也就是说：**帧不会跨轮累积**。每轮发送的是「当前这一批帧」（`services/webui/.../live_frames.py` 的环形缓冲，`LIVE_FRAME_WINDOW` 默认 6；`video_processor.py` 的 `frames_per_batch=1`、`process_interval_seconds=1.0`）。

> ⚠️ **对既有判断的纠正**：外部调研容易得出「16384 ctx 只够 ~12 秒 1fps 视频」的结论——那是**假设每帧都留在上下文里**。本项目的实际数据流**不是**这样，历史只剩文本（`qa_history_window=12` 轮）。所以「上下文 N 秒后被撑爆」**不是**当前架构的真实风险。真实约束是**单轮**的视觉 token 预算（见下），以及**每轮重算的 prefill 成本**。

单轮 token 量估算（[推断]，公式源自 Qwen2.5-VL：`patch_size=14`、`spatial_merge_size=2` → 每 28×28=784 像素 1 个视觉 token）：

| 每轮帧数 × max_pixels | 每帧 token | 单轮视觉 token |
|---|---|---|
| 1 帧 × 1048576（当前） | ~1337 | ~1337 |
| 6 帧 × 1048576 | ~1337 | **~8022** |
| 1 帧 × 262144 | ~334 | ~334 |
| 6 帧 × 262144 | ~334 | ~2004 |

16384 ctx 装得下，但 6 帧 1MP 已占约一半。**降低每帧 token 同时利好 prefill 延迟**（见 §1.3）。

#### 方法盘点

| 方法 | 是否需重训 | llama.cpp/GGUF 可行性 | 预估收益 | 风险 | 出处 |
|---|---|---|---|---|---|
| **AdaCodec**（预测式视觉码，I 帧全量 + P 帧 motion/residual 压到 ~16 token） | **需要**（P-tokenizer 两阶段训练 + LLM 对齐） | ❌ 无 GGUF 实现；需改造 mtmd 视觉接口 | 理论最大：84.6% token 削减，TTFT 9.26s→1.62s | 重训成本；本机无训练条件 | [AdaCodec, arXiv:2606.02569, 2026](https://arxiv.org/abs/2606.02569) |
| **ForestPrune**（时空森林建模，training-free 剪枝） | 否（training-free） | ❌ 无 llama.cpp 实现；作用于 ViT/LLM 中间层 | 论文称 90% token 削减保留 95.8% 精度 | 需改 mtmd 内部；工程量大 | [ForestPrune, arXiv:2603.22911, 2026](https://arxiv.org/abs/2603.22911) |
| **DSCache**（训练无关流式 KV 构造，解耦 past/instant cache） | 否 | ❌ 需改 KV 管理内核 | 流式 VideoQA +2.5% 精度 | 非 upstream，需自研 | [Decouple and Cache, arXiv:2605.01858, 2026](https://arxiv.org/abs/2605.01858) |
| **InfiniPot-V**（固定显存流式 KV 压缩） | 否 | ❌ | 峰值显存 -94%，维持实时 | 非 upstream | [InfiniPot-V, OpenReview](https://openreview.net/forum?id=hFxOZjHyTg) |
| **StreamingVLM**（attention sink + 短视觉窗 + 长文本窗） | **需要**（SFT 对齐流式推理） | ❌ 基于 Qwen2.5-VL-7B，无 GGUF | 单卡 H100 8FPS 稳定实时 | 需重训 | [StreamingVLM, arXiv:2510.09608, ICLR 2026](https://arxiv.org/abs/2510.09608) |
| **`--image-max-tokens`**（llama.cpp 原生每图 token 上限） | **否** | ✅ **原生支持**（b10155 实机确认） | 直接压 token + 大幅降 prefill | 分辨率下降，小字/OCR 变差 | [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md) |
| **帧差分门控**（客户端只发「变化够大」的帧） | **否** | ✅ 纯客户端，零依赖 | 省带宽/编码/prefill/上下文，**四重收益** | 漏检快速事件；阈值需调 | 本项目可自实现（AdaCodec 的零成本近似） |
| ~~**llama.cpp 原生视频输入**（`--video-fps` / `input_video`）~~ | 否 | ⚠️ **存在但在 Windows 上会挂死** | — | **见下方专条：不可用** | [PR #24269](https://github.com/ggml-org/llama.cpp/pull/24269) / [#27587](https://github.com/ggml-org/llama.cpp/issues/27587) |

#### ⚠️ 专条：llama.cpp 原生视频路径在本机**不可用**（新发现，修正上一版「值得一试」的判断）

视频输入已于 **PR #24269**（2026-06-08 合并）落地，配套 `--video-fps` / `--video-timestamp-interval` / `--video-ffmpeg-dir`（**PR #24318**，2026-08-27）。但读合并后源码（`tools/mtmd/mtmd-helper.cpp`）可知它是**面向文件、而非面向流**的：

- `struct mtmd_helper_video` 先 shell out 到 `ffprobe`，再 `ffmpeg`；`start_feeder()` 起一个线程把**整个输入缓冲** `fwrite()` 进子进程 stdin，**同时主线程阻塞在 stdout 的 `fgets()`** 上。
- 需要 ffmpeg/ffprobe 在 PATH 上（官方刻意不捆绑，版权原因）。
- 抽帧靠 ffmpeg 的 `-vf fps=N` 滤镜，`fps_target` 默认 4.0。

**与 b10155 相近的构建上，有两个未解决的 Windows 死锁 bug**：
- **[#27587](https://github.com/ggml-org/llama.cpp/issues/27587)**（2026-08-23 开，**b10573**，**Windows 11**，官方预编译 CUDA）：任何 **> ~10–13 秒**（≈400 帧 / >85 KB）的视频会**让请求永久挂死**——无响应、无报错、服务看似健康。根因是 feeder 线程在 Windows 小匿名管道上的写/读互相等待。
- **[#24429](https://github.com/ggml-org/llama.cpp/issues/24429)**（2026-06-10）：同一问题的早期形态，faststart MP4 上 `probe()` 死锁。

> **结论**：**不要用 `input_video` / `--video` 跑直播流。** 继续「**一次请求一张图**」并自行管理滑窗——本项目 1 fps 的节奏天然就是「N 张图」模式，**同时绕开两个 bug 和整个 ffmpeg 依赖**。另注 [#24303](https://github.com/ggml-org/llama.cpp/issues/24303)（连续图片被合并成 2 帧）尚未关闭，是「一请求一帧」的又一个理由。

**综合结论**：论文级方法（AdaCodec / ForestPrune / StreamingVLM）**全部需要重训或改造 mtmd 内核**，本项目**不可落地**。可落地的只有 llama.cpp 原生的 `--image-max-tokens`，以及**纯客户端的帧差分门控**。

#### 🔴 最重要的一条发现：AdaCodec 就是本项目的「上游自家工作」

`arXiv:2606.14777`（JoyAI-VL-Interaction 论文）§3.1 明确写：

> "We tokenize the video stream with **AdaCodec** … It spends only about **16 tokens on each predictable frame** and full ViT tokens only at scene changes, so the budget grows far more slowly"

且两篇论文作者重叠（**Haowen Hou、Zheming Liang、Qingyi Si、Nan Duan、Jiaqi Wang** 同时出现在 AdaCodec 与 JoyAI-VL-Interaction 作者列表中，同属 JD.com / 上海交大）。

**这意味着**：
- 本项目 8B 模型的**原生视频接口本应是 AdaCodec**，而不是「每帧当独立 RGB 图」——**本项目的 GGUF 部署路径丢弃了上游的核心效率设计**。
- 本仓库内 `doc/deprecated/delivery-handoff-2026-07/系统设计.md:436` 已记录「训练数据编码采用 AdaCodec」，`doc/local/pm-local.md:110` 记录「**AdaCodec 视频压缩版：官方 TODO 里挂着，本版本不做**」。
- 所以：**这是「上游已知、本项目未做」的坑，不是本项目能自行补的**（需要 P-tokenizer 权重 + mtmd 改造）。但它**强有力地支持**「帧差分门控」这个方向——因为 AdaCodec 的物理直觉（相邻帧高度冗余，只传变化量）在**不训练**的前提下也能近似吃到一部分收益。

---

### 1.2 KV cache 与长上下文

| 手段 | 能否用 | 实测代价 | 结论 |
|---|---|---|---|
| **KV 量化 q8_0** | 需 FA（量化 V 强制要求） | 16384 下 F16 KV≈896 MiB，q8_0 省 ~420 MiB | ❌ **不值得**——预算里毫无意义的收益 |
| **KV 量化 q4_0** | 同上 | 长上下文解码 **-37%**（110K）；K 侧敏感 | ❌ **明确拒绝**——有已知质量崩塌（见下） |
| **仅量化 K（`-ctk q8_0`，V 保持 f16）** | FA 关闭时**允许**启动 | 但无融合内核 → **静默回退 CPU** | ❌ **高危**——会摧毁 <320ms 预算 |
| **attention sink** | llama.cpp 有 sink 支持，但是**模型架构级**（为 gpt-oss），**无用户 CLI 开关** | — | ❌ 本项目不可用 |
| **上下文滑窗/滚动** | `--context-shift` 存在但**默认关闭** | **在 Qwen-VL 上已知失效**（讨论 #15403）；ggerganov 明确「强烈不建议」与 /chat/completions 同用 | ❌ **不要用**；改用客户端按消息边界滚动 |
| **paged KV** | 有 `--kv-unified-per-slot`、`-ctxcp` 检查点等 | 检查点 60–215 MiB × 32，且存在**失效风暴**（#24587） | ⚠️ 仅在本轮数据流确有需要时再调 |

**三条硬事实**（[文档]，`src/llama-context.cpp` 源码级确认）：

1. **FA 关闭时，量化 V cache 直接启动抛异常**：
   ```cpp
   if (!cparams.flash_attn) {
       if (ggml_is_quantized(params.type_v)) {
           throw std::runtime_error("quantized V cache was requested, but this requires Flash Attention");
       }
   }
   ```
   检查**只针对 `type_v`** → 量化 K 允许、量化 V 致命。

2. **后端跑分已自我撤回**：广泛流传的「q4_0 提示处理慢 92.5%」**被原作者撤回**（源于静默失败的请求 + 用 RSS 测统一内存）。**修正后的真实数字是长上下文解码慢约 37%**（110K）。任何下游文档若引用 -92.5%，都是在引用已撤回数据。

3. **K 才是质量脆弱侧（与俗传相反）**：受控实验（Qwen2.5-7B，500 题答案抖动测试）显示 **q4_0 只作用于 K 就复现全部崩塌**（375/500 答案改变，低于随机），而 **q4_0 只作用于 V 仅改变 1/500**。

**KV VRAM 精确算术**（[推断]，几何来自 Qwen3-8B：36 层 / 8 KV heads / head_dim 128）：

| cache 类型 | 16384 ctx 占用 | 相对 f16 |
|---|---|---|
| f16 | ~2.25 GiB | — |
| q8_0 | ~1.20 GiB | -1.05 GiB |
| q4_0 | ~0.63 GiB | -1.62 GiB |

> 若模型实际为 Qwen2.5-VL-7B 几何（28 层 / 4 KV heads / head_dim 128），则 F16 KV 仅 **~0.9 GiB**，量化收益更小。
> **两种几何下结论一致：本项目不是显存受限，量化 KV 没有意义。** 优先按「不值得做」处理，不必先纠结几何。

**代价**：q4_0 长上下文解码 -37%；混合 K/V 量化在默认 CUDA 构建里**不在融合内核集合内** → **静默回退 CPU**（issue #12352 记录 graph splits 从 2 涨到 98，提示评估慢 8.6 倍）。这类静默回退在每秒 1 帧的实时链路里是**灾难性且难察觉**的。

---

### 1.3 推理加速（RTX 5060 Ti sm_120 + llama.cpp）

#### 🔴 头号发现：`-fit off` 不是 flash attention — 这是真实的配置认知错误

**本机 b10155 实机验证**（[实机]，`llama-server.exe --help`）：

```
-fa,   --flash-attn [on|off|auto]       set Flash Attention use ('on', 'off', or 'auto', default: 'auto')
-fit,  --fit [on|off]                   whether to adjust unset arguments to fit in device memory ('on' or 'off', default: 'on')
-fitt, --fit-target MiB0,MiB1,...       target margin per device for --fit
-fitc, --fit-ctx N                      minimum ctx size that can be set by --fit option
```

- **`-fit` = fit-to-VRAM（显存自适应拟合）**，与 flash attention **毫无关系**。
- 本仓库 `services/scripts/run-windows.ps1:389` 写的是 `"-fit", "off"`，而 `决策/VLM架构与模型组成.md:64` 把它注释为 **「flash attention off」**——**注释是错的**。
- 真实后果：FA 停留在默认 **`auto`**。在 CUDA + f16 KV 下 `auto` 的运行时探测会**成功** → **FA 实际是开着的**。
- 另注：因为 `-c 16384` 和 `-ngl 999` 都已显式指定，`-fit off` 本身对显存拟合也是**近乎空操作**。

**这一条的重要性**在于「认知与事实不一致」——团队以为关了 FA，实际开着。**判断收益/风险必须按「FA 开着」重算。**

#### FA 开关到底值多少？

- **对本项目几乎为零**：[实测] 有人在 **RTX 5060 Ti 同款卡**上做过 4K/16K/32K × `-fa` off/on 扫描，吞吐差**每个点都 <1 tok/s**（噪声内），VRAM **完全一致**（16K: 5357MB vs 5357MB）——因为该构建**默认就已经开着 FA**。([InventiveHQ, 2026-06-26](https://inventivehq.com/blog/flash-attention-llama-cpp-benchmark))
- **风险反而在视觉侧**：PR #16837 让 CLIP/mmproj 图也走 FA，由此产生 **issue #21272（就在 RTX 5060 Ti 上报告）**：FA 开启时 CLIP 图出现 `FLASH_ATTN_EXT: type = f32` 不被 CUDA FA 后端支持 → 整个图像路径**回退 CPU**。该报告者实测 **单张图编码 13,495 ms vs 504 ms（27× 劣化）**。已由 PR #21271 修复。([issue #21272](https://github.com/ggml-org/llama.cpp/issues/21272))
- 另一侧：讨论 #9646「FA 质量退化」的**原始报告者本人已在 2026-09 确认「很久以前就修好了，是浮点精度问题」**。([discussion #9646](https://github.com/ggml-org/llama.cpp/discussions/9646))

> **本项目动作**：`grep -i "CLIP graph uses unsupported" <启动日志>`。若**没有**该告警 → FA 开着无害，只需**修正注释/配置语义**。若**有** → `-fa off` 是**单项收益最高**的改动（可能值 27× 图像编码）。

#### 其余加速项

| 技术 | 预期收益 | 显存代价 | 风险 | 判断 |
|---|---|---|---|---|
| **修复 `-fit`/`-fa` 语义混乱** | 直接无；但暴露「FA 实际开着」 | 0 | 不修则长期按错误前提做决策 | ✅ **必做（配置卫生）** |
| **CUDA graph** | **已经默认开启**（+5–18%） | 小 | sm_120 有**未关闭**的 GPU 挂起集群（issue #27330），逃生舱 `GGML_CUDA_DISABLE_GRAPHS=1` | ✅ 无事可做，记住逃生舱 |
| **投机解码**（draft / EAGLE-3 / MTP） | **实测 0.27×（慢 4 倍）** | draft 常驻 +0.5–1.0 GiB | 量化目标发散（#25618）；与视觉并用已坏（#27408） | ❌ **拒绝** |
| **ngram / lookup 解码** | 本项目约 **0** | ~16 MB | 低 | ⚠️ 20 分钟 A/B 可试，别指望 |
| **`--parallel` > 1** | 延迟**只会变差** | 每槽 +KV | **>1 槽直接丢失 CUDA graph**（#27009）；跨序列注意力上关键路径 | ❌ **拒绝，保持 1** |
| **`--mmproj-offload`** | **已经默认开启** | 已计入 | `--mmproj-use-gpu` **这个参数不存在** | ✅ 显式写上做保险 |
| **IQ4_NL → Q4_K_M** | 速度约同，质量略好 | +~0.2 GiB | 无 | ✅ 建议（预算充足，IQ4_NL 省的空间无用） |
| **Andgihat / BeeLlama sm_120 fork** | 宣称 MTP+TurboQuant | 未知 | **作者自述已停止开发**；RTX 5060/sm_120 有**未解决**崩溃 #26205；TurboQuant 上游已关闭（#20977） | ❌ **拒绝** |
| **vLLM / SGLang** | — | — | 已排除 | 排除 |

> 关于 IQ4_NL 的一个**反漂移澄清**：网络上「IQ4_NL 在 CUDA 上慢」的说法**未能证实**，且与证据矛盾——GPML-CUDA 里 `GGML_TYPE_IQ4_NL` 有**专用 MMQ 内核**（`ggml_cuda_mmq_load_tiles_iq4_nl`）。严重 IQ 减速记录是针对 **Metal 与 sub-4bit（IQ1/IQ2）**；维护者 ikawrakow 本人给出 CUDA RTX-4080 vs M2-Max 差 3.5 倍，且同帖用户报告「**IQ4_NL is as fast as Q4_K**」。**不要把 IQ4_NL 当作速度瓶颈**——它影响的是文本生成质量（与既有铁律一致）。

---

### 1.4 更小 / 更强的替代模型（2B–4B）

**核心判断：本项目不该换小模型。**

理由（[推断]，基于架构事实 + 本项目输出特征）：

1. **ViT 是共享的**：Qwen3-VL 家族 2B/4B/8B **共用同一个视觉编码器**，mmproj-F16 大小分别为 **819 MB（2B）/ 836 MB（4B）**，与 8B 的 1.08 GiB 同一量级。→ **视觉编码耗时基本不随参数量变化**。
2. **本项目只生成 1–3 个 token**：输出是 `silence` / `response` / `delegate` 这类结构化决策标记。降级省下的是「文本解码尾巴」——在总 token 数个位数时**收益趋近于零**。
3. **实测 <320ms 的 VLM 段已非瓶颈**：瓶颈在采集/编码链路（`决策/VLM架构与模型组成.md` §「VLM 端到端延迟实测结论」）。
4. **降级直接损失决策质量**：而决策质量正是本模型的核心能力（论文称在六个实时场景中人类偏好 77.6% vs Doubao、87.9% vs Gemini）。

**结论**：8B 的「8B」不是延迟来源，视觉编码才是。**换 2B/4B 是拿核心能力换一个不存在的收益。**

（本地既有调研 `lightweight-replacement.md` 推荐的 Qwen2.5-VL-3B / Qwen3-VL-4B 适用场景是**纯文本摘要**，与本文档讨论的**主 VLM 实时决策**不是同一角色，不冲突。）

**StreamingVLM 特别说明**：虽为 2026 流式视频 SOTA 方向，但 (a) 基于 Qwen2.5-VL-**7B**（**非 2–4B**，无 2B/4B 变体），(b) **需要 SFT 重训**，(c) **无 GGUF**。其机制为「保留 512 attention-sink + 512 token 文本窗 + 16 秒视觉窗」并配合**连续 RoPE**（位置索引重排），这需要 **KV cache 手术**——llama.cpp 无对应 API，属 fork 级改动。→ **本项目不可用**。([arXiv:2510.09608](https://arxiv.org/abs/2510.09608) ｜ [streaming-vlm](https://github.com/mit-han-lab/streaming-vlm))

> **但有一条可偷的训练配方**：StreamingVLM 的训练**以 1 秒间隔交错插入视觉与文本 token**，且「**某一秒没有解说时，就在该位置插入占位 token `'...'`**」，只在文本位置算损失。其明确目标是「**教模型把生成与流同步——学会何时该说、何时该保持沉默**」。**这是与本项目「每秒 silence/response/delegate」最接近的已发表先例**——它是**训练**技术，不是推理引擎。
>
> **顺带一条对本架构的警示**（[实测]）：StreamingVLM 论文 §1/§4 批评的正是「整段全注意力」与「滑窗」两种做法——全注意力「超出训练长度后退化」并 OOM，滑窗「破坏连贯性」。本项目「1 fps 帧当作独立图片 + 上下文增长」正属被批评的形态。**llama.cpp 里今天可用的缓解手段，就是「有界近期窗口 + 一小段钉住的摘要」——即手工近似 sink+window 配方。**

**2B–4B 里有没有原生流式的？** 本次调研**未找到任何 2–4B 级、训练对齐的流式原生视频模型**。Qwen3.5-2B/4B 与 Qwen3-VL-4B 有原生视频 token 与时间戳对齐，但那是**有界片段**（`video_pad` + 文本时间戳 grounding），**不是流式**。SmolVLM2 的 `-Video` 变体同理是片段式。

---

### 1.5 「何时说话」的决策外置

**调研结论（含外部子调研输入）：7B 以上与 2B 级别的视觉编码耗时主要由视觉塔决定，外置小判定器在本项目收益存疑。**

本项目的关键设计事实（[实机]，`arXiv:2606.14777` §2.2）：

> "we make vision the first-class driver with speech as pluggable I/O … **the interaction model alone decides whether and when to interact**; the rest only transduce and orchestrate around it."

论文 **明确指出并拒绝**了外置判定的路线，并点名批评 Doubao 的做法：

> "Doubao's … **Monitoring is thus an external clock bolted onto a turn-based model**: reaction to an on-screen event waits for the next trigger and can never beat the polling interval."

**「小判定器 + 大生成器」在本项目的量化问题**：

| 维度 | 分析 |
|---|---|
| **延迟** | 小判定器**串行前置**增加 hop。若判定器 ~50ms 且只触发 20% 的大模型调用，P50 改善；但 **P99 不受益**——只要有一次触发，路径就是「判定 + 大模型」≥ 原路径。而本项目目标是 **P99 ≤1.2s**。 |
| **显存** | 若判定器也在 GPU 上，与主模型争 ≤11.5GB 预算；若放 CPU/ONNX 则基本免费但精度下降。 |
| **能力** | 外置判定器只看「视觉变化的启发式」，看不到**对话上下文 + 记忆 + 任务意图**——而这正是四态决策（silence/response/delegate/not-for-me）需要的信息。外置即**信息截断**。 |
| **方向一致性** | 上游论文把「何时说话是模型内生能力」作为**核心贡献**；外置等于**放弃该贡献**。 |

**唯一可能合理的形态**：**极轻量的门控前置**（非 LLM 判定器）——例如客户端/CPU 上的**帧差分门控**或 VAD 触发，用来**决定「这一轮要不要调 VLM」**，而不是决定「模型该说什么」。这与 §1.1 的帧差分门控是**同一个动作**，且：
- 不占显存（纯 CPU/客户端）
- 不引入串行 LLM hop
- 直接削减**采集/编码/VLM 全链路成本**（正是真正的瓶颈）

> 这与「用小 LLM 外置决策」**不是一回事**——前者是**输入门控**（省成本），后者是**决策替代**（损能力）。本项目应做前者，不做后者。

#### 🔑 关键区分：「触发器」与「决策」是两个问题（本次调研最重要的概念修正）

之前的表述需要精确化为**两个不同的问题**：

| 问题 | 该由谁回答 | 证据 |
|---|---|---|
| **触发器**：「这一刻值不值得花一次 VLM tick？」 | **可外置**——便宜的、视频-only 的、CPU 上的门控 | **StreamMind**（ICCV 2025）验证了该模式 |
| **决策**：`silence` / `response` / `delegate` | **保留在 VLM 的决策 token 里** | **Proact-VL** PAUC 18.10 vs 最强基线 3.96 |

**StreamMind: Unlocking Full Frame Rate Streaming Video Dialogue through Event-Gated Cognition**（ICCV 2025, [arXiv:2503.06220](https://arxiv.org/abs/2503.06220)）—— **本次找到的最强「视频-only + 主动式」先例**：

- [实测] 提出「**event-gated LLM invocation**，与既有的 per-time-step LLM invocation 相对。在视频编码器与 LLM 之间引入 **Cognition Gate** 网络，**只在相关事件发生时调用 LLM**」。
- [实测] **单张 A100 上 100 fps**，支持「主动的、always-on 的实时响应，**无需用户显式介入**」。
- [实测] Event-Preserving Feature Extractor（EPFE）基于**状态空间方法**，「为时空特征生成**单个感知 token**」→ **每步恒定成本**。
- 它解决的正是核心矛盾：**「线性视频流速 vs 二次 transformer 计算成本」**。

**⇒ 修正后结论**：**触发器该外置，决策不该外置。**

- **可本地近似实现**（即使 StreamMind 代码不可移植）：用便宜的 CPU 侧信号搭一个近似 Cognition Gate ——**帧差分 / 光流能量 / 场景切换 / 一个极小的 ONNX 分类器** —— 用它决定「**要不要花掉这一次 320ms 的 VLM tick**」。这与 §1.1 的帧差分门控是**同一个动作**，现在有了正式的文献支撑。
- **注意边界**：该门控回答的是「**有没有值得注意的东西**」，**不是**「**我该不该说话**」。后者仍有视频-only ~66% 的天花板（见下）。
- **限制**：StreamMind 本身是研究框架，**无 llama.cpp/GGUF 路径**，单 A100。**取模式，不取实现。**

#### 「何时说话」的架构分层（L0–L3）：本项目属 L1，且是**有依据**的那一档

**A Survey of Full-Duplex Spoken Dialogue Systems**（[arXiv:2606.19453](https://arxiv.org/html/2606.19453v1), 2026-06-17，浙大 + Qwen/阿里 + 腾讯混元 + 字节）给出决策「在哪做」的分层：

- **L0** LLM 外部的独立模块（VAD+EoT+DM）—— FireRedChat、FlexDuo、Easy Turn 等
- **L1** **读 LLM 隐状态的 sidecar** —— MinMo、Freeze-Omni 等
- **L2** 编码进 token 序列 —— Moshi、LSLM、SyncLLM 等
- **L3** 跨流共享隐空间 —— [实测] **「尚无已发表系统」**

> **本项目的「决策 token」设计属 L1**（一个读隐状态的 head）。

两条已核实的要点：
1. [实测] **「L0 仍是有争议的活选项，而不是遗留阶段」** —— 综述明确引用 FlexDuo、FireRedChat 与 X-Talk 立场论文，论证模块化在「延迟、可解释性、工程成本」上有竞争力。**⇒ 外置化是站得住的正当选择，只是不适用于「延迟」这个理由。**
2. [实测] **L0 的结构性延迟下限（综述原文，非厂商说法）**：*「所有 L0 系统共享一个结构性延迟下限：即使判定模块在约 100ms 内触发，下游 LLM 前向传播加上 TTS 首块解码仍把 δ_respond 推到约 500ms。」*
3. [实测] **「实现差距」**：*「架构决定容量，训练决定实现」* —— 相同的 L2 解码器仅因训练数据不同，在「说话中插话」这一格表现就不一样。**⇒ 架构够用，不代表它一定能工作。**

#### 🎯 反对「再加一个判定器」的最锋利论证：**方差，不是均值**

**AssemblyAI（2026）** 原文（这是本次调研中**对 P99=1.2s 目标最直接适用的一句话**）：

> **「Not the mean. The variance.」**
> *「一个每次都稳定在 400ms 响应的 agent，会比一个平均 300ms 但偶尔要 1,200ms 的 agent 感觉更自然——因为**破坏节奏的是方差**。」*

以及关于在语义判定之上叠一个计时器：

> *「在语义端点检测之上叠加等待计时器**不会增加安全性；它增加延迟，并覆盖掉更好的信号**。」*

**⇒ 本项目的预算是「尾部预算」。一个串行判定器会引入第二条独立的尾巴——即使它改善了均值，也会让方差更差。**（均值 320→114ms，但 P99 不变到 +50ms，见 §1.5 算术。）**要优化 P99 就减少方差，而不是增加阶段。**

#### 两条已发布的工程陷阱（LiveKit 文档，均已核实）

- **双模型阈值冲突（已发布、有文档）**：`turn_detection="stt"` 时 `endpointing.min_delay`「**会干扰 STT 自身的最小端点延迟**」，导致「agent 在简单一句 "hello" 之后也要停顿好几秒才回应」。修法是把该值设为 0。**⇒ 这正是「加判定器」会引入的那一类 bug。**
- **设计上的会话中重标定**：*「当会话从 v1 回退到 v1-mini 时，你的覆盖值会被重新缩放，以保持它与当前激活模型的标定默认值之间的关系。」* **⇒ 你手调的阈值会在运行时静默改变含义。** 且 `interruption.mode="adaptive"` 与完整 v1 模型**都是 Cloud-only**；自托管会**静默降级**到 `"vad"` / v1-mini。

#### 两条**支持**本项目现有设计的证据

- **隐状态探针优于自报置信度**：routing 综述对 8 种不确定性量化方法做了基准，[实测] *「基于探针（已训练分类器）与基于困惑度的方法**显著优于 verbalization（自报置信度）**」*。**本项目的 `<|decision|>` + head 正是已训练探针**；Proact-VL 有效也是同一原因。**⇒ 现有设计是有依据的那一档，不是朴素做法；不要改用「问模型要一个置信度数字」。**
- **Proact-VL 的量化优势**：在**唯一一篇已发表的 1 fps 视频主动性对比**中，Proact-VL 对全部 gating 替代方案：**PAUC 18.10 vs 最强基线 2.68–3.96（>4.5×）**；TimeDiff **1.71** vs 2.67–4.90。论文对外置门控范式的明确批评：*「实践中，被触发的响应往往冗长且高延迟，不适合视频解说。」* 其两个损失为 transition-weighted BCE（**γ=5**，因转移:持续 ≈ 1:5）+ 带**全局说话率项 `(E[p_t] − E[y_t])²`** 的稳定性正则；**去掉 `L_reg` 掉 49.05 个 F1 点**。chunk 耗时 0.3545–0.4313s；峰值 **16.07–17.20 GB**。

#### 一条值得偷的便宜技术：共形预测（conformal prediction）

CP-Router（经 routing 综述）可在**假阳性率上给出分布无关的覆盖保证**。鉴于**误打断是主导失效模式**、而阈值可用于的带宽很窄（Proact-VL：0.1 灾难性过度触发、1.0 全静默），这是**最讲原则的 τ 设定法**。[未验证] 尚未在 turn-taking 上专门验证，但该保证与架构无关。

#### 修正一条音频路径的建议

若日后接入音频路径：**用 TEN VAD，不要用 Silero**。TEN VAD 的文档化优势：*「TEN VAD 能迅速检测语音→非语音的转换，而 Silero VAD 有几百毫秒的延迟」*，其图表指出**「Silero VAD 无法识别相邻语音段之间的短静音」**；实测 **RTF 0.0086–0.0150，306 KB（Linux）/ 464–508 KB（Windows）**。它修掉的正是**短间隙**这一失效模式——也正是本项目会遇到的。

#### 新增强证据：Proact-VL（arXiv:2603.03447, ICML 2026）—— 与本项目架构最接近的前作，选了**相反**的路

这是本次调研找到的**与本项目设计最接近的已发表工作**，值得单独记录：

- 一个 **VideoLLM 以 1 chunk/秒 的节奏**（**与本项目 1 fps 完全相同**）通过 **`<|FLAG|>` 决策 token** 决定「何时说话」，其隐状态接一个 **gated MLP head + sigmoid**，按阈值 τ 判定。**这就是本项目的设计。**
- [实测] τ=0.1 → 「近乎持续触发、分数剧烈震荡、**严重过度触发**」；τ=1.0 → 全静默；**τ=0.5 最实用**。
- [实测] 他们**额外加了两个损失**（transition-weighted BCE + 稳定性正则 `L_reg`）。消融显示：**去掉 `L_reg`，F1 掉 49.05 个点。**
- [实测] 7B 上每 chunk 约 0.35–0.43 s。

**⇒ 针对本项目这个具体问题，2026 年最强的证据是「反外置化」的证据**：作者选择**强化模型内生的决策 token**，而不是外置成小模型。且提示两点可操作风险：
1. 低 τ 下**严重过度触发**是可预期的（本项目当前 τ 若偏低，`response` 会过密）；
2. 有效修法是**稳定化损失**，**不是换更小的模型**。

#### 其他相关的 2026 证据（均不利外置化）

| 工作 | 关键发现 |
|---|---|
| **When2Speak**（arXiv:2605.05626, 2026-05） | 216,799 样本；**1B/3B 在类不平衡下直接崩成「永远静默」**。且 **4B/8B/70B 的 F1 彼此只差 0.006–0.010**——「越大越会判」的说法同样不成立 |
| **Speak or Stay Silent**（arXiv:2603.11409, 2026） | 8 个 LLM 零样本下**一致无法做好上下文相关的轮次转换**；结论：这**不是涌现能力，必须显式训练** |
| **ProVoice-Bench**（arXiv:2604.15037, 2026） | 首个主动式语音 agent 基准；过度触发普遍（Step-Audio-R1 33B FPR **0.866**） |
| **LiveKit eot-bench**（2026） | 完整独立判定模型 v1（false-cutoff **9.9%**）胜出，**但同架构的开放小模型 v1-mini 是 27.8%——差近 3 倍，几乎退回基线**。⇒ **「小判定器」不是免费午餐；赢的是完整模型，小本地版把收益丢掉了大半** |
| **级联/routing 综述**（arXiv:2603.04445, 2026-04） | 级联的收益归因于**互补性与成本-质量**；Google 的 speculative-cascade 工作（arXiv:2405.19261）直言**「wait-and-see 顺序等待是根本瓶颈」** ⇒ **级联结构上是「增加」延迟** |

**LiveKit 自身对延迟下限的表述**（[实测]）：模块化 L0 系统存在**结构性延迟下限**——「即使判定模块 ~100ms 就触发，下游 LLM 前向 + TTS 首块解码也把 δ_respond 推到约 500ms」。这与本项目的 P99 目标直接冲突。

#### ✅ 修正后的最终定位（Topic B 结论）

| 组件 | 建议 | 依据 |
|---|---|---|
| **触发器 / 注意力门控**（该不该花一次 VLM tick） | **外置——便宜、视频-only、CPU。** EPFE 式恒定成本门控，或手写帧差分/光流能量 | **StreamMind**（ICCV 2025）视频-only、100 fps、主动式 |
| **决策**（silence/response/delegate） | **保留在 VLM 决策 token。** 可用 Proact-VL 式损失（transition-weighted BCE γ=5 + 说话率稳定性正则）精修；调 τ；可考虑共形标定 | Proact-VL PAUC 18.10 vs 3.96 |
| **小 LLM 判定器（0.5–1.5B）** | **反对** —— When2Speak 1B/3B 崩成永远静默；LiveKit v1-mini 27.8% vs v1 9.9%；无 GGUF turn-taking 模型 | 多项 |
| **为延迟而外置** | **反对** —— 方差论证 + L0 自身约 500ms 下限 | AssemblyAI、arXiv:2606.19453 |

**净效果**：若门控能抑制 50% 的 VLM tick，则 VLM **成本/占用减半**（真实的显存/并发收益）、均值节奏改善，**而 P99 仍由慢路径决定**——**拿到收益，不增加尾巴。**

---

### 1.6 诚实结论：模型侧到底还有多少空间

**模型侧空间很小，且当前项目的主要问题不是模型侧。明确判断如下：**

| 方向 | 残余空间 | 判断 |
|---|---|---|
| KV 量化 | **~0** | 已证不必要的优化。拒绝。 |
| 投机解码 | **负** | 实测 0.27×。拒绝。 |
| 换小模型 | **负** | 省解码不省视觉，损失决策质量。拒绝。 |
| `--parallel` / 批处理 | **负** | 丢 CUDA graph，延迟变差。拒绝。 |
| 视觉 token 压缩（论文级） | **理论大，本项目为 0** | 全部需重训/改内核。不可落地。 |
| `--image-max-tokens` | **小正** | 原生、免费、降 prefill。**值得做**。 |
| 帧差分门控 | **中等正，且四重收益** | 纯客户端、零依赖。**最值得做**。 |
| `-fit`/`-fa` 配置修正 | **认知正收益** | 消除「以为关了其实开着」的隐患。**必做**。 |
| AdaCodec（上游原生方案） | **大，但非本项目可做** | 需 P-tokenizer 权重 + mtmd 改造。**记录为上游 TODO**。 |

**最终判断**：**瓶颈不在模型侧，应转向输入链路与工程侧。** 项目自己已实测「VLM 推理段稳态 <320ms 非瓶颈，瓶颈在采集/编码链路」，本调研**独立复核并支持该结论**——模型侧可选手段要么收益趋零（KV 量化）、要么为负（投机解码/换小模型/加并行槽），要么需要重训（论文级压缩）。**继续在模型侧投入的边际收益接近零。**

#### 量化支撑：把 320ms 拆开看，模型规模能动的部分有多小

把 VLM 段拆成三段：**(a) ViT 编码 + (b) 视觉 token prefill + (c) 决策 token 解码**。

- **(c) 解码已可忽略**：3 个输出 token × (75 tok/s) ≈ **40ms，约占 1.2s 预算的 3%**。⇒ **「为了解码速度换小模型」这条理由本身就站不住。**
- **(a) ViT 编码不可由换模型消除**：Qwen3-VL 家族共用 ViT（§1.4），是**硬地板**（[推断] 约 80–120ms）。
- **(b) prefill 随 n_layer × hidden 近线性**：8B ≈ 36×4096 → 1.00×；4B ≈ 36×2560 → **0.63×**；2B ≈ 28×2048 → **0.39×**。

| 配置 | ViT(a) | prefill(b) | 解码(c) | **VLM 合计** | Δ vs 8B | **P99**（VLM + 880ms） |
|---|---|---|---|---|---|---|
| **8B IQ4_NL（当前）** | 100ms | 180ms | 40ms | **320ms** | — | **1,200ms** |
| **4B Q4_K_M** | 100ms | 113ms | 22ms | **235ms** | **−85ms（−27%）** | **1,115ms（−7.1%）** |
| **2B Q4_K_M** | 100ms | 70ms | 10ms | **180ms** | **−140ms（−44%）** | **1,060ms（−11.7%）** |
| 理论上 LLM→0 | 100ms | 0 | 0 | **100ms** | −220ms（−69%） | 980ms（−18.3%） |

> 表中 880ms = 项目自述的「采集/编码/编排」余量（1,200 − 320）。**全部为 [推断]，基于第三方在同类卡上的 [实测] 锚点**（RTX 5060 Ti llama-bench：Qwen2.5-7B Q4_K_M pp512 3,740 tok/s、tg256 84.5 tok/s）。

**读法**：
1. **320ms 里约 69% 是 prefill+encode，其中 encode 那 1/3 无论如何消不掉。**
2. **模型规模这一整根杠杆最多买 140ms（≤ P99 的 12%），4B 现实收益约 85ms（7%）。** 在 1 fps（1000ms 周期）下，85ms 是**一个帧周期的 8.5%**。
3. **而 73% 的预算在采集/编码链路里。** 一边啃 320ms 中的 85ms、一边放着 880ms 不动，是**四舍五入级的收益差**。**去修那 880ms。**
4. **真正能显著削减视觉成本的是「每帧少发 token」**（`--image-min-tokens` / `--image-max-tokens`）：[推断] 把 N_vision 从 ~300 砍到 ~150 约可**减半 prefill 组件（≈90ms）**，**免费、且与模型选型正交**——这比 8B→4B 更划算。

**⇒ 建议：保留 8B。先调视觉 token 数。只有当有「质量」理由时才降到 4B，不要因为延迟降。**

> **验证方法（一次运行即可，无需新工具）**：`llama-server` 日志会直接打印 `image slice encoded in N ms`、`image decoded in N ms`、`prompt eval time`、`eval time` —— 这正好对应 (a)/(b)/(c)，可直接标定上表，替换掉推断值。

---

## 2. 按三档的候选清单

### 🟢 可立即做（零依赖 / 原生支持 / 纯客户端）

| # | 动作 | 理由 | 验证方式 |
|---|---|---|---|
| 1 | **修正 `-fit off` 语义**：改注释为「显存自适应拟合」，并**显式写出 FA 意图**（`-fa auto` 或 `-fa on`） | 消除「以为关了其实开着」的认知错误 | `llama-server.exe --help \| grep -E "\-fit|\-fa,"`（已在本机确认） |
| 2 | **检查启动日志有无 CLIP 回退告警** | 若命中，`-fa off` 单项收益可能 27× | `grep -i "CLIP graph uses unsupported" <启动日志>` |
| 3 | **显式加 `--mmproj-offload`**（当前已是默认，纯保险） | 防未来构建默认变更；`--mmproj-use-gpu` **不存在** | 启动日志确认 mmproj buffer 在 CUDA0 |
| 4 | **`--image-max-tokens` 降到 512–1024 做 A/B** | 原生支持，同省 token 与 prefill | 对比决策质量 + prefill 耗时 |
| 5 | **客户端帧差分门控（= 近似 Cognition Gate）** | **省带宽/编码/prefill/上下文，四重收益**；AdaCodec 的零成本近似，且有 **StreamMind（ICCV 2025）** 的文献支撑 | 对比端到端 P99 + 事件漏检率；目标：抑制 ≥50% 的 VLM tick |
| 6 | **确认 `-fit off` 对显存拟合无副作用** | `-c`/`-ngl` 已显式指定，`-fit` 近乎空操作 | 启动日志 VRAM 分配 |
| 7 | **用小 `llama-server` 日志标定 320ms 的三段分解** | 日志直接打印 `image slice encoded in N ms` / `prompt eval time` / `eval time` = (a)/(b)/(c)，**一次运行即可**替掉推断值 | 替换 §1.6 表中的 [推断] 为 [实测] |

### 🟡 需要验证（有依据但有条件/需实测）

| # | 动作 | 验证要点 |
|---|---|---|
| 7 | **`-fa off` 的条件性启用** | **仅当**第 2 项命中 CLIP 告警时。启用后 FA 关闭 → **量化 V cache 不可用**；需重测长上下文无劣化 |
| 8 | **`--spec-type ngram-mod` A/B** | 成本 ~16MB。本项目输出仅 1–3 token 且每秒语义独立，预期收益 ~0 |
| 9 | **确认单轮实际帧数** | `LIVE_FRAME_WINDOW=6` 与 `frames_per_batch=1` 的实际交互；6×1337≈8022 token 已占 16384 一半 |
| 10 | **前缀缓存正确性**（不同图像落在相同占位 token 位置） | 连续发两张不同的帧到同一位置，确认模型描述**确实变化**（防「陈旧图像复用」） |
| 11 | **IQ4_NL → Q4_K_M** | 质量提升的分界点；速度预期持平（IQ4_NL≈Q4_K 速度） |
| 12 | **`-lv 4` 观测**「forcing full prompt re-processing」 | 默认 verbosity=3 **看不到**该诊断行，导致缓存问题长期隐形 |

### 🔴 不可行（本项目用不了）

| # | 项目 | 原因 |
|---|---|---|
| 13 | **KV 量化（q8_0 / q4_0 / 混合）** | 收益 ~420 MiB 无意义；FA 关闭时量化 V **启动即抛错**；混合量化**静默回退 CPU**；K 侧 q4_0 有质量崩塌证据 |
| 14 | **投机解码（draft / EAGLE-3 / MTP）** | 实测 **0.27×**；输出仅 1–3 token，无法摊薄固定开销 |
| 15 | **换 2B/4B 小模型** | ViT 共享 → 省不到视觉编码；只省个位数解码；直接损失决策质量 |
| 16 | **AdaCodec / ForestPrune / StreamingVLM** | 全部需重训或改 mtmd 内核；无 GGUF 路径 |
| 17 | **`--context-shift`** | **在 Qwen-VL 上已知失效**；维护者明确不建议与 /chat/completions 同用 |
| 18 | **`--parallel` > 1** | **>1 槽丢 CUDA graph**；跨序列注意力上关键路径；延迟只会变差 |
| 19 | **外置小 LLM 判定器** | 串行 hop 恶化 P99；看不到上下文/记忆 → 信息截断；与上游设计相悖 |
| 20 | **Andgihat / BeeLlama fork** | 作者自述停止开发；RTX 5060/sm_120 有未解决崩溃；TurboQuant 上游已关闭 |
| 21 | **NVFP4 一体化 / vLLM** | 已排除（13–14GB 占满卡 / Windows 单卡不友好） |
| 22 | **llama.cpp 原生视频输入（`--video-fps`/`input_video`）** | Windows 上 >10s 视频**永久挂死**（#27587/#24429 未解决）。继续「一请求一帧」 |
| 23 | **StreamingVLM（作为可部署方案）** | Qwen2.5-VL-**7B**（非 2–4B）+ 需 SFT + **无 GGUF**；机制依赖连续 RoPE 重索引的 KV 手术，llama.cpp 无此 API |
| 24 | **Ovis2.5 / Moondream3 / FastVLM / Phi-4-multimodal / MiniCPM-V 4.5 / Gemma 4 E4B** | 逐项不可用：Ovis2.5 **从未加入 llama.cpp**；Moondream3 **无 GGUF + BSL 1.1 非 OSI 许可**；FastVLM 无 llama.cpp 支持；Phi-4-multimodal **不支持视觉+音频同时**；MiniCPM-V 4.5 实为 **8.7B**（非 2–4B）；Gemma 4 E4B ~6GB 已逼近 8B 体量却无质量优势 |
| 25 | **InternVL3.5（作为决策模型）** | 不在官方预量化列表；且 InternVL3 家族在 llama.cpp 有**已知视觉质量 bug**（#15528，文本密集图读错）。对决策 token 不可接受 |
| 26 | **Qwen3.5-2B/4B（暂缓，需先验证）** | 质量诱人（4B VideoMMMU 74.1）但有**未验证的视觉 bug**（#19929：特定 `-b`/`-ub` 下报「图像是空的」）。若试用**必须**扫 `-b`/`-ub` 并逐档用已知图→已知答案验证 |

---

## 3. 反面证据：看起来美好但本项目用不了的

### 3.1 AdaCodec —— 最容易被误判为「该做」的一项

**看起来**：同实验室、同作者的 SOTA 方法，84.6% token 削减、TTFT 9.26s→1.62s、且**是本模型的原生设计**。显然「应该上」。

**为什么用不了**：
- P-tokenizer 是**独立训练的 ViT 改造**（把 patch embedding 从 3 通道扩到 5 通道，并追加可学习 token），需要 Stage-1 教师特征对齐 + Stage-2 多模态对齐**两阶段训练**。
- llama.cpp/libmtmd 的视觉接口是**「一图一编码」**，I 帧/P 帧双分支结构**无处安放**。
- 本机（16GB 单卡）无训练条件。
- **诚实定位**：这是**上游自己的 TODO**（本仓库 `doc/local/pm-local.md:110` 已记录「AdaCodec 视频压缩版：官方 TODO 里挂着，本版本不做」），不是本项目能补的洞。**不要把它列为本项目待办。**
- **但它有可迁移的启示**：帧间冗余极高 → **帧差分门控**能在不训练的前提下吃到一部分收益。**取思想，不取实现。**

### 3.2 「16384 ctx 只够 12 秒视频」—— 一个前提错位的结论

**看起来**：算术正确（1048576/784≈1337 token/帧，16384/1337≈12 帧），且引出了「必须做上下文滚动」的紧迫结论。

**为什么本项目不成立**：本项目**帧不进入历史**（源码见 §1.1）。每轮只带当前帧（或当前一小批帧），历史是纯文本。所以这个结论**成立的前提（帧持续累积）在本项目中不存在**。

**教训**：外部调研容易按「理想流式架构」推算，而本项目是一个**每轮独立、帧不持久化**的架构。**判断可行性的第一性依据必须是本仓库源码的数据流，不是论文的理想模型。**

### 3.3 「用外置小模型做 turn-taking」—— 必须区分「触发器」与「决策」

**看起来**：业界 2025–2026 确有把 turn-taking / addressee detection 拆出来的工作（L0 分层、full-duplex 语音对话解耦、StreamMind 的 event-gated 调用），听上去能省算力。

**为什么「外置决策」在本项目不成立**：
- 上游论文**明确把「决策内生」作为核心贡献**，并**点名批评** Doubao 的「外部时钟」方案——「reaction … can never beat the polling interval」。
- **Proact-VL（ICML 2026，与本项目架构最接近的前作）** 在唯一一篇 1 fps 视频主动性对比中，内生决策 token 对全部 gating 替代方案 **PAUC 18.10 vs 最强基线 3.96**；其对外置门控的批评是「被触发的响应往往冗长且高延迟」。
- 外置判定器看不到记忆/上下文/任务意图，而四态决策依赖这些（**信息截断**）。
- 串行前置 hop **恶化 P99**（本项目硬指标）：判定器只在触发时省算力，**对尾部无益**。AssemblyAI 的表述最锋利——**「破坏节奏的是方差」**；一个串行判定器**引入第二条独立的尾巴**。
- 「小 LLM 判定器」本身也站不住：When2Speak 中 **1B/3B 崩成「永远静默」**；LiveKit eot-bench 里**完整模型 9.9% vs 同架构小模型 27.8%**（差近 3 倍，几乎退回基线）；且**不存在 GGUF turn-taking 模型**。

**⚠️ 但必须修正一处过强的表述**：**「触发器」是另一个问题，它是可以外置的。**
**StreamMind（ICCV 2025）** 是**视频-only + 主动式**的强先例：在视频编码器与 LLM 之间插一个 **Cognition Gate**，「**只在相关事件发生时调用 LLM**」，单 A100 跑 **100 fps**，配**恒定成本**的状态空间特征提取器。它直接解决「线性视频流速 vs 二次计算成本」。

**⇒ 正确结论**：
- **触发器**（该不该花一次 VLM tick）→ **可外置**，用便宜的 CPU 侧信号（帧差分/光流能量/极小 ONNX 分类器）近似 Cognition Gate。**这是拿收益不增加尾巴的做法**：抑制 tick 省成本与占用，而 P99 仍由慢路径决定。
- **决策**（silence/response/delegate）→ **保留在模型内**。
- **为延迟而外置** → **反对**（方差论证 + L0 自身约 500ms 下限）。

（完整论证、L0–L3 分层、以及两条 LiveKit 已发布的阈值冲突陷阱，见 §1.5。）

### 3.4 「换 3B/4B 更快」—— 忽略了共享 ViT

**看起来**：参数量少 2/3，理应快很多。

**为什么用不了**：Qwen3-VL 家族 2B/4B/8B **共用同一 ViT**（mmproj 819MB / 836MB / 1.08GiB）。视觉编码耗时**与 LLM 参数量无关**。而本项目每轮只生成 1–3 个 token——省下的解码时间在总量里可忽略。**换来的是决策质量下降。**

### 3.5 DGX Spark 的「q4_0 慢 92.5%」—— 已撤回，仍在流传

若任何下游文档引用「q4_0 提示处理慢 92.5%」或「q4_0 比 f16 更占显存」，请注意**这两条均已被原作者撤回**（源于静默失败请求 + 用 RSS 测统一内存）。修正后数字：**长上下文解码约 -37%**。

---

## 4. 来源清单

### 4.1 论文 / 模型

- **JoyAI-VL-Interaction: Real-Time Vision-Language Interaction Intelligence**（arXiv:2606.14777, 2026）— 本项目上游论文；§3.1 明确 AdaCodec 为原生视频接口、§2.2 明确「决策内生」并批评外部轮询。<https://arxiv.org/abs/2606.14777>
- **AdaCodec: A Predictive Visual Code for Video MLLMs**（arXiv:2606.02569, 2026）— 84.6% token 削减，TTFT 9.26s→1.62s；P-tokenizer 两阶段训练。<https://arxiv.org/abs/2606.02569>
- **ForestPrune: High-ratio Visual Token Compression for Video MLLMs**（arXiv:2603.22911, 2026）— training-free 时空森林剪枝。<https://arxiv.org/abs/2603.22911>
- **Decouple and Cache: KV Cache Construction for Streaming Video Understanding**（arXiv:2605.01858, 2026）— DSCache。<https://arxiv.org/abs/2605.01858>
- **InfiniPot-V: Memory-Constrained KV Cache Compression for Streaming Video**（OpenReview）— 峰值显存 -94%。<https://openreview.net/forum?id=hFxOZjHyTg>
- **StreamingVLM: Real-Time Understanding for Infinite Video Streams**（arXiv:2510.09608, ICLR 2026）— 需 SFT；Qwen2.5-VL-7B 基础。<https://arxiv.org/abs/2510.09608> ｜ 代码 <https://github.com/mit-han-lab/streaming-vlm>
- **A Survey of Token Compression for Efficient Multimodal Large Language Models**（arXiv:2507.20198v5, 2026）— 多模态 token 压缩系统综述。<https://arxiv.org/html/2507.20198v5>
- **LLaVA-UHD v4: What Makes Efficient Visual Encoding**（arXiv:2605.08985, 2026）。<https://arxiv.org/html/2605.08985v1>
- **MiniCPM-V 4.6**（2026-05）— 混合 4x/16x 视觉 token 压缩。HuggingFace: <https://huggingface.co/openbmb/MiniCPM-V-4.6>
- **Qwen3-VL Technical Report**（arXiv:2511.21631）。<https://arxiv.org/pdf/2511.21631>

### 4.2 llama.cpp 官方（文档 / 源码）

- **server README（参数权威表）** — `-fa` / `-fit` / `-ctk` / `-ctv` / `--image-max-tokens` / `--mmproj-offload` / `--parallel`。<https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md>
- **`src/llama-context.cpp`** — 「quantized V cache … requires Flash Attention」硬抛异常源码。<https://github.com/ggml-org/llama.cpp/blob/master/src/llama-context.cpp>
- **`common/arg.cpp`** — `-fit` = fit-to-VRAM 的参数定义。<https://github.com/ggml-org/llama.cpp/blob/master/common/arg.cpp>
- **`docs/multimodal.md`** — mmproj 默认 offload GPU；`--no-mmproj-offload`。<https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md>
- **`docs/speculative.md`** — 2026 支持的投机解码类型（EAGLE-3 / MTP / DFlash / DSpark / ngram）。<https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md>

### 4.3 llama.cpp issues / discussions / PR

- **discussion #9646「Quality concerns with Flash Attention」** — 原始报告者 2026-09 确认「很久以前已修好，是浮点精度问题」。<https://github.com/ggml-org/llama.cpp/discussions/9646>
- **issue #21272（RTX 5060 Ti）** — FA 导致 CLIP f32 回退 CPU：单图编码 13,495ms vs 504ms；修复 PR #21271。<https://github.com/ggml-org/llama.cpp/issues/21272>
- **issue #22582** — `mtmd_encode_chunk()` 82 秒/图（llama-server）；**closed as not planned**（维护者 ngxson：cli 也是调 server，问题在用户侧）。<https://github.com/ggml-org/llama.cpp/issues/22582>
- **discussion #21619「TriAttention KV Cache Pruning — Early llama.cpp Prototype」**（2026-04）— KV 剪枝原型，**非生产就绪**。<https://github.com/ggml-org/llama.cpp/discussions/21619>
- **issue #18389 → PR #24269** — mtmd 视频输入支持；API 设计讨论（流式帧、ffmpeg 依赖）。<https://github.com/ggml-org/llama.cpp/issues/18389>
- **issue #16842 / PR #16878** — QwenVL 图像预处理尺寸；「OCR 推荐 1024–2048 image tokens」。<https://github.com/ggml-org/llama.cpp/issues/16842>
- **issue #12352** — q8_0 KV 导致 graph splits 2→98（静默回退 CPU），提示评估 928.67→108.21 tok/s。<https://github.com/ggml-org/llama.cpp/issues/12352>
- **discussion #23470** — 非对称 KV 量化；K 侧敏感 vs V 侧（KLD 矩阵 + 答案抖动测试）。<https://github.com/ggml-org/llama.cpp/discussions/23470>
- **discussion #20969** — TurboQuant；**DGX Spark 数字撤回**（dentity007）。<https://github.com/ggml-org/llama.cpp/discussions/20969>
- **discussion #5617** — IQ 量化在 Apple Silicon 慢；维护者 ikawrakow 说明是 Metal 的 codebook 查表问题。<https://github.com/ggml-org/llama.cpp/discussions/5617>
- **discussion #4130** — `--ctx-size` 是 KV cache 总大小；多序列注意力跨序列计算（维护者 ggerganov）。<https://github.com/ggml-org/llama.cpp/discussions/4130>
- **issue #27009** — CUDA graph 限制 batch size 1；`--parallel N` 丢 CUDA graph。<https://github.com/ggml-org/llama.cpp/issues/27009>
- **issue #27330** — sm_120 CUDA graph GPU 挂起（Xid 8），逃生舱 `GGML_CUDA_DISABLE_GRAPHS=1`。<https://github.com/ggml-org/llama.cpp/issues/27330>
- **issue #26205** — Andgihat Blackwell 构建在 **RTX 5060 (sm_120)** 静默崩溃，**stale-bot 关闭、从未解决**。<https://github.com/ggml-org/llama.cpp/issues/26205>
- **issue #20977** — TurboQuant 支持请求，**上游已关闭**。<https://github.com/ggml-org/llama.cpp/issues/20977>
- **discussion #15403** — `--context-shift` 在 Qwen-VL 上失效。<https://github.com/ggml-org/llama.cpp/discussions/15403>
- **PR #24269 / #24318** — mtmd 视频输入 + `--video-*` 参数（源码 `tools/mtmd/mtmd-helper.cpp` 为文件式 ffmpeg 子进程实现）。<https://github.com/ggml-org/llama.cpp/pull/24269> ｜ <https://github.com/ggml-org/llama.cpp/pull/24318>
- **issue #27587**（2026-08-23，b10573，**Windows 11**）— 视频 >~10–13s 使 llama-server **永久挂死**（feeder 线程死锁）。<https://github.com/ggml-org/llama.cpp/issues/27587>
- **issue #24429**（2026-06-10）— 同一死锁的早期形态。<https://github.com/ggml-org/llama.cpp/issues/24429>
- **issue #19929 / PR #19930** — Qwen3.5 视觉在特定 `-b`/`-ub` 下报「图像是空的」。<https://github.com/ggml-org/llama.cpp/issues/19929>
- **issue #15528** — InternVL3 在含文字图像上输出异常（视觉质量 bug）。<https://github.com/ggml-org/llama.cpp/issues/15528>
- **issue #17345** — Qwen3-VL-4B 日志（确认 `block_count 36`、`embedding_length 2560`、DeepStack 宽度）。<https://github.com/ggml-org/llama.cpp/issues/17345>
- **issue #24303** — 连续图片被合并为 2 帧（未关闭）。<https://github.com/ggml-org/llama.cpp/issues/24303>
- **Andgihat/llama-cpp-mtp-turboquant-sm120-blackwell-windows** — 作者自述「not actively developed」。<https://github.com/Andgihat/llama-cpp-mtp-turboquant-sm120-blackwell-windows>

### 4.4 小模型 / 基准 / 主动式交互（turn-taking）

- **Proact-VL**（arXiv:2603.03447, ICML 2026）— **与本项目架构最接近的前作**：1 chunk/秒 + `<|FLAG|>` 决策 token；τ=0.5 最实用；去掉稳定性正则 F1 掉 49.05 点。**反外置化证据**。<https://arxiv.org/abs/2603.03447>
- **When2Speak**（arXiv:2605.05626, 2026）— 1B/3B 崩成「永远静默」；4B/8B/70B F1 仅差 0.006–0.010。<https://arxiv.org/abs/2605.05626>
- **Speak or Stay Silent**（arXiv:2603.11409, 2026）— 轮次转换**不是涌现能力，必须显式训练**。<https://arxiv.org/abs/2603.11409>
- **ProVoice-Bench**（arXiv:2604.15037, 2026）— 主动式语音 agent 基准；过度触发普遍。<https://arxiv.org/abs/2604.15037>
- **LiveKit Turn Detector / eot-bench**（2026）— 完整模型 v1 false-cutoff 9.9%，**同架构小模型 v1-mini 27.8%**。<https://livekit.com/blog/solving-end-of-turn-detection> ｜ <https://github.com/livekit/eot-bench>
- **Pipecat Smart Turn v3.2**（~8M 参数，8MB int8，~65ms CPU）— 2026 主流「小判定器」实现。<https://github.com/pipecat-ai/smart-turn>
- **Speculative cascades**（arXiv:2405.19261, Google Research）— 直言顺序 wait-and-see 是根本瓶颈。<https://arxiv.org/abs/2405.19261>
- **Dynamic Model Routing survey**（arXiv:2603.04445, 2026）— 级联收益归因于互补性与成本-质量，非延迟。<https://arxiv.org/abs/2603.04445>
- **Qwen3.5-4B model card**（2026）— VideoMMMU 74.1 / Video-MME 83.5 / OCRBench 85.0；对比 9B 与 2B（62.1）。<https://huggingface.co/Qwen/Qwen3.5-4B>
- **RTX 5060 Ti llama-bench**（第三方）— Qwen2.5-7B Q4_K_M：pp512 3,740 / tg256 84.5 tok/s（8B 级实测量级锚点）。<https://treeru.com/en/blog/rtx-5060-ti-local-llm-benchmark>
- **StreamMind: Unlocking Full Frame Rate Streaming Video Dialogue through Event-Gated Cognition**（ICCV 2025）— **视频-only + 主动式**的 Cognition Gate；100 fps 单 A100；恒定成本 EPFE。**「触发器可外置」的核心依据。**<https://arxiv.org/abs/2503.06220>
- **A Survey of Full-Duplex Spoken Dialogue Systems**（arXiv:2606.19453, 2026-06-17，浙大 + Qwen/阿里 + 腾讯混元 + 字节）— **L0–L3 架构分层**；本项目决策设计属 **L1**；L0 结构性延迟下限约 500ms；「L0 remains contested rather than legacy」。<https://arxiv.org/html/2606.19453v1>
- **AssemblyAI, turn detection & endpointing**（2026）— **「Not the mean. The variance.」**；反对在语义判定上叠计时器。<https://www.assemblyai.com/blog/turn-detection-endpointing-voice-agent>
- **LiveKit, turn detection & interruption handling**（2026）— 双模型阈值冲突（`endpointing.min_delay` 干扰 STT 自身延迟）；v1→v1-mini 会话中重标定；`adaptive` 模式 Cloud-only。 <https://livekit.com/blog/turn-detection-and-interruption-handling>
- **TEN VAD**（腾讯）— 修掉 Silero 漏检**短静音间隙**的问题；RTF 0.0086–0.0150，306 KB / 464–508 KB。<https://github.com/ten-framework/ten-vad> ｜ Silero 性能对比 <https://github.com/snakers4/silero-vad/wiki/Performance-Metrics>
- **Easy Turn**（arXiv:2509.23938, 2025）— 模态消融：单模态 ~85–86，**融合 95.75**（单模态约掉 10 点）。<https://arxiv.org/html/2509.23938v1>
- **Levinson & Torreira 2015**（Frontiers in Psychology 6:731）— 轮次间隙均值 ~200ms，而词产出延迟 >600ms ⇒ 人类靠**预判**。**⇒ 1 fps（1000ms 量化）根本无法表达 200ms 的预判窗口。**<https://pmc.ncbi.nlm.nih.gov/articles/PMC4464110/>

### 4.5 实测/工程博客

- **InventiveHQ, "Flash Attention in llama.cpp: -fa Is Free Because It's Already On"**（2026-06-26）— **RTX 5060 Ti** 同款卡 4K/16K/32K × fa off/on 扫描，结论 `-fa` 是 no-op（默认已开）。<https://inventivehq.com/blog/flash-attention-llama-cpp-benchmark>
- **InventiveHQ, 投机解码消费者 GPU 实测**（2026-06-25）— 7B 目标在 RTX 5060 Ti CUDA 上 **0.27×**。<https://inventivehq.com/blog/llama-cpp-speculative-decoding-consumer-gpu>
- **blog.c42.ro, RTX 5060 Ti 吞吐实测**（2026-08-12）— `--parallel` 1/2/4 = 46.5/74.8/105.1 t/s；TTFT p50 203→2077ms；量化 KV 与 FA 关闭互斥。<https://blog.c42.ro/2026/08/rtx-5060-ti-llm-throughput/
- **NVIDIA Developer Blog, "Optimizing llama.cpp AI Inference with CUDA Graphs"** — H100 上最高 1.2×。<https://developer.nvidia.com/blog/optimizing-llama-cpp-ai-inference-with-cuda-graphs/>
- **deepwiki: JoyAI-VL-Interaction §4.1 WebRTC Camera Input** — 采集链路：aiortc、`process_interval_seconds=1.0`、`frames_per_batch=1`、`max_frame_latency` 丢帧。<https://deepwiki.com/jd-opensource/JoyAI-VL-Interaction/4.1-webrtc-camera-input>

### 4.6 本仓库内证据（[实机] 一手）

| 结论 | 位置 |
|---|---|
| `-fit off` 被注释为「flash attention off」（**错误**） | `services/scripts/run-windows.ps1:389`、`决策/VLM架构与模型组成.md:64` |
| 本机 b10155 `--help` 证实 `-fit`=fit-to-VRAM、`-fa`=flash attention | 实机执行 `D:\AI\bin\llama.cpp\llama-server.exe --help` |
| **帧不进入历史**（每轮只带当前帧） | `services/webinfer/prompt_assembly.py:90-155` |
| 环形帧缓冲窗口默认 6 | `services/webui/src/joy_interaction_webui/live_frames.py:19` |
| 每 1.0s 一次 VLM 调用、每批 1 帧 | `services/webui/src/joy_interaction_webui/video_processor.py:71-75` |
| `qa_history_window=12`（纯文本） | `services/webinfer/adapter_types.py:144-145` |
| `max_pixels` 默认 1048576 | `services/webinfer/adapter_types.py:120` |
| VLM 推理段 <320ms 非瓶颈，瓶颈在采集/编码 | `决策/VLM架构与模型组成.md` §「VLM 端到端延迟实测结论」 |
| AdaCodec 为上游官方 TODO、本版本不做 | `doc/local/pm-local.md:110`、`doc/deprecated/delivery-handoff-2026-07/系统设计.md:436` |

---

## 附：待验证清单（诚实标注）

1. **本机 FA 实际状态**：`auto` 的运行时探测在 CUDA+f16 KV 下应成功 → FA 开着。**需启动日志确认**（找 `flash attention is enabled` 或 `CLIP graph uses unsupported operators`）。
2. **单轮实际帧数**：`LIVE_FRAME_WINDOW=6` 与 `frames_per_batch=1` 的实际组合结果需实测确认（影响单轮 token 量）。
3. **CLIP 回退是否已在 b10155 修复**：PR #21271 约 2026-04 合并，b10155 约 2026-07（[推断]），**应已包含但未直接确认**。
4. **KV 几何**：模型实际是 Qwen3-8B（36L/8KV，F16 KV≈2.25 GiB）还是 Qwen2.5-VL-7B（28L/4KV，≈0.9 GiB）几何——**两种情况下「不该量化 KV」的结论一致**，故不阻塞决策。
5. **前缀缓存 + 图像占位 token**：不同图像在相同位置产生**相同占位 token ID**，基于 token ID 的前缀匹配理论上无法区分 → **存在「陈旧图像复用」风险**，未找到上游确认或否认。**必须实测**。
6. **`-fit off` 在本机的显存副作用**：因 `-c`/`-ngl` 已显式指定，预期为空操作，未实测。
7. **Reddit 主帖不可达**（人机验证墙），相关 r/LocalLLaMA 数据仅通过搜索摘要引用，未一手核实。本文档中所有量化表格均来自**直接抓取的一手来源**。

---

> **端点**：调研（research / AFK，只读）｜ **产出**：本文档 ｜ **未修改任何代码/配置**
> 本调研支持并独立复核了项目既有结论「VLM 推理段 <320ms 非瓶颈、瓶颈在采集/编码链路」，并新增一条配置认知纠偏（`-fit` ≠ flash attention）。
