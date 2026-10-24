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
SHAOXING_RULE = "shaoxing"
SHAOXING_SUB_RULE = "shaoxing/shaoxing"
TAIZHOU_RULE = "taizhou"
TAIZHOU_SUB_RULE = "taizhou/taizhou"
YIWU_RULE = "yiwu"
YIWU_SUB_RULE = "yiwu/yiwu"
GUOBIAO_RULE = "guobiao"
GUOBIAO_SUB_RULE = "guobiao/standard"
RULE_ROOM_NAMES = {HANGZHOU_RULE: "单机杭州麻将", XIAOSHAN_RULE: "单机萧山麻将",
                   SHAOXING_RULE: "单机绍兴麻将", TAIZHOU_RULE: "单机台州麻将",
                   YIWU_RULE: "单机义乌麻将", GUOBIAO_RULE: "单机国标麻将"}
RULE_LABELS = {HANGZHOU_RULE: "杭州麻将", XIAOSHAN_RULE: "萧山麻将",
               SHAOXING_RULE: "绍兴麻将", TAIZHOU_RULE: "台州麻将",
               YIWU_RULE: "义乌麻将", GUOBIAO_RULE: "国标麻将"}

# 复用杭州栈的大厅变体（萧山/绍兴/台州/义乌）→ 各自 sub_rule；
# 客户端按 (room_rule, sub_rule) 解析规则清单，变体引擎 ROOM_RULE/SUB_RULE_ID 与此一致。
VARIANT_SUB_RULES = {XIAOSHAN_RULE: XIAOSHAN_SUB_RULE, SHAOXING_RULE: SHAOXING_SUB_RULE,
                     TAIZHOU_RULE: TAIZHOU_SUB_RULE, YIWU_RULE: YIWU_SUB_RULE}

# 三种加机器人入口对应的机器人 id（1=摸切、2=牌效罗伯特、3=高性能罗伯特），
# 用于房主按入口偏好选人；被占用的 id 自动跳过。
BOT_PREFERRED_IDS = {"room/add_guobiao_heuristic_bot": 3, "room/add_smart_bot": 2, "room/add_bot": 1}

# 三个机器人各有人设难度（杭州家族 AI）：
#   1=机器人·摸切    → "moqie"  纯摸切流，不杠不鸣，永远打刚摸的牌（简单陪练）
#   2=牌效罗伯特     → "medium" 标准牌效
#   3=高性能罗伯特   → "hard"   按进张数严格决策
# 房主在房间里显式改过机器人难度（room/set_bot_level）后统一覆盖人设；
# 不在人设表内的机器人（如 4=演示助教）始终走房间级 bot_level。
BOT_SEAT_LEVELS = {1: "moqie", 2: "medium", 3: "hard"}


