# KWS v5 真人录音采集规格（Capture Spec）

> 子任务 A（issue #132）：KWS 定点 recall 49.06% → ≥90%、FAR ≤2%。
> 召回不足根因 = 正样本过少（当前 **53 段**）。49→90 **只能靠训练扩数据**，AI 无法合成替代真人声纹多样性。
> 本规格定义「真人录音」这一**用户侧阻塞项**：工具已就绪，需用户执行。

## 1. 目标数量

| 类别 | 当前 | v5 目标 | 说明 |
| --- | --- | --- | --- |
| 正样本（说 "BT"） | 53 段 | **≥200 段真人录音** | MUSAN 增广再扩 3×，但真人声纹多样性必须由真人提供 |
| 负样本（非 BT 语音/噪声） | 200 段 | 已够（MUSAN 再自动补 ~400 段） | 用户无需额外录负样本 |

**结论**：用户需至少补录 **~150 段** 新的真人 "BT"（53 + 150 = 203），覆盖多人次/多距离/多音量/多语速。

## 2. 每人次 / 多样性矩阵

为最大化声纹与信道多样性，单人建议按以下组合各录若干遍（总量摊到 ≥200 段即可）：

- **距离**：~30cm / ~1m / ~2m 各若干
- **音量**：正常 / 偏大 / 偏小 各若干
- **语速**：正常 / 稍快 / 稍慢 各若干
- **建议**：2–3 人参与，每人 60–100 段，覆盖上述矩阵

> 不要连续念同一语调 200 遍——多样性 > 数量。

## 3. 采样 / 格式（工具已固定，勿改）

| 项 | 值 |
| --- | --- |
| 采样率 | **16000 Hz** |
| 声道 | **mono（单声道）** |
| 位深 | **int16（PCM_16）** |
| 单段时长 | 0.3–3.0 s（能量 VAD 自动裁剪静音头尾） |
| 容器 | **WAV** |

这些与 `record_kws_corpus.py` 默认（`TARGET_SR=16000`, `TARGET_CHANNELS=1`, `DTYPE="int16"`）一致，
也是 sherpa-onnx KWS 训练/推理的硬性要求（kws.py: `sample_rate=16000`）。

## 4. 落点目录（与训练管线契约一致）

```
D:/AI/data/kws/bt-en/positive/        # 正样本 wav
D:/AI/data/kws/bt-en/positive.jsonl   # manifest（由工具自动重建）
```

> ⚠️ **数据卫生**：当前 `positive.jsonl` 列 53 条但 `positive/` 仅 50 个 wav（3 条过期）。
> 重跑采集脚本会用目录下真实 wav **重建** manifest，自动消解该不一致。
> 之后 `prep_kws_data.py` 会校验 manifest 引用的 wav 必须存在，缺失即显式报错（fail-fast）。

## 5. 采集命令（工具就绪）

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

## 6. 如何纳入训练管线（prep 清单）

`record_kws_corpus.py` 每次运行会扫描 `positive/` 下全部 `*.wav` 并**重建** `positive.jsonl`
（字段：`id, audio, duration, sampling_rate, channels, text="BT", tokens="B T", keyword="bt"`）。

`prep_kws_data.py` 直接读取 `positive.jsonl` + `negative.jsonl` 构造训练清单：

- 正样本不足时，MUSAN 增广（`--aug-per-pos`，默认 3）把每段扩到 3 段混噪版本；
- MUSAN 还自动生成 ~400 段负样本（noise/music/speech 切 2s 片段）；
- MUSAN 缺失 → **fail-open**：仅用录制数据训练，不阻断。

一键训练见 `services/kws-training/run_kws_v5.sh`。

## 7. 验收（训练后）

```bash
# 用导出的 bt-en 模型重测 recall / FAR
& "D:\AI\envs\joyai-sherpa\python.exe" services\scripts\test_jarvis_kws_e2e.py
# 另可 analyze_kws_captures.py 分析 capture 分布
```

目标：定点 recall ≥90%、FAR ≤2%（阈值仍为 score=10.0 / th=0.25，ADR 0002）。
