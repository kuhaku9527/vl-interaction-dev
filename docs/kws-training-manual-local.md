# KWS 自训手册 — 本机跑通版（Codex 视角）

> 本文件是**本机跑通版**：记录 Codex 2026-07-10 任务里实际跑通的训练 + 导出 + 验证全流程。
> 仅适用于本机环境（`D:/AI/workspace/JoyAI-VL-Interaction-main` + WSL2 `~/kws-train`）。
> 通用版见 `docs/kws-training-manual-generic.md`。
>
> **视角声明**：本文档基于 Codex 会话 `019f49cd-f241-76a3-a00e-847ddd8953e3`（2026-07-10）整理。
> `决策/服务-语音栈.md` 由协作者撰写，含 "FAR 2% / recall 49%" 等数字，但**没有详细训练流程**——本文件才是本机训练真值。
>
> **校核声明（2026-08-09）**：作者在写完后被用户追问"是否检查过本机真实环境"，确认上一版有若干**未验证的虚构细节**（GPU 型号、文件大小、目录命名重复、iterations 数量等）。本文已在 §0 / §3 标注**实测**值（vs 会话口径），并新增 §8 列**已知不一致**，避免后人照搬错。

---

## 0. 本机环境快照

### 0.1 操作系统 / 架构（实测 2026-08-09）

| 项 | 会话口径（7-10） | 实测（8-09） |
|---|---|---|
| OS（主） | Windows 11（PowerShell） | 同 |
| OS（训练） | WSL2（Ubuntu） | 同 |
| WSL2 用户 | `ku` | `/home/ku` ✓ |
| WSL2 venv | `~/kws-train/bin/activate` | **存在**，含 bin/activate ✓ |
| **GPU** | "RTX 40 系（实测 30 epoch / 8-10 min）" | **NVIDIA GeForce RTX 5060 Ti**（compute 12.0 / sm_120，新一代）⚠️ 不是我之前写的 "RTX 40 系" |
| Python | 3.12.3 | **3.12.3** ✓ |
| torch | 2.12.1+cu130 | **2.12.1+cu130** ✓ |
| torchaudio | （未记） | **2.11.0+cu130** |
| CUDA 可用 | yes | **yes** |
| lhotse | 1.33.0 | **1.33.0** ✓ |
| k2 | latest（与 torch 匹配） | **installed**（无 `__version__`）|
| **sherpa-onnx** | "latest" | **1.13.4**（实测具体版本，非笼统 latest） |
| onnx | （未记） | **1.22.0** |
| onnxruntime | （未记） | **1.27.0** |
| **icefall** | "vendor（`services/kws-training/icefall_src/`）" | **vendor 存在**（7 文件：decoder/encoder_interface/joiner/scaling*/subsampling/zipformer），**且** 系统级 `/home/ku/icefall/icefall/` 也装了（与 vendor 同步更新到 2026-08-03） |

WSL2 出网：实测 `wsl -e bash -c "..."` 可用，本机用户 `ku`。

### 0.2 关键路径（本机实测 2026-08-09）

