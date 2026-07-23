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
    """
    Movies: Title + Year
    Series: Title + (Year) + Season/Ep No 
    အတိအကျ ထွက်ရှိစေရန် စိစစ်ပေးသည့် Function
    """
    if not raw_name:
        raw_name = ""

    raw_name = myanmar_to_english_digits(raw_name)
    caption_text = myanmar_to_english_digits(caption_text)
    fwd_title = myanmar_to_english_digits(fwd_title)

    # 1. Extension သီးသန့် ခွဲထုတ်ခြင်း (.mp4, .mkv စသည်)
    ext = ".mp4"
    if "." in raw_name:
        parts = raw_name.rsplit(".", 1)
        if len(parts[1]) <= 4:
            raw_name, ext = parts[0], f".{parts[1]}"

    full_text = f"{raw_name} {caption_text} {fwd_title}"

    # 2. Season နှင့် Episode ဂဏန်းများ ရှာဖွေခြင်း
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

    # 3. Year (ခုနှစ်) ရှာဖွေခြင်း (ဥပမာ 2026, 2024)
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', full_text)
    year_str = f"({year_match.group(1)})" if year_match else ""

    # 4. TITLE စိစစ်ထုတ်ယူခြင်း
    clean_title = ""

    # မလိုအပ်သော Ads, Channel, Quality စာလုံးများ Filter လုပ်ခြင်း
    unwanted_patterns = [
        r'http\S+', r'www\.\S+', r'@\w+', r't\.me/\S+',
        r'translation\s*-\s*\w+', r'uploader\s*-\s*\w+', r'subbed\s*by\s*\w+',
        r'\bcrawler\b', r'\bjoined\b', r'\bjoin\b', r'\bkara\b', r'\bsu\b', r'\bmw\b',
        r'\bmyanmar\s*sub(?:titles?)?\b', r'\bmmsub(?:titles?)?\b', r'\bsubtitles?\b', r'\bsub\b',
        r'\b1080p?\b', r'\b720p?\b', r'\b480p?\b', r'\b360p?\b', r'\b4k\b', r'\bhd\b', r'\bfhd\b', r'\bhq\b',
        r'\bweb\s*dl\b', r'\bweb-dl\b', r'\bwebrip\b', r'\bbluray\b', r'\bhdrip\b',
        r'\bx264\b', r'\bx265\b', r'\baac\b', r'\besub\b', r'\bdecensored\b', r'\bcensored\b',
        r'\btrue\s*homie\b', r'\bhomie\b', r'\btamil\b', r'\bmalayalam\b', r'\benglish\b', r'\braw\b',
        r'\b19\d{2}\b', r'\b20\d{2}\b' # Year ကို Title ထဲမှ ဖျက်ပြီး သီးသန့်ပြန်ဆက်မည်
    ]

    # Code Name (ဥပမာ ATID-574) ရှာဖွေခြင်း
    code_match = re.search(r'\b([a-zA-Z]{2,5}[-_]?\d{3,4})\b', full_text)
    if code_match:
        clean_title = code_match.group(1).upper().replace("_", "-")

    # Hashtag မှ Title ယူခြင်း
    if not clean_title:
        hashtags = re.findall(r'#(\w+)', full_text)
        ignore_tags = ['1080p', '720p', '480p', '4k', 'hd', 'mmsub', 'sub', 'raw']
        for tag in hashtags:
            if tag.lower() not in ignore_tags:
                tag_spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', tag)
                clean_title = tag_spaced.replace("_", " ").strip().title()
                break

    # မူရင်း ဖိုင်နာမည် / Caption မှ Title ရွေးထုတ်ခြင်း
    if not clean_title:
        target_text = raw_name if (raw_name and raw_name.lower() not in ["video", "file", "movie", "video_file"]) else f"{caption_text} {fwd_title}"
        
        temp_text = target_text
        for pattern in unwanted_patterns:
            temp_text = re.sub(pattern, ' ', temp_text, flags=re.IGNORECASE)

        temp_text = re.sub(r'[\u1000-\u109F]+', ' ', temp_text) # မြန်မာစာ ရှင်းထုတ်မည်
        temp_text = re.sub(r'[^a-zA-Z0-9\s]', ' ', temp_text)
        
        words = temp_text.split()
        dedup_words = []
        for w in words:
            if len(w) > 1 and w.lower() not in ["the", "and", "for", "with"]:
                dedup_words.append(w)
                if len(dedup_words) >= 4:
                    break
        
        if dedup_words:
            clean_title = " ".join(dedup_words).strip().title()

    if not clean_title or clean_title.lower() in ["video", "file", "movie"]:
        clean_title = "Media"

    # 5. သင့် တောင်းဆိုချက်အတိုင်း အတိအကျ Output ပုံစံထုတ်ခြင်း
    # A. Season + Episode ပါသည့် Series များ
    if season_number and ep_number:
        if year_str:
            final_name = f"{clean_title} {year_str} S{season_number} Ep {ep_number}"
        else:
            final_name = f"{clean_title} S{season_number} Ep {ep_number}"

    # B. Episode သီးသန့်ပါသည့် Series များ
    elif ep_number:
        if year_str:
            final_name = f"{clean_title} {year_str} Ep {ep_number}"
        else:
            final_name = f"{clean_title} Ep {ep_number}"

    # C. Movie များ (ခုနှစ် ပါသည်)
    elif year_str:
        final_name = f"{clean_title} {year_str}"

    # D. ခုနှစ်/Ep No မပါသည့် ဗီဒီယိုများ
    else:
        final_name = clean_title

    return f"{final_name}{ext}"


def extract_file_name(message) -> str:
    """Telegram Message မှ File Name၊ Forward Info နှင့် Caption ကို စိစစ်ထုတ်ယူပေးမည့် Function"""
    file_name = None
    caption = message.text or message.caption or ""
    fwd_title = ""

    if message.forward:
        if message.forward.chat:
            fwd_title = message.forward.chat.title or ""
        elif message.forward.sender:
            first_name = message.forward.sender.first_name or ""
            last_name = message.forward.sender.last_name or ""
            fwd_title = f"{first_name} {last_name}".strip()

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
    chunk_size = 1024 * 1024
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
        
