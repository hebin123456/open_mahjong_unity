"""掉线托管：移交给该玩法现成的机器人（不使用摸切，也不使用高性能启发式）。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from .ai.auto_cut_ai import auto_cut_action
from .offline.offline_auto_action import (
    _OFFLINE_FIRST_CUT_READY,
    _OFFLINE_FIRST_CUT_TICK,
    offline_auto_action,
)


def _make_player(user_id=11, username="u"):
    return SimpleNamespace(
        user_id=user_id,
        username=username,
        player_index=0,
        hand_tiles=[1, 2, 3],
        tag_list=[],
        dingque_suit=0,
    )


def _set_protection_pending(player):
    setattr(player, _OFFLINE_FIRST_CUT_READY, True)
    setattr(player, _OFFLINE_FIRST_CUT_TICK, None)


def test_offline_action_delegates_to_smart_bot():
    """非 play_bot 玩法（如台湾）交给牌效机器人，而不是摸切。"""
    async def run():
        player = _make_player()
        state = SimpleNamespace(
            player_list=[player], server_action_tick=1, room_rule="taiwan",
            waiting_players_list=[0],
        )
        with patch(
            "server.gamestate.public.ai.smart_bot_ai.smart_bot_action",
            new_callable=AsyncMock,
        ) as bot:
            await offline_auto_action(state, 0, ["cut"], "waiting_hand_action")
            bot.assert_awaited_once_with(state, 0, ["cut"], "waiting_hand_action")

    asyncio.run(run())


def test_offline_action_uses_rule_play_bot():
    """杭州家族等自带 play_bot 的玩法交给本族机器人，并等操作窗口建立。"""
    async def run():
        player = _make_player()
        state = SimpleNamespace(
            player_list=[player], server_action_tick=7, room_rule="hangzhou",
            waiting_players_list=[0], action_dict={0: ["cut"]},
        )
        with patch("server.gamestate.game_hangzhou.bot.play_bot", new_callable=AsyncMock) as bot:
            await offline_auto_action(state, 0, ["cut"], "waiting_hand_action")
            bot.assert_awaited_once_with(state, 0, 7)

    asyncio.run(run())


def test_offline_action_skips_when_window_never_opens():
    """窗口迟迟不建立时不再硬塞动作，交给原有超时兜底。"""
    async def run():
        player = _make_player()
        state = SimpleNamespace(
            player_list=[player], server_action_tick=1, room_rule="hangzhou",
            waiting_players_list=[], action_dict={0: ["cut"]},
        )
        with patch("server.gamestate.game_hangzhou.bot.play_bot", new_callable=AsyncMock) as bot, \
                patch(
                    "server.gamestate.public.offline.offline_auto_action._WINDOW_WAIT_ATTEMPTS",
                    2,
                ):
            await offline_auto_action(state, 0, ["cut"], "waiting_hand_action")
            bot.assert_not_awaited()

    asyncio.run(run())


def test_auto_cut_action_first_ask_protected_for_offline_player():
    """摸切机器人给掉线玩家兜底时，首个出牌询问仍不自动切。"""
    async def run():
        player = _make_player()
        player.tag_list = ["offline"]
        _set_protection_pending(player)
        state = SimpleNamespace(
            player_list=[player], server_action_tick=5, room_rule="taiwan",
            claim_protection=False, waiting_players_list=[0], _waiting_action_tick=5,
        )
        with patch(
            "server.gamestate.public.ai.auto_cut_ai.get_ai_action",
            new_callable=AsyncMock,
        ) as act:
            await auto_cut_action(state, 0, ["cut"], "waiting_hand_action")
            act.assert_not_awaited()
            state.server_action_tick = 6
            state._waiting_action_tick = 6
            await auto_cut_action(state, 0, ["cut"], "waiting_hand_action")
            act.assert_awaited_once()

    asyncio.run(run())


def test_auto_cut_action_bot_ignores_first_ask_protection():
    async def run():
        player = _make_player(user_id=1, username="bot")
        _set_protection_pending(player)
        state = SimpleNamespace(
            player_list=[player], server_action_tick=5, room_rule="taiwan",
            claim_protection=False, waiting_players_list=[0], _waiting_action_tick=5,
        )
        with patch(
            "server.gamestate.public.ai.auto_cut_ai.get_ai_action",
            new_callable=AsyncMock,
        ) as act:
            await auto_cut_action(state, 0, ["cut"], "waiting_hand_action")
            act.assert_awaited_once()

    asyncio.run(run())
