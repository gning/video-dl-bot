import os
import math
import logging
import subprocess
import shlex
import asyncio
from urllib.parse import urlparse, parse_qs
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TimedOut
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
TELEGRAM_WRITE_TIMEOUT = 300.0
TELEGRAM_READ_TIMEOUT = 300.0
TELEGRAM_CONNECT_TIMEOUT = 30.0
PLAYLIST_RANGE_REPLY_TIMEOUT = 30

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
    'preferred_audio_lang': 'none',  # Preferred audio: 'original', 'zh' for Chinese, 'en' for English, 'none' for default
    'download_timeout_minutes': 60,  # Maximum time to download one item
}

# User settings dictionary
user_settings = {}
pending_playlist_ranges = {}

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
                    user_settings[user_id].pop('playlist_timeout_minutes', None)
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
        '/set-proxy URL - Set proxy server\n'
        '/set-cookies BROWSER - Use browser cookies for auth\n'
        '/set-download-timeout MINUTES - Set single download timeout'
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
        )],
        [InlineKeyboardButton(
            f"🔊 Audio Language: {get_audio_lang_display(settings.get('preferred_audio_lang', 'none'))}",
            callback_data='cycle_audio_lang'
        )],
        [InlineKeyboardButton(
            f"⏳ Download Timeout: {settings.get('download_timeout_minutes', 60)} min",
            callback_data='show_download_timeout_info'
        )]
    ]
    return keyboard

def get_audio_lang_display(lang: str) -> str:
    """Get display name for audio language setting."""
    lang_names = {
        'none': 'Default',
        'original': 'Original',
        'zh': 'Chinese',
        'en': 'English'
    }
    return lang_names.get(lang, 'Default')

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
            "Use /set-proxy URL to change",
            show_alert=True
        )
        return
    elif query.data == 'show_cookies_info':
        await query.answer(
            f"Current cookies browser: {settings['cookies_browser']}\n"
            "Use /set-cookies BROWSER to change\n"
            "(chrome, firefox, edge, safari, opera, brave)",
            show_alert=True
        )
        return
    elif query.data == 'cycle_audio_lang':
        # Cycle through: none -> original -> zh -> en -> none
        current_lang = settings.get('preferred_audio_lang', 'none')
        lang_cycle = ['none', 'original', 'zh', 'en']
        current_index = lang_cycle.index(current_lang) if current_lang in lang_cycle else 0
        next_index = (current_index + 1) % len(lang_cycle)
        settings['preferred_audio_lang'] = lang_cycle[next_index]
    elif query.data == 'show_download_timeout_info':
        await query.answer(
            f"Current single download timeout: {settings.get('download_timeout_minutes', 60)} minutes\n"
            "Use /set-download-timeout MINUTES to change",
            show_alert=True
        )
        return

    save_settings()

    # Update the keyboard
    keyboard = await get_settings_keyboard(update.effective_user.id)
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
    await query.answer()

def get_message_args(update: Update, context: CallbackContext) -> list:
    """Return command arguments for both standard and dashed command handlers."""
    if getattr(context, 'args', None):
        return context.args
    if update.message and update.message.text:
        return update.message.text.split()[1:]
    return []

def get_chat_user_key(update: Update) -> tuple:
    """Return a key for pending per-chat, per-user prompts."""
    return update.effective_chat.id, update.effective_user.id

def parse_playlist_index(text: str, minimum: int, maximum: int) -> int:
    """Parse and clamp a playlist index."""
    index = int(text.strip())
    return max(minimum, min(index, maximum))

