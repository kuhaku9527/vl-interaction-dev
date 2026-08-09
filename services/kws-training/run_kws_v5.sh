#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# run_kws_v5.sh — KWS v5 一键训练编排（数据就绪即可一键跑）
#
# 链路：prep(MUSAN fail-open) → train(CTC+Zipformer2+lhotse) → export(bt-en)
#
# 前提（用户侧，非本脚本职责）：
#   - 真人录 BT 到 $DATA_ROOT/positive/（见 KWS_V5_CAPTURE_SPEC.md）
#   - WSL2 kws-train 环境已装 torch/torchaudio/lhotse/icefall/onnx
#     （Windows 侧用 D:\AI\envs\joyai-sherpa，并把下方路径改成 D:/AI/...）
#
# 可覆盖（环境变量）：
#   KWS_PY        python 解释器（默认 python；WSL2 建议 ~/kws-train/bin/python）
#   KWS_DATA_ROOT 训练数据根（默认 /mnt/d/AI/data/kws/bt-en）
#   KWS_MUSAN_DIR MUSAN 目录（空=自动探测 <repo>/.cache/musan）；--no-musan 见下
#   KWS_LIVE_CAPTURE_DIR live 采集目录（默认 /mnt/d/AI/data/kws/mic_captures，jarvis_mode 落盘）
#   KWS_LIVE_FILTER live 样本过滤（默认 all；可选 asr-bt，需 analyze_kws_captures 依赖）
#   KWS_OUT_DIR   导出目标（默认 /mnt/d/AI/models/sherpa-onnx/models/kws/bt-en，
#                 须 == JarvisConfig.kws_model_dir）
#   KWS_EPOCHS    训练轮数（默认 30）
#
# MUSAN 缺失时 prep 自动 fail-open（仅用录制数据），不阻断本脚本。
# ---------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"   # services/kws-training -> repo

PY="${KWS_PY:-python}"
DATA_ROOT="${KWS_DATA_ROOT:-/mnt/d/AI/data/kws/bt-en}"
MUSAN_DIR="${KWS_MUSAN_DIR:-}"            # 空 = 自动探测 .cache/musan
LIVE_CAPTURE_DIR="${KWS_LIVE_CAPTURE_DIR:-/mnt/d/AI/data/kws/mic_captures}"
LIVE_FILTER="${KWS_LIVE_FILTER:-all}"
OUT_DIR="${KWS_OUT_DIR:-/mnt/d/AI/models/sherpa-onnx/models/kws/bt-en}"
NUM_EPOCHS="${KWS_EPOCHS:-30}"

PREP="$REPO_ROOT/services/scripts/prep_kws_data.py"
TRAIN="$REPO_ROOT/services/kws-training/train_kws.py"
EXPORT="$REPO_ROOT/services/kws-training/export_kws_onnx.py"
EXP_DIR="$DATA_ROOT/exp"

echo "==> KWS v5 训练编排"
echo "    PY=$PY"
echo "    DATA_ROOT=$DATA_ROOT"
echo "    OUT_DIR=$OUT_DIR"
echo "    MUSAN_DIR=${MUSAN_DIR:-<auto-detect .cache/musan>}"
echo "    LIVE_CAPTURE_DIR=$LIVE_CAPTURE_DIR (filter=$LIVE_FILTER)"

echo
echo "==> [1/3] prep（MUSAN fail-open）"
PREP_ARGS=(--data-root "$DATA_ROOT" --test-ratio 0.2)
if [ -n "$MUSAN_DIR" ]; then
  PREP_ARGS+=(--musan-dir "$MUSAN_DIR")
fi
PREP_ARGS+=(--live-capture-dir "$LIVE_CAPTURE_DIR" --live-filter "$LIVE_FILTER")
"$PY" "$PREP" "${PREP_ARGS[@]}"

echo
echo "==> [2/3] train（CTC + Zipformer2 + lhotse）"
"$PY" "$TRAIN" \
  --manifests-dir "$DATA_ROOT/manifests" \
  --exp-dir "$EXP_DIR" \
  --num-epochs "$NUM_EPOCHS"

echo
echo "==> [3/3] export → bt-en（与 JarvisConfig.kws_model_dir 一致）"
"$PY" "$EXPORT" \
  --ckpt "$EXP_DIR/best.pt" \
  --out-dir "$OUT_DIR"

echo
echo "==> done. 模型已导出到 $OUT_DIR"
echo "    验收：python services/scripts/test_jarvis_kws_e2e.py（recall≥90% / FAR≤2%）"
