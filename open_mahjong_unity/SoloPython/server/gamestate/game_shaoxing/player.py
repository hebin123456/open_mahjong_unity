from dataclasses import dataclass, field

from ..game_hangzhou.player import HangzhouPlayer


@dataclass
class ShaoxingPlayer(HangzhouPlayer):
    huapai_list: list[int] = field(default_factory=list)

    def reset_for_round(self, round_time):
        super().reset_for_round(round_time)
        self.huapai_list = []
