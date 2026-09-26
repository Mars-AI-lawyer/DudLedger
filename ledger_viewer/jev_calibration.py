"""Offline calibration metrics; never calls a cloud service or reads credentials."""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


def _needs_manual_review(prediction) -> bool:
    try:
        return bool(json.loads(prediction['response_json'] or '{}').get('needs_manual_review'))
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def metrics(samples, predictions, thresholds=None):
    thresholds = thresholds or {'谨慎': 35, '中': 50, '宽松': 65}
    approved = [s for s in samples if s['标注状态'] == '已确认']
    for row in approved:
        if row['律师确认优先级'] not in ('0', '1', '2', '3', '4'):
            raise ValueError(f"{row['样例编号']} 缺少有效的律师优先级标注")
    report = {'样例总数': len(samples), '律师确认数': len(approved),
              '指标口径': '人工优先级 ≥ 2 为需核查正例；任一主题概率达到各档阈值即判为检出（不再使用加权优先分）；仅统计律师已确认且分析成功的样例。理想做法是按主题分别标注样例后逐主题统计。',
              '状态': '可计算' if approved else '待律师标注，不提供准确率或召回率结论', '分组': {}}
    for split in ('校准', '验证'):
        selected = [s for s in approved if s['数据集'] == split]
        scored = [(s, predictions[s['样例编号']]) for s in selected if s['样例编号'] in predictions]
        result = {'律师已确认': len(selected), '分析成功': len(scored), '未分析或失败': len(selected)-len(scored),
                  '判断不确定数': sum((p['insufficient'] or 0) >= 0.5 or _needs_manual_review(p) for _, p in scored), '三档': {}}
        for tier, threshold in thresholds.items():
            positive = [(s,p) for s,p in scored if int(s['律师确认优先级']) >= 2]
            negative = [(s,p) for s,p in scored if int(s['律师确认优先级']) < 2]
            tp = sum(p['score'] >= threshold for _,p in positive)
            fp = sum(p['score'] >= threshold for _,p in negative)
            result['三档'][tier] = {'阈值': threshold, '需核查正例数': len(positive), '检出正例': tp, '误报数': fp,
                '召回率': tp / len(positive) if positive else None,
                '误报率': fp / len(negative) if negative else None,
                '未命中样例': [s['样例编号'] for s,p in positive if p['score'] < threshold]}
        report['分组'][split] = result
    return report


def main():
    parser = argparse.ArgumentParser(description='对虚构样例进行本地校准统计，不发送数据')
    parser.add_argument('--samples', type=Path, required=True)
    parser.add_argument('--book', type=Path, required=True)
    parser.add_argument('--batch', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    with args.samples.open(encoding='utf-8-sig', newline='') as handle:
        samples = list(csv.DictReader(handle))
    db = sqlite3.connect(args.book.resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        batch = db.execute('''SELECT b.id,b.model,b.rule_id,r.body_json FROM jev_batches b
            JOIN jev_rules r ON r.id=b.rule_id WHERE b.id=?''', (args.batch,)).fetchone()
        if batch is None:
            raise ValueError('指定批次不存在')
        predictions = {json.loads(r['local_json'])['voucher_no']: dict(r) for r in db.execute(
            "SELECT * FROM jev_results WHERE batch_id=? AND status='success' AND stale=0", (args.batch,))}
        report = metrics(samples, predictions, json.loads(batch['body_json'])['thresholds'])
        report.update({'批次': args.batch, '规则版本': batch['rule_id'], '请求模型': batch['model']})
    finally:
        db.close()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'本地统计已保存：{args.output.resolve()}')


if __name__ == '__main__':
    main()
