import datetime
import json
import subprocess
import time
import os
import logging
from typing import List, Dict, Any, Optional
import urllib.parse 
import xml.etree.ElementTree as ET
import random
import sys
import glob
import signal 
import argparse 

# --- 1. CONFIGURATION ---

# Base path for content, relative to the script location
BASE_CONTENT_PATH = os.path.dirname(os.path.abspath(__file__))

# Define the log directory path
LOG_DIR = os.path.join(BASE_CONTENT_PATH, 'logs')

# Ensure the log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Generate a timestamped log filename
TIMESTAMP = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
LOG_FILE = os.path.join(LOG_DIR, f"{TIMESTAMP}_tvplayer.log")

# UPDATE: Channel list file path
CHANNEL_LIST_FILE = os.path.join(BASE_CONTENT_PATH, 'channel_configs', 'channel_list.json')

# UPDATE: Schedule files are in 'schedule_data'
SCHEDULE_DIR = os.path.join(BASE_CONTENT_PATH, 'schedule_data')

# Ensure the directories exist
os.makedirs(os.path.dirname(CHANNEL_LIST_FILE), exist_ok=True)
os.makedirs(SCHEDULE_DIR, exist_ok=True)

# Minimum duration a video must have to be worth playing, used for late slots.
MIN_PLAYBACK_TIME = 5.0 # seconds

# VLC Configuration (Updated to use cvlc)
VLC_PATH = 'cvlc' # Using command-line VLC
VLC_ARGS = [
    '--no-video-title-show', # Do not show video title
    '--play-and-exit',       # Exit after playback finishes
    '--no-loop',             # Do not loop the video
    '--quiet',               # Reduce noise from VLC itself
    '--fullscreen',
]

# Flags specific to remote streaming (network caching and resilience)
REMOTE_STREAMING_FLAGS = [
    '--network-caching', '5000', # Increased cache for resilience
    '--http-reconnect',          # Force re-connect on stream interruption
]

# Global flag for dry-run mode, set by argparse
DRY_RUN = False 
# Global variable to track simulated time progression in dry-run mode
CURRENT_SIMULATED_TIME = datetime.datetime.now().replace(tzinfo=None) 


def setup_logging():
    """
    Sets up logging to write to both a timestamped file and the console (stdout).
    """
    
    # Base configuration: Set log level and output format
    log_format = '%(asctime)s - %(levelname)s - %(funcName)s: %(message)s'
    
    # Remove any existing root handlers to avoid duplicate output
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    # 1. File Handler
    file_handler = logging.FileHandler(LOG_FILE, mode='w')
    file_handler.setFormatter(logging.Formatter(log_format))
    file_handler.setLevel(logging.DEBUG) # Log everything to the file

    # 2. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(log_format))
    console_handler.setLevel(logging.INFO) # Only show INFO and above on console

    # Root Logger Configuration
    logging.basicConfig(
        level=logging.DEBUG, # Set the minimum level for the root logger
        handlers=[file_handler, console_handler]
    )
    
    # Log where the file is being saved
    logging.info(f"Logging initialized. Output saved to: {LOG_FILE}")


# --- 2. HELPER FUNCTIONS ---

def graceful_exit(signum=None, frame=None):
    """Handles script termination gracefully."""
    logging.info("Received signal, performing graceful shutdown...")
    sys.exit(0)

def load_channel_list():
    """
    Loads the channel list JSON and returns the list of channel names 
    from the 'channel_order' key.
    """
    try:
        with open(CHANNEL_LIST_FILE, 'r') as f:
            data = json.load(f)
            
            if 'channel_order' in data and isinstance(data['channel_order'], list):
                return data['channel_order']
            else:
                logging.error(f"Channel list file {CHANNEL_LIST_FILE} is missing the 'channel_order' list.")
                return []
                
    except FileNotFoundError:
        logging.error(f"Channel list file not found at {CHANNEL_LIST_FILE}. Cannot determine default channel.")
        return []
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {CHANNEL_LIST_FILE}.")
        return []

def load_schedule_for_channel(channel_name, schedule_date):
    """
    Loads the schedule from a JSON file based on the channel and date.
    Returns the list of scheduled program dictionaries.
    """
    schedule_filename = f"{channel_name}_{schedule_date}_schedule.json"
    schedule_path = os.path.join(SCHEDULE_DIR, schedule_filename)
    
    try:
        with open(schedule_path, 'r') as f:
            schedule_data = json.load(f)
            
            if not isinstance(schedule_data, list) or not schedule_data:
                logging.error(f"Schedule file {schedule_filename} is empty or malformed.")
                return []
                
            logging.info(f"Loaded {len(schedule_data)} program segments for {channel_name} on {schedule_date}.")
            return schedule_data
            
    except FileNotFoundError:
        logging.error(f"Schedule file not found: {schedule_path}.")
        return []
    except json.JSONDecodeError:
        logging.error(f"Error decoding JSON from {schedule_path}.")
        return []
    except Exception as e:
        logging.error(f"Error loading schedule: {e}")
        return []

