import asyncio
import os
import re
from urllib.parse import quote, unquote
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
import uvicorn

# --- [ CONFIGURATIONS ] ---
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

SERVER_URL = os.environ.get("SERVER_URL", "https://your-app-name.onrender.com")

bot = TelegramClient('telethon_stream_bot', API_ID, API_HASH)
app = FastAPI(title="Telegram Video Streamer")


# --- [ HELPER FUNCTIONS ] ---

def myanmar_to_english_digits(text: str) -> str:
    """မြန်မာဂဏန်းများကို အင်္ဂလိပ်ဂဏန်းသို့ ပြောင်းပေးသည့် Function"""
    mm_digits = '၀၁၂၃၄၅၆၇၈၉'
    en_digits = '0123456789'
    trans_table = str.maketrans(mm_digits, en_digits)
    return text.translate(trans_table)

def clean_and_format_title(raw_name: str, caption_text: str = "", fwd_title: str = "") -> str:
    """ရှုပ်ထွေးနေသော စာသားများနှင့် ကြော်ငြာများကို ဖယ်ထုတ်ပြီး Clean Title ထုတ်ပေးသည့် Function"""
    if not raw_name:
        raw_name = ""

    raw_name = myanmar_to_english_digits(raw_name)
    caption_text = myanmar_to_english_digits(caption_text)
    fwd_title = myanmar_to_english_digits(fwd_title)

    # Extension ခွဲထုတ်ခြင်း (.mp4, .mkv, etc.)
    ext = ".mp4"
    if "." in raw_name:
        parts = raw_name.rsplit(".", 1)
        if len(parts[1]) <= 4 and re.match(r'^[a-zA-Z0-9]+$', parts[1]):
            raw_name, ext = parts[0], f".{parts[1]}"

    # Forward Message ခင်းကျင်းထားပါက ထို Name ကို ပိုမို ဦးစားပေး စစ်ဆေးမည်
    full_search_text = f"{fwd_title} {raw_name} {caption_text}"

    # Season နှင့် Episode ဂဏန်းများ ရှာဖွေခြင်း
    ep_number = ""
    season_number = ""

    # Season (S01, Season 1)
    season_match = re.search(r'\b(?:s|season)\s*[\.\_\-]?\s*(\d{1,2})\b', full_search_text, re.IGNORECASE)
    if season_match:
        season_number = str(int(season_match.group(1))).zfill(2)

    # Episode (Ep 01, E01, Episode 1, အပိုင်း ၁)
    ep_match = re.search(r'\b(?:ep|episode|e|အပိုင်း)\s*[\(\[\{:._-]?\s*(\d{1,3})\b', full_search_text, re.IGNORECASE)
    if ep_match:
        ep_number = str(int(ep_match.group(1))).zfill(2)

    # Year (1990 - 2029)
    year_match = re.search(r'\b(19\d{2}|20[0-2]\d)\b', full_search_text)
    year_str = f"({year_match.group(1)})" if year_match else ""

    # TITLE ဦးစားပေး သတ်မှတ်ခြင်း
    clean_title = ""

    # 1. Forward လုပ်ထားသော Channel Name ရှိပါက (ဥပမာ Dr. Romantic (2020) - Season (1))
    if fwd_title:
        # Season / Episode ပါရင် စာသားရှင်းပေးမည်
        temp_fwd = re.sub(r'\(?\bSeason\s*\d+\)?', '', fwd_title, flags=re.IGNORECASE)
        temp_fwd = re.sub(r'\b(19\d{2}|20[0-2]\d)\b', '', temp_fwd)
        temp_fwd = re.sub(r'[^a-zA-Z0-9\s]', ' ', temp_fwd)
        clean_title = " ".join(temp_fwd.split()).strip().title()

    # 2. Forward Name မရှိပါက Telegram File Name မူရင်းကို အသုံးချမည်
    if not clean_title and raw_name and not raw_name.lower().startswith(("video", "file", "doc")):
        temp_raw = re.sub(r'\b(19\d{2}|20[0-2]\d)\b', '', raw_name)
        temp_raw = re.sub(r'\b(?:ep|episode|e|s|season)\s*\d+\b', '', temp_raw, flags=re.IGNORECASE)
        temp_raw = re.sub(r'[^a-zA-Z0-9\s]', ' ', temp_raw)
        clean_title = " ".join(temp_raw.split()).strip().title()

    # 3. ပါလာသော Caption ထဲမှ Hashtag စစ်ထုတ်ခြင်း (#DrRomantic)
    if not clean_title:
        ignore_tags = ['1080p', '720p', '480p', '360p', '4k', 'hd', 'fhd', 'bluray', 'mmsub', 'sub', 'engsub']
        hashtags = re.findall(r'#(\w+)', full_search_text)
        for tag in hashtags:
            if tag.lower() not in ignore_tags:
                tag_spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', tag)
                clean_title = tag_spaced.replace("_", " ").strip().title()
                break

    # 4. စာသားရှင်းထုတ်ပြီး နောက်ဆုံး Title သတ်မှတ်ခြင်း
    if not clean_title or clean_title.lower() in ["video", "file", "movie", "telegram"]:
        clean_title = "Media Movie"

    # Formatting ပြန်ပေါင်းခြင်း
    if season_number and ep_number:
        final_name = f"{clean_title} S{season_number} Ep {ep_number}"
    elif ep_number:
        final_name = f"{clean_title} Ep {ep_number}"
    elif year_str:
        final_name = f"{clean_title} {year_str}"
    else:
        final_name = clean_title

    return f"{final_name}{ext}"


