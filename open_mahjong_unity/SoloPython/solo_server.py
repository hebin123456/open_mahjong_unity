"""杭州麻将单机版 —— 本地 WebSocket 服务端。

设计目标：
- 只依赖 Python 标准库 + 仓库内的 ``pydantic``/``mahjong``（不依赖 FastAPI / 数据库 / pythonnet）。
- 复用线上服务端的杭州对局引擎（``server.gamestate.game_hangzhou``）与其动作处理 ``handle_action``。
- 在 127.0.0.1:PORT 上提供客户端所需的最小协议子集：版本检查、登录、单机开局、对局动作。

协议流程（客户端 SoloMode）：
    send_release_version  -> 忽略（单机不校验版本）
    login(is_tourist)     -> 回 login 成功 + login_info
    solo/start            -> 建内存房间（1 真人 + 3 机器人）并启动杭州对局引擎
    gamestate/hangzhou/*  -> handle_action（复用线上实现）
之后服务端把引擎产生的 gamestate 消息推给客户端，客户端收到 game_start 即进入牌桌。
"""

import asyncio
import base64
import collections
import hashlib
import json
import logging
import os
import struct
import sys
import uuid

# 让 `server` 包可被导入（本文件位于 open_mahjong_server/ 下，与 server/ 同级）
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger("solo")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 协议轨迹环形缓冲：记录收到的请求 / 发出的消息 / 异常，供设备端诊断面板展示
# （手机上看不到 logcat 时，这是定位「点了没反应」最快的手段）。
_EVENTS = collections.deque(maxlen=80)


def _note(text: str) -> None:
    _EVENTS.append(text)
    logger.info("TRACE %s", text)


def recent_events() -> str:
    """返回最近的协议轨迹（每行一条），供 C# 诊断面板读取。"""
    return "\n".join(_EVENTS)


# 真人座位：user_id 必须 > 10（引擎用 user_id <= 10 判定机器人）
HUMAN_USER_ID = 101
HUMAN_USERNAME = "我"
BOT_USER_IDS = [1, 2, 3]

# 单机房间标识：客户端建房/开局复用线上 `room/*` 协议，本地唯一房间用固定 id 表示。
SOLO_ROOM_ID = "solo-hangzhou"
SOLO_INSTANCE_ID = "solo-instance-1"
BOT_USERNAMES = {1: "机器人·摸切", 2: "牌效罗伯特", 3: "高性能罗伯特"}

# 大厅可选规则：萧山麻将完全复用杭州引擎，引擎内部恒按 hangzhou 处理；
# 国标麻将使用独立的 GuobiaoGameState 引擎与 gamestate/GB/* 协议通道。
# 这里记录用户在建房时选择的大厅规则，仅用于对外消息与房间信息的展示名。
HANGZHOU_RULE = "hangzhou"
HANGZHOU_SUB_RULE = "hangzhou/mil2025"
XIAOSHAN_RULE = "xiaoshan"
XIAOSHAN_SUB_RULE = "xiaoshan/xiaoshan"
GUOBIAO_RULE = "guobiao"
GUOBIAO_SUB_RULE = "guobiao/standard"
RULE_ROOM_NAMES = {HANGZHOU_RULE: "单机杭州麻将", XIAOSHAN_RULE: "单机萧山麻将",
                   GUOBIAO_RULE: "单机国标麻将"}

# 国标建房会透传给引擎的开局参数（起和番限制、花牌、天地人和、错和、鸣牌保护等）
GUOBIAO_PASSTHROUGH_KEYS = (
    "hepai_limit", "use_flowers", "tian_di_ren_he", "open_cuohe", "cuohe_type",
    "claim_protection", "show_moqie_hint", "random_seed", "tips", "count_tips",
    "pointer_tips", "allow_spectator", "tactical_call",
)

# 萧山馆规：开局积分、总倍率封顶（建房时由客户端下传，透传给引擎 detailed_config）
XIAOSHAN_PASSTHROUGH_KEYS = ("total_cap", "starting_score")


