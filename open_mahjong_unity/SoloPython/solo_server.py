"""杭州麻将单机版 —— 本地 WebSocket 服务端。

设计目标：
- 只依赖 Python 标准库 + 仓库内的 ``pydantic``/``mahjong``（不依赖 FastAPI / 数据库 / pythonnet）。
- 复用线上服务端的杭州对局引擎（``server.gamestate.game_hangzhou``）与其动作处理 ``handle_action``。
- 默认监听 127.0.0.1 提供单机对局；``SOLO_LAN=1`` 时监听 0.0.0.0 并开启 UDP 房间发现，
  同一局域网的其它客户端可搜索并加入房间，房主可随时用机器人补位。

协议流程（客户端 SoloMode）：
    send_release_version  -> 忽略（单机不校验版本）
    login(is_tourist)     -> 动态分配真人 user_id（101 起自增）并回 login 成功
    room/create_*         -> 建房；局域网模式下初始只有房主一人，其余座位留给真人/机器人
    room/join_room        -> 其它客户端加入本机房间（满座时顶替一个机器人座位）
    room/add_bot(*)       -> 房主给空座补机器人（摸切/牌效罗伯特/高性能罗伯特）
    room/start_game       -> 房主开局，缺座自动补满机器人后启动引擎
    gamestate/*           -> handle_action（复用线上实现），引擎按连接广播给所有真人
"""

import asyncio
import base64
import collections
import hashlib
import json
import logging
import os
import random
import socket
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
# 演示局（教学回放）用 4 个机器人补满牌桌；uid 4 只在演示局出现，普通房间不会加到它。
DEMO_BOT_IDS = [1, 2, 3, 4]

# 单机房间标识：客户端建房/开局复用线上 `room/*` 协议。房间号随机 3 位数字，
# 局域网内已有相同房号时重新随机；SOLO_ROOM_ID 仅作为兜底占位。
SOLO_ROOM_ID = "solo-hangzhou"
SOLO_INSTANCE_ID = "solo-instance-1"
BOT_USERNAMES = {1: "机器人·摸切", 2: "牌效罗伯特", 3: "高性能罗伯特", 4: "演示助教"}

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
RULE_LABELS = {HANGZHOU_RULE: "杭州麻将", XIAOSHAN_RULE: "萧山麻将", GUOBIAO_RULE: "国标麻将"}

# 三种加机器人入口对应的机器人 id（1=摸切、2=牌效罗伯特、3=高性能罗伯特），
# 用于房主按入口偏好选人；被占用的 id 自动跳过。
BOT_PREFERRED_IDS = {"room/add_guobiao_heuristic_bot": 3, "room/add_smart_bot": 2, "room/add_bot": 1}

# 局域网房间发现：客户端广播该探测包，服务端回一包 JSON 房间摘要。
LAN_DISCOVERY_PORT = 18764
LAN_DISCOVERY_MAGIC = b"OMU_DISCOVER"
LAN_DISCOVERY_REPLY_MAGIC = "OMU_SOLO"


def _running_under_chaquopy() -> bool:
    """Android 端经 Chaquopy 内嵌运行，无独立进程可注入环境变量，默认开启局域网。"""
    try:
        import java  # noqa: F401  Chaquopy 提供的桥接模块，仅 Android 上存在
        return True
    except Exception:
        return False


def _env_lan_mode() -> bool:
    """SOLO_LAN=1 开启局域网模式；Android(Chaquopy) 默认开启，PC 默认关闭。"""
    return os.environ.get("SOLO_LAN", "1" if _running_under_chaquopy() else "0") == "1"


def _env_host() -> str:
    """监听地址：显式 SOLO_HOST 优先；局域网模式对整个局域网开放，否则仅本机。"""
    host = os.environ.get("SOLO_HOST")
    if host:
        return host
    return "0.0.0.0" if _env_lan_mode() else "127.0.0.1"


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
        # 对端 IP（如 127.0.0.1），用于识别「本机房主连接」以便断线后重连复用房主身份
        self.peer = ""
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
        self._demo_task = None  # 教学演示对局任务（与房间对局互不干扰）
        self._room = None        # 当前单机房间的 room_info；None 表示尚未建房
        self._room_opts = {}     # 建房时选定的开局参数（局数等）
        self._room_password = ""  # 建房密码（加入方需校验）
        self._label_rule = HANGZHOU_RULE             # 对外展示的大厅规则（杭州/萧山）
        self._label_sub_rule = HANGZHOU_SUB_RULE
        self._start_lock = asyncio.Lock()
        # 局域网多人：真人 user_id 动态分配（101 起自增），昵称按序号生成
        self._next_human_id = HUMAN_USER_ID
        self._human_names = {}
        # Android(Chaquopy) 无法由 C# 注入环境变量，默认开启局域网；PC 由 Bootstrap 注入 SOLO_LAN。
        self.lan_mode = _env_lan_mode()
        self._discovery_transport = None


