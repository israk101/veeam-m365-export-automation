"""Refresh documentation screenshots from an isolated, offline app run."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def main() -> None:
    target = ROOT / 'docs/images'
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='veeam-ui-') as temporary:
        output = Path(temporary) / 'check'
        env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QTWEBENGINE_CHROMIUM_FLAGS': '--no-sandbox --disable-gpu'}
        subprocess.run([sys.executable, str(ROOT / 'main.py'), '--self-test', str(output)], env=env, check=True, timeout=90)
        for name in ('dashboard', 'run', 'reports', 'settings'):
            shutil.copy2(output / f'{name}.png', target / f'{name}.png')
    print(target)

if __name__ == '__main__':
    main()
