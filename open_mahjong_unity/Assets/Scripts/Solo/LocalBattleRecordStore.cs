using System;
using System.Collections.Generic;
using System.IO;
using UnityEngine;

/// <summary>
/// 单机版本地历史战绩：整局结算（EndGamePanel）时把 4 名玩家的名次/最终分落盘到
/// persistentDataPath 下的 JSON 文件，供 LocalBattleRecordPanel 展示。仅单机版使用。
/// </summary>
public static class LocalBattleRecordStore {
    private const int MaxRecords = 200;
    private static readonly object Lock = new object();

    [Serializable]
    public class PlayerResult {
        public int rank;
        public string name;
        public int score;
        public float pt;
    }

    [Serializable]
    public class BattleRecord {
        public long time;   // Unix 秒（对局结束时间）
        public string rule; // 规则显示名，如「杭州麻将」
        public List<PlayerResult> players = new List<PlayerResult>();
    }

    [Serializable]
    private class RecordFile {
        public List<BattleRecord> records = new List<BattleRecord>();
    }

    private static string FilePath => Path.Combine(Application.persistentDataPath, "local_battle_records.json");

    /// <summary>把整局结算数据（EndGamePanel 的 player_final_data）记录为一条战绩，最新在前。</summary>
    public static void RecordFromEndGame(Dictionary<string, Dictionary<string, object>> playerFinalData, string ruleName) {
        if (playerFinalData == null || playerFinalData.Count == 0) return;
        var record = new BattleRecord {
            time = DateTimeOffset.UtcNow.ToUnixTimeSeconds(),
            rule = string.IsNullOrEmpty(ruleName) ? "麻将" : ruleName,
        };
        foreach (var kv in playerFinalData) {
            var data = kv.Value;
            if (data == null) continue;
            record.players.Add(new PlayerResult {
                rank = ToInt(data, "rank"),
                name = data.TryGetValue("username", out var n) && n != null ? n.ToString() : "?",
                score = ToInt(data, "score"),
                pt = data.TryGetValue("pt", out var p) && p != null ? Convert.ToSingle(p) : 0f,
            });
        }
        record.players.Sort((a, b) => a.rank != b.rank ? a.rank.CompareTo(b.rank) : b.score.CompareTo(a.score));
        lock (Lock) {
            var all = LoadLocked();
            all.Insert(0, record);
            if (all.Count > MaxRecords) all.RemoveRange(MaxRecords, all.Count - MaxRecords);
            SaveLocked(all);
        }
    }

    public static List<BattleRecord> LoadAll() {
        lock (Lock) return LoadLocked();
    }

    public static void ClearAll() {
        lock (Lock) SaveLocked(new List<BattleRecord>());
    }

    private static List<BattleRecord> LoadLocked() {
        try {
            if (!File.Exists(FilePath)) return new List<BattleRecord>();
            var file = JsonUtility.FromJson<RecordFile>(File.ReadAllText(FilePath));
            return file?.records ?? new List<BattleRecord>();
        } catch (Exception e) {
            Debug.LogWarning($"[LocalBattleRecord] 读取战绩失败: {e.Message}");
            return new List<BattleRecord>();
        }
    }

    private static void SaveLocked(List<BattleRecord> records) {
        try {
            File.WriteAllText(FilePath, JsonUtility.ToJson(new RecordFile { records = records }));
        } catch (Exception e) {
            Debug.LogWarning($"[LocalBattleRecord] 保存战绩失败: {e.Message}");
        }
    }

    private static int ToInt(Dictionary<string, object> data, string key) {
        return data.TryGetValue(key, out var v) && v != null ? Convert.ToInt32(v) : 0;
    }
}
