"""Changsha Mahjong tenpai check."""
from typing import List, Set

try:
    from .changsha_hepai_check import Changsha_Hepai_Check, VALID_TILES, _expand_all
except ImportError:
    from changsha_hepai_check import Changsha_Hepai_Check, VALID_TILES, _expand_all  # type: ignore


class Changsha_Tingpai_Check:
    def __init__(self):
        self._hepai = Changsha_Hepai_Check()

    def tingpai_check(self, hand_tile_list: List[int], combination_list: List[str]) -> Set[int]:
        waits: Set[int] = set()
        physical = _expand_all(list(hand_tile_list), list(combination_list))
        for tile in VALID_TILES:
            if physical.count(tile) >= 4:
                continue
            score, fan_list = self._hepai.hepai_check(
                list(hand_tile_list) + [tile],
                list(combination_list),
                [],
                tile,
            )
            if score > 0 and fan_list:
                waits.add(tile)
        return waits
