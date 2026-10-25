"""Serve only the isolated real public cases; never open the user's workbench DB."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/public-cases/20261023"
os.environ["YANZHENG_DB_PATH"] = str(OUT / "cases.db")
sys.path.insert(0, str(ROOT / "webapp"))
from app import create_app
from fastapi.responses import FileResponse, HTMLResponse
from fastapi import HTTPException
import uvicorn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    application = create_app(OUT / "cases.db")

    @application.get("/showcase")
    def showcase():
        source = (OUT / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(source.replace('<meta charset="utf-8">', '<meta charset="utf-8"><base href="/case-artifact/">').replace('href="#', 'href="/showcase#'))

    @application.get("/case-artifact/{name:path}")
    def artifact(name: str):
        target = (OUT / name).resolve()
        if not target.is_relative_to(OUT) or not target.is_file() or target.suffix not in (".pdf", ".json", ".md", ".csv", ".png", ".webm", ".mp4", ".srt"):
            raise HTTPException(404)
        return FileResponse(target)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    (OUT / "SERVER.json").write_text(json.dumps({"base": f"http://127.0.0.1:{listener.getsockname()[1]}",
        "database": str(OUT / "cases.db"), "pid": os.getpid(), "fixture": False,
        "providers": "real official metadata and configured Qwen; no fake transport"}), encoding="utf-8")
    try:
        uvicorn.Server(uvicorn.Config(application, log_level="warning")).run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
