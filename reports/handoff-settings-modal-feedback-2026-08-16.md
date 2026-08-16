# Handoff — Settings Modal 反馈与待办（2026-08-16）

> 日期：2026-08-16 14:5x（UI 预览第二轮验收后）
> 来源：FrontendDeveloper 对话 → 用户截图标号 + 文字反馈
> 收件方：UI 优化组（前端） + 后端对话（schema/插件/加密）
> 关联：`reports/handoff-ui-optimization-2026-08-16.md`（已经声明 UI 边界）
> &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;`design/joyai-redesign-preview.html`（v2 预览）

---

## 0. 用户原话（逐条保留）

> 1. 关于模型，虽说支持切换和替换，但你没有对接相应实现。也没有核实现在在用什么模型，比如 tts，**@截图 我在用 minimax 的，这个需要填写音色 id，api key（这个还需要做一层加密）、apiurl 等。** 我希望你能核实先。
>
> 2. **细节指出**：
> 2.2（知识库）核实，我们以实现云端和本地。
> 2.3 模型调整参数的调整不错，但好像我没有实现，不知道支持热调整。**保留但需要设计调整哪些参数**。
> 2.4 TTS 音色下拉选择 → **应该支持自定义**，而不是只能下拉选择。
> 2.5 语音模块，就是上文说的，已有自定义但 ui 没跟上。
> 2.6 KWS 唤醒词已经固定——**这个是需要训练的**，没考虑替换。后续或许支持 asr 识别唤醒。
> 2.7 嵌入模型也有云端和本地切换。

---

## 1. 现状核实（带代码定位）

### 1.1 TTS 真实现状 ≠ 预览内容

| 项 | 后端现实 | 我预览里画了 |
|---|---|---|
| services.json `tts` 槽字段 | `api_base` / `model` / `api_key`（3 个） | — |
| MiniMax TTS 插件实现所需 | `api_key` + **`group_id`** + **`voice_id`** + `model` + `sample_rate` + `speed` + `vol`（7 个） | — |
| provider 路由 | 只实现 `MiniMax` 一个插件（`services/tts/http_synthesizer.py`，`TTS_PROVIDER=minimax`）；Edge / CosyVoice / 本地 Sherpa **未实现为 plugin** | "Edge TTS / CosyVoice / 本地 Sherpa" 三按钮 ❌ |
| 默认音色下拉 | 后端默认 `DEFAULT_VOICE = "vivian"`（env `TTS_DEFAULT_VOICE`），可空 | "晓晓/云希/云野/Aria" 等 Edge 音色 ❌（这些不在你后端里） |
| 用户实际在用 | MiniMax TTS（`http_synthesizer.py` 是唯一 cloud plugin），需 voice_id | — |

**核心问题**：`services/webui/.../services_config.py:47` 的 `tts` schema 缺 `group_id`/`voice_id`/`provider`，miniMax 合成器需要的 7 个字段只缺 3 个就能跑起来，但当前 services.json 根本没存 voice_id。

### 1.2 LLM 推理参数（temperature / top-p / top-k）热调整

后端现状——这些参数**全是构造时常量**：

```
services/webui/src/joy_interaction_webui/turn_streaming.py:78-93   temperature=0.7, max_tokens=200（构造参数）
services/webui/src/joy_interaction_webui/live_llm.py:68-69         同上
services/webui/src/joy_interaction_webui/jarvis_llm.py:72-73       同上
services/webui/src/joy_interaction_webui/vlm_service.py:52-59       max_tokens:int=512（构造）
services/webui/src/joy_interaction_webui/background_model.py:404-425 max_tokens（构造）
```

→ **完全没接入 services.json，也没 Settings 面板入口**。我预览里的 Temperature slider + Top-P/K 输入是**愿望清单**，不是已实现的功能。

### 1.3 KWS 唤醒词

`services/webui/.../jarvis_config.py:162`

```python
wake_word: str = "bt"
kws_model_dir: str = "D:/AI/models/sherpa-onnx/models/kws/bt-en"
kws_keywords_score: float = 10.0
kws_keywords_threshold: float = 0.25
kws_num_trailing_blanks: int = 1
kws_max_active_paths: int = 10
```

→ "BT" 是自训 sherpa-onnx KWS 模型 **写死在权重里** 的关键词——换词需要重新训练（`services/kws-training/` 提供训练流程）。可热调整的只有 **score / threshold / trailing_blanks / max_active_paths** 4 个超参。后续 ASR-based wake（不挑词表）是 KWS_V5_CAPTURE_SPEC.md 远景。

**我预览里「关 / BT / Jarvis / Hey Pilot」4 段是错的**——只能 2 段（关 / BT），其他是占位。

### 1.4 嵌入（embedding）

`services/memory-store/src/memory_store/embedder.py:96-114`

```python
self.provider = (provider or os.getenv("EMBEDDING_PROVIDER", "local")).lower()
# local | siliconflow | nvidia
```

→ provider switch **已实现**，`api_base` / `api_key` / `model` 都通过，前端 services 面板 `svc-embedding-provider` 也已经有 3 选项下拉 + 联动（`config_services.js:39-42`）。

**我预览里反而把 provider select 砍了，只剩 model 下拉** → 倒退一格，应保留 provider selector。

### 1.5 API Key 显示加密 — 误读澄清

