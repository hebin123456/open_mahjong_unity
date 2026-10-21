using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public partial class CreatePanel {
    private Toggle XiaoshanBaotouToggle;
    private Toggle XiaoshanNegativeToggle;
    private Toggle XiaoshanClaimToggle;

    /// <summary>运行时克隆萧山专属 DIY 开关（爆头/负分/三摊承包），并挂接右侧悬浮说明。</summary>
    private void EnsureXiaoshanBaotouToggle() {
        if (XiaoshanBaotouToggle != null || TacticalCallToggle == null) return;
        XiaoshanBaotouToggle = Instantiate(TacticalCallToggle, TacticalCallToggle.transform.parent);
        XiaoshanBaotouToggle.name = "XiaoshanBaotou";
        XiaoshanBaotouToggle.SetIsOnWithoutNotify(false);
        // 克隆只复用样式，不继承模板互斥组及其他玩法的回调。
        XiaoshanBaotouToggle.group = null;
        XiaoshanBaotouToggle.onValueChanged = new Toggle.ToggleEvent();
        TMP_Text label = XiaoshanBaotouToggle.GetComponentInChildren<TMP_Text>(true);
        if (label != null) label.text = "有财神必须爆头";

        XiaoshanNegativeToggle = Instantiate(TacticalCallToggle, TacticalCallToggle.transform.parent);
        XiaoshanNegativeToggle.name = "XiaoshanNegative";
        XiaoshanNegativeToggle.SetIsOnWithoutNotify(false);
        XiaoshanNegativeToggle.group = null;
        XiaoshanNegativeToggle.onValueChanged = new Toggle.ToggleEvent();
        label = XiaoshanNegativeToggle.GetComponentInChildren<TMP_Text>(true);
        if (label != null) label.text = "积分小于 0 继续";

        XiaoshanClaimToggle = Instantiate(TacticalCallToggle, TacticalCallToggle.transform.parent);
        XiaoshanClaimToggle.name = "XiaoshanClaim";
        // 「允许三摊承包」默认开启；关闭时对同一家最多吃碰 2 摊。
        XiaoshanClaimToggle.SetIsOnWithoutNotify(true);
        XiaoshanClaimToggle.group = null;
        XiaoshanClaimToggle.onValueChanged = new Toggle.ToggleEvent();
        label = XiaoshanClaimToggle.GetComponentInChildren<TMP_Text>(true);
        if (label != null) label.text = "允许三摊承包";

        BindXiaoshanHelp(XiaoshanBaotouToggle,
            "有财神必须爆头：开启后，含财神的和牌须财神做将；杠爆（杠后补牌恰摸进财神）、清一色、七对子豁免。");
        BindXiaoshanHelp(XiaoshanNegativeToggle,
            "积分小于 0 继续：开启后输家可扣成负分且不触发清零终局，打满局数才收场；关闭为清零制，任一家 0 分整场结束。");
        BindXiaoshanHelp(XiaoshanClaimToggle,
            "允许三摊承包：开启时对同一家累计吃碰满三摊触发承包/反承包；关闭后对同一家最多吃碰 2 摊，不触发承包（杠不属于摊，不受限）。");
    }

    private void BindXiaoshanHelp(Component control, string message) {
        if (control == null) return;
        CreateRoomHelpTrigger trigger = control.GetComponent<CreateRoomHelpTrigger>();
        if (trigger == null) trigger = control.gameObject.AddComponent<CreateRoomHelpTrigger>();
        trigger.Configure(this, message);
    }

    /// <summary>
    /// 萧山馆规开关随建房请求下发。「积分小于 0 继续」：输家可扣成负分且不触发
    /// 清零终局；「允许三摊承包」默认开启，仅关闭时携带 false；其余开关关闭时
    /// 不携带，服务端按默认关闭处理。
    /// </summary>
    private Dictionary<string, object> BuildHangzhouDetailedConfig() {
        if (_ruleState != XiaoshanRuleBootstrap.RuleId) return null;
        Dictionary<string, object> config = null;
        if (XiaoshanBaotouToggle != null && XiaoshanBaotouToggle.isOn)
            (config ??= new Dictionary<string, object>())["baotou_required"] = true;
        if (XiaoshanNegativeToggle != null && XiaoshanNegativeToggle.isOn)
            (config ??= new Dictionary<string, object>())["allow_negative"] = true;
        if (XiaoshanClaimToggle != null && !XiaoshanClaimToggle.isOn)
            (config ??= new Dictionary<string, object>())["claim_allowed"] = false;
        return config;
    }

    private void CreateHangzhouRoom() {
        var config = new Qingque_Create_RoomConfig {
            Rule=_ruleState, SubRule=GetSelectedSubRule(),
            RoomName=roomNameInput.text.Trim(), GameRound=GetSelectedGameTime(),
            Password=passwordToggle.isOn ? passwordInput.text.Trim() : "",
            RandomSeed=SetRandomSeedToggle.isOn ? randomSeedInput.text.Trim() : "",
            RoundTimer=GetSelectedRoundTimer(), StepTimer=GetSelectedStepTimer(),
            Tips=tipsToggle.isOn, CountTips=countTipsToggle.isOn, PointerTips=pointerTipsToggle.isOn,
            TouristLimit=TouristLimitToggle.isOn, AllowSpectator=AllowSpectatorToggle.isOn,
            ClaimProtection=false, TacticalCall=TacticalCallToggle.isOn, EventId=_venueEventId,
            DetailedConfig=BuildHangzhouDetailedConfig(),
        };
        if (_ruleState == XiaoshanRuleBootstrap.RuleId) {
            // 萧山馆规：开局积分与总倍率封顶
            config.StartingScore = GetXiaoshanStartingScore();
            config.TotalCap = GetXiaoshanTotalCap();
        }
        if (!config.Validate(out string error,passwordToggle.isOn,SetRandomSeedToggle.isOn)) {
            NotificationManager.Instance.ShowTip("create_room",false,error); return;
        }
        RoomNetworkManager.Instance.Create_Hangzhou_Room(config);
    }
}
