import os
import math
import logging
import subprocess
import shlex
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import json

# Constants
BOT_TOKEN = '<Your-Telegram-Bot-Token>'
MB_IN_BYTES = 1024 * 1024
UPLOAD_SIZE_LIMIT_MB = 50
SPLIT_SIZE_LIMIT_MB = 40 #If one or more splitted file are bigger than UPLOAD_SIZE_LIMIT_MB, decrease this value
SUBDIR = "downloads"
SETTINGS_FILE = "user_settings.json"

# Default user settings
DEFAULT_SETTINGS = {
    'download_audio': False,
    'compress_video': True,
    'split_large_files': True,
    'proxy_url': 'none'
}

# User settings dictionary
user_settings = {}

def load_settings():
    """Load user settings from file"""
    global user_settings
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r') as f:
                user_settings = json.load(f)
    except Exception as e:
        logger.error(f"Error loading settings: {e}")

def save_settings():
    """Save user settings to file"""
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(user_settings, f)
    except Exception as e:
        logger.error(f"Error saving settings: {e}")

def get_user_settings(user_id: int) -> dict:
    """Get settings for a specific user"""
    if str(user_id) not in user_settings:
        user_settings[str(user_id)] = DEFAULT_SETTINGS.copy()
        save_settings()
    return user_settings[str(user_id)]

