using UnityEngine;

/// <summary>
/// 单机版角色选择：顶栏「角色」按钮打开，网格展示《大富翁》14 个角色头像。
/// 用 IMGUI 绘制，避免改场景；选中写入 <see cref="ConfigManager.PlayerCharacterId"/>，
/// 大厅头像走 ProfileImageId、对局内头像由 GameCanvas 覆写 profile_used 生效。
/// </summary>
public class CharacterPicker : MonoBehaviour {
    private const int Columns = 4;
    private const float CellGapRatio = 0.16f;

    private static CharacterPicker instance;

    private bool open;
    private bool appliedOnce;
    private GUIStyle bodyStyle;
    private GUIStyle titleStyle;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    private static void Create() {
        if (!ConfigManager.SoloMode) return;
        var go = new GameObject("CharacterPicker");
        DontDestroyOnLoad(go);
        instance = go.AddComponent<CharacterPicker>();
    }

    /// <summary>顶栏「角色」按钮点击时调用。</summary>
    public static void Open() {
        if (instance != null) instance.open = true;
    }

    private void Update() {
        // 登录后把本地角色套到当前用户（服务器默认头像是 1）
        if (appliedOnce) return;
        var udm = UserDataManager.Instance;
        if (udm == null || udm.UserId == 0) return;
        udm.ApplyLocalCharacter(ConfigManager.PlayerCharacterId);
        appliedOnce = true;
    }

    private void OnGUI() {
        if (!open) return;
        if (GameSessionGuard.HasExclusiveSession) {
            open = false;
            return;
        }
        EnsureStyles();
        DrawPanel();
    }

    private void EnsureStyles() {
        if (bodyStyle != null) return;
        int size = Mathf.Max(20, Screen.height / 38);
        bodyStyle = new GUIStyle(GUI.skin.label) { fontSize = size, alignment = TextAnchor.MiddleCenter };
        titleStyle = new GUIStyle(GUI.skin.label) { fontSize = Mathf.RoundToInt(size * 1.25f) };
    }

    private void DrawPanel() {
        var entries = CharacterCatalog.Entries;
        int rows = Mathf.CeilToInt(entries.Length / (float)Columns);
        float margin = bodyStyle.fontSize * 0.7f;
        float baseSize = bodyStyle.fontSize;

        float cellW = baseSize * 8.5f;
        float textH = baseSize * 2.2f;
        float cellH = cellW + textH;
        float headH = baseSize * 2.6f;
        float needH = margin + headH + rows * (cellH + margin) + margin;
        float maxH = Screen.height * 0.86f;
        if (needH > maxH) {
            float usable = maxH - margin * 2f - headH - rows * margin;
            cellW = Mathf.Max(baseSize * 3f, usable / rows - textH);
            cellH = cellW + textH;
            needH = margin + headH + rows * (cellH + margin) + margin;
        }

        float panelW = Columns * cellW + margin * (Columns + 1);
        float px = (Screen.width - panelW) * 0.5f;
        float py = (Screen.height - needH) * 0.5f;

        GUI.Box(new Rect(px, py, panelW, needH), GUIContent.none);
        GUI.Label(new Rect(px + margin, py + margin * 0.5f, panelW - margin * 2f, headH),
            "选择你的角色", titleStyle);
        if (GUI.Button(new Rect(px + panelW - margin - baseSize * 5f, py + margin * 0.5f,
                                baseSize * 5f, headH * 0.9f), "关闭", bodyStyle)) {
            open = false;
        }

        int current = ConfigManager.PlayerCharacterId;
        float gridTop = py + margin + headH;
        for (int i = 0; i < entries.Length; i++) {
            var entry = entries[i];
            int col = i % Columns;
            int row = i / Columns;
            var cell = new Rect(px + margin + col * (cellW + margin),
                                gridTop + row * (cellH + margin), cellW, cellH);
            bool selected = entry.Id == current;

            if (GUI.Button(new Rect(cell.x, cell.y, cell.width, cell.height - textH), GUIContent.none)) {
                ConfigManager.SetPlayerCharacter(entry.Id);
                current = entry.Id;
                selected = true;
            }
            if (selected) {
                var previous = GUI.color;
                GUI.color = new Color(0.45f, 0.85f, 0.55f, 1f);
                GUI.Box(new Rect(cell.x - 2f, cell.y - 2f, cell.width + 4f, cell.height - textH + 4f), GUIContent.none);
                GUI.color = previous;
            }

            Sprite sprite = CharacterCatalog.GetSprite(entry.Id);
            if (sprite != null && sprite.texture != null) {
                var art = new Rect(cell.x + 6f, cell.y + 6f,
                                   cell.width - 12f, cell.height - textH - 12f);
                GUI.DrawTexture(art, sprite.texture, ScaleMode.ScaleToFit, true);
            }

            GUI.Label(new Rect(cell.x, cell.y + cell.height - textH, cell.width, textH),
                (selected ? "✓ " : "") + entry.Name, bodyStyle);
        }
    }
}
