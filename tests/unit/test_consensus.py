"""Tests for softmax-averaged consensus."""
import numpy as np
import pytest

from alphaTrade.consensus.softmax_avg import consensus, consensus_by_ticker


def test_single_model_buy():
    logits = np.array([2.0, -1.0, 0.0])  # BUY dominant
    assert consensus([logits]) == "BUY"


def test_single_model_sell():
    logits = np.array([-1.0, 3.0, 0.0])
    assert consensus([logits]) == "SELL"


def test_single_model_hold():
    logits = np.array([0.0, 0.0, 5.0])
    assert consensus([logits]) == "HOLD"


def test_two_models_agree():
    l1 = np.array([3.0, -1.0, 0.0])
    l2 = np.array([2.0, -0.5, 0.5])
    assert consensus([l1, l2]) == "BUY"


def test_two_models_disagree_dominant_sell():
    # One weakly BUYs, one strongly SELLs → SELL wins after softmax avg
    l1 = np.array([0.1, -0.1, 0.0])   # near uniform, slight BUY
    l2 = np.array([-5.0, 10.0, -5.0])  # strongly SELL
    assert consensus([l1, l2]) == "SELL"


def test_empty_defaults_hold():
    assert consensus([]) == "HOLD"


def test_consensus_by_ticker():
    result = consensus_by_ticker({
        "AAPL": [np.array([3.0, -1.0, 0.0])],
        "MSFT": [np.array([-1.0, 3.0, 0.0])],
    })
    assert result["AAPL"] == "BUY"
    assert result["MSFT"] == "SELL"
