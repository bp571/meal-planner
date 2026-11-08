"""Start the local web UI: python -m meal_planner"""

import os
import threading
import time
import webbrowser

import uvicorn
from dotenv import find_dotenv, load_dotenv

from meal_planner import db


def _open_browser_when_up(server: uvicorn.Server, url: str) -> None:
    while not server.started:
        time.sleep(0.1)
    webbrowser.open(url)


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    db.init_db()
    from meal_planner.app import app  # after load_dotenv, so the app sees the environment

    port = int(os.getenv("MEAL_PLANNER_PORT", "8000"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port))
    threading.Thread(
        target=_open_browser_when_up, args=(server, f"http://127.0.0.1:{port}"), daemon=True
    ).start()
    server.run()


if __name__ == "__main__":
    main()
