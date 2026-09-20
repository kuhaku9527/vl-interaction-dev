# 上游 JoyAI-VL-Interaction 进展与社区痛点调研（截至 2026-09-15）

> **类型**：证据链型调研（doc/research/）
> **端点身份**：调研端点（research / AFK，只读）
> **调研日期**：2026-09-19
> **上游仓库**：https://github.com/jd-opensource/JoyAI-VL-Interaction （默认分支 main，Apache-2.0，1922 stars，192 forks，41 open issues）
> **本地 fork**：`D:\AI\workspace\JoyAI-VL-Interaction-main`（origin = `kuhaku9527/vl-interaction-dev`）
> **方法**：gh_* 插件 + gh CLI（账号 kuhaku9527）+ HuggingFace API；未 clone 上游、未构建、未下载大文件
> **上游数据时效**：main 最新提交 2026-09-15（`32add65c0`）；quantized 权重最后更新 2026-07-31

---

## 0. 一句话结论

**上游近两个月在"能跑起来"上只做了三件实事（Docker 三档硬件 profile、量化权重发布、linear backend 开关），但对本地最痛的四件事——llama.cpp/GGUF 官方支持、上下文无限膨胀修复、多路视频、RTSP 显存泄漏——上游一个都没修（4 个相关 PR 全部 open/closed 未合并，0 个社区 PR 被 merge），而本地 fork 反而已经自行修复了其中两项（qa_history 与 long_term_memory 双上限，对应上游未合并的 PR #25）；因此本地的正确策略是"取上游的配置数值、弃上游的代码路径"。**

关键量化差距：本地 `max_pixels` 默认 **1048576**，上游默认 **262144**（4× 差距，直接决定每帧视觉 token 数与 16GB 显存能撑几路）。

---

## 1. 上游 2026-07-01 之后变更清单（main 分支全量，含 docs）

采集命令：`gh api "repos/jd-opensource/JoyAI-VL-Interaction/commits?sha=main&since=2026-07-01T00:00:00Z"`，共 39 条。

| # | SHA | 日期 | 提交 | 类别 | 与本地相关性 |
|---|---|---|---|---|---|
| 1 | `32add65c0` | 09-15 | Add blind interaction demo videos | 资源 | 无关 |
| 2 | `cf6a07e5c` | 09-15 | Add demo video with Git LFS | 资源 | 无关 |
| 3 | `90bb195a1` | 09-15 | **Support system prompt overrides in streaming inference** | **feat** | **低**（本地有自建 `system_prompts.py` + 角色 prompt 注入；见 §4） |
| 4 | `6913198b3` | 09-15 | Add files via upload (weixin.jpg) | 资源 | 无关 |
| 5 | `cc5c93804` | 09-15 | Delete weixin.jpg | 资源 | 无关 |
| 6-11 | `5519d3537` `8862d5bf5` `cf973c8fd` `ffeab343b` `2a3c994d2` `293dec1e5` | 08-29 | weixin.jpg 增删循环 ×3 | 资源 | 无关 |
| 12 | `f8bcde678` | 08-25 | docs: add API usage guides | docs | 低（可参考 `doc/api.md` 契约） |
| 13 | `6bb5a0a52` | 08-15 | docs: add low-CPU RTSP streaming mode | docs | **中**（低 CPU 推流法，见 §5-3） |
| 14 | `d64522c8c` | 08-13 | docs: update WeChat image | docs | 无关 |
| 15 | `fd7112570` | 08-11 | docs: add JD Cloud API access links | docs | 无关（商业云 API） |
| 16 | `c98e67044` | 08-06 | chore: update branding assets | chore | 无关 |
| 17 | `58722e94a` | 08-05 | chore: add .env.example files | chore | **中**（三档 profile 的完整参数模板，见 §5-1） |
| 18 | `8d655a02f` | 08-03 | docs: add quantized model links | docs | **高**（量化权重清单，见 §2） |
| 19 | `500a037f4` | 08-03 | **feat: support configurable linear backend** | **feat** | **低**（vLLM 专属，见 §2.3） |
| 20-22 | `ce73e048c` `35965f55e` | 07-29 | weixin.jpg 增删 | 资源 | 无关 |
| 23 | `184c97c0c` | 07-22 | docs: update evaluation sections | docs | 低 |
| 24 | `452e14723` | 07-22 | docs: correct benchmark count in Chinese README | docs | 无关 |
| 25-27 | `07d1f387d` `14ad99f89` `278f45ab5` | 07-22 | Update README.md ×3 | docs | 无关 |
| 28 | `89fd54349` | 07-22 | **release: update model path and add Qwen3-VL benchmarks** | **release** | **高**（主模型 `JoyAI-VL-Interaction` 取代 `-Preview`；Qwen3-VL-8B 对比基准） |
| 29 | `f4f2a611c` | 07-22 | docs: update unified model defaults | docs | **高**（统一在线+离线模型） |
| 30 | `002d3f8de` | 07-21 | docs: document LiveKit deployment option | docs | 低（livekit 分支） |
| 31 | `12db53341` | 07-21 | docs: add bilingual navigation and deployment tuning | docs | **高**（记忆/轮时长调参附录，见 §4） |
| 32 | `332ac2e44` | 07-21 | docs: mark optimized inference configs complete | docs | 低 |
| 33 | `745c2a6b6` | 07-21 | docs: remove RTX optimization announcement | docs | 无关 |
| 34 | `60b117de5` | 07-21 | docs: highlight optimized RTX 3090 and 5090 profiles | docs | **高**（档位参数，见 §1.1） |
| 35 | `bcfab4411` | 07-21 | **feat: release Docker deployment for RTX 3090 and 5090** | **feat** | **高**（30 个新文件，`container/` 全套，见 §1.1） |
| 36 | `fb0312e2a` | 07-21 | **Add service startup and smoke warmup helpers** | **feat/perf** | **高**（预热 + prompt 选择，见 §3-issue#18） |
| 37-39 | `6cce28d97` `d6f3eb1d9` | 07-14 | weixin.jpg 增删 | 资源 | 无关 |
| 40-43 | `3789e8da1` `5fb642dfd` `73c4396d9` | 07-06 | Update README.md ×2 + upload | docs | 无关 |

### 1.1 从 Docker profile 提炼的可迁移参数（**本次最可直接落地的事实**）

上游 `container/` 三档 profile（来源：`container/README.md` @ main，commit `bcfab4411` + `58722e94a`）：

