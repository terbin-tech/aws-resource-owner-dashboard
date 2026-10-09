"""Build the Lambda bundle: pinned wheels + src/dashboard -> build/lambda.zip.

    python scripts/build_lambda.py      # needs PyPI access; never touches AWS

Reproducible: sorted entries, fixed timestamps and modes, no bytecode. The same sources
give the same zip, so Terraform only redeploys the function when something changed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "lambda"
ZIP = ROOT / "build" / "lambda.zip"
REQUIREMENTS = ROOT / "requirements-lambda.txt"
FIXED_TIME = (1980, 1, 1, 0, 0, 0)  # the earliest time a zip entry can hold


def install_deps(target: Path) -> None:
    subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--require-hashes",
            "--no-deps",
            "--no-compile",
            "--only-binary=:all:",
            "--platform",
            "manylinux2014_aarch64",
            "--python-version",
            "3.14",
            "--implementation",
            "cp",
            "--target",
            str(target),
            "-r",
            str(REQUIREMENTS),
        ],
        check=True,
    )


def copy_package(src: Path, target: Path) -> None:
    shutil.copytree(
        src, target / "dashboard", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )


def make_zip(src: Path, dest: Path) -> None:
    files = sorted(p for p in src.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    with zipfile.ZipFile(dest, "w") as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(src).as_posix(), FIXED_TIME)
            info.external_attr = 0o644 << 16
            zf.writestr(
                info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )


def main() -> int:
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir(parents=True)
    install_deps(BUILD)
    copy_package(ROOT / "src" / "dashboard", BUILD)
    make_zip(BUILD, ZIP)
    print(f"wrote {ZIP.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
