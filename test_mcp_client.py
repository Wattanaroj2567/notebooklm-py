import asyncio
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

async def main():
    url = "http://localhost:8000/sse"
    print(f"Connecting to MCP Server at {url}...\n")
    
    # Establish SSE connection
    async with sse_client(url) as streams:
        # Create an MCP session over the streams
        async with ClientSession(streams[0], streams[1]) as session:
            # Send initialization handshake
            await session.initialize()
            print("✅ Connected and initialized MCP Session!\n")
            
            # Step 1: Request list of available tools
            tools = await session.list_tools()
            print(f"✅ Discovered {len(tools.tools)} tools from the server.")
            
            # Step 2: Call the 'list_notebooks' tool via MCP JSON-RPC
            print("🚀 Calling tool: 'list_notebooks'...")
            result = await session.call_tool("list_notebooks", {})
            
            # Display results and get the first notebook ID
            print("\n📥 Result from 'list_notebooks':")
            first_notebook_id = None
            if result.isError:
                print("Error:", result.content)
            else:
                for item in result.content:
                    if getattr(item, "type", None) == "text":
                        print(item.text)
                        # Extract the first notebook ID manually for the next test
                        import json
                        try:
                            data = json.loads(item.text)
                            if not first_notebook_id:
                                first_notebook_id = data.get("id")
                        except json.JSONDecodeError:
                            pass
                    else:
                        print(item)
            
            # Step 3: Test ask_question
            if first_notebook_id:
                print(f"\n🚀 Calling tool: 'ask_question' on notebook: {first_notebook_id}...")
                question = "Can you give a 1 sentence summary of what this notebook is about?"
                answer_result = await session.call_tool("ask_question", {
                    "notebook_id": first_notebook_id,
                    "question": question
                })
                
                print("\n📥 Answer from NotebookLM:")
                if answer_result.isError:
                    print("Error:", answer_result.content)
                else:
                    for item in answer_result.content:
                        if getattr(item, "type", None) == "text":
                            print(item.text)
                        else:
                            print(item)

if __name__ == "__main__":
    asyncio.run(main())