| Profile | 目标 GPU | 主模型 | `MAX_MODEL_LEN` | 主 GPU 利用率 | 记忆策略 | `CHUNK` |
|---|---|---|---|---|---|---|
| `regular` | 1×32GB + 3×32GB/API | BF16 | 67,174 | 0.95 | 5 中期 / 2 长期块 | — |
| `24GB` | 1×24GB + 3×24GB/API | INT4 AWQ G32 | 81,920 | 0.95 | 5 中期 / 5 长期块 | 100 |
| **`16GB`** | **1×16GB + 3×16GB/API** | **INT4 AWQ G32** | **32,768** | **0.95** | **3 中期 / 1 长期块** | **70** |

**16GB profile 完整参数**（`container/16GB/.env.example`，commit `58722e94a`）：

```
MAX_MODEL_LEN=32768
MAIN_GPU_MEMORY_UTILIZATION=0.95
SUMMARY_MAX_MODEL_LEN=8192
SUMMARY_GPU_MEMORY_UTILIZATION=0.95
ASR_GPU_MEMORY_UTILIZATION=0.60
TTS_GPU_MEMORY_UTILIZATION=0.90
CHUNK=70
COMPRESS_EVERY_N_CHUNKS=3
MID_TERM_MAX_TOKENS=3000
MID_TERM_TARGET_TOKEN_COUNT=2500
LONG_TERM_MAX_TOKENS=3000
LONG_TERM_TARGET_TOKEN_COUNT=2000
LONG_TERM_MEMORY_WINDOW=1
MAIN_MAX_TOKENS=256
LIVE_VLM_PROCESS_INTERVAL=1.0
LIVE_VLM_FRAMES_PER_BATCH=1
```

**上游对"小时级连续视频"的参数含义定义**（`container/README.md` 附录，commit `12db53341` 原文）：

- `CHUNK`：每个中期记忆块包含的帧数。每轮处理一帧时，**一个记忆块的近似时长 = `CHUNK × LIVE_VLM_PROCESS_INTERVAL` 秒**。16GB 档 = `70 × 1.0` = **70 秒/中期块**。
- `COMPRESS_EVERY_N_CHUNKS`：累计多少个中期块后压缩为长期记忆。16GB 档 = 3 → **每 210 秒进一次长期压缩**。
- `MID_TERM_MAX_TOKENS` / `MID_TERM_TARGET_TOKEN_COUNT`：中期摘要最大/目标长度。16GB 档 3000/2500。
- `LONG_TERM_MAX_TOKENS` / `LONG_TERM_TARGET_TOKEN_COUNT`：长期摘要最大/目标长度。16GB 档 3000/2000。
- `LONG_TERM_MEMORY_WINDOW`：上下文中保留的长期记忆块数量。16GB 档 **= 1**（最激进裁剪；24GB 档 = 5，regular = 2）。
- `LIVE_VLM_PROCESS_INTERVAL`：一次推理 turn 的间隔秒数，**增大可降低处理频率与硬件负载**。
- 官方调参口径：*"缩短记忆长度、减少保留块数或降低处理频率，可以减少上下文和计算资源需求"*。

> **注**：上游 16GB profile 是 **vLLM + INT4 AWQ + 4 卡**方案（1 主 + 3 API），**不是** 单卡 16GB 方案。本地是单卡 16GB 全栈共享，因此只能迁移**数值口径**（记忆策略/帧预算/轮间隔），不能迁移**卡分配**。

### 1.2 主模型代际切换（本地需注意的事实）

`89fd54349`（07-22）将默认主模型从 `JoyAI-VL-Interaction-Preview` 切到 **`JoyAI-VL-Interaction`**（统一在线+离线），`bcfab4411`/`89fd54349` 同步更新了 docker-compose 默认路径。

后果：**本地当前加载的是 `-Preview` 世代的 GGUF**（`install/download-gguf-models.ps1` 指向 `Nasa1423/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF`）。新统一模型的社区 GGUF **在上游发布两个月后仍不存在**（见 §2.4）——这是本地最大的版本落后，且**不是靠同步代码能解决的**。

---

## 2. 官方量化模型现状

来源：commit `8d655a02f`（README 表格）/ HuggingFace API `?blobs=true`（2026-09-19 实测）。

### 2.1 四个官方量化权重

| 变体 | HF 仓库 | 权重体积 | 格式 | 最后更新 | 下载量 | Likes |
|---|---|---|---|---|---|---|
| Full (BF16) | `jdopensource/JoyAI-VL-Interaction` | **17.53 GB**（4 shard） | safetensors bf16 | 2026-07-22 | 951 | 19 |
| **INT4** | `jdopensource/JoyAI-VL-Interaction-INT4` | **7.55 GB** | compressed-tensors, W4A16, group_size=32 | 2026-07-31 | 320 | 6 |
| INT8 | `jdopensource/JoyAI-VL-Interaction-INT8` | **10.59 GB** | compressed-tensors | 2026-07-31 | 86 | 2 |
| FP8 | `jdopensource/JoyAI-VL-Interaction-FP8` | **10.59 GB** | compressed-tensors | 2026-07-31 | 57 | 2 |
| NVFP4 | `jdopensource/JoyAI-VL-Interaction-NVFP4` | **7.55 GB** | compressed-tensors | 2026-07-31 | 75 | 3 |
| （旧）Preview | `jdopensource/JoyAI-VL-Interaction-Preview` | 17.53 GB | safetensors bf16 | 2026-06-22 | 539 | 86 |

**INT4 的技术细节**（`config.json` + `recipe.yaml` 实测）：
- `quantization_config.format = "pack-quantized"`，`num_bits: 4`，`group_size: 32`，`symmetric: true`，`strategy: group`，`observer: memoryless_minmax`；
- 由 **llm-compressor** 的 `AWQModifier`（`duo_scaling: false`, `n_grid: 20`）+ `QuantizationModifier` 生成；
- **`ignore` 列表保留 vision tower 全部 block 的 qkv/proj/linear_fc1/linear_fc2 为高精度**——即视觉编码器未量化，量化只作用于 LLM 主干。
- 架构 `Qwen3VLForConditionalGeneration`，`hidden_size: 4096`，tags 含 `compressed-tensors`、`qwen3_vl`。

### 2.2 为什么 INT4 只有 7.55 GB 而 Full 是 17.53 GB

8B 模型 BF16 ≈ 16 GB 主干 + vision tower + embed/lm_head。INT4 把 LLM 主干压到 ~4 bit（≈4.5 GB）但**保留 vision tower 高精度**（见 `ignore` 列表），故总 7.55 GB。这解释了为什么官方 INT4 不能简单转成"更小的 GGUF"。

