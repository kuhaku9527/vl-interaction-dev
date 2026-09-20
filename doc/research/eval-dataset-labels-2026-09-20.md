# 上游数据集标注能否直接作为「决策能力评测」ground truth

> **端点身份**：调研端点（research / AFK，严格只读）｜**日期**：2026-09-20
> **唯一输入**：`datasets/README.md`、`datasets/convert_data.py`（读完整）、HF 数据集页 + HF API + HF 原始 README
> **未做**：未 `hf download`、未下视频、未跑 pytest、未起服务

---

## 0. 一句话结论

**部分能。** `response` 可直接推导；`delegate` 需假设（可推导，靠字符串解析）；`silence` 可被填充但语义已被污染（混入"正在思考的延迟窗口"与"无人在说话"）；`not-for-me` **完全不可推导**——原始标注无任何 addressee 字段或负样本，且 `convert_data.py` 主动把该区分抹平成 `</silence>`。故**不能**直接当四态评测 ground truth，只能当"response vs 非 response"的二态（充其量三态）评测集。

---

## 1. 真实字段与语义

### 1.1 原始 schema（README §2 原文）

```json
{"video_name":"name.mp4","video_path":"/path/to/videos/name.mp4","task_type":"task_category","source":"source_dataset",
 "question":[{"content":"user question or instruction","time":"4"}],
 "response":[{"content":"expected answer or event response","time":"7"}]}
```

README §2 的时间定义（原文）：
- `question`：*"`time` is the timestamp in seconds when the prompt is issued."*
- `response`：*"`time` is the timestamp in seconds when the answer or event should occur."*

**"is issued" vs "should occur" 的用词差异是关键**：前者是"用户开口"，后者是"应答应当发生"，两者是**不同事件**，中间那段不是沉默决策，是**处理延迟**。

### 1.2 字段只有 6 个，无决策字段

HF 数据集页 viewer 列头：`video_name` / `task_type` / `source` / `question` / `response`。

**没有** addressee / speaker / decision / label。`task_type` 是任务分桶（文件树：`chat/chat_shard_01..06.json`、`background/background.json`、`event_grounding/event_grounding.json`、`narration/narration.json`），**不是**每秒决策标签。

### 1.3 两个 schema 陷阱

`parse_times` docstring 原文：*"Parse a time field such as `'8'` or `'5,6,7'`"* → **`time` 可多值**，一条 content 可落多秒。

`convert_sample` 注释原文：*"support both flat list [...] and nested list [[...]] formats"*。HF 预览行确认**实际发布的 `response` 就是嵌套 list**：`"response": [ [ {"content":"...","time":"13"} ] ]`。嵌套层疑似携带事件簇分组语义，代码用 `flat_responses.extend` 把它**丢弃**了。

### 1.4 规模 / 许可（HF API）

`license: apache-2.0`、arxiv `2606.14777`、`usedStorage: 3487716460`（**≈3.49 GB，仅标注 JSON，不含视频**）。自述 *"over 4 million time-aligned video-language interaction samples"*。视频需按 `source` 自取（README §2 要求手工填 `video_path`）。

## 2. 四态可推导性逐条判断

四态以本地 SSOT `doc/specs/addressee-detection.md` §4.1 定义为准。

| 态 | 判定 | 依据 |
|---|---|---|
| `response` | **可直接推导** | 显式标注 + 明确时刻语义 |
| `delegate` | **需假设** | 仅内联标记，非字段 |
| `silence` | **可推导但语义污染** | 填充产物，非标注决策 |
| `not-for-me` | **不可推导** | 无字段、无负样本、管线未开源 |

### 2.1 `response` ✅

`convert_data.py` 明确落标：

```python
if sec in response_map:
    messages.append({"role":"assistant","content":f"</response> {response_map[sec]}"})
```

