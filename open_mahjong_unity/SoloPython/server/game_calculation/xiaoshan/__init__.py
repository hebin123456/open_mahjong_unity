"""萧山麻将（杭州麻将拓展·家庭房规 v2.0）计分：番种倍率相乘。"""

from .rules import (
    BASE_SCORE, CLAIM_THRESHOLD, DEFAULT_DEALER_CAP, DEFAULT_TOTAL_CAP, JOKER,
    RULE_VERSION, SUB_RULE, Settlement, Transfer, XFan, XWinScore, evaluate_win,
    normalize_config, score_ten_winds, settle_win,
)

__all__ = [
    "BASE_SCORE", "CLAIM_THRESHOLD", "DEFAULT_DEALER_CAP", "DEFAULT_TOTAL_CAP",
    "JOKER", "RULE_VERSION", "SUB_RULE", "Settlement", "Transfer", "XFan",
    "XWinScore", "evaluate_win", "normalize_config", "score_ten_winds", "settle_win",
]