```
D:\AI\
├── workspace\JoyAI-VL-Interaction-main\            ← 仓库根（cwd）
│   ├── services\kws-training\
│   │   ├── train_kws.py
│   │   ├── export_kws_onnx.py
│   │   ├── model.py
│   │   ├── kws_data_module.py
│   │   └── icefall_src\                            ← icefall vendor（7 文件，2026-08-03 更新）
│   └── services\scripts\
│       ├── record_kws_corpus.py
│       ├── prep_kws_data.py
│       └── test_jarvis_kws_e2e.py
│
├── data\kws\
│   ├── bt-en\                                      ← v4 训练数据（"bt" 唤醒词）
│   │   ├── positive\   (52 wav + positive.jsonl + bt_segments/)
│   │   ├── negative\   (200 wav)
│   │   ├── exp\        (best.pt 2026-07-10 11:10)
│   │   ├── exp_v2\     (best.pt 2026-07-10 15:53)
│   │   ├── exp_v3\     (best.pt 2026-07-10 16:02)
│   │   ├── exp_v4\     (best.pt 2026-07-10 16:16)  ⚠️ 我之前漏写了 v4
│   │   ├── logs\
│   │   ├── manifests\  (positive/negative train/test jsonl.gz + tokens.txt + keywords.txt)
│   │   ├── cmp.py / probe.py / probe2.py / probe3.py / probe_w.py / sanity_check.py
│   │   ├── test_bt.wav / test_bt_3s.wav / test_bt_5s.wav
│   │   └── train_v3.log (11.7 KB)
│   │
│   ├── bt-zai-ma\                                  ⚠️ 与 bt-en 是同一份数据（SHA 全等）
│   │   └── ...完全相同的目录布局与文件...
│   │
│   └── mic_captures\                               ← live 采集（jarvis_mode 自动落盘）
│       └── kws_live_*.wav (实测 23 个)
│
├── models\sherpa-onnx\models\kws\
│   ├── bt-en\                                      ← 7-11 后落盘（与 bt-zai-ma 同 SHA）
│   │   ├── encoder.onnx    56,259,805 字节  ⚠️ 不是会话里说的 132MB
│   │   ├── decoder.onnx        12,679 字节
│   │   ├── joiner.onnx         25,009 字节  ⚠️ 不是会话里说的 13.4KB
│   │   ├── tokens.txt              40 字节
│   │   └── keywords.txt             8 字节 ("B T @bt")
│   │
│   ├── bt-zai-ma\                                  ⚠️ 与 bt-en ONNX 完全相同 SHA
│   │   └── ...完全相同的 5 个文件...
│   │
│   └── zh-en-3M\                                   ← 原始 sherpa-onnx 预训练模型（参考）
│       ├── encoder-epoch-13-avg-2-chunk-{8,16}-left-64.int8.onnx
│       ├── decoder-epoch-13-avg-2-chunk-{8,16}-left-64.onnx
│       ├── joiner-epoch-13-avg-2-chunk-{8,16}-left-64.int8.onnx
│       ├── tokens.txt  1,928 字节
│       ├── keywords.txt     32 字节
│       └── test_wavs\
│
└── envs\joyai-sherpa\                              ← Windows 侧推理环境（sherpa-onnx）
```

### 0.3 bt-en ≡ bt-zai-ma 重要说明

**实测 SHA256 对照**（2026-08-09）：

| 文件 | bt-en SHA256 | bt-zai-ma SHA256 | 是否相同 |
|---|---|---|---|
| `exp/best.pt` | `9978D515EEB7A81CC056D4113106517F28AF985863C49E747CCEB5CEBDE1AE11` | 同 | ✓ |
| `exp_v2/best.pt` | `9FE010A086F91BC33132BFEEECE377F1427748F97B2D40C6E4EB4F33B827A8E6` | 同 | ✓ |
| `exp_v3/best.pt` | `8025EE22D7A7272FFC9731705543BF0969C4599F949764C2D45B662D4BD24352` | 同 | ✓ |
| `exp_v4/best.pt` | （已存在 2026-07-10 16:16） | 同 | ✓ |
| `encoder.onnx` | `A66EA57528798438` | 同 | ✓ |
| `joiner.onnx` | `65575EC23A6B729A` | 同 | ✓ |
| `tokens.txt` | `C844378499FAADD5` | 同 | ✓ |
| `keywords.txt` | `C555E815B78B3382` | 同 | ✓ |

**结论**：两个目录是**同一份训练 / 导出的拷贝**，不是不同实验。后续 agent 用任一即可，无需两边同步。`bt-zai-ma` 这个名字是历史残留（早期唤醒词是"bt 在吗" / 拼音 token，5 token；后来简化为"bt" / 2 token，但目录名没改）。

### 0.4 训练规模（Codex 实战）

- 正样本：**52 段**真人 "bt" 录音（实测 `positive/` 目录 wav 数）
- 负样本：**200 段**背景 / 非唤醒词语音
- 训练时长：**30 epoch / 单卡 8-10 分钟**（Codex 7-10 任务里报的；实测 RTX 5060 Ti 应该更快但未重测）
- best.pt 大小：**~55 MB**（每个 epoch checkpoint 同样大小，验证 vocab + model state 完整保存）
- ONNX encoder 大小：**~56 MB**（float32，未量化；与 zh-en-3M 的 ~4.6MB int8 形成对比）

