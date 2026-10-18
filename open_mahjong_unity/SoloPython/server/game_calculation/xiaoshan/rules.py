"""萧山麻将（杭州麻将拓展·家庭房规 v1.3）计分。

与杭州最大的区别：杭州按「番」相加后取 ``2**番``，萧山按番种「倍率」逐项
相乘，再按协商好的总倍率封顶截断（默认 32 倍）。牌型校验与财神求解完全复用
``..hangzhou.rules`` 的参数化实现（其所有入口均已接受 ``jokers`` 集合），
因此本模块只负责：番种判定、倍率相乘、承包/天胡/地胡结算。
"""

from collections import Counter
from dataclasses import asdict, dataclass, replace

from ..hangzhou.rules import (HONORS, JOKER, WinContext, _luxury_pairs, _policy,
                              _valid, can_baotou, ten_winds_eligible)

RULE_VERSION = "xiaoshan-house-2025-v1"
SUB_RULE = "xiaoshan/xiaoshan"
DEFAULT_TOTAL_CAP = 32
DEFAULT_DEALER_CAP = 3          # 2**3 = 8 倍连庄封顶
BASE_SCORE = 1
CLAIM_THRESHOLD = 3             # 累计吃碰三次形成承包关系


def normalize_config(raw=None):
    """萧山房规可协商总倍率封顶；其余沿用杭州引擎的配置形状。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"rule_version", "total_cap"}:
        raise ValueError("萧山麻将没有其它馆规选项")
    version = raw.get("rule_version", RULE_VERSION)
    if version not in (RULE_VERSION, "mil-hangzhou-2025-om1"):
        raise ValueError("不支持的萧山麻将规则版本")
    cap = raw.get("total_cap", DEFAULT_TOTAL_CAP)
    if cap not in (32, 64):
        raise ValueError("总倍率封顶只能为 32 或 64")
    return {"rule_version": RULE_VERSION, "total_cap": cap}


@dataclass(frozen=True)
class XFan:
    id: str
    name: str
    multiplier: int


@dataclass(frozen=True)
class XWinScore:
    shape: str
    fans: tuple
    total_cap: int = DEFAULT_TOTAL_CAP
    decomposition: dict | None = None

    @property
    def total_multiplier(self):
        value = 1
        for fan in self.fans:
            value *= max(1, fan.multiplier)
        return min(self.total_cap, value)

    @property
    def points(self):
        return BASE_SCORE * self.total_multiplier

    @property
    def fan_ids(self):
        return tuple(f"HZ|{fan.id}|{fan.multiplier}|{fan.name}" for fan in self.fans)

    @property
    def fan_names(self):
        return [f"{fan.name}（×{fan.multiplier}）" for fan in self.fans]

    def as_dict(self):
        return {**asdict(self), "rule_version": RULE_VERSION,
                "total_multiplier": self.total_multiplier, "points": self.points,
                "fan_cap": self.total_cap}


def _numeral_suit(tile):
    return tile // 10 if tile < 40 else 0


def _decomposition(shape, hand):
    return {**asdict(shape), "physical_tiles": tuple(hand),
            "logical_tiles": shape.logical_tiles,
            "closed_logical_tiles": shape.closed_logical_tiles,
            "winning_logical_tile": shape.winning_logical_tile}


def _shape_fans(shape, hand, melds, context, jokers, pre_draw):
    """同一副合法拆解下的全部番种；倍率逐项相乘，不做加法封顶。"""
    fans = []
    logical = shape.logical_tiles
    if shape.kind == "seven_pairs":
        luxury, _ = _luxury_pairs(hand, jokers)
        fans.append(XFan("luxury_seven_pairs", "豪华七对", 16) if luxury
                    else XFan("seven_pairs", "七对子", 4))
    else:
        non_pair = [g for g in shape.groups if g.kind != "pair"]
        if len(melds) == 4 and all(not str(code).startswith("G") for code in melds):
            fans.append(XFan("quandao", "全倒", 2))
        if non_pair and all(g.kind in ("triplet", "kong") for g in non_pair):
            fans.append(XFan("pengpeng", "碰碰胡", 2))
    suits = {_numeral_suit(t) for t in logical}
    if len(suits) == 1 and next(iter(suits)) in (1, 2, 3):
        fans.append(XFan("qingyise", "清一色", 8))
    elif logical and all(t in HONORS for t in logical):
        fans.append(XFan("ziyise", "字一色", 16))
    if can_baotou(pre_draw, melds, jokers):
        fans.append(XFan("baotou", "爆头", 2))
        for index in range(context.cai_piao_count):
            label = "财飘" if context.cai_piao_count == 1 else f"财飘×{index + 1}"
            fans.append(XFan("cai_piao", label, 2))
    if context.after_kong:
        fans.append(XFan("kong_draw", "杠开", 2))
    if not any(t in jokers for t in hand):
        fans.append(XFan("no_joker", "无财神", 2))
    if not fans:
        fans.append(XFan("plain", "平和", 1))
    return tuple(fans)


def evaluate_win(hand, melds=(), win_tile=None, context=None, jokers=(JOKER,),
                 total_cap=DEFAULT_TOTAL_CAP):
    """最优合法自摸：枚举全部逻辑拆解，取倍率相乘后最大者。"""
    jokers = tuple(jokers)
    if _valid(hand, melds, True, jokers) is None:
        return None
    if context is None:
        context = WinContext()
    if not isinstance(context, WinContext):
        raise ValueError("和牌上下文必须来自权威摸牌记录")
    if not context.self_drawn:
        return None
    if context.after_kong and not any(code[0] in "gG" for code in melds):
        return None
    if win_tile is None:
        win_tile = hand[-1]
    if type(win_tile) is not int or win_tile not in hand:
        return None
    pre_draw = list(hand)
    pre_draw.remove(win_tile)
    if context.pre_draw_tiles is not None:
        if (_valid(context.pre_draw_tiles, melds, False, jokers) is None
                or Counter(pre_draw) != Counter(context.pre_draw_tiles)):
            return None
        pre_draw = list(context.pre_draw_tiles)
    from ..jokers import iter_winning_shapes
    policy = _policy(("standard", "seven_pairs"), jokers)
    best = None
    for shape in iter_winning_shapes(hand, melds, policy, winning_tile=win_tile,
                                     equivalence="logical_structure"):
        score = XWinScore(shape.kind,
                          _shape_fans(shape, hand, melds, context, jokers, pre_draw),
                          total_cap, _decomposition(shape, hand))
        if best is None or score.total_multiplier > best.total_multiplier:
            best = score
    return best


def score_ten_winds(hand, discard_history, melds=(), *, had_meld_action=False,
                    jokers=(JOKER,), total_cap=DEFAULT_TOTAL_CAP):
    """十风：×8；十张弃牌中含财神（白板或翻出的财神）计 ×16。"""
    jokers = tuple(jokers)
    if not ten_winds_eligible(hand, discard_history, melds,
                              had_meld_action=had_meld_action, jokers=jokers):
        return None
    multiplier = 16 if any(t in jokers for t in discard_history) else 8
    fans = [XFan("ten_winds", "十风", multiplier)]
    if not any(t in jokers for t in hand):
        fans.append(XFan("no_joker", "无财神", 2))
    return XWinScore("ten_winds", tuple(fans), total_cap)


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
    dealer_multiplier: int
    base_points: int = 0

    def as_dict(self):
        return asdict(self)


def _validate_seats(winner, dealer):
    for value, name in ((winner, "和家"), (dealer, "庄家")):
        if type(value) is not int or value not in range(4):
            raise ValueError(f"非法{name}")


def settle_win(score, winner, dealer, dealer_streak, claim_from=None, *,
               base_score=BASE_SCORE, total_cap=DEFAULT_TOTAL_CAP,
               dealer_cap=DEFAULT_DEALER_CAP, tian_hu=False, di_hu=False,
               current_scores=None):
    """萧山结算：连庄倍数 + 承包关系；天胡通杀、地胡按封顶结算。"""
    if not isinstance(score, XWinScore):
        raise ValueError("缺少合法和牌计分")
    _validate_seats(winner, dealer)
    if type(dealer_streak) is not int or not 1 <= dealer_streak <= dealer_cap:
        raise ValueError("老庄数超出封顶")
    multiplier = 2 ** dealer_streak
    claim_from = claim_from or [[0] * 4 for _ in range(4)]

    if tian_hu:
        scores = list(current_scores or [0] * 4)
        changes = [0] * 4
        transfers = []
        for seat in range(4):
            if seat == winner:
                continue
            amount = max(0, int(scores[seat]))
            if amount:
                changes[seat] -= amount
                changes[winner] += amount
                transfers.append(Transfer(seat, winner, amount, "天胡通杀"))
        return Settlement(tuple(changes), tuple(transfers),
                          sum(item.points for item in transfers), multiplier,
                          sum(item.points for item in transfers))

    points = base_score * total_cap if di_hu else score.points
    normal = {seat: points * (multiplier if winner == dealer or seat == dealer else 1)
              for seat in range(4) if seat != winner}
    total = sum(normal.values())
    transfers = []
    for provider in range(4):
        if provider != winner and claim_from[winner][provider] >= CLAIM_THRESHOLD:
            transfers.append(Transfer(provider, winner, total, "承包"))
    for claimer in range(4):
        if claimer != winner and claim_from[claimer][winner] >= CLAIM_THRESHOLD:
            transfers.append(Transfer(claimer, winner, 2 * total, "反承包"))
    if not transfers:
        transfers = [Transfer(seat, winner, amount, "自摸")
                     for seat, amount in normal.items()]
    changes = [0] * 4
    for transfer in transfers:
        changes[transfer.payer] -= transfer.points
        changes[winner] += transfer.points
    return Settlement(tuple(changes), tuple(transfers), total, multiplier, points)
