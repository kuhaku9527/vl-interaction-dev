# ruff: noqa: RUF001, RUF002, RUF003
# (RUF001/002/003 = ambiguous fullwidth punctuation; this module's prose is
# Chinese. Same established repo convention as decision_eval_timing_criteria.py.)
"""时序轴判据的**负控**与可证伪性自检（工单 #158，父 spec #154 §七）.

为什么单独一个模块
------------------
「判据是什么」与「判据是否可证伪」是**两件不同的变化原因**
（``code-review-checklist.md`` 的 Divergent Change 条）：

* 判据变了（加一条、改阈值、改口径）→ 只该动
  :mod:`.decision_eval_timing_criteria`；
* 负控变了（补一个故意做错的输入）→ 只该动本模块。

合在一处还让 criteria 越过 ``coding-standards.md`` §7 的 **1000 行「problem」线**
（实测 1004 行）—— 一次实测触发的拆分，不是预防性重构。

★ 父 spec §七的原文要求是「每条新判据必须配套一份故意做错的输入，并证明它会判红」。
**没有负控的判据可能是恒真的**，而恒真的判据与恒红的判据一样没有分辨力，
只是换了个方向骗人。:func:`self_check` 因此强制两件事：

1. 每条判据至少被一个负控覆盖（否则报错）；
2. 每个负控都必须让它声明的那几条**真的**不能判绿。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing_criteria.py -q
Self-check: python -m decision_eval_timing_negatives --self-check
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass

from decision_eval_timing import (
    FIELD_STILL_SPEAKING,
    LATENCY_SOURCE_FRAME_TS,
    spoke,
    timing_block,
)
from decision_eval_timing_criteria import (
    TIMING_CRITERIA,
    VERDICT_FAIL,
    VERDICT_PASS,
    criteria_verdicts,
    statements_match_bounds,
    statements_mention_axis_separation,
)
from decision_eval_timing_synthetic import (
    synthetic_healthy_rows,
    synthetic_silent_rows,
    synthetic_unstamped_rows,
)

# --- 负控（故意做错的输入） --------------------------------------------------

#: 负控的作用对象是**整份时序块**.
BlockMutator = Callable[[dict], dict]


def _never_speaks(block: dict) -> dict:
    """把块换成「永远沉默」的桩的读数.

    ★ 声明它必须让哪条判据不能判绿：**一条也不该判绿**地证明「沉默很乖」是不可能的。
    在时序轴上，「永远沉默」的产物是：无开口样本 ⇒ 三个量全部不可测。
    故它不是「表现完美」，而是**没东西可测** —— 声明 ``must_fail`` 为空
    是**错的**，正确声明是 ``must_not_pass`` 全部三条计量判据。
    """
    return timing_block(synthetic_silent_rows())


def _unstamped(block: dict) -> dict:
    """★ **删掉轮次打点** —— 本票 ★ 负控的字面形态.

    ``ts`` 一律保留（这正是关键：若实现用 ts 差值兜底，这里就会重新出数）。
    """
    return timing_block(synthetic_unstamped_rows())


def _frame_ts_source(block: dict) -> dict:
    """★ 耗时**真的**由帧的采集端时间戳算出 —— 本票点名禁止的那一种.

    ★★ 这个变异体是**对抗性复核逼出来的重写**。初版只是把 ``latency_source``
    这个**字符串**改掉，于是它测的是「声明被改了会不会被发现」——
    而真正要挡的是「耗时**其实是**用墙钟算的」。两者天差地别：
    复核把 ``latency_ms`` 真的换成 ts 差值算出来的数、**声明一个字都没改**，
    卡片照样判绿。故现在两个变异体都**改数据**：

    * ``frame-ts-latency-source``：逐行 ``latency_source`` 写成帧钟，
      且耗时**真的**取「帧间隔」（1 Hz ⇒ 1000 ms 的整数倍）；
    * ``event-ts-diff-latency-source``：逐行 ``latency_source`` **保持链上声明
      （甚至什么都不改）**，但耗时**真的**改成 ts 差值 —— 这一条专打
      「只查声明、不查数据」的实现。
    """
    rows = [
        {
            **row,
            "latency_source": LATENCY_SOURCE_FRAME_TS,
            # 帧是 1 Hz：由帧钟推出的耗时必然是 1000 ms 的整数倍。
            "latency_ms": 1000 * (1 + (index % 3)),
        }
        for index, row in enumerate(synthetic_healthy_rows())
        if spoke(row)
    ] + [row for row in synthetic_healthy_rows() if not spoke(row)]
    return timing_block(rows)


def _event_ts_diff_source(block: dict) -> dict:
    """★★ 耗时**真的**由事件 ``ts`` 差值算出，而**声明保持链上打点不动**.

    ★ 这是本票 ★ 冲突的核心形态，也是对抗性复核实测通过的那个漏洞：
    一个「发现 latency_ms 常缺、于是顺手改用 ts 差值」的实现，只要不主动
    声明，读侧就无从分辨。本变异体**特意不改任何声明字段**，
    故它只能被「耗时恰好等于某段 ts 差值」这条**代数检验**抓住。
    """
    from datetime import datetime

    base_rows = synthetic_healthy_rows()
    first_ts: dict[str, datetime] = {}
    for row in base_rows:
        stamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        first_ts.setdefault(str(row["session_id"]), stamp)

    rows: list[dict] = []
    for row in base_rows:
        if not spoke(row):
            rows.append(row)
            continue
        stamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        delta_ms = round((stamp - first_ts[str(row["session_id"])]).total_seconds() * 1000.0)
        rows.append({**row, "latency_ms": max(delta_ms, 1)})
    return timing_block(rows)


def _missing_origin_field(block: dict) -> dict:
    """★ 耗时在，但**出处字段被抹掉** ⇒ 不可归因，必须判红.

    ★ 它对应的真实情形：事件流来自一个**没有写** ``latency_source`` 的版本。
    那时「这个数是从哪来的」没有证据 —— 而「没写」与「写了链上打点」
    必须可区分。判绿就等于替写入侧圆了一句它没说过的话。
    """
    rows = [
        {k: v for k, v in row.items() if k != "latency_source"} for row in synthetic_healthy_rows()
    ]
    return timing_block(rows)


def _frame_lattice(block: dict) -> dict:
    """★★ 绕过①：耗时**真的**取帧钟点阵（10 fps ⇒ 全是 100 ms 的整数倍），
    而逐行 ``latency_source`` 一字不改.

    ★ 这是对抗复核查出的绕过，**主控已独立复现**：初版只比对「恰好等于某个 ts
    差值」，而点阵值**没有一个**等于 ts 差值 ⇒ 判绿；且 200–500 ms 落在 onset
    阈值带内，**阈值也抓不到** ⇒ 卡片总判 pass。

    ★ 为什么必须把 ``latency_source`` 保持成链上常量：`_frame_ts_source()` 看似
    覆盖了帧钟，但它**同时**把逐行来源写成 ``frame_capture_ts_ms``，于是被来源
    名单抓住 —— 那测的是「声明被改了会不会被发现」，不是「耗时是否真的来自帧钟」。
    本例专测后者。
    """
    lattice = [200, 300, 400, 500]
    out: list[dict] = []
    index = 0
    for row in synthetic_healthy_rows():
        if not spoke(row):
            out.append(row)
            continue
        out.append({**row, "latency_ms": lattice[index % len(lattice)]})
        index += 1
    return timing_block(out)


def _ts_diff_with_offset(block: dict) -> dict:
    """★★ 绕过②：耗时 = ts 差值 **+ 常数偏移**，逐行声明不改.

    ★ 复核实测：初版的「恰好相等」检验对 offset 0..1999 **1999/2000 全盲**
    （含 offset=1）—— 它当初之所以"有效"，靠的是 offset 恰好为 0 这个巧合。

    ★ 修法不是再堵 offset=1 这个特例，而是把代数族一般化：**常数偏移在差分里
    会自己消掉**（``latency_i − latency_j == ts_i − ts_j``），故仿射检验一次性
    覆盖任意常数偏移。
    """
    return timing_block(_ts_affine_rows(offset=1))


def _ts_diff_large_offset(block: dict) -> dict:
    """★★ 绕过②的**大偏移**变体：offset=997（刻意取一个"不像 0"的数）.

    ★ 单列一条是为了证明修复**不是**只针对 offset=1 打的补丁：
    仿射检验对偏移大小**完全不敏感**（它在差分里消掉了）。
    """
    return timing_block(_ts_affine_rows(offset=997))


def _non_iso_ts(block: dict) -> dict:
    """★★ 绕过③：``ts`` 换成 **epoch 毫秒串**（非 ISO），逐行声明不改.

    ★ 复核指出：两个解析器都返回 ``None`` ⇒ 差值集合**为空** ⇒ 代数检验
    **空转恒真通过**，而 ``session_seconds`` 静默退化为 0。
    修法：解析不了的 ts **直接判红**（fail-closed），不再空转。
    """
    rows = [{**row, "ts": "1758523200000"} for row in synthetic_healthy_rows()]
    return timing_block(rows)


def _ts_affine_rows(*, offset: int) -> list[dict]:
    """构造「耗时 = 到会话首行的 ts 差值 + offset」的行（声明不动）."""
    from datetime import datetime

    base_rows = synthetic_healthy_rows()
    first: dict[str, datetime] = {}
    for row in base_rows:
        first.setdefault(
            str(row["session_id"]),
            datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00")),
        )
    rows: list[dict] = []
    for row in base_rows:
        if not spoke(row):
            rows.append(row)
            continue
        stamp = datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00"))
        delta = round((stamp - first[str(row["session_id"])]).total_seconds() * 1000) + offset
        rows.append({**row, "latency_ms": max(delta, 1)})
    return rows


def _failed_rounds(block: dict) -> dict:
    """★ 把全部**开口**轮次标成失效输出（``empty_output``）—— 必须判红.

    ★ 对抗复核 D2：失效输出原先被静默算进 ``n_quiet``，于是「没测到」在安静轮次里
    消失，而 quiet 正是误触发率的分母侧。

    ★ 用 ``output_state == empty_output``（读侧从 ``raw_text_len`` 算出、真机可得）
    而**不是** ``ok=False`` —— 后者是 benchmark 行的概念，真机事件流里根本没有这个
    形态（写入侧只在达成决策时才写事件），拿它判会是一条**死代码**。实测确认过。
    """
    rows = [{**row, "output_state": "empty_output"} for row in synthetic_healthy_rows()]
    return timing_block(rows)


def _add_combined_accuracy(block: dict) -> dict:
    """往块里塞一个 ``accuracy`` 字段（模拟「顺手合成一下」）."""
    return {**block, "accuracy": 0.83}


def _drop_premature_labels(block: dict) -> dict:
    """删掉全部 premature 标注 —— 必须判「无法测量」，**不得**判绿."""
    rows = [
        {k: v for k, v in row.items() if k != FIELD_STILL_SPEAKING}
        for row in synthetic_healthy_rows()
    ]
    return timing_block(rows)


def _tiny_sample(block: dict) -> dict:
    """只留 2 次开口 —— 分位数在此样本量下不可作结论."""
    return timing_block(synthetic_healthy_rows()[:3])


def _no_timebase(block: dict) -> dict:
    """把所有 ``ts`` 抹成空串 —— 速率失去时间基准."""
    rows = [{**row, "ts": ""} for row in synthetic_healthy_rows()]
    return timing_block(rows)


def _no_truth(block: dict) -> dict:
    """★ 抹掉全部真值（``expected=None``）—— **真机事件流的真实形状**.

    ★ D1（真机核验查出）的负控：真机 09-21 的 204 轮里没有任何「该不该开口」
    标注（``n_expected_speak = 0``），而初版仍报 ``0.0 次/秒`` 并让
    ``T_SPURIOUS_TIMEBASE`` 判 pass —— 「0 次乱插话」与「不知道有几次」同形。
    """
    rows = [{**row, "expected": None} for row in synthetic_healthy_rows()]
    return timing_block(rows)


@dataclass(frozen=True)
class Mutation:
    """一份**故意做错**的输入，及其「必须让哪些判据不能判绿」的声明.

    Attributes
    ----------
        must_fail: 必须判 :data:`VERDICT_FAIL` 的判据 id。
        must_not_pass: 必须**不判 PASS**（FAIL 或「无法测量」皆可）的判据 id。
            用于「分母退化 / 未接线」这一类：正确行为是判「无法测量」，
            但它同样**绝不能判绿**。
        is_identity: 恒等对照。它**不得**让任何判据变红，作用是把
            「基线本身就不干净」从其它负控里分离出来。恰好一个。
    """

    mutation_id: str
    description: str
    apply: BlockMutator
    must_fail: tuple[str, ...] = ()
    must_not_pass: tuple[str, ...] = ()
    is_identity: bool = False

    @property
    def covers(self) -> tuple[str, ...]:
        """本负控触及的全部判据 id（用于覆盖完整性检查）."""
        return tuple(dict.fromkeys((*self.must_fail, *self.must_not_pass)))


def _identity(block: dict) -> dict:
    """恒等变换 —— 它必须让**任何判定都不变**."""
    return block


#: ★ 负控集合。父 spec §七要求「每条新判据必须配套一份故意做错的输入，
#: 并证明它会判红」；:func:`self_check` 会强制「每条判据至少被一个负控覆盖」，
#: 且拒绝 ``covers`` 为空的负控。
#:
#: ⚠️ **一条实测结论，写在这里以免后人重犯**：
#: ``always-silent`` 在时序轴上**没有** ``must_fail`` —— 它的产物是「没有开口样本」，
#: 故三个量全部「无法测量」。声明它必须让某条判据判红会让自检**假失败**，
#: 进而诱使人去放宽判据；那比漏检更危险。真正该断言的是**它一条都没判绿**。
MUTATIONS: tuple[Mutation, ...] = (
    Mutation(
        mutation_id="identity",
        description="恒等变换（什么都不改）—— 证明「判红」不是来自基线输入本身。",
        apply=_identity,
        is_identity=True,
    ),
    Mutation(
        mutation_id="dropped-round-stamp",
        description=(
            "★ 本票 ★ 负控的字面形态：**删掉轮次打点**（latency_ms 全为 None），"
            "而 ts 一律保留。期望：T_ONSET_MEASURED 判**红**（不是「无法测量」），"
            "且 onset 不得退回用 ts 差值重新出数。"
        ),
        apply=_unstamped,
        must_fail=("T_ONSET_MEASURED",),
        must_not_pass=("T_ONSET_MEDIAN", "T_ONSET_P90", "T_SAMPLE_FLOOR"),
    ),
    Mutation(
        mutation_id="frame-ts-latency-source",
        description=(
            "★ 耗时**真的**由**帧的采集端时间戳**算出（逐行出处标注帧钟，"
            "耗时取 1 Hz 的整数倍）。数字会变得更好看，但归因是错的 —— 必须判红。"
        ),
        apply=_frame_ts_source,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="event-ts-diff-latency-source",
        description=(
            "★★ 耗时**真的**由事件 ts 差值算出，而**声明一个字都不改**"
            "（逐行仍写着链上打点）。这一条专打「只查声明、不查数据」的实现 —— "
            "对抗性复核实测正是用这种形态让初版判绿的。"
        ),
        apply=_event_ts_diff_source,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="missing-origin-field",
        description=(
            "★ 耗时在，但**逐行出处字段被抹掉**（模拟事件来自一个没写该字段的版本）"
            " —— 「这个数从哪来」没有证据 ⇒ 必须判红，不得替写入侧圆一句它没说过的话。"
        ),
        apply=_missing_origin_field,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="frame-lattice-latency",
        description=(
            "★★ 绕过①：耗时**真的**取帧钟点阵（10 fps ⇒ 100 ms 的整数倍），"
            "而逐行 latency_source **一字不改**。初版因「没有一个值等于 ts 差值」"
            "而判绿，且值落在阈值带内 ⇒ 卡片总判 pass（对抗复核发现，主控复现）。"
        ),
        apply=_frame_lattice,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="ts-affine-offset-1",
        description=(
            "★★ 绕过②：耗时 = ts 差值 **+1 ms**，逐行声明不改。"
            "初版「恰好相等」检验对 offset 0..1999 中 1999/2000 全盲。"
        ),
        apply=_ts_diff_with_offset,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="ts-affine-offset-997",
        description=(
            "★★ 绕过②的大偏移变体：offset=997 —— 证明修复不是只堵 offset=1 的补丁"
            "（仿射检验对偏移大小不敏感：常数在差分里消掉）。"
        ),
        apply=_ts_diff_large_offset,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="non-iso-ts",
        description=(
            "★★ 绕过③：ts 换成 epoch 毫秒串（非 ISO）⇒ 初版差值集合为空、"
            "代数检验**空转恒真**通过。必须判红（fail-closed）。"
        ),
        apply=_non_iso_ts,
        must_fail=("T_LATENCY_SOURCE",),
    ),
    Mutation(
        mutation_id="failed-rounds",
        description=(
            "★ 全部轮次标成推理失败（ok=False）⇒ 必须判红："
            "「没测到」不是「判定沉默」，混入会把失效输出洗成正确的安静。"
        ),
        apply=_failed_rounds,
        must_fail=("T_SPURIOUS_TIMEBASE",),
    ),
    Mutation(
        mutation_id="combined-accuracy",
        description=(
            "★ 往块里塞一个 accuracy 字段（「顺手合成两轴」）—— 父 spec §三明确否决，必须判红。"
        ),
        apply=_add_combined_accuracy,
        must_fail=("T_NO_COMBINED_ACCURACY",),
    ),
    Mutation(
        mutation_id="dropped-premature-labels",
        description=(
            "删掉全部 premature 标注（模拟**未接线的真机链路**）—— "
            "必须判「无法测量」，**不得**判绿。"
        ),
        apply=_drop_premature_labels,
        must_not_pass=("T_PREMATURE_MEASURED",),
    ),
    Mutation(
        mutation_id="tiny-sample",
        description="只留 2 次开口 —— onset 分位数不可作结论。",
        apply=_tiny_sample,
        # ★★ D2（真机核验查出）：初版这里**只**声明了 T_SAMPLE_FLOOR，于是那两条
        #   带阈值的 onset 判据在 2 个样本上照报 pass 而没人管 —— 负控自检还报 killed。
        #   实测真机卡片并排打印「T_SAMPLE_FLOOR=无法测量（样本 1<10）」与
        #   「T_ONSET_MEDIAN=pass（16.0<=898）」。⇒ 三条一起声明。
        must_not_pass=("T_SAMPLE_FLOOR", "T_ONSET_MEDIAN", "T_ONSET_P90"),
    ),
    Mutation(
        mutation_id="no-truth",
        description=(
            "★ 抹掉**全部真值**（``expected=None``）—— 这正是真机事件流的形状。"
            "「误触发」是真值相关量：没有真值就没有「误」可言，故每秒误触发必须判"
            "「无法测量」，**不得**给 0.0。"
        ),
        apply=_no_truth,
        must_not_pass=("T_SPURIOUS_TIMEBASE",),
    ),
    Mutation(
        mutation_id="no-timebase",
        description="抹掉全部 ts —— 每秒误触发失去时间基准，必须不可判绿。",
        apply=_no_timebase,
        must_not_pass=("T_SPURIOUS_TIMEBASE",),
    ),
    Mutation(
        mutation_id="always-silent",
        description=(
            "★ 「永远沉默」的桩。在时序轴上它的产物是**没有开口样本** ⇒ 三个量全部"
            "「无法测量」。★ 它**没有** must_fail（见上方注释）：声明它必须判红会让"
            "自检假失败；要断言的是它**一条都没判绿**。"
        ),
        apply=_never_speaks,
        must_not_pass=("T_ONSET_MEDIAN", "T_ONSET_P90", "T_PREMATURE_MEASURED"),
    ),
)


def _verdict_map(block: dict) -> dict[str, str]:
    """一次算完全部时序判据的判定."""
    return {item["criterion_id"]: item["verdict"] for item in criteria_verdicts(block)}


def self_check() -> int:
    """★ 可证伪性自检：证明每条时序判据**真的会判红**（而不是恒真）.

    做五件事，并把两侧读数都打出来：

    1. **文本与阈值的分叉守卫**（:func:`statements_match_bounds`）；
    2. **无负控的判据 ⇒ 失败**（禁止恒真判据、禁止装饰性负控）；
    3. **恒等变换必须让任何判定都不变**；
    4. 每个负控必须让它 ``must_fail`` 里的判据**判 FAIL**、
       ``must_not_pass`` 里的**不判 PASS**；
    5. ★ **被负控覆盖的判据集合必须等于全部判据集合** —— 守「新增判据即必须配负控」。

    ★ 另外单独断言本票 ★ 负控的**方向**：``dropped-round-stamp`` 必须让
    ``T_ONSET_MEASURED`` 判 **FAIL**，而不是「无法测量」。
    判「无法测量」会让「打点被删掉」看起来像「这项不适用」—— 那正是 fail-open。
    """
    baseline = timing_block(synthetic_healthy_rows())
    baseline_verdicts = _verdict_map(baseline)
    problems: list[str] = []
    problems.extend(statements_match_bounds())
    problems.extend(statements_mention_axis_separation())

    all_ids = {c.criterion_id for c in TIMING_CRITERIA}
    covered = {cid for mutation in MUTATIONS for cid in mutation.covers}
    for criterion_id in sorted(all_ids - covered):
        problems.append(f"{criterion_id} 没有任何负控声明它必须判红 / 不得判绿 ⇒ 它可能是恒真的")

    for mutation in MUTATIONS:
        if not mutation.covers and not mutation.is_identity:
            problems.append(
                f"负控 {mutation.mutation_id} 既没有 must_fail 也没有 must_not_pass，"
                "又不是显式标注的恒等对照 ⇒ 装饰性负控"
            )
    identities = [m for m in MUTATIONS if m.is_identity]
    if len(identities) != 1:
        problems.append(
            f"恒等对照负控有 {len(identities)} 个（应为恰好 1 个）—— "
            "没有它，「负控会让判据变红」这句话就没有对照"
        )

    identity_verdicts = _verdict_map(_identity(baseline))
    if identity_verdicts != baseline_verdicts:
        problems.append("恒等变换改变了判定 ⇒ 后面的「变红」可能来自基线输入本身")

    print("=== 时序轴判据的负控自检 (#158) ===")
    print(f"  基线（合成输入）: {_fmt_verdicts(baseline_verdicts)}")
    for mutation in MUTATIONS:
        mutated = mutation.apply(baseline)
        verdicts = _verdict_map(mutated)
        fired = sorted(k for k, v in verdicts.items() if v == VERDICT_FAIL)
        not_green = sorted(k for k, v in verdicts.items() if v != VERDICT_PASS)
        if mutation.is_identity:
            print(f"  {mutation.mutation_id:28s} 判红={fired}（应为空）")
            if fired:
                problems.append(
                    f"恒等对照负控让 {fired} 判红了 ⇒ 基线输入本身就不干净，"
                    "其它负控的「变红」证明不了任何事"
                )
            continue
        print(f"  {mutation.mutation_id:28s} 判红={fired} 非绿={not_green}")
        missing = [cid for cid in mutation.must_fail if verdicts.get(cid) != VERDICT_FAIL]
        if missing:
            problems.append(f"负控 {mutation.mutation_id} 没能让 {missing} 判红")
        still_green = [cid for cid in mutation.must_not_pass if verdicts.get(cid) == VERDICT_PASS]
        if still_green:
            problems.append(
                f"负控 {mutation.mutation_id} 让 {still_green} **判绿了** —— "
                "分母退化 / 未接线这类情形不得判绿"
            )

    # ★ 本票 ★ 负控的方向：必须是 FAIL，不能是 UNMEASURABLE。
    drop_verdict = _verdict_map(_unstamped(baseline)).get("T_ONSET_MEASURED")
    if drop_verdict != VERDICT_FAIL:
        problems.append(
            f"★ 删掉轮次打点后 T_ONSET_MEASURED 的实际判定是 {drop_verdict!r}，"
            "而本票要求它**判红**。判「无法测量」会把接线缺陷说成「这项不适用」"
            "（fail-open）。"
        )

    if problems:
        print("  verdict: FAIL")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print(
        f"  verdict: PASS（{len(all_ids)} 条判据各有负控；{len(MUTATIONS)} 个故意做错的输入"
        "全部按声明判红或「无法测量」；删掉轮次打点确实判**红**）"
    )
    return 0


def _fmt_verdicts(verdicts: dict[str, str]) -> str:
    """把判定映射打成一行（稳定顺序，便于跨运行 diff）."""
    return "  ".join(f"{k}={verdicts[k]}" for k in sorted(verdicts))


def main(argv: list[str] | None = None) -> int:
    """CLI：跑负控自检（默认）或打印负控清单."""
    parser = argparse.ArgumentParser(description="时序轴判据的负控自检（工单 #158）")
    parser.add_argument("--self-check", action="store_true", help="跑负控自检（默认）")
    parser.add_argument("--list", action="store_true", help="打印负控清单与其声明的期望")
    args = parser.parse_args(argv)

    if args.list:
        for mutation in MUTATIONS:
            kind = "恒等对照" if mutation.is_identity else "负控"
            print(f"  [{kind}] {mutation.mutation_id}")
            print(
                f"      必须判红={list(mutation.must_fail)} 不得判绿={list(mutation.must_not_pass)}"
            )
            print(f"      {mutation.description}")
        return 0

    return self_check()


if __name__ == "__main__":  # pragma: no cover - CLI glue
    raise SystemExit(main())


__all__ = [
    "MUTATIONS",
    "BlockMutator",
    "Mutation",
    "main",
    "self_check",
]
