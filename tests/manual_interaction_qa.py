"""Launch an isolated synthetic book for native mouse interaction acceptance."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from openpyxl import Workbook

from ledger_viewer import issue_store
from ledger_viewer.importer import classify_sheet, discover_sheets
from ledger_viewer.ui import Application
from ledger_viewer.voucher_notes import voucher_key


def main():
    with tempfile.TemporaryDirectory(prefix='dudledger-interaction-qa-') as folder:
        base = Path(folder)
        with patch('ledger_viewer.ui.Path.home', return_value=base):
            root = Application()
        root.set_font_size(13, persist=False)
        app = next(iter(root.windows))
        app._use_book(base / '交互验收.sqlite')
        source = base / '合成分录.csv'
        source.write_text('日期,凭证号,科目编码,科目名称,摘要,借方金额,贷方金额\n' + ''.join(
            f'2026-01-02,{number},1002,银行存款,验收凭证{number}到账,{number * 100},0\n'
            f'2026-01-02,{number},4001,实收资本,验收凭证{number}确认资本,0,{number * 100}\n'
            for number in range(1, 258)), encoding='utf-8')
        app.store.import_sheet(classify_sheet(source, 'CSV'))
        book = Workbook()
        balance = book.active
        balance.title = '资产负债表'
        balance.append(['资产', '期末数', '年初数', '负债与权益', '期末数', '年初数'])
        balance.append(['银行存款', 1000, 0, '实收资本', 1000, 0])
        balance.append(['其他应收款', -125.15, None, '应付账款', 0, 0])
        income = book.create_sheet('利润表')
        income.append(['项目', '本期金额', '上期金额'])
        income.append(['营业收入', 1000, 0])
        income.append(['营业成本', 200, -12.15])
        reports = base / '合成报表.xlsx'
        book.save(reports)
        for spec in discover_sheets([reports])[0]:
            app.store.import_sheet(spec)
        for title in ('出资真实性', '关联方资金往来', '待补充资料'):
            issue_store.create_issue(app.store, title)
        issue_store.mark_vouchers(app.store, [voucher_key(app.store.entries()[index]) for index in (0, 4, 8)])
        app.refresh_all()
        app.select_page('entries')
        app.geometry('1160x690')
        app.title('DudLedger 交互验收（临时合成数据）')
        app.workbench.show()
        app.workbench.edit_issue(1)
        app.status.set('全部为临时合成数据；关闭程序后删除。可验证框选、多选、跨页与原生拖曳。')
        try:
            root.mainloop()
        except KeyboardInterrupt:
            app._close()


if __name__ == '__main__':
    main()