def _bot_levels_for_players(players, room=None):
    """按座位生成机器人难度表；room 带 bot_level_locked（房主改过难度）时统一覆盖。"""
    players = list(players or [])
    if room is not None and room.get("bot_level_locked"):
        from server.gamestate.public.ai.level import normalize_bot_level
        uniform = normalize_bot_level(room.get("bot_level"))
        return {i: uniform for i, uid in enumerate(players) if uid <= 10}
    return {i: BOT_SEAT_LEVELS[uid] for i, uid in enumerate(players) if uid in BOT_SEAT_LEVELS}

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
    """从建房请求中解析大厅规则；仅杭州系与国标有效，其它一律回退杭州。"""
    rule = message.get("rule") or HANGZHOU_RULE
    if rule == XIAOSHAN_RULE:
        return XIAOSHAN_RULE, message.get("sub_rule") or XIAOSHAN_SUB_RULE
    if rule == SHAOXING_RULE:
        return SHAOXING_RULE, message.get("sub_rule") or SHAOXING_SUB_RULE
    if rule == TAIZHOU_RULE:
        return TAIZHOU_RULE, message.get("sub_rule") or TAIZHOU_SUB_RULE
    if rule == YIWU_RULE:
        return YIWU_RULE, message.get("sub_rule") or YIWU_SUB_RULE
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

    async def cleanup_game_state_complete(self, gamestate_id=None, **_kwargs):
        # 单机无全局注册表，收尾为空操作；**_kwargs 兼容 reason 等线上参数
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
    # 引擎按 room_rule/sub_rule 选择；杭州系变体复用杭州栈，国标走独立引擎。
    if rule == GUOBIAO_RULE:
        room_rule, sub = GUOBIAO_RULE, sub_rule or GUOBIAO_SUB_RULE
    elif rule in VARIANT_SUB_RULES:
        room_rule, sub = rule, VARIANT_SUB_RULES[rule]
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
    elif rule in (XIAOSHAN_RULE, SHAOXING_RULE, YIWU_RULE):
        # 萧山开局默认 1 秒节奏（其余玩法沿用 0.5 秒快速档）。
        data["bot_speed"] = "medium"
        # 馆规：开局积分、总倍率封顶与房规开关（如「有财神必须爆头」），
        # 经 detailed_config 交引擎 normalize_config 校验。绍兴/义乌未传时
        # 用各自引擎默认（绍兴 8 倍封顶、义乌 64 倍封顶）。
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
        # 翻财系变体（萧山/绍兴/义乌）开局默认 1 秒节奏，其余玩法 0.5 秒。
        "bot_speed": "medium" if rule in (XIAOSHAN_RULE, SHAOXING_RULE, YIWU_RULE) else "fast",
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
    elif rule in (XIAOSHAN_RULE, SHAOXING_RULE, YIWU_RULE):
        # 翻财系变体馆规回填到房间信息，供客户端房间面板展示开局积分/倍率封顶
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
    elif rule == SHAOXING_RULE:
        from server.gamestate.game_shaoxing.ShaoxingGameState import ShaoxingGameState
        engine = ShaoxingGameState
    elif rule == TAIZHOU_RULE:
        from server.gamestate.game_taizhou.TaizhouGameState import TaizhouGameState
        engine = TaizhouGameState
    elif rule == YIWU_RULE:
        from server.gamestate.game_yiwu.YiwuGameState import YiwuGameState
        engine = YiwuGameState
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
        # 机器人人设难度（摸切/中等/困难；房主改过难度则统一覆盖）
        state.bot_level_by_index = _bot_levels_for_players(
            room_data.get("player_list") or [], server._room)

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
    if rule == GUOBIAO_RULE:
        room_rule, sub = GUOBIAO_RULE, GUOBIAO_SUB_RULE
    elif rule in VARIANT_SUB_RULES:
        room_rule, sub = rule, VARIANT_SUB_RULES[rule]
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
# 交互式新手教程：真人坐庄 + 固定教学牌墙 + 行动窗口推送提示（solo/start_tutorial）
# ----------------------------------------------------------------------------
TUTORIAL_BOTS = [1, 2, 3]  # 教程座位 1-3 固定为机器人，真人固定 0 号位（庄家）

# 牌 id：万 11-19 / 筒 21-29 / 条 31-39 / 东南西北 41-44 / 中 45、白 46（财神）、发 47
_TILE_NAMES = {41: "东风", 42: "南风", 43: "西风", 44: "北风", 45: "红中", 46: "白板", 47: "发财"}


def _tile_display_name(tile) -> str:
    if not isinstance(tile, int):
        return str(tile)
    if 11 <= tile <= 19:
        return f"{tile - 10}万"
    if 21 <= tile <= 29:
        return f"{tile - 20}筒"
    if 31 <= tile <= 39:
        return f"{tile - 30}条"
    return _TILE_NAMES.get(tile, str(tile))


