using System;
using System.Collections.Generic;
using Newtonsoft.Json;
using UnityEngine;
public partial class RoomNetworkManager {
    public async void Create_Hangzhou_Room(Qingque_Create_RoomConfig config) {
        if (!TryBeginCreateRequest()) return;
        try {
            if (!TryResolveRandomSeed(config.RandomSeed,out string seed,out string error)) {
                CancelPendingRoomEntry(); NotificationManager.Instance.ShowTip("create_room",false,error); return;
            }
            object detailedConfig = config.TotalCap.HasValue
                ? (object)new Dictionary<string, object> {
                    // 萧山馆规：总倍率封顶、开局积分；rule_version 服务端按萧山版本归一
                    ["rule_version"] = HangzhouGameState.RuleVersion,
                    ["total_cap"] = config.TotalCap.Value,
                    ["starting_score"] = config.StartingScore ?? 0,
                }
                : new { rule_version = HangzhouGameState.RuleVersion };
            var request = new {
                type="room/create_Hangzhou_room", rule=config.Rule, sub_rule=config.SubRule,
                roomname=config.RoomName, gameround=config.GameRound, password=config.Password, random_seed=seed,
                roundTimerValue=config.RoundTimer, stepTimerValue=config.StepTimer,
                tips=config.Tips, count_tips=config.CountTips, pointer_tips=config.PointerTips,
                tourist_limit=config.TouristLimit, allow_spectator=config.AllowSpectator,
                tactical_call=config.TacticalCall,
                event_id=string.IsNullOrEmpty(config.EventId) ? null : config.EventId,
                detailed_config=detailedConfig,
            };
            await GetWebSocket().SendText(JsonConvert.SerializeObject(request));
        } catch (Exception e) { CancelPendingRoomEntry(); Debug.LogError($"创建杭州麻将房间失败: {e.Message}"); }
    }
}
