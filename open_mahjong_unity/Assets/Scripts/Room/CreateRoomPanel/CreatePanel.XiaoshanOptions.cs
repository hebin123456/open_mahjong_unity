using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public partial class CreatePanel {
    private GameObject XiaoshanScoreRow;
    private GameObject XiaoshanStartingScorePanel;
    private GameObject XiaoshanTotalCapPanel;
    private TMP_Dropdown XiaoshanStartingScoreDropdown;
    private TMP_Dropdown XiaoshanTotalCapDropdown;

    private static readonly int[] XiaoshanStartingScores = { 50, 100, 200 };
    private static readonly int[] XiaoshanTotalCaps = { 32, 64 };

    /// <summary>运行时补齐萧山馆规选项（开局积分/倍率封顶）；克隆通用下拉模板，不依赖场景改动。</summary>
    private void EnsureXiaoshanScoreControls() {
        EnsureXiaoshanBaotouToggle();
        if (XiaoshanScoreRow == null) {
            XiaoshanScoreRow = new GameObject("XiaoshanScoreRow",
                typeof(RectTransform), typeof(HorizontalLayoutGroup), typeof(LayoutElement));
            // 锚定到规则区大容器（开关网格行的父级，VerticalLayoutGroup）独立成行：
            // 开关行是 4 列自适应网格（每格约 339px），行容器塞进去会被压成一个窄格，
            // 和「有财神必须爆头」挤在同一排而无法辨认；独立成行则显示在开关排正下方。
            Transform gridRow = XiaoshanBaotouToggle != null
                ? XiaoshanBaotouToggle.transform.parent
                : HepaiWayPanel != null ? HepaiWayPanel.transform.parent : null;
            Transform anchor = gridRow != null && gridRow.parent != null ? gridRow.parent : null;
            if (anchor != null) XiaoshanScoreRow.transform.SetParent(anchor, false);
            HorizontalLayoutGroup layout = XiaoshanScoreRow.GetComponent<HorizontalLayoutGroup>();
            layout.spacing = 12f;
            layout.childControlWidth = layout.childControlHeight = true;
            layout.childForceExpandWidth = true;
            layout.childForceExpandHeight = false;
            XiaoshanScoreRow.GetComponent<LayoutElement>().minHeight = 64f;
            if (gridRow != null) {
                // 插到开关网格行之后（开局积分/倍率封顶显示在萧山开关排的下一行）。
                XiaoshanScoreRow.transform.SetSiblingIndex(gridRow.GetSiblingIndex() + 1);
            }
        }
        XiaoshanStartingScorePanel = EnsureXiaoshanDropdown(XiaoshanStartingScorePanel, "XiaoshanStartingScore", "开局积分");
        XiaoshanTotalCapPanel = EnsureXiaoshanDropdown(XiaoshanTotalCapPanel, "XiaoshanTotalCap", "倍率封顶");
        foreach (GameObject panel in new[] { XiaoshanStartingScorePanel, XiaoshanTotalCapPanel }) {
            if (panel == null) continue;
            panel.transform.SetParent(XiaoshanScoreRow.transform, false);
            LayoutElement element = panel.GetComponent<LayoutElement>();
            if (element == null) element = panel.AddComponent<LayoutElement>();
            element.minWidth = 100f;
            element.preferredWidth = 180f;
            element.flexibleWidth = 1f;
        }
        XiaoshanStartingScoreDropdown = XiaoshanStartingScorePanel != null
            ? XiaoshanStartingScorePanel.GetComponentInChildren<TMP_Dropdown>(true) : null;
        XiaoshanTotalCapDropdown = XiaoshanTotalCapPanel != null
            ? XiaoshanTotalCapPanel.GetComponentInChildren<TMP_Dropdown>(true) : null;
        if (XiaoshanStartingScoreDropdown != null) {
            // 克隆只复用样式，不继承模板其它玩法的回调。
            XiaoshanStartingScoreDropdown.onValueChanged = new TMP_Dropdown.DropdownEvent();
            XiaoshanStartingScoreDropdown.ClearOptions();
            var labels = new List<string>();
            foreach (int score in XiaoshanStartingScores) labels.Add(score + "分");
            XiaoshanStartingScoreDropdown.AddOptions(labels);
            XiaoshanStartingScoreDropdown.SetValueWithoutNotify(1); // 默认 100 分
            XiaoshanStartingScoreDropdown.RefreshShownValue();
        }
        if (XiaoshanTotalCapDropdown != null) {
            XiaoshanTotalCapDropdown.onValueChanged = new TMP_Dropdown.DropdownEvent();
            XiaoshanTotalCapDropdown.ClearOptions();
            var labels = new List<string>();
            foreach (int cap in XiaoshanTotalCaps) labels.Add(cap + "倍");
            XiaoshanTotalCapDropdown.AddOptions(labels);
            XiaoshanTotalCapDropdown.SetValueWithoutNotify(1); // 默认 64 倍
            XiaoshanTotalCapDropdown.RefreshShownValue();
        }
        if (XiaoshanScoreRow != null) XiaoshanScoreRow.SetActive(false);
    }

    private GameObject EnsureXiaoshanDropdown(GameObject existing, string name, string labelText) {
        if (existing != null || HepaiWayPanel == null) return existing;
        GameObject result = Instantiate(HepaiWayPanel, HepaiWayPanel.transform.parent);
        result.name = name;
        foreach (TMP_Text label in result.GetComponentsInChildren<TMP_Text>(true)) {
            if (label.GetComponentInParent<TMP_Dropdown>(true) != null) continue;
            label.text = labelText;
            break;
        }
        result.SetActive(false);
        return result;
    }

    private void SetXiaoshanOptionsVisible(bool visible) {
        if (XiaoshanScoreRow != null) XiaoshanScoreRow.SetActive(visible);
    }

    private int GetXiaoshanStartingScore() {
        return XiaoshanStartingScoreDropdown != null
            ? XiaoshanStartingScores[Mathf.Clamp(XiaoshanStartingScoreDropdown.value, 0, XiaoshanStartingScores.Length - 1)]
            : 100;
    }

    private int GetXiaoshanTotalCap() {
        return XiaoshanTotalCapDropdown != null
            ? XiaoshanTotalCaps[Mathf.Clamp(XiaoshanTotalCapDropdown.value, 0, XiaoshanTotalCaps.Length - 1)]
            : 64;
    }
}
