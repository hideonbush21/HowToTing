"""
四川麻将单人教学：练习题生成与会话管理

generate_quiz_hand      — 生成单道题（2花色缺一门）
create_session_with_first — 生成第1题并返回 rng 状态，供后台继续生成
generate_remaining      — 后台线程调用，追加剩余题目
create_session          — 同步生成全部题目（供测试 / CLI 使用）
"""
from __future__ import annotations
import random
import threading
import uuid
from dataclasses import dataclass, field

from .tiles import Tile, Suit, sichuan_deck
from .rules import DiscardOption, best_discards

SICHUAN_SUITS = (Suit.WAN, Suit.TIAO, Suit.BING)


# ── 数据结构 ──────────────────────────────────────────────────

@dataclass
class QuizHand:
    hand: list[Tile]
    options: list[DiscardOption]
    max_tenpai: int


@dataclass
class AnswerResult:
    correct: bool
    your_choice: DiscardOption
    best_options: list[DiscardOption]
    max_tenpai: int


@dataclass
class QuizSession:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    questions: list[QuizHand] = field(default_factory=list)
    total_expected: int = 10       # 预期总题数（后台生成未完成时也对外声明）
    current_index: int = 0
    answered: int = 0
    correct_count: int = 0
    generation_done: bool = False
    generation_error: str | None = None
    suits_plan: list[int] = field(default_factory=list)  # 每题花色数：1=清一色，2=双色

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # ── 后台生成接口 ──────────────────────────────────────────

    def append_question(self, q: QuizHand) -> None:
        """后台线程调用：追加一道题（线程安全）。"""
        with self._lock:
            self.questions.append(q)

    def mark_generation_done(self) -> None:
        with self._lock:
            self.generation_done = True

    def mark_generation_error(self, msg: str) -> None:
        with self._lock:
            self.generation_error = msg
            self.generation_done = True

    # ── 状态查询 ──────────────────────────────────────────────

    def current_question_ready(self) -> bool:
        """当前题目是否已生成就绪。"""
        with self._lock:
            return self.current_index < len(self.questions)

    @property
    def finished(self) -> bool:
        with self._lock:
            return self.current_index >= self.total_expected

    def current_question(self) -> QuizHand:
        with self._lock:
            if self.current_index >= len(self.questions):
                raise ValueError("题目尚未生成完成")
            return self.questions[self.current_index]

    # ── 答题 ──────────────────────────────────────────────────

    def submit_answer(self, discard: Tile) -> AnswerResult:
        q = self.current_question()
        choice = next((o for o in q.options if o.discard == discard), None)
        if choice is None:
            choice = DiscardOption(discard=discard, tenpai_tiles=(), tenpai_count=0)

        best = [o for o in q.options if o.tenpai_count == q.max_tenpai]
        is_correct = choice.tenpai_count == q.max_tenpai

        with self._lock:
            self.answered += 1
            if is_correct:
                self.correct_count += 1
            self.current_index += 1

        return AnswerResult(
            correct=is_correct,
            your_choice=choice,
            best_options=best,
            max_tenpai=q.max_tenpai,
        )


# ── 题目生成 ──────────────────────────────────────────────────

def _make_suits_plan(rng: random.Random, n: int = 10, min_single: int = 4) -> list[int]:
    """
    生成 n 道题的花色计划。
    至少 min_single 道清一色（n_suits=1），其余为双色（n_suits=2）。
    """
    n_single = rng.randint(min_single, min(6, n))
    plan = [1] * n_single + [2] * (n - n_single)
    rng.shuffle(plan)
    return plan


def generate_quiz_hand(rng: random.Random | None = None, n_suits: int = 2) -> QuizHand:
    """
    从四川麻将中随机抽 14 张，保证至少一种打法可以听牌。
    n_suits=1：清一色（单色牌堆）；n_suits=2：双色牌堆（缺一门）。
    """
    if rng is None:
        rng = random.Random()

    suits = list(SICHUAN_SUITS)
    while True:
        chosen = rng.sample(suits, n_suits)
        deck = [Tile(s, v) for s in chosen for v in range(1, 10) for _ in range(4)]
        rng.shuffle(deck)
        hand = sorted(deck[:14])
        options = best_discards(hand, require_que_yi_men=True)
        if options:
            return QuizHand(hand=hand, options=options, max_tenpai=options[0].tenpai_count)


# ── 会话工厂 ──────────────────────────────────────────────────

def create_session_with_first(
    seed: int | None = None,
    total: int = 10,
) -> tuple[QuizSession, random.Random]:
    """
    仅生成第 1 题，返回 (session, rng)。
    调用方负责将 rng 传给 generate_remaining() 完成后台生成。
    """
    rng = random.Random(seed)
    plan = _make_suits_plan(rng, total)
    first = generate_quiz_hand(rng, n_suits=plan[0])
    sess = QuizSession(
        questions=[first],
        total_expected=total,
        suits_plan=plan,
    )
    return sess, rng


def generate_remaining(sess: QuizSession, rng: random.Random, n: int = 9) -> None:
    """
    后台线程入口：生成剩余 n 道题并逐一追加到 sess。
    异常不向外抛出，通过 sess.mark_generation_error 记录。
    """
    try:
        start_idx = len(sess.questions)
        for i in range(n):
            idx = start_idx + i
            n_suits = sess.suits_plan[idx] if idx < len(sess.suits_plan) else 2
            q = generate_quiz_hand(rng, n_suits=n_suits)
            sess.append_question(q)
        sess.mark_generation_done()
    except Exception as exc:
        sess.mark_generation_error(str(exc))


def create_session(n_questions: int = 10, seed: int | None = None) -> QuizSession:
    """同步生成全部题目（供测试 / CLI 使用）。"""
    rng = random.Random(seed)
    plan = _make_suits_plan(rng, n_questions)
    questions = [generate_quiz_hand(rng, n_suits=plan[i]) for i in range(n_questions)]
    sess = QuizSession(
        questions=questions,
        total_expected=n_questions,
        generation_done=True,
        suits_plan=plan,
    )
    return sess
