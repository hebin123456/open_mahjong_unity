using UnityEngine;

/// <summary>
/// 「萧山麻将」大厅入口：完全复用杭州麻将引擎与整套对局流程，仅规则文案与财神不同。
/// 客户端与服务端都按杭州引擎处理，协议通道沿用 hangzhou，消息按 xiaoshan 前缀重标注。
/// </summary>
internal static class XiaoshanRuleBootstrap {
    public const string RuleId = "xiaoshan";
    public const string SubRule = "xiaoshan/xiaoshan";
    public const string Description = "《萧山麻将（家庭房规 v1.3）》：杭州麻将拓展版，136张、十三张手牌，仅自摸。四家抓完后翻出牌墙剩张为财神，白板顶替该牌，两者皆可代任意牌。财神不可吃碰杠、可打出；打出后他家仍可正常吃碰杠。番种倍率相乘（爆头×2、碰碰胡×2、全倒×2、清一色×8、字一色×16……），默认32倍封顶。一老庄2倍、二老庄4倍、三老庄8倍起翻番；三吃承包、天胡通杀、地胡按封顶结算。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "萧山麻将", "家庭房规 v1.3", "萧山麻将（家庭房规）", "萧山", 13,
            Description,
            "番种倍率相乘，默认32倍封顶\n老庄2/4/8倍起翻番；三吃承包依责任支付。",
            "倍率相乘", baotouRequiredSwitch: true);
    }
}
