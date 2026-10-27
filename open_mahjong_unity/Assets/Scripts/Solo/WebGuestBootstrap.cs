using UnityEngine;

/// <summary>
/// WebGL 访客引导：浏览器玩家通过房主机页面（http://主机IP:18763/）进入时，
/// 把连接地址从本机 127.0.0.1 改写为「页面加载来源」——页面从哪个 IP 加载，
/// WebSocket 就连 ws://该IP:18763，访客无需输入任何地址。
/// 房主端（Android/桌面）由 <see cref="WebInviteOverlay"/> 展示该地址与二维码。
///
/// Unity WebGL 下没有本地 Python 服务，NetworkManager 默认连 127.0.0.1:18763 会失败；
/// 本类在场景加载前（先于 NetworkManager.Start）用页面 URL 的 host:port 覆写
/// ConfigManager.gameUrl/chatUrl/webApiUrl，访客随后照常自动游客登录并加入房间。
/// </summary>
public static class WebGuestBootstrap {
    /// <summary>本次是否作为浏览器访客运行（页面来源非本机回环地址）。</summary>
    public static bool IsWebGuest { get; private set; }

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
    private static void Initialize() {
#if UNITY_WEBGL && !UNITY_EDITOR
        if (IsWebGuest) return;
        string url = Application.absoluteUrl;
        if (string.IsNullOrEmpty(url)) return;
        if (!TryParseOrigin(url, out string scheme, out string host, out int port)) return;
        // 用打包目录直接开 file:// 或本机起静态服务器调试时，保持默认的 127.0.0.1 服务地址。
        if (host == "127.0.0.1" || host == "localhost" || host == "[::1]") return;
        string wsScheme = scheme == "https" ? "wss" : "ws";
        ConfigManager.gameUrl = $"{wsScheme}://{host}:{port}/game";
        ConfigManager.chatUrl = $"{wsScheme}://{host}:{port}/chat";
        ConfigManager.webApiUrl = $"{scheme}://{host}:{port}";
        IsWebGuest = true;
        Debug.Log($"[WebGuest] 浏览器访客连接页面来源主机: {ConfigManager.gameUrl}");
#endif
    }

    /// <summary>解析 http(s)://host[:port]/... 的来源三元组；省略端口时按协议补默认值。</summary>
    private static bool TryParseOrigin(string url, out string scheme, out string host, out int port) {
        scheme = host = "";
        port = 0;
        int schemeEnd = url.IndexOf("://", System.StringComparison.Ordinal);
        if (schemeEnd <= 0) return false;
        scheme = url.Substring(0, schemeEnd).ToLowerInvariant();
        if (scheme != "http" && scheme != "https") return false;

        int hostStart = schemeEnd + 3;
        int pathStart = url.IndexOf('/', hostStart);
        string authority = pathStart < 0 ? url.Substring(hostStart) : url.Substring(hostStart, pathStart - hostStart);
        int query = authority.IndexOfAny(new[] { '?', '#' });
        if (query >= 0) authority = authority.Substring(0, query);
        int at = authority.LastIndexOf('@');
        if (at >= 0) authority = authority.Substring(at + 1); // 去掉可能的用户信息
        if (authority.Length == 0) return false;

        // "[::1]:18763" 这类 IPv6 字面量：冒号只可能出现在端口分隔处（方括号已包含在内）。
        int colon = authority.LastIndexOf(':');
        if (colon > authority.IndexOf(']')) {
            if (!int.TryParse(authority.Substring(colon + 1), out port)) return false;
            host = authority.Substring(0, colon);
        } else {
            host = authority;
            port = scheme == "https" ? 443 : 80;
        }
        return host.Length > 0 && port > 0;
    }
}
