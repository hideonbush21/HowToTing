"""测试：四川麻将规则引擎 + 教学系统（缺一门规则 + 懒加载）"""
import pytest
from majiang.tiles import Tile, Suit, sichuan_deck, all_sichuan_tile_types, full_deck, tiles_from_str
from majiang.rules import is_win, is_tenpai, tenpai_tiles, best_discards
from majiang.quiz import create_session, create_session_with_first, generate_remaining, generate_quiz_hand


def t(s: str) -> list[Tile]:
    return tiles_from_str(s)


# ── tiles ────────────────────────────────────────────────────

class TestDecks:
    def test_sichuan_deck_size(self):
        assert len(sichuan_deck()) == 108

    def test_sichuan_no_feng_jian(self):
        suits = {tile.suit for tile in sichuan_deck()}
        assert suits == {Suit.WAN, Suit.TIAO, Suit.BING}

    def test_sichuan_each_tile_4_copies(self):
        from collections import Counter
        c = Counter(sichuan_deck())
        assert all(v == 4 for v in c.values()) and len(c) == 27

    def test_all_sichuan_tile_types_count(self):
        assert len(all_sichuan_tile_types()) == 27

    def test_full_deck_size(self):
        assert len(full_deck()) == 136


# ── is_win ───────────────────────────────────────────────────

class TestIsWin:
    def test_standard_single_suit(self):
        hand = t("1万1万1万2万3万4万5万6万7万8万9万9万9万") + [Tile(Suit.WAN, 1)]
        assert is_win(sorted(hand))

    def test_seven_pairs_single_suit(self):
        hand = t("1万1万2万2万3万3万4万4万5万5万6万6万7万7万")
        assert is_win(hand)

    def test_two_suit_standard(self):
        # 123万 456条 789条 + 111万 + 55万 — 仅万+条
        hand = sorted(t("1万1万1万2万3万4万5万6万7万1条1条1条9条9条"))
        assert is_win(hand)

    # ── 缺一门核心测试 ────────────────────────────────────────

    def test_three_suits_rejected_by_default(self):
        """三花色合法牌型在默认（缺一门）模式下不算胡牌。"""
        # 123万 456条 789饼 + 111条 + 55饼 — 万+条+饼 三花色
        hand = sorted(t("1万2万3万4条5条6条7饼8饼9饼1条1条1条5饼5饼"))
        assert not is_win(hand)

    def test_three_suits_accepted_when_disabled(self):
        """同一手牌关闭缺一门后胡牌成立。"""
        hand = sorted(t("1万2万3万4条5条6条7饼8饼9饼1条1条1条5饼5饼"))
        assert is_win(hand, require_que_yi_men=False)

    def test_not_win(self):
        hand = t("1万2万3万4万5万6万7万8万9万1条2条3条4条5条")
        assert not is_win(hand)

    def test_wrong_count(self):
        assert not is_win(t("1万2万3万"))


# ── tenpai ───────────────────────────────────────────────────

class TestTenpai:
    def test_single_wait_two_suits(self):
        # 万+条两花色手牌
        hand = sorted(t("1万2万3万4万5万6万7万8万1条1条1条5条5条"))
        waits = tenpai_tiles(hand)
        assert len(waits) > 0
        # 缺一门：结果中不应包含饼子
        assert all(w.suit != Suit.BING for w in waits)

    def test_no_third_suit_in_waits(self):
        """双色手牌的听牌结果中不含第三种花色。"""
        hand = sorted(t("1万2万3万4万5万6万7万8万1条1条1条5条5条"))
        waits = tenpai_tiles(hand)
        suits_in_waits = {w.suit for w in waits}
        assert Suit.BING not in suits_in_waits

    def test_not_tenpai(self):
        # 两花色散牌
        hand = sorted(t("1万3万5万7万9万1条3条5条7条9条1万3万5万"))
        assert tenpai_tiles(hand) == []

    def test_is_tenpai_consistency(self):
        hand = sorted(t("1万2万3万4万5万6万7万8万1条1条1条5条5条"))
        assert is_tenpai(hand) == bool(tenpai_tiles(hand))

    def test_que_yi_men_disabled_can_return_third_suit(self):
        """关闭缺一门时可以返回第三种花色的听牌。"""
        # 单色 13 张手牌，关闭缺一门
        hand = sorted(t("1万2万3万4万5万6万7万8万1条1条1条5条5条"))
        waits_disabled = tenpai_tiles(hand, require_que_yi_men=False)
        # 不限花色时候选集更大，结果 ≥ 缺一门时的结果
        waits_enabled = tenpai_tiles(hand, require_que_yi_men=True)
        assert len(waits_disabled) >= len(waits_enabled)


# ── best_discards ────────────────────────────────────────────

