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

def myanmar_to_english_digits(text: str) -> str:
    """မြန်မာဂဏန်းများကို အင်္ဂလိပ်ဂဏန်းသို့ ပြောင်းပေးသည့် Function"""
    mm_digits = '၀၁၂၃၄၅၆၇၈၉'
    en_digits = '0123456789'
    trans_table = str.maketrans(mm_digits, en_digits)
    return text.translate(trans_table)

def clean_and_format_title(raw_name: str, caption_text: str = "") -> str:
    """Movie/Series ဖိုင်များအတွက် Crawler, Joined, မြန်မာစာ စာလုံးများ ရှင်းထုတ်ပြီး King Avatar Ep 01.mp4 ပုံစံထုတ်ပေးသည့် Function"""
    if not raw_name:
        raw_name = ""

    # မြန်မာဂဏန်းများကို အင်္ဂလိပ်ဂဏန်းသို့ ပြောင်းမည်
    raw_name = myanmar_to_english_digits(raw_name)
    caption_text = myanmar_to_english_digits(caption_text)

    # 1. Extension ကို ခွဲထုတ်မည်
    ext = ".mp4"
    if "." in raw_name:
        parts = raw_name.rsplit(".", 1)
        if len(parts[1]) <= 4:
            raw_name, ext = parts[0], f".{parts[1]}"

    caption_first_line = caption_text.split('\n')[0] if caption_text else ""
    clean_text = f"{raw_name} {caption_first_line}"

    # 2. Season/Episode ဂဏန်းကို အရင်ဆုံး ရှာဖွေမည်
    ep_number = ""
    season_number = ""

    s_ep_match = re.search(r'\bs(\d{1,2})\s*e(\d{1,4})\b', clean_text, re.IGNORECASE)
    if s_ep_match:
        season_number = str(int(s_ep_match.group(1))).zfill(2)
        ep_number = str(int(s_ep_match.group(2))).zfill(2)
    else:
        # Ep, Episode, E သို့မဟုတ် "အပိုင်း" အနောက်မှ ဂဏန်းများကို ရှာမည်
        ep_match = re.search(r'(?:ep|episode|e|အပိုင်း)\s*[:._-]?\s*(\d{1,4})', clean_text, re.IGNORECASE)
        if not ep_match:
            ep_match = re.search(r'\b(\d{1,3})\b', clean_text)
        if ep_match:
            ep_number = str(int(ep_match.group(1))).zfill(2)

    # 3. Year/ခုနှစ် ပါမပါ ရှာမည်
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', clean_text)
    year_str = f"({year_match.group(1)})" if year_match else ""

    # 4. မလိုလားအပ်သော စာလုံးများနှင့် မြန်မာစာလုံးများ ဖယ်ထုတ်ခြင်း
    # Crawler, Joined, Subtitle, အပိုင်း စသည့် စာလုံးများအားလုံးရှင်းထုတ်မည်
    unwanted_patterns = [
        r'crawler', r'joined', r'join', r'channel', r'telegram',
        r'myanmar\s*sub(?:titles?)?', r'mmsub(?:titles?)?', r'subtitles?', r'sub',
        r'\[mmsub\]', r'\(mmsub\)', r'\[\s*\]', r'\(\s*\)',
        r'1080p?', r'720p?', r'480p?', r'4k', r'hd', r'web-dl',
        r'bluray', r'hdrip', r'x264', r'x265', r'aac', r'esub',
        r'http\S+', r'www\.\S+', r'@\w+',
        r'အပိုင်း', r'စစ်နတ်ဘုရားရဲ့ဂုဏ်သရေ' # အပိုစာလုံးများ
    ]
    
    for pattern in unwanted_patterns:
        clean_text = re.sub(pattern, ' ', clean_text, flags=re.IGNORECASE)

    # မြန်မာစာလုံးများပါပါက Title သန့်သွားစေရန် ဖယ်ထုတ်မည် (အင်္ဂလိပ် Title သီးသန့်ရရန်)
    title_name = re.sub(r'[\u1000-\u109F]', ' ', clean_text)

    # Ep / Season / Year ဂဏန်းများကို Title ထဲမှ ဖယ်ထုတ်မည်
    title_name = re.sub(r'\bs\d{1,2}\s*e\d{1,4}\b', ' ', title_name, flags=re.IGNORECASE)
    title_name = re.sub(r'(?:ep|episode|e)\s*[:._-]?\s*\d{1,4}', ' ', title_name, flags=re.IGNORECASE)
    if year_match:
        title_name = re.sub(r'\b(19\d{2}|20\d{2})\b', ' ', title_name)

    # Special Characters နှင့် ပိုနေသော Space များကို ရှင်းထုတ်ခြင်း
    title_name = re.sub(r'[^a-zA-Z0-9\s]', ' ', title_name)
    title_name = re.sub(r'\s+', ' ', title_name).strip()

    # Title Case ပြုလုပ်ခြင်း (king avatar -> King Avatar)
    title_name = title_name.title()

    if not title_name or title_name.lower() in ["video", "file", "movie"]:
        title_name = "King Avatar"  # Default အမည်

    # 5. Final Output Format ပြန်လည်ပေါင်းစပ်ခြင်း
    if season_number and ep_number:
        final_name = f"{title_name} S{season_number} Ep {ep_number}"
    elif ep_number:
        final_name = f"{title_name} Ep {ep_number}"
    elif year_str:
        final_name = f"{title_name} {year_str}"
    else:
        final_name = title_name

    return f"{final_name}{ext}"


def extract_file_name(message) -> str:
    """Telegram Message မှ Video/Document ရဲ့ File Name နှင့် Caption ကို တွဲဖက်ထုတ်ယူပေးသည့် Function"""
    file_name = None
    caption = message.text or ""

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

    return clean_and_format_title(file_name, caption_text=caption)


# --- [ TELEGRAM BOT SECTION ] ---

@bot.on(events.NewMessage(pattern='/start', incoming=True))
async def start_handler(event):
    await event.reply("👋 မင်္ဂလာပါ! ကျွန်တော့်ဆီကို ဘယ်ဗီဒီယိုဖိုင်မဆို ပို့ပေးပါ။ တိုက်ရိုက်ကြည့်ရှုနိုင်မယ့် Stream Link ထုတ်ပေးပါမယ်။")

@bot.on(events.NewMessage(incoming=True))
async def video_handler(event):
    # Command စာသားများဖြစ်ပါက ကျော်မည်
    if event.message.text and event.message.text.startswith('/start'):
        return

    # Video တစ်ခုတည်းကိုသာ စစ်ဆေးမည် (Message ၂ ခါ ထွက်ခြင်းကို ကာကွယ်ရန်)
    media = event.message.video
    if not media and event.message.document:
        if event.message.document.mime_type and event.message.document.mime_type.startswith('video/'):
            media = event.message.document

    if media:
        chat_id = event.chat_id
        message_id = event.message.id
        
        # ဖိုင်နာမည် သန့်ရှင်း၍ ရယူခြင်း
        raw_file_name = extract_file_name(event.message)
        # URL Safe ဖြစ်စေရန် Quote ပြုလုပ်ခြင်း
        safe_file_name = quote(raw_file_name)
        
        # Cloud Domain ဖြင့် Link ထုတ်ပေးခြင်း
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
