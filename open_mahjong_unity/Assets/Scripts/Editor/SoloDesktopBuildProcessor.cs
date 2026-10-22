using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>
/// 桌面单机版打包：构建结束后把本地服务运行时放到可执行文件同级的 SoloRuntime/，
/// 内容为随包分发的 Python（含标准库，用户无需安装）与 SoloPython 服务端源码。
///
/// 运行时需先放到 open_mahjong_unity/.cache/solo-python/：
/// - Windows：python-3.10.11-embed-amd64/（含 python.exe），CI 由 workflow 下载解压；
/// - Linux：cpython-3.10.14+20240726-x86_64-unknown-linux-gnu-install_only/（含 bin/python3）。
/// 本机构建可用菜单 Tools/单机版/打开嵌入式 Python 下载页 获取官方 Windows 包。
/// </summary>
public class SoloDesktopBuildProcessor : IPostprocessBuildWithReport {
    private const string PythonVersion = "3.10.11";
    private const string PythonEmbedUrl =
        "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip";
    private const string LinuxPythonFolder = "cpython-3.10.14+20240726-x86_64-unknown-linux-gnu-install_only";
    private const string RuntimeFolderName = "SoloRuntime";
    private const string PythonFolderName = "python";
    private const string PythonSourceFolderName = "SoloPython";

    public int callbackOrder => 0;

    public void OnPostprocessBuild(BuildReport report) {
        BuildTarget platform = report.summary.platform;
        if (platform != BuildTarget.StandaloneWindows64 && platform != BuildTarget.StandaloneLinux64) return;
        try {
            BundleRuntime(report.summary.outputPath, platform);
        } catch (Exception e) {
            Debug.LogError("[SoloDesktop] 打包本地运行时失败，构建中止: " + e.Message);
            throw;
        }
    }

    [MenuItem("Tools/单机版/打开嵌入式 Python 下载页")]
    private static void OpenPythonDownloadPage() {
        EditorUtility.RevealInFinder(WindowsPythonRuntimeDir);
        Application.OpenURL(PythonEmbedUrl);
    }

    private static string ProjectRoot => Directory.GetParent(Application.dataPath).FullName;

    private static string WindowsPythonRuntimeDir =>
        Path.Combine(ProjectRoot, ".cache", "solo-python",
                     "python-" + PythonVersion + "-embed-amd64");

    private static string LinuxPythonRuntimeDir =>
        Path.Combine(ProjectRoot, ".cache", "solo-python", LinuxPythonFolder);

    private static string PythonRuntimeDirFor(BuildTarget platform) =>
        platform == BuildTarget.StandaloneLinux64 ? LinuxPythonRuntimeDir : WindowsPythonRuntimeDir;

    /// <summary>校验运行时是否齐全：Windows 看 python.exe，Linux 看 bin/python3。</summary>
    private static bool HasPythonRuntime(string dir, BuildTarget platform) =>
        platform == BuildTarget.StandaloneLinux64
            ? File.Exists(Path.Combine(dir, "bin", "python3"))
            : File.Exists(Path.Combine(dir, "python.exe"));

    private static void BundleRuntime(string outputPath, BuildTarget platform) {
        string projectDir = ProjectRoot;
        string soloSrc = Path.Combine(projectDir, PythonSourceFolderName);
        if (!Directory.Exists(soloSrc)) {
            throw new Exception("缺少 Python 源码目录: " + soloSrc);
        }
        string pythonSrc = PythonRuntimeDirFor(platform);
        if (!HasPythonRuntime(pythonSrc, platform)) {
            throw new Exception(
                "缺少随包 Python 运行时，请先下载对应版本并解压到 " + pythonSrc
                + (platform == BuildTarget.StandaloneLinux64 ? "（需要 bin/python3）" : "（需要 python.exe）"));
        }

        string outputDir = Directory.Exists(outputPath) ? outputPath : Path.GetDirectoryName(outputPath);
        string runtimeDst = Path.Combine(outputDir, RuntimeFolderName);
        CopyDirectory(pythonSrc, Path.Combine(runtimeDst, PythonFolderName));
        CopyDirectory(soloSrc, Path.Combine(runtimeDst, PythonSourceFolderName));
        Debug.Log("[SoloDesktop] 本地运行时已打包到 " + runtimeDst);
    }

    private static void CopyDirectory(string source, string destination) {
        Directory.CreateDirectory(destination);
        foreach (string file in Directory.GetFiles(source)) {
            File.Copy(file, Path.Combine(destination, Path.GetFileName(file)), true);
        }
        foreach (string dir in Directory.GetDirectories(source)) {
            string name = Path.GetFileName(dir);
            if (name == "__pycache__") continue;
            CopyDirectory(dir, Path.Combine(destination, name));
        }
    }
}
