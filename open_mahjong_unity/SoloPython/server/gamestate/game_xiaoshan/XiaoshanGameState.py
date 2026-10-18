"""萧山麻将（杭州麻将拓展·家庭房规 v1.3）引擎子类。

整套动作/流程/协议/结算栈复用 ``HangzhouGameState``，仅覆盖萧山差异：

- 财神翻出：四家抓完牌后翻出牌墙所剩的那张牌为财神，白板顶替该牌，二者皆可
  代替任意牌（供两人及以上受用）。
- 打出财神后他家仍可正常吃、碰、杠（``cai_lock_on_discard = False``）。
- 番种倍率相乘而非相加取 2^n（见 ``game_calculation/xiaoshan/rules.py``）。
- 承包：一位玩家从另一位玩家处累计吃、碰三次即形成承包关系（按牌手身份跨局累计）。
- 天胡通杀、地胡按封顶结算。
- 流局庄家留任且连庄倍数不递增。
"""

from dataclasses import replace

from ..game_hangzhou.HangzhouGameState import HangzhouGameState
from ..game_hangzhou.state_machine import Phase as P
from ...game_calculation.hangzhou.rules import JOKER, WinContext
from ...game_calculation.xiaoshan import rules as xs

_CLAIM_ACTIONS = ("chi_left", "chi_mid", "chi_right", "peng")


