"""Shared loaders for synthetic fixtures."""

import copy
import json
from pathlib import Path

from contracts.analysis import AnalysisSubject
from rules.catalog import RuleCatalog, load_rules

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def subject_from(payload: dict) -> AnalysisSubject:
    return AnalysisSubject.model_validate(copy.deepcopy(payload))


def load_subject(name: str) -> AnalysisSubject:
    return subject_from(load_json(name))


def catalog() -> RuleCatalog:
    return load_rules(ROOT / "config" / "custom-rules.json")
