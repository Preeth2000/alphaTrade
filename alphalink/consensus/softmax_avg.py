"""Softmax-averaged consensus across models for the same ticker.

Single model: trivially equivalent to argmax(logits).
Multiple models: average softmax probabilities, then argmax.
"""
from __future__ import annotations

import numpy as np

CLASS_NAMES = ["BUY", "SELL", "HOLD"]


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - np.max(x))
    return e / e.sum()


def consensus(model_logits: list[np.ndarray]) -> str:
    """Return BUY | SELL | HOLD signal from one or more logit vectors (shape (3,))."""
    if not model_logits:
        return "HOLD"
    probs = np.mean([_softmax(l) for l in model_logits], axis=0)
    return CLASS_NAMES[int(np.argmax(probs))]


def consensus_by_ticker(
    ticker_logits: dict[str, list[np.ndarray]]
) -> dict[str, str]:
    """Map {ticker: [logits, ...]} → {ticker: signal}."""
    return {ticker: consensus(logits) for ticker, logits in ticker_logits.items()}
