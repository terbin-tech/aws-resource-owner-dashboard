import json
from pathlib import Path

import pytest

from dashboard.config import (
    Aws,
    Config,
    ConfigError,
    load_config,
    load_settings,
    per_region,
)

REPO = Path(__file__).resolve().parents[1]
GOOD = {
    "regions": ["us-east-1", "eu-central-1"],
    "max_age_days": 4,
    "lookback_days": 90,
}


def _settings(tmp_path, **changes):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(GOOD | changes))
    return str(path)


def test_committed_settings_file_has_the_course_defaults():
    assert load_settings(REPO / "settings.json") == GOOD
    assert load_config(settings_path=REPO / "settings.json") == Config()


def test_settings_and_local_file_combine(tmp_path):
    local = tmp_path / "dashboard.toml"
    local.write_text('roster_path = "roster.csv"\n[cohort_groups]\n"DV_FT" = "DV_FT"\n')
    cfg = load_config(
        str(local), settings_path=_settings(tmp_path, regions=["eu-central-1"], max_age_days=7)
    )
    assert cfg.regions == ("eu-central-1",)
    assert (cfg.max_age_days, cfg.lookback_days) == (7, 90)
    assert cfg.cohort_groups == {"DV_FT": "DV_FT"}
    assert cfg.roster_path == "roster.csv"


def test_local_file_is_optional(tmp_path):
    cfg = load_config(None, settings_path=_settings(tmp_path))
    assert cfg.cohort_groups == {}
    assert cfg.roster_path is None


def test_local_file_rejects_unknown_keys(tmp_path):
    local = tmp_path / "dashboard.toml"
    local.write_text('regions = ["us-east-1"]\nmax_age_days = 4\ncolour = "red"\n')
    with pytest.raises(ConfigError) as err:
        load_config(str(local), settings_path=_settings(tmp_path))
    assert "unknown keys colour, max_age_days, regions" in str(err.value)


@pytest.mark.parametrize(
    "change",
    [{"lookback_days": None}, {"extra": 1}],
    ids=["missing", "extra"],
)
def test_settings_must_have_exactly_the_three_keys(tmp_path, change):
    data = {k: v for k, v in (GOOD | change).items() if v is not None}
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ConfigError, match="exactly the keys"):
        load_settings(path)


@pytest.mark.parametrize(
    "regions",
    [[], ["us-east-1", "us-east-1"], ["EU-Central-1"], ["eu-central"], [42], ["eu-central-١"]],
)
def test_rejects_bad_region_lists(tmp_path, regions):
    with pytest.raises(ConfigError):
        load_config(settings_path=_settings(tmp_path, regions=regions))


@pytest.mark.parametrize(
    "change",
    [
        {"lookback_days": 0},
        {"lookback_days": 91},
        {"max_age_days": True},
        {"max_age_days": 0},
    ],
)
def test_rejects_bad_numbers(tmp_path, change):
    with pytest.raises(ConfigError):
        load_config(settings_path=_settings(tmp_path, **change))


def test_per_region_returns_one_result_per_region():
    assert per_region(["a", "b"], str.upper) == {"a": "A", "b": "B"}


def test_aws_creates_each_client_once():
    calls = []

    class Session:
        def client(self, service, region_name, config):
            calls.append((service, region_name))
            return object()

    aws = Aws(Session())
    assert aws.client("ec2", "us-east-1") is aws.client("ec2", "us-east-1")
    assert calls == [("ec2", "us-east-1")]
