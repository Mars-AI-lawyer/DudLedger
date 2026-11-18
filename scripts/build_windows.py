"""Build a Windows x64 portable ZIP from an explicit set of application files."""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from ledger_viewer import __version__
from ledger_viewer.importer import ImportSpec, guess_header
from ledger_viewer.store import LedgerStore

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'build' / 'windows'
DIST = ROOT / 'dist'
RESOURCES = ('specialist_rules.json', 'skills/dudledger-import/SKILL.md', 'resources/icon-beta.png')
MANUAL = Path('说明书.html')
SAMPLE_DIR = Path('模拟账套')
SAMPLE_FILES = tuple(SAMPLE_DIR / f'模拟数据_{kind}.xlsx' for kind in ('序时账', '科目余额表', '资产负债表', '利润表'))
SAMPLE_ENTRY = SAMPLE_DIR / '模拟数据_序时账.xlsx'
SAMPLE_BOOK = SAMPLE_DIR / '模拟序时账.sqlite'


def include_examples(app: Path):
    shutil.copyfile(ROOT / 'docs/index.html', app / MANUAL)
    (app / SAMPLE_DIR).mkdir()
    for relative in SAMPLE_FILES:
        shutil.copyfile(ROOT / relative, app / relative)

    source = app / SAMPLE_ENTRY
    sheet = '序时账'
    header, _, mapping = guess_header(source, sheet, 'entry')
    store = LedgerStore(app / SAMPLE_BOOK)
    try:
        store.import_sheet(ImportSpec(source, sheet, header, 'entry', mapping))
        with store.db:
            # Keep the bundled source reference relative to the example book.
            store.db.execute('UPDATE sources SET original_path=filename')
        store.db.execute('PRAGMA journal_mode=DELETE')
    finally:
        store.close()


def main():
    if sys.platform != 'win32' or platform.machine().upper() != 'AMD64' or sys.maxsize <= 2**32:
        raise SystemExit('Build this package with x64 Python on Windows x64.')

    stage = WORK / 'source'
    if stage.exists():
        shutil.rmtree(stage)
    source = ROOT / 'ledger_viewer'
    package = stage / source.name
    package.mkdir(parents=True)
    for module in source.glob('*.py'):
        shutil.copyfile(module, package / module.name)
    shutil.copyfile(ROOT / 'run.py', stage / 'run.py')

    for relative in RESOURCES:
        target = package / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)

    command = [
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--name', 'DudLedger', '--noupx', '--hide-console', 'hide-early',
        '--python-option', 'X utf8', '--hidden-import', 'keyring.backends.Windows',
        '--icon', str(package / 'resources/icon-beta.png'),
        '--specpath', str(WORK), '--workpath', str(WORK / 'pyinstaller'),
        '--distpath', str(WORK / 'runtime'),
    ]
    for module in ('pytest', 'tests', 'IPython', 'notebook'):
        command.extend(['--exclude-module', module])
    for relative in RESOURCES:
        target = package / relative
        command.extend(['--add-data', f'{target}:ledger_viewer/{Path(relative).parent.as_posix()}'])
    subprocess.run([*command, str(stage / 'run.py')], cwd=stage, check=True)

    include_examples(WORK / 'runtime' / 'DudLedger')
    archive_name = f'DudLedger-{__version__}-Windows-x64'
    DIST.mkdir(exist_ok=True)
    archive = Path(shutil.make_archive(str(WORK / archive_name), 'zip', WORK / 'runtime', 'DudLedger'))
    archive.replace(DIST / archive.name)
    print(f'Built dist/{archive.name}')


if __name__ == '__main__':
    main()