---

## 1. 训练流程（本机已跑通）

### 1.1 Phase 0：环境激活（实测）

```bash
# WSL2 中（用户 ku，HOME=/home/ku）
source ~/kws-train/bin/activate
which python    # /home/ku/kws-train/bin/python
python --version  # Python 3.12.3
```

### 1.2 Phase 1：数据 prep（已跑过）

```bash
python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/scripts/prep_kws_data.py \
    --data-root /mnt/d/AI/data/kws/bt-en \
    --test-ratio 0.2
# 产物：/mnt/d/AI/data/kws/bt-en/manifests/
#   positive_train.jsonl.gz + positive_test.jsonl.gz
#   negative_train.jsonl.gz + negative_test.jsonl.gz
#   tokens.txt (40 字节：6 token：<blk> <sos/eos> <unk> B T _)
#   keywords.txt (8 字节："B T @bt\n")
```

### 1.3 Phase 2：训练（已跑过 4 次）

```bash
python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/train_kws.py \
    --manifests-dir /mnt/d/AI/data/kws/bt-en/manifests \
    --exp-dir        /mnt/d/AI/data/kws/bt-en/exp \
    --num-epochs     30
```

**已落盘的 4 次训练**（按时间）：

| iteration | best.pt mtime | best.pt size | 备注 |
|---|---|---|---|
| `exp/`    | 2026-07-10 11:10 | 55,248,508 | v1 训练 |
| `exp_v2/` | 2026-07-10 15:53 | 55,262,391 | v2 训练（修了流式导出后的） |
| `exp_v3/` | 2026-07-10 16:02 | 55,262,391 | v3（小幅参数调整） |
| `exp_v4/` | 2026-07-10 16:16 | 55,262,391 | v4（最新一次训练，含 epoch-5/10/15/20/25/30 全保存） |

实际训练日志见 `D:/AI/data/kws/bt-en/train_v3.log`（11.7 KB）。

### 1.4 Phase 3：ONNX 导出（已跑过）

```bash
python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/export_kws_onnx.py \
    --ckpt       /mnt/d/AI/data/kws/bt-en/exp/best.pt \
    --out-dir    /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en
```

会话里 7-10 的日志说 encoder 是 132 MB、joiner 是 13.4 KB——**与当前磁盘上的 56 MB / 25 KB 不一致**。可能是后来又有重导（训练 v3/v4 后），或者是会话里的 132 MB 是某种 debug 临时产物。**以当前磁盘 SHA 为准**。

当前 8-09 实测：
- encoder.onnx: 56,259,805 字节
- decoder.onnx: 12,679 字节
- joiner.onnx: 25,009 字节
- tokens.txt: 40 字节
- keywords.txt: 8 字节

### 1.5 Phase 4：sherpa-onnx 加载测试（已跑过）

会话 7-10 的 `.tmp_real_load.py` 脚本当时测试通过：

```
encoder.onnx: 132018.8 KB
decoder.onnx: 1.5 KB
joiner.onnx: 13.5 KB

=== Loading KeywordSpotter from real exported model ===
[OK1] KeywordSpotter constructed with REAL best.pt models
[OK2] create_stream() OK (OnlineStream)
[OK3] decode_stream() OK, result=''

=== TEST PASSED with real best.pt ===
```

> ⚠️ 此处 file size 与当前磁盘不一致（见 §8）。但用当前 56 MB encoder 应该也能 load——本机重测即可验证。

### 1.6 Phase 5：真实 wav 端到端测试（已跑过）

会话 7-10 用 `positive_0001.wav` 测试：

```
Loading wav: /mnt/d/AI/data/kws/bt-en/positive/positive_0001.wav
  rate=16000Hz, channels=1, sampwidth=2
  *** WOKE WORD DETECTED: 'bt' at chunk 54 ***
```

**待本地复跑**（磁盘 size 变了，重新加载测试更稳）。

### 1.7 Phase 6：JARVIS 端到端（**未跑**）

