using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;

public class HeaderPanel : MonoBehaviour {
    public static HeaderPanel Instance { get; private set; }

    [SerializeField] private HeaderButton menuButton;
    [SerializeField] private HeaderButton roomButton;
    [SerializeField] private HeaderButton recordButton;
    [SerializeField] private HeaderButton playerDataButton;
    [SerializeField] private HeaderButton configButton;
    [SerializeField] private HeaderButton aboutUsButton;
    [SerializeField] private HeaderButton noticeButton;
    [SerializeField] private HeaderButton sceneConfigButton;
    [SerializeField] private HeaderButton spectatorButton;
    [SerializeField] private HeaderButton matchButton;
    [SerializeField] private HeaderButton eventButton;
    [SerializeField] private HeaderButton friendButton;
    [SerializeField] private HeaderButton backToGameButton;
    [SerializeField] private HeaderButton logoutButton;

    // 单机「新手教程」入口：场景里没有现成按钮，运行时克隆战绩按钮生成（见 AttachSoloTutorialButton）
    private HeaderButton soloTutorialButton;
    // 单机「教学演示」入口：克隆「新手教程」按钮生成，插在其右侧
    private HeaderButton soloDemoButton;
    private HeaderButton soloCharacterButton;
    [Tooltip("「正在对局中」的红色提醒按钮所使用的底色")]
    [SerializeField] private Color backToGameTintColor = new Color(0.92f, 0.34f, 0.34f);
    [Header("轮到自己操作时的提醒闪烁")]
    [Tooltip("回到主菜单后轮到自己操作，按钮在 turnFlashColorA 与 turnFlashColorB 之间渐变循环")]
    [SerializeField] private Color turnFlashColorA = new Color(0.94f, 0.32f, 0.32f);
    [SerializeField] private Color turnFlashColorB = new Color(1.00f, 0.78f, 0.20f);
    [Tooltip("一次完整渐变周期所需秒数")]
    [SerializeField] private float turnFlashCycleSeconds = 1.0f;

    [Header("仅由 Panel 设置的 Stay 颜色（如：在房间里但未选房间页时房间按钮的颜色）")]
    [SerializeField] private Color playOnStayColor = new Color(0.6f, 0.85f, 1f);

    private string _currentWindowName;

    private void Awake() {
        if (Instance != null && Instance != this) {
            Destroy(gameObject);
            return;
        }
        Instance = this;
    }

    private void Start() {
        UserDataManager.Instance.OnRoomIdChanged += RefreshButtonAppearance;
        if (backToGameButton != null) {
            backToGameButton.Button.gameObject.SetActive(false);
            backToGameButton.Button.onClick.AddListener(BackToGame);
            ApplyBackToGameTint();
        }

        if (matchButton != null) {
            matchButton.Button.onClick.AddListener(Match);
        }
        if (eventButton != null) {
            eventButton.Button.onClick.AddListener(Event);
        }

        if (menuButton != null) menuButton.Button.onClick.AddListener(Menu);
        if (roomButton != null) roomButton.Button.onClick.AddListener(Room);
        if (recordButton != null) recordButton.Button.onClick.AddListener(Record);
        if (playerDataButton != null) playerDataButton.Button.onClick.AddListener(PlayerData);
        if (configButton != null) configButton.Button.onClick.AddListener(Config);
        if (aboutUsButton != null) aboutUsButton.Button.onClick.AddListener(AboutUs);
        if (noticeButton != null) noticeButton.Button.onClick.AddListener(Notice);
        if (sceneConfigButton != null) sceneConfigButton.Button.onClick.AddListener(SceneConfig);
        if (spectatorButton != null) spectatorButton.Button.onClick.AddListener(Spectator);
        if (friendButton != null) friendButton.Button.onClick.AddListener(Friend);
        if (logoutButton != null) logoutButton.Button.onClick.AddListener(Logout);

        ApplySoloModeVisibility();

        // 单机：在「战绩」后面插入「新手教程」「教学演示」入口（克隆按钮，样式与导航栏一致）
        if (ConfigManager.SoloMode) {
            AttachSoloTutorialButton(SoloTutorialController.OpenPicker);
            AttachSoloDemoButton(SoloDemoController.OpenPicker);
            AttachSoloCharacterButton(CharacterPicker.Open);
        }

        ConfigManager.OnLanguageChanged += RefreshHeaderLabels;
        UnreadBadgeStore.OnChanged += RefreshUnreadBadges;
        RefreshHeaderLabels();
        RefreshUnreadBadges();
    }

