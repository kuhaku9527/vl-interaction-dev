# KWS 自训手册 — 通用复刻版（Codex 视角）

> 本文件是**通用复刻手册**：目标读者是任何想在自己环境复现"训练 bt 唤醒词"流程的 agent / 工程师。
> 不绑定本机路径 / WSL2 实例。所有路径以 `<...>` 占位符给出，命令可直接复制后改路径。
>
> **视角声明**：本文档基于 Codex 在 2026-07-06 ~ 2026-07-13 期间的会话记录整理（来源已落盘到 `会话记录/sessions/`），不引用其他 agent 的副产物。`决策/`、`reports/`、`archive/` 等目录下的"语音栈决策"由协作 agent 撰写，**不要作为训练真值**——本文件才是。

---

## 0. 背景与决策

### 0.1 为什么自己训练 KWS

预训练的 KWS 模型（如 Google `hey_snips`、Picovoice `leopard`）：

- 唤醒词固定，无法自定义 / 训练成本高
- 中文唤醒词召回率不稳定
- 商业 SDK 受限（无网络 / 无闭源依赖 / 隐私）

我们选择**自训**是为了：

- 唤醒词完全可控（"bt" 两个字符）
- 完全离线 / CPU 推理（sherpa-onnx ONNX）
- 数据 / 模型完全本地，无外发

### 0.2 架构选型

| 维度 | 选择 | 理由 |
|---|---|---|
| 引擎 | **sherpa-onnx** | ONNX / CPU / 流式 / 中文友好（k2-fsa 出品） |
| 训练框架 | **icefall**（k2-fsa 训练 recipe） | Zipformer2 是当前最优 CTC/Transducer 架构之一 |
| 推理接口 | **OnlineZipformer2TransducerModel** | 流式接口（cached states），适合实时 KWS |
| 训练目标 | **CTC + Zipformer2 head** | CTC 简单稳定，适合小数据集；joiner head 在导出时降级为 no-op |
| 唤醒词 | **"bt"**（2 token: B + T） | 经多轮迭代从"bt 在吗"（5 token）简化而来 |
| 数据增强 | MUSAN（noise/music/speech）按 SNR 混合 | MUSAN 公开 / 自动增广 |

### 0.3 训练规模（Codex 实战数据）

- 正样本：**53 段**真人 "bt" 录音（实测 recall=49%，不足；推荐 ≥200 段才能上 90%+）
- 负样本：**200 段**背景 / 非唤醒词语音
- 训练时长：**30 epoch / 单卡 8-10 分钟**（实测 RTX 40 系）
- 模型参数量：~14M（Zipformer2 6-stacks × 小尺寸）
- ONNX encoder 大小：**~130 MB**（float32，未量化）

---

## 1. 环境准备

### 1.1 硬件

- GPU：≥8 GB 显存（Zipformer2 训练 + CTC）。CPU 也能跑但很慢。
- 内存：≥16 GB（lhotse CutSet 缓存 + DataLoader worker）
- 磁盘：≥20 GB（数据 + checkpoints + ONNX + MUSAN）

### 1.2 软件依赖

| 包 | 版本（实测） | 用途 |
|---|---|---|
| Python | 3.12.3 | 基座 |
| torch | 2.12.1+cu130 | 训练 |
| torchaudio | 对应 torch 版本 | fbank 提取 |
| k2 | latest（与 torch 版本匹配） | icefall 依赖（FSA / 构图） |
| lhotse | 1.33.0 | 数据准备 / CutSet |
| icefall | 源码（vendor） | Zipformer2 实现 |
| sherpa-onnx | latest | 推理 / 验证 |
| onnx | ≥1.14 | 模型导出 |
| onnxruntime | ≥1.16 | 推理后端 |
| sounddevice / soundfile | latest | 录音（Windows 侧采集） |
| MUSAN 数据集 | 公开下载 | 数据增广（可选） |

### 1.3 推荐目录结构

```
<workspace>/
├── services/
│   ├── kws-training/
│   │   ├── train_kws.py              ← 训练入口
│   │   ├── export_kws_onnx.py        ← ONNX 导出
│   │   ├── model.py                  ← KwsModel（Zipformer2 + CTC）
│   │   ├── kws_data_module.py        ← lhotse 数据读取
│   │   ├── icefall_src/              ← icefall 源码（vendor）
│   │   └── ...
│   └── scripts/
│       ├── record_kws_corpus.py      ← Windows 侧录音
│       ├── prep_kws_data.py          ← 数据 prep + MUSAN
│       └── test_jarvis_kws_e2e.py    ← 端到端验证
├── data/
│   └── kws/
│       └── <keyword>-en/             ← 数据集根
│           ├── positive/             ← 正样本 wav
│           ├── negative/             ← 负样本 wav
│           ├── manifests/            ← prep 产物
│           └── exp/                  ← 训练 checkpoint
└── models/
    └── sherpa-onnx/
        └── models/
            └── kws/
                └── <keyword>-en/     ← 导出 ONNX
                    ├── encoder.onnx
                    ├── decoder.onnx
                    ├── joiner.onnx
                    ├── tokens.txt
                    └── keywords.txt
```

