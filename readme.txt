90s TV Player by 90sNick_Pinesal - https://www.youtube.com/@90sNick_Pinesal

“What if you could relive Saturday mornings like it’s 1993?”

“In this video, I’ll show you how to turn your Raspberry Pi into a fully automated 90s-style TV station using free tools and some retro magic.”

🔧 Pre-Requisites

🧱 What You’ll Need
A Raspberry Pi 3 is recommended but other models might work too.

MicroSD card (32GB+ recommended) or USB SSD for storage.

Internet connection for setup and downloading packages.

You’ll need the Raspberry Pi OS (Bullseye) with Desktop version installed.
➤ Download from: https://www.raspberrypi.com/software/

📦 Once the OS is installed and running:

📦 Install Required Software
🖥️ Open Terminal on your Raspberry Pi and enter the following commands one by one:

✅ 1. Update your system

sudo apt update && sudo apt upgrade -y
🎙️ "This makes sure everything is up to date and avoids weird package errors later."

✅ 2. Install VLC


sudo apt install vlc python3-tk unclutter xdotool ffmpeg -y

sudo apt install vlc -y
🎙️ "VLC is our media player behind the scenes—it handles all video playback."

✅ 3. Install Python and tkinter
Python 3 is preinstalled on Raspberry Pi OS, but tkinter might not be:

sudo apt install python3-tk -y
🎙️ "Tkinter gives us the GUI elements for the TV Guide."

✅ 4. Install Pygame

pip3 install pygame
🎙️ "Pygame is what powers the original scrolling TV guide interface. Even if you use the fancy one, it doesn’t hurt to have it installed."

✅ 5. Install unclutter (to hide the mouse cursor)

sudo apt install unclutter -y
🎙️ "This removes the mouse cursor from the screen after a few seconds of inactivity. It’s a small touch—but makes a huge difference."

✅ 6. Install xdotool (to minimize or control VLC)

sudo apt install xdotool -y
🎙️ "This lets the guide push VLC to the background, so the guide window stays in front."

✅ 7. (Optional but Recommended) Install ffmpeg

sudo apt install ffmpeg -y
🎙️ "This tool helps you convert or compress video files if needed. Not required for playback, but super helpful when prepping your content."

🗃️ Transfer Files Easily with FileZilla
🎙️ "To move videos and scripts to your Pi from a PC, FileZilla is the easiest way."

Download: https://filezilla-project.org/

On your PC, install FileZilla.

Connect to the Pi using:

Host: sftp://<your-pi-ip>

Username: pi (or your custom name)

Password: raspberry (or your custom password)

Port: 22

🎙️ "Just drag and drop video files into the proper folders. Done."

📁 Folder Setup
“Our TV scheduler runs off a very specific folder structure. Let’s set it up.”

Create /home/pi/Videos/90s shows/

Inside that, make these folders:

01morning

02afternoon

03evening

04night

Inside each time block, add channel folders like FOX, Nickelodeon, Discovery, etc.

Place .mp4 files inside the channels.

🎃 Optional:

For holiday specials, create:

/home/pi/Videos/holiday_specials/halloween

/home/pi/Videos/holiday_specials/christmas

For commercials:

/home/pi/Videos/commercials_day/

/home/pi/Videos/commercials_night/

/home/pi/Videos/commercials_halloween/

/home/pi/Videos/commercials_christmas/

🐍 Copy Over the Scripts
“Time to drop in the brains of this operation.”

Use FileZilla to copy:

tvplayer.py

tvguide2.py

Place them in /home/pi/Documents/

🪵 Enable or Disable Logging
“Want to know what’s playing or troubleshoot an issue? Turn on logging.”

Inside tvplayer.py, change:

ENABLE_LOGGING = True  # or False
Log will be saved to /home/pi/Documents/tvplayer_log.txt

⌨️ Set a Keyboard Shortcut for the TV Guide
“This shortcut lets you pop open the interactive TV guide at any time.”

mkdir /home/pi/.config/openbox
cp /etc/xdg/openbox/lxde-pi-rc.xml ~/.config/openbox

Edit Openbox config at:
~/.config/openbox/lxde-pi-rc.xml

Add inside <keyboard> block:

<keybind key="A-G">
  <action name="Execute">
    <command>python3 /home/pi/Documents/tvguide2.py</command>
  </action>
</keybind>
Reload config:
openbox --reconfigure

🚀 Setup Auto-Start
“Let’s make the TV station fire up automatically when the Pi boots.”

Edit or create:
/etc/xdg/lxsession/LXDE-pi/autostart

Add this line:
@python3 /home/pi/Documents/tvplayer.py

📺 First Launch Demo
“Let’s see it in action!”

Reboot the Pi.

It should boot straight into playback.

Press Alt+G to open the guide, pick a show.

Show a commercial break, followed by a show from a time slot.

Holiday triggers based on date

“Congratulations! You’ve just made your own nostalgic TV network. Fire it up, grab a bowl of cereal, and let the retro vibes roll.”