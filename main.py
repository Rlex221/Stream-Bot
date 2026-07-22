import asyncio
import os
import re
from urllib.parse import quote, unquote
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from telethon import TelegramClient, events
import uvicorn

# --- [ CONFIGURATIONS ] ---
API_ID = int(os.environ.get("API_ID", 0))  # သို့မဟုတ် int("YOUR_API_ID")
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

# သင့် Render / Railway ရဲ့ Domain URL ကို ဒီနေရာမှာ ထည့်ပါ (အနောက်မှာ / မပါရပါ)
SERVER_URL = os.environ.get("SERVER_URL", "https://your-app-name.onrender.com")

# Telethon Telegram Client
bot = TelegramClient('telethon_stream_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = FastAPI(title="Telegram Video Streamer")


# --- [ HELPER FUNCTIONS ] ---

def clean_and_format_title(name: str) -> str:
    """mmsub ဖြုတ်ခြင်း၊ စာလုံးရှေ့အကြီးပြောင်းခြင်း နှင့် EP နံပါတ်ခြားပေးသည့် Function"""
    if not name:
        return ""

    # 1. Extension ကို သီးသန့်ခွဲထုတ်ထားမည်
    ext = ""
    if "." in name:
        parts = name.rsplit(".", 1)
        name, ext = parts[0], f".{parts[1]}"

    # 2. Underscore (_), Dot (.) များကို Space သို့ ပြောင်းမည်
    name = re.sub(r'[\._]', ' ', name)

    # 3. mmsub, mmsubtitle, sub, myanmar sub စသည်တို့ကို ဖြုတ်ပါ (Case-insensitive)
    unwanted_patterns = [
        r'\bmmsubtitles?\b', r'\bmmsubs?\b', r'\bmyanmar\s*sub\b', 
        r'\bsubtitles?\b', r'\[mmsub\]', r'\(mmsub\)', r'\bsub\b'
    ]
    for pattern in unwanted_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # 4. Episode နံပါတ်များ မကပ်စေရန် Space ခြားပေးခြင်း
    # ဥပမာ - MovieEp01 -> Movie Ep 01, SeriesE05 -> Series E 05, Ep-01 -> Ep 01
    name = re.sub(r'([a-zA-Z]+)(ep|e|sp)(\d+)', r'\1 \2 \3', name, flags=re.IGNORECASE)
    name = re.sub(r'\b(ep|e|sp)(\d+)\b', r'\1 \2', name, flags=re.IGNORECASE)
    name = re.sub(r'(\d+)(ep|e|sp)', r'\1 \2', name, flags=re.IGNORECASE)
    name = re.sub(r'[-_]+', ' ', name)

    # 5. မလိုလားအပ်သော Special Characters များကို ရှင်းထုတ်ခြင်း
    name = re.sub(r'[\\/*?:"<>|\[\]()]', ' ', name)

    # 6. Space မျိုးစုံကို Single Space ပြောင်းခြင်း
    name = re.sub(r'\s+', ' ', name).strip()

    # 7. စာလုံးတိုင်း၏ ရှေ့စာလုံးကို အကြီးပြောင်းခြင်း (Title Case)
    name = name.title()

    # EP / E / SP စာလုံးများကို ပုံစံမှန် အက္ခရာကြီး ပြောင်းပေးခြင်း
    name = re.sub(r'\bEp\b', 'Ep', name, flags=re.IGNORECASE)
    name = re.sub(r'\bE\b', 'E', name, flags=re.IGNORECASE)

    return f"{name}{ext if ext else '.mp4'}"


def extract_file_name(message) -> str:
    """Telegram Message မှ Video/Document ရဲ့ File Name ကို ရှာဖွေထုတ်ယူပေးသည့် Function"""
    file_name = None
    
    # Document ဖြစ်ပါက attributes ထဲမှ file_name ကို ရှာမည်
    if message.document and message.document.attributes:
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                file_name = attr.file_name
                break
                
    # Video file ဖြစ်ပြီး file_name မရှိသေးပါက
    if not file_name and message.video:
        if hasattr(message.video, 'attributes'):
            for attr in message.video.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    file_name = attr.file_name
                    break

    # Caption သို့မဟုတ် Text မှ ရှာခြင်း
    if not file_name and message.text:
        first_line = message.text.split('\n')[0].strip()
        if first_line and len(first_line) < 100:
            file_name = first_line

    # ဖိုင်နာမည် လုံးဝ မရှိပါက Default ပေးခြင်း
    if not file_name:
        file_name = "video.mp4"

    # Clean & Format ပြုလုပ်ခြင်း
    return clean_and_format_title(file_name)


# --- [ TELEGRAM BOT SECTION ] ---

@bot.on(events.NewMessage(pattern='/start', incoming=True))
async def start_handler(event):
    await event.reply("👋 မင်္ဂလာပါ! ကျွန်တော့်ဆီကို ဘယ်ဗီဒီယိုဖိုင်မဆို ပို့ပေးပါ။ တိုက်ရိုက်ကြည့်ရှုနိုင်မယ့် Stream Link ထုတ်ပေးပါမယ်။")

@bot.on(events.NewMessage(incoming=True))
async def video_handler(event):
    if event.message.text and event.message.text.startswith('/start'):
        return

    if event.message.video or (event.message.document and event.message.document.mime_type and event.message.document.mime_type.startswith('video/')):
        chat_id = event.chat_id
        message_id = event.message.id
        
        # ဖိုင်နာမည် ရယူခြင်း
        raw_file_name = extract_file_name(event.message)
        # URL Safe ဖြစ်စေရန် Quote ပြုလုပ်ခြင်း
        safe_file_name = quote(raw_file_name)
        
        # Cloud Domain ဖြင့် Link ထုတ်ပေးခြင်း (ဖိုင်နာမည် ပါဝင်သည်)
        stream_link = f"{SERVER_URL}/stream/{chat_id}/{message_id}/{safe_file_name}"
        
        response_text = (
            f"🔗 **သင့်ဗီဒီယိုအတွက် Stream Link ရပါပြီ:**\n\n"
            f"📁 **File Name:** `{raw_file_name}`\n\n"
            f"`{stream_link}`\n\n"
            f"💡 ဒီ link ကို VLC, MX Player သို့မဟုတ် Browser ထဲမှာ ထည့်သွင်းကြည့်ရှုနိုင်ပါတယ်။"
        )
        await event.reply(response_text)


# --- [ STREAM SERVER SECTION ] ---

async def tg_file_streamer(client, file, offset, limit):
    chunk_size = 1024 * 1024  # 1MB Chunk
    bytes_to_send = limit - offset + 1
    
    start_chunk_offset = (offset // chunk_size) * chunk_size
    skip_bytes = offset - start_chunk_offset

    try:
        async for chunk in client.iter_download(
            file,
            offset=start_chunk_offset,
            request_size=chunk_size
        ):
            if not chunk:
                break
            
            if skip_bytes > 0:
                if skip_bytes >= len(chunk):
                    skip_bytes -= len(chunk)
                    continue
                else:
                    chunk = chunk[skip_bytes:]
                    skip_bytes = 0
            
            if len(chunk) > bytes_to_send:
                yield chunk[:bytes_to_send]
                break
            else:
                yield chunk
                bytes_to_send -= len(chunk)
                
            if bytes_to_send <= 0:
                break
                
            await asyncio.sleep(0.0001)
            
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

@app.get("/")
async def root():
    return {"status": "ok", "message": "Telegram Streaming Server is running!"}

@app.get("/stream/{chat_id}/{message_id}/{file_name}")
async def stream_video(chat_id: int, message_id: int, file_name: str, request: Request):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        file_size = file.size
        mime_type = file.mime_type or "video/mp4"
        range_header = request.headers.get("range")
        
        # Display name အတွက် Decode လုပ်ခြင်း
        display_name = unquote(file_name)
        
        headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
            # Player/Browser တွင် Video နာမည် မှန်မှန်ပေါ်စေရန် Content-Disposition ထည့်သွင်းခြင်း
            "Content-Disposition": f'inline; filename="{display_name}"'
        }
        
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            
            if end >= file_size:
                end = file_size - 1
                
            content_length = end - start + 1
            headers.update({
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length),
            })
            
            return StreamingResponse(
                tg_file_streamer(bot, file, start, end),
                status_code=206,
                headers=headers
            )
        else:
            headers["Content-Length"] = str(file_size)
            return StreamingResponse(
                tg_file_streamer(bot, file, 0, file_size - 1),
                status_code=200,
                headers=headers
            )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- [ MAIN RUNNER SECTION ] ---

async def main():
    # Cloud Platform ကပေးတဲ့ PORT ကို ယူသုံးခြင်း (မရှိရင် 8080 ကိုသုံးမည်)
    port = int(os.environ.get("PORT", 8080))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    
    server_task = asyncio.create_task(server.serve())
    
    try:
        await bot.run_until_disconnected()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        server.should_exit = True
        await server_task

if __name__ == "__main__":
    try:
        bot.loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n👋 Bot ရပ်နားလိုက်ပါပြီ။")