# ----------------------------------------------------------------------------
# 单机对局启动
# ----------------------------------------------------------------------------
def _human_name(server: SoloServer, uid: int) -> str:
    """真人昵称：登录时按序号生成（玩家1/玩家2…），未知 id 兜底显示。"""
    if uid in server._human_names:
        return server._human_names[uid]
    return ("玩家" + str(uid - HUMAN_USER_ID + 1)) if uid > 10 else BOT_USERNAMES.get(uid, f"机器人{uid}")


def _build_room_data(server: SoloServer, rounds: int = 4, step_timer: int = 20,
                     round_timer: int = 300, rule: str = HANGZHOU_RULE, sub_rule: str = None,
                     opts: dict = None) -> dict:
    # 引擎按 room_rule/sub_rule 选择；萧山复用杭州栈，国标走独立引擎。
    if rule == XIAOSHAN_RULE:
        room_rule, sub = XIAOSHAN_RULE, XIAOSHAN_SUB_RULE
    elif rule == GUOBIAO_RULE:
        room_rule, sub = GUOBIAO_RULE, sub_rule or GUOBIAO_SUB_RULE
    else:
        room_rule, sub = HANGZHOU_RULE, HANGZHOU_SUB_RULE
    opts = opts or {}
    # 座位以房间当前人员为准：房主开局时若仍缺人，处理层已自动补满机器人
    room = server._room
    players = list(room["player_list"]) if room is not None else [HUMAN_USER_ID] + BOT_USER_IDS
    # 国标三麻为 3 家；杭州/萧山/国标均为 4 家
    while len(players) < (3 if (rule == GUOBIAO_RULE and sub == "guobiao/sanma") else 4):
        for bot_id in BOT_USER_IDS:
            if bot_id not in players:
                players.append(bot_id)
                break
    # 国标引擎的 GameInfo（服务端与客户端）都要求 room_id 为整数，字符串会校验失败
    # 导致 game_start 发不出去（表现为创建房间后点开始游戏无反应）；杭州引擎不受此限。
    data = dict(
        room_id=0 if rule == GUOBIAO_RULE else "solo-" + room_rule,
        room_rule=room_rule,
        sub_rule=sub,
        player_list=players,
        player_settings=_player_settings_for(server, players),
        game_round=rounds,
        round_timer=round_timer,
        step_timer=step_timer,
        room_type="custom",
        tips=True,
        random_seed=0,
        bot_speed="fast",
        bot_level="medium",
    )
    if rule == GUOBIAO_RULE:
        # 国标特有开局参数：起和番限制、花牌、天地人和、错和、鸣牌保护等
        for key in GUOBIAO_PASSTHROUGH_KEYS:
            if key in opts:
                data[key] = opts[key]
    elif rule == XIAOSHAN_RULE:
        # 萧山开局默认 1 秒节奏（其余玩法沿用 0.5 秒快速档）。
        data["bot_speed"] = "medium"
        # 萧山馆规：开局积分、总倍率封顶与房规开关（如「有财神必须爆头」），
        # 经 detailed_config 交引擎 normalize_config 校验。
        detailed = {key: opts[key] for key in XIAOSHAN_PASSTHROUGH_KEYS if key in opts}
        if isinstance(opts.get("detailed_config"), dict):
            detailed.update(opts["detailed_config"])
        if detailed:
            data["detailed_config"] = detailed
    return data


def _player_settings_for(server: SoloServer, players) -> dict:
    """房间各座位的展示信息，客户端用其中的 username 渲染座位名（真人/机器人通吃）。

    键用 int 的 user_id：引擎按 ``player_settings.get(user_id)``（int）取昵称，
    JSON 序列化后键自动变成字符串，客户端 ``Dictionary<string, UserSettings>`` 不受影响。
    """
    settings = {}
    for uid in players:
        if uid > 10:
            name = _human_name(server, uid)
        else:
            name = BOT_USERNAMES.get(uid, f"机器人{uid}")
        settings[int(uid)] = {
            "user_id": uid, "username": name,
            "title_id": 1, "profile_image_id": 1, "character_id": 1,
            "voice_id": 1, "avatar_frame_id": 0,
        }
    return settings


def _sync_room_view(server: SoloServer) -> None:
    """座位变化后刷新 room_info 的派生字段（seat_list/准备名单/展示信息）。"""
    room = server._room
    if room is None:
        return
    players = room["player_list"]
    room["seat_list"] = list(players)
    room["player_settings"] = _player_settings_for(server, players)
    # 客户端把 user_id<=10 视为机器人且默认「已准备」，ready_list 只需维护真人
    room["ready_list"] = [uid for uid in room.get("ready_list", [])
                          if uid in players and uid > 10]
    host_uid = room.get("host_user_id")
    if host_uid is not None:
        room["host_name"] = _human_name(server, host_uid) if host_uid > 10 else BOT_USERNAMES.get(host_uid, "机器人")


def _local_ip_set() -> set:
    """收集本机所有 IPv4 地址，用于过滤探测时收到的本机应答。"""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.add("127.0.0.1")
    return ips


