"""台州麻将（黄岩基准）规则。

台州/黄岩麻将使用 136 张、十三张手牌，可吃碰杠、可点炮。开局翻一张
“得”，翻出的牌本身是万能牌；白板只在组成顺子时可顶替“得”的本牌值，
做对子/刻子时仍按白板本身计算。

计分沿用黄岩的“胡数 × 翻数”：
    胡数 =（基础牌点 + 底胡 10）× 2^番数，100 胡封顶（腊子）。
基础牌点按对子/明刻/暗刻/明杠/暗杠、自摸、嵌档、单吊、靠柄、对对胡累计；
番数按门风刻杠、字牌刻杠、清一色、混一色、对对和、旁和、海底、无得、
得张数、杠开、抢杠、自摸得、大吊车、四风会齐等累计。

本模块只负责纯规则；牌墙留 7 墩、每杠加 2 墩的流程在引擎中实现。
"""

from collections import Counter
from dataclasses import asdict, dataclass
from functools import lru_cache

RULE_VERSION = "taizhou-huangyan-2026-v2"
SUB_RULE = "taizhou/taizhou"

NUMBERS = tuple(s * 10 + n for s in (1, 2, 3) for n in range(1, 10))
WINDS = (41, 42, 43, 44)
DRAGONS = (45, 46, 47)
HONORS = WINDS + DRAGONS
TILES = NUMBERS + HONORS
WHITE = 46
JOKER = WHITE
BASE_HU = 10
LIZI_CAP = 100
NUM_TILES = len(TILES)
INDEX = {t: i for i, t in enumerate(TILES)}


def normalize_config(raw=None):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"rule_version", "edition"}:
        raise ValueError("台州麻将没有其它馆规选项")
    if raw.get("rule_version", RULE_VERSION) not in (RULE_VERSION, "taizhou-house-2026-v1"):
        raise ValueError("不支持的台州麻将规则版本")
    edition = raw.get("edition", "huangyan")
    if edition != "huangyan":
        raise ValueError("当前只开放黄岩基准的台州麻将")
    return {"rule_version": RULE_VERSION, "edition": edition}


def deck():
    return [t for t in TILES for _ in range(4)]


def is_terminal(tile):
    return tile in NUMBERS and tile % 10 in (1, 9)


def is_high(tile):
    return tile in HONORS or is_terminal(tile)


def natural(tile, joker):
    """白板作为“得”的本牌只用于顺子；其它情况白板仍是白板。"""
    return tile


def _parse_meld(value):
    physical = None
    if isinstance(value, (tuple, list)):
        if len(value) != 2:
            raise ValueError("非法面子帧")
        value, physical = value[0], value[1]
    if not isinstance(value, str) or len(value) != 3 or value[0] not in "skgG":
        raise ValueError("非法面子")
    try:
        tile = int(value[1:])
    except ValueError as exc:
        raise ValueError("面子牌张无效") from exc
    if value[0] == "s":
        if tile not in NUMBERS or not 2 <= tile % 10 <= 8:
            raise ValueError("非法顺子")
        logical = (tile - 1, tile, tile + 1)
        kind, concealed = "sequence", False
    else:
        if tile not in TILES:
            raise ValueError("面子牌张无效")
        kind = "triplet" if value[0] == "k" else "kong"
        logical = (tile,) * (3 if kind == "triplet" else 4)
        concealed = value[0] == "G"
    if physical is None:
        physical = logical
    physical = tuple(physical)
    if len(physical) != len(logical) or any(t not in TILES for t in physical):
        raise ValueError("副露实体牌无效")
    return {"kind": kind, "tile": logical[0] if kind != "sequence" else logical[1],
            "concealed": concealed, "logical": logical, "physical": physical}


def _fixed_groups(melds, joker):
    groups = []
    physical = []
    for value in melds:
        group = _parse_meld(value)
        if joker in group["physical"]:
            raise ValueError("得不能吃碰杠")
        groups.append(group)
        physical.extend(group["physical"])
    return tuple(groups), tuple(physical)


