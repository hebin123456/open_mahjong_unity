"""绍兴麻将（144 张花麻）引擎。

与杭州/萧山栈只共用传输、动作窗口和记录外壳；牌组、补花、财神、番数
和结算全部走 ``game_calculation.shaoxing.rules``，不再套用萧山倍率表。
"""

import random
from collections import Counter

from ..game_hangzhou.HangzhouGameState import HangzhouGameState
from ..game_hangzhou.state_machine import Phase as P
from ...game_calculation.shaoxing import rules as sx
from .player import ShaoxingPlayer

_CLAIM_ACTIONS = ("chi_left", "chi_mid", "chi_right", "peng", "gang")


class ShaoxingGameState(HangzhouGameState):
    ROOM_RULE = "shaoxing"
    SUB_RULE_ID = sx.SUB_RULE
    RULE_VERSION_ID = sx.RULE_VERSION
    DEALER_STREAK_CAP = 1
    use_flowers = True
    allow_discard_win = False
    allow_rob_kong = True
    burn_on_kong = False

    def __init__(self, game_server=None, room_data=None, calculation_service=None,
                 db_manager=None, gamestate_id="shaoxing-test"):
        super().__init__(game_server, room_data, calculation_service, db_manager, gamestate_id)
        self.player_list = [ShaoxingPlayer(**vars(p)) for p in self.player_list]
        self.cai_lock_on_discard = False
        self.dead_wall_count = 0
        self.joker_indicator = 0
        self.jokers = ()
        self.claim_from = [[0] * 4 for _ in range(4)]

    @staticmethod
    def _normalize_config(raw):
        return sx.normalize_config(raw)

    @staticmethod
    def _default_room_data():
        return dict(room_id="shaoxing-test", room_rule="shaoxing", sub_rule=sx.SUB_RULE,
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
        self.claim_from = [[0] * 4 for _ in range(4)]
        self._recorded_hint_keys = {}
        self._prepared_hints = {}
        physical = sx.deck()
        rng = random.Random(self.round_random_seed)
        self.dice = [rng.randint(1, 6), rng.randint(1, 6)]
        if wall is None:
            rng.shuffle(physical)
            offset = ((sum(self.dice) - 1) % 4 * 36 + min(self.dice) * 2) % 144
            wall = physical[offset:] + physical[:offset]
        elif Counter(wall) != Counter(physical):
            raise ValueError("牌墙须为144张、含中发白与八花的完整牌组")
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
        # 翻牌加一；翻到花牌则东风为财神。翻出的牌移出牌墙，不归任何玩家。
        self.joker_indicator = self.tiles_list.pop(0)
        self.jokers = (sx.next_joker(self.joker_indicator),)
        self.joker_natural_map = ()
        self.joker_physical_limits = ((self.jokers[0], 4),)
        self.dead_wall_count = 0

    def _replace_flowers(self, index):
        p = self.player_list[index]
        while True:
            flowers = sorted(t for t in p.hand_tiles if sx.is_flower(t))
            if not flowers:
                break
            flower = flowers[0]
            drawn = p.has_draw_slot and p.hand_tiles[-1] == flower
            p.hand_tiles.remove(flower)
            p.huapai_list.append(flower)
            self._event("buhua", index, tile=flower, buhua_tile=flower, is_mo_buhua=drawn)
            if len(self.tiles_list) < 1:
                return True
            replacement = self.tiles_list.pop()
            p.hand_tiles.append(replacement)
            p.hand_tiles.sort()
            p.has_draw_slot = True
            self._event("deal_buhua_tile", index, tile=replacement)
        if p.has_draw_slot:
            p.pre_draw_tiles = list(p.hand_tiles[:-1])
        return False

    def _before_turn(self, index):
        return self._replace_flowers(index)

    def score_ten_winds_for(self, hand, discard_history, melds, had_meld_action=False):
        return None

    def _is_tian_hu(self, winner):
        return (winner == self.dealer_index and not self.discard_log
                and not any(p.combination_tiles for p in self.player_list))

    def _is_di_hu(self, winner):
        return (winner != self.dealer_index
                and self.natural_draw_count.get(winner, 0) == 1
                and not any(p.combination_tiles for p in self.player_list))

    def score_win(self, index, source, tile):
        p = self.player_list[index]
        if source not in ("self_draw", "rob_kong"):
            return None
        hand = list(p.hand_tiles)
        pre_draw = tuple(p.pre_draw_tiles) if p.pre_draw_tiles else None
        if source == "rob_kong":
            hand.append(tile)
            pre_draw = tuple(p.hand_tiles)
        context = sx.WinContext(
            self_drawn=source == "self_draw",
            after_kong=p.after_kong and source == "self_draw",
            rob_kong=source == "rob_kong",
            tian_hu=self._is_tian_hu(index) if source == "self_draw" else False,
            di_hu=self._is_di_hu(index) if source == "self_draw" else False,
            cai_piao_count=p.cai_piao_count if p.drawn_after_cai else 0,
            pre_draw_tiles=pre_draw)
        return sx.evaluate_win(hand, p.combination_tiles, tile, context,
                               joker=self.jokers[0], flower_tiles=tuple(p.huapai_list))

    def can_win(self, index, source, tile):
        p = self.player_list[index]
        if p.is_hu or source not in ("self_draw", "rob_kong"):
            return False
        if source == "self_draw" and not p.has_draw_slot:
            return False
        return self.score_win(index, source, tile) is not None

    def _event(self, action, actor, **fields):
        if action in _CLAIM_ACTIONS:
            provider = fields.get("cut_from_player")
            if provider is not None and provider != actor:
                claimer_id = self.player_list[actor].original_player_index
                provider_id = self.player_list[provider].original_player_index
                self.claim_from[claimer_id][provider_id] += 1
        super()._event(action, actor, **fields)

    def _seat_claims(self):
        table = [[0] * 4 for _ in range(4)]
        for winner in range(4):
            winner_id = self.player_list[winner].original_player_index
            for other in range(4):
                other_id = self.player_list[other].original_player_index
                table[winner][other] = self.claim_from[winner_id][other_id]
        return table

    def settle_win(self, winner, source, tile, payer=None):
        if self.round_settlement is not None or type(winner) is not int or winner not in range(4):
            raise ValueError("本局已结算或和家无效")
        p = self.player_list[winner]
        if source == "self_draw":
            if self.machine.phase != P.TURN or not p.has_draw_slot or not p.hand_tiles or p.hand_tiles[-1] != tile:
                raise ValueError("自摸牌无效")
        elif source == "rob_kong":
            if self.machine.phase != P.KONG or payer is None or payer == winner:
                raise ValueError("抢杠时机无效")
        else:
            raise ValueError("绍兴只能自摸或抢杠和牌")
        score = self.score_win(winner, source, tile)
        if score is None:
            raise ValueError("和牌资格不成立")
        if source == "rob_kong":
            # 绍兴抢杠：被抢杠者独付三家分数，其余两家不承担。
            points = 3 * score.points
            changes = [0, 0, 0, 0]
            changes[payer] -= points
            changes[winner] += points
            payment = sx.Settlement(tuple(changes),
                                    (sx.Transfer(payer, winner, points, "抢杠"),),
                                    points, 1, score.points)
        else:
            payment = sx.settle_win(score, winner, self.dealer_index, self._seat_claims())
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
        self.next_dealer = winner
        self._finish_round("win")
        self._event("hu_self" if source == "self_draw" else "hu", winner,
                    tile=tile, win_source=source)
        return self._window(P.END)

    def hangzhou_info(self, viewer=None):
        data = super().hangzhou_info(viewer)
        data["joker_indicator"] = self.joker_indicator
        data["flower_counts"] = [len(p.huapai_list) for p in self.player_list]
        data["ten_winds_counts"] = [0] * 4
        data["dealer_multiplier"] = 1
        return data

    def _player_view(self, index, viewer):
        data = super()._player_view(index, viewer)
        data["huapai_list"] = list(self.player_list[index].huapai_list)
        return data

    def emit_visible_action_payloads(self, event, *, reveal_final=False):
        payloads = super().emit_visible_action_payloads(event, reveal_final=reveal_final)
        if event.get("action") == "buhua":
            for payload in payloads:
                info = payload.get("do_action_info")
                if info is not None:
                    info["buhua_tile"] = event.get("tile")
                    info["is_mo_buhua"] = bool(event.get("is_mo_buhua"))
        return payloads

    def record_visible_action(self, event):
        if not self._has_active_round_record():
            return
        from ..public import game_record_manager as record
        action, actor, tile = event.get("action"), event.get("player"), event.get("tile")
        if action == "buhua":
            record.player_action_record_buhua(self, tile, actor, bool(event.get("is_mo_buhua")))
            return
        if action == "deal_buhua_tile":
            record.player_action_record_deal(self, tile, "bd", actor)
            return
        super().record_visible_action(event)