### 2.3 `configurable linear backend` 是什么，影响什么（问题 1 之二）

**commit `500a037f4`（2026-08-03），只改 1 个文件、+7/-0 行**：`services/webinfer/scripts/start_model.sh`

```bash
LINEAR_BACKEND="${LINEAR_BACKEND:-auto}"
LINEAR_BACKEND_ARGS=()
if [[ "${LINEAR_BACKEND}" != "auto" ]]; then
  LINEAR_BACKEND_ARGS=(--linear-backend "${LINEAR_BACKEND}")
fi
# ...
"${LINEAR_BACKEND_ARGS[@]}" \
--enable-prefix-caching \
--enable-chunked-prefill &
```

**判定**：
- 它**只是把 vLLM 原生的 `--linear-backend` 参数透传出来**（vLLM 的 linear layer 内核选择器：`auto` / `cutlass` / `flashinfer` / `triton` / `marlin` 等）。
- **不是** attention 后端，**不是**新量化内核，**不是** llama.cpp 相关。
- 影响面：仅在 vLLM 部署下选择 Linear 层算子的实现（决定量化 GEMM 走哪个 kernel，进而影响吞吐/显存）。
- **对本地 llama.cpp 路径影响：零。** llama.cpp 的 GGUF 量化内核由 ggml 自行选择，没有等价开关。本地 `services/webinfer/scripts/start_model.sh` 是旧版 vLLM 脚本（无 `LINEAR_BACKEND`），但因为本地根本不跑 vLLM，此差异**无实际影响**。

### 2.4 是否支持 llama.cpp？（**问题 1 的核心答案：不支持**）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 官方量化格式是否为 GGUF | **否** | 4 个仓库 tags 全部含 `compressed-tensors`，全部为单文件 `model.safetensors` |
| 上游 README 是否提及 llama.cpp / GGUF | **否** | `gh api .../README.md` grep `llama.cpp\|gguf` → 0 命中 |
| 上游是否有 GGUF 发布计划 | **未提及** | README 路线图只列 "Codec version (AdaCodec)" 为唯一未完成项，量化项已勾选 |
| HF 上是否存在**统一新模型**的社区 GGUF | **否** | `hf api models?search=JoyAI-VL-Interaction&filter=gguf` → 仅 1 个，且 base_model 是 `-Preview` |
| HF 上是否存在 **`-Preview`** 的社区 GGUF | **是** | `Nasa1423/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF`（4.79 GB，imatrix，346 下载，最后更新 **2026-06-20**） |

**结论**：官方 4 个量化权重**全部是 vLLM/compressed-tensors 格式，无法被 llama.cpp 加载**。本地走 llama.cpp 就必须继续依赖社区 GGUF，而社区 GGUF 只覆盖**旧 `-Preview` 世代**，且自 2026-06-20 起未再更新。**本地当前用的正是这个仓库的 IQ4_NL**（`install/download-gguf-models.ps1` L149）。

**其它社区量化**（旁证，均非 GGUF / 非 llama.cpp）：
- `claris153/JoyAI-VL-Interaction-Preview-AWQ-W4A16`（66 dl）
- `openaiarka/JoyAI-VL-Interaction-Preview-AWQ`（20 dl）
- `xiaowangzhixiao/JoyAI-VL-Interaction-MLX-4bit`（134 dl，base 已是**新统一模型**，Apple Silicon 专用）

> **可执行推论**：若要上"新统一模型 + llama.cpp"，本地必须**自行 `convert_hf_to_gguf.py` 转换**（上游 INT4 的 vision tower 未量化，转换时需注意 mmproj 单独导出）。这是**有工作量、有失败风险**的路径，不是"下载即用"。

---

## 3. 社区 issue 高价值清单

### 3.0 全局事实：**上游社区 PR 合并率 ≈ 0**

`gh pr list --repo jd-opensource/JoyAI-VL-Interaction --state all`（共 8 个 PR）：

| PR | 标题 | 状态 | 作者 | 说明 |
|---|---|---|---|---|
| #2 | docs: add vLLM-Omni day-0 serving support | **MERGED**（2026-06-20） | lishunyang12 | **唯一的社区合并 PR**，且早于调研窗口 |
| #25 | Fix unbounded context growth (`qa_history` + `long_term_memory`) | **CLOSED（未合并）** | app/ | 修复 #23 |
| #28 | Fix: 上下文会不断累加…（Fixes #23） | OPEN（未合并） | MayVerse4 | 自动生成，作者自述未过 code review |
| #29 | feat: add LLaMA-Factory weighted SFT loss integration | OPEN（未合并） | MayVerse4 | 对应 #22 |
| #30 | Fix: 微信群 | OPEN（未合并） | MayVerse4 | — |
| #31 | Feat/for webide | OPEN（未合并） | structDream | — |
| #34 | 适配API（替换为阿里 API） | CLOSED（未合并） | zilin1024 | 作者自评"失去了项目最大特色：主动性" |
| #36 | Guard streaming prompts against context growth（Fixes #35） | OPEN（未合并） | xiaowangzhixiao | **+377/-44，5 文件，含回归测试** |

**判定**：**2026-07-01 之后 main 上没有任何社区贡献被合入**。所有上下文/显存类修复都停留在 open/closed 状态。这意味着：**指望"同步上游"来解决本地痛点是不成立的**——上游没有可同步的修复。

---

### 3.1 上下文无限膨胀类（本地最高价值组）

#### Issue #23 — 上下文会不断累加，最终超过 context length 报错
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/23 （2026-07-02，open，4 评论）
- **问题**：A5000 24G，`ENABLE_SUMMARIZER=false`，`CHUNK=6`，`MAX_MODEL_LEN=12000`，**跑 400+ 轮后** 报 `ValueError: Input length (12272) exceeds model's maximum context length (12000)`。
- **官方回应**：**无团队成员回复**。仅有社区回复：`romantic2016cool` 建议 *"MAX_MODEL_LEN 设置 8192，超出的就把它最久的那个截掉可以解决"*（即手动裁剪最旧上下文）；`ghost` 指路 PR #25。
- **是否已修**：**否**。修复 PR #25 被 **closed 未合并**；PR #28（Fixes #23）**open 未合并**。
- **可用 workaround**：手动裁剪最旧 turn（社区实测有效）；或降 `MAX_MODEL_LEN` 并无界增长下"延后报错"。
- **可用 PR 号**：#25（已关闭，但**代码可借鉴**）、#28（open）、#36（open，更完整）
- **与本地相关性**：**高**。本地已有对应修复（见 §4），但此 issue 证实**上游同样存在且未修**——本地 fork 在此项**领先上游**。

