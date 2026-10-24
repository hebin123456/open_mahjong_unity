"""义乌麻将（翻牌财神、财神头/单双飘、点炮）引擎。

使用 ``game_calculation.yiwu.rules`` 的 5/7 底分与 ×2/×4/×8/×16
倍数，不再套用萧山的番种倍率表。
"""

import random
from collections import Counter

from ..game_hangzhou.HangzhouGameState import HangzhouGameState
from ..game_hangzhou.state_machine import Phase as P
from ...game_calculation.yiwu import rules as yw
from .flow import YiwuHandFlow


class YiwuGameState(YiwuHandFlow, HangzhouGameState):
    ROOM_RULE = "yiwu"
    SUB_RULE_ID = yw.SUB_RULE
    RULE_VERSION_ID = yw.RULE_VERSION
    DEALER_STREAK_CAP = 1
    use_flowers = False
    allow_discard_win = True
    allow_rob_kong = True
    burn_on_kong = False

    def __init__(self, game_server=None, room_data=None, calculation_service=None,
                 db_manager=None, gamestate_id="yiwu-test"):
        super().__init__(game_server, room_data, calculation_service, db_manager, gamestate_id)
        self.cai_lock_on_discard = False
        self.dead_wall_count = 34
        self.joker_indicator = yw.WHITE
        self.jokers = (yw.WHITE,)
        self.buy_in = bool(self.detailed_config.get("buy_in", False))

    @staticmethod
    def _normalize_config(raw):
        return yw.normalize_config(raw)

    @staticmethod
    def _default_room_data():
        return dict(room_id="yiwu-test", room_rule="yiwu", sub_rule=yw.SUB_RULE,
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
        physical = yw.deck()
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
        self.joker_natural_map = ((yw.WHITE, self.joker_indicator),) if self.joker_indicator != yw.WHITE else ()
        self.joker_physical_limits = ((self.joker_indicator, 3),)
        self.dead_wall_count = 34

    def _after_joker_discard(self, index, player):
        self._refresh_dead_wall()

    def score_ten_winds_for(self, hand, discard_history, melds, had_meld_action=False):
        return None

    def score_win(self, index, source, tile):
        p = self.player_list[index]
        if source not in ("self_draw", "discard", "rob_kong"):
            return None
        hand = list(p.hand_tiles)
        if source != "self_draw":
            hand.append(tile)
        context = yw.WinContext(
            self_drawn=source == "self_draw",
            after_kong=p.after_kong and source == "self_draw",
            rob_kong=source == "rob_kong",
            cai_piao_count=p.cai_piao_count if p.drawn_after_cai else 0,
            pre_draw_tiles=(tuple(p.pre_draw_tiles)
                            if source == "self_draw" and p.pre_draw_tiles else None))
        melds = tuple((code, tuple(mask[1::2]))
                      for code, mask in zip(p.combination_tiles, p.combination_mask))
        return yw.evaluate_win(hand, melds, tile, context,
                               joker=self.jokers[0], dealer=self.dealer_index,
                               winner=index, buy_in=self.buy_in)

    def can_win(self, index, source, tile):
        p = self.player_list[index]
        if p.is_hu or source not in ("self_draw", "discard", "rob_kong"):
            return False
        if source == "self_draw" and not p.has_draw_slot:
            return False
        # 单飘后其他人只能自摸（抢杠按自摸）；双飘后其他人只能开杆胡。
        active_piao = max((q.cai_piao_count for q in self.player_list if q.player_index != index),
                          default=0)
        if active_piao >= 1 and source == "discard":
            return False
        if active_piao >= 2:
            if source != "self_draw" or not p.after_kong:
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
            raise ValueError("义乌和牌来源无效")
        score = self.score_win(winner, source, tile)
        if score is None:
            raise ValueError("和牌资格不成立")
        payment = yw.settle_win(score, winner, self.dealer_index, buy_in=self.buy_in,
                                piao_counts=[q.cai_piao_count for q in self.player_list])
        changes = list(payment.changes)
        entry = dict(winner=winner, source=source, tile=tile,
                     discarder=payer if source == "discard" else None,
                     kong_player=payer if source == "rob_kong" else None,
                     points=score.points, fan_ids=list(score.fan_ids),
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
        data["joker_white_rule"] = "substitute"
        data["dead_wall_count"] = self.dead_wall_count
        data["buy_in"] = self.buy_in
        data["ten_winds_counts"] = [0] * 4
        data["dealer_multiplier"] = 1
        return data