async def _probe_lan_room_ids(timeout: float = 0.8) -> set:
    """向局域网广播一次发现查询，收集其它主机已开房间的 room_id。

    复用 _DiscoveryProtocol 的应答（OMU_SOLO JSON），本机自己的应答按源 IP 过滤。
    任何失败都返回空集，只影响冲突重随，不阻塞建房。
    """
    loop = asyncio.get_running_loop()
    local_ips = _local_ip_set()
    found = set()
    queue = asyncio.Queue()

    class _ProbeProtocol(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            try:
                sock = transport.get_extra_info("socket")
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass

        def datagram_received(self, data, addr):
            try:
                reply = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return
            room = reply.get("room") or {}
            if reply.get("magic") != LAN_DISCOVERY_REPLY_MAGIC or not room.get("in_room"):
                return
            if addr and addr[0] in local_ips:
                return
            rid = str(room.get("room_id") or "")
            if rid:
                queue.put_nowait(rid)

        def error_received(self, exc):
            pass

    try:
        transport, _ = await loop.create_datagram_endpoint(
            _ProbeProtocol, local_addr=("0.0.0.0", 0), allow_broadcast=True)
    except OSError as exc:
        logger.warning("局域网房号探测 socket 创建失败（跳过冲突检测）: %s", exc)
        return found
    try:
        transport.sendto(LAN_DISCOVERY_MAGIC, ("<broadcast>", LAN_DISCOVERY_PORT))
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                found.add(await asyncio.wait_for(queue.get(), timeout=remaining))
            except asyncio.TimeoutError:
                break
    finally:
        transport.close()
    return found


async def _pick_room_id(server: SoloServer) -> str:
    """房间号随机 3 位数字；局域网内已有相同房号则重新随机。"""
    taken = await _probe_lan_room_ids() if server.lan_mode else set()
    candidate = str(random.randint(100, 999))
    for _ in range(60):
        if candidate not in taken:
            return candidate
        candidate = str(random.randint(100, 999))
    return candidate


def _build_room_info(server: SoloServer, host_uid: int, rounds: int, room_name: str = "",
                     password: str = "", rule: str = HANGZHOU_RULE,
                     sub_rule: str = HANGZHOU_SUB_RULE, opts: dict = None,
                     room_id: str = SOLO_ROOM_ID) -> dict:
    """客户端 RoomPanel 需要的 room_info。

    纯单机（lan_mode=False）保持旧行为：建房即放满机器人，人数一满「开始游戏」即可点击。
    局域网模式（lan_mode=True）初始只有房主一人，其余座位留给加入的真人或房主手动补机器人。
    """
    opts = opts or {}
    sanma = rule == GUOBIAO_RULE and (sub_rule or GUOBIAO_SUB_RULE) == "guobiao/sanma"
    max_player = 3 if sanma else 4
    if server.lan_mode:
        players = [host_uid]
    else:
        players = [host_uid] + (BOT_USER_IDS[:2] if sanma else BOT_USER_IDS)
    info = {
        "room_id": room_id,
        "instance_id": SOLO_INSTANCE_ID,
        "room_type": "custom",
        "room_rule": rule,
        "sub_rule": sub_rule,
        "hepai_limit": opts.get("hepai_limit", 8) if rule == GUOBIAO_RULE else 0,
        "tourist_limit": False,
        "allow_spectator": False,
        "max_player": max_player,
        "player_list": players,
        "seat_list": list(players),
        "ready_list": [],
        "player_settings": {},
        "has_password": bool(password),
        "tips": True,
        "count_tips": False,
        "pointer_tips": True,
        "host_name": _human_name(server, host_uid),
        "host_user_id": host_uid,
        "room_name": room_name or RULE_ROOM_NAMES.get(rule, "单机麻将"),
        "game_round": rounds,
        "round_timer": 300,
        "step_timer": 20,
        "is_game_running": False,
        # 萧山开局默认 1 秒节奏，其余玩法 0.5 秒。
        "bot_speed": "medium" if rule == XIAOSHAN_RULE else "fast",
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
    server._room = info
    _sync_room_view(server)
    return info


async def _broadcast_room_info(server: SoloServer, conn: WsConnection):
    """把最新 room_info 推给客户端，触发 RoomPanel 刷新。"""
    if server._room is None:
        return
    await _send(conn, {"type": "room/refresh_room_info", "success": True,
                       "message": "", "room_info": server._room})


async def _send_room_info(server: SoloServer, conn: WsConnection):
    """推最新 room_info 给单个连接（房间已解散时提示不在房间）。"""
    room = server._room
    if room is None:
        await _send(conn, {"type": "room/sync_not_in_room", "success": True,
                           "message": "当前不在房间中"})
        return
    await _send(conn, {"type": "room/refresh_room_info", "success": True,
                       "message": "", "room_info": room})


async def _broadcast_room_info_all(server: SoloServer):
    """座位/状态变化后，把最新 room_info 推给房间内所有真人连接。"""
    for uid, conn in list(server.user_id_to_connection.items()):
        if uid <= 10:
            continue
        try:
            await _send(conn, {"type": "room/refresh_room_info", "success": True,
                               "message": "", "room_info": server._room})
        except Exception as exc:
            logger.warning("房间广播失败 uid=%s: %s", uid, exc)


async def _notify_others(server: SoloServer, exclude_uid: int, payload: dict):
    """给除 exclude_uid 外的所有真人连接推一条消息（如房主变更/解散通知）。"""
    for uid, conn in list(server.user_id_to_connection.items()):
        if uid <= 10 or uid == exclude_uid:
            continue
        try:
            await _send(conn, payload)
        except Exception:
            pass


async def _dissolve_room(server: SoloServer, exclude_uid: int = None, reason: str = "房主已解散房间") -> None:
    """解散当前房间：结束对局、清空房间状态并通知房内真人。"""
    room = server._room
    if room is None:
        return
    await _stop_solo_game(server)
    members = [uid for uid in room.get("player_list", []) if uid > 10]
    server._room = None
    server._room_opts = {}
    server._room_password = ""
    server._label_rule = HANGZHOU_RULE
    server._label_sub_rule = HANGZHOU_SUB_RULE
    for uid in members:
        if uid == exclude_uid:
            continue
        conn = server.user_id_to_connection.get(uid)
        if conn is None:
            continue
        try:
            await _send(conn, {"type": "room/sync_not_in_room", "success": True,
                               "message": reason})
        except Exception:
            pass


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
        # 对局正常打完：复位房间状态，房主可直接再次开局
        room = server._room
        if room is not None and room.get("is_game_running"):
            room["is_game_running"] = False
            await _broadcast_room_info_all(server)
    except asyncio.CancelledError:
        _note("对局循环被取消")
        raise
    except Exception as exc:
        reason = "%s: %s" % (type(exc).__name__, exc)
        logger.exception("单机对局循环异常退出")
        _note("!! 对局循环异常退出: " + reason)
        for uid, conn in list(server.user_id_to_connection.items()):
            if uid <= 10:
                continue
            try:
                await conn.send_json({
                    "type": "error_message",
                    "success": False,
                    "message": "单机对局启动失败: " + reason,
                })
            except Exception:
                pass


def _new_game_state(server: SoloServer, room_data: dict, rule: str):
    """按大厅规则实例化引擎：萧山复用杭州栈，国标走独立引擎并挂计算服务。"""
    calculation = None
    if rule == XIAOSHAN_RULE:
        from server.gamestate.game_xiaoshan.XiaoshanGameState import XiaoshanGameState
        engine = XiaoshanGameState
    elif rule == GUOBIAO_RULE:
        from server.gamestate.game_guobiao.GuobiaoGameState import GuobiaoGameState
        from server.game_calculation.game_calculation_service import GameCalculationService
        engine = GuobiaoGameState
        calculation = GameCalculationService()  # 国标听牌/和牌校验依赖计算服务
    else:
        from server.gamestate.game_hangzhou.HangzhouGameState import HangzhouGameState
        engine = HangzhouGameState
    # 国标引擎会调用 db_manager 存取段位/牌谱（单机空实现），杭州/萧山不查库传 None。
    # gamestate_id 每局必须唯一：客户端按它区分新旧对局并重置询问时钟残留，
    # 固定值会让「离开房间重建后再开局」沿用上一场的 tick 状态，真人出牌被服务端丢弃。
    return engine(server, room_data, calculation,
                  _DbStub() if rule == GUOBIAO_RULE else None,
                  "solo-" + uuid.uuid4().hex[:8])


async def start_solo_game(server: SoloServer, conn: WsConnection, **opts):
    async with server._start_lock:
        # 每次开局都先结束上一局，保证客户端总能收到一份新的 game_start
        # （否则大厅里再次点「开始游戏」会复用尚未结束的旧对局，界面毫无反应）。
        await _stop_solo_game(server)
        rule = server._label_rule

        room_data = _build_room_data(
            server,
            rounds=int(opts.get("rounds") or 4),
            step_timer=int(opts.get("step_timer") or 20),
            round_timer=int(opts.get("round_timer") or 300),
            rule=rule, sub_rule=opts.get("sub_rule"), opts=opts)
        state = _new_game_state(server, room_data, rule)
        try:
            from server.gamestate.public.ai.pacing import DEFAULT_BOT_SPEED, configure_bot_pacing, normalize_bot_speed
            room_data.setdefault("bot_speed", DEFAULT_BOT_SPEED)
            # 房间面板改过节奏时按房间当前值开局；旧值不在档位表里会归一成默认。
            room_data["bot_speed"] = normalize_bot_speed(
                (server._room or {}).get("bot_speed") or room_data["bot_speed"])
            configure_bot_pacing(state, room_data)
        except Exception as exc:      # 配置失败不影响对局
            logger.warning("configure_bot_pacing 失败: %s", exc)
        try:
            from server.gamestate.public.ai.level import normalize_bot_level
            state.bot_level = normalize_bot_level((server._room or {}).get("bot_level"))
        except Exception as exc:      # 配置失败不影响对局
            logger.warning("configure_bot_level 失败: %s", exc)

        server._game = state
        server.gamestate_manager._game = state
        state.game_task = asyncio.create_task(_run_game_loop(server, state))
        logger.info("单机%s对局已启动 gamestate_id=%s 玩家=%s",
                    RULE_LABELS.get(rule, rule), state.gamestate_id, room_data["player_list"])
        _note("已开局 gamestate_id=" + str(state.gamestate_id))
        return state


# ----------------------------------------------------------------------------
# 教学演示：4 机器人 instant 速度自动打一局，产出完整牌谱供客户端回放讲解
# ----------------------------------------------------------------------------
def _build_demo_room_data(server: SoloServer, rule: str, rounds: int) -> dict:
    """演示牌桌：固定 4 个机器人座位，无真人连接，instant 速度快速跑完。"""
    if rule == XIAOSHAN_RULE:
        room_rule, sub = XIAOSHAN_RULE, XIAOSHAN_SUB_RULE
    elif rule == GUOBIAO_RULE:
        room_rule, sub = GUOBIAO_RULE, GUOBIAO_SUB_RULE
    else:
        room_rule, sub = HANGZHOU_RULE, HANGZHOU_SUB_RULE
    return dict(
        room_id=0 if rule == GUOBIAO_RULE else "solo-demo",
        room_rule=room_rule,
        sub_rule=sub,
        player_list=list(DEMO_BOT_IDS),
        player_settings=_player_settings_for(server, DEMO_BOT_IDS),
        game_round=rounds,
        round_timer=300,
        step_timer=20,
        room_type="custom",
        tips=True,
        random_seed=0,
        bot_speed="instant",
        bot_level="medium",
    )


async def _run_demo_loop(server: SoloServer, state, conn: WsConnection):
    """跑完演示局后把完整牌谱（record_detail）回给请求方，客户端据此打开回放。"""
    try:
        await state.run_game_loop()
        # complete_game_lifecycle 已把牌谱挂到 state._local_record_detail
        detail = getattr(state, "_local_record_detail", None)
        if detail is None:
            _note("!! 演示牌谱缺失")
            await _send(conn, {"type": "solo/demo_record", "success": False,
                               "message": "演示牌谱生成失败"})
            return
        _note("演示牌局完成，牌谱已下发")
        await _send(conn, {"type": "solo/demo_record", "success": True, "message": "",
                           "record_detail": detail.dict(exclude_none=True)})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("演示对局异常退出")
        _note("!! 演示对局异常: %s" % exc)
        try:
            await _send(conn, {"type": "solo/demo_record", "success": False,
                               "message": "演示牌局生成失败: %s" % exc})
        except Exception:
            pass


async def _handle_start_demo(server: SoloServer, conn: WsConnection, message: dict):
    """solo/start_demo：后台跑一局 4 机器人演示局，结束后下发 solo/demo_record。"""
    task = server._demo_task
    if task is not None and not task.done():
        await _send(conn, {"type": "solo/start_demo", "success": False,
                           "message": "演示牌局正在生成中，请稍候"})
        return
    rule = message.get("rule")
    if rule not in RULE_LABELS:  # 未指定或非法时按杭州演示
        rule = HANGZHOU_RULE
    try:
        rounds = max(1, min(int(message.get("rounds") or 1), 4))
    except (TypeError, ValueError):
        rounds = 1
    room_data = _build_demo_room_data(server, rule, rounds)
    state = _new_game_state(server, room_data, rule)
    try:
        from server.gamestate.public.ai.pacing import configure_bot_pacing
        configure_bot_pacing(state, room_data)
    except Exception as exc:      # 配置失败不影响演示
        logger.warning("演示 configure_bot_pacing 失败: %s", exc)
    server._demo_task = asyncio.create_task(_run_demo_loop(server, state, conn))
    _note("演示牌局已启动 rule=%s rounds=%d" % (rule, rounds))
    await _send(conn, {"type": "solo/start_demo", "success": True,
                       "message": "演示牌局生成中，请稍候"})


# ----------------------------------------------------------------------------
# 消息分发
# ----------------------------------------------------------------------------
async def _send(conn: WsConnection, payload: dict):
    await conn.send_json(payload)


# ----------------------------------------------------------------------------
# 房间协议处理（局域网多真人）
# ----------------------------------------------------------------------------
def _is_local_connection(conn: WsConnection) -> bool:
    """是否来自本机（房主客户端）。房主断线重连时借此复用房主身份。"""
    return conn.peer in ("127.0.0.1", "::1", "")


def _is_room_host(server: SoloServer, conn: WsConnection) -> bool:
    room = server._room
    return room is not None and conn.user_id is not None and conn.user_id == room.get("host_user_id")


def _acquire_user_id(server: SoloServer, conn: WsConnection) -> int:
    """登录分配真人 user_id；本机重连且房主座位空置时沿用原房主身份。"""
    room = server._room
    if room is not None and _is_local_connection(conn):
        host_uid = room.get("host_user_id")
        if isinstance(host_uid, int) and host_uid > 10 \
                and server.user_id_to_connection.get(host_uid) is None:
            return host_uid
    uid = server._next_human_id
    server._next_human_id += 1
    server._human_names[uid] = f"玩家{uid - HUMAN_USER_ID + 1}"
    return uid


async def _handle_get_room_list(server: SoloServer, conn: WsConnection, message: dict):
    """单机服务只有一个房间；返回它让加入方的房间列表能看到（也可走 UDP 发现）。"""
    room = server._room
    room_list = [dict(room)] if room is not None else []
    await _send(conn, {"type": "room/get_room_list", "success": True, "message": "",
                       "room_list": room_list, "show_tip": bool(message.get("show_tip"))})


async def _handle_create_room(server: SoloServer, conn: WsConnection, message: dict):
    """建房。局域网模式下初始只有房主一人；重复建房视为房主重建（可改规则/参数）。"""
    room = server._room
    uid = conn.user_id
    if room is not None and uid not in room.get("player_list", []):
        await _send(conn, {"type": "room/create_room_done", "success": False,
                           "message": "本机已存在房间，无法重复创建"})
        return
    if room is not None:
        await _dissolve_room(server, exclude_uid=uid, reason="房主重建了房间")
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
    server._room_password = str(message.get("password") or "")
    room_id = await _pick_room_id(server)
    _build_room_info(server, uid, rounds, room_name=message.get("roomname", ""),
                     password=server._room_password, rule=rule, sub_rule=sub_rule, opts=opts,
                     room_id=room_id)
    logger.info("已创建单机房间 %s，规则=%s，玩家=%s，局域网=%s",
                room_id, rule, server._room["player_list"], server.lan_mode)
    await _send(conn, {"type": "room/create_room_done", "success": True,
                       "message": "房间创建成功", "room_info": server._room})


async def _handle_add_bot(server: SoloServer, conn: WsConnection, message: dict, mtype: str):
    """房主给空座补机器人；三种入口对应偏好机器人，被占用则自动顺延。"""
    room = server._room
    if room is None:
        await _send(conn, {"type": "error_message", "success": False, "message": "尚未创建房间"})
        return
    if room.get("is_game_running"):
        await _send(conn, {"type": "error_message", "success": False, "message": "对局进行中，无法加机器人"})
        return
    if not _is_room_host(server, conn):
        await _send(conn, {"type": "error_message", "success": False, "message": "只有房主可以加机器人"})
        return
    players = room["player_list"]
    max_player = int(room.get("max_player") or 4)
    if len(players) >= max_player:
        await _send(conn, {"type": "error_message", "success": False, "message": "房间已满座"})
        return
    free = [uid for uid in BOT_USER_IDS if uid not in players]
    if not free:
        await _send(conn, {"type": "error_message", "success": False, "message": "机器人已用完"})
        return
    preferred = BOT_PREFERRED_IDS.get(mtype, 1)
    bot_uid = preferred if preferred in free else free[0]
    players.append(bot_uid)
    _sync_room_view(server)
    _note("房主加机器人 uid=" + str(bot_uid) + " 座位=" + str(players))
    await _broadcast_room_info_all(server)


async def _handle_kick_player(server: SoloServer, conn: WsConnection, message: dict):
    """房主移出玩家/机器人；支持 seat_index 或 target_user_id 定位。"""
    room = server._room
    if room is None:
        await _send(conn, {"type": "error_message", "success": False, "message": "尚未创建房间"})
        return
    if not _is_room_host(server, conn):
        await _send(conn, {"type": "error_message", "success": False, "message": "只有房主可以移出玩家"})
        return
    players = room["player_list"]
    target = message.get("target_user_id")
    seat = message.get("seat_index")
    if not isinstance(target, int) and isinstance(seat, int) and 0 <= seat < len(players):
        target = players[seat]
    if not isinstance(target, int) or target not in players:
        await _send(conn, {"type": "error_message", "success": False, "message": "目标座位没有玩家"})
        return
    if target == room.get("host_user_id"):
        await _send(conn, {"type": "error_message", "success": False, "message": "不能移出房主"})
        return
    players.remove(target)
    _sync_room_view(server)
    _note("房主移出 uid=" + str(target) + " 座位=" + str(players))
    if target > 10:
        kicked_conn = server.user_id_to_connection.get(target)
        if kicked_conn is not None:
            try:
                await _send(kicked_conn, {"type": "room/sync_not_in_room", "success": True,
                                          "message": "房主已将你移出房间"})
            except Exception:
                pass
    await _broadcast_room_info_all(server)


async def _handle_set_ready(server: SoloServer, conn: WsConnection, message: dict):
    """非房主真人的准备状态；机器人视为永远已准备（客户端约定 uid<=10）。"""
    room = server._room
    uid = conn.user_id
    if room is None or uid is None or uid not in room.get("player_list", []):
        return
    ready_list = room.setdefault("ready_list", [])
    ready = bool(message.get("ready"))
    if ready and uid not in ready_list:
        ready_list.append(uid)
    if not ready and uid in ready_list:
        ready_list.remove(uid)
    await _broadcast_room_info_all(server)


async def _handle_join_room(server: SoloServer, conn: WsConnection, message: dict):
    """其它客户端加入本机房间；满座时可顶替一个机器人座位。"""
    room = server._room
    if room is None:
        await _send(conn, {"type": "room/join_room_done", "success": False,
                           "message": "本机暂无开放房间"})
        return
    if room.get("is_game_running"):
        await _send(conn, {"type": "room/join_room_done", "success": False,
                           "message": "对局已开始，无法加入"})
        return
    if server._room_password and str(message.get("password") or "") != server._room_password:
        await _send(conn, {"type": "room/join_room_done", "success": False,
                           "message": "房间密码错误"})
        return
    uid = conn.user_id
    players = room["player_list"]
    if uid in players:
        await _send(conn, {"type": "room/join_room_done", "success": True,
                           "message": "已在房间中", "room_info": room})
        await _send_room_info(server, conn)
        return
    bot_seat = None
    if len(players) >= int(room.get("max_player") or 4):
        for i, seat_uid in enumerate(players):
            if seat_uid <= 10:
                bot_seat = i
                break
        if bot_seat is None:
            await _send(conn, {"type": "room/join_room_done", "success": False,
                               "message": "房间已满"})
            return
    if bot_seat is not None:
        players[bot_seat] = uid
    else:
        players.append(uid)
    _sync_room_view(server)
    _note("玩家加入房间 uid=" + str(uid) + " 座位=" + str(players))
    await _send(conn, {"type": "room/join_room_done", "success": True,
                       "message": "加入房间成功", "room_info": room})
    await _broadcast_room_info_all(server)


async def _handle_start_game(server: SoloServer, conn: WsConnection, message: dict):
    """房主开局；缺座自动补满机器人后启动引擎（引擎按连接广播 game_start）。"""
    room = server._room
    if room is None:
        await _send(conn, {"type": "error_message", "success": False, "message": "尚未创建房间"})
        return
    if room.get("is_game_running"):
        await _send(conn, {"type": "error_message", "success": False, "message": "对局已开始"})
        return
    if not _is_room_host(server, conn):
        await _send(conn, {"type": "error_message", "success": False, "message": "只有房主可以开始游戏"})
        return
    players = room["player_list"]
    max_player = int(room.get("max_player") or 4)
    # 缺人的座位自动补机器人，保证引擎能凑齐一桌
    changed = False
    while len(players) < max_player:
        for bot_id in BOT_USER_IDS:
            if bot_id not in players:
                players.append(bot_id)
                changed = True
                break
        else:
            break
    if changed:
        _sync_room_view(server)
        _note("开局自动补机器人 座位=" + str(players))
    room["is_game_running"] = True
    await _broadcast_room_info_all(server)
    await start_solo_game(server, conn, **server._room_opts)


async def _handle_leave_room(server: SoloServer, conn: WsConnection, message: dict):
    """离开房间：房主离开→解散并通知；普通真人离开→移出座位并广播。"""
    room = server._room
    uid = conn.user_id
    done = {"type": "room/leave_room_done", "success": True, "message": "离开房间成功",
            "room_id": (room or {}).get("room_id") or SOLO_ROOM_ID,
            "room_instance_id": SOLO_INSTANCE_ID}
    if room is None or uid is None or uid not in room.get("player_list", []):
        await _send(conn, done)
        return
    if room.get("is_game_running"):
        await _stop_solo_game(server)
        room["is_game_running"] = False
    if uid == room.get("host_user_id"):
        await _dissolve_room(server, exclude_uid=uid, reason="房主已离开，房间解散")
        await _send(conn, done)
        return
    room["player_list"].remove(uid)
    _sync_room_view(server)
    _note("玩家离开房间 uid=" + str(uid) + " 座位=" + str(room["player_list"]))
    await _send(conn, done)
    await _broadcast_room_info_all(server)


async def _handle_sync_my_room(server: SoloServer, conn: WsConnection):
    """客户端重连/切页后同步自己是否在房间及最新房间数据。"""
    room = server._room
    uid = conn.user_id
    if room is not None and uid is not None and uid in room.get("player_list", []):
        await _send_room_info(server, conn)
    else:
        await _send(conn, {"type": "room/sync_not_in_room", "success": True,
                           "message": "当前不在房间中"})


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
        uid = _acquire_user_id(server, conn)
        conn.user_id = uid
        server.players[conn.Connect_id] = conn
        server.user_id_to_connection[uid] = conn
        await _send(conn, {
            "type": "login",
            "success": True,
            "message": "登录成功",
            "login_info": {
                "user_id": uid,
                "username": _human_name(server, uid),
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
    # 局域网模式下这些处理支持多真人：加入、准备、房主补机器人/踢人、开局广播。
    if mtype == "room/get_room_list":
        await _handle_get_room_list(server, conn, message)
        return

    if mtype in ("room/create_Hangzhou_room", "room/create_Xiaoshan_room", "room/create_GB_room"):
        await _handle_create_room(server, conn, message)
        return

    if mtype.startswith("room/create_"):
        # 单机内嵌的引擎只实现了杭州麻将（萧山为其别名）与国标麻将，其它规则直接拒绝
        await _send(conn, {"type": "room/create_room_done", "success": False,
                           "message": "单机版仅支持杭州麻将、萧山麻将和国标麻将"})
        return

    if mtype in ("room/add_bot", "room/add_smart_bot", "room/add_guobiao_heuristic_bot"):
        await _handle_add_bot(server, conn, message, mtype)
        return

    if mtype == "room/kick_player":
        await _handle_kick_player(server, conn, message)
        return

    if mtype == "room/set_ready":
        await _handle_set_ready(server, conn, message)
        return

    if mtype == "room/join_room":
        await _handle_join_room(server, conn, message)
        return

    if mtype == "room/start_game":
        await _handle_start_game(server, conn, message)
        return

    if mtype == "room/leave_room":
        await _handle_leave_room(server, conn, message)
        return

    if mtype == "room/sync_my_room":
        await _handle_sync_my_room(server, conn)
        return

    if mtype == "solo/start_demo":
        await _handle_start_demo(server, conn, message)
        return

    if mtype == "room/set_claim_protection":
        if server._room is not None:
            server._room["claim_protection"] = bool(message.get("claim_protection"))
            await _broadcast_room_info_all(server)
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
            await _broadcast_room_info_all(server)
        return

    if mtype == "room/set_bot_level":
        level = message.get("bot_level")
        if server._room is not None and isinstance(level, str) and level:
            server._room["bot_level"] = level
            # 对局进行中也即时生效；非法档位由 normalize 归一成默认。
            game = getattr(server, "_game", None)
            if game is not None:
                from server.gamestate.public.ai.level import normalize_bot_level
                game.bot_level = normalize_bot_level(level)
            await _broadcast_room_info_all(server)
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
    try:
        peername = writer.get_extra_info("peername")
        if isinstance(peername, tuple) and peername:
            conn.peer = str(peername[0])
    except Exception:
        pass
    logger.info("客户端已连接 %s path=%s peer=%s", conn.Connect_id, path, conn.peer or "?")
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
        await _on_connection_lost(server, conn)
        logger.info("客户端断开 %s", conn.Connect_id)
        _note("客户端断开 " + str(conn.Connect_id))


async def _on_connection_lost(server: SoloServer, conn: WsConnection):
    """真人断线后的房间收尾：房主保留座位等待重连，普通真人移出座位。

    对局中任一真人掉线都会结束对局（引擎无法实时补位），其余玩家退回房间界面。
    """
    room = server._room
    uid = conn.user_id
    if room is None or uid is None or uid not in room.get("player_list", []):
        return
    if room.get("is_game_running"):
        await _stop_solo_game(server)
        room["is_game_running"] = False
    if uid == room.get("host_user_id"):
        _note("房主断线，保留房间等待重连")
        await _broadcast_room_info_all(server)
        return
    room["player_list"].remove(uid)
    _sync_room_view(server)
    _note("玩家断线移出 uid=" + str(uid) + " 座位=" + str(room["player_list"]))
    await _broadcast_room_info_all(server)


def _room_summary(server: SoloServer) -> dict:
    """UDP 发现应答携带的房间摘要（加入方据此展示房间列表并决定是否加入）。"""
    room = server._room
    if room is None:
        return {"in_room": False}
    players = room.get("player_list", [])
    return {
        "in_room": True,
        "room_id": room.get("room_id"),
        "room_name": room.get("room_name"),
        "rule": room.get("room_rule"),
        "rule_label": RULE_LABELS.get(room.get("room_rule"), room.get("room_rule")),
        "host_name": room.get("host_name"),
        "human_count": len([uid for uid in players if uid > 10]),
        "player_count": len(players),
        "max_player": room.get("max_player"),
        "has_password": room.get("has_password"),
        "is_game_running": room.get("is_game_running"),
    }


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """局域网房间发现应答器：收到广播探测包即回一份单包 JSON 摘要。"""

    def __init__(self, server: SoloServer, ws_port: int):
        self.server = server
        self.ws_port = ws_port
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() != LAN_DISCOVERY_MAGIC:
            return
        reply = {
            "magic": LAN_DISCOVERY_REPLY_MAGIC,
            "port": self.ws_port,
            "room": _room_summary(self.server),
        }
        try:
            self.transport.sendto(json.dumps(reply, ensure_ascii=False).encode("utf-8"), addr)
        except Exception:
            pass

    def error_received(self, exc):
        logger.warning("UDP 发现应答出错: %s", exc)


async def _start_discovery(server: SoloServer, ws_port: int):
    """绑定 UDP 发现端口并开始应答（绑定失败不影响对局，仅无房间发现）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 同机多开（两台 PC 客户端测试）需要允许复用端口；应答内容各自独立。
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", LAN_DISCOVERY_PORT))
    except OSError as exc:
        logger.warning("UDP 发现端口 %d 绑定失败（不影响对局）: %s", LAN_DISCOVERY_PORT, exc)
        sock.close()
        return
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _DiscoveryProtocol(server, ws_port), sock=sock)
    server._discovery_transport = transport
    _note("UDP 房间发现已开启 port=" + str(LAN_DISCOVERY_PORT))


async def run_server(host=None, port=None, ready=None):
    server = SoloServer()
    if host is None:
        host = _env_host()
    if port is None:
        port = int(os.environ.get("SOLO_PORT", "18763"))
    srv = await asyncio.start_server(
        lambda r, w: _serve_connection(server, r, w), host, port)
    logger.info("单机服务已监听 ws://%s:%d/game（局域网=%s）", host, port, server.lan_mode)
    if server.lan_mode:
        await _start_discovery(server, port)
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

    host = _env_host()
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
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
