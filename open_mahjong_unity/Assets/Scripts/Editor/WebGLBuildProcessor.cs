#if UNITY_EDITOR
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>
/// WebGL 构建前置：固定 Brotli 压缩 + Decompression Fallback，保证产物可直接
/// 注入各平台包内 StreamingAssets/WebHost，由内嵌 SoloPython 服务在局域网托管。
///
/// 选 Decompression Fallback 的原因：内嵌服务端是纯标准库实现，直接回 .br 的
/// 原始字节、不带 Content-Encoding 头；该选项让 Unity 加载器内嵌 JS 解压逻辑，
/// 浏览器自行解开 .br，服务端无需任何压缩协商。
/// </summary>
public class WebGLBuildProcessor : IPreprocessBuildWithReport {
    public int callbackOrder => 0;

    public void OnPreprocessBuild(BuildReport report) {
        if (report.summary.platform != BuildTarget.WebGL) return;
        bool changed = false;
        if (PlayerSettings.webGLCompressionFormat != WebGLCompressionFormat.Brotli) {
            PlayerSettings.webGLCompressionFormat = WebGLCompressionFormat.Brotli;
            changed = true;
        }
        if (!PlayerSettings.webGLDecompressionFallback) {
            PlayerSettings.webGLDecompressionFallback = true;
            changed = true;
        }
        if (changed) {
            Debug.Log("[WebGLBuild] 已固定 Brotli 压缩 + Decompression Fallback（供内嵌服务免压缩头托管）");
        }
    }
}
#endif