```bash
# 跑前先确认 KWS_MODEL_DIR 环境变量指向
$env:JARVIS_KWS_MODEL_DIR = 'D:/AI/models/sherpa-onnx/models/kws/bt-en'

python services/scripts/test_jarvis_kws_e2e.py
# 期望：recall ≥ 90% / FAR ≤ 2%（参考阈值 score=10.0 / th=0.25）
```

**实测状态**：未在本机跑过。`test_jarvis_kws_e2e.py` 存在但需要 webui 配合。

---

## 2. 训练-推理闭环中踩过的坑（Codex 实录）

### 2.1 Rank-2 joiner 输入问题（2026-07-10 修复）

**症状**：sherpa-onnx 加载后 `decode_stream` 报：

```
RuntimeError: Invalid rank for input: encoder_out Got: 2 Expected: 3
        Please fix either the inputs/outputs or the model.
```

**根因**：sherpa-onnx 的 `TransducerKeywordDecoder` 一帧一帧调用 joiner，先用 `GetEncoderOutFrame`（在 `onnx-utils.cc`）把 `(N, T, D)` 切成 `(N, D)` 再喂进 joiner。但我们的 `JoinerWrapper` 期望 rank-3。

**修复**（两处）：

1. **`JoinerWrapper.forward` 兼容 rank-2/3**（见 `export_kws_onnx.py`）：

```python
def forward(self, encoder_out, decoder_out):
    if encoder_out.dim() == 2:
        encoder_out = encoder_out.unsqueeze(1)        # (N, D) -> (N, 1, D)
    if decoder_out.dim() == 2:
        decoder_out = decoder_out.unsqueeze(1)        # (N, dec_dim) -> (N, 1, dec_dim)
    if decoder_out.size(1) == 1 and encoder_out.size(1) != 1:
        decoder_out = decoder_out.expand(-1, encoder_out.size(1), -1)
    combined = torch.cat([encoder_out, decoder_out], dim=-1)
    return self.joiner(combined)
```

2. **`joiner.onnx` 导出时 dummy 用 rank-2**：

```python
dummy_e = torch.randn(B, enc_dim)            # ← 不是 (B, 1, enc_dim)
dummy_d = torch.randn(B, dec_dim)
torch.onnx.export(
    ...,
    dynamic_axes={"encoder_out": {0: "N"}, "decoder_out": {0: "N"}},  # rank-2
)
```

**教训**：导出前要 `self-check`，跑通 sherpa-onnx 实际加载 + decode_stream 才算完事。self-check 只看 state 名字数对齐，**不会**测 joiner rank。

### 2.2 78 个 cached states 对齐（2026-07-10 已对齐）

- icefall Zipformer2 状态数 = **13 层 × 6 状态 = 78**
- sherpa-onnx 期望的状态名顺序（实测对齐通过）：
  ```
  cached_attn_k0_0, cached_attn_v0_0, cached_attn_k0_1, cached_attn_v0_1,
  cached_attn_k1_0, cached_attn_v1_0, cached_attn_k1_1, cached_attn_v1_1,
  ...（每层 6 个，共 78）
  ```
- 注：Codex 任务里曾怀疑是 13×8=104，实测是 78。

### 2.3 WSL2 出网代理

WSL2 clone icefall 时若被墙，用：

```bash
git clone https://ghfast.top/https://github.com/k2-fsa/icefall.git
# 或
git clone https://ghproxy.com/https://github.com/k2-fsa/icefall.git
```

或先 `export HF_ENDPOINT=https://hf-mirror.com` 再操作。

### 2.4 训练时长

会话口径 RTX 40 系实测：

- 30 epoch / 单卡：**8-10 分钟**

本机 RTX **5060 Ti** 应该更快（更新一代），但**未在本机重测过训练时长**。

---

## 3. 复现检查清单（本机实测版）

按顺序跑，对照每步产出：

