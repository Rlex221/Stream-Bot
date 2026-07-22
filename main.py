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

def clean_and_format_title(raw_title: str) -> str:
    """မည်သည့် Movie/Series ဖိုင်မဆို မလိုလားအပ်သည်များရှင်းထုတ်ပြီး Universal Format ထုတ်ပေးသည့် Function"""
    if not raw_title:
        return "Video.mp4"

    # မြန်မာဂဏန်းများကို အင်္ဂလိပ်ဂဏန်းသို့ ပြောင်းမည်
    text = myanmar_to_english_digits(raw_title)

    # 1. Extension ကို ခွဲထုတ်မည်
    ext = ".mp4"
    if "." in text:
        parts = text.rsplit(".", 1)
        if len(parts[1]) <= 4 and re.match(r'^[a-zA-Z0-9]+$', parts[1]):
            text, ext = parts[0], f".{parts[1]}"

    # 2. မလိုလားအပ်သော Quality, Channel Name, Crawler, Subtitle Tag များကို အရင်ဆုံး ရှင်းထုတ်မည်
    unwanted_patterns = [
        r'crawler', r'joined', r'mmsubtitle[s]?', r'mmsub[s]?', r'myanmar\s*sub', 
        r'subtitle[s]?', r'\[mmsub\]', r'\(mmsub\)', r'\d+sub',
        r'bot', r'channel', r'telegram', r't\.me/\S+', r'https?://\S+',
        r'1080p?', r'720p?', r'480p?', r'4k', r'hd', r'web-dl',
        r'bluray', r'hdrip', r'x264', r'x265', r'aac', r'esub'
    ]
    
    for pattern in unwanted_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # 3. Episode သို့မဟုတ် Season ပါမပါ ရှာမည်
    ep_number = ""
    season_number = ""

    # Season & Episode တွဲလျက်ပါပါက (ဥပမာ S01E05, S1E2)
    s_ep_match = re.search(r's(\d{1,2})\s*e(\d{1,4})', text, re.IGNORECASE)
    if s_ep_match:
        season_number = str(int(s_ep_match.group(1)))
        ep_number = str(int(s_ep_match.group(2)))
        text = re.sub(r's\d{1,2}\s*e\d{1,4}', '', text, flags=re.IGNORECASE)
    else:
        # Episode သီးသန့်ပါပါက (ဥပမာ Ep 107, Episode 5, E02, သို့မဟုတ် စာကြောင်းအဆုံး/အလယ်ရှိ Ep)
        ep_match = re.search(r'(?:ep|episode|e)?\s*[:._-]?\s*(\d{1,4})', text, re.IGNORECASE)
        if ep_match:
            ep_number = str(int(ep_match.group(1)))
            text = re.sub(r'(?:ep|episode|e)?\s*[:._-]?\s*\d{1,4}', '', text, flags=re.IGNORECASE)

    # 4. Year/ခုနှစ် ပါမပါ ရှာမည်
    year_match = re.search(r'(19\d{2}|20\d{2})', text)
    year_str = f"({year_match.group(1)})" if year_match else ""
    if year_match:
        text = re.sub(r'(19\d{2}|20\d{2})', '', text)

    # Special Characters နှင့် ပိုနေသော Space များကို ရှင်းထုတ်ခြင်း
    text = re.sub(r'[\\/*?:"<>|\[\]()]', ' ', text)
    text = re.sub(r'[\._-]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Title Case ပြုလုပ်ခြင်း
    text = text.title()

    # ဖိုင်နာမည် လုံးဝမကျန်ပါက Default ထည့်ပေးခြင်း
    if not text:
        text = "Video"

    # 5. Output Format ပြန်လည်ပေါင်းစပ်ခြင်း
    if season_number and ep_number:
        final_name = f"{text} S{season_number} Ep {ep_number}"
    elif ep_number:
        final_name = f"{text} Ep {ep_number}"
    elif year_str:
        final_name = f"{text} {year_str}"
    else:
        final_name = text

    return f"{final_name}{ext}"


def extract_file_name(message) -> str:
    """Telegram Message မှ Video/Document ရဲ့ File Name နှင့် Caption ကို တွဲဖက်ထုတ်ယူပေးသည့် Function"""
    file_name = None
    caption = message.text or ""

    # 1. Video Attribute ထဲမှ File Name ရှာမည်
    if message.document and message.document.attributes:
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                file_name = attr.file_name
                break

    if not file_name and message.video and hasattr(message.video, 'attributes'):
        for attr in message.video.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                file_name = attr.file_name
                break

    # 2. File Name မရှိပါက သို့မဟုတ် File Name က 'video.mp4' ကဲ့သို့ ယေဘုယျဆန်နေပါက Caption မှ နာမည်ယူမည်
    if not file_name or file_name.lower().startswith("video"):
        if caption:
            # Caption ၏ ပထမဆုံး လိုင်းကို ယူမည်
            first_line = caption.strip().split('\n')[0].strip()
            if first_line:
                file_name = first_line

    if not file_name:
        file_name = "Video.mp4"

    return clean_and_format_title(file_name)


# --- [ TELEGRAM BOT SECTION ] ---

@bot.on(events.NewMessage(pattern='/start', incoming=True))
async def start_handler(event):
    await event.reply("👋 မင်္ဂလာပါ! ကျွန်တော့်ဆီကို ဘယ်ဗီဒီယိုဖိုင်မဆို ပို့ပေးပါ။ တိုက်ရိုက်ကြည့်ရှုနိုင်မယ့် Stream Link ထုတ်ပေးပါမယ်။")

@bot.on(events.NewMessage(incoming=True))
async def video_handler(event):
    if event.message.text and event.message.text.startswith('/start'):
        return

    # Message နှစ်ခါမထွက်စေရန် Video (သို့မဟုတ်) Video Mime Type ရှိသော Document ကိုသာ စစ်ထုတ်ခြင်း
    is_video = False
    if event.message.video:
        is_video = True
    elif event.message.document and event.message.document.mime_type and event.message.document.mime_type.startswith('video/'):
        is_video = True

    if is_video:
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
