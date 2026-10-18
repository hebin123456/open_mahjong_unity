"""萧山流程覆盖：吃碰杠按 natural 身份扣实体牌，组合码记逻辑身份。

杭州流程按实体牌直接 ``hand_tiles.remove(t)``；萧山白板顶替本牌后，扣牌
必须先经 natural 映射挑选实体（真实牌优先、财神实体永不消耗），掩码保留
实体编码供计分层校验。
"""

from ..game_hangzhou.actions import CHOWS
from ..game_hangzhou.flow import HandFlow
from ..game_hangzhou.state_machine import Phase as P
from ...game_calculation.xiaoshan import rules as xs
from .actions import CHOW_OFFSETS, claim_actions, kong_tiles, physical_for, turn_actions


class XiaoshanHandFlow(HandFlow):
    def begin_turn(self, index, discard_only=False):
        self.current_player_index = index
        self.hand_action_is_gang_draw[index] = self.player_list[index].after_kong
        return self._window(P.DISCARD_ONLY if discard_only else P.TURN, index,
                            actions=turn_actions(self, index, discard_only))

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
            if type(tile) is not int or tile not in kong_tiles(self, index, action):
                raise ValueError("不能对指定牌开杠")

    def _after_discard(self, index, tile):
        if len(self.tiles_list) <= 20:
            return self.end_draw()
        return self._window(P.RESPONSE, index, tile, claim_actions(self, index, tile))

    def _response(self, window, responses):
        actor, tile = window["player"], window["tile"]
        claims = [i for i, data in responses.items() if data["action_type"] not in ("pass", "force_pass")]
        if not claims:
            return self.draw_for((actor + 1) % 4)
        index = min(claims, key=lambda i: (responses[i]["action_type"] in CHOWS, (i - actor) % 4))
        action, p = responses[index]["action_type"], self.player_list[index]
        joker = self.joker_indicator
        logical = xs.natural(tile, joker)
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

    def _self_kong(self, index, action, tile):
        p = self.player_list[index]
        joker = self.joker_indicator
        drawn = p.has_draw_slot and p.hand_tiles[-1] == tile
        if action == "angang":
            logical = xs.natural(tile, joker)
            held = physical_for(p.hand_tiles, [logical] * 4, joker)
            if held is None:
                raise ValueError("暗杠手牌不足")
            for t in held:
                p.hand_tiles.remove(t)
            code, mask = f"G{logical}", [value for t in held for value in (2, t)]
            p.combination_tiles.append(code)
            p.combination_mask.append(mask)
        else:
            p.hand_tiles.remove(tile)
            logical = xs.natural(tile, joker)
            position = p.combination_tiles.index(f"k{logical}")
            code, mask = f"g{logical}", list(p.combination_mask[position])
            called = next(i for i in range(0, len(mask), 2) if mask[i] == 1)
            mask[called:called] = [3, tile]
            p.combination_tiles[position], p.combination_mask[position] = code, mask
        p.hand_tiles.sort()
        p.has_draw_slot = False
        self._owner_operation(index, meld=True)
        self._event(action, index, tile=tile, meld_code=code, combination_mask=mask, is_mo_gang=drawn)
        # 只有自摸和牌：没有杠上被抢的响应窗口。
        return self.draw_for(index, replacement=True)
