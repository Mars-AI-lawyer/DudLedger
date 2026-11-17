"""Capture real DudLedger screens using an isolated, synthetic due-diligence book."""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
PROMO = ROOT / "promo"
ASSETS = PROMO / "assets"
DEMO = PROMO / "demo"
sys.path.insert(0, str(ROOT))

from ledger_viewer import issue_store
from ledger_viewer.importer import classify_sheet, discover_sheets
from ledger_viewer.ui import Application, ImportDialog
from ledger_viewer.voucher_notes import voucher_key


def build_sources() -> tuple[Path, Path]:
    DEMO.mkdir(parents=True, exist_ok=True)
    entries = DEMO / "示例科技_序时账.csv"
    fields = ["日期", "会计年度", "期间", "凭证字", "凭证号", "科目编码", "科目名称", "摘要", "往来单位", "借方金额", "贷方金额", "制单人"]
    vouchers = [
        ("2026-01-06", "收到股东首期出资款", [("100201", "银行存款—基本户", "银行", 2000000, 0), ("400101", "实收资本—股东出资", "创始人股东", 0, 2000000)]),
        ("2026-01-11", "股东借款转入公司账户", [("100201", "银行存款—基本户", "银行", 800000, 0), ("224101", "其他应付款—股东借款", "创始人股东", 0, 800000)]),
        ("2026-01-16", "向关联企业支付临时周转款", [("122101", "其他应收款—关联往来", "示例咨询有限公司", 350000, 0), ("100201", "银行存款—基本户", "银行", 0, 350000)]),
        ("2026-01-22", "收到关联企业部分归还款", [("100201", "银行存款—基本户", "银行", 150000, 0), ("122101", "其他应收款—关联往来", "示例咨询有限公司", 0, 150000)]),
        ("2026-02-03", "支付年度法律顾问服务费", [("660201", "管理费用—专业服务费", "示例律师事务所", 120000, 0), ("100201", "银行存款—基本户", "银行", 0, 120000)]),
        ("2026-02-08", "收到产品设计服务款", [("100201", "银行存款—基本户", "银行", 678000, 0), ("600101", "主营业务收入—设计服务", "客户甲", 0, 640000), ("222101", "应交税费—销项税额", "税务", 0, 38000)]),
        ("2026-02-14", "支付模具开发款", [("160401", "在建工程—模具开发", "供应商甲", 460000, 0), ("100201", "银行存款—基本户", "银行", 0, 460000)]),
        ("2026-02-20", "支付异常大额咨询费", [("660202", "管理费用—咨询费", "示例咨询有限公司", 280000, 0), ("100201", "银行存款—基本户", "银行", 0, 280000)]),
        ("2026-03-02", "预付供应链采购款", [("112301", "预付账款—材料采购", "供应商乙", 520000, 0), ("100201", "银行存款—基本户", "银行", 0, 520000)]),
        ("2026-03-09", "收到项目合作收入", [("100201", "银行存款—基本户", "银行", 960000, 0), ("600102", "主营业务收入—项目合作", "客户乙", 0, 960000)]),
        ("2026-03-15", "支付办公场地季度租金", [("660203", "管理费用—租赁费", "场地运营方", 180000, 0), ("100201", "银行存款—基本户", "银行", 0, 180000)]),
        ("2026-03-21", "支付员工报销款", [("660204", "管理费用—差旅费", "员工报销", 38600, 0), ("100201", "银行存款—基本户", "银行", 0, 38600)]),
        ("2026-04-07", "新增股东资本溢价投入", [("100201", "银行存款—基本户", "银行", 1500000, 0), ("400201", "资本公积—资本溢价", "投资股东", 0, 1500000)]),
        ("2026-04-12", "向关联企业追加资金往来", [("122101", "其他应收款—关联往来", "示例咨询有限公司", 180000, 0), ("100201", "银行存款—基本户", "银行", 0, 180000)]),
        ("2026-04-19", "支付软件系统实施费", [("170101", "无形资产—软件系统", "软件服务商", 320000, 0), ("100201", "银行存款—基本户", "银行", 0, 320000)]),
        ("2026-05-06", "收到产品销售收入", [("100201", "银行存款—基本户", "银行", 1250000, 0), ("600103", "主营业务收入—产品销售", "客户丙", 0, 1250000)]),
        ("2026-05-13", "支付物流与仓储费用", [("660101", "销售费用—物流仓储", "物流服务商", 76000, 0), ("100201", "银行存款—基本户", "银行", 0, 76000)]),
        ("2026-05-26", "支付营业外赔偿支出", [("671101", "营业外支出—赔偿支出", "争议相对方", 95000, 0), ("100201", "银行存款—基本户", "银行", 0, 95000)]),
        ("2026-06-04", "收到关联方后续归还款", [("100201", "银行存款—基本户", "银行", 100000, 0), ("122101", "其他应收款—关联往来", "示例咨询有限公司", 0, 100000)]),
        ("2026-06-18", "支付借款利息", [("660301", "财务费用—利息支出", "金融机构", 42800, 0), ("100201", "银行存款—基本户", "银行", 0, 42800)]),
    ]
    with entries.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fields)
        for number, (date, summary, lines) in enumerate(vouchers, start=1):
            year, month, _ = date.split("-")
            for code, name, party, debit, credit in lines:
                writer.writerow([date, year, str(int(month)), "记", f"{number:04d}", code, name, summary, party, debit, credit, "演示账套"])

    tables = DEMO / "示例科技_报表.xlsx"
    book = Workbook()
    balance = book.active
    balance.title = "科目余额表"
    balance.append(["科目编码", "科目名称", "期初借方", "期初贷方", "本期借方", "本期贷方", "期末借方", "期末贷方"])
    balances = [
        ("100201", "银行存款—基本户", 300000, 0, 6558800, 2893400, 3965400, 0),
        ("122101", "其他应收款—关联往来", 0, 0, 530000, 250000, 280000, 0),
        ("112301", "预付账款—材料采购", 0, 0, 520000, 0, 520000, 0),
        ("160401", "在建工程—模具开发", 0, 0, 460000, 0, 460000, 0),
        ("170101", "无形资产—软件系统", 0, 0, 320000, 0, 320000, 0),
        ("224101", "其他应付款—股东借款", 0, 0, 0, 800000, 0, 800000),
        ("400101", "实收资本—股东出资", 0, 0, 0, 2000000, 0, 2000000),
        ("400201", "资本公积—资本溢价", 0, 0, 0, 1500000, 0, 1500000),
        ("600101", "主营业务收入—设计服务", 0, 0, 0, 640000, 0, 640000),
        ("600102", "主营业务收入—项目合作", 0, 0, 0, 960000, 0, 960000),
        ("600103", "主营业务收入—产品销售", 0, 0, 0, 1250000, 0, 1250000),
        ("660201", "管理费用—专业服务费", 0, 0, 120000, 0, 120000, 0),
        ("660202", "管理费用—咨询费", 0, 0, 280000, 0, 280000, 0),
        ("671101", "营业外支出—赔偿支出", 0, 0, 95000, 0, 95000, 0),
        ("660301", "财务费用—利息支出", 0, 0, 42800, 0, 42800, 0),
    ]
    for row in balances:
        balance.append(row)
    bs = book.create_sheet("资产负债表")
    bs.append(["资产", "期末数", "年初数", "负债与所有者权益", "期末数", "年初数"])
    for row in [
        ("货币资金", 3965400, 300000, "应付账款", 0, 0),
        ("其他应收款", 280000, 0, "其他应付款", 800000, 0),
        ("预付账款", 520000, 0, "负债合计", 800000, 0),
        ("在建工程", 460000, 0, "实收资本", 2000000, 0),
        ("无形资产", 320000, 0, "资本公积", 1500000, 0),
        ("资产合计", 5545400, 300000, "所有者权益合计", 4745400, 300000),
    ]:
        bs.append(row)
    income = book.create_sheet("利润表")
    income.append(["项目", "本期金额", "上期金额"])
    for row in [("营业收入", 2850000, 1720000), ("营业成本", 1640000, 980000), ("管理费用", 618600, 355000), ("销售费用", 76000, 42000), ("财务费用", 42800, 18500), ("营业利润", 472600, 324500), ("营业外支出", 95000, 0), ("利润总额", 377600, 324500)]:
        income.append(row)
    book.save(tables)
    return entries, tables