#### Issue #25 / PR #25 — Fix unbounded context growth in long streaming sessions
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/25 （2026-07-08 创建，**CLOSED 未合并**）
- **问题（作者原文）**：*"Long-running streaming sessions eventually fail with a context-length error from the main model. **This happens regardless of how large `--max-model-len` is set**, since it's a symptom of unbounded prompt growth rather than an undersized context window."* — 即：**加大 `--max-model-len` 治不了本**。
- **作者自述**：*"Meanwhile we had a bug fix in our environment that we believe worth contributing back to the upstream team."*（+72/-4，2 文件）
- **官方回应**：**无**。直接 closed。
- **与本地相关性**：**高**（本地已移植，见 §4）。

#### Issue #35 / PR #36 — Streaming adapter can accumulate failed frames past context and image limits
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/35 （2026-09-11，open，**0 评论**）
- **问题（原文）**：`WebInfer` 在调用主模型**之前**就把每帧 append 进 `current_chunk`。若调用失败（例如 prompt 超上下文），该 turn **仍留在 session 状态里**；重试会**再次 append 同一帧**，于是 prompt 持续增长，直到触发后端多模态条数上限：
  1. 首次失败：`Input length (...) exceeds ... 8192`
  2. 每次重试往同一 session 再加图
  3. 后端最终返回 `At most 32 image(s) may be provided in one prompt`
- **官方回应**：**无**（0 评论）。
- **对应 PR #36**（**open 未合并**，+377/-44，5 文件）：opt-in 的 recent-turn / recent-image 窗口；**保留完整 chunk/session 状态供摘要与长期记忆使用**；**在 prompt 构造或主模型请求失败时回滚当前 turn**；CLI/env/`start_adapter.sh` 暴露；中英文文档 + 回归测试。作者自测 `python -m unittest discover -s services/webinfer/tests`，5 tests passed。**两个限默认 `0`（不限）**，保证既有部署行为不变。
- **是否已修**：**否**（PR open）。
- **与本地相关性**：**高**。这条对本地尤其重要：**"失败帧留在 session 且重试再 append"是典型的显存/上下文雪崩机制**，本地长跑场景会出现同样的累积。
- **可用 workaround**：#36 的 patch 目前是唯一系统性方案（**注意默认 0，必须显式开**）。

#### Issue #28 / PR #28 — 同 #23 的修复尝试
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/28 （open，+74/-3，1 文件）
- 作者自述：*"Autonomously generated fix for issue. **Note: The agent reached its maximum step limit before explicitly passing code review.**"*
- **官方回应**：无。
- **与本地相关性**：中（#36 更完整，优先参考 #36）。

> **#23 / #25 / #28 / #35 / #36 合并判定**：5 条**全部未修复**，0 条被 merge。上游在上下文增长问题上**零进展**。

---

### 3.2 显存与多路视频类

#### Issue #20 — 4 大模型配置要求高 + 只推理一个摄像头
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/20 （2026-07-01，open，2 评论）
- **问题**：*"4大模型对机器配置要求有点高了，而且只推理一个摄像头，实际落地场景，一个摄像头很难捕捉全有用信息，后续这方面有什么优化计划吗"*
- **官方回应**：`ydyhello`（COLLABORATOR）：*"感谢反馈~ **多摄像头确实是实际落地中很关键的一点，后续我们会持续优化**。"*（2026-07-01）
- **是否已修**：**否**。截至 2026-09-15 无任何多路视频能力落地。
- **后续**：`KylinMountain` 追问 *"@ydyhello 支持接外部模型吗？还是说只有这个模型可以？"*（07-21）— **无人回复**。
- **与本地相关性**：**高**（本地单卡 16GB，多路视频是明确需求）。

#### Issue #38 — How is the performance? How many cameras can be monitored at the same time
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/38 （2026-08-19，open，**0 评论**）
- **官方回应**：**无**。
- **与本地相关性**：**高**（正是本地要回答的问题；上游无法提供可比数据）。

#### Issue #50 — JoyAI-VL-Interaction 是否支持多路视频？
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/50 （2026-09-11，open，**0 评论**）
- **官方回应**：**无**。
- **与本地相关性**：**高**。与 #38 同题重复出现（相隔 23 天），说明**社区对该能力有持续诉求且官方沉默**。

#### Issue #16 — RTSP 拉流持续监控几分钟后显存占用超出限制
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/16 （2026-06-30，open，5 评论）
- **问题**：*"有办法解决 RTSP 一直拉流，会堆积占满显存的问题吗，我试了面壁智能的那个模型好像也会有这个问题"*；题面提到 **16K 上下文已占 A6000 单卡 30 多 G 显存**，持续监控几分钟后超限。
- **社区旁证**：`xqy99482-source`：*"我也是这样，**应该没做内存释放**"*（1 👍）；`romantic2016cool`：*"存在同样的问题，而且**好像只支持 tcp 传输的 rtsp 流**"*；`Siyong1997`：*"用本地视频进行推流拉流，在 web 浏览器上会**长时间卡顿和阻塞**"*。
- **官方回应**：`ydyhello` 两次表态：
  1. *"感谢反馈，**我们将在下一个版本解决这个问题**"*（06-30）
  2. *"对于全量模型，**建议将最大上下文长度设置为 60K**。我们后续也会推出量化版本的模型，进一步降低显存占用。"*（07-01）
- **是否已修**：**否**。承诺的"下一个版本解决"截至 2026-09-15 未兑现（无相关 commit）。官方给出的 **60K 上下文** 建议与 §1.1 的 16GB profile（`MAX_MODEL_LEN=32768`）**自相矛盾**——后者才是贴合小显存的实际口径。
- **与本地相关性**：**高**。本地 RTSP 是主输入路径之一。

#### Issue #15 — 5090 32G 跑不动
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/15 （2026-06-29，open，5 评论）
- **官方回应**：`ydyhello`：*"建议将**交互模型和摘要模型分别部署在两张 GPU 上**，其余模块可按需作为可选项启动，以降低单卡显存压力。我们后也会开源量化版本的模型。"*
- **是否已修**：**部分**。量化模型已于 2026-08-03 发布（§2.1）——**这一条官方兑现了**；但"双卡分载"建议对本地单卡不可用。
- **与本地相关性**：**高**（本地单卡，只能靠量化+参数裁剪，不能分卡）。

---

### 3.3 端侧/量化类