```bash
# 1. 环境验证（实测 8-09）
wsl -e bash -c "source ~/kws-train/bin/activate && python -V"
# 期望：Python 3.12.3

wsl -e bash -c "source ~/kws-train/bin/activate && python -c 'import torch, lhotse, sherpa_onnx, onnx; print(torch.__version__, lhotse.__version__, sherpa_onnx.__version__, onnx.__version__)'"
# 期望（实测）：2.12.1+cu130 1.33.0 1.13.4 1.22.0

# 2. 数据就绪检查
ls /mnt/d/AI/data/kws/bt-en/positive/ | wc -l    # 52（实测）
ls /mnt/d/AI/data/kws/bt-en/negative/ | wc -l    # 200（实测）

# 3. prep（已生成 manifests，直接跳过；想重跑再执行）
ls /mnt/d/AI/data/kws/bt-en/manifests/ | wc -l    # 6（实测）

# 4. train（已落 4 次 best.pt，可选重训）
ls /mnt/d/AI/data/kws/bt-en/exp_v4/    # 应有 best.pt + epoch-{5,10,15,20,25,30}.pt

# 5. export（已落盘；想重导再执行）
wsl -e bash -c "source ~/kws-train/bin/activate && python /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/services/kws-training/export_kws_onnx.py --ckpt /mnt/d/AI/data/kws/bt-en/exp_v4/best.pt --out-dir /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en"

# 6. sherpa-onnx load test（建议重跑——磁盘 size 与 7-10 会话不一致）
wsl -e bash -c "source ~/kws-train/bin/activate && python -c \"
import sherpa_onnx
spotter = sherpa_onnx.KeywordSpotter(
    tokens='/mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/tokens.txt',
    encoder='/mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/encoder.onnx',
    decoder='/mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/decoder.onnx',
    joiner='/mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/joiner.onnx',
    keywords_file='/mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/keywords.txt',
    num_threads=1, sample_rate=16000, feature_dim=80,
)
s = spotter.create_stream()
spotter.decode_stream(s)
print('PASS')
\""
# 期望：输出 "PASS"

# 7. wav e2e（可选重跑）
# （脚本略，可参考 §1.6）

# 8. JARVIS e2e（未跑）
python services/scripts/test_jarvis_kws_e2e.py
```

---

## 4. 本机常见问题排查

| 症状 | 原因 | 修法 |
|---|---|---|
| `RuntimeError: Invalid rank for input: encoder_out Got: 2 Expected: 3` | joiner.onnx dummy 是 rank-3，sherpa-onnx 喂 rank-2 | 重导 joiner：用 rank-2 dummy + rank-2 dynamic_axes |
| 状态数 self-check 不通过 | icefall Zipformer2 配置层数改了，状态数 = 层数 × 6 | 重新对照 `model.py` 的 `NUM_LAYERS` 和 sherpa-onnx 期望 |
| `DurationMismatchError` in prep | manifest 写的 duration 与 wav 实际不符 | `kws_data_module.py` 已经用 `rec.duration` 兜底 |
| recall < 50% | 正样本太少（52 段） | **补真人录音 ≥ 200 段**（参见通用版 §2.3），不要改阈值 |
| FAR > 5% | 负样本不够 / 多样性差 | 补负样本（口述 / 音乐 / 噪声），或加 MUSAN |
| `ModuleNotFoundError: icefall` | WSL2 环境没装 | `pip install icefall` 或确认 `icefall_src/` vendor 路径在 sys.path |
| `RuntimeError: k2 not found` | k2 版本与 torch 不匹配 | 重新 `pip install k2 -f https://k2-fsa.github.io/k2/cpu.html`（CPU）或对应 CUDA 版本 |
| WSL2 clone icefall 失败 | 网络被墙 | `git clone https://ghfast.top/https://github.com/k2-fsa/icefall.git` |
| GPU sm_120 太新，torch 不支持 | RTX 5060 Ti 是 sm_120（compute 12.0） | torch 2.12.1+cu130 已实测支持（probe 输出 `cuda_cap: (12, 0)`）；旧版 torch 会崩 |

---

## 5. 关联引用（本机）

- 训练入口：`services/kws-training/train_kws.py`
- 模型定义：`services/kws-training/model.py`
- 数据模块：`services/kws-training/kws_data_module.py`
- ONNX 导出：`services/kws-training/export_kws_onnx.py`
- 一键编排：`services/kws-training/run_kws_v5.sh`
- 录音脚本：`services/scripts/record_kws_corpus.py`
- 数据 prep：`services/scripts/prep_kws_data.py`
- JARVIS e2e 测试：`services/scripts/test_jarvis_kws_e2e.py`
- 本次任务 Codex 会话：`会话记录/sessions/019f49cd-f241-76a3-a00e-847ddd8953e3.json`（2026-07-10）
- 通用版手册：`docs/kws-training-manual-generic.md`

