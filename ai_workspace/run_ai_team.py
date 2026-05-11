import argparse
import asyncio
import logging
import sys

from notebooklm import NotebookLMClient
from notebooklm.exceptions import AuthError, NotebookLMError, RPCError

# Configure logging to be clean but informative
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("AI-Team")


class AITeamWorkflow:
    """
    NotebookLM AI Team Framework Implementation:
    Minnie (Memory) -> Indy (Integrations) -> Vera (Verification) -> Reas (Reasoning) -> Day (Delivery)
    """

    def __init__(self, client: NotebookLMClient):
        self.client = client

    async def execute(self, url: str, title: str):
        print(f"\n🚀 สตาร์ทระบบ NotebookLM AI Team: หัวข้อ '{title}'")
        print("=" * 60)

        try:
            # 1. Minnie (Memory): จัดการโครงสร้างความจำ
            print(f"📁 [Minnie] กำลังสร้าง Notebook ใหม่: {title}...")
            nb = await self.client.notebooks.create(title)
            print(f"✅ [Minnie] สร้างสำเร็จ ID: {nb.id}")

            # 2. Indy (Integrations): นำเข้าข้อมูลจากโลกภายนอก
            print(f"🔗 [Indy] กำลังนำเข้าข้อมูลจาก URL: {url}...")
            source = await self.client.sources.add_url(nb.id, url)
            print(f"✅ [Indy] ส่งคำขอนำเข้าสำเร็จ ID: {source.id}")

            # 3. Vera (Verification): ตรวจสอบความถูกต้องและเฝ้าดูสถานะ
            print("⏳ [Vera] กำลังตรวจสอบและรอให้ข้อมูลพร้อมใช้งาน (Timeout: 120s)...")
            await self.client.sources.wait_until_ready(nb.id, source.id, timeout=120)
            print("✅ [Vera] ข้อมูลพร้อมใช้งานแล้ว 100%")

            # 4. Reas (Reasoning): วิเคราะห์และประมวลผลข้อมูล
            print("🧠 [Reas] กำลังวิเคราะห์เนื้อหาและสรุปประเด็นสำคัญ...")
            # We use chat.ask for grounded reasoning
            prompt = "วิเคราะห์เนื้อหาจากแหล่งข้อมูลนี้ และสรุปประเด็นที่น่าสนใจที่สุด 5 ข้อ พร้อมระบุความสำคัญ"
            result = await self.client.chat.ask(nb.id, prompt)

            # Extract answer text
            analysis = getattr(result, "answer", str(result))
            print("✅ [Reas] วิเคราะห์เสร็จสิ้น")

            # 5. Day (Delivery): ส่งมอบงานในรูปแบบที่สวยงาม
            print("\n✨ [Day] รายงานสรุปผลจาก AI Team:")
            print("-" * 40)
            print(analysis)
            print("-" * 40)

            # Additional Delivery: Generate a Study Guide artifact
            print("📚 [Day] กำลังสร้าง Study Guide เพื่อเก็บไว้ใน Notebook...")
            artifact_task = await self.client.artifacts.generate_study_guide(nb.id)
            print(f"✅ [Day] สั่งสร้างรายงานสำเร็จ (Task: {artifact_task.task_id})")
            print(
                f"\n🎉 ภารกิจเสร็จสิ้น! คุณสามารถดูผลงานเต็มๆ ได้ที่: https://notebooklm.google.com/notebook/{nb.id}"
            )

        except AuthError as e:
            print(f"\n❌ [Error] ปัญหาการยืนยันตัวตน: {e}")
            print("💡 กรุณารัน 'notebooklm login' ที่เครื่อง Host ก่อน")
        except RPCError as e:
            print(f"\n❌ [Error] API ของ Google ตอบกลับผิดพลาด: {e}")
        except NotebookLMError as e:
            print(f"\n❌ [Error] เกิดข้อผิดพลาดในระบบ NotebookLM: {e}")
        except Exception as e:
            print(f"\n❌ [Critical] เกิดข้อผิดพลาดที่ไม่คาดคิด: {e}")


async def main():
    parser = argparse.ArgumentParser(description="NotebookLM AI Team Workflow Automation")
    parser.add_argument("--url", required=True, help="URL ของแหล่งข้อมูลที่ต้องการทำวิจัย")
    parser.add_argument("--title", required=True, help="ชื่อโปรเจค/Notebook")

    # Check for help or missing args
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    try:
        # Correctly await the async factory method, then use the client
        client = await NotebookLMClient.from_storage()
        async with client:
            team = AITeamWorkflow(client)
            await team.execute(args.url, args.title)
    except KeyboardInterrupt:
        print("\n\n⚠️ ยกเลิกการทำงานโดยผู้ใช้")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ ไม่สามารถเริ่มระบบได้: {e}")


if __name__ == "__main__":
    asyncio.run(main())
