"""
游戏房间与状态机
"""
from __future__ import annotations
import random
import uuid
from enum import Enum
from dataclasses import dataclass, field
from .tiles import Tile, full_deck
from .rules import is_win, possible_actions


class Phase(str, Enum):
    WAITING = "waiting"      # 等待玩家
    PLAYING = "playing"      # 游戏中
    FINISHED = "finished"    # 结束


@dataclass
class Player:
    pid: str
    name: str
    hand: list[Tile] = field(default_factory=list)
    melds: list[list[Tile]] = field(default_factory=list)  # 已亮牌的面子
    wind: int = 1  # 1=东 2=南 3=西 4=北
    score: int = 0


@dataclass
class Room:
    room_id: str = field(default_factory=lambda: uuid.uuid4().hex[:6].upper())
    players: list[Player] = field(default_factory=list)
    phase: Phase = Phase.WAITING
    wall: list[Tile] = field(default_factory=list)
    discard_pile: list[Tile] = field(default_factory=list)
    current_player_idx: int = 0
    round_wind: int = 1  # 1=东风圈

    # ── 房间管理 ────────────────────────────────────────────

    def add_player(self, pid: str, name: str) -> Player:
        if len(self.players) >= 4:
            raise ValueError("房间已满")
        winds = [1, 2, 3, 4]
        used = {p.wind for p in self.players}
        wind = next(w for w in winds if w not in used)
        p = Player(pid=pid, name=name, wind=wind)
        self.players.append(p)
        return p

    def start(self) -> None:
        if len(self.players) < 2:
            raise ValueError("至少需要 2 名玩家")
        self.wall = full_deck()
        random.shuffle(self.wall)
        self.phase = Phase.PLAYING
        # 发牌：庄家 13 张，其余 13 张
        for i, p in enumerate(self.players):
            p.hand = sorted(self.wall[:13])
            self.wall = self.wall[13:]
        # 庄家多摸一张
        self._draw(self.current_player_idx)

    # ── 游戏动作 ────────────────────────────────────────────

    def _draw(self, idx: int) -> Tile | None:
        if not self.wall:
            return None
        t = self.wall.pop(0)
        self.players[idx].hand.append(t)
        self.players[idx].hand.sort()
        return t

    def discard(self, pid: str, tile: Tile) -> dict:
        idx = self._pid_to_idx(pid)
        if idx != self.current_player_idx:
            raise ValueError("不是你的回合")
        p = self.players[idx]
        p.hand.remove(tile)
        self.discard_pile.append(tile)
        # 检查其他玩家可否响应
        responses = {}
        for i, other in enumerate(self.players):
            if i == idx:
                continue
            acts = possible_actions(other.hand, tile, other.wind, self.round_wind)
            if any([acts["hu"], acts["peng"], acts["chi"], acts["gang"]]):
                responses[other.pid] = acts
        return responses

    def peng(self, pid: str) -> None:
        idx = self._pid_to_idx(pid)
        tile = self.discard_pile[-1]
        p = self.players[idx]
        for _ in range(2):
            p.hand.remove(tile)
        meld = [tile, tile, tile]
        p.melds.append(meld)
        self.discard_pile.pop()
        self.current_player_idx = idx

    def draw_and_next(self) -> Tile | None:
        self.current_player_idx = (self.current_player_idx + 1) % len(self.players)
        return self._draw(self.current_player_idx)

    def declare_win(self, pid: str) -> dict:
        idx = self._pid_to_idx(pid)
        p = self.players[idx]
        if not is_win(p.hand):
            raise ValueError("手牌未胡")
        self.phase = Phase.FINISHED
        return {"winner": pid, "hand": p.hand, "melds": p.melds}

    # ── 查询 ────────────────────────────────────────────────

    def state_for(self, pid: str) -> dict:
        """返回某玩家视角的游戏状态（不暴露他人手牌）。"""
        me = self._get_player(pid)
        others = []
        for p in self.players:
            if p.pid == pid:
                continue
            others.append({
                "pid": p.pid, "name": p.name,
                "hand_count": len(p.hand),
                "melds": [[str(t) for t in m] for m in p.melds],
                "wind": p.wind, "score": p.score,
            })
        return {
            "room_id": self.room_id,
            "phase": self.phase,
            "my_hand": [str(t) for t in me.hand],
            "my_melds": [[str(t) for t in m] for m in me.melds],
            "discard_pile": [str(t) for t in self.discard_pile[-20:]],
            "wall_remaining": len(self.wall),
            "current_player": self.players[self.current_player_idx].pid,
            "others": others,
        }

    def _pid_to_idx(self, pid: str) -> int:
        for i, p in enumerate(self.players):
            if p.pid == pid:
                return i
        raise ValueError(f"玩家 {pid} 不在此房间")

    def _get_player(self, pid: str) -> Player:
        return self.players[self._pid_to_idx(pid)]
