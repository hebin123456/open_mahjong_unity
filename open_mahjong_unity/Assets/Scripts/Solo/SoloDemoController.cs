using System;
using UnityEngine;

/// <summary>
/// 教学演示控制器：一键生成「4 个机器人自动对局」的完整牌谱并打开回放，用于看机器人怎么打。
///
/// 流程：
/// 1. 点击顶栏「教学演示」按钮（HeaderPanel 在单机下克隆「新手教程」按钮生成，插在其右侧）
///    → 弹出玩法选择（杭州/萧山/绍兴/台州/义乌/国标六选一）；
/// 2. 选择玩法后发送 solo/start_demo {rule}（服务端以 instant 速度后台跑一局 4 机器人演示局）；
/// 3. 服务端跑完回传 solo/demo_record（record_detail 完整牌谱）；
/// 4. 调 RecordPanel.OpenRecord 打开现成的牌谱回放播放器，逐帧观看吃/碰/杠/胡。
///
/// 演示局与房间对局互不干扰（服务端独立任务）；生成期间按钮禁用并显示进度。
/// </summary>
public class SoloDemoController : MonoBehaviour {
    /// <summary>从发起请求到收到牌谱的总超时（instant 速度跑一局通常只需数秒）。</summary>
    private const float GenerateTimeoutSeconds = 180f;

    private static SoloDemoController instance;
    private bool pickerOpen;
    private bool generating;
    private float generatingSince;
    private GUIStyle body;

    // 与服务端 RULE_LABELS 一致的六种玩法，每种玩法各自生成一局演示
    private static readonly string[][] Rules = {
        new[] { "hangzhou", "杭州麻将" },
        new[] { "xiaoshan", "萧山麻将" },
        new[] { "shaoxing", "绍兴麻将" },
        new[] { "taizhou", "台州麻将" },
        new[] { "yiwu", "义乌麻将" },
        new[] { "guobiao", "国标麻将" },
    };

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("SoloDemoController");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<SoloDemoController>();
    }

    /// <summary>顶栏「教学演示」按钮（HeaderPanel 克隆生成）点击时调用：打开玩法选择面板。</summary>
    public static void OpenPicker() {
        if (instance == null) return;
        if (instance.generating) {
            NotificationManager.Instance?.ShowTip("教学演示", false, "正在生成演示牌局，请稍候");
            return;
        }
        instance.pickerOpen = true;
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

    private void StartDemo(string rule) {
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
            var request = new DemoRequest { type = "solo/start_demo", rounds = 1, rule = rule };
            _ = nm.GetWebSocket().SendText(Newtonsoft.Json.JsonConvert.SerializeObject(request));
            Debug.Log("[SoloDemo] 已请求生成演示牌局: " + rule);
        } catch (Exception e) {
            generating = false;
            Debug.LogError("[SoloDemo] 发送演示请求失败: " + e.Message);
            NotificationManager.Instance?.ShowTip("教学演示", false, "发送演示请求失败");
        }
    }

    private sealed class DemoRequest {
        public string type;
        public int rounds;
        public string rule;
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
        // 对局/观战中不显示（生成进度除外，回到牌桌前也能看到状态）
        if (GameSessionGuard.HasExclusiveSession) {
            pickerOpen = false;
            if (!generating) return;
        }
        if (body == null) {
            int size = Mathf.Max(20, Screen.height / 52);
            body = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false };
        }
        // 生成进度指示：原左下角入口按钮已移至顶栏「教学演示」
        if (generating) {
            float h = body.fontSize * 2.6f;
            // 第一行是诊断面板按钮，这里排第二行
            float y = 8f + h + 6f;
            GUI.Box(new Rect(8, y, Mathf.Max(260, body.fontSize * 12), h), GUIContent.none);
            GUI.Label(new Rect(16, y + h * 0.18f, body.fontSize * 11, h), "生成演示牌局中…");
        }
        if (pickerOpen) DrawPicker();
    }

    private void DrawPicker() {
        float h = body.fontSize * 2.6f;
        float pw = Mathf.Max(320, body.fontSize * 16);
        float ph = (h + 8f) * (Rules.Length + 1) + h + 20f;
        float px = (Screen.width - pw) / 2f;
        float py = (Screen.height - ph) / 2f;
        GUI.Box(new Rect(px, py, pw, ph), GUIContent.none);
        GUI.Label(new Rect(px + 16, py + 10, pw - 32, h), "选择演示的玩法（4 个机器人自动对局）", body);
        float by = py + 10f + h + 8f;
        foreach (var rule in Rules) {
            if (GUI.Button(new Rect(px + 16, by, pw - 32, h), rule[1])) {
                pickerOpen = false;
                StartDemo(rule[0]);
            }
            by += h + 8f;
        }
        if (GUI.Button(new Rect(px + 16, by, pw - 32, h), "关闭")) {
            pickerOpen = false;
        }
    }
}
