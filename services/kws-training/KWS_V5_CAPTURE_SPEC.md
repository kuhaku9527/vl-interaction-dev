# KWS v5 真人录音采集规格（Capture Spec）

> 子任务 A（issue #132）：KWS 定点 recall 49.06% → ≥90%、FAR ≤2%。
> 召回不足根因 = 正样本过少（当前 **53 段**）。49→90 **只能靠训练扩数据**，AI 无法合成替代真人声纹多样性。
> 本规格定义「真人录音」这一**用户侧阻塞项**：工具已就绪，需用户执行。
>
> ⚠️ **策略已拍板（见 §0.6）**：数据最终策略落点为**本 spec §0 草稿，不升 `决策/`**。
> 因果链见 §0.1–§0.6。端点对话只产 spec/adr（审查组 SSOT 纪律），故不写 `决策/`。

## 0. 背景与因果链

本规格的采集策略不是凭空定的，而是来自早期 spec **`doc/specs/kws-recall-optimization.md`（v3.20）** 的硬约束。
下面把「为什么必须双源」的因果链一次讲清，便于审查组拍板时追溯。

### 0.1 根因（来自 v3.20）

- v4 KWS 在现有正样本上实测 **recall = 49.06%**（`kws-recall-optimization.md` 第 5、41 行）。
- 全参数扫描（score × threshold）**没有任何组合能提升 recall/FAR 权衡**（第 37–44 行 sweeps 表）：
  `10.0/0.25 → 49.06%`、`8.0/0.25 → FAR 飙到 9%`、`12.0/0.25 → recall 掉到 13.21%`。
  → **阈值扫描无收益**，recall 不足是数据问题，不是阈值问题。
- v3.20 结论（第 16 行）：v5 必须**靠扩数据**——用 captured live samples 构建正样本 / 难负样本 / Broadcast-WebRTC 域样本。

### 0.2 为什么「只吃干净主动录音」修不好 live 漏唤醒

- v3.20 第 63 行（Further Notes）明确指出：**NVIDIA Broadcast 会重塑短辅音与尾静音**（降噪副作用）。
  这导致真正的 live 麦克风路径里的 "BT" 波形，和录音棚里干净主动录的 "BT" 不是同一分布。
- 模型只在干净主动录音上学，遇到被 Broadcast 重塑过的 live 短辅音就触发不了 → 这正是 49% 漏唤醒的来源之一。
- 因此：**单源（仅 `positive.jsonl` 主动录音）无法覆盖 live 域分布，扩再多干净样本也修不好 live 漏唤醒**。

### 0.3 为什么必须「双源」

正样本必须同时来自：

- **A. 主动录音**（`positive.jsonl` / `positive/`，由 `record_kws_corpus.py` 生成）——可控、干净、覆盖多人次/距离/音量/语速；
- **B. live 采集**（`kws_live_*.wav`，jarvis_mode 监听时自动落盘）——**真实 live 域分布**，含 Broadcast 重塑后的短辅音/尾静音，是修 live 漏唤醒的唯一来源。

二者合并后（`prep_kws_data.py` 的 `build_live_positives` 合并），整个正样本池再统一过 MUSAN 增广（混 SNR）。
详见 §6。

### 0.4 为什么 live 样本「已被实现」，无需另造采集工具

- live 采集机制**已实现并在跑**，不在这份 spec 的 scope 内再造：
  - `services/webui/src/joy_interaction_webui/jarvis_mode.py:185` `kws_capture_enabled=True`；
  - `:186` `kws_capture_dir = "D:/AI/data/kws/mic_captures"`；
  - `:816-828` `_write_kws_capture` 把语音窗口写成 `kws_live_{ts}_{seq}_peak{..}_rms{..}.wav`。
- 早期 capture spec（本文件旧版）**犯了个治理错误**：只定义了独立的主动录音工具 `record_kws_corpus.py`，
  完全没用这个已有的 live 流。这会让 v5 训练只吃干净主动录音、漏掉真正能修 49% 漏唤醒的 live 域样本。
- **本版修正**：`prep_kws_data.py` 现在双源摄入——主动录音保留，live 采集（`kws_live_*.wav`）作为第二正样本源合并进来。
  不再需要为 live 域另写采集工具。

### 0.5 金样本：ASR-shadow 命中 BT 的 live 样本（过滤策略待拍板）

- v3.20 第 30 行：ASR shadow 在 `KWS_LISTENING` 诊断运行，KWS 没触发但 ASR 听到 wake pattern 时打 `KWS MISS`。
- 含义：**「KWS 没触发、但 ASR shadow 听到 BT」的 live 样本，是最金贵的正样本**——它们正好是当前漏唤醒的实例。
- 当前**没有每文件的 BT 标注**。`services/scripts/analyze_kws_captures.py` 能批量把 wav 过「当前 KWS + ASR」，
  打出 `file / kws_hit / asr_text`，从而挑出 ASR 命中 BT 的样本。
- `prep_kws_data.py` 已实现**可选**过滤 `--live-filter {all,asr-bt}`（默认 `all`）：
  - `all`（默认）：把全部 live 采集当正样本，零额外依赖，可跑；
  - `asr-bt`：复用 `analyze_kws_captures` 标注逻辑，只保留 ASR 识别到 "bt" 的 live 样本（需其依赖，非默认路径）。
