# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation. This module's prose is
# Chinese and quotes real test sentences; the same suppression is used by
# services/background-agent and the sibling benchmarks. Established repo
# convention, not a local carve-out.)
"""决策评测的**冻结测试集资产**（决策质量量具 ①）.

Spec: #154 决策质量量具；本模块 = 工单 #155 的交付物。

为什么要有这个模块
------------------
此前决策评测的输入场景是**硬编码在一次性测量脚本里的字面量**，另一个脚本靠
``from benchmark_4state_notforme import TEST_SET`` 复用它。后果：

* 一份没有归属、没有子集标注的口头资产；
* **分母不统一**：历史结果文件里非面向句的分母是 25，而现集合是 26
  （``N01b`` 后补入）—— 跨变体比较在此之上**已经静默失效过**；
* **「开卷考」只是一句要人记得的警告**：生产 prompt 的 few-shot 与测试集
  **逐字重叠 10 句（其中 8 句非面向）**，扣掉记忆效应后泛化 recall 仅 0–5.6%。
  这个事实写在工单评论里，**代码不知道**；
* 四态里的 ``delegate`` **没有 ground-truth 类别** ⇒ 该态永远测不到。

本模块把以上四条都变成**代码里的事实**。

★ 核心设计：子集归属是**算出来的**，不是标出来的
------------------------------------------------
``CASES`` 里**不存** ``subset`` 字段。归属由 :func:`load_cases` 对生产 prompt
做**逐字匹配**当场算得。理由：手写标签会在 prompt 改动后**静默过期** ——
而「开卷考」这个缺陷本身，正是因为有人改了 prompt few-shot、但没人重算标签。

落在 CI 可见处
--------------
本模块置于 ``services/webinfer/``（而非 ``services/scripts/``）：
两个 benchmark **本来就把 webinfer 目录加进了 ``sys.path``**，生产 prompt 常量
也在这里；且 webinfer **在 CI 的 pytest 矩阵内**。
放进 ``services/scripts/`` 会让它的测试**永远不被收集**——正是 #152 的缺陷。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_set.py -q
"""

from __future__ import annotations

from dataclasses import dataclass

# --- 分组常量 ---------------------------------------------------------------

#: 面向 AI（应 response / delegation）—— 定向轴的正类。
GROUP_DIRECTED = "directed"
#: 非面向 AI（应 not-for-me / silence）—— 定向轴的负类。
GROUP_NONDIRECTED = "nondirected"
#: 应委派给后台查证（外部信息才触发）。**独立成组**，不并入 directed。
GROUP_DELEGATE = "delegate"

GROUPS: tuple[str, ...] = (GROUP_DIRECTED, GROUP_NONDIRECTED, GROUP_DELEGATE)

#: 定向轴（该不该开口）只在 directed / nondirected 两组上计算。
#: ★ delegate **刻意排除**在此轴之外 —— 它是「开口之后走哪条路」，不是「该不该开口」。
#: 这样排除也保证两条既有分母（25 / 26）与历史结果**仍然可比**。
DIRECTED_AXIS_GROUPS: tuple[str, ...] = (GROUP_DIRECTED, GROUP_NONDIRECTED)

# --- 子集常量（开卷 / 泛化）-------------------------------------------------

#: few-shot 与测试句**零逐字重叠** —— 真正的泛化分。
SUBSET_GENERALIZATION = "generalization"
#: 测试句**逐字出现在**被测 prompt 里 —— 记忆效应可见，必须单独标注。
SUBSET_OPEN_BOOK = "open-book"

SUBSETS: tuple[str, ...] = (SUBSET_GENERALIZATION, SUBSET_OPEN_BOOK)

# --- 期望动作（比分组更细的 ground truth）-----------------------------------

ACTION_RESPOND = "respond"
ACTION_DELEGATE = "delegate"
ACTION_SILENT = "silent"

# --- 分母与历史差异（显式记录，不藏在注释里）--------------------------------

#: 本资产的权威分母。``total`` 由三组相加得到，见 :func:`group_counts`。
CANONICAL_COUNTS: dict[str, int] = {
    GROUP_DIRECTED: 25,
    GROUP_NONDIRECTED: 26,
    GROUP_DELEGATE: 5,
}