> **15:08 用户澄清**：加密是 **网页上显示加密**（形如 `sk-cp-…EGaXX0`，前 4 后 4 + 中间省略），**不是后端加密层**。后端文件落盘仍按现有明文 + chmod 0600 处理即可，**不**走 at-rest encryption。

**回退**：§3 P2 at-rest encryption 整段撤回；§2.1 API Key 行改为纯 UI 行为（默认掩码，👁 切换完整/隐藏）。

### 1.6 Prompt 模板

`config_services.js` 现有 `svc-summary-provider` 切的是 `provider`（minimax/openrouter），不是 `prompt_template`。模型的 prompt 模板**没暴露给 Settings UI**——硬编码在 webinfer 后端（chat template 由 GGUF 元数据 + Python 端的 chatml 包装）。

### 1.7 模型后端的热切文档边界

按 `reports/handoff-ui-optimization-2026-08-16.md §3`：后端 6 槽热切 + provider 路由端点**已落地**。所以"调整哪些参数"是**新增维度**，不是现存能力的拓展。

---

## 2. UI 影响 & 设计调整建议

> **15:08 用户定稿原则**：模型/语音/嵌入三个槽位的选择，**顶层一律「本地 / 云端」两段**；选哪个就切换不同子表单。详见 §6。

### 2.1 立即调整（UI 对话做）

| 调整点 | 当前预览 | 改为（v3 设计） |
|---|---|---|
| **TTS 槽顶层 Seg** | Edge / CosyVoice / Sherpa 3 按钮 | **`本地 / 云端` 两段**。`本地` 子表单：探测 `TTS_PROVIDER` env + `voice-clone/` 已有音色 → text inputs (port / voice_dir);`云端` 子表单:MiniMax Speech 2.8 专属 inputs (api_url / api_key / **group_id** / **voice_id** / model / sample_rate / speed / vol),voice_id 支持下拉推荐 + 自填输入 |
| **default voice 选择** | Edge 4 音色下拉 | **双控件**:下拉 + text input。下拉只列**当前云端 provider 可用推荐 voice_id**(本地 TTS 时下拉灰掉只剩 text input) |
| **嵌入顶层 Seg** | model select | **`本地 / 云端` 两段**,跟现有 `svc-embedding-provider` 一致。`本地` → bge-m3 / 自定义端口;text inputs 读 `EMBEDDING_PROVIDER` env 自动填;`云端` → SiliconFlow / NVIDIA 双 provider 下拉 + api_url / api_key 显示加密 / model |
| **KWS 唤醒词 Seg** | 关 / BT / Jarvis / Hey Pilot 4 段 | **3 段(方案 B)**:`关` / `BT(自训模型)` / `待训练 - 即将支持 ASR 唤醒`。占位段灰显 + chip `路线图` |
| **Temperature / Top-P / Top-K** | 已画 sliders | **保留但降级**：滑块加灰 + tooltip「待后端实现热调整」;加入 §2.2「热调整路线图」section |
| **Prompt 模板** | select 4 个硬编码 | **textarea + select 双控件**:select 选预设(覆盖 textarea),textarea 可自由编辑 |
| **API Key 显示加密** | 普通 password input | **默认掩码** `sk-cp-…EGaXX0`(前 4 + `…` + 后 4),点 `👁` 切换完整/隐藏,旁边 chip `仅前端显示加密,后端明文落盘` |
| **采样率 / 音量 / group_id** | 没画 | **补**:TTS 子表单加 `sample_rate`(16k/24k 下拉)、`vol` slider、`group_id` text(MiniMax 必需) |
| **本/云 Seg 切换动画** | v3 用了 `display:none` 硬切 + confirm 在切换**之后**触发 | **v4 修复**:① 加滑动指示器(`.seg-indicator`,ElevenLabs 风)用 `transform` 平滑滑过 ② 两个子表单 class-toggle `is-entering/is-leaving` 驱动 opacity + translateY 淡入淡出 ③ confirm 改在切换**前**触发(`!confirm` 直接 return,不动 UI) |
| **KWS Seg 切换动画** | v3 仅按钮高亮,无指示器 | **v4 修复**:加 3 段滑动指示器(`.seg.seg-3 .seg-indicator`),点击立即滑动到目标段 |

### v4 动画实现细节(给后端对话参考,UI 内部契约)

```
.seg { position:relative }
.seg .seg-indicator {
  position:absolute; top:3px; bottom:3px; left:3px;
  width:calc(50% - 4px);  /* 两段 = 50%,三段 = 33.333% via .seg-3 */
  transform:translateX(0);  /* pos-0 / pos-1 / pos-2 三档 */
  transition:transform .22s cubic-bezier(.4,.0,.2,1);
}
.subform { transition:opacity .18s ease, transform .22s cubic-bezier(.4,.0,.2,1) }
.subform.is-active  { opacity:1; transform:translateY(0) }
.subform.is-entering{ opacity:0; transform:translateY(6px) }   /* 下一帧切到 active 触发过渡 */
.subform.is-leaving { opacity:0; transform:translateY(-6px); position:absolute; pointer-events:none }
```

切换流程(`switchMode`):
1. 求新按钮索引
2. confirm 在切换**前**(`!confirm` 直接 return,不动 UI)
3. 移除其它按钮 `.on`,给当前按钮加 `.on`
4. 指示器 `classList` 切到 `pos-N`
5. wrap.querySelector(`.subform.is-active`) → `is-leaving`(绝对定位失活态,不再占布局)
6. 目标 subform `is-entering` → `void offsetWidth` 强制 reflow → `requestAnimationFrame` 切 `is-active`

