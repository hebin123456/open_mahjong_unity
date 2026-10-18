using UnityEngine;

/// <summary>
/// 「萧山麻将」大厅入口：完全复用杭州麻将引擎与整套对局流程，仅展示名不同。
/// 客户端与服务端都按杭州引擎处理，协议通道沿用 hangzhou。
/// </summary>
internal static class XiaoshanRuleBootstrap {
    public const string RuleId = "xiaoshan";
    public const string SubRule = "xiaoshan/xiaoshan";

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.SubsystemRegistration)]
    public static void Register() {
        HangzhouRuleBootstrap.RegisterVariant(RuleId, SubRule, "萧山麻将", "萧山麻将", "萧山麻将", "萧山", 13);
    }
}
