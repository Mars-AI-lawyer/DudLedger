import csv
import unittest
from pathlib import Path
from ledger_viewer.jev_calibration import metrics


class CalibrationTest(unittest.TestCase):
    def test_unapproved_examples_never_become_ground_truth(self):
        path = Path(__file__).resolve().parents[1] / 'examples' / 'Jev中文校准样例.csv'
        with path.open(encoding='utf-8-sig', newline='') as handle:
            samples = list(csv.DictReader(handle))
        self.assertEqual(len(samples), 72)
        self.assertEqual(sum(r['数据集'] == '验证' for r in samples), 24)
        result = metrics(samples, {})
        self.assertEqual(result['律师确认数'], 0)
        self.assertIsNone(result['分组']['校准']['三档']['谨慎']['召回率'])

    def test_metrics_keep_missing_predictions_and_low_score_misses_visible(self):
        samples = [{'样例编号': key, '数据集': '验证', '标注状态': '已确认', '律师确认优先级': level}
                   for key, level in [('A','4'),('B','3'),('C','0'),('D','4')]]
        predictions = {key: dict(score=score, confidence=0.9, insufficient=0.1) for key, score in [('A',90),('B',50),('C',70)]}
        result = metrics(samples, predictions)['分组']['验证']
        self.assertEqual(result['未分析或失败'], 1)
        self.assertEqual(result['三档']['中']['低分漏检样例'], ['B'])
        self.assertEqual(result['三档']['中']['误报率'], 1)
        self.assertEqual(result['三档']['谨慎']['召回率'], 1)
        self.assertEqual(result['三档']['宽松']['召回率'], 0.5)


if __name__ == '__main__':
    unittest.main()
