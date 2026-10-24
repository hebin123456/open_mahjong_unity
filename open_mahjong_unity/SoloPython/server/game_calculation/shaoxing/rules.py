"""绍兴麻将（144 张花麻）规则。

依据同城游绍兴麻将公开规则整理：

* 144 张：万/筒/索各 36 张、东南西北各 4 张，中发白各 4 张与
  春夏秋冬梅兰竹菊各 1 张合计 20 张花牌。
* 花牌抓到后明放并补一张；花牌、财神、风刻/风杠、普通杠分别计花。
* 财神为翻牌加一（九转一、风牌循环）；翻到花牌则定东风为财神。
  财神可代任意非花牌，不可吃碰杠；白板是花牌，不顶替财神。
* 只能自摸；爆头、杠开、杠爆、财飘、大吊车、抢杠等按番计，
  常规番数三番到底，天胡/地胡四番。
* 计分：总花数 × 2^番数；吃碰同一家三次形成承包（3 倍），
  反承包（6 倍）。

本模块只做纯规则计算，牌墙、补花和动作流程由引擎负责。
"""

from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache

from ..hangzhou.rules import can_baotou as _hz_can_baotou
from ..hangzhou.rules import waiting_tiles as _hz_waiting_tiles
from ..jokers import JokerPolicy, iter_winning_shapes

RULE_VERSION = "shaoxing-house-2026-v2"
SUB_RULE = "shaoxing/shaoxing"

NUMBERS = tuple(s * 10 + n for s in (1, 2, 3) for n in range(1, 10))
WINDS = (41, 42, 43, 44)
DRAGON_FLOWERS = (45, 46, 47)
BONUS_FLOWERS = tuple(range(51, 59))
FLOWER_TILES = DRAGON_FLOWERS + BONUS_FLOWERS
NORMAL_TILES = NUMBERS + WINDS
TILES = NORMAL_TILES

FAN_CAP = 3
BASE_FLOWERS = 5
CLAIM_THRESHOLD = 3


