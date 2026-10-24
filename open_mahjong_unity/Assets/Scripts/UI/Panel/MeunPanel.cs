using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using TMPro;

public class MeunPanel : MonoBehaviour {
    public static MeunPanel Instance { get; private set; }

    [Header("排位匹配入口")]
    [SerializeField] private Button matchEntryButton;
    [SerializeField] private TMP_Text matchPlayerCountText;

    // 折叠屏适配：左右两栏都是中心锚 + 固定偏移，展开态画布变窄（逻辑 ~1499 宽，
    // 场景摆位按 1920 调）后左栏左缘/右栏右缘会推出屏；画布尺寸变化时钳回画布。
    private RectTransform menuCanvasRect;
    private Vector2 lastMenuCanvasSize;
    private RectTransform menuLeftColumn;
    private RectTransform menuRightColumn;

    private void Update() {
        if (menuCanvasRect == null) {
            Canvas canvas = GetComponentInParent<Canvas>();
            if (canvas == null) return;
            menuCanvasRect = canvas.transform as RectTransform;
        }
        Vector2 size = menuCanvasRect.rect.size;
        if (Mathf.Abs(size.x - lastMenuCanvasSize.x) < 0.5f
            && Mathf.Abs(size.y - lastMenuCanvasSize.y) < 0.5f) {
            return;
        }
        lastMenuCanvasSize = size;
        if (menuLeftColumn == null) menuLeftColumn = transform.Find("GameObject") as RectTransform;
        if (menuRightColumn == null) menuRightColumn = transform.Find("RoomListPanel/GameObject") as RectTransform;
        UiRectClamp.ClampIntoCanvas(menuLeftColumn);
        UiRectClamp.ClampIntoCanvas(menuRightColumn);
    }

    private void Awake() {
        if (Instance != null && Instance != this) {
            Destroy(gameObject);
            return;
        }
        Instance = this;
    }

    private void Start() {
        if (matchEntryButton != null) {
            matchEntryButton.onClick.RemoveListener(GoToMatch);
            matchEntryButton.onClick.AddListener(GoToMatch);
            // 单机版没有排位匹配大厅功能，隐藏主菜单上的匹配入口。
            if (ConfigManager.SoloMode) matchEntryButton.gameObject.SetActive(false);
        }
        // 单机版隐藏主菜单上的联机残留：「匹配人数(N)」文本与背包入口（装扮/道具走服务器，单机无数据）。
        if (ConfigManager.SoloMode) {
            if (matchPlayerCountText != null) matchPlayerCountText.gameObject.SetActive(false);
            var inventory = transform.Find("GameObject/InventoryButton");
            if (inventory != null) inventory.gameObject.SetActive(false);
        }
    }

    private void OnEnable() {
        UpdateMatchPlayerCountText(0);
        if (ConfigManager.SoloMode) return;
        NetworkPollingManager.Instance.StartMenuMatchPlayerCountPolling();
    }

    private void OnDisable() {
        if (ConfigManager.SoloMode) return;
        NetworkPollingManager.Instance.StopMenuMatchPlayerCountPolling();
    }

    // 设置用户信息（通过UserContainer处理UI，UserDataManager管理数据）
    public void SetUserInfo(string username, string userkey, int user_id, bool isTourist = false) {
        UserContainer.Instance.SetUserInfo(username, userkey, user_id, isTourist);
    }

    // 显示服务器统计信息（通过NowPlayer显示）
    public void DisplayServerStats(int onlinePlayerCount, int waitingRoomCount, int playingRoomCount, int matchPlayingGames) {
        NowPlayer.Instance.DisplayServerStats(onlinePlayerCount, waitingRoomCount, playingRoomCount, matchPlayingGames);
    }

    public void GoToMatch() {
        // 对局/房间中允许查阅匹配页人数；真正加入队列时再由 MatchNetworkManager 拦截
        WindowsManager.Instance.SwitchWindow("match");
    }

    public void UpdateMatchPlayerCount(Dictionary<string, QueueStatusEntry> queueStatus, int? uniquePlayerCount = null) {
        UpdateMatchPlayerCountText(uniquePlayerCount ?? CountTotalMatchPlayers(queueStatus));
    }

    public static int CountTotalMatchPlayers(Dictionary<string, QueueStatusEntry> queueStatus) {
        if (queueStatus == null) return 0;
        int total = 0;
        foreach (var entry in queueStatus.Values) {
            total += entry.waiting + entry.playing;
        }
        return total;
    }

    private void UpdateMatchPlayerCountText(int count) {
        if (matchPlayerCountText != null) {
            matchPlayerCountText.text = $"匹配人数({count})";
        }
    }

}
