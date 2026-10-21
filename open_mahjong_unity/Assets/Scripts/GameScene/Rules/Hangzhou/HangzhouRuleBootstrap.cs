using System;
using System.Collections.Generic;
using System.Linq;
using UnityEngine;

internal static class HangzhouRuleBootstrap {
    public const string Description = "MIL《杭州麻将（推广）竞赛规则（试行2025版）》：136张、十三张手牌，白板为财神，仅自摸。爆头、财飘、七对、十风，4番封顶，老庄2/4/8倍，三吃承包，墙尾20张流局。";
    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        RegisterVariant(HangzhouGameState.RuleId, HangzhouGameState.SubRule, "杭州麻将", "MIL 2025", "MIL 杭州（2025）", "杭州", 12);
        ActionWords.Register(new ActionWordSpec { Word = "hangzhou_piao", Kind = ActionWordKind.Other, Label = _ => "飘" });
        ActionWords.Register(new ActionWordSpec { Word = "hangzhou_tail_burn", Kind = ActionWordKind.Other, Label = _ => "" });
        ActionWords.Register(new ActionWordSpec { Word = "hangzhou_ten_winds", Kind = ActionWordKind.Tsumo, Label = _ => "十风和" });
    }

    /// <summary>
    /// 注册一套「杭州引擎」的大厅清单。杭州麻将与萧山麻将共用同一引擎，
    /// 仅展示名/子规则文案不同，故清单按参数化生成。
    /// </summary>
    internal static void RegisterVariant(
        string ruleId, string subRule, string displayName, string subRuleDisplay,
        string subRuleWholeName, string shortName, int lobbyOrder,
        string description = null, string settlementFootnote = null, string roundSupplement = null,
        bool baotouRequiredSwitch = false, bool allowNegativeSwitch = false, bool claimAllowedSwitch = false) {
        RuleNameDictionary.WholeName[ruleId] = displayName;
        RuleNameDictionary.WholeName[subRule] = subRuleWholeName;
        RuleNameDictionary.ShortName[ruleId] = shortName;
        RuleNameDictionary.ShortName[subRule] = shortName;
        var createRoomDefaults = new Dictionary<string,object> {
            { CreateRoomKeys.SubRule, 0 }, { CreateRoomKeys.GameRound, 4 }, { CreateRoomKeys.RoundTimer, 3 }, { CreateRoomKeys.StepTimer, 1 },
            { CreateRoomKeys.Tips, true }, { CreateRoomKeys.CountTips, false }, { CreateRoomKeys.PointerTips, true },
            { CreateRoomKeys.TacticalCall, true },
            { CreateRoomKeys.Password, false }, { CreateRoomKeys.RandomSeed, false }, { CreateRoomKeys.TouristLimit, false }, { CreateRoomKeys.AllowSpectator, true },
        };
        if (baotouRequiredSwitch) createRoomDefaults[CreateRoomKeys.BaotouRequired] = false;
        if (allowNegativeSwitch) createRoomDefaults[CreateRoomKeys.AllowNegative] = false;
        if (claimAllowedSwitch) createRoomDefaults[CreateRoomKeys.ClaimAllowed] = true;
        RuleRegistry.Register(new RuleManifest {
            RuleId = ruleId, DefaultSubRule = subRule,
            DisplayName = displayName, LobbyName = displayName, LobbyOrder = lobbyOrder,
            LobbySubRules = new[] { new RuleLobbySubRule(subRule, subRuleDisplay, description ?? Description) },
            CreateRoomDefaults = createRoomDefaults,
            GameStateFactory = () => new HangzhouGameState(), OutboundChannel = "hangzhou",
            Tingpai = query => GameRecordManager.Instance?.ShouldShowRecordTips() == true
                ? GameRecordManager.Instance.HangzhouWaiting(query) : HangzhouGameState.Active?.Hints.Waiting(query) ?? new HashSet<int>(),
            DescribeWaitingTile = query => WaitTileHint.TsumoOnly("仅自摸"),
            TileBadgeText = tile => IsJokerTile(tile) ? "财" : null,
            FanNameText = (rule,label) => HangzhouFanText.Name(label),
            FanValueText = (rule,label) => rule == XiaoshanRuleBootstrap.RuleId
                ? HangzhouFanText.Multiplier(label) : HangzhouFanText.Value(label),
            SettlementTotal = query => new SettlementTotalDisplay { FanText = $"基本分 {query.HuScore}分", ScoreText = query.WinnerPointDelta.HasValue ? $"{query.WinnerPointDelta.Value:+#;-#;0}分" : "" },
            ScoreboardFanText = query => $"基本分 {query.HuScore}分",
            SettlementFootnote = query => settlementFootnote != null
                ? (query.HuFan != null && query.HuFan.Length > 0 ? settlementFootnote : null)
                : query.HuFan != null && query.HuFan.Length > 0
                    ? "4番封顶；庄家2/4/8倍\n三吃承包依责任支付。" : null,
            SettlementHasNoWinTile = HangzhouFanText.IsTenWinds,
            RecordFlowerWin = tick => tick != null && tick.Count > 3 && tick[3].Contains("十风"),
            HuPresentationAction = (kind,fans,config) => HangzhouFanText.IsTenWinds(fans) ? "hangzhou_ten_winds" : kind,
            ActionCaption = word => word == "hangzhou_piao" ? "飘" : word == "hangzhou_ten_winds" || (word == "hu_self" && HangzhouGameState.Active?.Info?.phase == "waiting_hangzhou_ten_winds") ? "十风和" : null,
            ActionVoice = word => word == "hangzhou_ten_winds" || word == "hu_self" ? "hu" : word == "hangzhou_piao" ? "piao" : null,
            RoundName = round => $"第{round}局", MaxRoundText = rounds => $"{rounds * 4}局", RoundStatusText = info => $"第{info.current_round}局",
            RoundSupplementText = info => roundSupplement ?? "4番封顶",
            DefaultHepaiLimit = 0, HasFlowerReplacement = false, ReplacementFromTailEnd = true,
            ShowsRonDangerHints = false,
            ReplayConcealedKongMask = (rule,tiles) => new[] { 2,tiles[0],2,tiles[1],2,tiles[2],2,tiles[3] },
            RulebookKey = "hangzhou",
        });
    }

    /// <summary>财神牌判定：优先取当前对局下发的动态财神集合（萧山翻财），否则回退固定白板。</summary>
    internal static bool IsJokerTile(int tile) {
        var tiles = HangzhouGameState.Active?.Info?.joker_tiles;
        if (tiles != null && tiles.Length > 0) return tiles.Contains(tile);
        return tile == HangzhouGameState.Joker;
    }
}
internal static class HangzhouFanText {
    public static string Name(string label) { var f=label?.Split('|'); return f?.Length>=4 && f[0]=="HZ" ? f[3] : label; }
    public static string Value(string label) { var f=label?.Split('|'); return f?.Length>=4 && f[0]=="HZ" ? f[2]+"番" : ""; }
    /// <summary>萧山按「倍率相乘」，番种标注为 ×N倍 而非 N番；+1 番附加条目（杠后和牌/对手飘）标注 +1番。</summary>
    public static string Multiplier(string label) {
        var f=label?.Split('|');
        if (f == null || f.Length < 4 || f[0] != "HZ") return "";
        return f[1] == "kong_extra" || f[1] == "piao_fail" ? "+"+f[2]+"番" : "×"+f[2]+"倍";
    }
    public static bool IsTenWinds(IList<string> fans) => fans != null && fans.Any(f => Name(f)=="十风");
}
