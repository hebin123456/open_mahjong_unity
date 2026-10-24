using UnityEngine;

/// <summary>
/// 《大富翁》角色目录。头像资源：Resources/image/Profiles/3~16（256×256 方形）。
/// 玩家在单机版自选角色（存 <see cref="ConfigManager.PlayerCharacterId"/>），
/// 机器人按 user_id 档位映射；局内头像由 GameCanvas 覆写 profile_used 生效。
/// </summary>
public static class CharacterCatalog {
    public struct Entry {
        public int Id;
        public string Name;
        public string English;
        public string Tagline;

        public Entry(int id, string name, string english, string tagline) {
            Id = id;
            Name = name;
            English = english;
            Tagline = tagline;
        }
    }

    public const int MinId = 3;
    public const int MaxId = 16;
    /// <summary>默认角色：孙小美（系列看板娘）。</summary>
    public const int DefaultId = 4;

    public static readonly Entry[] Entries = {
        new Entry(3,  "阿土伯",   "Uncle Tuu",       "乐天老农，初代主角"),
        new Entry(4,  "孙小美",   "Sun Hsiao Mei",   "杂技团台柱，系列看板娘"),
        new Entry(5,  "钱夫人",   "Madame Chayn",    "珠光宝气的拜金公关经理"),
        new Entry(6,  "沙隆巴斯", "Salonbus",        "中东石油大亨"),
        new Entry(7,  "金贝贝",   "Baby Gin",        "巨婴老板，运气流"),
        new Entry(8,  "忍太郎",   "Jintaro",         "伊贺流忍者"),
        new Entry(9,  "大老千",   "Da Lao Qian",     "戴墨镜的职业赌徒"),
        new Entry(10, "乌咪",     "Umi",             "印第安小女孩"),
        new Entry(11, "糖糖",     "Tang Tang",       "魔法少女"),
        new Entry(12, "宫本宝藏", "Miyamoto Takara", "日本武士"),
        new Entry(13, "舞美拉",   "Mira",            "明星舞者"),
        new Entry(14, "小丹尼",   "Daniel",          "金发小男孩"),
        new Entry(15, "小忍",     "Shinobu",         "忍者少女"),
        new Entry(16, "阿土仔",   "Uncle Tuu Jr.",   "阿土伯的儿子"),
    };

    public static bool IsValid(int id) {
        return id >= MinId && id <= MaxId;
    }

    public static Entry Get(int id) {
        for (int i = 0; i < Entries.Length; i++) {
            if (Entries[i].Id == id) return Entries[i];
        }
        return Entries[DefaultId - MinId];
    }

    public static string GetName(int id) {
        return IsValid(id) ? Get(id).Name : Get(DefaultId).Name;
    }

    public static Sprite GetSprite(int id) {
        return ConfigManager.GetProfileSprite(IsValid(id) ? id : DefaultId);
    }

    /// <summary>
    /// 机器人按 user_id 档位取角色：0/1 摸切→阿土伯，2 低级牌效→阿土仔，
    /// 3 高性能/高级→钱夫人，其它→孙小美。
    /// </summary>
    public static int ForBot(int botUserId) {
        switch (botUserId) {
            case 0:
            case 1:
                return 3;
            case 2:
                return 16;
            case 3:
            case 4:
                return 5;
            default:
                return DefaultId;
        }
    }

    /// <summary>
    /// 按 user_id 取头像 id：单机版真人是 101 起、机器人是 1~4，
    /// 所以 <=10 走机器人档位映射，其余用玩家本地自选角色。
    /// </summary>
    public static int ResolveProfileId(int userId) {
        return userId > 0 && userId <= 10 ? ForBot(userId) : ConfigManager.PlayerCharacterId;
    }
}
