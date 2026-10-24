import unittest

from . import rules as r
from ...gamestate.game_yiwu.YiwuGameState import YiwuGameState
from ...gamestate.game_hangzhou.state_machine import Phase as P


class YiwuRulesTest(unittest.TestCase):
    def test_deck_is_136_without_flowers(self):
        self.assertEqual(len(r.deck()), 136)
        self.assertNotIn(51, r.deck())

    def test_plain_self_draw(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41, 41]
        score = r.evaluate_win(hand, (), 41, joker=31)
        self.assertEqual(score.multiplier, 2)
        self.assertEqual(score.base_score, 7)  # 庄家
        self.assertEqual(score.points, 14)

    def test_caishen_head(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 31, 41]
        score = r.evaluate_win(hand, (), 41, joker=31)
        self.assertEqual(score.multiplier, 4)
        self.assertEqual(score.fans[0].id, "caishen_head")

    def test_single_and_double_piao(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 31, 41]
        single = r.evaluate_win(hand, (), 41, r.WinContext(cai_piao_count=1), joker=31)
        double = r.evaluate_win(hand, (), 41, r.WinContext(cai_piao_count=2), joker=31)
        self.assertEqual(single.multiplier, 8)
        self.assertEqual(double.multiplier, 16)

    def test_two_jokers_must_self_draw(self):
        hand = [31, 31, 11, 12, 13, 14, 15, 16, 17, 18, 19, 41, 41]
        self.assertIsNone(r.evaluate_win(hand + [41], (), 41, r.WinContext(self_drawn=False), joker=31))

    def test_three_jokers_only_head_or_kong(self):
        # 三个财神的普通牌型也按财神头计，不能按平胡/自摸计。
        plain = [11, 12, 13, 14, 15, 16, 17, 18, 19, 31, 31, 31, 41, 41]
        score = r.evaluate_win(plain, (), 41, r.WinContext(), joker=31)
        self.assertIsNotNone(score)
        self.assertEqual(score.multiplier, 4)
        self.assertEqual(score.fans[0].id, "caishen_head")
        head = [11, 12, 13, 14, 15, 16, 41, 41, 31, 42, 42, 31, 31, 41]
        self.assertIsNotNone(r.evaluate_win(head, (), 41, r.WinContext(), joker=31))

    def test_thirteen_orphans(self):
        hand = [11, 14, 17, 21, 24, 27, 31, 34, 37, 41, 42, 43, 44, 45]
        score = r.evaluate_win(hand, (), 45, joker=46)
        self.assertEqual(score.shape, "shisan_bukao")

    def test_settlement_uses_winner_base(self):
        score = r.WinScore("standard", (r.Fan("self_draw", "自摸", 2),), 7, 2)
        settle = r.settle_win(score, 0, 0)
        self.assertEqual(settle.changes, (42, -14, -14, -14))


class YiwuEngineTest(unittest.TestCase):
    def _state(self):
        return YiwuGameState(room_data=dict(
            room_id="yw-test", room_rule="yiwu", sub_rule=r.SUB_RULE,
            player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
            round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=9))

    def test_deal_keeps_three_joker_copies(self):
        state = self._state()
        state.initialize_round()
        self.assertEqual(state.jokers, (state.joker_indicator,))
        all_tiles = list(state.tiles_list)
        for p in state.player_list:
            all_tiles.extend(p.hand_tiles)
        self.assertEqual(all_tiles.count(state.joker_indicator), 3)

    def test_piao_shrinks_dead_wall(self):
        state = self._state()
        state.initialize_round()
        self.assertEqual(state.dead_wall_count, 34)
        state.player_list[0].cai_piao_count = 1
        state._refresh_dead_wall()
        self.assertEqual(state.dead_wall_count, 14)
        state.player_list[1].cai_piao_count = 2
        state._refresh_dead_wall()
        self.assertEqual(state.dead_wall_count, 6)

    def test_discard_window_settles(self):
        state = self._state()
        state.initialize_round()
        state.jokers = (31,)
        state.machine.transition(P.TURN)
        state.player_list[1].hand_tiles = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41]
        window = state.open_action_window(dict(
            status="waiting_action_after_cut", player=0, tile=41,
            actions={0: [], 1: ["hu", "pass"]}))
        state.apply_action_results(window, {1: {"action_type": "hu"}})
        self.assertEqual(state.round_settlement["winner"], 1)
        self.assertEqual(state.round_settlement["source"], "discard")
        self.assertEqual(sum(state.round_changes), 0)


if __name__ == "__main__":
    unittest.main()
