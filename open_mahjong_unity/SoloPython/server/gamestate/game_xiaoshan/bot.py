"""萧山 bot：杭州决策骨架 + 萧山翻财语义。

听牌/爆头按 ``game_calculation.xiaoshan`` 求解（白板固定本牌、同种财神
3 张）；连子价值把白板折算成本牌参与，财神本身按万能资源计。
"""

import asyncio
import random
from collections import Counter
from copy import copy
from types import SimpleNamespace
import time

from ..game_hangzhou.actions import CHOWS, claim_tiles, legal_cuts
from ..public.ai.level import DEFAULT_BOT_LEVEL
from ..public.ai.pacing import wait_bot_delay
from ...game_calculation.xiaoshan import rules as xs
from .actions import kong_tiles

CHI_MID_OFFSET = {"chi_left": -1, "chi_mid": 0, "chi_right": 1}
EASY_CLAIM_THRESHOLD, EASY_MAX_JOKERS = 18, 3
MEDIUM_CLAIM_THRESHOLD, MEDIUM_MAX_JOKERS = 12, 2


def _claim_frame(action, tile):
    """鸣牌并入副露的求解帧：码 + 按逻辑身份合成的实体牌。"""
    if action == "peng":
        return (f"k{tile}", (tile,) * 3)
    mid = tile + CHI_MID_OFFSET[action]
    return (f"s{mid}", (mid - 1, mid, mid + 1))


def _best_waits_after_claim(hand, frames, joker):
    """鸣牌并打出一手后的最大进张数（hard 档鸣牌决策用）。"""
    best = 0
    for tile in set(hand):
        rest = list(hand)
        rest.remove(tile)
        try:
            best = max(best, len(xs.waiting_tiles(rest, frames, joker)))
        except ValueError:
            continue
    return best


def _connections(hand, joker):
    wild = hand.count(joker)
    counts = Counter(xs.natural(t, joker) for t in hand if t != joker)
    return (wild * 20 + sum(min(2, n - 1) * n * 3 for n in counts.values())
            + sum(n * (counts[t + 1] * 2 + counts[t + 2]) for t, n in counts.items()
                  if t < 40 and t % 10 <= 7))


def choose_action(state, index, actions):
    p = state.player_list[index]
    joker = state.joker_indicator
    level = getattr(state, "bot_level", DEFAULT_BOT_LEVEL)
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
        # easy：前三候选里随机挑一手，模拟普通玩家水平。
        candidates.sort(reverse=True)
        tile = random.choice(candidates[:3] if level == "easy" else candidates[:1])[-1]
        position = max(i for i, t in enumerate(p.hand_tiles) if t == tile)
        return dict(action_type="cut", TileId=tile, cutIndex=position,
                    cutClass=p.has_draw_slot and position == len(p.hand_tiles) - 1)
    if "gang" in actions:
        return dict(action_type="gang")
    # 只在确实改善手牌结构时吃碰；白板按本牌身份折算后再评估。
    tile = xs.natural(state.live_pending_window.get("tile", 0), joker)
    mapped = [xs.natural(t, joker) for t in p.hand_tiles]
    threshold, max_jokers = ((EASY_CLAIM_THRESHOLD, EASY_MAX_JOKERS) if level == "easy"
                             else (MEDIUM_CLAIM_THRESHOLD, MEDIUM_MAX_JOKERS))
    if p.hand_tiles.count(joker) >= max_jokers:
        return dict(action_type="pass")
    claims = []
    for action in actions:
        if action not in ("peng",) + CHOWS:
            continue
        needed = claim_tiles(action, tile)
        if any(t not in mapped for t in needed):
            continue
        rest = list(mapped)
        for held in needed:
            rest.remove(held)
        claims.append((action, rest))
    if level == "hard":
        # 按鸣牌后的进张数严格决策：只有真正推进听牌的鸣牌才做。
        best = None
        for action, rest in claims:
            after = frames + (_claim_frame(action, tile),)
            try:
                score = (_best_waits_after_claim(rest, after, joker),
                         max(_connections(rest[:i] + rest[i + 1:], joker) for i in range(len(rest))))
            except ValueError:
                continue
            if best is None or score > best[0]:
                best = (score, action)
        if best is not None and best[0][0] > len(xs.waiting_tiles(mapped, frames, joker)):
            return dict(action_type=best[1])
        return dict(action_type="pass")
    before = _connections(mapped, joker)
    for action, rest in claims:
        after = max(_connections(rest[:i] + rest[i + 1:], joker) for i in range(len(rest)))
        if after + threshold >= before:
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
                           bot_level=getattr(state, "bot_level", DEFAULT_BOT_LEVEL),
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