async def start_playlist_range_prompt(
    update: Update,
    context: CallbackContext,
    *,
    url: str,
    filename_base: str,
    settings: dict,
    entries: list,
    mode: str
) -> None:
    """Ask the user for playlist start/end range before processing."""
    key = get_chat_user_key(update)
    existing_request = pending_playlist_ranges.pop(key, None)
    if existing_request and existing_request.get('timeout_task'):
        existing_request['timeout_task'].cancel()

    item_name = 'video' if mode == 'video' else 'audio'
    request = {
        'stage': 'start',
        'url': url,
        'filename_base': filename_base,
        'settings': settings.copy(),
        'entries': entries,
        'mode': mode,
        'update': update,
        'item_name': item_name,
        'timeout_task': None,
    }
    pending_playlist_ranges[key] = request

    await update.message.reply_text(
        f"Found {len(entries)} {item_name}s. Reply with the start index number within "
        f"{PLAYLIST_RANGE_REPLY_TIMEOUT} seconds. Default: 1."
    )
    schedule_playlist_range_timeout(context, key, 'start')

def schedule_playlist_range_timeout(context: CallbackContext, key: tuple, stage: str) -> None:
    """Schedule a default answer for a playlist range prompt."""
    request = pending_playlist_ranges.get(key)
    if not request:
        return

    timeout_task = request.get('timeout_task')
    if timeout_task and not timeout_task.done() and timeout_task is not asyncio.current_task():
        timeout_task.cancel()

    request['timeout_task'] = context.application.create_task(
        playlist_range_timeout(context, key, stage)
    )

async def playlist_range_timeout(context: CallbackContext, key: tuple, stage: str) -> None:
    """Apply default playlist range values when the user does not reply."""
    await asyncio.sleep(PLAYLIST_RANGE_REPLY_TIMEOUT)

    request = pending_playlist_ranges.get(key)
    if not request or request.get('stage') != stage:
        return

    if stage == 'start':
        request['start_index'] = 1
        request['stage'] = 'end'
        await context.bot.send_message(
            chat_id=key[0],
            text=(
                "No start index received. Using 1.\n"
                f"Reply with the end index number within {PLAYLIST_RANGE_REPLY_TIMEOUT} seconds. "
                f"Default: {len(request['entries'])}."
            )
        )
        schedule_playlist_range_timeout(context, key, 'end')
        return

    pending_playlist_ranges.pop(key, None)
    end_index = len(request['entries'])
    await context.bot.send_message(
        chat_id=key[0],
        text=f"No end index received. Using {end_index}."
    )
    await process_playlist_range(context, request, request['start_index'], end_index)

async def handle_playlist_range_reply(update: Update, context: CallbackContext) -> bool:
    """Consume text replies for pending playlist range prompts."""
    key = get_chat_user_key(update)
    request = pending_playlist_ranges.get(key)
    if not request:
        return False

    text = update.message.text.strip()
    item_name = request['item_name']
    max_index = len(request['entries'])

    try:
        if request['stage'] == 'start':
            start_index = parse_playlist_index(text, 1, max_index)
            request['start_index'] = start_index
            request['stage'] = 'end'

            if request.get('timeout_task'):
                request['timeout_task'].cancel()

            await update.message.reply_text(
                f"Start index set to {start_index}. Reply with the end index number within "
                f"{PLAYLIST_RANGE_REPLY_TIMEOUT} seconds. Default: {max_index}."
            )
            schedule_playlist_range_timeout(context, key, 'end')
            return True

        start_index = request['start_index']
        end_index = parse_playlist_index(text, start_index, max_index)

        if request.get('timeout_task'):
            request['timeout_task'].cancel()
        pending_playlist_ranges.pop(key, None)

        await update.message.reply_text(
            f"Downloading playlist {item_name}s from index {start_index} to {end_index}."
        )
        await process_playlist_range(context, request, start_index, end_index)
        return True
    except ValueError:
        await update.message.reply_text(
            f"Please reply with a whole number between 1 and {max_index}."
        )
        return True

async def handle_text_message(update: Update, context: CallbackContext) -> None:
    """Route text messages to pending prompts or the URL downloader."""
    if await handle_playlist_range_reply(update, context):
        return
    await download_video(update, context)

