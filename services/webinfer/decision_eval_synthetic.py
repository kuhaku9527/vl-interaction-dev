# ruff: noqa: RUF001, RUF002, RUF003
"""定向轴记分卡的**离线合成输入**（工单 #157 的负控自检夹具）.

为什么单独一个模块
------------------
这些夹具**不需要模型、也不需要真机产物** —— 它们是「判据是否可证伪」这件事
在任何机器上都能被复核的前提。而引用它们的地方有三个（卡片自检、判据自检、
以及两边的行为测试），放在任何一处都会让另外两处反向依赖。

单独成模块还顺手解决两件事：

* 三处引用**同一份**夹具 ⇒ 不会出现「自检用的输入」与「负控用的输入」悄悄分家
  （分家之后，负控证明的就不是判据，而是两份夹具的差异）；
* 每个模块回到 ``coding-standards.md`` §7 的 ~600 行以内。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_card.py -q
"""

from __future__ import annotations

from decision_eval_set import GROUP_DIRECTED, GROUP_NONDIRECTED

# --- 离线合成输入（负控自检用；不需要模型、不需要产物）----------------------

#: 合成输入里的「该开口」句。
SYNTHETIC_DIRECTED: tuple[tuple[str, str], ...] = (
    ("D01", "response"),
    ("D02", "response"),
    ("D03", "delegation"),
    ("D04", "response"),
    ("D05", "response"),
    ("D06", "silence"),
)

#: 合成输入里的「不该开口」句。
SYNTHETIC_NONDIRECTED: tuple[tuple[str, str], ...] = (
    ("N01", "silence"),
    ("N02", "not-for-me"),
    ("N03", "silence"),
    ("N04", "response"),
    ("N05", "silence"),
)

#: 决策 → token 级证据（首位 token）。``silence`` 的首位是 special token 151669；
#: ``not-for-me`` 的首位是 151670 但后面跟普通 token —— 这正是「不能用首位 token
#: 判定说话与否」的实测依据。
TOKEN_FOR_DECISION: dict[str, list[int]] = {
    "silence": [151669, 151645],
    "response": [151670, 200],
    "delegation": [151670, 300],
    "not-for-me": [151670, 222, 99507],
}


def synthetic_findings() -> dict:
    """一份**离线**跑分行，含已知的两类错误（供自检与负控使用）.

    刻意做得「有好有坏」：3 例非面向被正确判成不开口、1 例被误响应、
    5 例面向里 1 例被吞。于是四项比例都不在退化点上，判据有东西可判。
    """
    rows: list[dict] = []
    for case_id, decision in SYNTHETIC_DIRECTED:
        rows.append(
            {
                "id": case_id,
                "expected": GROUP_DIRECTED,
                "decision": decision,
                "ok": True,
                "emitted_token_ids": list(TOKEN_FOR_DECISION[decision]),
            }
        )
    for case_id, decision in SYNTHETIC_NONDIRECTED:
        rows.append(
            {
                "id": case_id,
                "expected": GROUP_NONDIRECTED,
                "decision": decision,
                "ok": True,
                "emitted_token_ids": list(TOKEN_FOR_DECISION[decision]),
            }
        )
    return {"rows": rows}


def synthetic_prompt() -> str:
    """合成输入对应的 prompt —— **零逐字重叠**，故全部落在泛化子集.

    刻意不重叠：自检要走的正是「扣掉记忆效应之后还剩多少」那条路径。
    """
    return "（离线合成输入：与任何 case 文本都不逐字重叠）"
