"""义乌流程：翻牌财神 + 白板顶替，支持点炮、抢杠和单/双飘牌墙。"""

from collections import Counter

from ..game_hangzhou.actions import CHOWS, empty_actions, legal_cuts
from ..game_hangzhou.flow import HandFlow
from ..game_hangzhou.state_machine import Phase as P
from ..game_xiaoshan.actions import CHOW_OFFSETS, natural, physical_for
from ...game_calculation.hangzhou.rules import NUMBERS


def _kong_tiles(state, index, kind):
    p = state.player_list[index]
    joker = state.joker_indicator
    if len(state.tiles_list) < state._wall_floor(True):
        return set()
    if kind == "angang":
        counts = Counter(natural(t, joker) for t in p.hand_tiles if t != joker)
        return {identity for identity, n in counts.items() if n == 4}
    return {natural(t, joker) for t in p.hand_tiles
            if t != joker and f"k{natural(t, joker)}" in p.combination_tiles}


def _turn_actions(state, index, discard_only=False):
    p = state.player_list[index]
    actions = []
    if not discard_only and state.can_win(index, "self_draw", p.hand_tiles[-1]):
        actions.append("hu_self")
    actions += [kind for kind in ("angang", "jiagang") if _kong_tiles(state, index, kind)]
    if legal_cuts(state, index):
        actions.append("cut")
    return {**empty_actions(), index: actions}