@lru_cache(maxsize=4096)
def _decompose_counts(counts, wild, white, pair_left, melds_left, joker):
    """把暗手拆成面子 + 一对；返回所有拆法（kind, 起始牌, 是否暗）。"""
    total = sum(counts) + wild + white
    if melds_left == 0 and pair_left == 0:
        return ((),) if total == 0 else ()
    if total != melds_left * 3 + pair_left * 2:
        return ()
    counts = list(counts)
    anchor = next((i for i, n in enumerate(counts) if n), None)
    anchors = range(NUM_TILES) if anchor is None else (anchor,)
    results = []
    seen = set()
    for i in anchors:
        tile = TILES[i]
        # 对子
        if pair_left:
            taken, nc, nw, nwh = _take(counts, wild, white, tile, 2, joker)
            if taken:
                sub = _decompose_counts(tuple(nc), nw, nwh, 0, melds_left, joker)
                for rest in sub:
                    group = ("pair", tile, True)
                    key = (group,) + rest
                    if key not in seen:
                        seen.add(key)
                        results.append(key)
        # 刻子
        if melds_left:
            taken, nc, nw, nwh = _take(counts, wild, white, tile, 3, joker)
            if taken:
                sub = _decompose_counts(tuple(nc), nw, nwh, pair_left, melds_left - 1, joker)
                for rest in sub:
                    group = ("triplet", tile, True)
                    key = (group,) + rest
                    if key not in seen:
                        seen.add(key)
                        results.append(key)
        # 顺子：锚牌可位于顺子的任意位置
        if melds_left and tile in NUMBERS:
            base = tile // 10 * 10
            for start in (tile - 2, tile - 1, tile):
                if start < base + 1 or start > base + 7:
                    continue
                nc, nw, nwh, ok = _take_sequence(counts, wild, white, start, joker)
                if not ok:
                    continue
                sub = _decompose_counts(tuple(nc), nw, nwh, pair_left, melds_left - 1, joker)
                for rest in sub:
                    group = ("sequence", start, True)
                    key = (group,) + rest
                    if key not in seen:
                        seen.add(key)
                        results.append(key)
    return tuple(results)


def _take(counts, wild, white, tile, need, joker):
    """取同种牌：优先实体，其次白板（仅当取的就是白板），最后得。"""
    counts = list(counts)
    idx = INDEX[tile]
    use = min(counts[idx], need)
    counts[idx] -= use
    remain = need - use
    if remain and tile == WHITE and joker != WHITE:
        use_white = min(white, remain)
        white -= use_white
        remain -= use_white
    if remain:
        if wild < remain:
            return False, counts, wild, white
        wild -= remain
    return True, counts, wild, white


def _take_sequence(counts, wild, white, start, joker):
    counts = list(counts)
    used_wild = 0
    used_white = 0
    for tile in (start, start + 1, start + 2):
        idx = INDEX[tile]
        if counts[idx]:
            counts[idx] -= 1
        elif tile == joker and white:
            white -= 1
            used_white += 1
        else:
            used_wild += 1
    if used_wild > wild:
        return counts, wild, white, False
    return counts, wild - used_wild, white, True


def _hand_groups(hand, joker, meld_count=0):
    """暗手所有拆法；每个拆法含（4-副露数）组 + 1 对。"""
    counts = [0] * NUM_TILES
    wild = 0
    white = 0
    for tile in hand:
        if tile == joker:
            wild += 1
        elif tile == WHITE:
            white += 1
        else:
            counts[INDEX[tile]] += 1
    if joker == WHITE:
        wild += white
        white = 0
    return _decompose_counts(tuple(counts), wild, white, 1, 4 - meld_count, joker)


def _all_groups(fixed, decomposition):
    groups = list(fixed)
    pair = None
    for kind, tile, concealed in decomposition:
        if kind == "pair":
            pair = {"kind": "pair", "tile": tile, "concealed": True,
                    "logical": (tile, tile)}
        elif kind == "sequence":
            groups.append({"kind": "sequence", "tile": tile, "concealed": concealed,
                           "logical": (tile, tile + 1, tile + 2)})
        else:
            groups.append({"kind": kind, "tile": tile, "concealed": concealed,
                           "logical": (tile,) * (3 if kind == "triplet" else 4)})
    if pair is not None:
        groups.append(pair)
    return groups, pair


