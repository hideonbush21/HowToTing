# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

四川麻将单人辅助教学系统。每轮 10 道练习题，每题展示 14 张牌，用户选择打出哪张可以听牌，以听牌种数最多为最优解。仅使用万子/条子/筒子三色（108 张），无风牌/字牌。

## 常用命令

```bash
# 安装依赖（开发模式）
pip install -e ".[dev]"

# 启动开发服务器
uvicorn majiang.server:app --reload --port 8000

# 运行全量测试
pytest

# 运行单个测试类
pytest tests/test_core.py::TestTenpai -v

# Lint
ruff check majiang/ tests/
```

## 架构概览

```
majiang/
  tiles.py   — 牌数据结构：Tile/Suit；sichuan_deck()（108张）/ full_deck()（136张）
  rules.py   — 纯函数：is_win / is_tenpai / tenpai_tiles / best_discards / DiscardOption
  quiz.py    — 教学层：generate_quiz_hand / QuizSession / create_session
  server.py  — FastAPI，/quiz 路由（教学）+ /rooms + WebSocket（多人，保留不动）
  game.py    — 多人对战状态机（Room/Player/Phase），与教学系统无交叉
tests/
  test_core.py — 25 个测试覆盖四层
```

### 关键分层约定

- **依赖方向单向**：`server` → `quiz` → `rules` → `tiles`，`game` 独立挂在 `tiles`/`rules`
- `tiles.py` 同时维护两个牌堆函数，命名必须区分：`sichuan_deck()`（108张）vs `full_deck()`（136张）
- `rules.py` 全部为纯函数，无副作用，可直接测试无需 Room 实例
- `quiz.py` 的 `QuizSession` 仅存活于内存，服务重启丢失为预期行为

### 听牌数计算口径（已决策）

采用**理论种数**（方案 A）：遍历 27 种四川麻将牌型，满足 `is_win(hand_13 + [t])` 即计入。不考虑牌墙剩余张数。原因：单人练习题无真实牌墙，方案 A 答案确定，是标准教学口径。

### API 端点

| 端点 | 说明 |
|------|------|
| `POST /quiz/sessions` | 创建 10 题会话，返回 `session_id` |
| `GET /quiz/sessions/{sid}` | 查询进度 |
| `GET /quiz/sessions/{sid}/current` | 获取当前题目手牌（不含答案）|
| `POST /quiz/sessions/{sid}/answer` | 提交答案 `{"discard": "1万"}`，返回评分与标准答案 |

正确判定：用户选择的打法 `tenpai_count == max_tenpai`（并列最优均算正确）。

## 扩展点

- **题目难度分级**：在 `generate_quiz_hand()` 加 `min_options: int` 参数，筛选有多种打法的手牌
- **实战模式**：`tenpai_tiles()` 预留 `known_discards` 参数，传入已出牌列表后按实际剩余张数计分
- **番型计分**：在 `rules.py` 新增 `score_hand()` 纯函数
