using System;
using UnityEngine;

/// <summary>
/// 交互式新手教程控制器：固定牌墙的教学局里，跟着屏幕提示一步步学出牌/吃/碰/杠/胡。
///
/// 流程：
/// 1. 点击顶栏「新手教程」按钮（HeaderPanel 在单机下克隆战绩按钮生成，插在战绩后面）；
/// 2. 先选玩法（杭州/萧山/绍兴/台州/义乌/国标六选一，各玩法规则与牌型不同），
///    再选场景（出牌/吃/碰/杠/胡五选一），发送 solo/start_tutorial {rule, scenario}
///    → 服务端按所选玩法开一局教学对局（真人固定坐庄、座位 1-3 是机器人、首局发牌按脚本固定）；
/// 3. 每当真人座位出现可操作窗口，服务端推 solo/tutorial_hint，这里显示成屏幕上方的指导气泡。
///
/// 教程局一局（胡牌或流局）即整场结束；提示气泡在对局内持续显示，超时或被下一条提示替换。
/// </summary>
public class SoloTutorialController : MonoBehaviour {
    /// <summary>无新提示时气泡保留时长（教学局里每个行动窗口都会刷新）。</summary>
    private const float HintDurationSeconds = 15f;

    private static SoloTutorialController instance;
    private bool pickerOpen;
    private bool inTutorial;
    private bool tutorialGameEntered;
    private string pickerRule;
    private string hint;
    private float hintExpiresAt;
    private GUIStyle body;
    private GUIStyle buttonStyle;
    private GUIStyle hintStyle;

    private static readonly string[][] Scenarios = {
        new[] { "chu", "学会出牌" },
        new[] { "chi", "学会吃" },
        new[] { "peng", "学会碰" },
        new[] { "angang", "学会杠" },
        new[] { "hu", "学会胡牌" },
    };

