using UnityEngine;

/// <summary>
/// 房间等待界面的折叠屏适配。展开态画布逻辑宽只有约 1662（16:9 letterbox
/// 收紧到 4:3 后由 CanvasScaler 算出），按 1920 摆的元素出屏：
///   - 4 个座位栏（351×4 + 50×3 = 1554.6）居中 -134：左缘出屏，右缘还会压住
///     右侧「规则」栏（RoomConfig 左缘约 1386）→ 放不下时整体等比缩小并重新
///     居中到规则栏左侧，留 20 间隙；
///   - 底部按钮行比画布宽、房间名与两个提示按固定偏移摆位 → 缩放/平移钳回。
/// 1920×1080（折叠态 letterbox 回 16:9）与更宽画布保持原摆位，零回归。
/// </summary>
public partial class RoomPanel {
    private const float SeatCanvasMargin = 48f;   // 座位区左侧留白
    private const float SeatConfigGap = 20f;      // 座位区与规则栏之间的间隙
    private const float SeatRightMargin = 40f;    // 无规则栏时座位区右侧留白
    private const float AdaptiveMargin = 8f;      // 通用画布留白

    private RectTransform adaptiveCanvas;
    private RectTransform adaptiveSeat;
    private RectTransform adaptiveConfig;
    private RectTransform adaptiveFooter;
    private Vector2 adaptiveLastCanvasSize;
    private Vector2 adaptiveSeatBasePosition;
    private float adaptiveSeatBaseWidth;
    private bool adaptiveReady;

    private void OnEnable() {
        // 每次进入房间页重新适配：画布尺寸可能在上次关闭后变了
        adaptiveLastCanvasSize = Vector2.zero;
        adaptiveReady = false;
        adaptiveCanvas = null;
        adaptiveSeat = null;
        adaptiveConfig = null;
        adaptiveFooter = null;
    }

    private void Update() {
        if (!EnsureAdaptiveSeats()) return;
        Vector2 size = adaptiveCanvas.rect.size;
        if (Mathf.Abs(size.x - adaptiveLastCanvasSize.x) < 0.5f
            && Mathf.Abs(size.y - adaptiveLastCanvasSize.y) < 0.5f) {
            return;
        }
        adaptiveLastCanvasSize = size;
        ApplyAdaptiveSeats(size.x);
        // 底部按钮行比画布宽时等比缩小；房间名与两个提示是点锚固定偏移，平移钳回
        UiRectClamp.FitIntoCanvas(adaptiveFooter, AdaptiveMargin);
        UiRectClamp.ClampIntoCanvas(transform.Find("RoomName") as RectTransform, AdaptiveMargin);
        UiRectClamp.ClampIntoCanvas(transform.Find("NoSpectators") as RectTransform, AdaptiveMargin);
        UiRectClamp.ClampIntoCanvas(transform.Find("NoRecord") as RectTransform, AdaptiveMargin);
    }

    private bool EnsureAdaptiveSeats() {
        if (adaptiveReady) return adaptiveSeat != null;
        Canvas canvas = GetComponentInParent<Canvas>();
        if (canvas == null) return false;
        adaptiveCanvas = canvas.transform as RectTransform;
        adaptiveSeat = transform.Find("GameObject") as RectTransform;
        adaptiveConfig = transform.Find("RoomConfig") as RectTransform;
        adaptiveFooter = transform.Find("GameObject (1)") as RectTransform;
        if (adaptiveCanvas == null || adaptiveSeat == null) return false;
        // 首帧记录原始摆位；HorizontalLayoutGroup 只排子元素，不改容器自身尺寸
        adaptiveSeatBasePosition = adaptiveSeat.anchoredPosition;
        adaptiveSeatBaseWidth = adaptiveSeat.rect.width;
        adaptiveReady = adaptiveSeatBaseWidth > 1f;
        return adaptiveReady;
    }

    private void ApplyAdaptiveSeats(float canvasWidth) {
        if (canvasWidth <= 1f || adaptiveSeatBaseWidth <= 1f) return;
        float rightLimit = canvasWidth - SeatRightMargin;
        if (adaptiveConfig != null && adaptiveConfig.gameObject.activeInHierarchy) {
            // 规则栏锚在画布右侧：左缘 = 锚点参照 + anchoredPosition - pivot * 宽
            float configLeft = adaptiveConfig.anchorMin.x * canvasWidth
                + adaptiveConfig.anchoredPosition.x
                - adaptiveConfig.rect.width * adaptiveConfig.pivot.x;
            rightLimit = Mathf.Min(rightLimit, configLeft - SeatConfigGap);
        }
        float usableWidth = rightLimit - SeatCanvasMargin;
        if (usableWidth >= adaptiveSeatBaseWidth) {
            // 放得下就还原场景摆位，折叠态与桌面布局完全不变
            adaptiveSeat.localScale = Vector3.one;
            adaptiveSeat.anchoredPosition = adaptiveSeatBasePosition;
            return;
        }
        if (usableWidth <= 1f) return;
        float scale = usableWidth / adaptiveSeatBaseWidth;
        adaptiveSeat.localScale = new Vector3(scale, scale, 1f);
        float centerX = SeatCanvasMargin + usableWidth * 0.5f;
        adaptiveSeat.anchoredPosition = new Vector2(
            centerX - canvasWidth * adaptiveSeat.anchorMin.x, adaptiveSeatBasePosition.y);
    }
}
