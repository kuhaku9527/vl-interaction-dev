# KWS v5「BT」回归记录表（§10.3）

每次「加数据 + 重训 + §10.2 验收」后 append 一行。markdown 表即单人实验登记。
**纪律**：未记此表、未跑 §10.2，不得宣称"模型更好"。

| 日期 | 模型 tag | 数据配方(域:段) | 参数(epoch/lr) | recall_broadcast | recall_gameDAC | FAR | 总recall | 结论 |
|------|----------|----------------|---------------|------------------|----------------|-----|----------|------|
| 2026-08-10 | bt-en-v5-0810-exp_bt_v5 | B:85+15hold G:85+15hold +live13 / neg40 | 30/ep, best val_loss=6.09 | 93.3% | 100% | **100%** | 96.7% | **FAIL** |
| 2026-08-10 | bt-en-v5-realneg | pos136(录,无live) / neg277(含bt-zai-ma正50源≈87段) | 30/ep, best val_loss=15.12 | 42.9% | 69.2% | **27.3%** | 52.9% | **FAIL(翻转:recall崩)** |
| 2026-08-10 | bt-en-v5-fixed | pos183(170录+13live) / neg190(去bt-zai-ma正) | 30/ep, best val_loss=6.38 | 81.8% | 64.3% | **89.5%** | 75.0% | **FAIL(recall回弹但FAR回爆)** |
| 2026-08-10 | bt-en-esc50 | pos147 / neg990(762非语音ESC-50) | 30/ep | 63.6% | 78.6% | **49.0%** | 69.4% | **FAIL** |
| 2026-08-10 | bt-en-candidate-aug-20260810_212842 | pos147+549增广 / neg990 | 30/ep | 90.9% | 78.6% | **68.2%** | 86.1% | **FAIL(增广↑recall但加噪抬FAR)** |
| _(示例/目标)_ | bt-en-v5-0809 | B:100 G:100 + live | 30/ep | 92% | 88% | 1.5% | 90% | PASS(目标线) |

## 2026-08-10 realneg 实跑详情（FAIL 翻转：recall 崩）

- **数据配方**：正样本=170 录(136训/34测，device 已补)；负样本=277 训(来自 bt-zai-ma/正50源+负200源 + 录40，
  build_negative_pool 切段去重)。**负:正 = 222:136 ≈ 1.63:1**（扭转了 FAIL 版 32:183 的正压倒）。
- **§10.2 验收**：recall_overall 52.9% <90% ❌；FAR_overall 27.3% ≫2% ❌ → FAIL。
- **阈值扫描**（同模型，keywords_threshold 0.25→1.0）：
  | thr | recall | FAR |
  |---|---|---|
  | 0.25 | 52.9% | 27.3% |
  | 0.50 | 52.9% | 25.5% |
  | 0.70 | 52.9% | 14.5% |
  | 0.90 | 23.5% | 5.5% |
  | 1.00 | 0.0% | 0.0% |
  → recall 在 0.25~0.70 卡死 52.9%（模型根本不认 BT），阈值救不回 → **模型级 recall 失败，非配置问题**。
- **两处 silent 坑（本次新发现，已修）**：
  1. `mic_captures/`(13条有效live) 其实存在，但 prep 默认回退 Windows 路径 `D:/AI/data/kws/mic_captures`，
     WSL 解析不到 → live 正样本被 fail-open 跳过 → recall 崩的主因之一。需显式 `--live-capture-dir /mnt/d/AI/data/kws/mic_captures`。
  2. 正样本 manifest 缺 `device` 字段（路径文件名含 broadcast_/gameDAC_ 前缀可推导）→ 已补，prep 透传。
  3. 建池切段余数 <0.3s 碎片（positive_0001.wav 114s 末段 0.048s）→ 训练 fbank 崩溃；已修 chunk_long_wav 丢弃短余数。
