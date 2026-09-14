#!/usr/bin/env bash
# =============================================================================
# 本项目专用环境隔离（跨项目隔离 2026-09-14）
# =============================================================================
# 用法：
#     source scripts/isolate-env.sh        # 当前 shell 生效
#     或在 CI / 脚本里： bash -c '. scripts/isolate-env.sh && npm ci'
#
# 背景
# -----------------------------------------------------------------------------
# 本机有多个独立 agent 项目共享【用户级环境变量】：
#     D:\AI\workspace\JoyAI-VL-Interaction-main   ← 本项目
#     D:\Workspace\hermes-agent                   ← 另一个独立项目（Node 项目）
#
# 用户级 npm_config_cache 指向 hermes-agent 的缓存目录（它有 344MB 缓存，且其
# 代码 mcp_tool_config.py:201 读取该变量定位 npx 缓存）—— **不能改它**，
# 否则会破坏 hermes-agent。
#
# 而 npm 的配置优先级实测为：
#     env (npm_config_*)  >  project .npmrc  >  user .npmrc  >  builtin
# 即【环境变量高于项目 .npmrc】—— 单加 .npmrc 不足以覆盖。
#
# 因此本项目采用【进程级覆盖】：在本项目的 shell / CI 里把变量钉回本项目目录。
# 这样两个项目各用各的缓存，实现双向隔离，且互不干扰。
#
# 验证：
#     /d/AI/envs/joyai-main/python.exe scripts/cross_project_isolation.py
# =============================================================================

# 本项目根（本脚本在 scripts/ 下）
_JOYAI_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"

# ── npm：缓存钉回本项目 ──────────────────────────────────────────────
# 不设则继承用户级值 → 会写进 hermes-agent 的目录
export npm_config_cache="${_JOYAI_ROOT}/.cache/npm"

# ── 以下缓存变量用户级已正确指向本项目，此处显式重设以防被其他项目改写 ──
export HF_HOME="${_JOYAI_ROOT}/.cache/huggingface"
export HF_HUB_CACHE="${_JOYAI_ROOT}/.cache/huggingface/hub"
export PIP_CACHE_DIR="${_JOYAI_ROOT}/.cache/pip"
export UV_CACHE_DIR="${_JOYAI_ROOT}/.cache/uv"
export PLAYWRIGHT_BROWSERS_PATH="${_JOYAI_ROOT}/.cache/playwright"
export ELECTRON_CACHE="${_JOYAI_ROOT}/.cache/electron"

# ── Hermes：本项目只作为 background-agent 的上游，显式用自己的变量名 ──
# 用户级 HERMES_HOME 指向 hermes-data（那是 hermes-agent 的数据目录）。
# 本项目脚本应优先读 JOYAI_HERMES_HOME（见 services/scripts/run-windows.ps1）。
# 此处不覆盖 HERMES_HOME —— 那会破坏 hermes-agent；只是明确本项目不依赖它。

# 确保缓存目录存在
mkdir -p "${npm_config_cache}" 2>/dev/null || true

echo "[isolate-env] 本项目环境隔离已生效:"
echo "  npm_config_cache = ${npm_config_cache}"
