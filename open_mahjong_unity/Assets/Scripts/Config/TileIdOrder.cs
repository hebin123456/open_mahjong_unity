using System.Collections.Generic;

/// <summary>
/// 牌 id 排序工具。
/// 牌 id 使用 11-19 万 / 21-29 饼 / 31-39 条 / 41-47 字牌；立直规则额外使用 105/205/305 表示赤 5m/5p/5s；国标花牌使用 51-58。
/// 默认 <c>Array.Sort</c> / <c>List.Sort</c> 按数值升序会把 105/205/305 甩到全部普通牌之后，
/// 导致赤 5 出现在手牌最右侧，与常规 5 不相邻。此工具按"归一化值"排序，并让赤 5 稳定紧跟同色普通 5。
/// 排序顺序支持自定义：万/饼/条三种花色的相对顺序（SuitOrderOptions 共 6 种全排列）、字牌插入的位置（HonorOrderOptions 共 4 种），
/// 以及三元牌（中/白/发）的相对顺序（DragonOrderOptions 共 6 种；日麻对局使用 RiichiDragonOrderOptions 共 3 种）。
/// 自定义规则由 <see cref="ConfigManager"/> 在加载/变更时通过 <see cref="SetSortRule"/> 推入，默认万→饼→条→字牌、三元中发白。
/// </summary>
public static class TileIdOrder {
    public static readonly Comparer<int> Comparer = Comparer<int>.Create(Compare);

    private const int Chun = 45;
    private const int Haku = 46;
    private const int Hatsu = 47;

    /// <summary>花色顺序下拉选项（索引即 ConfigManager.HandSortSuitOrderMode）。万/饼/条共 3! = 6 种全排列。</summary>
    public static readonly string[] SuitOrderOptions = {
        "万饼条", "万条饼", "饼万条", "饼条万", "条万饼", "条饼万"
    };

    /// <summary>字牌位置下拉选项（索引即 ConfigManager.HandSortHonorOrderMode）。</summary>
    public static readonly string[] HonorOrderOptions = {
        "最后", "第三", "第二", "最前"
    };

    /// <summary>三元牌排序下拉选项（索引即 ConfigManager.HandSortDragonOrderMode）。非日麻对局使用。</summary>
    public static readonly string[] DragonOrderOptions = {
        "中发白", "中白发", "发中白", "发白中", "白中发", "白发中"
    };

    /// <summary>日麻三元牌排序下拉选项（索引即 ConfigManager.HandSortRiichiDragonOrderMode）。日麻对局使用。</summary>
    public static readonly string[] RiichiDragonOrderOptions = {
        "中白发", "发中白", "白发中"
    };

    // 每种花色顺序对应的三花色排列（0=万 1=饼 2=条）。
    private static readonly int[][] SuitPermutations = {
        new[] { 0, 1, 2 }, // 万饼条
        new[] { 0, 2, 1 }, // 万条饼
        new[] { 1, 0, 2 }, // 饼万条
        new[] { 1, 2, 0 }, // 饼条万
        new[] { 2, 0, 1 }, // 条万饼
        new[] { 2, 1, 0 }, // 条饼万
    };

    // 三元牌排列：与 DragonOrderOptions 文案一致，从左到右；45=中 46=白 47=发。
    private static readonly int[][] DragonPermutations = {
        new[] { Chun, Hatsu, Haku }, // 中发白 → 45,47,46
        new[] { Chun, Haku, Hatsu }, // 中白发 → 45,46,47
        new[] { Hatsu, Chun, Haku }, // 发中白 → 47,45,46
        new[] { Hatsu, Haku, Chun }, // 发白中 → 47,46,45
        new[] { Haku, Chun, Hatsu }, // 白中发 → 46,45,47
        new[] { Haku, Hatsu, Chun }, // 白发中 → 46,47,45
    };

    private static readonly int[][] RiichiDragonPermutations = {
        new[] { Chun, Haku, Hatsu }, // 中白发 → 45,46,47
        new[] { Hatsu, Chun, Haku }, // 发中白 → 47,45,46
        new[] { Haku, Hatsu, Chun }, // 白发中 → 46,47,45
    };

    // suit -> 组排名（越小越靠左）。索引：0 万 1 饼 2 条 3 字牌 4 花牌/其它。
    private static readonly int[] groupRank = { 0, 1, 2, 3, 4 };
    private static int dragonOrderMode;
    private static int riichiDragonOrderMode = 2;

