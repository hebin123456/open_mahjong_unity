using UnityEngine;

/// <summary>
/// 「义乌麻将」大厅入口：136 张、翻牌财神、白板顶替，财神头/单飘/双飘
/// 与 5/7 底分结算；不再复用萧山番种倍率表。
/// </summary>
internal static class YiwuRuleBootstrap {
    public const string RuleId = "yiwu";
    public const string SubRule = "yiwu/yiwu";
    public const string Description =
        "《义乌麻将》——136张、十三张手牌，无花；可吃、碰、杠、点炮、自摸。\n" +
        "【财神】开局翻一张牌，翻出的牌本身为财神，同种场上只剩三张；白板顶替财神的本牌，可参与吃碰杠。财神不可吃碰杠，打出财神后其他人不能和这张财神。\n" +
        "【牌型】平胡、七对子、十三不靠；财神头=十二张已组合好、单张财神任意进张成对即和。\n" +
        "【财神限制】两个财神必须自摸；三个财神只能财神头或杠开，不能平胡、自摸、抢杠。\n" +
        "【飘】财神头时打出一张财神为单飘，打出两张为双飘；单飘×8、双飘×16，飘未成按财神头赔给和家。\n" +
        "【计分】闲家底分5、庄家底分7；平胡×1、自摸/抢杠×2、财神头/杠开×4、单飘×8、双飘×16。买子时底分+2。\n" +
        "【牌墙】正常剩17墩（34张）流局；单飘剩7墩（14张）、双飘剩3墩（6张）。\n" +
        "【轮庄】庄家和牌连庄，闲家和牌由庄家下家上庄。";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "义乌麻将", "翻财·财神头", "义乌麻将", "义乌", 3,
            Description,
            "闲5/庄7底分；自摸×2、财神头×4、单飘×8、双飘×16\n两个财神须自摸；17/7/3墩流局。",
            "财神头·单双飘");
    }
}
