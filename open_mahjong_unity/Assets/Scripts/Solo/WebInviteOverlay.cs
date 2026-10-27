using System;
using System.IO;
using System.Text;
using System.Threading;
using UnityEngine;

/// <summary>
/// 网页邀请浮层（房主端：Android/Windows/Linux 内嵌服务）：进入房间后轮询本机
/// /host_info，把「同一局域网的朋友用浏览器加入」的地址与二维码画在屏幕上。
/// 访客扫码或手动输入地址即可在浏览器里加入对局，无需安装任何东西。
///
/// 走裸 TcpClient 发 HTTP GET 而不是 UnityWebRequest：Android 9+ 默认拦截
/// cleartext HTTP（平台 HTTP 栈策略），而 Mono/IL2CPP 的 Socket 不受该策略限制，
/// 与游戏自身连 ws://127.0.0.1 的 WebSocket 同一通道类型。
/// </summary>
public class WebInviteOverlay : MonoBehaviour {
    private const float PollInterval = 2f;
    private const float ConnectTimeoutMs = 1500f;

    private class HostInfo {
        public bool ok;
        public string url;
        public QrMatrix qr;
    }

    private class QrMatrix {
        public int size;
        public int[][] modules;
    }

    private bool wasInRoom;
    private bool expanded;
    private string url = "";
    private Texture2D qrTexture;
    private GUIStyle body;
    private float pollTimer;