def _group_hu(group, seat_wind, joker):
    kind, tile = group["kind"], group["tile"]
    if tile == joker:
        return 0
    if kind == "pair":
        return 2 if tile == seat_wind or tile in DRAGONS else 0
    high = is_high(tile)
    if kind == "triplet":
        value = 4 if high else 2
        return value * 2 if group["concealed"] else value
    if kind == "kong":
        value = 16 if high else 8
        return value * 2 if group["concealed"] else value
    return 0


def _suit_of(tile):
    return tile // 10 if tile in NUMBERS else 0


def _is_qingyise(groups):
    suits = {_suit_of(g["tile"]) for g in groups if g["tile"] != 0}
    return len(suits) == 1 and next(iter(suits)) in (1, 2, 3) and all(g["tile"] in NUMBERS for g in groups)


def _is_hunyise(groups):
    suits = {_suit_of(g["tile"]) for g in groups if g["tile"] in NUMBERS}
    return len(suits) == 1 and any(g["tile"] in HONORS for g in groups)


def _wait_type(groups, win_tile, pre_tiles, joker):
    """返回 (单吊, 嵌档, 靠柄, 大吊车)。"""
    single = middle = edge = False
    for group in groups:
        if group["kind"] == "pair" and win_tile in group["logical"]:
            single = True
        if group["kind"] == "sequence" and win_tile in group["logical"]:
            if group["logical"][1] == win_tile:
                middle = True
            if win_tile in (group["logical"][0], group["logical"][2]) and win_tile % 10 in (3, 7):
                edge = True
    diaoche = len(pre_tiles) == 1 and single
    return single, middle, edge, diaoche


def _score_groups(groups, pair, win_tile, pre_tiles, *, joker, seat_wind,
                  self_draw, after_kong, rob_kong, sea_bottom, joker_count, physicals):
    base_hu = BASE_HU
    for group in groups:
        base_hu += _group_hu(group, seat_wind, joker)
    fans = []
    if self_draw:
        base_hu += 2
        fans.append(("self_draw", "自摸", 0))
    if all(g["kind"] in ("triplet", "kong") for g in groups if g["kind"] != "pair"):
        base_hu += 4
        fans.append(("duiduihu", "对对胡", 2))
    single, middle, edge, diaoche = _wait_type(groups, win_tile, pre_tiles, joker)
    if single:
        base_hu += 2
    if middle:
        base_hu += 2
    if edge:
        base_hu += 2
    if diaoche:
        fans.append(("diaoche", "大吊车", 1))

    for group in groups:
        tile, kind = group["tile"], group["kind"]
        if kind in ("triplet", "kong"):
            if tile == seat_wind:
                fans.append(("seat_wind", "门风刻杠", 1))
            if tile in DRAGONS:
                fans.append(("dragon", "字牌刻杠", 1))
    if _is_qingyise(groups):
        fans.append(("qingyise", "清一色", 4))
    elif _is_hunyise(groups):
        fans.append(("hunyise", "混一色", 2))
    if all(g["kind"] == "sequence" for g in groups if g["kind"] != "pair"):
        if not (middle or edge or single):
            fans.append(("panghe", "旁和", 1))
    if sea_bottom:
        fans.append(("haidi", "海底捞月", 1))
    if joker_count == 0:
        fans.append(("wude", "无得", 1))
    if joker_count == 2:
        fans.append(("two_de", "二得", 1))
    elif joker_count >= 3:
        fans.append(("three_de", "三得", 2))
    if after_kong:
        fans.append(("kong_draw", "杠上开花", 1))
    if rob_kong:
        fans.append(("rob_kong", "抢杠和", 1))
    if self_draw and win_tile == joker:
        fans.append(("zimo_de", "自摸得", 1))
    if all(t in WINDS for g in groups for t in g["logical"]):
        fans.append(("sifeng", "四风会齐", 4))
    if joker == WHITE:
        fans.extend(("fan_white", "翻白板", 1) for t in physicals if t == WHITE)
    elif joker == 45:
        fans.extend(("fan_red", "翻红中", 1) for t in physicals if t == 45)

    fan_total = sum(item[2] for item in fans)
    total_hu = min(LIZI_CAP, base_hu * (1 << fan_total))
    return base_hu, fans, fan_total, total_hu


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
    sea_bottom: bool = False
    seat_wind: int = 41


