# ruff: noqa: RUF002, RUF003
"""#159 评测回归门禁 —— 两套测试**共用**的常量（单一来源，不重复字面量）.

★ 为什么这个文件存在
--------------------
`test_eval_gate_contract.py`（契约接线的行为测试）与
`test_drift_gate_missing_result.py`（`present_when_missing` / `parse_as` 的行为测试）
**用途不同、不该合并**，但它们必须认同**同一组事实**：

  * 哪 4 条 check 是 #159 接进来的（`EVAL_CHECK_IDS`）；
  * #159 之前那 11 条既有 check 是谁（`BASELINE_CHECK_IDS`）；
  * 两张记分卡产物落在哪个入库路径（`DIRECTED_ARTIFACT` / `TIMING_ARTIFACT`）。

这些值曾在两个文件里**各写一份**（一处 `= (...)`、一处 `= [...]`）。两份拷贝的
危险不在于"多打几个字"，而在于**它们会各自漂移**：改一处、忘另一处，于是两个
文件对"接线清单是什么"给出不同答案 —— 而两个都对门禁行为下断言。那种"两边都绿、
但说的不是同一件事"的形态，正是本仓反复付费的那一类。

本模块**只放常量**（外加一个纯函数），不含测试、不被 pytest 收集
（文件名不匹配 `test_*.py`），故两套测试各自独立运行、互不牵连。

★ 目录位置：与两套测试同目录，pytest 的 `rootdir` 插入规则会让它可直接 import；
两个测试文件另有显式 `sys.path` 插入，避免依赖 pytest 的隐式行为。
"""

from __future__ import annotations

#: #159 引入的评测 check（定向轴 ×2 + 时序轴 ×2）。顺序与契约一致。
EVAL_CHECK_IDS: tuple[str, ...] = (
    "eval-directed-axis-frozen-reading",
    "eval-directed-axis-structural-guards",
    "eval-timing-axis-frozen-reading",
    "eval-timing-axis-structural-guards",
)

#: #159 之前契约里的 11 条 check —— 一条都不许增删改名（回归保护）。
#: ⚠️ `vlm-n_ctx` 是**连字符**（不是下划线），照抄易错，故一律由测试比对真契约。
BASELINE_CHECK_IDS: tuple[str, ...] = (
    "vlm-kv-quantization",
    "vlm-n_ctx",
    "memory-store-port",
    "webui-gateway-port",
    "main-context-env",
    "webinfer-request-timeout",
    "ruff-pin",
    "webui-vitest",
    "webui-joywiki",
    "webui-wikinspace",
    "webinfer-wiki-recall",
)

#: 两张记分卡的**入库**结果产物（`git ls-files` 认，未被 .gitignore 覆盖）。
DIRECTED_ARTIFACT = "doc/research/data/decision_eval_directed_card.json"
TIMING_ARTIFACT = "doc/research/data/decision_eval_timing_card.json"

#: 轴的短名 → 产物路径（供 `@pytest.mark.parametrize` 用）。
ARTIFACTS_BY_AXIS: dict[str, str] = {
    "directed": DIRECTED_ARTIFACT,
    "timing": TIMING_ARTIFACT,
}


def is_eval_check(check_id: str) -> bool:
    """该 check id 是否是 #159 接进来的评测 check（**纯函数**，无 IO）."""
    return check_id in EVAL_CHECK_IDS