async def set_proxy_command(update: Update, context: CallbackContext) -> None:
    """Handle the proxy URL setting command"""
    args = get_message_args(update, context)
    if not args:
        await update.message.reply_text(
            "Please provide a proxy URL or 'none' to disable proxy.\n"
            "Example: /set-proxy http://proxy.example.com:8080\n"
            "Or: /set-proxy none"
        )
        return

    settings = get_user_settings(update.effective_user.id)
    proxy_url = args[0].lower()

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
    args = get_message_args(update, context)

    if not args:
        await update.message.reply_text(
            "Please provide a browser name or 'none' to disable cookies.\n"
            f"Valid browsers: {', '.join(valid_browsers[:-1])}\n"
            "Example: /set-cookies chrome\n"
            "Or: /set-cookies none"
        )
        return

    settings = get_user_settings(update.effective_user.id)
    browser = args[0].lower()

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

async def set_download_timeout_command(update: Update, context: CallbackContext) -> None:
    """Handle the single download timeout setting command."""
    args = get_message_args(update, context)
    if not args:
        await update.message.reply_text(
            "Please provide the single download timeout in minutes.\n"
            "Example: /set-download-timeout 60"
        )
        return

    try:
        timeout_minutes = int(args[0])
    except ValueError:
        await update.message.reply_text("Download timeout must be a whole number of minutes.")
        return

    if timeout_minutes < 1 or timeout_minutes > 1440:
        await update.message.reply_text("Download timeout must be between 1 and 1440 minutes.")
        return

    settings = get_user_settings(update.effective_user.id)
    settings['download_timeout_minutes'] = timeout_minutes
    save_settings()
    await update.message.reply_text(f"Single download timeout set to {timeout_minutes} minutes.")

def is_twitter_url(url: str) -> bool:
    """Check if URL is a Twitter/X post that may contain multiple videos."""
    twitter_patterns = (
        'twitter.com/', 'x.com/',
        'mobile.twitter.com/', 'mobile.x.com/',
        'www.twitter.com/', 'www.x.com/'
    )
    return any(pattern in url.lower() for pattern in twitter_patterns)

def is_youtube_playlist_url(url: str) -> bool:
    """Check if URL is a YouTube playlist (not a single video with a list param)."""
    return 'youtube.com/playlist' in url.lower() and 'list=' in url.lower()

async def refine_url_and_filename(url: str) -> tuple:
    refined_url = url.split('?')[0]
    filename_base = refined_url.rstrip('/').split('/')[-1]
    if url.startswith(("https://youtube.com/watch", "https://www.youtube.com/watch")):
        refined_url = url.split('&')[0]
        filename_base = refined_url.split('?')[-1].split('=')[-1]
    elif is_youtube_playlist_url(url):
        playlist_id = url.split('list=')[1].split('&')[0]
        refined_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        filename_base = playlist_id
    return refined_url, filename_base

def extract_error_message(stderr: str) -> str:
    """Extract a short user-facing error from yt-dlp stderr."""
    error_lines = [line for line in stderr.split('\n') if 'ERROR' in line or 'error' in line.lower()]
    return '\n'.join(error_lines[-3:]) if error_lines else stderr[-500:] if stderr else 'Unknown error'

def get_youtube_playlist_id(url: str) -> str:
    """Return the YouTube playlist id from a playlist URL."""
    return parse_qs(urlparse(url).query).get('list', ['playlist'])[0]

def get_youtube_video_id(entry: dict) -> str:
    """Return a YouTube video id from a yt-dlp flat playlist entry."""
    if entry.get('id'):
        return entry['id']

    for key in ('webpage_url', 'url'):
        value = entry.get(key)
        if not value:
            continue
        parsed_url = urlparse(str(value))
        video_id = parse_qs(parsed_url.query).get('v', [''])[0]
        if video_id:
            return video_id

    return ''

