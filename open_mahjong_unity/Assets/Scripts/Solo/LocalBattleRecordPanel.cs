using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using TMPro;

/// <summary>
/// 单机版本地历史战绩面板：纯代码构建的 uGUI 浮层，展示 <see cref="LocalBattleRecordStore"/>
/// 落盘的对局记录（时间/规则/名次/四家分数明细）。顶栏「战绩」按钮（单机下替换牌谱入口）点击打开。
/// </summary>
public class LocalBattleRecordPanel : MonoBehaviour {
    public static LocalBattleRecordPanel Instance { get; private set; }

    private RectTransform rowsRoot;
    private TMP_Text emptyText;
    private TMP_Text summaryText;
    private Button clearButton;
    private TMP_Text clearButtonLabel;
    private bool clearArmed;           // 清空按钮二段确认：第一次点击进入待确认，3 秒内再点执行
    private float clearArmedUntil;

    private const float ReferenceHeight = 1080f;
    private static readonly Color RankGold = new Color(1.00f, 0.82f, 0.30f);
    private static readonly Color RankSilver = new Color(0.76f, 0.80f, 0.88f);
    private static readonly Color RankBronze = new Color(0.82f, 0.62f, 0.42f);
    private static readonly Color RankOther = new Color(0.55f, 0.59f, 0.66f);
    private static readonly Color RowBg = new Color(1f, 1f, 1f, 0.06f);
    private static readonly Color PanelBg = new Color(0.11f, 0.12f, 0.15f, 0.98f);
    private static readonly Color TextMain = new Color(0.92f, 0.93f, 0.95f);
    private static readonly Color TextSub = new Color(0.62f, 0.66f, 0.72f);
    private static readonly Color SelfHighlight = new Color(0.50f, 0.86f, 0.62f);

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Bootstrap() {
        if (!ConfigManager.SoloMode) return;
        Create();
    }

    private static void Create() {
        if (Instance != null) return;
        var go = new GameObject("LocalBattleRecordPanel", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
        DontDestroyOnLoad(go);
        var canvas = go.GetComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        canvas.sortingOrder = 900;
        var scaler = go.GetComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1920, ReferenceHeight);
        scaler.matchWidthOrHeight = 1f; // 按高度适配，竖屏/横屏都能铺满

        Instance = go.AddComponent<LocalBattleRecordPanel>();
        Instance.BuildLayout();
        go.SetActive(false);
    }

    public static void Show() {
        if (Instance == null) Create();
        Instance.gameObject.SetActive(true);
        Instance.clearArmed = false;
        Instance.Rebuild();
    }

    private void Update() {
        // 清空待确认超时后还原按钮文案
        if (clearArmed && Time.unscaledTime > clearArmedUntil) {
            clearArmed = false;
            if (clearButtonLabel != null) clearButtonLabel.text = "清空记录";
        }
    }

    // ---------- UI 构建 ----------

    private static RectTransform AddRect(Transform parent, string name) {
        var go = new GameObject(name, typeof(RectTransform));
        go.transform.SetParent(parent, false);
        return go.GetComponent<RectTransform>();
    }

    private static void Stretch(RectTransform rect, Vector2 offsetMin, Vector2 offsetMax) {
        rect.anchorMin = Vector2.zero;
        rect.anchorMax = Vector2.one;
        rect.offsetMin = offsetMin;
        rect.offsetMax = offsetMax;
    }

    private static TMP_Text AddText(Transform parent, string name, int fontSize, Color color, TextAlignmentOptions alignment) {
        var rect = AddRect(parent, name);
        var text = rect.gameObject.AddComponent<TextMeshProUGUI>();
        text.fontSize = fontSize;
        text.color = color;
        text.alignment = alignment;
        text.raycastTarget = false;
        return text;
    }

