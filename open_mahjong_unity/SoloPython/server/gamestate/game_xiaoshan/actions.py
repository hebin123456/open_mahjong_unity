"""萧山动作供给：副露报价按 natural 身份匹配实体牌。

与杭州的差异：白板顶替翻出的财神本牌，可参与吃碰杠；财神实体本身不可
吃碰杠、也不可作为手中的顶替实体被消耗。报价与校验沿用杭州动作词汇
（peng/gang/chi_*），组合码记逻辑身份，掩码保留实体牌。
"""

from collections import Counter

from ..game_hangzhou.actions import CHOWS, empty_actions, legal_cuts
from ...game_calculation.hangzhou.rules import JOKER, NUMBERS
from ...game_calculation.xiaoshan import rules as xs

CHOW_OFFSETS = {"chi_left": (-2, -1), "chi_mid": (-1, 1), "chi_right": (1, 2)}


def natural(tile, joker):
    return xs.natural(tile, joker)


def physical_for(hand, logical_tiles, joker):
    """按 natural 身份挑选手牌实体；真实牌优先于白板，财神实体永不选用。"""
    remaining = sorted(t for t in hand if t != joker)
    selected = []
    for logical in logical_tiles:
        tile = next((t for t in remaining if natural(t, joker) == logical), None)
        if tile is None:
            return None
        selected.append(tile)
        remaining.remove(tile)
    return selected


def turn_actions(state, index, discard_only=False):
    p = state.player_list[index]
    actions = []
    if not discard_only and state.can_win(index, "self_draw", p.hand_tiles[-1]):
        actions.append("hu_self")
    actions += [kind for kind in ("angang", "jiagang") if kong_tiles(state, index, kind)]
    if legal_cuts(state, index):
        actions.append("cut")
    return {**empty_actions(), index: actions}


def kong_tiles(state, index, kind):
    """杠候选按逻辑身份（本牌）下发：白板与真实本牌合并为同一身份。

    客户端选项、目标提交与验证全部使用逻辑身份；白板 4 张与「白板+本牌」
    混合都归一为本牌暗杠，避免客户端按物理牌计数漏出候选导致卡死。
    """
    p = state.player_list[index]
    joker = state.joker_indicator
    if kind == "angang":
        counts = Counter(natural(t, joker) for t in p.hand_tiles if t != joker)
        return {identity for identity, n in counts.items() if n == 4}
    return {natural(t, joker) for t in p.hand_tiles
            if t != joker and f"k{natural(t, joker)}" in p.combination_tiles}


def claim_actions(state, actor, tile):
    actions = empty_actions()
    if tile in state.jokers or len(state.tiles_list) <= 20:
        return actions
    joker = state.joker_indicator
    logical = natural(tile, joker)
    # DIY「允许三摊承包」关闭：对同一家累计吃碰满 2 摊后不得再吃碰该家
    # （吃碰计数按牌手身份跨局累计；杠不属于摊，不受限）。
    claims = None if getattr(state, "allow_claim", True) else state._seat_claims()
    for index, p in enumerate(state.player_list):
        if index == actor or state.is_cai_locked(index):
            continue
        restricted = claims is not None and claims[index][actor] >= xs.CLAIM_THRESHOLD - 1
        if not restricted and physical_for(p.hand_tiles, [logical] * 2, joker):
            actions[index].append("peng")
        if len(state.tiles_list) >= 22 and physical_for(p.hand_tiles, [logical] * 3, joker):
            actions[index].append("gang")
        if not restricted and index == (actor + 1) % 4 and logical in NUMBERS:
            for action in CHOWS:
                needed = [logical + n for n in CHOW_OFFSETS[action]]
                if (all(t in NUMBERS and t // 10 == logical // 10 for t in needed)
                        and physical_for(p.hand_tiles, needed, joker)):
                    actions[index].append(action)
        if actions[index]:
            actions[index].append("pass")
            if state.sync_tactical_enabled():
                actions[index].append("force_pass")
    return actions