class TestBestDiscards:
    def test_returns_sorted_descending(self):
        q = generate_quiz_hand()
        options = best_discards(q.hand)
        counts = [o.tenpai_count for o in options]
        assert counts == sorted(counts, reverse=True)

    def test_optimal_is_first(self):
        q = generate_quiz_hand()
        assert best_discards(q.hand)[0].tenpai_count == q.max_tenpai

    def test_discard_in_original_hand(self):
        q = generate_quiz_hand()
        for opt in best_discards(q.hand):
            assert opt.discard in q.hand

    def test_wrong_length(self):
        assert best_discards(t("1万2万3万")) == []

    def test_que_yi_men_param_affects_result(self):
        """require_que_yi_men=False 时可能产生更多的打法选项（三花色手牌）。"""
        import random
        rng = random.Random(0)
        # 强制生成一个三花色手牌
        while True:
            from majiang.tiles import sichuan_deck
            deck = sichuan_deck()
            rng.shuffle(deck)
            hand = sorted(deck[:14])
            suits = {tt.suit for tt in hand}
            if len(suits) == 3:
                break
        r_on  = best_discards(hand, require_que_yi_men=True)
        r_off = best_discards(hand, require_que_yi_men=False)
        # 关闭缺一门时 ≥ 开启时（三花色打法缺一门下不计入）
        assert len(r_off) >= len(r_on)


# ── quiz session ─────────────────────────────────────────────

class TestQuizHandGeneration:
    def test_hand_is_one_or_two_suits(self):
        """双色模式恰好 2 种花色，清一色模式恰好 1 种花色。"""
        import random
        for seed in range(20):
            q2 = generate_quiz_hand(random.Random(seed), n_suits=2)
            assert len({t.suit for t in q2.hand}) == 2, f"seed={seed} n_suits=2 花色数不对"
        for seed in range(20):
            q1 = generate_quiz_hand(random.Random(seed), n_suits=1)
            assert len({t.suit for t in q1.hand}) == 1, f"seed={seed} n_suits=1 花色数不对"

    def test_double_suit_waits_stay_in_hand_suits(self):
        """双色手牌的听牌结果中所有牌的花色必须在手牌花色范围内。"""
        import random
        q = generate_quiz_hand(random.Random(42), n_suits=2)
        hand_suits = {t.suit for t in q.hand}
        for opt in q.options:
            for wait in opt.tenpai_tiles:
                assert wait.suit in hand_suits

    def test_single_suit_hand_has_waits(self):
        """清一色手牌必须有至少一种听牌打法。"""
        import random
        for seed in range(10):
            q = generate_quiz_hand(random.Random(seed), n_suits=1)
            assert len(q.options) > 0


class TestQuizSession:
    def test_create_session_10_questions(self):
        sess = create_session(seed=42)
        assert len(sess.questions) == 10

    def test_deterministic_with_seed(self):
        s1 = create_session(seed=99)
        s2 = create_session(seed=99)
        assert [str(t) for t in s1.questions[0].hand] == [str(t) for t in s2.questions[0].hand]

    def test_create_session_with_first_returns_one_question(self):
        sess, rng = create_session_with_first(seed=5)
        assert len(sess.questions) == 1
        assert sess.total_expected == 10

    def test_generate_remaining_fills_session(self):
        sess, rng = create_session_with_first(seed=5)
        generate_remaining(sess, rng, n=9)
        assert len(sess.questions) == 10
        assert sess.generation_done is True

    def test_deterministic_with_lazy_path(self):
        """懒加载路径与同步路径产生相同的题目序列。"""
        sync_sess = create_session(seed=7)
        lazy_sess, rng = create_session_with_first(seed=7)
        generate_remaining(lazy_sess, rng, n=9)
        for i in range(10):
            assert ([str(t) for t in sync_sess.questions[i].hand] ==
                    [str(t) for t in lazy_sess.questions[i].hand]), f"第 {i+1} 题不一致"

    def test_submit_correct_answer(self):
        sess = create_session(seed=1)
        q = sess.current_question()
        result = sess.submit_answer(q.options[0].discard)
        assert result.correct is True
        assert sess.answered == 1 and sess.correct_count == 1 and sess.current_index == 1

    def test_submit_wrong_answer(self):
        sess = create_session(seed=1)
        q = sess.current_question()
        worst = next((o for o in q.options if o.tenpai_count < q.max_tenpai), None)
        if worst is None:
            pytest.skip("所有打法并列最优")
        result = sess.submit_answer(worst.discard)
        assert result.correct is False and sess.correct_count == 0

    def test_session_finishes_after_all_answered(self):
        sess = create_session(seed=7)
        for _ in range(10):
            q = sess.current_question()
            sess.submit_answer(q.options[0].discard)
        assert sess.finished

    def test_finished_session_raises(self):
        sess = create_session(seed=7)
        for _ in range(10):
            sess.submit_answer(sess.current_question().options[0].discard)
        with pytest.raises(ValueError):
            sess.current_question()

    def test_current_question_ready(self):
        sess, rng = create_session_with_first(seed=3)
        assert sess.current_question_ready() is True
        # 答完第1题后，第2题尚未生成
        sess.submit_answer(sess.current_question().options[0].discard)
        assert sess.current_question_ready() is False

    def test_each_question_one_or_two_suits(self):
        """每道题手牌为 14 张，花色数为 1（清一色）或 2（双色）。"""
        sess = create_session(seed=5)
        for q in sess.questions:
            assert len(q.hand) == 14
            suits = {tile.suit for tile in q.hand}
            assert len(suits) in (1, 2)

    def test_session_has_at_least_4_qingyise(self):
        """每局至少 4 道清一色题目。"""
        for seed in range(5):
            sess = create_session(seed=seed)
            single_suit_count = sum(
                1 for q in sess.questions
                if len({t.suit for t in q.hand}) == 1
            )
            assert single_suit_count >= 4, (
                f"seed={seed} 清一色题数={single_suit_count}，期望>=4"
            )
