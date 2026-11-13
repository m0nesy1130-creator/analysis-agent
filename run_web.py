"""从 PyCharm 启动 analysis-agent 的本地网页。"""

from http.client import HTTPConnection, HTTPException
from threading import Thread
from time import monotonic, sleep
import webbrowser

import uvicorn


HOST = "127.0.0.1"
PORT = 8000


def console_is_running() -> bool:
    connection = HTTPConnection(HOST, PORT, timeout=1)
    try:
        connection.request("GET", "/")
        response = connection.getresponse()
        return response.status == 200 and b"analysis-agent</title>" in response.read(4096)
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def open_console() -> None:
    url = f"http://{HOST}:{PORT}/"
    print(f"Agent 网页：{url}", flush=True)
    webbrowser.open_new_tab(url)


def open_after_startup(server: uvicorn.Server) -> None:
    deadline = monotonic() + 10
    while not server.started and monotonic() < deadline:
        sleep(0.1)
    if server.started:
        open_console()


def main() -> None:
    if console_is_running():
        open_console()
        return

    server = uvicorn.Server(uvicorn.Config("app.api:app", host=HOST, port=PORT))
    Thread(target=open_after_startup, args=(server,), daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
