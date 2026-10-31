"""Provider adapter interface."""

from ingestion.adapters.base import FetchResult, ProviderAdapter
from ingestion.adapters.nba_api_logs import NbaApiPlayerLogAdapter

__all__ = ["FetchResult", "NbaApiPlayerLogAdapter", "ProviderAdapter"]