- **默认 `all`**——具体「是否只收金样本 / 如何定义金样本」留给用户拍板，不在本草稿硬编码。

### 0.6 已拍板数据策略（2026-08-09，主理人定方案，用户确认落点 = spec §0 草稿）

三决策点最终方案 + 因果链（非用户倾向，系按工程原则由主理人给出、用户接受）：

- **① live 摄入开关：保持 ON（`--use-live-captures` 默认开）**
  - 因果：49% 漏唤醒根因之一是 live 域（Broadcast 重塑）分布未被覆盖（§0.2）；live 采集（源A, `kws_live_*.wav`）正是该域唯一真实样本源，且 fail-open（目录空/无文件不崩）。关掉等于丢掉最值钱的数据。故保持开。

- **② 标签纯度：`all` 先跑 baseline，ASR 稳后切 `asr-bt`**
  - 因果：当前正样本极缺（53 段主动 + 24 live + 0 主动新增），首要矛盾是**样本量**。`all` 零依赖、把全部 live 当正样本，给最大训练量、最快出 baseline 模型；标签含静音/别词的噪点对 baseline 可接受。
  - `asr-bt` 标签干净但依赖 ASR，且 ASR 可能漏听真 BT（false negative）使训练样本变少——数据本就匮乏时直接用会饿死模型。故**第一版不启用**，待：① ASR 在训练环境稳定；② 主动录音量起来后，再切 `asr-bt` 提纯（届时即便 ASR 漏一些，总量也够）。
  - 修订认知：此前交流曾误写「用户倾向 asr-bt」——用户明确不懂因果、无倾向；本方案由主理人按「数据稀缺优先保量」原则给出。

- **③ 文档落点：spec §0 草稿（本文件）**，不升 `决策/`
  - 因果：属采集/训练工程策略，非架构级长期治理决定；审查组 SSOT 纪律要求端点对话只产 spec/adr，故留本草稿即可，不污染 `决策/`。

> 本方案解锁 PR #140 合并阻塞（此前「先不合」纪律因数据策略未定而挂起）。合并前置：可选修 `train_kws.py:107` numpy 崩溃隐患（main 既有 bug，非本 PR 引入）。

---

## 1. 目标数量（双源视角）

| 类别 | 当前 | v5 目标 | 说明 |
| --- | --- | --- | --- |
| 正样本 A：主动录音（说 "BT"） | 53 段 | **≥200 段真人录音** | MUSAN 增广再扩 3×；真人声纹多样性必须由真人提供 |
| 正样本 B：live 采集（`kws_live_*.wav`） | 0（未摄入） | **全部合并** | jarvis_mode 监听时自动落盘；真实 live 域分布，修漏唤醒关键 |
| 负样本（非 BT 语音/噪声） | 200 段 | 已够（MUSAN 再自动补 ~400 段） | 用户无需额外录负样本 |

**结论**：
- 主动录音：用户需至少补录 **~150 段** 新的真人 "BT"（53 + 150 = 203），覆盖多人次/多距离/多音量/多语速。
- live 采集：只要让 Jarvis 监听跑一段时间（见 §5 触发），`D:/AI/data/kws/mic_captures` 会自动累积 `kws_live_*.wav`，
  prep 默认全部摄入（§6）。监听越久、说 "BT" 越多，live 正样本越足。

## 2. 每人次 / 多样性矩阵（针对主动录音 A）

为最大化声纹与信道多样性，单人建议按以下组合各录若干遍（总量摊到 ≥200 段即可）：

- **距离**：~30cm / ~1m / ~2m 各若干
- **音量**：正常 / 偏大 / 偏小 各若干
- **语速**：正常 / 稍快 / 稍慢 各若干
- **建议**：2–3 人参与，每人 60–100 段，覆盖上述矩阵

> 不要连续念同一语调 200 遍——多样性 > 数量。
> live 采集 B 的多样性由真实使用场景自动提供（不同人/距离/设备/Broadcast 状态），无需人工设计。

## 3. 采样 / 格式（两类正样本一致）

### 主动录音 A（`record_kws_corpus.py` 固定，勿改）

| 项 | 值 |
| --- | --- |
| 采样率 | **16000 Hz** |
| 声道 | **mono（单声道）** |
| 位深 | **int16（PCM_16）** |
| 单段时长 | 0.3–3.0 s（能量 VAD 自动裁剪静音头尾） |
| 容器 | **WAV** |

### live 采集 B（jarvis_mode 自动落盘，勿改）

- 格式同样为 **16 kHz mono PCM16 WAV**（`jarvis_mode.py:828-833` 写盘）。
- 文件名含 peak/rms，便于后续按能量筛（如只收 peak 较高的真实语音窗口）。
- 与 sherpa-onnx KWS 训练/推理硬性要求（`kws.py: sample_rate=16000`）一致。

## 4. 落点目录（与训练管线契约一致，双源）