def _build_scenario_wall(h0, h1, h2, h3, draw_seq, fill_seed=1, flip=None):
    """把 4 家 13 张手牌与摸牌序列装配成 136 张完整牌墙。

    发牌顺序由 initialize_round 固定：每轮庄家（0 号位）起每家拿 4 张，共 3 轮
    （占墙前 48 张）；随后 5 张依次是庄/闲1 的第 13 张、庄家第 14 张（首摸）、
    闲2/闲3 的第 13 张；此后 wall[53] 起为按座次的自然摸牌，杠补牌从墙尾取。

    开局会 pop 出牌墙首张作翻财神指示牌的玩法（萧山/义乌 pop 一张、绍兴在
    萧山之后再 pop 一张），消耗摸牌序列头部的牌。传 flip 指定牺牲牌（单张或
    序列）顶到 wall[53] 起，既满足翻牌又保住后续摸牌剧本；序列最后一张即
    指示牌，决定财神身份，须选对各家手牌无影响的牌。
    """
    import random as _random
    from server.game_calculation.hangzhou.rules import TILES as _HZ_TILES
    flips = [flip] if isinstance(flip, int) else list(flip or ())
    for hand in (h0, h1, h2, h3):
        if len(hand) != 13:
            raise ValueError("教程手牌必须 13 张")
    used = collections.Counter(h0) + collections.Counter(h1) + collections.Counter(h2) \
        + collections.Counter(h3) + collections.Counter(draw_seq)
    for tile in flips:
        if tile not in _HZ_TILES:
            raise ValueError("翻牌牺牲牌不在牌集内")
        used[tile] += 1
    if any(n > 4 for n in used.values()):
        raise ValueError("教程场景用牌超出 4 张上限")
    wall = [None] * 136

    def put(pos, tiles):
        wall[pos:pos + len(tiles)] = list(tiles)

    for row in range(3):
        base = row * 16
        put(base, h0[row * 4:row * 4 + 4])
        put(base + 4, h1[row * 4:row * 4 + 4])
        put(base + 8, h2[row * 4:row * 4 + 4])
        put(base + 12, h3[row * 4:row * 4 + 4])
    put(48, [h0[12], h1[12], draw_seq[0], h2[12], h3[12]])
    planned = list(draw_seq[1:])  # 从牺牲牌之后起按座次的摸牌
    filler = [t for t in _HZ_TILES for _ in range(4 - used.get(t, 0))]
    _random.Random(fill_seed).shuffle(filler)
    wall[53:] = flips + planned + filler
    return wall


# 教程机器人底牌：条子连张 + 字牌散张、全部单张——天然无法鸣牌（更不会碰/吃
# 真人打出的牌），保证教学流程不受机器人鸣牌干扰。
_BOT_A = [31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42, 43, 45]
_BOT_B = [31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 44, 46, 47]
_BOT_C = [33, 34, 35, 36, 37, 38, 39, 42, 43, 44, 45, 46, 47]
# peng 场景变体：不含 43（43 要凑成真人手里的对子+教学牌）
_BOT_A_NO43 = [31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42, 45, 47]
_BOT_C_NO43 = [33, 34, 35, 36, 37, 38, 39, 41, 42, 44, 45, 46, 47]

