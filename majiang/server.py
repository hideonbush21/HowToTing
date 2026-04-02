"""
FastAPI 服务入口
  - /quiz/*  单人教学（懒加载：首题同步返回，剩余后台生成）
  - /rooms/* WebSocket 多人对战（保留）
"""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import os
import random
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
_SESSION_SECRET = os.environ.get("SESSION_SECRET", "majiang-quiz-secret").encode()

from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .game import Room
from .tiles import Tile, tiles_from_str
from .quiz import QuizSession, create_session, create_session_with_first, generate_remaining
from .rules import find_structure_for_tile

app = FastAPI(title="麻将")

_sessions: dict[str, QuizSession] = {}
_rooms: dict[str, Room] = {}
_connections: dict[str, dict[str, WebSocket]] = {}


# ═══════════════════════════════════════════════════════════════
#  Session token：seed.index.answered.correct.sig8
#  任意实例均可从 token 重建 session，无需共享内存。
# ═══════════════════════════════════════════════════════════════

def _make_sid(seed: int, index: int, answered: int, correct: int, endgame: int = 0) -> str:
    payload = f"{seed}.{index}.{answered}.{correct}.{endgame}"
    sig = hmac.new(_SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:8]
    return f"{payload}.{sig}"


def _parse_sid(sid: str) -> tuple[int, int, int, int, int] | None:
    """解码 token，签名不合法返回 None。返回 (seed, index, answered, correct, endgame)。"""
    parts = sid.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    expected = hmac.new(_SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()[:8]
    if not hmac.compare_digest(sig, expected):
        return None
    sub = payload.split(".")
    # 向下兼容旧 4 字段 token（无 endgame）
    if len(sub) == 4:
        try:
            return int(sub[0]), int(sub[1]), int(sub[2]), int(sub[3]), 0
        except ValueError:
            return None
    if len(sub) == 5:
        try:
            return int(sub[0]), int(sub[1]), int(sub[2]), int(sub[3]), int(sub[4])
        except ValueError:
            return None
    return None


def _get_or_reconstruct(sid: str) -> QuizSession:
    """优先从内存取；冷启动时从 token 重建。"""
    if sid in _sessions:
        return _sessions[sid]
    parsed = _parse_sid(sid)
    if parsed is None:
        raise HTTPException(404, {"error": "SESSION_NOT_FOUND", "message": "会话不存在或已过期"})
    seed, index, answered, correct, endgame = parsed
    sess = create_session(seed=seed, endgame=bool(endgame))
    sess.seed = seed
    sess.session_id = sid
    sess.current_index = index
    sess.answered = answered
    sess.correct_count = correct
    _sessions[sid] = sess
    return sess


# ═══════════════════════════════════════════════════════════════
#  单人教学：/quiz 路由组
# ═══════════════════════════════════════════════════════════════

class NewSessionReq(BaseModel):
    seed: int | None = None
    endgame: bool = False

class AnswerReq(BaseModel):
    discard: str

def _structure_dict(s) -> dict | None:
    if not s:
        return None
    return {
        "melds":     [[str(t) for t in m] for m in s.melds],
        "pair":      [str(t) for t in s.pair] if s.pair else None,
        "wait_part": [str(t) for t in s.wait_part],
        "wait_type": s.wait_type,
    }


def _opt_dict(opt, hand_13: list[Tile] | None = None) -> dict:
    d = {
        "discard":          str(opt.discard),
        "tenpai_tiles":     [str(t) for t in opt.tenpai_tiles],
        "tenpai_count":     opt.tenpai_count,
        "effective_count":  opt.effective_count,
    }
    if opt.structure:
        d["structure"] = _structure_dict(opt.structure)
    if hand_13 is not None:
        d["tile_structures"] = {
            str(t): _structure_dict(find_structure_for_tile(hand_13, t))
            for t in opt.tenpai_tiles
        }
    return d


@app.post("/quiz/sessions")
async def new_session(req: NewSessionReq = NewSessionReq(),
                      background_tasks: BackgroundTasks = BackgroundTasks()):
    """
    立即返回第 1 题。session_id 是携带状态的签名 token，
    冷启动时可从 token 重建 session，无需外部存储。
    """
    seed = req.seed if req.seed is not None else random.randint(0, 2**31 - 1)
    sess, rng = create_session_with_first(seed=seed, total=10, endgame=req.endgame)
    sess.seed = seed
    sid = _make_sid(seed, 0, 0, 0, int(req.endgame))
    sess.session_id = sid
    _sessions[sid] = sess

    # 后台生成剩余 9 题（CPU 密集 → to_thread 避免阻塞事件循环）
    async def _fill():
        await asyncio.to_thread(generate_remaining, sess, rng, 9)

    background_tasks.add_task(_fill)

    first_q = sess.questions[0]
    first_q_resp: dict = {
        "index": 0,
        "total": sess.total_expected,
        "hand":  [str(t) for t in first_q.hand],
    }
    if req.endgame and first_q.remaining_tiles is not None:
        first_q_resp["remaining_tiles"] = first_q.remaining_tiles

    return {
        "session_id":    sid,
        "total_expected": sess.total_expected,
        "current_index": 0,
        "endgame":       req.endgame,
        "first_question": first_q_resp,
    }


@app.get("/quiz/sessions/{sid}")
def session_status(sid: str):
    sess = _get_or_reconstruct(sid)
    with sess._lock:
        return {
            "session_id":      sess.session_id,
            "total_expected":  sess.total_expected,
            "questions_ready": len(sess.questions),
            "current_index":   sess.current_index,
            "generation_done": sess.generation_done,
            "score":           {"answered": sess.answered, "correct": sess.correct_count},
            "finished":        sess.finished,
        }


@app.get("/quiz/sessions/{sid}/current")
def current_question(sid: str):
    sess = _get_or_reconstruct(sid)
    if sess.generation_error:
        raise HTTPException(500, {"error": "GENERATION_FAILED",
                                   "message": sess.generation_error})
    if sess.finished:
        raise HTTPException(400, {"error": "SESSION_FINISHED",
                                   "message": "该会话已完成全部题目"})
    if not sess.current_question_ready():
        raise HTTPException(503, {"error": "QUESTION_NOT_READY",
                                   "message": "题目生成中，请稍后重试",
                                   "retry_after_ms": 200})
    q = sess.current_question()
    resp: dict = {
        "session_id": sid,
        "index":      sess.current_index,
        "total":      sess.total_expected,
        "hand":       [str(t) for t in q.hand],
        "endgame":    sess.endgame,
    }
    if sess.endgame and q.remaining_tiles is not None:
        resp["remaining_tiles"] = q.remaining_tiles
    return resp


@app.post("/quiz/sessions/{sid}/answer")
def submit_answer(sid: str, req: AnswerReq):
    sess = _get_or_reconstruct(sid)
    if sess.finished:
        raise HTTPException(400, {"error": "SESSION_FINISHED",
                                   "message": "该会话已完成全部题目"})
    if not sess.current_question_ready():
        raise HTTPException(503, {"error": "QUESTION_NOT_READY",
                                   "message": "题目生成中，请稍后重试",
                                   "retry_after_ms": 200})
    try:
        ts = tiles_from_str(req.discard)
        if len(ts) != 1:
            raise ValueError()
        discard_tile = ts[0]
    except (KeyError, ValueError):
        raise HTTPException(422, {"error": "INVALID_DISCARD",
                                   "message": f"无效牌: {req.discard}"})

    q = sess.current_question()
    if discard_tile not in q.hand:
        raise HTTPException(422, {"error": "INVALID_DISCARD",
                                   "message": f"{req.discard} 不在当前手牌中"})

    result = sess.submit_answer(discard_tile)

    # 更新 session_id（携带最新状态），前端需用新 id 发起后续请求
    new_sid = _make_sid(sess.seed, sess.current_index, sess.answered, sess.correct_count, int(sess.endgame))
    sess.session_id = new_sid
    _sessions[new_sid] = sess
    _sessions.pop(sid, None)

    # 每个打法选项的 13 张剩余手牌（用于计算每张听牌的独立牌谱）
    def _hand13(opt_discard: Tile) -> list[Tile]:
        h = list(q.hand)
        h.remove(opt_discard)
        return h

    return {
        "correct":         result.correct,
        "your_choice":     _opt_dict(result.your_choice,  _hand13(result.your_choice.discard)),
        "best_options":    [_opt_dict(o, _hand13(o.discard)) for o in result.best_options],
        "max_tenpai":      result.max_tenpai,
        "max_effective":   q.max_effective,
        "endgame":         sess.endgame,
        "remaining_tiles": q.remaining_tiles if sess.endgame else None,
        "next_session_id": new_sid,
        "session_progress": {
            "answered": sess.answered,
            "correct":  sess.correct_count,
            "total":    sess.total_expected,
        },
        "finished": sess.finished,
    }


# ═══════════════════════════════════════════════════════════════
#  多人对战：/rooms 路由组
# ═══════════════════════════════════════════════════════════════

class CreateRoomResp(BaseModel):
    room_id: str
    pid: str

class JoinReq(BaseModel):
    name: str


def _get_room(room_id: str) -> Room:
    if room_id not in _rooms:
        raise HTTPException(404, "房间不存在")
    return _rooms[room_id]

def _new_pid() -> str:
    return uuid.uuid4().hex[:8]

def _parse_tile(s: str) -> Tile:
    ts = tiles_from_str(s)
    if len(ts) != 1:
        raise ValueError(f"无效牌: {s}")
    return ts[0]


@app.post("/rooms", response_model=CreateRoomResp)
def create_room(req: JoinReq):
    room = Room()
    player = room.add_player(pid=_new_pid(), name=req.name)
    _rooms[room.room_id] = room
    _connections[room.room_id] = {}
    return CreateRoomResp(room_id=room.room_id, pid=player.pid)

@app.post("/rooms/{room_id}/join")
def join_room(room_id: str, req: JoinReq):
    return {"pid": _get_room(room_id).add_player(pid=_new_pid(), name=req.name).pid}

@app.post("/rooms/{room_id}/start")
def start_game(room_id: str):
    _get_room(room_id).start()
    return {"status": "started"}

@app.get("/rooms/{room_id}/state")
def get_state(room_id: str, pid: str):
    return _get_room(room_id).state_for(pid)


@app.websocket("/ws/{room_id}/{pid}")
async def ws_endpoint(websocket: WebSocket, room_id: str, pid: str):
    _get_room(room_id)
    await websocket.accept()
    _connections.setdefault(room_id, {})[pid] = websocket
    try:
        while True:
            msg: dict = json.loads(await websocket.receive_text())
            await _handle_ws(room_id, pid, msg)
    except WebSocketDisconnect:
        _connections[room_id].pop(pid, None)


async def _handle_ws(room_id: str, pid: str, msg: dict) -> None:
    room = _rooms[room_id]
    try:
        action = msg.get("action")
        if action == "discard":
            tile = _parse_tile(msg["tile"])
            responses = room.discard(pid, tile)
            await _broadcast(room_id, {"event": "discard", "pid": pid, "tile": msg["tile"]})
            if responses:
                await _broadcast(room_id, {"event": "action_required", "responses": responses})
            else:
                drawn = room.draw_and_next()
                next_pid = room.players[room.current_player_idx].pid
                await _broadcast(room_id, {"event": "draw", "pid": next_pid,
                                           "tile": str(drawn) if drawn else None})
        elif action == "peng":
            room.peng(pid)
            await _broadcast(room_id, {"event": "peng", "pid": pid})
        elif action == "win":
            result = room.declare_win(pid)
            await _broadcast(room_id, {"event": "win", **result})
    except Exception as e:
        ws = _connections.get(room_id, {}).get(pid)
        if ws:
            await ws.send_text(json.dumps({"event": "error", "msg": str(e)}))


async def _broadcast(room_id: str, payload: dict) -> None:
    msg = json.dumps(payload, ensure_ascii=False)
    for ws in list(_connections.get(room_id, {}).values()):
        try:
            await ws.send_text(msg)
        except Exception:
            pass


# ── 静态文件 ─────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "frontend" / "dist")), name="static")
app.mount("/tiles", StaticFiles(directory=str(BASE_DIR / "麻将素材")), name="tiles")

@app.get("/")
def index():
    return FileResponse(str(BASE_DIR / "frontend" / "dist" / "index.html"))