def _relabel_payload(payload: dict, rule: str, sub_rule: str) -> None:
    """把杭州引擎产生的对外消息重新标注为所选大厅规则（如萧山）。

    引擎的 ``type`` 前缀（``gamestate/hangzhou/...``）与 ``game_info.room_rule``
    都写死为 hangzhou，客户端据此解析规则清单；改标注后客户端即按萧山清单处理。
    """
    mtype = payload.get("type")
    if isinstance(mtype, str) and mtype.startswith("gamestate/hangzhou/"):
        payload["type"] = "gamestate/" + rule + mtype[len("gamestate/hangzhou"):]
    game_info = payload.get("game_info")
    if isinstance(game_info, dict):
        if game_info.get("room_rule") == HANGZHOU_RULE:
            game_info["room_rule"] = rule
        if game_info.get("sub_rule") == HANGZHOU_SUB_RULE:
            game_info["sub_rule"] = sub_rule


def _resolve_room_rule(message: dict):
    """从建房请求中解析大厅规则；仅杭州/萧山/国标有效，其它一律回退杭州。"""
    rule = message.get("rule") or HANGZHOU_RULE
    if rule == XIAOSHAN_RULE:
        return XIAOSHAN_RULE, message.get("sub_rule") or XIAOSHAN_SUB_RULE
    if rule == GUOBIAO_RULE:
        return GUOBIAO_RULE, message.get("sub_rule") or GUOBIAO_SUB_RULE
    return HANGZHOU_RULE, HANGZHOU_SUB_RULE


# ----------------------------------------------------------------------------
# 极简 WebSocket 服务端（RFC6455 子集）
# ----------------------------------------------------------------------------
class WsConnection:
    """一条已握手的连接：提供引擎需要的 ``send_json``。"""

    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.closed = False
        self.user_id = None
        self.Connect_id = None
        # 握手请求的 path（如 /game/<id>、/chat/<id>），用于区分对局连接与其它连接
        self.path = ""
        # 所属单机服务：用于把杭州引擎的对外消息按所选大厅规则（杭州/萧山）重新标注
        self.server = None
        # 对局任务与连接读循环可能并发写同一 socket；不加锁会让两帧字节交错，
        # 客户端解析出错后表现为「消息没到」。所有写出都串行化。
        self._write_lock = asyncio.Lock()
        # 引擎通过 `connection.websocket.send_json()` 推送，故指向自身
        self.websocket = self

    async def send_json(self, payload):
        server = self.server
        if server is not None and server._label_rule != "hangzhou":
            # 引擎内部固定按杭州处理，但大厅入口可能是萧山；对外消息按所选规则重新标注，
            # 使客户端按萧山清单解析与显示。
            _relabel_payload(payload, server._label_rule, server._label_sub_rule)
        msg_type = str(payload.get("type"))
        try:
            await self._send_text(json.dumps(payload, ensure_ascii=False))
        except Exception as exc:
            # 只有真正写进 socket 才算发送成功，避免「先记轨迹后写失败」造成假阳性
            _note("!! 发送失败: %s (%s)" % (msg_type, exc))
            raise
        _note("→ " + msg_type)

    async def _send_text(self, text):
        await self._send_frame(0x1, text.encode("utf-8"))

    async def _send_frame(self, opcode, data):
        if self.closed:
            raise ConnectionError("websocket closed")
        header = bytearray()
        header.append(0x80 | opcode)
        n = len(data)
        if n < 126:
            header.append(n)
        elif n < (1 << 16):
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        try:
            async with self._write_lock:
                self.writer.write(bytes(header) + data)
                await self.writer.drain()
        except Exception:
            self.closed = True
            raise

    async def close(self):
        if self.closed:
            return
        try:
            await self._send_frame(0x8, b"")
        except Exception:
            pass
        self.closed = True
        try:
            self.writer.close()
        except Exception:
            pass


async def _read_exact(reader, n):
    data = await reader.readexactly(n)
    return data


