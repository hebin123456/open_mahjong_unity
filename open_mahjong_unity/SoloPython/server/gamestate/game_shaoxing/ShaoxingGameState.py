"""绍兴麻将（家庭房规 v1.0）引擎子类。

整套动作/流程/协议/结算栈复用 ``XiaoshanGameState``（最终是杭州引擎栈），
仅覆盖绍兴差异：

- 财神翻牌加一：开局翻出一张指示牌，同色序号加一即为财神——万/筒/索按
  1-9 循环（翻五万则六万为财神，九转一），风牌东→南→西→北循环，字牌
  中→发→白→中。翻出的指示牌移出牌墙，同种四张全为财神（绍兴「财神共
  4 个」）；白板没有特殊身份，作为普通牌参与吃碰杠。
- 倍率封顶默认 8 倍（「三番到底」的家规化），可在建房时另行选择。
- 其余（爆头、财飘、杠后和牌、老庄、三摊承包、仅自摸）与萧山家庭房规一致。
"""

from ..game_xiaoshan.XiaoshanGameState import XiaoshanGameState
from ...game_calculation.xiaoshan import rules as xs
from ...game_calculation.hangzhou.rules import WinContext

_SUB_RULE = "shaoxing/shaoxing"
_RULE_VERSION = "shaoxing-house-2026-v1"
DEFAULT_TOTAL_CAP = 8  # 「三番到底」的家规化封顶

_DRAGON_NEXT = {45: 47, 47: 46, 46: 45}  # 中→发→白→中


def _next_indicator_tile(tile: int) -> int:
    """翻牌加一：万/筒/索 1-9 循环（九转一）；风牌东→南→西→北循环；字牌中→发→白→中。"""
    if tile < 40:
        return tile + 1 if tile % 10 < 9 else tile - 8
    if tile < 45:
        return 41 + (tile - 41 + 1) % 4
    return _DRAGON_NEXT[tile]


class ShaoxingGameState(XiaoshanGameState):
    ROOM_RULE = "shaoxing"
    SUB_RULE_ID = _SUB_RULE
    RULE_VERSION_ID = _RULE_VERSION

    def __init__(self, game_server=None, room_data=None, calculation_service=None,
                 db_manager=None, gamestate_id="shaoxing-test"):
        super().__init__(game_server, room_data, calculation_service, db_manager, gamestate_id)

    @staticmethod
    def _normalize_config(raw):
        config = xs.normalize_config(raw)
        # 绍兴默认 8 倍封顶；建房显式传 total_cap 时以建房选择为准。
        if "total_cap" not in (raw or {}):
            config["total_cap"] = DEFAULT_TOTAL_CAP
        return config

    @staticmethod
    def _default_room_data():
        return dict(room_id="shaoxing-test", room_rule="shaoxing", sub_rule=_SUB_RULE,
                    player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
                    round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=1)

    # ---- 财神：翻牌加一 -------------------------------------------------
    def initialize_round(self, *, wall=None):
        super().initialize_round(wall=wall)
        # 翻出指示牌（移出牌墙），同色序号加一为财神，同种四张全为财神；
        # 白板无特殊身份，不参与顶替映射。
        self.joker_indicator = self.tiles_list.pop(0)
        self.jokers = (_next_indicator_tile(self.joker_indicator),)
        self.joker_natural_map = ()
        self.joker_physical_limits = ((self.jokers[0], 4),)

    @property
    def _scoring_joker(self):
        """计分/听牌用的万能牌：翻牌加一后的财神，而非翻出的指示牌。"""
        return self.jokers[0] if self.jokers else self.joker_indicator

    def waiting_tiles_for(self, hand, melds, physicals=()):
        return xs.waiting_tiles(hand, xs.meld_frames(melds, physicals), self._scoring_joker)

    def score_ten_winds_for(self, hand, discard_history, melds, had_meld_action=False):
        return xs.score_ten_winds(hand, discard_history, melds,
                                  had_meld_action=had_meld_action, joker=self._scoring_joker,
                                  total_cap=self.total_cap)

    def score_win(self, index, source, tile):
        p = self.player_list[index]
        if source == "ten_winds":
            return self.score_ten_winds_for(p.hand_tiles, p.discard_origin_tiles,
                                            p.combination_tiles, p.had_meld_action)
        if source != "self_draw":
            return None
        return xs.evaluate_win(p.hand_tiles, self._meld_frames(p), tile,
            WinContext(self_drawn=True, after_kong=p.after_kong,
                       kong_concealed=p.kong_concealed,
                       cai_piao_count=p.cai_piao_count if p.drawn_after_cai else 0,
                       pre_draw_tiles=tuple(p.pre_draw_tiles)),
            joker=self._scoring_joker, total_cap=self.total_cap,
            baotou_required=self.baotou_required)
