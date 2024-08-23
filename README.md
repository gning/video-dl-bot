# video-dl-bot

A Telegram Bot that can download videos from Twitter(X), YouTube, etc from given URLs. For each URL, the tracing parameters will be removed before downloading. The downloaded video will be sent back to the user via the Telegram Bot. 

## Deployment

### Install the dependencies

1, Install the required python libs
```
pip install -r requirements.txt
```

2, Install `yt-dlp`

Download the latest version of yt-dlp [here](https://github.com/yt-dlp/yt-dlp/releases). And then put it in one of the directories defined in the `PATH` environment variable. For example, `/usr/bin`. Remember to give the execution permission to `yt-dlp`. For example, `sudo chmod +x /usr/bin/yt-dlp`.

3, Install `ffmpeg`

Use the system command to install `ffmpeg`. For example, `sudo apt install ffmpeg`.

### Specify the Bot Token

Open the file `video_dl_bot.py` and replace `<Your-Telegram-Bot-Token>` with your Telegram Bot's token. Then save the changed file.

### Run the Bot

Run the Bot with the command:
```
python3 video_dl_bot.py
```
A log file named `video_dl_bot.log` will be generated on the same directory you start the Bot. 

It's better to run the Bot with a systemd service on Linux. Or run it with `nohup` in background with the command:
```
nohup python3 video_dl_bot.py &
```
