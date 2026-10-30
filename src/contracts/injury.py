"""Point-in-time injury report."""

from typing import Annotated

from pydantic import AfterValidator

from contracts.common import (
    UTCDateTime,
    InjuryStatus,
    PlayerId,
    SourceId,
    StrictModel,
    TeamId,
    require_sources,
)


class InjuryReport(StrictModel):
    canonical_player_id: PlayerId
    canonical_team_id: TeamId
    status: InjuryStatus
    detail: str
    reported_at: UTCDateTime
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
