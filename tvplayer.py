import os
import threading
import time
import random
import subprocess
from datetime import datetime

# Hide mouse cursor
subprocess.Popen(["unclutter", "--timeout", "0"])

#Define logging method
ENABLE_LOGGING = False
LOG_DIR = "/home/pi/Documents/tvplayer_logs"
CURRENT_LOG_FILE = None # Will be set dynamically in main()

def log(message):
    if ENABLE_LOGGING and CURRENT_LOG_FILE:
        timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
        with open(CURRENT_LOG_FILE, "a") as f:
            f.write(f"{timestamp} {message}\n")

# Define folder structure for channels based on time blocks
BASE_PATH = "/home/pi/Videos/90s shows"
COMMERCIALS_PATH = "/home/pi/Videos/commercials"
HOLIDAY_PATH = "/home/pi/Videos/holiday_specials"

# Define supported video file extensions
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv")

# Define schedule times (24-hour format)
SCHEDULE = {
    "06:00": "01morning",
    "12:00": "02afternoon",
    "17:00": "03evening",
    "21:00": "04night"
}

# Define holiday periods
HOLIDAYS = {
    "halloween": ("10-21", "10-31"),
    "christmas": ("12-15", "12-25")
}

# Helper function to read a .urls file and pick one
def _select_url_from_file(file_path):
    """Reads a .urls file, selects a random URL, and returns it."""
    try:
        with open(file_path, 'r') as f:
            urls = [line.strip() for line in f if line.strip()]
        if urls:
            final_selection = random.choice(urls)
            log(f"URL list selected: {file_path}, playing URL: {final_selection}")
            return final_selection
        else:
            log(f"URL file is empty: {file_path}. Skipping.")
            return None
    except Exception as e:
        log(f"Error reading URL file {file_path}: {e}")
        return None

# Define day or night commercials
def get_commercials_path():
    holiday = is_holiday()
    if holiday:
        path = f"/home/pi/Videos/commercials_{holiday}"
        if os.path.exists(path):
            log(f"Using holiday commercials: {path}")
            return path  # Use holiday commercials if folder exists

    # Fallback to day/night
    hour = datetime.now().hour
    if 6 <= hour < 20:
        log("Using day commercials")
        return "/home/pi/Videos/commercials_day"
    else:
        log("Using night commercials")
        return "/home/pi/Videos/commercials_night"

def is_holiday():
    today = datetime.today().strftime("%m-%d")
    for holiday, (start, end) in HOLIDAYS.items():
        if start <= today <= end:
            return holiday
    return None


def get_current_time_block():
    now = datetime.now().strftime("%H:%M")
    for switch_time, block in reversed(list(SCHEDULE.items())):
        if now >= switch_time:
            log(f"Current time block: {block}")
            return block
    return "night"  # Default fallback


