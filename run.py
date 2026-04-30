import os
from wsgiref.simple_server import make_server

from app.main import app


def main():
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    with make_server(host, port, app) as server:
        print(f"Serving Capitol Trades API on http://{host}:{port}")
        server.serve_forever()


if __name__ == "__main__":
    main()