- **根因判断（待用户定方向）**：277 负样本中混入了 bt-zai-ma/positive（"zai ma" 真人语音）作为硬负样本，
  与 "BT" 声学过近、且量过大（222训负 vs 136训正），把模型压成"几乎不触发" → recall 崩；
  同时 FAR 仍 27%（负样本没效教会"非BT"）。**单纯加负样本 = 把失败从 FAR 翻到 recall，治标不治本。**
- **待办（修复方向，待用户确认）**：① 负池剔除 bt-zai-ma/positive（保留 negative+录的，约240条较干净）；
  ② 显式恢复 live 采集（修 WSL 路径）；③ 重 prep→重训→重验。必要时先 ASR 扫负池剔除含"BT"的污染段。

## 2026-08-10 fixed 实跑详情（recall 回弹，FAR 回爆 → 证实耦合）

- **修复动作**：负池剔除 bt-zai-ma/positive（避免"在吗"易混语音）；prep 显式 `--live-capture-dir /mnt/d/AI/data/kws/mic_captures` 恢复 13 live 正样本；
  并修两处 silent 坑（live 条目缺 device、prep 默认 Windows 路径找不到 live 目录）。
- **数据配方**：pos=183(170录+13live)，neg=190(去zai-ma正)；**pos训147 / neg训152 ≈ 1:1**（配比健康）。
- **§10.2 验收**：recall_overall 75.0%(broadcast 81.8%/gameDAC 64.3%) <90% ❌；FAR_overall 89.5% ≫2% ❌ → FAIL。
- **阈值扫描**（0.25→1.0）：recall 75→11%，FAR 89.5→0%，**无任何阈值双过**（0.90 时 recall 11%/FAR 2.6%）。
- **结论（决定性）**：recall 与 FAR 经"触发灵敏度"强耦合——要 recall 高模型必爱触发→FAR 爆；要 FAR 低模型必保守→recall 崩。
  **三条实验（32/222/152 负）沿曲线移动但都到不了 (recall≥90 & FAR≤2) 的角点。**
  根因 = **负样本全是语音（bt-zai-ma/negative="非zai-ma语音" + 录的环境声），与 "BT" 声学过近，模型无法干净分离**；
  spec 原设计靠 **MUSAN 非语音噪声** 解耦，而本机无 MUSAN、网络不可下。
- **出路（待用户定）**：
  ① **本地采环境噪声**（开 Jarvis 录各 mic 的 room-tone/静默背景，切片当非语音负样本）——等价于本地 MUSAN，无需网络；
  ② 放宽 FAR 门禁到务实值（如 ≤10~15%）先上线 fixed（recall 75% 优于当前 7/10 备份的不明状态）；
  ③ 重开 VPN 重试 MUSAN 下载（用户曾因网速关 VPN）。
  **当前线上仍用 bt-en.bak-jul10 备份（保 Jarvis），fixed 未部署（FAR 89.5% 会过触发）。**

## 2026-08-10 实跑详情（FAIL 根因）

- **验收命令**：`test_kws.py --model-dir .../bt-en --manifests-dir .../test_manifests --bar-recall 0.9 --bar-far 0.02`
- **判据**：recall_overall 96.7% ≥90% ✅；FAR_overall 100% ≫2% ❌ → FAIL。
- **阈值扫描**（同模型，区分"配置松" vs "模型烂"）：
  | keywords_threshold | recall | FAR |
  |---|---|---|
  | 0.25(默认) | 96.7% | 100% |
  | 0.5 | 96.7% | 97.5% |
  | 0.7 | 96.7% | 97.5% |
  | 0.9(极严) | 96.7% | 87.5% |
  → 阈值从 0.25 拉到 0.9，FAR 仅 100%→87.5%、recall 纹丝不动。**结论：模型本身过触发，阈值调参救不回 → 训练缺陷（负样本不足）。**
