using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using UnityEngine;

/// <summary>
/// 局域网房间直连器：只做局域网对战，不再有局域网房间列表浮层。
/// 单机模式下输入房间号加入（RoomListPanel 调用 <see cref="JoinRoomById"/>）：
/// 后台通过 UDP 18764 广播探测（OMU_DISCOVER），发现房号匹配的主机后直连其
/// ws://{ip}:{port}/game 并自动加入房间；搜索超时或连接失败时提示并切回本机。
///
/// 加入流程：
/// 1. 把 ConfigManager.gameUrl/chatUrl 指向房间主机的 ws://{ip}:{port}/game；
/// 2. 清空本机登录态（ClearSessionState），让新连接在远端重新自动登录；
/// 3. NetworkManager.BeginNewConnection() 连接远端；
/// 4. 登录成功（UserId != 0）后自动发送 room/join_room，进入房间的 RoomPanel。
///
/// 房主机无需任何额外操作：正常创建房间即可，内嵌服务端（SOLO_LAN=1）会监听
/// 0.0.0.0 并应答 UDP 发现广播。
/// </summary>
public class LanRoomBrowser : MonoBehaviour {
    /// <summary>与服务端 SoloPython/solo_server.py 的 LAN_DISCOVERY_PORT 保持一致。</summary>
    private const int DiscoveryPort = 18764;
    private const string DiscoverMagic = "OMU_DISCOVER";
    private const string ReplyMagic = "OMU_SOLO";
    /// <summary>加入流程从发起连接到自动进入房间的总超时。</summary>
    private const float PendingJoinTimeoutSeconds = 20f;
    /// <summary>房间条目超过该秒数未再应答即从列表移除。</summary>
    private const float RoomStaleSeconds = 5f;
    /// <summary>按房号搜索期间重发广播的间隔与总超时。</summary>
    private const float SearchBroadcastIntervalSeconds = 0.5f;
    private const float SearchTimeoutSeconds = 4f;

    /// <summary>一条发现应答解析出的房间条目。</summary>
    private class LanRoom {
        public string Ip;
        public int Port;
        public string RoomId;
        public string RoomName;
        public string RuleLabel;
        public string HostName;
        public int HumanCount;
        public int PlayerCount;
        public int MaxPlayer;
        public bool HasPassword;
        public bool IsGameRunning;
        public float LastSeen;
    }

    private class DiscoveryReply {
        public string magic;
        public int port;
        public RoomSummary room;
    }

    private class RoomSummary {
        public bool in_room;
        public string room_id;
        public string room_name;
        public string rule_label;
        public string host_name;
        public int human_count;
        public int player_count;
        public int max_player;
        public bool has_password;
        public bool is_game_running;
    }

