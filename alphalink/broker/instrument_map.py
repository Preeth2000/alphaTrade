"""yfinance ticker → T212 instrument_ticker resolution.

Priority: overrides.yaml config → SQLite cache → T212 instruments API best-match.
Fails loud if unresolvable.
"""
from __future__ import annotations

from typing import Optional

from alphalink.broker.t212_client import T212Client
from alphalink.store.repos import InstrumentCacheRepo, SectorCacheRepo


class InstrumentMap:
    def __init__(
        self,
        t212: T212Client,
        cache_repo: InstrumentCacheRepo,
        static_overrides: dict[str, str],
        sector_repo: Optional[SectorCacheRepo] = None,
    ) -> None:
        self._t212 = t212
        self._cache = cache_repo
        self._static = static_overrides
        self._sector_repo = sector_repo

    def resolve(self, yf_ticker: str) -> str:
        if yf_ticker in self._static:
            return self._static[yf_ticker]

        cached = self._cache.get(yf_ticker)
        if cached:
            return cached.t212_ticker

        t212_ticker = self._api_resolve(yf_ticker)
        self._cache.put(yf_ticker, t212_ticker)

        # Opportunistically populate sector cache on first resolution
        if self._sector_repo is not None:
            from alphalink.risk.sector import fetch_sector
            fetch_sector(yf_ticker, self._sector_repo)

        return t212_ticker

    def _api_resolve(self, yf_ticker: str) -> str:
        instruments = self._t212.get_instruments()
        upper = yf_ticker.upper()
        for inst in instruments:
            ticker = inst.get("ticker", "")
            short = inst.get("shortName", "")
            if ticker.upper().startswith(upper):
                return ticker
            if short.upper() == upper:
                return ticker

        raise RuntimeError(
            f"Cannot resolve {yf_ticker!r} to a T212 instrument_ticker. "
            f"Add it to overrides.yaml under models.<run_name>.t212_ticker."
        )
