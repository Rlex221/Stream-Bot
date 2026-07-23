import asyncio
import os
import re
import base64
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

def encode_stream_id(chat_id: int, message_id: int) -> str:
    """chat_id နဲ့ message_id ကို URL ထဲမှာ မမြင်ရအောင် Encoded String အဖြစ် ပြောင်းပေးသည့် Function"""
    raw_str = f"{chat_id}:{message_id}"
    return base64.urlsafe_b64encode(raw_str.encode()).decode().strip("=")

def decode_stream_id(stream_id: str) -> tuple[int, int]:
    """Encoded String မှ chat_id နဲ့ message_id ကို မူရင်းအတိုင်း ပြန်ထုတ်ပေးသည့် Function"""
    padding = '=' * (4 - (len(stream_id) % 4))
    decoded_str = base64.urlsafe_b64decode(stream_id + padding).decode()
    chat_id_str, msg_id_str = decoded_str.split(":")
    return int(chat_id_str), int(msg_id_str)

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

    full_text = f"{raw_name}\n{caption_text}\n{fwd_title}"

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

    # 3. Year (ခုနှစ်) ရှာဖွေခြင်း
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', full_text)
    year_str = f"({year_match.group(1)})" if year_match else ""

    # 4. TITLE စိစစ်ထုတ်ယူခြင်း
    clean_title = ""

    # A. Caption/Text ထဲမှ English Title များကို ဦးစားပေး ရှာဖွေခြင်း (ဥပမာ - "Falling into your Smile")
    lines = full_text.split('\n')
    for line in lines:
        line_clean = re.sub(r'http\S+|t\.me/\S+|#\w+|@\w+', '', line).strip()
        # "EP - 2" သို့မဟုတ် "Episode 2" ကဲ့သို့ စာသားများကို Title ထဲမပါအောင် ဖယ်ထုတ်ခြင်း
        line_clean = re.sub(r'(?:ep|episode|e|အပိုင်း)\s*[\(\[\{:._-]?\s*\d{1,3}\b.*', '', line_clean, flags=re.IGNORECASE).strip()
        
        # English စာလုံး အနည်းဆုံး ၂ လုံးပါဝင်ပြီး Crd, Join, Main စသည်တို့မဟုတ်သော Line ကို Title အဖြစ်ယူမည်
        eng_words = re.findall(r'[a-zA-Z]{2,}', line_clean)
        filtered_words = [w for w in eng_words if w.lower() not in ['crd', 'credit', 'channel', 'link', 'main', 'join', 'sub', 'mmsub', 'video']]
        
        if len(filtered_words) >= 2:
            clean_title = " ".join(filtered_words).title()
            break

    # B. အထက်ပါနည်းဖြင့် မရပါက Code Name (ဥပမာ ATID-574) ရှာဖွေခြင်း
    if not clean_title:
        code_match = re.search(r'\b([a-zA-Z]{2,5}[-_]?\d{3,4})\b', full_text)
        if code_match:
            clean_title = code_match.group(1).upper().replace("_", "-")

    # C. Hashtag မှ Title ယူခြင်း (Crd, 1080p Tag များကို ပယ်သည်)
    if not clean_title:
        hashtags = re.findall(r'#(\w+)', full_text)
        ignore_tags = ['1080p', '720p', '480p', '4k', 'hd', 'mmsub', 'sub', 'raw', 'crd', 'credit']
        for tag in hashtags:
            if tag.lower() not in ignore_tags:
                tag_spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', tag)
                clean_title = tag_spaced.replace("_", " ").strip().title()
                break

    # D. raw_name (Filename) ထဲမှ ရှာဖွေခြင်း
    if not clean_title and raw_name and raw_name.lower() not in ["video", "file", "movie", "crd", "crd.mp4"]:
        eng_words = re.findall(r'[a-zA-Z]{2,}', raw_name)
        filtered_words = [w for w in eng_words if w.lower() not in ['crd', 'credit', 'video', 'mp4', 'mkv']]
        if filtered_words:
            clean_title = " ".join(filtered_words).title()

    if not clean_title or clean_title.lower() in ["video", "file", "movie", "crd"]:
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
        
        # chat_id နဲ့ message_id ကို ID သီးသန့် Encode ပြုလုပ်ခြင်း
        stream_id = encode_stream_id(chat_id, message_id)
        
        raw_file_name = extract_file_name(event.message)
        safe_file_name = quote(raw_file_name)
        
        # Link ပုံစံ: SERVER_URL/stream/STREAM_ID/FILE_NAME
        stream_link = f"{SERVER_URL}/stream/{stream_id}/{safe_file_name}"
        
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

@app.get("/stream/{stream_id}/{file_name:path}")
async def stream_video(stream_id: str, file_name: str, request: Request):
    try:
        # Encoded string မှ chat_id နှင့် message_id ကို ပြန်ဖြည်ယူခြင်း
        try:
            chat_id, message_id = decode_stream_id(stream_id)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid stream ID format")

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
            
    except HTTPException:
        raise
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