```
D:/AI/data/kws/bt-en/positive/        # 正样本 A wav（主动录音）
D:/AI/data/kws/bt-en/positive.jsonl   # 正样本 A manifest（record_kws_corpus.py 自动重建）
D:/AI/data/kws/mic_captures/          # 正样本 B wav（jarvis_mode 自动落盘 kws_live_*.wav）
D:/AI/data/kws/bt-en/negative/        # 负样本 wav
D:/AI/data/kws/bt-en/negative.jsonl   # 负样本 manifest
```

> ⚠️ **数据卫生（主动录音 A）**：当前 `positive.jsonl` 列 53 条但 `positive/` 仅 50 个 wav（3 条过期）。
> 重跑采集脚本会用目录下真实 wav **重建** manifest，自动消解该不一致。
> 之后 `prep_kws_data.py` 会校验 manifest 引用的 wav 必须存在，缺失即显式报错（fail-fast）。
>
> live 采集 B 由 jarvis_mode 自动维护，`prep_kws_data.py` 用 glob `kws_live_*.wav` 摄入；
> 目录缺失/为空 → **fail-open 跳过**（不影响主动录音训练），见 §6。

## 5. 采集命令（两类正样本）

### 5.1 主动录音 A（工具就绪）

Windows（建议在 `D:\AI\envs\joyai-sherpa` 环境；先 `pip install sounddevice soundfile numpy`）：

```powershell
# 正样本：录到 ≥200 段（脚本会跳过已存在的，可分批累加）
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\record_kws_corpus.py `
    --label positive --count 200

# 负样本（可选，已有 200 段，通常不必补）
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\record_kws_corpus.py `
    --label negative --count 200
```

WSL2（拾音设备走 Windows 侧，录制在 WSL2 内跑同理）：

```bash
~/kws-train/bin/python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/scripts/record_kws_corpus.py \
    --label positive --count 200
```

交互：按 Enter 开始 → 说 "BT" → 自动裁剪静音 → 写 wav；`Ctrl+C` 中断。

### 5.2 live 采集 B（无需手工录，监听自动落盘）

- 启动 Jarvis 监听（`kws_capture_enabled=True` 默认开），在 `KWS_LISTENING` 状态说若干遍 "BT"。
- 语音窗口自动写入 `D:/AI/data/kws/mic_captures/kws_live_*.wav`（见 §0.4 引用行号）。
- v3.20 建议的手动测试：监听激活时说 10–20 遍 "BT"，再回看 `webui.err.log` 与 `kws_live_*.wav`。
- 可选：跑 `services/scripts/analyze_kws_captures.py` 批量过 KWS+ASR，看哪些 live 样本是 "KWS 没触发但 ASR 听到 BT" 的金样本。

## 6. 如何纳入训练管线（prep 双源清单）

`record_kws_corpus.py` 每次运行会扫描 `positive/` 下全部 `*.wav` 并**重建** `positive.jsonl`
（字段：`id, audio, duration, sampling_rate, channels, text="BT", tokens="B T", keyword="bt"`）。

`prep_kws_data.py` **双源摄入**正样本：

1. **主动录音 A**：直接读 `positive.jsonl` + `negative.jsonl`（fail-fast 校验 wav 存在）。
2. **live 采集 B**：`build_live_positives` 摄入 `kws_live_*.wav`（默认 `--live-capture-dir D:/AI/data/kws/mic_captures`，
   `--use-live-captures` 默认 on）：
   - **fail-open**：目录缺失/无 `kws_live_*.wav`/样本损坏 → 跳过并 `logger.warning`，不阻断管线；
   - 合并后总正样本 = 主动录音 + live 采集；
   - `--live-filter {all,asr-bt}`（默认 `all`）：`all`=全部当正样本（零额外依赖）；
     `asr-bt`=复用 `analyze_kws_captures` 标注逻辑，只保留 ASR 命中 "bt" 的样本（策略待用户拍板）。
3. **增广**：MUSAN 混 SNR（`--aug-per-pos`，默认 3）对**整个正样本池**（含 live）生效，把每段扩到 3 段混噪版本；
   MUSAN 还自动生成 ~400 段负样本（noise/music/speech 切 2s 片段）。
4. MUSAN 缺失 → **fail-open**：仅用录制数据训练，不阻断。

一键训练见 `services/kws-training/run_kws_v5.sh`（已带入 `--live-capture-dir` / `--live-filter` 透传，
对应环境变量 `KWS_LIVE_CAPTURE_DIR` / `KWS_LIVE_FILTER`）。

## 7. 验收（训练后）

```bash
# 用导出的 bt-en 模型重测 recall / FAR
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\test_jarvis_kws_e2e.py
# 另可 analyze_kws_captures.py 分析 capture 分布（含 KWS MISS / ASR 命中 BT 的金样本）
```

目标：定点 recall ≥90%、FAR ≤2%（阈值仍为 score=10.0 / th=0.25，ADR 0002）。

---

## 8. 录音采集实验设计（用户方案，待确认）

> 2026-08-09 用户提出，主理人已给判断；数据策略已由 §0.6 拍板，本节目实验设计为佐证（落点 = spec §0 草稿，不升 `决策/`）。

### 8.1 用户提案（已修正）：单一变量 = 麦克风设备选择