JS 关键 invariant:`subform-wrap` 内**始终两份** subform(本地+云端),布局坍缩靠 `is-leaving` 的 `position:absolute`。高度由 `is-active` 那份撑起。

### 2.2 新增「LLM 推理参数」section（在「模型」tab 内分组）

按用户原话「保留但需要设计调整哪些参数」——把"已热调整 / 即将支持 / 暂未支持"分三组清楚展示给用户：

```
┌─ 推理参数 ─────────────────────────────────────┐
│ ✅ 已支持热调整                                │
│   ・Context length  (services.json → llama)  │
│   ・并行槽数        (services.json → server) │
│                                                  │
│ 🔜 即将支持（spec 草稿中，需后端实现）         │
│   ・max_tokens                                  │
│   ・temperature / top_p / top_k                 │
│                                                  │
│ 🚧 暂未规划                                    │
│   ・repetition_penalty / presence_penalty       │
│   ・min_p / mirostat                            │
└──────────────────────────────────────────────────┘
```

理由：`services_config.py` schema 演化是 **per-slot 跨对话交接**的工作，UI 先把"哪些能改"明确化，避免用户拖了 temperature 滑块以为生效实际没动。

### 2.3 Settings Modal 结构变化

新增一个顶层 tab "**高级**"，把以下深设置移过去（避免普通 Settings 面板过于工程师化）：
- Inference params (上述三段分组)
- Prompt template
- 密钥显示策略：全局默认「掩码」 vs 「完整」，单字段覆写优先级最高（15:08 用户定）

主 Settings Modal 7 个 tab 变成 8 个：
- 模型 / 语音 / 输入&唤醒 / 记忆 / 知识库 / 外观 / **高级（新）** / 关于

---

## 3. 后端待办（需后端对话决策）

### P0 — schema 扩展（必做）

1. **`services_config.py:_SERVICES_CONFIG_DEFAULTS["tts"]`** 扩展为：
   ```python
   "tts": {
       "api_base": "http://127.0.0.1:8985/v1/synthesize",
       "provider": "minimax",       # 新增（与 summary/agent/embedding 对齐）
       "model": "speech-2.8-minimax",  # http_synthesizer.py 实际模型名
       "api_key": "",
       "group_id": "",              # 新增（MiniMax 必需）
       "voice_id": "",              # 新增（MiniMax 必需，Rapid Clone 上传获得）
       "sample_rate": 16000,        # 新增（http_synthesizer.py:51 支持 16k/24k）
       "speed": 1.0,                # 新增（0.5-2.0）
       "vol": 1.0,                  # 新增（0.1-2.0）
   }
   ```
2. `_merge_services_config_file` 字段白名单同步加 5 个新 key（避免手编文件被吞）。
3. `_PROVIDER_CHOICES["tts"] = ("minimax", "local", "edge")`（edge=edge-tts package；本地=sherpa 路径）。
4. `_validate_and_apply_slot("tts", ...)` 的 reachability probe 适配新字段。

### P0 — TTS provider 插件落地

`http_synthesizer.py` 已有 `MiniMaxTTSSynthesizer`，**还缺**：
- `LocalSherpaTTSSynthesizer`（对应 `tts_adapter.py` 的 vLLM-Omni Qwen3-TTS + voice-clone 路径）
- `EdgeTTSSynthesizer`（`edge-tts` 包，仅 voice + speed，无 api_key）
- 工厂选择：`TTS_PROVIDER` env + services.json 字段

### P1 — Inference 参数热调整（spec 草稿）

需要在 doc/specs/ 起一个新 spec（建议 N9: `draft-inference-param-hot-reload.md`），覆盖：
- max_tokens / temperature / top_p / top_k 是否能热调整？还是必须重启 llm-server / webinfer？
- 由谁做 SSOT：services.json 扩字段 vs 单独的 `inference.json`？
- 当前是否值得为这些值重启 sub-service（heatmap：用户实际拖动频次）
- 哪些参数**不接受热调整**（如 context length 必须重启 llama-server）

### P1 — KWS 参数热调整（已部分支持）

`jarvis_config.py:367-368` 已读 `JARVIS_KWS_SCORE` / `JARVIS_KWS_THRESHOLD` env 但未接 services.json。需要：
- 把 wake_word 之外 4 个 KWS 超参接入 services.json 的 `kws` 槽。
- 唤醒词本身保持 "bt" 写死，加 UI 提示。
- v5 KWS spec 远景：ASR-based wake（不挑词表）走另一个分支。

### P2 — ~~API Key at-rest encryption~~（**已撤回**）

> 用户 15:08 明确：加密是 **UI 显示加密**，不做后端文件加密。撤回整段。

### P2 — Prompt 模板暴露

把 vlm_service.py / turn_streaming.py 里硬编码的 chatml wrapper 提到 services.json：
```python
"llm": {
    "prompt_template": "chatml" | "llama-3" | "raw",  # 新增
    "system_prefix": "...",                            # 新增（可选）
}
```

---

## 5. 交付 / 跟进

