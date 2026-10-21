using UnityEngine;
using UnityEngine.EventSystems;

public sealed class CreateRoomHelpTrigger : MonoBehaviour, IPointerEnterHandler, IPointerDownHandler, ISelectHandler {
    [SerializeField] private CreatePanel owner;
    [SerializeField, TextArea] private string message;

    // 运行时克隆的控件（如萧山 DIY 开关）也要能挂说明，Configure 不能限定编辑器。
    public void Configure(CreatePanel panel, string text) { owner = panel; message = text; }

    internal bool IsOwnedBy(CreatePanel panel) => owner == panel;
    public void OnPointerEnter(PointerEventData eventData) {
        if (owner && eventData.pointerId < 0) owner.ShowRoomHelp(message, this);
    }
    public void OnPointerDown(PointerEventData eventData) {
        if (owner && eventData.button == PointerEventData.InputButton.Left)
            owner.ShowRoomHelp(message, this);
    }
    public void OnSelect(BaseEventData eventData) { if (owner) owner.ShowRoomHelp(message, this); }
}
