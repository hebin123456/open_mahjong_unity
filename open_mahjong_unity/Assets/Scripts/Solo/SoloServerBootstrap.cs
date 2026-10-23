using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Sockets;
using UnityEngine;

/// <summary>
/// 单机版启动引导：拉起内嵌的本地杭州麻将服务（127.0.0.1:18763）。
/// Android 走 Chaquopy；Windows/Linux 在可执行文件同级 SoloRuntime/ 里用随包分发的
/// Python 以子进程方式运行同一份 SoloPython 服务端。编辑器为空操作，仍走原有联网流程。
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

    /// <summary>
    /// 内嵌 Python 服务最近的协议轨迹（收/发的消息类型与异常），供诊断面板展示。
    /// 手机上无法查看 logcat，「点了没反应」时这是唯一的定位手段。
    /// </summary>
    public static string RecentEvents() {
        try {
            using (var bridge = new AndroidJavaClass("com.omj.solo.SoloBridge")) {
                return bridge.CallStatic<string>("recentEvents");
            }
        } catch (System.Exception e) {
            return "ERR(csharp): " + e.Message;
        }
    }
#elif (UNITY_STANDALONE_WIN || UNITY_STANDALONE_LINUX) && !UNITY_EDITOR
    private static bool started;
    private static System.Diagnostics.Process _proc;
    private static readonly object _logLock = new object();
    private static readonly Queue<string> _trace = new Queue<string>();

    /// <summary>最近一次启动结果，以 "OK" 开头表示本地服务已就绪；否则为 "ERR: 原因"。</summary>
    public static string Status { get; private set; } = "";

    /// <summary>随包的运行时目录：可执行文件同级的 SoloRuntime/python 与 SoloRuntime/SoloPython。</summary>
    private static string RuntimeRoot =>
        Path.Combine(Path.GetDirectoryName(Application.dataPath) ?? ".", "SoloRuntime");

    /// <summary>各平台随包 Python 解释器在 SoloRuntime/python 下的相对路径。</summary>
    private static string PythonExecutablePath =>
#if UNITY_STANDALONE_WIN
        Path.Combine(RuntimeRoot, "python", "python.exe");
#else
        Path.Combine(RuntimeRoot, "python", "bin", "python3");
#endif

    /// <summary>
    /// 幂等确保本地 Python 服务已启动：拉起嵌入式 Python 子进程，阻塞至端口就绪。
    /// 应在客户端发起 WebSocket 连接之前调用。
    /// </summary>
    public static void EnsureStarted() {
        if (started) return;
        try {
            string root = RuntimeRoot;
            string exe = PythonExecutablePath;
            string script = Path.Combine(root, "SoloPython", "solo_server.py");
            if (!File.Exists(exe) || !File.Exists(script)) {
                Status = "ERR: 缺少本地运行时（" + root + "）";
                Debug.LogWarning("[SoloServer] " + Status);
                return;
            }

            var psi = new System.Diagnostics.ProcessStartInfo(exe, "\"" + script + "\"") {
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            // 局域网多人：监听 0.0.0.0 并开启 UDP 房间发现应答（Android 由 Python 侧默认开启）。
            psi.EnvironmentVariables["SOLO_LAN"] = "1";
            _proc = new System.Diagnostics.Process {
                StartInfo = psi,
                EnableRaisingEvents = true,
            };
            _proc.OutputDataReceived += (_, e) => CaptureTrace(e.Data);
            _proc.ErrorDataReceived += (_, e) => CaptureTrace(e.Data);
            _proc.Start();
            _proc.BeginOutputReadLine();
            _proc.BeginErrorReadLine();
            Application.quitting += Stop;

            bool ok = WaitForPort(ConfigManager.SoloPort, 15.0);
            Status = ok ? "OK" : "ERR: 15s 内未监听 127.0.0.1:" + ConfigManager.SoloPort;
            started = ok;
            Debug.Log("[SoloServer] " + Status);
        } catch (System.Exception e) {
            Status = "ERR(csharp): " + e.Message;
            Debug.LogError("[SoloServer] 启动本地 Python 服务异常: " + e.Message);
        }
    }

    /// <summary>本地 Python 服务最近的协议轨迹，与 Android 端格式一致（→/←/!! 行）。</summary>
    public static string RecentEvents() {
        lock (_logLock) {
            var lines = new string[_trace.Count];
            _trace.CopyTo(lines, 0);
            return string.Join("\n", lines);
        }
    }

    /// <summary>只保留 Python 服务打的 TRACE 行（含 →/←/!! 轨迹），供诊断面板展示。</summary>
    private static void CaptureTrace(string line) {
        if (string.IsNullOrEmpty(line)) return;
        int i = line.IndexOf("TRACE ", StringComparison.Ordinal);
        if (i < 0) return;
        lock (_logLock) {
            if (_trace.Count >= 80) _trace.Dequeue();
            _trace.Enqueue(line.Substring(i + 6));
        }
    }

    /// <summary>轮询直到服务端端口可连上，确认 asyncio 事件循环已监听。</summary>
    private static bool WaitForPort(int port, double timeoutSeconds) {
        var deadline = DateTime.UtcNow.AddSeconds(timeoutSeconds);
        while (DateTime.UtcNow < deadline) {
            try {
                using (var client = new TcpClient()) {
                    var ar = client.BeginConnect("127.0.0.1", port, null, null);
                    if (ar.AsyncWaitHandle.WaitOne(300) && client.Connected) return true;
                }
            } catch { }
            System.Threading.Thread.Sleep(200);
        }
        return false;
    }

    /// <summary>退出时回收子进程，避免残留 Python 进程占住端口。</summary>
    private static void Stop() {
        try {
            if (_proc != null && !_proc.HasExited) _proc.Kill();
        } catch { }
        _proc = null;
        started = false;
    }
#else
    /// <summary>编辑器等未内嵌服务端的平台，状态恒为空。</summary>
    public static string Status => "";
    /// <summary>未内嵌服务端的平台为空操作。</summary>
    public static void EnsureStarted() { }
    /// <summary>未内嵌服务端的平台没有服务端轨迹。</summary>
    public static string RecentEvents() => "";
#endif
}