# ---------------------------------------------------------------------------
# 场景设计说明（配合机器人人设后全部确定性触发）：
#   座位 1 是摸切流机器人（永远打刚摸的牌）→ 它每轮弃什么完全由 draw_seq 决定；
#   教学关键牌固定放在 draws[1]，即座位 1 首轮摸牌后必定打出，触发真人窗口。
#   真人是庄家：draws[0] 是首摸。真人首打的牌对机器人无威胁
#   （机器人底牌全单张，碰不成刻子；座位 1 摸切流从不鸣牌）。
#   座位 2=中等、座位 3=困难，本轮教学中不参与关键触发。
TUTORIAL_SCENARIOS = {
    # 学出牌：杂牌起手 + 摸进红中，提示打一张即可（无胡、无杠）
    "chu": dict(title="学会出牌",
                hands=[[11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 43, 44], _BOT_A, _BOT_B, _BOT_C],
                draws=[45, 46, 47, 21]),
    # 学吃：真人手里 12、13（+对 14）；座位 3（真人的上家——吃只对上家）
    # 起手全是搭子，摸进 14 是全场唯一孤张。上家座位 3 覆盖成摸切流
    # （摸切必打刚摸的牌 → 必打 14；hard 档算分会认为财神邻张 47 更低分，
    # 教学需要确定性触发）→ 真人吃成 1-2-3 万（或碰 14）。
    # 真人首打 21（座位 3 只有 1 张 21 碰不成；座位 1 摸切流从不鸣牌）。
    "chi": dict(title="学会吃",
                hands=[[13, 14, 14, 21, 22, 23, 31, 32, 33, 41, 42, 43, 46],
                       _BOT_A, _BOT_B,
                       [21, 22, 23, 24, 25, 26, 27, 28, 29, 45, 45, 46, 47]],
                draws=[12, 11, 43, 14], seat_levels={3: "moqie"}),
    # 学碰：真人手里 43 两张，座位 1 摸进 43（draws[1]）摸切打出 → 碰成刻子；
    # 真人首摸 46（财神）后打掉，财神弃牌天然不可鸣，避免流程分叉
    "peng": dict(title="学会碰",
                 hands=[[43, 43, 11, 12, 13, 21, 22, 23, 31, 32, 33, 46, 47],
                        _BOT_A_NO43, _BOT_B, _BOT_C_NO43],
                 draws=[46, 43, 44, 21]),
    # 学杠：真人手里 11 三张（散牌断刻防自摸），首摸第 4 张 11 → 暗杠
    "angang": dict(title="学会杠",
                   hands=[[11, 11, 11, 21, 31, 32, 41, 42, 43, 44, 45, 46, 47], _BOT_A, _BOT_B, _BOT_C],
                   draws=[11, 46, 44, 21]),
    # 学胡牌：三组刻子 + 15 单张，首摸第 2 张 15 → 自摸
    "hu": dict(title="学会胡牌",
               hands=[[11, 11, 11, 12, 12, 12, 13, 13, 13, 14, 14, 14, 15], _BOT_A, _BOT_B, _BOT_C],
               draws=[15, 46, 44, 21]),
}

# 各玩法一句话简介：客户端选择面板展示 + 教程开始消息附带
TUTORIAL_RULE_BRIEFS = {
    HANGZHOU_RULE: "经典杭州玩法：翻牌下一张是财神，可爆头财飘",
    XIAOSHAN_RULE: "家庭房规：仅自摸，翻牌本身就是财神",
    SHAOXING_RULE: "翻牌加一（翻五万六万财神），同种四张全财神，8 倍封顶",
    TAIZHOU_RULE: "黄岩打法：白板是财神，MIL 4 番封顶，轮庄",
    YIWU_RULE: "翻牌即财神，轮庄，牌墙剩 17 墩流局",
    GUOBIAO_RULE: "国标 81 番，起和 8 番，吃碰杠齐全",
}

