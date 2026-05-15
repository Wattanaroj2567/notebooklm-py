import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("test")
_sse_app = mcp.sse_app()


class SSEProxy:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope["root_path"] = ""
        scope["path"] = "/sse"
        await self.app(scope, receive, send)


app = FastAPI()
app.mount("/sse", SSEProxy(_sse_app))  # type: ignore

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8005)
