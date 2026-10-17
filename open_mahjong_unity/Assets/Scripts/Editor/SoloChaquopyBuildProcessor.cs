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
/// 1. 根 build.gradle：声明 Chaquopy Gradle 插件（Unity 6 用 plugins DSL，老工程回退 buildscript classpath）；
/// 2. launcher 模块 build.gradle：应用 Chaquopy 插件 + 指定 Python 版本/ABI；
/// 3. launcher/src/main/python：复制项目根目录下 SoloPython 的全部 Python 源码；
/// 4. launcher/src/main/java/com/omj/solo/SoloBridge.java：供 C# 调用的启动桥接类。
/// </summary>
public class SoloChaquopyBuildProcessor : IPostGenerateGradleAndroidProject {
    // Chaquopy 版本：17.1.0 起正式适配 Gradle 9/10，支持 Python 3.10–3.15、AGP 7.3–9.4
    private const string ChaquopyVersion = "17.1.0";
    // 与 unityci/editor 容器（Ubuntu 22.04）自带的 python3 版本保持一致，命中 Chaquopy 的 buildPython 自动探测
    private const string PythonVersion = "3.10";
    // Unity 项目 AndroidTargetArchitectures = ARM64
    private const string PythonAbi = "arm64-v8a";
    private const string PythonSourceFolderName = "SoloPython";
    private const string BridgeRelativePath = "com/omj/solo/SoloBridge.java";

    public int callbackOrder => 0;

    public void OnPostGenerateGradleAndroidProject(string path) {
        if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.Android) {
            return;
        }
        LogBuildEnvironment();
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

        PatchSettingsGradle(projectRoot);
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

    // ---------------------------------------------------------------------
    // settings.gradle：确保 pluginManagement 仓库包含 mavenCentral，
    // Chaquopy 的 Gradle 插件（plugin marker）发布在 Maven Central。
    // ---------------------------------------------------------------------
    private static void PatchSettingsGradle(string projectRoot) {
        string file = Path.Combine(projectRoot, "settings.gradle");
        if (!File.Exists(file)) {
            return;
        }
        string text = File.ReadAllText(file);
        Debug.Log("[SoloChaquopy] settings.gradle 原文:\n" + text);

        if (text.Contains("com.chaquo.python")) {
            return;
        }
        int pm = text.IndexOf("pluginManagement", StringComparison.Ordinal);
        if (pm < 0) {
            string block = "pluginManagement {\n"
                + "    repositories {\n"
                + "        google()\n"
                + "        mavenCentral()\n"
                + "        gradlePluginPortal()\n"
                + "    }\n"
                + "}\n\n";
            File.WriteAllText(file, block + text);
            Debug.Log("[SoloChaquopy] settings.gradle 已补充 pluginManagement 仓库");
            return;
        }
        int window = Math.Min(text.Length - pm, 1000);
        if (text.Substring(pm, window).Contains("mavenCentral()")) {
            return; // 已具备，无需改动
        }
        int repos = text.IndexOf("repositories", pm, StringComparison.Ordinal);
        int brace = repos >= 0 ? text.IndexOf('{', repos) : -1;
        if (brace < 0) {
            throw new Exception("settings.gradle pluginManagement 缺少 repositories 块: " + file);
        }
        File.WriteAllText(file, text.Insert(brace + 1, "\n        mavenCentral()"));
        Debug.Log("[SoloChaquopy] settings.gradle pluginManagement 已补充 mavenCentral()");
    }

