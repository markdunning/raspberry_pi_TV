import os
import sys
import json
import datetime
import subprocess
import time
import logging
import urllib.parse
from typing import Dict, Any, Optional, List
import xml.etree.ElementTree as ET
import random

# --- CONFIGURATION & PATHS ---
# Base path is assumed to be the directory containing this script.
BASE_PATH = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_DIR = os.path.join(BASE_PATH, 'schedule_data')
CHANNEL_STATE_FILE = os.path.join(BASE_PATH, 'current_channel.txt')
CHANNEL_REQUEST_FILE = os.path.join(BASE_PATH, 'channel_request.txt')
OVERRIDE_FILE = os.path.join(BASE_PATH, 'override_video.txt')
CHANNEL_LIST_FILE = os.path.join(BASE_PATH, 'channel_configs', 'channel_list.json')
LOG_DIR = os.path.join(BASE_PATH, 'logs') # Dedicated log directory

# --- VLC COMMAND CONSTANTS (Now split into discrete arguments) ---
VLC_BASE_OPTS = [
    "cvlc",
    "--fullscreen", 
    "--no-video-title-show", 
    "--play-and-exit", 
    "--no-repeat", 
    "--no-loop", 
    "--http-reconnect", 
    "--no-skip-frames",
    # Separating option and value to avoid shell quoting issues
    "--http-user-agent",
    "Mozilla/5.0 (compatible; TVPlayer/1.0)" 
]

CACHE_REMOTE = 5000 
CACHE_LOCAL = 300  

# --- STATE AND UTILITY FUNCTIONS ---

def setup_logging(channel_name: Optional[str]):
    """
    Configures detailed logging to output to a file named 'logs/[Date]_[Time]_[ChannelName].log'.
    """
    # 1. Ensure logs directory exists
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # 2. Define dynamic log filename (Includes full time stamp)
    now = datetime.datetime.now()
    safe_channel_name = channel_name.replace(' ', '_').replace('/', '_') if channel_name else "default"
    log_time_stamp = now.strftime('%Y-%m-%d_%H-%M-%S')
    log_filename = f"{log_time_stamp}_{safe_channel_name}.log"
    log_file_path = os.path.join(LOG_DIR, log_filename)
    
    # 3. Configure logging system
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    # 4. Clear existing handlers to prevent duplicate logging if called again
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
            
    # 5. Add FileHandler
    try:
        fh = logging.FileHandler(log_file_path, mode='a')
        fh.setFormatter(formatter)
        logger.addHandler(fh)
    except Exception as e:
        # Fallback to console logging if file setup fails
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            stream=sys.stderr
        )
        logger.critical(f"Failed to set up file logging. Using console only. Error: {e}")

    # 6. Add a StreamHandler for console output (helpful if running interactively)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    logger.info(f"Logging output directed to: {log_file_path}")


def load_channel_order() -> List[str]:
    """Loads the ordered list of channel names."""
    try:
        with open(CHANNEL_LIST_FILE, 'r') as f:
            data = json.load(f)
            return data.get('channel_order', [])
    except Exception as e:
        logging.error(f"Failed to load channel list: {e}")
        return []

def load_channel_state() -> Optional[str]:
    """Reads the name of the currently active channel."""
    try:
        with open(CHANNEL_STATE_FILE, 'r') as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except Exception:
        return None

def save_channel_state(channel_name: str):
    """Saves the name of the current channel."""
    try:
        with open(CHANNEL_STATE_FILE, 'w') as f:
            f.write(channel_name)
    except Exception as e:
        logging.error(f"Failed to save channel state: {e}")