class XiaoshanGameState(HangzhouGameState):
    ROOM_RULE = "xiaoshan"
    SUB_RULE_ID = xs.SUB_RULE
    RULE_VERSION_ID = xs.RULE_VERSION
    DEALER_STREAK_CAP = xs.DEFAULT_DEALER_CAP

    def __init__(self, game_server=None, room_data=None, calculation_service=None,
                 db_manager=None, gamestate_id="xiaoshan-test"):
        super().__init__(game_server, room_data, calculation_service, db_manager, gamestate_id)
        # 萧山不锁出财神后他家的吃碰；并跨局累计承包关系（按牌手原始座位号）。
        self.cai_lock_on_discard = False
        self.joker_indicator = 0
        self.total_cap = self.detailed_config.get("total_cap", xs.DEFAULT_TOTAL_CAP)
        self.claim_from = [[0] * 4 for _ in range(4)]

    @staticmethod
    def _normalize_config(raw):
        return xs.normalize_config(raw)

    @staticmethod
    def _default_room_data():
        return dict(room_id="xiaoshan-test", room_rule="xiaoshan", sub_rule=xs.SUB_RULE,
                    player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
                    round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=1)

    # ---- 财神翻出 -------------------------------------------------------
    def initialize_round(self, *, wall=None):
        super().initialize_round(wall=wall)
        # 四家抓完牌后，翻出牌墙所剩的那张牌为财神；白板恒为财神，二者皆可替代。
        self.joker_indicator = self.tiles_list.pop(0)
        self.jokers = tuple(sorted({JOKER, self.joker_indicator}))

    # ---- 承包关系累计 ---------------------------------------------------
    def _event(self, action, actor, **fields):
        claim_from = getattr(self, "claim_from", None)
        if claim_from is not None and action in _CLAIM_ACTIONS:
            provider = fields.get("cut_from_player")
            if provider is not None and provider != actor:
                claimer_id = self.player_list[actor].original_player_index
                provider_id = self.player_list[provider].original_player_index
                claim_from[claimer_id][provider_id] += 1
        super()._event(action, actor, **fields)

    def _seat_claims(self):
        """把按牌手身份累计的承包次数换算到当前座次空间。"""
        table = [[0] * 4 for _ in range(4)]
        for winner in range(4):
            winner_id = self.player_list[winner].original_player_index
            for other in range(4):
                other_id = self.player_list[other].original_player_index
                table[winner][other] = self.claim_from[winner_id][other_id]
        return table

    # ---- 计分（倍率相乘） ----------------------------------------------
    def score_ten_winds_for(self, hand, discard_history, melds, had_meld_action=False):
        return xs.score_ten_winds(hand, discard_history, melds,
                                  had_meld_action=had_meld_action, jokers=self.jokers,
                                  total_cap=self.total_cap)

    def score_win(self, index, source, tile):
        p = self.player_list[index]
        if source == "ten_winds":
            return self.score_ten_winds_for(p.hand_tiles, p.discard_origin_tiles,
                                            p.combination_tiles, p.had_meld_action)
        if source != "self_draw":
            return None
        return xs.evaluate_win(p.hand_tiles, p.combination_tiles, tile,
            WinContext(self_drawn=True, after_kong=p.after_kong,
                       cai_piao_count=p.cai_piao_count if p.drawn_after_cai else 0,
                       pre_draw_tiles=tuple(p.pre_draw_tiles)),
            jokers=self.jokers, total_cap=self.total_cap)

    def _is_tian_hu(self, winner):
        return (winner == self.dealer_index and not self.discard_log
                and not any(p.combination_tiles for p in self.player_list))

    def _is_di_hu(self, winner):
        return (winner != self.dealer_index
                and self.natural_draw_count.get(winner, 0) == 1
                and not any(p.combination_tiles for p in self.player_list))

    # ---- 结算（承包 / 天胡通杀 / 地胡封顶） ------------------------------
    def settle_win(self, winner, source, tile):
        if self.round_settlement is not None or type(winner) is not int or winner not in range(4):
            raise ValueError("本局已结算或和家无效")
        p = self.player_list[winner]
        if winner != self.current_player_index:
            raise ValueError("和家不是当前行动者")
        if source == "self_draw":
            if self.machine.phase != P.TURN or not p.has_draw_slot or not p.hand_tiles or p.hand_tiles[-1] != tile:
                raise ValueError("自摸牌无效")
        elif source == "ten_winds":
            if self.machine.phase != P.TEN_WINDS or len(p.discard_origin_tiles) != 10 or p.discard_origin_tiles[-1] != tile:
                raise ValueError("未到十风宣告时机")
        else:
            raise ValueError("萧山只能自摸或十风和牌")
        quote = self.score_win(winner, source, tile)
        if quote is None:
            raise ValueError("和牌资格不成立")
        special = "self_draw"
        tian_hu = di_hu = False
        if source != "ten_winds":
            tian_hu = self._is_tian_hu(winner)
            di_hu = not tian_hu and self._is_di_hu(winner)
        display = quote
        if tian_hu:
            display = replace(quote, fans=tuple(quote.fans) + (xs.XFan("tian_hu", "天胡", 1),))
        elif di_hu:
            display = replace(quote, fans=tuple(quote.fans) + (xs.XFan("di_hu", "地胡", 1),))
        payment = xs.settle_win(display, winner, self.dealer_index, self.dealer_streak,
                                self._seat_claims(), base_score=xs.BASE_SCORE,
                                total_cap=self.total_cap, dealer_cap=self.DEALER_STREAK_CAP,
                                tian_hu=tian_hu, di_hu=di_hu,
                                current_scores=[q.score for q in self.player_list])
        changes = list(payment.changes)
        entry = dict(winner=winner, source=source, tile=tile if source == "self_draw" else 0,
                     discarder=None, kong_player=None, points=payment.base_points,
                     fan_ids=list(display.fan_ids), fan_names=display.fan_names,
                     fan_details=display.as_dict(), score_changes=changes,
                     rule_version=self.rule_version, multi_ron=False,
                     recycle_discard=False, hu_order=1, special=special)
        self.deferred_hu_settlements.append(entry)
        self.round_changes = changes
        self.round_settlement = dict(winner=winner, source=source, tile=tile,
                                     changes=changes, score=display.as_dict(),
                                     payment=payment.as_dict())
        p.is_hu, p.hu_order, p.has_draw_slot = True, 1, False
        self.hu_order_counter = 1
        self.next_dealer = winner
        self._finish_round("win")
        self._event("hu_self", winner, tile=entry["tile"], win_source=source)
        return self._window(P.END)

    # ---- 连庄：流局不递增，庄家留任 -------------------------------------
    def advance_round_after_ready(self):
        drawn = bool(self.round_settlement and self.round_settlement.get("source") == "draw")
        streak = self.dealer_streak
        super().advance_round_after_ready()
        if drawn:
            self.dealer_streak = streak

    # ---- 协议：额外下发翻出的财神与承包次数 ------------------------------
    def hangzhou_info(self, viewer=None):
        data = super().hangzhou_info(viewer)
        data["joker_indicator"] = self.joker_indicator
        data["claim_counts"] = [list(row) for row in self.claim_from]
        data["total_cap"] = self.total_cap
        return data
