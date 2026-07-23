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

# --- [ FILENAME CLEANER FUNCTION ] ---
def clean_filename(filename: str) -> str:
    """
    ဖိုင်အမည်ထဲမှ Name, Year, Episode များကို သီးသန့်ထုတ်ယူပြီး Stream Link အတွက် သန့်စင်ပေးသော Function
    - Movie: Name Year.ext (e.g. Inception 2010.mp4)
    - Series: Name Year Ep.ext (e.g. Loki 2021 S01E05.mp4)
    """
    # Extension ခွဲထုတ်ခြင်း (.mp4 သို့မဟုတ် .mkv)
    name_part, ext = os.path.splitext(filename)
    if not ext:
        ext = ".mp4"

    # Dot (.) နှင့် Underscore (_) များကို Space ဖြင့် အစားထိုးခြင်း
    clean_name = re.sub(r'[\._]', ' ', name_part)

    # 1. Series Pattern ရှာဖွေခြင်း (e.g., S01E05, S1E5, EP01, Ep 05, E05)
    ep_match = re.search(r'(?i)\b(s\d{1,2}\s*e\d{1,2}|ep?\s*\d{1,3})\b', clean_name)
    
    # 2. Year Pattern ရှာဖွေခြင်း (e.g., 19xx သို့မဟုတ် 20xx)
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', clean_name)

    extracted_title = ""
    year_str = year_match.group(1) if year_match else ""
    ep_str = ep_match.group(1) if ep_match else ""

    # Title အပိုင်းကို သီးသန့်ဖြတ်ယူခြင်း (Year သို့မဟုတ် Episode မတိုင်မီအပိုင်း)
    cutoff_index = len(clean_name)
    if year_match:
        cutoff_index = min(cutoff_index, year_match.start())
    if ep_match:
        cutoff_index = min(cutoff_index, ep_match.start())

    extracted_title = clean_name[:cutoff_index].strip()

    # Title မရှိပါက မူရင်းနာမည်အတိုင်း ပြန်သုံးခြင်း
    if not extracted_title:
        extracted_title = clean_name

    # တပ်ဆင်ပေါင်းစပ်ခြင်း (Name + Year + EP + Ext)
    result_components = [extracted_title]
    if year_str:
        result_components.append(year_str)
    if ep_str:
        # EP စာသားကို Standard Format (e.g., S01E05 သို့မဟုတ် E05) သို့ ပြောင်းခြင်း
        ep_formatted = re.sub(r'\s+', '', ep_str).upper()
        result_components.append(ep_formatted)

    formatted_name = " ".join(result_components)
    return f"{formatted_name}{ext}"


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
        
        raw_filename = "video.mp4"
        
        # Telethon မှ မူရင်း ဖိုင်အမည် ရယူခြင်း
        if event.message.document:
            from telethon.tl.types import DocumentAttributeFilename
            for a in event.message.document.attributes:
                if isinstance(a, DocumentAttributeFilename):
                    raw_filename = a.file_name
                    break
        elif event.message.video:
            # Video attribute များထဲမှ မူရင်း အမည်ရှိမရှိ စစ်ဆေးခြင်း
            from telethon.tl.types import DocumentAttributeFilename
            for a in event.message.video.attributes:
                if isinstance(a, DocumentAttributeFilename):
                    raw_filename = a.file_name
                    break

        # ဖိုင်အမည်ကို Name Year EP.mp4 ဖြစ်အောင် သန့်စင်ပေးခြင်း
        cleaned_filename = clean_filename(raw_filename)

        # Stream Link ထုတ်ပေးခြင်း
        stream_link = f"{SERVER_URL}/stream/{chat_id}/{message_id}/{quote(cleaned_filename)}"
        
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

@app.get("/stream/{chat_id}/{message_id}/{filename:path}")
async def stream_video(chat_id: int, message_id: int, filename: str, request: Request):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        file_size = file.size
        mime_type = file.mime_type or "video/mp4"
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
                "Content-Disposition": f'inline; filename="{filename}"',
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
                "Content-Disposition": f'inline; filename="{filename}"',
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