#### Issue #32 — 有计划提供量化版本吗，可用于端侧设备
- **URL**：https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/32 （2026-07-15，open，1 评论）
- **官方回应**：**无团队成员回复**。仅 `garyganyang`：*"我觉得这个很有必要, 客户喜欢这些"*。
- **是否已修**：**是（部分）**。2026-08-03 commit `8d655a02f` 发布 INT4/INT8/FP8/NVFP4。但**均为 vLLM `compressed-tensors` 格式，"可用于端侧设备"的诉求（llama.cpp/GGUF/ONNX）并未被满足**。
- **与本地相关性**：**高**。本地正是"端侧/单卡"路径，官方量化**格式不兼容**（§2.4）。

---

### 3.4 其它 issue

| Issue | 标题 | 官方回应 | 是否已修 | Workaround / PR | 与本地相关性 |
|---|---|---|---|---|---|
| **#18** | rtsp 首次推理慢，建议添加 VLLM 预热、解码器预热 | `ydyhello`（06-30）：*"我们将在**下一个版本**中添加预热。"* | **已修** | commit **`fb0312e2a`**（07-21）"Add service startup and smoke warmup helpers"：新增 `services/webinfer/smoke.py`、warmup 逻辑写入 `start_model.sh` / `start_summary_model.sh` / `start_adapter.sh` / `run.sh`，并在 Docker 中新增 4 个 `warmup-*` 一次性服务（`warmup-multimodal` 发 13 帧静默 + 1 次真实 query；`warmup-summary`；`warmup-asr` 生成 440Hz WAV 走 smoke；`warmup-tts`） | **高**（本地启动慢，冷启动预热可直接借鉴） |
| **#12** | Preview 在 vLLM 和 Transformers 下都产生不连贯输出 | **无回应**（0 评论） | **否** | 无。提问者已排除编排层（直接 HF Transformers 复现），怀疑 checkpoint/tokenizer 与标准 loader 不兼容 | **中**。若本地也用非官方 loader（llama.cpp + 社区 GGUF 转换），**同一类"不连贯输出"风险同源** |
| **#44** | EgoIT-99K 来源数据的处理 | **无回应** | **否** | 无。提问：`video_name` 含 `__interval__xxxx` 字段，原始 EgoIT-99K 无此字段；一个 video 对应多个哈希值 | 低（仅训练数据加工） |
| **#17** | 后训练脚本有计划开源吗 | **无回应**（0 评论） | **否** | 官方在 #3/#8 已表态"训练代码暂不开源"，推荐 **LLaMA-Factory + EasyVideoR1** 自建 | 低 |
| **#13** | 数据集构建工具（每秒标签）有计划开源吗 | `ydyhello`：*"我们后续会有一项工作专门介绍交互数据的构造 pipeline，后续会同步更多细节。"* | **否**（截至 09-15 无后续） | 无 | 低-中 |
| **#26** | 未来是否可把语音交互并入视频交互模型 | `PhoebusSi`（COLLABORATOR）：*"**会的。在推进中。**"* | **否** | 无。提问者也提到"视觉交互如果支持多路视频流那就更好了" | **中**。本地 ASR/TTS 是旁路（sherpa-onnx + Edge/MiniMax），**与上游架构现状一致**，无需改动 |
| **#14** | 基模为什么选 Qwen3-8B + Qwen3-VL ViT | `ydyhello`：*"JoyAI-VL-1.0 和 Qwen3-VL 同结构，但多模态能力是我们团队重新训练得到的。**可以直接使用 Qwen3-VL-8B 作为基模**，并基于我们开源的数据进行微调。"* | 信息性（非 bug） | **官方明确背书 Qwen3-VL-8B 可直接作基模** — 对本地轻量化选型是**重要许可** | **中-高**（若考虑换基模/自训） |

---

## 4. 本地 fork 相对上游的缺口（**含本地领先项**）

> 重要发现：本地 fork **不是**上游的落后副本，而是**已分叉演进的独立实现**（`live_adapter.py` 已拆成 17 个子模块，见 ADR 0007；本地 `live_adapter.py` 仅 73 行 facade，上游同名单文件 **2809 行**）。因此"同步上游"在文件级别**已不可行**。

### 4.1 本地**领先**上游的地方（上游未修、本地已修）

| 项 | 上游 | 本地 | 证据 |
|---|---|---|---|
| **qa_history 上限** | 无界 append（#23/#25 根因 1） | `qa_history_window: int = 12`（0=禁用旧无界行为） | `services/webinfer/adapter_types.py:145`；`app.py:123`（`QA_HISTORY_WINDOW`）；`memory_io.py:430` |
| **long_term_memory token 预算** | 仅按**条数**裁剪，不按 token（#25 根因 2） | 双重裁剪：`long_term_memory_window: int = 40` **且** `long_term_memory_max_tokens: int = 1800`（token 预算循环删除最旧块） | `adapter_types.py:169-170`；`summarizer_routing.py:216-236` |
| **回归测试** | 无 | `services/webinfer/tests/test_context_overflow_bounds.py`，文件头**明确写 "upstream PR #25 port"** | 测试文件 docstring |
| **prompt guard（字符预算裁剪）** | 无（PR #36 才引入窗口，且未合并） | `_trim_messages_to_ctx` + `_compute_prompt_guard_max_chars`，预算 = `main_ctx_tokens × 3 chars × 0.85`；保留 system + 最近 2 轮 | `prompt_building.py:93-123`；`prompt_constants.py`（`_CHARS_PER_TOKEN_BUDGET=3.0`, `_CTX_SAFETY_FACTOR=0.85`, `_PROMPT_GUARD_MIN_RECENT=2`）；`infer_loop.py:378/600/1334`、`prompt_assembly.py:520` |
| **memory-store 集成** | 无此服务 | `memory_store_client.py`（v0.2，`/v1/blocks/push` `/v1/blocks/recall`），端口 8997 | `services/webinfer/memory_store_client.py`；上游 `services/` 只有 `asr background-agent scripts tts webinfer webui` |

**结论**：**上游 #23/#25 描述的"长会话上下文溢出"，本地已通过 3 层防护（qa_history 窗口 + long_term token 预算 + prompt guard）解决。** 这是本地 fork 的核心资产。

### 4.2 本地**落后**上游的地方（真实缺口）