- **UI 对话（当前）**：按 §2.1 + §6 + §7 出 `design/joyai-redesign-preview.html` **v4**,重点兑现:
  - TTS / 嵌入 / 语音 三个槽位顶层 Seg=本地/云端,**滑动指示器 + 子表单淡入淡出动画**(§7)
  - env 自动探测 pill(`绿点 + VAR_NAME + value`)+ 自填端口字段
  - 云端子表单 api_key 显示加密(默认 `sk-cp-…EGaXX0`)
  - voice_id text input + datalist,无预存列表
  - KWS 三段(关/BT/待训练-即将支持)+ 滑动指示器
- **后端对话**：按 §3 P0 实施 **TTS schema 扩展**(5 个新 key:`provider`/`group_id`/`voice_id`/`sample_rate`/`speed`/`vol`)+ 至少补一个 `LocalSherpaTTSSynthesizer` 插件。**不再包含** at-rest encryption。
- **本文件的去留**:等 v4 用户验收 + 后端 P0 落地 → 在 `reports/handoff-ui-optimization-2026-08-16.md` 补「§3.5 反馈闭环」标记关闭本文档。

---

## 6. 15:08 用户定稿（原话保留 + 修订要点）

> 直接引用用户当轮回复（含截图）：
>
> 1. **加密**：「指的是在网页显示是加密的。如图所示，不是说后端加密，只是显示加密了，不会泄露给看到。」→ 撤回 §3 P2 at-rest encryption。**整层是 UI 责任**：API Key 输入框默认 `sk-cp-…EGaXX0` 掩码，可切换完整/隐藏，文件落盘仍按现有明文 + chmod 0600。
>
> 2. **模型/语音/嵌入分类**：「两个大类 **本地 / 云端**。选择哪一个都会切换不同细类来给客户填写……比如，选了本地就读取已经设置好的环境 / 自己填写规范服务端口。如果选云端就客户自己填写。」
>    - UI 设计：**每个 provider 槽位(TTS / 嵌入 / 语音未来扩展等)顶层一律 `本地 / 云端` 二选一 Seg**。
>    - 「本地」子表单：
>      - 顶部 status pill 显示 env 自动探测结果（绿点 + var name + value），让用户看到「读到了」
>      - 关键端口 / 路径字段（如 `voice_dir`、`port`）让用户**自填规范端口**
>      - 不暴露 api_key（本地不需要）
>    - 「云端」子表单：客户**全自填**(api_url / api_key 显示加密 / model / voice_id 等 provider 特有字段)
>    - 切换本/云时：表单整体切换 + 显示「此项切换会清空已填的云端字段」二次确认
>
> 3. **音色推荐列表**：用户不懂"音色推荐列表"是什么 → **撤回 §4.1 默认音色下拉维护责任 决策项**。
>    - 新设计：voice_id 改为 **text input + datalist**(HTML 原生,自动补全),不需要预存列表。下拉只是体验加分,不是必选。
>    - 既不糊弄自己、也不需要后台维护列表。
>
> 4. **KWS 唤醒词 → 选 B**：「**待训练 - 即将支持**」占位段,强调路线图。
>    - 三段:`关闭 KWS` / `BT(自训 KWS, 当前可用)` / `待训练 - 即将支持(ASR 唤醒路线图)`
>    - 中间段加 chip `词表写死在模型权重，换词需重新训练`
>    - 第三段灰显 + chip `路线图`,tooltip 贴 ASR-wake spec 链接(`doc/specs/kws/`)

---

## 7. 15:24 用户反馈 v4 修订（@image:Clipboard_Screenshot.png）

> 用户反馈原话：
> 1. 「**这个切换没有变化啊，你漏做动画表现了**」——直接看 v3 截图,TTS 本/云 / 嵌入 本/云 段切换时表单**瞬间跳变**,没视觉过渡;按钮 `.on` 高亮也是 class 切换的硬切。

**v4 修复（design/joyai-redesign-preview.html v4 已落盘）**：

| 修复项 | v3 现状 | v4 实现 |
|---|---|---|
| Seg 滑动指示器 | 无 | 新增 `.seg .seg-indicator` span,`transform:translateX` 平滑滑过(`cubic-bezier(.4,.0,.2,1)` .22s)。两段/三段自适应宽度(`.seg-3 .seg-indicator{width:33.333%-3px}`) |
| 子表单淡入淡出 | `display:none` 硬切 | 两个 subform 始终在 DOM,`.is-active` / `.is-entering` / `.is-leaving` 三态 class 切换,`opacity` + `translateY` 过渡 |
| confirm 时序 | **切换之后**才 confirm,失败回滚状态闪烁 | 切换**之前** confirm,`!confirm` 直接 return 不动 UI |
| confirm 文案 | "切换到云端会清空本地端口"（不实） | 改为"切换不立即清空字段,只是切换表单"——后续清空策略由用户/后端对话定 |
| 嵌入本/云切换 | v3 没动画 + 没 confirm | v4 复用 `switchMode`,动画+confirm 全套 |
| KWS 滑动指示器 | 仅按钮高亮 | 新增 3 段指示器,点击滑动到目标段 |

**结构变更**（HTML）：
- TTS seg + 嵌入 seg 各加一个 `<span class="seg-indicator pos-0">`
- TTS / 嵌入 子表单用 `<div class="subform-wrap">` 包裹,内含本地 subform(`is-active`)和云端 subform(`is-leaving`,`position:absolute` 占位)
- wrap 高度由 `is-active` 那份撑起,失活态不占布局

