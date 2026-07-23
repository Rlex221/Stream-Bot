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
API_ID = int(os.environ.get("API_ID", 0))  # သို့မဟုတ် int("YOUR_API_ID")
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN")

# သင့် Render / Railway ရဲ့ Domain URL ကို ဒီနေရာမှာ ထည့်ပါ (အနောက်မှာ / မပါရပါ)
SERVER_URL = os.environ.get("SERVER_URL", "https://your-app-name.onrender.com")

# Telethon Telegram Client
bot = TelegramClient('telethon_stream_bot', API_ID, API_HASH)
app = FastAPI(title="Telegram Video Streamer")


# --- [ HELPER FUNCTIONS ] ---

def myanmar_to_english_digits(text: str) -> str:
    """မြန်မာဂဏန်းများကို အင်္ဂလိပ်ဂဏန်းသို့ ပြောင်းပေးသည့် Function"""
    mm_digits = '၀၁၂၃၄၅၆၇၈၉'
    en_digits = '0123456789'
    trans_table = str.maketrans(mm_digits, en_digits)
    return text.translate(trans_table)

def clean_and_format_title(raw_name: str, caption_text: str = "", forward_from_name: str = "") -> str:
    """
    Title ဖော်ထုတ်ပေးမည့် Advanced Logic
    1. Telegram File Name အစစ်ပါရင် အရင်ယူမည်။
    2. မပါရင် Forward Channel Name ကို ဦးစားပေးယူမည်။
    3. Code Name (ATID-574 စသည်) သို့မဟုတ် Caption ထဲမှ Episode / Season များကို ပေါင်းစပ်မည်။
    """
    if not raw_name:
        raw_name = ""

    raw_name = myanmar_to_english_digits(raw_name)
    caption_text = myanmar_to_english_digits(caption_text)
    forward_from_name = myanmar_to_english_digits(forward_from_name)

    # Extension မူရင်းအတိုင်း ခွဲထုတ်မည် (.mp4, .mkv, .avi စသည်)
    ext = ".mp4"
    if "." in raw_name:
        parts = raw_name.rsplit(".", 1)
        if len(parts[1]) <= 4:
            raw_name, ext = parts[0], f".{parts[1]}"

    # တကယ်လို့ မူရင်း File Name ရှိနေပြီး မူလ Default မဟုတ်ရင် Direct သုံးမည်
    if raw_name and raw_name.lower() not in ["video", "file", "movie", "video.mp4", "file.mp4"]:
        # အပို စာလုံးအချို့ သန့်စင်ပြီး မူရင်းအတိုင်း ပြန်ပေးမည်
        return f"{raw_name}{ext}"

    # File Name မပါပါက Forward Channel Name သို့မဟုတ် Caption ထဲမှ စာသားကို ယူမည်
    full_text = f"{forward_from_name} {caption_text}".strip()

    # Episode & Season ရှာဖွေခြင်း
    ep_number = ""
    season_number = ""

    s_ep_match = re.search(r'\bs(\d{1,2})\s*e(\d{1,3})\b', full_text, re.IGNORECASE)
    if s_ep_match:
        season_number = str(int(s_ep_match.group(1))).zfill(2)
        ep_number = str(int(s_ep_match.group(2))).zfill(2)
    else:
        ep_match = re.search(r'(?:ep|episode|e|အပိုင်း)\s*[\(\[\{:._-]?\s*(\d{1,3})\b', full_text, re.IGNORECASE)
        if ep_match:
            ep_number = str(int(ep_match.group(1))).zfill(2)

    # Movie Code (ဥပမာ- ATID-574, SSIS-123) ရှာဖွေခြင်း
    code_match = re.search(r'\b([a-zA-Z]{2,5}[-_]\d{3,4})\b', full_text)
    movie_code = code_match.group(1).upper() if code_match else ""

    clean_title = ""

    # A. Forward From Name ရှိပါက ၎င်းကို အဓိက Title အဖြစ်ယူမည် (ဥပမာ- Dr. Romantic (2020) - Season (1))
    if forward_from_name:
        # Link များနှင့် မလိုအပ်သော Tag များ ဖျက်မည်
        clean_title = re.sub(r'http\S+|www\.\S+|@\w+', '', forward_from_name).strip()

    # B. Movie Code မိပါက Code ကို Title အဖြစ် သုံးမည်
    elif movie_code:
        clean_title = movie_code

    # C. အထက်ပါအတိုင်း မမိပါက Caption ထဲမှ Title စာသား ထုတ်ယူမည်
    if not clean_title:
        ignore_tags = [
            '1080p', '720p', '480p', '360p', '4k', 'hd', 'fhd', 'bluray',
            'webrip', 'webdl', 'mmsub', 'sub', 'engsub', 'esub', 'raw'
        ]
        
        # Hashtags ရှာဖွေခြင်း
        hashtags = re.findall(r'#(\w+)', full_text)
        for tag in hashtags:
            if tag.lower() not in ignore_tags:
                tag_spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', tag)
                clean_title = tag_spaced.replace("_", " ").strip().title()
                break

        if not clean_title:
            unwanted_patterns = [
                r'main\s*channel\s*link', r'translation\s*-\s*\w+', r'uploader\s*-\s*\w+',
                r'http\S+', r'www\.\S+', r'@\w+', r't\.me\/\S+'
            ]
            temp_text = full_text
            for pattern in unwanted_patterns:
                temp_text = re.sub(pattern, ' ', temp_text, flags=re.IGNORECASE)

            temp_text = re.sub(r'[\u1000-\u109F]+', ' ', temp_text) # မြန်မာစာ ဖျက်မည်
            temp_text = re.sub(r'[^a-zA-Z0-9\s-]', ' ', temp_text)
            
            words = temp_text.split()
            dedup_words = [w for w in words if w.lower() not in ignore_tags and len(w) > 1]
            if dedup_words:
                clean_title = " ".join(dedup_words[:4]).strip().title()

    if not clean_title:
        clean_title = "Video"

    # Title အချောသတ် ပေါင်းစပ်ခြင်း
    final_name = clean_title

    # Season/Ep ထည့်သွင်းခြင်း (Title ထဲမှာ Season/Ep မပါသေးပါက)
    if ep_number and f"ep {ep_number}".lower() not in final_name.lower():
        if season_number and f"s{season_number}".lower() not in final_name.lower():
            final_name = f"{final_name} S{season_number} Ep {ep_number}"
        else:
            final_name = f"{final_name} Ep {ep_number}"

    return f"{final_name}{ext}"


async def extract_file_name(message) -> str:
    """Telegram Message မှ Original File Name, Forward Channel Name နှင့် Caption တို့ကို ထုတ်ယူပေးသည့် Function"""
    file_name = None
    caption = message.text or message.caption or ""
    forward_from_name = ""

    # 1. Telegram Original File Name စစ်ဆေးခြင်း
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

    # 2. Forward Message မှ Channel/Sender Name စစ်ဆေးခြင်း
    if message.forward:
        if message.forward.chat:
            forward_from_name = message.forward.chat.title or ""
        elif message.forward.sender:
            forward_from_name = f"{message.forward.sender.first_name or ''} {message.forward.sender.last_name or ''}".strip()
        elif message.forward.from_name:
            forward_from_name = message.forward.from_name

    return clean_and_format_title(file_name, caption_text=caption, forward_from_name=forward_from_name)


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
        
        raw_file_name = await extract_file_name(event.message)
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