def load_filler_videos_from_manifest(base_path, manifest_path):
    """
    Loads a list of filler video data (path and duration) from the specified XML manifest,
    resolving the manifest path relative to the base_path.
    """
    abs_manifest_path = os.path.join(base_path, manifest_path)
    
    if not os.path.exists(abs_manifest_path):
        logging.error(f"Manifest file not found: {abs_manifest_path}")
        return []

    filler_list = []
    
    try:
        tree = ET.parse(abs_manifest_path)
        root = tree.getroot()
        
        for file_elem in root.findall('file'):
            file_path = file_elem.get('name')
            length_elem = file_elem.find('length')
            
            if file_path and length_elem is not None and length_elem.text:
                try:
                    duration = float(length_elem.text)
                    filler_list.append({
                        'path': file_path,
                        'duration': duration
                    })
                except ValueError:
                    logging.warning(f"Skipping filler entry due to invalid duration: {length_elem.text}")
                    continue
        
        return filler_list
        
    except ET.ParseError as e:
        logging.error(f"Error parsing XML filler manifest {manifest_path}: {e}")
        return []
    except Exception as e:
        logging.error(f"An unexpected error occurred while loading filler manifest: {e}")
        return []


def run_filler_break(filler_xml_path, duration_seconds, content_root):
    """
    Plays filler content for a specified duration, loading video paths and durations 
    from the manifest specified by filler_xml_path.
    """
    global CURRENT_SIMULATED_TIME, DRY_RUN
    
    if duration_seconds <= 1.0:
        logging.warning("FILLER: Requested duration is too short (<1s). Skipping filler break.")
        return
    
    filler_list = load_filler_videos_from_manifest(content_root, filler_xml_path) 
    
    if not filler_list:
        logging.error(f"FILLER: Skipping break. Could not load filler videos from {filler_xml_path}.")
        return
        
    logging.info(f"FILLER: Starting break for {duration_seconds:.2f}s, using manifest: {filler_xml_path} (Items: {len(filler_list)})")
    
    if DRY_RUN:
        # Dry Run Mode: Simulate duration instantly
        logging.warning(f"DRY RUN: Simulating filler break for {duration_seconds:.2f}s.")
        CURRENT_SIMULATED_TIME += datetime.timedelta(seconds=duration_seconds)
        logging.info(f"FILLER END: Simulated break completed. Simulated time advanced to {CURRENT_SIMULATED_TIME}.")
        return

    # REAL PLAYBACK MODE
    start_time = time.time()
    
    while (time.time() - start_time) < duration_seconds:
        time_left = duration_seconds - (time.time() - start_time)
        
        if time_left < 1.0:
            logging.debug("FILLER: Time remaining is less than 1 second. Exiting filler loop.")
            break

        filler_video_data = random.choice(filler_list)
        
        filler_duration = filler_video_data['duration']
        max_clip_run_time = min(filler_duration, time_left)
        
        logging.debug(f"FILLER: Playing {os.path.basename(filler_video_data['path'])} (Length: {filler_duration:.2f}s) for max {max_clip_run_time:.2f}s.") 
        
        # Note: Filler video paths in the XML are often absolute paths and are passed directly
        played_time = play_video(filler_video_data, os.path.basename(filler_video_data['path']), max_clip_run_time, is_filler=True)

        if played_time is None:
            logging.error("FILLER: Filler playback failed. Consuming 5 seconds from the break duration.")
            time.sleep(5) 
            
    actual_filler_duration = time.time() - start_time
    logging.info(f"FILLER END: Break completed. Ran for {actual_filler_duration:.2f} seconds.")