    private static LanRoomBrowser instance;
    private UdpClient udp;
    private readonly Dictionary<string, LanRoom> rooms = new Dictionary<string, LanRoom>();
    /// <summary>连接建立后待自动加入的房间号（SwitchToRemote 设置，TickPendingJoin 消费）。</summary>
    private string pendingRoomId;
    private string pendingPassword;
    private float pendingSince;
    /// <summary>按房号后台搜索的目标房号（TickRoomSearch 消费；空表示未在搜索）。</summary>
    private string searchingRoomId;
    private float searchDeadline;
    private float searchSendTimer;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
#if UNITY_WEBGL && !UNITY_EDITOR
        // 浏览器访客连的是页面来源主机（WebGuestBootstrap），UDP 发现与本机切换不适用。
        return;
#else
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("LanRoomBrowser");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<LanRoomBrowser>();
#endif
    }

    private void Update() {
        TickPendingJoin();
        TickRoomSearch();
    }

    // ========== 按房号加入（静态入口） ==========

    /// <summary>
    /// 单机模式下按房间号加入局域网房间：后台广播搜索房号匹配的主机并直连，
    /// 超时未搜到则提示失败。由 RoomListPanel 在单机模式"加入房间"时调用。
    /// </summary>
    public static void JoinRoomById(string roomId) {
#if UNITY_WEBGL && !UNITY_EDITOR
        // 浏览器访客不走 UDP 搜索：RoomListPanel 已直连页面来源主机按房号加入。
        NotificationManager.Instance?.ShowTip("join_room", false, "请输入房间号后点击加入");
        return;
#endif
        roomId = (roomId ?? "").Trim();
        if (string.IsNullOrEmpty(roomId)) {
            NotificationManager.Instance?.ShowTip("join_room", false, "请输入房间号");
            return;
        }
        if (!ConfigManager.SoloMode) return;
        if (instance == null) {
            var go = new GameObject("LanRoomBrowser");
            DontDestroyOnLoad(go);
            instance = go.AddComponent<LanRoomBrowser>();
        }
        instance.BeginRoomSearch(roomId);
    }

    private void BeginRoomSearch(string roomId) {
        EnsureUdp();
        searchingRoomId = roomId;
        searchDeadline = Time.unscaledTime + SearchTimeoutSeconds;
        searchSendTimer = 0f;
        rooms.Clear();
        Debug.Log($"[LanRoomBrowser] 开始搜索房间 {roomId}");
    }

    /// <summary>后台搜索循环：重发广播、匹配房号、超时提示失败。</summary>
    private void TickRoomSearch() {
        if (string.IsNullOrEmpty(searchingRoomId)) return;
        PollDiscoveryReplies();
        searchSendTimer -= Time.unscaledDeltaTime;
        if (searchSendTimer <= 0f) {
            searchSendTimer = SearchBroadcastIntervalSeconds;
            SendDiscovery();
        }
        LanRoom match = FindRoom(searchingRoomId);
        if (match != null) {
            string roomId = searchingRoomId;
            searchingRoomId = null;
            SwitchToRemote(match.Ip, match.Port, roomId, "");
            return;
        }
        if (Time.unscaledTime >= searchDeadline) {
            Debug.LogWarning($"[LanRoomBrowser] 搜索房间超时: {searchingRoomId}");
            searchingRoomId = null;
            NotificationManager.Instance?.ShowTip(
                "join_room", false, "加入失败：未找到该房间号，请确认房主已创建房间且设备在同一局域网内");
        }
    }

    private LanRoom FindRoom(string roomId) {
        foreach (var room in rooms.Values) {
            if (string.Equals(room.RoomId, roomId, StringComparison.Ordinal)) return room;
        }
        return null;
    }

    // ========== UDP 发现 ==========

    private void EnsureUdp() {
        if (udp != null) return;
        try {
            udp = new UdpClient(0);
            udp.EnableBroadcast = true;
        } catch (Exception e) {
            Debug.LogWarning("[LanRoomBrowser] UDP 初始化失败: " + e.Message);
            udp = null;
        }
    }

    private void SendDiscovery() {
        EnsureUdp();
        if (udp == null) return;
        try {
            byte[] magic = Encoding.UTF8.GetBytes(DiscoverMagic);
            udp.Send(magic, magic.Length, new IPEndPoint(IPAddress.Broadcast, DiscoveryPort));
        } catch (Exception e) {
            Debug.LogWarning("[LanRoomBrowser] 发送发现广播失败: " + e.Message);
        }
    }

    private void PollDiscoveryReplies() {
        if (udp == null) return;
        try {
            while (udp.Available > 0) {
                IPEndPoint sender = null;
                byte[] data = udp.Receive(ref sender);
                HandleReply(data, sender);
            }
        } catch (Exception) {
            // 单次收包异常不影响后续轮询
        }
        PruneStaleRooms();
    }

    private void HandleReply(byte[] data, IPEndPoint sender) {
        if (data == null || data.Length == 0 || sender == null) return;
        DiscoveryReply reply;
        try {
            reply = Newtonsoft.Json.JsonConvert.DeserializeObject<DiscoveryReply>(
                Encoding.UTF8.GetString(data));
        } catch (Exception) {
            return;
        }
        if (reply == null || reply.magic != ReplyMagic || reply.room == null || !reply.room.in_room) {
            return; // 未开房的空服务不进列表
        }
        string ip = sender.Address.ToString();
        string key = ip + ":" + reply.port;
        rooms[key] = new LanRoom {
            Ip = ip,
            Port = reply.port,
            RoomId = string.IsNullOrEmpty(reply.room.room_id) ? "solo-hangzhou" : reply.room.room_id,
            RoomName = reply.room.room_name,
            RuleLabel = reply.room.rule_label,
            HostName = string.IsNullOrEmpty(reply.room.host_name) ? ip : reply.room.host_name,
            HumanCount = reply.room.human_count,
            PlayerCount = reply.room.player_count,
            MaxPlayer = reply.room.max_player > 0 ? reply.room.max_player : 4,
            HasPassword = reply.room.has_password,
            IsGameRunning = reply.room.is_game_running,
            LastSeen = Time.unscaledTime,
        };
    }

    private void PruneStaleRooms() {
        float now = Time.unscaledTime;
        List<string> stale = null;
        foreach (var pair in rooms) {
            if (now - pair.Value.LastSeen > RoomStaleSeconds) {
                (stale ??= new List<string>()).Add(pair.Key);
            }
        }
        if (stale != null) {
            foreach (string key in stale) rooms.Remove(key);
        }
    }

    // ========== 加入 / 返回本机 ==========

    /// <summary>把客户端切到远端主机并重建连接；登录成功后由 TickPendingJoin 自动进房。</summary>
    private void SwitchToRemote(string ip, int port, string roomId, string password) {
        ConfigManager.gameUrl = $"ws://{ip}:{port}/game";
        ConfigManager.chatUrl = $"ws://{ip}:{port}/chat";
        // 清掉本机登录态：远端服务器上的 user_id 是新分配的，旧身份必须作废，
        // 否则 AutoSoloLogin 会因 UserId != 0 跳过登录，远端连接永远无法进入房间。
        UserDataManager.Instance?.ClearSessionState();
        RoomNetworkManager.Instance?.ResetForSessionEnd();
        pendingRoomId = roomId;
        pendingPassword = password ?? "";
        pendingSince = Time.unscaledTime;
        NetworkManager.Instance?.BeginNewConnection();
        Debug.Log($"[LanRoomBrowser] 切换到远端 {ip}:{port}，待加入房间 {roomId}");
    }

    /// <summary>切回本机内嵌服务（加入失败后恢复本地单机可用）。</summary>
    private void BackToLocal() {
        pendingRoomId = null;
        ConfigManager.gameUrl = $"ws://127.0.0.1:{ConfigManager.SoloPort}/game";
        ConfigManager.chatUrl = $"ws://127.0.0.1:{ConfigManager.SoloPort}/chat";
        UserDataManager.Instance?.ClearSessionState();
        RoomNetworkManager.Instance?.ResetForSessionEnd();
        SoloServerBootstrap.EnsureStarted();
        NetworkManager.Instance?.BeginNewConnection();
        Debug.Log("[LanRoomBrowser] 已切回本机单机服务");
    }

    /// <summary>新连接登录成功后自动发送加入房间请求；超时则提示、切回本机并放弃。</summary>
    private void TickPendingJoin() {
        if (string.IsNullOrEmpty(pendingRoomId)) return;
        var nm = NetworkManager.Instance;
        var udm = UserDataManager.Instance;
        if (nm != null && nm.IsWebSocketOpen && udm != null && udm.UserId != 0) {
            string roomId = pendingRoomId;
            string password = pendingPassword;
            pendingRoomId = null;
            RoomNetworkManager.Instance?.JoinRoom(roomId, password);
            return;
        }
        if (Time.unscaledTime - pendingSince > PendingJoinTimeoutSeconds) {
            pendingRoomId = null;
            NotificationManager.Instance?.ShowTip(
                "join_room", false, "加入失败：无法连接到房间主机，请确认在同一局域网内");
            BackToLocal();
        }
    }
}
