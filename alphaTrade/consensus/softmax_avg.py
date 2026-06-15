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


def consensus(
    model_logits: list[np.ndarray],
    min_confidence: float = 0.0,
    min_margin: float = 0.0,
) -> str:
    """Return BUY | SELL | HOLD from one or more logit vectors (shape (3,)).

    min_confidence: winning class probability must exceed this threshold (e.g. 0.5).
    min_margin: winning class probability must exceed runner-up by at least this amount.
    Returns HOLD when neither gate is passed.
    """
    if not model_logits:
        return "HOLD"
    probs = np.mean([_softmax(v) for v in model_logits], axis=0)
    idx = int(np.argmax(probs))
    top_prob = float(probs[idx])

    if min_confidence > 0.0 and top_prob < min_confidence:
        return "HOLD"

    if min_margin > 0.0:
        sorted_probs = np.sort(probs)[::-1]
        margin = float(sorted_probs[0] - sorted_probs[1])
        if margin < min_margin:
            return "HOLD"

    return CLASS_NAMES[idx]


def check_model_gate(logits: np.ndarray, min_confidence: float, min_margin: float) -> bool:
    """Return True if this model's individual logit vector passes confidence + margin gates.

    Used to apply per-model override gates before multi-model softmax fusion.
    A return of False means this model's contribution should be excluded from the ticker
    consensus (equivalent to a HOLD vote from that model).
    """
    probs = _softmax(logits.ravel())
    sorted_probs = np.sort(probs)[::-1]
    if min_confidence > 0.0 and float(sorted_probs[0]) < min_confidence:
        return False
    if min_margin > 0.0 and len(sorted_probs) >= 2:
        if float(sorted_probs[0] - sorted_probs[1]) < min_margin:
            return False
    return True


def consensus_by_ticker(
    ticker_logits: dict[str, list[np.ndarray]],
    min_confidence: float = 0.0,
    min_margin: float = 0.0,
) -> dict[str, str]:
    """Map {ticker: [logits, ...]} → {ticker: signal}."""
    return {
        ticker: consensus(logits, min_confidence=min_confidence, min_margin=min_margin)
        for ticker, logits in ticker_logits.items()
    }