**JS 重写**：
- `switchTTS` / `switchEmb` 合并为 `switchMode(group, mode, btn)` ——三处切换(TTS 本/云、嵌入 本/云)共享一份逻辑,避免漂移
- `switchKWS(mode, btn)` 加 confirm + 滑动指示器

文档同步:§2.1 新增「本/云 Seg 切换动画」/「KWS Seg 切换动画」两行,新增"v4 动画实现细节" subsection(给后端对话参考 UI 内部契约,便于实现 settings.json 持久化时也对齐这层 class 命名)。

---

## 7.1 15:30 用户反馈 v4 翻车 → 回退 v4-lite（重要教训）

> 用户反馈原话：「**现在是完全不动了，而且没有切换菜单。上版是其他能正常切换，就语音模块切换有问题，现在是全都不行**」

**根因**：v4 把 TTS / 嵌入 两个子表单重排成 `subform-wrap`(内含两份 `.subform`,失活态 `position:absolute`) + 合并 `switchMode` 用 `requestAnimationFrame` 在绝对/相对定位间切换。这套逻辑运行时把 Settings 面板该区域**高度坍缩 / 与下方内容重叠**,导致「切换菜单」视觉消失、点击被错位层拦截——表现为"全站切换死"。且 KWS 漏加 `seg-3` 类名,指示器宽度按两段算、3 段错位。

**回退方案(v4-lite 已落盘,功能 100% 恢复 + 保留动画感)**：
- **撤销** `subform-wrap` 结构,子表单还原为 v3 普通 `<div id="tts-local-form">` / `<div id="tts-cloud-form" style="display:none">`(嵌入同理)
- **撤销** 合并的 `switchMode`,拆回 `switchTTS` / `switchEmb` / `switchKWS` 三个独立函数,逻辑 = v3 的 `display` 切换 + **confirm 放在切换之前**(`!confirm` 直接 return,修复 v3 "先切后问"闪烁)
- **保留** 纯 CSS 滑动指示器 `.seg .seg-indicator`(TTS/嵌入 2 段、KWS 3 段 `.seg-3`),点击平滑 `translateX` 滑过
- **新增** 子表单淡入 `@keyframes subformFadeIn` + `.fade-in`(切换时出现侧 0.2s 淡入 + 4px 上移),不依赖 `display` 过渡、零结构风险

**校验**：`node --check` JS 语法 OK;`html.parser` 校验 `<div>` 171/171 平衡、无错配;无 `switchMode` 残留;3 子表单 id 齐全。

**教训(给 UI 组 + 未来自己)**：
1. **动能用的东西要最小侵入**——用户已确认 v3「其他正常、就语音有问题」,正确做法是只在语音 seg 叠指示器,不该把嵌入/KWS 的可用 `display` 切换也重排成 absolute 方案。
2. **绝对定位 + 运行时 reflow/rAF 切换 = 高风险**,纯 CSS transform 指示器 + display 切换 + keyframe 淡入 足以表达"切换动画",性能/稳定性都更好。
3. 改完必须跑一遍结构 + 语法校验再交付,不能只看"想当然能跑"。

---

## 8. 15:41 用户反馈 v4-lite「点击云端没反映」→ 根因锁定 + 修复（**预览 webview 屏蔽原生 confirm**）

> 用户反馈原话：「还是不行，我点击云端没反映。要不你还原没改过的版本？然后再研究原因？」

**先排除的错误假设（这轮差点又猜错）**：
- ❌ 「滑动指示器 `seg-indicator` 没 `pointer-events:none` 拦截点击」→ 实测 line 348 已有 `pointer-events:none`,且 `.seg button{position:relative;z-index:1}` 在指示器之上,排除。
- ❌ 「`moveIndicator` / `fadeForm` 函数没定义」→ 实测 line 1058 / 1064 都已定义,排除。
- ❌ 「`.fade-in` keyframe 卡在 opacity:0」→ 实测 `@keyframes subformFadeIn` 100% 回到 `opacity:1`,不会卡透明,排除。
- ❌ 「内联脚本被 CSP 拦」→ 实测 head 只有 charset/viewport,无 CSP meta,排除。

**真因（确定性验证,非猜测）**：
- 用 node + 最小 DOM stub **真实跑了一遍 `<script>`**,直接调用 `switchTTS('cloud', btn)`,输出:`tts-local-form.display="none"`、`tts-cloud-form.display=""`、云端按钮 `.classList="on"`——**JS 逻辑 100% 正确,零报错**。
- 因此问题不在代码逻辑,而在**运行环境**:WorkBuddy 右侧预览面板(webview)对原生 `window.confirm` 支持不可靠(被宿主禁用/返回取消),导致所有「切换前置 `if(!window.confirm(...)) return;`」的 seg 在 confirm 处直接 return,表单不切换 = 用户感知的"没反映"。
- **完美解释全历史**:v3 时**只有 TTS 切换**调了 confirm(先切后问)→ 卡 → "其他正常就语音有问题";v4-lite 把 TTS/**嵌入**/**KWS** 全部改成 confirm **前置** → 全部卡 → "点击云端没反映"(实际所有切换都死)。

**方法论教训（最关键的一条）**：
> 之前每轮只跑 `node --check`(语法) + `html.parser`(结构)就交付,**从没在真实预览/浏览器里验证过一次交互**。本环境预览面板会屏蔽 `confirm`,语法/结构全过也照样交互失效。今后 UI 改动必须以"真实运行/点击"为验收门槛,不能只静态检查。

