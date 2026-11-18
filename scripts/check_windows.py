"""Exercise the executable from the exact ZIP in a display-free Windows process."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from audit_mac import audit_bundle
from build_windows import MANUAL, RESOURCES, ROOT, SAMPLE_BOOK, SAMPLE_ENTRY, SAMPLE_FILES
from ledger_viewer.importer import file_hash
from ledger_viewer.store import LedgerStore

ROW_COUNT = 1238
TOTAL_CENTS = 5423636800


def agent(command, folder, *args):
    result = subprocess.run(
        [*command, '--agent-import', *map(str, args)], cwd=folder,
        stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8', timeout=60,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
    )
    if result.returncode:
        raise RuntimeError(f'Agent {args[0]} failed ({result.returncode}): {result.stderr or result.stdout}')
    return json.loads(result.stdout)


def exercise_import(command, folder, sample):
    source = folder / '模拟 序时账.xlsx'
    shutil.copyfile(sample, source)
    digest = file_hash(source)
    preview = agent(command, folder, 'inspect', source, '--limit', 2)
    if preview['sheets'] != ['序时账'] or preview['sha256'] != digest:
        raise RuntimeError('The executable did not read the expected workbook.')

    plan = folder / '导入 方案.json'
    plan.write_text(json.dumps({'version': 1, 'sheets': [{
        'path': source.name, 'sha256': digest, 'sheet': '序时账', 'kind': 'entry', 'header_row': 1,
        'mapping': {'date': 1, 'voucher_no': 2, 'summary': 3, 'account_code': 4,
                    'account_name': 5, 'debit': 6, 'credit': 7},
    }]}, ensure_ascii=False), encoding='utf-8')
    checked = agent(command, folder, 'validate', plan)
    if checked['status'] != 'ready' or checked['reports'][0]['stats']['imported_rows'] != ROW_COUNT:
        raise RuntimeError('The import plan did not pass validation with the expected row count.')

    book = folder / '便携 验证账套.sqlite'
    # The committed sample has date-format warnings; verify its values and totals below.
    created = agent(command, folder, 'create', plan, '--output', book, '--accept-warnings')
    if created['status'] != 'created' or created['sheets'][0]['imported_rows'] != ROW_COUNT or not book.is_file():
        raise RuntimeError('The executable did not create the expected ledger.')
    if file_hash(source) != digest:
        raise RuntimeError('The source workbook changed during import.')

    check_book(book, digest)
    print(f'Headless import passed: {ROW_COUNT} entries, matching totals, intact source, and voucher lookup.')


def check_book(book, source_digest):
    store = LedgerStore.connect_existing(book)
    try:
        integrity = store.db.execute('PRAGMA integrity_check').fetchone()[0]
        totals = store.db.execute('SELECT COUNT(*), SUM(debit_cents), SUM(credit_cents) FROM entries').fetchone()
        if integrity != 'ok' or tuple(totals) != (ROW_COUNT, TOTAL_CENTS, TOTAL_CENTS):
            raise RuntimeError(f'Unexpected SQLite integrity or entry totals: {integrity}, {tuple(totals)}')
        if [row['sha256'] for row in store.sources()] != [source_digest]:
            raise RuntimeError('The ledger does not reference the expected sample workbook.')
        entry_id = store.db.execute('SELECT id FROM entries WHERE row_no=2').fetchone()[0]
        lines = store.voucher_lines(entry_id)
        amounts = [(line['code'], line['debit_cents'], line['credit_cents']) for line in lines]
        if amounts != [('1002', 500000000, 0), ('4001', 0, 500000000)]:
            raise RuntimeError(f'Unexpected voucher lines: {amounts}')
    finally:
        store.close()


def main():
    archive = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix='DudLedger smoke ') as temporary:
        folder = Path(temporary) / '验证 home'
        shutil.unpack_archive(archive, folder)
        app = folder / 'DudLedger'
        binary = app / 'DudLedger.exe'
        bundled = app / '_internal'
        for path in [binary, bundled / '_tcl_data/init.tcl', bundled / '_tk_data/tk.tcl',
                     *(bundled / 'ledger_viewer' / relative for relative in RESOURCES)]:
            if not path.is_file():
                raise RuntimeError(f'Missing runtime file: {path.relative_to(app)}')
        required = {'ledger_viewer.ui', 'ledger_viewer.agent_import', 'openpyxl', 'xlrd',
                    'keyring.backends.Windows', 'typesafe_sdk', 'tkinter'}
        examples = {MANUAL: ROOT / 'docs/index.html'}
        examples.update((relative, ROOT / relative) for relative in SAMPLE_FILES)
        for relative, source in examples.items():
            if file_hash(app / relative) != file_hash(source):
                raise RuntimeError(f'Unexpected bundled file content: {relative}')
        check_book(app / SAMPLE_BOOK, file_hash(app / SAMPLE_ENTRY))
        print('Bundled offline manual, four example workbooks, and ready-to-open example ledger verified.')
        audit_bundle(app, binary, required, archive.with_suffix('.audit.json'),
                     sample_files={*SAMPLE_FILES, SAMPLE_BOOK})
        exercise_import([binary], folder, app / SAMPLE_ENTRY)


if __name__ == '__main__':
    main()
