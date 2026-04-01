"""
胡牌判断与听牌分析（四川麻将缺一门规则）
"""
from __future__ import annotations
from collections import Counter
from dataclasses import dataclass
from .tiles import Tile, Suit, all_sichuan_tile_types

SICHUAN_SUITS = (Suit.WAN, Suit.TIAO, Suit.BING)


@dataclass(frozen=True)
class DiscardOption:
    """打出一张牌后的听牌分析结果。"""
    discard: Tile
    tenpai_tiles: tuple[Tile, ...]
    tenpai_count: int


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
            ))
    options.sort(key=lambda o: o.tenpai_count, reverse=True)
    return options


# ── 内部实现 ─────────────────────────────────────────────────

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
