import os
import math
import logging
import subprocess
import shlex
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Constants
BOT_TOKEN = '<Your-Telegram-Bot-Token>'
MB_IN_BYTES = 1024 * 1024
UPLOAD_SIZE_LIMIT_MB = 50
SPLIT_SIZE_LIMIT_MB = 40 #If one or more splitted file are bigger than UPLOAD_SIZE_LIMIT_MB, decrease this value
SUBDIR = "downloads"

# Setup logging
logging.basicConfig(filename="video_dl_bot.log", filemode='a', format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text('Hi! Send me a video URL to download.')

def refine_url_and_filename(url: str) -> tuple:
    refined_url = url.split('?')[0]
    filename_base = refined_url.rstrip('/').split('/')[-1]
    if url.startswith(("https://youtube.com/watch", "https://www.youtube.com/watch")):
        refined_url = url.split('&')[0]
        filename_base = refined_url.split('?')[-1].split('=')[-1]
    return refined_url, filename_base

async def download_video(update: Update, context: CallbackContext) -> None:
    refined_url, filename_base = refine_url_and_filename(update.message.text)
    await update.message.reply_text(f"Trying to download the video to a local file starting with {filename_base} from the refined URL: {refined_url}")

    file_path = f'{SUBDIR}/{filename_base}'
    command = f"yt-dlp -S vcodec:h264 --merge-output-format mp4 -o '{file_path}.%(ext)s' {shlex.quote(refined_url)}"

    try:
        result = subprocess.run(command, shell=True, check=True, text=True, capture_output=True)
        logger.info(f"Video downloaded successfully! Output:\n{result.stdout}")

        full_file_path = find_downloaded_file(filename_base)
        file_size = os.path.getsize(full_file_path)
        await update.message.reply_text(f"Video downloaded successfully to file {os.path.basename(full_file_path)} with size {file_size} bytes.")

        if file_size / MB_IN_BYTES > UPLOAD_SIZE_LIMIT_MB:
            await split_and_send_video(update, context, full_file_path, filename_base)
        else:
            await send_video(update, context, full_file_path)
    except Exception as e:
        handle_error(update, e)
    finally:
        os.remove(full_file_path)

def find_downloaded_file(filename_base: str) -> str:
    for file in os.listdir(SUBDIR):
        if file.startswith(filename_base):
            return f"{SUBDIR}/{file}"
    raise FileNotFoundError("The video file was not found.")

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

def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))
    application.run_polling()

if __name__ == '__main__':
    main()
