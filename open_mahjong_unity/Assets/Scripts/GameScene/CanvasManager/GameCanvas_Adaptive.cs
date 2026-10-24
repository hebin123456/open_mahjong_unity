using System.Collections.Generic;
using UnityEngine;

public partial class GameCanvas {
    // ---------------------------------------------------------------------
    // 自适应布局：折叠屏折/展、转屏都会中途改变画布逻辑尺寸（CanvasScaler
    // match=0.5），而手牌缩放与四个座位面板位置只在开局设置一次，之后永不
    // 重算——荣耀 Magic V5 上实测：折叠横屏(逻辑 2156×962) 左家面板飞出
    // 顶部（场景 y=1162 是在 ~1500×1384 的画布上调的），内屏展开后手牌
    // 右缘出屏（fitScale 冻结在旧分辨率）。这里在画布尺寸变化后统一适配：
    //   1. 座位面板钳制回画布内（保留原始摆位，只在放不下时往回收）；
    //   2. 折叠态宽屏比下左家面板再左移一个头像宽度，让开对家吃/碰副露区
    //      的屏幕投影（纵向钳到顶带后头像正好压在上面）；
    //   3. 手牌布局按当前画布重新计算（含拖拽安全带与右侧边距）。
    // ---------------------------------------------------------------------

    private const float SeatPanelMargin = 8f;

    // 宽屏比阈值与左家面板让位量：Magic V5 外屏折叠横屏逻辑 2156×962
    // (2.24:1)，展开内屏 ~1499×1384 (1.08:1)、16:9 桌面 1.78:1 都低于阈值。
    // 让位量 = 一个头像宽度（ProfileImageEdge 110）。
    private const float WideAspectThreshold = 2f;
    private const float LeftPanelWideShift = 110f;

    private Vector2 lastCanvasSize;
    private string adaptiveRoomRule;
    private string adaptiveSubRule;
    private RectTransform canvasRect;
    // 场景原始摆位：每次适配都从它出发，保证折/展循环里钳制与让位重复应用不叠加
    private readonly Dictionary<GamePlayerPanel, Vector2> seatOriginalPositions = new Dictionary<GamePlayerPanel, Vector2>();

    private RectTransform CanvasRect {
        get {
            if (canvasRect == null) canvasRect = transform as RectTransform;
            return canvasRect;
        }
    }

    private void Update() {
        RectTransform rt = CanvasRect;
        if (rt == null) return;
        Vector2 size = rt.rect.size;
        // 画布尺寸与上次适配时不同（含首帧从 0 变为实际值）即重新适配
        if (Mathf.Abs(size.x - lastCanvasSize.x) < 0.5f
            && Mathf.Abs(size.y - lastCanvasSize.y) < 0.5f) {
            return;
        }
        ApplyAdaptiveLayout(size);
    }

    private void ApplyAdaptiveLayout(Vector2 canvasSize) {
        lastCanvasSize = canvasSize;
        ClampSeatPanelsIntoCanvas(canvasSize);
        ClampFixedWidgetsIntoCanvas();
        // 手牌规则缓存自最近一次 ConfigureHandLayoutForRule（开局/读谱都会走）
        if (!string.IsNullOrEmpty(adaptiveRoomRule)) {
            ConfigureHandLayoutForRule(adaptiveRoomRule, adaptiveSubRule);
        }
    }

