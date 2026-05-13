import uvicorn
from fastapi import FastAPI

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test")
_sse_app = mcp.sse_app()

app = FastAPI()


class Proxy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope["root_path"] = ""
        scope["path"] = "/sse"
        await self.app(scope, receive, send)


app.add_route("/sse", Proxy(_sse_app), methods=["GET", "OPTIONS"])  # type: ignore
app.add_route("/sse/", Proxy(_sse_app), methods=["GET", "OPTIONS"])  # type: ignore

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
