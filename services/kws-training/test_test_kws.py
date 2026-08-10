"""KWS §10.2 compute_metrics 单元测试（纯 Python，不依赖 numpy/sherpa_onnx/音频）。

仅验证 compute_metrics 的门禁逻辑：按声学域(device)分组 recall + 整体 recall/FAR。

为了让本测试在裸 Python 3.13（未装 numpy/sherpa-onnx）上可跑，通过 importlib
加载 test_kws.py 并注入一个 numpy 桩模块——numpy 仅在 test_one_sherpa 内使用，
本测试不调用它，故桩足以避免 ImportError。本文件自身不写 `import numpy` / `import sherpa_onnx`。
"""
from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEST_KWS = _HERE / "test_kws.py"


def _load_compute_metrics():
    """加载 test_kws.compute_metrics，不触发真实 numpy 导入。"""
    if "numpy" not in sys.modules:
        # numpy 仅在 test_one_sherpa 内使用，本测试不调用；注入桩避免 ImportError
        sys.modules["numpy"] = types.ModuleType("numpy")
    spec = importlib.util.spec_from_file_location("test_kws_under_test", _TEST_KWS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compute_metrics


compute_metrics = _load_compute_metrics()


class TestComputeMetrics(unittest.TestCase):
    def test_A_perfect_domains(self):
        # nvidia_broadcast 10 全 hit; gameDAC_chat 10 中 8 hit -> overall 18/20=0.9
        pos_entries = (
            [{"device": "nvidia_broadcast", "hit": True} for _ in range(10)]
            + [{"device": "gameDAC_chat", "hit": (i < 8)} for i in range(10)]
        )
        neg_hits = [False] * 20
        m = compute_metrics(pos_entries, neg_hits, 0.9, 0.02)
        self.assertAlmostEqual(m["recall_by_device"]["nvidia_broadcast"], 1.0)
        self.assertAlmostEqual(m["recall_by_device"]["gameDAC_chat"], 0.8)
        self.assertAlmostEqual(m["recall_overall"], 0.9)
        self.assertAlmostEqual(m["far_overall"], 0.0)
        self.assertTrue(m["passed"])

    def test_B_recall_below_bar(self):
        # nvidia_broadcast 10 全 hit; gameDAC_chat 10 中 7 hit -> overall 17/20=0.85 (<0.9)
        pos_entries = (
            [{"device": "nvidia_broadcast", "hit": True} for _ in range(10)]
            + [{"device": "gameDAC_chat", "hit": (i < 7)} for i in range(10)]
        )
        neg_hits = [False] * 20
        m = compute_metrics(pos_entries, neg_hits, 0.9, 0.02)
        self.assertAlmostEqual(m["recall_overall"], 0.85)
        self.assertFalse(m["passed"])
        self.assertTrue(any("recall" in r for r in m["reasons"]))

    def test_C_far_above_bar(self):
        # 全部命中 -> recall 1.0; neg 20 中 1 命中 -> FAR 0.05 (>0.02)
        pos_entries = [{"device": "nvidia_broadcast", "hit": True} for _ in range(20)]
        neg_hits = [True] + [False] * 19
        m = compute_metrics(pos_entries, neg_hits, 0.9, 0.02)
        self.assertAlmostEqual(m["far_overall"], 0.05)
        self.assertFalse(m["passed"])
        self.assertTrue(any("far" in r for r in m["reasons"]))

    def test_D_missing_domain_zero_recall(self):
        # 只有 gameDAC_chat 样本，nvidia_broadcast 0 条 -> 该域 recall==0.0 且不崩
        pos_entries = [{"device": "gameDAC_chat", "hit": True} for _ in range(10)]
        neg_hits = [False] * 20
        m = compute_metrics(pos_entries, neg_hits, 0.9, 0.02)
        self.assertAlmostEqual(m["recall_by_device"].get("nvidia_broadcast", 0.0), 0.0)
        self.assertAlmostEqual(m["recall_by_device"]["gameDAC_chat"], 1.0)
        self.assertAlmostEqual(m["recall_overall"], 1.0)
        self.assertTrue(m["passed"])


if __name__ == "__main__":
    unittest.main()