且 README §2 定义 `time` 即"应答应当发生"的时刻。**唯一既有显式标注、又有明确时刻语义的态。**
⚠️ 但 label 是**文本不是类别**，且文本内混着**其它标记**（见 2.2），评测需自行抽类别 token。

### 2.2 `delegate` ⚠️ 需假设

HF 预览前数十行几乎全是 `task_type: "background"`，其 `response[].content` 形如：

```
"Solving this directly is error-prone, so I will send it to the background for a complete solution first. </delegation> Calculate the integral: ..."
```

即上游把 delegation 编码成 **response 文本内部的内联标记 `</delegation>`**，**不是**独立字段或独立态。

可推导路径两条：`task_type == "background"`，或 `"</delegation>" in content`。但两个前提**README 未书面确认**：(a) `background` 分桶 ↔ delegate 一一对应；(b) 词形 `</delegation>` 在所有分片稳定（本地 `prompt_constants.py` 用同一词形，属**旁证**而非数据集自身契约）。

### 2.3 `silence` ⚠️ 语义污染（最致命）

机制是**默认填充**：

```python
else:
    messages.append({"role":"assistant","content":"</silence>"})
```

README §4 的 Q=4s / R=7s 示例确认：第 4、5、6 秒全为 `</silence>`。**用户在第 4 秒已明确对 AI 发问，第 4–6 秒的"沉默"是模型正在思考**，既不是"不是对我说的"，也不是"无需回复"。

而 §4.1 定义 `</silence>` = *"用户在跟我说话，但无需回复"*。上游这个填充式 `</silence>` **同时吞掉三类语义**：

1. (a) 已面向 AI、正在处理中的**延迟窗口**（README 的 4→7 秒）；
2. (b) 视频里根本没人说话 / 无信息意图的空档；
3. (c) 有人在说话但**不是对 AI 说**——这才应当是 `not-for-me`。

只有 (b)(c) 勉强算决策，而它们**在数据里不可区分**。所以 `silence` 能"推出来"（占满每个空秒），但推出来的是一个**占位符而非决策标签**。

### 2.4 `not-for-me` ❌ 三重否证

1. **schema 层无字段**：6 字段无 addressee / speaker / directedness（§1.2）。
2. **转换层主动抹平**：`convert_data.py` 只有 `if sec in response_map … else </silence>` 一个二元分支，**无任何路径产出 `</not-for-me>`**；`datasets/` 目录 grep `not-for-me` / `not_for_me` **零命中**。
3. **构造管线未开源**：`doc/research/upstream-delta-2026-09.md` 记录 upstream issue **#13**"数据集构建工具（每秒标签）有计划开源吗"，维护者答*"我们后续会有一项工作专门介绍交互数据的构造 pipeline"*，但截至 2026-09-15 **无后续**。

⚠️ 更糟的是**系统性偏置**：`question` 全是"对 AI 说话"的正样本（数学题、指令），"空档"几乎全属 (a)(b)，**(c) 几乎无样本**。直接用它评 not-for-me，等于在缺少目标类别的集合上考 not-for-me。

---

## 3. `convert_data.py` 转换产出什么

`convert_sample` return 字段：`messages` / `images` / `video_name` / `video_path` / `task_type` / `source`。

- `messages`：**逐秒一对** user/assistant（README §4 原文 *"creates one user/assistant pair for each second"*）
- assistant 侧**只有两种取值**：`</response> {content}` 或 `</silence>`
- **决策只有二元**；`delegate` 仅作 response 文本内联标记；`not-for-me` 从不产生
- `task_type` / `source` 原样透传——**唯一能区分 delegate 的元数据**
- fps 按时长自适应（≥160s→1.0，≥64s→2.0，否则→4.0），逐秒用 `frames_per_sec` 个 `<image>` 拼一条 user message
- 超 `--max_duration`（默认 320s）截断；`time` 超长的样本**直接丢弃**（返回 `None` + warning）

**结论：转换产物不含四态决策标签，只含二态。**