    // 后台线程产出、主线程消费的结果槽
    private volatile bool fetching;
    private volatile bool hasPendingQr;
    private string pendingUrl = "";
    private int pendingSize;
    private int[][] pendingModules;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
#if !UNITY_EDITOR && (UNITY_ANDROID || UNITY_STANDALONE_WIN || UNITY_STANDALONE_LINUX)
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("WebInviteOverlay");
        DontDestroyOnLoad(go);
        go.AddComponent<WebInviteOverlay>();
#endif
    }

    private void Update() {
        bool inRoom = UserDataManager.Instance != null
            && UserDataManager.Instance.RoomId != UserDataManager.ROOM_ID_NONE;
        // 开房瞬间自动展开一次；玩家收起后同房间不再打扰。
        if (inRoom && !wasInRoom) expanded = true;
        wasInRoom = inRoom;
        if (!inRoom) {
            url = "";
            if (qrTexture != null) {
                Destroy(qrTexture);
                qrTexture = null;
            }
            pollTimer = 0f;
            return;
        }
        if (hasPendingQr) ApplyPendingQr();
        pollTimer -= Time.unscaledDeltaTime;
        if (pollTimer > 0f) return;
        pollTimer = PollInterval;
        StartFetch();
    }

    // ========== 后台拉取 ==========

    private void StartFetch() {
        if (fetching) return;
        fetching = true;
        var thread = new Thread(FetchLoop) { IsBackground = true };
        thread.Start();
    }

    private void FetchLoop() {
        try {
            string json = FetchHostInfoJson(out _);
            if (json != null) {
                var info = Newtonsoft.Json.JsonConvert.DeserializeObject<HostInfo>(json);
                if (info != null && info.ok && !string.IsNullOrEmpty(info.url) && info.qr != null
                    && info.qr.modules != null && info.qr.size > 0) {
                    pendingUrl = info.url;
                    pendingSize = info.qr.size;
                    pendingModules = info.qr.modules;
                    hasPendingQr = true;
                }
            }
        } catch (Exception) {
            // 轮询失败静默等下一轮
        } finally {
            fetching = false;
        }
    }

    /// <summary>裸 TCP 发送 HTTP GET /host_info；Connection: close，读到对端关闭为止。</summary>
    private static string FetchHostInfoJson(out string error) {
        error = null;
        try {
            using (var client = new System.Net.Sockets.TcpClient()) {
                var ar = client.BeginConnect("127.0.0.1", ConfigManager.SoloPort, null, null);
                if (!ar.AsyncWaitHandle.WaitOne((int)ConnectTimeoutMs) || !client.Connected) {
                    error = "本地服务未就绪";
                    return null;
                }
                using (var stream = client.GetStream()) {
                    byte[] request = Encoding.ASCII.GetBytes(
                        "GET /host_info HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n");
                    stream.Write(request, 0, request.Length);
                    using (var ms = new MemoryStream()) {
                        var buffer = new byte[4096];
                        int read;
                        while ((read = stream.Read(buffer, 0, buffer.Length)) > 0) {
                            ms.Write(buffer, 0, read);
                        }
                        string raw = Encoding.UTF8.GetString(ms.ToArray());
                        int split = raw.IndexOf("\r\n\r\n", StringComparison.Ordinal);
                        if (split < 0 || !raw.StartsWith("HTTP/1.", StringComparison.Ordinal)) {
                            error = "host_info 响应格式异常";
                            return null;
                        }
                        if (!raw.StartsWith("HTTP/1.1 200", StringComparison.Ordinal)
                            && !raw.StartsWith("HTTP/1.0 200", StringComparison.Ordinal)) {
                            error = "host_info 请求失败";
                            return null;
                        }
                        return raw.Substring(split + 4);
                    }
                }
            }
        } catch (Exception e) {
            error = e.Message;
            return null;
        }
    }

    // ========== 二维码纹理（主线程） ==========

    private void ApplyPendingQr() {
        hasPendingQr = false;
        string nextUrl = pendingUrl;
        int size = pendingSize;
        int[][] modules = pendingModules;
        pendingModules = null;
        if (nextUrl != url) {
            url = nextUrl;
            if (qrTexture != null) {
                Destroy(qrTexture);
                qrTexture = null;
            }
        }
        if (qrTexture != null || modules.Length != size) return;
        var tex = new Texture2D(size, size, TextureFormat.RGBA32, false) {
            filterMode = FilterMode.Point,
            wrapMode = TextureWrapMode.Clamp,
        };
        var pixels = new Color32[size * size];
        var white = new Color32(255, 255, 255, 255);
        var black = new Color32(0, 0, 0, 255);
        for (int row = 0; row < size; row++) {
            int[] line = modules[row];
            int baseIndex = row * size;
            for (int col = 0; col < size; col++) {
                pixels[baseIndex + col] = col < line.Length && line[col] == 1 ? black : white;
            }
        }
        tex.SetPixels32(pixels);
        tex.Apply(false, true);
        qrTexture = tex;
    }

    // ========== 绘制 ==========

    private void OnGUI() {
        try {
            DrawOverlay();
        } catch (Exception e) {
            Debug.LogError("[WebInvite] OnGUI 异常: " + e);
        }
    }

    private void DrawOverlay() {
        if (string.IsNullOrEmpty(url)) return; // 不在房间或还没拿到 host_info
        if (body == null) {
            body = new GUIStyle(GUI.skin.label) {
                fontSize = Mathf.Max(18, Screen.height / 52),
                wordWrap = false,
            };
        }
        float fontSize = body.fontSize;
        float qrSize = Mathf.Min(Screen.height / 3f, 340f);

        if (!expanded) {
            Rect buttonRect = new Rect(Screen.width - fontSize * 8 - 24, 8, fontSize * 8 + 8, fontSize * 2.4f);
            if (GUI.Button(buttonRect, "网页邀请")) expanded = true;
            return;
        }

        float width = Mathf.Max(qrSize + 24, fontSize * 22);
        float height = qrSize + fontSize * 6.5f;
        var rect = new Rect(Screen.width - width - 12, 8, width, height);
        GUI.Box(rect, GUIContent.none);

        float x = rect.x + 12;
        float y = rect.y + 8;
        GUI.Label(new Rect(x, y, rect.width - 24, fontSize * 1.4f), "同一局域网的朋友，浏览器扫码加入：", body);
        y += fontSize * 1.5f;
        if (qrTexture != null) {
            GUI.DrawTexture(new Rect(x, y, qrSize, qrSize), qrTexture, ScaleMode.ScaleToFit, false);
        } else {
            GUI.Label(new Rect(x, y, rect.width - 24, fontSize * 1.4f), "二维码生成中…", body);
        }
        y += qrSize + 6;
        GUI.Label(new Rect(x, y, rect.width - 24, fontSize * 1.4f), url, body);
        y += fontSize * 1.6f;
        if (GUI.Button(new Rect(x, y, fontSize * 8, fontSize * 1.8f), "复制地址")) {
            GUIUtility.systemCopyBuffer = url;
        }
        if (GUI.Button(new Rect(rect.x + rect.width - fontSize * 6 - 20, y, fontSize * 6, fontSize * 1.8f), "收起")) {
            expanded = false;
        }
    }
}