    private void OnDestroy() {
        ConfigManager.OnLanguageChanged -= RefreshHeaderLabels;
        UnreadBadgeStore.OnChanged -= RefreshUnreadBadges;
        UserDataManager.Instance.OnRoomIdChanged -= RefreshButtonAppearance;
    }

    /// <summary>
    /// 单机版只保留打牌相关入口（房间/战绩/设置/场景设置/关于我们），
    /// 好友、观战、数据、匹配、赛事&基地、通知、登出等大厅功能入口一律隐藏；
    /// 牌谱入口在单机下改为「战绩」（本地历史战绩，见 LocalBattleRecordPanel）。
    /// </summary>
    private void ApplySoloModeVisibility() {
        if (!ConfigManager.SoloMode) return;
        SetNavActive(friendButton, false);
        SetNavActive(spectatorButton, false);
        SetNavActive(playerDataButton, false);
        SetNavActive(matchButton, false);
        SetNavActive(eventButton, false);
        SetNavActive(noticeButton, false);
        SetNavActive(logoutButton, false);
        // recordButton 保留：单机下作为「战绩」入口
    }

    private static void SetNavActive(HeaderButton button, bool active) {
        if (button == null) return;
        button.gameObject.SetActive(active);
    }

    /// <summary>
    /// 单机版：克隆「战绩」按钮生成「新手教程」入口并插在其后（LeftHorizontalLayoutGroup 自动排列）。
    /// 教程入口没有场景资源，克隆现成按钮以保证图标/字号/间距与导航栏一致；
    /// 运行时监听不会随 Instantiate 复制，但仍重置 onClick 以防战绩按钮带持久化调用。
    /// </summary>
    private void AttachSoloTutorialButton(UnityEngine.Events.UnityAction onClick) {
        if (soloTutorialButton != null) return;
        soloTutorialButton = AttachSoloHeaderButton(recordButton, "SoloTutorial", 150f, onClick);
    }

    private void AttachSoloDemoButton(UnityEngine.Events.UnityAction onClick) {
        if (soloDemoButton != null) return;
        soloDemoButton = AttachSoloHeaderButton(soloTutorialButton, "SoloDemo", 150f, onClick);
    }

    /// <summary>单机版：在「教学演示」右侧再克隆一个「角色」入口，打开角色选择面板。</summary>
    private void AttachSoloCharacterButton(UnityEngine.Events.UnityAction onClick) {
        if (soloCharacterButton != null) return;
        soloCharacterButton = AttachSoloHeaderButton(soloDemoButton, "SoloCharacter", 150f, onClick);
    }

    /// <summary>
    /// 克隆一个现有导航按钮并插在其右侧：重命名、加宽、清掉被复制的 EventTrigger 条目与 onClick
    /// （运行时监听不会随 Instantiate 复制，但仍重置 onClick 以防源按钮带持久化调用）。
    /// EventTrigger.entries 会被序列化复制：若源按钮的悬停效果已注册，克隆体会双份，
    /// 清空后由克隆体自己的 HeaderButton.Start 重新注册。
    /// </summary>
    private HeaderButton AttachSoloHeaderButton(HeaderButton source, string buttonName, float width,
                                                UnityEngine.Events.UnityAction onClick) {
        if (source == null) return null;
        var go = Instantiate(source.gameObject, source.transform.parent);
        go.name = buttonName;
        go.transform.SetSiblingIndex(source.transform.GetSiblingIndex() + 1);
        var rect = go.GetComponent<RectTransform>();
        rect.sizeDelta = new Vector2(width, rect.sizeDelta.y);
        var cloned = go.GetComponent<HeaderButton>();
        if (cloned == null) {
            Destroy(go);
            return null;
        }
        var trigger = go.GetComponent<EventTrigger>();
        if (trigger != null) trigger.triggers.Clear();
        cloned.Button.onClick = new Button.ButtonClickedEvent();
        cloned.Button.onClick.AddListener(onClick);
        cloned.SetState(false, false, default);
        return cloned;
    }

