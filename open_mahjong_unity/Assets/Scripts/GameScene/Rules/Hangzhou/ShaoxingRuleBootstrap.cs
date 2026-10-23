using UnityEngine;

/// <summary>
/// 「绍兴麻将」大厅入口：复用萧山家庭房规整套翻财/爆头/财飘/承包栈（最终是杭州引擎）。
/// 绍兴差异——财神翻牌加一（翻五万则六万为财神，九转一、北转东、白转中），
/// 同种四张全为财神、白板为普通牌；倍率封顶默认 8 倍（三番到底的家规化）。
/// </summary>
internal static class ShaoxingRuleBootstrap {
    public const string RuleId = "shaoxing";
    public const string SubRule = "shaoxing/shaoxing";
    public const string Description =
        "《绍兴麻将（家庭房规 v1.0）》——136张、十三张手牌，只能自摸和牌（含十风宣告），无点炮。\n" +
        "【财神·翻牌加一】开局翻出一张指示牌（移出牌墙），同色序号加一即为财神：万筒索按 1-9 循环（翻五万则六万为财神，九转一），风牌东→南→西→北循环，字牌中→发→白→中；同种四张皆为财神，可代替任意牌。财神不可吃碰杠，打出后他家照常鸣牌；白板为普通牌。\n" +
        "【番种·倍率相乘】爆头（财神做将）×2、明杠开×2、暗杠开×4、财飘每飘×4、碰碰胡×2、全倒×2、大吊车×2、无财神×2、七对子×4（豪华再翻）、清一色×8、乱风×32、十风×8；总倍率默认 8 倍封顶（三番到底的家规化），无番可和时按平和×1保底。\n" +
        "【杠与飘附加】本局开过杠但和牌非杠开：按杠数向各家各收1番，计「杠后和牌」；飘了未飘成（别家先自摸）：每飘向和牌家多付1番。\n" +
        "【老庄】连庄翻番：一老庄×2、二老庄×4、三老庄×8封顶，庄家一份按老庄倍数。\n" +
        "【承包】三摊承包：对同一人累计吃碰杠满三次，你和牌时他全包，他和牌时你双倍全包（反承包）。\n" +
        "【天胡/地胡】庄家开局第一巡自摸且全场无弃牌无副露为天胡，闲家第一巡自摸且全场无副露为地胡，均按封顶顶格结算。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        // lobbyOrder=1：排在萧山之后、其余杭州系变体之前。
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "绍兴麻将", "家庭房规 v1.0", "绍兴麻将（家庭房规）", "绍兴", 1,
            Description,
            "倍率相乘，默认8倍封顶\n老庄翻番；三摊承包；明杠开×2/暗杠开×4，财飘×4、飘不成+1番。",
            "倍率相乘", baotouRequiredSwitch: true, allowNegativeSwitch: true, claimAllowedSwitch: true);
    }
}
