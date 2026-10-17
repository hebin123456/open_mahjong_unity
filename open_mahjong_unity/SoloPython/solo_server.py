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


async def start_solo_game(server: SoloServer, conn: WsConnection, **opts):
    async with server._start_lock:
        if server._game is not None and not server._game.game_task.done():
            return server._game
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


async def run_server(host="127.0.0.1", port=18763):
    server = SoloServer()
    srv = await asyncio.start_server(
        lambda r, w: _serve_connection(server, r, w), host, port)
    logger.info("单机服务已监听 ws://%s:%d/game", host, port)
    async with srv:
        await srv.serve_forever()


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
