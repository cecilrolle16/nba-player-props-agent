"""Research-only decisions. Live recommendations and stakes stay off."""

from decision.policy import DEFAULT_RESEARCH_POLICY, research_decision

__all__ = ["DEFAULT_RESEARCH_POLICY", "research_decision"]
