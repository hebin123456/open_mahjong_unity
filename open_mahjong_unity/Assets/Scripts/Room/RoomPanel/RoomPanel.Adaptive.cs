using UnityEngine;

public partial class RoomPanel {
    // ---------------------------------------------------------------------
    // 房间页横向自适应：折叠屏展开态（荣耀 Magic V5 内屏逻辑宽 ~1499，
    // CanvasScaler match=0.5）画布比场景摆位基线（1920）窄：
    //   - 玩家面板行「GameObject」（1554 宽）与底部按钮行「GameObject (1)」
    //     （1781 宽）比画布还宽，平移钳不住 → FitIntoCanvas 等比缩放；
    //     两个行内的按钮/面板由 HorizontalLayoutGroup 排布，缩放整体生效；
    //   - 房间名（RoomName）、无观战/无牌谱提示（NoSpectators/NoRecord）
    //     是点锚固定偏移，横推出屏 → 平移钳回。
    // 1920/2156 基线 scale=1、偏移不动，零回归。
    // ---------------------------------------------------------------------

    private const float AdaptiveMargin = 8f;

    private Vector2 lastCanvasSize;
    private RectTransform playerRow;  // GameObject：四个玩家面板行
    private RectTransform footerRow;  // GameObject (1)：离开/开始/准备按钮行

    private void OnEnable() {
        // 每次进入房间页重新适配一次（画布尺寸可能在上次关闭后变了）
        lastCanvasSize = Vector2.zero;
        playerRow = null;
        footerRow = null;
    }

    private void Update() {
        Canvas canvas = GetComponentInParent<Canvas>();
        if (canvas == null) return;
        RectTransform canvasRt = canvas.transform as RectTransform;
        if (canvasRt == null) return;
        Vector2 size = canvasRt.rect.size;
        if (Mathf.Abs(size.x - lastCanvasSize.x) < 0.5f
            && Mathf.Abs(size.y - lastCanvasSize.y) < 0.5f) {
            return;
        }
        lastCanvasSize = size;
        if (playerRow == null) playerRow = transform.Find("GameObject") as RectTransform;
        if (footerRow == null) footerRow = transform.Find("GameObject (1)") as RectTransform;
        UiRectClamp.FitIntoCanvas(playerRow, AdaptiveMargin);
        UiRectClamp.FitIntoCanvas(footerRow, AdaptiveMargin);
        UiRectClamp.ClampIntoCanvas(transform.Find("RoomName") as RectTransform, AdaptiveMargin);
        UiRectClamp.ClampIntoCanvas(transform.Find("NoSpectators") as RectTransform, AdaptiveMargin);
        UiRectClamp.ClampIntoCanvas(transform.Find("NoRecord") as RectTransform, AdaptiveMargin);
    }
}
