using UnityEngine;

/// <summary>
/// 「台州麻将」大厅入口：以黄岩打法为基准的家庭房规，复用杭州 MIL 引擎栈
/// （白板财神、爆头、财飘、老庄、三吃承包）。台州差异——轮庄制：
/// 庄家和牌连庄，闲家和牌由庄家下家上庄；家庭房规暂不开放放铳（仅自摸）。
/// </summary>
internal static class TaizhouRuleBootstrap {
    public const string RuleId = "taizhou";
    public const string SubRule = "taizhou/taizhou";
    public const string Description =
        "《台州麻将（黄岩打法·家庭房规）》——136张、十三张手牌，可吃、可碰、可杠，仅自摸和牌（家庭房规暂不开放放铳）。\n" +
        "【庄家·轮庄制】庄家和牌连庄，闲家和牌由庄家下家上庄，流局连庄。\n" +
        "【财神】白板固定为财神，可代替任意牌；打出财神后本巡他家不得吃碰杠。\n" +
        "【番种】4番封顶：爆头、财飘、杠开、七对、清一色等按杭州MIL番种表计；老庄连庄 2/4/8 倍；三吃承包依责任支付。\n" +
        "【流局】墙尾 20 张（10 墩）流局。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        // lobbyOrder=2：萧山、绍兴之后。
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "台州麻将", "黄岩打法·家庭房规", "台州麻将（黄岩家庭房规）", "台州", 2,
            Description);
    }
}
