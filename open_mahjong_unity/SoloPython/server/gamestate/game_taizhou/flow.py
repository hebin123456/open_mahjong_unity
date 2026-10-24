"""台州流程：吃牌时白板只能顶替“得”的本牌组成顺子。"""

from ..game_hangzhou.actions import CHOWS, claim_tiles, empty_actions
from ..game_hangzhou.flow import HandFlow
from ..game_hangzhou.state_machine import Phase as P
from ...game_calculation.taizhou.rules import NUMBERS, WHITE


class TaizhouHandFlow(HandFlow):
    def begin_turn(self, index, discard_only=False):
        # 过胡不胡只锁同一轮；轮到自己摸牌后恢复。
        self.player_list[index].discard_win_lockout_tiles.clear()
        return super().begin_turn(index, discard_only)

    def _has_chi_tile(self, hand, tile):
        joker = self.jokers[0]
        if tile == joker:
            return joker != WHITE and WHITE in hand
        return tile in hand

    def _claim_options(self, actor, tile):
        actions = empty_actions()
        jokers = tuple(self.jokers)
        if tile in jokers or len(self.tiles_list) <= self.dead_wall_count:
            return actions
        for index, p in enumerate(self.player_list):
            if index == actor or self.is_cai_locked(index):
                continue
            if self.allow_discard_win and self.can_win(index, "discard", tile):
                actions[index].append("hu")
            count = p.hand_tiles.count(tile)
            if count >= 2:
                actions[index].append("peng")
            if count >= 3 and len(self.tiles_list) >= self._wall_floor(True):
                actions[index].append("gang")
            if index == (actor + 1) % 4 and tile in NUMBERS:
                for action in CHOWS:
                    needed = claim_tiles(action, tile)
                    if all(t in NUMBERS and t // 10 == tile // 10
                           and self._has_chi_tile(p.hand_tiles, t) for t in needed):
                        actions[index].append(action)
            if actions[index]:
                actions[index].append("pass")
                if self.sync_tactical_enabled():
                    actions[index].append("force_pass")
        return actions

    def _after_discard(self, index, tile):
        if len(self.tiles_list) <= self.dead_wall_count:
            return self.end_draw()
        return self._window(P.RESPONSE, index, tile, self._claim_options(index, tile))

    def _response(self, window, responses):
        actor, tile = window["player"], window["tile"]
        winners = [i for i, data in responses.items() if data["action_type"] == "hu"]
        if winners:
            winner = min(winners, key=lambda i: (i - actor) % 4)
            return self.settle_win(winner, "discard", tile, payer=actor)
        for index, data in responses.items():
            if "hu" in window["actions"].get(index, []) and data["action_type"] != "hu":
                self.player_list[index].discard_win_lockout_tiles.add(tile)
        claims = [i for i, data in responses.items() if data["action_type"] not in ("pass", "force_pass")]
        if not claims:
            return self.draw_for((actor + 1) % 4)
        index = min(claims, key=lambda i: (responses[i]["action_type"] in CHOWS, (i - actor) % 4))
        action, p = responses[index]["action_type"], self.player_list[index]
        joker = self.jokers[0]
        if action in CHOWS:
            needed = claim_tiles(action, tile)
            held = []
            for t in needed:
                if t == joker:
                    if joker == WHITE or WHITE not in p.hand_tiles:
                        raise ValueError("白板不能这样顶替得")
                    held.append(WHITE)
                elif t in p.hand_tiles:
                    held.append(t)
                else:
                    raise ValueError("手牌不足")
            for t in held:
                p.hand_tiles.remove(t)
            logical = sorted(needed + [tile])
            code = f"s{logical[1]}"
            mask = [1, tile] + [value for t in held for value in (0, t)]
            p.chi_count += 1
        else:
            needed = [tile] * (3 if action == "gang" else 2)
            if tile in self.jokers or any(p.hand_tiles.count(t) < needed.count(t) for t in set(needed)):
                raise ValueError("得不能吃碰杠或手牌不足")
            for t in needed:
                p.hand_tiles.remove(t)
            tiles = sorted(needed + [tile])
            code = ("g" if action == "gang" else "k") + str(tile)
            marked = {1: len(tiles) - 1, 2: 1, 3: 0}[(actor - index) % 4]
            mask = [value for n, t in enumerate(tiles) for value in (1 if n == marked else 0, t)]
            if action == "gang":
                p.kongs_this_round += 1
                self.dead_wall_count = 14 + 4 * sum(q.kongs_this_round for q in self.player_list)
                p.kong_concealed = False
        p.combination_tiles.append(code)
        p.combination_mask.append(mask)
        p.has_draw_slot = p.after_kong = False
        p.pre_draw_tiles = []
        p.draw_kind = ""
        self.player_list[actor].discard_tiles.pop()
        self.current_player_index = index
        self._owner_operation(index, meld=True)
        self._event(action, index, tile=tile, meld_code=code, combination_mask=mask, cut_from_player=actor)
        return self.draw_for(index, replacement=True) if action == "gang" else self.begin_turn(index, discard_only=True)

    def _event(self, action, actor, **fields):
        if action in ("angang", "jiagang") and 0 <= actor < len(self.player_list):
            self.dead_wall_count = 14 + 4 * sum(q.kongs_this_round for q in self.player_list)
        super()._event(action, actor, **fields)