def get_video_file():
    selected_show_path = '/home/pi/Documents/selected_show.txt'
    # Define acceptable extensions for local files and URL lists
    # Now includes the common video extensions plus the .urls file
    ALLOWED_EXTENSIONS = VIDEO_EXTENSIONS + (".urls",)

    # 1. Check for user-selected show first
    if os.path.exists(selected_show_path):
        with open(selected_show_path, 'r') as f:
            selected_video = f.read().strip()
        
        # NOTE: User selection always assumes the content is the video file or URL itself
        if selected_video.startswith('http') or os.path.exists(selected_video):
            log(f"User-selected show detected: {selected_video}")
            os.remove(selected_show_path)  # Prevent repeat plays
            return selected_video
        else:
            log("Selected show not found on disk or invalid, ignoring.")


    # 2. Check for holiday programming
    holiday = is_holiday()
    if holiday:
        holiday_folder = os.path.join(HOLIDAY_PATH, holiday)
        if os.path.exists(holiday_folder):
            # Check for allowed extensions
            videos = [os.path.join(holiday_folder, f) for f in os.listdir(holiday_folder) if f.lower().endswith(ALLOWED_EXTENSIONS)]
            if videos:
                selected = random.choice(videos)
                
                # Handle .urls file selection
                if selected.lower().endswith(".urls"):
                    return _select_url_from_file(selected)
                
                log(f"Holiday programming active: {holiday}, playing {selected}")
                return selected

    # 3. Fallback to normal schedule
    time_block = get_current_time_block()
    time_block_path = os.path.join(BASE_PATH, time_block)

    if os.path.exists(time_block_path):
        all_videos = []
        for channel in os.listdir(time_block_path):
            channel_path = os.path.join(time_block_path, channel)
            if os.path.isdir(channel_path):
                # Check for allowed extensions
                videos = [os.path.join(channel_path, f) for f in os.listdir(channel_path) if f.lower().endswith(ALLOWED_EXTENSIONS)]
                all_videos.extend(videos)

        if all_videos:
            selected = random.choice(all_videos)
            
            # Handle .urls file selection
            if selected.lower().endswith(".urls"):
                return _select_url_from_file(selected)
            
            log(f"Scheduled programming selected from block {time_block}: {selected}")
            return selected

    log("No video file could be selected.")
    return None  # No video found

def play_video(file_path):
    # Check for local file path errors only if it does not look like a URL
    if file_path.startswith('/'): # Heuristic check for local path
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            log(f"Error: Local file does not exist or is empty: {file_path}")
            return

    log("Stopping any existing VLC instances before playing video...")
    subprocess.run(["pkill", "-9", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # Allow VLC to fully close

    log(f"Now playing: {file_path}")
    
    # VLC arguments work for both local files and URLs
    subprocess.run([
        "cvlc", "--fullscreen", "--vout", "x11", "--play-and-exit", "--no-repeat", "--no-loop",
        # NOTE: Aspect ratio/crop may not work well or be desired for all streaming URLs
        "--aspect-ratio=4:3", "--crop=4:3", file_path 
    ])

    log("Ensuring VLC is stopped after video playback...")
    subprocess.run(["pkill", "-9", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)  # Short delay to ensure VLC has fully terminated

def play_commercials():
    log("Stopping any existing VLC instances before commercials...")
    subprocess.run(["pkill", "-9", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)  # Give time for VLC to close completely

    commercial_folder = get_commercials_path()
    # Commercials MUST be local files, so we only look for the defined VIDEO_EXTENSIONS
    commercials = [os.path.join(commercial_folder, f) for f in os.listdir(commercial_folder) if f.lower().endswith(VIDEO_EXTENSIONS)]

    if not commercials:
        log("No commercials found. Skipping commercial break.")
        return

    total_commercial_time = 0
    commercial_duration = 180  # 3 minutes

    log("Starting commercial break...")
    while total_commercial_time < commercial_duration:
        selected_commercial = random.choice(commercials)
        log(f"Now playing commercial: {selected_commercial}")

        subprocess.run([
            "cvlc", "--fullscreen", "--vout", "x11", "--play-and-exit", "--no-repeat", "--no-loop",
            "--aspect-ratio=4:3", "--crop=4:3", selected_commercial
        ])

        subprocess.run(["pkill", "-9", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)

        total_commercial_time += 30  # Estimate per commercial

    log("Commercial break finished.")

def main():
    global CURRENT_LOG_FILE # Declare use of global variable

    # --- Log Setup: Create a unique log file for this session ---
    os.makedirs(LOG_DIR, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    CURRENT_LOG_FILE = os.path.join(LOG_DIR, f"tvplayer_log_{now_str}.txt")
    # --- End Log Setup ---

    log("=== TV Player Script Started ===")
    while True:
        video_file = None

        while not video_file:
            video_file = get_video_file()
            time.sleep(1)

        if video_file:
            play_commercials()
            play_video(video_file)
        else:
            log("No video found, retrying in 3 seconds...")
            time.sleep(3)

if __name__ == "__main__":
    main()