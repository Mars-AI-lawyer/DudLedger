# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH).parent
stage = root / 'build' / 'mac' / 'source'
name = 'DudLedger'
a = Analysis(
    [str(stage / 'run.py')],
    pathex=[str(stage)],
    binaries=[],
    datas=[
        (str(stage / 'ledger_viewer' / 'resources' / 'icon-beta.png'), 'ledger_viewer/resources'),
        (str(stage / 'ledger_viewer' / 'specialist_rules.json'), 'ledger_viewer'),
        (str(stage / 'ledger_viewer' / 'skills' / 'dudledger-import' / 'SKILL.md'),
         'ledger_viewer/skills/dudledger-import'),
    ],
    hiddenimports=['keyring.backends.macOS'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['pytest', 'tests', 'IPython', 'notebook'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name=name,
    debug=False, strip=False, upx=False, console=True,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='mac-runtime')
app = BUNDLE(
    coll, name=name + '.app', icon=str(root / 'build/mac/DudLedger.icns'),
    bundle_identifier='com.dudledger.desktop',
    version='0.3.0',
    info_plist={
        'CFBundleDisplayName': name + ' Beta',
        'CFBundleShortVersionString': '0.3.0',
        'NSHighResolutionCapable': True,
        'LSMultipleInstancesProhibited': True,
    },
)
