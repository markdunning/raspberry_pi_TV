import tkinter as tk
import random
import datetime
import os
import subprocess  # For playing the video

class ScrollingTable:
    def __init__(self, root):
        self.root = root
        self.root.title("TV Guide Channel")
        self.root.configure(bg="#032039")

        self.base_directory = "/home/pi/Videos/90s shows"
        self.canvas = tk.Canvas(root, width=720, height=480, bg='#043e72', highlightthickness=0)
        self.canvas.pack(padx=0, pady=5)

        # Footer Frame
        self.footer_frame = tk.Frame(root, bg='#043e72')
        self.footer_frame.place(x=0, y=355, width=720, height=55)

        footer_labels = ["Thanks", "for", "watching", "cable!"]
        for col, title in enumerate(footer_labels):
            label = tk.Label(self.footer_frame, text=title, fg='white', bg='#043e72', width=19, anchor='center', font=("Arial", 12, "bold"))
            label.grid(row=0, column=col)

        # Header Frame
        self.header_frame = tk.Frame(root, bg='#043e72')
        self.header_frame.place(x=5, y=0, width=640, height=30)

        self.column_widths = [8, 16, 16, 16]
        self.update_timeslots()

        headers = ["Channel"] + self.timeslots
        for col, title in enumerate(headers):
            label = tk.Label(self.header_frame, text=title, fg='white', bg='#043e72',
                             width=self.column_widths[col], anchor='center', font=("Arial", 15, "bold"))
            label.grid(row=0, column=col)

        self.rows = []
        self.row_height = 40
        self.start_y = 330
        self.active_rows = []
        self.channel_names = self.get_channels()
        self.current_channel_index = 0
        self.num_visible_rows = 3
        self.selected_show_label = None  # To keep track of the currently selected label
        self.shows_full_paths = []
        self.show_labels = [] #list to store show labels

        for i in range(self.num_visible_rows):
            self.spawn_row(y=300 - i * self.row_height)

        self.root.after(self.time_until_next_half_hour() * 1000, self.update_timeslots)
        self.scroll()
        self.bind_keyboard_events()

    def update_timeslots(self):
        now = datetime.datetime.now()
        minute = now.minute
        if minute < 30:
            base_time = now.replace(minute=0, second=0, microsecond=0)
        else:
            base_time = now.replace(minute=30, second=0, microsecond=0)

        self.timeslots = [(base_time + datetime.timedelta(minutes=30 * i)).strftime('%I:%M %p') for i in range(0, 3)]

        for col in range(1, 4):
            label = tk.Label(self.header_frame, text=self.timeslots[col - 1], fg='white', bg='#043e72',
                             width=self.column_widths[col], anchor='center')
            label.grid(row=0, column=col)

        self.root.after(self.time_until_next_half_hour() * 1000, self.update_timeslots)

    def time_until_next_half_hour(self):
        now = datetime.datetime.now()
        next_half_hour = now.replace(second=0, microsecond=0)
        if now.minute < 30:
            next_half_hour = next_half_hour.replace(minute=30)
        else:
            next_half_hour = next_half_hour.replace(minute=0, hour=now.hour + 1)
        return (next_half_hour - now).seconds

    def get_active_timeblock(self):
        now = datetime.datetime.now()
        hour = now.hour
        if 6 <= hour < 11:
            return "01morning"
        elif 11 <= hour < 15:
            return "02afternoon"
        elif 15 <= hour < 20:
            return "03evening"
        else:
            return "04night"

    def get_channels(self):
        timeblock_path = os.path.join(self.base_directory, self.get_active_timeblock())
        if os.path.exists(timeblock_path):
            return [d for d in os.listdir(timeblock_path) if os.path.isdir(os.path.join(timeblock_path, d))]
        return []

    def get_random_shows(self, channel):
        timeblock_path = os.path.join(self.base_directory, self.get_active_timeblock())
        channel_path = os.path.join(timeblock_path, channel)
        valid_extensions = (".mp4", ".avi", ".mpg", ".wmv")

        if os.path.exists(channel_path):
            show_files = [f for f in os.listdir(channel_path) if f.endswith(valid_extensions)]
            random.shuffle(show_files)
            selected_shows = [self.truncate_filename(f.rsplit('.', 1)[0]) for f in show_files[:3]]
            full_paths = [os.path.join(channel_path, f) for f in show_files[:3]]
            while len(selected_shows) < 3:
                selected_shows.append("TBD")
                full_paths.append("TBD")
            return selected_shows, full_paths
        return ["TBD", "TBD", "TBD"], ["TBD", "TBD", "TBD"]

    def truncate_filename(self, name):
        return name[:15] + "..." if len(name) > 10 else name

    def spawn_row(self, y=None):
        if y is None:
            y = self.start_y
        frame = tk.Frame(self.canvas, bg='#032039')
        window_id = self.canvas.create_window(325, y, window=frame, width=640,
                                              height=self.row_height)
        self.populate_row(frame)
        separator = tk.Frame(self.canvas, bg='black', height=1, width=700)
        separator_id = self.canvas.create_window(325, y + self.row_height - 1,
                                                 window=separator)
        self.active_rows.append((frame, window_id))
        self.active_rows.append((separator, separator_id))

    def populate_row(self, frame):
        channel = self.channel_names[self.current_channel_index]
        shows, full_paths = self.get_random_shows(channel)
        self.shows_full_paths.extend(full_paths)
        channel_label = tk.Label(frame, text=channel, width=self.column_widths[0], anchor='center', relief='solid',
                                 borderwidth=1, bg="#002d54", fg="#fff000", font=("Arial", 20, "bold"))
        channel_label.grid(row=0, column=0, sticky='nsew')

        for col in range(1, 4):
            show_label = tk.Label(frame, text=shows[col - 1], width=self.column_widths[col], anchor='w', relief='solid',
                                  bg="#002d54", fg="white", font=("Arial", 15), borderwidth=1, cursor="hand2")
            show_label.grid(row=0, column=col, sticky='nsew')
            if shows[col - 1] != "TBD":
                show_label.bind("<Button-1>", lambda event, path=full_paths[col - 1]: self.play_show(event, path))
            self.show_labels.append(show_label)  # store the show labels
        self.current_channel_index = (self.current_channel_index + 1) % len(self.channel_names)

    def scroll(self):
        rows_to_remove = []
        for i in range(0, len(self.active_rows), 2):
            if i + 1 < len(self.active_rows):
                frame, window_id = self.active_rows[i]
                separator, separator_id = self.active_rows[i + 1]
                x, y = self.canvas.coords(window_id)
                y -= 1
                self.canvas.coords(window_id, x, y)
                self.canvas.coords(separator_id, x, y + self.row_height - 1)
                self.active_rows[i] = (frame, window_id)
                self.active_rows[i + 1] = (separator, separator_id)

        if self.active_rows and self.canvas.coords(self.active_rows[-2][1])[1] <= 330:
            last_y = self.canvas.coords(self.active_rows[-2][1])[1] if self.active_rows else 300
            self.spawn_row(y=last_y + self.row_height)

        if self.active_rows and self.canvas.coords(self.active_rows[0][1])[1] < -self.row_height:
            rows_to_remove.append(self.active_rows.pop(0))
            rows_to_remove.append(self.active_rows.pop(0))
            if self.show_labels and self.shows_full_paths:
              del self.show_labels[:3]
              del self.shows_full_paths[:3]
              
        for frame, window_id in rows_to_remove:
            self.canvas.delete(window_id)

        self.root.after(50, self.scroll)

    def play_show(self, event, path):
        if path != "TBD":
            try:
                with open("/home/pi/Documents/selected_show.txt", "w") as file:
                    file.write(path)
                subprocess.run(["pkill", "-9", "vlc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.root.destroy()
                print(f"Selected show written to file: {path}")
            except Exception as e:
                print(f"Error playing show: {e}")
                tk.messagebox.showerror("Error", f"Could not play show: {e}")

    def bind_keyboard_events(self):
        self.root.bind("<Up>", self.on_up_arrow)
        self.root.bind("<Down>", self.on_down_arrow)
        self.root.bind("<Return>", self.on_enter)
        self.root.bind("<Escape>", lambda event: self.root.destroy())
        self.root.focus_set()

    def on_up_arrow(self, event):
        if not self.shows_full_paths:
            return

        if self.selected_show_label is None:
            self.selected_show_label = self.show_labels[0]
            self.highlight_selected_show()
        else:
            current_index = self.show_labels.index(self.selected_show_label)
            if current_index > 0:
                self.selected_show_label = self.show_labels[current_index - 1]
                self.highlight_selected_show()

    def on_down_arrow(self, event):
        if not self.shows_full_paths:
            return

        if self.selected_show_label is None:
            self.selected_show_label = self.show_labels[0]
            self.highlight_selected_show()
        else:
            current_index = self.show_labels.index(self.selected_show_label)
            if current_index < len(self.show_labels) - 1:
                self.selected_show_label = self.show_labels[current_index + 1]
                self.highlight_selected_show()

    def on_enter(self, event):
        if self.selected_show_label is not None:
            show_index = self.show_labels.index(self.selected_show_label)
            if 0 <= show_index < len(self.shows_full_paths):
                path = self.shows_full_paths[show_index]
                if path != "TBD":
                    self.play_show(None, path)

    def highlight_selected_show(self):
        for label in self.show_labels:
            label.config(bg="#002d54", fg="white")

        self.selected_show_label.config(bg="yellow", fg="black")

if __name__ == "__main__":
    root = tk.Tk()
    #subprocess.run(["xdotool", "search", "--class", "vlc", "windowminimize"])
    #root.attributes('-fullscreen', True)
    #root.after(100, lambda: root.overrideredirect(True))
    app = ScrollingTable(root)
    root.mainloop()