    private void BuildLayout() {
        float h = ReferenceHeight;

        // 全屏遮罩：点击关闭
        var mask = AddRect(transform, "Mask");
        Stretch(mask, Vector2.zero, Vector2.zero);
        var maskImage = mask.gameObject.AddComponent<Image>();
        maskImage.color = new Color(0f, 0f, 0f, 0.65f);
        var maskButton = mask.gameObject.AddComponent<Button>();
        maskButton.transition = Selectable.Transition.None;
        maskButton.onClick.AddListener(Hide);

        // 中央面板
        var panel = AddRect(mask, "Panel");
        panel.anchorMin = panel.anchorMax = new Vector2(0.5f, 0.5f);
        panel.pivot = new Vector2(0.5f, 0.5f);
        panel.sizeDelta = new Vector2(0.86f * 1920f, 0.82f * h);
        var panelImage = panel.gameObject.AddComponent<Image>();
        panelImage.color = PanelBg;

        // 标题
        var title = AddText(panel, "Title", Mathf.RoundToInt(h / 18f), TextMain, TextAlignmentOptions.MidlineLeft);
        var titleRect = title.rectTransform;
        titleRect.anchorMin = new Vector2(0f, 1f);
        titleRect.anchorMax = new Vector2(1f, 1f);
        titleRect.pivot = new Vector2(0f, 1f);
        titleRect.anchoredPosition = new Vector2(36f, -28f);
        titleRect.sizeDelta = new Vector2(-96f, 72f);
        title.text = "历史战绩";
        title.fontStyle = FontStyles.Bold;

        // 关闭按钮（右上 ×）
        var closeButton = CreatePanelButton(panel, "CloseButton", "×", Mathf.RoundToInt(h / 17f));
        var closeRect = closeButton.GetComponent<RectTransform>();
        closeRect.anchorMin = new Vector2(1f, 1f);
        closeRect.anchorMax = new Vector2(1f, 1f);
        closeRect.pivot = new Vector2(1f, 1f);
        closeRect.anchoredPosition = new Vector2(-20f, -20f);
        closeRect.sizeDelta = new Vector2(72f, 72f);
        closeButton.onClick.AddListener(Hide);

        // 滚动列表
        var scrollRect = BuildScroll(panel);
        Stretch(scrollRect.GetComponent<RectTransform>(), new Vector2(28f, 108f), new Vector2(-28f, -118f));

        // 底部：清空按钮 + 汇总文本
        var clear = CreatePanelButton(panel, "ClearButton", "清空记录", Mathf.RoundToInt(h / 32f));
        var clearRect = clear.GetComponent<RectTransform>();
        clearRect.anchorMin = new Vector2(0f, 0f);
        clearRect.anchorMax = new Vector2(0f, 0f);
        clearRect.pivot = new Vector2(0f, 0f);
        clearRect.anchoredPosition = new Vector2(28f, 22f);
        clearRect.sizeDelta = new Vector2(240f, 66f);
        clear.onClick.AddListener(OnClearClicked);
        clearButton = clear;
        clearButtonLabel = clear.GetComponentInChildren<TMP_Text>();

        summaryText = AddText(panel, "Summary", Mathf.RoundToInt(h / 34f), TextSub, TextAlignmentOptions.MidlineRight);
        var summaryRect = summaryText.rectTransform;
        summaryRect.anchorMin = new Vector2(1f, 0f);
        summaryRect.anchorMax = new Vector2(1f, 0f);
        summaryRect.pivot = new Vector2(1f, 0f);
        summaryRect.anchoredPosition = new Vector2(-28f, 22f);
        summaryRect.sizeDelta = new Vector2(560f, 66f);

        rowsRoot = scrollRect.content;
        // 空状态提示放在 viewport（content 之外），避免 Rebuild 清空行时被一并销毁
        emptyText = AddText(scrollRect.viewport, "Empty", Mathf.RoundToInt(h / 27f), TextSub, TextAlignmentOptions.Center);
        Stretch(emptyText.rectTransform, new Vector2(40f, 40f), new Vector2(-40f, -40f));
        emptyText.text = "还没有战绩\n打完一局（整局结束的总结算）就会记录在这里";
    }

    private static ScrollRect BuildScroll(RectTransform parent) {
        var scrollGo = new GameObject("Scroll", typeof(RectTransform), typeof(Image), typeof(ScrollRect));
        scrollGo.transform.SetParent(parent, false);
        var scrollRect = scrollGo.GetComponent<ScrollRect>();
        scrollGo.GetComponent<Image>().color = new Color(1f, 1f, 1f, 0.03f);

        var viewport = AddRect(scrollRect.transform, "Viewport");
        Stretch(viewport, Vector2.zero, Vector2.zero);
        viewport.gameObject.AddComponent<RectMask2D>();

        var content = AddRect(viewport, "Content");
        content.anchorMin = new Vector2(0f, 1f);
        content.anchorMax = new Vector2(1f, 1f);
        content.pivot = new Vector2(0.5f, 1f);

        var layout = content.gameObject.AddComponent<VerticalLayoutGroup>();
        layout.childAlignment = TextAnchor.UpperCenter;
        layout.childForceExpandHeight = false;
        layout.childForceExpandWidth = true;
        layout.childControlHeight = false;
        layout.childControlWidth = true;
        layout.spacing = 10f;
        layout.padding = new RectOffset(12, 12, 12, 12);
        content.gameObject.AddComponent<ContentSizeFitter>().verticalFit = ContentSizeFitter.FitMode.PreferredSize;

        scrollRect.content = content;
        scrollRect.viewport = viewport;
        scrollRect.horizontal = false;
        scrollRect.vertical = true;
        scrollRect.movementType = ScrollRect.MovementType.Clamped;
        scrollRect.scrollSensitivity = 36f;
        return scrollRect;
    }

    /// <summary>构建一个带半透明底色与文字的普通按钮。</summary>
    private static Button CreatePanelButton(RectTransform parent, string name, string label, int fontSize) {
        var rect = AddRect(parent, name);
        var image = rect.gameObject.AddComponent<Image>();
        image.color = new Color(1f, 1f, 1f, 0.10f);
        var button = rect.gameObject.AddComponent<Button>();
        var text = AddText(rect, "Label", fontSize, TextMain, TextAlignmentOptions.Center);
        Stretch(text.rectTransform, new Vector2(8f, 4f), new Vector2(-8f, -4f));
        text.text = label;
        return button;
    }

    // ---------- 列表刷新 ----------