def get_current_schedule_item(channel_name: str) -> Optional[Dict[str, Any]]:
    """
    Finds the scheduled program that should be playing right now for the given channel.
    """
    now = datetime.datetime.now()
    schedule_date_str = now.strftime("%Y-%m-%d")
    schedule_filename = f"{channel_name}_{schedule_date_str}_schedule.json"
    schedule_path = os.path.join(SCHEDULE_DIR, schedule_filename)

    try:
        with open(schedule_path, 'r') as f:
            schedule = json.load(f)
    except FileNotFoundError:
        logging.error(f"Schedule file not found for {channel_name} on {schedule_date_str}.")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding schedule JSON: {e}")
        return None

    # Check the schedule against the current time
    for program in schedule:
        try:
            # Parse start time (which is in ISO format)
            start_dt = datetime.datetime.fromisoformat(program['start_time'])
            
            # Calculate end time based on total slot duration
            slot_duration = program.get('slot_duration_total', 0)
            end_dt = start_dt + datetime.timedelta(seconds=slot_duration)
            
            # Check if 'now' falls in the slot
            if start_dt <= now < end_dt:
                return program
                
        except (ValueError, KeyError, TypeError) as e:
            logging.warning(f"Skipping corrupt schedule item: {program} - Error: {e}")
            continue

    return None

def read_channel_request() -> Optional[str]:
    """Reads and clears the channel request file."""
    if os.path.exists(CHANNEL_REQUEST_FILE):
        try:
            with open(CHANNEL_REQUEST_FILE, 'r') as f:
                channel_request = f.read().strip()
            os.remove(CHANNEL_REQUEST_FILE) # Clear the request
            return channel_request
        except Exception as e:
            logging.error(f"Error reading/clearing channel request: {e}")
    return None

def read_override_request() -> Optional[str]:
    """Reads and clears the video override file (used by the TV Guide GUI)."""
    if os.path.exists(OVERRIDE_FILE):
        try:
            with open(OVERRIDE_FILE, 'r') as f:
                video_path = f.read().strip()
            os.remove(OVERRIDE_FILE) # Clear the request
            return video_path
        except Exception as e:
            logging.error(f"Error reading/clearing override request: {e}")
    return None

def load_video_paths_from_xml(xml_full_path: str) -> List[str]:
    """Reads the list of video paths from an XML file."""
    paths = []
    if not xml_full_path or not os.path.exists(xml_full_path):
        return paths
    try:
        tree = ET.parse(xml_full_path)
        # Assuming simple <file name="path"/> or <video path="path"/>
        for element in tree.findall('.//file') + tree.findall('.//video'): 
             path = element.get('name') or element.get('path')
             if path:
                 paths.append(path)
    except ET.ParseError as e:
        logging.error(f"Error parsing filler XML {xml_full_path}: {e}")
    return paths

def select_filler_for_gap(filler_xml_path: str, content_root: str) -> Optional[str]:
    """
    Selects a random video from the filler XML list.
    """
    if not filler_xml_path:
        return None
        
    filler_full_path = os.path.join(content_root, filler_xml_path)
    available_fillers = load_video_paths_from_xml(filler_full_path)
    
    if available_fillers:
        return random.choice(available_fillers)
        
    return None


# --- VIDEO PLAYBACK FUNCTION ---

