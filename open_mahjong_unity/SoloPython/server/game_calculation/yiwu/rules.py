"""义乌麻将规则。

义乌麻将 136 张、十三张手牌、无花。开局翻一张牌，翻出的牌本身为财神
（场上同种仅剩三张），白板顶替财神本牌；财神不可吃碰杠，打出财神后
其他人不能和这张财神。

牌型：平胡、七对子、十三不靠（十三烂）、财神头（四组 + 单张财神，
任意进张成对即和）。两个财神必须自摸；三个财神只能财神头或杠开，
不能平胡、自摸、抢杠。单飘、双飘分别是打出 1/2 张财神后的财神头。

计分：闲家基础 5、庄家基础 7；自摸 ×2、财神头/杠开 ×4、
单飘 ×8、双飘 ×16。买子时基础分 +2。
"""

from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache

from ..xiaoshan import rules as xs

RULE_VERSION = "yiwu-house-2026-v2"
SUB_RULE = "yiwu/yiwu"

NUMBERS = tuple(s * 10 + n for s in (1, 2, 3) for n in range(1, 10))
HONORS = tuple(range(41, 48))
TILES = NUMBERS + HONORS
WHITE = 46
BASE_DEALER = 7
BASE_PLAYER = 5
BUY_IN_BONUS = 2


def normalize_config(raw=None):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"rule_version", "buy_in"}:
        raise ValueError("义乌麻将没有其它馆规选项")
    if raw.get("rule_version", RULE_VERSION) not in (RULE_VERSION, "yiwu-house-2026-v1"):
        raise ValueError("不支持的义乌麻将规则版本")
    buy_in = raw.get("buy_in", False)
    if not isinstance(buy_in, bool):
        raise ValueError("买子开关只能是开或关")
    return {"rule_version": RULE_VERSION, "buy_in": buy_in}


def deck():
    return [t for t in TILES for _ in range(4)]


def waiting_tiles(hand, melds=(), joker=WHITE):
    if len(hand) + 3 * len(melds) != 13:
        return set()
    return set(xs.waiting_tiles(hand, melds, joker))


