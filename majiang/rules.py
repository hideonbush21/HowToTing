"""
胡牌判断与听牌分析（四川麻将缺一门规则）
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from .tiles import Tile, Suit, all_sichuan_tile_types

SICHUAN_SUITS = (Suit.WAN, Suit.TIAO, Suit.BING)


@dataclass(frozen=True)
class TenpaiStructure:
    """13 张听牌手牌的分解结构。"""
    melds:     tuple[tuple[Tile, ...], ...]   # 完整面子（顺子/刻子）
    pair:      tuple[Tile, Tile] | None        # 雀头；七对/单钓时为 None
    wait_part: tuple[Tile, ...]                # 未完成部分（1-2 张）
    wait_type: str                             # 两面/嵌张/边张/单钓/双碰/七对


@dataclass(frozen=True)
class DiscardOption:
    """打出一张牌后的听牌分析结果。"""
    discard:    Tile
    tenpai_tiles: tuple[Tile, ...]
    tenpai_count: int
    effective_count: int = 0   # 残局模式：剩余可摸听牌总张数
    structure:  TenpaiStructure | None = None


# ── 核心判断 ─────────────────────────────────────────────────

def _suit_count(hand: list[Tile]) -> int:
    """返回手牌中 WAN/TIAO/BING 三种花色实际出现的种数。"""
    return len({t.suit for t in hand if t.suit in SICHUAN_SUITS})


def is_win(hand: list[Tile], *, require_que_yi_men: bool = True) -> bool:
    """
    判断 14 张手牌是否胡牌。
    支持：标准 4 面子 + 1 对；七对。
    require_que_yi_men=True（默认）：四川麻将缺一门，手牌最多使用两种花色。
    """
    if len(hand) != 14:
        return False
    if require_que_yi_men and _suit_count(hand) > 2:
        return False
    counts = Counter(hand)
    return _is_seven_pairs(counts) or _is_standard(counts)


def is_tenpai(hand: list[Tile], *, require_que_yi_men: bool = True) -> bool:
    """判断 13 张手牌是否处于听牌状态。"""
    return bool(tenpai_tiles(hand, require_que_yi_men=require_que_yi_men))


def tenpai_tiles(hand: list[Tile], *, require_que_yi_men: bool = True) -> list[Tile]:
    """
    返回能使 13 张手牌胡牌的所有牌型（理论听牌，不计剩余张数）。
    缺一门模式：若手牌已有两种花色，候选集缩小至这两种花色（约 33% 加速）。
    """
    if len(hand) != 13:
        return []

    if require_que_yi_men:
        existing_suits = {t.suit for t in hand if t.suit in SICHUAN_SUITS}
        if len(existing_suits) >= 2:
            # 已有两种花色：摸入牌只能来自这两种花色
            candidates = [Tile(s, v) for s in existing_suits for v in range(1, 10)]
        else:
            # 单色手牌：可加入任意第二种花色
            candidates = all_sichuan_tile_types()
    else:
        candidates = all_sichuan_tile_types()

    return [t for t in candidates
            if is_win(sorted(hand + [t]), require_que_yi_men=require_que_yi_men)]


def best_discards(hand: list[Tile], *, require_que_yi_men: bool = True) -> list[DiscardOption]:
    """
    对 14 张手牌枚举每种可打出的牌，返回按听牌种数降序排列的 DiscardOption 列表。
    """
    if len(hand) != 14:
        return []
    options: list[DiscardOption] = []
    seen: set[Tile] = set()
    for tile in hand:
        if tile in seen:
            continue
        seen.add(tile)
        remaining = hand.copy()
        remaining.remove(tile)
        waits = tenpai_tiles(remaining, require_que_yi_men=require_que_yi_men)
        if waits:
            options.append(DiscardOption(
                discard=tile,
                tenpai_tiles=tuple(waits),
                tenpai_count=len(waits),
                structure=find_tenpai_structure(remaining),
            ))
    options.sort(key=lambda o: o.tenpai_count, reverse=True)
    return options


def best_discards_endgame(
    hand: list[Tile],
    remaining: dict[str, int],
    *,
    require_que_yi_men: bool = True,
) -> list[DiscardOption]:
    """
    残局模式：在 best_discards 基础上计算每种打法的剩余可摸张数
    (effective_count)，并按此降序排列。
    remaining: tile_str → 当前牌墙剩余张数
    """
    base = best_discards(hand, require_que_yi_men=require_que_yi_men)
    options = [
        DiscardOption(
            discard=opt.discard,
            tenpai_tiles=opt.tenpai_tiles,
            tenpai_count=opt.tenpai_count,
            effective_count=sum(remaining.get(str(t), 0) for t in opt.tenpai_tiles),
            structure=opt.structure,
        )
        for opt in base
    ]
    options.sort(key=lambda o: o.effective_count, reverse=True)
    return options


# ── 听牌结构分解 ──────────────────────────────────────────────

def _extract_all_as_melds(counts: Counter, collected: list) -> bool:
    """
    尝试将 counts 中所有牌组成面子，将面子追加到 collected。
    就地修改 counts，调用者需传入副本。
    """
    if not counts:
        return True
    tile = min(counts)

    if counts[tile] >= 3:
        counts[tile] -= 3
        if counts[tile] == 0:
            del counts[tile]
        collected.append((tile, tile, tile))
        if _extract_all_as_melds(counts, collected):
            return True
        collected.pop()
        counts[tile] = counts.get(tile, 0) + 3

    if tile.suit in SICHUAN_SUITS and tile.value <= 7:
        t2 = Tile(tile.suit, tile.value + 1)
        t3 = Tile(tile.suit, tile.value + 2)
        if counts.get(t2, 0) >= 1 and counts.get(t3, 0) >= 1:
            for t in (tile, t2, t3):
                counts[t] -= 1
                if counts[t] == 0:
                    del counts[t]
            collected.append((tile, t2, t3))
            if _extract_all_as_melds(counts, collected):
                return True
            collected.pop()
            for t in (tile, t2, t3):
                counts[t] = counts.get(t, 0) + 1

    return False


def _classify_wait(wp: list[Tile]) -> str:
    """根据未完成部分判断听牌形式。"""
    if len(wp) == 1:
        return '单钓'
    a, b = sorted(wp, key=lambda t: (t.suit.value, t.value))
    if a.suit != b.suit or a == b:
        return '双碰'
    diff = b.value - a.value
    if diff == 1:
        return '边张' if a.value == 1 or b.value == 9 else '两面'
    if diff == 2:
        return '嵌张'
    return '双碰'


def find_tenpai_structure(hand: list[Tile]) -> TenpaiStructure | None:
    """
    对 13 张听牌手牌进行分解，返回 TenpaiStructure。
    涵盖：标准型（3 面子 + 雀头 + 等待）、单钓（4 面子 + 单张）、七对。
    """
    if len(hand) != 13:
        return None
    counts = Counter(hand)

    # ── 七对 ──
    singles = [t for t in counts if counts[t] % 2 == 1]
    pairs   = [t for t in counts if counts[t] >= 2]
    if len(singles) == 1 and sum(1 for t in counts if counts[t] >= 2) == 6:
        pair_tiles = sorted(pairs, key=lambda t: (t.suit.value, t.value))
        return TenpaiStructure(
            melds=tuple(tuple([t, t]) for t in pair_tiles),
            pair=None,
            wait_part=(singles[0],),
            wait_type='七对',
        )

    # ── 标准型：枚举雀头，再枚举 wait_part ──
    key = lambda t: (t.suit.value, t.value)
    for pair_tile in sorted(set(hand), key=key):
        if counts[pair_tile] < 2:
            continue
        remaining = list(hand)
        remaining.remove(pair_tile)
        remaining.remove(pair_tile)   # 剩余 11 张

        seen_wp: set = set()
        for i in range(len(remaining)):
            for j in range(i + 1, len(remaining)):
                wp = tuple(sorted([remaining[i], remaining[j]], key=key))
                if wp in seen_wp:
                    continue
                seen_wp.add(wp)

                meld9 = [remaining[k] for k in range(len(remaining)) if k != i and k != j]
                collected: list = []
                if _extract_all_as_melds(Counter(meld9), collected):
                    return TenpaiStructure(
                        melds=tuple(tuple(m) for m in collected),
                        pair=(pair_tile, pair_tile),
                        wait_part=wp,
                        wait_type=_classify_wait(list(wp)),
                    )

    # ── 单钓：4 面子 + 1 张 ──
    for tanki in sorted(set(hand), key=key):
        meld12 = list(hand)
        meld12.remove(tanki)
        collected = []
        if _extract_all_as_melds(Counter(meld12), collected):
            return TenpaiStructure(
                melds=tuple(tuple(m) for m in collected),
                pair=None,
                wait_part=(tanki,),
                wait_type='单钓',
            )

    return None

def _is_seven_pairs(counts: Counter) -> bool:
    return len(counts) == 7 and all(v == 2 for v in counts.values())


def _is_standard(counts: Counter) -> bool:
    for tile in set(counts.elements()):
        if counts[tile] >= 2:
            remaining = counts.copy()
            remaining[tile] -= 2
            if remaining[tile] == 0:
                del remaining[tile]
            if _can_form_melds(remaining):
                return True
    return False


def _can_form_melds(counts: Counter) -> bool:
    if not counts:
        return True
    tile = min(counts)
    n = counts[tile]

    if n >= 3:
        counts[tile] -= 3
        if counts[tile] == 0:
            del counts[tile]
        if _can_form_melds(counts):
            return True
        counts[tile] = counts.get(tile, 0) + 3

    if tile.suit in SICHUAN_SUITS and tile.value <= 7:
        t2 = Tile(tile.suit, tile.value + 1)
        t3 = Tile(tile.suit, tile.value + 2)
        if counts.get(t2, 0) >= 1 and counts.get(t3, 0) >= 1:
            for t in (tile, t2, t3):
                counts[t] -= 1
                if counts[t] == 0:
                    del counts[t]
            if _can_form_melds(counts):
                return True
            for t in (tile, t2, t3):
                counts[t] = counts.get(t, 0) + 1

    return False


def _can_form_one_meld(tiles: list[Tile]) -> bool:
    """判断恰好 3 张牌是否构成刻子或顺子。"""
    if len(tiles) != 3:
        return False
    if tiles[0] == tiles[1] == tiles[2]:
        return True
    suits = {t.suit for t in tiles}
    if len(suits) != 1 or next(iter(suits)) not in SICHUAN_SUITS:
        return False
    vals = sorted(t.value for t in tiles)
    return vals[0] + 1 == vals[1] and vals[1] + 1 == vals[2]


def find_structure_for_tile(hand: list[Tile], wait_tile: Tile) -> TenpaiStructure | None:
    """
    针对指定听牌 wait_tile，返回能解释「为什么这张牌能胡」的 TenpaiStructure。
    与 find_tenpai_structure 的区别：强制 wait_tile 出现在 wait_part 中，
    因此对于双碰/多解构等牌型，每张听牌会得到各自独立的说明。
    """
    if len(hand) != 13:
        return None
    counts = Counter(hand)
    key = lambda t: (t.suit.value, t.value)

    # ── 七对 ──
    singles = [t for t in counts if counts[t] % 2 == 1]
    evens   = [t for t in counts if counts[t] % 2 == 0 and counts[t] >= 2]
    if len(singles) == 1 and singles[0] == wait_tile and len(evens) == 6:
        return TenpaiStructure(
            melds=tuple(tuple([t, t]) for t in sorted(evens, key=key)),
            pair=None,
            wait_part=(wait_tile,),
            wait_type='七对',
        )

    # ── 标准型：枚举雀头 → wait_part + wait_tile 能成面子即可（wait_tile 不必在手牌中）──
    for pair_tile in sorted(set(hand), key=key):
        if counts[pair_tile] < 2:
            continue
        remaining = list(hand)
        remaining.remove(pair_tile)
        remaining.remove(pair_tile)  # 剩余 11 张

        seen_wp: set = set()
        for i in range(len(remaining)):
            for j in range(i + 1, len(remaining)):
                wp = tuple(sorted([remaining[i], remaining[j]], key=key))
                if wp in seen_wp:
                    continue
                seen_wp.add(wp)
                # wait_part + wait_tile 必须构成一个面子
                if not _can_form_one_meld(sorted(list(wp) + [wait_tile])):
                    continue
                meld9 = [remaining[k] for k in range(len(remaining)) if k != i and k != j]
                collected: list = []
                if _extract_all_as_melds(Counter(meld9), collected):
                    return TenpaiStructure(
                        melds=tuple(tuple(m) for m in collected),
                        pair=(pair_tile, pair_tile),
                        wait_part=wp,
                        wait_type=_classify_wait(list(wp)),
                    )

    # ── 单钓：wait_tile 本身是孤张 ──
    if wait_tile in hand:
        meld12 = list(hand)
        meld12.remove(wait_tile)
        collected = []
        if _extract_all_as_melds(Counter(meld12), collected):
            return TenpaiStructure(
                melds=tuple(tuple(m) for m in collected),
                pair=None,
                wait_part=(wait_tile,),
                wait_type='单钓',
            )

    return None


# ── 多人对战兼容 ─────────────────────────────────────────────

def possible_actions(hand: list[Tile], discard: Tile, player_wind: int, round_wind: int) -> dict:
    """多人对战：碰/吃/杠/胡判断（缺一门开启）。"""
    actions: dict = {"hu": False, "peng": False, "chi": [], "gang": False}
    test_hand = sorted(hand + [discard])
    if is_win(test_hand, require_que_yi_men=True):
        actions["hu"] = True
    counts = Counter(hand)
    if counts[discard] >= 2:
        actions["peng"] = True
    if counts[discard] >= 3:
        actions["gang"] = True
    if discard.suit in SICHUAN_SUITS:
        v = discard.value
        for combo in [(v - 2, v - 1), (v - 1, v + 1), (v + 1, v + 2)]:
            if all(1 <= x <= 9 and counts[Tile(discard.suit, x)] >= 1 for x in combo):
                actions["chi"].append(combo)
    return actions
