# ruff: noqa: RUF001, RUF002, RUF003
"""时序轴的**离线合成输入**（工单 #158 的自检与负控夹具）.

为什么单独一个模块
------------------
这些夹具**不需要模型、也不需要真机** —— 它们是「时序轴判据是否可证伪」
在任何机器上都能被复核的前提。引用它们的至少有三处（轴自检、判据自检、
行为测试），放在任何一处都会让另外两处反向依赖它。

三份夹具各自对应一条**必须双向成立**的性质
------------------------------------------
* :func:`synthetic_healthy_rows` —— 三个量都算得出来（证明判据不是恒红）；
* :func:`synthetic_unstamped_rows` —— **删掉轮次打点**，而 ``ts`` 仍在场。
  ★ 它专门证明实现**没有**退回用墙钟差值补数：若哪天有人「顺手」用 ``ts``
  兜底，onset 会重新出数，自检当场判红。
* :func:`synthetic_silent_rows` —— 永远沉默的桩。它的 premature 分母为 0，
  ★ 必须得到 ``None`` 而不是一个「完美」的 0.0。

★ 真值（``expected``）在这里是**逐行显式写死**的，不用序号推算
----------------------------------------------------------------
推算式构造（「第 7 行该沉默」）在增删一行时会**静默错位**，而错位的真值会让
判据在一个错的世界里「通过」。显式表格是唯一不会悄悄失效的写法。

Run tests: cd services/webinfer && python -m pytest tests/test_decision_eval_timing.py -q
"""

from __future__ import annotations

from decision_eval_timing import EXPECTED_QUIET, EXPECTED_SPEAK, FIELD_STILL_SPEAKING, spoke
from decision_events import ROUND_KIND_PROACTIVE, ROUND_KIND_USER

#: 合成输入的会话标识（一条会话，跨度 400 秒）。
SYNTHETIC_SESSION = "sess-timing-synthetic"

#: (秒, 决策, 真值, 轮次打点 ms, 决策时用户是否仍在说话)。
#:
#: ★ **不变式一：每一轮「开口」（response/delegation）都必须有显式的
#: True/False 标注**，不开口的轮次给 ``None``（抢话只在开口时可能发生）。
#: 缺一个标注就会让 premature 的分母不完整，而那会把本夹具从
#: 「三个量都算得出来」悄悄降级成「premature 不可测」，自检就失去了它在证的东西
#: （实测踩到过：一个 delegation 轮漏标 ⇒ premature rate 变 None）。
#:
#: ★ **不变式二：每一轮开口都必须带轮次打点（``latency_ms``）**。
#: 这份夹具是**干净的基线**：自检里的恒等对照负控要求基线**一条判据都不判红**。
#: 一个「缺打点」的基线会让 T_ONSET_MEASURED 在基线上就判红，于是恒等对照变脏、
#: 后面所有负控的「变红」都证明不了任何事（实测踩到过）。
#: 「缺打点」这条退化**单独**由 ``synthetic_unstamped_rows()`` 承载。
#: 两条不变式都由 ``_validated`` 与 ``tests/test_decision_eval_timing.py`` 断言。
#:
#: ★ **不变式三：不等距**。误触发落在不同的秒边界上，于是
#: 「每秒误触发次数」与「误触发次数 ÷ 轮数」**不可互换** —— 否则自检证明不了
#: 速率真的以**时间**为分母（那正是本票要修的一类静默退出口径）。
#:
#: 构造出的读数（由 ``tests/test_decision_eval_timing.py`` 钉住）：
#: 18 轮 / 13 次开口（全部有打点）/ 3 次误触发 / 3 次抢话 / 跨度 400 s。
SYNTHETIC_PLAN: tuple[tuple[int, str, str, int | None, bool | None], ...] = (
    (0, "response", EXPECTED_SPEAK, 380, False),
    (20, "response", EXPECTED_SPEAK, 512, False),
    (45, "silence", EXPECTED_QUIET, None, None),
    (70, "not-for-me", EXPECTED_QUIET, None, None),
    # ★ 抢话①：用户还在说，它就插了。
    (95, "response", EXPECTED_SPEAK, 421, True),
    (120, "delegation", EXPECTED_SPEAK, 655, False),
    (145, "response", EXPECTED_SPEAK, 298, False),
    # 该开口却沉默（漏判）—— 保留它是为了证明**时序轴不替定向轴判漏判**。
    (170, "silence", EXPECTED_SPEAK, None, None),
    # ★ 抢话②。
    (195, "response", EXPECTED_SPEAK, 733, True),
    (215, "response", EXPECTED_SPEAK, 501, False),
    (240, "not-for-me", EXPECTED_QUIET, None, None),
    (265, "silence", EXPECTED_QUIET, None, None),
    # ★ 误触发①：真值不该开口，它开口了。
    (280, "response", EXPECTED_QUIET, 604, False),
    # ★ 误触发②：delegation 更贵（会触发外部检索 + 播报）。
    (300, "delegation", EXPECTED_QUIET, 612, False),
    (325, "response", EXPECTED_SPEAK, 466, False),
    # ★ 误触发③。
    (350, "response", EXPECTED_QUIET, 450, False),
    (375, "response", EXPECTED_SPEAK, 389, False),
    # ★ 抢话③。
    (400, "response", EXPECTED_SPEAK, 477, True),
)


