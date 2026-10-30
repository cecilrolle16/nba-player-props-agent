"""Player-to-team membership."""

from typing import Annotated

from pydantic import AfterValidator, Field

from contracts.common import (
    UTCDateTime,
    PlayerId,
    SourceId,
    StrictModel,
    TeamId,
    require_sources,
)


class RosterEntry(StrictModel):
    canonical_player_id: PlayerId
    canonical_team_id: TeamId
    season: str = Field(pattern=r"^\d{4}-\d{2}$")
    display_name: str = Field(min_length=1)
    active: bool
    available_at: UTCDateTime
    source_ids: Annotated[tuple[SourceId, ...], AfterValidator(require_sources)]