---

## 2. 数据采集

### 2.1 音频规格

| 项 | 值 |
|---|---|
| 采样率 | 16000 Hz |
| 声道 | mono |
| 位深 | int16 (PCM_16) |
| 单段时长 | 0.3 – 3.0 s |
| 容器 | WAV |
| 裁剪 | 能量 VAD 自动剪头尾静音（阈值 0.01 RMS，pad 0.15 s） |

### 2.2 录音命令（Windows 侧）

```powershell
# 依赖：pip install sounddevice soundfile numpy
# 正样本：录 N 句 "bt"（推荐 ≥200 段才能上 90%+ recall）
python services/scripts/record_kws_corpus.py --label positive --count 200

# 负样本：录 200 段背景 / 非唤醒词语音（口述 / 音乐 / 噪声皆可）
python services/scripts/record_kws_corpus.py --label negative --count 200

# 调试：不落盘
python services/scripts/record_kws_corpus.py --label positive --count 3 --dry-run
```

### 2.3 多样性矩阵（单人）

录够量比"录得齐"重要，建议覆盖：

- 距离：30cm / 1m / 2m
- 音量：正常 / 偏大 / 偏小
- 语速：正常 / 稍快 / 稍慢

多人次（≥2 人）每人均摊 60-100 段，能极大提升模型泛化。

### 2.4 输出 manifest

`record_kws_corpus.py` 同时输出 `<data_root>/<keyword>-en/positive/positive.jsonl`：

```json
{"id": "positive_0001", "audio": "<abs path>.wav",
 "duration": 1.2, "sampling_rate": 16000, "channels": 1,
 "text": "bt", "tokens": "B T", "keyword": "bt"}
```

注意 `text` 是用户实际念的内容；`tokens` 是 BPE/char 分解；`keyword` 是分类标签。

---

## 3. 数据 prep

### 3.1 用途

`prep_kws_data.py` 把录音产物切成 train/test，生成 lhotse CutSet，可选做 MUSAN 增广。

### 3.2 简化词表

唤醒词 "bt" 的最小词表（PINYIN_VOCAB）：

```python
["<blk>", "<sos/eos>", "<unk>", "B", "T", "_"]
```

CTC 训练时 `<blk>=0` 是 blank。`<sos/eos>` / `<unk>` 兜底。"_" 是静音占位。

### 3.3 运行 prep

```bash
# WSL2 侧（推荐，路径前缀 /mnt/d/）
python services/scripts/prep_kws_data.py \
    --data-root /mnt/d/<workspace>/data/kws/<keyword>-en \
    --test-ratio 0.2

# 可选：MUSAN 自动增广（fail-open）
#  - 正样本 ×3 混噪扩样
#  - 负样本补 MUSAN 切 2s 片段 ~400 段
#  - MUSAN 缺失 → log WARN + 跳过（不阻断）

# 可选：live 采集摄入（v5+，监听自动落盘的 mic_captures/）
python services/scripts/prep_kws_data.py \
    --data-root /mnt/d/<workspace>/data/kws/<keyword>-en \
    --live-capture-dir /mnt/d/<workspace>/data/kws/mic_captures \
    --live-filter all
```

### 3.4 产物

```
<data_root>/<keyword>-en/manifests/
├── positive_train.jsonl.gz
├── positive_test.jsonl.gz
├── negative_train.jsonl.gz
├── negative_test.jsonl.gz
├── tokens.txt       # 6 行：<blk> <sos/eos> <unk> B T _
└── keywords.txt     # "B T @bt\n"
```

---

## 4. 训练

### 4.1 模型架构（Zipformer2 + CTC）

`model.py` 定义 `KwsModel`：

```python
ENCODER_DIMS = (192, 256, 384, 512, 384, 256)   # 6 stacks
NUM_LAYERS   = (2, 2, 2, 3, 2, 2)                # 总 13 层
DOWNSAMPLING = (1, 2, 4, 8, 4, 2)
FEEDFORWARD  = (256, 384, 512, 768, 512, 384)
NUM_HEADS    = (4, 4, 4, 8, 4, 4)
```

- fbank 输入：`(B, T, 80)` → input_proj → `(T, B, encoder_dim[0])` → Zipformer2 → `(T', B, max(encoder_dim))`
- CTC head：`linear(max_dim → vocab)`
- 训练时把 Zipformer2 的 `chunk_size=[32]`、`left_context_frames=[64]`，与 ONNX 导出对齐

### 4.2 训练命令

