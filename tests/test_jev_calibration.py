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

    def test_metrics_keep_missing_predictions_and_unhit_misses_visible(self):
        samples = [{'样例编号': key, '数据集': '验证', '标注状态': '已确认', '律师确认优先级': level}
                   for key, level in [('A','4'),('B','3'),('C','0'),('D','4')]]
        predictions = {key: dict(score=score, insufficient=0.1, response_json='{}')
                       for key, score in [('A',90),('B',45),('C',70)]}
        result = metrics(samples, predictions)['分组']['验证']
        self.assertEqual(result['未分析或失败'], 1)
        self.assertEqual(result['三档']['中']['未命中样例'], ['B'])
        self.assertEqual(result['三档']['中']['误报率'], 1)
        self.assertEqual(result['三档']['谨慎']['召回率'], 1)
        self.assertEqual(result['三档']['宽松']['召回率'], 0.5)



class MultiProviderCalibrationTest(unittest.TestCase):
    def test_flash_boolean_hits_are_separate_from_jev_thresholds(self):
        import json
        samples = [{'样例编号': key, '数据集': '验证', '标注状态': '已确认', '律师确认优先级': level}
                   for key, level in [('A', '4'), ('B', '0'), ('C', '4')]]
        predictions = {
            'A': dict(score=None, insufficient=0, response_json=json.dumps({'provider':'deepseek',
                'model':'deepseek-flash', 'hits':[{'id':'OR01','evidence':'股东借款'}]})),
            'B': dict(score=None, insufficient=0, response_json=json.dumps({'provider':'deepseek',
                'model':'deepseek-flash','hits':[]})),
            'C': dict(score=55, insufficient=0, response_json=json.dumps({'provider':'jev',
                'model':'jev-1.13.0','answers':{}})),
        }
        report = metrics(samples, predictions)
        groups = report['按模型分别统计']
        self.assertEqual(set(groups), {'deepseek / deepseek-flash', 'jev / jev-1.13.0'})
        flash = groups['deepseek / deepseek-flash']['分组']['验证']['三档']
        self.assertEqual(flash['宽松']['召回率'], 1)
        self.assertEqual(flash['宽松']['误报率'], 0)
        self.assertEqual(groups['jev / jev-1.13.0']['分组']['验证']['三档']['宽松']['召回率'], 0)
    def test_bailian_calibration_uses_independent_thresholds(self):
        import json
        samples = [{'样例编号':'A', '数据集':'验证', '标注状态':'已确认', '律师确认优先级':'4'}]
        predictions = {'A': dict(score=60, insufficient=0, response_json=json.dumps({
            'provider':'bailian', 'model':'decision-model-preview', 'answers':{}}))}
        report = metrics(samples, predictions, {'谨慎':95, '中':97, '宽松':99},
            provider_thresholds={'bailian':{'谨慎':50, '中':65, '宽松':85}})
        self.assertEqual(report['分组']['验证']['三档']['谨慎']['召回率'], 1)
        group = report['按模型分别统计']['bailian / decision-model-preview']
        self.assertEqual(group['分组']['验证']['三档']['谨慎']['阈值'], 50)
        self.assertEqual(group['分组']['验证']['三档']['中']['召回率'], 0)



if __name__ == '__main__':
    unittest.main()