- **变量（仅一个，声学域）**：**NVIDIA Broadcast 虚拟麦 vs GameDAC Chat 物理麦**。
  - `麦克风 (NVIDIA Broadcast)` = GameDAC Chat 原始音 → 经 NVIDIA Broadcast 降噪/回声消除后的**虚拟输出设备**；短辅音/尾静音被 AI 重塑，是 live 漏唤醒的真实域；
  - `麦克风 (GameDAC Chat)` = **原始物理麦**，不经过 Broadcast，保留更多呼吸/吞咽/环境杂音。
- **“Broadcast 开/关”在 Windows 声音设置里就体现为“选哪个录音设备”**：选 NVIDIA Broadcast = 开降噪；选 GameDAC Chat = 不开。**两个设备可同时存在**，不同程序可接不同麦。
- **采集路径**：仅走 `record_kws_corpus.py` 脚本（auto-feed 进 `positive/`），用 `--device <index>` 指定要录的麦。**去掉 Windows 录音机轴**（用户 2026-08-09 确认：该轴归一化后洗净、非模型变量，仅作可选对照，不纳入主方案）。
- 模型实际见到的域 = **2 个（Broadcast 降噪后 / GameDAC Chat 原始）**，不是 4 个。

### 8.2 主理人判断（修正认知）

- **NVIDIA Broadcast vs GameDAC Chat 是真正的声学域变量**，必须保留：改变的是模型实际听到的波形分布（降噪重塑 vs 原始保真），属于 v3.20 §0.2 明令要覆盖的 live 域。✅
- **采集工具（脚本 vs Win 录音机）不是模型训练变量，是采集链路/校验轴**：`prep_kws_data.py` 训练时统一重采样到 16 kHz mono，工具信号归一化后基本洗净；真实差异只在「部署链路匹配」。⇒ 用户已采纳：**去掉 Win 录音机轴，主方案仅两麦 + 脚本采集路径**。
- **主动录 BT（源B）与 真实 live 自动采集（源A, §0.4/§5.2）是两条独立收集路**：本方案变量只作用于源B 主动录音；源A 由 jarvis_mode 在 KWS_LISTENING 时被动落盘 `kws_live_*.wav`，不挑麦克风设置、自动累积真实域样本（正是修 49% 的关键），与主动录音正交。
- **两域平等、无主次之分（2026-08-09 用户强调「只要识别率高，无偏好偏差」）**：
  - 用户物理事实：**未降噪的 GameDAC Chat 声纹振动更大、更明显**，信号保真度高；降噪（Broadcast）虽压掉环境噪，但会重塑短辅音/尾静音（§0.2 已知是漏唤醒来源之一），对 NN 识别未必更优。**故不能预设哪个域更重要**。
  - **不再设「主/副域数量分配」**：两域各 ≥100 段**等量**录，让模型对两个域都充分学习。
  - **部署设备选择 = 数据驱动，非预设**：Jarvis 接 NVIDIA Broadcast 还是 GameDAC Chat 可单独选（两设备并存）；最终接哪个由**验收实测 recall/FAR 决定**——哪个麦上 v5 模型识别率高就接哪个（§9.5 验收可分别测两设备）。
  - 此双域覆盖原则同样适用于 ASR（同一语音信号，降噪对 ASR 也是「重塑 vs 保真」的权衡），但本 spec 仅管 KWS 采集。

### 8.3 待澄清 / 注意

- **数量口径（已定）**：主动录音（源B）按 **总量 ≥200 段** 摊（§1 约定），**两域等量各 ≥100 段**（无主次之分）；与 §2 多样性矩阵（距离/音量/语速）正交，可叠加。
- **采集入口事实澄清**：**没有前端网页"录制 BT 语料"的入口**——`index.html` 的 `getUserMedia` 仅用于 jarvis_mode 实时监听（喂 KWS/ASR 引擎），语料**不落盘**。主动录音（源B）唯一入口是 **`record_kws_corpus.py`**（Windows 脚本，见 §9）；live 自动采集（源A）由 jarvis_mode KWS_LISTENING 监听被动落盘（§5.2）。
- **Win 录音机格式**：默认录制多为 44.1k/48k 立体声，须落入 `D:/AI/data/kws/bt-en/positive/`（或经 corpus 脚本重建 manifest），训练侧 lhotse 会重采样到 16k；勿直接丢 `mic_captures/`（那是 live 源 A 目录）。

### 交叉引用

- 根因 / 阈值扫描无收益 / live 域样本强制必须（NVIDIA Broadcast 重塑）：`doc/specs/kws-recall-optimization.md` v3.20（第 5、37–44、63 行）。
- live 采集实现（已实现，勿再造）：`services/webui/src/joy_interaction_webui/jarvis_mode.py:185-189, 816-828`。
- 金样本标注逻辑：`services/scripts/analyze_kws_captures.py`（`analyze_one` 打 `file/kws_hit/asr_text`）。
- 双源摄入实现：`services/scripts/prep_kws_data.py`（`build_live_positives` / `--live-capture-dir` / `--live-filter`）。
- 一键编排：`services/kws-training/run_kws_v5.sh`。