def build_playlist_entry_url(entry: dict, playlist_id: str = '', index: int = None) -> str:
    """Build a downloadable URL from a yt-dlp flat playlist entry."""
    video_id = get_youtube_video_id(entry)
    if playlist_id and video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}&list={playlist_id}"
        if index is not None:
            video_url += f"&index={index}"
        return video_url

    if entry.get('webpage_url'):
        return entry['webpage_url']
    if entry.get('url') and str(entry['url']).startswith(('http://', 'https://')):
        return entry['url']
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    if entry.get('url'):
        return f"https://www.youtube.com/watch?v={entry['url']}"
    return ''

def get_youtube_playlist_entries(url: str, settings: dict, timeout_seconds: int) -> tuple:
    """Fetch a flat list of YouTube playlist entries."""
    cmd = ['yt-dlp']
    cmd.extend(build_ytdlp_base_options(settings, url))
    cmd.extend(['--flat-playlist', '--dump-single-json', url])

    success, stdout, stderr = run_ytdlp_command(cmd, timeout_seconds=timeout_seconds)
    if not success:
        return False, [], stderr

    try:
        playlist_data = json.loads(stdout)
    except json.JSONDecodeError as e:
        return False, [], f"Could not parse playlist metadata: {e}"

    entries = [
        entry for entry in playlist_data.get('entries', [])
        if entry and build_playlist_entry_url(entry)
    ]
    return True, entries, ''

def build_ytdlp_base_options(settings: dict, url: str = '') -> list:
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
        '--no-overwrites',

        # Better compatibility
        '--no-check-certificates',
        '--prefer-free-formats',

        # Verbose progress for debugging
        '--newline',
    ]

    # For Twitter/X and YouTube playlists, allow downloading all videos
    # For other platforms, use --no-playlist to avoid accidentally downloading entire playlists
    if not is_twitter_url(url) and not is_youtube_playlist_url(url):
        options.append('--no-playlist')

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
    cmd.extend(build_ytdlp_base_options(settings, url))

    # Check preferred audio language
    preferred_lang = settings.get('preferred_audio_lang', 'none')

    # Build format selection based on language preference
    # Base format without language preference (uses default audio)
    base_format = (
        'bv*[vcodec^=avc1][height<=1080]+ba/bv*[vcodec^=avc1]+ba/'
        'bv*[height<=1080]+ba/bv*+ba/best[height<=1080]/best'
    )

    if preferred_lang == 'original':
        # Select original audio track using format_note filter
        format_selection = (
            'bv*[vcodec^=avc1][height<=1080]+ba[format_note*=original]/'
            'bv*[height<=1080]+ba[format_note*=original]/'
            'bv*+ba[format_note*=original]/'
            + base_format
        )
    elif preferred_lang == 'zh':
        # Select Chinese audio - match zh in format_note (e.g., [zh-Hant], [zh-CN])
        format_selection = (
            'bv*[vcodec^=avc1][height<=1080]+ba[format_note*=zh]/'
            'bv*[height<=1080]+ba[format_note*=zh]/'
            'bv*+ba[format_note*=zh]/'
            + base_format
        )
    elif preferred_lang == 'en':
        # Select English audio - match en in format_note (e.g., [en-US], [en-GB])
        format_selection = (
            'bv*[vcodec^=avc1][height<=1080]+ba[format_note*=en]/'
            'bv*[height<=1080]+ba[format_note*=en]/'
            'bv*+ba[format_note*=en]/'
            + base_format
        )
    else:
        # Default: no preference, let yt-dlp choose
        format_selection = base_format

    cmd.extend(['-f', format_selection])

    # Output format and path
    # For Twitter/X and YouTube playlists, use playlist index to handle multiple videos
    if is_twitter_url(url) or is_youtube_playlist_url(url):
        cmd.extend([
            '--merge-output-format', 'mp4',
            '-o', f'{output_path}_%(playlist_index|0)s.%(ext)s'
        ])
    else:
        cmd.extend([
            '--merge-output-format', 'mp4',
            '-o', f'{output_path}.%(ext)s'
        ])

    cmd.append(url)
    return cmd