#: 历史结果文件里的分母。``nondirected`` 的 **25 vs 26** 不是笔误：
#: ``N01b``（真机失败句 08-13）是后补入的，历史结果早于它。
#: ⇒ 凡拿历史结果与本资产比较，**必须先统一分母**，否则比较无效。
HISTORICAL_COUNTS: dict[str, int] = {
    GROUP_DIRECTED: 25,
    GROUP_NONDIRECTED: 25,
}

#: 历史差异的人读说明（测试会断言它存在且点明了 25/26）。
HISTORICAL_DENOMINATOR_NOTE = (
    "历史结果文件（doc/research/data/benchmark_4state_notforme_results.json）"
    "的非面向分母是 25，本资产是 26：N01b（'哎呀，这关怎么那么难'，真机失败句 08-13）"
    "是后补入的。跨变体比较前必须先统一分母，否则 25 与 26 的差别会静默污染结论。"
)


@dataclass(frozen=True)
class DecisionCase:
    """一条决策评测场景（子集归属为**算得**，故不存于静态定义）."""

    case_id: str
    text: str
    group: str
    category: str
    note: str
    expected_action: str
    #: ``generalization`` / ``open-book`` —— 由 :func:`load_cases` 计算填入。
    subset: str = SUBSET_GENERALIZATION

    @property
    def is_open_book(self) -> bool:
        """该句是否逐字出现在被测 prompt 里（记忆效应可见）."""
        return self.subset == SUBSET_OPEN_BOOK


