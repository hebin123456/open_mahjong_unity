using UnityEngine;

/// <summary>
/// 「萧山麻将」大厅入口：完全复用杭州麻将引擎与整套对局流程，仅规则文案与财神不同。
/// 客户端与服务端都按杭州引擎处理，协议通道沿用 hangzhou，消息按 xiaoshan 前缀重标注。
/// </summary>
internal static class XiaoshanRuleBootstrap {
    public const string RuleId = "xiaoshan";
    public const string SubRule = "xiaoshan/xiaoshan";
    public const string Description = "《萧山麻将（家庭房规 v1.3）》：杭州麻将拓展版，136张、十三张手牌，仅自摸。四家抓完后翻出牌墙剩张为财神，是万能牌；白板固定顶替其本牌（如翻出一万则白板当一万用），本身不是万能牌。财神不可吃碰杠、可打出；打出后他家仍可正常吃碰杠。开启「有财神必须爆头」时，含财神的和牌须财神做将；清一色、七对子豁免。番种倍率相乘（爆头×2、碰碰胡×2、全倒×2、清一色×8、字一色×16……），默认32倍封顶。一老庄2倍、二老庄4倍、三老庄8倍起翻番；三吃承包、天胡通杀、地胡按封顶结算。持有开局积分时为清零制：输家最多输光不出现负分，任一家0分整场结束；开启「积分小于 0 继续」则输家可扣成负分，打满局数才收场。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "萧山麻将", "家庭房规 v1.3", "萧山麻将（家庭房规）", "萧山", 13,
            Description,
            "番种倍率相乘，默认32倍封顶\n老庄2/4/8倍起翻番；三吃承包依责任支付。",
            "倍率相乘", baotouRequiredSwitch: true, allowNegativeSwitch: true);
    }
}
