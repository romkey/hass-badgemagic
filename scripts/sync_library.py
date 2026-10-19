#!/usr/bin/env python3
"""Copy the badgemagic_ble library into the Home Assistant integration.

The library's source of truth is ``badgemagic-ble/src/badgemagic_ble``. The
integration ships its own copy in ``custom_components/badgemagic/badgemagic_ble``
so Home Assistant needs no requirements: nothing is installed from PyPI or
downloaded when Home Assistant starts.

    python3 scripts/sync_library.py          # update the bundled copy
    python3 scripts/sync_library.py --check  # exit 1 if the copy is out of date

Uses only the standard library.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "badgemagic-ble" / "src" / "badgemagic_ble"
TARGET = ROOT / "custom_components" / "badgemagic" / "badgemagic_ble"
NOTICE_NAME = "BUNDLED.md"
NOTICE = """# Bundled copy of badgemagic_ble

Do not edit the files in this folder. They are copied from
`badgemagic-ble/src/badgemagic_ble/` by `scripts/sync_library.py`, and CI fails if
they differ. Change the library there, then run:

```bash
python3 scripts/sync_library.py
```
""".encode()


def _wanted(path: Path) -> bool:
    return "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}


def _files(base: Path) -> dict[str, bytes]:
    if not base.is_dir():
        return {}
    return {
        path.relative_to(base).as_posix(): path.read_bytes()
        for path in sorted(base.rglob("*"))
        if path.is_file() and _wanted(path.relative_to(base))
    }


def expected() -> dict[str, bytes]:
    files = _files(SOURCE)
    if not files:
        raise SystemExit(f"library source not found at {SOURCE}")
    files[NOTICE_NAME] = NOTICE
    return files


def differences() -> list[str]:
    want, have = expected(), _files(TARGET)
    problems = [f"missing: {name}" for name in sorted(want.keys() - have.keys())]
    problems += [f"extra: {name}" for name in sorted(have.keys() - want.keys())]
    problems += [f"differs: {name}" for name in sorted(want.keys() & have.keys()) if want[name] != have[name]]
    return problems


def sync() -> int:
    want = expected()
    have = _files(TARGET)
    changed = 0
    for name in sorted(have.keys() - want.keys()):
        (TARGET / name).unlink()
        changed += 1
    for name, data in want.items():
        if have.get(name) != data:
            dest = TARGET / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            changed += 1
    # Remove folders left empty by deleted files.
    for folder in sorted((p for p in TARGET.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="report differences instead of copying")
    args = parser.parse_args()
    rel = TARGET.relative_to(ROOT)
    if args.check:
        if problems := differences():
            print(f"{rel} is out of date with the library:", file=sys.stderr)
            for problem in problems:
                print(f"  {problem}", file=sys.stderr)
            print("Run: python3 scripts/sync_library.py", file=sys.stderr)
            return 1
        print(f"{rel} matches the library.")
        return 0
    changed = sync()
    print(f"{rel}: {changed} file(s) updated." if changed else f"{rel} already matches the library.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
