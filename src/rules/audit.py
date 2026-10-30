"""Deterministic audit ids."""

from hashlib import sha256

from contracts.analysis import AdjustmentDecision, AnalysisSubject, AngleAudit, RuleAudit, RuleFiring
from contracts.common import AnalysisState
from rules.catalog import RuleCatalog


def make_audit(
    catalog: RuleCatalog,
    subject: AnalysisSubject,
    state: AnalysisState,
    reason: str,
    firings: tuple[RuleFiring, ...],
    angles: tuple[AngleAudit, ...],
    adjustments: tuple[AdjustmentDecision, ...],
) -> RuleAudit:
    draft = RuleAudit(
        audit_id="0" * 64,
        rules_version=catalog.rules_version,
        model_version=catalog.model_version,
        rules_fingerprint=catalog.source_sha256,
        as_of=subject.as_of,
        state=state,
        reason=reason,
        firings=firings,
        angles=angles,
        adjustments=adjustments,
    )
    body = draft.model_dump_json(exclude={"audit_id"})
    subject_body = subject.model_dump_json()
    digest = sha256(f"{body}|{subject_body}".encode("utf-8")).hexdigest()
    return draft.model_copy(update={"audit_id": digest})