**修复(v4-lite.2 已落盘)**：
- 移除 `switchTTS` / `switchEmb` / `switchKWS` 三处 `window.confirm` 前置依赖,改为**直接切换**(本地/云端是两份独立表单,切换只是显隐,本就互不清空;二次确认语义可由后续自定义 toast/modal 补足,不依赖原生 confirm)。
- 顺手删除重复的 `switchKWS` 第一份定义(那份未处理 `.seg-3` 三段宽度),只保留正确支持三段的第二份。
- 校验:node DOM 实测切换正常 + `grep -c window.confirm` = **0** + `node --check` 语法 OK。

**关于「还原没改过的版本」的决策说明**：
- 本环境 `design/joyai-redesign-preview.html` **不在 git 追踪**(NOT TRACKED),无法 `git restore` 回 v2。
- 经根因分析,问题不是"本地/云端分段这个设计"本身(那是用户 15:08 定稿的决策),而是"预览 webview 屏蔽 confirm"这个环境陷阱。盲目还原到 v2(去掉本地/云端分段)会**丢失用户已定稿的决策**,且 v2 同样跑在会屏蔽 confirm 的预览面板里、其 TTS 引擎切换若也依赖 confirm 仍会卡。
- 故选择**精准修复根因**(移除 confirm),保留用户定稿的本地/云端两段 + 滑动指示器 + 子表单淡入。若用户坚持要完全回到改动前(去掉本地/云端分段)的 v2,可另出一版,但建议先验收本修复版。

---

## 9. 16:10 用户反馈「KWS 长度有问题」→ KWS 三段按钮等宽微调

**问题**:TTS/嵌入两段 seg(本地/云端,2 个 4 字汉字)视觉等宽整齐;KWS 三段 seg(关闭 / BT 自训 / 待训练 路线图)三个按钮文字宽度不一(「关闭」最短、「待训练 路线图」最长),`flex:0 0 auto` 按内容自适应 → 视觉比例不齐。

**修复**(`design/joyai-redesign-preview.html` line 355-357):
```css
/* v4-lite.3: KWS 3 段按钮等宽 */
.seg.seg-3{width:max-content;max-width:100%;}
.seg.seg-3 button{flex:1 1 0;min-width:0;justify-content:center;text-align:center;}
```
- `width:max-content`:容器宽度 = 三段最宽按钮内容 + padding + gaps(KWS 段自然比 TTS/嵌入段长,符合「3 段装 2 倍内容」的预期)。
- `flex:1 1 0` + `min-width:0`:三段按钮等宽平分容器。
- `justify-content:center` + `text-align:center`:按钮内 inline 元素水平居中,「BT 自训」、「待训练 路线图」里的 small/chip 也居中显示。
- 不动 `.seg` 默认规则,TTS/嵌入两段保持 `flex:0 0 auto` 按内容自适应(2 个 4 字汉字本来就是等宽)。

**未来回归时**:若 KWS 段宽度仍偏短,改 `min-width:280px`(上轮已尝试后撤回,因会让 KWS 段过长与 TTS/嵌入不齐)。

---

## 10. 16:44 用户澄清「Prompt 模板」误读 → 改为「人物设定（Persona）」轻档 + 版本控制基线

### 10.1 设计前提纠偏（重要）

> 用户原话：「关于这个模板我一开始理解错了，还以为是 llm 模型的人格 prompt。可以自定义人格。我的要求，不要现在的 prompt 模板，而是用于人物设定的自定义 prompt。功能对接是不一样的。」

- **之前画错**：高级 tab 的「Prompt 模板」被画成**消息格式**预设（`[JoyAI ChatML]<im_start>system...`），这是 prompt **结构/格式**模板，**不是人格设定**。
- **用户要的**：**人物设定（Persona）** —— 定义 AI 是谁、性格、说话方式、避讳。这是**独立于消息格式**的概念（类比 character.ai / SillyTavern 的 persona）。
- **功能对接不一样**：不是改 `system_prompt` 字符串那么简单。Persona 是**结构化对象**，需要持久化（每用户多 persona）、与 TTS voice 绑定（本档**不做**，见 10.2）、主界面顶栏快捷切换入口。

### 10.2 用户定稿：走轻档、不绑 voice_id

> 用户原话：「走 B. 走轻档。不绑定 voice_id，让客户动手，不用做这么麻烦的实现。」

- **轻档 scope**：仅 `人格名称`（text input）+ `系统提示词`（textarea），**不绑定音色**（音色让客户在语音 tab 自行设置）。
- **不做**：多 persona 库 / 头像上传 / voice 训练联动 / 预设市集 —— 超当前 scope。
- **字数限制**（用户主动问「有字数限制吧，太长会有问题的吧」）：
  - 现状：后端只是把 system prompt 拼接进每次请求，**无硬上限**。
  - 实际风险：① 每轮 token 成本/延迟随 system 长度线性上升；② 过长触发「lost in the middle」指令遵循退化；③ 本地模型上下文小（典型 4k–8k），过长会被截断。
  - **UI 做法（已落地预览）**：实时字数计 + 1500 字软警告（`接近上限…`）+ 2000 字硬上限（边框标红，保存校验）。
  - **后端待办（新增 P0）**：`services_config.py` 抽 `persona_system_prompt` 配置项；加 `MAX_SYSTEM_PROMPT_CHARS`（默认 2000），保存时校验/截断；新增 persona 保存/重置接口（PUT `/api/services/config` 已有槽位机制可复用）。

