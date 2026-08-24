"""
Control-test: сервис для проведения тестирования студентов по Wi-Fi.

Логика:
- Студент открывает "/", вводит имя, подключается по WebSocket "/ws/student".
- Преподаватель открывает "/teacher", задаёт ожидаемое число студентов,
  видит счётчик подключённых в реальном времени, может начать тест
  (кнопка активна, когда подключено >= ожидаемого, либо принудительно).
- После старта всем студентам рассылается команда начать тест.

Важно: сервис держит состояние (список подключённых студентов) в памяти
одного процесса. Запускать строго ОДНИМ процессом uvicorn, без
gunicorn с несколькими воркерами — иначе счётчик подключений будет
считаться некорректно.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Control Test")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


class AppState:
    def __init__(self) -> None:
        self.expected: int = 0
        self.test_started: bool = False
        # student_id -> {"name": str, "ws": WebSocket}
        self.students: dict[str, dict] = {}
        self.teacher_sockets: set[WebSocket] = set()
        self.lock = asyncio.Lock()

    def connected_count(self) -> int:
        return len(self.students)


state = AppState()


async def notify_teachers() -> None:
    payload = {
        "type": "status",
        "connected": state.connected_count(),
        "expected": state.expected,
        "test_started": state.test_started,
        "students": [s["name"] for s in state.students.values()],
    }
    dead = []
    for ws in state.teacher_sockets:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        state.teacher_sockets.discard(ws)


@app.get("/", response_class=HTMLResponse)
async def join_page(request: Request):
    return templates.TemplateResponse("join.html", {"request": request})


@app.get("/teacher", response_class=HTMLResponse)
async def teacher_page(request: Request):
    return templates.TemplateResponse("teacher.html", {"request": request})


@app.get("/test", response_class=HTMLResponse)
async def test_page(request: Request):
    return templates.TemplateResponse("test.html", {"request": request})


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "connected": state.connected_count(), "expected": state.expected}


@app.websocket("/ws/student")
async def ws_student(websocket: WebSocket, name: str = "Студент"):
    await websocket.accept()
    student_id = str(uuid.uuid4())

    async with state.lock:
        state.students[student_id] = {"name": name, "ws": websocket}

    await notify_teachers()
    await websocket.send_json({
        "type": "joined",
        "student_id": student_id,
        "test_started": state.test_started,
    })

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            # Место для расширения: обработка ответов на вопросы теста
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        async with state.lock:
            state.students.pop(student_id, None)
        await notify_teachers()


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
                force = bool(data.get("force", False))
                ready = state.connected_count() >= state.expected and state.expected > 0
                if ready or force:
                    state.test_started = True
                    dead_ids = []
                    for sid, info in state.students.items():
                        try:
                            await info["ws"].send_json({"type": "start"})
                        except Exception:
                            dead_ids.append(sid)
                    for sid in dead_ids:
                        state.students.pop(sid, None)
                    await notify_teachers()
                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": "Не все студенты подключились",
                    })

            elif action == "reset":
                state.test_started = False
                state.expected = 0
                await notify_teachers()

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        state.teacher_sockets.discard(websocket)