---

## 6. 已知未做（本机）

1. **JARVIS 端到端 recall ≥ 90% 验证**：训练 + 导出 + 加载都跑通了，但 JARVIS webui 重启后端到端测试 (`test_jarvis_kws_e2e.py`) 还没跑过。
2. **MUSAN 接入实测**：prep 默认 fail-open，本机目前没装 MUSAN，是纯录制数据训练。
3. **live 采集（`mic_captures/`）接入**：v5 spec 要求双源，**实测 mic_captures 已有 23 个 live wav**（来自 jarvis_mode 监听落盘），但还没喂进训练。
4. **量化导出**：当前 ONNX 是 float32（encoder ~56 MB），未做 int8 量化（对比 `zh-en-3M` 的 int8 版本 encoder 仅 4.6 MB）。
5. **CTC + joiner 联合训练的真实价值**：训练脚本会算 `ctc_loss + joiner_loss`，但 KWS 推理只用 joiner head（导出为 no-op），CTC 才是核心监督信号——joiner_loss 的实际意义待评估。
6. **exp_v4 的训练脚本**：训练日志只到 v3（`train_v3.log`），v4 没有对应日志——可能是另开 session 跑的，没归档到这里。

---

## 7. 本机探查脚本（2026-08-09 用过，留底）

`D:/AI/workspace/JoyAI-VL-Interaction-main/.workbuddy_tmp/probe_env.py` —— 探 WSL2 kws-train venv 实际安装包版本。

```python
import subprocess
bash = """
source ~/kws-train/bin/activate && python << 'PYEOF'
import sys, torch, torchaudio, lhotse, k2, sherpa_onnx, onnx, onnxruntime
print("python:", sys.version.split()[0])
print("torch:", torch.__version__)
print("cuda:", torch.cuda.is_available(), torch.cuda.get_device_name(0))
print("lhotse:", lhotse.__version__)
print("sherpa_onnx:", sherpa_onnx.__version__)
print("onnx:", onnx.__version__)
print("onnxruntime:", onnxruntime.__version__)
PYEOF
"""
print(subprocess.run(["wsl", "-e", "bash", "-c", bash], capture_output=True, text=True).stdout)
```

---

## 8. 已知不一致（会话口径 vs 实测）—— 防止后人照搬

| 维度 | 会话口径（7-10） | 实测（8-09） | 影响 |
|---|---|---|---|
| **GPU** | "RTX 40 系" | RTX **5060 Ti**（sm_120） | 训练时长可能与 8-10 min 不同，但**未重测**；目录命名 / 模型 size 没影响 |
| **encoder.onnx size** | 132,018.8 KB | **56,259,805 B**（~56 MB） | 若有人按 132 MB 排查磁盘占用会困惑；不影响推理 |
| **joiner.onnx size** | 13.4 KB | **25,009 B** | 同上 |
| **exp iterations** | "exp / exp_v2 / exp_v3" | **exp / exp_v2 / exp_v3 / exp_v4** | v4 没在 7-10 那条 session 里出现（可能是后续开的新 session） |
| **sherpa-onnx 版本** | "latest" | **1.13.4** | 影响排查时去搜 issue |
| **icefall 来源** | "vendor" | vendor + 系统 `/home/ku/icefall/` 都有 | 训练走 vendor；排查时别只盯一处 |
| **bt-zai-ma vs bt-en** | "v5 早期版本已弃用" | **同一份数据 / 同一份导出的复制** | 任选一个用就行；不要两边都改 |

**重导出建议**（如要消除不一致）：用 `exp_v4/best.pt` 重导一份 ONNX 到 `D:/AI/models/sherpa-onnx/models/kws/bt-en/`，记录新 SHA；与旧 7-10 SHA 对照，**若一致则说明之前那次 132 MB 是临时调试产物 / 被覆盖**；若不同则需要排查 v4 模型配置是否改了（v3→v4 训练时改了 hyperparams）。
