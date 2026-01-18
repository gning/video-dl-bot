import os
import math
import logging
import subprocess
import shlex
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Constants
BOT_TOKEN = os.getenv('BOT_TOKEN')
MB_IN_BYTES = 1024 * 1024
UPLOAD_SIZE_LIMIT_MB = int(os.getenv('UPLOAD_SIZE_LIMIT_MB', 50))
SPLIT_SIZE_LIMIT_MB = int(os.getenv('SPLIT_SIZE_LIMIT_MB', 40)) #If one or more splitted file are bigger than UPLOAD_SIZE_LIMIT_MB, decrease this value
SUBDIR = "downloads"
SETTINGS_FILE = "user_settings.json"

# Default user settings
DEFAULT_SETTINGS = {
    'download_audio': False,
    'audio_only': False,
    'compress_video': True,
    'split_large_files': True,
    'proxy_url': 'none',
    'cookies_browser': 'none',  # Browser to extract cookies from (chrome, firefox, edge, safari, etc.)
    'use_aria2': False,  # Use aria2c for faster downloads
    'force_ipv4': False,  # Force IPv4 connections
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
                
                # Update existing users with new settings
                for user_id in user_settings:
                    for key, default_value in DEFAULT_SETTINGS.items():
                        if key not in user_settings[user_id]:
                            user_settings[user_id][key] = default_value
                save_settings()
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
    await update.message.reply_text(
        'Hi! Send me a video URL to download.\n\n'
        'Commands:\n'
        '/settings - Configure download options\n'
        '/set_proxy URL - Set proxy server\n'
        '/set_cookies BROWSER - Use browser cookies for auth'
    )

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
            f"{'✅' if settings['audio_only'] else '❌'} Audio Only",
            callback_data='toggle_audio_only'
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
            f"{'✅' if settings['use_aria2'] else '❌'} Use aria2 (faster)",
            callback_data='toggle_aria2'
        )],
        [InlineKeyboardButton(
            f"{'✅' if settings['force_ipv4'] else '❌'} Force IPv4",
            callback_data='toggle_ipv4'
        )],
        [InlineKeyboardButton(
            f"🌐 Proxy: {settings['proxy_url']}",
            callback_data='show_proxy_info'
        )],
        [InlineKeyboardButton(
            f"🍪 Cookies: {settings['cookies_browser']}",
            callback_data='show_cookies_info'
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
        # If audio_only is enabled but download_audio is disabled, disable audio_only too
        if not settings['download_audio'] and settings['audio_only']:
            settings['audio_only'] = False
    elif query.data == 'toggle_audio_only':
        settings['audio_only'] = not settings['audio_only']
        # If audio_only is enabled, make sure download_audio is enabled too
        if settings['audio_only']:
            settings['download_audio'] = True
    elif query.data == 'toggle_compress':
        settings['compress_video'] = not settings['compress_video']
    elif query.data == 'toggle_split':
        settings['split_large_files'] = not settings['split_large_files']
    elif query.data == 'toggle_aria2':
        settings['use_aria2'] = not settings['use_aria2']
    elif query.data == 'toggle_ipv4':
        settings['force_ipv4'] = not settings['force_ipv4']
    elif query.data == 'show_proxy_info':
        await query.answer(
            f"Current proxy: {settings['proxy_url']}\n"
            "Use /set_proxy URL to change",
            show_alert=True
        )
        return
    elif query.data == 'show_cookies_info':
        await query.answer(
            f"Current cookies browser: {settings['cookies_browser']}\n"
            "Use /set_cookies BROWSER to change\n"
            "(chrome, firefox, edge, safari, opera, brave)",
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

    settings = get_user_settings(update.effective_user.id)
    proxy_url = context.args[0].lower()

    if proxy_url == 'none':
        settings['proxy_url'] = 'none'
        await update.message.reply_text("Proxy disabled.")
    else:
        settings['proxy_url'] = proxy_url
        await update.message.reply_text(f"Proxy set to: {proxy_url}")

    save_settings()

async def set_cookies_command(update: Update, context: CallbackContext) -> None:
    """Handle the cookies browser setting command"""
    valid_browsers = ['chrome', 'firefox', 'edge', 'safari', 'opera', 'brave', 'chromium', 'vivaldi', 'none']

    if not context.args:
        await update.message.reply_text(
            "Please provide a browser name or 'none' to disable cookies.\n"
            f"Valid browsers: {', '.join(valid_browsers[:-1])}\n"
            "Example: /set_cookies chrome\n"
            "Or: /set_cookies none"
        )
        return

    settings = get_user_settings(update.effective_user.id)
    browser = context.args[0].lower()

    if browser not in valid_browsers:
        await update.message.reply_text(f"Invalid browser. Valid options: {', '.join(valid_browsers)}")
        return

    if browser == 'none':
        settings['cookies_browser'] = 'none'
        await update.message.reply_text("Cookies disabled.")
    else:
        settings['cookies_browser'] = browser
        await update.message.reply_text(f"Cookies browser set to: {browser}")

    save_settings()

async def refine_url_and_filename(url: str) -> tuple:
    refined_url = url.split('?')[0]
    filename_base = refined_url.rstrip('/').split('/')[-1]
    if url.startswith(("https://youtube.com/watch", "https://www.youtube.com/watch")):
        refined_url = url.split('&')[0]
        filename_base = refined_url.split('?')[-1].split('=')[-1]
    return refined_url, filename_base

def build_ytdlp_base_options(settings: dict) -> list:
    """Build base yt-dlp options for improved success rate."""
    options = [
        # Retry mechanisms for network resilience
        '--retries', '10',
        '--fragment-retries', '10',
        '--retry-sleep', '3',

        # Geo-bypass options
        '--geo-bypass',

        # Rate-limiting protection
        '--sleep-requests', '1',
        '--sleep-interval', '1',
        '--max-sleep-interval', '5',

        # Concurrent fragments for faster downloads
        '--concurrent-fragments', '4',

        # Safety options
        '--no-playlist',
        '--no-overwrites',

        # Better compatibility
        '--no-check-certificates',
        '--prefer-free-formats',

        # Verbose progress for debugging
        '--newline',
    ]

    # Add proxy if configured
    if settings.get('proxy_url', 'none') != 'none':
        options.extend(['--proxy', settings['proxy_url']])

    # Add cookies from browser if configured
    if settings.get('cookies_browser', 'none') != 'none':
        options.extend(['--cookies-from-browser', settings['cookies_browser']])

    # Force IPv4 if enabled
    if settings.get('force_ipv4', False):
        options.extend(['--force-ipv4'])

    # Use aria2c for faster multi-connection downloads
    if settings.get('use_aria2', False):
        options.extend([
            '--downloader', 'aria2c',
            '--downloader-args', 'aria2c:-c -j 8 -x 8 -s 8 -k 1M'
        ])

    return options

def build_video_command(url: str, output_path: str, settings: dict) -> list:
    """Build yt-dlp command for video download with improved success rate."""
    cmd = ['yt-dlp']
    cmd.extend(build_ytdlp_base_options(settings))

    # Improved format selection with fallbacks
    # Priority: h264 video + best audio, then any video + audio, then best available
    format_selection = (
        'bestvideo[vcodec^=avc1][height<=1080]+bestaudio[acodec^=mp4a]/bestvideo[vcodec^=avc1]+bestaudio/'
        'bestvideo[height<=1080]+bestaudio/bestvideo+bestaudio/best[height<=1080]/best'
    )
    cmd.extend(['-f', format_selection])

    # Output format and path
    cmd.extend([
        '--merge-output-format', 'mp4',
        '-o', f'{output_path}.%(ext)s'
    ])

    cmd.append(url)
    return cmd

def build_audio_command(url: str, output_path: str, settings: dict) -> list:
    """Build yt-dlp command for audio-only download with improved success rate."""
    cmd = ['yt-dlp']
    cmd.extend(build_ytdlp_base_options(settings))

    # Audio extraction options
    cmd.extend([
        '-x',
        '--audio-format', 'mp3',
        '--audio-quality', '0',  # Best quality
        '-o', f'{output_path}.%(ext)s'
    ])

    cmd.append(url)
    return cmd

def run_ytdlp_command(cmd: list) -> tuple:
    """Run yt-dlp command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            check=False,  # Don't raise exception, we'll check returncode
            text=True,
            capture_output=True,
            timeout=600  # 10 minute timeout
        )

        # yt-dlp returns 0 on success, non-zero on failure
        success = result.returncode == 0

        return success, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, '', 'Download timed out after 10 minutes'
    except Exception as e:
        return False, '', str(e)

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
    settings = get_user_settings(update.effective_user.id)
    refined_url, filename_base = await refine_url_and_filename(update.message.text)

    # Create downloads directory if it doesn't exist
    if not os.path.exists(SUBDIR):
        os.makedirs(SUBDIR)

    # If audio_only is enabled, only download audio
    if settings['audio_only']:
        await update.message.reply_text(f"Downloading audio only from: {refined_url}")
        await download_audio_only(update, context, refined_url, filename_base, settings)
        return

    await update.message.reply_text(f"Downloading video from: {refined_url}")

    # Build and run the improved yt-dlp command
    video_path = f'{SUBDIR}/{filename_base}'
    cmd = build_video_command(refined_url, video_path, settings)
    logger.info(f"Running yt-dlp command: {' '.join(cmd)}")

    success, stdout, stderr = run_ytdlp_command(cmd)

    if not success:
        # Extract meaningful error message from stderr
        error_lines = [line for line in stderr.split('\n') if 'ERROR' in line or 'error' in line.lower()]
        error_msg = '\n'.join(error_lines[-3:]) if error_lines else stderr[-500:] if stderr else 'Unknown error'
        logger.error(f"Download failed: {stderr}")
        await update.message.reply_text(f"Download failed:\n{error_msg}")
        return

    logger.info(f"Video downloaded successfully! Output:\n{stdout}")

    try:
        video_file_path = find_downloaded_file(filename_base)
    except FileNotFoundError as e:
        logger.error(f"Could not find downloaded file: {e}")
        await update.message.reply_text(f"Download completed but file not found. Check logs for details.")
        return

    video_size = os.path.getsize(video_file_path)
    await update.message.reply_text(f"Video downloaded: {os.path.basename(video_file_path)} ({video_size / MB_IN_BYTES:.2f} MB)")

    # Handle video sending
    try:
        if video_size / MB_IN_BYTES > UPLOAD_SIZE_LIMIT_MB:
            if settings['compress_video']:
                await update.message.reply_text("Video is too large. Compressing...")
                compressed_path = await compress_video(video_file_path)
                compressed_size = os.path.getsize(compressed_path)
                if compressed_size / MB_IN_BYTES <= UPLOAD_SIZE_LIMIT_MB:
                    await send_video(update, context, compressed_path)
                    os.remove(compressed_path)
                else:
                    os.remove(compressed_path)
                    if settings['split_large_files']:
                        await split_and_send_video(update, context, video_file_path, filename_base)
                    else:
                        await update.message.reply_text("Video is too large to send, even after compression. Attempting to send directly...")
                        await send_video(update, context, video_file_path)
            elif settings['split_large_files']:
                await split_and_send_video(update, context, video_file_path, filename_base)
            else:
                await send_video(update, context, video_file_path)
        else:
            await send_video(update, context, video_file_path)
    except Exception as e:
        logger.error(f"Error during video processing/sending: {e}")
        await update.message.reply_text(f"Error during video processing/sending: {e}")

    # Download audio if enabled and no fatal error occurred
    if settings['download_audio'] and not settings['audio_only']:
        await download_audio_only(update, context, refined_url, filename_base + "_audio", settings)

    # Clean up video file
    if os.path.exists(video_file_path):
        os.remove(video_file_path)

async def download_audio_only(update: Update, context: CallbackContext, url: str, filename_base: str, settings: dict) -> None:
    """Download audio only version of the content"""
    audio_path = f'{SUBDIR}/{filename_base}'
    cmd = build_audio_command(url, audio_path, settings)
    logger.info(f"Running yt-dlp audio command: {' '.join(cmd)}")

    success, stdout, stderr = run_ytdlp_command(cmd)

    if not success:
        error_lines = [line for line in stderr.split('\n') if 'ERROR' in line or 'error' in line.lower()]
        error_msg = '\n'.join(error_lines[-3:]) if error_lines else stderr[-500:] if stderr else 'Unknown error'
        logger.error(f"Audio download failed: {stderr}")
        await update.message.reply_text(f"Audio download failed:\n{error_msg}")
        return

    try:
        audio_file_path = find_downloaded_file(filename_base)
    except FileNotFoundError as e:
        logger.error(f"Could not find downloaded audio file: {e}")
        await update.message.reply_text(f"Audio download completed but file not found.")
        return

    try:
        with open(audio_file_path, 'rb') as audio_file:
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=audio_file,
                caption=f"Audio from {url}"
            )
    except Exception as e:
        logger.error(f"Failed to send audio: {e}")
        await update.message.reply_text(f"Failed to send audio: {e}")
    finally:
        if os.path.exists(audio_file_path):
            os.remove(audio_file_path)

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

    success_count = 0
    failed_parts = []
    
    for i in range(0, num_parts):
        split_file = f"{SUBDIR}/{filename_base}_{i:03d}{file_extension}"
        j = i + 1
        await update.message.reply_text(f"Sending part {j} of {num_parts}...")
        try:
            await send_video(update, context, split_file)
            success_count += 1
        except Exception as e:
            error_message = str(e)
            await update.message.reply_text(f"Failed to send part {j}: {error_message}")
            logger.error(f"Failed to send part {j}: {error_message}")
            failed_parts.append(j)
        finally:
            if os.path.exists(split_file):
                os.remove(split_file)

    logger.info("All split parts processed.")
    
    if success_count == num_parts:
        await update.message.reply_text("All video parts sent successfully.")
    else:
        await update.message.reply_text(f"Sent {success_count} of {num_parts} parts. Failed parts: {', '.join(map(str, failed_parts))}")

async def send_video(update: Update, context: CallbackContext, file_path: str) -> None:
    try:
        with open(file_path, 'rb') as video_file:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=video_file,
                filename=os.path.basename(file_path),
                write_timeout=60.0,
                read_timeout=60.0,
                connect_timeout=30.0
            )
    except Exception as e:
        error_message = str(e)
        logger.error(f"Failed to send video file {file_path}: {error_message}")
        raise  # Re-raise to be handled by the caller

def find_downloaded_file(filename_base: str) -> str:
    for file in os.listdir(SUBDIR):
        if file.startswith(filename_base):
            return f"{SUBDIR}/{file}"
    raise FileNotFoundError(f"The file with base name {filename_base} was not found in {SUBDIR}.")

def main() -> None:
    # Check if downloads directory exists
    if not os.path.exists(SUBDIR):
        os.makedirs(SUBDIR)
        
    load_settings()
    application = Application.builder()\
        .token(BOT_TOKEN)\
        .base_url("http://127.0.0.1:8081/bot")\
        .build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("set_proxy", set_proxy_command))
    application.add_handler(CommandHandler("set_cookies", set_cookies_command))
    application.add_handler(CallbackQueryHandler(settings_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_video))

    application.run_polling()

if __name__ == '__main__':
    main()
