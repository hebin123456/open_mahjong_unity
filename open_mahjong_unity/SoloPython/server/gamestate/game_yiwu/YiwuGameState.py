"""义乌麻将（家庭房规 v1.0）引擎子类。

义乌麻将：136 张、十三张手牌，翻牌本身为财神（同种共 4 张）、白板顶替
翻出的那张——这与萧山家庭房规的翻财完全一致，故整套翻财/爆头/财飘/
承包栈直接复用 ``XiaoshanGameState``，仅覆盖义乌差异：

- 轮庄制：庄家和牌连庄，闲家和牌由庄家下家上庄，流局连庄。
- 牌墙剩 17 墩（34 张）流局；杭州默认墙尾 20 张（10 墩）。
- 含财神的牌型只能自摸、不可靠打出的财神点炮——引擎恒仅自摸，天然满足。
"""

from ..game_xiaoshan.XiaoshanGameState import XiaoshanGameState

_SUB_RULE = "yiwu/yiwu"
_RULE_VERSION = "yiwu-house-2026-v1"


class YiwuGameState(XiaoshanGameState):
    ROOM_RULE = "yiwu"
    SUB_RULE_ID = _SUB_RULE
    RULE_VERSION_ID = _RULE_VERSION

    def __init__(self, game_server=None, room_data=None, calculation_service=None,
                 db_manager=None, gamestate_id="yiwu-test"):
        super().__init__(game_server, room_data, calculation_service, db_manager, gamestate_id)
        # 义乌：剩 17 墩（34 张）流局；杭州默认墙尾 20 张（10 墩）。
        self.dead_wall_count = 34

    @staticmethod
    def _default_room_data():
        return dict(room_id="yiwu-test", room_rule="yiwu", sub_rule=_SUB_RULE,
                    player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
                    round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=1)

    def _next_dealer_on_win(self, winner):
        # 轮庄制：庄家（旋转后座位 0）和牌连庄；闲家和牌则庄家下家（座位 1）上庄。
        return 0 if winner == 0 else 1
