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

    // 展开态倒计时与按钮行/表情按钮的让位间隙
    private const float WidgetOverlapGap = 8f;

    // 宽屏比阈值与让位量：折叠横屏 letterbox 到 16:9 后逻辑画布 1920×1080
    // (1.78:1)，展开内屏 1662×1247 (1.33:1)。折叠/宽屏态顶家（对家）面板右缘
    // 会压住对家的吃碰副露区，往左让 40；左家让位一个头像宽度只在更宽的
    // 外屏任务里触发。
    private const float WideAspectThreshold = 2f;
    private const float LeftPanelWideShift = 110f;
    private const float FoldAspectThreshold = 1.6f;
    private const float TopPanelFoldShift = 40f;

    private Vector2 lastCanvasSize;
    private string adaptiveRoomRule;
    private string adaptiveSubRule;
    private RectTransform canvasRect;
    // 场景原始摆位：每次适配都从它出发，保证折/展循环里钳制与让位重复应用不叠加
    private readonly Dictionary<GamePlayerPanel, Vector2> seatOriginalPositions = new Dictionary<GamePlayerPanel, Vector2>();
    private RectTransform gamePanelRect;
    private bool lastGamePanelActive = true;

    private RectTransform CanvasRect {
        get {
            if (canvasRect == null) canvasRect = transform as RectTransform;
            return canvasRect;
        }
    }

    private bool TryGetGamePanelActive() {
        if (gamePanelRect == null) gamePanelRect = transform.Find("GamePanel") as RectTransform;
        return gamePanelRect != null && gamePanelRect.gameObject.activeInHierarchy;
    }

    private void Update() {
        RectTransform rt = CanvasRect;
        if (rt == null) return;
        Vector2 size = rt.rect.size;
        // 触发条件：画布尺寸变化，或 GamePanel 刚激活——UiRectClamp 会跳过
        // inactive 物体，展开态开机（画布尺寸此后不再变化）直接进对局时，
        // 若只在尺寸变化时钳制，对局内所有固定件一次都不会被钳。
        bool panelActive = TryGetGamePanelActive();
        if (Mathf.Abs(size.x - lastCanvasSize.x) < 0.5f
            && Mathf.Abs(size.y - lastCanvasSize.y) < 0.5f
            && panelActive == lastGamePanelActive) {
            return;
        }
        ApplyAdaptiveLayout(size);
        lastGamePanelActive = panelActive;
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
            // 倒计时：钳回画布后再对可见的吃碰杠按钮行做重叠让位（展开态两者都贴右缘）
            ResolveTimerPlacement();
            UiRectClamp.ClampIntoCanvas(selfControl.Find("SideBar") as RectTransform);
            UiRectClamp.ClampIntoCanvas(selfControl.Find("OpenStickerPanelButton") as RectTransform);
            UiRectClamp.ClampIntoCanvas(selfControl.Find("DingquePanel") as RectTransform);
            UiRectClamp.ClampIntoCanvas(selfControl.Find("RiichiPanel") as RectTransform);
        }
        // 吃碰杠按钮行与候选块容器平时隐藏/为空，画布变化的自适应时机覆盖不到
        // 激活瞬间；这里兜底转屏时已展开的情况，生成/展开时机另调同名方法。
        FitActionHostIntoCanvas(ActionButtonContainer);
        FitActionHostIntoCanvas(DedicatedActionButtonContainer);
        FitActionBlockContainerIntoCanvas();
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("StickerPanel") as RectTransform);
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("ShowSpectatorPlayer") as RectTransform);
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("AutoAction/SidebarFooter/ExpandNames/Triangle") as RectTransform);
        // 右上角的页面切换按钮组与退出按钮也是中心锚 + 固定偏移
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("GamePageControl") as RectTransform);
        UiRectClamp.ClampIntoCanvas(gameSceneUi.Find("#ExitButtonManager") as RectTransform);
    }

    // 场景原始摆位缓存：每次适配先复位再钳制/让位，折/展循环不叠加
    private readonly Dictionary<RectTransform, Vector2> widgetOriginalPositions = new Dictionary<RectTransform, Vector2>();

    private void ResetWidgetToOriginal(RectTransform rt) {
        if (rt == null) return;
        if (!widgetOriginalPositions.TryGetValue(rt, out Vector2 original)) {
            original = rt.anchoredPosition;
            widgetOriginalPositions[rt] = original;
        }
        rt.anchoredPosition = original;
    }

    /// <summary>
    /// 吃碰杠按钮行与候选块容器平时隐藏，画布变窄时的统一钳制覆盖不到激活
    /// 瞬间；在按钮生成与候选展开后调用：先复位场景摆位再钳回画布，保证
    /// 展开态下按钮行右缘（UpperRight 布局对齐）完整在屏内。折叠态画布更宽，
    /// 钳制不触发，零回归。
    /// </summary>
    internal void FitActionHostIntoCanvas(Transform host) {
        if (!(host is RectTransform rt)) return;
        ResetWidgetToOriginal(rt);
        UiRectClamp.ClampIntoCanvas(rt);
    }

    internal void FitActionBlockContainerIntoCanvas() {
        FitActionHostIntoCanvas(ActionBlockContenter);
    }

    /// <summary>
    /// 倒计时让位：展开态画布逻辑宽 ~1499，Timer（场景 ap 754.5 按 1920 调）
    /// 钳回屏内会整个压在吃碰杠按钮行上（按钮行同样贴画布右缘）。按钮行可见
    /// 且矩形相交时把 Timer 抬到行上方，再依次让开表情按钮与右侧座位面板；
    /// 按钮行隐藏时只做复位 + 钳制。
    /// </summary>
    internal void ResolveTimerPlacement() {
        if (FreeGameState.Active != null) return;
        RectTransform gameSceneUi = transform.Find("GamePanel/GameSceneUI") as RectTransform;
        if (gameSceneUi == null) return;
        RectTransform selfControl = gameSceneUi.Find("SelfControl") as RectTransform;
        RectTransform timer = selfControl != null ? selfControl.Find("Timer") as RectTransform : null;
        if (timer == null) return;
        ResetWidgetToOriginal(timer);
        UiRectClamp.ClampIntoCanvas(timer);

        RectTransform host = ActionButtonHost as RectTransform;
        if (host == null || !host.gameObject.activeInHierarchy) return;
        if (!UiRectClamp.TryGetRectInCanvas(timer, out Rect timerRect)) return;
        if (!UiRectClamp.TryGetRectInCanvas(host, out Rect hostRect)) return;
        if (!timerRect.Overlaps(hostRect)) return;

        // 抬到按钮行上方
        float dy = hostRect.yMax + WidgetOverlapGap - timerRect.yMin;
        UiRectClamp.MoveByCanvasDelta(timer, new Vector2(0f, dy));
        if (!UiRectClamp.TryGetRectInCanvas(timer, out timerRect)) return;

        // 让开右下的表情按钮（往左移，右缘退到表情按钮左侧）
        RectTransform sticker = selfControl.Find("OpenStickerPanelButton") as RectTransform;
        if (sticker != null && sticker.gameObject.activeInHierarchy
            && UiRectClamp.TryGetRectInCanvas(sticker, out Rect stickerRect)
            && timerRect.Overlaps(stickerRect)) {
            float dx = Mathf.Min(timerRect.xMax - (stickerRect.xMin - WidgetOverlapGap),
                                 timerRect.xMin - WidgetOverlapGap);
            if (dx > 0f) {
                UiRectClamp.MoveByCanvasDelta(timer, new Vector2(-dx, 0f));
                if (!UiRectClamp.TryGetRectInCanvas(timer, out timerRect)) return;
            }
        }

        // 竖屏折叠态右侧座位面板被钳到右缘中部，抬升后的倒计时可能压上，再左移让开
        RectTransform rightPanel = playerRightPanel != null ? playerRightPanel.transform as RectTransform : null;
        if (rightPanel != null && rightPanel.gameObject.activeInHierarchy
            && UiRectClamp.TryGetRectInCanvas(rightPanel, out Rect rightRect)
            && timerRect.Overlaps(rightRect)) {
            float dx = Mathf.Min(timerRect.xMax - (rightRect.xMin - WidgetOverlapGap),
                                 timerRect.xMin - WidgetOverlapGap);
            if (dx > 0f) UiRectClamp.MoveByCanvasDelta(timer, new Vector2(-dx, 0f));
        }
    }

    /// <summary>把四个座位面板钳制回画布内，并按形态做横向让位。</summary>
    private void ClampSeatPanelsIntoCanvas(Vector2 canvasSize) {
        if (canvasSize.x <= 1f || canvasSize.y <= 1f) return;
        float aspect = canvasSize.x / canvasSize.y;
        AdaptSeatPanel(playerSelfPanel, canvasSize, 0f);
        AdaptSeatPanel(playerLeftPanel, canvasSize,
            aspect >= WideAspectThreshold ? -LeftPanelWideShift : 0f);
        AdaptSeatPanel(playerTopPanel, canvasSize,
            aspect >= FoldAspectThreshold ? -TopPanelFoldShift : 0f);
        AdaptSeatPanel(playerRightPanel, canvasSize, 0f);
    }

    /// <summary>
    /// 单个座位面板适配：先复位到场景原始摆位（首次适配时记录），再钳回画布，
    /// 最后按需横向让位（负数左移）。从原始摆位出发保证折/展循环里位移不叠加。
    /// </summary>
    private void AdaptSeatPanel(GamePlayerPanel panel, Vector2 canvasSize, float xShift) {
        if (panel == null) return;
        RectTransform rt = panel.transform as RectTransform;
        if (rt == null || rt.anchorMin != rt.anchorMax) return; // 仅处理点锚座位面板
        if (!seatOriginalPositions.TryGetValue(panel, out Vector2 original)) {
            original = rt.anchoredPosition;
            seatOriginalPositions[panel] = original;
        }
        rt.anchoredPosition = original;
        ClampPanelIntoCanvas(panel, canvasSize);
        if (Mathf.Abs(xShift) > 0.01f) {
            rt.anchoredPosition += new Vector2(xShift, 0f);
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
