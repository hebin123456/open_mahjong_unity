using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public partial class CreatePanel {
    private GameObject XiaoshanStartingScorePanel;
    private GameObject XiaoshanTotalCapPanel;
    private TMP_Dropdown XiaoshanStartingScoreDropdown;
    private TMP_Dropdown XiaoshanTotalCapDropdown;

    private static readonly int[] XiaoshanStartingScores = { 50, 100, 200 };
    private static readonly int[] XiaoshanTotalCaps = { 32, 64 };

    /// <summary>运行时补齐萧山馆规选项（开局积分/倍率封顶）。
    /// 两个下拉框克隆通用模板后直接放进开关网格（ToggleContainer）作为网格单元，
    /// 与「有财神必须爆头」等开关同一布局、同一位置区域——
    /// 自建行方案（单独 LayoutElement 高度）在真实设备上无法稳定显示，弃用。</summary>
    private void EnsureXiaoshanScoreControls() {
        EnsureXiaoshanBaotouToggle();
        Transform grid = XiaoshanBaotouToggle != null ? XiaoshanBaotouToggle.transform.parent : null;
        XiaoshanStartingScorePanel = EnsureXiaoshanDropdown(XiaoshanStartingScorePanel, "XiaoshanStartingScore", "开局积分");
        XiaoshanTotalCapPanel = EnsureXiaoshanDropdown(XiaoshanTotalCapPanel, "XiaoshanTotalCap", "倍率封顶");
        foreach (GameObject panel in new[] { XiaoshanStartingScorePanel, XiaoshanTotalCapPanel }) {
            if (panel == null) continue;
            // 追加为网格最后两个单元（此时萧山开关已克隆完，恰好紧随其后）；
            // GridLayoutGroup 会强制把子物体摆成与开关相同的格子尺寸。
            if (grid != null && panel.transform.parent != grid) panel.transform.SetParent(grid, false);
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
        SetXiaoshanOptionsVisible(false);
    }

    private GameObject EnsureXiaoshanDropdown(GameObject existing, string name, string labelText) {
        if (existing != null || HepaiWayPanel == null) return existing;
        GameObject result = Instantiate(HepaiWayPanel, HepaiWayPanel.transform.parent);
        result.name = name;
        TMP_Text panelLabel = null;
        foreach (TMP_Text label in result.GetComponentsInChildren<TMP_Text>(true)) {
            if (label.GetComponentInParent<TMP_Dropdown>(true) != null) continue;
            panelLabel = label;
            label.text = labelText;
            break;
        }
        FitDropdownToGridCell(result, panelLabel);
        result.SetActive(false);
        return result;
    }

    /// <summary>
    /// 模板（HepaiConfig，462×64）里下拉是 stretch 且 sizeDelta.y=-40，放进开关网格
    /// （单元格 339×64）后只剩 24px 高，文字被裁掉。这里按单元格尺寸重排：
    /// 左侧标签占满行高，下拉占其余宽度并保留 60px 高（单元格 64 高，上下各留 2px）。
    /// </summary>
    private static void FitDropdownToGridCell(GameObject panel, TMP_Text panelLabel) {
        const float labelWidth = 120f;
        const float verticalPadding = 2f;

        RectTransform labelRect = panelLabel != null ? panelLabel.rectTransform : null;
        if (labelRect != null) {
            labelRect.anchorMin = new Vector2(0f, 0f);
            labelRect.anchorMax = new Vector2(0f, 1f);
            labelRect.pivot = new Vector2(0f, 0.5f);
            labelRect.anchoredPosition = Vector2.zero;
            labelRect.sizeDelta = new Vector2(labelWidth, 0f);
            panelLabel.enableAutoSizing = true;
            panelLabel.fontSizeMin = 16f;
            panelLabel.fontSizeMax = 30f;
        }

        TMP_Dropdown dropdown = panel.GetComponentInChildren<TMP_Dropdown>(true);
        if (dropdown == null) return;
        RectTransform dropdownRect = dropdown.GetComponent<RectTransform>();
        dropdownRect.anchorMin = new Vector2(0f, 0f);
        dropdownRect.anchorMax = new Vector2(1f, 1f);
        dropdownRect.pivot = new Vector2(0.5f, 0.5f);
        // 宽度 = 单元格宽 - 标签宽；右侧留 4px，居中对齐到标签右侧余量。
        dropdownRect.sizeDelta = new Vector2(-labelWidth - 4f, -verticalPadding * 2f);
        dropdownRect.anchoredPosition = new Vector2((labelWidth + 4f) * 0.5f, 0f);

        if (dropdown.captionText != null) {
            dropdown.captionText.enableAutoSizing = true;
            dropdown.captionText.fontSizeMin = 16f;
            dropdown.captionText.fontSizeMax = 30f;
        }
    }

    private void SetXiaoshanOptionsVisible(bool visible) {
        if (XiaoshanStartingScorePanel != null) XiaoshanStartingScorePanel.SetActive(visible);
        if (XiaoshanTotalCapPanel != null) XiaoshanTotalCapPanel.SetActive(visible);
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