    /// <summary>
    /// 控制「正在对局中」红色返回按钮的显示状态。游戏中允许玩家在主菜单浏览时随时回到对局。
    /// </summary>
    public void SetBackToGameVisible(bool visible) {
        if (backToGameButton == null) return;
        backToGameButton.Button.gameObject.SetActive(visible);
        if (visible) ApplyBackToGameTint();
    }

    private void ApplyBackToGameTint() {
        if (backToGameButton == null) return;
        backToGameButton.SetState(false, true, backToGameTintColor);
    }

    private void Update() {
        if (backToGameButton == null) return;
        if (!backToGameButton.Button.gameObject.activeInHierarchy) return;
        bool inGameWindow = _currentWindowName == "game";
        bool needFlash = !inGameWindow
            && NormalGameStateManager.Instance != null
            && NormalGameStateManager.Instance.IsGameActive
            && NormalGameStateManager.Instance.IsSelfActionRequired;
        if (needFlash) {
            float t = Mathf.PingPong(Time.unscaledTime / Mathf.Max(0.05f, turnFlashCycleSeconds * 0.5f), 1f);
            backToGameButton.SetState(false, true, Color.Lerp(turnFlashColorA, turnFlashColorB, t));
        } else {
            ApplyBackToGameTint();
        }
    }

    private void BackToGame() {
        SetBackToGameVisible(false);
        WindowsManager.Instance.SwitchWindow("game");
    }

    private void Menu() => WindowsManager.Instance.SwitchWindow("menu");
    private void Room() {
        if (!LobbyStateGuard.IsInRoom && LobbyStateGuard.BlockIfInMatchQueueForRoom()) {
            return;
        }
        WindowsManager.Instance.SwitchWindow("room");
    }
    private void Record() {
        if (ConfigManager.SoloMode) {
            LocalBattleRecordPanel.Show();
            return;
        }
        WindowsManager.Instance.SwitchWindow("record");
        RecordPanel.Instance.OpenAndReload();
    }
    private void PlayerData() {
        WindowsManager.Instance.SwitchWindow("player");
    }
    private void Config() => WindowsManager.Instance.SwitchWindow("config");
    private void AboutUs() => WindowsManager.Instance.SwitchWindow("aboutUs");
    private void Notice() => WindowsManager.Instance.SwitchWindow("notice");
    private void SceneConfig() => WindowsManager.Instance.SwitchWindow("sceneConfig");
    private void Spectator() => WindowsManager.Instance.SwitchWindow("spectator");
    private void Match() {
        // 对局/房间中允许查阅匹配页人数；真正加入队列时再由 MatchNetworkManager 拦截
        WindowsManager.Instance.SwitchWindow("match");
    }

    private void Event() {
        WindowsManager.Instance.SwitchWindow("event");
    }

    public void RefreshEventButtonVisibility() {
        if (ConfigManager.SoloMode || eventButton == null) return;
        eventButton.gameObject.SetActive(true);
    }

    /// <summary>
    /// 确保排位匹配导航入口可见；登录成功后由 NetworkManager 调用。
    /// </summary>
    public void RefreshMatchButtonVisibility() {
        if (ConfigManager.SoloMode || matchButton == null) return;
        matchButton.gameObject.SetActive(true);
    }
    private void Friend() {
        WindowsManager.Instance.SwitchWindow("friend");
        FriendNetworkManager.Instance.ListAllFriendPanels();
    }

