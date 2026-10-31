"""Provider adapter interface."""

from ingestion.adapters.base import FetchResult, ProviderAdapter
from ingestion.adapters.nba_api_logs import NbaApiPlayerLogAdapter
from ingestion.adapters.odds_api_markets import OddsApiMarketAdapter

__all__ = ["FetchResult", "NbaApiPlayerLogAdapter", "OddsApiMarketAdapter", "ProviderAdapter"]