def play_video(video_data, show_name, max_runtime_seconds, start_offset=0.0, is_filler=False):
    """
    Plays a video file (local or remote) using cvlc with offset and hard cutoff,
    applying streaming-specific flags for remote content.
    """
    global DRY_RUN, CURRENT_SIMULATED_TIME

    path = video_data['path']
    is_remote = path.lower().startswith(('http://', 'https://', 'ftp://'))
    
    # 1. Determine Stop Time (Absolute time in the video file)
    vlc_stop_time = start_offset + max_runtime_seconds 
    
    logging.info(
        f"PLAYING: {show_name} (Remote: {is_remote}). Offset: {start_offset:.2f}s, "
        f"Max Run: {max_runtime_seconds:.2f}s (Stop Time: {vlc_stop_time:.2f}s)."
    )

    if DRY_RUN:
        logging.warning(f"DRY RUN MODE: Skipping actual playback via CVLC.")
        
        # Advance simulated time
        CURRENT_SIMULATED_TIME += datetime.timedelta(seconds=max_runtime_seconds)
        logging.info(f"DRY RUN COMPLETE. Simulated time advanced by {max_runtime_seconds:.2f}s to {CURRENT_SIMULATED_TIME}.")
        
        # In dry run, we assume perfect playback up to the max_runtime_seconds cutoff
        return max_runtime_seconds 

    # --- REAL PLAYBACK LOGIC BELOW ---

    # 2. Build the Base CVLC Command and Scheduling Flags
    command = [VLC_PATH] 
    command.extend(VLC_ARGS)
    
    if is_remote:
        command.extend(REMOTE_STREAMING_FLAGS)
    
    command.extend([
        f'--start-time={start_offset:.2f}', 
        f'--stop-time={vlc_stop_time:.2f}' 
    ])
    
    # 3. Handle Path and Appending to Command
    if is_remote:
        try:
            parsed_url = urllib.parse.urlparse(path)
            encoded_path = parsed_url._replace(path=urllib.parse.quote(parsed_url.path)).geturl()
        except Exception:
            encoded_path = path

        command.append(encoded_path)
    else:
        # Local path handling
        command.append(path)

    # 4. Execute Playback
    playback_start_time = time.time()
    
    try:
        timeout_buffer = max_runtime_seconds + 5 
        
        logging.info(f"Executing command: {' '.join(command)}")

        subprocess.run(
            command, 
            timeout=timeout_buffer, 
            check=True,
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        
        # FIX: Even if VLC returns quickly due to stream issues, if it returns 0, 
        # we assume the intended scheduled time was consumed to maintain clock stability.
        # This prevents the filler from incorrectly running for 300 seconds.
        if is_filler:
            playback_end_time = time.time()
            actual_run_time = playback_end_time - playback_start_time
            logging.info(f"Playback finished successfully. Actual run time: {actual_run_time:.2f}s (FILLER).")
            return actual_run_time
        else:
            logging.info(f"Playback finished successfully. Assuming consumed time: {max_runtime_seconds:.2f}s (MAIN SHOW STABILITY).")
            return max_runtime_seconds
        
    except subprocess.TimeoutExpired:
        # If the timeout is reached, it means the video played for the intended duration
        logging.info(f"Playback stopped by scheduler timeout after {max_runtime_seconds:.2f}s (Hard Cutoff reached).")
        return max_runtime_seconds
        
    except subprocess.CalledProcessError as e:
        logging.error(f"Playback FAILED for {path}. Error code: {e.returncode}.")
        return None
        
    except FileNotFoundError:
        logging.error(f"CVLC not found at '{VLC_PATH}'. Cannot play video. Make sure VLC is installed and 'cvlc' is in your PATH.")
        return None
    
    except Exception as e:
        logging.error(f"An unexpected error occurred during playback of {path}: {e}")
        return None

# --- 3. MAIN SCHEDULER LOGIC ---

def main(channel_name, schedule_date, initial_start_time: datetime.datetime):
    """
    The main scheduler loop that runs the channel for the specified day.
    
    Args:
        channel_name (str): The name of the channel.
        schedule_date (str): The date string (YYYY-MM-DD).
        initial_start_time (datetime.datetime): The time point to start the scheduling from.
    """
    global CURRENT_SIMULATED_TIME, DRY_RUN
    
    # --- 1. SETUP & INITIAL LOAD ---
    schedule = load_schedule_for_channel(channel_name, schedule_date)
    if not schedule:
        logging.error(f"Cannot run channel {channel_name}: No schedule loaded.")
        return

    # Find the correct starting point in the schedule (i.e., the currently running or next program)
    start_index = 0
    now = initial_start_time
    
    for i, program in enumerate(schedule):
        # We must parse the time strings consistently here and in the main loop
        try:
            prog_start_datetime = datetime.datetime.strptime(program['start_time'], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
            slot_duration = program['slot_duration_total']
            prog_end_datetime = prog_start_datetime + datetime.timedelta(seconds=slot_duration)
            
            if prog_end_datetime > now:
                # This is the first program that hasn't completely finished yet.
                start_index = i
                break
        except (ValueError, KeyError) as e:
            logging.error(f"Skipping malformed schedule entry at index {i}: {e}")
            continue
        
    current_program_index = start_index 
    logging.info(f"Scheduler starting at index {current_program_index} ({schedule[current_program_index].get('show_name', 'Unknown')})")
    
    # For dry run, set the global simulated time to the calculated start time
    if DRY_RUN:
        CURRENT_SIMULATED_TIME = now
        logging.info(f"DRY RUN: Initial simulated time set to: {CURRENT_SIMULATED_TIME}")
    
    CHANNEL_CONTENT_ROOT = schedule[current_program_index].get('content_root', BASE_CONTENT_PATH)
    logging.info(f"Channel Content Root set to: {CHANNEL_CONTENT_ROOT}")

    # --- 2. MAIN PLAYBACK LOOP ---
    while current_program_index < len(schedule):
        program = schedule[current_program_index]
        
        # Use the current time from the global variable (if DRY_RUN) or actual time
        current_time = CURRENT_SIMULATED_TIME if DRY_RUN else datetime.datetime.now().replace(tzinfo=None)
        
        # Recalculate times based on the program data
        prog_start_datetime = datetime.datetime.strptime(program['start_time'], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
        slot_duration = program['slot_duration_total']
        prog_end_datetime = prog_start_datetime + datetime.timedelta(seconds=slot_duration)
        
        
        # Log the start of the slot processing
        logging.info(f"\n--- START SLOT: {program.get('show_name', 'Program')} ---")
        if DRY_RUN:
            logging.info(f"DRY RUN TIME: {current_time}")
        logging.info(f"Slot Start: {prog_start_datetime}, Slot End: {prog_end_datetime}, Current Time: {current_time}")
        
        # --- A. Pre-Program Wait (If we are early) ---
        time_to_start = (prog_start_datetime - current_time).total_seconds()
        
        if time_to_start > 1.0: 
            logging.warning(f"WAIT: Current time is ahead of schedule. Sleeping/Simulating wait for {time_to_start:.2f} seconds until {prog_start_datetime}.")
            
            if DRY_RUN:
                CURRENT_SIMULATED_TIME += datetime.timedelta(seconds=time_to_start)
                current_time = CURRENT_SIMULATED_TIME
            else:
                time.sleep(time_to_start)
                current_time = datetime.datetime.now().replace(tzinfo=None) 
                
            logging.info(f"Wait finished. Actual slot start time reached.")
        elif time_to_start < -1.0:
            logging.warning(f"LATE: Scheduler is running {abs(time_to_start):.2f} seconds late for this slot.")

        
        # --- B. EXECUTE PLAYBACK LOGIC ---
        
        # Recalculate current time based on the time progression (either sleep or simulation)
        current_time = CURRENT_SIMULATED_TIME if DRY_RUN else datetime.datetime.now().replace(tzinfo=None)
        
        time_elapsed_in_slot = (current_time - prog_start_datetime).total_seconds()
        time_available_seconds = slot_duration - time_elapsed_in_slot
        
        logging.info(f"Timing: Elapsed in slot: {time_elapsed_in_slot:.2f}s, Time available: {time_available_seconds:.2f}s (Total duration: {slot_duration:.2f}s)")

        if time_available_seconds <= 1.0:
            logging.error(f"Slot for {program.get('show_name', 'Program')} too short ({time_available_seconds:.2f}s). Skipping to next program.")
            current_program_index += 1
            continue

        video_data = program['video_data']
        video_full_duration = video_data.get('duration', 0)
        
        # 1. Calculate Offset (The "Live" Catch-up component)
        start_offset = time_elapsed_in_slot % video_full_duration if video_full_duration > 0 else 0 
        video_segment_remaining_time = video_full_duration - start_offset 

        # 2. Determine Final Run Time (The hard cutoff)
        max_run_time = min(video_segment_remaining_time, time_available_seconds)
        
        logging.info(f"Video Logic: Total video duration: {video_full_duration:.2f}s")
        logging.info(f"Video Logic: Calculated start_offset: {start_offset:.2f}s (Current catch-up point)")
        logging.info(f"Playback Max Runtime (Hard Cutoff): {max_run_time:.2f}s")
        
        
        # Retrieve Filler Manifest Path
        filler_manifest_path = program.get('filler_xml_path', 'ads/default_ads.xml')
        
        # Resolve path for local content (assuming main program video path is relative to CHANNEL_CONTENT_ROOT)
        # Note: If video_data['path'] is a URL, os.path.join will yield the URL
        video_path_to_play = os.path.join(CHANNEL_CONTENT_ROOT, program['video_data']['path'])
        
        # Use the correct path for playback, maintaining duration
        playback_video_data = {
            'path': video_path_to_play,
            'duration': video_full_duration
        }

        # --- C. Play Main Show or Filler ---
        
        if max_run_time < MIN_PLAYBACK_TIME: 
            # Low time left, skip main video and run filler for full remaining slot
            logging.warning(f"MAIN SHOW SKIPPED: Remaining run time is only {max_run_time:.2f}s. Running filler for full slot.")
            run_filler_break(filler_manifest_path, time_available_seconds, CHANNEL_CONTENT_ROOT)
            
        else:
            # Enough time for main show
            
            # 1. Play the main program
            actual_run_time = play_video(
                playback_video_data, 
                program['show_name'], 
                max_run_time, 
                start_offset=start_offset,
                is_filler=False # This is the main content
            )
            
            # --- D. Fill the Gap (Ads/Filler) ---
            
            if actual_run_time is not None:
                # Main show ran successfully (consumed 'actual_run_time', which now equals max_run_time)
                time_remaining_in_slot = time_available_seconds - actual_run_time 

                if time_remaining_in_slot > 1.0:
                    logging.info(f"FILLER START: Main show ran for {actual_run_time:.2f}s. Running filler for remaining {time_remaining_in_slot:.2f}s.")
                    run_filler_break(filler_manifest_path, time_remaining_in_slot, CHANNEL_CONTENT_ROOT)
                    
                elif time_remaining_in_slot < -1.0:
                    logging.warning(f"SLOT OVERRUN: Program ran {abs(time_remaining_in_slot):.2f}s past the scheduled slot end time! This may affect the next slot.")
                else:
                    logging.info(f"Slot ended precisely. Remaining time: {time_remaining_in_slot:.2f}s.")
                    
            elif actual_run_time is None:
                # Main show failed (e.g., file not found, crash)
                logging.error("MAIN SHOW FAILED. Running filler for the full time available.")
                run_filler_break(filler_manifest_path, time_available_seconds, CHANNEL_CONTENT_ROOT)

        
        # --- E. Advance ---
        
        logging.info(f"--- END SLOT: {program.get('show_name', 'Program')}. Advancing to next program. ---")
        current_program_index += 1
        
    logging.info("--- End of Schedule Reached. Exiting. ---")


if __name__ == '__main__':
    setup_logging()
    
    parser = argparse.ArgumentParser(description="A robust TV channel scheduler using VLC.")
    parser.add_argument('channel', nargs='?', type=str, 
                        help="The name of the channel to run (overrides default).")
    parser.add_argument('--dry-run', action='store_true', 
                        help="Simulate the scheduling and logging without playing videos.")
    parser.add_argument('--simulate-time', type=str, default=None,
                        help="Start dry-run simulation at a specific time (HH:MM:SS) on the run date.")

    args = parser.parse_args()
    
    DRY_RUN = args.dry_run

    # Determine which channel to run
    channel_to_run = args.channel
    if not channel_to_run:
        # Load the default channel from the channel list file
        channel_order = load_channel_list()
        if channel_order:
            channel_to_run = channel_order[0]
        else:
            logging.error("No channel specified and no default channel found in channel_list.json. Exiting.")
            sys.exit(1)

    # Determine the date to run (Today's date)
    run_date_str = datetime.date.today().strftime("%Y-%m-%d")
    
    # Determine the time to start the scheduler/simulation
    if DRY_RUN and args.simulate_time:
        try:
            # Combine today's date with the user-provided time
            start_time_str = f"{run_date_str}T{args.simulate_time}"
            initial_start_datetime = datetime.datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=None)
            logging.warning(f"DRY RUN: Simulation starting at specified time: {initial_start_datetime}")
        except ValueError:
            logging.error(f"Invalid time format for --simulate-time: {args.simulate_time}. Must be HH:MM:SS. Falling back to current time.")
            initial_start_datetime = datetime.datetime.now().replace(tzinfo=None)
    else:
        # Use real current time for live mode or default dry-run start
        initial_start_datetime = datetime.datetime.now().replace(tzinfo=None)


    logging.info(f"--- Starting Channel Scheduler for '{channel_to_run}' on {run_date_str} (Mode: {'DRY-RUN' if DRY_RUN else 'LIVE'}) ---")
    
    try:
        main(channel_to_run, run_date_str, initial_start_datetime)
    except KeyboardInterrupt:
        logging.warning("Scheduler manually stopped (KeyboardInterrupt).")
    except Exception as e:
        logging.critical(f"A fatal unhandled error occurred: {e}")
        sys.exit(1)
    finally:
        logging.info("Scheduler process finished.") 
