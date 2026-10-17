#if UNITY_EDITOR
using System;
using System.IO;
using System.Text;
using UnityEditor;
using UnityEditor.Android;
using UnityEngine;

/// <summary>
/// 单机版构建后处理：Unity 生成 Android Gradle 工程后，把内嵌的杭州麻将服务端
/// （Chaquopy + 纯 Python 引擎）注入进去，使规则/引擎/机器人随 APK 一起打包，
/// 运行时在 127.0.0.1 起本地 WebSocket 服务，实现离线单机对战。
///
/// 注入内容：
/// 1. 根 build.gradle：添加 Chaquopy Gradle 插件 classpath；
/// 2. launcher 模块 build.gradle：apply Chaquopy 插件 + 指定 Python 版本/ABI；
/// 3. launcher/src/main/python：复制项目根目录下 SoloPython 的全部 Python 源码；
/// 4. launcher/src/main/java/com/omj/solo/SoloBridge.java：供 C# 调用的启动桥接类。
/// </summary>
public class SoloChaquopyBuildProcessor : IPostGenerateGradleAndroidProject {
    // Chaquopy 版本：支持 Python 3.10–3.14、AGP 7.3–9.2、minSdk 24（Unity 6 生成的 AGP 8.x 在范围内）
    private const string ChaquopyVersion = "17.0.0";
    // 与服务器源码保持一致、且经语法校验兼容的 Python 版本（同时兼容 32/64 位 ABI）
    private const string PythonVersion = "3.11";
    // Unity 项目 AndroidTargetArchitectures = ARM64
    private const string PythonAbi = "arm64-v8a";
    private const string PythonSourceFolderName = "SoloPython";
    private const string BridgeRelativePath = "com/omj/solo/SoloBridge.java";

    public int callbackOrder => 0;

