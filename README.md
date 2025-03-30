# video-dl-bot

A Telegram Bot that can download videos from Twitter, YouTube, etc. The downloaded video will be sent back to the user via the Telegram Bot. 

## Features

- Download videos from various platforms using yt-dlp
- Download audio-only version of videos
- Compress videos for easier sharing
- Split large files into smaller chunks for Telegram's file size limits
- Configure proxy for downloads in regions with restrictions
- User-specific settings saved between sessions

## Requirements

- Python 3.7+
- ffmpeg installed on the system
- yt-dlp installed on the system

## Installation

1. Clone this repository
2. Install the required Python packages:

```bash
pip install -r requirements.txt
```

3. Create a `.env` file with your configuration:

```
BOT_TOKEN=your_telegram_bot_token
UPLOAD_SIZE_LIMIT_MB=50
SPLIT_SIZE_LIMIT_MB=40
```

## Usage

1. Run the bot:

```bash
python video_dl_bot.py
```

2. Open Telegram and start a chat with your bot
3. Use the `/settings` command to configure download preferences:
   - Download Audio: Download audio version alongside video
   - Audio Only: Download only audio, no video
   - Compress Video: Compress large videos for easier sharing
   - Split Large Files: Split videos exceeding Telegram's size limits
   - Proxy: Set a proxy for downloads (with `/set_proxy` command)
4. Send a video URL to the bot to download it

## Deployment

The bot can be deployed on any server with Python and the required dependencies installed.

## License

This project is open-source software.
