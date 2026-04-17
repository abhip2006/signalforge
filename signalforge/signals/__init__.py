"""Signal source adapters — each implements SignalSource.collect()."""
from signalforge.signals.ashby import AshbySource
from signalforge.signals.base import SignalSource, SourceContext
from signalforge.signals.exa import ExaSource
from signalforge.signals.github_activity import GitHubActivitySource
from signalforge.signals.greenhouse import GreenhouseSource
from signalforge.signals.lever import LeverSource
from signalforge.signals.news_rss import NewsRSSSource
from signalforge.signals.sec_edgar import SecEdgarSource

REGISTRY: dict[str, type[SignalSource]] = {
    "greenhouse": GreenhouseSource,
    "ashby": AshbySource,
    "lever": LeverSource,
    "github": GitHubActivitySource,
    "sec_edgar": SecEdgarSource,
    "news_rss": NewsRSSSource,
    "exa": ExaSource,
}

__all__ = [
    "SignalSource",
    "SourceContext",
    "REGISTRY",
    "GreenhouseSource",
    "AshbySource",
    "LeverSource",
    "GitHubActivitySource",
    "SecEdgarSource",
    "NewsRSSSource",
    "ExaSource",
]