    /// <summary>
    /// 设置自定义排序规则。
    /// </summary>
    /// <param name="suitOrderMode">花色顺序，索引对应 <see cref="SuitOrderOptions"/>（0-5）。</param>
    /// <param name="honorOrderMode">字牌位置，索引对应 <see cref="HonorOrderOptions"/>：0 最后 1 第三 2 第二 3 最前。</param>
    /// <param name="dragonOrderMode">三元牌顺序，索引对应 <see cref="DragonOrderOptions"/>（0-5，默认 0 中发白）。</param>
    /// <param name="riichiDragonOrderMode">日麻三元牌顺序，索引对应 <see cref="RiichiDragonOrderOptions"/>（0-2，默认 2 白发中）。</param>
    public static void SetSortRule(int suitOrderMode, int honorOrderMode, int dragonOrderMode, int riichiDragonOrderMode) {
        if (suitOrderMode < 0 || suitOrderMode >= SuitPermutations.Length) suitOrderMode = 0;
        if (honorOrderMode < 0 || honorOrderMode > 3) honorOrderMode = 0;
        if (dragonOrderMode < 0 || dragonOrderMode >= DragonPermutations.Length) dragonOrderMode = 0;
        if (riichiDragonOrderMode < 0 || riichiDragonOrderMode >= RiichiDragonPermutations.Length) riichiDragonOrderMode = 2;

        int[] order = SuitPermutations[suitOrderMode];
        // 4 个分组槽位（3 花色 + 字牌）；字牌插入位置：最后=3 第三=2 第二=1 最前=0。
        int honorSlot = 3 - honorOrderMode;
        for (int k = 0; k < order.Length; k++) {
            int slot = k < honorSlot ? k : k + 1;
            groupRank[order[k]] = slot;
        }
        groupRank[3] = honorSlot; // 字牌
        groupRank[4] = 4;         // 花牌/未知，恒定排在四组之后

        TileIdOrder.dragonOrderMode = dragonOrderMode;
        TileIdOrder.riichiDragonOrderMode = riichiDragonOrderMode;
    }

    /// <summary>按手牌排序设置返回花色 id 顺序（1=万 2=筒 3=条）。</summary>
    public static int[] GetOrderedSuitIds(int suitOrderMode) {
        if (suitOrderMode < 0 || suitOrderMode >= SuitPermutations.Length) suitOrderMode = 0;
        int[] order = SuitPermutations[suitOrderMode];
        return new[] { order[0] + 1, order[1] + 1, order[2] + 1 };
    }

    /// <summary>萧山翻财排列是否生效：生效时摸牌也需要立即重排，让白板按财神本牌落位。</summary>
    public static bool XiaoshanJokerActive => TryGetXiaoshanJoker(out _);

    public static int Compare(int a, int b) {
        int ka = SortKey(a);
        int kb = SortKey(b);
        if (ka != kb) return ka.CompareTo(kb);
        // 排序键相同时（普通 5 与赤 5 同色）按原 id 升序：普通 5(=15/25/35) 在前，赤 5(=105/205/305) 在后
        return a.CompareTo(b);
    }

    // 组排名占高位、组内排序值占低位。字牌组内：风牌 0-3，三元牌 10+dragonRank。
    private static int SortKey(int tile) {
        // 萧山翻财排列：翻出的财神实体（万能牌）独立排最右；白板按其顶替的财神本牌排序，
        // 即白板插入到财神牌原本的位置，而财神牌不再占据本牌序列、移到手牌独立位置。
        if (TryGetXiaoshanJoker(out int joker)) {
            if (tile == joker) return int.MaxValue;
            if (tile == Haku) return BaseKey(joker);
        }
        return BaseKey(tile);
    }

    // 无翻财调整的基础排序键。
    private static int BaseKey(int tile) {
        int suit = SuitOf(tile);
        int inner = suit == 3 ? HonorSortValue(tile) : Normalize(tile);
        return groupRank[suit] * 1000 + inner;
    }

