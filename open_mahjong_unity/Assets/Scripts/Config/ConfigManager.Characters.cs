using UnityEngine;

public partial class ConfigManager {
    private const string KEY_PLAYER_CHARACTER = "PlayerCharacterId";

    /// <summary>
    /// 单机版本地保存的玩家角色 id，对应 Resources/image/Profiles/{id}。
    /// 联机版走服务器装扮（InventoryAppearance），这里只在单机生效。
    /// </summary>
    public static int PlayerCharacterId {
        get {
            int id = PlayerPrefs.GetInt(KEY_PLAYER_CHARACTER, CharacterCatalog.DefaultId);
            return CharacterCatalog.IsValid(id) ? id : CharacterCatalog.DefaultId;
        }
        set {
            int id = CharacterCatalog.IsValid(value) ? value : CharacterCatalog.DefaultId;
            PlayerPrefs.SetInt(KEY_PLAYER_CHARACTER, id);
            PlayerPrefs.Save();
        }
    }

    /// <summary>切换角色：写本地配置并刷新当前用户头像。</summary>
    public static void SetPlayerCharacter(int id) {
        PlayerCharacterId = id;
        if (UserDataManager.Instance != null) {
            UserDataManager.Instance.ApplyLocalCharacter(PlayerCharacterId);
        }
    }
}
