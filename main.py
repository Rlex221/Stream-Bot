import asyncio
import os
import re
from urllib.parse import quote
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from telethon import TelegramClient, events
import uvicorn

# --- [ CONFIGURATIONS ] ---
API_ID = int(os.environ.get("API_ID", 36973326))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

# သင့် Domain URL (အနောက်မှာ / မပါရပါ)
SERVER_URL = os.environ.get("SERVER_URL", "https://streamtg21.v6.navy")

bot = TelegramClient('telethon_stream_bot', API_ID, API_HASH).start(bot_token=BOT_TOKEN)
app = FastAPI(title="Telegram Video Streamer")


# --- [ HELPER: CLEAN FILENAME ] ---
def extract_clean_filename(file) -> str:
    """Telegram file မှ မူရင်းအမည်ကိုယူ၍ Movie / Series Name CleanUp လုပ်ပေးသည့် function"""
    original_name = ""
    
    # ၁။ Telegram Attributes ထဲမှ မူရင်း File Name ကို ရှာခြင်း
    if hasattr(file, 'attributes') and file.attributes:
        for attr in file.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                original_name = attr.file_name
                break

    if not original_name:
        original_name = "video.mp4"

    # Extension (.mp4 / .mkv) ခွဲထုတ်ခြင်း
    name, ext = os.path.splitext(original_name)
    if not ext:
        ext = ".mp4" if getattr(file, "mime_type", "") == "video/mp4" else ".mkv"

    # Dot, Underscore များကို Space ဖြင့် လဲလှယ်ခြင်း
    clean_name = re.sub(r'[\._]', ' ', name)

    # Patterns များ ရှာဖွေခြင်း
    # ၁။ Series Pattern: Name + Year (optional) + Episode (Ep 01, S01E01, etc.)
    series_match = re.search(r'(.*?)(?:\b(19\d{2}|20\d{2})\b)?.*?\b(S\d+E\d+|E\d+|\d+x\d+|Ep\s*\d+|EP\d+)\b', clean_name, re.IGNORECASE)
    
    # ၂။ Movie Pattern: Name + Year (2024, 2023)
    movie_match = re.search(r'(.*?)\b(19\d{2}|20\d{2})\b', clean_name)

    if series_match:
        title = series_match.group(1).strip()
        year = f" {series_match.group(2).strip()}" if series_match.group(2) else ""
        ep = series_match.group(3).strip()
        # Ep 01 ပုံစံမျိုး လှပအောင် Format လုပ်ခြင်း
        ep = re.sub(r'(?i)ep\s*', 'Ep ', ep)
        formatted = f"{title}{year} {ep}{ext}"
    elif movie_match:
        title = movie_match.group(1).strip()
        year = movie_match.group(2).strip()
        formatted = f"{title} {year}{ext}"
    else:
        # Pattern မမိပါက Space သန့်ရှင်းထားသည့် မူရင်း နာမည်အတိုင်း သုံးမည်
        formatted = f"{clean_name.strip()}{ext}"

    # Extra Space များကို ရှင်းထုတ်ခြင်း
    formatted = re.sub(r'\s+', ' ', formatted)
    return formatted


# --- [ TELEGRAM BOT SECTION ] ---

@bot.on(events.NewMessage(pattern='/start', incoming=True))
async def start_handler(event):
    await event.reply("👋 မင်္ဂလာပါ! ကျွန်တော့်ဆီကို ဘယ်ဗီဒီယိုဖိုင်မဆို ပို့ပေးပါ။ တိုက်ရိုက်ကြည့်ရှုနိုင်မယ့် Stream Link ထုတ်ပေးပါမယ်။")

@bot.on(events.NewMessage(incoming=True))
async def video_handler(event):
    if event.message.text and event.message.text.startswith('/start'):
        return

    file = event.message.video or (event.message.document if event.message.document and event.message.document.mime_type and event.message.document.mime_type.startswith('video/') else None)

    if file:
        chat_id = event.chat_id
        message_id = event.message.id
        
        # Clean Filename ထုတ်ယူခြင်း (e.g. King Avatar Ep 01.mp4)
        clean_filename = extract_clean_filename(file)
        
        # URL ထဲတွင် Space တိုင်းကို %20 ဖြစ်အောင် Encode ပြုလုပ်ခြင်း
        encoded_filename = quote(clean_filename, safe='')
        
        # သင့်ဥပမာအတိုင်း link ထွက်ပေါ်လာမည်: .../stream/5461422048/416/King%20Avatar%20Ep%2001.mp4
        stream_link = f"{SERVER_URL}/stream/{chat_id}/{message_id}/{encoded_filename}"
        
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
            
    except (asyncio.CancelledError, Exception):
        pass

@app.get("/")
async def root():
    return {"status": "ok", "message": "Telegram Streaming Server is running!"}

# FastAPI Route: URL Path မှ filename ကို တိုက်ရိုက်ဖတ်ယူခြင်း
@app.get("/stream/{chat_id}/{message_id}/{filename}")
@app.get("/stream/{chat_id}/{message_id}")
async def stream_video(chat_id: int, message_id: int, filename: str = None, request: Request = None):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        clean_filename = filename or extract_clean_filename(file)
        encoded_filename = quote(clean_filename, safe='')

        file_size = file.size
        mime_type = file.mime_type or "video/mp4"
        range_header = request.headers.get("range")
        
        # Player များအတွက် Content-Disposition Header သတ်မှတ်ခြင်း
        content_disposition = f"inline; filename*=UTF-8''{encoded_filename}"

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