    private void Logout() {
        NotificationManager.Instance.ShowMessage(
            "登出",
            "确定要登出当前账号吗？",
            "logout_confirm"
        );
    }

    /// <summary>
    /// 根据当前窗口更新导航栏按钮状态（由 WindowsManager.SwitchWindow 末尾调用）。Stay 颜色仅在此处设置。
    /// </summary>
    public void UpdateButtonState(string windowName) {
        _currentWindowName = windowName;
        RefreshButtonAppearance();
    }

    private void RefreshButtonAppearance() {
        bool inRoom = IsInRoom();
        menuButton?.SetState(_currentWindowName == "menu", false, default);
        roomButton?.SetState(_currentWindowName == "room", inRoom, playOnStayColor);
        recordButton?.SetState(_currentWindowName == "record", false, default);
        playerDataButton?.SetState(_currentWindowName == "player", false, default);
        configButton?.SetState(_currentWindowName == "config", false, default);
        aboutUsButton?.SetState(_currentWindowName == "aboutUs", false, default);
        noticeButton?.SetState(_currentWindowName == "notice", false, default);
        sceneConfigButton?.SetState(_currentWindowName == "sceneConfig", false, default);
        spectatorButton?.SetState(_currentWindowName == "spectator", false, default);
        matchButton?.SetState(_currentWindowName == "match", false, default);
        eventButton?.SetState(_currentWindowName == "event", false, default);
        friendButton?.SetState(_currentWindowName == "friend", false, default);
        logoutButton?.SetState(false, false, default);
        // 新手教程是弹层入口而非窗口页，永远保持常态外观
        soloTutorialButton?.SetState(false, false, default);
        soloDemoButton?.SetState(false, false, default);
        soloCharacterButton?.SetState(false, false, default);
    }

    private static bool IsInRoom() {
        return UserDataManager.Instance.RoomId != UserDataManager.ROOM_ID_NONE;
    }

    private void RefreshHeaderLabels() {
        SetButtonLabel(menuButton, HeaderNavItem.Menu);
        SetButtonLabel(roomButton, HeaderNavItem.Room);
        SetButtonLabel(friendButton, HeaderNavItem.Friend);
        SetButtonLabel(spectatorButton, HeaderNavItem.Spectator);
        SetButtonLabel(playerDataButton, HeaderNavItem.PlayerData);
        SetButtonLabel(recordButton, ConfigManager.SoloMode ? HeaderNavItem.SoloRecord : HeaderNavItem.Record);
        SetButtonLabel(soloTutorialButton, HeaderNavItem.SoloTutorial);
        SetButtonLabel(soloDemoButton, HeaderNavItem.SoloDemo);
        SetButtonLabel(soloCharacterButton, HeaderNavItem.SoloCharacter);
        SetButtonLabel(matchButton, HeaderNavItem.Match);
        SetButtonLabel(eventButton, HeaderNavItem.Event);
        SetButtonLabel(sceneConfigButton, HeaderNavItem.SceneConfig);
        SetButtonLabel(aboutUsButton, HeaderNavItem.AboutUs);
        SetButtonLabel(noticeButton, HeaderNavItem.Notice);
        SetButtonLabel(configButton, HeaderNavItem.Config);
        SetButtonLabel(backToGameButton, HeaderNavItem.BackToGame);
        SetButtonLabel(logoutButton, HeaderNavItem.Logout);
    }

    private static void SetButtonLabel(HeaderButton button, HeaderNavItem item) {
        if (button == null) {
            return;
        }
        button.SetLabel(AppLanguageTexts.GetHeaderNavLabel(item));
    }

    private void RefreshUnreadBadges() {
        noticeButton?.SetBadgeCount(UnreadBadgeStore.NoticeUnread);
        friendButton?.SetBadgeCount(UnreadBadgeStore.FriendUnread);
    }
}
