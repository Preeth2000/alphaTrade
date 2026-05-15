"""E2E: two models for same stock → consensus path exercised."""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from alphaTrade.consensus.softmax_avg import consensus_by_ticker


def test_two_models_same_ticker_consensus():
    """Two models on AAPL; softmax avg decides signal."""
    # Model 1 slightly favours BUY, model 2 strongly favours BUY → should be BUY
    l1 = np.array([0.5, 0.3, 0.2])
    l2 = np.array([3.0, -1.0, 0.0])
    result = consensus_by_ticker({"AAPL": [l1, l2]})
    assert result["AAPL"] == "BUY"


def test_mixed_signals_averaged():
    """One weak BUY + one strong SELL → SELL wins."""
    l1 = np.array([0.1, -0.1, 0.0])
    l2 = np.array([-5.0, 10.0, -5.0])
    result = consensus_by_ticker({"AAPL": [l1, l2]})
    assert result["AAPL"] == "SELL"


def test_three_models_majority():
    """Three models: 2 BUY logits, 1 SELL logit → BUY wins."""
    buy_strong = np.array([4.0, -2.0, 0.0])
    buy_weak   = np.array([0.5, 0.3, 0.2])
    sell       = np.array([-1.0, 2.0, -1.0])
    result = consensus_by_ticker({"AAPL": [buy_strong, buy_weak, sell]})
    assert result["AAPL"] == "BUY"