---

## 9. 数据闭环 Runbook（常驻 — 之后会反复跑）

> 本节目「采集 → 训练 → 导出 → 验收」全链路，固化为本 spec 常驻章节（2026-08-09 补）。
> 立项以来会反复跑：每次补录语料 / live 累积后，都要重跑本链路刷模型。
> 环境已实测就绪（见 `docs/kws-training-manual-local.md` §0.1），**无需重装依赖**。

### 9.1 环境（实测就绪，不要重建）

训练在 **WSL2 用户 `ku` 的 `~/kws-train` venv** 跑（不在 Windows `D:/AI/envs/*`，也不在 WSL `ai-base`）。实测栈：

```
torch 2.12.1+cu130 | torchaudio 2.11.0+cu130 | lhotse 1.33.0 | k2 OK | sherpa_onnx 1.13.4 | onnx 1.22.0 | onnxruntime 1.27.0
GPU: NVIDIA RTX 5060 Ti (sm_120) WSL 透传 OK；30 epoch 约 5-10 min
```

> ⚠️ 激活用 `source ~/kws-train/bin/activate`（WSL 内），不是 Windows 的 `D:\AI\envs\*`。
> 拾音（主动录音）走 Windows 侧 `record_kws_corpus.py`（见 §9.2）；训练/导出全在 WSL2 内。

### 9.2 采集（主动录音，源B — 用户侧）

Windows PowerShell（环境 `D:\AI\envs\joyai-sherpa`，需 `sounddevice soundfile numpy`）：

先列设备（你当前环境实测）：

```powershell
& "D:\AI\envs\joyai-sherpa\python.exe" -c "import sounddevice as sd; [print(f\"{d['index']}: {d['name']}\") for d in sd.query_devices() if d['max_input_channels']>0]"
```

当前关键设备（以你截图为准）：

```
1: 麦克风 (NVIDIA Broadcast)   # AI 降噪/回声消除后的虚拟麦
3: 麦克风 (GameDAC Chat)        # 原始物理麦（声纹振动更完整）
```

录两批（每批保持同一设备，**两域等量各 ≥100 段**，无主次）：

```powershell
# 第一批：NVIDIA Broadcast 域（≥100 段，--data-root 隔离到独立子目录）
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\record_kws_corpus.py `
    --label positive --count 100 --device 1 --skip-existing --data-root D:/AI/data/kws/bt-en/broadcast

# 第二批：GameDAC Chat 原始域（≥100 段）
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\record_kws_corpus.py `
    --label positive --count 100 --device 3 --skip-existing --data-root D:/AI/data/kws/bt-en/gameDAC
```

- 每按一次 Enter 说一句 "BT"，脚本自动裁剪静音、写 wav。
- **⚠️ 必须带 `--skip-existing`**：脚本默认（不带此 flag）重跑时会从 `positive_0001.wav` **从头覆盖**已录文件（计数归零）；带 `--skip-existing` 才按"已有 N 条 → 只补录缺的"累加（今天录 60，明天再 `--count 40` 补到 100）。
- **两域等量，不预设主次**：用户明确「只要识别率高、无偏好偏差」；GameDAC Chat 声纹更完整，Broadcast 是常见部署场景，两者都要覆盖。
- **Jarvis 接哪个麦事后由实测决定**：验收阶段（§9.5）分别用两个设备测 recall/FAR，识别率高的作为最终部署设备。
- 负样本已有 200 段，通常不必补；要补同理 `--label negative`。
- **录完跑合并**：两批录完后用 `assemble_kws_corpus.py` 一键合并训练池 + 切冻结留出集（见 §10.1），不要手动平铺。

> ⚠️ **切换的是设备，不是软件开关**：NVIDIA Broadcast 应用保持打开、噪音消除保持开启；只要 Windows 录音设备选 NVIDIA Broadcast，录到的就是降噪后音源。GameDAC Chat 则完全不经过 Broadcast。

### 9.3 live 自动采集（源A — 被动，零操作）

- 正常用 Jarvis（KWS_LISTENING 监听开，`kws_capture_enabled=True` 默认开）即可。
- `D:/AI/data/kws/mic_captures/kws_live_*.wav` 自动累积真实 live 域样本（含 Broadcast 重塑域）。
- 不用你特意录；监听越久、说 "BT" 越多，live 正样本越足。训练时由 `prep_kws_data.py` 自动摄入（§6）。

### 9.4 训练 + 导出（WSL2 内一键）

```bash
# WSL2 用户 ku 家目录
source ~/kws-train/bin/activate

# 一键：prep（双源 + MUSAN 增广）→ train（30 epoch）→ export ONNX
bash /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/run_kws_v5.sh
```

- `run_kws_v5.sh` 已透传 `--live-capture-dir` / `--live-filter`（对应 `KWS_LIVE_CAPTURE_DIR` / `KWS_LIVE_FILTER`）。
- 当前策略：标签纯度先用 **`all`**（§0.6 ②）；要提纯时设 `KWS_LIVE_FILTER=asr-bt`（需 ASR 在训练环境可用）。
- 产物：`D:/AI/models/sherpa-onnx/models/kws/bt-en/{encoder,decoder,joiner}.onnx` + `tokens.txt` + `keywords.txt`。

