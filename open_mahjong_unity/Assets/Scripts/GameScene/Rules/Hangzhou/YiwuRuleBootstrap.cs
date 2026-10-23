using UnityEngine;

/// <summary>
/// 「义乌麻将」大厅入口：复用萧山家庭房规整套翻财/爆头/财飘/承包栈（最终是杭州引擎）。
/// 义乌差异——轮庄制（庄家和牌连庄、闲家和牌庄下家上庄）、剩 17 墩（34 张）流局。
/// </summary>
internal static class YiwuRuleBootstrap {
    public const string RuleId = "yiwu";
    public const string SubRule = "yiwu/yiwu";
    public const string Description =
        "《义乌麻将（家庭房规 v1.0）》——136张、十三张手牌，只能自摸和牌（含十风宣告），无点炮。\n" +
        "【财神】开局翻出一张牌，该牌同种四张皆为财神；白板固定顶替翻出的那张（如翻出五万则白板当五万用），本身不是万能牌。财神不可吃碰杠、不进副露，可打出；白板按本牌身份可正常吃碰杠。\n" +
        "【财神头】含财神的牌型只能自摸，不可靠打出的财神点炮（本房规恒仅自摸，天然满足）。\n" +
        "【牌墙】剩 17 墩（34 张）流局；单飘/双飘提前收尾的家规暂未实现，飘按财飘番种计。\n" +
        "【番种·倍率相乘】爆头×2、明杠开×2、暗杠开×4、财飘每飘×4、碰碰胡×2、全倒×2、大吊车×2、无财神×2、七对子×4（豪华再翻）、清一色×8、乱风×32、十风×8；总倍率默认 64 封顶，无番可和时按平和×1保底。\n" +
        "【庄家·轮庄制】庄家和牌连庄，闲家和牌由庄家下家上庄，流局连庄。\n" +
        "【杠与飘附加】本局开过杠但和牌非杠开：按杠数向各家各收1番；飘了未飘成（别家先自摸）：每飘向和牌家多付1番。\n" +
        "【承包】三摊承包：对同一人累计吃碰杠满三次，你和牌时他全包，他和牌时你双倍全包（反承包）。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        // lobbyOrder=3：萧山、绍兴、台州之后。
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "义乌麻将", "家庭房规 v1.0", "义乌麻将（家庭房规）", "义乌", 3,
            Description,
            "倍率相乘，默认64倍封顶\n轮庄制；剩17墩流局；三摊承包；明杠开×2/暗杠开×4，财飘×4、飘不成+1番。",
            "倍率相乘", baotouRequiredSwitch: true, allowNegativeSwitch: true, claimAllowedSwitch: true);
    }
}