    // ---------------------------------------------------------------------
    // 根 build.gradle：声明 Chaquopy 插件。Unity 6 生成的是 plugins DSL
    // （没有 buildscript/dependencies），因此优先注入根 plugins 块；
    // 若为老式工程则回退到 buildscript classpath。
    // ---------------------------------------------------------------------
    private static void PatchRootBuildGradle(string file) {
        if (!File.Exists(file)) {
            throw new Exception("缺少根 build.gradle: " + file);
        }
        string text = File.ReadAllText(file);
        Debug.Log("[SoloChaquopy] 根 build.gradle 原文:\n" + text);

        if (text.Contains("com.chaquo.python")) {
            return;
        }

        // 首选：plugins DSL（id + version，apply false，供各子模块 apply）
        if (TryInjectIntoPluginsBlock(file, text,
                "    id \"com.chaquo.python\" version \"" + ChaquopyVersion + "\" apply false")) {
            Debug.Log("[SoloChaquopy] 已在根 plugins 块声明 Chaquopy " + ChaquopyVersion);
            return;
        }

        string classpath = "classpath \"com.chaquo.python:gradle:" + ChaquopyVersion + "\"";
        int bs = text.IndexOf("buildscript", StringComparison.Ordinal);
        if (bs < 0) {
            string block = "buildscript {\n"
                + "    repositories {\n"
                + "        google()\n"
                + "        mavenCentral()\n"
                + "    }\n"
                + "    dependencies {\n"
                + "        " + classpath + "\n"
                + "    }\n"
                + "}\n\n";
            File.WriteAllText(file, block + text);
            Debug.Log("[SoloChaquopy] 已在根 build.gradle 头部注入 buildscript classpath");
            return;
        }

        int deps = text.IndexOf("dependencies", bs, StringComparison.Ordinal);
        int brace = deps >= 0 ? text.IndexOf('{', deps) : -1;
        if (brace > 0) {
            File.WriteAllText(file, text.Insert(brace + 1, "\n        " + classpath));
            Debug.Log("[SoloChaquopy] 已向已有 buildscript.dependencies 注入 Chaquopy classpath");
            return;
        }
        int bsBrace = text.IndexOf('{', bs);
        if (bsBrace > 0) {
            File.WriteAllText(file, text.Insert(bsBrace + 1,
                "\n    dependencies {\n        " + classpath + "\n    }"));
            Debug.Log("[SoloChaquopy] 已向已有 buildscript 注入 dependencies 块");
            return;
        }
        throw new Exception("根 build.gradle 的 buildscript 块格式异常: " + file);
    }

    /// <summary>把一行声明插入到最外层 plugins { } 块中；找不到该块时返回 false。</summary>
    private static bool TryInjectIntoPluginsBlock(string file, string text, string line) {
        int idx = 0;
        while ((idx = text.IndexOf("plugins", idx, StringComparison.Ordinal)) >= 0) {
            int brace = text.IndexOf('{', idx);
            if (brace < 0) {
                return false;
            }
            string between = text.Substring(idx + "plugins".Length, brace - idx - "plugins".Length);
            if (between.Trim().Length == 0) {
                File.WriteAllText(file, text.Insert(brace + 1, "\n" + line));
                return true;
            }
            idx += "plugins".Length;
        }
        return false;
    }

    // ---------------------------------------------------------------------
    // 模块 build.gradle：应用 Chaquopy 插件并配置 Python 版本 / ABI。
    // ---------------------------------------------------------------------
    private static void PatchModuleBuildGradle(string file) {
        if (!File.Exists(file)) {
            throw new Exception("缺少模块 build.gradle: " + file);
        }
        string text = File.ReadAllText(file);
        Debug.Log("[SoloChaquopy] 模块 build.gradle 原文:\n" + text);
        if (text.Contains("com.chaquo.python")) {
            return;
        }

        if (!TryInjectIntoPluginsBlock(file, text, "    id \"" + ChaquopyPluginId + "\"")) {
            File.AppendAllText(file, "\napply plugin: '" + ChaquopyPluginId + "'\n");
            text = File.ReadAllText(file);
        }

        StringBuilder sb = new StringBuilder(File.ReadAllText(file));
        sb.AppendLine();
        sb.AppendLine("// ---- injected by SoloChaquopyBuildProcessor (offline solo mode) ----");
        sb.AppendLine("chaquopy {");
        sb.AppendLine("    defaultConfig {");
        sb.AppendLine("        version = \"" + PythonVersion + "\"");
        sb.AppendLine("        pyc {");
        sb.AppendLine("            // 构建机 Python 版本不确定，关闭字节码编译以避免依赖 buildPython；");
        sb.AppendLine("            // 代价是首次运行时在设备上编译，启动稍慢。");
        sb.AppendLine("            src = false");
        sb.AppendLine("            pip = false");
        sb.AppendLine("            stdlib = false");
        sb.AppendLine("        }");
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

    private const string ChaquopyPluginId = "com.chaquo.python";

    // 诊断用：记录 Unity 进程所在环境（即 CI 容器）里的 Python 解释器情况。
    private static void LogBuildEnvironment() {
        try {
            var psi = new System.Diagnostics.ProcessStartInfo {
                FileName = "/bin/bash",
                Arguments = "-lc \"for c in python3 python3.10 python3.11 python3.12 python; do "
                          + "printf '%s -> ' \\\"$c\\\"; command -v $c || echo '(not found)'; done; "
                          + "python3 -V 2>&1 || true\"",
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
            };
            using (var p = System.Diagnostics.Process.Start(psi)) {
                string output = p.StandardOutput.ReadToEnd() + p.StandardError.ReadToEnd();
                p.WaitForExit(5000);
                Debug.Log("[SoloChaquopy] 构建机 Python 探测:\n" + output);
            }
        } catch (Exception e) {
            Debug.LogWarning("[SoloChaquopy] 构建机 Python 探测失败: " + e.Message);
        }
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
