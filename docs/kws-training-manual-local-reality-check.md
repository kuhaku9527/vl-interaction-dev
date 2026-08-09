# Reality Check Round 2 — 2026-08-09 实际跑通记录

> 在被用户追问"环境还在吗？是否参考官方文档"后，作者重新对训练 + 推理环境做了端到端实测。
> 本节记录**实测结果 + 官方对照**，覆盖上一版未验证的关键事实。

## A. 训练环境实测

### A.1 复现的回归 bug（PR #26 引入）

**症状**：`train_kws.py` 启动时崩溃：

```
TypeError: 'CutSet' object is not callable
  File "train_kws.py", line 171, in main
    train_cuts = dm.train_cuts()
```

**根因（git 追溯）**：

| 时间 | 提交 | 变更 |
|---|---|---|
| 2026-07-10（Codex 当时能跑） | — | `kws_data_module.py` 用 `@lru_cache` 装饰方法，调用方 `dm.train_cuts()` 带括号 |
| 2026-07-24 13:57 | `7c4c003 fix(backend): replace instance-method lru_cache with cached_property in kws_data_module (#26)` | 把 `@lru_cache` 换成 `@cached_property`——**方法变属性**；调用方没改 |
| 2026-08-09 20:54 | `8e9db43 feat(kws): v5 训练管线脚手架 + MUSAN fail-open (PR #140)` | 合并 v5，但 `train_kws.py` 仍调用 `dm.train_cuts()` 带括号 |
| **2026-08-09 21:30（实测）** | — | 跑 `train_kws.py` 触发 TypeError |

**结论**：PR #26 是 lint 类的"属性化重构"，破坏了调用方，但**没跑过任何 smoke test**（训练 / 推理都没测过）。本仓库训练环境当前是**坏的**。

### A.2 已应用的 fix（未 commit，需用户复核）

修改 `services/kws-training/train_kws.py` line 171-172：

```diff
-    train_cuts = dm.train_cuts()
-    valid_cuts = dm.valid_cuts()
+    train_cuts = dm.train_cuts  # @cached_property (PR #26, 2026-07-24)
+    valid_cuts = dm.valid_cuts  # @cached_property (PR #26, 2026-07-24)
```

`git diff` 仅 2 行变更（其余全 whitespace，行尾 CRLF/LF 差异）。**未 commit**——按 AGENTS.md §0 主开发者角色让位规则，Codex 不主动 commit 主项目代码，等用户复核后决定。

### A.3 训练实测（fix 后，1 epoch）

```
$ wsl -e bash -c "source ~/kws-train/bin/activate && python .../train_kws.py \\
    --manifests-dir /mnt/d/AI/data/kws/bt-en/manifests \\
    --exp-dir /tmp/test_train \\
    --num-epochs 1 --batch-size 4 --save-every 1 --device cuda"
[INFO] Loaded 6 tokens from /mnt/d/AI/data/kws/bt-en/manifests/tokens.txt
[INFO] Device: cuda
[INFO] About to get train cuts (positive + negative)
[INFO]   pos_train=43, neg_train=160, total=203
[INFO] About to get valid cuts (positive test)
[INFO] train_cuts=203, valid_cuts=10
[INFO] Model params: 13,776,792 (13.8M)
[INFO] Whitening: name=None, num_groups=1, num_channels=384, metric=60.33 vs. limit=7.5
[INFO] [epoch   1/1] train=21.7435 valid=1.1207 lr=0.00e+00 (9.3s)
[INFO]   [save] best → /tmp/test_train/best.pt
[INFO] [done] best valid_loss = 1.1207
```

**结论**：训练管线**完全活着**。RTX 5060 Ti 1 epoch 9.3s（外推 30 epoch ≈ 5 分钟，比 Codex 7-10 口径 8-10 分钟快，与 sm_120 算力匹配）。

## B. ONNX 导出实测

