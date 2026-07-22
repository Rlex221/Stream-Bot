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

def clean_and_format_title(name: str, caption_text: str = "") -> str:
    """mmsub ဖြုတ်ခြင်း၊ စာလုံးပိုများရှင်းခြင်း၊ စာလုံးရှေ့အကြီးပြောင်းခြင်း နှင့် EP နံပါတ်စနစ်တကျ ခွဲထုတ်ပေးသည့် Function"""
    if not name:
        name = ""

    # 1. Extension ကို သီးသန့်ခွဲထုတ်ထားမည်
    ext = ".mp4"
    if "." in name:
        parts = name.rsplit(".", 1)
        if len(parts[1]) <= 4:
            name, ext = parts[0], f".{parts[1]}"

    # 2. Underscore (_), Dot (.) များကို Space သို့ ပြောင်းမည်
    name = re.sub(r'[\._]', ' ', name)

    # 3. မလိုလားအပ်သော မကင်းရာမကင်းကြောင်း စာသားများ (Crawler, Joined, mmsub စသည်) ရှင်းထုတ်ခြင်း
    unwanted_patterns = [
        r'\bmmsubtitles?\b', r'\bmmsubs?\b', r'\bmyanmar\s*sub\b', 
        r'\bsubtitles?\b', r'\[mmsub\]', r'\(mmsub\)', r'\bsub\b',
        r'\bcrawler\b', r'\bjoined\b', r'\bbot\b', r'\bchannel\b'
    ]
    for pattern in unwanted_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # 4. Caption ထဲတွင် Episode ဂဏန်း ပါမပါ စစ်ဆေးခြင်း
    ep_number = ""
    if caption_text:
        # Ep 107, Episode 107, E107 သို့မဟုတ် စာကြောင်းအစ/အဆုံး၌ ဂဏန်းသီးသန့်ပါပါက ရှာမည်
        ep_match = re.search(r'\b(?:ep|episode|e)?\s*[:._-]?\s*(\d{1,4})\b', caption_text, re.IGNORECASE)
        if ep_match:
            ep_number = ep_match.group(1)

    # File Name ကိုယ်တိုင်ထဲတွင်လည်း Episode ပါမပါ ရှာခြင်း
    if not ep_number:
        ep_match_name = re.search(r'\b(?:ep|episode|e)\s*[:._-]?\s*(\d{1,4})\b', name, re.IGNORECASE)
        if ep_match_name:
            ep_number = ep_match_name.group(1)

    # 5. File Name ထဲမှ Ep / Episode စာသားများနှင့် သီးသန့် ဂဏန်းများကို ခဏရှင်းထုတ်၍ Clean လုပ်ခြင်း
    name = re.sub(r'\b(?:ep|episode|e)?\s*[:._-]?\s*\d{1,4}\b', '', name, flags=re.IGNORECASE)
    
    # Special Characters များ ရှင်းထုတ်ခြင်း
    name = re.sub(r'[\\/*?:"<>|\[\]()]', ' ', name)
    name = re.sub(r'[-_]+', ' ', name)
    name = re.sub(r'\s+', ' ', name).strip()

    # 6. စာလုံးတိုင်း၏ ရှေ့စာလုံးကို အကြီးပြောင်းခြင်း (Title Case)
    name = name.title()

    # 7. Series Name + Episode Number ကို စနစ်တကျ ပြန်လည် ပေါင်းစပ်ခြင်း
    if ep_number:
        if name:
            final_name = f"{name} Ep {ep_number}"
        else:
            final_name = f"Episode {ep_number}"
    else:
        final_name = name if name else "Video"

    return f"{final_name}{ext}"


def extract_file_name(message) -> str:
    """Telegram Message မှ Video/Document ရဲ့ File Name နှင့် Caption ကို တွဲဖက်ထုတ်ယူပေးသည့် Function"""
    file_name = None
    caption = message.text or ""

    # Document ဖြစ်ပါက attributes ထဲမှ file_name ကို ရှာမည်
    if message.document and message.document.attributes:
        for attr in message.document.attributes:
            if hasattr(attr, 'file_name') and attr.file_name:
                file_name = attr.file_name
                break

    # Video file ဖြစ်ပြီး file_name မရှိသေးပါက
    if not file_name and message.video:
        if hasattr(message.video, 'attributes'):
            for attr in message.video.attributes:
                if hasattr(attr, 'file_name') and attr.file_name:
                    file_name = attr.file_name
                    break

    # Caption သို့မဟုတ် Text ၏ ပထမစာကြောင်းကို ဖိုင်နာမည်အဖြစ် သုံးခြင်း
    if not file_name and caption:
        first_line = caption.split('\n')[0].strip()
        if first_line and len(first_line) < 100:
            file_name = first_line

    # ဖိုင်နာမည် လုံးဝ မရှိပါက Default ပေးခြင်း
    if not file_name:
        file_name = "Video.mp4"

    # Clean & Format ပြုလုပ်ခြင်း
    return clean_and_format_title(file_name, caption_text=caption)


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
        
        # ဖိုင်နာမည် ရယူခြင်း
        raw_file_name = extract_file_name(event.message)
        # URL Safe ဖြစ်စေရန် Quote ပြုလုပ်ခြင်း
        safe_file_name = quote(raw_file_name)
        
        # Cloud Domain ဖြင့် Link ထုတ်ပေးခြင်း (ဖိုင်နာမည် ပါဝင်သည်)
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

@app.get("/stream/{chat_id}/{message_id}/{file_name}")
async def stream_video(chat_id: int, message_id: int, file_name: str, request: Request):
    try:
        message = await bot.get_messages(chat_id, ids=message_id)
        file = message.video or message.document
        if not file:
            raise HTTPException(status_code=404, detail="Media not found")
        
        file_size = file.size
        mime_type = file.mime_type or "video/mp4"
        range_header = request.headers.get("range")
        
        # Display name အတွက် Decode လုပ်ခြင်း
        display_name = unquote(file_name)
        
        headers = {
            "Content-Type": mime_type,
            "Accept-Ranges": "bytes",
            "Cache-Control": "public, max-age=3600",
            # Player/Browser တွင် Video နာမည် မှန်မှန်ပေါ်စေရန် Content-Disposition ထည့်သွင်းခြင်း
            "Content-Disposition": f'inline; filename="{display_name}"'
        }
        
        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else file_size - 1
            
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
