from pathlib import Path

import pytest

from callreport.config import ConfigError, load_config

EXAMPLE = Path(__file__).parents[1] / "config" / "large_banks.toml"

MINIMAL = """
[run]
start = "2024Q1"
[[banks]]
name = "Bank A"
rssd_id = 1
[[fields]]
mdrm = "rcfd2170"
label = "Total assets"
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "c.toml"
    path.write_text(text)
    return path


def test_example_config_loads():
    config = load_config(EXAMPLE)
    assert len(config.banks) == 7
    assert len(config.fields) == 50
    assert config.end == "latest"


def test_defaults_normalization_and_overrides(tmp_path):
    config = load_config(write(tmp_path, MINIMAL), workers=2, output_format="csv", start=None)
    assert config.fields[0].mdrm == "RCFD2170"
    assert (config.workers, config.output_format, config.start) == (2, "csv", "2024Q1")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t + '[[banks]]\nname = "Dup"\nrssd_id = 1\n', "Duplicate rssd_id"),
        (lambda t: t.replace("rcfd2170", "RCFD21"), "MDRM"),
        (lambda t: t.replace('start = "2024Q1"', ""), "start"),
        (lambda t: t.replace("[run]", "[run]\nworkers = 50"), "workers"),
        (lambda t: t.replace("[run]", "[run]\nrequests_per_hour = 9000"), "2500"),
        (lambda t: t + '[output]\nname = "../escape"\n', "output name"),
        (lambda t: t.replace("rssd_id = 1", 'rssd_id = "abc"'), "integer rssd_id"),
        (lambda t: "not = [valid", "c.toml"),
    ],
)
def test_invalid_configs(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load_config(write(tmp_path, change(MINIMAL)))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
