"""萧山 bot：杭州决策骨架 + 萧山翻财语义。

听牌/爆头按 ``game_calculation.xiaoshan`` 求解（白板固定本牌、同种财神
3 张）；连子价值把白板折算成本牌参与，财神本身按万能资源计。
"""

import asyncio
from collections import Counter
from copy import copy
from types import SimpleNamespace
import time

from ..game_hangzhou.actions import CHOWS, claim_tiles, legal_cuts
from ..public.ai.pacing import wait_bot_delay
from ...game_calculation.xiaoshan import rules as xs
from .actions import kong_tiles


def _connections(hand, joker):
    wild = hand.count(joker)
    counts = Counter(xs.natural(t, joker) for t in hand if t != joker)
    return (wild * 20 + sum(min(2, n - 1) * n * 3 for n in counts.values())
            + sum(n * (counts[t + 1] * 2 + counts[t + 2]) for t, n in counts.items()
                  if t < 40 and t % 10 <= 7))


def choose_action(state, index, actions):
    p = state.player_list[index]
    joker = state.joker_indicator
    frames = tuple((code, tuple(mask[1::2]))
                   for code, mask in zip(p.combination_tiles, p.combination_mask))
    for action in ("hu_self", "ready"):
        if action in actions:
            return dict(action_type=action)
    for action in ("angang", "jiagang"):
        if action in actions:
            return dict(action_type=action, target_tile=min(kong_tiles(state, index, action)))
    if "cut" in actions:
        candidates = []
        legal = legal_cuts(state, index)
        for tile in sorted(legal):
            rest = list(p.hand_tiles)
            rest.remove(tile)
            floating = tile == joker and xs.can_baotou(rest, frames, joker)
            outs = len(xs.waiting_tiles(rest, frames, joker)) if len(legal) > 1 else 0
            candidates.append((floating, outs, _connections(rest, joker), tile == p.hand_tiles[-1], tile))
        tile = max(candidates)[-1]
        position = max(i for i, t in enumerate(p.hand_tiles) if t == tile)
        return dict(action_type="cut", TileId=tile, cutIndex=position,
                    cutClass=p.has_draw_slot and position == len(p.hand_tiles) - 1)
    if "gang" in actions:
        return dict(action_type="gang")
    # 只在确实改善手牌结构时吃碰；白板按本牌身份折算后再评估。
    tile = xs.natural(state.live_pending_window.get("tile", 0), joker)
    before = _connections([xs.natural(t, joker) for t in p.hand_tiles], joker)
    for action in actions:
        if action not in ("peng",) + CHOWS:
            continue
        mapped = [xs.natural(t, joker) for t in p.hand_tiles]
        needed = claim_tiles(action, tile)
        if any(t not in mapped for t in needed):
            continue
        for held in needed:
            mapped.remove(held)
        after = max(_connections(mapped[:i] + mapped[i + 1:], joker) for i in range(len(mapped)))
        if after + 12 >= before and p.hand_tiles.count(joker) < 2:
            return dict(action_type=action)
    return dict(action_type="pass")


async def play_bot(state, index, tick):
    if tick != state.server_action_tick or index not in state.waiting_players_list:
        return
    started = time.monotonic()
    # 冻结 bot 自己的手牌与公开行动约束，避免超时/接管期间的并发变更。
    own = copy(state.player_list[index])
    own.hand_tiles = list(own.hand_tiles)
    own.combination_tiles = list(own.combination_tiles)
    own.combination_mask = list(own.combination_mask)
    players = [None] * 4
    players[index] = own
    locked = state.is_cai_locked(index)
    view = SimpleNamespace(player_list=players, tiles_list=[0] * len(state.tiles_list),
                           jokers=(state.joker_indicator,), joker_indicator=state.joker_indicator,
                           is_cai_locked=lambda _: locked,
                           live_pending_window=dict(state.live_pending_window or {}))
    action = await asyncio.to_thread(choose_action, view, index, list(state.action_dict[index]))
    if action["action_type"] != "pass":
        await wait_bot_delay(state, started)
    if tick == state.server_action_tick and index in state.waiting_players_list and state.action_queues[index].empty():
        try:
            await state.submit_action(index, **action)
        except ValueError:
            return  # 用户接管或投票关闭了该窗口。