    public void OnPostGenerateGradleAndroidProject(string path) {
        if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android) {
            return;
        }
        try {
            Inject(path);
        } catch (Exception e) {
            Debug.LogError("[SoloChaquopy] 注入失败，构建中止: " + e);
            throw;
        }
    }

    private static void Inject(string basePath) {
        string projectRoot = FindProjectRoot(basePath);
        string appModule = FindApplicationModule(projectRoot, basePath);
        if (appModule == null) {
            throw new Exception("未找到 com.android.application 模块，projectRoot=" + projectRoot);
        }

        string projectDir = Directory.GetParent(Application.dataPath).FullName;
        string pythonSrc = Path.Combine(projectDir, PythonSourceFolderName);
        if (!Directory.Exists(pythonSrc)) {
            throw new Exception("缺少 Python 源码目录: " + pythonSrc);
        }

        string pythonDst = Path.Combine(appModule, "src", "main", "python");
        CopyDirectory(pythonSrc, pythonDst);
        Debug.Log("[SoloChaquopy] Python 源码已复制到 " + pythonDst);

        string javaDst = Path.Combine(appModule, "src", "main", "java",
            BridgeRelativePath.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(javaDst));
        File.WriteAllText(javaDst, BridgeJavaSource, new UTF8Encoding(false));
        Debug.Log("[SoloChaquopy] Java 桥接已写入 " + javaDst);

        PatchRootBuildGradle(Path.Combine(projectRoot, "build.gradle"));
        PatchModuleBuildGradle(Path.Combine(appModule, "build.gradle"));
        Debug.Log("[SoloChaquopy] Chaquopy 注入完成（app module = " + appModule + "）");
    }

    // 生成工程中 settings.gradle 所在目录即工程根目录（basePath 可能是根或某个模块）
    private static string FindProjectRoot(string basePath) {
        DirectoryInfo dir = new DirectoryInfo(basePath);
        for (int i = 0; i < 4 && dir != null; i++) {
            if (File.Exists(Path.Combine(dir.FullName, "settings.gradle"))) {
                return dir.FullName;
            }
            dir = dir.Parent;
        }
        return basePath;
    }

    private static string FindApplicationModule(string projectRoot, string basePath) {
        if (LooksLikeApplication(Path.Combine(basePath, "build.gradle"))) {
            return basePath;
        }
        foreach (string child in Directory.GetDirectories(projectRoot)) {
            if (LooksLikeApplication(Path.Combine(child, "build.gradle"))) {
                return child;
            }
        }
        return null;
    }

    private static bool LooksLikeApplication(string buildGradle) {
        return File.Exists(buildGradle)
            && File.ReadAllText(buildGradle).Contains("com.android.application");
    }

    private static void CopyDirectory(string source, string destination) {
        if (Directory.Exists(destination)) {
            Directory.Delete(destination, true);
        }
        Directory.CreateDirectory(destination);
        foreach (string dir in Directory.GetDirectories(source, "*", SearchOption.AllDirectories)) {
            Directory.CreateDirectory(dir.Replace(source, destination));
        }
        foreach (string file in Directory.GetFiles(source, "*", SearchOption.AllDirectories)) {
            if (file.Contains("__pycache__") || file.EndsWith(".pyc", StringComparison.OrdinalIgnoreCase)) {
                continue; // 跳过字节码缓存，源码会被 Chaquopy 现场编译
            }
            File.Copy(file, file.Replace(source, destination), true);
        }
    }

    private static void PatchRootBuildGradle(string file) {
        if (!File.Exists(file)) {
            throw new Exception("缺少根 build.gradle: " + file);
        }
        string text = File.ReadAllText(file);
        if (text.Contains("com.chaquo.python:gradle")) {
            return;
        }
        int buildscript = text.IndexOf("buildscript", StringComparison.Ordinal);
        int dependencies = buildscript >= 0
            ? text.IndexOf("dependencies", buildscript, StringComparison.Ordinal)
            : -1;
        if (dependencies < 0) {
            dependencies = text.IndexOf("dependencies", StringComparison.Ordinal);
        }
        if (dependencies < 0) {
            throw new Exception("根 build.gradle 缺少 dependencies 块: " + file);
        }
        int brace = text.IndexOf('{', dependencies);
        if (brace < 0) {
            throw new Exception("根 build.gradle dependencies 块格式异常: " + file);
        }
        string injection = "\n        classpath \"com.chaquo.python:gradle:" + ChaquopyVersion + "\"";
        File.WriteAllText(file, text.Insert(brace + 1, injection));
        Debug.Log("[SoloChaquopy] 已注入 Chaquopy Gradle 插件依赖 " + ChaquopyVersion);
    }

    private static void PatchModuleBuildGradle(string file) {
        if (!File.Exists(file)) {
            throw new Exception("缺少模块 build.gradle: " + file);
        }
        string text = File.ReadAllText(file);
        if (text.Contains("com.chaquo.python")) {
            return;
        }
        StringBuilder sb = new StringBuilder(text);
        sb.AppendLine();
        sb.AppendLine("// ---- injected by SoloChaquopyBuildProcessor (offline solo mode) ----");
        sb.AppendLine("apply plugin: 'com.chaquo.python'");
        sb.AppendLine("chaquopy {");
        sb.AppendLine("    defaultConfig {");
        sb.AppendLine("        version = \"" + PythonVersion + "\"");
        sb.AppendLine("    }");
        sb.AppendLine("}");
        sb.AppendLine("android {");
        sb.AppendLine("    defaultConfig {");
        sb.AppendLine("        ndk {");
        sb.AppendLine("            abiFilters \"" + PythonAbi + "\"");
        sb.AppendLine("        }");
        sb.AppendLine("    }");
        sb.AppendLine("}");
        File.WriteAllText(file, sb.ToString());
        Debug.Log("[SoloChaquopy] 已注入 Chaquopy 插件配置（Python " + PythonVersion + " / " + PythonAbi + "）");
    }

    // 桥接类：不接收参数，自行从 UnityPlayer 取 Context，避免 Unity 调用 Java 时的 JNI 签名不匹配问题。
    private const string BridgeJavaSource =
@"package com.omj.solo;

import android.content.Context;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

/**
 * 启动内嵌的杭州麻将 Python 服务（Chaquopy）。由 C# 通过 AndroidJavaClass 调用 start()。
 */
public final class SoloBridge {
    private static final String TAG = ""SoloBridge"";
    private static boolean started = false;

    /** 幂等启动；成功返回 true，失败返回 false 并记录日志，供调用方重试。 */
    public static synchronized boolean start() {
        if (started) {
            return true;
        }
        try {
            Context context = com.unity3d.player.UnityPlayer.currentActivity;
            if (context == null) {
                Log.e(TAG, ""currentActivity is null, retry later"");
                return false;
            }
            if (!Python.isStarted()) {
                Python.start(new AndroidPlatform(context));
            }
            PyObject module = Python.getInstance().getModule(""solo_server"");
            boolean ok = module.callAttr(""start_background"").toBoolean();
            started = true;
            Log.i(TAG, ""solo python server start_background -> "" + ok);
            return true;
        } catch (Throwable t) {
            Log.e(TAG, ""failed to start solo python server"", t);
            return false;
        }
    }
}
";
}
#endif