def normalize_config(raw=None):
    """绍兴花麻只保留规则版本；不沿用萧山/杭州的馆规倍率开关。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"rule_version"}:
        raise ValueError("绍兴麻将没有其它馆规选项")
    if raw.get("rule_version", RULE_VERSION) not in (RULE_VERSION, "shaoxing-house-2026-v1"):
        raise ValueError("不支持的绍兴麻将规则版本")
    return {"rule_version": RULE_VERSION}


def is_flower(tile):
    return tile in FLOWER_TILES


def next_joker(indicator):
    """翻牌加一；翻到花牌时按绍兴规则定东风为财神。"""
    if type(indicator) is not int:
        raise ValueError("翻牌无效")
    if indicator in FLOWER_TILES:
        return 41
    if indicator in NUMBERS:
        return indicator + 1 if indicator % 10 < 9 else indicator - 8
    if indicator in WINDS:
        return 41 + (indicator - 41 + 1) % 4
    raise ValueError("翻牌不在绍兴牌组中")


def deck():
    """完整 144 张实体牌：中发白各四张、八花各一张。"""
    return ([t for t in NORMAL_TILES for _ in range(4)]
            + [t for t in DRAGON_FLOWERS for _ in range(4)]
            + list(BONUS_FLOWERS))


def _meld_frame(value):
    if isinstance(value, tuple):
        code, physical = value[0], tuple(value[1]) if len(value) > 1 and value[1] is not None else None
    else:
        code, physical = value, None
    if not isinstance(code, str) or len(code) != 3 or code[0] not in "skgG":
        raise ValueError("非法面子")
    try:
        tile = int(code[1:])
    except ValueError as exc:
        raise ValueError("面子牌张无效") from exc
    if code[0] == "s":
        if tile not in NUMBERS or not 2 <= tile % 10 <= 8:
            raise ValueError("非法顺子")
        expected = (tile - 1, tile, tile + 1)
    else:
        if tile not in NORMAL_TILES:
            raise ValueError("面子牌张无效")
        expected = (tile,) * (3 if code[0] == "k" else 4)
    return code, expected, (physical if physical is not None else expected)


def _melds(melds):
    groups = []
    for value in melds:
        code, expected, physical = _meld_frame(value)
        if len(physical) != len(expected) or any(t not in NORMAL_TILES for t in physical):
            raise ValueError("副露实体牌无效")
        groups.append((code, expected, physical))
    return tuple(groups)


def _valid(hand, melds, joker, *, complete):
    if not isinstance(hand, (list, tuple)) or not isinstance(melds, (list, tuple)):
        return None
    if len(melds) > 4 or len(hand) + 3 * len(melds) != (14 if complete else 13):
        return None
    if any(type(t) is not int or t not in NORMAL_TILES for t in hand):
        return None
    try:
        groups = _melds(melds)
    except ValueError:
        return None
    physical = list(hand)
    for _, _, tiles in groups:
        if joker in tiles:
            return None
        physical.extend(tiles)
    counts = Counter(physical)
    if any(n > 4 for n in counts.values()):
        return None
    if counts[joker] > 4:
        return None
    return groups


@lru_cache(maxsize=8)
def _policy(joker):
    return JokerPolicy(joker_tiles=(joker,), meld_count=4,
                       shapes=("standard", "seven_pairs"))


def waiting_tiles(hand, melds=(), joker=41):
    if _valid(hand, melds, joker, complete=False) is None:
        return set()
    return set(_hz_waiting_tiles(hand, melds, jokers=(joker,)))


@dataclass(frozen=True)
class Fan:
    id: str
    name: str
    fan: int


@dataclass(frozen=True)
class WinContext:
    self_drawn: bool = True
    after_kong: bool = False
    rob_kong: bool = False
    tian_hu: bool = False
    di_hu: bool = False
    cai_piao_count: int = 0
    pre_draw_tiles: tuple | None = None


@dataclass(frozen=True)
class WinScore:
    shape: str
    fans: tuple[Fan, ...]
    flower_tiles: tuple[int, ...]
    natural_flowers: int
    base_flowers: int = BASE_FLOWERS
    no_flower_bonus: int = 0
    fan_total: int = 0
    decomposition: dict | None = None

    @property
    def raw_fan(self):
        return sum(item.fan for item in self.fans)

    @property
    def total_flowers(self):
        return self.base_flowers + self.natural_flowers + self.no_flower_bonus

    @property
    def multiplier(self):
        return 1 << self.fan_total

    @property
    def points(self):
        return self.total_flowers * self.multiplier

    @property
    def fan_ids(self):
        return tuple(f"SX|{item.id}|{item.fan}|{item.name}" for item in self.fans)

    @property
    def fan_names(self):
        names = [f"底花×{self.base_flowers}"]
        if self.flower_tiles:
            names.append(f"花牌{len(self.flower_tiles)}张")
        if self.no_flower_bonus:
            names.append(f"无花无财神+{self.no_flower_bonus}花")
        names.extend(f"{item.name}（{item.fan}番）" for item in self.fans)
        names.append(f"总花{self.total_flowers}×{self.multiplier}={self.points}")
        return names

    def as_dict(self):
        return {**asdict(self), "rule_version": RULE_VERSION,
                "raw_fan": self.raw_fan, "total_flowers": self.total_flowers,
                "multiplier": self.multiplier, "points": self.points,
                "fan_cap": FAN_CAP}


def _meld_flower_points(groups):
    """风刻各 1 花；风明杠 2/暗杠 3；普通明杠 1/暗杠 2。"""
    points = 0
    has_wind_pung = False
    for code, expected, _ in groups:
        tile = expected[0]
        if code[0] == "s":
            continue
        wind = tile in WINDS
        if code[0] == "k":
            if wind:
                points += 1
                has_wind_pung = True
            continue
        concealed = code[0] == "G"
        if wind:
            points += 3 if concealed else 2
            has_wind_pung = True
        else:
            points += 2 if concealed else 1
    return points, has_wind_pung


def _wait_bonus(pre_draw, melds, win_tile, joker):
    """大吊车：四组已副露、手里只剩一张且该张是唯一和牌张。"""
    if len(melds) != 4 or len(pre_draw) != 1:
        return False
    waits = set(_hz_waiting_tiles(pre_draw, melds, jokers=(joker,)))
    waits.discard(joker)
    return waits == {win_tile}


def _shape_flower_points(shape, joker):
    """手中暗刻按风牌 1 花；暗杠不拆手牌，按暗刻计。"""
    points = 0
    for group in shape.groups:
        if group.external or group.kind != "triplet":
            continue
        if group.tile in WINDS:
            points += 1
    return points


def _score_shape(shape, hand, melds, win_tile, context, joker, flower_tiles):
    groups = _melds(melds)
    meld_points, wind_pung = _meld_flower_points(groups)
    hand_wind_pung = any(g.kind == "triplet" and g.tile in WINDS and not g.external
                         for g in shape.groups)
    wind_pung = wind_pung or hand_wind_pung
    natural = len(flower_tiles) + hand.count(joker) + meld_points + _shape_flower_points(shape, joker)
    no_flower_bonus = 20 if (not flower_tiles and joker not in hand) else 0

    fans = []
    if shape.logical_tiles and all(t in WINDS for t in shape.logical_tiles):
        fans.append(Fan("quan_feng", "全风", 1))
    if _hz_can_baotou(context.pre_draw_tiles if context.pre_draw_tiles is not None
                      else tuple(hand[:-1]), melds, jokers=(joker,)):
        fans.append(Fan("baotou", "爆头", 1))
    if context.after_kong:
        fans.append(Fan("kong_draw", "杠开", 1))
    if context.rob_kong:
        fans.append(Fan("rob_kong", "抢杠", 1))
    if _wait_bonus(context.pre_draw_tiles if context.pre_draw_tiles is not None
                   else tuple(hand[:-1]), melds, win_tile, joker):
        fans.append(Fan("diao_che", "大吊车", 1))
    if context.cai_piao_count:
        fans.append(Fan("cai_piao", "财飘", 1))
    if context.tian_hu:
        fans.append(Fan("tian_hu", "天胡", 4))
    if context.di_hu:
        fans.append(Fan("di_hu", "地胡", 4))
    if not fans:
        fans.append(Fan("plain", "平胡", 0))

    # 平胡至少两花起和；爆头/杠开等特殊牌型与无花无财神不受此限。
    if (not any(item.id not in ("plain",) for item in fans)
            and natural < 2 and not no_flower_bonus):
        return None

    if context.tian_hu or context.di_hu:
        fan_total = 4
    else:
        fan_total = min(FAN_CAP, sum(item.fan for item in fans))
    return WinScore(shape.kind, tuple(fans), tuple(flower_tiles), natural,
                    no_flower_bonus=no_flower_bonus, fan_total=fan_total,
                    decomposition={**asdict(shape), "physical_tiles": tuple(hand),
                                   "logical_tiles": shape.logical_tiles,
                                   "winning_logical_tile": shape.winning_logical_tile})


def evaluate_win(hand, melds=(), win_tile=None, context=None, *, joker,
                 flower_tiles=()):
    """返回最佳合法绍兴自摸/抢杠计分；不合法返回 None。"""
    groups = _valid(hand, melds, joker, complete=True)
    if groups is None:
        return None
    if context is None:
        context = WinContext()
    if not isinstance(context, WinContext):
        raise ValueError("和牌上下文必须来自权威摸牌记录")
    if not (context.self_drawn or context.rob_kong):
        return None
    if win_tile is None:
        win_tile = hand[-1]
    if type(win_tile) is not int or win_tile not in hand:
        return None
    if context.pre_draw_tiles is not None:
        pre = tuple(context.pre_draw_tiles)
        if (_valid(pre, melds, joker, complete=False) is None
                or Counter(hand) != Counter(pre) + Counter({win_tile: 1})):
            return None
    shapes = list(iter_winning_shapes(hand, melds, _policy(joker),
                                      winning_tile=win_tile,
                                      equivalence="logical_structure"))
    best = None
    for shape in shapes:
        score = _score_shape(shape, hand, melds, win_tile, context, joker, flower_tiles)
        if score is not None and (best is None or score.points > best.points):
            best = score
    return best


@dataclass(frozen=True)
class Transfer:
    payer: int
    winner: int
    points: int
    reason: str


@dataclass(frozen=True)
class Settlement:
    changes: tuple
    transfers: tuple
    normal_total: int
    dealer_multiplier: int = 1
    base_points: int = 0

    def as_dict(self):
        return asdict(self)


def settle_win(score, winner, dealer, claim_from=None):
    """总花数×番数结算；同一人吃碰三次承包 3 倍、反承包 6 倍。"""
    if not isinstance(score, WinScore):
        raise ValueError("缺少合法绍兴和牌计分")
    for value, name in ((winner, "和家"), (dealer, "庄家")):
        if type(value) is not int or value not in range(4):
            raise ValueError(f"非法{name}")
    points = score.points
    claim_from = claim_from or [[0] * 4 for _ in range(4)]
    transfers = []
    for payer in range(4):
        if payer == winner:
            continue
        if claim_from[winner][payer] >= CLAIM_THRESHOLD:
            amount, reason = 3 * points, "承包"
        elif claim_from[payer][winner] >= CLAIM_THRESHOLD:
            amount, reason = 6 * points, "反承包"
        else:
            amount, reason = points, "自摸"
        transfers.append(Transfer(payer, winner, amount, reason))
    changes = [0] * 4
    for transfer in transfers:
        changes[transfer.payer] -= transfer.points
        changes[transfer.winner] += transfer.points
    return Settlement(tuple(changes), tuple(transfers), points, 1, points)
