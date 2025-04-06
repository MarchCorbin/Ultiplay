import os
import sys
import tkinter as tk
from tkinter import filedialog, colorchooser
from tkinterdnd2 import *
import vlc
from PIL import Image, ImageTk, ImageDraw, ImageFont
import random
import json
import math
import re
import logging
import time
import threading

# Configure logging
if getattr(sys, 'frozen', False):
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    log_path = os.path.join(exe_dir, 'ultiplay.log')
else:
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ultiplay.log')

logging.basicConfig(
    level=logging.DEBUG,
    filename=log_path,
    filemode='w',
    format='%(asctime)s - %(levelname)s - %(message)s'
)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger('').addHandler(console_handler)

logging.debug("Starting Ultiplay application")

if getattr(sys, 'frozen', False):
    tkdnd_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'tkinterdnd2', 'tkdnd')
    os.environ['TKDND_LIBRARY'] = tkdnd_path

class PlayerWidget:
    def __init__(self, parent, app, canvas, x, y, width=400, height=300, playlist=None):
        logging.debug("Initializing player widget at x=%d, y=%d", x, y)
        self.parent = parent
        self.app = app
        self.canvas = canvas
        self.is_swapping = False

        # VLC initialization
        try:
            if getattr(sys, 'frozen', False):
                if hasattr(sys, '_MEIPASS'):
                    vlc_path = os.path.join(sys._MEIPASS, 'vlc')
                else:
                    vlc_path = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'vlc')
                if os.path.exists(vlc_path):
                    os.environ['VLC_PLUGIN_PATH'] = vlc_path
                    os.environ['PATH'] = os.environ.get('PATH', '') + ';' + vlc_path
                    logging.debug("Using bundled VLC path: %s", vlc_path)
                else:
                    logging.error("VLC path not found: %s", vlc_path)
                    raise FileNotFoundError(f"VLC path not found: {vlc_path}")
                self.instance = vlc.Instance(['--quiet', '--no-video-title-show', '--aout=directx', '--verbose', '0'])
            else:
                self.instance = vlc.Instance(['--quiet', '--no-video-title-show', '--aout=directx', '--verbose', '0'])

            if self.instance is None:
                logging.error("VLC Instance is None - cannot initialize VLC")
                raise ValueError("Failed to initialize VLC Instance")
            self.player = self.instance.media_player_new()
            logging.debug("VLC initialized successfully for frame_id: %d", id(self))
        except Exception as e:
            logging.error(f"VLC initialization failed: {e}")
            raise

        self.frame = tk.Frame(self.canvas, bg="black", borderwidth=2, relief="raised")
        self.frame_id = self.canvas.create_window(x, y, window=self.frame, width=width, height=height, anchor="nw")

        self.video_frame = tk.Frame(self.frame, bg="black")
        self.video_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.video_frame.drop_target_register(DND_FILES)
        self.video_frame.dnd_bind('<<Drop>>', self.drop_video)

        self.controls_frame = tk.Frame(self.frame, bg="gray")
        self.controls_visible = False
        self.hide_timeout = None

        self.slider_frame = tk.Frame(self.controls_frame, bg="gray")
        self.slider_frame.pack(side=tk.TOP, fill=tk.X)
        self.seek_bar = tk.Scale(self.slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                 command=self.seek, showvalue=0, length=int(width * 0.75) - 100,
                                 highlightbackground="green", troughcolor="darkgreen")
        self.seek_bar.bind("<Button-1>", self.seek_to_position)
        self.time_label = tk.Label(self.slider_frame, text="0:00 / 0:00", bg="gray", fg="white")
        self.time_label.pack(side=tk.LEFT, padx=2)
        self.volume_bar = tk.Scale(self.slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                   command=self.set_volume, showvalue=0, length=int((width * 0.75) * 0.25),
                                   highlightbackground="blue", troughcolor="darkblue")
        self.volume_bar.set(100)

        self.button_frame = tk.Frame(self.controls_frame, bg="gray")
        self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.drag_handle = tk.Label(self.button_frame, text="Move", bg="gray", fg="white", cursor="fleur")
        # Unified toggle button replaces separate play and pause buttons
        self.toggle_button = tk.Button(self.button_frame, text="▶", command=self.toggle_play_pause, width=3)
        self.toggle_button.bind("<Control-Button-1>", self.universal_toggle)
        self.prev_button = tk.Button(self.button_frame, text="<<", command=self.play_previous_video, width=3)
        self.prev_button.bind("<Control-Button-1>", self.universal_prev)
        self.next_button = tk.Button(self.button_frame, text=">>", command=self.play_next_video, width=3)
        self.next_button.bind("<Control-Button-1>", self.universal_next)
        self.mode_button = tk.Button(self.button_frame, text="Shuf", command=self.toggle_next_mode, width=4)
        self.playlist_button = tk.Button(self.button_frame, text="Play", width=4)
        self.playlist_button.bind("<Button-1>", self.on_playlist_button_click)
        self.close_button = tk.Button(self.button_frame, text="X", command=self.close, width=3)
        self.resize_handle = tk.Label(self.button_frame, text="↘", bg="gray", fg="white", cursor="sizing")
        self.title_label = tk.Label(self.button_frame, text="No video loaded", bg="gray", fg="white")

        # Packing layout adjusted for single toggle button
        if self.app.is_maximized and len(self.app.players) == 5 and self == self.app.players[1]:
            self.seek_bar.pack(side=tk.RIGHT, padx=2)
            self.time_label.pack(side=tk.RIGHT, padx=2)
            self.volume_bar.pack(side=tk.RIGHT, padx=2)
            self.resize_handle.pack(side=tk.RIGHT, padx=2)
            self.close_button.pack(side=tk.RIGHT, padx=1)
            self.playlist_button.pack(side=tk.RIGHT, padx=1)
            self.mode_button.pack(side=tk.RIGHT, padx=1)
            self.next_button.pack(side=tk.RIGHT, padx=1)
            self.prev_button.pack(side=tk.RIGHT, padx=1)
            self.toggle_button.pack(side=tk.RIGHT, padx=1)
            self.drag_handle.pack(side=tk.RIGHT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5)
        else:
            self.seek_bar.pack(side=tk.LEFT, padx=2)
            self.time_label.pack(side=tk.LEFT, padx=2)
            self.volume_bar.pack(side=tk.LEFT, padx=2)
            self.drag_handle.pack(side=tk.LEFT, padx=2)
            self.toggle_button.pack(side=tk.LEFT, padx=1)
            self.prev_button.pack(side=tk.LEFT, padx=1)
            self.next_button.pack(side=tk.LEFT, padx=1)
            self.mode_button.pack(side=tk.LEFT, padx=1)
            self.playlist_button.pack(side=tk.LEFT, padx=1)
            self.close_button.pack(side=tk.LEFT, padx=1)
            self.resize_handle.pack(side=tk.RIGHT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5)

        self.frame.update()
        self.player.set_hwnd(self.video_frame.winfo_id())
        self.player.video_set_scale(0)
        self.player.audio_set_volume(100)
        self.volume_bar.set(self.player.audio_get_volume())

        self.player.event_manager().event_attach(vlc.EventType.MediaPlayerEndReached, self.on_video_end)
        self.update_seek_bar()
        self.update_toggle_button()  # Initialize button state

        self.frame.bind("<Enter>", self.show_controls)
        self.frame.bind("<Leave>", self.schedule_hide_controls)
        self.frame.bind("<Motion>", self.show_controls)
        self.drag_handle.bind("<Button-1>", self.start_drag)
        self.drag_handle.bind("<B1-Motion>", self.drag)
        self.drag_handle.bind("<ButtonRelease-1>", self.stop_drag)
        self.resize_handle.bind("<Button-1>", self.start_resize)
        self.resize_handle.bind("<B1-Motion>", self.resize)

        self.drag_start_x = 0
        self.drag_start_y = 0

        self.current_file = None
        self.next_mode = "random_repo" if not playlist else "playlist"
        self.playlist = playlist if playlist is not None else []
        self.current_playlist_index = -1

        self.update_mode_button_text()
        if self.playlist and playlist is not None:
            logging.debug("Loading first video from playlist: %s", self.playlist[0] if self.playlist else "No playlist")
            self.load_first_video()

    def update_mode_button_text(self):
        mode_labels = {
            "random_repo": "Shuf",
            "playlist": "Play",
            "next_repo": "Repo",
            "random_playlist": "Rand"
        }
        text = mode_labels.get(self.next_mode, "Shuf")
        self.mode_button.config(text=text)

    def update_title_label(self):
        if self.current_file:
            title = os.path.basename(self.current_file)
            self.title_label.config(text=title)
        else:
            self.title_label.config(text="No video loaded")

    def load_video(self, file_path):
        logging.debug("Attempting to load video: %s", file_path)
        if getattr(sys, 'frozen', False):
            file_path = os.path.abspath(file_path)
        if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
            self.current_file = file_path
            def load_in_thread():
                try:
                    self.player.stop()
                    time.sleep(0.2)
                    self.reset_vlc()
                    media = self.instance.media_new(file_path)
                    self.player.set_media(media)
                    self.player.set_hwnd(self.video_frame.winfo_id())
                    self.player.play()
                    self.seek_bar.set(0)
                    self.parent.after(200, self.refresh_vlc)
                    self.parent.after(2000, self.check_playback_start)
                    logging.debug("Loaded and playing video: %s", file_path)
                    self.parent.after(0, self.update_title_label)
                    self.parent.after(0, self.update_toggle_button)
                except Exception as e:
                    logging.error(f"Failed to load video {file_path}: {e}")
                    self.current_file = None
                    self.parent.after(0, self.update_title_label)
                    self.parent.after(0, self.update_toggle_button)
                    self.reset_vlc()
            threading.Thread(target=load_in_thread, daemon=True).start()
        else:
            logging.error("Invalid or unsupported video file: %s", file_path)
            self.current_file = None
            self.update_title_label()
            self.update_toggle_button()
            self.play_next_video()

    def check_playback_start(self):
        if self.current_file:
            state = self.player.get_state()
            is_playing = self.player.is_playing()
            logging.debug("Checking playback start for %s: is_playing=%s, state=%s", 
                         self.current_file, is_playing, state)
            if not is_playing and state != vlc.State.Paused:
                logging.debug("Video %s failed to start or stalled after 2s (state=%s), skipping to next", 
                             self.current_file, state)
                self.play_next_video()
            elif is_playing:
                logging.debug("Video %s started successfully (state=%s)", self.current_file, state)
            else:
                logging.debug("Video %s is paused at start (state=%s), no action taken", self.current_file, state)

    def reset_vlc(self, seek_position=None):
        logging.debug("Resetting VLC instance for frame_id: %d", self.frame_id)
        was_playing = self.player.is_playing()
        if was_playing:
            self.player.pause()
            time.sleep(0.2)
        position = seek_position if seek_position is not None else (self.player.get_time() if was_playing else 0)
        media = self.player.get_media()
        self.player.stop()
        try:
            logging.debug("Creating new VLC player instance")
            self.player = self.instance.media_player_new()
            if media:
                self.player.set_media(media)
                self.player.set_hwnd(self.video_frame.winfo_id())
                if was_playing or seek_position is not None:
                    self.player.play()
                    self.parent.after(200, lambda: self.player.set_time(position))
            logging.debug("VLC reset completed for frame_id: %d, playing: %s, position: %d", self.frame_id, was_playing, position)
        except Exception as e:
            logging.error(f"Failed to reset VLC for frame_id %d: {e}", self.frame_id)
        width = self.frame.winfo_width()
        if self.app.is_maximized and len(self.app.players) == 5 and self == self.app.players[4]:
            self.seek_bar.config(length=100)
            self.volume_bar.config(length=50)
        else:
            slider_length = int(width * 0.75)
            self.seek_bar.config(length=max(100, slider_length - 100))
            self.volume_bar.config(length=min(100, int(slider_length * 0.25)))
        self.player.audio_set_volume(100)
        self.volume_bar.set(self.player.audio_get_volume())
        if self.controls_visible:
            self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.video_frame.lift()
            self.parent.after(50, self.refresh_vlc)
        self.frame.update_idletasks()
        self.update_toggle_button()  # Update button state after reset

    def refresh_vlc(self):
        try:
            if self.player.is_playing():
                self.player.pause()
                self.parent.after(10, lambda: self.player.play())
            self.player.set_hwnd(self.video_frame.winfo_id())
            self.volume_bar.set(self.player.audio_get_volume())
            logging.debug("VLC refreshed for frame_id: %d", self.frame_id)
            self.update_toggle_button()  # Update button state
        except Exception as e:
            logging.error(f"Failed to refresh VLC: {e}")

    def load_first_video(self):
        logging.debug("Loading first video from playlist: %s", self.playlist[0] if self.playlist else "No playlist")
        if self.playlist:
            self.current_playlist_index = 0
            self.load_video(self.playlist[self.current_playlist_index])

    def play_video(self):
        logging.debug("Playing video: %s", self.current_file)
        self.toggle_play_pause()

    def pause_video(self):
        logging.debug("Pausing video: %s", self.current_file)
        self.toggle_play_pause()

    def toggle_play_pause(self):
        """Toggle between play and pause states."""
        if self.player.is_playing():
            self.player.pause()
        elif self.current_file or self.playlist:
            self.player.play()
            self.frame.lift()
            self.video_frame.lift()
        self.update_toggle_button()

    def update_toggle_button(self):
        """Update toggle button text based on playback state."""
        if self.player.is_playing():
            self.toggle_button.config(text="||")  # Pause icon when playing
        else:
            self.toggle_button.config(text="▶")   # Play icon when paused/stopped

    def seek(self, value):
        logging.debug("Seeking to position via drag: %s%%", value)
        if self.player.get_media():
            duration = self.player.get_length()
            if duration > 0:
                position = int(float(value) / 100 * duration)
                self.player.set_time(position)
                self.seek_bar.set(value)
                if int(float(value)) == 100:
                    self.on_video_end(None)
                self.update_toggle_button()  # Ensure button updates after seek

    def seek_to_position(self, event):
        logging.debug("Seek bar clicked at x=%d", event.x)
        if self.player.get_media():
            duration = self.player.get_length()
            if duration > 0:
                seek_bar_width = self.seek_bar.winfo_width()
                if seek_bar_width <= 0:
                    logging.warning("Seek bar width is zero or negative, cannot seek")
                    return
                click_position = event.x
                percentage = min(100, max(0, (click_position / seek_bar_width) * 100))
                position = int((percentage / 100) * duration)
                self.player.set_time(position)
                self.seek_bar.set(percentage)
                logging.debug("Jumped to %.1f%% (position: %d ms)", percentage, position)
                if percentage >= 100:
                    self.on_video_end(None)
                self.update_toggle_button()  # Ensure button updates after seek

    def set_volume(self, value):
        logging.debug("Setting volume to %s%% for frame_id: %d", value, self.frame_id)
        volume = int(float(value))
        self.player.audio_set_volume(volume)
        self.volume_bar.set(self.player.audio_get_volume())

    def format_time(self, ms):
        seconds = ms // 1000
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}:{seconds:02d}"

    def update_seek_bar(self):
        if self.player.get_media():
            duration = self.player.get_length()
            if duration > 0:
                position = self.player.get_time()
                percentage = (position / duration) * 100
                self.seek_bar.set(percentage)
                self.time_label.config(text=f"{self.format_time(position)} / {self.format_time(duration)}")
                state = self.player.get_state()
                time_left = (duration - position) / 1000
                if percentage > 95 or time_left < 5:
                    if not self.player.is_playing() and state != vlc.State.Paused:
                        self.play_next_video()
                    elif state == vlc.State.Ended:
                        self.play_next_video()
                self.update_toggle_button()  # Sync button with state
        self.parent.after(500, self.update_seek_bar)

    def drop_video(self, event):
        logging.debug("Raw drop event data (video frame): %s", event.data)
        data = event.data.strip()
        if data.startswith("{") and data.endswith("}"):
            file_path = data[1:-1].strip()
        else:
            file_path = data
        if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
            self.load_video(file_path)
        else:
            logging.error("Invalid or unsupported video file dropped: %s", file_path)

    def show_controls(self, event=None):
        logging.debug("Showing controls for player widget")
        if not self.controls_visible:
            self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            if self.app.is_maximized and len(self.app.players) == 5 and self == self.app.players[1]:
                self.seek_bar.pack_forget()
                self.time_label.pack_forget()
                self.volume_bar.pack_forget()
                self.drag_handle.pack_forget()
                self.toggle_button.pack_forget()
                self.prev_button.pack_forget()
                self.next_button.pack_forget()
                self.mode_button.pack_forget()
                self.playlist_button.pack_forget()
                self.close_button.pack_forget()
                self.resize_handle.pack_forget()
                self.title_label.pack_forget()
                self.seek_bar.pack(side=tk.RIGHT, padx=2)
                self.time_label.pack(side=tk.RIGHT, padx=2)
                self.volume_bar.pack(side=tk.RIGHT, padx=2)
                self.resize_handle.pack(side=tk.RIGHT, padx=2)
                self.close_button.pack(side=tk.RIGHT, padx=1)
                self.playlist_button.pack(side=tk.RIGHT, padx=1)
                self.mode_button.pack(side=tk.RIGHT, padx=1)
                self.next_button.pack(side=tk.RIGHT, padx=1)
                self.prev_button.pack(side=tk.RIGHT, padx=1)
                self.toggle_button.pack(side=tk.RIGHT, padx=1)
                self.drag_handle.pack(side=tk.RIGHT, padx=2)
                self.title_label.pack(side=tk.LEFT, padx=5)
            else:
                self.seek_bar.pack_forget()
                self.time_label.pack_forget()
                self.volume_bar.pack_forget()
                self.drag_handle.pack_forget()
                self.toggle_button.pack_forget()
                self.prev_button.pack_forget()
                self.next_button.pack_forget()
                self.mode_button.pack_forget()
                self.playlist_button.pack_forget()
                self.close_button.pack_forget()
                self.resize_handle.pack_forget()
                self.title_label.pack_forget()
                self.seek_bar.pack(side=tk.LEFT, padx=2)
                self.time_label.pack(side=tk.LEFT, padx=2)
                self.volume_bar.pack(side=tk.LEFT, padx=2)
                self.drag_handle.pack(side=tk.LEFT, padx=2)
                self.toggle_button.pack(side=tk.LEFT, padx=1)
                self.prev_button.pack(side=tk.LEFT, padx=1)
                self.next_button.pack(side=tk.LEFT, padx=1)
                self.mode_button.pack(side=tk.LEFT, padx=1)
                self.playlist_button.pack(side=tk.LEFT, padx=1)
                self.close_button.pack(side=tk.LEFT, padx=1)
                self.resize_handle.pack(side=tk.RIGHT, padx=2)
                self.title_label.pack(side=tk.LEFT, padx=5)
            self.controls_visible = True
            self.video_frame.lift()
            self.frame.update_idletasks()
            self.parent.after(50, self.refresh_vlc)
        if self.hide_timeout:
            self.parent.after_cancel(self.hide_timeout)
            self.hide_timeout = None

    def schedule_hide_controls(self, event=None):
        logging.debug("Scheduling hide controls")
        if self.controls_visible and not self.hide_timeout:
            self.hide_timeout = self.parent.after(3000, self.hide_controls)

    def hide_controls(self):
        logging.debug("Hiding controls for player widget")
        if self.controls_visible:
            self.controls_frame.pack_forget()
            self.controls_visible = False
            self.video_frame.lift()
            self.frame.update_idletasks()
            self.parent.after(50, self.refresh_vlc)
        self.hide_timeout = None

    def start_drag(self, event):
        logging.debug("Starting drag at canvas x=%d, y=%d", event.x + self.canvas.coords(self.frame_id)[0], event.y + self.canvas.coords(self.frame_id)[1])
        frame_x, frame_y = self.canvas.coords(self.frame_id)
        self.drag_start_x = event.x + frame_x
        self.drag_start_y = event.y + frame_y
        self.canvas.tag_raise(self.frame_id)
        if self.app.is_maximized and not self.app.is_fullscreen:
            logging.info("Dragging disabled in Screen Maximize Mode (SMM). Use number keys 1-5 to swap players or F11 for manipulation.")
            return
        print(f"Start drag at canvas x={self.drag_start_x}, y={self.drag_start_y}")

    def drag(self, event):
        logging.debug("Dragging to new position")
        frame_x, frame_y = self.canvas.coords(self.frame_id)
        current_x = event.x + frame_x
        current_y = event.y + frame_y
        delta_x = current_x - self.drag_start_x
        delta_y = current_y - self.drag_start_y
        new_x = frame_x + delta_x
        new_y = frame_y + delta_y
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        self.frame.update_idletasks()
        frame_width = self.frame.winfo_width()
        frame_height = self.frame.winfo_height()

        new_x = max(0, min(new_x, canvas_width - frame_width))
        new_y = max(0, min(new_y, canvas_height - frame_height))
        self.canvas.coords(self.frame_id, new_x, new_y)
        self.drag_start_x = current_x
        self.drag_start_y = current_y
        logging.debug("Dragging to x=%d, y=%d, canvas w=%d, h=%d", new_x, new_y, canvas_width, canvas_height)

    def stop_drag(self, event):
        logging.debug("Drag stopped")
        print("Drag stopped")
        width = self.frame.winfo_width()
        slider_length = int(width * 0.75)
        self.seek_bar.config(length=max(100, slider_length - 100))
        self.volume_bar.config(length=min(100, int(slider_length * 0.25)))

    def start_resize(self, event):
        logging.debug("Starting resize at x=%d, y=%d", event.x_root, event.y_root)
        if not self.app.is_maximized or self.app.is_fullscreen:
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root
            print(f"Start resize at x={self.drag_start_x}, y={self.drag_start_y}")

    def resize(self, event):
        logging.debug("Resizing player widget")
        if not self.app.is_maximized or self.app.is_fullscreen:
            delta_x = event.x_root - self.drag_start_x
            delta_y = event.y_root - self.drag_start_y
            new_width = max(200, self.frame.winfo_width() + delta_x)
            new_height = max(150, self.frame.winfo_height() + delta_y)
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            new_width = min(new_width, canvas_width)
            new_height = min(new_height, canvas_height)
            self.canvas.itemconfig(self.frame_id, width=new_width, height=new_height)
            current_x, current_y = self.canvas.coords(self.frame_id)
            if current_y + new_height > canvas_height:
                self.canvas.coords(self.frame_id, current_x, canvas_height - new_height)
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root
            self.frame.lift()
            logging.debug("Resized to width=%d, height=%d", new_width, new_height)
            slider_length = int(new_width * 0.75)
            self.seek_bar.config(length=max(100, slider_length - 100))
            self.volume_bar.config(length=min(100, int(slider_length * 0.25)))

    def close(self):
        logging.debug("Closing player widget")
        self.player.stop()
        self.canvas.delete(self.frame_id)
        self.app.players.remove(self)

    def toggle_next_mode(self):
        logging.debug("Toggling next mode")
        modes = ["random_repo", "playlist", "next_repo", "random_playlist"]
        current_idx = modes.index(self.next_mode)
        next_idx = (current_idx + 1) % len(modes)
        self.next_mode = modes[next_idx]
        self.update_mode_button_text()
        logging.debug("Next mode set to: %s", self.next_mode)

    def universal_toggle(self, event):
        logging.debug("Universal toggle triggered")
        for player in self.app.players:
            player.toggle_play_pause()

    def universal_prev(self, event):
        logging.debug("Universal prev triggered")
        for player in self.app.players:
            player.play_previous_video()

    def universal_next(self, event):
        logging.debug("Universal next triggered")
        for player in self.app.players:
            player.play_next_video()

    def on_playlist_button_click(self, event):
        x = self.parent.winfo_pointerx()
        y = self.parent.winfo_pointery()
        self.edit_playlist_at_position(x, y)
        return "break"

    def edit_playlist_at_position(self, x, y):
        logging.debug("Opening playlist editor at click position x=%d, y=%d", x, y)
        playlist_window = tk.Toplevel(self.parent)
        playlist_window.title("Edit Playlist")
        
        window_width = 400
        window_height = 300
        
        screen_width = self.parent.winfo_screenwidth()
        screen_height = self.parent.winfo_screenheight()
        x = max(0, min(x, screen_width - window_width))
        y = max(0, min(y, screen_height - window_height))
        
        playlist_window.geometry(f"{window_width}x{window_height}+{x}+{y}")

        tk.Label(playlist_window, text="Current Playlist:").pack(pady=5)
        self.playlist_listbox = tk.Listbox(playlist_window)
        for file in self.playlist:
            self.playlist_listbox.insert(tk.END, os.path.basename(file))
        self.playlist_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.playlist_listbox.drop_target_register(DND_FILES)
        self.playlist_listbox.dnd_bind('<<Drop>>', self.handle_drop)

        button_frame = tk.Frame(playlist_window)
        button_frame.pack(fill=tk.X, pady=2)
        tk.Button(button_frame, text="Add Videos", command=self.add_to_playlist).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Remove Selected", command=self.remove_from_playlist).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Move Up", command=self.move_up).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Move Down", command=self.move_down).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Close", command=playlist_window.destroy).pack(side=tk.LEFT, padx=2)

    def move_up(self):
        logging.debug("Moving selected item up in playlist")
        selection = self.playlist_listbox.curselection()
        if selection:
            index = selection[0]
            if index > 0:
                item = self.playlist.pop(index)
                self.playlist.insert(index - 1, item)
                self.update_playlist_listbox()
                self.playlist_listbox.selection_clear(0, tk.END)
                self.playlist_listbox.selection_set(index - 1)
                if self.current_playlist_index == index:
                    self.current_playlist_index -= 1
                elif self.current_playlist_index == index - 1:
                    self.current_playlist_index += 1
                logging.debug("Moved up: %s from %d to %d", os.path.basename(item), index, index - 1)

    def move_down(self):
        logging.debug("Moving selected item down in playlist")
        selection = self.playlist_listbox.curselection()
        if selection:
            index = selection[0]
            if index < len(self.playlist) - 1:
                item = self.playlist.pop(index)
                self.playlist.insert(index + 1, item)
                self.update_playlist_listbox()
                self.playlist_listbox.selection_clear(0, tk.END)
                self.playlist_listbox.selection_set(index + 1)
                if self.current_playlist_index == index:
                    self.current_playlist_index += 1
                elif self.current_playlist_index == index + 1:
                    self.current_playlist_index -= 1
                logging.debug("Moved down: %s from %d to %d", os.path.basename(item), index, index + 1)

    def handle_drop(self, event):
        logging.debug("Handling drop - Raw data: %s", event.data)
        data = event.data.strip()
        if data.startswith('{') and data.endswith('}') and ' ' not in data:
            file_path = data[1:-1].strip()
            if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
                self.playlist.append(file_path)
                self.update_playlist_listbox()
                self.parent.update()
                logging.debug("Added via drop: %s", file_path)
        elif ' ' in data:
            paths = re.findall(r'(?:{[^{}]*}|[^{}\s]+)', data)
            added_files = []
            for path in paths:
                cleaned_path = path.strip('{}"\'')
                if os.path.isfile(cleaned_path) and cleaned_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
                    added_files.append(cleaned_path)
            if added_files:
                self.playlist.extend(added_files)
                self.update_playlist_listbox()
                self.parent.update()
                logging.debug("Added via multi-drop: %s", added_files)

    def add_to_playlist(self):
        logging.debug("Adding videos to playlist via button")
        files = filedialog.askopenfilenames(filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm *.gif *.mpeg *.mpg")])
        if files:
            self.playlist.extend(files)
            self.update_playlist_listbox()
            logging.debug("Added to playlist via button: %s", files)

    def remove_from_playlist(self):
        logging.debug("Removing selected item from playlist")
        selection = self.playlist_listbox.curselection()
        if selection:
            index = selection[0]
            removed_file = self.playlist.pop(index)
            self.update_playlist_listbox()
            if self.current_playlist_index > index:
                self.current_playlist_index -= 1
            elif self.current_playlist_index == index:
                self.current_playlist_index = -1
            logging.debug("Removed from playlist: %s", removed_file)

    def update_playlist_listbox(self):
        logging.debug("Updating playlist listbox")
        self.playlist_listbox.delete(0, tk.END)
        for file in self.playlist:
            self.playlist_listbox.insert(tk.END, os.path.basename(file))
        logging.debug("Updated playlist listbox: %s", [os.path.basename(f) for f in self.playlist])

    def on_video_end(self, event):
        logging.debug("Video ended event triggered for %s", self.current_file)
        self.play_next_video()
        self.update_toggle_button()  # Update button state

    def play_next_video(self):
        logging.debug("Playing next video in mode: %s", self.next_mode)
        if not self.current_file and self.playlist:
            self.load_first_video()
            return
        try:
            next_file = None
            if self.next_mode == "random_repo" and self.current_file:
                repo_dir = os.path.dirname(self.current_file)
                all_files = [f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')) and os.path.isfile(os.path.join(repo_dir, f))]
                if all_files:
                    all_files.sort()
                    next_file = os.path.join(repo_dir, random.choice(all_files))
            elif self.next_mode == "playlist" and self.playlist:
                self.current_playlist_index = (self.current_playlist_index + 1) % len(self.playlist)
                next_file = self.playlist[self.current_playlist_index]
            elif self.next_mode == "next_repo" and self.current_file:
                repo_dir = os.path.dirname(self.current_file)
                all_files = [f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')) and os.path.isfile(os.path.join(repo_dir, f))]
                if all_files:
                    all_files.sort()
                    current_idx = all_files.index(os.path.basename(self.current_file)) if os.path.basename(self.current_file) in all_files else -1
                    next_idx = (current_idx + 1) % len(all_files) if current_idx != -1 else 0
                    next_file = os.path.join(repo_dir, all_files[next_idx])
            elif self.next_mode == "random_playlist" and self.playlist:
                self.current_playlist_index = random.randrange(len(self.playlist))
                next_file = self.playlist[self.current_playlist_index]

            if next_file and os.path.isfile(next_file):
                logging.debug("Next file selected: %s", next_file)
                self.load_video(next_file)
                self.update_toggle_button()  # Update button state
            else:
                logging.warning("No valid next file found for mode %s", self.next_mode)
                self.current_file = None
                self.update_title_label()
                self.player.stop()
                self.update_toggle_button()  # Update button state
        except Exception as e:
            logging.error("Error in play_next_video: %s", str(e))
            self.current_file = None
            self.update_title_label()
            self.reset_vlc()

    def play_previous_video(self):
        logging.debug("Playing previous video in mode: %s", self.next_mode)
        if not self.current_file and self.playlist:
            self.load_first_video()
            return
        try:
            prev_file = None
            if self.next_mode == "random_repo" and self.current_file:
                repo_dir = os.path.dirname(self.current_file)
                all_files = [f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')) and os.path.isfile(os.path.join(repo_dir, f))]
                if all_files:
                    all_files.sort()
                    prev_file = os.path.join(repo_dir, random.choice(all_files))
            elif self.next_mode == "playlist" and self.playlist:
                self.current_playlist_index = (self.current_playlist_index - 1) % len(self.playlist)
                prev_file = self.playlist[self.current_playlist_index]
            elif self.next_mode == "next_repo" and self.current_file:
                repo_dir = os.path.dirname(self.current_file)
                all_files = [f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')) and os.path.isfile(os.path.join(repo_dir, f))]
                if all_files:
                    all_files.sort()
                    current_idx = all_files.index(os.path.basename(self.current_file)) if os.path.basename(self.current_file) in all_files else -1
                    prev_idx = (current_idx - 1) % len(all_files) if current_idx > 0 else len(all_files) - 1
                    prev_file = os.path.join(repo_dir, all_files[prev_idx])
            elif self.next_mode == "random_playlist" and self.playlist:
                self.current_playlist_index = random.randrange(len(self.playlist))
                prev_file = self.playlist[self.current_playlist_index]

            if prev_file and os.path.isfile(prev_file):
                self.load_video(prev_file)
                self.update_toggle_button()  # Update button state
            else:
                logging.warning("No valid previous file found for mode %s", self.next_mode)
                self.current_file = None
                self.update_title_label()
                self.player.stop()
                self.update_toggle_button()  # Update button state
        except Exception as e:
            logging.error("Error in play_previous_video: %s", str(e))
            self.current_file = None
            self.update_title_label()
            self.reset_vlc()

class VideoPlayerApp:
    def __init__(self, root):
        logging.debug("Initializing VideoPlayerApp")
        self.root = root
        self.root.title("Ultiplay")
        self.root.geometry("1200x800")
        self.players = []
        self.is_fullscreen = False
        self.is_maximized = False
        self.first_smm_entry = True
        self.player_positions = []
        self.pre_smm_positions = []  # Ensure this is initialized
        self.swap_first = None
        self.position_map = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
        self.swap_history = []
        self.last_resize_time = 0
        self.last_selected_player = None

        self.canvas = tk.Canvas(self.root)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.background_image = None
        self.background_photo = None

        self.bg_file_path = self.create_default_background()
        self.update_background(None)

        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Add Player", command=self.add_player)
        self.context_menu.add_command(label="Set Background", command=self.set_background)
        self.context_menu.add_command(label="Save Playlists", command=self.save_playlists)
        self.context_menu.add_command(label="Load Playlists", command=self.load_playlists)
        self.canvas.bind("<Button-3>", self.show_context_menu)

        self.root.bind("<Configure>", self.handle_resize)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("f", self.toggle_maximize_screen)
        self.root.bind("b", self.raise_screen_5)
        self.root.bind("<Escape>", self.exit_fullscreen)
        self.root.bind("<space>", self.toggle_play_pause)
        self.root.bind("n", self.trigger_next_for_selected)
        self._bind_number_keys()
        self.root.update_idletasks()

    def create_default_background(self):
        """Create a default background image with key input instructions."""
        width, height = 1200, 800
        bg_color = (50, 50, 50)
        text_color = (255, 255, 255)
        image = Image.new("RGB", (width, height), bg_color)
        draw = ImageDraw.Draw(image)
        try:
            font_large = ImageFont.truetype("arial.ttf", 36)
            font_small = ImageFont.truetype("arial.ttf", 24)
        except:
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

        instructions = [
            ("F: Toggle Screen Maximize Mode (SMM)", font_large, 50),
            ("F11: Toggle Fullscreen Manipulation Mode", font_small, 120),
            ("Esc: Exit Fullscreen or SMM", font_small, 160),
            ("Space: Play/Pause All Players", font_small, 200),
            ("1-5: Select Player for Swap or Next (in SMM)", font_small, 240),
            ("N: Next Video for Selected Player (in SMM)", font_small, 280),
            ("B: Raise Screen 5 (in SMM with 5+ players)", font_small, 320),
            ("Right-Click: Open Context Menu (Add Player, etc.)", font_small, 360)
        ]

        for text, font, y in instructions:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            x = (width - text_width) // 2
            draw.text((x, y), text, fill=text_color, font=font)

        temp_path = os.path.join(os.path.dirname(log_path), "default_background.png")
        image.save(temp_path)
        logging.debug("Created default background at %s", temp_path)
        return temp_path

    def handle_resize(self, event):
        current_time = time.time()
        if current_time - self.last_resize_time < 0.1 or self.is_maximized or self.is_fullscreen:
            return
        self.last_resize_time = current_time
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        logging.debug("Canvas resized to w=%d, h=%d", canvas_width, canvas_height)
        self.update_background(event)

    def _bind_number_keys(self):
        logging.debug("Binding number keys for player selection")
        for key in range(1, 6):
            self.root.unbind(str(key))
        for key, index in enumerate(self.players, 1):
            self.root.bind(str(key), lambda e, idx=key-1: self._select_player(idx))

    def raise_screen_5(self, event=None):
        logging.debug("Raising Screen 5")
        if len(self.players) < 5:
            logging.info("Need 5 players to raise Screen 5.")
            return
        screen_5 = self.players[4]
        for player in self.players[:4]:
            self.canvas.tag_lower(player.frame_id)
            player.video_frame.lower()
        self.canvas.tag_raise(screen_5.frame_id)
        screen_5.frame.lift()
        screen_5.video_frame.tkraise()
        self.canvas.update_idletasks()

    def _render_quadrants(self):
        logging.debug("Rendering quadrants layout")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        quadrant_width = screen_width // 2
        quadrant_height = screen_height // 2
        quadrant_positions = [
            (0, 0),
            (quadrant_width, 0),
            (0, quadrant_height),
            (quadrant_width, quadrant_height)
        ]

        num_quadrants = min(4, len(self.players))
        logging.debug("Rendering %d quadrants", num_quadrants)
        for i, player in enumerate(self.players[:num_quadrants]):
            x, y = quadrant_positions[i]
            self.canvas.coords(player.frame_id, x, y)
            self.canvas.itemconfig(player.frame_id, width=quadrant_width, height=quadrant_height)
            if not self.is_maximized and not self.is_fullscreen:
                self.player_positions[i] = {'x': x, 'y': y, 'width': quadrant_width, 'height': quadrant_height}
            player.player.set_hwnd(player.video_frame.winfo_id())
            player.drag_handle.pack_forget()
            player.resize_handle.pack(side=tk.RIGHT, padx=2)
            player.resize_handle.unbind("<Button-1>")
            player.resize_handle.unbind("<B1-Motion>")
            slider_length = int(quadrant_width * 0.75)
            player.seek_bar.config(length=max(100, slider_length - 100))
            player.volume_bar.config(length=min(100, int(slider_length * 0.25)))
            if player.controls_visible:
                player.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                player.video_frame.lift()
                self.root.after(50, lambda p=player: p.refresh_vlc())
            # Updated for toggle_button
            if i == 1:
                player.seek_bar.pack_forget()
                player.time_label.pack_forget()
                player.volume_bar.pack_forget()
                player.drag_handle.pack_forget()
                player.toggle_button.pack_forget()
                player.prev_button.pack_forget()
                player.next_button.pack_forget()
                player.mode_button.pack_forget()
                player.playlist_button.pack_forget()
                player.close_button.pack_forget()
                player.resize_handle.pack_forget()
                player.title_label.pack_forget()
                player.seek_bar.pack(side=tk.RIGHT, padx=2)
                player.time_label.pack(side=tk.RIGHT, padx=2)
                player.volume_bar.pack(side=tk.RIGHT, padx=2)
                player.resize_handle.pack(side=tk.RIGHT, padx=2)
                player.close_button.pack(side=tk.RIGHT, padx=1)
                player.playlist_button.pack(side=tk.RIGHT, padx=1)
                player.mode_button.pack(side=tk.RIGHT, padx=1)
                player.next_button.pack(side=tk.RIGHT, padx=1)
                player.prev_button.pack(side=tk.RIGHT, padx=1)
                player.toggle_button.pack(side=tk.RIGHT, padx=1)
                player.drag_handle.pack(side=tk.RIGHT, padx=2)
                player.title_label.pack(side=tk.LEFT, padx=5)
            else:
                player.seek_bar.pack_forget()
                player.time_label.pack_forget()
                player.volume_bar.pack_forget()
                player.drag_handle.pack_forget()
                player.toggle_button.pack_forget()
                player.prev_button.pack_forget()
                player.next_button.pack_forget()
                player.mode_button.pack_forget()
                player.playlist_button.pack_forget()
                player.close_button.pack_forget()
                player.resize_handle.pack_forget()
                player.title_label.pack_forget()
                player.seek_bar.pack(side=tk.LEFT, padx=2)
                player.time_label.pack(side=tk.LEFT, padx=2)
                player.volume_bar.pack(side=tk.LEFT, padx=2)
                player.drag_handle.pack(side=tk.LEFT, padx=2)
                player.toggle_button.pack(side=tk.LEFT, padx=1)
                player.prev_button.pack(side=tk.LEFT, padx=1)
                player.next_button.pack(side=tk.LEFT, padx=1)
                player.mode_button.pack(side=tk.LEFT, padx=1)
                player.playlist_button.pack(side=tk.LEFT, padx=1)
                player.close_button.pack(side=tk.LEFT, padx=1)
                player.resize_handle.pack(side=tk.RIGHT, padx=2)
                player.title_label.pack(side=tk.LEFT, padx=5)

        if len(self.players) >= 5:
            screen_5 = self.players[4]
            self.canvas.coords(screen_5.frame_id, -1000, -1000)
            self.canvas.itemconfig(screen_5.frame_id, width=320, height=200)
            if not self.is_maximized and not self.is_fullscreen:
                self.player_positions[4] = {'x': -1000, 'y': -1000, 'width': 320, 'height': 200}
            screen_5.player.set_hwnd(screen_5.video_frame.winfo_id())
            screen_5.drag_handle.pack_forget()
            screen_5.resize_handle.pack(side=tk.RIGHT, padx=2)
            screen_5.resize_handle.unbind("<Button-1>")
            screen_5.resize_handle.unbind("<B1-Motion>")
            screen_5.seek_bar.config(length=100)
            screen_5.volume_bar.config(length=50)
            if screen_5.controls_visible:
                screen_5.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                screen_5.video_frame.lift()
                self.root.after(50, lambda p=screen_5: p.refresh_vlc())

        self.canvas.update_idletasks()
        logging.debug("Rendered quadrants - stack: %s", self.canvas.find_all())

    def _render_screen_5(self):
        if len(self.players) < 5:
            return
        logging.debug("Rendering Screen 5 layout")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        center_width = (screen_width // 2) // 3
        center_height = (screen_height // 2) // 3
        center_x = (screen_width // 2) - (center_width // 2)
        center_y = (screen_height // 2) - (center_height // 2)

        screen_5 = self.players[4]
        self.canvas.coords(screen_5.frame_id, center_x, center_y)
        self.canvas.itemconfig(screen_5.frame_id, width=center_width, height=center_height)
        if not self.is_maximized and not self.is_fullscreen:
            self.player_positions[4] = {'x': center_x, 'y': center_y, 'width': center_width, 'height': center_height}
        for player in self.players[:4]:
            self.canvas.tag_lower(player.frame_id)
            player.video_frame.lower()
        self.canvas.tag_raise(screen_5.frame_id)
        screen_5.frame.lift()
        screen_5.video_frame.tkraise()
        screen_5.seek_bar.config(length=100)
        screen_5.volume_bar.config(length=50)
        if screen_5.controls_visible:
            screen_5.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            screen_5.video_frame.lift()
            self.root.after(50, lambda p=screen_5: p.refresh_vlc())
        logging.debug("Rendered Screen 5 - frame_id: %d, coords: (%d,%d)", screen_5.frame_id, center_x, center_y)

    def _finalize_render(self):
        logging.debug("Finalizing render layout")
        self.root.after(50, self._render_quadrants)
        if len(self.players) >= 5:
            self.root.after(3000, self._render_screen_5)

    def _select_player(self, index):
        logging.debug("Selecting player %d", index + 1)
        if not (0 <= index < len(self.players)):
            logging.info("Invalid index %d for %d players.", index, len(self.players))
            return
        if self.swap_first is None:
            self.swap_first = index
            self.last_selected_player = index
            max_keys = len(self.players) if len(self.players) > 4 else 4
            logging.info("Selected player %d as first for swap or 'n' action.", index + 1)
            return
        if self.swap_first == index:
            logging.info("Cannot swap a player with itself.")
            self.swap_first = None
            return

        player1_index = self.swap_first
        player2_index = index
        logging.debug("Attempting swap: player %d with %d", player1_index + 1, player2_index + 1)

        try:
            if len(self.players) <= 4 and self.is_maximized:
                # SMM swap for ≤4 players
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                if len(self.players) == 4:
                    half_width = screen_width // 2
                    half_height = screen_height // 2
                    positions = [
                        (0, 0, half_width, half_height),
                        (half_width, 0, half_width, half_height),
                        (0, half_height, half_width, half_height),
                        (half_width, half_height, half_width, half_height)
                    ]
                elif len(self.players) == 3:
                    top_height = int(screen_height * 0.6)
                    bottom_height = screen_height - top_height
                    half_width = screen_width // 2
                    positions = [
                        (0, 0, screen_width, top_height),
                        (0, top_height, half_width, bottom_height),
                        (half_width, top_height, half_width, bottom_height)
                    ]
                else:
                    positions = [
                        (0, 0, screen_width, screen_height // 2),
                        (0, screen_height // 2, screen_width, screen_height // 2)
                    ]

                states = {}
                for i in range(len(self.players)):
                    player = self.players[i]
                    was_playing = player.player.is_playing()
                    state = player.player.get_state()
                    states[i] = {
                        'position': player.player.get_time(),
                        'playing': was_playing and state == vlc.State.Playing
                    }
                    logging.debug("Player %d pre-swap: pos=%d, playing=%s", i + 1, states[i]['position'], states[i]['playing'])

                player1 = self.players[player1_index]
                player2 = self.players[player2_index]
                pos1 = positions[player1_index]
                pos2 = positions[player2_index]

                self.canvas.coords(player1.frame_id, pos2[0], pos2[1])
                self.canvas.itemconfig(player1.frame_id, width=pos2[2], height=pos2[3])
                self.player_positions[player1_index] = {'x': pos2[0], 'y': pos2[1], 'width': pos2[2], 'height': pos2[3]}
                self.canvas.coords(player2.frame_id, pos1[0], pos1[1])
                self.canvas.itemconfig(player2.frame_id, width=pos1[2], height=pos1[3])
                self.player_positions[player2_index] = {'x': pos1[0], 'y': pos1[1], 'width': pos1[2], 'height': pos1[3]}
                self.players[player1_index], self.players[player2_index] = player2, player1

                for player in (self.players[player1_index], self.players[player2_index]):
                    player.player.set_hwnd(player.video_frame.winfo_id())
                    slider_length = int(self.player_positions[player1_index if player == self.players[player1_index] else player2_index]['width'] * 0.75)
                    player.seek_bar.config(length=max(100, slider_length - 100))
                    player.volume_bar.config(length=min(100, int(slider_length * 0.25)))
                    if player.controls_visible:
                        player.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                        player.video_frame.lift()

                self.canvas.update_idletasks()
                self.root.after(200, lambda: (
                    self.players[player1_index].player.set_time(states[player2_index]['position']),
                    self.players[player2_index].player.set_time(states[player1_index]['position']),
                    self.players[player1_index].player.play() if states[player2_index]['playing'] else self.players[player1_index].player.pause(),
                    self.players[player2_index].player.play() if states[player1_index]['playing'] else self.players[player2_index].player.pause()
                ))
            else:
                # Swap for 5+ players or non-SMM
                states = {}
                for i in (player1_index, player2_index):
                    player = self.players[i]
                    was_playing = player.player.is_playing()
                    state = player.player.get_state()
                    states[i] = {
                        'file': player.current_file,
                        'playlist': player.playlist[:],
                        'playlist_index': player.current_playlist_index,
                        'mode': player.next_mode,
                        'playing': was_playing and state == vlc.State.Playing,
                        'position': player.player.get_time()
                    }
                    logging.debug("Player %d state before swap: playing=%s, position=%d", i + 1, states[i]['playing'], states[i]['position'])

                self.players[player1_index], self.players[player2_index] = self.players[player2_index], self.players[player1_index]
                for i, src_i in [(player1_index, player2_index), (player2_index, player1_index)]:
                    player = self.players[i]
                    player.current_file = states[src_i]['file']
                    player.playlist = states[src_i]['playlist']
                    player.current_playlist_index = states[src_i]['playlist_index']
                    player.next_mode = states[src_i]['mode']
                    player.update_mode_button_text()
                    player.update_title_label()
                    player.player.set_hwnd(player.video_frame.winfo_id())
                    player.player.set_time(states[src_i]['position'])
                    if states[src_i]['playing']:
                        player.player.play()
                    elif player.player.get_state() == vlc.State.Playing:
                        player.player.pause()
                    slider_length = int(self.player_positions[i]['width'] * 0.75)
                    player.seek_bar.config(length=max(100, slider_length - 100))
                    player.volume_bar.config(length=min(100, int(slider_length * 0.25)))
                    if player.controls_visible:
                        player.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                        player.video_frame.lift()
                        self.root.after(50, lambda p=player: p.refresh_vlc())

                if self.is_maximized:
                    self._finalize_render()  # Re-render layout after swap

            self.canvas.update_idletasks()
            logging.debug("After swap - Player list: %s", [p.current_file for p in self.players])
            self.swap_history.append((player1_index + 1, player2_index + 1))
            if len(self.swap_history) > 5:
                self.swap_history.pop(0)
            self._bind_number_keys()
        except Exception as e:
            logging.error("Error during swap: %s", str(e))
        finally:
            self.swap_first = None

    def trigger_next_for_selected(self, event):
        logging.debug("Triggering 'n' key action")
        if not self.is_maximized:
            return
        if self.last_selected_player is None:
            logging.info("No player selected.")
            return
        if 0 <= self.last_selected_player < len(self.players):
            self.players[self.last_selected_player].play_next_video()
            self.last_selected_player = None
            self.swap_first = None

    def add_player(self):
        logging.debug("Adding new player at last right-click position")
        x = self.last_click_x if hasattr(self, 'last_click_x') else 50
        y = self.last_click_y if hasattr(self, 'last_click_y') else 50
        player = PlayerWidget(self.root, self, self.canvas, x, y)
        self.players.append(player)
        frame_id = player.frame_id
        px, py = self.canvas.coords(frame_id)
        pwidth = player.frame.winfo_width()
        pheight = player.frame.winfo_height()
        self.player_positions.append({'x': px, 'y': py, 'width': pwidth, 'height': pheight})
        if not self.is_maximized:  # Only update pre_smm_positions if not in SMM
            self.pre_smm_positions.append({'x': px, 'y': py, 'width': pwidth, 'height': pheight})
        self._bind_number_keys()

    def toggle_fullscreen(self, event=None):
        logging.debug("Toggling fullscreen: %s", not self.is_fullscreen)
        self.is_fullscreen = not self.is_fullscreen
        self.root.attributes('-fullscreen', self.is_fullscreen)
        if not self.is_fullscreen:
            self.is_maximized = False
            self.root.geometry("1200x800")
        self.canvas.update_idletasks()

    def exit_fullscreen(self, event=None):
        logging.debug("Exiting fullscreen with Esc")
        if self.is_fullscreen or self.is_maximized:
            self.is_fullscreen = False
            self.is_maximized = False
            self.root.attributes('-fullscreen', False)
            self.root.geometry("1200x800")
            canvas_width = 1200
            canvas_height = 800

            # Redraw players to pre-SMM positions and sizes
            if hasattr(self, 'pre_smm_positions') and self.pre_smm_positions:
                logging.debug("Restoring pre-SMM positions: %s", self.pre_smm_positions)
                for i, player in enumerate(self.players):
                    if not self.canvas.tk.call('winfo', 'exists', player.frame_id):
                        continue
                    if i < len(self.pre_smm_positions):
                        pos = self.pre_smm_positions[i]
                    else:
                        pos = {'x': 50 + i * 50, 'y': 50 + i * 50, 'width': 400, 'height': 300}
                    new_x = max(0, min(pos['x'], canvas_width - pos['width']))
                    new_y = max(0, min(pos['y'], canvas_height - pos['height']))
                    new_width = min(pos['width'], canvas_width - new_x)
                    new_height = min(pos['height'], canvas_height - new_y)
                    
                    # Reset SMM-specific layout changes
                    player.drag_handle.pack_forget()
                    player.resize_handle.pack_forget()
                    if player.controls_visible:
                        player.controls_frame.pack_forget()
                    
                    # Update canvas coordinates and size
                    self.canvas.coords(player.frame_id, new_x, new_y)
                    self.canvas.itemconfig(player.frame_id, width=new_width, height=new_height)
                    self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                    logging.debug("Player %d redrawn to x=%d, y=%d, width=%d, height=%d", 
                                i+1, new_x, new_y, new_width, new_height)
                    
                    # Reset control lengths and bindings
                    slider_length = int(new_width * 0.75)
                    player.seek_bar.config(length=max(100, slider_length - 100))
                    player.volume_bar.config(length=min(100, int(slider_length * 0.25)))
                    
                    # Repack all widgets as in __init__ (non-SMM layout)
                    player.seek_bar.pack_forget()
                    player.time_label.pack_forget()
                    player.volume_bar.pack_forget()
                    player.drag_handle.pack_forget()
                    player.toggle_button.pack_forget()
                    player.prev_button.pack_forget()
                    player.next_button.pack_forget()
                    player.mode_button.pack_forget()
                    player.playlist_button.pack_forget()
                    player.close_button.pack_forget()
                    player.resize_handle.pack_forget()
                    player.title_label.pack_forget()
                    player.seek_bar.pack(side=tk.LEFT, padx=2)
                    player.time_label.pack(side=tk.LEFT, padx=2)
                    player.volume_bar.pack(side=tk.LEFT, padx=2)
                    player.drag_handle.pack(side=tk.LEFT, padx=2)
                    player.toggle_button.pack(side=tk.LEFT, padx=1)
                    player.prev_button.pack(side=tk.LEFT, padx=1)
                    player.next_button.pack(side=tk.LEFT, padx=1)
                    player.mode_button.pack(side=tk.LEFT, padx=1)
                    player.playlist_button.pack(side=tk.LEFT, padx=1)
                    player.close_button.pack(side=tk.LEFT, padx=1)
                    player.resize_handle.pack(side=tk.RIGHT, padx=2)
                    player.title_label.pack(side=tk.LEFT, padx=5)
                    
                    # Restore drag/resize bindings
                    player.drag_handle.bind("<Button-1>", player.start_drag)
                    player.drag_handle.bind("<B1-Motion>", player.drag)
                    player.drag_handle.bind("<ButtonRelease-1>", player.stop_drag)
                    player.resize_handle.bind("<Button-1>", player.start_resize)
                    player.resize_handle.bind("<B1-Motion>", player.resize)
                    
                    # Repack controls and refresh VLC
                    if player.controls_visible:
                        player.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                        player.video_frame.lift()
                    player.player.set_hwnd(player.video_frame.winfo_id())
                    self.root.after(50, lambda p=player: p.refresh_vlc())
                
                # Force canvas and window update
                self.canvas.update()
                self.root.update()
            else:
                # Fallback: stack players if no pre-SMM data
                logging.debug("No pre-SMM positions available, stacking players")
                y_offset = 0
                for i, player in enumerate(self.players):
                    if not self.canvas.tk.call('winfo', 'exists', player.frame_id):
                        continue
                    new_x = 0
                    new_y = y_offset
                    new_width = min(400, canvas_width)
                    new_height = min(300, canvas_height - y_offset)
                    self.canvas.coords(player.frame_id, new_x, new_y)
                    self.canvas.itemconfig(player.frame_id, width=new_width, height=new_height)
                    self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                    logging.debug("Player %d stacked at x=%d, y=%d, width=%d, height=%d", 
                                i+1, new_x, new_y, new_width, new_height)
                    slider_length = int(new_width * 0.75)
                    player.seek_bar.config(length=max(100, slider_length - 100))
                    player.volume_bar.config(length=min(100, int(slider_length * 0.25)))
                    y_offset += new_height
                    
                    # Reset layout as above
                    player.seek_bar.pack_forget()
                    player.time_label.pack_forget()
                    player.volume_bar.pack_forget()
                    player.drag_handle.pack_forget()
                    player.toggle_button.pack_forget()
                    player.prev_button.pack_forget()
                    player.next_button.pack_forget()
                    player.mode_button.pack_forget()
                    player.playlist_button.pack_forget()
                    player.close_button.pack_forget()
                    player.resize_handle.pack_forget()
                    player.title_label.pack_forget()
                    player.seek_bar.pack(side=tk.LEFT, padx=2)
                    player.time_label.pack(side=tk.LEFT, padx=2)
                    player.volume_bar.pack(side=tk.LEFT, padx=2)
                    player.drag_handle.pack(side=tk.LEFT, padx=2)
                    player.toggle_button.pack(side=tk.LEFT, padx=1)
                    player.prev_button.pack(side=tk.LEFT, padx=1)
                    player.next_button.pack(side=tk.LEFT, padx=1)
                    player.mode_button.pack(side=tk.LEFT, padx=1)
                    player.playlist_button.pack(side=tk.LEFT, padx=1)
                    player.close_button.pack(side=tk.LEFT, padx=1)
                    player.resize_handle.pack(side=tk.RIGHT, padx=2)
                    player.title_label.pack(side=tk.LEFT, padx=5)
                    
                    player.drag_handle.bind("<Button-1>", player.start_drag)
                    player.drag_handle.bind("<B1-Motion>", player.drag)
                    player.drag_handle.bind("<ButtonRelease-1>", player.stop_drag)
                    player.resize_handle.bind("<Button-1>", player.start_resize)
                    player.resize_handle.bind("<B1-Motion>", player.resize)
                    if player.controls_visible:
                        player.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                        player.video_frame.lift()
                    player.player.set_hwnd(player.video_frame.winfo_id())
                    self.root.after(50, lambda p=player: p.refresh_vlc())
                
                self.canvas.update()
                self.root.update()

            self.swap_first = None
            self.position_map = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
            self.swap_history = []
            self.canvas.update_idletasks()
            logging.debug("Exited fullscreen to %dx%d with players fully redrawn", canvas_width, canvas_height)

    def toggle_maximize_screen(self, event=None):
        logging.debug("Toggling maximize screen: %s", not self.is_maximized)
        if not self.is_maximized:
            self.root.attributes('-fullscreen', True)
            self.is_maximized = True
            if not self.is_fullscreen:
                self.pre_smm_positions = [pos.copy() for pos in self.player_positions]
                logging.debug("Stored pre-SMM positions: %s", self.pre_smm_positions)
            self.is_fullscreen = True
            self._finalize_render()
            self._bind_number_keys()
        else:
            self.is_maximized = False
            self.is_fullscreen = False
            self.root.attributes('-fullscreen', False)
            self.root.geometry("1200x800")
            for player in self.players:
                player.drag_handle.pack(side=tk.LEFT, padx=2)
                player.drag_handle.bind("<Button-1>", player.start_drag)
                player.drag_handle.bind("<B1-Motion>", player.drag)
                player.drag_handle.bind("<ButtonRelease-1>", player.stop_drag)
                player.resize_handle.pack(side=tk.RIGHT, padx=2)
                player.resize_handle.bind("<Button-1>", player.start_resize)
                player.resize_handle.bind("<B1-Motion>", player.resize)
            self.swap_first = None
            self.position_map = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
            self.swap_history = []
            self.canvas.update_idletasks()

    def toggle_play_pause(self, event=None):
        logging.debug("Toggling play/pause for all players universally")
        any_playing = any(player.player.is_playing() for player in self.players)
        if any_playing:
            for player in self.players:
                player.pause_video()
        else:
            for player in self.players:
                player.play_video()
        if self.is_maximized and len(self.players) >= 5:
            self._finalize_render()
        self.canvas.update_idletasks()

    def update_background(self, event=None):
        logging.debug("Updating background image")
        if self.bg_file_path and os.path.exists(self.bg_file_path):
            win_width = self.canvas.winfo_width()
            win_height = self.canvas.winfo_height()
            if win_width > 1 and win_height > 1:
                try:
                    image = Image.open(self.bg_file_path)
                    scale = max(win_width / image.width, win_height / image.height)
                    new_width = int(image.width * scale)
                    new_height = int(image.height * scale)
                    image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                    self.background_photo = ImageTk.PhotoImage(image)
                    if self.background_image:
                        self.canvas.delete(self.background_image)
                    self.background_image = self.canvas.create_image(
                        win_width // 2, win_height // 2, 
                        image=self.background_photo, anchor="center"
                    )
                    self.canvas.lower(self.background_image)
                    for player in self.players:
                        if self.canvas.tk.call('winfo', 'exists', player.frame_id):
                            self.canvas.tag_raise(player.frame_id)
                    logging.debug("Background image updated successfully")
                except Exception as e:
                    logging.error(f"Failed to update background image: {e}")

    def show_context_menu(self, event):
        logging.debug("Showing context menu at x=%d, y=%d", event.x, event.y)
        self.last_click_x = event.x
        self.last_click_y = event.y
        self.context_menu.post(event.x_root, event.y_root)

    def set_background(self):
        logging.debug("Setting background image")
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif")])
        if file_path:
            self.bg_file_path = file_path
            self.update_background(None)

    def save_playlists(self):
        logging.debug("Saving playlists")
        file_path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON Files", "*.json")])
        if file_path:
            data = {
                "background": self.bg_file_path,
                "players": []
            }
            for i, player in enumerate(self.players):
                x, y = self.canvas.coords(player.frame_id)
                data["players"].append({
                    "index": i,
                    "x": x,
                    "y": y,
                    "width": player.frame.winfo_width(),
                    "height": player.frame.winfo_height(),
                    "current_file": player.current_file,
                    "mode": player.next_mode,
                    "playlist": player.playlist
                })
            with open(file_path, "w") as f:
                json.dump(data, f, indent=4)
            logging.debug("Saved %d players", len(data["players"]))

    def load_playlists(self):
        logging.debug("Loading playlists")
        file_path = filedialog.askopenfilename(filetypes=[("JSON Files", "*.json")])
        if file_path:
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)
                
                self.players.clear()
                self.player_positions.clear()
                self.pre_smm_positions.clear()  # Clear pre-SMM positions too

                if isinstance(data, dict) and "players" in data:
                    bg_path = data.get("background")
                    if bg_path and os.path.isfile(bg_path):
                        self.bg_file_path = bg_path
                        self.update_background(None)
                    player_data = data["players"]
                else:
                    logging.warning("Invalid playlist format")
                    return

                for p_data in player_data:
                    x = p_data.get("x", 50)
                    y = p_data.get("y", 50)
                    width = p_data.get("width", 400)
                    height = p_data.get("height", 300)
                    current_file = p_data.get("current_file")
                    mode = p_data.get("mode", "random_repo")
                    playlist = p_data.get("playlist", [])

                    player = PlayerWidget(self.root, self, self.canvas, x, y, width, height, playlist)
                    self.players.append(player)
                    player.next_mode = mode
                    player.update_mode_button_text()
                    frame_id = player.frame_id
                    px, py = self.canvas.coords(frame_id)
                    pwidth = player.frame.winfo_width()
                    pheight = player.frame.winfo_height()
                    self.player_positions.append({'x': px, 'y': py, 'width': pwidth, 'height': pheight})
                    self.pre_smm_positions.append({'x': px, 'y': py, 'width': pwidth, 'height': pheight})  # Store initial state

                    if current_file and os.path.isfile(current_file):
                        player.load_video(current_file)

                self.root.update_idletasks()
                self._bind_number_keys()
                logging.debug("Loaded %d players with pre-SMM positions: %s", len(self.players), self.pre_smm_positions)
            except Exception as e:
                logging.error("Failed to load playlists: %s", str(e))

if __name__ == "__main__":
    logging.debug("Launching main application")
    try:
        root = TkinterDnD.Tk()
        # Set the window icon
        icon_path = r"C:\Users\march\OneDrive\Desktop\Ultiplay\appicon.ico"
        if os.path.exists(icon_path):
            root.iconbitmap(icon_path)  # Set the icon for the Tkinter window
            logging.debug("Set window icon to %s", icon_path)
        else:
            logging.warning("Icon file not found at %s", icon_path)
        
        app = VideoPlayerApp(root)
        root.protocol("WM_DELETE_WINDOW", lambda: [root.quit(), root.destroy()])  # Ensure clean exit
        root.mainloop()
    except Exception as e:
        logging.error("Failed to launch application: %s", str(e))
        sys.exit(1)