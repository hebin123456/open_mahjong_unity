using UnityEngine;

/// <summary>
/// 「萧山麻将」大厅入口：完全复用杭州麻将引擎与整套对局流程，仅规则文案与财神不同。
/// 客户端与服务端都按杭州引擎处理，协议通道沿用 hangzhou，消息按 xiaoshan 前缀重标注。
/// </summary>
internal static class XiaoshanRuleBootstrap {
    public const string RuleId = "xiaoshan";
    public const string SubRule = "xiaoshan/xiaoshan";
    public const string Description =
        "《萧山麻将（家庭房规 v1.3）》——杭州麻将拓展版，136张、十三张手牌，只能自摸和牌（含十风宣告），无点炮。\n" +
        "【财神】四家抓完后翻出牌墙所剩的一张为财神，是万能牌，场上同种财神仅剩3张；白板固定顶替其本牌（如翻出一万则白板当一万用），本身不是万能牌。财神不可吃碰杠、不进副露，可打出；白板按本牌身份可正常吃碰杠。\n" +
        "【爆头】开启「有财神必须爆头」时，含财神的和牌须财神做将；杠爆（杠后补牌摸进的恰是财神）与清一色、七对子豁免。\n" +
        "【番种·倍率相乘】碰碰胡×2、全倒（四组面子全为明副露）×2、爆头（财神做将）×2、杠开（杠后补牌自摸）×2、无财神×2、财飘每飘×2、七对子×4、清一色×8、十风×8（所弃十张字牌含财神×16）、豪华七对×16、字一色×16；总倍率默认32封顶（可协商64），无番可和时按平和×1保底。\n" +
        "【杠与飘附加】本局开过杠但和牌非杠开：按杠数向各家各收1番，计「杠后和牌」；飘了未飘成（别家先自摸）：每飘向和牌家多付1番。两类附加均在结算番种（+N番）与支付明细中逐条展示。\n" +
        "【老庄】连庄翻番：一老庄×2、二老庄×4、三老庄×8封顶，庄家一份按老庄倍数。\n" +
        "【承包】对同一人累计吃碰满三次：你和牌时他全包，他和牌时你双倍全包（反承包）。\n" +
        "【天胡/地胡】庄家开局第一巡自摸且全场无弃牌无副露为天胡，一把通杀其余三家；闲家第一巡自摸且全场无副露为地胡，按封顶结算。\n" +
        "【积分】持有开局积分（50/100/200）时为清零制：输家最多输光不出现负分，任一家0分整场结束；开启「积分小于 0 继续」则输家可扣成负分，打满局数才收场。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "萧山麻将", "家庭房规 v1.3", "萧山麻将（家庭房规）", "萧山", 13,
            Description,
            "番种倍率相乘，默认32倍封顶\n老庄翻番；三吃承包；杠开×2、未杠开+1番，财飘×2、飘不成+1番；清零制可选。",
            "倍率相乘", baotouRequiredSwitch: true, allowNegativeSwitch: true);
    }
}
