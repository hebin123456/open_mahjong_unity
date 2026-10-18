"""房间级机器人水平档位；杭州/萧山 bot 决策读取，国标引擎暂不感知。

easy：切牌带随机性（前三候选随机）、吃碰门槛放宽；
medium：默认牌效策略（现状）；
hard：吃碰按「鸣牌并打出一手后的进张数」严格决策，只做真正推进听牌的鸣牌。
"""

BOT_LEVELS = ("easy", "medium", "hard")
DEFAULT_BOT_LEVEL = "medium"


def normalize_bot_level(level) -> str:
    return level if isinstance(level, str) and level in BOT_LEVELS else DEFAULT_BOT_LEVEL