    private void Rebuild() {
        var records = LocalBattleRecordStore.LoadAll();
        foreach (Transform child in rowsRoot) Destroy(child.gameObject);
        bool empty = records.Count == 0;
        if (emptyText != null) emptyText.gameObject.SetActive(empty);
        if (summaryText != null) summaryText.text = empty ? "" : $"共 {records.Count} 局";
        if (clearButton != null) clearButton.gameObject.SetActive(!empty);
        if (clearButtonLabel != null) clearButtonLabel.text = "清空记录";
        clearArmed = false;

        float h = ReferenceHeight;
        string selfName = PlayerSession.Current != null ? PlayerSession.Current.Username : null;
        int index = 0;
        foreach (var record in records) {
            BuildRow(record, Mathf.RoundToInt(h / 30f), Mathf.RoundToInt(h / 40f), selfName, index++ == 0);
        }
    }

    private void BuildRow(LocalBattleRecordStore.BattleRecord record, int fontSize, int subFontSize, string selfName, bool highlightTop) {
        float rowHeight = 176f;
        var row = AddRect(rowsRoot, "Row");
        row.sizeDelta = new Vector2(0f, rowHeight);
        var layout = row.gameObject.AddComponent<LayoutElement>();
        layout.preferredHeight = rowHeight;
        layout.minHeight = rowHeight;
        var bg = row.gameObject.AddComponent<Image>();
        bg.color = highlightTop ? new Color(1f, 1f, 1f, 0.09f) : RowBg;

        var rankColor = record.players.Count > 0
            ? RankColor(record.players[0].rank)
            : RankOther;

        // 名次（左）
        var rankText = AddText(row, "Rank", Mathf.RoundToInt(fontSize * 1.5f), rankColor, TextAlignmentOptions.MidlineLeft);
        var rankRect = rankText.rectTransform;
        rankRect.anchorMin = new Vector2(0f, 0f);
        rankRect.anchorMax = new Vector2(0f, 1f);
        rankRect.pivot = new Vector2(0f, 0.5f);
        rankRect.anchoredPosition = new Vector2(24f, 0f);
        rankRect.sizeDelta = new Vector2(200f, 0f);
        rankText.fontStyle = FontStyles.Bold;
        rankText.text = record.players.Count > 0 ? $"第{record.players[0].rank}名" : "-";

        // 右侧两行：规则/时间 + 分数（上），四家明细（下）
        var right = AddRect(row, "Right");
        right.anchorMin = new Vector2(0f, 0f);
        right.anchorMax = new Vector2(1f, 1f);
        right.offsetMin = new Vector2(248f, 12f);
        right.offsetMax = new Vector2(-24f, -12f);

        var headText = AddText(right, "Head", fontSize, TextMain, TextAlignmentOptions.MidlineLeft);
        var headRect = headText.rectTransform;
        headRect.anchorMin = new Vector2(0f, 0.5f);
        headRect.anchorMax = new Vector2(1f, 1f);
        headRect.offsetMin = Vector2.zero;
        headRect.offsetMax = Vector2.zero;
        var when = DateTimeOffset.FromUnixTimeSeconds(record.time).LocalDateTime.ToString("yyyy-MM-dd HH:mm");
        headText.text = $"{record.rule}  ·  {when}";
        if (record.players.Count > 0) {
            headText.text += $"    <color=#{ColorHex(rankColor)}>{record.players[0].score}</color>";
        }

        var detailText = AddText(right, "Detail", subFontSize, TextSub, TextAlignmentOptions.MidlineLeft);
        var detailRect = detailText.rectTransform;
        detailRect.anchorMin = new Vector2(0f, 0f);
        detailRect.anchorMax = new Vector2(1f, 0.5f);
        detailRect.offsetMin = Vector2.zero;
        detailRect.offsetMax = Vector2.zero;
        detailText.text = BuildDetail(record, selfName);
    }

    private static string BuildDetail(LocalBattleRecordStore.BattleRecord record, string selfName) {
        var parts = new List<string>();
        foreach (var p in record.players) {
            string name = string.IsNullOrEmpty(p.name) ? "?" : p.name;
            bool isSelf = !string.IsNullOrEmpty(selfName) && name == selfName;
            string shown = isSelf ? $"<color=#{ColorHex(SelfHighlight)}>{name}(我)</color>" : name;
            parts.Add($"{p.rank}.{shown} {p.score}");
        }
        return string.Join("    ", parts);
    }

    private void OnClearClicked() {
        if (!clearArmed) {
            clearArmed = true;
            clearArmedUntil = Time.unscaledTime + 3f;
            if (clearButtonLabel != null) clearButtonLabel.text = "再点一次确认清空";
            return;
        }
        LocalBattleRecordStore.ClearAll();
        Rebuild();
    }

    private void Hide() {
        gameObject.SetActive(false);
    }

    private static Color RankColor(int rank) {
        switch (rank) {
            case 1: return RankGold;
            case 2: return RankSilver;
            case 3: return RankBronze;
            default: return RankOther;
        }
    }

    private static string ColorHex(Color color) {
        return ColorUtility.ToHtmlStringRGB(color);
    }
}