- **根因**：MUSAN 本机未装（docs §389 已记），prep fail-open 只用录的 40 负样本 → 训练池 183 正 / 32 训负（≈5.7:1），模型未学会"非 BT 时保持 blank"，塌成"永远说 BT"。
- **处置**：失败导出存 `.../kws/bt-en.failed-20260810`；生效 `bt-en/` 已恢复 7/10 备份 `bt-en.bak-jul10`（Jarvis 不中断）。`exp_bt_v5/best.pt` 保留可复训。
- **待办（修复路径待用户定）**：补负样本（装 MUSAN 让 prep 生 ~400 / 或重录 ~200 负 / 或合成增强）→ 重训 → 重验。

## 2026-08-10 esc50 / A增广 实跑详情（诊断收敛：真瓶颈是线上 recall，非 offline FAR）

- **esc50 轮**：负样本=990（762 段 ESC-50 非语音，替 MUSAN）；pos=147 原始（未增广）。
  §10.2 验收 recall_overall 69.4%(broadcast 63.6%/gameDAC 78.6%) / FAR_overall 49.0% → FAIL。
  决定性：非语音负样本把 FAR 89.5%→49%（有效降 FAR），但 recall 75%→69.4%（沿耦合曲线平移），
  **曲线没拐到角点**；瓶颈从 FAR 转到 recall（正样本仅 147、2 个 mic，多样性薄）。
- **A 增广轮（candidate-aug-20260810_212842）**：用 prep 内建 `build_augmented_positives` + ESC-50
  拼 MUSAN 布局，pos 增广到 147+549；负样本保持 990（变量隔离）。
  §10.2 验收 recall_overall **86.1%**(broadcast 90.9%/gameDAC 78.6%) / FAR_overall **68.2%** → FAIL。
  - **A vs esc50（同纯原始测试集，严格可比）**：recall +16.7pp（69.4→86.1）坐实"正样本多样性薄"是真因；
    FAR 不降反升 +19.2pp（49→68.2），加噪增广让模型更激进触发，过触发加剧。
  - **原 §10.2 FAIL 是脚本崩溃非模型不达标**：test_kws.py:244 读 e["device"] 抛 KeyError（增广条目缺 device
    且混入测试集），真实指标没算出来就被判 FAIL。已修 prep（增广只进训练集 + 补 device），重测得真实 86.1/68.2。
- **阈值扫描（candidate-aug，thr 0.25→3.0）**：0.25→86.1/68.2；0.5→52.8/22.7；0.75→19.4/5.1；1.0↑→0/0。
  **无甜点区** → recall 对阈值极敏感（0.25→0.5 仅 +0.25 掉 33pp）→ 模型未学会"BT=高分"，
  属表征/训练层面问题，阈值平移救不回。
- **⚠️ 评估口径脱节（本次最重要发现）**：`test_kws.py` 的 §10.2 offline FAR 是"直跑干净流"口径，
  与线上真实链路（100ms chunk 持久流包装层 + WAIT_ASR_CONFIRM）脱节。
  `jarvis_mode.py:132` 实测：直跑 FAR 15.5%/recall 75.5%；**线上包装层 FAR 2.0%（已达标）/ recall 49.0%**。
  → 线上 FAR 本来≈2%，真瓶颈是**线上 recall 49%**；offline 直跑 FAR≤2% 卡模型是假目标，
  抬阈值压 FAR 必塌 recall。**部署决策须以真机端到端（完整包装层）recall/FAR 为准**，offline 直跑仅作开发健康检查。
- **事实更正（核查后）**：① 真人录音=170(85+85 均衡)+13 live=183，接近 spec≥200（初版写"147/gameDAC不足"错）；
  ② VAD 代码已落地（`vad_bypass.py`+集成+`services/scripts/run-windows.env` L88-97 env+测试），默认关、
  `silero_vad.onnx` 资产缺失→fail-open 透传（初版说"未实施"错）。
- **沉淀文档**：`doc/research/kws-v5-2026-08-10-diagnosis.md`（调研/诊断落库，避免只留临时日志被清）。
- **当前状态**：线上=esc50 备份恢复 live（保 Jarvis）；candidate-aug 未部署（需真机验）。prep 修复 +
  脚本（ingest_esc50 / make_esc50_musan_layout / build_negative_pool 改 / sweep 脚本）未 git 提交。

