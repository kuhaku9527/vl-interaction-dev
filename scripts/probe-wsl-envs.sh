#!/usr/bin/env bash
# 探测 WSL 内所有 conda 环境的训练栈（在 WSL 内执行，避开 Windows 侧转义问题）。
for E in ai-base joyai-vllm; do
  P="/home/ku/miniconda3/envs/$E/bin/python"
  echo "=== $E ==="
  if [ -x "$P" ]; then
    "$P" /mnt/d/AI/workspace/JoyAI-VL-Interaction-main/scripts/probe-env.py 2>&1 | head -14
  else
    echo "  (no python at $P)"
  fi
done