# 国标教程场景：走国标引擎 Debug 固定手牌机制。真人固定坐庄，手牌 14 张
# （13 张 + 庄家首摸已含）；wall_top=(槽位, 牌) 把关键牌顶到牌山摸牌槽
# （1=南家首摸，之后西、北、庄依次摸），配合机器人底牌实现剧本化教学。
# 机器人底牌全部是条子/字牌散张，摸到万筒孤张必打，保证教学牌按剧本出现。
_TUTORIAL_GB_BOTS = [_BOT_A, _BOT_B, _BOT_C]
TUTORIAL_SCENARIOS_GUOBIAO = {
    # 学出牌：杂牌起手，庄家随便打一张
    "chu": dict(title="学会出牌",
                hands=[[11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 43, 45, 46]] + _TUTORIAL_GB_BOTS),
    # 学吃：手里 1筒/2筒 搭子，北家摸进孤张 3 筒后必打，庄家吃成 1-2-3 筒
    "chi": dict(title="学会吃",
                hands=[[21, 22, 11, 12, 13, 31, 32, 33, 41, 42, 43, 44, 45, 46]] + _TUTORIAL_GB_BOTS,
                wall_top=(3, 23)),
    # 学碰：手里 4筒 一对，南家摸进孤张 4 筒后必打，庄家碰成刻子
    "peng": dict(title="学会碰",
                 hands=[[24, 24, 11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 45]] + _TUTORIAL_GB_BOTS,
                 wall_top=(1, 24)),
    # 学杠：手里 1 万四张，直接暗杠
    "angang": dict(title="学会杠",
                   hands=[[11, 11, 11, 11, 21, 22, 23, 31, 32, 33, 41, 42, 43, 45]] + _TUTORIAL_GB_BOTS),
    # 学胡牌：三刻（1/3/5 万）+ 对 7 筒，打出发财后对倒听 7/9 筒，
    # 第二轮自摸 9 筒成碰碰和+门前清+自摸（9 番 ≥ 起和 8 番）。
    # 机器人底牌是全孤张：不含 11/33/35/27/29/47（真人手牌），彼此间隔 ≥3
    # （无吃）、无对子（无碰）、无四张（无杠）——任何机器人都不可能鸣牌，
    # 保证南/西/北按槽位顺序摸牌、庄家第二轮稳摸到槽位 4 的 9 筒。
    "hu": dict(title="学会胡牌",
               hands=[[11, 11, 11, 33, 33, 33, 35, 35, 35, 27, 27, 29, 29, 47],
                      [14, 17, 21, 24, 31, 34, 37, 41, 42, 43, 44, 45, 46],
                      [14, 17, 21, 24, 31, 34, 37, 41, 42, 43, 44, 45, 46],
                      [14, 17, 21, 24, 31, 34, 37, 41, 42, 43, 44, 45, 46]],
               wall_top=(4, 29)),
}


# 杭州家族教程钩子：固定庄家、首局教学牌墙、一局即整场、行动窗口推送提示。
# 做成 mixin，与所选大厅变体引擎（萧山/绍兴/台州/义乌/杭州）动态合成教程子类，
# 变体引擎未覆写的钩子自然回落到杭州基类实现。
class _TutorialHangzhouMixin:
    def _rotate_initial_dealer_randomly(self):
        return  # 教程固定真人（0 号位）坐庄

    def initialize_round(self, *, wall=None):
        if wall is None:
            walls = getattr(self, "tutorial_walls", None) or []
            if walls:
                wall = walls.pop(0)  # 教学牌墙只用于第一小局，之后回退随机
        super().initialize_round(wall=wall)

    def _finish_round(self, reason):
        # 教程只打一小局：无论胡牌还是流局，本局结束即整场结束。
        from server.gamestate.game_hangzhou.state_machine import Phase as P
        self.match_finishing = True
        self.ended_by = reason
        self.machine.transition(P.END)

    def open_action_window(self, window):
        result = super().open_action_window(window)
        self._publish_tutorial_hint(result)
        return result

    def _publish_tutorial_hint(self, window):
        """真人座位出现可操作窗口时，推一条 solo/tutorial_hint 指导语。"""
        try:
            if self.game_server is None:
                return
            mine = (window.get("actions") or {}).get(0)
            if not mine or 0 not in self.waiting_players_list:
                return
            message = self._tutorial_hint_text(window, mine)
            if not message:
                return
            connection = (self.game_server.user_id_to_connection or {}).get(self.player_list[0].user_id)
            if connection is None or connection.websocket is None:
                return
            asyncio.ensure_future(connection.websocket.send_json({
                "type": "solo/tutorial_hint",
                "gamestate_id": self.gamestate_id,
                "message": message,
                "actions": list(mine),
                "action_tick": window.get("action_tick"),
            }))
        except Exception:
            logger.warning("教程提示推送失败", exc_info=True)

    @staticmethod
    def _tutorial_hint_text(window, actions):
        status = window.get("status")
        if status in ("waiting_hand_action", "onlycut_after_action"):
            if any(a in actions for a in ("hu_self", "hu_first", "hu_second", "hu_third", "hu")):
                return "自摸胡牌！点击「胡」按钮赢得这一局"
            if "angang" in actions or "jiagang" in actions:
                return "凑齐了四张相同的牌可以「杠」！点击「杠」按钮（不想杠也可以点一张牌正常打出）"
            return "轮到你出牌：点击手牌里要打出的那张牌"
        if status == "waiting_action_after_cut":
            tile = _tile_display_name(window.get("tile"))
            if "gang" in actions:
                return f"他家打出{tile}，你手里有三张，可以「杠」！点击「杠」按钮"
            if "peng" in actions:
                return f"他家打出{tile}，你手里有一对，可以「碰」成刻子！点击「碰」按钮"
            if any(a in actions for a in ("chi_left", "chi_mid", "chi_right")):
                return f"上家打出{tile}，可以「吃」成顺子！点击「吃」按钮（有多种吃法时会再让你选）"
            return "他家出了牌，不需要操作时点「过」跳过"
        return None


