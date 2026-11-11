"""Run manually: python tests/benchmark_responsiveness.py (synthetic, no network)."""
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ledger_viewer.ui import Application
from ledger_viewer.jev_rules import load_rules
from ledger_viewer.jev_review import voucher_result_context, voucher_results


def main():
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        with patch('ledger_viewer.ui.Path.home', return_value=base):
            root = Application()
        app = next(iter(root.windows))
        app._use_book(base / 'bench.sqlite')
        db = app.store.db
        db.execute("INSERT INTO sources(id,kind,filename,original_path,sheet,sha256,mapping_json) VALUES(1,'entry','synthetic.csv','/synthetic.csv','CSV','bench','{}')")
        db.executemany('INSERT INTO accounts VALUES(?,?,?)',
                       [(f'C:{1000+i}', str(1000+i), f'科目{i}') for i in range(1000)])
        version, rules = load_rules(app.store)
        batch = db.execute("INSERT INTO jev_batches(rule_id,model,scope_json,snapshot_hash,status) VALUES(?,?,'{}','','completed')",
                           (version, rules['model'])).lastrowid
        previous = 0
        try:
            for size in (100000, 500000):
                db.executemany('''INSERT INTO entries(source_id,row_no,entry_date,fiscal_year,period,voucher_no,
                    voucher_type,account_key,summary,counterparty,auxiliary,debit_cents,credit_cents,raw_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    ((1, i + 1, '2026-01-01', '2026', '1', str(i // 2), '记', f'C:{1000+i%1000}',
                      '正常业务', '某公司', '', 100 if i % 2 == 0 else 0, 100 if i % 2 else 0, '{}') for i in range(previous, size)))
                previous = size
                db.execute('UPDATE sources SET row_count=? WHERE id=1', (size,))
                app.store._touch_ledger()
                db.commit()
                root.update()
                gaps = []
                last = time.perf_counter()
                def heartbeat():
                    nonlocal last
                    now = time.perf_counter()
                    gaps.append((now - last) * 1000)
                    last = now
                    return root.after(10, heartbeat)
                heartbeat_id = root.after(10, heartbeat)
                begin = time.perf_counter()
                app.refresh_all()
                request_ms = (time.perf_counter() - begin) * 1000
                deadline = begin + 30
                while app.tasks.active and time.perf_counter() < deadline:
                    root.update()
                    time.sleep(0.002)
                root.update()
                loaded_ms = (time.perf_counter() - begin) * 1000
                heartbeat_commands = [job for job in root.tk.splitlist(root.tk.call('after', 'info'))
                    if 'heartbeat' in str(root.tk.call('after', 'info', job))]
                for job in heartbeat_commands:
                    root.after_cancel(job)
                assert not app.tasks.active, 'background refresh timed out'
                begin = time.perf_counter()
                app.summary_search.set('科目1')
                app.refresh_summary()
                summary_request_ms = (time.perf_counter() - begin) * 1000
                while app.tasks.active and time.perf_counter() < deadline:
                    root.update()
                    time.sleep(0.002)
                begin = time.perf_counter()
                context = voucher_result_context(app.store, batch)
                initial_ai_ms = (time.perf_counter() - begin) * 1000
                begin = time.perf_counter()
                voucher_results(app.store, batch, context=context)
                voucher_results(app.store, batch, lane='candidates', context=context)
                reused_ai_ms = (time.perf_counter() - begin) * 1000
                print(json.dumps(dict(rows=size, refresh_request_ms=round(request_ms, 1),
                    refresh_complete_ms=round(loaded_ms, 1), heartbeat_max_gap_ms=round(max(gaps, default=0), 1),
                    summary_search_request_ms=round(summary_request_ms, 1), visible_detail_rows=len(app.detail_view.get_children()),
                    initial_ai_membership_ms=round(initial_ai_ms, 1), reused_ai_two_filters_ms=round(reused_ai_ms, 1))), flush=True)
        finally:
            app._close()


def benchmark_import():
    import csv
    from ledger_viewer.ui import BatchImportDialog
    for size in ((100000,) if '--quick' in sys.argv else (100000, 500000)):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            source = base / 'synthetic.csv'
            with source.open('w', encoding='utf-8', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['日期', '凭证号', '科目编码', '科目名称', '摘要', '借方金额', '贷方金额'])
                writer.writerows(('2026-01-01', str(index // 2), '1002' if index % 2 == 0 else '2001',
                    '银行存款' if index % 2 == 0 else '应付款', '正常业务',
                    1 if index % 2 == 0 else 0, 0 if index % 2 == 0 else 1) for index in range(size))
            with patch('ledger_viewer.ui.Path.home', return_value=base):
                root = Application()
            app = next(iter(root.windows))
            app._use_book(base / 'import.sqlite')
            root.update()
            gaps = []
            last = time.perf_counter()
            heartbeat_job = None
            def heartbeat():
                nonlocal last, heartbeat_job
                now = time.perf_counter()
                gaps.append((now - last) * 1000)
                last = now
                heartbeat_job = root.after(10, heartbeat)
            heartbeat_job = root.after(10, heartbeat)
            started = time.perf_counter()
            deadline = started + 120
            try:
                with patch('ledger_viewer.ui.messagebox.showinfo'):
                    dialog = BatchImportDialog(app, [source])
                    while dialog.working and time.perf_counter() < deadline:
                        root.update()
                        time.sleep(0.002)
                    assert dialog.items, dialog.ai_status.get()
                    inventory_ms = (time.perf_counter() - started) * 1000
                    dialog._use_local_mode()
                    while dialog.ai_running and time.perf_counter() < deadline:
                        root.update()
                        time.sleep(0.002)
                    dialog._import_selected()
                    while dialog.winfo_exists() and dialog.working and time.perf_counter() < deadline:
                        root.update()
                        time.sleep(0.002)
                    assert not dialog.winfo_exists(), dialog.ai_status.get()
                    while app.tasks.active and time.perf_counter() < deadline:
                        root.update()
                        time.sleep(0.002)
                total = app.store.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
                assert total == size, total
                print(json.dumps(dict(import_rows=size, inventory_ms=round(inventory_ms, 1),
                    import_and_refresh_ms=round((time.perf_counter()-started)*1000, 1),
                    heartbeat_max_gap_ms=round(max(gaps, default=0), 1))), flush=True)
            finally:
                if heartbeat_job:
                    root.after_cancel(heartbeat_job)
                app._close()


if __name__ == '__main__':
    benchmark_import() if '--import' in sys.argv else main()
