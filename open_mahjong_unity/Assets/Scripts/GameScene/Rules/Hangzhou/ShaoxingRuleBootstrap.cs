using UnityEngine;

/// <summary>
/// 「绍兴麻将」大厅入口：144 张花麻，独立补花、财神与花数结算。
/// 不再复用萧山的倍率表。
/// </summary>
internal static class ShaoxingRuleBootstrap {
    public const string RuleId = "shaoxing";
    public const string SubRule = "shaoxing/shaoxing";
    public const string Description =
        "《绍兴麻将（144张花麻）》——144张、十三张手牌，只能自摸（可抢杠），无点炮。\n" +
        "【花牌·补花】春夏秋冬梅兰竹菊各1张，加上中发白各4张，共20张花牌；抓花明放并从牌尾补牌。中发白在绍兴是花牌，不参与手牌。\n" +
        "【财神】翻牌加一：万筒索九转一，风牌东南西北循环；翻到花牌则定东风为财神。财神共4张，可代任意非花牌，不可吃碰杠；白板是花牌，不顶替财神。\n" +
        "【花数】底花5；每张花牌/手中财神各1花；风刻1花；风牌明杠2花、暗杠3花；普通明杠1花、暗杠2花；风板大对+1花；无花无财神+20花。\n" +
        "【番数·三番到底】爆头、杠开、大吊车、抢杠、财飘各1番，天胡/地胡4番；常规封顶3番。得分=总花数×2^番数。\n" +
        "【承包】同一人吃碰三次形成承包：承包者胡3倍，被承包者胡（反承包）6倍。\n" +
        "【庄家】胡牌者坐庄，流局庄家连庄；坐庄不翻倍。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "绍兴麻将", "144张花麻", "绍兴麻将（花麻）", "绍兴", 1,
            Description,
            "总花数×2^番，三番到底\n承包3倍/反承包6倍；底花5，花牌与财神各1花。",
            "三番到底", hasFlowerReplacement: true);
    }
}