def build_audio_command(url: str, output_path: str, settings: dict) -> list:
    """Build yt-dlp command for audio-only download with improved success rate."""
    cmd = ['yt-dlp']
    cmd.extend(build_ytdlp_base_options(settings, url))

    # Audio extraction options
    # For Twitter/X and YouTube playlists, use playlist index to handle multiple audio files
    if is_twitter_url(url) or is_youtube_playlist_url(url):
        cmd.extend([
            '-x',
            '--audio-format', 'mp3',
            '--audio-quality', '0',  # Best quality
            '-o', f'{output_path}_%(playlist_index|0)s.%(ext)s'
        ])
    else:
        cmd.extend([
            '-x',
            '--audio-format', 'mp3',
            '--audio-quality', '0',  # Best quality
            '-o', f'{output_path}.%(ext)s'
        ])

    cmd.append(url)
    return cmd

def run_ytdlp_command(cmd: list, timeout_seconds: int = 600) -> tuple:
    """Run yt-dlp command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            check=False,  # Don't raise exception, we'll check returncode
            text=True,
            capture_output=True,
            timeout=timeout_seconds
        )

        # yt-dlp returns 0 on success, non-zero on failure
        success = result.returncode == 0

        return success, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        timeout_minutes = max(1, math.ceil(timeout_seconds / 60))
        return False, '', f'Download timed out after {timeout_minutes} minutes'
    except Exception as e:
        return False, '', str(e)

def get_download_timeout_seconds(settings: dict) -> int:
    """Return the per-item download timeout in seconds."""
    return int(settings.get('download_timeout_minutes', 60)) * 60

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

async def process_and_send_video(update: Update, context: CallbackContext, video_file_path: str, settings: dict) -> bool:
    """Process a single video file (compress/split if needed) and send it."""
    filename_base = os.path.splitext(os.path.basename(video_file_path))[0]
    video_size = os.path.getsize(video_file_path)

    try:
        if video_size / MB_IN_BYTES > UPLOAD_SIZE_LIMIT_MB:
            if settings['compress_video']:
                await update.message.reply_text(f"Video {os.path.basename(video_file_path)} is too large. Compressing...")
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
        return True
    except Exception as e:
        logger.error(f"Error during video processing/sending: {e}")
        await update.message.reply_text(f"Error during video processing/sending: {e}")
        return False

async def process_playlist_range(context: CallbackContext, request: dict, start_index: int, end_index: int) -> None:
    """Download and send a selected inclusive playlist range."""
    update = request['update']
    url = request['url']
    filename_base = request['filename_base']
    settings = request['settings']
    entries = request['entries']
    mode = request['mode']
    item_name = request['item_name']
    download_timeout_seconds = get_download_timeout_seconds(settings)
    playlist_id = get_youtube_playlist_id(url)
    selected_entries = entries[start_index - 1:end_index]
    sent_count = 0
    failed_count = 0

    if not selected_entries:
        await update.message.reply_text("No playlist items selected.")
        return

    await update.message.reply_text(
        f"Starting playlist {item_name} download for index {start_index} to {end_index} "
        f"({len(selected_entries)} {item_name}s)."
    )

    for offset, entry in enumerate(selected_entries):
        index = start_index + offset
        item_url = build_playlist_entry_url(entry, playlist_id, index)
        title = entry.get('title') or f"{item_name} {index}"
        item_filename_base = (
            f"{filename_base}_audio_{update.message.message_id}_{index:03d}"
            if mode == 'audio'
            else f"{filename_base}_{update.message.message_id}_{index:03d}"
        )
        item_path = f'{SUBDIR}/{item_filename_base}'

        await update.message.reply_text(
            f"Downloading playlist {item_name} {index}/{len(entries)}: {title}\n{item_url}"
        )

        cmd = (
            build_audio_command(item_url, item_path, settings)
            if mode == 'audio'
            else build_video_command(item_url, item_path, settings)
        )
        logger.info(f"Running yt-dlp playlist {item_name} command: {' '.join(cmd)}")

        success, stdout, stderr = run_ytdlp_command(cmd, timeout_seconds=download_timeout_seconds)

        if not success:
            failed_count += 1
            error_msg = extract_error_message(stderr)
            logger.error(f"Playlist {item_name} download failed for {item_url}: {stderr}")
            await update.message.reply_text(
                f"Failed to download playlist {item_name} {index}/{len(entries)}:\n"
                f"{item_url}\n{error_msg}"
            )
            continue

        logger.info(f"Playlist {item_name} downloaded successfully ({playlist_id} #{index}). Output:\n{stdout}")

        try:
            downloaded_file_path = find_downloaded_file(item_filename_base)
        except FileNotFoundError as e:
            failed_count += 1
            logger.error(f"Could not find downloaded playlist {item_name} file: {e}")
            await update.message.reply_text(
                f"Download completed but file was not found for playlist {item_name} {index}/{len(entries)}:\n{item_url}"
            )
            continue

        if mode == 'audio':
            caption = f"Audio {index}/{len(entries)} from {item_url}"
            if await send_audio_file(update, context, downloaded_file_path, caption):
                sent_count += 1
            else:
                failed_count += 1

            if os.path.exists(downloaded_file_path):
                os.remove(downloaded_file_path)
            continue

        video_size = os.path.getsize(downloaded_file_path)
        await update.message.reply_text(
            f"Video downloaded: {os.path.basename(downloaded_file_path)} ({video_size / MB_IN_BYTES:.2f} MB)"
        )

        if await process_and_send_video(update, context, downloaded_file_path, settings):
            sent_count += 1
        else:
            failed_count += 1

        if os.path.exists(downloaded_file_path):
            os.remove(downloaded_file_path)

        if settings['download_audio'] and not settings['audio_only']:
            await download_audio_only(
                update,
                context,
                item_url,
                f"{item_filename_base}_audio",
                settings
            )

    await update.message.reply_text(
        f"Playlist {item_name} processing finished. Sent {sent_count} {item_name}s. Failed {failed_count} {item_name}s."
    )

async def download_youtube_playlist(update: Update, context: CallbackContext, url: str, filename_base: str, settings: dict) -> None:
    """Fetch a YouTube playlist and ask which video range to download."""
    download_timeout_seconds = get_download_timeout_seconds(settings)

    await update.message.reply_text(f"Fetching playlist videos from: {url}")

    success, entries, stderr = get_youtube_playlist_entries(url, settings, download_timeout_seconds)
    if not success:
        error_msg = extract_error_message(stderr)
        logger.error(f"Failed to fetch playlist entries: {stderr}")
        await update.message.reply_text(f"Failed to fetch playlist:\n{error_msg}")
        return

    if not entries:
        await update.message.reply_text("Playlist contains no downloadable videos.")
        return

    await start_playlist_range_prompt(
        update,
        context,
        url=url,
        filename_base=filename_base,
        settings=settings,
        entries=entries,
        mode='video'
    )

async def send_audio_file(update: Update, context: CallbackContext, audio_file_path: str, caption: str) -> bool:
    """Send a single audio file with the provided caption."""
    try:
        with open(audio_file_path, 'rb') as audio_file:
            await context.bot.send_audio(
                chat_id=update.effective_chat.id,
                audio=audio_file,
                caption=caption,
                write_timeout=TELEGRAM_WRITE_TIMEOUT,
                read_timeout=TELEGRAM_READ_TIMEOUT,
                connect_timeout=TELEGRAM_CONNECT_TIMEOUT
            )
        return True
    except TimedOut as e:
        logger.warning(f"Telegram timed out while sending audio file {audio_file_path}: {e}")
        return True
    except Exception as e:
        logger.error(f"Failed to send audio: {e}")
        await update.message.reply_text(f"Failed to send audio: {e}")
        return False

async def download_youtube_playlist_audio(update: Update, context: CallbackContext, url: str, filename_base: str, settings: dict) -> None:
    """Fetch a YouTube playlist and ask which audio range to download."""
    download_timeout_seconds = get_download_timeout_seconds(settings)

    await update.message.reply_text(f"Fetching playlist audios from: {url}")

    success, entries, stderr = get_youtube_playlist_entries(url, settings, download_timeout_seconds)
    if not success:
        error_msg = extract_error_message(stderr)
        logger.error(f"Failed to fetch playlist entries for audio: {stderr}")
        await update.message.reply_text(f"Failed to fetch playlist:\n{error_msg}")
        return

    if not entries:
        await update.message.reply_text("Playlist contains no downloadable audios.")
        return

    await start_playlist_range_prompt(
        update,
        context,
        url=url,
        filename_base=filename_base,
        settings=settings,
        entries=entries,
        mode='audio'
    )

async def download_video(update: Update, context: CallbackContext) -> None:
    settings = get_user_settings(update.effective_user.id)
    refined_url, filename_base = await refine_url_and_filename(update.message.text)
    download_timeout_seconds = get_download_timeout_seconds(settings)

    # Create downloads directory if it doesn't exist
    if not os.path.exists(SUBDIR):
        os.makedirs(SUBDIR)

    # If audio_only is enabled, only download audio
    if settings['audio_only']:
        if is_youtube_playlist_url(refined_url):
            await download_youtube_playlist_audio(update, context, refined_url, filename_base, settings)
            return

        await update.message.reply_text(f"Downloading audio only from: {refined_url}")
        await download_audio_only(update, context, refined_url, filename_base, settings)
        return

    if is_youtube_playlist_url(refined_url):
        await download_youtube_playlist(update, context, refined_url, filename_base, settings)
        return

    await update.message.reply_text(f"Downloading video from: {refined_url}")

    # Build and run the improved yt-dlp command
    video_path = f'{SUBDIR}/{filename_base}'
    cmd = build_video_command(refined_url, video_path, settings)
    logger.info(f"Running yt-dlp command: {' '.join(cmd)}")

    success, stdout, stderr = run_ytdlp_command(cmd, timeout_seconds=download_timeout_seconds)

    if not success:
        # Extract meaningful error message from stderr
        error_msg = extract_error_message(stderr)
        logger.error(f"Download failed: {stderr}")
        await update.message.reply_text(f"Download failed:\n{error_msg}")
        return

    logger.info(f"Video downloaded successfully! Output:\n{stdout}")

    # For Twitter/X and YouTube playlists, handle multiple videos
    if is_twitter_url(refined_url) or is_youtube_playlist_url(refined_url):
        try:
            video_files = find_all_downloaded_files(filename_base)
        except FileNotFoundError as e:
            logger.error(f"Could not find downloaded files: {e}")
            await update.message.reply_text(f"Download completed but files not found. Check logs for details.")
            return

        num_videos = len(video_files)
        if num_videos > 1:
            await update.message.reply_text(f"Found {num_videos} videos. Sending all...")

        for i, video_file_path in enumerate(video_files, 1):
            video_size = os.path.getsize(video_file_path)
            if num_videos > 1:
                await update.message.reply_text(f"Sending video {i}/{num_videos}: {os.path.basename(video_file_path)} ({video_size / MB_IN_BYTES:.2f} MB)")
            else:
                await update.message.reply_text(f"Video downloaded: {os.path.basename(video_file_path)} ({video_size / MB_IN_BYTES:.2f} MB)")

            await process_and_send_video(update, context, video_file_path, settings)

            # Clean up video file
            if os.path.exists(video_file_path):
                os.remove(video_file_path)

        # Download audio if enabled
        if settings['download_audio'] and not settings['audio_only']:
            await download_audio_only(update, context, refined_url, filename_base + "_audio", settings)
    else:
        # Single video handling for non-Twitter URLs
        try:
            video_file_path = find_downloaded_file(filename_base)
        except FileNotFoundError as e:
            logger.error(f"Could not find downloaded file: {e}")
            await update.message.reply_text(f"Download completed but file not found. Check logs for details.")
            return

        video_size = os.path.getsize(video_file_path)
        await update.message.reply_text(f"Video downloaded: {os.path.basename(video_file_path)} ({video_size / MB_IN_BYTES:.2f} MB)")

        await process_and_send_video(update, context, video_file_path, settings)

        # Download audio if enabled
        if settings['download_audio'] and not settings['audio_only']:
            await download_audio_only(update, context, refined_url, filename_base + "_audio", settings)

        # Clean up video file
        if os.path.exists(video_file_path):
            os.remove(video_file_path)

async def download_audio_only(update: Update, context: CallbackContext, url: str, filename_base: str, settings: dict, timeout_seconds: int = None) -> None:
    """Download audio only version of the content"""
    audio_path = f'{SUBDIR}/{filename_base}'
    cmd = build_audio_command(url, audio_path, settings)
    logger.info(f"Running yt-dlp audio command: {' '.join(cmd)}")

    if timeout_seconds is None:
        timeout_seconds = get_download_timeout_seconds(settings)

    success, stdout, stderr = run_ytdlp_command(cmd, timeout_seconds=timeout_seconds)

    if not success:
        error_msg = extract_error_message(stderr)
        logger.error(f"Audio download failed: {stderr}")
        await update.message.reply_text(f"Audio download failed:\n{error_msg}")
        return

    # For Twitter/X and YouTube playlists, handle multiple audio files
    if is_twitter_url(url) or is_youtube_playlist_url(url):
        try:
            audio_files = find_all_downloaded_files(filename_base)
        except FileNotFoundError as e:
            logger.error(f"Could not find downloaded audio files: {e}")
            await update.message.reply_text(f"Audio download completed but files not found.")
            return

        num_audios = len(audio_files)
        for i, audio_file_path in enumerate(audio_files, 1):
            try:
                caption = f"Audio {i}/{num_audios} from {url}" if num_audios > 1 else f"Audio from {url}"
                await send_audio_file(update, context, audio_file_path, caption)
            except Exception as e:
                logger.error(f"Failed to send audio: {e}")
                await update.message.reply_text(f"Failed to send audio {i}: {e}")
            finally:
                if os.path.exists(audio_file_path):
                    os.remove(audio_file_path)
    else:
        # Single audio handling for non-Twitter URLs
        try:
            audio_file_path = find_downloaded_file(filename_base)
        except FileNotFoundError as e:
            logger.error(f"Could not find downloaded audio file: {e}")
            await update.message.reply_text(f"Audio download completed but file not found.")
            return

        try:
            await send_audio_file(update, context, audio_file_path, f"Audio from {url}")
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
    """Find a single downloaded file matching the filename base."""
    for file in os.listdir(SUBDIR):
        if file.startswith(filename_base):
            return f"{SUBDIR}/{file}"
    raise FileNotFoundError(f"The file with base name {filename_base} was not found in {SUBDIR}.")

def find_all_downloaded_files(filename_base: str) -> list:
    """Find all downloaded files matching the filename base, sorted by name."""
    files = []
    for file in os.listdir(SUBDIR):
        if file.startswith(filename_base):
            files.append(f"{SUBDIR}/{file}")
    if not files:
        raise FileNotFoundError(f"No files with base name {filename_base} were found in {SUBDIR}.")
    return sorted(files)

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
    application.add_handler(MessageHandler(filters.Regex(r'^/set-proxy(?:\s|$)'), set_proxy_command))
    application.add_handler(MessageHandler(filters.Regex(r'^/set-cookies(?:\s|$)'), set_cookies_command))
    application.add_handler(MessageHandler(filters.Regex(r'^/set-download-timeout(?:\s|$)'), set_download_timeout_command))
    application.add_handler(CommandHandler("set_proxy", set_proxy_command))
    application.add_handler(CommandHandler("set_cookies", set_cookies_command))
    application.add_handler(CommandHandler("set_download_timeout", set_download_timeout_command))
    application.add_handler(CallbackQueryHandler(settings_button))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    application.run_polling()

if __name__ == '__main__':
    main()