def _shisan_bukao(hand, joker):
    """十三不靠：14 张互不相同、同花色数字不相邻（含隔一张）；财神可补缺。"""
    wild = hand.count(joker)
    logical = [joker if t == WHITE else t for t in hand if t != joker]
    if len(set(logical)) != len(logical):
        return False
    if len(logical) + wild != 14:
        return False
    by_suit = {}
    for tile in logical:
        if tile in NUMBERS:
            by_suit.setdefault(tile // 10, []).append(tile % 10)
    for ranks in by_suit.values():
        ranks.sort()
        if any(b - a < 3 for a, b in zip(ranks, ranks[1:])):
            return False
    return True


@dataclass(frozen=True)
class Fan:
    id: str
    name: str
    multiplier: int


@dataclass(frozen=True)
class WinContext:
    self_drawn: bool = True
    after_kong: bool = False
    rob_kong: bool = False
    cai_piao_count: int = 0
    pre_draw_tiles: tuple | None = None


@dataclass(frozen=True)
class WinScore:
    shape: str
    fans: tuple[Fan, ...]
    base_score: int
    multiplier: int
    piao_count: int = 0
    decomposition: dict | None = None

    @property
    def points(self):
        return self.base_score * self.multiplier

    @property
    def fan_ids(self):
        return tuple(f"YW|{item.id}|{item.multiplier}|{item.name}" for item in self.fans)

    @property
    def fan_names(self):
        return ([f"底分{self.base_score}"]
                + [f"{item.name}×{item.multiplier}" for item in self.fans]
                + [f"{self.base_score}×{self.multiplier}={self.points}"])

    def as_dict(self):
        return {**asdict(self), "rule_version": RULE_VERSION,
                "points": self.points}


def evaluate_win(hand, melds=(), win_tile=None, context=None, *, joker,
                 dealer=0, winner=0, buy_in=False):
    """返回最佳合法义乌和牌计分；不合法返回 None。"""
    external = xs._valid(hand, melds, joker, complete=True)
    if external is None:
        return None
    if context is None:
        context = WinContext()
    if not isinstance(context, WinContext):
        raise ValueError("和牌上下文必须来自权威摸牌记录")
    if win_tile is None:
        win_tile = hand[-1]
    if type(win_tile) is not int or win_tile not in hand:
        return None
    pre = list(hand)
    pre.remove(win_tile)
    if context.pre_draw_tiles is not None and Counter(context.pre_draw_tiles) != Counter(pre):
        return None

    joker_count = hand.count(joker)
    baotou = xs.can_baotou(pre, melds, joker)
    if joker_count >= 2 and not context.self_drawn:
        return None
    if joker_count >= 3 and not (baotou or context.after_kong):
        return None
    if context.rob_kong and joker_count >= 3:
        return None

    shapes = list(xs.iter_winning_shapes(
        hand, external, xs._policy(joker), winning_tile=win_tile,
        equivalence="logical_structure"))
    shape = next(iter(shapes), None)
    shisan = not melds and _shisan_bukao(hand, joker)
    if shape is None and not shisan:
        return None

    if context.cai_piao_count and baotou:
        multiplier = 16 if context.cai_piao_count >= 2 else 8
        fan = Fan("double_piao" if context.cai_piao_count >= 2 else "single_piao",
                  "双飘" if context.cai_piao_count >= 2 else "单飘", multiplier)
    elif baotou:
        multiplier = 4
        fan = Fan("caishen_head", "财神头", 4)
    elif context.after_kong:
        multiplier = 4
        fan = Fan("kong_draw", "杠开", 4)
    elif context.self_drawn:
        multiplier = 2
        fan = Fan("self_draw", "自摸", 2)
    elif context.rob_kong:
        multiplier = 2
        fan = Fan("rob_kong", "抢杠", 2)
    else:
        multiplier = 1
        fan = Fan("ping_hu", "平胡", 1)

    if shisan:
        fan = Fan("shisan_bukao", "十三不靠", multiplier)
    elif shape is not None and shape.kind == "seven_pairs":
        fan = Fan("seven_pairs", "七对子", multiplier)

    base = (BASE_DEALER if winner == dealer else BASE_PLAYER) + (BUY_IN_BONUS if buy_in else 0)
    decomposition = None
    if shape is not None:
        decomposition = {**asdict(shape), "physical_tiles": tuple(hand),
                         "logical_tiles": shape.logical_tiles,
                         "winning_logical_tile": shape.winning_logical_tile}
    else:
        decomposition = {"kind": "shisan_bukao", "physical_tiles": tuple(hand),
                         "winning_tile": win_tile}
    return WinScore("shisan_bukao" if shisan else shape.kind,
                    (fan,), base, multiplier,
                    context.cai_piao_count, decomposition)


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


def settle_win(score, winner, dealer, *, buy_in=False, piao_counts=None):
    """按闲 5 / 庄 7 的基础分乘和牌倍数；飘未成按财神头赔给和家。"""
    if not isinstance(score, WinScore):
        raise ValueError("缺少合法义乌和牌计分")
    for value, name in ((winner, "和家"), (dealer, "庄家")):
        if type(value) is not int or value not in range(4):
            raise ValueError(f"非法{name}")
    piao_counts = piao_counts or [0] * 4
    changes = [0] * 4
    transfers = []
    for payer in range(4):
        if payer == winner:
            continue
        points = score.base_score * score.multiplier
        transfers.append(Transfer(payer, winner, points, "自摸" if score.multiplier >= 2 else "点炮"))
        changes[payer] -= points
        changes[winner] += points
    for payer in range(4):
        if payer == winner or not piao_counts[payer]:
            continue
        base = (BASE_DEALER if payer == dealer else BASE_PLAYER) + (BUY_IN_BONUS if buy_in else 0)
        points = base * 4
        transfers.append(Transfer(payer, winner, points, "飘未成"))
        changes[payer] -= points
        changes[winner] += points
    return Settlement(tuple(changes), tuple(transfers),
                      sum(t.points for t in transfers), 1, score.points)