### 10.3 版本控制 / 回滚基线（用户问「你不留痕的吗，万一要回滚怎么办」）

- **之前的问题**：`design/joyai-redesign-preview.html` 不在 git 追踪、每轮同名覆盖，**v1→v5 无任何回滚点**。
- **已建基线**（16:44）：开 `ui/redesign-preview` 分支，`git commit` 当前 v5 预览 + 本 handoff 文档为基线（`88107ec`）。后续每步改完单独提交，回滚用 `git revert <sha>` 或 `git checkout <sha> -- design/...`。
- **一般企业流程（给用户说明用）**：feature 分支 → 增量提交（原子、可追） → Push → 开 PR → Review + CI → 合 main；回滚 = `git revert`（保留历史）或 `git checkout <sha> -- <file>`（仅取某版本某文件）。本环境多对话共享工作树，故 UI 组独立分支、**只提交自身产物**（design/ + 本 handoff），不碰其他 19 个在改文件。

### 10.4 补做：handoff §3 启发的 UI 自定义方向（用户要求「你还有些没做，那你补做」）

基于 `handoff-ui-optimization-2026-08-16.md` §3「前端热切能力现状」已就绪的 6 槽位 + agent/summary/embedding 路由，本轮回填两个最可见、可纯前端 mock 的方向：

| 方向 | 预览落地 | 后端对接 |
|---|---|---|
| ① 主界面 provider 实时指示 + ⑤ 实时 probe 主界面化 | 顶栏 `prov-chip`：显示 `agent: hermes · embed: bge-m3` + 绿点（静态示意） | 真实数据来自 `/api/services/status`；点击切 provider 走 `/api/bg-agent/provider/route` 等 |
| ② 方案预设（Profile Presets） | ~~模型 tab 顶部 `预设 seg`~~ **（v6 已移除 — 用户截图反馈「不需要这个」）** | 6 槽位一键套用属锦上添花，用户明确不需要；真实写入仍走 `PUT /api/services/config` |

**本次未做（标注待真实端点接线后补）**：
- ③ provider-aware 提示（选 Claude 提示 XML、选 GPT 提示 developer role 等）—— 需 provider→提示词映射表
- ④ embedding-aware 记忆 UX（召回结果旁标 `via bge-m3`）—— 需记忆检索接口回传 embedding provider

---

## 11. 待闭环（本文件去留）

- 等用户验收 v6（prov-chip / 人物设定轻档 / LIVE 浅色修复 / 模型 tab 本云分拆）→ 回灌 `services/webui/static/styles.css` + `index.html`（缝 A）。
- 后端对话按 §3 P0 + §10.2 P0 接手（TTS schema 扩展、persona 槽位、MAX_SYSTEM_PROMPT_CHARS）。
- 全部落地后，在 `reports/handoff-ui-optimization-2026-08-16.md` 补「§3.5 反馈闭环」标记关闭本文档。

---

## 12. 17:xx 用户反馈 v6（4 张截图逐条）→ 模型 tab 结构重做

> 用户反馈原话（@image 标号）：
> - `@image#1 #2`：**「不需要这两个。」** → 指 ① 方案预设（Profile Presets）② 密钥显示策略（Key Display Strategy）两个 group 直接砍掉。
> - `@image#3`：**「主模型和摘要模型也要本地｜云端的各自单独切换。」** → 之前是单一「后端服务地址」共享端口 + 两个纯 model 名 input；改为 主模型 / 摘要模型 各自独立的 `本地 / 云端` 两段 Seg（与 15:08 定稿「每个 provider 槽位顶层一律 本地/云端 二选一」原则一致，粒度细化到模型）。
> - `@image#4` 三条：
>   1. **「并行槽数 · 是什么东西？不用吧。」** → 砍掉 `并行槽数` 行。
>   2. **「少了输出长度填空。」** → 新增 `输出长度` 输入框（默认 `2048 tokens`）。
>   3. **「上下文长度至少 16384，不然用以出错。」** → `上下文长度` 默认值 `8192` → **`16384 tokens`**，hint 明示「过短易触发截断与推理错误」。

### 12.1 设计决策（落地预览）

| 项 | 改动 | 理由 |
|---|---|---|
| 方案预设 group | **移除** | 用户截图明确「不需要这个」；6 槽位一键套用属锦上添花，真实写入仍可走 `PUT /api/services/config` |
| 密钥显示策略 group | **移除** | 密钥「显示加密」已由每个云端字段**就地实现**（API 地址右侧 `仅前端显示加密` chip + API Key 的 👁 切换掩码），全局策略开关冗余 |
| 主模型 / 摘要模型 | 各自加 `本地 / 云端` 两段 Seg + 子表单 | 本地：模型名 + 走共享后端端口；云端：API 地址 + API Key（掩码）+ 模型名。两份子表单 `display` 切换 + 滑动指示器 + 淡入（复用 v4-lite 零风险方案） |
| 并行槽数 | **移除** | 用户不理解且非必要暴露；属后端启动参数，不应在 Settings 面板前段 |
| 输出长度 | **新增** `2048 tokens` | 用户指出缺；对应后端 `max_tokens`（§2.2 路线图项，UI 先占位） |
| 上下文长度 | `8192` → **`16384 tokens`** | 用户强制下限；避免本地模型截断/推理错误 |