```bash
python services/kws-training/train_kws.py \
    --manifests-dir <data_root>/<keyword>-en/manifests \
    --exp-dir        <data_root>/<keyword>-en/exp \
    --num-epochs     30 \
    --lr             1e-3 \
    --batch-size     4 \
    --num-workers    0 \
    --device         cuda
```

默认超参（实测稳定）：

| 项 | 值 |
|---|---|
| 优化器 | AdamW（weight_decay=1e-4） |
| lr | 1e-3 |
| lr schedule | CosineAnnealingLR（T_max=num_epochs） |
| 梯度裁剪 | max_norm=5.0 |
| 损失 | CTCLoss（blank=0, zero_infinity=True）；部分 batch 加 joiner loss（CTC + joiner 总和） |
| batch_size | 4（小数据集友好） |
| num_workers | 0（WSL2 / Windows 互通兼容性） |
| seed | 42 |
| save_every | 5（中间 checkpoint） |
| best.pt 选取 | 最低 valid_loss |

### 4.3 数据流（`kws_data_module.py`）

- `KwsAsrDataModule` 读 `<manifests_dir>/{positive,negative}_{train,test}.jsonl.gz` + `tokens.txt`
- 自定义 `_jsonl_gz_to_cuts` 把 Windows 录的 JSONL 转 lhotse `MonoCut`
- 训练时 FbankDataset 实时提 fbank（10ms frame_shift, 25ms frame_length, 80 mel bins, CMN）
- 关键 trick：用 `rec.duration`（实际 wav 时长）而非 manifest 的 `duration` 字段——避免 manifest 与 wav 不一致导致 `DurationMismatchError`

### 4.4 期望产出

```
<data_root>/<keyword>-en/exp/
├── best.pt        ← valid_loss 最低
├── epoch-5.pt
├── epoch-10.pt
├── ...
└── epoch-30.pt
```

每个 checkpoint 包含 `model_state`、`vocab_size`、`token_table`、`valid_loss`。

---

## 5. ONNX 导出

### 5.1 三件套 + tokens + keywords

`sherpa-onnx` 的 Transducer KWS 需要 5 个文件：

| 文件 | 内容 |
|---|---|
| `encoder.onnx` | Zipformer2 流式 encoder（带 cached states） |
| `decoder.onnx` | no-op（输入空 token，输出零向量） |
| `joiner.onnx` | linear(encoder_out + decoder_out → vocab) |
| `tokens.txt` | 词表 |
| `keywords.txt` | 唤醒词 tokens（"B T @bt"） |

### 5.2 流式接口（关键）

`export_kws_onnx.py` 包出 `StreamEncoderWrapper`：

```
Inputs:
  x:        (T, B, 80)            ← fbank
  x_lens:   (B,)
  state_0..state_77:               ← 13 层 × 6 状态 = 78 个 cached states
Outputs:
  encoder_out: (B, T', D)
  new_state_0..new_state_77:       ← 78 个新 cached states
```

**状态名顺序必须严格对齐 sherpa-onnx 期望**：
```
cached_attn_k0_0, cached_attn_v0_0, cached_attn_k0_1, cached_attn_v0_1,
cached_attn_k1_0, cached_attn_v1_0, cached_attn_k1_1, cached_attn_v1_1,
...
（共 13 层 × 6 状态 = 78 个）
```

导出前会 `self-check`：encoder 输入数=81（含 x），输出数=81（含 encoder_out），状态名 / 数 / 顺序与 sherpa-onnx 完全对齐才 OK。

### 5.3 关键 fix：rank-2 joiner 输入

sherpa-onnx 的 `TransducerKeywordDecoder` 一帧一帧调用 joiner，先用 `GetEncoderOutFrame` 把 `(N, T, D)` 切成 `(N, D)` 再喂进 joiner。所以：

```python
# JoinerWrapper.forward 必须兼容 rank-2 / rank-3 两种入参
def forward(self, encoder_out, decoder_out):
    if encoder_out.dim() == 2:
        encoder_out = encoder_out.unsqueeze(1)        # (N, D) -> (N, 1, D)
    if decoder_out.dim() == 2:
        decoder_out = decoder_out.unsqueeze(1)        # (N, dec_dim) -> (N, 1, dec_dim)
    ...
```

而且 **joiner.onnx 导出时 dummy 输入要用 rank-2**：

```python
dummy_e = torch.randn(B, enc_dim)        # ← 不是 (B, 1, enc_dim)
dummy_d = torch.randn(B, dec_dim)
torch.onnx.export(
    ...,
    dynamic_axes={"encoder_out": {0: "N"}, "decoder_out": {0: "N"}},  # rank-2
)
```

否则 sherpa-onnx 加载时报错：
```
RuntimeError: Invalid rank for input: encoder_out Got: 2 Expected: 3
```