def _stamp(second: int) -> str:
    """``2026-09-22T05:MM:SS.000Z``（秒数展开，便于肉眼核对间隔）."""
    return f"2026-09-22T05:{second // 60:02d}:{second % 60:02d}.000Z"


def _labeled_rows(rows: list[dict]) -> list[dict]:
    """断言合成夹具满足两条不变式（见 :data:`SYNTHETIC_PLAN` 的说明）.

    ★ 为什么在**生产代码路径**上查而不是只写在测试里：自检
    （:func:`decision_eval_timing.self_check` 与
    ``decision_eval_timing_criteria.self_check``）依赖这两条不变式成立才能证明
    「premature 真的被读进去了」与「基线本身是干净的」。不变式破掉时自检会报出
    一个**指向错误方向**的失败（「premature 为 None」看着像实现坏了、其实是夹具
    漏了一行；「恒等对照判红」看着像判据恒红、其实是基线脏了）。
    把它在这里当场说清，后人不必靠猜。

    Raises
    ------
        ValueError: 某个开口轮缺 premature 标注，或缺轮次打点。
    """
    missing_label = [
        row["id"]
        for row in rows
        if spoke(row)
        and str(row.get("round_kind") or "") != ROUND_KIND_PROACTIVE
        and row.get(FIELD_STILL_SPEAKING) is None
    ]
    if missing_label:
        raise ValueError(
            f"合成夹具违反不变式一：开口轮 {missing_label} 缺 "
            f"{FIELD_STILL_SPEAKING} 标注 ⇒ premature 分母不完整"
        )
    missing_stamp = [row["id"] for row in rows if spoke(row) and not row.get("latency_ms")]
    if missing_stamp:
        raise ValueError(
            f"合成夹具违反不变式二：开口轮 {missing_stamp} 缺 latency_ms ⇒ "
            "基线不再干净（恒等对照会判红，其它负控的「变红」就证明不了任何事）；"
            "「缺打点」这条退化应由 synthetic_unstamped_rows() 单独承载"
        )
    return rows


def synthetic_healthy_rows() -> list[dict]:
    """一份「有好有坏」的合成输入：三个量都算得出来.

    刻意不等距（见 :data:`SYNTHETIC_PLAN` 的说明），且三种「坏」各自出现：
    误触发 3 次、抢话 3 次、一次开口缺轮次打点、一次该开口却沉默。
    """
    rows: list[dict] = []
    for index, (second, decision, expected, latency, speaking) in enumerate(SYNTHETIC_PLAN, 1):
        row: dict = {
            "id": f"{SYNTHETIC_SESSION}-{index:02d}",
            "session_id": SYNTHETIC_SESSION,
            "ts": _stamp(second),
            "round_kind": ROUND_KIND_USER,
            "decision": decision,
            "expected": expected,
            "ok": True,
            "latency_ms": latency,
        }
        if speaking is not None:
            row[FIELD_STILL_SPEAKING] = speaking
        rows.append(row)
    return _labeled_rows(rows)


def synthetic_unstamped_rows() -> list[dict]:
    """★ **删掉全部轮次打点**，但 ``ts`` 一律保留.

    这正是本票 ★ 负控的输入：它对应的真实情形是
    ``live_mode._turn_started_at`` 的打点没接线（或事件来自 #156 之前的版本）。

    期望行为（由 :func:`decision_eval_timing.self_check` 断言）：
      * ``onset_latency_ms.median is None`` —— **不得**退回用 ``ts`` 差值；
      * ``n_missing_stamp`` 等于开口轮数 —— 缺失被**显式计数**；
      * ``session_seconds`` 仍为正 —— 否则这条负控证明不了「没退回用 ts」。
    """
    return [{**row, "latency_ms": None} for row in synthetic_healthy_rows()]


def synthetic_silent_rows() -> list[dict]:
    """★ 「永远沉默」的桩：每一轮都不开口.

    它的 premature 分母（开口次数）为 0 ⇒ 该比率必须是 ``None``（未测），
    **不得**是 0.0。一个 0.0 的抢话率会被读成「表现完美」，而真相是
    「这个桩根本没开口，故抢话这件事在它身上无从谈起」。
    """
    return [{**row, "decision": "silence", "latency_ms": None} for row in synthetic_healthy_rows()]


__all__ = [
    "SYNTHETIC_PLAN",
    "SYNTHETIC_SESSION",
    "synthetic_healthy_rows",
    "synthetic_silent_rows",
    "synthetic_unstamped_rows",
]
