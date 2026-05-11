import asyncio
import json
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


def _extract_text(content_item: Any) -> str:
    """Safely extract text from an MCP content item."""
    if hasattr(content_item, "text"):
        return content_item.text  # type: ignore
    return str(content_item)


async def main():
    sse_url = "http://127.0.0.1:8000/sse"
    print(f"🔌 Connecting to MCP Server: {sse_url}")

    async with sse_client(sse_url) as streams, ClientSession(streams[0], streams[1]) as session:
        await session.initialize()
        print("✅ MCP Session Initialized")

        # 1. Create Notebook
        print("\n📓 Calling tool: create_notebook")
        nb_res = await session.call_tool("create_notebook", {"title": "Test MCP End-to-End"})
        nb_data = json.loads(_extract_text(nb_res.content[0]))
        nb_id = nb_data["id"]
        print(f"Created Notebook ID: {nb_id}")

        # 2. Add URL Source
        print("\n🔗 Calling tool: add_url_source (wait=False)")
        url = "https://youtu.be/j8yO0LkqA7s?si=0lEO1EvRNkdUwYrT"
        src_res = await session.call_tool(
            "add_url_source", {"notebook_id": nb_id, "url": url, "wait": False}
        )
        src_data = json.loads(_extract_text(src_res.content[0]))
        src_id = src_data["id"]
        print(f"Source ID: {src_id} | Status: {src_data['status']}")

        # 3. Poll with wait_source_ready
        print("\n⏳ Polling with wait_source_ready...")
        ready = False
        for i in range(15):
            wait_res = await session.call_tool(
                "wait_source_ready",
                {"notebook_id": nb_id, "source_id": src_id, "timeout_seconds": 10},
            )
            wait_data = json.loads(_extract_text(wait_res.content[0]))
            print(
                f"Poll {i+1}: status={wait_data.get('status')} is_ready={wait_data.get('is_ready')}"
            )

            if wait_data.get("is_ready"):
                ready = True
                break
            if wait_data.get("status") == "error":
                break

        if ready:
            # 4. Ask Question
            print("\n💬 Calling tool: ask_question (Translating to Thai...)")
            prompt = "สรุปเนื้อหาและแปลเนื้อเพลง/วิดีโอนี้เป็นภาษาไทยทั้งหมดเท่านั้น ห้ามตอบเป็นภาษาอื่น"
            ask_res = await session.call_tool(
                "ask_question", {"notebook_id": nb_id, "question": prompt}
            )
            print("\n📝 === FINAL RESULT FROM MCP ===")
            print(_extract_text(ask_res.content[0]))
            print("=================================\n")
        else:
            print("❌ Source failed to become ready.")


if __name__ == "__main__":
    asyncio.run(main())
