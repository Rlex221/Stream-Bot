import asyncio
import os
import re
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeFilename
import uvicorn

# --- [ CONFIGURATIONS ] ---
# Environment Variables ကနေ ယူသုံးခြင်း သို့မဟုတ် အောက်တွင် တိုက်ရိုက်ထည့်ပါ
API_ID = int(os.environ.get("API_ID", 36973326))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

# သင့် Render / Hosting ရဲ့ Domain URL ကို ဒီနေရာမှာ ထည့်ပါ (အနောက်မှာ / မပါရပါ)
SERVER_URL = os.environ.get("SERVER_URL", "https://your-app-name.onrender.com")

# Telethon Telegram Client
bot = TelegramClient('telethon_stream_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = FastAPI(title="Telegram Video Streamer")

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
        
        # Cloud Domain ဖြင့် Link ထုတ်ပေးခြင်း
        stream_link = f"{SERVER_URL}/stream/{chat_id}/{message_id}"
        
        response_text = (
            f"🔗 **သင့်ဗီဒီယိုအတွက် Stream Link ရပါပြီ:**\n\n"
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

@app.get("/stream/{chat_id}/{message_id}")
async def stream_video(chat_id: int, message_id: int, request: Request):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        
        file_size = file.size
        mime_type = file.mime_type or "video/mp4"

        original_name = "video.mp4"
        if message.document:
            for attr in message.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    original_name = attr.file_name
                    break
        ext=os.path.splitext(original_name)[1] or ".mp4"
        base=os.path.splitext(original_name)[0].replace("."," ").replace("_"," ")
        y=re.search(r"(19|20)\d{2}",base)
        year=y.group(0) if y else ""
        e=re.search(r"(?:S\d{1,2}E|EP\s*|E)(\d{1,3})",base,re.I)
        ep=e.group(1) if e else ""
        title=re.sub(r"(19|20)\d{2}","",base)
        title=re.sub(r"S\d{1,2}E\d{1,3}|EP\s*\d+|E\d+","",title,flags=re.I)
        title=re.sub(r"\b(1080p|720p|480p|2160p|WEB.?DL|WEBRip|BluRay|HDRip|x264|x265|HEVC|AAC|NF|AMZN)\b","",title,flags=re.I)
        title=re.sub(r"\s+"," ",title).strip()
        stream_filename=f"{title} {year} E{ep}{ext}" if ep and year else (f"{title} E{ep}{ext}" if ep else (f"{title} {year}{ext}" if year else f"{title}{ext}"))

        range_header = request.headers.get("range")
        
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            
            if end >= file_size:
                end = file_size - 1
                
            content_length = end - start + 1
            headers = {
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(content_length),
                "Content-Type": mime_type,
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": f'inline; filename="{stream_filename}"',
            }
            
            return StreamingResponse(
                tg_file_streamer(bot, file, start, end),
                status_code=206,
                headers=headers
            )
        else:
            headers = {
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Type": mime_type,
                "Cache-Control": "public, max-age=3600",
                "Content-Disposition": f'inline; filename="{stream_filename}"',
            }
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
