#!/usr/bin/env python3
"""Check DMs profile routing using real QML, native controls and inert I/O.

Only a fresh Bubblewrap namespace may run the fixture. It has no network,
production HOME, desktop bus, compositor sockets or Python plugin helper.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHELL = Path('/usr/share/omarchy/shell')
UI = [
    'Panel', 'PanelController', 'BarWidget', 'BarIconButton', 'WidgetButton',
    'OpticalGlyph', 'Button', 'TextField', 'ToggleSwitch', 'PanelHero',
    'PanelSeparator', 'PanelSectionHeader', 'BorderSurface', 'BorderOverlay',
    'PanelKeyCatcher', 'PanelActionButton', 'PanelToolTip',
]
COMMONS = ['Style.qml', 'Util.qml', 'Border.qml', 'BorderGeometry.js']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    runner = Path('/usr/lib/qt6/bin/qmltestrunner')
    native_inputs = [
        *[SHELL / 'Ui' / (name + '.qml') for name in UI],
        *[SHELL / 'Commons' / name for name in COMMONS],
    ]
    for path in [runner, *native_inputs]:
        if not path.is_file():
            raise RuntimeError('missing QML prerequisite: ' + str(path))
    if not shutil.which('bwrap'):
        raise RuntimeError('Bubblewrap is required; no host fallback')

    work = args.output.absolute()
    if (work.parent != Path('/tmp')
            or not re.fullmatch('omabit-dms-profile-qml-[a-zA-Z0-9-]+', work.name)
            or work.exists()):
        raise RuntimeError('use a fresh /tmp/omabit-dms-profile-qml-NAME')
    work.mkdir(mode=0o700)
    for name in ['home', 'runtime', 'cache', 'tests']:
        (work / name).mkdir(mode=0o700)
    fixture = ROOT / 'tests/qml'
    shutil.copytree(fixture / 'imports', work / 'imports')
    for name in UI:
        shutil.copy2(SHELL / 'Ui' / (name + '.qml'),
                     work / 'imports/qs/Ui' / (name + '.qml'))
    (work / 'imports/qs/Ui/qmldir').write_text(
        'module qs.Ui\n' + ''.join(
            name + ' 1.0 ' + name + '.qml\n'
            for name in [*UI, 'KeyboardPanel']))
    for name in COMMONS:
        shutil.copy2(SHELL / 'Commons' / name, work / 'imports/qs/Commons' / name)

    source_hashes = {}
    for profile in ['stable', 'dev', 'edge']:
        target = work / profile
        target.mkdir()
        for path in [*sorted(source.glob('*.qml')),
                     *sorted(source.glob('*.js')), source / 'qmldir']:
            contents = path.read_text()
            source_hashes[path.name] = digest(path)
            if path.name == 'Dms.qml':
                # Replace only the compositor window. Production routing and
                # native controls execute unchanged, with no real process I/O.
                assert contents.count('  PanelWindow {') == 1
                contents = contents.replace('import Quickshell.Wayland\n', '')
                contents = contents.replace(
                    '  PanelWindow {', '  Rectangle {\n    width: 1024; height: 768')
                contents = contents.replace(
                    '    anchors { top: true; bottom: true; left: true; right: true }\n', '')
                contents = '\n'.join(
                    line for line in contents.split('\n')
                    if not line.strip().startswith(('WlrLayershell.', 'exclusionMode:')))
            (target / path.name).write_text(contents)
    shutil.copy2(fixture / 'tst_profiles.qml', work / 'tests/tst_profiles.qml')
    inputs = {str(path): digest(path) for path in [runner, *native_inputs]}
    inputs.update({str(path): digest(path) for path in sorted(fixture.rglob('*'))
                   if path.is_file() and '__pycache__' not in path.parts})

    command = [
        'bwrap', '--unshare-all', '--die-with-parent', '--clearenv',
        '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
        '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib', '/lib64',
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/etc',
        '--ro-bind', '/etc/fonts', '/etc/fonts', '--bind', str(work), '/lab',
        '--chdir', '/lab',
    ]
    environment = {
        'HOME': '/lab/home', 'XDG_RUNTIME_DIR': '/lab/runtime',
        'XDG_CACHE_HOME': '/lab/cache', 'PATH': '/usr/bin:/bin',
        'LANG': 'C.UTF-8', 'QT_QPA_PLATFORM': 'offscreen',
        'QT_QUICK_BACKEND': 'software', 'QML_DISABLE_DISK_CACHE': '1',
        'QT_QUICK_CONTROLS_STYLE': 'Basic', 'QT_QPA_PLATFORMTHEME': '',
    }
    for key, value in environment.items():
        command += ['--setenv', key, value]
    command += ['--', str(runner), '-input', '/lab/tests', '-import', '/lab/imports']
    result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    output = result.stdout + result.stderr
    (work / 'output.log').write_text(output)
    passed = result.returncode == 0 and not any(
        marker in output for marker in ('QWARN', 'ReferenceError', 'TypeError', 'FAIL!'))
    (work / 'evidence.json').write_text(json.dumps({
        'passed': passed, 'exit': result.returncode, 'source': str(source),
        'source_sha256': source_hashes, 'fixture_native_sha256': inputs,
        'no_network_or_host_home': True, 'inert_io': True,
        'actual_compositor': False,
    }, indent=2) + '\n')
    print(output)
    print('Evidence:', work)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
