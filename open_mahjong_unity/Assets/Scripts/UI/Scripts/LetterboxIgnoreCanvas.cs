using UnityEngine;

/// <summary>挂了此标记的 Canvas 不参与 letterbox 的 Overlay→Camera 模式转换，
/// 始终保持 Screen Space - Overlay 渲染在所有相机输出之上（如战绩浮层等全屏模态）。</summary>
/// <remarks>
/// 此类必须独立成文件：Unity 解析场景 m_Script 引用（guid + fileID 11500000）时，
/// 绑定的是 .cs 文件里的第一个类。之前与 FixedAspectRatio 同文件时，KeepWindowsSize
/// 上的组件被绑到本空类，导致 FixedAspectRatio 的全部适配逻辑从未运行
/// （折叠屏展开态牌桌左右被裁出屏的根因）。
/// </remarks>
public sealed class LetterboxIgnoreCanvas : MonoBehaviour { }