| # | 缺口 | 上游 | 本地 | 影响 | 可否简单同步 |
|---|---|---|---|---|---|
| **G1** | **主模型世代落后** | 统一在线+离线 `JoyAI-VL-Interaction`（07-22） | 仍加载 `-Preview` 世代 GGUF | 中-高（能力落后一版；且#12"输出不连贯"风险同源） | **否** —— 需等社区 GGUF 或自行转换 |
| **G2** | **`max_pixels` 默认为上游 4 倍** | `262144` | **`1048576`** | **高** —— 直接放大每帧视觉 token 与显存占用 | **是**（改默认/改 env，**风险最低收益最高**） |
| **G3** | **16GB 档位参数未对齐** | 16GB profile：`LONG_TERM_MEMORY_WINDOW=1`、`CHUNK=70`、`COMPRESS_EVERY_N_CHUNKS=3`、`MAIN_MAX_TOKENS=256` | `long_term_memory_window=40`、`chunk=200`、`compress_every_n_chunks=5`、`main_max_tokens=1024` | **高** —— 本地保留 40 个长期块 vs 上游 1 个；本地 `chunk=200` 意味着**200 秒才出一个中期摘要**，摘要压力过大 | **是**（纯数值） |
| **G4** | **失败帧回滚缺失**（issue #35 语义） | 上游同样缺失，但 PR #36 已提供方案 | 无"prompt 构造/请求失败时回滚当前 turn"逻辑 | 中-高（长跑雪崩机制） | 需自行实现（#36 可参考） |
| **G5** | 无 `container/` 部署套件 | 30 文件 Docker 三档 profile | 无 `container/` 目录 | 低（本地是 Windows 原生 + llama.cpp，Docker 方案不适用） | 否（也不需要） |
| **G6** | 无 warmup 一键预热 | `smoke.py` + 4 个 warmup-* compose 服务（`fb0312e2a`） | 无 `smoke.py`；`start_model.sh` 无 SMOKE 逻辑 | **中**（冷启动首轮慢，#18 本地同样存在） | **部分可借鉴**（预热思路，vLLM 参数不可用） |
| **G7** | 无 system prompt override（HTTP 层） | `90bb195a1`：`_extract_system_prompt_override` 从请求 messages 提取 system 并覆盖 | 无（有自建 `system_prompts.py` + 角色 prompt 注入，但**非** HTTP override 语义） | 低-中 | 可选 |
| **G8** | 无 `LINEAR_BACKEND` | `500a037f4` | 无 | **零**（vLLM 专属，本地不跑 vLLM） | 不需要 |
| **G9** | 无 `doc/api.md` / `doc/rtsp_streaming.md` | `f8bcde678`、`6bb5a0a52` | `doc/` 无这两个文件 | 低（文档） | 可直接抄 |

### 4.3 上游对"小时级连续视频"的机制 vs 本地 llama.cpp 路径（问题 4）

**上游机制（三层记忆 + 窗口裁剪）**，来源：`container/README.md` 附录（`12db53341`）+ `live_adapter.py` 实测：

1. **逐帧进入 `current_chunk`** → 满 `CHUNK` 帧生成一条**中期摘要**（`mid_term_summaries`）。
2. 累积 `COMPRESS_EVERY_N_CHUNKS` 个中期块 → `batch_compress_to_longterm()` **压缩进长期记忆** `long_term_memory`。
3. **长期记忆滑动窗口**：`long_term_history` 超过 `long_term_memory_window` 时 `del [:dropped]`，并用留存块的 `compressed_text` 重建 `long_term_memory`（上游 `live_adapter.py:1794-1804`）。
4. **上下文组装**：`build_static_system_content()` 注入 `long_term_memory` + **全部** `mid_term_summaries`；`build_dynamic_system_content()` 注入 `qa_history`（**按 `archived_in_chunk < current_chunk_index` 过滤**，即已归档的问答）。
5. **帧预算**：`summarizer_max_pixels = 262144`、`summarizer_key_frames = 0`（不抽关键帧，每帧都进摘要）。

**上游机制的两个已知漏洞**（正是 #23/#25/#35 的根因）：
- `qa_history` **无上限**（`build_dynamic_system_content` 每次把本 chunk 之前的全部问答塞进 prompt）；
- 长期记忆**只按条数裁剪**（`window`），**不按 token**，且 `long_term_memory_window` 默认 **40** 意味着极端情况下保留 40 个大块。

**本地 llama.cpp 路径的差异与缺失**：

| 维度 | 上游 vLLM | 本地 llama.cpp | 本地具体缺什么 / 差异后果 |
|---|---|---|---|
| **上下文窗口** | `MAX_MODEL_LEN` 32K~131K | `-c 16384`（`MAIN_CONTEXT=16384`，`start-llama-server.ps1 -CtxSize 16384`） | **窗口小 2~8 倍**，同样内容更早溢出 → 更依赖 prompt guard |
| **KV cache** | vLLM `--enable-prefix-caching` + `--enable-chunked-prefill` | `--parallel 1`、**flash attention 关**（`-fit off`） | **无 prefix caching**：每轮 prompt 前缀重复 prefill；无 PagedAttention，KV 按最大长度预分配 → **上下文越长显存越紧张** |
| **量化格式** | compressed-tensors INT4/FP8/NVFP4 | IQ4_NL GGUF（**旧 Preview 世代**） | **拿不到新模型的量化**（§2.4） |
| **多模态条数上限** | vLLM `--limit-mm-per-prompt`（#23 用 `{"image":8,"video":1}`） | llama.cpp 无等价显式上限 | llama.cpp 侧超限表现为**静默截断或报错**，不像 vLLM 给出 `At most 32 image(s)` 的清晰错误 → **更难诊断** |
| **帧预算** | `262144` px | **`1048576` px** | **每帧视觉 token 约为上游 4 倍**（§4.2 G2）—— 这是**当前最大的单点浪费** |
| **摘要模型** | Qwen3-VL-4B-Instruct（vLLM，独立卡） | Qwen2.5-VL-3B-Instruct Q4_K_M GGUF（同卡共享） | **摘要与主模型抢同一张 16GB 卡**，摘要触发即挤占主模型显存 |
| **失败帧回滚** | 无 | 无 | **两边都缺**（#35）；LLM 场景下现场更严重（显存与上下文双涨） |
| **记忆裁剪** | 条数窗口（默认 40） | **条数窗口 40 + token 预算 1800** | **本地领先**；但 `window=40` 仍远大于上游 16GB 档的 `1` |

**本地具体缺什么（按优先级）**：
1. **`max_pixels` 未对齐**（1048576 → 262144）—— 最大且修复成本最低；
2. **`long_term_memory_window` 未按小显存收紧**（40 → 上游 16GB 档为 1）；
3. **失败帧 / 失败 turn 回滚**（issue #35 语义）完全缺失；
4. **无 prefix caching / KV cache 量化** —— llama.cpp 侧可考虑 `--cache-type-k q8_0 --cache-type-v q8_0` 与开启 flash attention（本地当前 `-fit off` **关闭**了 FA），但**需要 sm_120 稳定性验证**；
5. **拿不到新统一模型的 GGUF**。

