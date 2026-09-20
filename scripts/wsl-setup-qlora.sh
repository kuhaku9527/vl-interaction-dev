#!/usr/bin/env bash
# 在 WSL 的 joyai-vllm 环境里准备并验证 QLoRA 训练栈。
#
# 唯一的资源密集步骤是最后的 QLoRA 冒烟测试（会占显存）。
# 前置的 pip install 是纯网络/磁盘操作。
#
# 用法（从 Windows 侧）:
#   MSYS_NO_PATHCONV=1 wsl.exe bash //mnt/d/.../scripts/wsl-setup-qlora.sh
set -u

PY=/home/ku/miniconda3/envs/joyai-vllm/bin/python
PIP=/home/ku/miniconda3/envs/joyai-vllm/bin/pip

echo "=== [0] 环境自检 ==="
"$PY" - <<'EOF'
import torch
print("torch       ", torch.__version__)
print("cuda build  ", torch.version.cuda)
print("cuda avail  ", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu         ", torch.cuda.get_device_name(0))
    print("capability  ", torch.cuda.get_device_capability(0))
    print("arch_list   ", torch.cuda.get_arch_list())
EOF

echo
echo "=== [1] 安装训练栈（网络/磁盘操作，不占 GPU）==="
"$PIP" install --quiet bitsandbytes peft accelerate 2>&1 | tail -5

echo
echo "=== [2] 版本确认 ==="
"$PY" - <<'EOF'
import importlib
for m in ("bitsandbytes", "peft", "accelerate"):
    try:
        mod = importlib.import_module(m)
        print("  %-14s %s" % (m, getattr(mod, "__version__", "?")))
    except Exception as exc:
        print("  %-14s FAILED: %s" % (m, exc))
EOF

echo
echo "=== [3] sm_120 上的 4-bit 反向传播（CUDA 13 关键验证）==="
"$PY" - <<'EOF'
import torch
try:
    from bitsandbytes.nn import Linear4bit
    lin = Linear4bit(512, 512, quant_type="nf4",
                     compute_dtype=torch.bfloat16).cuda()
    x = torch.randn(16, 512, dtype=torch.bfloat16, device="cuda",
                    requires_grad=True)
    y = lin(x)
    y.float().pow(2).mean().backward()
    torch.cuda.synchronize()
    print("  OK  4bit forward+backward  grad_norm=%.6f" % float(x.grad.norm()))
except Exception as exc:
    print("  FAIL  %s: %s" % (type(exc).__name__, str(exc)[:200]))
EOF

echo
echo "=== [4] 8-bit AdamW ==="
"$PY" - <<'EOF'
import torch
try:
    import bitsandbytes as bnb
    p = torch.nn.Parameter(torch.randn(1024, 1024, device="cuda"))
    opt = bnb.optim.AdamW8bit([p], lr=1e-4)
    p.float().pow(2).mean().backward()
    opt.step()
    torch.cuda.synchronize()
    print("  OK  8-bit AdamW step")
except Exception as exc:
    print("  FAIL  %s: %s" % (type(exc).__name__, str(exc)[:200]))
EOF

echo
echo "=== [5] PEFT LoRA on 4-bit base + gradient checkpointing ==="
"$PY" - <<'EOF'
import warnings
warnings.filterwarnings("ignore")
import torch
try:
    from transformers import AutoConfig, AutoModelForCausalLM
    from peft import LoraConfig, get_peft_model

    cfg = AutoConfig.for_model(
        "qwen3", hidden_size=1024, intermediate_size=2816,
        num_hidden_layers=4, num_attention_heads=16,
        num_key_value_heads=8, vocab_size=32768,
        max_position_embeddings=4096,
    )
    model = AutoModelForCausalLM.from_config(cfg).to(torch.bfloat16)
    lora = LoraConfig(r=8, lora_alpha=16, lora_dropout=0.05, bias="none",
                      target_modules=["q_proj", "v_proj"],
                      task_type="CAUSAL_LM")
    model = get_peft_model(model, lora).cuda()
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()   # 必需：否则梯度断裂
    model.train()
    ids = torch.randint(0, 32768, (2, 128), device="cuda")
    out = model(input_ids=ids, labels=ids)
    out.loss.backward()
    torch.cuda.synchronize()
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print("  OK  loss=%.4f  trainable=%d/%d (%.2f%%)"
          % (float(out.loss), trainable, total, 100 * trainable / total))
except Exception as exc:
    print("  FAIL  %s: %s" % (type(exc).__name__, str(exc)[:250]))
EOF

echo
echo "=== 完成 ==="
