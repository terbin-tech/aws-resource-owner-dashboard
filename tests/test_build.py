import os
import re
import tomllib
import zipfile
from dataclasses import replace
from pathlib import Path

from dashboard.config import Config, config_from_env
from scripts import build_lambda
from scripts.local_lambda_run import lambda_env

ROOT = Path(__file__).resolve().parents[1]


def _lambda_requirements() -> list[str]:
    text = (ROOT / "requirements-lambda.txt").read_text().replace("\\\n", " ")
    return [
        " ".join(line.split())
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_every_lambda_requirement_is_pinned_with_a_hash():
    lines = _lambda_requirements()
    assert lines
    for line in lines:
        assert re.fullmatch(r"[a-z0-9._-]+==[0-9A-Za-z.+-]+( --hash=sha256:[0-9a-f]{64})+", line)


def test_lambda_requirements_pin_the_same_boto3_as_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    pins = dict(d.split("==") for d in pyproject["project"]["dependencies"])
    bundle = dict(line.split(" ")[0].split("==") for line in _lambda_requirements())
    assert bundle["boto3"] == pins["boto3"]
    assert bundle["botocore"] == pins["botocore"]


def test_zip_is_reproducible(tmp_path):
    src = tmp_path / "src"
    (src / "dashboard").mkdir(parents=True)
    (src / "dashboard" / "handler.py").write_text("x = 1\n")
    (src / "dashboard" / "data.json").write_text("{}")
    build_lambda.make_zip(src, tmp_path / "a.zip")
    os.utime(src / "dashboard" / "handler.py", (0, 0))  # a fresh checkout changes mtimes
    build_lambda.make_zip(src, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()
    assert zipfile.ZipFile(tmp_path / "a.zip").namelist() == [
        "dashboard/data.json",
        "dashboard/handler.py",
    ]


def test_copy_package_skips_bytecode(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "handler.py").write_text("")
    (pkg / "__pycache__" / "handler.cpython-314.pyc").write_bytes(b"x")
    build_lambda.copy_package(pkg, tmp_path / "out")
    found = sorted(
        p.relative_to(tmp_path / "out").as_posix() for p in (tmp_path / "out").rglob("*")
    )
    assert found == ["dashboard", "dashboard/handler.py"]


def test_local_run_env_round_trips_through_config_from_env():
    cfg = Config(cohort_groups={"DV_FT": "DV_FT"})
    assert config_from_env(lambda_env(cfg, "example-report-bucket")) == replace(
        cfg, bucket="example-report-bucket"
    )
