"""探测某个 python 环境的训练栈版本（供 WSL 内调用）。

用法: <python> probe_env.py
"""
import importlib

MODULES = ["torch", "bitsandbytes", "transformers", "peft",
           "accelerate", "datasets", "trl", "torchao", "xformers"]

for name in MODULES:
    try:
        mod = importlib.import_module(name)
        ver = getattr(mod, "__version__", "?")
        print("  %-14s %s" % (name, ver))
    except Exception:
        print("  %-14s -" % name)

try:
    import torch

    print("  CUDA: %s | build: %s" % (torch.cuda.is_available(), torch.version.cuda))
    if torch.cuda.is_available():
        print("  GPU: %s | capability: %s"
              % (torch.cuda.get_device_name(0), torch.cuda.get_device_capability(0)))
        print("  arch_list: %s" % (torch.cuda.get_arch_list(),))
except Exception as exc:
    print("  torch error: %s" % exc)