> 手动分步（调试用，等价于一键）：
> ```bash
> python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/scripts/prep_kws_data.py \
>     --data-root /mnt/d/AI/data/kws/bt-en --test-ratio 0.2
> python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/train_kws.py \
>     --manifests-dir /mnt/d/AI/data/kws/bt-en/manifests --exp-dir /mnt/d/AI/data/kws/bt-en/exp --num-epochs 30
> python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/export_kws_onnx.py \
>     --ckpt /mnt/d/AI/data/kws/bt-en/exp/best.pt --out-dir /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en
> ```

### 9.5 验收 + 推理自测

```bash
# 1) sherpa-onnx 加载 + 检测自测（用仓库既有脚本，别自己造轮子）
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\test_sherpa_load.py `
    --encoder D:/AI/models/sherpa-onnx/models/kws/bt-en/encoder.onnx `
    --decoder D:/AI/models/sherpa-onnx/models/kws/bt-en/decoder.onnx `
    --joiner  D:/AI/models/sherpa-onnx/models/kws/bt-en/joiner.onnx `
    --tokens  D:/AI/models/sherpa-onnx/models/kws/bt-en/tokens.txt `
    --keywords D:/AI/models/sherpa-onnx/models/kws/bt-en/keywords.txt `
    --test-wav D:/AI/data/kws/bt-en/test_bt.wav
# 期望：稳定 HIT 'bt'

# 2) JARVIS 端到端 recall/FAR（需 webui 配合，目标 recall≥90% / FAR≤2%）
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\test_jarvis_kws_e2e.py
```

### 9.6 已知未跑 / 待补（诚实标注，非阻塞）

- **JARVIS webui 端到端**（`test_jarvis_kws_e2e.py` 需 webui 跑起来）— 至今未在本机跑过。
- **MUSAN 实测**：prep 默认 fail-open，本机当前未装 MUSAN，纯录制数据训练。
- **int8 量化**：当前 ONNX float32（encoder ~56 MB），未量化（对比 `zh-en-3M` int8 仅 4.6 MB）。
- **mic_captures 喂训练效果**：代码已支持（§6），但 v5 双源迄今未实测端到端 recall 提升数字。
- 详细链路/踩坑/实测见 `docs/kws-training-manual-local.md` 与 `docs/kws-training-manual-local-reality-check.md`（Codex 实跑记录，SSOT 补）。

---

## 10. 回归测试与验收闭环（常驻 — 防"准确率不如意才临时设计"）

> 用户 2026-08-09 明确：不要等准确率不如意才开始设计调整策略；要事前把**纠错 / 调整变量 / 整套路归测试**固化成闭环。
> 本节目"事后补救"改"事前约定"。企业级 ML 流程（数据版本化 + 冻结测试集 + CI 门禁 + 实验登记 + 模型注册表）对单人项目过重，**只借最必要的四件**：冻结留出集、按域验收、运行记录、调整 playbook。

### 10.0 现状盘点（诚实）

**已有的（可复用，别再造）：**
- `services/kws-training/test_kws.py`（WSL2）：加载 sherpa 模型 → 评估 `<manifests-dir>/positive_test.jsonl.gz` / `negative_test.jsonl.gz`（默认 `manifests/`；冻结留出集用 `--manifests-dir .../test_manifests` 指向 §10.1 生成的集）→ 输出 recall + FAR + per-file 详情，且**已内置基础调整提示**（recall<80% → 加正样本/调阈值；FAR>10% → 加负样本/调阈值）。
- `services/scripts/kws_param_sweep.py`：扫 (score, threshold) 找 recall/FAR 最优权衡。
- `manifests/positive_test.jsonl.gz` / `negative_test.jsonl.gz`：历史 v4 时代留出集（**positive_test 仅 10 条 / negative_test 40 条，且无 device 标签**）。

**缺的（闭环未闭合）：**
1. **测试集陈旧且小**：10 正 / 40 负，来自旧分布，不代表 v5 双域数据；未随 v5 重训刷新。
2. **无按域拆分**：manifest 无 `device` 字段，无法分别测 NVIDIA Broadcast / GameDAC Chat 的 recall/FAR —— 而你恰恰要靠这个决定 Jarvis 接哪个麦。
3. **无运行记录**：每次评估只打印，不落表，跑两次没法对比。
4. **无验收门禁**：`test_kws.py` 只打印警告，不 `sys.exit(非0)`，无法当 CI/手动 gate。
5. **无模型版本标注**：导出目录同名覆盖，重训后分不清哪版。

### 10.1 冻结留出集（按域，永不作为训练）