def extract_file_name(message) -> str:
    """Telegram Message ထဲမှ Forward Name, File Name နှင့် Caption အကုန်ဆွဲထုတ်ပေးသည့် Function"""
    file_name = None
    caption = message.text or ""
    fwd_title = ""

    # Forward လုပ်ထားသည့် Channel / Chat Name ရှိပါက ယူမည်
    if message.forward and message.forward.chat:
        fwd_title = message.forward.chat.title or ""

    # Document / Video File Name စစ်ဆေးခြင်း
    if message.document and message.document.attributes:
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                file_name = attr.file_name
                break

    if not file_name and message.video:
        if hasattr(message.video, 'attributes'):
            for attr in message.video.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    file_name = attr.file_name
                    break

    if not file_name:
        file_name = "Video.mp4"

    return clean_and_format_title(file_name, caption_text=caption, fwd_title=fwd_title)


# --- [ TELEGRAM BOT SECTION ] ---

@bot.on(events.NewMessage(pattern='/start', incoming=True))
async def start_handler(event):
    await event.reply("👋 မင်္ဂလာပါ! ကျွန်တော့်ဆီကို ဘယ်ဗီဒီယိုဖိုင်မဆို ပို့ပေးပါ။ တိုက်ရိုက်ကြည့်ရှုနိုင်မယ့် Stream Link ထုတ်ပေးပါမယ်။")

@bot.on(events.NewMessage(incoming=True))
async def video_handler(event):
    if event.message.text and event.message.text.startswith('/start'):
        return

    media = event.message.video
    if not media and event.message.document:
        if event.message.document.mime_type and event.message.document.mime_type.startswith('video/'):
            media = event.message.document

    if media:
        chat_id = event.chat_id
        message_id = event.message.id
        
        raw_file_name = extract_file_name(event.message)
        safe_file_name = quote(raw_file_name)
        
        stream_link = f"{SERVER_URL}/stream/{chat_id}/{message_id}/{safe_file_name}"
        
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

@app.get("/stream/{chat_id}/{message_id}/{file_name:path}")
async def stream_video(chat_id: int, message_id: int, file_name: str, request: Request):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")
            
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        file_size = file.size
        mime_type = file.mime_type or "video/mp4"
        range_header = request.headers.get("range")
        
        display_name = unquote(file_name)
        
        headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
            "Content-Disposition": f'inline; filename="{display_name}"'
        }
        
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
            else:
                start, end = 0, file_size - 1
            
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
    try:
        await bot.start(bot_token=BOT_TOKEN)
        print("✅ Telegram Bot Successfully Started!")
    except FloodWaitError as e:
        print(f"⚠️ Telegram Rate Limit! Waiting for {e.seconds} seconds...")
        await asyncio.sleep(e.seconds)
        await bot.start(bot_token=BOT_TOKEN)

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
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot ရပ်နားလိုက်ပါပြီ။")
