"""Manual network checks for fictional AI sampling vouchers only."""
import json, threading, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from ledger_viewer.jev_mapping import get_api_key as jev_key
from ledger_viewer.deepseek_mapping import get_api_key as flash_key
from ledger_viewer.jev_review import request_review
from ledger_viewer.deepseek_review import request_review as flash_review
from ledger_viewer.specialist_rules import default_rules, rules_for_accounts
from ledger_viewer.bailian_review import DecisionClient, get_api_key as bailian_key, DEFAULT_ENDPOINT
import argparse
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

samples = [
 ('OR-positive','其他应收款','向股东虚构甲提供借款500000元','OR01'),
 ('OR-negative','其他应收款','员工正常差旅备用金预借',None),
 ('OP-positive','其他应付款','收到外部投资者虚构乙增资款，暂挂其他应付款','OP03'),
 ('OP-negative','其他应付款','收到正常租赁押金',None),
 ('PIC-positive','实收资本','收到股东虚构甲货币出资','PIC01'),
 ('PIC-negative','实收资本','会计差错更正，科目内部重分类，本次无出资、退资或减资交易',None),
 ('CR-positive','资本公积','收到投资者增资溢价，计入资本公积','CR01'),
 ('CR-negative','资本公积','权益法核算其他资本公积调整，无资本交易',None),
 ('NOE-positive','营业外支出','缴纳生态环境局行政罚款（行政处罚决定书，虚构示例）','NOE01'),
 ('NOE-negative','营业外支出','正常使用年限届满的常规固定资产报废，无事故',None),
 ('FE-positive','财务费用','向融资担保公司支付本公司融资担保费','FE03'),
 ('FE-negative','财务费用','按月正常计提银行贷款利息',None),
]
rules=default_rules()
parties={'股东':['虚构甲']}
keys={}

def run(provider, sample):
    ident,account,summary,expected=sample
    payload=[{'entry_date':'2026-01-05','name':account,'summary':summary,'counterparty':'虚构甲' if '虚构甲' in summary else '','debit':'500000.00' if ident=='OR-positive' else '1000.00'},
             {'entry_date':'2026-01-05','name':'银行存款','summary':summary,'credit':'500000.00' if ident=='OR-positive' else '1000.00'}]
    active=rules_for_accounts(rules,[account])
    start=time.monotonic()
    if provider in ('jev', 'bailian'):
        options={}
        if provider=='bailian':
            active['model']='decision-model-preview'
            options['client_factory']=lambda **kw: DecisionClient(**kw, endpoint=endpoint)
        result=request_review(payload,active,keys[provider],threading.Event(),parties=parties,**options,
            features={'limits':'全为虚构样例；没有账龄、历史交易及银行流水，不得推断长期未还或出资后转出。'})
    else:
        result=flash_review(payload,active,keys[provider],threading.Event(),parties=parties,
            background={'limits':'全为虚构样例；未提供账龄及历史记录。'})
    item={'provider':provider,'sample':ident,'account':account,'summary':summary,'expected':expected,
          'status':result['status'],'seconds':round(time.monotonic()-start,2)}
    if result['status']=='success':
        data=result['items'][0]
        item['model']=data['model']
        if provider in ('jev', 'bailian'):
            probabilities={k:v['noul'] for k,v in data['answers'].items()}
            item['probabilities']=probabilities
            hits=[k for k,v in probabilities.items() if k!='insufficient' and v>=.5]
            item['insufficient']=probabilities['insufficient']>=.5
        else:
            hits=[hit['id'] for hit in data['hits']]
            item['evidence']=data['hits']
            item['insufficient']=data['insufficient']
        item['hits']=hits
        item['passed']=(set(hits)=={expected} if expected else not hits) and not item['insufficient']
    else:
        item['error']=result.get('error','')
        item['quota']=result.get('quota',False)
    print(json.dumps({k:v for k,v in item.items() if k in ('provider','sample','status','hits','passed','error','seconds')},ensure_ascii=False),flush=True)
    return item

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='仅用虚构凭证验证 AI 抽查，会使用已保存的模型密钥调用相应服务。')
    parser.add_argument('--provider', choices=('all','bailian','jev','deepseek'), default='all')
    parser.add_argument('--output', type=Path, default=Path('docs/ai-review/虚构样例模型联调.json'))
    args=parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    providers=('bailian','jev','deepseek') if args.provider=='all' else (args.provider,)
    keys.update(bailian=bailian_key(), jev=jev_key(), deepseek=flash_key())
    try:
        preferences=json.loads((Path.home()/'.dudledger/preferences.json').read_text())
    except (OSError, ValueError):
        preferences={}
    endpoint=preferences.get('bailian_endpoint',DEFAULT_ENDPOINT)
    results=[]
    for provider in providers:
        if not keys[provider]:
            results.append({'provider':provider,'status':'not_configured'})
            continue
        first=run(provider,samples[0]);results.append(first)
        if first['status']!='success':
            continue
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures=[pool.submit(run,provider,sample) for sample in samples[1:]]
            results.extend(future.result() for future in as_completed(futures))
    report={'date':str(datetime.now(ZoneInfo('Asia/Shanghai')).date()),'data':'仅虚构凭证；未使用真实账簿','purpose':'六类科目的正反例冒烟测试，不代表真实账簿准确率或召回率','results':results}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('报告已保存：'+str(args.output),flush=True)