async def read_frame(reader):
    """读一帧，返回 (opcode, payload)；连接结束返回 (None, None)。"""
    try:
        b1, b2 = await _read_exact(reader, 2)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None, None
    opcode = b1 & 0x0F
    masked = b2 & 0x80
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack(">H", await _read_exact(reader, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", await _read_exact(reader, 8))[0]
    mask = await _read_exact(reader, 4) if masked else None
    payload = await _read_exact(reader, length) if length else b""
    if mask:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return opcode, payload


async def _handshake(reader, writer):
    """完成 HTTP Upgrade 握手；成功返回请求 path，失败返回 None。"""
    raw = await reader.readuntil(b"\r\n\r\n")
    text = raw.decode("utf-8", "replace")
    lines = text.split("\r\n")
    parts = lines[0].split(" ")
    path = parts[1] if len(parts) >= 2 else ""
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    key = headers.get("sec-websocket-key")
    if not key:
        writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        await writer.drain()
        return None
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
    writer.write((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode())
    await writer.drain()
    return path


# ----------------------------------------------------------------------------
# 引擎替身：只提供杭州对局真正会用到的接口
# ----------------------------------------------------------------------------
class _DbStub:
    def __getattr__(self, name):  # 引擎在杭州路径上不查库；兜底返回 None
        return lambda *a, **k: None


class _GameStateManagerStub:
    """handle_action 依赖 get_game_state_by_gamestate_id。"""

    def __init__(self):
        self._game = None

    def get_game_state_by_gamestate_id(self, gamestate_id):
        game = self._game
        if game is not None and str(gamestate_id) == str(game.gamestate_id):
            return game
        return None

    async def cleanup_game_state_complete(self, gamestate_id=None):  # 单机无全局注册表，收尾为空操作
        return None


class SoloServer:
    def __init__(self):
        self.players = {}
        self.user_id_to_connection = {}
        self.room_manager = None
        self.db_manager = _DbStub()
        self.calculation_service = None
        self.gamestate_manager = _GameStateManagerStub()
        self._game = None
        self._room = None        # 当前单机房间的 room_info；None 表示尚未建房
        self._room_opts = {}     # 建房时选定的开局参数（局数等）
        self._label_rule = HANGZHOU_RULE             # 对外展示的大厅规则（杭州/萧山）
        self._label_sub_rule = HANGZHOU_SUB_RULE
        self._start_lock = asyncio.Lock()


# ----------------------------------------------------------------------------
# 单机对局启动
# ----------------------------------------------------------------------------
def _build_room_data(rounds: int = 4, step_timer: int = 20, round_timer: int = 300,
                     rule: str = HANGZHOU_RULE, sub_rule: str = None,
                     opts: dict = None) -> dict:
    # 引擎按 room_rule/sub_rule 选择；萧山复用杭州栈，国标走独立引擎。
    if rule == XIAOSHAN_RULE:
        room_rule, sub = XIAOSHAN_RULE, XIAOSHAN_SUB_RULE
    elif rule == GUOBIAO_RULE:
        room_rule, sub = GUOBIAO_RULE, sub_rule or GUOBIAO_SUB_RULE
    else:
        room_rule, sub = HANGZHOU_RULE, HANGZHOU_SUB_RULE
    opts = opts or {}
    # 国标三麻：1 真人 + 2 机器人，共 3 家
    bots = BOT_USER_IDS[:2] if (rule == GUOBIAO_RULE and sub == "guobiao/sanma") else BOT_USER_IDS
    # 国标引擎的 GameInfo（服务端与客户端）都要求 room_id 为整数，字符串会校验失败
    # 导致 game_start 发不出去（表现为创建房间后点开始游戏无反应）；杭州引擎不受此限。
    data = dict(
        room_id=0 if rule == GUOBIAO_RULE else "solo-" + room_rule,
        room_rule=room_rule,
        sub_rule=sub,
        player_list=[HUMAN_USER_ID] + bots,
        player_settings={HUMAN_USER_ID: {"username": HUMAN_USERNAME}},
        game_round=rounds,
        round_timer=round_timer,
        step_timer=step_timer,
        room_type="custom",
        tips=True,
        random_seed=0,
        bot_speed="fast",
    )
    if rule == GUOBIAO_RULE:
        # 国标特有开局参数：起和番限制、花牌、天地人和、错和、鸣牌保护等
        for key in GUOBIAO_PASSTHROUGH_KEYS:
            if key in opts:
                data[key] = opts[key]
    elif rule == XIAOSHAN_RULE:
        # 萧山馆规：开局积分、总倍率封顶与房规开关（如「有财神必须爆头」），
        # 经 detailed_config 交引擎 normalize_config 校验。
        detailed = {key: opts[key] for key in XIAOSHAN_PASSTHROUGH_KEYS if key in opts}
        if isinstance(opts.get("detailed_config"), dict):
            detailed.update(opts["detailed_config"])
        if detailed:
            data["detailed_config"] = detailed
    return data


def _player_settings() -> dict:
    """房间 4 个座位（1 真人 + 3 机器人）的展示信息，客户端用其中的 username 渲染座位名。"""
    settings = {
        str(HUMAN_USER_ID): {
            "user_id": HUMAN_USER_ID, "username": HUMAN_USERNAME,
            "title_id": 1, "profile_image_id": 1, "character_id": 1,
            "voice_id": 1, "avatar_frame_id": 0,
        }
    }
    for bot_id in BOT_USER_IDS:
        settings[str(bot_id)] = {
            "user_id": bot_id, "username": BOT_USERNAMES.get(bot_id, f"机器人{bot_id}"),
            "title_id": 1, "profile_image_id": 1, "character_id": 1,
            "voice_id": 1, "avatar_frame_id": 0,
        }
    return settings


def _build_room_info(rounds: int, room_name: str = "", password: str = "",
                     rule: str = HANGZHOU_RULE, sub_rule: str = HANGZHOU_SUB_RULE,
                     opts: dict = None) -> dict:
    """客户端 RoomPanel 需要的 room_info。

    直接放入 1 真人 + 3 机器人（国标三麻为 1 真人 + 2 机器人）：客户端把
    user_id<=10 视为机器人且默认「已准备」，人数一满「开始游戏」即可点击。
    """
    opts = opts or {}
    sanma = rule == GUOBIAO_RULE and (sub_rule or GUOBIAO_SUB_RULE) == "guobiao/sanma"
    players = [HUMAN_USER_ID] + (BOT_USER_IDS[:2] if sanma else BOT_USER_IDS)
    info = {
        "room_id": SOLO_ROOM_ID,
        "instance_id": SOLO_INSTANCE_ID,
        "room_type": "custom",
        "room_rule": rule,
        "sub_rule": sub_rule,
        "hepai_limit": opts.get("hepai_limit", 8) if rule == GUOBIAO_RULE else 0,
        "tourist_limit": False,
        "allow_spectator": False,
        "max_player": 3 if sanma else 4,
        "player_list": players,
        "seat_list": players,
        "ready_list": [],
        "player_settings": _player_settings(),
        "has_password": bool(password),
        "tips": True,
        "count_tips": False,
        "pointer_tips": True,
        "host_name": HUMAN_USERNAME,
        "host_user_id": HUMAN_USER_ID,
        "room_name": room_name or RULE_ROOM_NAMES.get(rule, "单机麻将"),
        "game_round": rounds,
        "round_timer": 300,
        "step_timer": 20,
        "is_game_running": False,
        "bot_speed": "fast",
        "is_player_set_random_seed": False,
        "open_cuohe": False,
        "cuohe_type": 0,
        "show_moqie_hint": False,
        "tactical_call": False,
        "claim_protection": False,
        "use_flowers": False,
        "tian_di_ren_he": False,
        "is_duplicate": False,
        "duplicate_round_count": 0,
        "wall_wan": True,
        "wall_tong": True,
        "wall_suo": True,
        "wall_winds": True,
        "wall_dragons": True,
        "wall_flowers": False,
    }
    if rule == GUOBIAO_RULE:
        # 国标建房参数回填到房间信息，供客户端房间面板展示
        for key in GUOBIAO_PASSTHROUGH_KEYS:
            if key in opts:
                info[key] = opts[key]
    elif rule == XIAOSHAN_RULE:
        # 萧山馆规回填到房间信息，供客户端房间面板展示开局积分/倍率封顶
        info["detailed_config"] = {
            key: opts[key] for key in XIAOSHAN_PASSTHROUGH_KEYS if key in opts
        }
    return info


async def _broadcast_room_info(server: SoloServer, conn: WsConnection):
    """把最新 room_info 推给客户端，触发 RoomPanel 刷新。"""
    if server._room is None:
        return
    await _send(conn, {"type": "room/refresh_room_info", "success": True,
                       "message": "", "room_info": server._room})


async def _stop_solo_game(server: SoloServer):
    """离开房间时结束正在跑的对局引擎，允许重新建房开局。"""
    game = server._game
    if game is None:
        return
    task = getattr(game, "game_task", None)
    if task is not None and not task.done():
        task.cancel()
    server._game = None
    server.gamestate_manager._game = None


async def _run_game_loop(server: SoloServer, state) -> None:
    """包一层，把对局循环的异常暴露出来。

    引擎循环由 ``create_task`` 启动，其异常不会自动上报：一旦它在第一帧就死掉，
    客户端会连一份 game_start 都收不到，表现为「点了开始游戏毫无反应」。
    """
    _note("对局循环已启动")
    try:
        await state.run_game_loop()
        _note("对局循环正常结束")
    except asyncio.CancelledError:
        _note("对局循环被取消")
        raise
    except Exception as exc:
        reason = "%s: %s" % (type(exc).__name__, exc)
        logger.exception("单机对局循环异常退出")
        _note("!! 对局循环异常退出: " + reason)
        conn = server.user_id_to_connection.get(HUMAN_USER_ID)
        if conn is not None:
            try:
                await conn.send_json({
                    "type": "error_message",
                    "success": False,
                    "message": "单机对局启动失败: " + reason,
                })
            except Exception:
                pass


async def start_solo_game(server: SoloServer, conn: WsConnection, **opts):
    async with server._start_lock:
        # 每次开局都先结束上一局，保证客户端总能收到一份新的 game_start
        # （否则大厅里再次点「开始游戏」会复用尚未结束的旧对局，界面毫无反应）。
        await _stop_solo_game(server)
        rule = server._label_rule
        calculation = None
        if rule == XIAOSHAN_RULE:
            from server.gamestate.game_xiaoshan.XiaoshanGameState import XiaoshanGameState
            engine, label = XiaoshanGameState, "萧山"
        elif rule == GUOBIAO_RULE:
            from server.gamestate.game_guobiao.GuobiaoGameState import GuobiaoGameState
            from server.game_calculation.game_calculation_service import GameCalculationService
            engine, label = GuobiaoGameState, "国标"
            calculation = GameCalculationService()  # 国标听牌/和牌校验依赖计算服务
        else:
            from server.gamestate.game_hangzhou.HangzhouGameState import HangzhouGameState
            engine, label = HangzhouGameState, "杭州"

        room_data = _build_room_data(
            rounds=int(opts.get("rounds") or 4),
            step_timer=int(opts.get("step_timer") or 20),
            round_timer=int(opts.get("round_timer") or 300),
            rule=rule, sub_rule=opts.get("sub_rule"), opts=opts)
        # 国标引擎会调用 db_manager 存取段位/牌谱（单机空实现），杭州/萧山不查库传 None。
        # gamestate_id 每局必须唯一：客户端按它区分新旧对局并重置询问时钟残留，
        # 固定值会让「离开房间重建后再开局」沿用上一场的 tick 状态，真人出牌被服务端丢弃。
        state = engine(server, room_data, calculation,
                       _DbStub() if rule == GUOBIAO_RULE else None,
                       "solo-" + uuid.uuid4().hex[:8])
        try:
            from server.gamestate.public.ai.pacing import DEFAULT_BOT_SPEED, configure_bot_pacing, normalize_bot_speed
            room_data.setdefault("bot_speed", DEFAULT_BOT_SPEED)
            # 房间面板改过节奏时按房间当前值开局；旧值不在档位表里会归一成默认。
            room_data["bot_speed"] = normalize_bot_speed(
                (server._room or {}).get("bot_speed") or room_data["bot_speed"])
            configure_bot_pacing(state, room_data)
        except Exception as exc:      # 配置失败不影响对局
            logger.warning("configure_bot_pacing 失败: %s", exc)

        server._game = state
        server.gamestate_manager._game = state
        state.game_task = asyncio.create_task(_run_game_loop(server, state))
        logger.info("单机%s对局已启动 gamestate_id=%s 玩家=%s",
                    label, state.gamestate_id, room_data["player_list"])
        _note("已开局 gamestate_id=" + str(state.gamestate_id))
        return state


# ----------------------------------------------------------------------------
# 消息分发
# ----------------------------------------------------------------------------
async def _send(conn: WsConnection, payload: dict):
    await conn.send_json(payload)


async def _handle_message(server: SoloServer, conn: WsConnection, message: dict):
    mtype = message.get("type", "")
    _note("← " + str(mtype))

    if mtype == "send_release_version":
        return  # 单机不校验版本

    if mtype == "ping":
        await _send(conn, {"type": "pong"})
        return

    if mtype in ("login", "register"):
        # 聊天连接也会发 login；若让它注册成对局用户，引擎会把对局消息推到聊天 socket，
        # 对局界面（game socket）就永远收不到 game_start。故非对局连接一律不注册。
        if "/chat" in (conn.path or ""):
            _note("忽略聊天连接登录 " + str(conn.Connect_id))
            return
        conn.user_id = HUMAN_USER_ID
        server.players[conn.Connect_id] = conn
        server.user_id_to_connection[HUMAN_USER_ID] = conn
        await _send(conn, {
            "type": "login",
            "success": True,
            "message": "登录成功",
            "login_info": {
                "user_id": HUMAN_USER_ID,
                "username": HUMAN_USERNAME,
                "userkey": "solo-userkey",
                "is_tourist": True,
            },
        })
        return

    # 单机开局：客户端在 SoloMode 下登录成功后直接请求开局
    if mtype in ("solo/start", "solo_start"):
        opts = {}
        if message.get("game_round"):
            opts["rounds"] = int(message["game_round"])
        if message.get("step_timer") is not None:
            opts["step_timer"] = int(message["step_timer"])
        if message.get("round_timer") is not None:
            opts["round_timer"] = int(message["round_timer"])
        await start_solo_game(server, conn, **opts)
        return

    # ---- 房间协议：复用线上建房/开局流程，让大厅「创建房间 → 开始游戏」可用 ----
    if mtype == "room/get_room_list":
        await _send(conn, {"type": "room/get_room_list", "success": True, "message": "",
                           "room_list": [], "show_tip": bool(message.get("show_tip"))})
        return

    if mtype in ("room/create_Hangzhou_room", "room/create_Xiaoshan_room", "room/create_GB_room"):
        # 杭州/萧山共用同一引擎，国标走独立引擎；仅按建房时选择的大厅规则决定引擎与展示名。
        rule, sub_rule = _resolve_room_rule(message)
        server._label_rule = rule
        server._label_sub_rule = sub_rule
        rounds = int(message.get("gameround") or 4)
        opts = {"rounds": rounds, "sub_rule": sub_rule}
        for key in ("round_timer", "step_timer"):
            if message.get(key) is not None:
                opts[key] = int(message[key])
        if rule == GUOBIAO_RULE:
            # 国标：透传起和番限制、花牌、天地人和、错和、鸣牌保护等开局参数
            for key in GUOBIAO_PASSTHROUGH_KEYS:
                if key in message and message[key] is not None:
                    opts[key] = message[key]
        elif rule == XIAOSHAN_RULE:
            # 萧山：透传开局积分、总倍率封顶与房规开关（detailed_config，如「有财神必须爆头」）
            for key in XIAOSHAN_PASSTHROUGH_KEYS:
                if key in message and message[key] is not None:
                    opts[key] = message[key]
            detailed = message.get("detailed_config")
            if isinstance(detailed, dict) and detailed:
                opts["detailed_config"] = detailed
        server._room_opts = opts
        server._room = _build_room_info(rounds, room_name=message.get("roomname", ""),
                                        password=message.get("password", ""), rule=rule, sub_rule=sub_rule,
                                        opts=opts)
        logger.info("已创建单机房间 %s，规则=%s，玩家=%s", SOLO_ROOM_ID, rule, server._room["player_list"])
        await _send(conn, {"type": "room/create_room_done", "success": True,
                           "message": "房间创建成功", "room_info": server._room})
        return

    if mtype.startswith("room/create_"):
        # 单机内嵌的引擎只实现了杭州麻将（萧山为其别名）与国标麻将，其它规则直接拒绝
        await _send(conn, {"type": "room/create_room_done", "success": False,
                           "message": "单机版仅支持杭州麻将、萧山麻将和国标麻将"})
        return

    if mtype in ("room/add_bot", "room/add_smart_bot", "room/add_guobiao_heuristic_bot",
                 "room/kick_player", "room/set_ready", "room/set_claim_protection"):
        # 房间已是满座（1 真人 + 3 机器人），这些操作只需回推最新座位状态
        await _broadcast_room_info(server, conn)
        return

    if mtype == "room/set_bot_speed":
        speed = message.get("bot_speed")
        if server._room is not None and isinstance(speed, str) and speed:
            server._room["bot_speed"] = speed
            # 对局进行中也即时生效；非法档位由 normalize 归一成默认。
            game = getattr(server, "_game", None)
            if game is not None:
                from server.gamestate.public.ai.pacing import normalize_bot_speed
                game.bot_speed = normalize_bot_speed(speed)
        await _broadcast_room_info(server, conn)
        return

    if mtype == "room/start_game":
        if server._room is not None:
            server._room["is_game_running"] = True
        await start_solo_game(server, conn, **server._room_opts)
        return

    if mtype == "room/leave_room":
        await _stop_solo_game(server)
        server._room = None
        server._room_opts = {}
        server._label_rule = HANGZHOU_RULE
        server._label_sub_rule = HANGZHOU_SUB_RULE
        await _send(conn, {"type": "room/leave_room_done", "success": True,
                           "message": "离开房间成功", "room_id": message.get("room_id", SOLO_ROOM_ID),
                           "room_instance_id": SOLO_INSTANCE_ID})
        return

    if mtype == "room/sync_my_room":
        if server._room is not None:
            await _broadcast_room_info(server, conn)
        else:
            await _send(conn, {"type": "room/sync_not_in_room", "success": True,
                               "message": "当前不在房间中"})
        return

    if mtype.startswith("gamestate/"):
        if mtype.startswith("gamestate/GB/"):
            # 国标：复用线上 gamestate_router 的 GB 路由（动作/观战/投票等）
            from server.gamestate.gamestate_router import handle_gamestate_message
            await handle_gamestate_message(server, conn.Connect_id, message, conn)
        else:
            from server.gamestate.game_hangzhou.get_action import handle_action
            await handle_action(server, conn.Connect_id, message, conn)
        return

    if mtype.startswith(("room/", "title/", "inventory/", "match/", "data/", "friend/", "event/")):
        logger.info("忽略未实现的单机消息: %s", mtype)
        return

    logger.info("未知消息: %s", mtype)


async def _serve_connection(server: SoloServer, reader, writer):
    path = await _handshake(reader, writer)
    if path is None:
        return
    conn = WsConnection(reader, writer)
    conn.server = server
    conn.path = path
    conn.Connect_id = f"solo-{id(conn)}"
    logger.info("客户端已连接 %s path=%s", conn.Connect_id, path)
    _note("客户端已连接 " + str(conn.Connect_id))
    try:
        while True:
            opcode, payload = await read_frame(reader)
            if opcode is None:
                break
            if opcode == 0x8:          # close
                break
            if opcode == 0x9:          # ping
                await conn._send_frame(0xA, payload)
                continue
            if opcode != 0x1:
                continue
            try:
                message = json.loads(payload.decode("utf-8"))
            except Exception:
                continue
            try:
                await _handle_message(server, conn, message)
            except Exception as exc:
                logger.exception("处理消息失败: %s", message.get("type"))
                reason = "%s: %s" % (type(exc).__name__, exc)
                _note("!! " + str(message.get("type")) + " -> " + reason)
                # 内嵌服务没有独立的运维日志通道，失败原因必须回吐给客户端，
                # 否则用户只会看到「点了没反应」，无从定位。
                try:
                    await conn.send_json({
                        "type": "error_message",
                        "success": False,
                        "message": "单机服务处理 %s 失败: %s" % (message.get("type"), reason),
                    })
                except Exception:
                    pass
    except Exception:
        logger.exception("连接异常")
    finally:
        conn.closed = True
        try:
            writer.close()
        except Exception:
            pass
        if conn.user_id is not None:
            server.user_id_to_connection.pop(conn.user_id, None)
        game = server._game
        if game is not None and not game.game_task.done():
            game.game_task.cancel()
        logger.info("客户端断开 %s", conn.Connect_id)
        _note("客户端断开 " + str(conn.Connect_id))


async def run_server(host="127.0.0.1", port=18763, ready=None):
    server = SoloServer()
    srv = await asyncio.start_server(
        lambda r, w: _serve_connection(server, r, w), host, port)
    logger.info("单机服务已监听 ws://%s:%d/game", host, port)
    if ready is not None:
        ready.set()
    async with srv:
        await srv.serve_forever()


# ----------------------------------------------------------------------------
# Android（Chaquopy）入口：在独立线程里跑事件循环，主线程立即返回
# ----------------------------------------------------------------------------
_server_thread = None
_server_ready = None
_server_error = None


def start_background():
    """供 C# 通过 Chaquopy 调用的零参入口。

    在后台线程内启动 asyncio 事件循环并监听本地端口，等待端口就绪后返回，
    使 Unity 主线程在客户端发起 WebSocket 连接前即可连上服务。

    返回启动状态字符串：以 ``"OK"`` 开头表示端口已就绪；否则为 ``"ERR: <原因>"``。
    线程内的异常没法抛回 Java，所以在这里转成字符串返回给 C#，失败时客户端会原样展示。
    """
    global _server_thread, _server_ready, _server_error
    import threading

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if _server_ready is None:
        _server_ready = threading.Event()
    if _server_thread is not None and _server_thread.is_alive():
        return "OK（服务已在运行）"

    host = os.environ.get("SOLO_HOST", "127.0.0.1")
    port = int(os.environ.get("SOLO_PORT", "18763"))
    _server_ready.clear()
    _server_error = None

    def _run():
        global _server_error
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_server(host, port, _server_ready))
        except BaseException as exc:
            _server_error = f"{type(exc).__name__}: {exc}"
            logger.exception("单机服务退出")
        finally:
            _server_ready.set()

    _server_thread = threading.Thread(target=_run, name="solo-ws", daemon=True)
    _server_thread.start()
    # 首次启动 Chaquopy 需要解压标准库，给足冷启动时间
    ok = _server_ready.wait(timeout=15.0)
    logger.info("单机服务后台线程已启动 ready=%s error=%s", ok, _server_error)
    if _server_error:
        return "ERR: " + _server_error
    if ok:
        return "OK"
    return f"ERR: 15s 内未监听 {host}:{port}"


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("SOLO_HOST", "127.0.0.1")
    port = int(os.environ.get("SOLO_PORT", "18763"))
    try:
        asyncio.run(run_server(host, port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
