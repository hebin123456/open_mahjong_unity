using UnityEngine;

/// <summary>
/// 「台州麻将」大厅入口：黄岩基准，136 张、可点炮、得牌与胡数/番结算。
/// 不再复用杭州 MIL 番种表。
/// </summary>
internal static class TaizhouRuleBootstrap {
    public const string RuleId = "taizhou";
    public const string SubRule = "taizhou/taizhou";
    public const string Description =
        "《台州麻将（黄岩基准）》——136张、十三张手牌，无花；可吃、碰、杠、点炮、自摸。\n" +
        "【得】开局翻一张牌，翻出的牌本身为“得”，同种场上只剩三张；得可代任意牌，不可吃碰杠。白板只在组成顺子时顶替得的本牌，做对子/刻子仍按白板计算。\n" +
        "【轮庄】庄家和牌连庄，闲家和牌由庄家下家上庄；流局庄家留任，不计分。\n" +
        "【流局】墙尾留7墩（14张）；每开一杠加留2墩（4张）。\n" +
        "【胡数】基础牌点按对子/明刻/暗刻/明杠/暗杠累计，加底胡10；自摸、嵌档、单吊、靠柄、对对胡另有胡数。\n" +
        "【番数】门风刻杠、字牌刻杠各1番；清一色4番、混一色2番、对对和2番、旁和1番、海底1番、无得1番、二得1番、三得2番、杠开1番、抢杠1番、自摸得1番、大吊车1番、四风会齐4番。\n" +
        "【结算】胡数=（基础牌点+底胡10）×2^番数，100胡封顶（腊子）；庄家输满额，闲家按胡数差减半。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "台州麻将", "黄岩基准", "台州麻将（黄岩）", "台州", 2,
            Description,
            "胡数×2^番，100胡封顶\n得牌+白板顺子顶替；可点炮/抢杠；轮庄。",
            "100胡封顶");
    }
}
