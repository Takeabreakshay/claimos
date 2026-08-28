"""ClaimOS - global constants, paths, and enums.

Single home for the reproducibility seed, filesystem paths, and the fixed
vocabulary (lanes, claim types, severities, ...) referenced across the build.

RULES this file exists to enforce (see CLAUDE.md §3):
  * SEED = 42 everywhere. Every random draw in the project seeds off this.
  * Lanes / claim types / severities are FIXED by CLAUDE.md §1 - do not rename
    or invent new ones here.
  * No magic numbers in logic files: distributions live in
    ``config/distributions.yaml`` and thresholds in ``config/thresholds.yaml``.
    This module only holds the seed, paths, enums, and config loaders.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# --------------------------------------------------------------------------- #
# Reproducibility (CLAUDE.md §3 rule 4)
# --------------------------------------------------------------------------- #
SEED: int = 42

# --------------------------------------------------------------------------- #
# Paths - resolved relative to the repo root so commands work from anywhere.
# repo root = parent of the ``src`` package directory.
# --------------------------------------------------------------------------- #
SRC_DIR: Path = Path(__file__).resolve().parent
ROOT_DIR: Path = SRC_DIR.parent

CONFIG_DIR: Path = ROOT_DIR / "config"
DATA_DIR: Path = ROOT_DIR / "data"
SYNTH_DIR: Path = DATA_DIR / "synth"
MODELS_DIR: Path = ROOT_DIR / "models"
REPORTS_DIR: Path = ROOT_DIR / "reports"

DISTRIBUTIONS_YAML: Path = CONFIG_DIR / "distributions.yaml"
THRESHOLDS_YAML: Path = CONFIG_DIR / "thresholds.yaml"

# Canonical dataset artifact names.
CLAIMS_PARQUET: Path = SYNTH_DIR / "claims.parquet"
CLAIMS_CSV: Path = SYNTH_DIR / "claims.csv"
SAMPLE_CSV: Path = SYNTH_DIR / "sample_100.csv"


# --------------------------------------------------------------------------- #
# Fixed vocabulary (CLAUDE.md §1 - DO NOT rename / reinvent)
# --------------------------------------------------------------------------- #
class Lane(str, Enum):
    """The three execution lanes (CLAUDE.md §1.1)."""

    TOUCHLESS = "lane1_touchless"  # straight-through, no human
    ASSISTED = "lane2_assisted"  # AI-prepared file, officer approves (default)
    INVESTIGATIVE = "lane3_investigative"  # surveyor + fraud investigator


class ClaimType(str, Enum):
    """Motor claim types (CLAUDE.md §5.1)."""

    OD = "OD"  # own damage
    TP = "TP"  # third party
    THEFT_TOTAL = "theft_total"  # theft / total loss


class Severity(str, Enum):
    """Incident severity (CLAUDE.md §5.3). theft_total is always TOTAL."""

    MINOR = "minor"
    MODERATE = "moderate"
    SEVERE = "severe"
    TOTAL = "total"


class FraudType(str, Enum):
    """Fraud typology given a claim is fraudulent (CLAUDE.md §5.2)."""

    DOCUMENT_FALSIFICATION = "document_falsification"
    STAGED_OR_INFLATED = "staged_or_inflated"
    OTHER = "other"


class PolicyStatus(str, Enum):
    ACTIVE = "active"
    LAPSED = "lapsed"


class GarageType(str, Enum):
    NETWORK = "network"
    NON_NETWORK = "non_network"


class Geo(str, Enum):
    METRO = "metro"
    URBAN = "urban"
    RURAL = "rural"


class SurveyorVerdict(str, Enum):
    APPROVE = "approve"
    PARTIAL = "partial"
    REJECT = "reject"


# --------------------------------------------------------------------------- #
# Config loaders - the ONLY sanctioned way to pull tunables into logic files.
# --------------------------------------------------------------------------- #
def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_distributions() -> dict[str, Any]:
    """Return parsed ``config/distributions.yaml`` (synthetic-data params)."""
    return _load_yaml(DISTRIBUTIONS_YAML)


def load_thresholds() -> dict[str, Any]:
    """Return parsed ``config/thresholds.yaml`` (routing thresholds)."""
    return _load_yaml(THRESHOLDS_YAML)