---

## 5. 对本地可立即采用的建议（含前提与风险）

### 建议 1（**最高优先级 / 最低风险**）：把 `max_pixels` 从 1048576 降到 262144
- **依据**：上游默认 `max_pixels = 262144`（`live_adapter.py:519`），本地默认 `1048576`（`adapter_types.py:120`、`app.py:56` `MAX_PIXELS`）。
- **动作**：`services/scripts/run-windows.env` 增补 `MAX_PIXELS=262144`（与上游对齐）；视效果再决定是否对 `SUMMARIZER_MAX_PIXELS` 同样处理。
- **前提**：需确认本地 WebUI 采集为 1920×1080（`capture_webcam.js:44-45` `width: {ideal:1920}`），1048576 px ≈ 1024×1024，262144 ≈ 512×512。
- **风险**：**低**。风险是**远距离小目标识别率下降**（安防/监控场景关键）。建议 A/B 对比"远距离人脸/车牌/小物体"识别率后再定稿；可先取中间值（如 524288）折中。
- **收益**：每帧视觉 token 约降为 1/4 → 直接降低 prefill 时间与 KV 占用，**在 16K 上下文下可显著延长连续运行时间**。

### 建议 2（**高优先级 / 低风险**）：按上游 16GB 档收紧记忆与轮次参数
- **依据**：§1.1 上游 16GB profile + §4.2 G3。
- **动作**（对照本地现值）：

| 参数 | 本地现值 | 上游 16GB 档 | 建议 |
|---|---|---|---|
| `LONG_TERM_MEMORY_WINDOW` | 40 | **1** | 收到 **2~3**（本地有 token 预算 1800 兜底，不必像上游那样激进到 1） |
| `CHUNK` | 200 | **70** | 降到 **70~100**（本地 `CHUNK=200` + `LIVE_VLM_PROCESS_INTERVAL=1.0` = 200 秒才出中期摘要，摘要过稀、单次摘要负担过重） |
| `COMPRESS_EVERY_N_CHUNKS` | 5 | **3** | 降到 **3** |
| `MID_TERM_TARGET_TOKEN_COUNT` | 3000 | 2500 | 2500 |
| `LONG_TERM_TARGET_TOKEN_COUNT` | 1000 | 2000 | 维持 1000（本地更省）或 1500 |
| `MAIN_MAX_TOKENS` | 1024 | 256 | **维持 1024**（本地注释明确说明 128→1024 是为修长回答截断，**不要**退回去） |

- **前提**：先测量当前单轮 `total_time` 与显存峰值作为基线。
- **风险**：**低-中**。降 `CHUNK` 增加摘要调用频率 → 摘要模型（本地与主模型**同卡**）挤占更频繁，**可能反而增加显存抖动**。建议 `CHUNK` 与 `max_pixels` **不要同时改**，以便归因。

### 建议 3（**中优先级 / 零代码**）：采用上游低 CPU 推流法（`6bb5a0a52`）
- **依据**：commit `6bb5a0a52`（08-15）新增 `doc/rtsp_streaming.md` "Low-CPU Mode"。
- **动作**：两步固定套路 ——
  1. **一次性预处理**：`ffmpeg -i in.mp4 -vf "scale='min(1280,iw)':-2" -c:v libx264 -preset medium -crf 23 -pix_fmt yuv420p -an out-h264.mp4`
  2. **循环推流用拷贝模式**：`ffmpeg -re -stream_loop -1 -i out-h264.mp4 -map 0:v:0 -c:v copy -an -f rtsp -rtsp_transport udp rtsp://127.0.0.1:8554/fire1`
- **前提**：源文件需已是兼容 H.264；拷贝模式**不能**与 `-vf`/`-preset`/`-tune`/`-b:v` 同用；**宽度上限 1280**（与建议 1 的 262144 px 预算同向）。
- **风险**：**低**。需注意 `-rtsp_transport udp`（issue #16 提到本地"好像只支持 tcp 传输的 rtsp 流"，需实测 udp/tcp 哪个在本环境更稳）。
- **收益**：消除持续转码 CPU 占用，**减少 WebUI/推流侧的卡顿与阻塞**（对应 issue #16 中 `Siyong1997` 反馈的"长时间卡顿和阻塞"）。

### 建议 4（**中优先级 / 需自研**）：补齐"失败帧回滚"（参考上游未合并 PR #36）
- **依据**：issue #35（+ PR #36，open，+377/-44，含 5 个回归测试）。本地当前**无**此保护。
- **动作**：在 `prompt` 构造或主模型请求失败时**回滚当前 turn**（不要留在 session state），并可选地加 recent-turn/recent-image 窗口（**注意 #36 默认 `0`=不限，必须显式启用**）。
- **前提**：本地 `live_adapter` 已拆模块，落点应在 `infer_loop.py` / `session.py`；**不要照抄**上游单文件 patch（结构已不一致）。
- **风险**：**中**。回滚语义若处理不当可能丢帧或重复计帧；需补本地回归测试（可仿 `test_context_overflow_bounds.py` 风格）。
- **收益**：消除**长跑雪崩**（一次失败→持续累积→上下文与显存双爆），这是 #23/#35 的共同机制。

### 建议 5（**可选 / 需验证**）：评估 llama.cpp 侧 KV cache 量化与 flash attention
- **依据**：§4.3 —— 本地 `-fit off` **关闭**了 flash attention，且无 KV 量化。上游靠 vLLM 的 `--enable-prefix-caching` 缓解，llama.cpp 侧对应手段是 `--cache-type-k q8_0 --cache-type-v q8_0` 与开启 FA。
- **前提**：需在 **RTX 5060 Ti / sm_120** 上验证稳定性（本地历史记录已提示 "llama.cpp sm_120 不稳 → 官方 bin" 的回退经验，见 `doc/deprecated/delivery-handoff-2026-07/material_digest.md` D42）。
- **风险**：**中-高**。可能触发 sm_120 kernel 问题或输出质量下降（KV 量化对长上下文尤其敏感）。
- **收益**：在 16K 上下文下显著降低 KV cache 显存占用，为多路视频/更长上下文腾出空间。
- **建议做法**：**独立实验分支 + 单独指标**，不要与建议 1/2 混在同一次变更里。

