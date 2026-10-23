using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Sockets;
using System.Text;
using UnityEngine;

/// <summary>
/// 局域网房间浏览器：在单机模式下广播探测同一局域网内开放的主机房间（UDP 18764），
/// 列出可加入的房间并一键加入；也支持直连 IP（广播被路由器/隔离网段拦掉时的兜底）。
///
/// 加入流程：
/// 1. 把 ConfigManager.gameUrl/chatUrl 指向房间主机的 ws://{ip}:{port}/game；
/// 2. 清空本机登录态（ClearSessionState），让新连接在远端重新自动登录；
/// 3. NetworkManager.BeginNewConnection() 连接远端；
/// 4. 登录成功（UserId != 0）后自动发送 room/join_room，进入房间的 RoomPanel。
///
/// 房主机无需任何额外操作：正常创建房间即可，内嵌服务端（SOLO_LAN=1）会监听 0.0.0.0
/// 并应答 UDP 发现广播；加入方按房间列表或直连 IP 接入。
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
    private const float ScanIntervalSeconds = 1f;

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

        public string Label {
            get {
                string name = string.IsNullOrEmpty(RoomName) ? (RuleLabel ?? "麻将房间") : RoomName;
                string state = IsGameRunning ? " · 对局中" : "";
                string pwd = HasPassword ? " · 需密码" : "";
                return $"{name} · {HostName} · {HumanCount}/{MaxPlayer}人{pwd}{state}";
            }
        }
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

    private bool visible;
    /// <summary>浏览器窗口是否展开（教学演示按钮借此避让，避免 IMGUI 重叠）。</summary>
    public static bool BrowserWindowVisible => instance != null && instance.visible;
    private static LanRoomBrowser instance;
    private UdpClient udp;
    private float scanTimer;
    private readonly Dictionary<string, LanRoom> rooms = new Dictionary<string, LanRoom>();
    private Vector2 scroll;
    private string joinPassword = "";
    private string manualAddress = "";
    private string pendingRoomId;
    private string pendingPassword;
    private float pendingSince;
    private GUIStyle body;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("LanRoomBrowser");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<LanRoomBrowser>();
    }

    private void Update() {
        TickPendingJoin();
        if (!visible) return;
        scanTimer -= Time.unscaledDeltaTime;
        if (scanTimer <= 0f) {
            scanTimer = ScanIntervalSeconds;
            SendDiscovery();
        }
        PollDiscoveryReplies();
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

    /// <summary>切回本机内嵌服务（离开他人房间 / 加入失败后想自己开房时用）。</summary>
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

    /// <summary>新连接登录成功后自动发送加入房间请求；超时则提示并放弃。</summary>
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
        }
    }

    // ========== UI ==========

    private void OnGUI() {
        try {
            DrawOverlay();
        } catch (Exception e) {
            Debug.LogError("[LanRoomBrowser] OnGUI 异常: " + e);
        }
    }

    private void DrawOverlay() {
        if (body == null) {
            int size = Mathf.Max(20, Screen.height / 52);
            body = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false };
        }
        if (!visible) {
            // 与诊断面板按钮错开一档，避免重叠
            float y = 8f + body.fontSize * 2.6f + 6f;
            if (GUI.Button(new Rect(8, y, Mathf.Max(200, body.fontSize * 9), body.fontSize * 2.6f),
                           "局域网房间")) {
                visible = true;
                SendDiscovery();
            }
            return;
        }

        float width = Mathf.Min(760f, Screen.width * 0.8f);
        float height = Screen.height * 0.56f;
        var area = new Rect(8, 8, width, height);
        GUI.Box(area, GUIContent.none);
        GUILayout.BeginArea(area);

        GUILayout.BeginHorizontal();
        GUILayout.Label("局域网房间（自动搜索中）", body);
        if (GUILayout.Button("关闭", GUILayout.Width(90))) {
            visible = false;
        }
        GUILayout.EndHorizontal();

        scroll = GUILayout.BeginScrollView(scroll);
        if (rooms.Count == 0) {
            GUILayout.Label("未发现房间。请让房主先创建房间，并确认设备在同一局域网内。", body);
        }
        foreach (var room in rooms.Values) {
            GUILayout.BeginHorizontal();
            GUILayout.Label(room.Label + "  [" + room.Ip + "]", body);
            if (room.IsGameRunning) {
                GUILayout.Label("对局中", body, GUILayout.Width(body.fontSize * 4));
            } else if (GUILayout.Button("加入", GUILayout.Width(90))) {
                JoinRoom(room);
            }
            GUILayout.EndHorizontal();
        }
        GUILayout.EndScrollView();

        GUILayout.BeginHorizontal();
        GUILayout.Label("密码", body, GUILayout.Width(body.fontSize * 3));
        joinPassword = GUILayout.TextField(joinPassword, GUILayout.Width(width * 0.32f));
        GUILayout.Space(10);
        GUILayout.Label("直连", body, GUILayout.Width(body.fontSize * 3));
        manualAddress = GUILayout.TextField(manualAddress, GUILayout.Width(width * 0.3f));
        if (GUILayout.Button("连接", GUILayout.Width(90))) {
            JoinManual(manualAddress);
        }
        GUILayout.EndHorizontal();

        GUILayout.BeginHorizontal();
        GUILayout.Label("当前: " + ConfigManager.gameUrl, body);
        GUILayout.FlexibleSpace();
        if (GUILayout.Button("回到本机", GUILayout.Width(120))) {
            BackToLocal();
            visible = false;
        }
        GUILayout.EndHorizontal();

        GUILayout.EndArea();
    }

    private void JoinRoom(LanRoom room) {
        SwitchToRemote(room.Ip, room.Port, room.RoomId, joinPassword);
        visible = false;
    }

    /// <summary>直连 "IP[:端口]"（默认端口 18763），进入该主机当前开放的单机房间。</summary>
    private void JoinManual(string address) {
        address = (address ?? "").Trim();
        if (string.IsNullOrEmpty(address)) return;
        string ip = address;
        int port = ConfigManager.SoloPort;
        int idx = address.LastIndexOf(':');
        if (idx > 0 && int.TryParse(address.Substring(idx + 1), out int parsed)) {
            ip = address.Substring(0, idx);
            port = parsed;
        }
        SwitchToRemote(ip, port, "solo-hangzhou", joinPassword);
        visible = false;
    }
}
