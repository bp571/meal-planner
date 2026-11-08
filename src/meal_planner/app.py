"""FastAPI app: REST endpoints for the tabs, the streamed chat and the static frontend.

Single local user: one global chat session and one agent turn at a time.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from meal_planner import services
from meal_planner.db import session_scope
from meal_planner.models import PlanView, ShoppingList, WeekPlan
from meal_planner.services import ServiceError

STATIC_DIR = Path(__file__).parent / "static"


class Chat:
    """The one conversation: the agent session plus the transcript a page reload restores."""

    def __init__(self) -> None:
        self.session = None  # agent.ChatSession, created on the first turn
        self.transcript: list[dict] = []  # {"role": "user" | "assistant" | "memory", "text": str}
        self.lock = asyncio.Lock()
        self.task: asyncio.Task | None = None

    async def close(self) -> None:
        if self.session is not None:
            session, self.session = self.session, None
            await session.close()
        self.transcript.clear()


chat = Chat()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    if chat.task and not chat.task.done():
        chat.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await chat.task
    await chat.close()


class StaticNoCache(StaticFiles):
    """Static files that the browser revalidates, so an update never runs with stale JS."""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app = FastAPI(title="Meal Planner", lifespan=lifespan)
app.mount("/static", StaticNoCache(directory=STATIC_DIR), name="static")


@app.exception_handler(ServiceError)
async def service_error(_request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"errors": exc.errors})


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


# --- Chat ---


class ChatIn(BaseModel):
    message: str = Field(min_length=1)


async def _run_turn(
    events: Callable[[object], AsyncIterator[dict]], user_text: str | None = None
) -> StreamingResponse:
    """Run one agent turn in a background task and stream its events as NDJSON.

    The task, not the HTTP response, drives the agent: a closed or reloaded tab neither cuts the
    turn short nor leaves the SDK session half-read, and the transcript still gets the whole turn.
    """
    if chat.lock.locked():
        raise HTTPException(409, "A chat turn is running.")
    await chat.lock.acquire()  # free lock: returns without yielding, so no other turn slips in
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def turn() -> None:
        try:
            if user_text is not None:
                chat.transcript.append({"role": "user", "text": user_text})
            if chat.session is None:
                from meal_planner.agent import ChatSession  # lazy: the app imports without the agent

                session = ChatSession()
                await session.start()
                chat.session = session
            reply: dict | None = None  # the assistant message being assembled
            async for event in events(chat.session):
                kind = event.get("type")
                if kind == "text":
                    if reply is None:
                        reply = {"role": "assistant", "text": ""}
                        chat.transcript.append(reply)
                    reply["text"] += event["text"]
                elif kind == "memory":
                    chat.transcript.append({"role": "memory", "text": event["text"]})
                    reply = None
                queue.put_nowait(event)
        except Exception as exc:  # the browser shows it; the next turn may succeed
            queue.put_nowait({"type": "error", "message": str(exc) or type(exc).__name__})
            queue.put_nowait({"type": "done"})
        finally:
            chat.lock.release()
            queue.put_nowait(None)

    chat.task = asyncio.create_task(turn())

    async def stream() -> AsyncIterator[str]:
        while (event := await queue.get()) is not None:
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/api/chat/history")
def chat_history() -> list[dict]:
    return chat.transcript


@app.post("/api/chat/start")
async def chat_start() -> StreamingResponse:
    """Open a new conversation with the agent's greeting. A running conversation gets nothing new."""
    if chat.transcript:
        return StreamingResponse(iter(['{"type": "done"}\n']), media_type="application/x-ndjson")
    return await _run_turn(lambda session: session.greet())


@app.post("/api/chat")
async def chat_send(body: ChatIn) -> StreamingResponse:
    return await _run_turn(lambda session: session.send(body.message), user_text=body.message)


@app.post("/api/chat/reset")
async def chat_reset() -> dict:
    """Close the session and clear the transcript. Profile, dishes and memory stay in the database."""
    if chat.lock.locked():
        raise HTTPException(409, "A chat turn is running.")
    async with chat.lock:
        await chat.close()
    return {"ok": True}


# --- Data for the tabs ---


def _plan_summary(plan: WeekPlan | None) -> dict | None:
    if plan is None:
        return None
    return {"id": plan.id, "status": plan.status, "week_start": plan.week_start.isoformat()}


@app.get("/api/state")
def get_state() -> dict:
    with session_scope() as session:
        return {
            "has_profile": services.get_profile(session) is not None,
            "dish_count": len(services.list_dishes(session)),
            "plan": _plan_summary(services.get_current_plan(session)),
        }


@app.get("/api/plan")
def get_plan() -> PlanView | None:
    with session_scope() as session:
        return services.get_plan_view(session)


@app.get("/api/shopping")
def get_shopping() -> ShoppingList | None:
    with session_scope() as session:
        return services.get_shopping_list(session)


@app.get("/api/dishes")
def get_dishes() -> list[dict]:
    """Active dishes, each with its stats (average rating, times planned)."""
    with session_scope() as session:
        stats = services.dish_stats(session)
        return [
            {
                **dish.model_dump(mode="json"),
                "stats": stats[dish.id].model_dump(mode="json") if dish.id in stats else None,
            }
            for dish in services.list_dishes(session)
        ]


@app.delete("/api/dishes/{dish_id}")
def delete_dish(dish_id: int) -> dict:
    with session_scope() as session:
        services.archive_dish(session, dish_id)
    return {"ok": True}


@app.get("/api/profile")
def get_profile() -> dict:
    """The profile (or null) plus the names of the dishes its fixed items point to."""
    with session_scope() as session:
        profile = services.get_profile(session)
        fixed_ids = {item.dish_id for item in profile.fixed_items} if profile else set()
        names = {
            dish.id: dish.name
            for dish in services.list_dishes(session, include_archived=True)
            if dish.id in fixed_ids
        }
        return {"profile": profile.model_dump(mode="json") if profile else None, "dish_names": names}


@app.get("/api/memories")
def get_memories() -> list[dict]:
    with session_scope() as session:
        return [entry.model_dump(mode="json") for entry in services.list_memories(session)]


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: int) -> dict:
    with session_scope() as session:
        services.delete_memory(session, memory_id)
    return {"ok": True}
