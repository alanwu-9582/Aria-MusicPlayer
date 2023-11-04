MAX_ERROR_TIMES = 5
LOGLIMIT = 8
THUMBNAILSIZE = (210, 120)
YDL_OPTIONS = {
    "BestAudio" : {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet":True,
        "outtmpl": "assets\\audio\\%(title)s.%(ext)s",
    }
}

BACKGROUND_COLOR = "#5A5A5A"
PLAYER_BACKGROUND_COLOR = "#444444"
DASHBOARD_BACKGROUND_COLOR = "#555555"
FOREGROUND_COLOR = "#FFFFFF"

LOG_PROCESS_COLOR = '#E8E576'
LOG_ERROR_COLOR = '#E87676'
LOG_DONE_COLOR = '#98E876'
