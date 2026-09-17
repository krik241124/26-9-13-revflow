"""Allowlist-only source distribution. Credentials and runtime data are never selected."""
from __future__ import annotations
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = ['README.md', 'requirements.txt', 'setup.bat', 'run.bat', 'VERSION.txt',
              '.gitignore', 'project_config.py', 'launch.py', 'start.bat', '使用说明.html']


def release_files() -> list[Path]:
    files = [ROOT / name for name in ROOT_FILES]
    for directory, patterns in {
        'config': ['runtime.json', 'markets.json'],
        'docs': ['*.md'], 'sku_detect': ['sku_detect.js', 'detect.py', 'README.md'],
        'webui': ['*.py'], 'webui/templates': ['*.html'], 'webui/static': ['*.js', '*.css'],
        'revflow': ['revflow.py', 'README.md', 'getdetail.curl.example.txt'],
        'sku_write': ['*.py', 'README.md', 'config.json', 'auth.example.json'],
        'sku_write/mapping': ['category_map.json', 'attr_aliases.json', 'sku_overrides.json'],
        'tests': ['*.py', '*.js'], 'tools': ['build_release.py'],
    }.items():
        for pattern in patterns:
            files.extend(sorted((ROOT / directory).glob(pattern)))
    return sorted(set(files))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    files = release_files()
    # Empty templates only; a renamed credential file must not sneak into a release.
    import json
    auth = json.loads((ROOT / 'sku_write/auth.example.json').read_text(encoding='utf-8-sig'))
    if auth != {'authorization_web': '', 'raw_cookie': ''}:
        raise ValueError('auth.example.json must contain empty credential fields only')
    curl = (ROOT / 'revflow/getdetail.curl.example.txt').read_text(encoding='utf-8-sig')
    if any(line.strip() and not line.lstrip().startswith('#') for line in curl.splitlines()):
        raise ValueError('cURL example must contain instructions only')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as out:
        for path in files:
            out.write(path, 'ArkSwift_AutoListing/' + path.relative_to(ROOT).as_posix())
    print(f'Created {args.output} ({len(files)} files)')


if __name__ == '__main__':
    main()
