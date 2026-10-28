"""
Control-test: сервис для проведения тестирования студентов по Wi-Fi.

Логика:
- Студент открывает "/", вводит имя, подключается по WebSocket "/ws/student".
- Преподаватель открывает "/teacher", задаёт ожидаемое число студентов,
  видит счётчик подключённых в реальном времени, загружает тест в формате
  GIFT, может начать тест (кнопка активна, когда подключено >= ожидаемого,
  либо принудительно).
- После старта всем студентам рассылается первый вопрос теста.
- Студенты отвечают по одному вопросу за раз, ответы сохраняются на сервере.
- Преподаватель может выгрузить результаты после завершения теста.

Важно: сервис держит состояние в памяти одного процесса. Запускать строго
ОДНИМ процессом uvicorn, без gunicorn с несколькими воркерами.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from dataclasses import dataclass, field

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Control Test")

APP_DIR = Path(__file__).resolve().parent
if (APP_DIR / "static").is_dir():
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


# ---------------------------------------------------------------------------
# GIFT-парсер (упрощённая версия: multiple choice, true/false, short answer)
# ---------------------------------------------------------------------------

@dataclass
class Answer:
    text: str
    correct: bool


@dataclass
class Question:
    id: str
    title: str
    text: str
    qtype: str  # "multiple_choice" | "true_false" | "short_answer"
    answers: list[Answer] = field(default_factory=list)

    def to_public_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "type": self.qtype,
            "multiple": self.qtype == "multiple_choice" and sum(a.correct for a in self.answers) > 1,
            "answers": [a.text for a in self.answers] if self.qtype != "short_answer" else [],
        }

    def check(self, given) -> bool:
        if self.qtype == "short_answer":
            given_norm = str(given).strip().lower()
            correct_texts = {a.text.strip().lower() for a in self.answers if a.correct}
            return given_norm in correct_texts
        if self.qtype == "true_false":
            correct = next((a.text for a in self.answers if a.correct), "TRUE")
            return str(given).strip().upper() == correct.upper()
        correct_indices = {i for i, a in enumerate(self.answers) if a.correct}
        if isinstance(given, list):
            given_indices = {int(x) for x in given}
        else:
            given_indices = {int(given)}
        return given_indices == correct_indices


_TITLE_RE = re.compile(r"^::(.*?)::\s*(.*)$", re.DOTALL)
_COMMENT_RE = re.compile(r"^\s*//.*$", re.MULTILINE)


def _strip_comments(text: str) -> str:
    return _COMMENT_RE.sub("", text)


def _split_blocks(text: str) -> list[str]:
    text = _strip_comments(text)
    raw_blocks = re.split(r"\n\s*\n+", text.strip())
    return [b.strip() for b in raw_blocks if b.strip()]


def _parse_block(block: str) -> Question | None:
    title = ""
    body = block

    m = _TITLE_RE.match(block)
    if m:
        title = m.group(1).strip()
        body = m.group(2).strip()

    brace_match = re.search(r"\{(.*)\}", body, re.DOTALL)
    if not brace_match:
        return None

    question_text = body[: brace_match.start()].strip()
    answer_block = brace_match.group(1).strip()

    qid = str(uuid.uuid4())
    if not title:
        title = question_text[:60]

    if answer_block.upper() in ("T", "TRUE"):
        return Question(id=qid, title=title, text=question_text, qtype="true_false",
                         answers=[Answer("TRUE", True), Answer("FALSE", False)])
    if answer_block.upper() in ("F", "FALSE"):
        return Question(id=qid, title=title, text=question_text, qtype="true_false",
                         answers=[Answer("TRUE", False), Answer("FALSE", True)])

    parts = re.split(r"(?=[=~])", answer_block)
    parts = [p.strip() for p in parts if p.strip()]

    answers: list[Answer] = []
    for part in parts:
        if part.startswith("="):
            clean = re.sub(r"^=(%-?\d+%)?", "", part).strip()
            clean = re.sub(r"#.*$", "", clean).strip()
            answers.append(Answer(clean, True))
        elif part.startswith("~"):
            clean = re.sub(r"^~(%-?\d+%)?", "", part).strip()
            clean = re.sub(r"#.*$", "", clean).strip()
            answers.append(Answer(clean, False))

    if not answers:
        return None

    if len(answers) == 1 and answers[0].correct:
        return Question(id=qid, title=title, text=question_text, qtype="short_answer", answers=answers)

    return Question(id=qid, title=title, text=question_text, qtype="multiple_choice", answers=answers)


def parse_gift(text: str) -> list[Question]:
    questions = []
    for block in _split_blocks(text):
        q = _parse_block(block)
        if q is not None:
            questions.append(q)
    return questions


# ---------------------------------------------------------------------------
# Состояние приложения
# ---------------------------------------------------------------------------

class AppState:
    def __init__(self) -> None:
        self.expected: int = 0
        self.test_started: bool = False
        self.test_finished: bool = False
        self.questions: list[Question] = []
        self.current_index: int = -1
        # student_id -> {"name": str, "ws": WebSocket}
        self.students: dict[str, dict] = {}
        # student_id -> {question_id: answer}
        self.answers: dict[str, dict] = {}
        self.teacher_sockets: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    def connected_count(self) -> int:
        return sum(s.get("ws") is not None for s in self.students.values())


state = AppState()


async def notify_teachers() -> None:
    payload = {
        "type": "status",
        "connected": state.connected_count(),
        "expected": state.expected,
        "test_started": state.test_started,
        "questions_loaded": len(state.questions),
        "current_index": state.current_index,
        "students": [s["name"] for s in state.students.values() if s.get("ws") is not None],
    }
    dead = []
    for ws in tuple(state.teacher_sockets):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.teacher_sockets.discard(ws)


async def broadcast_question(question: Question) -> None:
    payload = {"type": "question", "index": state.current_index,
               "total": len(state.questions), "question": question.to_public_dict()}
    dead_ids = []
    for sid, info in tuple(state.students.items()):
        if info.get("ws") is None:
            continue
        try:
            await info["ws"].send_json(payload)
        except Exception:
            dead_ids.append(sid)
    for sid in dead_ids:
        state.students[sid]["ws"] = None


# ---------------------------------------------------------------------------
# HTTP-маршруты
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def join_page(request: Request):
    return templates.TemplateResponse(request, "student.html", {})


@app.get("/teacher", response_class=HTMLResponse)
async def teacher_page(request: Request):
    return templates.TemplateResponse(request, "teacher.html", {})


@app.get("/test", response_class=HTMLResponse)
async def test_page(request: Request):
    return templates.TemplateResponse(request, "student.html", {})


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "connected": state.connected_count(), "expected": state.expected}


@app.post("/api/upload_test")
async def upload_test(file: UploadFile = File(...)):
    if state.test_started:
        return JSONResponse({"ok": False, "error": "Сначала завершите текущий тест"}, status_code=409)
    content = (await file.read()).decode("utf-8", errors="replace")
    questions = parse_gift(content)
    if not questions:
        return JSONResponse({"ok": False, "error": "Не удалось распознать вопросы в файле"}, status_code=400)

    async with state.lock:
        state.questions = questions
        state.test_finished = False
        state.current_index = -1
        state.answers = {}

    await notify_teachers()
    return {"ok": True, "count": len(questions)}


@app.get("/api/results")
async def get_results():
    rows = []
    for sid, info in state.students.items():
        student_answers = state.answers.get(sid, {})
        correct_count = 0
        for q in state.questions:
            given = student_answers.get(q.id)
            if given is not None and q.check(given):
                correct_count += 1
        rows.append({
            "name": info["name"],
            "answered": len(student_answers),
            "correct": correct_count,
            "total": len(state.questions),
        })
    return {"results": rows, "total_questions": len(state.questions)}


# ---------------------------------------------------------------------------
# WebSocket: студент
# ---------------------------------------------------------------------------

@app.websocket("/ws/student")
async def ws_student(websocket: WebSocket, name: str = "Студент", student_id: str = ""):
    await websocket.accept()
    name = name.strip()[:100] or "Студент"
    async with state.lock:
        if student_id not in state.students:
            student_id = str(uuid.uuid4())
        previous = state.students.get(student_id, {}).get("ws")
        state.students[student_id] = {"name": name, "ws": websocket}
        state.answers.setdefault(student_id, {})
    if previous is not None:
        await previous.close(code=4001)
    await notify_teachers()
    await websocket.send_json({
        "type": "joined", "student_id": student_id,
        "test_started": state.test_started, "test_finished": state.test_finished,
    })
    if state.test_finished:
        await websocket.send_json({"type": "finished"})
    elif state.test_started and 0 <= state.current_index < len(state.questions):
        question = state.questions[state.current_index]
        saved = state.answers[student_id]
        await websocket.send_json({
            "type": "question", "index": state.current_index, "total": len(state.questions),
            "question": question.to_public_dict(),
            "answered": question.id in saved, "answer": saved.get(question.id),
        })
    try:
        while True:
            msg = await websocket.receive_json()
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg.get("type") == "answer":
                qid, value = msg.get("question_id"), msg.get("value")
                async with state.lock:
                    if state.students[student_id].get("ws") is not websocket:
                        break
                    question = state.questions[state.current_index] if state.test_started and 0 <= state.current_index < len(state.questions) else None
                    valid = question is not None and qid == question.id
                    if valid and question.qtype == "short_answer":
                        valid = isinstance(value, str) and 0 < len(value.strip()) <= 2000
                        if valid:
                            value = value.strip()
                    elif valid and question.qtype == "true_false":
                        valid = isinstance(value, str) and value in ("TRUE", "FALSE")
                    elif valid:
                        valid = (isinstance(value, list) and 0 < len(value) <= len(question.answers)
                                 and all(type(v) is int and 0 <= v < len(question.answers) for v in value)
                                 and len(set(value)) == len(value))
                    if valid:
                        state.answers.setdefault(student_id, {}).setdefault(qid, value)
                if not valid:
                    await websocket.send_json({"type": "error", "message": "Ответ не принят. Проверьте, что вопрос ещё открыт и ответ заполнен."})
                    continue
                await websocket.send_json({"type": "answer_saved", "question_id": qid})
                await notify_teachers()
    except WebSocketDisconnect:
        pass
    finally:
        async with state.lock:
            if state.students.get(student_id, {}).get("ws") is websocket:
                state.students[student_id]["ws"] = None
        await notify_teachers()


# ---------------------------------------------------------------------------
# WebSocket: преподаватель
# ---------------------------------------------------------------------------

@app.websocket("/ws/teacher")
async def ws_teacher(websocket: WebSocket):
    await websocket.accept()
    state.teacher_sockets.add(websocket)
    await notify_teachers()

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("type")

            if action == "set_expected":
                try:
                    value = int(data.get("value", 0))
                except (TypeError, ValueError):
                    value = 0
                state.expected = max(0, value)
                await notify_teachers()

            elif action == "start_test":
                if state.test_started:
                    continue
                force = bool(data.get("force", False))
                ready = state.connected_count() >= state.expected and state.expected > 0
                has_questions = len(state.questions) > 0
                if (ready or force) and has_questions:
                    state.test_started = True
                    state.test_finished = False
                    state.current_index = 0
                    await broadcast_question(state.questions[0])
                    await notify_teachers()
                elif not has_questions:
                    await websocket.send_json({"type": "error", "message": "Сначала загрузите тест (GIFT-файл)"})
                else:
                    await websocket.send_json({"type": "error", "message": "Не все студенты подключились"})

            elif action == "next_question":
                if not state.test_started:
                    continue
                if state.current_index + 1 < len(state.questions):
                    state.current_index += 1
                    await broadcast_question(state.questions[state.current_index])
                    await notify_teachers()
                else:
                    state.test_started = False
                    state.test_finished = True
                    dead_ids = []
                    for sid, info in tuple(state.students.items()):
                        if info.get("ws") is None:
                            continue
                        try:
                            await info["ws"].send_json({"type": "finished"})
                        except Exception:
                            dead_ids.append(sid)
                    for sid in dead_ids:
                        state.students[sid]["ws"] = None
                    await notify_teachers()

            elif action == "reset":
                state.test_started = False
                state.test_finished = False
                state.expected = 0
                state.questions = []
                state.current_index = -1
                state.answers = {}
                for info in tuple(state.students.values()):
                    if info.get("ws") is not None:
                        try:
                            await info["ws"].send_json({"type": "reset"})
                        except Exception:
                            pass
                await notify_teachers()

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        state.teacher_sockets.discard(websocket)