def play_video(video_path: str, content_root: str, max_run_time: float, seek_time: float, enforce_kill: bool) -> float:
    """
    Plays a video file (local or remote) using VLC.
    Returns the actual duration the video ran for (in seconds).
    """
    
    # 1. Determine if the path is a remote URL
    is_remote = video_path.lower().startswith(('http://', 'https://'))
    
    # 2. Determine the full path/URL and the appropriate caching
    if is_remote:
        # Remote: Must be URL encoded (e.g., spaces become %20)
        player_full_path = urllib.parse.quote(video_path, safe=':/%')
        network_caching = CACHE_REMOTE 
        logging.info(f"play_video: Using REMOTE caching ({network_caching}ms) for URL: {player_full_path}")
    else:
        # Local: Use absolute path directly (Popen list handling is robust to spaces)
        player_full_path = os.path.abspath(os.path.join(content_root, video_path))
        network_caching = CACHE_LOCAL
        logging.info(f"play_video: Using LOCAL absolute path for file: {player_full_path}")

    # 3. Assemble the VLC command (using discrete list elements for robustness)
    vlc_command_parts = list(VLC_BASE_OPTS) 
    
    # Add network caching option
    vlc_command_parts.append("--network-caching")
    vlc_command_parts.append(str(network_caching))

    if seek_time > 0 and seek_time < 3600:
        logging.info(f"play_video: CVLC OFFSET: Seeking to {int(seek_time)} seconds (--start-time flag).")
        # Add start time option
        vlc_command_parts.append("--start-time")
        vlc_command_parts.append(str(int(seek_time)))
    else:
        logging.info(f"play_video: Seek time is 0 or invalid. Starting video from the beginning.")


    # Append the video path/URI as the final argument
    vlc_command_parts.append(player_full_path)
    player_command = vlc_command_parts

    # 4. Execute and monitor the video playback
    try:
        # NOTE: The command is executed as a list of arguments, but logged as a single string 
        # for easy copy/paste testing in the shell.
        logging.info(f"VLC COMMAND: {' '.join(player_command)}")
        
        player_proc = subprocess.Popen(
            player_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        logging.info(f"VLC started (PID: {player_proc.pid}) with max_run_time {max_run_time:.2f}s")

        start_time = time.time()
        
        while player_proc.poll() is None:
            elapsed_time = time.time() - start_time
            
            # Check for time limit or explicit kill flag
            if elapsed_time > max_run_time or enforce_kill:
                logging.warning(f"Video exceeded max run time ({max_run_time:.2f}s) or kill requested. Terminating PID: {player_proc.pid}")
                player_proc.terminate()
                time.sleep(0.5)
                if player_proc.poll() is None:
                    player_proc.kill() 
                break
            
            time.sleep(1) 
        
        # Check how long the video actually ran for
        actual_run_time = time.time() - start_time
        logging.info(f"VLC (PID: {player_proc.pid}) finished. Return Code: {player_proc.returncode}. Actual run time: {actual_run_time:.2f}s.")

        # Return the actual run time
        return actual_run_time 

    except FileNotFoundError:
        logging.critical("VLC (cvlc) command not found. Is VLC installed and in your PATH?")
        return 0.0 # Return 0 run time on failure
    except Exception as e:
        logging.error(f"Error executing VLC command: {e}")
        return 0.0 # Return 0 run time on failure

# --- MAIN PLAYER LOGIC ---

def main():
    
    # 1. Determine Initial Channel State 
    current_channel_initial = load_channel_state()
    channel_order = load_channel_order()
    
    # NEW LOGIC: Check for command-line argument (sys.argv[0] is the script name)
    cli_channel = None
    if len(sys.argv) > 1:
        cli_channel = sys.argv[1].strip()
        
    # Determine the starting channel: CLI argument > State file > First in order
    if cli_channel and cli_channel in channel_order:
        current_channel = cli_channel
    else:
        # Fallback logic
        current_channel = current_channel_initial or (channel_order[0] if channel_order else "No_Channel")
        
    # Log if a CLI argument was provided but was invalid
    if cli_channel and cli_channel not in channel_order:
        # Note: If channel_order is empty, 'No_Channel' will be set, which is fine
        if channel_order:
             logging.warning(f"Invalid channel argument '{cli_channel}' provided. Falling back to state/default: {current_channel}")

    save_channel_state(current_channel)

    # 2. Setup Logging to the correct file path
    setup_logging(current_channel)

    logging.info(f"--- TV Player Main Loop Starting on channel: {current_channel} ---")
    
    if not channel_order:
        logging.critical("No channels found. Exiting.")
        sys.exit(1)
        
    MIN_GAP_FOR_PLAY = 5 # Minimum seconds required to start playing a video

    while True:
        
        # 3. CHECK FOR REQUESTS
        requested_channel = read_channel_request()
        if requested_channel and requested_channel in channel_order:
            if requested_channel != current_channel:
                logging.info(f"Channel switch requested: {current_channel} -> {requested_channel}")
                current_channel = requested_channel
                save_channel_state(current_channel)
                setup_logging(current_channel)
                logging.info("Logging reconfigured for new channel.")
                
        override_video_path = read_override_request()
        if override_video_path:
            logging.info(f"Override video requested: {override_video_path}")
            play_video(
                video_path=override_video_path,
                content_root="", 
                max_run_time=3600, 
                seek_time=0,
                enforce_kill=False 
            )
            continue 

        # 4. GET CURRENT SCHEDULE ITEM
        current_program = get_current_schedule_item(current_channel)

        if not current_program:
            logging.warning(f"No schedule found for {current_channel} at this time. Sleeping for 60s.")
            time.sleep(60)
            continue

        # Extract essential data
        main_video_path = current_program['video_data']['path']
        actual_video_duration = current_program['video_data']['duration']
        slot_duration_total = current_program['slot_duration_total']
        content_root = current_program['content_root'] 

        # Calculate time remaining in the slot
        current_slot_start_time = datetime.datetime.fromisoformat(current_program['start_time'])
        current_slot_end_time = current_slot_start_time + datetime.timedelta(seconds=slot_duration_total)
        
        # Calculate time since the scheduled program started (used for seeking)
        time_since_start = (datetime.datetime.now() - current_slot_start_time).total_seconds()
        
        # Total time remaining until the slot *must* end
        time_to_slot_end_initial = (current_slot_end_time - datetime.datetime.now()).total_seconds()
        
        # --- CRITICAL SLOT CHECK ---
        if time_to_slot_end_initial < 1:
            logging.info(f"Slot for {main_video_path} is over ({time_to_slot_end_initial:.1f}s remaining). Proceeding to next iteration.")
            continue 

        # --- A. Play Main Video (Play from start/seek) ---
        
        # Maximum time we will allow the main video to run for in this slot
        max_run_time_for_main = min(actual_video_duration, time_to_slot_end_initial)
        time_consumed_main = 0.0

        if max_run_time_for_main > MIN_GAP_FOR_PLAY:
            logging.info(f"Playing Main Video: {main_video_path} (Max run: {max_run_time_for_main:.2f}s). Seeking to {time_since_start:.1f}s.")
            
            # play_video returns actual run time
            time_consumed_main = play_video(
                video_path=main_video_path, 
                content_root=content_root,
                max_run_time=max_run_time_for_main,
                seek_time=time_since_start, 
                enforce_kill=False 
            )
            
            # FIX: Check for < 5.0s run time to catch instant VLC failures reliably
            if time_consumed_main < 5.0 and time_to_slot_end_initial > 60.0:
                time_remaining_after_fail = time_to_slot_end_initial - time_consumed_main
                logging.error(f"WARNING: Main video failed instantly. Sleeping until end of slot to prevent re-loop. Wait time: {time_remaining_after_fail:.1f}s.")
                time.sleep(time_remaining_after_fail)
                continue # Immediately check for the next program
                
        else:
             logging.info(f"Skipping Main Video: Time remaining ({max_run_time_for_main:.1f}s) too short.")

        
        # --- B. Handle Filler/Buffer Video ---
        
        # Recalculate time remaining based on the amount of time that actually passed during main video attempt
        time_remaining_in_slot = time_to_slot_end_initial - time_consumed_main

        if time_remaining_in_slot > MIN_GAP_FOR_PLAY:
            filler_xml_path = current_program.get('filler_xml_path')
            
            if filler_xml_path: 
                
                filler_video_path = select_filler_for_gap(filler_xml_path, content_root)

                if filler_video_path:
                    logging.info(f"Playing Filler Video: {filler_video_path} (Gap: {time_remaining_in_slot:.2f}s)")
                    
                    # Play filler for the remainder of the slot
                    play_video(
                        video_path=filler_video_path,
                        content_root=content_root,
                        max_run_time=time_remaining_in_slot,
                        seek_time=0,
                        enforce_kill=False
                    )
                else:
                    logging.info(f"No suitable filler found. Waiting for {time_remaining_in_slot:.1f}s until next program.")
                    time.sleep(time_remaining_in_slot)
                    
            else:
                 logging.info(f"No filler defined. Waiting for {time_remaining_in_slot:.1f}s until next program.")
                 time.sleep(time_remaining_in_slot)
        else:
            logging.info(f"Gap too short ({time_remaining_in_slot:.1f}s). Proceeding to next program.")
            time.sleep(max(0, time_remaining_in_slot)) # Wait any remaining milliseconds

if __name__ == '__main__':
    main()