class YiwuHandFlow(HandFlow):
    def begin_turn(self, index, discard_only=False):
        self.current_player_index = index
        self.hand_action_is_gang_draw[index] = self.player_list[index].after_kong
        return self._window(P.DISCARD_ONLY if discard_only else P.TURN, index,
                            actions=_turn_actions(self, index, discard_only))

    def validate_response(self, index, data, offered):
        if type(index) is not int or index not in range(4) or not isinstance(data, dict):
            raise ValueError("非法行动座位或参数")
        action = data.get("action_type")
        if action not in offered:
            raise ValueError("行动不在当前窗口内")
        if action == "cut":
            self._cut_identity(index, data)
        elif action in ("angang", "jiagang"):
            tile = data.get("target_tile")
            if (type(tile) is not int
                    or natural(tile, self.joker_indicator) not in _kong_tiles(self, index, action)):
                raise ValueError("不能对指定牌开杠")
    def _claim_options(self, actor, tile):
        actions = empty_actions()
        if tile in self.jokers or len(self.tiles_list) <= self.dead_wall_count:
            return actions
        joker = self.joker_indicator
        logical = natural(tile, joker)
        for index, p in enumerate(self.player_list):
            if index == actor or self.is_cai_locked(index):
                continue
            if self.allow_discard_win and self.can_win(index, "discard", tile):
                actions[index].append("hu")
            if physical_for(p.hand_tiles, [logical] * 2, joker):
                actions[index].append("peng")
            if (len(self.tiles_list) >= self._wall_floor(True)
                    and physical_for(p.hand_tiles, [logical] * 3, joker)):
                actions[index].append("gang")
            if index == (actor + 1) % 4 and logical in NUMBERS:
                for action in CHOWS:
                    needed = [logical + n for n in CHOW_OFFSETS[action]]
                    if (all(t in NUMBERS and t // 10 == logical // 10 for t in needed)
                            and physical_for(p.hand_tiles, needed, joker)):
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

    def _turn(self, window, data):
        result = super()._turn(window, data)
        self._refresh_dead_wall()
        return result

    def _response(self, window, responses):
        actor, tile = window["player"], window["tile"]
        winners = [i for i, data in responses.items() if data["action_type"] == "hu"]
        if winners:
            winner = min(winners, key=lambda i: (i - actor) % 4)
            return self.settle_win(winner, "discard", tile, payer=actor)
        claims = [i for i, data in responses.items() if data["action_type"] not in ("pass", "force_pass")]
        if not claims:
            return self.draw_for((actor + 1) % 4)
        index = min(claims, key=lambda i: (responses[i]["action_type"] in CHOWS, (i - actor) % 4))
        action, p = responses[index]["action_type"], self.player_list[index]
        joker = self.joker_indicator
        logical = natural(tile, joker)
        if action in CHOWS:
            needed = [logical + n for n in CHOW_OFFSETS[action]]
        else:
            needed = [logical] * (3 if action == "gang" else 2)
        held = physical_for(p.hand_tiles, needed, joker)
        if tile in self.jokers or held is None:
            raise ValueError("财神不可吃碰杠或手牌不足")
        for t in held:
            p.hand_tiles.remove(t)
        if action in CHOWS:
            code = f"s{sorted(needed + [logical])[1]}"
            mask = [1, tile] + [value for t in held for value in (0, t)]
            p.chi_count += 1
        else:
            marked = {1: len(held), 2: 1, 3: 0}[(actor - index) % 4]
            physical = list(held)
            physical.insert(marked, tile)
            code = ("g" if action == "gang" else "k") + str(logical)
            mask = [value for n, t in enumerate(physical) for value in (1 if n == marked else 0, t)]
            if action == "gang":
                p.kongs_this_round += 1
                p.kong_concealed = False
        p.combination_tiles.append(code)
        p.combination_mask.append(mask)
        p.has_draw_slot = p.after_kong = False
        p.pre_draw_tiles = []
        p.draw_kind = ""
        self.player_list[actor].discard_tiles.pop()
        self.current_player_index = index
        self._owner_operation(index, meld=True)
        self._event(action, index, tile=tile, meld_code=code, combination_mask=mask,
                    cut_from_player=actor)
        self._refresh_dead_wall()
        return self.draw_for(index, replacement=True) if action == "gang" else self.begin_turn(index, discard_only=True)

    def _self_kong(self, index, action, tile):
        p = self.player_list[index]
        joker = self.joker_indicator
        logical = natural(tile, joker)
        if action == "jiagang" and self.allow_rob_kong:
            drawn = p.has_draw_slot and p.hand_tiles[-1] in {
                t for t in p.hand_tiles if t != joker and natural(t, joker) == logical}
            self.pending_kong = dict(actor=index, tile=logical, concealed=False,
                                     is_mo_gang=drawn, physical_tile=tile)
            return self._window(P.KONG, index, logical,
                                self.rob_claim_options(index, logical))
        if action == "angang":
            held = physical_for(p.hand_tiles, [logical] * 4, joker)
            if held is None:
                raise ValueError("暗杠手牌不足")
            drawn = p.has_draw_slot and p.hand_tiles[-1] in held
            for t in held:
                p.hand_tiles.remove(t)
            code, mask = f"G{logical}", [value for t in held for value in (2, t)]
            p.combination_tiles.append(code)
            p.combination_mask.append(mask)
        else:
            position = p.combination_tiles.index(f"k{logical}")
            physical = next((t for t in sorted(p.hand_tiles)
                             if t != joker and natural(t, joker) == logical), None)
            if physical is None:
                raise ValueError("加杠手牌不足")
            drawn = p.has_draw_slot and p.hand_tiles[-1] == physical
            p.hand_tiles.remove(physical)
            code, mask = f"g{logical}", list(p.combination_mask[position])
            called = next(i for i in range(0, len(mask), 2) if mask[i] == 1)
            mask[called:called] = [3, physical]
            p.combination_tiles[position], p.combination_mask[position] = code, mask
        p.hand_tiles.sort()
        p.has_draw_slot = False
        p.kongs_this_round += 1
        p.kong_concealed = action == "angang"
        self._owner_operation(index, meld=True)
        self._event(action, index, tile=logical, meld_code=code, combination_mask=mask,
                    is_mo_gang=drawn)
        self._refresh_dead_wall()
        return self.draw_for(index, replacement=True)

    def rob_claim_options(self, actor, tile):
        actions = empty_actions()
        if tile in self.jokers:
            return actions
        for index in range(4):
            if index == actor or self.is_cai_locked(index):
                continue
            if self.can_win(index, "rob_kong", tile):
                actions[index] = ["hu", "pass"]
        return actions

    def _kong_response(self, window, responses):
        pending = self.pending_kong
        if pending is None:
            raise ValueError("没有待处理的加杠")
        actor, tile = pending["actor"], pending["tile"]
        winners = [i for i, data in responses.items() if data["action_type"] == "hu"]
        if winners:
            winner = min(winners, key=lambda i: (i - actor) % 4)
            self.pending_kong = None
            return self.settle_win(winner, "rob_kong", tile, payer=actor)
        self.pending_kong = None
        p = self.player_list[actor]
        position = p.combination_tiles.index(f"k{tile}")
        physical = next((t for t in sorted(p.hand_tiles)
                         if t != self.joker_indicator and natural(t, self.joker_indicator) == tile), None)
        if physical is None:
            raise ValueError("加杠手牌不足")
        p.hand_tiles.remove(physical)
        code, mask = f"g{tile}", list(p.combination_mask[position])
        called = next(i for i in range(0, len(mask), 2) if mask[i] == 1)
        mask[called:called] = [3, physical]
        p.combination_tiles[position], p.combination_mask[position] = code, mask
        p.has_draw_slot = False
        p.kongs_this_round += 1
        p.kong_concealed = False
        self._owner_operation(actor, meld=True)
        self._event("jiagang", actor, tile=tile, meld_code=code, combination_mask=mask,
                    is_mo_gang=pending["is_mo_gang"])
        self._refresh_dead_wall()
        return self.draw_for(actor, replacement=True)

    def _refresh_dead_wall(self):
        active = max((p.cai_piao_count for p in self.player_list), default=0)
        self.dead_wall_count = 34 if active <= 0 else (14 if active == 1 else 6)
