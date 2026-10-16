"""pearson_r 的单元测试:用已知数学事实做标准答案"""
import sys
import unittest
from pathlib import Path

# 让测试能找到上一级的 corr_tool.py(否则 import 会失败)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from corr_tool import pearson_r


class TestPearsonR(unittest.TestCase):
    """已知答案用例:1.0 / -1.0 是数学事实,不是算出来的。"""

    def test_perfect_positive(self):
        """[1,2,3] 与 [2,4,6] 完全正相关 → 1.0"""
        self.assertAlmostEqual(pearson_r([1, 2, 3], [2, 4, 6]), 1.0, places=9)

    def test_perfect_negative(self):
        """[1,2,3] 与 [6,4,2] 完全负相关 → -1.0"""
        self.assertAlmostEqual(pearson_r([1, 2, 3], [6, 4, 2]), -1.0, places=9)

    def test_zero_variance(self):
        """某列方差为 0 → 相关系数无定义,应抛 ZeroDivisionError"""
        with self.assertRaises(ZeroDivisionError):
            pearson_r([5, 5, 5], [1, 2, 3])

    def test_both_zero_variance(self):
        """两列都是常数 → 同样抛异常"""
        with self.assertRaises(ZeroDivisionError):
            pearson_r([5, 5, 5], [7, 7, 7])

    def test_empty(self):
        """空数据 → 应抛 ValueError,不是除以 0 崩溃"""
        with self.assertRaises(ValueError):
            pearson_r([], [])


if __name__ == "__main__":
    unittest.main()
