#if UNITY_STANDALONE_WIN && !UNITY_EDITOR
#define USE_WINAPI
#endif

using System;
using System.Collections.Generic;
using System.Diagnostics;
using UnityEngine;
using System.Runtime.InteropServices;
using UnityEngine.UI;

/// <summary>
/// 固定宽高比窗口
/// </summary>
public class FixedAspectRatio : MonoBehaviour {
    public float targetAspectRatio = 16f / 9f; // 例如 1.777
    [SerializeField] private Camera[] targetCameras;

    private const string BlackBarCanvasName = "FixedAspectRatio Black Bars";
    /// <summary>最大化 letterbox 时需随相机视口裁剪的 Overlay 根 Canvas（还原后必须回到 Screen Space - Overlay）。</summary>
    private static readonly string[] LetterboxOverlayRootNames = { "OverlayCanvas", "MainCanvas", "#GameCanvas" };

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT {
        public int left, top, right, bottom;
    }

    [DllImport("user32.dll")]
    private static extern System.IntPtr GetActiveWindow();

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr hWnd, out RECT lpRect);

    [DllImport("user32.dll")]
    private static extern bool IsZoomed(IntPtr hWnd);

    private delegate IntPtr WndProcDelegate(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW", SetLastError = true)]
    private static extern IntPtr SetWindowLongPtr64(IntPtr hWnd, int nIndex, IntPtr dwNewLong);

    [DllImport("user32.dll", EntryPoint = "SetWindowLongW", SetLastError = true)]
    private static extern int SetWindowLong32(IntPtr hWnd, int nIndex, int dwNewLong);

    [DllImport("user32.dll")]
    private static extern IntPtr CallWindowProc(IntPtr lpPrevWndFunc, IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);

    private const int GWL_WNDPROC = -4;
    private const uint WM_SIZING = 0x0214;
    private const int WMSZ_LEFT = 1;
    private const int WMSZ_RIGHT = 2;
    private const int WMSZ_TOP = 3;
    private const int WMSZ_TOPLEFT = 4;
    private const int WMSZ_TOPRIGHT = 5;
    private const int WMSZ_BOTTOM = 6;
    private const int WMSZ_BOTTOMLEFT = 7;
    private const int WMSZ_BOTTOMRIGHT = 8;

    private IntPtr hwnd;
    private IntPtr originalWndProc;
    private WndProcDelegate wndProcDelegate;
    private RectTransform[] blackBars;
    private Canvas blackBarCanvas;
    private Rect currentCameraRect = new Rect(0f, 0f, 1f, 1f);
    private bool isLetterboxActive;
    private readonly Dictionary<Canvas, CanvasState> overlayCanvasStates = new Dictionary<Canvas, CanvasState>();
    private Canvas[] letterboxRootCanvases;
    // 视口比例放宽到比 16:9 更方时，按比例放大透视相机竖直 FOV，保持水平视野与 16:9 构图一致。
    private readonly Dictionary<Camera, float> cameraBaseFov = new Dictionary<Camera, float>();

    private struct CanvasState {
        public RenderMode renderMode;
        public Camera worldCamera;
        public float planeDistance;
        public int sortingLayerID;
        public int sortingOrder;
    }

    void Start() {
#if USE_WINAPI
        hwnd = GetUnityWindowHandle();
        HookWindowProc();
#endif
        SetupBlackBars();
        CacheLetterboxRootCanvases();
        ApplyCameraViewport();
    }

    private void CacheLetterboxRootCanvases() {
        var roots = new List<Canvas>(LetterboxOverlayRootNames.Length);
        for (int i = 0; i < LetterboxOverlayRootNames.Length; i++) {
            GameObject root = GameObject.Find(LetterboxOverlayRootNames[i]);
            if (root == null) continue;
            Canvas canvas = root.GetComponent<Canvas>();
            if (canvas != null && canvas != blackBarCanvas) {
                roots.Add(canvas);
            }
        }
        letterboxRootCanvases = roots.ToArray();
    }

    private void Update() {
        ApplyCameraViewport();
    }

    private void OnDestroy() {
#if USE_WINAPI
        UnhookWindowProc();
#endif
    }

    private void HookWindowProc() {
        wndProcDelegate = WindowProc;
        IntPtr newWndProc = Marshal.GetFunctionPointerForDelegate(wndProcDelegate);
        originalWndProc = SetWindowLongPtr(hwnd, GWL_WNDPROC, newWndProc);
    }

    private IntPtr GetUnityWindowHandle() {
        IntPtr mainWindowHandle = Process.GetCurrentProcess().MainWindowHandle;
        if (mainWindowHandle != IntPtr.Zero) {
            return mainWindowHandle;
        }

        return GetActiveWindow();
    }

    private void UnhookWindowProc() {
        if (originalWndProc != IntPtr.Zero) {
            SetWindowLongPtr(hwnd, GWL_WNDPROC, originalWndProc);
            originalWndProc = IntPtr.Zero;
        }
    }

    private IntPtr WindowProc(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam) {
        if (msg == WM_SIZING) {
            ApplyAspectRatioToSizingRect(wParam.ToInt32(), lParam);
            return new IntPtr(1);
        }

        return CallWindowProc(originalWndProc, hWnd, msg, wParam, lParam);
    }

    private void ApplyAspectRatioToSizingRect(int sizingEdge, IntPtr rectPointer) {
        RECT rect = Marshal.PtrToStructure<RECT>(rectPointer);
        int width = GetRectWidth(rect);
        int height = GetRectHeight(rect);
        Vector2Int nonClientSize = GetNonClientSize();
        int clientWidth = Mathf.Max(1, width - nonClientSize.x);
        int clientHeight = Mathf.Max(1, height - nonClientSize.y);
        int targetWindowWidth = Mathf.RoundToInt(clientHeight * targetAspectRatio) + nonClientSize.x;
        int targetWindowHeight = Mathf.RoundToInt(clientWidth / targetAspectRatio) + nonClientSize.y;

        switch (sizingEdge) {
            case WMSZ_LEFT:
            case WMSZ_RIGHT:
                ResizeHeightFromCenter(ref rect, targetWindowHeight);
                break;
            case WMSZ_TOP:
            case WMSZ_BOTTOM:
                ResizeWidthFromCenter(ref rect, targetWindowWidth);
                break;
            case WMSZ_TOPLEFT:
                if (clientWidth > Mathf.RoundToInt(clientHeight * targetAspectRatio)) {
                    rect.left = rect.right - targetWindowWidth;
                } else {
                    rect.top = rect.bottom - targetWindowHeight;
                }
                break;
            case WMSZ_TOPRIGHT:
                if (clientWidth > Mathf.RoundToInt(clientHeight * targetAspectRatio)) {
                    rect.right = rect.left + targetWindowWidth;
                } else {
                    rect.top = rect.bottom - targetWindowHeight;
                }
                break;
            case WMSZ_BOTTOMLEFT:
                if (clientWidth > Mathf.RoundToInt(clientHeight * targetAspectRatio)) {
                    rect.left = rect.right - targetWindowWidth;
                } else {
                    rect.bottom = rect.top + targetWindowHeight;
                }
                break;
            case WMSZ_BOTTOMRIGHT:
                if (clientWidth > Mathf.RoundToInt(clientHeight * targetAspectRatio)) {
                    rect.right = rect.left + targetWindowWidth;
                } else {
                    rect.bottom = rect.top + targetWindowHeight;
                }
                break;
        }

        Marshal.StructureToPtr(rect, rectPointer, false);
    }

    private Vector2Int GetNonClientSize() {
        GetWindowRect(hwnd, out RECT windowRect);
        GetClientRect(hwnd, out RECT clientRect);
        int nonClientWidth = GetRectWidth(windowRect) - GetRectWidth(clientRect);
        int nonClientHeight = GetRectHeight(windowRect) - GetRectHeight(clientRect);
        return new Vector2Int(nonClientWidth, nonClientHeight);
    }

    private void SetupBlackBars() {
        GameObject canvasObject = new GameObject(BlackBarCanvasName);
        canvasObject.transform.SetParent(transform, false);
        Canvas canvas = canvasObject.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.ScreenSpaceOverlay;
        canvas.sortingOrder = short.MaxValue;
        blackBarCanvas = canvas;

        blackBars = new RectTransform[4];
        for (int i = 0; i < blackBars.Length; i++) {
            GameObject barObject = new GameObject($"Black Bar {i}");
            barObject.transform.SetParent(canvasObject.transform, false);
            Image image = barObject.AddComponent<Image>();
            image.color = Color.black;
            image.raycastTarget = false;
            blackBars[i] = image.rectTransform;
        }
    }

    private void ApplyCameraViewport() {
        bool shouldLetterbox = ShouldLetterbox();
        float aspect = Screen.width / (float)Screen.height;
        Rect targetRect = shouldLetterbox ? GetTargetViewport(aspect, targetAspectRatio) : new Rect(0f, 0f, 1f, 1f);

        currentCameraRect = targetRect;
        Camera[] cameras = GetTargetCameras();
        for (int i = 0; i < cameras.Length; i++) {
            cameras[i].rect = currentCameraRect;
        }

        ApplyCameraFov(targetAspectRatio, cameras);
        ApplyCanvasViewport(shouldLetterbox);
        ApplyBlackBars(currentCameraRect);
    }

    private void ApplyCanvasViewport(bool shouldLetterbox) {
        if (shouldLetterbox) {
            ActivateLetterboxCanvasMode();
            return;
        }

        DeactivateLetterboxCanvasMode();
        EnsureOverlayRootsUseOverlayMode();
    }

    private static CanvasState CaptureCanvasState(Canvas canvas) {
        return new CanvasState {
            renderMode = canvas.renderMode,
            worldCamera = canvas.worldCamera,
            planeDistance = canvas.planeDistance,
            sortingLayerID = canvas.sortingLayerID,
            sortingOrder = canvas.sortingOrder,
        };
    }

    private static void ApplyCanvasState(Canvas canvas, CanvasState state) {
        canvas.renderMode = state.renderMode;
        canvas.worldCamera = state.worldCamera;
        canvas.planeDistance = state.planeDistance;
        canvas.sortingLayerID = state.sortingLayerID;
        canvas.sortingOrder = state.sortingOrder;
    }

    private void ActivateLetterboxCanvasMode() {
        Camera canvasCamera = GetCanvasCamera();
        Canvas[] canvases = FindObjectsByType<Canvas>();
        for (int i = 0; i < canvases.Length; i++) {
            Canvas canvas = canvases[i];
            if (canvas == null || canvas == blackBarCanvas) continue;
            if (canvas.GetComponent<LetterboxIgnoreCanvas>() != null) continue;
            if (canvas.renderMode != RenderMode.ScreenSpaceOverlay) continue;

            if (!overlayCanvasStates.ContainsKey(canvas)) {
                overlayCanvasStates.Add(canvas, CaptureCanvasState(canvas));
            }

            canvas.renderMode = RenderMode.ScreenSpaceCamera;
            canvas.worldCamera = canvasCamera;
        }

        isLetterboxActive = true;
    }

    private void DeactivateLetterboxCanvasMode() {
        if (!isLetterboxActive && overlayCanvasStates.Count == 0) return;

        foreach (KeyValuePair<Canvas, CanvasState> pair in overlayCanvasStates) {
            if (pair.Key == null) continue;
            ApplyCanvasState(pair.Key, pair.Value);
        }

        overlayCanvasStates.Clear();
        isLetterboxActive = false;
    }

    /// <summary>
    /// 窗口还原后若 Canvas 仍卡在 Screen Space - Camera，Main(Overlay) 会整层盖住 Overlay(Camera)。
    /// 每帧在非最大化状态下强制根 Canvas 回到 Overlay 模式，修复最大化→还原后的「混合态」。
    /// </summary>
    private void EnsureOverlayRootsUseOverlayMode() {
        if (letterboxRootCanvases == null || letterboxRootCanvases.Length == 0) {
            CacheLetterboxRootCanvases();
        }

        for (int i = 0; i < letterboxRootCanvases.Length; i++) {
            Canvas canvas = letterboxRootCanvases[i];
            if (canvas == null) {
                CacheLetterboxRootCanvases();
                return;
            }
            if (canvas.renderMode == RenderMode.ScreenSpaceOverlay) continue;

            if (overlayCanvasStates.TryGetValue(canvas, out CanvasState saved)) {
                ApplyCanvasState(canvas, saved);
                continue;
            }

            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.worldCamera = null;
        }
    }

    private void ApplyBlackBars(Rect targetRect) {
        SetBarAnchors(blackBars[0], new Vector2(0f, 0f), new Vector2(targetRect.xMin, 1f));
        SetBarAnchors(blackBars[1], new Vector2(targetRect.xMax, 0f), new Vector2(1f, 1f));
        SetBarAnchors(blackBars[2], new Vector2(targetRect.xMin, 0f), new Vector2(targetRect.xMax, targetRect.yMin));
        SetBarAnchors(blackBars[3], new Vector2(targetRect.xMin, targetRect.yMax), new Vector2(targetRect.xMax, 1f));
    }

    private void SetBarAnchors(RectTransform bar, Vector2 anchorMin, Vector2 anchorMax) {
        bar.anchorMin = anchorMin;
        bar.anchorMax = anchorMax;
        bar.offsetMin = Vector2.zero;
        bar.offsetMax = Vector2.zero;
    }

    private Camera[] GetTargetCameras() {
        if (targetCameras != null && targetCameras.Length > 0) {
            return targetCameras;
        }

        return Camera.allCameras;
    }

    private Camera GetCanvasCamera() {
        if (targetCameras != null && targetCameras.Length > 0) {
            return targetCameras[0];
        }

        return Camera.main;
    }

    private Rect GetTargetViewport(float screenAspectRatio, float targetAspect) {
        if (screenAspectRatio > targetAspect) {
            float width = targetAspect / screenAspectRatio;
            return new Rect((1f - width) * 0.5f, 0f, width, 1f);
        }

        float height = screenAspectRatio / targetAspect;
        return new Rect(0f, (1f - height) * 0.5f, 1f, height);
    }

    /// <summary>
    /// 相机实际视口比 16:9 设计目标窄时（折叠屏展开内屏 1.08、平板 4:3 等），
    /// 同一竖直 FOV 下水平视野变窄，牌桌左右两家被裁出屏。按
    /// targetAspectRatio/视口比例 放大竖直 FOV，把水平视野补回 16:9 设计宽度，
    /// 恢复完整牌桌全貌；视口比设计目标宽时（折叠态 21:10 横持）保持基准竖直
    /// FOV，两侧多出的是环境画面，牌桌尺寸与折叠态一致。
    /// 计算用相机实际视口比例（pixelRect），而不是 Screen 比例换算值：设备上报
    /// 尺寸偏差时旧写法可能直接跳过补偿，展开态左右两墩牌被裁出屏。
    /// </summary>
    private void ApplyCameraFov(float targetAspect, Camera[] cameras) {
        if (targetAspect <= 0f) return;
        for (int i = 0; i < cameras.Length; i++) {
            Camera camera = cameras[i];
            if (camera == null || camera.orthographic) continue;
            if (!cameraBaseFov.TryGetValue(camera, out float baseFov)) {
                baseFov = camera.fieldOfView;
                cameraBaseFov.Add(camera, baseFov);
            }
            float viewportAspect = camera.pixelHeight > 0
                ? camera.pixelWidth / (float)camera.pixelHeight
                : targetAspect;
            // 只在视口比设计目标窄时放大竖直 FOV，更宽的视口保持基准，牌桌不放大。
            float fovScale = Mathf.Max(1f, targetAspectRatio / Mathf.Max(0.01f, viewportAspect));
            if (fovScale > 1.0001f) {
                float tanHalf = Mathf.Tan(baseFov * 0.5f * Mathf.Deg2Rad) * fovScale;
                camera.fieldOfView = 2f * Mathf.Atan(tanHalf) * Mathf.Rad2Deg;
            } else {
                camera.fieldOfView = baseFov;
            }
        }
    }

    private int GetRectWidth(RECT rect) {
        return rect.right - rect.left;
    }

    private int GetRectHeight(RECT rect) {
        return rect.bottom - rect.top;
    }

    private bool ShouldLetterbox() {
#if USE_WINAPI
        // Snapping, fullscreen changes and restored window sizes can bypass
        // WM_SIZING. Keep both the board and overlay UI inside the same viewport.
        return IsZoomed(hwnd)
            || Mathf.Abs(Screen.width - Screen.height * targetAspectRatio) > 2f;
#elif UNITY_ANDROID || UNITY_IOS
        // 移动端不做 letterbox：URP 14 不支持 Camera.rect 视口裁剪，Overlay→Camera
        // 模式转换有排序风险；折叠态宽视口保持基准 FOV（零回归），展开态/平板等
        // 窄视口由 ApplyCameraFov 补齐水平视野，恢复牌桌全貌。
        return false;
#else
        return false;
#endif
    }

    private void ResizeHeightFromCenter(ref RECT rect, int targetHeight) {
        int centerY = (rect.top + rect.bottom) / 2;
        rect.top = centerY - targetHeight / 2;
        rect.bottom = rect.top + targetHeight;
    }

    private void ResizeWidthFromCenter(ref RECT rect, int targetWidth) {
        int centerX = (rect.left + rect.right) / 2;
        rect.left = centerX - targetWidth / 2;
        rect.right = rect.left + targetWidth;
    }

    private static IntPtr SetWindowLongPtr(IntPtr hWnd, int nIndex, IntPtr dwNewLong) {
        if (IntPtr.Size == 8) {
            return SetWindowLongPtr64(hWnd, nIndex, dwNewLong);
        }

        return new IntPtr(SetWindowLong32(hWnd, nIndex, dwNewLong.ToInt32()));
    }
}
