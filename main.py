import asyncio
import os
import re
from urllib.parse import quote
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from telethon import TelegramClient, events
import uvicorn

# --- [ CONFIGURATIONS ] ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

SERVER_URL = os.environ.get("SERVER_URL", "https://your-app-name.onrender.com")

bot = TelegramClient('telethon_stream_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = FastAPI(title="Telegram Video Streamer")

# --- [ HELPER FUNCTIONS ] ---

def get_clean_filename(message) -> str:
    """
    ဖိုင်၏ မူရင်းအမည် သို့မဟုတ် Message Text/Caption မှ Name, Year, Episode စသည်တို့ကို 
    ဆွဲထုတ်ပြီး Clean URL Friendly ဖြစ်သော ဖိုင်အမည် ပြန်ပေးပါသည်။
    """
    file_name = None
    
    # 1. Telegram Document/Video Attribute ထဲမှ မူရင်းဖိုင်အမည်ကို ရှာခြင်း
    if message.document:
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                file_name = attr.file_name
                break
                
    # 2. ဖိုင်အမည် မရှိပါက Message/Caption စာသားကို ယူခြင်း
    if not file_name:
        file_name = message.text or message.caption or "video.mp4"

    # မလိုလားအပ်သော အထူးသင်္ကေတများကို ရှင်းထုတ်ပြီး Spaces များကို Hyphen/Underscore သို့ ပြောင်းခြင်း
    file_name = re.sub(r'[\\/*?:"<>|]', '', file_name)
    file_name = re.sub(r'\s+', '.', file_name.strip())
    
    # Extension မပါခဲ့ပါက .mp4 ဖြည့်ပေးခြင်း
    if not re.search(r'\.[a-zA-Z0-9]+$', file_name):
        file_name += ".mp4"
        
    return file_name


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
        
        # ဖိုင်အမည် သို့မဟုတ် Caption မှ စာသားကို ရှင်းလင်းစွာ ရယူခြင်း
        clean_name = get_clean_filename(event.message)
        
        # URL Friendly ဖြစ်အောင် Encode လုပ်ခြင်း
        encoded_name = quote(clean_name)
        
        # Stream Link မှာ မူရင်း/ရှင်းလင်းထားသော ဖိုင်အမည် ထည့်သွင်းခြင်း
        stream_link = f"{SERVER_URL}/stream/{chat_id}/{message_id}/{encoded_name}"
        
        response_text = (
            f"🎬 **ဖိုင်အမည်:** `{clean_name}`\n\n"
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

# Endpoint တွင် {filename} ပါဝင်အောင် ပြင်ဆင်ထားပါသည်
@app.get("/stream/{chat_id}/{message_id}/{filename}")
@app.get("/stream/{chat_id}/{message_id}")  # မူရင်း URL Structure အဟောင်းအတွက် Backward Compatibility
async def stream_video(chat_id: int, message_id: int, filename: str = "video.mp4", request: Request = None):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        file_size = file.size
        mime_type = file.mime_type or "video/mp4"
        range_header = request.headers.get("range") if request else None
        
        # Browser / Player များက ဖိုင်အမည်ကို အမှန်သိရှိစေရန် Content-Disposition header ထည့်ပေးခြင်း
        content_disposition = f'inline; filename="{filename}"'

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
                "Content-Disposition": content_disposition,
                "Cache-Control": "public, max-age=3600",
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
                "Content-Disposition": content_disposition,
                "Cache-Control": "public, max-age=3600",
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