⚠️ 评测集构建**必须复现同一套 fps 与截断规则**，否则与训练分布不一致。

---

## 4. 结论与最小补救成本

**不能直接用作四态评测 ground truth。** 可作为**二态**评测集直接使用。

| 缺口 | 性质 | 严重度 |
|---|---|---|
| `not-for-me` 标签 | 数据层缺失（无字段/无负样本/管线未开源） | **阻断** |
| `silence` 语义纯度 | 延迟窗口 (a) 与非面向 (c) 不可区分 | **高**（silence 指标虚高） |
| `delegate` 显式标签 | 仅文本内联标记，词形契约未文档化 | 中 |
| response 嵌套 list 分组 | 被 `flat_responses` 丢弃 | 低 |

**方案 A —— 收缩到二/三态（成本 ≈ 0）**
直接用 `convert_data.py` 输出评 response / silence；delegate 用 `task_type == "background"` 或 `"</delegation>" in content` 后处理打标。**约 40 行后处理脚本，无需重下数据。** 代价：放弃 not-for-me。

**方案 B —— 修复 silence 纯度（成本 ≈ 30 行纯代码）**
按 `question[].time` 与 `response[].time` 把 `</silence>` 再细分：区间 `[q.time, r.time)` 内 = **latency**（不参与决策评测）；区间外 = **真·空档**。无需重标。这一步把方案 A 的口径从"被污染"提升到"可信"。

**方案 C —— 真拿 not-for-me（成本 = 重新标注，无法自动化）**
需对"真·空档"逐秒做 addressee 标注（人标或 LLM 辅助 + 人工抽检）。上游不给，**不可推导**。缺口秒数 = 总秒数 −（question ∪ response 秒数），4M+ 样本量级下是**数十亿秒**候选池，必须先抽样再标注。**唯一真正昂贵的部分，也是 not-for-me 评测不可绕过的前置条件。**

**建议**：先做 **A + B**（当天可完成，纯代码），把"该不该说话 / 该不该回"评起来；**not-for-me 单独立项（C）**，在此之前**不要**用本数据集声称任何 not-for-me 指标——会得到系统性乐观的假结论。

---

## 5. 证据

- `datasets/README.md` —— `question`: *"when the prompt is issued"*；`response`: *"when the answer or event should occur"*；§4 *"creates one user/assistant pair for each second"*；Q=4/R=7 示例第 4/5/6 秒全为 `</silence>`
- `datasets/convert_data.py` —— `parse_times` docstring `'8' or '5,6,7'`；`# support both flat list ... and nested list`；`flat_responses.extend`；`if sec in response_map` → `</response>` / `else` → `</silence>`；`fps = 1.0 / 2.0 / 4.0`；`MAX_DURATION = 320`；`filtered_count`
- `datasets/` grep `not-for-me` / `not_for_me` / `label` —— **零命中**（仅 `task_type` 三处）
- `doc/specs/addressee-detection.md` §4.1 —— `</silence>` = *"用户在跟我说话，但无需回复"*；`</not-for-me>` = *"非面向 AI（自言自语/对旁人）"*
- `services/webinfer/prompt_constants.py` —— `</not-for-me>` 教学段、`</delegation>` 词形
- `doc/research/upstream-delta-2026-09.md` —— upstream issue **#13**（数据集构建工具，截至 2026-09-15 无后续）
- HF 数据集页 —— 6 列表头；`DatasetGenerationError`（`Couldn't cast array of type string to List({'content','time'})`，佐证 `response` 嵌套 list 与列类型不一致）；`task_type: background` 行的 `</delegation>` 内联标记
- HF API `/api/datasets/jdopensource/JoyAI-VL-Interaction` —— `license: apache-2.0`、`usedStorage: 3487716460`、四个分片 `siblings`
- HF 原始 README —— *"over 4 million time-aligned video-language interaction samples"*