    // 与服务端 RULE_LABELS 一致的六种玩法，每种玩法各有独立的教学牌墙与规则
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
        var go = new GameObject("SoloTutorialController");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<SoloTutorialController>();
    }

    /// <summary>顶栏「新手教程」按钮（HeaderPanel 克隆生成）点击时调用：打开玩法→场景选择面板。</summary>
    public static void OpenPicker() {
        if (instance == null) return;
        instance.pickerOpen = true;
        instance.pickerRule = null;
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
                    inTutorial = true;
                    pickerOpen = false;
                    pickerRule = null;
                } else {
                    NotificationManager.Instance?.ShowTip("新手教程", false,
                        string.IsNullOrEmpty(response.message) ? "教程启动失败" : response.message);
                }
                break;
            case "solo/stop_tutorial":
                if (response.success) {
                    inTutorial = false;
                    tutorialGameEntered = false;
                    hint = null;
                    // 服务端终止教学局是直接 cancel 任务、不发 game_end：必须走标准退场
                    // 强制清理对局场景（IsGameActive、3D 牌面、gamestate_id、返回按钮），
                    // 否则 HasExclusiveSession 残留会把教程/演示的选择面板每帧收起，
                    // 回到主界面后就再也打不开了。
                    PostGameNavigator.ExitToLobby(forceTeardown: true);
                } else {
                    NotificationManager.Instance?.ShowTip("新手教程", false,
                        string.IsNullOrEmpty(response.message) ? "结束教程失败" : response.message);
                }
                break;
            case "solo/tutorial_hint":
                hint = response.message;
                hintExpiresAt = Time.unscaledTime + HintDurationSeconds;
                break;
        }
    }

    private void StartScenario(string rule, string key) {
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
            var request = new TutorialRequest { type = "solo/start_tutorial", rule = rule, scenario = key };
            _ = nm.GetWebSocket().SendText(Newtonsoft.Json.JsonConvert.SerializeObject(request));
            Debug.Log("[SoloTutorial] 已请求教程场景: " + rule + "/" + key);
        } catch (Exception e) {
            Debug.LogError("[SoloTutorial] 发送教程请求失败: " + e.Message);
            NotificationManager.Instance?.ShowTip("新手教程", false, "发送教程请求失败");
        }
    }

    private sealed class TutorialRequest {
        public string type;
        public string rule;
        public string scenario;
    }

    private void Update() {
        if (hint != null && Time.unscaledTime > hintExpiresAt) hint = null;
        // 教学局打完离开对局场景后自动结束教程状态。
        // 注意 start ack 时游戏窗口尚未切过去，须等真正进过对局再回收，否则 inTutorial 会被立刻清掉。
        if (inTutorial && GameSessionGuard.HasExclusiveSession) tutorialGameEntered = true;
        if (inTutorial && tutorialGameEntered && !GameSessionGuard.HasExclusiveSession) {
            inTutorial = false;
            tutorialGameEntered = false;
        }
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
            // 教程面向新手，文字要比诊断面板（h/52≈20px@1080p）明显更大才看得清
            int size = Mathf.Max(28, Screen.height / 26);
            body = new GUIStyle(GUI.skin.label) { fontSize = size, wordWrap = false };
            buttonStyle = new GUIStyle(GUI.skin.button) { fontSize = size, wordWrap = false };
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
        // 玩法→场景选择面板：由顶栏「新手教程」按钮打开；进入对局/观战时自动收起
        if (GameSessionGuard.HasExclusiveSession) {
            pickerOpen = false;
            pickerRule = null;
            // 教学局没有现成的退出入口，对局内常驻「结束教程」按钮（提示气泡正下方）
            if (inTutorial) DrawStopButton();
            return;
        }
        if (!pickerOpen) return;
        if (pickerRule == null) DrawRulePicker();
        else DrawScenarioPicker();
    }

    private void DrawRulePicker() {
        float h = body.fontSize * 2.6f;
        float pw = Mathf.Max(320, body.fontSize * 16);
        // 标题一行 + 玩法按钮 + 关闭，标题高度也要计入，否则关闭按钮被挤出面板外
        float ph = (h + 8f) * (Rules.Length + 1) + h + 20f;
        float px = (Screen.width - pw) / 2f;
        float py = (Screen.height - ph) / 2f;
        GUI.Box(new Rect(px, py, pw, ph), GUIContent.none);
        GUI.Label(new Rect(px + 16, py + 10, pw - 32, h), "选择要学习的玩法（各玩法规则与牌型不同）", body);
        float by = py + 10f + h + 8f;
        foreach (var rule in Rules) {
            if (GUI.Button(new Rect(px + 16, by, pw - 32, h), rule[1], buttonStyle)) {
                pickerRule = rule[0];
            }
            by += h + 8f;
        }
        if (GUI.Button(new Rect(px + 16, by, pw - 32, h), "关闭", buttonStyle)) {
            pickerOpen = false;
        }
    }

    /// <summary>对局中常驻的「结束教程」按钮：点击后服务端终止教学局并回到主菜单。</summary>
    private void DrawStopButton() {
        float w = body.fontSize * 5f + 24f;
        float h = body.fontSize * 1.7f;
        float y = 12f;
        if (hint != null) {
            float bw = Mathf.Min(Screen.width * 0.72f, body.fontSize * 32f);
            y += hintStyle.CalcHeight(new GUIContent(hint), bw) + body.fontSize * 0.9f + 8f;
        }
        if (GUI.Button(new Rect((Screen.width - w) / 2f, y, w, h), "结束教程", buttonStyle)) {
            var nm = NetworkManager.Instance;
            if (nm != null && nm.IsWebSocketOpen) {
                _ = nm.GetWebSocket().SendText(Newtonsoft.Json.JsonConvert.SerializeObject(
                    new TutorialRequest { type = "solo/stop_tutorial" }));
            }
        }
    }

    private void DrawScenarioPicker() {
        string label = RuleLabel(pickerRule);
        float h = body.fontSize * 2.6f;
        float pw = Mathf.Max(320, body.fontSize * 16);
        float ph = (h + 8f) * (Scenarios.Length + 1) + h + 20f;
        float px = (Screen.width - pw) / 2f;
        float py = (Screen.height - ph) / 2f;
        GUI.Box(new Rect(px, py, pw, ph), GUIContent.none);
        GUI.Label(new Rect(px + 16, py + 10, pw - 32, h), "选择要学习的操作（" + label + "）", body);
        float by = py + 10f + h + 8f;
        foreach (var scenario in Scenarios) {
            if (GUI.Button(new Rect(px + 16, by, pw - 32, h), scenario[1], buttonStyle)) {
                string rule = pickerRule;
                pickerOpen = false;
                pickerRule = null;
                StartScenario(rule, scenario[0]);
            }
            by += h + 8f;
        }
        // 底部一行：左「返回」回玩法选择，右「关闭」
        float bw = (pw - 32f - 8f) / 2f;
        if (GUI.Button(new Rect(px + 16, by, bw, h), "返回", buttonStyle)) {
            pickerRule = null;
        }
        if (GUI.Button(new Rect(px + 16 + bw + 8f, by, bw, h), "关闭", buttonStyle)) {
            pickerOpen = false;
            pickerRule = null;
        }
    }

    private static string RuleLabel(string rule) {
        foreach (var r in Rules) {
            if (r[0] == rule) return r[1];
        }
        return rule;
    }
}
