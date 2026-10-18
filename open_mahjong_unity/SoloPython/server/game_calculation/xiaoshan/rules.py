"""萧山麻将（杭州麻将拓展·家庭房规 v1.3）计分。

与杭州最大的区别：杭州按「番」相加后取 ``2**番``，萧山按番种「倍率」逐项
相乘，再按协商好的总倍率封顶截断（默认 32 倍）。

财神语义（翻财）：四家抓完后翻出牌墙所剩的那张牌为财神，场上同种财神只剩
3 张；只有这张牌本身是万能牌，白板不是万能牌，而是固定顶替该牌的「本牌」
（如翻出一万，白板当一万用，可与二万三万成顺子）；翻出白板时白板即财神。
财神实体不可吃碰杠、也不进副露；白板按其本牌身份可正常吃碰杠。
牌型校验由共用 ``..jokers`` 求解器以 ``natural_map`` 精确完成，本模块只负责：
副露实体帧解析、番种判定、倍率相乘、承包/天胡/地胡结算。
"""

from collections import Counter
from dataclasses import asdict, dataclass, replace
from functools import lru_cache

from ..hangzhou.rules import HONORS, JOKER, TILES, WinContext, _luxury_pairs, ten_winds_eligible
from ..jokers import ExternalMeld, JokerPolicy, can_form_melds, can_form_pairs, iter_winning_shapes

RULE_VERSION = "xiaoshan-house-2025-v1"
SUB_RULE = "xiaoshan/xiaoshan"
DEFAULT_TOTAL_CAP = 32
DEFAULT_DEALER_CAP = 3          # 2**3 = 8 倍连庄封顶
BASE_SCORE = 1
CLAIM_THRESHOLD = 3             # 累计吃碰三次形成承包关系
# 开局积分（每人初始持有）：0 表示从零开始（旧对局/牌谱兼容）。
STARTING_SCORES = (0, 50, 100, 200)
DEFAULT_STARTING_SCORE = 0


def normalize_config(raw=None):
    """萧山房规可协商总倍率封顶与开局积分；其余沿用杭州引擎的配置形状。"""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"rule_version", "total_cap", "starting_score"}:
        raise ValueError("萧山麻将没有其它馆规选项")
    version = raw.get("rule_version", RULE_VERSION)
    if version not in (RULE_VERSION, "mil-hangzhou-2025-om1"):
        raise ValueError("不支持的萧山麻将规则版本")
    cap = raw.get("total_cap", DEFAULT_TOTAL_CAP)
    if cap not in (32, 64):
        raise ValueError("总倍率封顶只能为 32 或 64")
    starting = raw.get("starting_score", DEFAULT_STARTING_SCORE)
    if starting not in STARTING_SCORES:
        raise ValueError("开局积分只能为 50、100 或 200")
    return {"rule_version": RULE_VERSION, "total_cap": cap, "starting_score": starting}


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


def natural(tile, joker):
    """固定本牌映射：白板顶替翻出的财神；财神自身保持本牌。"""
    return joker if tile == JOKER and joker != JOKER else tile


@lru_cache(maxsize=8)
def _policy(joker, shapes=("standard", "seven_pairs")):
    """求解策略：财神唯一、白板按本牌映射、同种财神场上仅剩 3 张。"""
    return JokerPolicy(
        joker_tiles=(joker,),
        natural_map=((JOKER, joker),) if joker != JOKER else (),
        physical_limits=((joker, 3),),
        meld_count=4,
        closed_cap=None,
        shapes=shapes,
    )


def _code_tiles(code):
    """组合码 → (面子种类, 逻辑期望牌组)。"""
    if not isinstance(code, str) or len(code) != 3 or code[0] not in "skgG":
        raise ValueError("非法面子")
    try:
        tile = int(code[1:])
    except ValueError as exc:
        raise ValueError("面子牌张无效") from exc
    if code[0] == "s":
        if tile not in TILES or tile >= 40 or not 2 <= tile % 10 <= 8:
            raise ValueError("非法顺子")
        return "sequence", (tile - 1, tile, tile + 1)
    if tile not in TILES:
        raise ValueError("面子牌张无效")
    return ("triplet" if code[0] == "k" else "kong"), (tile,) * (3 if code[0] == "k" else 4)


def meld_frames(melds, physicals=()):
    """副露码与掩码实体对齐成 (code, physicals) 帧；缺掩码时按码面实体回退。"""
    frames = []
    for index, value in enumerate(melds):
        if isinstance(value, tuple):
            frames.append((value[0], tuple(value[1])))
            continue
        tiles = physicals[index] if index < len(physicals) else None
        frames.append((value, tuple(tiles) if tiles is not None else None))
    return tuple(frames)


def external_melds(melds, joker):
    """副露帧/码 → 求解器 ExternalMeld；财神实体不进副露，白板按本牌映射。"""
    external = []
    for code, physicals in meld_frames(melds):
        kind, expected = _code_tiles(code)
        if physicals is None:
            physicals = expected
        physicals = tuple(physicals)
        if (len(physicals) != len(expected)
                or any(type(t) is not int or t not in TILES for t in physicals)):
            raise ValueError("副露实体牌无效")
        if joker in physicals:
            raise ValueError("财神不可吃碰杠")
        logical = tuple(natural(t, joker) for t in physicals)
        if kind == "sequence":
            if Counter(logical) != Counter(expected):
                raise ValueError("顺子逻辑牌与面子码不符")
        elif len(set(logical)) != 1:
            raise ValueError("刻/杠逻辑牌必须同种")
        external.append(ExternalMeld(kind, physicals, logical, code[0] == "G"))
    return tuple(external)