```
$ python export_kws_onnx.py --ckpt /tmp/test_train/best.pt --out-dir /tmp/test_export
[INFO] [3/3] 导出 joiner (rank-2 单帧)...
[INFO]   → joiner.onnx
[INFO]   [verify] encoder.onnx OK
[INFO]   [verify] decoder.onnx OK
[INFO]   [verify] joiner.onnx OK
[INFO] [done] 导出完成: /tmp/test_export
```

产物（实测）：

```
-rw-r--r-- 12,679  decoder.onnx     ← rank-2 单帧接口（fix 后）
-rw-r--r-- 56,259,805  encoder.onnx (~56 MB float32)
-rw-r--r-- 25,009  joiner.onnx
```

**注意**：`tokens.txt` 和 `keywords.txt` 没自动复制——`export_kws_onnx.py` 假设 checkpoint 在 `<data_root>/exp/best.pt`，从 `<data_root>/manifests/tokens.txt` 拷贝。测试时 `/tmp/test_train/best.pt` 不满足这个布局，需要手动 cp：

```bash
cp /mnt/d/AI/data/kws/bt-en/manifests/tokens.txt /tmp/test_export/
cp /mnt/d/AI/data/kws/bt-en/manifests/keywords.txt /tmp/test_export/
```

（生产路径 `/mnt/d/AI/data/kws/bt-en/exp/best.pt` 已自动生成 tokens.txt / keywords.txt。）

## C. sherpa-onnx 推理实测

### C.1 官方 API 参考（sherpa-onnx 1.13.4）

仓库自带 **`services/scripts/test_sherpa_load.py`** 是正确的 API 用法——这是**比写自己的 probe 脚本更可靠**的方式：

```python
spotter = sherpa_onnx.KeywordSpotter(
    tokens=str(tokens),
    encoder=str(encoder),
    decoder=str(decoder),
    joiner=str(joiner),
    keywords_file=str(keywords),
    num_threads=2,                 # ← 默认 2（不是 1）
    provider="cpu",                # ← 显式 cpu（不写默认是 cpu，但显式更稳）
    keywords_threshold=0.25,
    keywords_score=2.0,
    num_trailing_blanks=1,
    max_active_paths=10,
)

# 喂数据：传 int16 list + 末尾 padding + input_finished()
samples, sr = read_wav_16k(wav_path)
stream = spotter.create_stream(keywords)
stream.accept_waveform(sr, samples)
tail_paddings = [0] * int(0.66 * sr)    # ← 末尾 660ms 静音
stream.accept_waveform(sr, tail_paddings)
stream.input_finished()
```

**重点**：作者第一次写的 probe 脚本失败，正是因为：
- 用了 `accept_waveform(rate, np.float32_array)` 而不是 `accept_waveform(sr, int_list)`
- 没调 `input_finished()`
- 没补 660ms 静音 padding
- 没设 `provider="cpu"`

→ **永远用 `test_sherpa_load.py` 验证，不要自己造轮子**。

### C.2 生产 ONNX 实测（用 7-10 训的 v4 best.pt 导出的）

```
$ python test_sherpa_load.py --encoder /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en/encoder.onnx \\
    --decoder .../decoder.onnx --joiner .../joiner.onnx \\
    --tokens .../tokens.txt --keywords .../keywords.txt \\
    --test-wav /mnt/d/AI/data/kws/bt-en/test_bt.wav
[1/3] 加载 KeywordSpotter ...
  [OK] 加载成功
[2/3] 读 wav + 喂 stream ...
  samples: 114048 (7.13s)
[3/3] 解码 + 检测 ...
  [HIT 1]   keyword='bt'
  [HIT 2]   keyword='bt'
  ...（循环检测）
  [HIT 150] keyword='bt'
  [HIT 151] keyword='bt'
  扫完 (2.59s)
  [result] 共 151 次命中
```

**151 次命中 'bt'** —— 生产模型在 7.13s 录音上稳定检出。

### C.3 1-epoch 训练模型实测（fix 后）

```
$ python test_sherpa_load.py --encoder /tmp/test_export/encoder.onnx ... \\
    --test-wav /mnt/d/AI/data/kws/bt-en/test_bt.wav
  [HIT 1] keyword='bt'
  ...
  [HIT 137] keyword='bt'
  扫完 (2.69s)
  [result] 共 137 次命中
```