### 5.4 导出命令

```bash
python services/kws-training/export_kws_onnx.py \
    --ckpt      <data_root>/<keyword>-en/exp/best.pt \
    --out-dir   <models>/sherpa-onnx/models/kws/<keyword>-en \
    --chunk-size 32 \
    --left-context 64
```

---

## 6. 验证

### 6.1 sherpa-onnx 加载测试

```python
import sherpa_onnx
spotter = sherpa_onnx.KeywordSpotter(
    tokens=str(OUT / "tokens.txt"),
    encoder=str(OUT / "encoder.onnx"),
    decoder=str(OUT / "decoder.onnx"),
    joiner=str(OUT / "joiner.onnx"),
    keywords_file=str(OUT / "keywords.txt"),
    num_threads=1,
    sample_rate=16000,
    feature_dim=80,
)
s = spotter.create_stream()
# ... 喂音频 ...
spotter.decode_stream(s)
print(spotter.get_result(s))   # 应为 'bt'
```

成功标志：`decode_stream(s)` 不抛 `RuntimeError: Invalid rank`。

### 6.2 真实 wav 端到端测试

```python
import wave, numpy as np, sherpa_onnx

with wave.open(str(WAV), "rb") as wf:
    assert wf.getnchannels() == 1 and wf.getsampwidth() == 2
    rate = wf.getframerate()
    s = spotter.create_stream()
    chunk = 1600   # 100 ms @ 16 kHz
    while True:
        data = wf.readframes(chunk // 2)
        if not data:
            break
        audio = np.frombuffer(data, dtype=np.int16).astype("float32") / 32768.0
        s.accept_waveform(rate, audio)
        if spotter.is_ready(s):
            spotter.decode_stream(s)
            if spotter.get_result(s):
                print("WOKE")
                break
```

成功标志：在正样本 wav 上能检出 'bt'。

### 6.3 端到端 JARVIS 测试

```bash
python services/scripts/test_jarvis_kws_e2e.py
```

目标：定点 **recall ≥ 90% / FAR ≤ 2%**（阈值通常 score=10.0 / th=0.25）。

---

## 7. 一键编排

```bash
# 全套：prep → train → export
bash services/kws-training/run_kws_v5.sh
```

可覆盖环境变量（见 `run_kws_v5.sh` 头注释）：

- `KWS_PY`：python 解释器路径（默认 `python`）
- `KWS_DATA_ROOT`：训练数据根
- `KWS_MUSAN_DIR`：MUSAN 目录（空 = 自动探测）
- `KWS_LIVE_CAPTURE_DIR`：live 采集目录（`jarvis_mode` 自动落盘）
- `KWS_LIVE_FILTER`：`all`（默认）或 `asr-bt`
- `KWS_OUT_DIR`：导出目标（必须等于 `JarvisConfig.kws_model_dir`）
- `KWS_EPOCHS`：训练轮数（默认 30）

---

## 8. 常见坑

1. **CTC + joiner 联合 loss**：训练脚本会算 `ctc_loss + joiner_loss`；全负样本 batch 时跳过 joiner 算 0。导出 ONNX 时 joiner head 降级为 no-op。
2. **manifest 时长不一致**：`record_kws_corpus.py` 可能写错的 duration；prep 时用 `rec.duration` 而非 manifest 字段，避免 `DurationMismatchError`。
3. **export_kws_onnx.py 改完没重导**：joiner 的 dummy 和 dynamic_axes 改完必须重跑 export，否则 sherpa-onnx 加载报 rank 错。
4. **状态数对不上**：13 层 × 6 = 78，不是 13×8=104（Codex 7-10 任务里实测过）。
5. **encoder 输入 dummy**：`(T, B, 80)` 而非 `(B, T, 80)`——icefall Zipformer2 接口约定。
6. **MUSAN 缺失不要慌**：fail-open，仅用录制数据训练，不阻断管线。
7. **数据量不够**：recall 上不去时第一反应是**补真人录音**（≥200 段），不是改阈值。阈值扫描无收益（v3.20 spec 实测）。

---

## 9. 关联引用

- 训练入口：`services/kws-training/train_kws.py`
- 模型定义：`services/kws-training/model.py`
- 数据模块：`services/kws-training/kws_data_module.py`
- ONNX 导出：`services/kws-training/export_kws_onnx.py`
- 一键编排：`services/kws-training/run_kws_v5.sh`
- 录音脚本：`services/scripts/record_kws_corpus.py`
- 数据 prep：`services/scripts/prep_kws_data.py`
- 端到端测试：`services/scripts/test_jarvis_kws_e2e.py`
- sherpa-onnx 官方文档：<https://k2-fsa.github.io/sherpa/onnx/>
- icefall 参考：`icefall/egs/librispeech/ASR/zipformer/export-onnx-streaming.py`