    /// <summary>
    /// 计时器、侧栏、表情按钮等都是中心锚 + 固定偏移，展开态画布变窄（逻辑宽
    /// ~1499，场景摆位按 1920 调）后会横推出屏；表情面板、观战栏、AutoAction
    /// 三角同理。统一钳回画布。注意 GameRecordUI/SideBar 与 SpectatorList 是
    /// 滑入抽屉，静止时本来就停在屏外，不能钳。
    /// </summary>
    private void ClampFixedWidgetsIntoCanvas() {
        RectTransform gameSceneUi = transform.Find("GamePanel/GameSceneUI") as RectTransform;
        if (gameSceneUi == null) return;
        RectTransform selfControl = gameSceneUi.Find("SelfControl") as RectTransform;
        if (selfControl != null) {
            UiRectClamp.ClampIntoCanvas(selfControl.Find("Timer") as RectTransform);
            UiRectClamp.ClampIntoCanvas(selfControl.Find("SideBar") as RectTransform);
            UiRectClamp.ClampIntoCanvas(selfControl.Find("OpenStickerPanelButton") as RectTransform);
        }
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("StickerPanel") as RectTransform);
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("ShowSpectatorPlayer") as RectTransform);
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("AutoAction/SidebarFooter/ExpandNames/Triangle") as RectTransform);
        // 右上角的页面切换按钮组与退出按钮也是中心锚 + 固定偏移
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("GamePageControl") as RectTransform);
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("#ExitButtonManager") as RectTransform);
    }

    /// <summary>把四个座位面板钳制回画布内：锚点与摆位不变，只在画布放不下时收进来。</summary>
    private void ClampSeatPanelsIntoCanvas(Vector2 canvasSize) {
        if (canvasSize.x <= 1f || canvasSize.y <= 1f) return;
        AdaptSeatPanel(playerSelfPanel, canvasSize, false);
        AdaptSeatPanel(playerLeftPanel, canvasSize, canvasSize.x / canvasSize.y >= WideAspectThreshold);
        AdaptSeatPanel(playerTopPanel, canvasSize, false);
        AdaptSeatPanel(playerRightPanel, canvasSize, false);
    }

    /// <summary>
    /// 单个座位面板适配：先复位到场景原始摆位（首次适配时记录），再钳回画布；
    /// 折叠态宽屏比下左家面板整体再左移一个头像宽度让开对家吃牌区。从原始摆
    /// 位出发保证折/展循环里位移不叠加。
    /// </summary>
    private void AdaptSeatPanel(GamePlayerPanel panel, Vector2 canvasSize, bool wideShiftLeft) {
        if (panel == null) return;
        RectTransform rt = panel.transform as RectTransform;
        if (rt == null || rt.anchorMin != rt.anchorMax) return; // 仅处理点锚座位面板
        if (!seatOriginalPositions.TryGetValue(panel, out Vector2 original)) {
            original = rt.anchoredPosition;
            seatOriginalPositions[panel] = original;
        }
        rt.anchoredPosition = original;
        ClampPanelIntoCanvas(panel, canvasSize);
        if (wideShiftLeft) {
            rt.anchoredPosition += new Vector2(-LeftPanelWideShift, 0f);
        }
    }

    private static void ClampPanelIntoCanvas(GamePlayerPanel panel, Vector2 canvasSize) {
        if (panel == null) return;
        RectTransform rt = panel.transform as RectTransform;
        if (rt == null || rt.anchorMin != rt.anchorMax) return; // 仅处理点锚座位面板
        Vector2 size = rt.rect.size;
        if (size.x <= 0f || size.y <= 0f) return;
        // 锚点参照点（画布坐标系）+ 锚定偏移 = 面板中心
        Vector2 anchorPoint = Vector2.Scale(rt.anchorMin, canvasSize);
        Vector2 center = anchorPoint + rt.anchoredPosition;
        // pivot 未必居中：按 pivot 算两侧伸出量
        Vector2 extentsMin = Vector2.Scale(size, rt.pivot);
        Vector2 extentsMax = size - extentsMin;
        Vector2 min = new Vector2(SeatPanelMargin + extentsMin.x, SeatPanelMargin + extentsMin.y);
        Vector2 max = new Vector2(canvasSize.x - SeatPanelMargin - extentsMax.x,
                                  canvasSize.y - SeatPanelMargin - extentsMax.y);
        // 极窄画布上区间可能为空，取中点避免抖动
        center.x = Mathf.Clamp(center.x, Mathf.Min(min.x, max.x), Mathf.Max(min.x, max.x));
        center.y = Mathf.Clamp(center.y, Mathf.Min(min.y, max.y), Mathf.Max(min.y, max.y));
        rt.anchoredPosition = center - anchorPoint;
    }
}
