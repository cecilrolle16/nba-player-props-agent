"""Shared labels for pipeline stages that do not calculate a result."""

from enum import Enum


class StageState(str, Enum):
    NOT_IMPLEMENTED = "not_implemented"
    UNAVAILABLE = "unavailable"
    SYNTHETIC = "synthetic"
    CALCULATED = "calculated"


class ValueOrigin(str, Enum):
    NOT_IMPLEMENTED = "not_implemented"
    SYNTHETIC = "synthetic"
    OBSERVED = "observed"


class CalibrationStatus(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    UNCALIBRATED = "uncalibrated"
