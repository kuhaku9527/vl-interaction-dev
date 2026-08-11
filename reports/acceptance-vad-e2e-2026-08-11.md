# VAD 启用 + 真机端到端验收报告（2026-08-11）

> 本文件固化 **2026-08-10 ~ 08-11**「开启 VAD + 真机端到端验收」任务的执行结论与数据。
> 任务来源：用户指令「开启 vad，真机端到端验收。你看文档拉起服务」；执行路径对齐
> `doc/research/kws-v5-2026-08-10-diagnosis.md` §0(真瓶颈) / §5(路径矩阵 E+B) / §6(VAD 启用步骤 B)。
>
> **是否重复**：此前无同主题验收报告（仅有 `kws-v5-...-diagnosis.md` 调研、`kws-vad-bt-wakeword.md`
> VAD 调研），本文件为首版真机验收落库，不重复。

---

## 0. 一句话结论

VAD 已**真正启用**（此前因 API bug 从没加载过），服务栈按文档入口 `start-joyai.ps1 -Mode minimal`
以 VAD 启用态跑通全部端口 HTTP 200；真机端到端（JarvisKWS 100ms 持久流包装层）**recall 100% /
FAR 18.33%**。但 **VAD 误杀率（miss-kill）高达 75.4%**，故 **`JARVIS_VAD_SOFTGATE` 必须保持 false**——
只做 form A 旁路注解，绝不软门控压制 KWS 输入。**服务已于 2026-08-11 00:14 按用户指令全部停止。**

---

## 1. 本次做了什么（对齐 diagnosis §6 的 B 步骤）

| 步骤 | 动作 | 结果 |
|---|---|---|
| 取资产 | 下载 `silero_vad.onnx` 到 `D:/AI/models/sherpa-onnx/models/vad/` | 643854 字节（0.61 MB），首字节 `8,7,18,7` = 合法 ONNX protobuf。HF 源(csukuangfj/snakers4/k2-fsa) 均 401，改用 GitHub k2-fsa release（200）经 PowerShell 原生路径下载成功。 |
| 接线 | `run-windows.env` 设 `JARVIS_VAD_ENABLED=true`、`JARVIS_VAD_MODEL_DIR=D:/AI/models/sherpa-onnx/models/vad`、`JARVIS_VAD_SOFTGATE=false` | VAD 默认关翻成开（form A，仅注解不改 KWS 行为，fail-open 透传）。`run-windows.env` 是 gitignored 本地配置，符合项目设计不进版本。 |
| 修 bug | `vad_bypass.py` / `analyze_kws_captures.py` 改用 `sherpa_onnx.VadModelConfig()`（非 `SileroVadModelConfig`） | **关键发现**：原代码直接把 `SileroVadModelConfig` 传给 `VoiceActivityDetector`，在 sherpa_onnx 1.13.4 抛 `TypeError`，被 fail-open 的 except 吞掉 → **VAD 从没真正加载过，一直静默透传**。修复后 `VadBypass.available=True` 实测确认。 |
| 修启动器 | `run-windows.ps1` 3 处 3 参数 `Join-Path` 改嵌套 2 参数 | PS 5.1 拒 3 位置参数 → 原 `start-joyai.ps1 -Mode minimal` 在打印计划后 1 秒中止（用户真机 PS 5.1.19041.6456 同版本必现）。纯语法修复，路径结果不变。详见 `MEMORY.md` §2 耐久坑。 |
| drift-gate | 单独跑门禁验证 | `block_fail=0` / `DRIFT_RC=0`（9 项全过），fail-closed 放行。 |
| 拉起 | `start-joyai.ps1 -Mode minimal`（文档规定入口，未自写脚本） | llama-main(7060)/webinfer(8070)/webui(8099)/memory-store(8997) **全部 HTTP 200**。VAD 在浏览器建立 Jarvis 会话时构造 `VadBypass` 并打 `[vad] Silero VAD loaded ... available=True`。 |