- **推荐（一键）**：录完两批后，用 `assemble_kws_corpus.py` 一次完成「合并训练池 + 切割冻结留出集 + 生成 manifest」（零手动复制）：
  ```bash
  source ~/kws-train/bin/activate
  python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/scripts/assemble_kws_corpus.py \
      --src-roots /mnt/d/AI/data/kws/bt-en/broadcast /mnt/d/AI/data/kws/bt-en/gameDAC \
      --out /mnt/d/AI/data/kws/bt-en
  ```
  - 它把两域正样本合并进 `<out>/positive/`（训练池，audio 路径已重写供 `prep_kws_data.py` 消费），从每域取前 `--holdout`(默认 15) 段**复制**进 `<out>/test/positive/{nvidia_broadcast,gameDAC_chat}/`，负样本取 `--neg-holdout`(默认 15) 进 `<out>/test/negative/`，最后调用 `build_test_manifest.py` 生成 `<out>/test_manifests/positive_test.jsonl.gz` + `negative_test.jsonl.gz`。
  - **Windows / PowerShell 版**（合并脚本是纯标准库，无需开 WSL2，用 `joyai-sherpa` 环境即可）：
    ```powershell
    # 先 dry-run 看计划、不写文件（确认无误再去掉 --dry-run 真跑）
    & "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\assemble_kws_corpus.py `
        --src-roots D:/AI/data/kws/bt-en/broadcast D:/AI/data/kws/bt-en/gameDAC `
        --out D:/AI/data/kws/bt-en --dry-run

    # 真跑（合并训练池 + 切冻结留出集 + 生成 test_manifests/）
    & "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\assemble_kws_corpus.py `
        --src-roots D:/AI/data/kws/bt-en/broadcast D:/AI/data/kws/bt-en/gameDAC `
        --out D:/AI/data/kws/bt-en
    ```
    > 真跑后检查：`<out>/positive/`(训练池)、`<out>/test/positive/nvidia_broadcast/` + `gameDAC_chat/`(各 15 段)、`<out>/test/negative/`、`<out>/test_manifests/positive_test.jsonl.gz` + `negative_test.jsonl.gz` 都应生成。
  - **关键：build 输出到独立 `test_manifests/`，不与 `manifests/` 同名互踩**——`prep_kws_data.py` 的 train/test 拆分也写 `manifests/positive_test.jsonl.gz`；若二者都写 `manifests/` 会互相覆盖，回归门禁（`test_kws.py`）读到的就不是真冻结集。
  - 参数：`--holdout` / `--neg-holdout` / `--no-build`（跳过 build）/ `--dry-run`（只打印计划）/ `--force`（覆盖已存在 test/ wav）/ `--device-map key=value`（覆盖域→device 映射）。
- **手动替代**（破坏性文件操作按用户纪律由你自己做）：从两域各复制 ≥15 段 BT 到 `test/positive/{nvidia_broadcast,gameDAC_chat}/`（**复制、不移动**——保留 `positive/` 原样本供训练，test/ 是独立冻结副本，训练绝不吃），负样本复制 ≥40 段入 `test/negative/`，再：
  ```bash
  source ~/kws-train/bin/activate
  python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/build_test_manifest.py \
      --test-root /mnt/d/AI/data/kws/bt-en/test --out /mnt/d/AI/data/kws/bt-en/test_manifests
  ```
  > 注：`build_test_manifest.py` 已实现并合入 main（`services/kws-training/build_test_manifest.py`，轻量脚本：从 `test/` 扫 wav + 按子目录名生成带 device 标签的 gz manifest）；附带 `test_build_test_manifest.py`（CI 的 pytest 矩阵与 ruff 门禁均不含 `kws-training`）。`assemble_kws_corpus.py` 同样已实现合入（`services/scripts/assemble_kws_corpus.py` + `test_assemble_kws_corpus.py`，4 用例）。
- 留出集**只增不删、不训练**，保证跨次评估可比。

### 10.1.1 FAR 门禁精度调整（负样本分辨率）

§10.2 的 FAR 门槛是 ≤2%，但 FAR 的**最小可测粒度 = 1/验收集负样本数 N**：

- 默认 `--neg-holdout 15` → 粒度 6.7%，**比 2% 门槛还粗**，门禁量不出 FAR 是否达标却会"静默通过"——这是真实精度缺陷。
- **调整方案（录多少 + 怎么切）**：
  1. **录负样本**：建议 ≥50 条（粒度 ≤2%，刚好能分辨 2% 门槛）；最少 40 条（粒度 2.5%，门禁等价于"零误接受"）。负样本不按域分组，用任一麦录 `--label negative` 即可（如 `--count 40 --device 1`）。
  2. **切分出集**：合并时显式 `--neg-holdout <N>`（N = 录的条数），让全部录的负样本进冻结验收集：
     ```powershell
     # 先删派生根 manifest（防 170→340 重复，见下方坑），再跑
     rm -f D:/AI/data/kws/bt-en/positive.jsonl D:/AI/data/kws/bt-en/negative.jsonl
     & "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\assemble_kws_corpus.py `
         --src-roots D:/AI/data/kws/bt-en/broadcast D:/AI/data/kws/bt-en/gameDAC `
         --out D:/AI/data/kws/bt-en --neg-holdout 40 --force
     ```
     > ⚠️ **重跑坑**：`assemble_kws_corpus.py` 会"载入既有 `positive.jsonl/negative.jsonl`"再叠加 src 根数据。若不先删根 manifest，重跑会把同一批正样本叠加进训练池 → manifest 计数翻倍（170→340，wav 重复）。删了根 manifest 从 src 根干净重建即可，wav 原样重拷、零损失。
  3. **验收脚本内置分辨率透明守卫**（`test_kws.py` 的 `compute_metrics`）：当 `1/N > bar_far` 时打印 `⚠️ 门禁分辨率提示`，显式说明门禁等价于"零误接受"，**不阻塞、不改变 pass/fail**（零误接受的合格跑仍 PASS）。从此不再静默粗粒度放行。
