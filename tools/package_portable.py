"""Package only the built runtime; check contents and publish checksums."""
from pathlib import Path
import hashlib
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    runtime = ROOT / 'portable/VeeamM365RestoreTester'
    version = re.search(r"StringStruct\('ProductVersion', '([^']+)'", (ROOT / 'version_info.txt').read_text()).group(1)
    assert (runtime / 'VeeamM365RestoreTester.exe').is_file()
    internal = runtime / '_internal'
    assert (internal / 'PySide6/QtWebEngineProcess.exe').is_file()
    assert not (internal / 'PySide6/qml').exists(), 'Unused QML content collected'
    for folder in ('assets', 'scripts'):
        for source in (ROOT / folder).iterdir():
            if source.is_file():
                assert source.read_bytes() == (internal / folder / source.name).read_bytes(), source
    for name in ('LICENSE', 'THIRD_PARTY_NOTICES.txt'):
        (runtime / ('LICENSE.txt' if name == 'LICENSE' else name)).write_bytes((ROOT / name).read_bytes())
    files = sorted(p for p in runtime.rglob('*') if p.is_file())
    assert {p.name for p in runtime.iterdir()} == {'VeeamM365RestoreTester.exe', '_internal', 'LICENSE.txt', 'THIRD_PARTY_NOTICES.txt'}
    archive = ROOT / 'portable' / f'VeeamM365RestoreTester-{version}-portable.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in files:
            output.write(path, path.relative_to(runtime.parent))
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
        assert len(output.namelist()) == len(files)
    with archive.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest().upper()
    (archive.parent / 'SHA256SUMS.txt').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    manifest = dict(version=version, archive=archive.name, sha256=checksum,
                    archive_bytes=archive.stat().st_size, runtime_bytes=sum(p.stat().st_size for p in files), files=len(files))
    (archive.parent / 'release.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest), flush=True)


if __name__ == '__main__':
    main()
