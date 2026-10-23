using System;
using UnityEngine;

/// <summary>
/// 教学演示控制器：一键生成「4 个机器人自动对局」的完整牌谱并打开回放，用于看机器人怎么打。
///
/// 流程：
/// 1. 点击「教学演示」→ 发送 solo/start_demo（服务端以 instant 速度后台跑一局 4 机器人演示局）；
/// 2. 服务端跑完回传 solo/demo_record（record_detail 完整牌谱）；
/// 3. 调 RecordPanel.OpenRecord 打开现成的牌谱回放播放器，逐帧观看吃/碰/杠/胡。
///
/// 演示局与房间对局互不干扰（服务端独立任务）；生成期间按钮禁用并显示进度。
/// </summary>
public class SoloDemoController : MonoBehaviour {
    /// <summary>从发起请求到收到牌谱的总超时（instant 速度跑一局通常只需数秒）。</summary>
    private const float GenerateTimeoutSeconds = 180f;

    private static SoloDemoController instance;
    private bool generating;
    private float generatingSince;
    private GUIStyle body;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("SoloDemoController");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<SoloDemoController>();
    }

    /// <summary>NetworkManager 收到 solo/start_demo / solo/demo_record 时转交这里。</summary>
    public static void HandleDemoMessage(Response response) {
        if (instance == null || response == null) return;
        instance.HandleMessage(response);
    }

    private void HandleMessage(Response response) {
        switch (response.type) {
            case "solo/start_demo":
                if (!response.success) {
                    generating = false;
                    NotificationManager.Instance?.ShowTip("教学演示", false,
                        string.IsNullOrEmpty(response.message) ? "演示牌局启动失败" : response.message);
                }
                break;
            case "solo/demo_record":
                generating = false;
                if (!response.success) {
                    NotificationManager.Instance?.ShowTip("教学演示", false,
                        string.IsNullOrEmpty(response.message) ? "演示牌谱生成失败" : response.message);
                    return;
                }
                // OpenRecord 内部会切到回放场景并自带失败提示
                RecordPanel.OpenRecord(response.record_detail, localPlayback: true);
                break;
        }
    }

    private void StartDemo() {
        var nm = NetworkManager.Instance;
        if (nm == null || !nm.IsWebSocketOpen) {
            NotificationManager.Instance?.ShowTip("教学演示", false, "尚未连接本地服务，请稍后再试");
            return;
        }
        var udm = UserDataManager.Instance;
        if (udm == null || udm.UserId == 0) {
            NotificationManager.Instance?.ShowTip("教学演示", false, "登录完成后才能生成演示");
            return;
        }
        generating = true;
        generatingSince = Time.unscaledTime;
        try {
            var request = new DemoRequest { type = "solo/start_demo", rounds = 1 };
            _ = nm.GetWebSocket().SendText(Newtonsoft.Json.JsonConvert.SerializeObject(request));
            Debug.Log("[SoloDemo] 已请求生成演示牌局");
        } catch (Exception e) {
            generating = false;
            Debug.LogError("[SoloDemo] 发送演示请求失败: " + e.Message);
            NotificationManager.Instance?.ShowTip("教学演示", false, "发送演示请求失败");
        }
    }

    private sealed class DemoRequest {
        public string type;
        public int rounds;
    }

    private void Update() {
        if (!generating) return;
        if (Time.unscaledTime - generatingSince > GenerateTimeoutSeconds) {
            generating = false;
            NotificationManager.Instance?.ShowTip("教学演示", false, "演示牌局生成超时，请重试");
        }
    }

    private void OnGUI() {
        try {
            DrawOverlay();
        } catch (Exception e) {
            Debug.LogError("[SoloDemo] OnGUI 异常: " + e);
        }
    }

    private void DrawOverlay() {
        // 对局/观战中不显示
        if (GameSessionGuard.HasExclusiveSession) return;
        if (body == null) {
            int size = Mathf.Max(20, Screen.height / 52);
            body = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false };
        }
        float h = body.fontSize * 2.6f;
        // 第一行是诊断面板按钮，这里排第二行（局域网房间按钮已随按房号加入改造移除）
        float y = 8f + h + 6f;
        if (generating) {
            GUI.Box(new Rect(8, y, Mathf.Max(260, body.fontSize * 12), h), GUIContent.none);
            GUI.Label(new Rect(16, y + h * 0.18f, body.fontSize * 11, h), "生成演示牌局中…");
            return;
        }
        if (GUI.Button(new Rect(8, y, Mathf.Max(200, body.fontSize * 9), h), "教学演示")) {
            StartDemo();
        }
    }
}
