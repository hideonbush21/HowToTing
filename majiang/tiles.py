"""
麻将牌定义与基础数据结构
"""
from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Iterator


class Suit(str, Enum):
    WAN = "万"   # 万子
    TIAO = "条"  # 条子
    BING = "饼"  # 饼子
    FENG = "风"  # 风牌
    JIAN = "箭"  # 箭牌（中发白）


class Wind(str, Enum):
    EAST = "东"
    SOUTH = "南"
    WEST = "西"
    NORTH = "北"


@dataclass(frozen=True, order=True)
class Tile:
    """一张麻将牌。数字牌 value 1-9；风/箭牌 value 对应枚举序号。"""
    suit: Suit
    value: int  # 1-9 for WAN/TIAO/BING; 1-4 for FENG; 1-3 for JIAN

    def __str__(self) -> str:
        if self.suit in (Suit.WAN, Suit.TIAO, Suit.BING):
            return f"{self.value}{self.suit.value}"
        if self.suit == Suit.FENG:
            return Wind(list(Wind)[self.value - 1]).value + "风"
        arrows = ["中", "发", "白"]
        return arrows[self.value - 1]

    def __repr__(self) -> str:
        return str(self)


SICHUAN_SUITS = (Suit.WAN, Suit.TIAO, Suit.BING)


def sichuan_deck() -> list[Tile]:
    """返回四川麻将的 108 张牌（万/条/饼各 36 张，每种牌 4 张）。"""
    tiles: list[Tile] = []
    for suit in SICHUAN_SUITS:
        for v in range(1, 10):
            tiles.extend([Tile(suit, v)] * 4)
    return tiles


def all_sichuan_tile_types() -> list[Tile]:
    """返回四川麻将的 27 种唯一牌型（用于听牌枚举）。"""
    return [Tile(suit, v) for suit in SICHUAN_SUITS for v in range(1, 10)]


def full_deck() -> list[Tile]:
    """返回完整的 136 张牌（含风/箭，供多人模式使用）。"""
    tiles: list[Tile] = []
    for suit in (Suit.WAN, Suit.TIAO, Suit.BING):
        for v in range(1, 10):
            tiles.extend([Tile(suit, v)] * 4)
    for v in range(1, 5):
        tiles.extend([Tile(Suit.FENG, v)] * 4)
    for v in range(1, 4):
        tiles.extend([Tile(Suit.JIAN, v)] * 4)
    return tiles


def tiles_from_str(s: str) -> list[Tile]:
    """快捷解析，如 '1万2万3万' → [Tile(WAN,1), Tile(WAN,2), Tile(WAN,3)]"""
    result: list[Tile] = []
    i = 0
    while i < len(s):
        if s[i].isdigit():
            v = int(s[i])
            suit_char = s[i + 1]
            suit_map = {"万": Suit.WAN, "条": Suit.TIAO, "饼": Suit.BING}
            result.append(Tile(suit_map[suit_char], v))
            i += 2
        else:
            special = {"东": (Suit.FENG, 1), "南": (Suit.FENG, 2),
                       "西": (Suit.FENG, 3), "北": (Suit.FENG, 4),
                       "中": (Suit.JIAN, 1), "发": (Suit.JIAN, 2), "白": (Suit.JIAN, 3)}
            suit, v = special[s[i]]
            result.append(Tile(suit, v))
            i += 1
    return result
