import asyncio
import argparse
import sys
import os
from notebooklm import NotebookLMClient
from notebooklm.exceptions import NotebookLMError, AuthenticationError, ApiError

async def run_ai_automation(url, title):
    """
    AI Team Automation Workflow (Standardized Version)
    """
    print(f"\n🚀 เริ่มงานทีม AI สำหรับ: {title}")
    print("="*50)
    
    try:
        async with await NotebookLMClient.from_storage() as client:
            # 1. Minnie: สร้าง Notebook
            print(f"📁 [Minnie] สร้าง Notebook: {title}...")
            nb = await client.notebooks.create(title)
            
            # 2. Indy: เพิ่มแหล่งข้อมูล
            print(f"🔗 [Indy] เพิ่มแหล่งข้อมูล: {url}...")
            source = await client.sources.add_url(nb.id, url)
            
            # 3. Vera: รอจนกว่าจะพร้อม
            print(f"⏳ [Vera] รอการประมวลผล (Grounded Verification)...")
            await client.sources.wait_for_ready(nb.id, source.id, timeout=600)
            
            # 4. Reas: วิเคราะห์
            print(f"🧠 [Reas] วิเคราะห์เนื้อหาเชิงลึก...")
            summary_result = await client.chat.ask(nb.id, "สรุปประเด็นสำคัญเป็นภาษาไทย")
            
            # 5. Day: ส่งมอบงาน
            filename = f"summary_{nb.id[:8]}.md"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# {title}\n\n{summary_result.answer}")
            
            print(f"🎉 [Day] สำเร็จ! ไฟล์: {filename}")

    except AuthenticationError:
        print("\n❌ [Vera] เกิดข้อผิดพลาดด้านสิทธิ์การเข้าถึง: กรุณารัน 'notebooklm login' อีกครั้ง")
    except ApiError as e:
        print(f"\n❌ [Vera] เกิดข้อผิดพลาดจาก API: {str(e)}")
    except NotebookLMError as e:
        print(f"\n❌ [Vera] เกิดข้อผิดพลาดในระบบ: {str(e)}")
    except Exception as e:
        print(f"\n❌ [Vera] เกิดข้อผิดพลาดไม่คาดคิด: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NotebookLM AI Team Automation Script")
    parser.add_argument("url", help="URL ของวิดีโอ YouTube หรือเว็บไซต์ที่ต้องการสรุป")
    parser.add_argument("-t", "--title", default="AI Automation Research", help="ชื่อหัวข้อของ Notebook (Default: AI Automation Research)")
    
    # หากไม่มี argument ให้แสดง help
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)
        
    args = parser.parse_args()
    
    try:
        asyncio.run(run_ai_automation(args.url, args.title))
    except KeyboardInterrupt:
        print("\n\n⚠️ ยกเลิกการทำงานโดยผู้ใช้")
        sys.exit(0)