### 建议 6（**背景任务 / 非立即**）：评估自行转换新统一模型为 GGUF
- **依据**：§2.4 —— 官方 4 个量化权重**均不可用于 llama.cpp**；新统一模型的社区 GGUF **不存在**；本地仍停留在 `-Preview` 世代。
- **动作**：用 llama.cpp `convert_hf_to_gguf.py` 从 `jdopensource/JoyAI-VL-Interaction`（BF16，17.53 GB）转换 + 单独导出 mmproj，再自行量化（IQ4_NL + imatrix）。
- **前提**：需要 ~35 GB 可用磁盘、足够的 CPU 转换时间；需验证 `qwen3_vl` 架构在本地 llama.cpp 版本（b10155）的转换支持度。
- **风险**：**高**。可能转换失败、mmproj 视觉塔不兼容、量化后决策 token（`</silence>`/`</response>`/`</delegation>`）行为漂移——正是 issue #12 描述的症状类别。
- **收益**：若成功，一次性解决 G1（世代落后）+ 获得官方打磨过的新模型能力。
- **建议做法**：**先做小规模可行性验证**（只转 mmproj + 少量层），不要直接投入全量转换。

---

## 6. 来源链接清单

### 仓库与元数据
- 上游仓库：https://github.com/jd-opensource/JoyAI-VL-Interaction
- 本地 fork origin：`https://github.com/kuhaku9527/vl-interaction-dev.git`
- 论文：https://arxiv.org/abs/2606.14777
- 项目主页：https://joyai-vl-video-future-academy-jd.github.io/JoyAI-VL-Interaction/

### 关键 commit（2026-07-01 之后）
| SHA | 日期 | 标题 |
|---|---|---|
| `8d655a02f` | 2026-08-03 | docs: add quantized model links |
| `500a037f4` | 2026-08-03 | feat: support configurable linear backend |
| `6bb5a0a52` | 2026-08-15 | docs: add low-CPU RTSP streaming mode |
| `90bb195a1` | 2026-09-15 | Support system prompt overrides in streaming inference |
| `bcfab4411` | 2026-07-21 | feat: release Docker deployment for RTX 3090 and 5090 |
| `12db53341` | 2026-07-21 | docs: add bilingual navigation and deployment tuning |
| `60b117de5` | 2026-07-21 | docs: highlight optimized RTX 3090 and 5090 profiles |
| `fb0312e2a` | 2026-07-21 | Add service startup and smoke warmup helpers |
| `89fd54349` | 2026-07-22 | release: update model path and add Qwen3-VL benchmarks |
| `f4f2a611c` | 2026-07-22 | docs: update unified model defaults |
| `58722e94a` | 2026-08-05 | chore: add .env.example files |
| `f8bcde678` | 2026-08-25 | docs: add API usage guides |

### 关键文件（main 分支）
- `container/README.md`（三档 profile 表 + 调参附录）
- `container/16GB/.env.example` / `container/16GB/README.md`
- `container/24GB/.env.example`
- `services/webinfer/scripts/start_model.sh`（LINEAR_BACKEND）
- `services/webinfer/live_adapter.py`（2809 行；`max_pixels=262144`、`long_term_memory_window=40`）
- `doc/troubleshooting.md`

### Issues
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/12 （输出不连贯）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/13 （数据集工具）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/14 （基模选型）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/15 （5090 跑不动）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/16 （RTSP 显存暴涨）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/17 （后训练脚本）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/18 （预热）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/20 （多摄像头/配置高）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/23 （上下文累加报错）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/25 （PR：修无界上下文）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/26 （语音并入）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/28 （PR：修 #23；open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/32 （端侧量化）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/35 （失败帧累积；open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/36 （PR：#35 的修复；open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/38 （几路摄像头；open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/44 （EgoIT 数据）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/issues/50 （多路视频；open）

### PR
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/2 （唯一 MERGED）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/25 （CLOSED 未合并）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/28 （open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/29 （open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/31 （open）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/34 （CLOSED 未合并）
- https://github.com/jd-opensource/JoyAI-VL-Interaction/pull/36 （open）

### HuggingFace 模型
- https://huggingface.co/jdopensource/JoyAI-VL-Interaction （BF16，17.53 GB）
- https://huggingface.co/jdopensource/JoyAI-VL-Interaction-INT4 （7.55 GB，compressed-tensors）
- https://huggingface.co/jdopensource/JoyAI-VL-Interaction-INT8 （10.59 GB）
- https://huggingface.co/jdopensource/JoyAI-VL-Interaction-FP8 （10.59 GB）
- https://huggingface.co/jdopensource/JoyAI-VL-Interaction-NVFP4 （7.55 GB）
- https://huggingface.co/jdopensource/JoyAI-VL-Interaction-Preview （旧世代，本地来源）
- https://huggingface.co/Nasa1423/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF （**本地实际使用的 GGUF**，4.79 GB，最后更新 2026-06-20）
- https://huggingface.co/xiaowangzhixiao/JoyAI-VL-Interaction-MLX-4bit （Apple，base 为新统一模型）

### 本地对照文件（证据锚点）
- `services/webinfer/adapter_types.py:120`（`max_pixels: int = 1048576`）
- `services/webinfer/adapter_types.py:145`（`qa_history_window: int = 12`）
- `services/webinfer/adapter_types.py:169-170`（`long_term_memory_window=40`、`long_term_memory_max_tokens=1800`）
- `services/webinfer/prompt_constants.py`（`_CHARS_PER_TOKEN_BUDGET=3.0`、`_CTX_SAFETY_FACTOR=0.85`、`_PROMPT_GUARD_MIN_RECENT=2`）
- `services/webinfer/tests/test_context_overflow_bounds.py`（"upstream PR #25 port"）
- `services/scripts/run-windows.env`（`MAIN_CONTEXT=16384`、`MAIN_CTX_TOKENS=16384`）
- `install/download-gguf-models.ps1:149`（`Nasa1423/JoyAI-VL-Interaction-Preview-IQ4_NL-GGUF`）
- `install/windows/start-llama-server.ps1:75`（`-c 16384 -ngl 999 -fit off --jinja`）
- `services/webui/src/joy_interaction_webui/static/capture_webcam.js:44-45`（`1920×1080`）

---

**调研边界声明**：本报告未 clone 上游、未构建、未下载权重。issue 评论时间戳经 GitHub API 返回（部分渲染为 `null`，已在表中尽量标注）。所有"是否已修"判定基于 main 分支截至 `32add65c0`（2026-09-15）的文件内容与 PR 状态。
