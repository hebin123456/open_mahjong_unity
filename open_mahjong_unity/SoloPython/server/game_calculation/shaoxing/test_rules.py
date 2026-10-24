import unittest
from collections import Counter

from . import rules as r
from ...gamestate.game_shaoxing.ShaoxingGameState import ShaoxingGameState
from ...gamestate.game_hangzhou.state_machine import Phase as P


class ShaoxingRulesTest(unittest.TestCase):
    def test_deck_has_144_tiles_and_20_flowers(self):
        deck = r.deck()
        self.assertEqual(len(deck), 144)
        self.assertEqual(sum(1 for t in deck if r.is_flower(t)), 20)
        self.assertEqual(Counter(deck)[51], 1)
        self.assertEqual(Counter(deck)[45], 4)

    def test_joker_flip_wraps_and_flower_gives_east(self):
        self.assertEqual(r.next_joker(19), 11)
        self.assertEqual(r.next_joker(44), 41)
        self.assertEqual(r.next_joker(45), 41)
        self.assertEqual(r.next_joker(58), 41)

    def test_plain_hand_scores_flowers(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41, 41]
        score = r.evaluate_win(hand, (), 41, joker=31, flower_tiles=(51, 52))
        self.assertIsNotNone(score)
        self.assertEqual(score.fan_total, 0)
        self.assertEqual(score.total_flowers, 7)  # 底花5 + 两张花牌
        self.assertEqual(score.points, 7)

    def test_baotou_adds_one_fan(self):
        hand = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 31, 41]
        score = r.evaluate_win(hand, (), 41, joker=31, flower_tiles=(51,))
        self.assertIsNotNone(score)
        self.assertEqual(score.fan_total, 1)
        self.assertEqual(score.points, 7 * 2)

    def test_contract_payments(self):
        score = r.WinScore("standard", (), (), 0, fan_total=0)
        table = [[0] * 4 for _ in range(4)]
        table[0][1] = 3  # winner claimed three from seat 1
        table[2][0] = 3  # seat 2 claimed three from winner
        settle = r.settle_win(score, 0, 0, table)
        self.assertEqual(settle.changes[1], -15)  # 5 * 3
        self.assertEqual(settle.changes[2], -30)  # 5 * 6
        self.assertEqual(settle.changes[3], -5)
        self.assertEqual(sum(settle.changes), 0)


class ShaoxingEngineTest(unittest.TestCase):
    def _state(self):
        return ShaoxingGameState(room_data=dict(
            room_id="sx-test", room_rule="shaoxing", sub_rule=r.SUB_RULE,
            player_list=[101, 102, 103, 104], player_settings={}, game_round=1,
            round_timer=0, step_timer=0, room_type="custom", tips=False, random_seed=3))

    def test_opening_replaces_dealer_flowers(self):
        state = self._state()
        state.initialize_round()
        self.assertEqual(len(state.jokers), 1)
        self.assertTrue(state.joker_indicator in r.FLOWER_TILES or state.joker_indicator in r.NORMAL_TILES)
        state.open_action_window(state.opening_window())
        self.assertFalse(any(r.is_flower(t) for t in state.player_list[0].hand_tiles))
        self.assertEqual([len(p.hand_tiles) for p in state.player_list], [14, 13, 13, 13])

    def test_no_flowers_left_in_any_hand_after_first_turn(self):
        state = self._state()
        state.initialize_round()
        state.open_action_window(state.opening_window())
        # 依次替三家补花：直接把行动窗口推进到每家摸牌。
        for index in (1, 2, 3):
            state.draw_for(index)
            self.assertFalse(any(r.is_flower(t) for t in state.player_list[index].hand_tiles))

    def test_rob_kong_window_settles(self):
        state = self._state()
        state.initialize_round()
        state.jokers = (31,)
        state.machine.transition(P.TURN)
        state.player_list[1].hand_tiles = [11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 23, 41]
        state.player_list[1].huapai_list = [51, 52]
        state.pending_kong = dict(actor=0, tile=41, concealed=False, is_mo_gang=False)
        window = state.open_action_window(dict(
            status="waiting_action_qianggang", player=0, tile=41,
            actions={0: [], 1: ["hu", "pass"]}))
        state.apply_action_results(window, {1: {"action_type": "hu"}})
        self.assertEqual(state.round_settlement["winner"], 1)
        self.assertEqual(state.round_settlement["source"], "rob_kong")
        self.assertEqual(sum(state.round_changes), 0)


if __name__ == "__main__":
    unittest.main()