def _valid(hand, melds, joker, *, complete):
    """无效外部输入返回 None；实体供给按「财神 3 张、其余 4 张」校验。"""
    if not isinstance(hand, (list, tuple)) or not isinstance(melds, (list, tuple)):
        return None
    if len(melds) > 4 or len(hand) + 3 * len(melds) != (14 if complete else 13):
        return None
    if any(type(t) is not int or t not in TILES for t in hand):
        return None
    try:
        external = external_melds(melds, joker)
    except ValueError:
        return None
    physical = Counter(hand)
    physical.update(t for meld in external for t in meld.physical)
    if any(n > (3 if t == joker else 4) for t, n in physical.items()):
        return None
    return external


def waiting_tiles(hand, melds=(), joker=JOKER):
    """实际可摸/可和的实体牌：白板按本牌身份参与，财神同种至多三张。"""
    external = _valid(hand, melds, joker, complete=False)
    if external is None:
        return set()
    from ..jokers import structural_waits
    return set(structural_waits(hand, external, _policy(joker)))


def can_baotou(pre_draw_tiles, melds=(), joker=JOKER):
    """爆头：摸牌前手牌去掉一张财神（白板不算）后仍可成搭/六对。"""
    external = _valid(pre_draw_tiles, melds, joker, complete=False)
    if external is None or not any(t == joker for t in pre_draw_tiles):
        return False
    remainder = list(pre_draw_tiles)
    remainder.remove(joker)
    policy = _policy(joker)
    if can_form_melds(remainder, external, policy):
        return True
    return not external and can_form_pairs(remainder, 6, policy)


def _numeral_suit(tile):
    return tile // 10 if tile < 40 else 0


def _decomposition(shape, hand):
    return {**asdict(shape), "physical_tiles": tuple(hand),
            "logical_tiles": shape.logical_tiles,
            "closed_logical_tiles": shape.closed_logical_tiles,
            "winning_logical_tile": shape.winning_logical_tile}


def _shape_fans(shape, hand, melds, context, joker, pre_draw):
    """同一副合法拆解下的全部番种；倍率逐项相乘，不做加法封顶。"""
    fans = []
    logical = shape.logical_tiles
    if shape.kind == "seven_pairs":
        luxury, _ = _luxury_pairs(hand, (joker,))
        fans.append(XFan("luxury_seven_pairs", "豪华七对", 16) if luxury
                    else XFan("seven_pairs", "七对子", 4))
    else:
        non_pair = [g for g in shape.groups if g.kind != "pair"]
        codes = [value[0] if isinstance(value, tuple) else value for value in melds]
        if len(melds) == 4 and all(not str(code).startswith("G") for code in codes):
            fans.append(XFan("quandao", "全倒", 2))
        if non_pair and all(g.kind in ("triplet", "kong") for g in non_pair):
            fans.append(XFan("pengpeng", "碰碰胡", 2))
    suits = {_numeral_suit(t) for t in logical}
    if len(suits) == 1 and next(iter(suits)) in (1, 2, 3):
        fans.append(XFan("qingyise", "清一色", 8))
    elif logical and all(t in HONORS for t in logical):
        fans.append(XFan("ziyise", "字一色", 16))
    if can_baotou(pre_draw, melds, joker):
        fans.append(XFan("baotou", "爆头", 2))
        for index in range(context.cai_piao_count):
            label = "财飘" if context.cai_piao_count == 1 else f"财飘×{index + 1}"
            fans.append(XFan("cai_piao", label, 2))
    if context.after_kong:
        fans.append(XFan("kong_draw", "杠开", 2))
    if not any(t == joker for t in hand):
        fans.append(XFan("no_joker", "无财神", 2))
    if not fans:
        fans.append(XFan("plain", "平和", 1))
    return tuple(fans)


def evaluate_win(hand, melds=(), win_tile=None, context=None, joker=JOKER,
                 total_cap=DEFAULT_TOTAL_CAP):
    """最优合法自摸：枚举全部逻辑拆解，取倍率相乘后最大者。"""
    external = _valid(hand, melds, joker, complete=True)
    if external is None:
        return None
    if context is None:
        context = WinContext()
    if not isinstance(context, WinContext):
        raise ValueError("和牌上下文必须来自权威摸牌记录")
    if not context.self_drawn:
        return None
    if context.after_kong and not any(str(value[0] if isinstance(value, tuple) else value)[0] in "gG"
                                      for value in melds):
        return None
    if win_tile is None:
        win_tile = hand[-1]
    if type(win_tile) is not int or win_tile not in hand:
        return None
    pre_draw = list(hand)
    pre_draw.remove(win_tile)
    if context.pre_draw_tiles is not None:
        if (_valid(context.pre_draw_tiles, melds, joker, complete=False) is None
                or Counter(pre_draw) != Counter(context.pre_draw_tiles)):
            return None
        pre_draw = list(context.pre_draw_tiles)
    policy = _policy(joker)
    best = None
    for shape in iter_winning_shapes(hand, external, policy, winning_tile=win_tile,
                                     equivalence="logical_structure"):
        score = XWinScore(shape.kind,
                          _shape_fans(shape, hand, melds, context, joker, pre_draw),
                          total_cap, _decomposition(shape, hand))
        if best is None or score.total_multiplier > best.total_multiplier:
            best = score
    return best


def score_ten_winds(hand, discard_history, melds=(), *, had_meld_action=False,
                    joker=JOKER, total_cap=DEFAULT_TOTAL_CAP):
    """十风：×8；十张弃牌中含翻出的财神计 ×16（白板不算财神）。"""
    if not ten_winds_eligible(hand, discard_history, melds,
                              had_meld_action=had_meld_action, jokers=(joker,)):
        return None
    multiplier = 16 if any(t == joker for t in discard_history) else 8
    fans = [XFan("ten_winds", "十风", multiplier)]
    if not any(t == joker for t in hand):
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