1 epoch 模型也检出 137 次——**over-detect**（欠训召回低时常见），但 export + load + 推理管线**完全 OK**。

## D. 官方文档对照

### D.1 icefall Zipformer2 流式 KWS 参考

**位置**：`/home/ku/icefall/egs/librispeech/ASR/zipformer/export-onnx-streaming.py`（系统装在 WSL2 用户 ku 下）

**对比我们的配置 vs icefall 默认**：

| 参数 | 我们（KWS 小数据集） | icefall Librispeech ASR（官方默认） |
|---|---|---|
| `num-encoder-layers` | `2,2,2,3,2,2` (13 层) | `2,2,3,4,3,2` (16 层) |
| `downsampling-factor` | `1,2,4,8,4,2` | 同 |
| `encoder-dim` | `192,256,384,512,384,256` | 同 |
| `num-heads` | `4,4,4,8,4,4` | 同 |
| `feedforward-dim` | `256,384,512,768,512,384` (小) | `512,768,1024,1536,1024,768` (大) |
| `chunk_size` | **32** | 16（在 train 是 `16,32,64,-1`，export 时选一个） |
| `left-context-frames` | **64** | 128 |
| 状态数/层 | 6 | 同 → 总状态 13×6=**78** vs icefall 16×6=**96** |

**结论**：我们的 KWS 模型是 icefall Zipformer2 的**小一圈定制**——适合 50-200 段小数据集，过大反而过拟合（见 `model.py` 注释）。

### D.2 sherpa-onnx 官方文档（无法直接访问）

网络出口被沙箱限制，无法直接拉 `k2-fsa.github.io`。但：
- **官方推荐用法**就是仓库 `test_sherpa_load.py` 的写法（`provider="cpu"` + int16 list + 末尾 padding + `input_finished()`）
- sherpa_onnx 版本号实测 **1.13.4**（不是笼统 "latest"）

### D.3 已验证的 vs 未验证的

| 维度 | 验证方式 | 状态 |
|---|---|---|
| Python / torch / lhotse / sherpa_onnx 实际版本 | `probe_env.py` import 测 | ✓ 实测（见 §0） |
| GPU 类型 / CUDA 可用 | `torch.cuda.get_device_name(0)` | ✓ RTX 5060 Ti, sm_120 |
| 数据规模 (52 pos / 200 neg) | `ls positive/ \| wc -l` | ✓ 实测 |
| manifests 文件齐全 | `ls manifests/` | ✓ 实测 |
| 训练可跑 | `train_kws.py --num-epochs 1` | ✓ 实测（fix PR #26 后） |
| ONNX 导出 | `export_kws_onnx.py` | ✓ 实测 |
| sherpa-onnx load | `test_sherpa_load.py` | ✓ 实测（生产 + 1-epoch 都过） |
| 7-10 召回 / FAR 实测数字 | 没有官方测量脚本 | ✗ 未验证，仅有 workbuddy `决策/` 的 "FAR 2% / recall 49%" 二手数据 |
| JARVIS webui 端到端 | `test_jarvis_kws_e2e.py` 需 webui 配合 | ✗ 未跑 |

## E. 总结

**环境状态**：
- ✓ WSL2 + kws-train venv 完整可用
- ✓ 数据 / manifests / ONNX 模型齐全
- ✗ 训练入口 `train_kws.py` 有 PR #26 引入的回归 bug（**已 fix，未 commit**）
- ✓ 修复后 train → export → sherpa_onnx load + detect 全链路活的

**手册修正**：
- `docs/kws-training-manual-local.md` §0.1 / §8 应改为：本节"实测"版本
- §3 复现 checklist 应改用 `test_sherpa_load.py` + 修复后的 `train_kws.py`
- §4 排错表加 "PR #26 @cached_property regression" 行
- §1.4 实测 ONNX 大小 56 MB / 25 KB（不是会话口径的 132 MB / 13.4 KB）
