using System;
using UnityEngine;

/// <summary>
/// 交互式新手教程控制器：固定牌墙的教学局里，跟着屏幕提示一步步学出牌/吃/碰/杠/胡。
///
/// 流程：
/// 1. 点击「新手教程」→ 弹出场景选择（出牌/吃/碰/杠/胡，五选一）；
/// 2. 发送 solo/start_tutorial {scenario} → 服务端开一局教学对局
///    （真人固定坐庄、座位 1-3 是机器人、首局发牌按脚本固定）；
/// 3. 每当真人座位出现可操作窗口，服务端推 solo/tutorial_hint，这里显示成屏幕上方的指导气泡。
///
/// 教程局一局（胡牌或流局）即整场结束；提示气泡在对局内持续显示，超时或被下一条提示替换。
/// </summary>
public class SoloTutorialController : MonoBehaviour {
    /// <summary>无新提示时气泡保留时长（教学局里每个行动窗口都会刷新）。</summary>
    private const float HintDurationSeconds = 15f;

    private static SoloTutorialController instance;
    private bool pickerOpen;
    private string hint;
    private float hintExpiresAt;
    private GUIStyle body;
    private GUIStyle hintStyle;

    private static readonly string[][] Scenarios = {
        new[] { "chu", "学会出牌" },
        new[] { "chi", "学会吃" },
        new[] { "peng", "学会碰" },
        new[] { "angang", "学会杠" },
        new[] { "hu", "学会胡牌" },
    };

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("SoloTutorialController");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<SoloTutorialController>();
    }

    /// <summary>NetworkManager 收到 solo/start_tutorial / solo/tutorial_hint 时转交这里。</summary>
    public static void HandleTutorialMessage(Response response) {
        if (instance == null || response == null) return;
        instance.HandleMessage(response);
    }

    private void HandleMessage(Response response) {
        switch (response.type) {
            case "solo/start_tutorial":
                if (response.success) {
                    pickerOpen = false;
                } else {
                    NotificationManager.Instance?.ShowTip("新手教程", false,
                        string.IsNullOrEmpty(response.message) ? "教程启动失败" : response.message);
                }
                break;
            case "solo/tutorial_hint":
                hint = response.message;
                hintExpiresAt = Time.unscaledTime + HintDurationSeconds;
                break;
        }
    }

    private void StartScenario(string key) {
        var nm = NetworkManager.Instance;
        if (nm == null || !nm.IsWebSocketOpen) {
            NotificationManager.Instance?.ShowTip("新手教程", false, "尚未连接本地服务，请稍后再试");
            return;
        }
        var udm = UserDataManager.Instance;
        if (udm == null || udm.UserId == 0) {
            NotificationManager.Instance?.ShowTip("新手教程", false, "登录完成后才能学习");
            return;
        }
        try {
            var request = new TutorialRequest { type = "solo/start_tutorial", scenario = key };
            _ = nm.GetWebSocket().SendText(Newtonsoft.Json.JsonConvert.SerializeObject(request));
            Debug.Log("[SoloTutorial] 已请求教程场景: " + key);
        } catch (Exception e) {
            Debug.LogError("[SoloTutorial] 发送教程请求失败: " + e.Message);
            NotificationManager.Instance?.ShowTip("新手教程", false, "发送教程请求失败");
        }
    }

    private sealed class TutorialRequest {
        public string type;
        public string scenario;
    }

    private void Update() {
        if (hint != null && Time.unscaledTime > hintExpiresAt) hint = null;
    }

    private void OnGUI() {
        try {
            DrawOverlay();
        } catch (Exception e) {
            Debug.LogError("[SoloTutorial] OnGUI 异常: " + e);
        }
    }

    private void DrawOverlay() {
        if (body == null) {
            int size = Mathf.Max(20, Screen.height / 52);
            body = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false };
            hintStyle = new GUIStyle(GUI.skin.box) {
                fontSize = size, wordWrap = true, alignment = TextAnchor.MiddleCenter,
            };
        }
        // 指导气泡：对局中也要显示（教学提示正是在对局里推送的），顶部居中
        if (hint != null) {
            float w = Mathf.Min(Screen.width * 0.72f, body.fontSize * 32f);
            float h = hintStyle.CalcHeight(new GUIContent(hint), w) + body.fontSize * 0.9f;
            GUI.Box(new Rect((Screen.width - w) / 2f, 12f, w, h), hint, hintStyle);
        }
        // 入口按钮：对局/观战中不显示；局域网浏览器窗口展开时避让（它铺满左上区域）
        if (GameSessionGuard.HasExclusiveSession || LanRoomBrowser.BrowserWindowVisible) return;
        float h2 = body.fontSize * 2.6f;
        // 第一行诊断面板、第二行局域网房间、第三行教学演示，这里排第四行
        float y = 8f + (h2 + 6f) * 3f;
        if (GUI.Button(new Rect(8, y, Mathf.Max(200, body.fontSize * 9), h2), "新手教程")) {
            pickerOpen = !pickerOpen;
        }
        if (!pickerOpen) return;
        // 场景选择：屏幕居中小面板
        float pw = Mathf.Max(320, body.fontSize * 16);
        float ph = (h2 + 8f) * Scenarios.Length + h2 + 20f;
        float px = (Screen.width - pw) / 2f;
        float py = (Screen.height - ph) / 2f;
        GUI.Box(new Rect(px, py, pw, ph), GUIContent.none);
        GUI.Label(new Rect(px + 16, py + 10, pw - 32, h2), "选择要学习的操作", body);
        float by = py + 10f + h2 + 8f;
        foreach (var scenario in Scenarios) {
            if (GUI.Button(new Rect(px + 16, by, pw - 32, h2), scenario[1])) {
                StartScenario(scenario[0]);
            }
            by += h2 + 8f;
        }
        if (GUI.Button(new Rect(px + 16, by, pw - 32, h2), "关闭")) {
            pickerOpen = false;
        }
    }
}
