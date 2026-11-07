"""Load and validate the TOML run configuration."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .periods import parse_quarter

MDRM_PATTERN = re.compile(r"^[A-Z0-9]{4}[A-Z0-9]{4}$")
FORMATS = ("xlsx", "csv")
MAX_WORKERS = 16


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Bank:
    name: str
    rssd_id: int
    fed_district: str | None = None


@dataclass(frozen=True)
class Field:
    mdrm: str
    label: str
    notes: str | None = None


@dataclass(frozen=True)
class Config:
    banks: tuple[Bank, ...]
    fields: tuple[Field, ...]
    start: str
    end: str = "latest"
    output_dir: Path = Path("output")
    output_name: str = "call_report"
    output_format: str = "xlsx"
    workers: int = 4
    requests_per_hour: float = 2400


def load_config(path: Path, **overrides: Any) -> Config:
    """Read ``path`` and apply non-None ``overrides`` (from the command line)."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from None

    run = raw.get("run", {})
    output = raw.get("output", {})
    settings = {
        "start": run.get("start"),
        "end": run.get("end", "latest"),
        "workers": run.get("workers", 4),
        "requests_per_hour": run.get("requests_per_hour", 2400),
        "output_dir": Path(output.get("dir", "output")),
        "output_name": output.get("name", "call_report"),
        "output_format": output.get("format", "xlsx"),
    }
    settings.update({k: v for k, v in overrides.items() if v is not None})

    config = Config(
        banks=tuple(_bank(b) for b in raw.get("banks", [])),
        fields=tuple(_field(f) for f in raw.get("fields", [])),
        **settings,
    )
    _validate(config)
    return config


def _bank(entry: dict[str, Any]) -> Bank:
    try:
        return Bank(str(entry["name"]), int(entry["rssd_id"]), entry.get("fed_district"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid [[banks]] entry {entry}: needs name and integer rssd_id") from exc


def _field(entry: dict[str, Any]) -> Field:
    try:
        return Field(str(entry["mdrm"]).strip().upper(), str(entry["label"]), entry.get("notes"))
    except KeyError as exc:
        raise ConfigError(f"Invalid [[fields]] entry {entry}: needs mdrm and label") from exc


def _validate(config: Config) -> None:
    if not config.banks:
        raise ConfigError("Config needs at least one [[banks]] entry")
    if not config.fields:
        raise ConfigError("Config needs at least one [[fields]] entry")
    if not config.start:
        raise ConfigError('Set [run] start (e.g. "2012Q1") or pass --start')
    for period in (config.start, config.end):
        if period.strip().lower() != "latest":
            try:
                parse_quarter(period)
            except ValueError as exc:
                raise ConfigError(str(exc)) from None
    _unique("rssd_id", [b.rssd_id for b in config.banks])
    _unique("mdrm", [f.mdrm for f in config.fields])
    for f in config.fields:
        if not MDRM_PATTERN.match(f.mdrm):
            raise ConfigError(f"{f.mdrm!r} is not an 8-character MDRM code (e.g. RCFD2170)")
    if config.output_format not in FORMATS:
        raise ConfigError(f"output format must be one of {FORMATS}")
    if not 1 <= config.workers <= MAX_WORKERS:
        raise ConfigError(f"workers must be between 1 and {MAX_WORKERS}")
    if not 0 < config.requests_per_hour <= 2500:
        raise ConfigError("requests_per_hour must be between 1 and 2500 (the FFIEC limit)")
    if not re.fullmatch(r"[\w.-]+", config.output_name):
        raise ConfigError("output name may contain only letters, digits, '_', '-' and '.'")


def _unique(name: str, values: list[Any]) -> None:
    seen = set()
    for value in values:
        if value in seen:
            raise ConfigError(f"Duplicate {name} in config: {value}")
        seen.add(value)
