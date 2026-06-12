"""Tests for alphaTrade.data.provider_verify."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from alphaTrade.data.provider_verify import (
    _verify_polygon_key,
    _verify_yfinance,
    verify_provider_credentials,
)


# ---------------------------------------------------------------------------
# verify_provider_credentials — dispatch
# ---------------------------------------------------------------------------

class TestVerifyProviderCredentialsDispatch:
    def _settings(self, provider: str, polygon_api_key: str = "test-key") -> MagicMock:
        s = MagicMock()
        s.data_provider = provider
        s.polygon_api_key = polygon_api_key
        return s

    def test_dispatches_to_polygon(self):
        settings = self._settings("polygon", polygon_api_key="pk-abc")
        with patch(
            "alphaTrade.data.provider_verify._verify_polygon_key",
            return_value=(True, {"valid": True}),
        ) as mock_fn:
            ok, result = verify_provider_credentials(settings)
        mock_fn.assert_called_once_with("pk-abc")
        assert ok is True
        assert result["valid"] is True

    def test_dispatches_to_yfinance(self):
        settings = self._settings("yfinance")
        with patch(
            "alphaTrade.data.provider_verify._verify_yfinance",
            return_value=(True, {"valid": True}),
        ) as mock_fn:
            ok, result = verify_provider_credentials(settings)
        mock_fn.assert_called_once()
        assert ok is True

    def test_unknown_provider_returns_false(self):
        settings = self._settings("unknown_provider")
        ok, result = verify_provider_credentials(settings)
        assert ok is False
        assert result["valid"] is False
        assert "unknown_provider" in result["error"]
        assert result["provider"] == "unknown_provider"


# ---------------------------------------------------------------------------
# _verify_polygon_key
# ---------------------------------------------------------------------------

class TestVerifyPolygonKey:
    def _mock_response(self, status_code: int, reason_phrase: str = "OK") -> MagicMock:
        resp = MagicMock()
        resp.status_code = status_code
        resp.reason_phrase = reason_phrase
        return resp

    def test_returns_true_on_200(self):
        with patch("httpx.get", return_value=self._mock_response(200)):
            ok, result = _verify_polygon_key("good-key")
        assert ok is True
        assert result["valid"] is True

    def test_returns_false_on_401(self):
        with patch("httpx.get", return_value=self._mock_response(401, "Unauthorized")):
            ok, result = _verify_polygon_key("bad-key")
        assert ok is False
        assert result["valid"] is False
        assert "401" in result["error"]

    def test_returns_false_on_404(self):
        with patch("httpx.get", return_value=self._mock_response(404, "Not Found")):
            ok, result = _verify_polygon_key("bad-key")
        assert ok is False
        assert result["valid"] is False
        assert "404" in result["error"]

    def test_returns_false_on_network_error(self):
        with patch("httpx.get", side_effect=httpx.ConnectError("connection refused")):
            ok, result = _verify_polygon_key("any-key")
        assert ok is False
        assert result["valid"] is False
        assert result["error"] != ""


# ---------------------------------------------------------------------------
# _verify_yfinance
# ---------------------------------------------------------------------------

class TestVerifyYfinance:
    def test_returns_true_when_data_returned(self):
        import pandas as pd
        mock_df = pd.DataFrame({"Close": [100.0, 101.0]})
        with patch("yfinance.download", return_value=mock_df):
            ok, result = _verify_yfinance()
        assert ok is True
        assert result["valid"] is True

    def test_returns_false_when_empty_dataframe(self):
        import pandas as pd
        with patch("yfinance.download", return_value=pd.DataFrame()):
            ok, result = _verify_yfinance()
        assert ok is False
        assert result["valid"] is False
        assert "no data" in result["error"]

    def test_returns_false_on_exception(self):
        with patch("yfinance.download", side_effect=RuntimeError("network error")):
            ok, result = _verify_yfinance()
        assert ok is False
        assert result["valid"] is False
        assert "network error" in result["error"]