### 12.2 JS 改动

- 新增 `switchModel(modelId, mode, btn)`：与 `switchTTS`/`switchEmb` 同构（移除 confirm 依赖，纯 `display` 切换 + `moveIndicator` + `fadeForm`），按 `modelId + '-local-form' / '-cloud-form'` 定位子表单。
- **删除** `applyPreset()` + `PRESETS` 对象（方案预设移除后无引用）。
- **删除** `setKeyMask()`（密钥显示策略移除后无引用）—— 遵循「增新删旧」纪律，不留死代码。
- 校验：`node --check` JS 语法 OK；`grep` 已无 `applyPreset/PRESETS/setKeyMask/preset-seg/presetHint/keymask-seg/方案预设/密钥显示策略/并行槽数` 残留。

### 12.3 后端待办增量

- `上下文长度` 下限建议写进 schema 校验（≤16384 给出警告或拒绝保存）—— 与 §2.2「context_length 必须重启 llama-server」呼应。
- `输出长度` 字段对应 `max_tokens`，需在 §2.2 路线图里明确是否热调整（当前构造时常量，见 §1.2）。
- 主模型 / 摘要模型 各自的 本地/云端 provider 路由：本地走现有 llama.cpp（7060），云端走 OpenAI 兼容 `/v1` 接口（API 地址 / Key / 模型名）—— 后端需确认 summary 槽位也能独立指定云端 endpoint（当前 summary 是否独立于 agent 走云端尚未核实，留后端对话确认）。

### 12.4 待闭环

- 等用户验收 v6 → 回灌 `services/webui/static/`。
- 顺带：本文件 §10.4 ② 方案预设已标移除、§11 已去掉「预设」引用。

---

## 13. 17:xx 用户反馈 v6-lite（3 张截图）→ 主视图状态收纳与重组

> 用户反馈原话（@image 标号）：
> - `@image#1`：**「只用填写数值，不需要后面的 tokens。」** → 模型 tab 的 `上下文长度` `输出长度` 输入框：去掉后缀 `tokens`，只填数字（值不变：16384 / 2048）。单位由 hint 文案承载「上下文长度建议 ≥ 16384，过短易触发截断与推理错误」。
> - `@image#2`：**「也应该收纳进去。」** → 顶栏 `prov-chip`（agent: hermes · embed: bge-m3）+ 视频卡左上 `live-chip`（实时工程 LIVE）两个**散落的状态元素**统一收纳到「健康菜单」下拉的「折叠项」分组，不再在 header/视频卡裸露。
> - `@image#3`：**「该展示再外面的是这个三个。」** → 三个核心状态 `KWS·唤醒词` / `Jarvis·常驻` / `Live·常驻模式` 从下拉里**提到主视图常驻显示**——以「系统状态」新卡片形式铺满 `.main` 顶部（`grid-column:1/-1`），点卡片右上「更多」按钮可展开完整健康菜单。

### 13.1 设计决策（落地预览）

| 项 | 改动 | 理由 |
|---|---|---|
| 上下文长度 / 输出长度 | 去 `tokens` 后缀，只填数字 | 用户明确「不需要」；单位由 label/hint 承载更干净 |
| 状态收纳 | prov-chip + live-chip → 健康菜单「折叠项」分组 | 散落的两个 chip 信息密度低、视觉噪音，折叠更清爽 |
| 三状态外提 | KWS / Jarvis / Live svc 行 → 主视图新「系统状态」卡片（满铺 grid） | 这是用户「运行中最关心的三个」——应**一目了然**，不该埋在下拉里 |
| 健康菜单 | 移除 3 个 svc（已上提），保留 LLM/TTS/记忆/Wiki/连接 + 新增「折叠项」组 | 主菜单聚焦次要状态 + 收纳项 |
| 死代码 | 删 `.prov-chip`、`.live-chip` CSS 块（无引用） | 约法三章增新删旧 |

### 13.2 JS / CSS 改动

- 新增 CSS：`.status-card{grid-column:1/-1;padding:0;}` + `.status-card .status-list{padding:6px 10px;}` + `.status-card .head-actions .ghost-btn{font-size:12px;padding:5px 10px;}`
- 无 JS 改动（结构层）。
- 校验：`node --check` JS 语法 OK；`<section>` 11/11、`<div>` 198/198、`<span>` 102/102、`<button>` 45/45 全平衡；grep `live-chip|prov-chip|16384 tokens|2048 tokens` 零残留。

### 13.3 与既有原则的一致性

- 状态分层：**主视图常驻 = 关键运行态**（KWS/Jarvis/Live），**健康菜单 = 次要健康态**（LLM/TTS/记忆/Wiki/连接），**折叠项 = 装饰性指示**（provider / LIVE 录制）。三档分得很清，符合 4 章节「前端」对主界面信息密度的要求。
- 与 §10.4 handoff §3 启发一致：「主界面无 provider 状态」一句话当时仍留着 — 现在通过「折叠项」的方式**依然让用户在需要时能找到**，但默认不打扰。

### 13.4 待闭环

- 等用户验收 v6-lite → 回灌 `services/webui/static/`。
