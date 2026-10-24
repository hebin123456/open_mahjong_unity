using UnityEngine;
using UnityEngine.UI;
using TMPro;

/// <summary>
/// 单个玩家栏位面板：负责显示玩家名、查看信息按钮、移除玩家按钮
/// </summary>
public partial class PlayerRoomPanel : MonoBehaviour {
    [SerializeField] private TMP_Text playerName;          // 玩家名文本
    [SerializeField] private Button playerinfoButton;      // 玩家信息按钮
    [SerializeField] private Button removePlayerButton;    // 移除玩家按钮
    [SerializeField] private GameObject readyIcon;         // 准备状态
    [SerializeField] private GameObject emptySeatBotControls;
    [SerializeField] private Button addAutoBotButton;
    [SerializeField] private Button addEasyBotButton;
    [SerializeField] private Button addEfficiencyBotButton;
    [SerializeField] private Button addHeuristicBotButton;

    public int UserId;
    public int SeatIndex { get; private set; }

    public void SetSeatIndex(int index) => SeatIndex = index;

    private void Awake() {

        playerinfoButton.onClick.AddListener(OnPlayerInfoClicked);
        removePlayerButton.onClick.AddListener(OnRemovePlayerClicked);
        EnsureBotButtons();
        if (addAutoBotButton) addAutoBotButton.onClick.AddListener(() => AddBot(0));
        if (addEfficiencyBotButton) addEfficiencyBotButton.onClick.AddListener(() => AddBot(2));
        if (addHeuristicBotButton) addHeuristicBotButton.onClick.AddListener(() => AddBot(3));

        Clear();
    }

    /// <summary>
    /// 设置玩家信息与按钮可用状态
    /// </summary>
    public void SetPlayer(string username, int userId, bool canRemove) {
        UserId = userId;
        playerName.text = username ?? string.Empty;
        // 玩家信息按钮始终可点击（是否有数据由点击回调内部判断）
        playerinfoButton.interactable = true;
        removePlayerButton.gameObject.SetActive(canRemove);
        removePlayerButton.interactable = canRemove;
        if (emptySeatBotControls) emptySeatBotControls.SetActive(false);
    }

    /// <summary>
    /// 设置准备状态，房主无需准备。
    /// </summary>
    public void SetReady(bool ready) {
        if (readyIcon != null) {
            readyIcon.SetActive(ready);
        }
    }

    /// <summary>
    /// 清空显示
    /// </summary>
    public void Clear() {
        UserId = -1;
        if (emptySeatBotControls) emptySeatBotControls.SetActive(false);
        playerName.text = string.Empty;
        playerinfoButton.interactable = true;
        removePlayerButton.gameObject.SetActive(false);
        if (readyIcon != null) {
            readyIcon.SetActive(false);
        }
    }

    private void OnPlayerInfoClicked() {
        if (UserId < 0) return;
        if (UserId >= 10) {
            // 第一次加载需要玩家信息
            DataNetworkManager.Instance.GetGuobiaoStats(UserId.ToString(), need_player_info: true);
        } else {
            NotificationManager.Instance.ShowTip("error", false, "机器人没有对局数据");
        }
    }

    public void SetEmptySeatBotControls(bool canAdd, bool supportsHighLevel) {
        if (!emptySeatBotControls) return;
        if (addHeuristicBotButton) addHeuristicBotButton.gameObject.SetActive(supportsHighLevel);
        LayoutBotButtons(supportsHighLevel);
        emptySeatBotControls.SetActive(UserId < 0 && canAdd);
    }

    /// <summary>旧场景只序列化了三个机器人按钮；运行时补一个低级按钮，四种水平可并存。</summary>
    private void EnsureBotButtons() {
        if (emptySeatBotControls == null) return;
        if (addEasyBotButton == null) {
            Button template = addEfficiencyBotButton != null ? addEfficiencyBotButton : addAutoBotButton;
            if (template != null) {
                addEasyBotButton = Instantiate(template, emptySeatBotControls.transform);
                addEasyBotButton.name = "AddEasyBot";
                addEasyBotButton.onClick = new Button.ButtonClickedEvent();
                addEasyBotButton.gameObject.SetActive(true);
            }
        }
        if (addEasyBotButton != null) addEasyBotButton.onClick.AddListener(() => AddBot(1));
        LayoutBotButtons(true);
    }

    private void LayoutBotButtons(bool includeHigh) {
        const float spacing = 72;
        float top = (includeHigh ? 1.5f : 1f) * spacing;
        LayoutBotButton(addAutoBotButton, "摸切机器人", top);
        LayoutBotButton(addEasyBotButton, "低级机器人", top - spacing);
        LayoutBotButton(addEfficiencyBotButton, "中级机器人", top - spacing * 2);
        if (includeHigh) {
            LayoutBotButton(addHeuristicBotButton, "高级机器人", top - spacing * 3);
        }
    }

    private static void LayoutBotButton(Button button, string label, float y) {
        if (button == null) return;
        var rect = (RectTransform)button.transform;
        rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(.5f, .5f);
        rect.anchoredPosition = new Vector2(0, y);
        rect.sizeDelta = new Vector2(284, 64);
        rect.localScale = Vector3.one;
        var text = button.GetComponentInChildren<TMP_Text>(true);
        if (text == null) return;
        text.text = label;
        text.enableAutoSizing = false;
        text.fontSize = 27;
    }

    private void AddBot(int kind) {
        if (UserId < 0 && RoomPanel.Instance != null) RoomPanel.Instance.AddBotFromSeat(kind, SeatIndex);
    }

    private void OnRemovePlayerClicked() {
        // 仅房主才有权限，RoomPanel 会控制 canRemove，因此这里不再重复判断
        string roomId = UserDataManager.Instance.RoomId;
        if (!string.IsNullOrEmpty(roomId)) {
            RoomNetworkManager.Instance.KickPlayerFromRoom(roomId, UserId, SeatIndex);
        }
    }
}
