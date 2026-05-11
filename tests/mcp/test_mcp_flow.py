import asyncio

from notebooklm import NotebookLMClient


async def main():
    print("🚀 1. Initializing client...")
    client = await NotebookLMClient.from_storage(timeout=120.0)
    await client.__aenter__()

    url = "https://youtu.be/j8yO0LkqA7s?si=0lEO1EvRNkdUwYrT"

    try:
        print("\n📓 2. Creating Notebook...")
        nb = await client.notebooks.create("Test Translation JJ Lin")
        print(f"Created: {nb.id} - {nb.title}")

        print("\n🔗 3. Adding URL Source (wait=False)...")
        source = await client.sources.add_url(nb.id, url, wait=False)
        print(f"Source ID: {source.id}")
        print(f"Initial Status: {source.status}")

        print("\n⏳ 4. Polling for Readiness (Simulating wait_source_ready)...")
        ready = False
        for i in range(20):  # Max 1 minute polling
            s = await client.sources.get(nb.id, source.id)
            print(f"Poll {i+1}: {s.status}")
            if s.is_ready:
                ready = True
                print("✅ Source is ready!")
                break
            if s.is_error:
                print("❌ Source error!")
                break
            await asyncio.sleep(5)

        if ready:
            print("\n💬 5. Asking Question (Verbatim proxy translation)...")
            prompt = "สรุปเนื้อหาและแปลเนื้อเพลง/วิดีโอนี้เป็นภาษาไทยทั้งหมดเท่านั้น ห้ามตอบเป็นภาษาอื่น"
            print(f"Prompt: {prompt}")
            result = await client.chat.ask(nb.id, prompt)
            print("\n📝 === FINAL RESULT ===")
            print(getattr(result, "answer", str(result)))
            print("======================\n")

    finally:
        await client.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())