_TUTORIAL_ENGINE_CACHE = {}


def _tutorial_engine_class(rule: str):
    """杭州家族大厅规则 → 教程引擎子类（mixin 钩子 + 变体引擎基类），结果缓存。"""
    cls = _TUTORIAL_ENGINE_CACHE.get(rule)
    if cls is not None:
        return cls
    if rule == XIAOSHAN_RULE:
        from server.gamestate.game_xiaoshan.XiaoshanGameState import XiaoshanGameState as base
    elif rule == SHAOXING_RULE:
        from server.gamestate.game_shaoxing.ShaoxingGameState import ShaoxingGameState as base
    elif rule == TAIZHOU_RULE:
        from server.gamestate.game_taizhou.TaizhouGameState import TaizhouGameState as base
    elif rule == YIWU_RULE:
        from server.gamestate.game_yiwu.YiwuGameState import YiwuGameState as base
    else:
        from server.gamestate.game_hangzhou.HangzhouGameState import HangzhouGameState as base
    cls = type("TutorialGameState_" + rule, (_TutorialHangzhouMixin, base), {})
    _TUTORIAL_ENGINE_CACHE[rule] = cls
    return cls


def _new_tutorial_state(server: SoloServer, room_data: dict, rule: str):
    """实例化杭州家族教程引擎：真人固定坐庄、首局用教学牌墙、行动窗口顺带推送新手提示。"""
    return _tutorial_engine_class(rule)(server, room_data, None, None,
                                        "solo-tutorial-" + uuid.uuid4().hex[:8])


def _new_guobiao_tutorial_state(server: SoloServer, room_data: dict):
    """实例化国标教程引擎：复用国标 Debug 固定手牌机制 + 教程提示推送。

    - Debug=True：开局不打乱座位（真人固定庄家）、tutorial_hands 注入四家手牌、
      tutorial_wall_top 顶牌山；
    - game_loop_chinese 前把 current_round 预设为本圈最后一小局：唯一一小局结束
      推进后外层整场循环自然退出，实现「一局即整场」（零引擎侵入）；
    - 覆写 wait_action 在等待窗口建立时推送 solo/tutorial_hint。
    """
    from server.gamestate.game_guobiao.GuobiaoGameState import GuobiaoGameState
    from server.game_calculation.game_calculation_service import GameCalculationService

    class TutorialGuobiaoGameState(GuobiaoGameState):
        async def game_loop_chinese(self):
            self.current_round = self.max_round * len(self.player_list)
            await super().game_loop_chinese()

        async def wait_action(self):
            self._publish_tutorial_hint()
            await super().wait_action()

        def _publish_tutorial_hint(self):
            try:
                if self.game_server is None:
                    return
                mine = [a for a in (self.action_dict or {}).get(0, []) if a != "pass"]
                if not mine or 0 not in (self.waiting_players_list or []):
                    return
                status = self.game_status
                if status in ("waiting_hand_action", "onlycut_after_action"):
                    if any(a in mine for a in ("hu_self", "hu_first", "hu_second", "hu_third", "hu")):
                        message = "自摸胡牌！点击「胡」按钮赢得这一局"
                    elif "angang" in mine or "jiagang" in mine:
                        message = "凑齐了四张相同的牌可以「杠」！点击「杠」按钮（不想杠也可以点一张牌正常打出）"
                    else:
                        message = "轮到你出牌：点击手牌里要打出的那张牌"
                elif status == "waiting_action_after_cut":
                    tile = _tile_display_name(self.player_list[self.current_player_index].discard_tiles[-1]) \
                        if self.player_list[self.current_player_index].discard_tiles else "牌"
                    if "gang" in mine:
                        message = f"他家打出{tile}，你手里有三张，可以「杠」！点击「杠」按钮"
                    elif "peng" in mine:
                        message = f"他家打出{tile}，你手里有一对，可以「碰」成刻子！点击「碰」按钮"
                    elif any(a in mine for a in ("chi_left", "chi_mid", "chi_right")):
                        message = f"上家打出{tile}，可以「吃」成顺子！点击「吃」按钮（有多种吃法时会再让你选）"
                    else:
                        message = "他家出了牌，不需要操作时点「过」跳过"
                else:
                    return
                connection = (self.game_server.user_id_to_connection or {}).get(self.player_list[0].user_id)
                if connection is None or connection.websocket is None:
                    return
                asyncio.ensure_future(connection.websocket.send_json({
                    "type": "solo/tutorial_hint",
                    "gamestate_id": self.gamestate_id,
                    "message": message,
                    "actions": list(mine),
                }))
            except Exception:
                logger.warning("国标教程提示推送失败", exc_info=True)

    return TutorialGuobiaoGameState(server, room_data, GameCalculationService(), _DbStub(),
                                    "solo-tutorial-" + uuid.uuid4().hex[:8])


