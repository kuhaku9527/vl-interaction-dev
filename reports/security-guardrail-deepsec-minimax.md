# DeepSec + MiniMax 安全护栏集成说明

> 本文说明 JoyAI-VL-Interaction 项目如何通过 DeepSec Shield 0.2.0 在 Git 提交前建立本地安全护栏，并可选接入 MiniMax-M3 远程语义层（remote L3）以降低误报。**本文档不含任何密钥、令牌或凭据值。**

## 1. 目标

- 提交前自动扫描 staged 文件中的密钥泄露、注入点、SSRF 等风险；命中 critical/high 即阻断提交。
- 默认**零外发、零成本**的本地扫描；远程语义层（MiniMax）仅按需显式开启。

## 2. 组件与版本

- **DeepSec Shield 0.2.0**（三层检测）：L1 正则 + 熵、L2 tree-sitter AST、L3 本地启发式 `audit_semantics`。
- **Remote L3**：`audit_with_llm` → MiniMax-M3，用于消除 L1/L2 的误报，尤其是对裸 `API_KEY=` / `password=` 等动态赋值场景的识别补位。
- **钩子**：真实钩子位于 `.githooks/pre-commit`，由 `.git/hooks/pre-commit` 薄加载器委托执行。

## 3. 关键修复：MiniMax `<think>` 块导致 remote L3 报错

MiniMax-M3 / M2.x 会忽略 `response_format=json_object` 并在返回体前置 `<think>...</think>` 推理文本。DeepSec 原 `_parse_json` 仅剥离 ``` 围栏，导致 `JSONDecodeError: Expecting value: line 1 column 1`。

修复位置：DeepSec 安装目录 `deepsec/core/llm.py` 的 `_parse_json`（**重新 `pip install --upgrade deepsec` 后需重打该补丁**）：

```python
def _parse_json(content: str) -> dict[str, Any]:
    # 仅剔除行首锚定的 <think> 块，避免误伤含 "<think>" 字样的 JSON 字符串
    cleaned = re.sub(r"^\s*<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("The LLM response must be a JSON object")
    return parsed
```

## 4. pre-commit 钩子行为

- 收集 staged 文件（`git diff --cached --name-only --diff-filter=ACM`），物化到 `.cache/deepsec/hook-staging.XXXXXX`（保留相对路径，跳过 >1MB / 二进制文件）。
- 运行 `deepsec shield scan <staging> --layer all --format json`。
- 退出码语义：
  - `2` = 命中 critical/high → 阻断提交（钩子 `exit 1`）；
  - `0` = 干净 → 放行；
  - 其它 = fail-open → 仅告警，不阻断。
- 远程 L3 开启：环境变量 `DEEPSEC_HOOK_REMOTE_L3=1`，钩子自动追加 `--remote-l3` 并导出 `DEEPSEC_LLM_API_KEY`。**默认关闭，保持本地零外发。**

## 5. 成本

- 本地 L1/L2/L3：零成本、零外发。
- MiniMax 远程 L3（官方标准价，≤512k tokens）：输入 ¥4.20/M、输出 ¥16.80/M、命中缓存 ¥0.84/M；>512k 翻倍。
- 注意：DeepSec 内置 `_usage` 使用 OpenAI 默认单价，会高估约 2×，仅作参考。

## 6. 启用 / 禁用

- 钩子已激活（`.git/hooks/pre-commit` 存在并委托 `.githooks/pre-commit`）。
- 临时跳过本次提交扫描：`git commit --no-verify`（不推荐，仅紧急用）。
- 开启远程 L3：在提交环境中 `export DEEPSEC_HOOK_REMOTE_L3=1`，并确保 `MINIMAX_API_KEY`（或 `DEEPSEC_LLM_API_KEY`）已安全注入。

## 7. 已知限制

- 安装的 DeepSec 0.2.0 的 L1 动态赋值规则仅识别带前缀变量名（如 `my_api_key`、`client_secret`）；裸 `API_KEY=` / `password=` 需依赖远程 L3 补位。高签名密钥（AWS `AKIA`、GitHub `ghp_`、OpenAI `sk-`、私钥块、DB URL）无论变量名均必中。
- Remote L3 为外部 SaaS 调用，失败时不阻断提交（fail-open），仅告警。
