# ruff: noqa: RUF001, RUF002, RUF003
"""CI ruff 运行器 —— 行为测试（#158 收口时新增）.

★ 为什么这个文件存在
--------------------
`/implement #158` 在 `services/webui/tests/` 留了一个 `# noqa: PLC0415`，
它在 **webui 自己的 ruff 配置**下变成 `RUF100 unused-directive` ⇒ CI 判红。
而我 push 前只验了 `services/webinfer` —— **本地比 CI 更宽松**，于是绿着推、
红了才发现。这正是 #151 已付费学过的形态（「CI 比裸跑更宽松，不是更严」）。

根因很具体也很好笑：每个 ruff job 各带自己的 `--extend-ignore`，
且 webui 从**自己的 working-directory** 跑（因而用**它自己的** `[tool.ruff.lint]`）。
**手抄那些 ignore 集一定会抄错** —— 实测三次手抄给出三个不同答案，
其中两次是在 `memory-store` / `asr` / `tts` 上**假红**。

故本文件钉住四件事，而不是靠人记得：

1. **命令是从 workflow 提取的**，不是重打的 —— 因而**不可能与 CI 漂移**；
2. **提取为空 ⇒ 判红**（解析器或 workflow 形状变了时，绿必须是不可达的）；
3. **workflow 缺失 ⇒ 判红**（「没有可检查的东西」不得读成「通过」）；
4. **加入一个真失败 ⇒ 退出码非 0**（负控：证明它真的会红）。

Run: python -m pytest scripts/tests/test_ci_ruff_runner.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import run_ci_ruff as runner  # noqa: E402


# --- 1. 提取：命令来自 workflow，不是重打的 ---------------------------------


def test_extracts_a_plausible_number_of_commands():
    """提取必须真的抓到命令（不是 0、也不是 1 条）.

    ★ 断言「数量 > 1」而不是写字面量：workflow 增减 job 是本脚本**应当**
    跟随的事，钉死数字会让它每次都假红 —— 而假红会诱使人改断言，
    那比漏检更危险（#158 的负控里已就同一形态写过一次）。
    """
    jobs = runner.extract_commands()
    assert len(jobs) > 1, f"只提取到 {len(jobs)} 条命令 —— 解析器或 workflow 形状变了"
    assert all(name.startswith("Ruff") for name, _wd, _cmd in jobs)


def test_every_command_targets_ruff():
    """每条提取出的命令都必须是 ruff 调用（防止误抓别的 run: 行）."""
    for name, _wd, cmd in runner.extract_commands():
        assert cmd.startswith("ruff "), f"{name} 提取到的不是 ruff 命令: {cmd!r}"


def test_webui_is_linted_from_its_own_directory():
    """★ 这是那次事故的**具体根因**，故单独钉住.

    webui 的 ruff job 带 ``working-directory: services/webui``，因而解析的是
    **webui 自己的** `[tool.ruff.lint]`（`line-length=100`、`target py310`），
    与仓库根配置**不同**。从仓库根跑 ``ruff check services/webui`` 会得到
    另一个答案 —— 那正是不够的那次验证。
    """
    webui = [
        (name, wd, cmd)
        for name, wd, cmd in runner.extract_commands()
        if "webui" in name
    ]
    assert webui, "workflow 里找不到 webui 的 ruff job"
    for name, wd, _cmd in webui:
        assert wd == "services/webui", (
            f"{name} 的 working-directory 是 {wd!r}，不再是 services/webui —— "
            "本脚本因而会在错的配置下跑它"
        )


def test_ignore_sets_are_carried_through_verbatim():
    """★ ``--extend-ignore`` 必须**原样**带过来.

    ★ 这是本脚本存在的全部理由：每个 job 的 ignore 集不同，手抄必错
    （实测三次抄出三个答案）。若提取把它们丢掉了，脚本就会比 CI 更严
    （假红）或更松（漏检），两种都错。
    """
    commands = [cmd for _n, _w, cmd in runner.extract_commands()]
    with_ignore = [c for c in commands if "--extend-ignore" in c]
    assert len(with_ignore) >= 5, (
        f"只有 {len(with_ignore)} 条命令带 --extend-ignore —— 提取把它们丢了"
    )
    # 至少有一条真的带着一串规则码（不只是空标志）。
    assert any(len(c.split("--extend-ignore", 1)[1].strip()) > 5 for c in with_ignore), (
        "--extend-ignore 后面的规则码没有被带出来"
    )


# --- 2 & 3. 缺输入 ⇒ 判红（fail-closed） -----------------------------------


def test_missing_workflow_is_a_failure(tmp_path: Path, capsys: pytest.CaptureFixture):
    """★ workflow 缺失 ⇒ 判红，且**明说原因**.

    ★ 「没有可检查的东西」不得读成「通过」—— 本仓最贵的那一类缺陷。
    """
    missing = tmp_path / "quality.yml"
    with pytest.raises(FileNotFoundError, match="CI workflow not found"):
        runner.extract_commands(missing)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner, "extract_commands", lambda *a, **k: (_ for _ in ()).throw(
            FileNotFoundError("CI workflow not found: nope")
        ))
        assert runner.run_all() == 1
    assert "CI workflow not found" in capsys.readouterr().err


def test_empty_extraction_is_a_failure(monkeypatch: pytest.MonkeyPatch,
                                       capsys: pytest.CaptureFixture):
    """★ 提取到 0 条命令 ⇒ 判红（不许「0 条全过」）."""
    monkeypatch.setattr(runner, "extract_commands", lambda *a, **k: [])
    assert runner.run_all() == 1
    err = capsys.readouterr().err
    assert "ZERO ruff commands" in err


# --- 4. 负控：真失败必须让退出码非 0 ----------------------------------------


def _fake_jobs(*commands: str) -> list[tuple[str, str | None, str]]:
    """构造提取结果.

    ★ 每条命令都**带 ``ruff `` 前缀** —— 那是 ``extract_commands`` 的真实契约
    （``run_all`` 会把它拆成 ``python -m ruff …``）。裸写 ``check …`` 会让
    ``python -m check`` 报 "No module named check"，于是**负控测的就不是
    ruff 的退出码，而是夹具自身的错**。实测踩到过。
    """
    return [(f"Ruff lint (fake {i})", None, c) for i, c in enumerate(commands)]


def test_a_failing_command_makes_the_runner_exit_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
):
    """★ 负控：注入一个**真会失败**的 ruff 调用 ⇒ 退出码 1.

    ★ 没有这条，「本脚本是绿的」只证明它跑完了，不证明它会红 ——
    而那正是它要替代的那种「本地比 CI 宽松」。

    ★ 夹具必须是**真违规的文件**，不能是「不存在的路径」：实测 ruff 对
    不存在的路径**退出 0**（`no/such/path.py` → exit 0），故用不存在的路径
    做负控会让这条测试测的是别的东西（甚至直接假绿）。见
    :func:`test_ruff_exits_zero_on_a_missing_path`。
    """
    bad = REPO_ROOT / ".tmp_ci_ruff_negative_control.py"
    bad.write_text("x = undefined_name_xyz\n", encoding="utf-8")
    try:
        monkeypatch.setattr(
            runner,
            "extract_commands",
            lambda *a, **k: _fake_jobs(f"ruff check --select F821 {bad.name}"),
        )
        assert runner.run_all() == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "0/1" in out
    finally:
        bad.unlink(missing_ok=True)


def test_a_passing_command_exits_zero(monkeypatch: pytest.MonkeyPatch):
    """对照：真会通过的命令 ⇒ 退出码 0（防止本脚本恒红）."""
    monkeypatch.setattr(
        runner,
        "extract_commands",
        lambda *a, **k: _fake_jobs("ruff check --select F821 scripts/run_ci_ruff.py"),
    )
    assert runner.run_all() == 0


# --- ★ 一条实测发现：ruff 对不存在的路径退出 0（fail-open） -----------------


def test_ruff_exits_zero_on_a_missing_path():
    """★ 实测钉住：``ruff check <不存在的路径>`` **退出 0**.

    ★ 这条是**对 ruff 自身行为的取证**，不是对被测代码的断言。它存在的理由：
    一个笔误的路径会让 ruff「lint 了 0 个文件」而报绿 —— 与「门禁在输入缺失时
    判绿」是同一类 fail-open。本脚本因此加了 :func:`runner.preflight` 前置守卫。
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821", "no/such/path.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        "ruff 对不存在的路径不再退出 0 —— 若它改成非 0，preflight 守卫的前提变了，"
        "请重新评估（但守卫本身仍然正确）"
    )


