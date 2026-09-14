# services/scripts — 维护脚本索引

> ⚠️ **2026-09-14 校正**：原写"仓库里所有可独立运行的 helper 脚本都列在这里"——实际本目录有 **24** 个脚本，索引只覆盖一部分。
> 下方新增「**未索引脚本**」一节补全。新增脚本请同步追加；改语义也要同步更新对应列。

## 日常维护（改动代码后必跑）

| 脚本 | 用途 | 用法 |
|---|---|---|
| **`sync-docs.py`** | 改代码后自动追加 `DELIVERY.md §7` 一行 + `doc/main/00-main-direction.md §4.0` 一项；打印受影响 doc 的复核清单 | `/d/AI/envs/joyai-main/python.exe services/scripts/sync-docs.py --version v3.4 --change "..." --affected doc/adr/0003-llm-reply-panel.md` |

> ⚠️ 上例原写 `python services\scripts\sync-docs.py`（反斜杠 + 裸 `python`）——在 DSH 环境下裸 `python` 是 Windows Store 存根，**静默失败**。见 `doc/environment-dsh.md`。

## 服务生命周期（启动 / 停止 / 单服务重启）

| 脚本 | 用途 | 用法 |
|---|---|---|
| `run-windows.ps1` | 编排器（默认 / minimal / voice / gaming 四模式 + `-Restart <name>`） | `powershell -ExecutionPolicy Bypass -File services\scripts\run-windows.ps1 -Restart llama-main` |
| `stop-windows.ps1` | 单服务停（`-Only <port>`） | `powershell -ExecutionPolicy Bypass -File services\scripts\stop-windows.ps1 -Only 8985` |
| `start-joyai.ps1` / `stop-joyai.ps1`（根目录） | 编排器的薄包装 | `powershell -ExecutionPolicy Bypass -File stop-joyai.ps1 -DryRun` |

## KWS 自训 / 测试（sherpa-onnx）

| 脚本 | 用途 |
|---|---|
| `record_kws_corpus.py` | 录唤醒词正样本 |
| `prep_kws_data.py` | 把原始录音切成 KWS 训练样本 |
| `kws_param_sweep.py` | KWS 甜蜜点参数扫描（FAR / recall 矩阵） |
| `test_sherpa_load.py` | sherpa-onnx 模型加载冒烟 |
| `test_jarvis_kws_e2e.py` | KWS 端到端（sherpa-onnx → KWS 引擎） |
| `test_jarvis_state_machine.py` | 全链路 e2e（KWS → ASR → LLM → TTS） |
| `test_jarvis_state_machine_lite.py` | 状态机测试（跳过 KWS / ASR / LLM） |
| `generate_event_audio.py` | 生成 wake.wav / goodbye.wav / error.wav 事件音频 |

## 声音克隆 / MiniMax

| 脚本 | 用途 |
|---|---|
| `smoke_voice_clone.py` | voice-clone API 冒烟测试 |

## 未索引脚本（2026-09-14 补全）

| 脚本 | 用途 |
|---|---|
| `analyze_kws_captures.py` | 分析 KWS 采集结果 |
| `assemble_kws_corpus.py` | 组装 KWS 训练语料（正/负样本） |
| `build_negative_pool.py` | 构建负样本池 |
| `ingest_esc50.py` | 导入 ESC-50 环境音数据集 |
| `make_esc50_musan_layout.py` | 生成 ESC-50/MUSAN 目录布局 |
| `benchmark_4state_notforme.py` | 四态 `not-for-me` 基准（addressee-detection Phase 2） |
| `benchmark_campplus_addressee.py` | CAM++ 声学门控基准（Phase 1） |
| `test_assemble_kws_corpus.py` | `assemble_kws_corpus.py` 的单测 |
| `verify-services.ps1` | 服务探活（PowerShell 版） |
| `verify-services.py` | 服务探活（Python 版，避免 PS 解析问题） |

> 另有 data env 与示例文件（非脚本）：`run-windows.env`（gitignore，含密钥）、`run-windows.env.example`、`run.sh`、`stop.sh`。

## 维护节奏提醒

- **改任何 Python 代码后** → 跑 `sync-docs.py`（除非你确认这次纯内部 refactor、无 doc 影响）
- **改 ADR / 路线图后** → 手动 `doc/adr/<id>.md` 加实施节，**不要**等 sync-docs.py —— 它只动 DELIVERY + 路线图
- **改 jarvis-mode / voice-clone / memory-architecture 等子系统 doc 后** → 手动 `doc/<system>.md §15` 加变更记录
