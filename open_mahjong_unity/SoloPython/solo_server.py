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
import hashlib
import json
import logging
import os
import struct
import sys

# 让 `server` 包可被导入（本文件位于 open_mahjong_server/ 下，与 server/ 同级）
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logger = logging.getLogger("solo")

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# 真人座位：user_id 必须 > 10（引擎用 user_id <= 10 判定机器人）
HUMAN_USER_ID = 101
HUMAN_USERNAME = "我"
BOT_USER_IDS = [1, 2, 3]

# 单机房间标识：客户端建房/开局复用线上 `room/*` 协议，本地唯一房间用固定 id 表示。
SOLO_ROOM_ID = "solo-hangzhou"
SOLO_INSTANCE_ID = "solo-instance-1"
BOT_USERNAMES = {1: "机器人·摸切", 2: "牌效罗伯特", 3: "高性能罗伯特"}


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
        # 引擎通过 `connection.websocket.send_json()` 推送，故指向自身
        self.websocket = self

    async def send_json(self, payload):
        await self._send_text(json.dumps(payload, ensure_ascii=False))

    async def _send_text(self, text):
        await self._send_frame(0x1, text.encode("utf-8"))

    async def _send_frame(self, opcode, data):
        if self.closed:
            return
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
            self.writer.write(bytes(header) + data)
            await self.writer.drain()
        except Exception:
            self.closed = True

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
    """完成 HTTP Upgrade 握手，失败返回 None。"""
    raw = await reader.readuntil(b"\r\n\r\n")
    headers = {}
    for line in raw.decode("utf-8", "replace").split("\r\n")[1:]:
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
    return True


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
        self._start_lock = asyncio.Lock()


# ----------------------------------------------------------------------------
# 单机对局启动
# ----------------------------------------------------------------------------
def _build_room_data(rounds: int = 4, step_timer: int = 20, round_timer: int = 300) -> dict:
    return dict(
        room_id="solo-hangzhou",
        room_rule="hangzhou",
        sub_rule="hangzhou/mil2025",
        player_list=[HUMAN_USER_ID] + BOT_USER_IDS,   # 1 真人 + 3 机器人
        player_settings={HUMAN_USER_ID: {"username": HUMAN_USERNAME}},
        game_round=rounds,
        round_timer=round_timer,
        step_timer=step_timer,
        room_type="custom",
        tips=True,
        random_seed=0,
        bot_speed="normal",
    )


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


def _build_room_info(rounds: int, room_name: str = "", password: str = "") -> dict:
    """客户端 RoomPanel 需要的 room_info。

    直接放入 1 真人 + 3 机器人：客户端把 user_id<=10 视为机器人且默认「已准备」，
    房主无需手动加机器人，人数一满「开始游戏」即可点击。
    """
    players = [HUMAN_USER_ID] + BOT_USER_IDS
    return {
        "room_id": SOLO_ROOM_ID,
        "instance_id": SOLO_INSTANCE_ID,
        "room_type": "custom",
        "room_rule": "hangzhou",
        "sub_rule": "hangzhou/mil2025",
        "hepai_limit": 0,
        "tourist_limit": False,
        "allow_spectator": False,
        "max_player": 4,
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
        "room_name": room_name or "单机杭州麻将",
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


async def start_solo_game(server: SoloServer, conn: WsConnection, **opts):
    async with server._start_lock:
        # 每次开局都先结束上一局，保证客户端总能收到一份新的 game_start
        # （否则大厅里再次点「开始游戏」会复用尚未结束的旧对局，界面毫无反应）。
        await _stop_solo_game(server)
        from server.gamestate.game_hangzhou.HangzhouGameState import HangzhouGameState

        room_data = _build_room_data(**opts)
        state = HangzhouGameState(server, room_data, None, None, "solo-1")
        try:
            from server.gamestate.public.ai.pacing import DEFAULT_BOT_SPEED, configure_bot_pacing
            room_data.setdefault("bot_speed", DEFAULT_BOT_SPEED)
            configure_bot_pacing(state, room_data)
        except Exception as exc:      # 配置失败不影响对局
            logger.warning("configure_bot_pacing 失败: %s", exc)

        server._game = state
        server.gamestate_manager._game = state
        state.game_task = asyncio.create_task(state.run_game_loop())
        logger.info("单机杭州对局已启动 gamestate_id=%s 玩家=%s",
                    state.gamestate_id, room_data["player_list"])
        return state


# ----------------------------------------------------------------------------
# 消息分发
# ----------------------------------------------------------------------------
async def _send(conn: WsConnection, payload: dict):
    await conn.send_json(payload)


async def _handle_message(server: SoloServer, conn: WsConnection, message: dict):
    mtype = message.get("type", "")

    if mtype == "send_release_version":
        return  # 单机不校验版本

    if mtype == "ping":
        await _send(conn, {"type": "pong"})
        return

    if mtype in ("login", "register"):
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

    if mtype == "room/create_Hangzhou_room":
        rounds = int(message.get("gameround") or 4)
        server._room_opts = {"rounds": rounds}
        server._room = _build_room_info(rounds, room_name=message.get("roomname", ""),
                                        password=message.get("password", ""))
        logger.info("已创建单机房间 %s，玩家=%s", SOLO_ROOM_ID, server._room["player_list"])
        await _send(conn, {"type": "room/create_room_done", "success": True,
                           "message": "房间创建成功", "room_info": server._room})
        return

    if mtype.startswith("room/create_"):
        # 单机内嵌的引擎只实现了杭州麻将，其它规则直接拒绝
        await _send(conn, {"type": "room/create_room_done", "success": False,
                           "message": "单机版仅支持杭州麻将"})
        return

    if mtype in ("room/add_bot", "room/add_smart_bot", "room/add_guobiao_heuristic_bot",
                 "room/kick_player", "room/set_ready", "room/set_claim_protection"):
        # 房间已是满座（1 真人 + 3 机器人），这些操作只需回推最新座位状态
        await _broadcast_room_info(server, conn)
        return

    if mtype == "room/set_bot_speed":
        if server._room is not None:
            server._room["bot_speed"] = message.get("bot_speed") or server._room.get("bot_speed", "fast")
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
        from server.gamestate.game_hangzhou.get_action import handle_action
        await handle_action(server, conn.Connect_id, message, conn)
        return

    if mtype.startswith(("room/", "title/", "inventory/", "match/", "data/", "friend/", "event/")):
        logger.info("忽略未实现的单机消息: %s", mtype)
        return

    logger.info("未知消息: %s", mtype)


async def _serve_connection(server: SoloServer, reader, writer):
    if await _handshake(reader, writer) is None:
        return
    conn = WsConnection(reader, writer)
    conn.Connect_id = f"solo-{id(conn)}"
    logger.info("客户端已连接 %s", conn.Connect_id)
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
            except Exception:
                logger.exception("处理消息失败: %s", message.get("type"))
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