# Setup logging
logging.basicConfig(filename="video_dl_bot.log", filemode='a', format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text('Hi! Send me a video URL to download.\nUse /settings to configure bot preferences.')

async def settings_command(update: Update, context: CallbackContext) -> None:
    """Handle the /settings command"""
    keyboard = await get_settings_keyboard(update.effective_user.id)
    await update.message.reply_text(
        "Configure your download settings:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def get_settings_keyboard(user_id: int) -> list:
    """Generate settings keyboard based on user settings"""
    settings = get_user_settings(user_id)
    keyboard = [
        [InlineKeyboardButton(
            f"{'✅' if settings['download_audio'] else '❌'} Download Audio",
            callback_data='toggle_audio'
        )],
        [InlineKeyboardButton(
            f"{'✅' if settings['compress_video'] else '❌'} Compress Video",
            callback_data='toggle_compress'
        )],
        [InlineKeyboardButton(
            f"{'✅' if settings['split_large_files'] else '❌'} Split Large Files",
            callback_data='toggle_split'
        )],
        [InlineKeyboardButton(
            f"🌐 Proxy: {settings['proxy_url']}",
            callback_data='show_proxy_info'
        )]
    ]
    return keyboard

async def settings_button(update: Update, context: CallbackContext) -> None:
    """Handle settings button presses"""
    query = update.callback_query
    user_id = str(update.effective_user.id)
    settings = get_user_settings(user_id)
    
    if query.data == 'toggle_audio':
        settings['download_audio'] = not settings['download_audio']
    elif query.data == 'toggle_compress':
        settings['compress_video'] = not settings['compress_video']
    elif query.data == 'toggle_split':
        settings['split_large_files'] = not settings['split_large_files']
    elif query.data == 'show_proxy_info':
        await query.answer(
            f"Current proxy: {settings['proxy_url']}\n"
            "Use /set_proxy URL to change",
            show_alert=True
        )
        return
    
    save_settings()
    
    # Update the keyboard
    keyboard = await get_settings_keyboard(update.effective_user.id)
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    await query.answer()

async def set_proxy_command(update: Update, context: CallbackContext) -> None:
    """Handle the proxy URL setting command"""
    if not context.args:
        await update.message.reply_text(
            "Please provide a proxy URL or 'none' to disable proxy.\n"
            "Example: /set_proxy http://proxy.example.com:8080\n"
            "Or: /set_proxy none"
        )
        return

    user_settings = get_user_settings(update.effective_user.id)
    proxy_url = context.args[0].lower()
    
    if proxy_url == 'none':
        user_settings['proxy_url'] = 'none'
        await update.message.reply_text("Proxy disabled.")
    else:
        user_settings['proxy_url'] = proxy_url
        await update.message.reply_text(f"Proxy set to: {proxy_url}")
    
    save_settings()

async def refine_url_and_filename(url: str) -> tuple:
    refined_url = url.split('?')[0]
    filename_base = refined_url.rstrip('/').split('/')[-1]
    if url.startswith(("https://youtube.com/watch", "https://www.youtube.com/watch")):
        refined_url = url.split('&')[0]
        filename_base = refined_url.split('?')[-1].split('=')[-1]
    return refined_url, filename_base

async def compress_video(file_path: str) -> str:
    """Compress video using ffmpeg and return the path to compressed file."""
    compressed_path = f"{file_path}_compressed.mp4"
    command = f"ffmpeg -i {shlex.quote(file_path)} -c:v libx264 -tag:v avc1 -movflags faststart -crf 30 -preset superfast {shlex.quote(compressed_path)}"
    
    try:
        subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        logger.info(f"Video compressed successfully to {compressed_path}")
        return compressed_path
    except Exception as e:
        logger.error(f"Failed to compress video: {str(e)}")
        raise

async def download_video(update: Update, context: CallbackContext) -> None:
    user_settings = get_user_settings(update.effective_user.id)
    refined_url, filename_base = await refine_url_and_filename(update.message.text)
    await update.message.reply_text(f"Trying to download the video from: {refined_url}")

    # Always download video
    video_path = f'{SUBDIR}/{filename_base}'
    video_command = f"yt-dlp -S vcodec:h264 --merge-output-format mp4 -o '{video_path}.%(ext)s' {shlex.quote(refined_url)}"

    if user_settings['proxy_url'] != 'none':
        video_command += f" --proxy {shlex.quote(user_settings['proxy_url'])}"

    try:
        # Download video
        result = subprocess.run(video_command, shell=True, check=True, text=True, capture_output=True)
        if result.stderr:
            error_msg = f"Download failed with error:\n{result.stderr}"
            logger.error(error_msg)
            await update.message.reply_text(error_msg)
            return

        logger.info(f"Video downloaded successfully! Output:\n{result.stdout}")
        video_file_path = find_downloaded_file(filename_base)
        video_size = os.path.getsize(video_file_path)
        await update.message.reply_text(f"Video downloaded successfully to {os.path.basename(video_file_path)} with size {video_size} bytes.")

        # Handle video sending
        if video_size / MB_IN_BYTES > UPLOAD_SIZE_LIMIT_MB:
            if user_settings['compress_video']:
                await update.message.reply_text("Video is too large. Compressing...")
                compressed_path = await compress_video(video_file_path)
                compressed_size = os.path.getsize(compressed_path)
                if compressed_size / MB_IN_BYTES <= UPLOAD_SIZE_LIMIT_MB:
                    await send_video(update, context, compressed_path)
                    os.remove(compressed_path)
                else:
                    os.remove(compressed_path)
                    if user_settings['split_large_files']:
                        await split_and_send_video(update, context, video_file_path, filename_base)
                    else:
                        await update.message.reply_text("Video is too large to send, even after compression. Enable 'Split Large Files' in settings to split it into smaller parts.")
            elif user_settings['split_large_files']:
                await split_and_send_video(update, context, video_file_path, filename_base)
            else:
                await send_video(update, context, video_file_path)
        else:
            await send_video(update, context, video_file_path)

        # Download audio if enabled
        if user_settings['download_audio']:
            await update.message.reply_text("Downloading audio version...")
            audio_path = f'{SUBDIR}/{filename_base}_audio'
            audio_command = f"yt-dlp -x --audio-format mp3 -o '{audio_path}.%(ext)s' {shlex.quote(refined_url)}"
            
            if user_settings['proxy_url'] != 'none':
                audio_command += f" --proxy {shlex.quote(user_settings['proxy_url'])}"

            audio_result = subprocess.run(audio_command, shell=True, check=True, text=True, capture_output=True)
            if audio_result.stderr:
                await update.message.reply_text(f"Audio download failed with error:\n{audio_result.stderr}")
            else:
                audio_file_path = find_downloaded_file(f"{filename_base}_audio")
                await context.bot.send_audio(
                    chat_id=update.effective_chat.id,
                    audio=open(audio_file_path, 'rb'),
                    caption=f"Audio version from {refined_url}"
                )
                os.remove(audio_file_path)

    except Exception as e:
        handle_error(update, e)
    finally:
        if 'video_file_path' in locals() and os.path.exists(video_file_path):
            os.remove(video_file_path)

async def split_and_send_video(update: Update, context: CallbackContext, full_file_path: str, filename_base: str) -> None:
    await update.message.reply_text("The video is larger than 50MB. Splitting it into smaller chunks...")

    file_size = os.path.getsize(full_file_path)
    num_parts = math.ceil(file_size / (SPLIT_SIZE_LIMIT_MB * MB_IN_BYTES))
    duration = float(subprocess.check_output(f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 {shlex.quote(full_file_path)}", shell=True).decode().strip())
    segment_duration = duration / num_parts

    # Get the original file extension
    _, file_extension = os.path.splitext(full_file_path)

    split_command = f"ffmpeg -i {shlex.quote(full_file_path)} -c copy -map 0 -segment_time {segment_duration} -f segment -reset_timestamps 1 {SUBDIR}/{filename_base}_%03d{file_extension}"
    subprocess.run(split_command, shell=True, check=True)

    for i in range(0, num_parts):  # Start from 1 instead of 0
        split_file = f"{SUBDIR}/{filename_base}_{i:03d}{file_extension}"
        j = i + 1
        await update.message.reply_text(f"Sending part {j} of {num_parts}...")
        try:
            await send_video(update, context, split_file)
        except Exception as e:
            error_message = str(e)
            await update.message.reply_text(f"Failed to send part {j}: {error_message}")
            logger.error(f"Failed to send part {j}: {error_message}")
        finally:
            os.remove(split_file)

    logger.info("All split parts processed.")
    await update.message.reply_text("Video split and processed in parts.")

async def send_video(update: Update, context: CallbackContext, file_path: str) -> None:
    #await update.message.reply_text(f"Sending the video {file_path} now...")
    with open(file_path, 'rb') as video_file:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=video_file,
            filename=os.path.basename(file_path),
            write_timeout=60.0,
            read_timeout=60.0,
            connect_timeout=30.0
        )

def handle_error(update: Update, error: Exception) -> None:
    error_message = str(error)
    logger.error(f"An error occurred: {error_message}")
    update.message.reply_text(f"Failed to process or send the video: {error_message}")

def find_downloaded_file(filename_base: str) -> str:
    for file in os.listdir(SUBDIR):
        if file.startswith(filename_base):
            return f"{SUBDIR}/{file}"
    raise FileNotFoundError("The video file was not found.")

def main() -> None:
    load_settings()
    application = Application.builder()\
        .token(BOT_TOKEN)\
        .base_url("http://127.0.0.1:8081/bot")\
        .build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("set_proxy", set_proxy_command))
    application.add_handler(CallbackQueryHandler(settings_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))

    application.run_polling()

if __name__ == '__main__':
    main()