@dataclass(frozen=True)
class WinScore:
    shape: str
    fans: tuple[Fan, ...]
    base_hu: int
    fan_total: int
    total_hu: int
    payer: int | None = None
    decomposition: dict | None = None

    @property
    def points(self):
        return self.total_hu

    @property
    def fan_ids(self):
        return tuple(f"TZ|{item.id}|{item.fan}|{item.name}" for item in self.fans)

    @property
    def fan_names(self):
        names = [f"底胡{BASE_HU}", f"基础胡{self.base_hu - BASE_HU}"]
        names.extend(f"{item.name}（{item.fan}番）" for item in self.fans if item.fan)
        names.append(f"{self.base_hu}×2^{self.fan_total}={self.total_hu}胡")
        return names

    def as_dict(self):
        return {**asdict(self), "rule_version": RULE_VERSION,
                "points": self.points, "fan_cap": LIZI_CAP}


def evaluate_win(hand, melds=(), win_tile=None, context=None, *, joker):
    """返回最佳合法台州和牌计分；不合法返回 None。"""
    if not isinstance(hand, (list, tuple)) or not isinstance(melds, (list, tuple)):
        return None
    if len(melds) > 4 or len(hand) + 3 * len(melds) != 14:
        return None
    if any(type(t) is not int or t not in TILES for t in hand):
        return None
    try:
        fixed, fixed_physical = _fixed_groups(melds, joker)
    except ValueError:
        return None
    physical = list(hand) + list(fixed_physical)
    if any(n > (3 if t == joker else 4) for t, n in Counter(physical).items()):
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
    if len(pre) != 13 - 3 * len(melds):
        return None

    best = None
    for decomposition in _hand_groups(hand, joker, len(melds)):
        groups, pair = _all_groups(fixed, decomposition)
        if len(groups) != 5:
            continue
        base_hu, fans, fan_total, total_hu = _score_groups(
            groups, pair, win_tile, pre, joker=joker, seat_wind=context.seat_wind,
            self_draw=context.self_drawn, after_kong=context.after_kong,
            rob_kong=context.rob_kong, sea_bottom=context.sea_bottom,
            joker_count=sum(1 for t in hand if t == joker),
            physicals=tuple(hand) + fixed_physical)
        score = WinScore("standard", tuple(Fan(*item) for item in fans),
                         base_hu, fan_total, total_hu,
                         decomposition={"groups": groups, "winning_tile": win_tile,
                                        "physical_tiles": tuple(hand)})
        if best is None or score.total_hu > best.total_hu:
            best = score
    return best


@lru_cache(maxsize=2048)
def _waits_cached(hand, melds, joker, seat_wind):
    waits = set()
    for tile in TILES:
        trial = tuple(hand) + (tile,)
        if evaluate_win(trial, melds, tile, WinContext(seat_wind=seat_wind), joker=joker):
            waits.add(tile)
    return tuple(sorted(waits))


def waiting_tiles(hand, melds=(), joker=JOKER, seat_wind=41):
    if len(hand) + 3 * len(melds) != 13:
        return set()
    return set(_waits_cached(tuple(sorted(hand)), tuple(melds), joker, seat_wind))


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


def settle_win(score, winner, dealer, hu_by_seat=None):
    """按胡数差结算；庄家输满额，闲家输一半。100 胡封顶已计入 total_hu。"""
    if not isinstance(score, WinScore):
        raise ValueError("缺少合法台州和牌计分")
    for value, name in ((winner, "和家"), (dealer, "庄家")):
        if type(value) is not int or value not in range(4):
            raise ValueError(f"非法{name}")
    hu_by_seat = hu_by_seat or [0] * 4
    winner_hu = score.total_hu
    dealer_loses = winner != dealer
    changes = [0] * 4
    transfers = []
    for payer in range(4):
        if payer == winner:
            continue
        own = int(hu_by_seat[payer] or 0)
        diff = max(0, winner_hu - own)
        if dealer_loses and payer != dealer:
            diff //= 2
        if diff <= 0:
            continue
        transfers.append(Transfer(payer, winner, diff,
                                  "点炮" if payer == score.payer else "自摸"))
        changes[payer] -= diff
        changes[winner] += diff
    return Settlement(tuple(changes), tuple(transfers), winner_hu, 1, winner_hu)
