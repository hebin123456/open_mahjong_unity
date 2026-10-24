using UnityEngine;

/// <summary>
/// 折叠屏折/展、转屏会让 CanvasScaler（match=0.5）画布逻辑尺寸突变，中心锚 +
/// 固定偏移的 UI 会横推出画布（荣耀 Magic V5 展开态逻辑宽仅 ~1499，而场景摆位
/// 多按 1920 调）。这里把点锚 RectTransform 沿 X 平移钳回所在 Canvas：锚点与
/// anchoredPosition 模型不变，只在放不下时收进来，与 GameCanvas 座位面板钳制
/// 同思路。只处理横向——底部条带（SelfControl 等）本来就比画布矮，纵向越界是
/// 既有设计；拉伸锚元素会自适应，直接跳过。
/// </summary>
public static class UiRectClamp {
    public static void ClampIntoCanvas(RectTransform rt, float margin = 8f) {
        if (rt == null || !rt.gameObject.activeInHierarchy) return;
        if (rt.anchorMin != rt.anchorMax) return; // 拉伸锚随画布自适应，无需处理
        Canvas canvas = rt.GetComponentInParent<Canvas>();
        if (canvas == null) return;
        RectTransform canvasRt = canvas.transform as RectTransform;
        RectTransform parent = rt.parent as RectTransform;
        if (canvasRt == null || parent == null) return;
        Vector2 canvasSize = canvasRt.rect.size;
        Vector2 size = rt.rect.size;
        if (canvasSize.x <= 1f || size.x <= 0f || size.y <= 0f) return;

        Vector2 canvasLossy = canvasRt.lossyScale;
        if (Mathf.Abs(canvasLossy.x) < 1e-4f) return;
        // 元素宽度换算到画布坐标（UI 链无旋转，lossyScale 比值即可）
        float width = size.x * Mathf.Abs(rt.lossyScale.x / canvasLossy.x);
        // 画布 rect 以 pivot 为原点，先换算到 [0..w] 的 rect 坐标系再钳制
        Vector2 rectMin = -Vector2.Scale(canvasRt.pivot, canvasSize);
        Vector2 center = (Vector2)canvasRt.worldToLocalMatrix.MultiplyPoint3x4(rt.TransformPoint(rt.rect.center)) - rectMin;

        float extentsLeft = width * rt.pivot.x;
        float extentsRight = width - extentsLeft;
        float min = margin + extentsLeft;
        float max = canvasSize.x - margin - extentsRight;
        // 画布放不下时区间为空，取可用区间避免抖动
        float clampedX = Mathf.Clamp(center.x, Mathf.Min(min, max), Mathf.Max(min, max));
        float dx = clampedX - center.x;
        if (Mathf.Abs(dx) < 0.01f) return;

        // 画布坐标位移 → 世界位移 → 父本地位移，只改 anchoredPosition 偏移
        Vector3 worldDisp = canvasRt.TransformVector(new Vector3(dx, 0f, 0f));
        float parentDx = parent.worldToLocalMatrix.MultiplyVector(worldDisp).x;
        rt.anchoredPosition += new Vector2(parentDx, 0f);
    }

    /// <summary>
    /// 取 RectTransform 在所在 Canvas 本地坐标系（rect 坐标，原点在画布左下、
    /// y 向上）下的包围矩形。UI 层级链无旋转，尺寸用 lossyScale 比值换算、
    /// 中心用 world 矩阵换算，供重叠检测与定向让位使用。
    /// </summary>
    public static bool TryGetRectInCanvas(RectTransform rt, out Rect rect, out Vector2 canvasSize) {
        rect = default;
        canvasSize = Vector2.zero;
        if (rt == null) return false;
        Canvas canvas = rt.GetComponentInParent<Canvas>();
        if (canvas == null) return false;
        RectTransform canvasRt = canvas.transform as RectTransform;
        if (canvasRt == null) return false;
        canvasSize = canvasRt.rect.size;
        Vector2 canvasLossy = canvasRt.lossyScale;
        if (canvasSize.x <= 1f || Mathf.Abs(canvasLossy.x) < 1e-4f || Mathf.Abs(canvasLossy.y) < 1e-4f) return false;
        Rect local = rt.rect;
        if (local.width <= 0f || local.height <= 0f) return false;
        Vector2 size = new Vector2(
            local.width * Mathf.Abs(rt.lossyScale.x / canvasLossy.x),
            local.height * Mathf.Abs(rt.lossyScale.y / canvasLossy.y));
        Vector2 rectMin = -Vector2.Scale(canvasRt.pivot, canvasSize);
        Vector2 center = (Vector2)canvasRt.worldToLocalMatrix.MultiplyPoint3x4(rt.TransformPoint(local.center)) - rectMin;
        rect = new Rect(center - Vector2.Scale(size, rt.pivot), size);
        return true;
    }

    public static bool TryGetRectInCanvas(RectTransform rt, out Rect rect) {
        return TryGetRectInCanvas(rt, out rect, out _);
    }

    /// <summary>把画布坐标系下的位移换算回 anchoredPosition 增量（横纵向通用）。</summary>
    public static void MoveByCanvasDelta(RectTransform rt, Vector2 canvasDelta) {
        if (rt == null) return;
        if (Mathf.Abs(canvasDelta.x) < 0.01f && Mathf.Abs(canvasDelta.y) < 0.01f) return;
        Canvas canvas = rt.GetComponentInParent<Canvas>();
        if (canvas == null) return;
        RectTransform canvasRt = canvas.transform as RectTransform;
        RectTransform parent = rt.parent as RectTransform;
        if (canvasRt == null || parent == null) return;
        Vector3 worldDisp = canvasRt.TransformVector(new Vector3(canvasDelta.x, canvasDelta.y, 0f));
        rt.anchoredPosition += (Vector2)parent.worldToLocalMatrix.MultiplyVector(worldDisp);
    }

    /// <summary>
    /// 展开态下比画布还宽的容器（房间页玩家面板行 1554、底部按钮行 1781，
    /// 画布逻辑宽只有 ~1499）平移钳不住——两端怎么移都会溢。先等比缩小到
    /// 可用宽（保持纵横比，1920 基线不缩放零回归），再平移钳回画布。
    /// </summary>
    public static void FitIntoCanvas(RectTransform rt, float margin = 8f) {
        if (rt == null || !rt.gameObject.activeInHierarchy) return;
        if (rt.anchorMin != rt.anchorMax) return;
        Canvas canvas = rt.GetComponentInParent<Canvas>();
        if (canvas == null) return;
        RectTransform canvasRt = canvas.transform as RectTransform;
        if (canvasRt == null) return;
        Vector2 canvasSize = canvasRt.rect.size;
        Vector2 size = rt.rect.size;
        if (canvasSize.x <= 1f || size.x <= 0f || size.y <= 0f) return;

        float avail = canvasSize.x - margin * 2f;
        float scale = Mathf.Min(1f, avail / size.x);
        if (scale < rt.localScale.x - 0.001f || scale > rt.localScale.x + 0.001f) {
            rt.localScale = Vector3.one * scale;
        }
        ClampIntoCanvas(rt, margin);
    }
}
