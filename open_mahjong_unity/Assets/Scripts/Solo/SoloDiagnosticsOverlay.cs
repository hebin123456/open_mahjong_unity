using System.Collections.Generic;
using UnityEngine;

/// <summary>
/// 单机版诊断面板：仅在 <see cref="ConfigManager.SoloMode"/> 下挂载，把本地 Python 服务状态、
/// 协议收发轨迹与最近的错误日志直接画在屏幕上。
///
/// 手机上无法查看 logcat，「点了开始游戏没反应」这类问题只能靠它定位：
/// - 看不到「→ room/start_game」说明客户端根本没发出请求；
/// - 看不到「→ gamestate/hangzhou/game_start」说明内嵌服务开局失败（原因会以「!!」开头的行显示）；
/// - 有异常日志则说明客户端收到 game_start 后初始化失败。
/// </summary>
public class SoloDiagnosticsOverlay : MonoBehaviour {
    private const int MaxLines = 9;
    private const float RefreshInterval = 0.5f;

    private static readonly List<string> Lines = new List<string>();
    private static readonly object Lock = new object();
    private static int recentFailures;   // Update 轮询连续失败计数，熔断后停止 JNI 轮询

    private string serverTrace = "";
    private string window = "";
    private float timer;
    // 默认收起：只在左上角保留一个「诊断面板」按钮，点开才展开详细日志。
    private bool visible = false;
    private GUIStyle body;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("SoloDiagnosticsOverlay");
        DontDestroyOnLoad(go);
        go.AddComponent<SoloDiagnosticsOverlay>();
        Application.logMessageReceived += OnLog;
    }

    private static void OnLog(string message, string stackTrace, LogType type) {
        string line = Condense(message, type);
        if (line == null) return;
        lock (Lock) {
            if (Lines.Count > 0 && Lines[Lines.Count - 1] == line) return; // 连续重复的消息只留一条
            Lines.Add(line);
            if (Lines.Count > MaxLines) Lines.RemoveAt(0);
        }
    }

    /// <summary>把一条 Unity 日志压缩成一行可读的关键信息，无关的返回 null 直接丢弃。</summary>
    private static string Condense(string message, LogType type) {
        if (string.IsNullOrEmpty(message)) return null;
        if (type == LogType.Error || type == LogType.Exception || type == LogType.Assert) {
            // Debug.LogError($"{e.Message}\n{e.StackTrace}") 的首行是异常文本，次行通常是第一条堆栈帧。
            // 把首帧一并显示，才能看出是在哪个方法/哪一行抛的（面板窄，只保留一行）。
            string[] parts = message.Split('\n');
            string head = parts.Length > 0 ? parts[0] : message;
            string frame = parts.Length > 1 ? parts[1].Trim() : "";
            string composed = string.IsNullOrEmpty(frame) ? head : head + "  @" + frame;
            return "!! " + (composed.Length > 160 ? composed.Substring(0, 160) + "…" : composed);
        }
        string messageType = ExtractType(message);
        if (messageType != null) return "收到 " + messageType;
        if (message.Contains("[Solo") || message.Contains("开始游戏") || message.Contains("单机")) {
            return message.Length > 140 ? message.Substring(0, 140) + "…" : message;
        }
        return null;
    }

    /// <summary>从「收到服务器消息: {json}」这类日志里取出协议 type，用于还原收到的消息序列。</summary>
    private static string ExtractType(string message) {
        const string key = "\"type\":\"";
        int start = message.IndexOf(key, System.StringComparison.Ordinal);
        if (start < 0) return null;
        start += key.Length;
        int end = message.IndexOf('"', start);
        if (end <= start) return null;
        return message.Substring(start, end - start);
    }

    private void Update() {
        timer -= Time.unscaledDeltaTime;
        if (timer > 0f) return;
        timer = RefreshInterval;
        // Android 上 RecentEvents 是主线程 JNI→Python 调用；连续失败说明桥接异常，
        // 熔断停止轮询，避免每 0.5s 的卡顿累积拖累整个 UI（表现为全屏点不动）。
        if (recentFailures >= 3) return;
        try {
            serverTrace = SoloServerBootstrap.RecentEvents();
            recentFailures = 0;
        } catch (System.Exception e) {
            recentFailures++;
            serverTrace = "ERR(csharp): " + e.Message;
        }
        window = WindowsManager.Instance != null ? WindowsManager.Instance.GetCurrentWindow() : "?";
    }

    private void OnGUI() {
        try {
            DrawOverlay();
        } catch (System.Exception e) {
            // OnGUI 抛异常会让「诊断面板」按钮再也画不出来（用户视角就是点了没反应），
            // 把异常本身写进日志缓冲，面板恢复后能自证问题。
            Debug.LogError("[SoloDiagnostics] OnGUI 异常: " + e);
        }
    }

    private void DrawOverlay() {
        if (body == null) {
            int size = Mathf.Max(20, Screen.height / 52);
            body = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false, richText = false };
        }
        if (!visible) {
            // 手机上默认字体下按钮太小容易点不中，适当加大点击区。
            if (GUI.Button(new Rect(8, 8, Mathf.Max(200, body.fontSize * 9), body.fontSize * 2.6f), "诊断面板")) visible = true;
            return;
        }

        var snapshot = new List<string>();
        lock (Lock) snapshot.AddRange(Lines);

        var text = new System.Text.StringBuilder();
        text.AppendLine("本地服务: " + SoloServerBootstrap.Status);
        text.AppendLine("窗口: " + window);
        text.AppendLine("--- 客户端日志 ---");
        foreach (string line in snapshot) text.AppendLine(line);
        text.AppendLine("--- 服务端轨迹 ---");
        string[] trace = serverTrace.Split('\n');
        int from = Mathf.Max(0, trace.Length - MaxLines);
        for (int i = from; i < trace.Length; i++) text.AppendLine(trace[i]);

        string content = text.ToString();
        float width = Screen.width * 0.72f;
        var rect = new Rect(8, 8, width, body.CalcSize(new GUIContent(content)).y + body.fontSize * 3f);
        GUI.Box(rect, GUIContent.none);
        GUI.Label(new Rect(rect.x + 8, rect.y + 6, rect.width - 16, rect.height), content, body);
        if (GUI.Button(new Rect(rect.x + rect.width - 100, rect.y + 4, 92, body.fontSize * 1.8f), "隐藏")) visible = false;
    }
}
