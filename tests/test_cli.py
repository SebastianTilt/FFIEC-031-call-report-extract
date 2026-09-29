from pathlib import Path

from callreport.cli import EXIT_ERROR, main

EXAMPLE = Path(__file__).parents[1] / "config" / "large_banks.toml"


def test_missing_config_exits_with_error(tmp_path, caplog):
    assert main(["run", "--config", str(tmp_path / "nope.toml")]) == EXIT_ERROR
    assert "Config file not found" in caplog.text


def test_missing_credentials_exits_with_error(monkeypatch, caplog):
    monkeypatch.delenv("FFIEC_USERNAME", raising=False)
    monkeypatch.delenv("FFIEC_BEARER_TOKEN", raising=False)
    assert main(["run", "--config", str(EXAMPLE)]) == EXIT_ERROR
    assert "FFIEC_BEARER_TOKEN" in caplog.text


def test_invalid_period_override_exits_with_error(monkeypatch, caplog, valid_token):
    monkeypatch.setenv("FFIEC_USERNAME", "user1")
    monkeypatch.setenv("FFIEC_BEARER_TOKEN", valid_token)
    assert main(["run", "--config", str(EXAMPLE), "--start", "2012Q5", "--workers", "1"]) == EXIT_ERROR
    assert valid_token not in caplog.text