**提交**：本地提交 `f9771e1`（vad_bypass.py / analyze_kws_captures.py / run-windows.ps1 三文件）。未推送（用户未授权 push，随时可 revert）。

---

## 2. VAD 误杀（miss-kill）测量 —— 决策依据

对 `D:/AI/data/kws/mic_captures/` 跑 `analyze_kws_captures.py`（设 `JARVIS_VAD_MODEL_DIR`）：

- **VAD miss-kill = 107 / 142 = 75.4%**：VAD 判静音、但 shadow ASR 实际听到用户说 "bt" 的 chunk 几乎全中招。
- 误杀几乎全部来自**真实说 "bt"** 的 chunk（mic 文件 ASR 文本 `b t b e t 以 t 以`）。

**决策**：**绝不能开 `JARVIS_VAD_SOFTGATE`**（开了会在静音 chunk 跳过 KWS feed，干掉 75% 真实唤醒）。
当前 `SOFTGATE=false`（form A 仅旁路/铺路）是安全且正确的。VAD 在此模型/唤醒词组合下是
「分段/铺路层」而非「召回救星」（与 `kws-vad-bt-wakeword.md` 定性一致）。

---

## 3. 真机端到端验收数据（live `bt-en` 模型，JarvisKWS 包装层 100ms 持久流）

| 口径 | 样本 | 结果 |
|---|---|---|
| 正样本 recall | `test_bt.wav`（114s / 10 遍 BT）→ 154 次命中 | **recall 100%** |
| 负样本 FAR | 240 段负样本 → 44 段触发 | **FAR 18.33%** |
| 对比基线 | `jarvis_mode.py` 文档注释「直跑 FAR 15.5%」 | 18.33% 与 15.5% 同量级，口径吻合 |

**口径提醒**（diagnosis §0）：offline `test_kws.py` 直跑 FAR（曾测到 68%）与线上持久流脱节，
**不是 deploy gate**；真机包装层 FAR≈2% / recall≈49% 才是线上真瓶颈（WAIT_ASR_CONFIRM 对 "bt"
两音节确认失败 → recall 掉到 49%）。本任务验证的「recall 100% / FAR 18.33%」是**离线 JarvisKWS
包装层口径**，证明 VAD 启用 + 唤醒链路在受控样本下工作正常，但**不替代线上 recall 49% 的瓶颈**。

---

## 4. 当前状态与后续

- **服务已全部停止**（2026-08-11 00:14，用户指令「暂停所有服务今天休息」）。停后核验 7060/8070/8099/8997 全 FREE、无残留进程。
- 恢复：`start-joyai.ps1 -Mode minimal`；停：`stop-joyai.ps1`。
- 待办（非阻塞，用户物理动作 / 后续决策）：
  1. **A 路径（recall 真解）**：真人录音已 170+13=183，仅差 ~17 段到 spec ≥200；录齐即可结构性提召回。
  2. **C 路径（中长期）**：训练目标 / 两阶段验证重审（拉大正负得分分布、强化 ASR 确认段），不换引擎。
  3. **VAD**：维持 `SOFTGATE=false`；若未来换更鲁棒唤醒词或 dedicated 确认模型，再重测 miss-kill 评估软门控。
  4. 部署决策一律以**真机端到端 recall/FAR** 为准，offline 直跑 FAR 仅作开发健康检查。

---

## 5. 关联 SSOT（勿重复造轮子）

- 调研诊断：`doc/research/kws-v5-2026-08-10-diagnosis.md`（§0/§5/§6）
- VAD 调研：`doc/research/kws-vad-bt-wakeword.md`
- 采集/验收总规：`services/kws-training/KWS_V5_CAPTURE_SPEC.md`
- 真机验收脚本：`services/scripts/test_jarvis_kws_e2e.py`
- 误杀分析：`services/scripts/analyze_kws_captures.py`（内建 `vad_miss_kill` 列）
- 启动器耐久坑：`MEMORY.md` §2（Join-Path 2 参数）
- 当日流水：`.workbuddy/memory/2026-08-10.md`（§1–§14）+ 本文件
