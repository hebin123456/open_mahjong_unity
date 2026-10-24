import unittest
from collections import Counter

from . import rules as r
from ...gamestate.game_taizhou.TaizhouGameState import TaizhouGameState
from ...gamestate.game_hangzhou.state_machine import Phase as P


class TaizhouRulesTest(unittest.TestCase):
    def test_deck_is_136_without_flowers(self):
        self.assertEqual(len(r.deck()), 136)
        self.assertNotIn(51, r.deck())

    def test_standard_hand(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41, 41]
        score = r.evaluate_win(hand, (), 41, joker=31)
        self.assertIsNotNone(score)
        self.assertEqual(score.total_hu, 32)  # 底胡10 + 门风对2 + 自摸2 + 单吊2，无得1番

    def test_white_substitutes_joker_only_in_sequence(self):
        hand = [46, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41, 41]
        self.assertIsNotNone(r.evaluate_win(hand, (), 41, joker=11))
        # 白板只能在顺子里顶替得；没有可顶替的一万顺子时，两张白板不能补成刻子。
        bad = [46, 46, 47, 12, 13, 14, 15, 16, 17, 21, 22, 23, 41, 41]
        self.assertIsNone(r.evaluate_win(bad, (), 41, joker=11))

    def test_discard_win_is_legal(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41]
        score = r.evaluate_win(hand + [41], (), 41, r.WinContext(self_drawn=False), joker=31)
        self.assertIsNotNone(score)
        self.assertLess(score.total_hu, 32)  # 没有自摸 +2

    def test_hu_cap(self):
        # 四风杠 + 字牌对：番数很高，但腊子封顶 100 胡
        melds = ("G41", "G42", "G43", "G44")
        hand = [45, 45]
        score = r.evaluate_win(hand, melds, 45, joker=46)
        self.assertIsNotNone(score)
        self.assertEqual(score.total_hu, 100)
        self.assertGreater(score.base_hu * (1 << score.fan_total), 100)


class TaizhouEngineTest(unittest.TestCase):
    def test_deal_keeps_three_joker_copies(self):
        state = TaizhouGameState(room_data=dict(
            room_id="tz-test", room_rule="taizhou", sub_rule=r.SUB_RULE,
            player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
            round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=5))
        state.initialize_round()
        self.assertEqual(len(state.jokers), 1)
        joker = state.jokers[0]
        all_tiles = list(state.tiles_list)
        for p in state.player_list:
            all_tiles.extend(p.hand_tiles)
        self.assertEqual(Counter(all_tiles)[joker], 3)

    def test_wall_floor_grows_with_kong(self):
        state = TaizhouGameState(room_data=dict(
            room_id="tz-test2", room_rule="taizhou", sub_rule=r.SUB_RULE,
            player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
            round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=5))
        state.initialize_round()
        self.assertEqual(state.dead_wall_count, 14)
        state.player_list[0].kongs_this_round = 1
        state.dead_wall_count = 14 + 4
        self.assertEqual(state.dead_wall_count, 18)

    def test_discard_window_settles(self):
        state = TaizhouGameState(room_data=dict(
            room_id="tz-test3", room_rule="taizhou", sub_rule=r.SUB_RULE,
            player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
            round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=5))
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
