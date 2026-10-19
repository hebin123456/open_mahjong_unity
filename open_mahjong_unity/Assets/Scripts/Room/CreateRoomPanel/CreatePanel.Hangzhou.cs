using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public partial class CreatePanel {
    private Toggle XiaoshanBaotouToggle;
    private Toggle XiaoshanNegativeToggle;

    /// <summary>运行时克隆萧山专属「有财神必须爆头」开关，避免场景内重复手工挂接。</summary>
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
    }

    /// <summary>
    /// 萧山馆规开关随建房请求下发。「积分小于 0 继续」：输家可扣成负分且不触发
    /// 清零终局；两者都关闭时不携带，服务端按默认关闭处理。
    /// </summary>
    private Dictionary<string, object> BuildHangzhouDetailedConfig() {
        if (_ruleState != XiaoshanRuleBootstrap.RuleId) return null;
        Dictionary<string, object> config = null;
        if (XiaoshanBaotouToggle != null && XiaoshanBaotouToggle.isOn)
            (config ??= new Dictionary<string, object>())["baotou_required"] = true;
        if (XiaoshanNegativeToggle != null && XiaoshanNegativeToggle.isOn)
            (config ??= new Dictionary<string, object>())["allow_negative"] = true;
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
