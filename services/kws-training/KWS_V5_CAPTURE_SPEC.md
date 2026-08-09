# KWS v5 真人录音采集规格（Capture Spec）

> 子任务 A（issue #132）：KWS 定点 recall 49.06% → ≥90%、FAR ≤2%。
> 召回不足根因 = 正样本过少（当前 **53 段**）。49→90 **只能靠训练扩数据**，AI 无法合成替代真人声纹多样性。
> 本规格定义「真人录音」这一**用户侧阻塞项**：工具已就绪，需用户执行。
>
> ⚠️ **草稿**：本文件先给因果链与默认双源方案。**数据最终策略由用户（审查组）拍板**，
> 可能提升到 `决策/` 决策文档；本文件不写 `决策/`（审查组专属域）。

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

### 交叉引用

- 根因 / 阈值扫描无收益 / live 域样本强制必须（NVIDIA Broadcast 重塑）：`doc/specs/kws-recall-optimization.md` v3.20（第 5、37–44、63 行）。
- live 采集实现（已实现，勿再造）：`services/webui/src/joy_interaction_webui/jarvis_mode.py:185-189, 816-828`。
- 金样本标注逻辑：`services/scripts/analyze_kws_captures.py`（`analyze_one` 打 `file/kws_hit/asr_text`）。
- 双源摄入实现：`services/scripts/prep_kws_data.py`（`build_live_positives` / `--live-capture-dir` / `--live-filter`）。
- 一键编排：`services/kws-training/run_kws_v5.sh`。
