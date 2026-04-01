"""
FastAPI 服务入口
  - /quiz/*  单人教学（懒加载：首题同步返回，剩余后台生成）
  - /rooms/* WebSocket 多人对战（保留）
"""
from __future__ import annotations
import asyncio
import json
import uuid

from fastapi import BackgroundTasks, FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .game import Room
from .tiles import Tile, tiles_from_str
from .quiz import QuizSession, create_session_with_first, generate_remaining

app = FastAPI(title="麻将")

_sessions: dict[str, QuizSession] = {}
_rooms: dict[str, Room] = {}
_connections: dict[str, dict[str, WebSocket]] = {}


# ═══════════════════════════════════════════════════════════════
#  单人教学：/quiz 路由组
# ═══════════════════════════════════════════════════════════════

class NewSessionReq(BaseModel):
    seed: int | None = None

class AnswerReq(BaseModel):
    discard: str


def _session_or_404(sid: str) -> QuizSession:
    if sid not in _sessions:
        raise HTTPException(404, {"error": "SESSION_NOT_FOUND", "message": "会话不存在或已过期"})
    return _sessions[sid]

def _opt_dict(opt) -> dict:
    d = {
        "discard":      str(opt.discard),
        "tenpai_tiles": [str(t) for t in opt.tenpai_tiles],
        "tenpai_count": opt.tenpai_count,
    }
    if opt.structure:
        s = opt.structure
        d["structure"] = {
            "melds":     [[str(t) for t in m] for m in s.melds],
            "pair":      [str(t) for t in s.pair] if s.pair else None,
            "wait_part": [str(t) for t in s.wait_part],
            "wait_type": s.wait_type,
        }
    return d


@app.post("/quiz/sessions")
async def new_session(req: NewSessionReq = NewSessionReq(),
                      background_tasks: BackgroundTasks = BackgroundTasks()):
    """
    立即返回第 1 题（内嵌在响应中），后台异步生成剩余 9 题。
    前端无需再请求 /current 即可渲染首题，减少一次 RTT。
    """
    sess, rng = create_session_with_first(seed=req.seed, total=10)
    _sessions[sess.session_id] = sess

    # 后台生成剩余 9 题（CPU 密集 → to_thread 避免阻塞事件循环）
    async def _fill():
        await asyncio.to_thread(generate_remaining, sess, rng, 9)

    background_tasks.add_task(_fill)

    first_q = sess.questions[0]
    return {
        "session_id":    sess.session_id,
        "total_expected": sess.total_expected,
        "current_index": 0,
        "first_question": {
            "index": 0,
            "total": sess.total_expected,
            "hand":  [str(t) for t in first_q.hand],
        },
    }


@app.get("/quiz/sessions/{sid}")
def session_status(sid: str):
    sess = _session_or_404(sid)
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
    sess = _session_or_404(sid)
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
    return {
        "session_id": sid,
        "index":      sess.current_index,
        "total":      sess.total_expected,
        "hand":       [str(t) for t in q.hand],
    }


@app.post("/quiz/sessions/{sid}/answer")
def submit_answer(sid: str, req: AnswerReq):
    sess = _session_or_404(sid)
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
    return {
        "correct":      result.correct,
        "your_choice":  _opt_dict(result.your_choice),
        "best_options": [_opt_dict(o) for o in result.best_options],
        "max_tenpai":   result.max_tenpai,
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

app.mount("/static", StaticFiles(directory="frontend/dist"), name="static")
app.mount("/tiles", StaticFiles(directory="麻将素材"), name="tiles")

@app.get("/")
def index():
    return FileResponse("frontend/dist/index.html")
