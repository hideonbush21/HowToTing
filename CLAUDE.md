# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

四川麻将单人辅助教学系统。每轮 10 道练习题，每题展示 14 张牌，用户选择打出哪张可以听牌，以听牌种数最多为最优解。仅使用万子/条子/筒子三色（108 张），无风牌/字牌。

线上部署于 Vercel：`git push origin main` 即自动触发部署。

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
  rules.py   — 纯函数：is_win / is_tenpai / tenpai_tiles / best_discards
               + find_tenpai_structure / find_structure_for_tile（听牌结构分解）
  quiz.py    — 教学层：generate_quiz_hand / QuizSession / create_session
  server.py  — FastAPI，/quiz 路由（教学）+ /rooms + WebSocket（多人，保留不动）
  game.py    — 多人对战状态机（Room/Player/Phase），与教学系统无交叉
frontend/
  dist/index.html — 单文件前端，含全部 CSS/JS（无构建步骤）
麻将素材/          — 27 张游戏牌图片（一万~九万、一条~九条、一饼~九饼），由 /tiles/ 路由服务
api/
  index.py   — Vercel serverless 入口（from majiang.server import app）
tests/
  test_core.py — 36 个测试覆盖四层
```

### 关键分层约定

- **依赖方向单向**：`server` → `quiz` → `rules` → `tiles`，`game` 独立挂在 `tiles`/`rules`
- `rules.py` 全部为纯函数，无副作用，可直接测试无需 Room 实例
- `quiz.py` 的 `QuizSession` 存活于进程内存；Vercel 无状态，靠 session_id token 重建（见下）
- 静态文件路径使用 `Path(__file__).parent.parent` 绝对路径，不依赖 CWD

### 听牌数计算口径（已决策）

采用**理论种数**：遍历 27 种四川麻将牌型，满足 `is_win(hand_13 + [t])` 即计入，不考虑牌墙剩余张数。

### 清一色比例控制

每局 10 题中至少 4 题为清一色（单色）。由 `_make_suits_plan(rng, n=10, min_single=4)` 在会话创建时生成 `suits_plan`，存入 `QuizSession.suits_plan`，后台生成线程按此计划执行。

### Vercel 无状态 session（重要）

Vercel serverless 冷启动时进程内存清空，原有 UUID session_id 会导致 SESSION_NOT_FOUND。

**解法**：session_id 是携带状态的 HMAC 签名 token，格式：`seed.index.answered.correct.sig8`。

- `_make_sid(seed, index, answered, correct)` — 编码
- `_parse_sid(sid)` — 解码并校验签名
- `_get_or_reconstruct(sid)` — 优先从内存取；缺失时用 `create_session(seed=seed)` 重建，再还原进度

答题后 `submit_answer` 返回 `next_session_id`，前端必须用新 id 发起后续请求。

### 听牌结构分解

`find_structure_for_tile(hand_13, wait_tile)` 为指定听牌单独计算 `TenpaiStructure`，与通用的 `find_tenpai_structure` 的区别在于：强制该 wait_tile 构成 wait_part 的完成牌，因此双碰等多解构牌型对每张听牌会返回不同的说明。

**重要约束**：wait_tile 不一定在手牌中（两面/嵌张/边张形式），校验逻辑只能靠 `_can_form_one_meld(wp + [wait_tile])`，不能用 `if wait_tile not in wp: continue`。

### API 端点

| 端点 | 说明 |
|------|------|
| `POST /quiz/sessions` | 创建 10 题会话，返回 `session_id`（token）及第 1 题 |
| `GET /quiz/sessions/{sid}` | 查询进度 |
| `GET /quiz/sessions/{sid}/current` | 获取当前题目手牌（不含答案）|
| `POST /quiz/sessions/{sid}/answer` | 提交答案，返回评分、`best_options`（含 `tile_structures`）和 `next_session_id` |

`best_options[i].tile_structures` 是 `{ "1万": TenpaiStructure, ... }` 字典，前端逐牌展示牌谱时使用。

正确判定：`tenpai_count == max_tenpai`（并列最优均算正确）。

### 前端说明

- 无构建步骤，直接编辑 `frontend/dist/index.html`
- 牌面图片 URL：`/tiles/${CN字}${花色}.png`，CN 字映射 `['一','二',...,'九']`
- 听牌区每张牌可点击，`selectWaitTile(tileStr)` 切换左栏牌谱，高亮当前选中牌
- 右栏最优打法行可点击，切换左栏展示对应打法的听牌和牌谱

## 扩展点

- **题目难度分级**：在 `generate_quiz_hand()` 加 `min_options: int` 参数，筛选有多种打法的手牌
- **实战模式**：`tenpai_tiles()` 预留 `known_discards` 参数，传入已出牌列表后按实际剩余张数计分
- **番型计分**：在 `rules.py` 新增 `score_hand()` 纯函数
