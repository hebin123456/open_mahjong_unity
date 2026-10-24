"""掉线玩家托管：交给该玩法现成的机器人接管。

此前是"摸切兜底"（只打刚摸的牌、鸣牌一律 pass），强度太低；高性能启发式
又只覆盖国标标准/血战，其它玩法没有。这里统一改为按玩法复用现成的机器人：
- 杭州家族、香港、贵州、温州由各自的 play_bot 驱动（同一入口也跑吃碰杠胡）；
- 其余玩法走牌效机器人 smart_bot_action。
既不是摸切，也不使用高性能启发式。
"""
import asyncio
import importlib
import logging

logger = logging.getLogger(__name__)

# room_rule -> 该玩法自带的机器人模块（暴露 play_bot(state, index, tick)）
_PLAY_BOT_MODULES = {
    "hangzhou": "...game_hangzhou.bot",
    "hongkong": "...game_hongkong.bot",
    "guizhou": "...game_guizhou.bot",
    "wenzhou": "...game_wenzhou.bot",
}

_WINDOW_WAIT_ATTEMPTS = 200
_WINDOW_WAIT_INTERVAL = 0.01

# 摸切机器人（auto_cut_ai）给掉线玩家兜底时仍会读这两个标记：
# 掉线后的首个出牌询问不自动切牌，留一次人工重连的机会。
_OFFLINE_FIRST_CUT_READY = "offline_first_cut_ready"
_OFFLINE_FIRST_CUT_TICK = "offline_first_cut_tick"


def _mark_offline_first_cut_protection(player) -> None:
    setattr(player, _OFFLINE_FIRST_CUT_READY, True)
    setattr(player, _OFFLINE_FIRST_CUT_TICK, None)


def _skip_offline_first_cut(game_state, player) -> bool:
    """掉线后的首个出牌询问返回 True；同一次询问重复派发保持跳过。"""
    tick = getattr(game_state, "server_action_tick", None)
    if tick is not None and getattr(player, _OFFLINE_FIRST_CUT_TICK, None) == tick:
        return True
    if getattr(player, _OFFLINE_FIRST_CUT_READY, False):
        setattr(player, _OFFLINE_FIRST_CUT_READY, False)
        if tick is not None:
            setattr(player, _OFFLINE_FIRST_CUT_TICK, tick)
        return True
    return False


async def _wait_until_waiting(game_state, player_index: int) -> bool:
    """等 wait_action 把该座位加入 waiting_players_list，play_bot 才会真正出手。"""
    for _ in range(_WINDOW_WAIT_ATTEMPTS):
        if player_index in getattr(game_state, "waiting_players_list", []):
            return True
        await asyncio.sleep(_WINDOW_WAIT_INTERVAL)
    return False


async def offline_auto_action(game_state, player_index: int, action_list: list, game_status: str):
    """掉线玩家由现成机器人接管：出牌、吃碰杠胡都按机器人策略决策。"""
    try:
        module_path = _PLAY_BOT_MODULES.get(getattr(game_state, "room_rule", ""))
        if module_path is not None:
            if not await _wait_until_waiting(game_state, player_index):
                logger.warning(f"掉线托管 {player_index} 未进入操作窗口，跳过本次派发")
                return
            module = importlib.import_module(module_path, __package__)
            await module.play_bot(
                game_state, player_index, getattr(game_state, "server_action_tick", 0)
            )
            return

        from ..ai.smart_bot_ai import smart_bot_action
        await smart_bot_action(game_state, player_index, action_list, game_status)
    except Exception as e:
        logger.error(f"掉线托管 {player_index} 执行失败: {e}", exc_info=True)


def schedule_offline_auto_on_disconnect(game_state, user_id: int) -> None:
    """玩家刚掉线时：若当前巡仍有待操作，立即派发托管，不必等下次 broadcast / 超时。"""
    player = next((p for p in game_state.player_list if p.user_id == user_id), None)
    if player is None:
        return

    from ..hand_slot_utils import bot_ask_hand_game_status
    from ..lifecycle import all_humans_offline, start_owned_task
    if all_humans_offline(game_state):
        return

    player_index = player.player_index
    action_list = list((getattr(game_state, "action_dict", None) or {}).get(player_index) or [])
    if not action_list:
        return

    status = bot_ask_hand_game_status(game_state, player_index)
    logger.info(
        f"掉线即时托管：user_id={user_id}, player_index={player_index}, "
        f"status={status}, actions={action_list}"
    )
    start_owned_task(
        game_state, offline_auto_action(game_state, player_index, action_list, status)
    )