async def _handle_start_tutorial(server: SoloServer, conn: WsConnection, message: dict):
    """solo/start_tutorial {rule, scenario}：按玩法进入教学对局（真人坐庄、固定教学手牌）。

    杭州家族（杭州/萧山/绍兴/台州/义乌）走杭州栈变体引擎 + 教学牌墙注入；
    国标走独立引擎 Debug 固定手牌机制（关花牌、起和 8 番）。
    """
    game = server._game
    if game is not None and getattr(game, "game_task", None) is not None and not game.game_task.done():
        # 上一个教学局还在跑：直接换场景重开；其它对局（房间局等）不抢。
        if not str(getattr(game, "gamestate_id", "")).startswith("solo-tutorial-"):
            await _send(conn, {"type": "solo/start_tutorial", "success": False,
                               "message": "请先结束当前对局再来学习"})
            return
        await _stop_solo_game(server)
    uid = conn.user_id
    if uid is None or uid <= 10:
        await _send(conn, {"type": "solo/start_tutorial", "success": False, "message": "请先登录"})
        return
    rule = message.get("rule")
    if rule not in RULE_LABELS:  # 未指定或非法时按杭州教学
        rule = HANGZHOU_RULE
    scenario_key = str(message.get("scenario") or "chu")
    player_list = [uid] + TUTORIAL_BOTS
    if rule == GUOBIAO_RULE:
        scenario = TUTORIAL_SCENARIOS_GUOBIAO.get(scenario_key) or TUTORIAL_SCENARIOS_GUOBIAO["chu"]
        room_data = dict(
            room_id=0,
            room_rule=GUOBIAO_RULE, sub_rule=GUOBIAO_SUB_RULE,
            player_list=player_list,
            player_settings=_player_settings_for(server, player_list),
            game_round=1, round_timer=600, step_timer=60,
            room_type="custom", tips=True, random_seed=0,
            bot_speed="slow", bot_level="medium",
            use_flowers=False,  # 教学局不用花牌，与 136 张教学手牌体系一致
            hepai_limit=8,
        )
        state = _new_guobiao_tutorial_state(server, room_data)
        state.Debug = True
        state.debug_scenario = "solo_tutorial"
        state.tutorial_hands = scenario["hands"]
        if scenario.get("wall_top"):
            state.tutorial_wall_top = scenario["wall_top"]
    else:
        scenario = TUTORIAL_SCENARIOS.get(scenario_key) or TUTORIAL_SCENARIOS["chu"]
        room_data = dict(
            room_id="solo-tutorial",
            room_rule=rule, sub_rule=VARIANT_SUB_RULES.get(rule, HANGZHOU_SUB_RULE),
            player_list=player_list,
            player_settings=_player_settings_for(server, player_list),
            game_round=1, round_timer=600, step_timer=60,
            room_type="custom", tips=True, random_seed=0,
            bot_speed="slow", bot_level="medium",
        )
        state = _new_tutorial_state(server, room_data, rule)
        # 教程机器人用人设难度：摸切流不鸣牌，教学关键牌更稳定；
        # 场景可按需覆盖个别座位（吃场景让上家用摸切流确定性打出孤张教学牌）
        state.bot_level_by_index = _bot_levels_for_players(room_data["player_list"])
        state.bot_level_by_index.update(scenario.get("seat_levels") or {})
        hands = scenario["hands"]
        # 萧山/义乌开局 pop 牌墙首张翻财神、绍兴在萧山之后再 pop 一张：
        # 用牺牲牌顶到 wall[53] 起保住 draw_seq 摸牌剧本。选 42/15 因五套场景中
        # 用量 ≤3/≤2 张（46 已满 4 张），且 42、15 及其衍生财神（43/16）都不是
        # 教学关键牌，各家手牌也不含 16。
        if rule == SHAOXING_RULE:
            flip = (42, 15)
        elif rule in (XIAOSHAN_RULE, YIWU_RULE):
            flip = 42
        else:
            flip = None
        state.tutorial_walls = [_build_scenario_wall(hands[0], hands[1], hands[2], hands[3],
                                                     scenario["draws"], fill_seed=1, flip=flip)]
    try:
        from server.gamestate.public.ai.pacing import configure_bot_pacing
        configure_bot_pacing(state, room_data)
    except Exception as exc:      # 配置失败不影响教程
        logger.warning("教程 configure_bot_pacing 失败: %s", exc)
    server._game = state
    server.gamestate_manager._game = state
    state.game_task = asyncio.create_task(_run_game_loop(server, state))
    brief = TUTORIAL_RULE_BRIEFS.get(rule, "")
    _note("教程对局已启动 rule=%s scenario=%s uid=%s" % (rule, scenario_key, uid))
    await _send(conn, {"type": "solo/start_tutorial", "success": True,
                       "message": "「%s」教程「%s」开始" % (RULE_LABELS[rule], scenario["title"]),
                       "rule": rule, "brief": brief})