# --- 冻结的场景集（唯一真值；顺序即稳定顺序）--------------------------------
# 6-tuple: (id, text, group, category, note, expected_action)
# ★ 不在此处写 subset —— 那是算出来的。
CASES: tuple[tuple[str, str, str, str, str, str], ...] = (
    # ---- 面向 AI（应 response）--------------------------------------------
    ("D01", "玛尔基特怎么打？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D02", "介绍一下你自己", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D03", "喂，帮我查一下明天的天气", GROUP_DIRECTED, "address", "带称呼+指令", ACTION_RESPOND),
    ("D04", "帮我定个闹钟，早上七点", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D05", "现在几点了？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D06", "BT，在吗？", GROUP_DIRECTED, "address", "带称呼", ACTION_RESPOND),
    ("D07", "嘿，你听到了吗？", GROUP_DIRECTED, "address", "带称呼", ACTION_RESPOND),
    ("D08", "艾尔登法环的黄金律法是什么？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D09", "给我讲个笑话", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D10", "把音乐声音调大一点", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D11", "今天有什么安排？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D12", "你叫什么名字？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    (
        "D13",
        "Can you help me with this boss fight?",
        GROUP_DIRECTED,
        "question",
        "提问 EN",
        ACTION_RESPOND,
    ),
    ("D14", "Turn off the lights, please", GROUP_DIRECTED, "command", "指令 EN", ACTION_RESPOND),
    ("D15", "What time is it?", GROUP_DIRECTED, "question", "提问 EN", ACTION_RESPOND),
    (
        "D16",
        "Hey, what's the weather today?",
        GROUP_DIRECTED,
        "address",
        "带称呼 EN",
        ACTION_RESPOND,
    ),
    ("D17", "帮我写个邮件草稿", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D18", "明天会议几点？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D19", "推荐配置是什么？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D20", "暂停一下，先听我说", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D21", "玛尔基特的出血抗性是多少？", GROUP_DIRECTED, "question", "提问", ACTION_RESPOND),
    ("D22", "Show me how to beat Margit", GROUP_DIRECTED, "question", "提问 EN", ACTION_RESPOND),
    ("D23", "记得提醒我买牛奶", GROUP_DIRECTED, "command", "指令", ACTION_RESPOND),
    ("D24", "你觉得这个方案怎么样？", GROUP_DIRECTED, "question", "提问(你→AI)", ACTION_RESPOND),
    ("D25", "Stop playing music", GROUP_DIRECTED, "command", "指令 EN", ACTION_RESPOND),
    # ---- 非面向 AI（应 not-for-me / silence）------------------------------
    ("N01", "这关怎么这么难啊", GROUP_NONDIRECTED, "self-talk", "自言自语", ACTION_SILENT),
    (
        "N01b",
        "哎呀，这关怎么那么难",
        GROUP_NONDIRECTED,
        "self-talk",
        "自言自语(真机失败句 08-13)",
        ACTION_SILENT,
    ),
    ("N02", "唉，好累", GROUP_NONDIRECTED, "exclamation", "感叹", ACTION_SILENT),
    ("N03", "对，我也觉得", GROUP_NONDIRECTED, "reply-other", "回应旁人", ACTION_SILENT),
    ("N04", "你把那个拿过来", GROUP_NONDIRECTED, "talk-other", "与他人对话(歧义)", ACTION_SILENT),
    ("N05", "今天天气真好啊", GROUP_NONDIRECTED, "exclamation", "感叹", ACTION_SILENT),
    ("N06", "完了完了，要迟到了", GROUP_NONDIRECTED, "self-talk", "自言自语", ACTION_SILENT),
    ("N07", "天哪，这都什么事啊", GROUP_NONDIRECTED, "exclamation", "感叹", ACTION_SILENT),
    ("N08", "你先走吧，我一会就来", GROUP_NONDIRECTED, "talk-other", "与他人对话", ACTION_SILENT),
    ("N09", "嗯，好的好的", GROUP_NONDIRECTED, "reply-other", "回应旁人", ACTION_SILENT),
    ("N10", "这个游戏真好玩", GROUP_NONDIRECTED, "exclamation", "感叹", ACTION_SILENT),
    ("N11", "你说得对，就这样吧", GROUP_NONDIRECTED, "reply-other", "回应旁人", ACTION_SILENT),
    ("N12", "啊，吓死我了", GROUP_NONDIRECTED, "exclamation", "感叹", ACTION_SILENT),
    ("N13", "我去拿个快递", GROUP_NONDIRECTED, "self-talk", "告知/自语", ACTION_SILENT),
    (
        "N14",
        "妈妈，我回来了",
        GROUP_NONDIRECTED,
        "talk-other",
        "与他人对话(称呼非AI)",
        ACTION_SILENT,
    ),
    (
        "N15",
        "老公，晚上吃什么？",
        GROUP_NONDIRECTED,
        "talk-other",
        "与他人对话(称呼非AI)",
        ACTION_SILENT,
    ),
    ("N16", "这孩子怎么又哭了", GROUP_NONDIRECTED, "self-talk", "自言自语", ACTION_SILENT),
    ("N17", "加油，你可以的", GROUP_NONDIRECTED, "self-talk", "自语/鼓励旁人", ACTION_SILENT),
    ("N18", "明天又要上班了，烦", GROUP_NONDIRECTED, "self-talk", "自言自语", ACTION_SILENT),
    ("N19", "哎，这日子什么时候是个头", GROUP_NONDIRECTED, "exclamation", "感叹", ACTION_SILENT),
    (
        "N20",
        "Oh no, I forgot my keys",
        GROUP_NONDIRECTED,
        "self-talk",
        "自言自语 EN",
        ACTION_SILENT,
    ),
    ("N21", "Yeah, I think so too", GROUP_NONDIRECTED, "reply-other", "回应旁人 EN", ACTION_SILENT),
    ("N22", "This game is so hard", GROUP_NONDIRECTED, "self-talk", "自言自语 EN", ACTION_SILENT),
    (
        "N23",
        "Honey, did you see my glasses?",
        GROUP_NONDIRECTED,
        "talk-other",
        "与他人对话 EN",
        ACTION_SILENT,
    ),
    ("N24", "Wow, that's amazing!", GROUP_NONDIRECTED, "exclamation", "感叹 EN", ACTION_SILENT),
    ("N25", "我先休息一下", GROUP_NONDIRECTED, "self-talk", "自言自语(告知)", ACTION_SILENT),
    # ---- 应委派（外部信息才触发）------------------------------------------
    # ★ 本组是本工单**新增**的第三组。此前 ``delegate`` 无 ground-truth 类别，
    #   故该态**永远测不到**（矩阵里有列、却没有任何一行能期望它是 delegation）。
    #   判据：委派协议是「**外部查才触发**」——下列均需实时外部信息，本地答不出。
    #   note 里标注依据；这些句子**不在** prompt few-shot 内（由测试断言，非人工保证）。
    (
        "G01",
        "帮我查一下明天北京的天气怎么样",
        GROUP_DELEGATE,
        "external-lookup",
        "委派: 实时天气(外部)",
        ACTION_DELEGATE,
    ),
    (
        "G02",
        "搜一下艾尔登法环最新DLC的玩家评价",
        GROUP_DELEGATE,
        "external-lookup",
        "委派: 需联网检索",
        ACTION_DELEGATE,
    ),
    (
        "G03",
        "现在比特币价格是多少",
        GROUP_DELEGATE,
        "external-lookup",
        "委派: 实时行情(外部)",
        ACTION_DELEGATE,
    ),
    (
        "G04",
        "帮我查查下周三从北京到上海的高铁时刻",
        GROUP_DELEGATE,
        "external-lookup",
        "委派: 需查时刻表",
        ACTION_DELEGATE,
    ),
    (
        "G05",
        "最新的显卡驱动版本号是多少",
        GROUP_DELEGATE,
        "external-lookup",
        "委派: 需查外部版本信息",
        ACTION_DELEGATE,
    ),
)


# --- 生产 prompt 解析（懒加载，避免 import 期耦合）---------------------------


def production_live_prompt(*, include_profile: bool = True) -> str:
    """返回 live 链路**真实发送**的 system prompt.

    ``include_profile=True`` 复现 live adapter 实际发送的内容：
    ``compose_system_prompt(LIVE_SYSTEM_PROMPT_EN, <prompts/bt-7274.txt>, "en")``。
    与 ``benchmark_production_live_prompt.build_production_prompt`` 同源。

    延迟 import：本模块须能被纯数据消费者 import 而不拉起 webinfer 依赖图。
    """
    from prompt_constants import LIVE_SYSTEM_PROMPT_EN
    from system_prompts import compose_system_prompt, load_character_prompts

    if not include_profile:
        return LIVE_SYSTEM_PROMPT_EN
    return compose_system_prompt(LIVE_SYSTEM_PROMPT_EN, load_character_prompts(), "en")


# --- 计算：谁在开卷考 -------------------------------------------------------


def overlapping_ids(prompt: str) -> frozenset[str]:
    """返回**逐字出现在** ``prompt`` 里的 case id 集合.

    这是「开卷考」的**唯一判据**。刻意做成纯函数、接受 prompt 为参数：
    改 prompt 的人不需要记得回来改标签，重算即可。
    """
    return frozenset(cid for cid, text, *_rest in CASES if text and text in prompt)


def subset_for(case_id: str, prompt: str) -> str:
    """该 case 在给定 prompt 下属于哪个子集."""
    return SUBSET_OPEN_BOOK if case_id in overlapping_ids(prompt) else SUBSET_GENERALIZATION


def load_cases(prompt: str | None = None) -> tuple[DecisionCase, ...]:
    """返回全部场景，**子集归属已按 ``prompt`` 算得**.

    Args:
        prompt: 被测 system prompt。``None`` ⇒ 生产 live prompt
            （含 BT-7274 persona）。传入自定义 prompt 即可看出
            「换成另一份 prompt 后，哪些句子仍是开卷」。

    Returns
    -------
        与 :data:`CASES` 同序的 :class:`DecisionCase` 元组。
    """
    effective = production_live_prompt() if prompt is None else prompt
    overlapping = overlapping_ids(effective)
    return tuple(
        DecisionCase(
            case_id=cid,
            text=text,
            group=group,
            category=category,
            note=note,
            expected_action=action,
            subset=SUBSET_OPEN_BOOK if cid in overlapping else SUBSET_GENERALIZATION,
        )
        for cid, text, group, category, note, action in CASES
    )


def subset_counts(cases: tuple[DecisionCase, ...] | list[DecisionCase]) -> dict[str, int]:
    """按子集统计句数（``generalization`` / ``open-book``）."""
    counts = dict.fromkeys(SUBSETS, 0)
    for case in cases:
        counts[case.subset] += 1
    return counts


def group_counts(
    cases: tuple[DecisionCase, ...] | list[DecisionCase] | None = None,
) -> dict[str, int]:
    """按**组**统计句数，并附 ``total``.

    ``cases=None`` ⇒ 对静态 :data:`CASES` 计数（与 prompt 无关，组归属是固定的）。
    """
    rows = CASES if cases is None else [(c.case_id, c.text, c.group, "", "", "") for c in cases]
    counts = dict.fromkeys(GROUPS, 0)
    for _cid, _text, group, *_rest in rows:
        counts[group] += 1
    counts["total"] = sum(counts[group] for group in GROUPS)
    return counts


def canonical_counts_match() -> bool:
    """本资产的组计数是否与 :data:`CANONICAL_COUNTS` 声明一致.

    「分母被钉住」必须是**可校验**的，否则声明的数字会与数据静默分叉。
    """
    actual = group_counts()
    return all(actual[group] == declared for group, declared in CANONICAL_COUNTS.items())


def legacy_test_set() -> list[tuple[str, str, str, str, str]]:
    """既有 benchmark 消费的 5-tuple 视图 ``(id, text, group, category, note)``.

    ★ 保留形状是为了让 `summarize()` 等既有计分代码**零改动**继续工作；
    数据**只有这一份**（:data:`CASES`），不再有第二个副本。
    ``delegate`` 组**包含在内** —— 否则该态又变成静默跳过。
    """
    return [(cid, text, group, category, note) for cid, text, group, category, note, _ in CASES]


def scored_groups() -> tuple[str, ...]:
    """需要进入混淆矩阵的分组（= 全部三组，**不静默跳过任何一组**）.

    既有 ``summarize()`` 只认 ``directed`` / ``nondirected`` 两组，
    新增的 ``delegate`` 会让它在 ``matrix[exp]`` 上 **KeyError**。
    该函数的用途就是让计分侧显式取用完整分组，而不是各自硬编码一个列表。
    """
    return GROUPS


def subset_by_id(prompt: str | None = None) -> dict[str, str]:
    """``case_id -> 子集`` 映射（供计分侧按子集拆分，无需自行重算）."""
    return {case.case_id: case.subset for case in load_cases(prompt)}


def expected_by_id() -> dict[str, str]:
    """``case_id -> 分组`` 映射（计分侧的 ground truth）."""
    return {cid: group for cid, _t, group, *_rest in CASES}


def describe(prompt: str | None = None) -> str:
    """Human-readable report of the asset under one prompt: counts + the
    open-book list.

    This is the "one command" view: it answers both "how many in each group /
    subset" and "exactly which sentences are open-book" without anyone having
    to open a REPL or read a test file.

    Run:  python -m decision_eval_set
    """
    effective = production_live_prompt() if prompt is None else prompt
    cases = load_cases(effective)
    groups = group_counts(cases)
    subsets = subset_counts(cases)
    lines = [
        "=== decision eval set (frozen asset) ===",
        f"total={groups['total']}  " + "  ".join(f"{g}={groups[g]}" for g in GROUPS),
        "subsets: " + "  ".join(f"{s}={subsets[s]}" for s in SUBSETS),
        "",
        f"open-book (逐字出现在被测 prompt 里, n={subsets[SUBSET_OPEN_BOOK]}):",
    ]
    for case in cases:
        if case.is_open_book:
            lines.append(f"  {case.case_id:5s} [{case.group:11s}] {case.text}")
    lines.append("")
    lines.append("denominator note:")
    lines.append(f"  {HISTORICAL_DENOMINATOR_NOTE}")
    return "\n".join(lines)


__all__ = [
    "ACTION_DELEGATE",
    "ACTION_RESPOND",
    "ACTION_SILENT",
    "CANONICAL_COUNTS",
    "CASES",
    "DIRECTED_AXIS_GROUPS",
    "GROUPS",
    "GROUP_DELEGATE",
    "GROUP_DIRECTED",
    "GROUP_NONDIRECTED",
    "HISTORICAL_COUNTS",
    "HISTORICAL_DENOMINATOR_NOTE",
    "SUBSETS",
    "SUBSET_GENERALIZATION",
    "SUBSET_OPEN_BOOK",
    "DecisionCase",
    "canonical_counts_match",
    "describe",
    "expected_by_id",
    "group_counts",
    "legacy_test_set",
    "load_cases",
    "overlapping_ids",
    "production_live_prompt",
    "scored_groups",
    "subset_by_id",
    "subset_counts",
    "subset_for",
]


if __name__ == "__main__":
    print(describe())
