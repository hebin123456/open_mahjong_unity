using UnityEngine;

/// <summary>
/// 单机版启动引导：Android 上通过 Chaquopy 拉起内嵌的本地杭州麻将服务（127.0.0.1:18763）。
/// 其他平台为空操作，编辑器/桌面仍走原有联网流程。
/// </summary>
public static class SoloServerBootstrap {
#if UNITY_ANDROID && !UNITY_EDITOR
    private static bool started;

    /// <summary>
    /// 幂等确保本地 Python 服务已启动。启动会阻塞至端口就绪（正常很快），
    /// 应在客户端发起 WebSocket 连接之前调用。
    /// </summary>
    public static void EnsureStarted() {
        if (started) return;
        try {
            using (var bridge = new AndroidJavaClass("com.omj.solo.SoloBridge")) {
                started = bridge.CallStatic<bool>("start");
            }
            if (started) {
                Debug.Log("[SoloServer] 内嵌 Python 服务已启动");
            } else {
                Debug.LogWarning("[SoloServer] 内嵌 Python 服务启动未成功，稍后重试");
            }
        } catch (System.Exception e) {
            Debug.LogError("[SoloServer] 启动内嵌 Python 服务异常: " + e.Message);
        }
    }
#else
    /// <summary>非 Android 平台为空操作。</summary>
    public static void EnsureStarted() { }
#endif
}
