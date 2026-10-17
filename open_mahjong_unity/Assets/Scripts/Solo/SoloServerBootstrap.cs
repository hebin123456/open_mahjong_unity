using UnityEngine;

/// <summary>
/// 单机版启动引导：Android 上通过 Chaquopy 拉起内嵌的本地杭州麻将服务（127.0.0.1:18763）。
/// 其他平台为空操作，编辑器/桌面仍走原有联网流程。
/// </summary>
public static class SoloServerBootstrap {
#if UNITY_ANDROID && !UNITY_EDITOR
    private static bool started;

    /// <summary>
    /// 最近一次启动结果，以 "OK" 开头表示本地服务已就绪；否则为 "ERR: 原因"。
    /// 连接失败时会随断线提示一起展示，便于定位问题。
    /// </summary>
    public static string Status { get; private set; } = "";

    /// <summary>
    /// 幂等确保本地 Python 服务已启动。启动会阻塞至端口就绪（含首次冷启动解压），
    /// 应在客户端发起 WebSocket 连接之前调用；失败后可再次调用重试。
    /// </summary>
    public static void EnsureStarted() {
        if (started) return;
        try {
            using (var bridge = new AndroidJavaClass("com.omj.solo.SoloBridge")) {
                Status = bridge.CallStatic<string>("startWithStatus");
            }
            started = !string.IsNullOrEmpty(Status) && Status.StartsWith("OK");
            if (started) {
                Debug.Log("[SoloServer] 内嵌 Python 服务已启动: " + Status);
            } else {
                Debug.LogWarning("[SoloServer] 内嵌 Python 服务启动失败: " + Status);
            }
        } catch (System.Exception e) {
            Status = "ERR(csharp): " + e.Message;
            Debug.LogError("[SoloServer] 启动内嵌 Python 服务异常: " + e.Message);
        }
    }
#else
    /// <summary>非 Android 平台没有内嵌服务，状态恒为空。</summary>
    public static string Status => "";
    /// <summary>非 Android 平台为空操作。</summary>
    public static void EnsureStarted() { }
#endif
}
