"""台州麻将（黄岩基准）引擎。

136 张、十三张手牌、可点炮；翻出的牌本身为“得”，白板只在顺子中
顶替“得”的本牌。计分、听牌和结算全部走
``game_calculation.taizhou.rules``，不沿用杭州 MIL 番种表。
"""

import random
from collections import Counter
from dataclasses import replace

from ..game_hangzhou.HangzhouGameState import HangzhouGameState
from ..game_hangzhou.state_machine import Phase as P
from ...game_calculation.taizhou import rules as tz
from .flow import TaizhouHandFlow


class TaizhouGameState(TaizhouHandFlow, HangzhouGameState):
    ROOM_RULE = "taizhou"
    SUB_RULE_ID = tz.SUB_RULE
    RULE_VERSION_ID = tz.RULE_VERSION
    DEALER_STREAK_CAP = 1
    use_flowers = False
    allow_discard_win = True
    allow_rob_kong = True
    burn_on_kong = False

    def __init__(self, game_server=None, room_data=None, calculation_service=None,
                 db_manager=None, gamestate_id="taizhou-test"):
        super().__init__(game_server, room_data, calculation_service, db_manager, gamestate_id)
        self.cai_lock_on_discard = False
        self.dead_wall_count = 14
        self.joker_indicator = tz.WHITE
        self.jokers = (tz.WHITE,)

    @staticmethod
    def _normalize_config(raw):
        return tz.normalize_config(raw)

    @staticmethod
    def _default_room_data():
        return dict(room_id="taizhou-test", room_rule="taizhou", sub_rule=tz.SUB_RULE,
                    player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
                    round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=1)

    def initialize_round(self, *, wall=None):
        if self.machine.phase != P.START:
            raise RuntimeError("须先结束上局的确认阶段")
        self.reset_round_state()
        self._action_clocks, self._action_deadlines = {}, {}
        self.round_settlement = None
        self.round_changes = [0] * 4
        self.round_start_scores = [p.score for p in self.player_list]
        self.cai_discard_locks = set()
        self.burned_tiles, self.domain_events = [], []
        self.pending_kong = None
        self._recorded_hint_keys = {}
        self._prepared_hints = {}
        physical = tz.deck()
        rng = random.Random(self.round_random_seed)
        self.dice = [rng.randint(1, 6), rng.randint(1, 6)]
        if wall is None:
            rng.shuffle(physical)
            offset = ((sum(self.dice) - 1) % 4 * 34 + min(self.dice) * 2) % 136
            wall = physical[offset:] + physical[:offset]
        elif Counter(wall) != Counter(physical):
            raise ValueError("牌墙须为136张无花完整牌组")
        self.tiles_list = list(wall)
        for _ in range(3):
            for p in self.player_list:
                p.hand_tiles.extend(self.tiles_list[:4])
                del self.tiles_list[:4]
        final, self.tiles_list = self.tiles_list[:5], self.tiles_list[5:]
        for index, offset in enumerate((0, 1, 3, 4)):
            self.player_list[index].hand_tiles.append(final[offset])
            self.player_list[index].hand_tiles.sort()
        dealer = self.player_list[0]
        dealer.pre_draw_tiles = list(dealer.hand_tiles)
        dealer.hand_tiles.append(final[2])
        dealer.has_draw_slot = True
        dealer.draw_kind = "normal"
        self.dealer_index = self.current_player_index = 0
        self.joker_indicator = self.tiles_list.pop(0)
        self.jokers = (self.joker_indicator,)
        self.joker_natural_map = ((tz.WHITE, self.joker_indicator),) if self.joker_indicator != tz.WHITE else ()
        self.joker_physical_limits = ((self.joker_indicator, 3),)
        self.dead_wall_count = 14

    def score_ten_winds_for(self, hand, discard_history, melds, had_meld_action=False):
        return None

    def score_win(self, index, source, tile):
        p = self.player_list[index]
        if source not in ("self_draw", "discard", "rob_kong"):
            return None
        hand = list(p.hand_tiles)
        if source != "self_draw":
            hand.append(tile)
        context = tz.WinContext(
            self_drawn=source == "self_draw",
            after_kong=p.after_kong and source == "self_draw",
            rob_kong=source == "rob_kong",
            sea_bottom=len(self.tiles_list) <= self.dead_wall_count,
            seat_wind=41 + index)
        melds = tuple((code, tuple(mask[1::2]))
                      for code, mask in zip(p.combination_tiles, p.combination_mask))
        return tz.evaluate_win(hand, melds, tile, context, joker=self.jokers[0])

    def can_win(self, index, source, tile):
        p = self.player_list[index]
        if p.is_hu or source not in ("self_draw", "discard", "rob_kong"):
            return False
        if source == "self_draw" and not p.has_draw_slot:
            return False
        if source != "self_draw" and tile in p.discard_win_lockout_tiles:
            return False
        return self.score_win(index, source, tile) is not None

    def settle_win(self, winner, source, tile, payer=None):
        if self.round_settlement is not None or type(winner) is not int or winner not in range(4):
            raise ValueError("本局已结算或和家无效")
        p = self.player_list[winner]
        if source == "self_draw":
            if self.machine.phase != P.TURN or not p.has_draw_slot or not p.hand_tiles or p.hand_tiles[-1] != tile:
                raise ValueError("自摸牌无效")
        elif source == "discard":
            if self.machine.phase != P.RESPONSE or payer is None or payer == winner:
                raise ValueError("点炮时机无效")
        elif source == "rob_kong":
            if self.machine.phase != P.KONG or payer is None or payer == winner:
                raise ValueError("抢杠时机无效")
        else:
            raise ValueError("台州和牌来源无效")
        score = self.score_win(winner, source, tile)
        if score is None:
            raise ValueError("和牌资格不成立")
        score = replace(score, payer=payer)
        payment = tz.settle_win(score, winner, self.dealer_index)
        changes = list(payment.changes)
        entry = dict(winner=winner, source=source, tile=tile,
                     discarder=payer if source == "discard" else None,
                     kong_player=payer if source == "rob_kong" else None,
                     points=score.total_hu, fan_ids=list(score.fan_ids),
                     fan_names=score.fan_names, fan_details=score.as_dict(),
                     score_changes=changes, rule_version=self.rule_version,
                     multi_ron=False, recycle_discard=False, hu_order=1,
                     special=source)
        self.deferred_hu_settlements.append(entry)
        self.round_changes = changes
        self.round_settlement = dict(winner=winner, source=source, tile=tile,
                                     changes=changes, score=score.as_dict(),
                                     payment=payment.as_dict())
        p.is_hu, p.hu_order, p.has_draw_slot = True, 1, False
        self.hu_order_counter = 1
        self.next_dealer = 0 if winner == 0 else 1
        self._finish_round("win")
        self._event("hu_self" if source == "self_draw" else "hu", winner,
                    tile=tile, win_source=source)
        return self._window(P.END)

    def hangzhou_info(self, viewer=None):
        data = super().hangzhou_info(viewer)
        data["joker_indicator"] = self.joker_indicator
        data["joker_white_rule"] = "sequence_only"
        data["dead_wall_count"] = self.dead_wall_count
        data["seat_winds"] = [41 + i for i in range(4)]
        data["ten_winds_counts"] = [0] * 4
        data["cai_piao_counts"] = [0] * 4
        data["dealer_multiplier"] = 1
        return data