    /// <summary>
    /// 翻财变体上下文（萧山/绍兴/义乌）：返回财神牌 id。对局取杭州引擎投影的 joker_tiles
    /// 首个（服务端下发的动态财神，绍兴已是翻牌加一后的财神牌）；杭州恒白板不在此列。
    /// 牌谱回放按 game_title 的 rule/sub_rule 与 joker_tiles。
    /// </summary>
    private static bool TryGetXiaoshanJoker(out int joker) {
        joker = 0;
        HangzhouInfo info = HangzhouGameState.Active?.Info;
        if (info != null) {
            if (HangzhouRuleBootstrap.IsFlippedJokerVersion(info.rule_version)) {
                if (info.joker_tiles != null && info.joker_tiles.Length > 0) {
                    joker = info.joker_tiles[0];
                    return true;
                }
                // 旧版本兜底：萧山 joker_tiles 缺失时用翻出的指示牌（即财神）。
                if (info.joker_indicator > 0) {
                    joker = info.joker_indicator;
                    return true;
                }
            }
            return false;
        }
        if (TryGetRecordRule(out string recordRule, out string recordSubRule)
                && (recordRule == "xiaoshan" || recordRule == "shaoxing" || recordRule == "taizhou"
                    || recordRule == "yiwu"
                    || (recordSubRule != null && (recordSubRule.StartsWith("xiaoshan")
                        || recordSubRule.StartsWith("shaoxing") || recordSubRule.StartsWith("taizhou")
                        || recordSubRule.StartsWith("yiwu"))))) {
            Dictionary<string, object> gameTitle = GameRecordManager.Instance.gameRecord?.gameTitle;
            if (gameTitle != null && gameTitle.TryGetValue("joker_tiles", out object tilesObj) && tilesObj != null
                    && tilesObj is System.Collections.IEnumerable list && !(tilesObj is string)) {
                foreach (object item in list) {
                    // 服务端记录的 joker_tiles 恰为翻出的那张，取首个即可。
                    if (item != null && int.TryParse(item.ToString(), out joker)) {
                        return true;
                    }
                    break;
                }
            }
        }
        return false;
    }

    private static int HonorSortValue(int tile) {
        int t = Normalize(tile);
        if (t >= 41 && t <= 44) return t - 41;
        if (t == Chun || t == Haku || t == Hatsu) return 10 + GetDragonRank(t);
        return t;
    }

    private static int GetDragonRank(int tileId) {
        int[] permutation = IsRiichiContext() ? RiichiDragonPermutations[riichiDragonOrderMode] : DragonPermutations[dragonOrderMode];
        for (int i = 0; i < permutation.Length; i++) {
            if (permutation[i] == tileId) return i;
        }
        return 0;
    }

    private static bool IsRiichiContext() {
        // 牌谱/观战回放：以 gameTitle 为准，避免误读上一局残留的 roomRule。
        if (TryGetRecordRule(out string recordRule, out string recordSubRule)) {
            return RuleRegistry.Resolve(recordRule, recordSubRule)?.UsesRiichiDragonOrder == true;
        }
        if (GameSession.Current.IsGameActive) {
            return RuleRegistry.Current?.UsesRiichiDragonOrder == true;
        }
        return false;
    }

    private static bool TryGetRecordRule(out string rule, out string subRule) {
        rule = null;
        subRule = null;
        if (GameRecordManager.Instance == null || !GameRecordManager.Instance.gameObject.activeSelf) return false;
        Dictionary<string, object> gameTitle = GameRecordManager.Instance.gameRecord?.gameTitle;
        if (gameTitle == null) return false;
        if (gameTitle.TryGetValue("rule", out object ruleObj)) rule = ruleObj?.ToString();
        if (gameTitle.TryGetValue("sub_rule", out object subRuleObj)) subRule = subRuleObj?.ToString();
        return true;
    }

    // 0 万 1 饼 2 条 3 字牌 4 花牌/其它。
    private static int SuitOf(int tile) {
        int t = Normalize(tile);
        if (t >= 11 && t <= 19) return 0;
        if (t >= 21 && t <= 29) return 1;
        if (t >= 31 && t <= 39) return 2;
        if (t >= 41 && t <= 47) return 3;
        return 4;
    }

    /// <summary>
    /// 将赤 5（105/205/305）归一化为同色普通 5（15/25/35），其余 id 不变。
    /// </summary>
    public static int Normalize(int tile) {
        if (tile == 105) return 15;
        if (tile == 205) return 25;
        if (tile == 305) return 35;
        return tile;
    }
}
