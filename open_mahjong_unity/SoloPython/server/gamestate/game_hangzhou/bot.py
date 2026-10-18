"""Public-information choices run outside the socket event loop."""

import asyncio
import random
from collections import Counter
from copy import copy
from types import SimpleNamespace
import time

from ..public.ai.level import DEFAULT_BOT_LEVEL
from ..public.ai.pacing import wait_bot_delay
from ...game_calculation.hangzhou.rules import JOKER, can_baotou, waiting_tiles
from .actions import CHOWS, claim_tiles, kong_tiles, legal_cuts

CHI_MID_OFFSET = {"chi_left": -1, "chi_mid": 0, "chi_right": 1}
EASY_CLAIM_THRESHOLD, EASY_MAX_JOKERS = 18, 3
MEDIUM_CLAIM_THRESHOLD, MEDIUM_MAX_JOKERS = 12, 2


def _claim_code(action, tile):
    """鸣牌并入副露后的面子码：碰为刻子码，吃为顺子中张码。"""
    if action == "peng":
        return f"k{tile}"
    return f"s{tile + CHI_MID_OFFSET[action]}"


def _best_waits_after_claim(hand, melds, jokers):
    """鸣牌并打出一手后的最大进张数（hard 档鸣牌决策用）。"""
    best = 0
    for tile in set(hand):
        rest = list(hand)
        rest.remove(tile)
        try:
            best = max(best, len(waiting_tiles(rest, melds, jokers)))
        except ValueError:
            continue
    return best


def _connections(hand, jokers=(JOKER,)):
    counts = Counter(hand)
    wild = sum(counts[t] for t in jokers)
    return (wild * 20 + sum(min(2, n - 1) * n * 3 for n in counts.values())
            + sum(n * (counts[t + 1] * 2 + counts[t + 2]) for t, n in counts.items() if t < 40 and t % 10 <= 7))


def choose_action(state, index, actions):
    p = state.player_list[index]
    jokers = tuple(getattr(state, "jokers", (JOKER,)))
    level = getattr(state, "bot_level", DEFAULT_BOT_LEVEL)
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
            # Preserve jokers except when an actual all-waits hand can float.
            floating = tile in jokers and can_baotou(rest, p.combination_tiles, jokers)
            outs = len(waiting_tiles(rest, p.combination_tiles, jokers)) if len(legal) > 1 else 0
            candidates.append((floating, outs, _connections(rest, jokers), tile == p.hand_tiles[-1], tile))
        # easy：前三候选里随机挑一手，模拟普通玩家水平。
        candidates.sort(reverse=True)
        tile = random.choice(candidates[:3] if level == "easy" else candidates[:1])[-1]
        position = max(i for i, t in enumerate(p.hand_tiles) if t == tile)
        return dict(action_type="cut", TileId=tile, cutIndex=position,
                    cutClass=p.has_draw_slot and position == len(p.hand_tiles) - 1)
    if "gang" in actions:
        return dict(action_type="gang")
    # A claim must improve the held physical hand; no wall/opponent inspection.
    tile = state.live_pending_window.get("tile")
    threshold, max_jokers = ((EASY_CLAIM_THRESHOLD, EASY_MAX_JOKERS) if level == "easy"
                             else (MEDIUM_CLAIM_THRESHOLD, MEDIUM_MAX_JOKERS))
    if sum(p.hand_tiles.count(t) for t in jokers) >= max_jokers:
        return dict(action_type="pass")
    claims = []
    for action in actions:
        if action not in ("peng",) + CHOWS:
            continue
        needed = claim_tiles(action, tile)
        if any(p.hand_tiles.count(t) < needed.count(t) for t in set(needed)):
            continue
        rest = list(p.hand_tiles)
        for held in needed:
            rest.remove(held)
        claims.append((action, rest))
    if level == "hard":
        # 按鸣牌后的进张数严格决策：只有真正推进听牌的鸣牌才做。
        best = None
        for action, rest in claims:
            melds = list(p.combination_tiles) + [_claim_code(action, tile)]
            try:
                score = (_best_waits_after_claim(rest, melds, jokers),
                         max(_connections(rest[:i] + rest[i + 1:], jokers) for i in range(len(rest))))
            except ValueError:
                continue
            if best is None or score > best[0]:
                best = (score, action)
        if best is not None and best[0][0] > len(waiting_tiles(p.hand_tiles, p.combination_tiles, jokers)):
            return dict(action_type=best[1])
        return dict(action_type="pass")
    before = _connections(p.hand_tiles, jokers)
    for action, rest in claims:
        after = max(_connections(rest[:i] + rest[i + 1:], jokers) for i in range(len(rest)))
        if after + threshold >= before:
            return dict(action_type=action)
    return dict(action_type="pass")


async def play_bot(state, index, tick):
    if tick != state.server_action_tick or index not in state.waiting_players_list:
        return
    started = time.monotonic()
    # A timeout/takeover may mutate the live state while this worker is
    # calculating. Freeze only the bot's hand and public action constraints.
    own = copy(state.player_list[index])
    own.hand_tiles, own.combination_tiles = list(own.hand_tiles), list(own.combination_tiles)
    players = [None] * 4
    players[index] = own
    locked = state.is_cai_locked(index)
    view = SimpleNamespace(player_list=players, tiles_list=[0] * len(state.tiles_list), jokers=tuple(state.jokers),
                           bot_level=getattr(state, "bot_level", DEFAULT_BOT_LEVEL),
                           is_cai_locked=lambda _: locked, live_pending_window=dict(state.live_pending_window or {}))
    action = await asyncio.to_thread(choose_action, view, index, list(state.action_dict[index]))
    if action["action_type"] != "pass":
        await wait_bot_delay(state, started)
    if tick == state.server_action_tick and index in state.waiting_players_list and state.action_queues[index].empty():
        try:
            await state.submit_action(index, **action)
        except ValueError:
            return  # The user can take over or a vote can close this window.
