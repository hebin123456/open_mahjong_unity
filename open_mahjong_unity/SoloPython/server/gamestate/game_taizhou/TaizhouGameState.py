"""台州麻将（黄岩打法·家庭房规）引擎子类。

黄岩麻将是台州麻将的基准打法：136 张、十三张手牌，可吃、可碰、可杠。
家庭房规沿用杭州 MIL 的白板财神与番种计分栈（爆头、财飘、七对、老庄、
三吃承包），差异与简化：

- 轮庄制：庄家和牌连庄，闲家和牌由庄家下家上庄，流局连庄；
  杭州引擎默认「赢家做庄」，这里覆盖为台州的轮庄制。
- 仅自摸：家庭房规暂不开放放铳（点炮），引擎能力所限，文案已注明。
- 其余与杭州 MIL 完全一致：白板财神、4 番封顶、墙尾 20 张（10 墩）流局。
"""

from ..game_hangzhou.HangzhouGameState import HangzhouGameState

_SUB_RULE = "taizhou/taizhou"
_RULE_VERSION = "taizhou-house-2026-v1"


class TaizhouGameState(HangzhouGameState):
    ROOM_RULE = "taizhou"
    SUB_RULE_ID = _SUB_RULE
    RULE_VERSION_ID = _RULE_VERSION

    @staticmethod
    def _default_room_data():
        return dict(room_id="taizhou-test", room_rule="taizhou", sub_rule=_SUB_RULE,
                    player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
                    round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=1)

    def _next_dealer_on_win(self, winner):
        # 轮庄制：庄家（旋转后座位 0）和牌连庄；闲家和牌则庄家下家（座位 1）上庄。
        return 0 if winner == 0 else 1
