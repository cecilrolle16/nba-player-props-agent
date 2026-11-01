"""Settlement shape. Grading is not implemented.

`unit_profit` is fixed null. That is a temporary restriction, not a claim
that settlement never has a price. A later grader may replace it.

Push and void are different. A push is a played stat equal to the line, and
the stake is returned. A void is a cancelled wager, including a game that is
not played or a bet the book voids. A void is not a push and is not a loss.
This contract does not decide which outcome applies.
"""

from enum import Enum
from typing import Literal

from pydantic import Field

from contracts.common import (
    GameId,
    Market,
    PlayerId,
    SettlementId,
    Side,
    SnapshotId,
    StrictModel,
)


class SettlementOutcome(str, Enum):
    WIN = "win"
    LOSS = "loss"
    PUSH = "push"
    VOID = "void"


class SettlementRecord(StrictModel):
    settlement_id: SettlementId
    pregame_snapshot_id: SnapshotId
    canonical_player_id: PlayerId
    canonical_game_id: GameId
    market: Market
    side: Side
    line: float = Field(gt=0, allow_inf_nan=False)
    outcome: SettlementOutcome | None = None
    stat: int | None = Field(default=None, ge=0)
    unit_profit: None = None
    graded: Literal[False] = False
    rules_version: str = Field(min_length=1)
    model_version: Literal["none"]
    log_snapshot_id: SnapshotId | None = None