- **本轮（2026-08-10）实际配方**：录 40 条负样本，`--neg-holdout 40` → 训练负 40 + 冻结验收集负 40，粒度 2.5%（门禁等价于"零误接受"）。正样本每域 100（训练 85+留出 15），冻结验收集共 正 30 + 负 40。

### 10.2 统一验收脚本（按域 recall/FAR + 总 + 门禁）— 已实现

- **状态**：已实现并合入（`services/kws-training/test_kws.py` + `test_test_kws.py`；commit `e156f35` 主体 + `d0b8b4e` 补 fail-closed 守卫；审查门禁 APPROVE 无 blocking）。
- **行为**：`test_kws.py` 加载模型 → 逐条测命中 → 调 `compute_metrics` 按 `device` 分组输出
  `recall_broadcast / recall_gameDAC / recall_overall / FAR_overall`，末尾断言
  **recall_overall ≥ bar_recall(默认 0.9) 且 FAR_overall ≤ bar_far(默认 0.02)** → 不达标打印 `FAIL`+原因并 `sys.exit(1)`（验收门禁），达标打印 `PASS`。
- **fail-closed**：正样本或负样本为 0 条 → `logger.error` + `sys.exit(1)`，**不会以空集假通过**（N1 已修）。
- **参数**：`--model-dir`(必填) / `--manifests-dir`(传 `test_manifests`) / `--bar-recall 0.9` / `--bar-far 0.02` / `--num-threads`。
- **单测**：`test_test_kws.py`（stdlib unittest，纯逻辑不依赖 sherpa/音频）4 用例全绿，覆盖按域分组 + 门禁边界（recall/FAR 等号边界）+ 空集兜底。
- 跑法（验收阶段，WSL2 训练导出后）：
  ```bash
  source ~/kws-train/bin/activate
  python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/test_kws.py \
      --model-dir /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en \
      --manifests-dir /mnt/d/AI/data/kws/bt-en/test_manifests && echo "验收通过"
  ```
  > Windows / PowerShell（仅跑单测门禁逻辑，模型推理需在 WSL2）：
  > ```powershell
  > & "D:\AI\envs\joyai-sherpa\python.exe" -m unittest services\kws-training\test_test_kws.py -v
  > ```

### 10.3 运行记录表（轻量实验追踪）

每次「加数据 + 重训 + 验收」后，append 一行到 `services/kws-training/REGRESSION_LOG.md`：

```
| 日期 | 模型 tag | 数据配方(域:段) | 参数(epoch/lr) | recall_broadcast | recall_gameDAC | FAR | 总recall | 结论 |
|------|----------|----------------|---------------|------------------|----------------|-----|----------|------|
| 2026-08-09 | bt-en-v5-0809 | B:100 G:100 + live | 30/ep | 92% | 88% | 1.5% | 90% | PASS |
```

- 不引入 MLflow/DVC；markdown 表即"单人实验登记"，够用。
- **纪律**：未记此表、未跑 §10.2，不得宣称"模型更好"。

### 10.4 调整 playbook（决策树，按域）

验收不达标时**按表查因**，不临时拍脑袋：

| 现象 | 优先动作 |
|------|---------|
| `recall_broadcast` < 90% | 加 NVIDIA Broadcast 域正样本（重录该域）；检查该域静音裁剪是否过激 |
| `recall_gameDAC` < 90% | 加 GameDAC Chat 域正样本；检查原始麦电平是否过低 |
| `FAR_overall` > 2% | **首要：加负样本**（装 MUSAN 让 prep 自动补 ~400 / 或重录 ~200 负 / 或合成增强）。`keywords_threshold` 升阈值只能边际压 FAR（0810 实测：0.25→0.9 仅 100%→87.5%，救不回）→ 阈值调参是次要手段，不是根因解。查 `negative` 是否误含 "bt" |
| 两域 recall 都低 | 加 epoch / 查 lr；查标签纯度（live 用 `all` 是否引入太多静音→切 `asr-bt`）；查 train/valid manifest 时长越界 |
| 单域 recall 波动大 | 该域录音多样性不足（距离/音量/语速），补多样性而非单纯加量 |

### 10.5 模型版本标注

导出目录按配方命名，避免覆盖分不清：
`D:/AI/models/sherpa-onnx/models/kws/bt-en-v5-YYYYMMDD-{recipe}/`
（如 `bt-en-v5-0809-B100G100live`）；`bt-en`（无后缀）仅作"当前生效"软链/副本。

### 10.6 闭环纪律（一句话）

**每次加数据 + 重训 → 必跑 §10.2 验收 + 记 §10.3 一行 → 对比上一行 → 才断言"更好"。**
准确率不如意时，翻 §10.4 按域查因，而非从头设计。
