"""Build a DataProvider from Settings."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from alphaTrade.config import Settings
    from alphaTrade.data.provider import DataProvider


def build_data_provider(settings: "Settings") -> "DataProvider":
    if settings.data_provider == "polygon":
        from alphaTrade.data.polygon_provider import PolygonProvider
        return PolygonProvider(api_key=settings.polygon_api_key)
    from alphaTrade.data.yfinance_provider import YFinanceProvider
    return YFinanceProvider()
