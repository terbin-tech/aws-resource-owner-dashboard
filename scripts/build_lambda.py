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
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv not found: the build installs the wheels with uv")
    # uv writes no bytecode unless asked (--compile-bytecode), and the platform names the
    # Lambda arm64 runtime (uv pip install --help).
    subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [
            uv,
            "pip",
            "install",
            "--quiet",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.14",
            "--target",
            str(target),
            "-r",
            str(REQUIREMENTS),
        ],
        check=True,
    )


def drop_install_leftovers(target: Path) -> None:
    # What the install leaves that Lambda never runs: uv's lock on the folder, and console
    # scripts, whose first line names the local interpreter and so changes the zip.
    (target / ".lock").unlink(missing_ok=True)
    shutil.rmtree(target / "bin", ignore_errors=True)


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
    drop_install_leftovers(BUILD)
    copy_package(ROOT / "src" / "dashboard", BUILD)
    make_zip(BUILD, ZIP)
    print(f"wrote {ZIP.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