def grab(window, name: str) -> None:
    window.update_idletasks()
    window.update()
    window.lift()
    x, y = window.winfo_rootx(), window.winfo_rooty()
    width, height = window.winfo_width(), window.winfo_height()
    output = ASSETS / f"{name}.png"
    subprocess.run(["/usr/sbin/screencapture", "-x", "-R", f"{x},{y},{width},{height}", str(output)], check=True)
    print(f"captured {output.name}: {width}x{height}", flush=True)


def main() -> None:
    entries, tables = build_sources()
    with patch("ledger_viewer.ui.Path.home", return_value=DEMO):
        root = Application()
    root.set_font_size(12, persist=False)
    app = next(iter(root.windows))
    app.geometry("1200x740+40+50")
    app.attributes("-topmost", True)
    app.title("DudLedger · 示例科技尽调项目")
    app._use_book(DEMO / f"示例科技尽调账套_{__import__('time').time_ns()}.sqlite")
    app.store.import_sheet(classify_sheet(entries, "序时账"))
    for spec in discover_sheets([tables])[0]:
        app.store.import_sheet(spec)

    entries_data = app.store.entries()
    keys = [voucher_key(entries_data[index]) for index in (0, 4, 10, 14, 26, 36)]
    issue_store.mark_vouchers(app.store, keys)
    issue_store.write_note(app.store, keys[1], "", "核查股东借款协议、约定利率及实际还款安排。")
    issue_store.write_note(app.store, keys[2], "", "关注关联资金用途，核对合同、流水与后续归还记录。")
    capital = issue_store.create_issue(app.store, "出资真实性与资本到位")
    related = issue_store.create_issue(app.store, "关联方资金往来")
    expense = issue_store.create_issue(app.store, "大额费用与营业外支出")
    issue_store.link_vouchers(app.store, capital, [keys[0], keys[4]])
    issue_store.link_vouchers(app.store, related, [keys[1], keys[2], keys[5]])
    issue_store.link_vouchers(app.store, expense, [keys[3]])
    app.refresh_all()
    app.book_name.set("示例科技 · 2026 尽调")
    app.status.set("演示数据 · 本地账套 · 双击明细打开完整凭证")

    steps = []

    def step(delay, action):
        steps.append((delay, action))

    step(700, lambda: (app.select_page("accounts"), app.workbench.hide()))
    step(1500, lambda: grab(app, "01_accounts"))
    step(2100, lambda: (app.select_page("entries"), app.show_all_entries()))
    step(2700, lambda: grab(app, "02_entries"))

    def show_import():
        dialog = ImportDialog(app, entries, sheet_name="序时账")
        dialog.attributes("-topmost", True)
        dialog.geometry("960x650+150+85")
        app.import_capture = dialog
        app.after(650, lambda: grab(dialog, "03_import"))
        app.after(1150, dialog.destroy)
    step(3300, show_import)

    def show_voucher():
        app.select_page("entries")
        app.open_voucher(None, entry_id=int(entries_data[4]["id"]))
        dialog = next(iter(app.voucher_windows))
        dialog.attributes("-topmost", True)
        dialog.geometry("920x480+170+150")
        app.voucher_capture = dialog
        app.after(650, lambda: grab(dialog, "04_voucher"))
        app.after(1150, dialog.destroy)
    step(4800, show_voucher)
    step(6200, lambda: (app.select_page("marks"), app.refresh_marks()))
    step(6800, lambda: grab(app, "05_marks"))

    def show_issue():
        app.select_page("entries")
        app.workbench.show()
        app.workbench.edit_issue(related)
        app.workbench.detail_tabs.select(app.workbench.record_page)
        app.workbench.detail_tab = "record"
    step(7400, show_issue)
    step(8100, lambda: grab(app, "06_issue_workbench"))
    step(8700, lambda: (app.workbench.hide(), app.select_page("reports")))
    step(9300, lambda: grab(app, "07_reports"))
    step(9900, lambda: app.select_page("review"))
    step(10600, lambda: grab(app, "08_ai_review"))

    def show_detail_focus():
        app.select_page("entries")
        app.search.set("关联")
        app.refresh_details()
        children = app.detail_view.get_children()
        if children:
            app.detail_view.selection_set(children[0])
            app.detail_view.see(children[0])
    step(11200, show_detail_focus)
    step(11900, lambda: grab(app, "09_entries_filtered"))
    step(12600, app._close)

    elapsed = 0
    for delay, action in steps:
        app.after(delay, action)
        elapsed = max(elapsed, delay)
    app.after(elapsed + 1500, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
