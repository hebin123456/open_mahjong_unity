using System;
using System.Linq;
using System.Text;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

/// <summary>公开杭州状态；不显示任何他家私密手牌或听牌提示。</summary>
public sealed class HangzhouStatePanel : MonoBehaviour {
    private static HangzhouStatePanel instance;
    private TMP_Text caption, body;
    private HangzhouInfo info;
    private bool expanded;
    public static void Show(HangzhouInfo value) {
        if (value == null || GameCanvas.Instance == null) { Hide(); return; }
        if (instance == null) {
            var root=new GameObject("HangzhouState",typeof(RectTransform),typeof(Image),typeof(Canvas),typeof(GraphicRaycaster),typeof(HangzhouStatePanel));
            root.transform.SetParent(GameCanvas.Instance.transform,false); root.layer=GameCanvas.Instance.gameObject.layer;
            // Share the table UI's ordering so the existing result content
            // naturally covers this HUD, and hiding that content reveals it.
            Transform presentation=RoundEndPresentation.Instance?.transform;
            while (presentation!=null && presentation.parent!=root.transform.parent) presentation=presentation.parent;
            if (presentation!=null) root.transform.SetSiblingIndex(presentation.GetSiblingIndex());
            else root.transform.SetAsFirstSibling();
            root.GetComponent<Canvas>().overrideSorting=false;
            instance=root.GetComponent<HangzhouStatePanel>(); instance.Build();
        }
        if (instance.info?.hand_number != value.hand_number) instance.expanded=false;
        instance.info=value; instance.gameObject.SetActive(true); instance.Refresh();
    }
    public static void Hide() { if (instance != null) { instance.gameObject.SetActive(false); instance.info=null; } }
    /// <summary>财神展示：杭州恒为白板；萧山/义乌翻出的牌即财神；绍兴为翻牌加一；台州白板仅顺子顶替。</summary>
    private static string JokerLabel(HangzhouInfo value) {
        string version = value?.rule_version ?? "";
        int indicator = value?.joker_indicator ?? 0;
        int joker = value?.joker_tiles != null && value.joker_tiles.Length > 0 ? value.joker_tiles[0] : indicator;
        if (version.StartsWith("shaoxing")) return $"{TileName(joker)}（翻牌加一）";
        if (version.StartsWith("taizhou")) return indicator == 46 ? "白板（得）" : $"{TileName(indicator)}（白板仅顺子顶替）";
        if (version.StartsWith("yiwu")) return indicator == 46 ? "白板（财神）" : $"{TileName(indicator)}（白板顶替）";
        if (version.StartsWith("xiaoshan")) return indicator == 46 ? "白板" : $"{TileName(indicator)}（白板顶替）";
        return "白板";
    }
    private static bool IsShaoxing(HangzhouInfo value) => (value?.rule_version ?? "").StartsWith("shaoxing");
    private static bool IsTaizhou(HangzhouInfo value) => (value?.rule_version ?? "").StartsWith("taizhou");
    private static bool IsYiwu(HangzhouInfo value) => (value?.rule_version ?? "").StartsWith("yiwu");
    private static string TileName(int tile) {
        if (tile >= 11 && tile <= 19) return $"{tile % 10}万";
        if (tile >= 21 && tile <= 29) return $"{tile % 10}饼";
        if (tile >= 31 && tile <= 39) return $"{tile % 10}条";
        switch (tile) {
            case 41: return "东"; case 42: return "南"; case 43: return "西"; case 44: return "北";
            case 45: return "中"; case 46: return "白"; case 47: return "发";
            case 51: return "春"; case 52: return "夏"; case 53: return "秋"; case 54: return "冬";
            case 55: return "梅"; case 56: return "兰"; case 57: return "竹"; case 58: return "菊";
            default: return "未翻财";
        }
    }
    private static string CaptionText(HangzhouInfo value) {
        if (IsShaoxing(value)) return $"绍兴花麻 · 财神 {JokerLabel(value)} · 牌墙{Math.Max(0, value?.wall_count ?? 0)}张";
        if (IsTaizhou(value)) return $"台州黄岩 · 得 {JokerLabel(value)} · 留{Math.Max(0, (value?.dead_wall_count ?? 0) / 2)}墩";
        if (IsYiwu(value)) return $"义乌麻将 · 财神 {JokerLabel(value)} · 留{Math.Max(0, (value?.dead_wall_count ?? 0) / 2)}墩";
        return $"财神 {JokerLabel(value)} · {Math.Max(1,value?.dealer_streak ?? 1)}老庄 · {(instance?.expanded == true ? "收起" : "详情")}";
    }
    public static string Describe(HangzhouInfo value) {
        if (value == null) return "";
        var text=new StringBuilder();
        text.AppendLine($"财神 {JokerLabel(value)} · 牌墙{Math.Max(0,value.wall_count)}张");
        if (IsShaoxing(value)) {
            text.AppendLine("底花5；花牌/财神各1花，风刻1花，风明杠2/暗杠3，普通明杠1/暗杠2；三番到底。");
        } else if (IsTaizhou(value)) {
            text.AppendLine($"得牌万能，白板仅顺子顶替；墙尾留{Math.Max(0,value.dead_wall_count)}张（{Math.Max(0,value.dead_wall_count)/2}墩）。");
        } else if (IsYiwu(value)) {
            text.AppendLine($"闲5/庄7底分；自摸×2、财神头×4、单飘×8、双飘×16；墙尾{Math.Max(0,value.dead_wall_count)/2}墩。");
        } else {
            text.AppendLine($"{Math.Max(1,value.dealer_streak)}老庄 ×{Math.Max(2,value.dealer_multiplier)}");
        }
        int Count(int[] values,int i) => values!=null && i<values.Length ? values[i] : 0;
        string Seat(int i) { if(i<0 || i>3) return "?"; int original=value.seat_to_original!=null && i<value.seat_to_original.Length ? value.seat_to_original[i] : i;return new[]{"东起","南起","西起","北起"}[Mathf.Clamp(original,0,3)]; }
        for (int seat=0;seat<4;seat++) {
            string label=Seat(seat);
            bool locked=value.forced_draw_discard!=null && seat<value.forced_draw_discard.Length && value.forced_draw_discard[seat];
            if (IsShaoxing(value)) {
                text.AppendLine($"{label} · 花{Count(value.flower_counts,seat)} · 吃{Count(value.chi_counts,seat)} · 财飘{Count(value.cai_piao_counts,seat)}{(locked ? " · 仅摸切" : "")}");
            } else if (IsTaizhou(value)) {
                text.AppendLine($"{label} · 吃{Count(value.chi_counts,seat)}{(locked ? " · 仅摸切" : "")}");
            } else if (IsYiwu(value)) {
                text.AppendLine($"{label} · 财飘{Count(value.cai_piao_counts,seat)} · 吃{Count(value.chi_counts,seat)}{(locked ? " · 仅摸切" : "")}");
            } else {
                text.AppendLine($"{label} · 财飘{Count(value.cai_piao_counts,seat)} · 十风{Count(value.ten_winds_counts,seat)}/10 · 吃{Count(value.chi_counts,seat)}{(locked ? " · 仅摸切" : "")}");
                if (Count(value.chi_counts,seat)>=3) text.AppendLine($"  {label}和：{Seat((seat+3)%4)}全包；{Seat((seat+3)%4)}和：{label}双倍全包");
            }
        }
        var payment=value.ledger?["payment"] as Newtonsoft.Json.Linq.JObject;
        if (payment?["transfers"] is Newtonsoft.Json.Linq.JArray transfers) {
            text.AppendLine("支付明细");
            foreach (var transfer in transfers.OfType<Newtonsoft.Json.Linq.JObject>()) {
                int payer=(int?)transfer["payer"] ?? -1,winner=(int?)transfer["winner"] ?? -1;
                string reason=(string)transfer["reason"] ?? "";
                if ((string)value.ledger?["source"]=="ten_winds" && reason=="自摸") reason="十风";
                text.AppendLine($"{Seat(payer)}付{Seat(winner)} {transfer["points"]}分 · {reason}");
            }
        }
        var score=value.ledger?["score"] as Newtonsoft.Json.Linq.JObject;
        var decomposition=score?["decomposition"] as Newtonsoft.Json.Linq.JObject;
        if (decomposition?["joker_uses"] is Newtonsoft.Json.Linq.JArray uses && uses.Count>0) {
            text.AppendLine("财神替代："+string.Join("、",uses.OfType<Newtonsoft.Json.Linq.JObject>().Select(use=>TileName((int?)use["logical"] ?? 0))));
        }
        text.AppendLine($"杠后暗弃{value.burned_count}张");
        if (value.phase=="waiting_hangzhou_ten_winds") text.AppendLine("十风和牌选择");
        return text.ToString().TrimEnd();
    }
    private void Refresh() {
        caption.text=CaptionText(info);
        body.text=Describe(info); body.gameObject.SetActive(expanded);
        ((RectTransform)transform).sizeDelta=new Vector2(410,expanded ? Mathf.Min(450,body.preferredHeight+62) : 36);
    }
    private void Build() {
        var rect=(RectTransform)transform; rect.anchorMin=rect.anchorMax=rect.pivot=new Vector2(0,1); rect.anchoredPosition=new Vector2(16,-185);
        GetComponent<Image>().color=new Color(.055f,.075f,.095f,.96f); GetComponent<Image>().raycastTarget=false;
        var font=GameCanvas.Instance.GetComponentsInChildren<TMP_Text>(true).FirstOrDefault(t=>t.font!=null)?.font ?? TMP_Settings.defaultFontAsset;
        var button=new GameObject("Toggle",typeof(RectTransform),typeof(Image),typeof(Button));button.transform.SetParent(transform,false);
        var buttonRect=(RectTransform)button.transform;buttonRect.anchorMin=new Vector2(0,1);buttonRect.anchorMax=new Vector2(1,1);buttonRect.pivot=new Vector2(.5f,1);buttonRect.sizeDelta=new Vector2(0,36);
        button.GetComponent<Image>().color=new Color(.15f,.28f,.25f);
        button.GetComponent<Button>().onClick.AddListener(()=>{expanded=!expanded;Refresh();});
        caption=Label(button.transform,"Caption",font);caption.alignment=TextAlignmentOptions.Center;caption.fontSize=18;
        caption.rectTransform.anchorMin=Vector2.zero;caption.rectTransform.anchorMax=Vector2.one;caption.rectTransform.offsetMin=caption.rectTransform.offsetMax=Vector2.zero;
        body=Label(transform,"Details",font);body.fontSize=18;body.alignment=TextAlignmentOptions.TopLeft;body.textWrappingMode=TextWrappingModes.Normal;
        body.rectTransform.anchorMin=Vector2.zero;body.rectTransform.anchorMax=Vector2.one;body.rectTransform.offsetMin=new Vector2(12,10);body.rectTransform.offsetMax=new Vector2(-12,-44);
    }
    private static TMP_Text Label(Transform parent,string name,TMP_FontAsset font) {
        var go=new GameObject(name,typeof(RectTransform),typeof(TextMeshProUGUI));go.transform.SetParent(parent,false);
        var text=go.GetComponent<TextMeshProUGUI>();text.font=font;text.raycastTarget=false;text.color=new Color(.96f,.93f,.82f);return text;
    }
}
