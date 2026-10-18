using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>
/// 桌面单机版打包：构建结束后把本地服务运行时放到 exe 同级的 SoloRuntime/，
/// 内容为随包分发的嵌入式 Python（含标准库，用户无需安装）与 SoloPython 服务端源码。
///
/// 嵌入式 Python 需先放到 <c>open_mahjong_unity/.cache/solo-python/python-3.10.11-embed-amd64/</c>：
/// CI 构建前由 workflow 下载解压；本机构建可用菜单 Tools/单机版/打开嵌入式 Python 下载页
/// 获取官方的 python-3.10.11-embed-amd64.zip，解压到该目录即可。
/// </summary>
public class SoloDesktopBuildProcessor : IPostprocessBuildWithReport {
    private const string PythonVersion = "3.10.11";
    private const string PythonEmbedUrl =
        "https://www.python.org/ftp/python/3.10.11/python-3.10.11-embed-amd64.zip";
    private const string RuntimeFolderName = "SoloRuntime";
    private const string PythonFolderName = "python";
    private const string PythonSourceFolderName = "SoloPython";

    public int callbackOrder => 0;

    public void OnPostprocessBuild(BuildReport report) {
        if (report.summary.platform != BuildTarget.StandaloneWindows64) return;
        try {
            BundleRuntime(report.summary.outputPath);
        } catch (Exception e) {
            Debug.LogError("[SoloDesktop] 打包本地运行时失败，构建中止: " + e.Message);
            throw;
        }
    }

    [MenuItem("Tools/单机版/打开嵌入式 Python 下载页")]
    private static void OpenPythonDownloadPage() {
        EditorUtility.RevealInFinder(PythonRuntimeDir);
        Application.OpenURL(PythonEmbedUrl);
    }

    private static string ProjectRoot => Directory.GetParent(Application.dataPath).FullName;

    private static string PythonRuntimeDir =>
        Path.Combine(ProjectRoot, ".cache", "solo-python",
                     "python-" + PythonVersion + "-embed-amd64");

    private static void BundleRuntime(string outputPath) {
        string projectDir = ProjectRoot;
        string soloSrc = Path.Combine(projectDir, PythonSourceFolderName);
        if (!Directory.Exists(soloSrc)) {
            throw new Exception("缺少 Python 源码目录: " + soloSrc);
        }
        string pythonSrc = PythonRuntimeDir;
        if (!File.Exists(Path.Combine(pythonSrc, "python.exe"))) {
            throw new Exception(
                "缺少嵌入式 Python 运行时，请先下载 " + PythonEmbedUrl
                + " 并解压到 " + pythonSrc);
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