async def _handle_stop_tutorial(server: SoloServer, conn: WsConnection, message: dict):
    """solo/stop_tutorial：中途退出新手教程（对局未结束也直接终止并清场）。"""
    game = server._game
    is_tutorial = game is not None and str(getattr(game, "gamestate_id", "")).startswith("solo-tutorial-")
    if not is_tutorial:
        await _send(conn, {"type": "solo/stop_tutorial", "success": False,
                           "message": "当前没有进行中的新手教程"})
        return
    await _stop_solo_game(server)
    _note("教程对局已终止 uid=%s" % conn.user_id)
    await _send(conn, {"type": "solo/stop_tutorial", "success": True, "message": "教程已结束"})


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
    elif rule in (XIAOSHAN_RULE, SHAOXING_RULE, YIWU_RULE):
        # 翻财系变体：透传开局积分、总倍率封顶与房规开关（detailed_config，如「有财神必须爆头」）
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

    if mtype == "solo/start_tutorial":
        await _handle_start_tutorial(server, conn, message)
        return

    if mtype == "solo/stop_tutorial":
        await _handle_stop_tutorial(server, conn, message)
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
            from server.gamestate.public.ai.level import normalize_bot_level
            server._room["bot_level"] = level
            # 房主显式改难度：标记后新对局按统一档位覆盖机器人人设梯度
            server._room["bot_level_locked"] = True
            # 对局进行中也即时生效；非法档位由 normalize 归一成默认。
            game = getattr(server, "_game", None)
            if game is not None:
                game.bot_level = normalize_bot_level(level)
                game.bot_level_by_index = {i: game.bot_level for i, p in enumerate(game.player_list)
                                           if getattr(p, "is_bot", False)}
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