def test_preflight_catches_a_mistyped_path(monkeypatch: pytest.MonkeyPatch,
                                          capsys: pytest.CaptureFixture):
    """★ 前置守卫：目标路径不存在 ⇒ 判红，而不是「lint 了 0 个文件」的假绿."""
    monkeypatch.setattr(
        runner,
        "extract_commands",
        lambda *a, **k: _fake_jobs("ruff check --select F821 no/such/path.py"),
    )
    assert runner.run_all() == 1
    err = capsys.readouterr().err
    assert "前置检查失败" in err
    assert "no/such/path.py" in err


def test_preflight_does_not_mistake_rule_codes_for_paths():
    """★ ``--extend-ignore D101,D102`` 的值是**规则码**，不是路径.

    ★ 不按形状排除它们，前置守卫会把每条带 ``--extend-ignore`` 的命令全判红
    —— 那会让本脚本恒红，进而诱使人拆掉守卫。
    """
    jobs = runner.extract_commands()
    assert runner.preflight(jobs) == [], runner.preflight(jobs)


def test_list_mode_prints_without_running(capsys: pytest.CaptureFixture):
    """``--list`` 只打印提取结果，不跑（供人核对提取是否正确）."""
    assert runner.run_all(list_only=True) == 0
    out = capsys.readouterr().out
    assert "ruff check" in out or "ruff format" in out


# --- 边界：本脚本**不**覆盖其余 job（防误读成「门禁通过」） ----------------


def test_script_documents_that_it_is_ruff_only():
    """★ 脚本必须**自己说清**它只覆盖 ruff job.

    ★ 否则一次绿读会被误读成「质量门禁通过」—— 而 pytest 矩阵 / eslint /
    drift-gate / package-smoke 都有各自的服务清单，本脚本一条都没复现。
    """
    doc = runner.__doc__ or ""
    assert "does NOT cover" in doc or "不覆盖" in doc, (
        "docstring 里没有说明它只覆盖 ruff job"
    )
    for other in ("pytest", "eslint", "drift-gate", "package-smoke"):
        assert other in doc, f"docstring 没点名它不覆盖 {other}"
