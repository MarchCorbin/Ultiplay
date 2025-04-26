import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
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
from tkinter import ttk
from collections import deque
import win32gui
import win32con

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
        logging.debug("Initializing player widget at x=%d, y=%d, player count=%d", x, y, len(app.players))
        self.parent = parent
        self.app = app
        self.canvas = canvas
        self.is_swapping = False
        self.index = len(app.players)
        self.layer = self.index  # Initialize layer as the creation order (0-based index)
        logging.debug("Initialized Player %d with layer %d", self.index + 1, self.layer)
        self.is_fullscreen = False  # Track fullscreen state
        self.pre_fullscreen_coords = None  # Store pre-fullscreen position and size
        self.was_in_smm = False

        # VLC Initialization with OpenGL output
        try:
            vlc_args = ['--quiet', '--no-video-title-show', '--aout=directx', '--vout=gl', '--verbose', '0']
            if getattr(sys, 'frozen', False):
                vlc_path = os.path.join(sys._MEIPASS, 'vlc') if hasattr(sys, '_MEIPASS') else os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'vlc')
                if os.path.exists(vlc_path):
                    os.environ['VLC_PLUGIN_PATH'] = vlc_path
                    os.environ['PATH'] = os.environ.get('PATH', '') + ';' + vlc_path
                    logging.debug("Using bundled VLC path: %s", vlc_path)
                else:
                    logging.error("VLC path not found: %s", vlc_path)
                    raise FileNotFoundError(f"VLC path not found: {vlc_path}")
                self.instance = vlc.Instance(vlc_args)
            else:
                self.instance = vlc.Instance(vlc_args)

            if self.instance is None:
                logging.error("VLC Instance is None - cannot initialize VLC")
                raise ValueError("Failed to initialize VLC Instance")
            self.player = self.instance.media_player_new()
            logging.debug("VLC initialized successfully for frame_id: %d", id(self))
        except Exception as e:
            logging.error(f"VLC initialization failed: {e}")
            raise

        # Frame setup
        self.frame = tk.Frame(self.canvas, bg="black", borderwidth=2, relief="raised")
        self.frame_id = self.canvas.create_window(x, y, window=self.frame, width=width, height=height, anchor="nw")
        logging.debug("Attached player to canvas with frame_id: %d", self.frame_id)

        self.video_frame = tk.Frame(self.frame, bg="black")
        self.video_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Ensure widget is fully realized before registering drop target and setting HWND
        self.frame.update_idletasks()
        self.hwnd = self.video_frame.winfo_id()  # Store HWND for VLC
        logging.debug("Video frame HWND for Player %d: %d", self.index + 1, self.hwnd)
        self.player.set_hwnd(self.hwnd)  # Set VLC output to video_frame

        # Delay drop target registration to avoid invalid handle
        def register_drop_target():
            try:
                self.video_frame.drop_target_register(DND_FILES)
                self.video_frame.dnd_bind('<<Drop>>', self.drop_video)
                logging.debug("Drop target registered for Player %d", self.index + 1)
            except tk.TclError as e:
                logging.error("Failed to register drop target for Player %d: %s", self.index + 1, e)

        self.parent.after(100, register_drop_target)

        # Controls setup
        self.controls_frame = tk.Frame(self.frame, bg="gray")
        self.controls_visible = False
        self.hide_timeout = None

        self.slider_frame = tk.Frame(self.controls_frame, bg="gray")
        self.slider_frame.pack(side=tk.TOP, fill=tk.X)
        
        self.seek_bar = tk.Scale(self.slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                command=self.seek, showvalue=0, length=int(width * 0.75) - 100,
                                sliderlength=8,  # Narrow ticker (8 pixels) to match DJD
                                highlightbackground="green", troughcolor="darkgreen")
        self.seek_bar.in_use = False
        self.seek_bar.bind("<Button-1>", lambda e: setattr(self.seek_bar, 'in_use', True))
        self.seek_bar.bind("<ButtonRelease-1>", lambda e: setattr(self.seek_bar, 'in_use', False))
        self.seek_bar.bind("<Button-1>", self.seek_to_position)

        self.time_label = tk.Label(self.slider_frame, text="0:00 / 0:00", bg="gray", fg="white")
        
        self.volume_bar = tk.Scale(self.slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                           command=self.set_volume, showvalue=0, length=int((width * 0.75) * 0.25),
                           sliderlength=8, highlightbackground="blue", troughcolor="darkblue")
        self.volume_bar.in_use = False
        self.volume_bar.bind("<Button-1>", self.jump_to_volume_position)  # Click-to-jump
        self.volume_bar.bind("<B1-Motion>", lambda e: setattr(self.volume_bar, 'in_use', True))  # Track dragging
        self.volume_bar.bind("<ButtonRelease-1>", lambda e: setattr(self.volume_bar, 'in_use', False))
        self.volume_bar.set(100)

        self.button_frame = tk.Frame(self.controls_frame, bg="gray")
        self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.drag_handle = tk.Label(self.button_frame, text="Move", bg="gray", fg="white", cursor="fleur")
        self.toggle_button = tk.Button(self.button_frame, text="▶", command=self.toggle_play_pause, width=3)
        self.toggle_button.bind("<Control-Button-1>", self.universal_toggle)
        self.prev_button = tk.Button(self.button_frame, text="<<", command=self.play_previous_video, width=3)
        self.prev_button.bind("<Control-Button-1>", self.universal_prev)
        self.next_button = tk.Button(self.button_frame, text=">>", command=self.play_next_video, width=3)
        self.next_button.bind("<Control-Button-1>", self.universal_next)
        self.mode_button = tk.Button(self.button_frame, text="Shuf", command=self.toggle_next_mode, width=4)
        self.playlist_button = tk.Button(self.button_frame, text="Play", command=self.edit_playlist, width=4)
        self.fullscreen_button = tk.Button(self.button_frame, text="⛶", command=self.toggle_fullscreen, width=3)  # Fullscreen button
        self.close_button = tk.Button(self.button_frame, text="X", command=self.close, width=3)
        self.resize_handle = tk.Label(self.button_frame, text="↘", bg="gray", fg="white", cursor="sizing")
        self.title_label = tk.Label(self.button_frame, text="No video loaded", bg="gray", fg="white")

        self.pack_controls()

        # Finalize VLC setup
        self.player.video_set_scale(0)  # Auto-scale video
        self.player.audio_set_volume(100)  # Ensure volume starts at 100
        self.intended_volume = 100  # Set initial intended volume
        self.parent.after(100, self._verify_volume)  # Verify after initialization
        logging.debug("Initialized Player %d volume to 100", self.index + 1)

        self.player.event_manager().event_attach(vlc.EventType.MediaPlayerEndReached, self.on_video_end)
        self.update_seek_bar()

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
        self.next_file = None  # New attribute to track "play next" selection
        self.next_mode = "random_repo" if not playlist else "playlist"
        self.playlist = playlist if playlist is not None else []
        self.current_playlist_index = -1
        self.update_mode_button_text()

        # Verify VLC attachment after initialization
        self.parent.after(200, self.refresh_vlc)
        self.next_playlist_index = None  # For random_playlist mode
        self.force_next_file = None  # New attribute to force next video regardless of mode

   
    def set_layer(self, layer):
        """Set the player's layer and redraw all players in layer order, preserving seek positions and visibility."""
        self.layer = layer
        try:
            player_states = []
            for player in self.app.players:
                try:
                    x, y = self.app.canvas.coords(player.frame_id)
                except tk.TclError:
                    logging.warning("Invalid frame_id for Player %d, using default coords (0, 0)", player.index + 1)
                    x, y = 0, 0
                width = player.frame.winfo_width()
                height = player.frame.winfo_height()
                current_time = player.player.get_time() if player.current_file else 0
                was_playing = player.player.is_playing() if player.current_file else False
                current_file = player.current_file
                player_states.append({
                    'player': player,
                    'x': x,
                    'y': y,
                    'width': width,
                    'height': height,
                    'current_time': current_time,
                    'was_playing': was_playing,
                    'layer': player.layer,
                    'current_file': current_file
                })
                if player.current_file:
                    player.player.stop()
                    player.player.set_hwnd(0)
                self.app.canvas.delete(player.frame_id)
                player.frame.destroy()

            sorted_states = sorted(player_states, key=lambda s: s['layer'])

            for state in sorted_states:
                player = state['player']
                player.frame = tk.Frame(self.app.canvas, bg="black", borderwidth=2, relief="raised")
                player.frame_id = self.app.canvas.create_window(
                    state['x'], state['y'], window=player.frame,
                    width=state['width'], height=state['height'], anchor="nw"
                )
                logging.debug("Recreated frame for Player %d at layer %d, frame_id: %d",
                            player.index + 1, player.layer, player.frame_id)

                player.video_frame = tk.Frame(player.frame, bg="black")
                player.video_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
                player.frame.update_idletasks()
                player.hwnd = player.video_frame.winfo_id()
                logging.debug("Recreated video frame HWND for Player %d: %d", player.index + 1, player.hwnd)

                player.controls_frame = tk.Frame(player.frame, bg="gray")
                player.slider_frame = tk.Frame(player.controls_frame, bg="gray")
                player.slider_frame.pack(side=tk.TOP, fill=tk.X)
                player.seek_bar = tk.Scale(
                    player.slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                    command=player.seek, showvalue=0, length=int(state['width'] * 0.75) - 100,
                    sliderlength=8, highlightbackground="green", troughcolor="darkgreen"
                )
                player.seek_bar.in_use = False
                player.seek_bar.bind("<Button-1>", lambda e: setattr(player.seek_bar, 'in_use', True))
                player.seek_bar.bind("<ButtonRelease-1>", lambda e: setattr(player.seek_bar, 'in_use', False))
                player.seek_bar.bind("<Button-1>", player.seek_to_position)
                player.time_label = tk.Label(player.slider_frame, text="0:00 / 0:00", bg="gray", fg="white")
                player.volume_bar = tk.Scale(
                    player.slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                    command=player.set_volume, showvalue=0, length=int((state['width'] * 0.75) * 0.25),
                    sliderlength=8, highlightbackground="blue", troughcolor="darkblue"
                )
                player.volume_bar.in_use = False
                player.volume_bar.bind("<Button-1>", player.jump_to_volume_position)
                player.volume_bar.bind("<B1-Motion>", lambda e: setattr(player.volume_bar, 'in_use', True))
                player.volume_bar.bind("<ButtonRelease-1>", lambda e: setattr(player.volume_bar, 'in_use', False))
                player.volume_bar.set(player.intended_volume if player.intended_volume is not None else 100)
                player.button_frame = tk.Frame(player.controls_frame, bg="gray")
                player.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
                player.drag_handle = tk.Label(player.button_frame, text="Move", bg="gray", fg="white", cursor="fleur")
                player.toggle_button = tk.Button(player.button_frame, text="▶", command=player.toggle_play_pause, width=3)
                player.toggle_button.bind("<Control-Button-1>", player.universal_toggle)
                player.prev_button = tk.Button(player.button_frame, text="<<", command=player.play_previous_video, width=3)
                player.prev_button.bind("<Control-Button-1>", player.universal_prev)
                player.next_button = tk.Button(player.button_frame, text=">>", command=player.play_next_video, width=3)
                player.next_button.bind("<Control-Button-1>", player.universal_next)
                player.mode_button = tk.Button(player.button_frame, text="Shuf", command=player.toggle_next_mode, width=4)
                player.playlist_button = tk.Button(player.button_frame, text="Play", command=player.edit_playlist, width=4)
                player.fullscreen_button = tk.Button(player.button_frame, text="⛶", command=player.toggle_fullscreen, width=3)
                player.close_button = tk.Button(player.button_frame, text="X", command=player.close, width=3)
                player.resize_handle = tk.Label(player.button_frame, text="↘", bg="gray", fg="white", cursor="sizing")
                player.title_label = tk.Label(
                    player.button_frame, text=os.path.basename(player.current_file) if player.current_file else "No video loaded",
                    bg="gray", fg="white"
                )
                player.pack_controls()

                player.frame.bind("<Enter>", player.show_controls)
                player.frame.bind("<Leave>", player.schedule_hide_controls)
                player.frame.bind("<Motion>", player.show_controls)
                player.drag_handle.bind("<Button-1>", player.start_drag)
                player.drag_handle.bind("<B1-Motion>", player.drag)
                player.drag_handle.bind("<ButtonRelease-1>", player.stop_drag)
                player.resize_handle.bind("<Button-1>", player.start_resize)
                player.resize_handle.bind("<B1-Motion>", player.resize)

                try:
                    player.video_frame.drop_target_register(DND_FILES)
                    player.video_frame.dnd_bind('<<Drop>>', player.drop_video)
                    logging.debug("Re-registered drop target for Player %d", player.index + 1)
                except tk.TclError as e:
                    logging.error("Failed to re-register drop target for Player %d: %s", player.index + 1, e)

            for i, state in enumerate(sorted_states):
                player = state['player']
                if state['current_file']:
                    def restore_state(p=player, s=state):
                        try:
                            if p.player.get_state() not in (vlc.State.Error, vlc.State.Ended):
                                p.player.set_hwnd(p.hwnd)
                                media = p.instance.media_new(s['current_file'])
                                p.player.set_media(media)
                                p.player.play()
                                p.player.set_time(s['current_time'])
                                if not s['was_playing']:
                                    p.player.pause()
                                    p.toggle_button.config(text="▶")
                                else:
                                    p.toggle_button.config(text="||")
                                p.update_title_label()
                                p.refresh_vlc()
                                logging.debug("Restored Player %d to %d ms, playing=%s",
                                            p.index + 1, s['current_time'], s['was_playing'])
                        except Exception as e:
                            logging.error("Failed to restore state for Player %d: %s", p.index + 1, e)
                            p.current_file = None
                            p.update_title_label()
                    self.parent.after(200 + i * 100, restore_state)

            self.app.canvas.update_idletasks()
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_dashboard()
            logging.debug("Set Player %d to layer %d, redrew all players", self.index + 1, self.layer)

        except Exception as e:
            logging.error("Failed to set layer for Player %d: %s", self.index + 1, e)
            sorted_players = sorted(self.app.players, key=lambda p: p.layer)
            for i, player in enumerate(sorted_players):
                try:
                    if i == 0:
                        self.app.canvas.tag_lower(player.frame_id)
                    else:
                        self.app.canvas.tag_raise(player.frame_id, sorted_players[i-1].frame_id)
                    logging.debug("Fallback: Set z-order for layer %d: Player %d, frame_id: %d",
                                player.layer, player.index + 1, player.frame_id)
                except tk.TclError:
                    logging.warning("Failed to adjust z-order for Player %d in fallback", player.index + 1)
            self.app.canvas.update_idletasks()
            logging.debug("Set Player %d to layer %d, fell back to Tkinter canvas stack", self.index + 1, self.layer)

    def pack_controls(self):
        """Dynamically pack controls based on SMM state and player index."""
        # Special case: Player 2 (index 1) in 5-player SMM layout, right-aligned
        if self.app.is_maximized and len(self.app.players) == 5 and self.index == 1:
            for widget in self.slider_frame.winfo_children() + self.button_frame.winfo_children():
                widget.pack_forget()
            self.slider_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.seek_bar.pack(side=tk.RIGHT, padx=2)
            self.time_label.pack(side=tk.RIGHT, padx=2)
            self.volume_bar.pack(side=tk.RIGHT, padx=2)
            self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.resize_handle.pack(side=tk.RIGHT, padx=2)
            self.close_button.pack(side=tk.RIGHT, padx=1)
            self.fullscreen_button.pack(side=tk.RIGHT, padx=1)
            self.playlist_button.pack(side=tk.RIGHT, padx=1)
            self.mode_button.pack(side=tk.RIGHT, padx=1)
            self.next_button.pack(side=tk.RIGHT, padx=1)
            self.prev_button.pack(side=tk.RIGHT, padx=1)
            self.toggle_button.pack(side=tk.RIGHT, padx=1)
            self.drag_handle.pack(side=tk.RIGHT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)  # Fill left space
        # Special case: Player 5 (index 4) in SMM with 5+ players, right-aligned
        elif self.app.is_maximized and len(self.app.players) >= 5 and self.index == 4:
            for widget in self.slider_frame.winfo_children() + self.button_frame.winfo_children():
                widget.pack_forget()
            self.slider_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.seek_bar.pack(side=tk.RIGHT, padx=2)
            self.time_label.pack(side=tk.RIGHT, padx=2)
            self.volume_bar.pack(side=tk.RIGHT, padx=2)
            self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.resize_handle.pack(side=tk.RIGHT, padx=2)
            self.close_button.pack(side=tk.RIGHT, padx=1)
            self.fullscreen_button.pack(side=tk.RIGHT, padx=1)
            self.playlist_button.pack(side=tk.RIGHT, padx=1)
            self.mode_button.pack(side=tk.RIGHT, padx=1)
            self.next_button.pack(side=tk.RIGHT, padx=1)
            self.prev_button.pack(side=tk.RIGHT, padx=1)
            self.toggle_button.pack(side=tk.RIGHT, padx=1)
            self.drag_handle.pack(side=tk.RIGHT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5)
        # Default layout: left-aligned
        else:
            for widget in self.slider_frame.winfo_children() + self.button_frame.winfo_children():
                widget.pack_forget()
            self.slider_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.seek_bar.pack(side=tk.LEFT, padx=2)
            self.time_label.pack(side=tk.LEFT, padx=2)
            self.volume_bar.pack(side=tk.LEFT, padx=2)
            self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.drag_handle.pack(side=tk.LEFT, padx=2)
            self.toggle_button.pack(side=tk.LEFT, padx=1)
            self.prev_button.pack(side=tk.LEFT, padx=1)
            self.next_button.pack(side=tk.LEFT, padx=1)
            self.mode_button.pack(side=tk.LEFT, padx=1)
            self.playlist_button.pack(side=tk.LEFT, padx=1)
            self.fullscreen_button.pack(side=tk.LEFT, padx=1)
            self.close_button.pack(side=tk.LEFT, padx=1)
            self.resize_handle.pack(side=tk.RIGHT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5)

    def update_mode_button_text(self):
            mode_labels = {
                "random_repo": "Shuf",      # Shuffle
                "playlist": "Play",        # Playlist
                "next_repo": "Repo",       # Next Repo
                "random_playlist": "Rand", # Random Playlist
                "repeat": "R"              # Repeat
            }
            self.mode_button.config(text=mode_labels.get(self.next_mode, "Shuf"))
            logging.debug("Player %d mode button updated to %s", self.index + 1, self.next_mode)

    def update_title_label(self):
        self.title_label.config(text=os.path.basename(self.current_file) if self.current_file else "No video loaded")

    def load_video(self, file_path):
        logging.debug("Loading video: %s", file_path)
        if getattr(sys, 'frozen', False):
            file_path = os.path.abspath(file_path)
        if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
            self.current_file = file_path
            def load_in_thread():
                try:
                    self.player.stop()
                    time.sleep(0.2)
                    media = self.instance.media_new(file_path)
                    media.parse_with_options(vlc.MediaParseFlag.local, 0)  # Force metadata parsing
                    self.player.set_media(media)
                    self.player.set_hwnd(self.video_frame.winfo_id())
                    self.player.play()

                    # Force volume to 100% after starting
                    self.player.audio_set_volume(100)
                    self.intended_volume = 100
                    self.volume_bar.set(100)
                    # Wait briefly for metadata to settle
                    start_time = time.time()
                    while time.time() - start_time < 1 and self.player.get_length() <= 0:
                        time.sleep(0.1)
                    self.toggle_button.config(text="||")
                    self.seek_bar.set(0)
                    self.parent.after(200, self.refresh_vlc)
                    self.parent.after(2000, self.check_playback_start)
                    logging.debug("Loaded and playing: %s with duration %d ms", file_path, self.player.get_length())
                    self.parent.after(0, self.update_title_label)
                    if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                        self.app.dj_dashboard.update_dashboard()
                except Exception as e:
                    logging.error(f"Failed to load video {file_path}: {e}")
                    self.current_file = None
                    self.parent.after(0, self.update_title_label)
                    self.toggle_button.config(text="▶")
                    self.reset_vlc()
            threading.Thread(target=load_in_thread, daemon=True).start()
        else:
            logging.error("Invalid video file: %s", file_path)
            self.current_file = None
            self.update_title_label()
            self.toggle_button.config(text="▶")
            self.play_next_video()

    def check_playback_start(self):
        if self.current_file:
            state = self.player.get_state()
            if not self.player.is_playing() and state != vlc.State.Paused:
                logging.debug("Video %s failed to start (state=%s), skipping", self.current_file, state)
                self.play_next_video()
            elif self.player.is_playing():
                logging.debug("Video %s started successfully", self.current_file)

    def reset_vlc(self, seek_position=None):
        logging.debug("Resetting VLC for frame_id: %d", self.frame_id)
        was_playing = self.player.is_playing()
        position = seek_position if seek_position is not None else (self.player.get_time() if was_playing else 0)
        media = self.player.get_media()
        self.player.stop()
        self.player = self.instance.media_player_new()
        if media:
            self.player.set_media(media)
            self.player.set_hwnd(self.video_frame.winfo_id())
            if was_playing or seek_position is not None:
                self.player.play()
                self.parent.after(200, lambda: self.player.set_time(position))
        width = self.frame.winfo_width()
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

    def refresh_vlc(self):
        try:
            was_playing = self.player.is_playing()
            self.player.set_hwnd(self.video_frame.winfo_id())
            if was_playing:
                self.player.play()
            else:
                self.player.pause()
            self.volume_bar.set(self.player.audio_get_volume())
            logging.debug("VLC refreshed for frame_id: %d", self.frame_id)
        except Exception as e:
            logging.error(f"Failed to refresh VLC: {e}")

    def load_first_video(self):
        if self.playlist:
            self.current_playlist_index = 0
            self.load_video(self.playlist[self.current_playlist_index])

    def toggle_play_pause(self):
        if self.player.is_playing():
            self.player.pause()
            self.toggle_button.config(text="▶")
            logging.debug("Paused: %s", self.current_file)
        else:
            if not self.current_file and self.playlist:
                self.load_first_video()
            else:
                self.player.play()
                self.toggle_button.config(text="||")
                logging.debug("Playing: %s", self.current_file)
                self.frame.lift()
                self.video_frame.lift()

    def universal_toggle(self, event):
        logging.debug("Universal toggle triggered")
        any_playing = any(p.player.is_playing() for p in self.app.players)
        for player in self.app.players:
            if any_playing and player.player.is_playing():
                player.player.pause()
                player.toggle_button.config(text="▶")
            elif not any_playing and not player.player.is_playing():
                if not player.current_file and player.playlist:
                    player.load_first_video()
                else:
                    player.player.play()
                    player.toggle_button.config(text="||")
        self.app.canvas.update_idletasks()

    def seek(self, value):
        if not self.seek_bar.in_use and self.player.get_media():
            duration = self.player.get_length()
            if duration > 0:
                position = int(float(value) / 100 * duration)
                self.player.set_time(position)
                if int(float(value)) == 100:
                    self.on_video_end(None)
        self.seek_bar.in_use = False

    def seek_to_position(self, event):
        logging.debug("Seek bar clicked at x=%d", event.x)
        if self.player.get_media():
            duration = self.player.get_length()
            if duration > 0 and self.seek_bar.winfo_width() > 0:
                percentage = min(100, max(0, (event.x / self.seek_bar.winfo_width()) * 100))
                position = int((percentage / 100) * duration)
                self.player.set_time(position)
                self.seek_bar.set(percentage)
                if percentage >= 100:
                    self.on_video_end(None)

    def set_volume(self, value):
        """Set the volume and ensure it sticks."""
        volume = int(float(value))
        self.intended_volume = volume
        self.player.audio_set_volume(volume)
        self.volume_bar.set(volume)  # Immediately update the slider to reflect the intended value
        logging.debug("Attempting to set volume to %d for Player %d", volume, self.index + 1)
        # Verify and correct volume after a short delay
        self.parent.after(100, self._verify_volume)

    def _verify_volume(self):
        """Verify and correct the volume to match the intended volume (100% for new videos)."""
        if self.intended_volume is None:
            self.intended_volume = 100
        try:
            current_volume = self.player.audio_get_volume()
            if current_volume != self.intended_volume:
                logging.warning("Volume mismatch for Player %d: expected %d, got %d. Correcting.",
                            self.index + 1, self.intended_volume, current_volume)
                self.player.audio_set_volume(self.intended_volume)
                self.volume_bar.set(self.intended_volume)
                # Double-check volume after setting
                current_volume = self.player.audio_get_volume()
                if current_volume == self.intended_volume:
                    logging.debug("Volume verified for Player %d: %d", self.index + 1, self.intended_volume)
                else:
                    logging.error("Failed to verify volume for Player %d: expected %d, got %d",
                                self.index + 1, self.intended_volume, current_volume)
                    # Force another attempt
                    self.player.audio_set_volume(self.intended_volume)
                    self.volume_bar.set(self.intended_volume)
            else:
                logging.debug("Volume verified for Player %d: %d", self.index + 1, self.intended_volume)
        except Exception as e:
            logging.error("Failed to verify volume for Player %d: %s", self.index + 1, e)


    def jump_to_volume_position(self, event):
        """Jump the volume to the clicked position on the volume bar, unless clicking the handle."""
        slider_width = self.volume_bar.winfo_width()
        if slider_width <= 0:
            return "break"

        # Get current slider value and handle position
        current_value = self.volume_bar.get()
        handle_width = 8  # sliderlength from tk.Scale config
        handle_center = (current_value / 100) * (slider_width - handle_width) + (handle_width / 2)
        handle_left = handle_center - (handle_width / 2)
        handle_right = handle_center + (handle_width / 2)

        # Check if the click is on the handle
        if handle_left <= event.x <= handle_right:
            # Clicked on handle: allow default dragging behavior
            return  # Let tk.Scale handle dragging

        # Clicked on trough: jump to position
        percentage = min(100, max(0, (event.x / slider_width) * 100))
        volume = int(percentage)
        self.intended_volume = volume
        self.player.audio_set_volume(volume)
        self.volume_bar.set(volume)
        logging.debug("Clicked volume bar for Player %d to %d%% (%d)", self.index + 1, percentage, volume)
        self.parent.after(100, self._verify_volume)
        return "break"  # Prevent default tk.Scale click behavior for trough clicks

    def format_time(self, ms):
        seconds = ms // 1000
        minutes = seconds // 60
        seconds %= 60
        return f"{minutes}:{seconds:02d}"

    def update_seek_bar(self):
        if self.is_swapping:
            self.parent.after(500, self.update_seek_bar)
            return
        if self.player.get_media():
            duration = self.player.get_length()
            if duration > 0:
                position = self.player.get_time()
                percentage = (position / duration) * 100
                self.seek_bar.set(percentage)
                self.time_label.config(text=f"{self.format_time(position)} / {self.format_time(duration)}")
                state = self.player.get_state()
                if state == vlc.State.Playing:
                    self.toggle_button.config(text="||")
                elif state in (vlc.State.Paused, vlc.State.Stopped, vlc.State.Ended):
                    self.toggle_button.config(text="▶")
                if percentage > 95 and not self.player.is_playing() and state != vlc.State.Paused:
                    self.play_next_video()
        self.parent.after(500, self.update_seek_bar)

    def toggle_fullscreen(self):
        """Toggle fullscreen mode for this specific player, allowing other players to be raised above."""
        if not self.is_fullscreen:
            # Store current state before entering fullscreen
            was_playing = self.player.is_playing()
            logging.debug("Player %d was_playing=%s before fullscreen", self.index + 1, was_playing)
            x, y = self.canvas.coords(self.frame_id)
            width = self.frame.winfo_width()
            height = self.frame.winfo_height()
            self.pre_fullscreen_coords = {'x': x, 'y': y, 'width': width, 'height': height}
            self.app.pre_fullscreen_state = {
                'players': [(p.canvas.coords(p.frame_id), p.frame.winfo_width(), p.frame.winfo_height()) for p in self.app.players],
                'geometry': self.app.root.winfo_geometry(),
                'is_maximized': self.app.is_maximized,
                'is_fullscreen': self.app.is_fullscreen,
                'pre_smm_positions': self.app.pre_smm_positions[:]
            }
            logging.debug("Player %d entering fullscreen, stored pre-fullscreen: %s", self.index + 1, self.pre_fullscreen_coords)

            # Set app window to fullscreen
            self.app.root.attributes('-fullscreen', True)
            screen_width = self.app.root.winfo_screenwidth()
            screen_height = self.app.root.winfo_screenheight()

            # Resize this player to fill the screen
            self.set_size_and_position(screen_width, screen_height, 0, 0)
            self.canvas.tag_raise(self.frame_id)

            # Store pre-fullscreen positions and lower other players below this one
            for player in self.app.players:
                if player != self:
                    player.pre_fullscreen_coords = {
                        'x': self.canvas.coords(player.frame_id)[0],
                        'y': self.canvas.coords(player.frame_id)[1],
                        'width': player.frame.winfo_width(),
                        'height': player.frame.winfo_height()
                    }
                    self.canvas.tag_lower(player.frame_id, self.frame_id)
                    logging.debug("Lowered Player %d below fullscreened Player %d", 
                                 player.index + 1, self.index + 1)

            # Schedule state restoration to ensure VLC has stabilized
            def restore_state():
                if self.player.get_media():
                    for _ in range(3):
                        if not was_playing:
                            self.player.pause()
                            self.toggle_button.config(text="▶")
                            logging.debug("Player %d restored to paused state after fullscreen, VLC state: %s", 
                                         self.index + 1, self.player.get_state())
                        else:
                            self.player.play()
                            self.toggle_button.config(text="||")
                            logging.debug("Player %d restored to playing state after fullscreen, VLC state: %s", 
                                         self.index + 1, self.player.get_state())
                        time.sleep(0.1)
                        if self.player.get_state() == (vlc.State.Paused if not was_playing else vlc.State.Playing):
                            break
                    else:
                        logging.warning("Failed to set Player %d state to %s after retries", 
                                       self.index + 1, "paused" if not was_playing else "playing")

            self.parent.after(100, restore_state)

            self.is_fullscreen = True
            self.fullscreen_button.config(text="⤬")
            self.app.is_fullscreen = True
            self.app.is_maximized = False
            self.app.canvas.update_idletasks()

        else:
            # Exit fullscreen via app-level handler
            self.app.handle_escape(None)

        # Update DJ dashboard
        if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
            self.app.dj_dashboard.update_dashboard()
        logging.debug("Player %d fullscreen state: %s", self.index + 1, self.is_fullscreen)


    def drop_video(self, event):
        logging.debug("Drop on video frame: %s", event.data)
        data = event.data.strip()
        file_path = data[1:-1].strip() if data.startswith("{") and data.endswith("}") else data
        if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
            self.load_video(file_path)

    def show_controls(self, event=None):
        if not self.controls_visible:
            self.controls_visible = True
            self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.video_frame.lift()
            self.canvas.update_idletasks()
            logging.debug("Showed controls for Player %d", self.index + 1)

    def schedule_hide_controls(self, event=None):
        if self.controls_visible and not self.hide_timeout:
            self.hide_timeout = self.parent.after(3000, self.hide_controls)

    def hide_controls(self):
        if self.controls_visible:
            self.controls_frame.pack_forget()
            self.controls_visible = False
            self.video_frame.lift()
            self.frame.update_idletasks()
            logging.debug("Hid controls for Player %d", self.index + 1)
        self.hide_timeout = None

    def start_drag(self, event):
        frame_x, frame_y = self.canvas.coords(self.frame_id)
        self.drag_start_x = event.x + frame_x
        self.drag_start_y = event.y + frame_y
        self.canvas.tag_raise(self.frame_id)
        if self.app.is_maximized and not self.app.is_fullscreen:
            logging.info("Dragging disabled in SMM. Use 1-5 to swap or F11 for manipulation.")
            return

    def drag(self, event):
        if self.app.is_maximized and not self.app.is_fullscreen:
            return
        frame_x, frame_y = self.canvas.coords(self.frame_id)
        new_x = max(0, min(event.x + frame_x, self.canvas.winfo_width() - self.frame.winfo_width()))
        new_y = max(0, min(event.y + frame_y, self.canvas.winfo_height() - self.frame.winfo_height()))
        self.canvas.coords(self.frame_id, new_x, new_y)
        self.drag_start_x = event.x + frame_x
        self.drag_start_y = event.y + frame_y

    def stop_drag(self, event):
        if not (self.app.is_maximized and not self.app.is_fullscreen):
            width = self.frame.winfo_width()
            slider_length = int(width * 0.75)
            self.seek_bar.config(length=max(100, slider_length - 100))
            self.volume_bar.config(length=min(100, int(slider_length * 0.25)))

    def start_resize(self, event):
        if not self.app.is_maximized or self.app.is_fullscreen:
            self.drag_start_x = event.x_root
            self.drag_start_y = event.y_root

    def resize(self, event):
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
            slider_length = int(new_width * 0.75)
            self.seek_bar.config(length=max(100, slider_length - 100))
            self.volume_bar.config(length=min(100, int(slider_length * 0.25)))

    def set_size_and_position(self, width, height, x, y):
        logging.debug("Setting Player %d size to %dx%d at (%d, %d)", self.index + 1, width, height, x, y)
        try:
            self.canvas.itemconfig(self.frame_id, width=width, height=height)
            self.canvas.coords(self.frame_id, x, y)
            slider_length = int(width * 0.75)
            self.seek_bar.config(length=max(100, slider_length - 100))
            self.volume_bar.config(length=min(100, int(slider_length * 0.25)))
            self.frame.lift()
            self.pack_controls()
            if self.controls_visible:
                self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                self.video_frame.lift()
            self.frame.update_idletasks()
        except tk.TclError as e:
            logging.error("Failed to set size and position for Player %d: %s", self.index + 1, e)

    def configure_for_width(self, width):
        slider_length = int(width * 0.75)
        self.seek_bar.config(length=max(100, slider_length - 100))
        self.volume_bar.config(length=min(100, int(slider_length * 0.25)))
        self.pack_controls()
        if self.controls_visible:
            self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.video_frame.lift()
        self.frame.update_idletasks()

    def close(self):
            """Close the player widget and notify the app to handle removal."""
            logging.debug("Closing player %d", self.index + 1)
            if self.player:
                self.player.stop()
            self.canvas.delete(self.frame_id)
            self.app.remove_player(self)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.build_player_controls()  # Rebuild on close
                self.app.dj_dashboard.adjust_height()  # Adjust height after removal
                self.app.dj_dashboard.update_dashboard()

    def toggle_next_mode(self):
            modes = ["random_repo", "playlist", "next_repo", "random_playlist", "repeat"]  # Added repeat
            self.next_mode = modes[(modes.index(self.next_mode) + 1) % len(modes)]
            self.update_mode_button_text()
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.mode_vars[self.index].set(self.app.dj_dashboard._display_mode(self.next_mode))
                logging.debug("Synced DJD mode for Player %d to %s", self.index + 1, self.next_mode)

    def universal_prev(self, event):
        for player in self.app.players:
            player.play_previous_video()

    def universal_next(self, event):
        for player in self.app.players:
            player.play_next_video()

    def edit_playlist(self):
        x = self.parent.winfo_pointerx()
        y = self.parent.winfo_pointery()
        playlist_window = tk.Toplevel(self.parent)
        playlist_window.title(f"Playlist - Player {self.index + 1}")
        window_width, window_height = 400, 300
        screen_width, screen_height = self.parent.winfo_screenwidth(), self.parent.winfo_screenheight()
        x = max(0, min(x, screen_width - window_width))
        y = max(0, min(y, screen_height - window_height))
        playlist_window.geometry(f"{window_width}x{window_height}+{x}+{y}")

        tk.Label(playlist_window, text="Current Playlist:").pack(pady=5)
        self.playlist_listbox = tk.Listbox(playlist_window)
        self.update_playlist_listbox(self.playlist_listbox)
        self.playlist_listbox.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.playlist_listbox.drop_target_register(DND_FILES)
        self.playlist_listbox.dnd_bind('<<Drop>>', self.handle_drop)

        button_frame = tk.Frame(playlist_window)
        button_frame.pack(fill=tk.X, pady=2)
        tk.Button(button_frame, text="Add Videos", command=self.add_to_playlist).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Remove", command=lambda: self.remove_from_playlist(self, self.playlist_listbox)).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Move Up", command=lambda: self.move_up(self, self.playlist_listbox)).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Move Down", command=lambda: self.move_down(self, self.playlist_listbox)).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Play Next", command=lambda: self.play_next(self, self.playlist_listbox)).pack(side=tk.LEFT, padx=2)
        tk.Button(button_frame, text="Close", command=playlist_window.destroy).pack(side=tk.LEFT, padx=2)

    def move_up(self, player, listbox):
        selection = listbox.curselection()
        if selection and selection[0] > 0:
            index = selection[0]
            item = player.playlist.pop(index)
            player.playlist.insert(index - 1, item)
            self.update_playlist_listbox(listbox)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_playlist(player, self.app.dj_dashboard.playlist_boxes[player.index])
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(index - 1)
            if player.current_playlist_index == index:
                player.current_playlist_index -= 1
            elif player.current_playlist_index == index - 1:
                player.current_playlist_index += 1

    def move_down(self, player, listbox):
        selection = listbox.curselection()
        if selection and selection[0] < len(player.playlist) - 1:
            index = selection[0]
            item = player.playlist.pop(index)
            player.playlist.insert(index + 1, item)
            self.update_playlist_listbox(listbox)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_playlist(player, self.app.dj_dashboard.playlist_boxes[player.index])
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(index + 1)
            if player.current_playlist_index == index:
                player.current_playlist_index += 1
            elif player.current_playlist_index == index + 1:
                player.current_playlist_index -= 1

    def handle_drop(self, event):
        data = event.data.strip()
        added_files = []
        if data.startswith('{') and data.endswith('}') and ' ' not in data:
            file_path = data[1:-1].strip()
            if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
                added_files.append(file_path)
        elif ' ' in data:
            added_files = [path.strip('{}"\'') for path in re.findall(r'(?:{[^{}]*}|[^{}\s]+)', data)
                           if os.path.isfile(path.strip('{}"\''))
                           and path.strip('{}"\'').lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))]
        else:
            file_path = data
            if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
                added_files.append(file_path)

        if added_files:
            self.playlist.extend(added_files)
            if hasattr(self, 'playlist_listbox') and self.playlist_listbox.winfo_exists():
                self.update_playlist_listbox(self.playlist_listbox)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_playlist(self, self.app.dj_dashboard.playlist_boxes[self.index])
            self.parent.update_idletasks()

    def add_to_playlist(self):
        files = filedialog.askopenfilenames(filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm *.gif *.mpeg *.mpg")])
        if files:
            self.playlist.extend(files)
            if hasattr(self, 'playlist_listbox') and self.playlist_listbox.winfo_exists():
                self.update_playlist_listbox(self.playlist_listbox)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_playlist(self, self.app.dj_dashboard.playlist_boxes[self.index])

    def play_next(self, player, listbox):
        if not player.playlist:
            logging.debug("No playlist items to queue for Player %d", player.index + 1)
            return
        selection = listbox.curselection()
        if not selection or not (0 <= selection[0] < len(player.playlist)):
            logging.debug("No valid selection for Player %d", player.index + 1)
            return
        index = selection[0]
        player.force_next_file = player.playlist[index]
        logging.debug("Queued %s to play next for Player %d", os.path.basename(player.force_next_file), player.index + 1)
        
        # Capture current scroll position
        scroll_pos = listbox.yview()[0]
        
        # Update playlist display
        self.update_playlist(player, listbox)
        
        # Restore scroll position
        listbox.yview_moveto(scroll_pos)
        
        # Ensure selection remains
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(index)
        
        # Update player’s playlist window if open
        if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
            player.update_playlist_listbox(player.playlist_listbox)
            player.playlist_listbox.yview_moveto(scroll_pos)

    def remove_from_playlist(self, player, listbox):
        selection = listbox.curselection()
        if selection:
            index = selection[0]
            removed_file = player.playlist.pop(index)
            self.update_playlist_listbox(listbox)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_playlist(player, self.app.dj_dashboard.playlist_boxes[player.index])
            if player.current_playlist_index > index:
                player.current_playlist_index -= 1
            elif player.current_playlist_index == index:
                player.current_playlist_index = -1
                if player.current_file == removed_file:
                    player.current_file = None
                    player.player.stop()
                    player.toggle_button.config(text="▶")
                    player.update_title_label()

    def update_playlist_listbox(self, listbox):
        listbox.delete(0, tk.END)
        current_idx = self.current_playlist_index
        next_idx = None

        # Determine next_idx based on mode or forced next file
        if self.force_next_file and self.force_next_file in self.playlist:
            next_idx = self.playlist.index(self.force_next_file)  # Priority for forced next
        elif self.next_mode == "playlist" and self.playlist:
            next_idx = (current_idx + 1) % len(self.playlist)
        elif self.next_mode == "random_playlist" and self.playlist:
            next_idx = self.next_playlist_index

        for i, file in enumerate(self.playlist):
            name = os.path.basename(file)
            if i == current_idx and file == self.current_file:
                listbox.insert(tk.END, f"▶ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#006400'})  # Playing: dark green
            elif i == next_idx and next_idx is not None:
                listbox.insert(tk.END, f"→ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#98FB98'})  # Next: light green
            else:
                listbox.insert(tk.END, name)
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': 'white'})

    def on_video_end(self, event):
        self.play_next_video()

    def play_next_video(self):
            if self.next_mode == "repeat" and self.current_file:
                logging.debug("Player %d in Repeat mode, replaying current video: %s", 
                             self.index + 1, os.path.basename(self.current_file))
                self.load_video(self.current_file)
                return

            if not self.current_file and self.playlist:
                self.load_first_video()
                return
            next_file = None

            # Check for forced next file (overrides mode)
            if self.force_next_file and os.path.isfile(self.force_next_file):
                next_file = self.force_next_file
                self.force_next_file = None  # Clear after use
                logging.debug("Forced next video: %s for Player %d", next_file, self.index + 1)
            else:
                # Normal mode behavior
                if self.next_mode == "random_repo" and self.current_file:
                    repo_dir = os.path.dirname(self.current_file)
                    all_files = [f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))]
                    if all_files:
                        next_file = os.path.join(repo_dir, random.choice(all_files))
                elif self.next_mode == "playlist" and self.playlist:
                    self.current_playlist_index = (self.current_playlist_index + 1) % len(self.playlist)
                    next_file = self.playlist[self.current_playlist_index]
                elif self.next_mode == "next_repo" and self.current_file:
                    repo_dir = os.path.dirname(self.current_file)
                    all_files = sorted([f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))])
                    if all_files:
                        current_idx = all_files.index(os.path.basename(self.current_file)) if os.path.basename(self.current_file) in all_files else -1
                        next_file = os.path.join(repo_dir, all_files[(current_idx + 1) % len(all_files)] if current_idx != -1 else all_files[0])
                elif self.next_mode == "random_playlist" and self.playlist:
                    if self.next_playlist_index is None or self.next_playlist_index == self.current_playlist_index:
                        self.next_playlist_index = random.randrange(len(self.playlist))
                        while self.next_playlist_index == self.current_playlist_index and len(self.playlist) > 1:
                            self.next_playlist_index = random.randrange(len(self.playlist))
                    self.current_playlist_index = self.next_playlist_index
                    next_file = self.playlist[self.current_playlist_index]
                    self.next_playlist_index = random.randrange(len(self.playlist))
                    while self.next_playlist_index == self.current_playlist_index and len(self.playlist) > 1:
                        self.next_playlist_index = random.randrange(len(self.playlist))

            if next_file and os.path.isfile(next_file):
                self.load_video(next_file)
                # Update current_playlist_index if the next file is in the playlist
                if next_file in self.playlist:
                    self.current_playlist_index = self.playlist.index(next_file)
            else:
                self.current_file = None
                self.update_title_label()
                self.player.stop()
                self.toggle_button.config(text="▶")
            
            # Update DJD if open
            if self.parent.dj_dashboard and self.parent.dj_dashboard.winfo_exists():
                self.parent.dj_dashboard.update_dashboard()

    def play_previous_video(self):
        if not self.current_file and self.playlist:
            self.load_first_video()
            return
        prev_file = None
        if self.next_mode == "random_repo" and self.current_file:
            repo_dir = os.path.dirname(self.current_file)
            all_files = [f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))]
            if all_files:
                prev_file = os.path.join(repo_dir, random.choice(all_files))
        elif self.next_mode == "playlist" and self.playlist:
            self.current_playlist_index = (self.current_playlist_index - 1) % len(self.playlist)
            prev_file = self.playlist[self.current_playlist_index]
        elif self.next_mode == "next_repo" and self.current_file:
            repo_dir = os.path.dirname(self.current_file)
            all_files = sorted([f for f in os.listdir(repo_dir) if f.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))])
            if all_files:
                current_idx = all_files.index(os.path.basename(self.current_file)) if os.path.basename(self.current_file) in all_files else -1
                prev_file = os.path.join(repo_dir, all_files[(current_idx - 1) % len(all_files)] if current_idx > 0 else all_files[-1])
        elif self.next_mode == "random_playlist" and self.playlist:
            self.current_playlist_index = random.randrange(len(self.playlist))
            prev_file = self.playlist[self.current_playlist_index]

        if prev_file and os.path.isfile(prev_file):
            self.load_video(prev_file)
        else:
            self.current_file = None
            self.update_title_label()
            self.player.stop()
            self.toggle_button.config(text="▶")

    def resize_video_window(self, width, height):
        """Resize the VLC video window to the specified dimensions."""
        try:
            # Update video_frame size
            self.video_frame.configure(width=width, height=height)
            # Only reassign HWND if the window ID has changed
            current_hwnd = self.video_frame.winfo_id()
            if current_hwnd != getattr(self, 'hwnd', None):
                self.hwnd = current_hwnd
                self.player.set_hwnd(self.hwnd)
                logging.debug("Reassigned HWND for Player %d: %d", self.index + 1, self.hwnd)
            # Reset VLC scaling to fit the new window size
            self.player.video_set_scale(0)
            # Update control sliders to match new width
            slider_length = int(width * 0.75)
            self.seek_bar.configure(length=max(100, slider_length - 100))
            self.volume_bar.configure(length=min(100, int(slider_length * 0.25)))
            self.pack_controls()
            if self.controls_visible:
                self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
                self.video_frame.lift()
            self.frame.update_idletasks()
            logging.debug("Resized VLC video window for Player %d to %dx%d, VLC state: %s", 
                         self.index + 1, width, height, self.player.get_state())
        except Exception as e:
            logging.error("Failed to resize VLC video window for Player %d: %s", self.index + 1, str(e))


class DJDashboard(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.parent = parent
        self.app = app
        self.title("Ultiplay DJ Dashboard")
        self.geometry("900x400")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_paused = False
        self.after_id = None
        self.scroll_positions = {}
        self.selected_indices = {}
        self.transition_queue = deque()  # Queue for staggering video transitions
        self.is_processing_transition = False

        icon_path = os.path.join(sys._MEIPASS, 'appicon.ico') if getattr(sys, 'frozen', False) else r"C:\Users\march\OneDrive\Desktop\Ultiplay\appicon.ico"
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
                logging.debug("Set DJ Dashboard icon to %s", icon_path)
            except Exception as e:
                logging.warning("Failed to set DJ Dashboard icon: %s", str(e))
        else:
            logging.warning("Icon file not found at %s for DJ Dashboard", icon_path)

        self.main_frame = tk.Frame(self)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.player_frames = {}
        self.seek_bars = {}
        self.volume_bars = {}
        self.mode_vars = {}
        self.playlist_boxes = {}
        self.swap_vars = {}
        self.layer_vars = {}
        self.now_playing_labels = {}
        self.toggle_buttons = {}
        self.skip_5s_buttons = {}
        self.skip_10s_buttons = {}
        self.next_buttons = {}
        self.seek_vars = {}
        self.time_labels = {}
        self.last_playing = {}

        self.build_player_controls()
        self.after(100, self.adjust_height)
        self.after(1000, self.start_update_loop)
        logging.debug("DJD initialized, update loop scheduled")

        self.bind('b', lambda e: self.raise_player_5())

    def start_update_loop(self):
            logging.debug("Starting DJD update loop")
            self.update_dashboard()

    def build_player_controls(self):
            logging.debug("Building DJD controls for %d players", len(self.app.players))
            old_main_frame = self.main_frame
            old_player_frames = self.player_frames.copy()

            self.main_frame = tk.Frame(self)
            self.main_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

            self.player_frames.clear()
            self.seek_bars.clear()
            self.volume_bars.clear()
            self.mode_vars.clear()
            self.playlist_boxes.clear()
            self.swap_vars.clear()
            self.now_playing_labels.clear()
            self.toggle_buttons.clear()
            self.skip_5s_buttons.clear()
            self.skip_10s_buttons.clear()
            self.next_buttons.clear()
            self.seek_vars.clear()
            self.time_labels.clear()
            self.last_playing.clear()

            num_players = len(self.app.players)
            if num_players == 0:
                tk.Label(self.main_frame, text="No players active").pack(pady=10)
                logging.debug("DJD built with no players")
            elif num_players <= 3:
                for i, player in enumerate(self.app.players):
                    self._build_player_frame(i, player, self.main_frame)
                # Add Player button below the last player's controls
                add_player_button = tk.Button(
                    self.main_frame,
                    text="+",
                    command=self.app.add_player,
                    width=3,
                    height=1,
                    font=("Arial", 8),
                    padx=2,
                    pady=0
                )
                add_player_button.pack(pady=(2, 0))
                logging.debug("Added Add Player button below Player %d controls (single column)", num_players)
            else:
                # Use PanedWindow for 4+ players
                self.paned_window = ttk.PanedWindow(self.main_frame, orient=tk.HORIZONTAL)
                self.paned_window.pack(fill=tk.BOTH, expand=True)
                left_frame = tk.Frame(self.paned_window)
                right_frame = tk.Frame(self.paned_window)
                self.paned_window.add(left_frame, weight=1)
                self.paned_window.add(right_frame, weight=1)
                left_count = (num_players + 1) // 2  # Distribute players evenly
                for i, player in enumerate(self.app.players):
                    target_frame = left_frame if i < left_count else right_frame
                    self._build_player_frame(i, player, target_frame)
                # Add Player button below the last player's controls in the right frame
                add_player_button = tk.Button(
                    right_frame,
                    text="+",
                    command=self.app.add_player,
                    width=3,
                    height=1,
                    font=("Arial", 8),
                    padx=2,
                    pady=0
                )
                add_player_button.pack(pady=(2, 0))
                logging.debug("Added Add Player button below Player %d controls (right column)", num_players)
                # Set sash position to middle of window width
                window_width = self.winfo_width() if self.winfo_width() > 1 else 900  # Use initial width if not yet rendered
                self.paned_window.sashpos(0, window_width // 2)
                logging.debug("Set PanedWindow sash to middle at %d pixels", window_width // 2)

            old_main_frame.pack_forget()
            for frame in old_player_frames.values():
                frame.destroy()

            self.update_idletasks()
            self.after(100, self.adjust_height)
            logging.debug("DJD controls built successfully for %d players", num_players)

    def _build_player_frame(self, i, player, parent_frame):
            frame = tk.LabelFrame(parent_frame, text=f"Player {i+1}")
            frame.config(bg='#D3D3D3' if i % 2 == 0 else '#ADD8E6')
            frame.pack(fill=tk.X, pady=2)
            self.player_frames[i] = frame

            now_playing = tk.Label(frame, text=f"Now Playing: {os.path.basename(player.current_file or 'None')}",
                                   anchor="w", bg=frame.cget('bg'))
            now_playing.pack(fill=tk.X, pady=(2, 0))
            self.now_playing_labels[i] = now_playing
            self.last_playing[i] = player.current_file

            slider_frame = tk.Frame(frame, bg=frame.cget('bg'))
            slider_frame.pack(fill=tk.X)

            time_label = tk.Label(slider_frame, text="0:00 / 0:00", bg=frame.cget('bg'), fg="black", width=10)
            time_label.pack(side=tk.LEFT, padx=2)
            self.time_labels[i] = time_label

            seek_var = tk.DoubleVar()
            self.seek_vars[i] = seek_var
            seek_bar = tk.Scale(slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                variable=seek_var, command=lambda v, p=player: p.seek(v),
                                showvalue=0, length=200, sliderlength=8,
                                highlightbackground="green", troughcolor="darkgreen")
            seek_bar.bind("<Button-1>", lambda e, p=player: self.seek_to_position(e, p))
            seek_bar.pack(side=tk.LEFT, padx=2)
            self.seek_bars[i] = seek_bar

            volume_bar = tk.Scale(slider_frame, from_=0, to=100, orient=tk.HORIZONTAL,
                                  command=lambda v, p=player: self.set_player_volume(p, v),
                                  showvalue=0, length=100, sliderlength=8,
                                  highlightbackground="blue", troughcolor="darkblue")
            volume_bar.set(100)
            volume_bar.bind("<Button-1>", lambda e, p=player: self.jump_to_volume_position(e, p))
            volume_bar.pack(side=tk.LEFT, padx=2)
            self.volume_bars[i] = volume_bar
            player.player.audio_set_volume(100)
            player.intended_volume = 100
            self.after(100, lambda: self._verify_player_volume(player))

            btn_frame = tk.Frame(frame, bg=frame.cget('bg'))
            btn_frame.pack(fill=tk.X, pady=(0, 2))

            toggle_btn = tk.Button(btn_frame, text="▶" if not player.player.is_playing() else "||",
                                   command=lambda p=player: p.toggle_play_pause(), width=4)
            toggle_btn.pack(side=tk.LEFT, padx=1)
            self.toggle_buttons[i] = toggle_btn

            skip_5s_btn = tk.Button(btn_frame, text=">", command=lambda p=player: self.skip_forward(p, 5000), width=4)
            skip_5s_btn.pack(side=tk.LEFT, padx=1)
            self.skip_5s_buttons[i] = skip_5s_btn

            skip_10s_btn = tk.Button(btn_frame, text=">>", command=lambda p=player: self.skip_forward(p, 10000), width=4)
            skip_10s_btn.pack(side=tk.LEFT, padx=1)
            self.skip_10s_buttons[i] = skip_10s_btn

            next_btn = tk.Button(btn_frame, text=">>|", command=lambda p=player: self._play_next_and_update(p), width=4)
            next_btn.pack(side=tk.LEFT, padx=1)
            self.next_buttons[i] = next_btn

            close_btn = tk.Button(btn_frame, text="X", command=lambda p=player: self.app.remove_player(p), width=3)
            close_btn.pack(side=tk.LEFT, padx=1)

            mode_var = tk.StringVar(value=self._display_mode(player.next_mode))
            modes = ["Shuffle", "Playlist", "Next Repo", "Random Playlist", "Repeat"]  # Added Repeat mode
            mode_menu = tk.OptionMenu(btn_frame, mode_var, *modes, command=lambda m, p=player: self.set_mode(p, m))
            mode_menu.config(width=10)
            mode_menu.pack(side=tk.LEFT, padx=1)
            self.mode_vars[i] = mode_var

            swap_var = tk.StringVar(value=f"P{i+1}")
            player_options = [f"P{j+1}" for j in range(len(self.app.players))]
            swap_menu = tk.OptionMenu(btn_frame, swap_var, *player_options,
                                      command=lambda val, src=i: self.swap_player(src, int(val[1:])-1))
            swap_menu.config(width=5)
            swap_menu.pack(side=tk.LEFT, padx=1)
            self.swap_vars[i] = swap_var

            # Functional Layer dropdown
            layer_var = tk.StringVar(value=f"Layer {player.layer}")
            layer_options = [f"Layer {j}" for j in range(len(self.app.players))]
            layer_menu = tk.OptionMenu(btn_frame, layer_var, *layer_options,
                                       command=lambda val, p=player: self.set_layer(p, int(val.split()[1])))
            layer_menu.config(width=6)
            layer_menu.pack(side=tk.LEFT, padx=1)
            self.layer_vars[i] = layer_var

            playlist_box = tk.Listbox(frame, height=6, selectmode="single")
            playlist_box.pack(fill=tk.X, pady=2)
            playlist_box.drop_target_register(DND_FILES)
            playlist_box.dnd_bind('<<Drop>>', lambda e, p=player: self.handle_playlist_drop(e, p))
            playlist_box.bind("<Button-1>", lambda e, p=player, lb=playlist_box: self.on_listbox_select(e, p, lb))
            self.update_playlist(player, playlist_box)
            self.playlist_boxes[i] = playlist_box

            playlist_btn_frame = tk.Frame(frame, bg=frame.cget('bg'))
            playlist_btn_frame.pack(fill=tk.X, pady=2)
            tk.Button(playlist_btn_frame, text="Move Up", command=lambda p=player, lb=playlist_box: self.move_up(p, lb)).pack(side=tk.LEFT, padx=2)
            tk.Button(playlist_btn_frame, text="Move Down", command=lambda p=player, lb=playlist_box: self.move_down(p, lb)).pack(side=tk.LEFT, padx=2)
            tk.Button(playlist_btn_frame, text="Remove", command=lambda p=player, lb=playlist_box: self.remove_from_playlist(p, lb)).pack(side=tk.LEFT, padx=2)
            tk.Button(playlist_btn_frame, text="Play Next", command=lambda p=player, lb=playlist_box: self.play_next(p, lb)).pack(side=tk.LEFT, padx=2)

    def set_layer(self, player, layer):
        """Set the player's layer and update the canvas stacking order."""
        player.set_layer(layer)
        for i, p in enumerate(self.app.players):
            self.layer_vars[i].set(f"Layer {p.layer}")
        logging.debug("Layer set for Player %d to %d", player.index + 1, layer)

    def on_listbox_select(self, event, player, listbox):
        selection = listbox.curselection()
        if selection:
            index = selection[0]
            self.selected_indices[player.index] = index
            logging.debug("Player %d: Selected index %d via click", player.index + 1, index)

    def update_dashboard(self):
        """Update the DJ Dashboard for all players, displaying full now playing text and syncing volume sliders."""
        if not self.winfo_exists():
            logging.debug("DJD window closed, stopping update loop")
            return
        if self.update_paused:
            logging.debug("DJD update paused, rescheduling")
            self.after_id = self.after(500, self.update_dashboard)
            return

        logging.debug("Running update_dashboard for %d players", len(self.app.players))

        if len(self.player_frames) != len(self.app.players):
            logging.debug("Player count changed (%d vs %d), rebuilding DJD", len(self.player_frames), len(self.app.players))
            self.build_player_controls()
            self.after_id = self.after(100, self.update_dashboard)
            return

        for i, player in enumerate(self.app.players):
            if i not in self.player_frames:
                logging.warning("Player %d frame missing, rebuilding DJD", i + 1)
                self.build_player_controls()
                self.after_id = self.after(100, self.update_dashboard)
                return

            current_file = player.current_file or 'None'
            # Always update now playing with full title
            display_text = f"Now Playing: {os.path.basename(current_file)}"
            self.now_playing_labels[i].config(text=display_text)
            if current_file != self.last_playing.get(i):
                self.last_playing[i] = current_file
                self.update_playlist(player, self.playlist_boxes[i])
                logging.debug("Updated Player %d 'Now Playing' to: %s and refreshed playlist", i + 1, current_file)

            if player.is_swapping:
                logging.debug("Player %d is swapping, skipping update", i + 1)
                continue

            if player.player.get_media():
                duration = player.player.get_length()
                state = player.player.get_state()
                if duration > 0:
                    position = player.player.get_time()
                    percentage = (position / duration) * 100
                    self.seek_vars[i].set(percentage)
                    formatted_time = f"{player.format_time(position)} / {player.format_time(duration)}"
                    self.time_labels[i].config(text=formatted_time)
                    if state == vlc.State.Playing:
                        self.toggle_buttons[i].config(text="||")
                    elif state in (vlc.State.Paused, vlc.State.Stopped, vlc.State.Ended):
                        self.toggle_buttons[i].config(text="▶")
                    if percentage > 95 and not player.player.is_playing() and state != vlc.State.Paused:
                        self.queue_video_transition(player)
                else:
                    self.seek_vars[i].set(0)
                    self.time_labels[i].config(text="0:00 / 0:00")
                    self.toggle_buttons[i].config(text="▶")
            else:
                self.seek_vars[i].set(0)
                self.time_labels[i].config(text="0:00 / 0:00")
                self.toggle_buttons[i].config(text="▶")

            # Sync volume bar
            current_volume = player.player.audio_get_volume()
            intended_volume = player.intended_volume if player.intended_volume is not None else 100
            if current_volume >= 0:
                if abs(current_volume - intended_volume) > 5:
                    logging.warning("DJD volume mismatch for Player %d: expected %d, got %d. Correcting.",
                                i + 1, intended_volume, current_volume)
                    self.volume_bars[i].set(intended_volume)
                    player.volume_bar.set(intended_volume)
                else:
                    self.volume_bars[i].set(current_volume)
                    player.volume_bar.set(current_volume)
                logging.debug("Synced volume bar for Player %d to %d", i + 1, self.volume_bars[i].get())
            else:
                self.volume_bars[i].set(intended_volume)
                player.volume_bar.set(intended_volume)
                logging.debug("Set volume bar for Player %d to intended %d (VLC reported %d)", 
                            i + 1, intended_volume, current_volume)

            # Sync layer dropdown
            self.layer_vars[i].set(f"Layer {player.layer}")

        self.update_idletasks()
        self.process_transition_queue()
        self.after_id = self.after(1000, self.update_dashboard)

    def queue_video_transition(self, player):
            if player.next_mode == "repeat":
                # In Repeat mode, replay the current video
                if player.current_file:
                    logging.debug("Player %d in Repeat mode, replaying current video: %s", 
                                 player.index + 1, os.path.basename(player.current_file))
                    player.player.set_mrl(player.current_file)
                    player.player.play()
                    self.update_dashboard()
                    return
            self.transition_queue.append(player)
            logging.debug("Queued video transition for Player %d", player.index + 1)

    def process_transition_queue(self):
        if self.is_processing_transition or not self.transition_queue:
            return
        self.is_processing_transition = True
        player = self.transition_queue.popleft()
        try:
            player.play_next_video()
            logging.debug("Processed video transition for Player %d", player.index + 1)
        except Exception as e:
            logging.error("Error transitioning video for Player %d: %s", player.index + 1, str(e))
        self.after(100, self._finish_transition)

    def _finish_transition(self):
        self.is_processing_transition = False
        if self.transition_queue:
            self.process_transition_queue()

    def update_playlist(self, player, listbox):
        scroll_pos = listbox.yview()[0]
        self.scroll_positions[player.index] = scroll_pos
        selected_idx = self.selected_indices.get(player.index)

        listbox.delete(0, tk.END)
        current_idx = player.current_playlist_index
        next_idx = None
        if player.force_next_file:
            try:
                next_idx = player.playlist.index(player.force_next_file)
                logging.debug("Player %d: Next index set to %d for file %s", player.index + 1, next_idx, player.force_next_file)
            except ValueError:
                logging.debug("Player %d: force_next_file %s not in playlist", player.index + 1, player.force_next_file)
                next_idx = None

        for i, file in enumerate(player.playlist):
            name = os.path.basename(file)
            is_current = i == current_idx and file == player.current_file
            is_next = i == next_idx
            if is_current and is_next:
                listbox.insert(tk.END, f"▶→ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#98FB98'})
            elif is_current:
                listbox.insert(tk.END, f"▶ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#006400'})
            elif is_next:
                listbox.insert(tk.END, f"→ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#98FB98'})
            else:
                listbox.insert(tk.END, name)
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': 'white'})

        listbox.yview_moveto(scroll_pos)
        if selected_idx is not None and 0 <= selected_idx < listbox.size():
            listbox.selection_set(selected_idx)
            logging.debug("Player %d: Restored selection to index %d", player.index + 1, selected_idx)

    def play_next(self, player, listbox):
        if not player.playlist:
            logging.debug("No playlist items to queue for Player %d", player.index + 1)
            return
        selection = listbox.curselection()
        if not selection or not (0 <= selection[0] < len(player.playlist)):
            logging.debug("No valid selection for Player %d", player.index + 1)
            return
        index = selection[0]
        player.force_next_file = player.playlist[index]
        self.selected_indices[player.index] = index
        self.update_playlist(player, listbox)
        logging.debug("Queued %s to play next for Player %d", os.path.basename(player.force_next_file), player.index + 1)
        if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
            player.update_playlist_listbox(player.playlist_listbox)
            player.playlist_listbox.yview_moveto(self.scroll_positions.get(player.index, 0.0))
            player.playlist_listbox.selection_clear(0, tk.END)
            player.playlist_listbox.selection_set(index)

    def move_up(self, player, listbox):
        selection = listbox.curselection()
        if selection and selection[0] > 0:
            idx = selection[0]
            player.playlist[idx], player.playlist[idx - 1] = player.playlist[idx - 1], player.playlist[idx]
            if player.current_playlist_index == idx:
                player.current_playlist_index -= 1
            elif player.current_playlist_index == idx - 1:
                player.current_playlist_index += 1
            self.selected_indices[player.index] = idx - 1
            self.update_playlist(player, listbox)
            if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
                player.update_playlist_listbox(player.playlist_listbox)

    def move_down(self, player, listbox):
        selection = listbox.curselection()
        if selection and selection[0] < len(player.playlist) - 1:
            idx = selection[0]
            player.playlist[idx], player.playlist[idx + 1] = player.playlist[idx + 1], player.playlist[idx]
            if player.current_playlist_index == idx:
                player.current_playlist_index += 1
            elif player.current_playlist_index == idx + 1:
                player.current_playlist_index -= 1
            self.selected_indices[player.index] = idx + 1
            self.update_playlist(player, listbox)
            if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
                player.update_playlist_listbox(player.playlist_listbox)

    def remove_from_playlist(self, player, listbox):
        selection = listbox.curselection()
        if selection:
            idx = selection[0]
            if idx == player.current_playlist_index:
                player.current_playlist_index = -1
            elif idx < player.current_playlist_index:
                player.current_playlist_index -= 1
            del player.playlist[idx]
            if idx < len(player.playlist):
                self.selected_indices[player.index] = idx
            elif player.playlist:
                self.selected_indices[player.index] = max(0, idx - 1)
            else:
                self.selected_indices.pop(player.index, None)
            self.update_playlist(player, listbox)
            if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
                player.update_playlist_listbox(player.playlist_listbox)

    def adjust_height(self):
            if not self.winfo_exists():
                return
            num_players = len(self.app.players)
            base_height_per_player = 250  # Increased from 220 to ensure playlist controls are fully visible
            button_height = 22  # Approximate height of Add Player button (20 + 2 padding)
            if num_players <= 3:
                total_height = max(300, base_height_per_player * num_players + 40 + (button_height if num_players > 0 else 0))
                max_height = min(total_height, self.winfo_screenheight() - 100)
                self.geometry(f"450x{max_height}")
                logging.debug("Adjusted DJD height to %d for %d players (single column, button=%s)", 
                              max_height, num_players, "included" if num_players > 0 else "excluded")
            else:
                left_count = (num_players + 1) // 2
                right_count = num_players - left_count
                max_players_per_column = max(left_count, right_count)
                total_height = max(300, base_height_per_player * max_players_per_column + 40 + button_height)
                max_height = min(total_height, self.winfo_screenheight() - 100)
                total_width = 900  # Increased initial width for better layout
                self.geometry(f"{total_width}x{max_height}")
                if hasattr(self, 'paned_window'):
                    self.paned_window.sashpos(0, total_width // 2)  # Ensure sash stays centered after resize
                    self.paned_window.update_idletasks()
                logging.debug("Adjusted DJD to %dx%d for %d players (two columns: %d left, %d right, button=included)", 
                              total_width, max_height, num_players, left_count, right_count)
            self.update_idletasks()

    def set_player_volume(self, player, value):
        volume = int(float(value))
        player.intended_volume = volume
        player.player.audio_set_volume(volume)
        self.volume_bars[player.index].set(volume)
        player.volume_bar.set(volume)
        logging.debug("DJD set volume to %d for Player %d", volume, player.index + 1)
        self.after(100, lambda: self._verify_player_volume(player))

    def _verify_player_volume(self, player):
        actual_volume = player.player.audio_get_volume()
        if actual_volume != player.intended_volume and player.intended_volume is not None:
            logging.warning("DJD volume mismatch for Player %d: expected %d, got %d. Correcting.", 
                            player.index + 1, player.intended_volume, actual_volume)
            player.player.audio_set_volume(player.intended_volume)
            self.volume_bars[player.index].set(player.intended_volume)
            player.volume_bar.set(player.intended_volume)
        else:
            logging.debug("DJD volume verified for Player %d: %d", player.index + 1, actual_volume)

    def jump_to_volume_position(self, event, player):
        volume_bar = self.volume_bars[player.index]
        slider_width = volume_bar.winfo_width()
        if slider_width <= 0:
            return "break"
        current_value = volume_bar.get()
        handle_width = 8
        handle_center = (current_value / 100) * (slider_width - handle_width) + (handle_width / 2)
        handle_left = handle_center - (handle_width / 2)
        handle_right = handle_center + (handle_width / 2)
        if handle_left <= event.x <= handle_right:
            return
        percentage = min(100, max(0, (event.x / slider_width) * 100))
        volume = int(percentage)
        player.intended_volume = volume
        player.player.audio_set_volume(volume)
        volume_bar.set(volume)
        player.volume_bar.set(volume)
        logging.debug("DJD clicked volume bar for Player %d to %d%% (%d)", player.index + 1, percentage, volume)
        self.after(100, lambda: self._verify_player_volume(player))
        return "break"

    def seek_to_position(self, event, player):
        if player.player.get_media():
            duration = player.player.get_length()
            if duration > 0 and event.widget.winfo_width() > 0:
                percentage = min(100, max(0, (event.x / event.widget.winfo_width()) * 100))
                position = int((percentage / 100) * duration)
                player.player.set_time(position)
                event.widget.set(percentage)
                logging.debug("Seeked Player %d to %.1f%% (%d ms)", player.index + 1, percentage, position)

    def swap_player(self, src_idx, dst_idx):
        if src_idx != dst_idx:
            logging.debug("Swapping Player %d with Player %d via dropdown", src_idx + 1, dst_idx + 1)
            self.app.swap_players(src_idx, dst_idx)
            for i in range(len(self.app.players)):
                self.swap_vars[i].set(f"P{i+1}")
            self.update_dashboard()

    def skip_forward(self, player, milliseconds):
        if player.player.get_media():
            duration = player.player.get_length()
            if duration > 0:
                new_time = min(player.player.get_time() + milliseconds, duration)
                player.player.set_time(new_time)
                logging.debug("Skipped Player %d forward %d ms to %d", player.index + 1, milliseconds, new_time)

    def set_mode(self, player, mode):
            mode_map = {
                "Shuffle": "random_repo",
                "Playlist": "playlist",
                "Next Repo": "next_repo",
                "Random Playlist": "random_playlist",
                "Repeat": "repeat"  # Added Repeat mode
            }
            player.next_mode = mode_map[mode]
            if player.next_mode == "random_playlist" and player.playlist:
                player.next_playlist_index = random.randrange(len(player.playlist))
                if player.current_playlist_index == player.next_playlist_index and len(player.playlist) > 1:
                    player.next_playlist_index = (player.next_playlist_index + 1) % len(player.playlist)
            player.update_mode_button_text()
            self.mode_vars[player.index].set(self._display_mode(player.next_mode))
            logging.debug("Set Player %d mode to %s", player.index + 1, mode)
            self.update_playlist(player, self.playlist_boxes[player.index])

    def handle_playlist_drop(self, event, player):
        logging.debug("Playlist drop on Player %d: %s", player.index + 1, event.data)
        data = event.data.strip()
        added_files = []
        if data.startswith('{') and data.endswith('}') and ' ' not in data:
            file_path = data[1:-1].strip()
            if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
                added_files.append(file_path)
        elif ' ' in data:
            paths = re.findall(r'(?:{[^{}]*}|[^{}\s]+)', data)
            added_files = [path.strip('{}"\'') for path in paths 
                           if os.path.isfile(path.strip('{}"\'')) and path.strip('{}"\'').lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))]
        else:
            file_path = data
            if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
                added_files.append(file_path)

        if added_files:
            player.playlist.extend(added_files)
            listbox = self.playlist_boxes[player.index]
            self.update_playlist(player, listbox)
            listbox.yview_moveto(1.0)
            if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
                player.update_playlist_listbox(player.playlist_listbox)
                player.playlist_listbox.yview_moveto(1.0)
            logging.debug("Added %d files to Player %d playlist", len(added_files), player.index + 1)

    def _play_next_and_update(self, player):
        player.play_next_video()
        if self.winfo_exists():
            self.update_dashboard()

    def raise_player_5(self):
        if len(self.app.players) >= 5:
            self.app.raise_screen_5()

    def on_close(self):
        self.destroy()
        self.app.dj_dashboard = None
        logging.debug("DJ Dashboard closed")

    def _display_mode(self, mode):
            mode_map = {
                "random_repo": "Shuffle",
                "playlist": "Playlist",
                "next_repo": "Next Repo",
                "random_playlist": "Random Playlist",
                "repeat": "Repeat"  # Added Repeat mode
            }
            return mode_map.get(mode, "Shuffle")

    
class VideoPlayerApp:
    def __init__(self, root):
            logging.debug("Initializing VideoPlayerApp")
            self.root = root
            self.root.title("Ultiplay")
            self.root.geometry("1200x800")
            self.pre_smm_geometry = "1200x800+100+100"
            self.dj_dashboard = None
            self.all_paused = False
            self.pre_fullscreen_state = None
            self.pre_smm_positions = []
            self.smm_mode = False
            self.canvas = tk.Canvas(self.root, width=1200, height=800)
            self.players = []
            self.player_positions = []
            self.dj_dashboard = None
            self.setup_context_menu()

            # Set window icon
            self.icon_path = os.path.join(sys._MEIPASS, 'appicon.ico') if getattr(sys, 'frozen', False) else r"C:\Users\march\OneDrive\Desktop\Ultiplay\appicon.ico"
            if os.path.exists(self.icon_path):
                try:
                    self.root.iconbitmap(self.icon_path)
                    logging.debug("Set window icon to %s", self.icon_path)
                except Exception as e:
                    logging.warning("Failed to set window icon: %s", str(e))
            else:
                logging.warning("Icon file not found at %s", self.icon_path)

            self.is_fullscreen = False
            self.is_maximized = False
            self.pre_fullscreen_positions = []
            self.swap_first = None
            self.swap_history = []
            self.last_resize_time = 0
            self.last_selected_player = None
            self.previous_geometry = "1200x800"

            # Create canvas
            self.canvas.pack(fill=tk.BOTH, expand=True)
            self.background_image = None
            self.background_photo = None

            # Enable drag-and-drop on background
            self.canvas.drop_target_register(DND_FILES)
            self.canvas.dnd_bind('<<Drop>>', self.handle_background_drop)

            # Set default background and force initial display
            self.bg_file_path = self.create_default_background()
            self.resize_background(1200, 800)
            self.canvas.update_idletasks()
            self.canvas.update()

            # Context menu
            self.context_menu = tk.Menu(self.root, tearoff=0)
            self.context_menu.add_command(label="Add Player", command=lambda: self.add_player_with_event())
            self.context_menu.add_command(label="Set Background", command=self.set_background)
            self.context_menu.add_command(label="Save Layout", command=self.save_layout)
            self.context_menu.add_command(label="Load Layout", command=self.load_layout)
            self.context_menu.add_command(label="Open DJD", command=self.toggle_dj_dashboard)
            self.context_menu.add_command(label="Reset Key Bindings", command=self.refresh_bindings)
            self.context_menu.add_command(label="Recover All Players", command=self.recover_all_players)
            self.canvas.bind("<Button-3>", self.show_context_menu)
            self.root.bind_all("<Control-s>", self.save_layout)

            # Initial bindings and focus
            self.refresh_bindings()
            self.root.focus_force()
            self.root.bind("<FocusIn>", lambda e: self.on_focus_in())
            self.root.after(5000, self.check_focus)

            # Global bindings with bind_all
            self.root.bind_all("<space>", self.toggle_play_pause)
            self.root.bind("<Configure>", self.handle_resize)
            self.root.bind("<F11>", self.toggle_fullscreen)
            self.root.bind("f", self.toggle_maximize_screen)
            self.root.bind("b", self.raise_screen_5)
            self.root.bind("<Escape>", self.handle_escape)

            for i in range(1, 10):
                self.root.bind_all(f"{i}", lambda e, idx=i-1: self.select_for_swap(idx))
            self.root.bind_all("n", self.handle_next)
            self.root.bind_all(".", self.handle_skip)

    def toggle_fullscreen(self, event=None):
            """Toggle the main application window between fullscreen and windowed mode."""
            current_state = self.root.attributes('-fullscreen')
            new_state = not current_state
            if new_state and not current_state:
                # Store pre-fullscreen positions before entering fullscreen
                self.pre_fullscreen_positions = []
                for i, player in enumerate(self.players):
                    try:
                        x, y = self.canvas.coords(player.frame_id)
                    except tk.TclError:
                        x, y = 50, 50
                    width = player.frame.winfo_width()
                    height = player.frame.winfo_height()
                    self.pre_fullscreen_positions.append({'x': x, 'y': y, 'width': width, 'height': height})
                    logging.debug("Stored pre-fullscreen state for Player %d: %dx%d at (%d, %d)", 
                                 player.index + 1, width, height, x, y)
            self.root.attributes('-fullscreen', new_state)
            self.is_fullscreen = new_state
            logging.debug("Toggled fullscreen: %s", "enabled" if new_state else "disabled")
            if new_state:
                # Resize background to fullscreen dimensions
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                self.resize_background(screen_width, screen_height)
                logging.debug("Resized background for fullscreen: %dx%d", screen_width, screen_height)
            else:
                # Exit to small window and restore pre-fullscreen positions
                self.root.geometry("800x600")
                self.is_maximized = False
                self.smm_mode = False
                if self.pre_fullscreen_positions:
                    canvas_width = 800
                    canvas_height = 600
                    scale = min(canvas_width / 1200, canvas_height / 800)
                    for i, player in enumerate(self.players):
                        if i < len(self.pre_fullscreen_positions):
                            pos = self.pre_fullscreen_positions[i]
                            new_width = int(pos['width'] * scale)
                            new_height = int(pos['height'] * scale)
                            new_x = min(pos['x'] * scale, canvas_width - new_width)
                            new_y = min(pos['y'] * scale, canvas_height - new_height)
                            new_x = max(0, new_x)
                            new_y = max(0, new_y)
                            try:
                                player.set_size_and_position(new_width, new_height, new_x, new_y)
                                player.configure_for_width(new_width)
                                self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                                if hasattr(player, 'player') and player.player.get_media():
                                    player.player.video_set_scale(0)  # Reset scale to fit new size
                                logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                             player.index + 1, new_width, new_height, new_x, new_y)
                            except tk.TclError as e:
                                logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                                player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                                self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300}
                else:
                    self.arrange_players()
                self.pre_fullscreen_positions = []
                self.pre_smm_positions = []
                self.update_background()  # Ensure background fits small window
            self.root.update_idletasks()
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.update_dashboard()

    def handle_escape(self, event=None):
            """Handle Escape key to exit fullscreen modes, restoring pre-fullscreen positions."""
            if not self.is_fullscreen:
                return

            fullscreen_player = next((p for p in self.players if p.is_fullscreen), None)

            if fullscreen_player and self.pre_fullscreen_state:
                fullscreen_player.is_fullscreen = False
                fullscreen_player.fullscreen_button.config(text="⛶")
                
                if fullscreen_player.pre_fullscreen_coords:
                    try:
                        fullscreen_player.set_size_and_position(
                            fullscreen_player.pre_fullscreen_coords['width'],
                            fullscreen_player.pre_fullscreen_coords['height'],
                            fullscreen_player.pre_fullscreen_coords['x'],
                            fullscreen_player.pre_fullscreen_coords['y']
                        )
                    except tk.TclError as e:
                        logging.error("Failed to restore fullscreen player size: %s", e)
                    fullscreen_player.pre_fullscreen_coords = None

                if self.pre_fullscreen_state['is_maximized']:
                    self.root.attributes('-fullscreen', True)
                    self.is_maximized = True
                    self.is_fullscreen = True
                    for i, player in enumerate(self.players):
                        if i < len(self.pre_fullscreen_state['players']):
                            try:
                                (x, y), width, height = self.pre_fullscreen_state['players'][i]
                                player.set_size_and_position(width, height, x, y)
                                player.configure_for_width(width)
                            except tk.TclError as e:
                                logging.error("Failed to restore player %d in SMM: %s", i + 1, e)
                    self.pre_fullscreen_state = None
                    logging.debug("Exited per-player fullscreen, restored to fullscreen SMM mode")
                else:
                    self.root.attributes('-fullscreen', False)
                    self.is_maximized = False
                    self.is_fullscreen = False
                    self.root.geometry("800x600")
                    if self.pre_fullscreen_positions:
                        canvas_width = 800
                        canvas_height = 600
                        scale = min(canvas_width / 1200, canvas_height / 800)
                        for i, player in enumerate(self.players):
                            if i < len(self.pre_fullscreen_positions):
                                pos = self.pre_fullscreen_positions[i]
                                new_width = int(pos['width'] * scale)
                                new_height = int(pos['height'] * scale)
                                new_x = min(pos['x'] * scale, canvas_width - new_width)
                                new_y = min(pos['y'] * scale, canvas_height - new_height)
                                new_x = max(0, new_x)
                                new_y = max(0, new_y)
                                try:
                                    player.set_size_and_position(new_width, new_height, new_x, new_y)
                                    player.configure_for_width(new_width)
                                    self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                                    if hasattr(player, 'player') and player.player.get_media():
                                        player.player.video_set_scale(0)  # Reset scale to fit new size
                                    logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                                 player.index + 1, new_width, new_height, new_x, new_y)
                                except tk.TclError as e:
                                    logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                                    player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                                    self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300}
                    else:
                        self.arrange_players()
                    self.pre_fullscreen_state = None
                    self.pre_fullscreen_positions = []
                    self.update_background()  # Ensure background fits small window
                    logging.debug("Exited per-player fullscreen, restored to small window mode")

            elif self.is_fullscreen and not fullscreen_player:
                self.root.attributes('-fullscreen', False)
                self.is_fullscreen = False
                self.is_maximized = False
                self.smm_mode = False
                self.root.geometry("800x600")
                positions = self.pre_smm_positions if self.pre_smm_positions else self.pre_fullscreen_positions
                if positions:
                    canvas_width = 800
                    canvas_height = 600
                    scale = min(canvas_width / 1200, canvas_height / 800)
                    for i, player in enumerate(self.players):
                        if i < len(positions):
                            pos = positions[i]
                            new_width = int(pos['width'] * scale)
                            new_height = int(pos['height'] * scale)
                            new_x = min(pos['x'] * scale, canvas_width - new_width)
                            new_y = min(pos['y'] * scale, canvas_height - new_height)
                            new_x = max(0, new_x)
                            new_y = max(0, new_y)
                            try:
                                player.set_size_and_position(new_width, new_height, new_x, new_y)
                                player.configure_for_width(new_width)
                                self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                                if hasattr(player, 'player') and player.player.get_media():
                                    player.player.video_set_scale(0)  # Reset scale to fit new size
                                logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                             player.index + 1, new_width, new_height, new_x, new_y)
                            except tk.TclError as e:
                                logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                                player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                                self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300}
                else:
                    self.arrange_players()
                self.pre_smm_positions = []
                self.pre_fullscreen_positions = []
                self.update_background()  # Ensure background fits small window
                logging.debug("Exited global fullscreen or SMM, restored to small window mode")

            self.canvas.update_idletasks()
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.update_dashboard()

    def resize_players_to_fit(self, window_width, window_height):
            """Resize all players to fit within the specified window dimensions."""
            if not self.players:
                logging.debug("No players to resize")
                return
            # Calculate scaling factor based on window size relative to original 1200x800
            scale = min(window_width / 1200, window_height / 800)
            canvas_width = window_width
            canvas_height = window_height
            for i, player in enumerate(self.players):
                # Get current dimensions and position
                current_width = self.player_positions[i]['width'] if i < len(self.player_positions) else 400
                current_height = self.player_positions[i]['height'] if i < len(self.player_positions) else 300
                current_x = self.player_positions[i]['x'] if i < len(self.player_positions) else 50
                current_y = self.player_positions[i]['y'] if i < len(self.player_positions) else 50
                # Scale dimensions
                new_width = int(current_width * scale)
                new_height = int(current_height * scale)
                # Reposition to keep players within canvas bounds
                new_x = min(current_x * scale, canvas_width - new_width)
                new_y = min(current_y * scale, canvas_height - new_height)
                new_x = max(0, new_x)
                new_y = max(0, new_y)
                # Update player and positions
                try:
                    player.set_size_and_position(new_width, new_height, new_x, new_y)
                    player.configure_for_width(new_width)
                    self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                    if hasattr(player, 'player') and player.player.get_media():
                        player.player.video_set_scale(0)  # Reset scale to fit new size
                    logging.debug("Resized Player %d to %dx%d at (%d,%d)", player.index + 1, new_width, new_height, new_x, new_y)
                except tk.TclError as e:
                    logging.error("Failed to resize Player %d: %s", player.index + 1, e)
            self.canvas.update_idletasks()

    def select_for_swap(self, index):
        if not (0 <= index < len(self.players)):
            logging.info("Invalid player index %d for swap", index + 1)
            return
        if self.swap_first is None:
            self.swap_first = index
            logging.info("Selected Player %d. Press 'n' for next, '.' to skip 20%, or another number to swap", index + 1)
        else:
            self.swap_players(self.swap_first, index)
            self.swap_first = None

    def handle_next(self, event):
        if self.swap_first is not None and 0 <= self.swap_first < len(self.players):
            self.play_next_for_player(self.swap_first)
            self.swap_first = None

    def handle_skip(self, event):
        if self.swap_first is not None and 0 <= self.swap_first < len(self.players):
            self.skip_forward_20_percent(self.swap_first)
            self.swap_first = None

    def toggle_dj_dashboard(self, event=None):
        """Toggle the DJ Dashboard open or closed with a single key press."""
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            # Close the DJD
            self.dj_dashboard.destroy()
            self.dj_dashboard = None
            logging.debug("Closed DJ Dashboard")
        else:
            # Open the DJD
            self.dj_dashboard = DJDashboard(self.root, self)
            self.dj_dashboard.update_dashboard()  # Initial update
            self.root.update_idletasks()
            logging.debug("Opened DJ Dashboard")

    def create_default_background(self):
        default_path = os.path.join(sys._MEIPASS, 'default_background.png') if getattr(sys, 'frozen', False) else r"C:\Users\march\OneDrive\Desktop\Ultiplay\default_background.png"
        if not os.path.exists(default_path):
            img = Image.new('RGB', (1200, 800), color='black')
            draw = ImageDraw.Draw(img)
            draw.text((600, 400), "Ultiplay", fill='white', anchor='mm', font_size=40)
            img.save(default_path)
            logging.debug("Created default background at %s", default_path)
        return default_path

    def swap_players(self, idx1, idx2, event=None):
            """Swap two players, maintaining SMM layout and seek positions."""
            if not (0 <= idx1 < len(self.players) and 0 <= idx2 < len(self.players)) or idx1 == idx2:
                logging.info("Invalid swap indices: %d, %d", idx1 + 1, idx2 + 1)
                return

            player1, player2 = self.players[idx1], self.players[idx2]
            logging.debug("Swapping Player %d with Player %d", idx1 + 1, idx2 + 1)

            # Pause all players and mark as swapping to prevent UI updates during transition
            for player in self.players:
                if player.player.is_playing():
                    player.player.pause()
                player.is_swapping = True
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.update_paused = True

            # Capture current state for both players
            p1_pos = player1.player.get_time() if player1.player.get_media() else 0
            p2_pos = player2.player.get_time() if player2.player.get_media() else 0
            p1_playing = player1.player.is_playing()
            p2_playing = player2.player.is_playing()
            p1_file, p2_file = player1.current_file, player2.current_file
            p1_playlist, p2_playlist = player1.playlist[:], player2.playlist[:]
            p1_index, p2_index = player1.current_playlist_index, player2.current_playlist_index
            p1_mode, p2_mode = player1.next_mode, player2.next_mode
            p1_layer, p2_layer = player1.layer, player2.layer

            # Perform the swap of player attributes
            player1.current_file, player2.current_file = p2_file, p1_file
            player1.playlist, player2.playlist = p2_playlist, p1_playlist
            player1.current_playlist_index, player2.current_playlist_index = p2_index, p1_index
            player1.next_mode, player2.next_mode = p2_mode, p1_mode
            player1.layer, player2.layer = p2_layer, p1_layer
            player1.update_mode_button_text()
            player2.update_mode_button_text()

            # Reload players with swapped content
            self._reload_player(player1, p2_file, p2_pos, p2_playing)
            self._reload_player(player2, p1_file, p1_pos, p1_playing)

            # Reapply layer order without recreating frames
            sorted_players = sorted(self.players, key=lambda p: p.layer)
            for player in sorted_players:
                self.canvas.tag_raise(player.frame_id)
                logging.debug("Raised Player %d to layer %d (frame_id: %s)", 
                             player.index + 1, player.layer, player.frame_id)

            # Update player UI titles
            player1.update_title_label()
            player2.update_title_label()

            # Adjust layout based on mode
            if self.is_maximized:
                self._arrange_players_in_smm()
                logging.debug("Reapplied SMM layout after swapping Player %d with Player %d", idx1 + 1, idx2 + 1)
            else:
                self.arrange_players()
                logging.debug("Reapplied normal layout after swapping Player %d with Player %d", idx1 + 1, idx2 + 1)

            # Resume updates and refresh DJ Dashboard
            self.root.after(2000, self._resume_updates)
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.update_paused = False
                self.dj_dashboard.update_dashboard()  # Immediate update
                self.root.after(100, self.dj_dashboard.update_dashboard)  # Ensure one more update for stability

            # Log swap history
            self.swap_history.append((idx1 + 1, idx2 + 1))
            self.swap_first = None
            logging.debug("Swap completed: Player %d <-> Player %d", idx1 + 1, idx2 + 1)

    def _reload_player(self, player, file, position, is_playing):
            """Reload a player with the given file, seek position, and play state."""
            if file and os.path.isfile(file):
                try:
                    # Stop current playback
                    player.player.stop()
                    player.current_file = file
                    # Set the new media using set_mrl (direct method for vlc.MediaPlayer)
                    player.player.set_mrl(file)
                    # Play and immediately pause to allow VLC to parse the media
                    player.player.play()
                    self.root.after(100)  # Short delay to let VLC parse the media
                    player.player.pause()
                    # Wait for media to be ready
                    max_attempts = 50  # 5 seconds at 100ms intervals
                    attempts = 0
                    while attempts < max_attempts:
                        state = player.player.get_state()
                        if state in (vlc.State.Playing, vlc.State.Paused, vlc.State.Ended):
                            break
                        self.root.after(100)
                        attempts += 1
                    if attempts >= max_attempts:
                        logging.warning("Player %d: Media not ready after %d attempts", player.index + 1, max_attempts)
                    # Set seek position
                    player.player.set_time(position)
                    # Verify seek position
                    actual_pos = player.player.get_time()
                    if abs(actual_pos - position) > 1000:  # Allow 1-second discrepancy
                        logging.warning("Player %d: Seek position mismatch, expected %d, got %d", 
                                       player.index + 1, position, actual_pos)
                    # Manage play state
                    if is_playing:
                        player.player.play()
                        player.toggle_button.config(text="||")
                    else:
                        player.player.pause()
                        player.toggle_button.config(text="▶")
                    logging.debug("Reloaded Player %d with file %s at position %d, is_playing=%s", 
                                 player.index + 1, file, position, is_playing)
                except Exception as e:
                    logging.error("Failed to reload Player %d: %s", player.index + 1, e)
            else:
                logging.warning("Player %d: Invalid or missing file for reload: %s", player.index + 1, file)

    def _arrange_players_in_smm(self):
            """Arrange players in Screen Maximize Mode (SMM) layout with customization support."""
            num_players = len(self.players)
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            # Check for customized positions
            all_customized = all(pos.get('customized', False) for pos in self.player_positions[:num_players])
            if all_customized and len(self.player_positions) == num_players:
                logging.debug("Using customized SMM positions for %d players", num_players)
                for i, player in enumerate(self.players):
                    pos = self.player_positions[i]
                    try:
                        # Capture current state
                        current_pos = player.player.get_time() if player.player.get_media() else 0
                        current_playing = player.player.is_playing()
                        # Apply new position and size
                        player.set_size_and_position(pos['width'], pos['height'], pos['x'], pos['y'])
                        player.configure_for_width(pos['width'])
                        if hasattr(player, 'player') and player.player.get_media():
                            # Avoid video_set_scale to prevent potential playback reset
                            # player.player.video_set_scale(0)  # Removed to prevent interference
                            # Verify seek position after resizing
                            actual_pos = player.player.get_time()
                            if abs(actual_pos - current_pos) > 1000:
                                logging.warning("Player %d: Seek position changed after resize, expected %d, got %d", 
                                               player.index + 1, current_pos, actual_pos)
                                player.player.set_time(current_pos)  # Restore seek position
                            # Restore play state
                            if current_playing:
                                player.player.play()
                            else:
                                player.player.pause()
                        logging.debug("Applied custom position for Player %d: %dx%d at (%d, %d)", 
                                     player.index + 1, pos['width'], pos['height'], pos['x'], pos['y'])
                    except tk.TclError as e:
                        logging.error("Failed to apply custom position for Player %d: %s", player.index + 1, e)
                self.canvas.update_idletasks()
                return

            # Define default layouts
            layouts = []
            if num_players == 1:
                layouts = [(0, 0, screen_width, screen_height)]
            elif num_players == 2:
                half_height = screen_height // 2
                layouts = [(0, 0, screen_width, half_height), (0, half_height, screen_width, half_height)]
            elif num_players == 3:
                top_height = int(screen_height * 0.6)
                bottom_height = screen_height - top_height
                half_width = screen_width // 2
                layouts = [
                    (0, 0, screen_width, top_height),
                    (0, top_height, half_width, bottom_height),
                    (half_width, top_height, half_width, bottom_height)
                ]
            elif num_players == 4:
                half_width = screen_width // 2
                half_height = screen_height // 2
                layouts = [
                    (0, 0, half_width, half_height),
                    (half_width, 0, half_width, half_height),
                    (0, half_height, half_width, half_height),
                    (half_width, half_height, half_width, half_height)
                ]
            elif num_players == 5:
                half_width = screen_width // 2
                half_height = screen_height // 2
                small_width = half_width // 3
                small_height = half_height // 3
                center_x = (screen_width - small_width) // 2
                center_y = (screen_height - small_height) // 2
                layouts = [
                    (0, 0, half_width, half_height),
                    (half_width, 0, half_width, half_height),
                    (0, half_height, half_width, half_height),
                    (half_width, half_height, half_width, half_height),
                    (center_x, center_y, small_width, small_height)
                ]
            elif num_players == 6:
                # Refined proportions from saved layout
                five_sevenths_width = (screen_width * 5) // 7
                bottom_height = int(screen_width * 0.1875)
                top_height = screen_height - bottom_height
                right_width = screen_width - five_sevenths_width
                right_half_height = top_height // 2
                layouts = [
                    (0, 0, five_sevenths_width, top_height),           # Player 1: Top-left
                    (0, top_height, screen_width // 3, bottom_height),  # Player 2: Bottom-left
                    (screen_width // 3, top_height, screen_width // 3, bottom_height),  # Player 3: Bottom-center
                    (2 * (screen_width // 3), top_height, screen_width // 3, bottom_height),  # Player 4: Bottom-right
                    (five_sevenths_width, 0, right_width, right_half_height),  # Player 5: Top-right
                    (five_sevenths_width, right_half_height, right_width, right_half_height)  # Player 6: Middle-right
                ]
            elif num_players == 7:
                half_width = screen_width // 2
                quarter_width = screen_width // 4
                third_height = screen_height // 3
                two_thirds_height = (screen_height * 2) // 3
                layouts = [
                    (half_width, 0, half_width, two_thirds_height),
                    (0, 0, half_width, third_height),
                    (0, third_height, half_width, third_height),
                    (0, 2 * third_height, half_width, third_height),
                    (half_width, 2 * third_height, quarter_width, third_height),
                    (half_width + quarter_width, 2 * third_height, quarter_width, third_height),
                    (half_width + quarter_width, third_height, quarter_width, third_height)
                ]
            elif num_players == 8:
                third_width = screen_width // 3
                third_height = screen_height // 3
                layouts = [
                    (0, 0, third_width, third_height),
                    (third_width, 0, third_width, third_height),
                    (2 * third_width, 0, third_width, third_height),
                    (0, third_height, third_width, third_height),
                    (third_width, third_height, third_width, third_height),
                    (2 * third_width, third_height, third_width, third_height),
                    (0, 2 * third_height, third_width, third_height),
                    (third_width, 2 * third_height, third_width, third_height)
                ]
            elif num_players == 9:
                third_width = screen_width // 3
                third_height = screen_height // 3
                layouts = [
                    (0, 0, third_width, third_height),
                    (third_width, 0, third_width, third_height),
                    (2 * third_width, 0, third_width, third_height),
                    (0, third_height, third_width, third_height),
                    (third_width, third_height, third_width, third_height),
                    (2 * third_width, third_height, third_width, third_height),
                    (0, 2 * third_height, third_width, third_height),
                    (third_width, 2 * third_height, third_width, third_height),
                    (2 * third_width, 2 * third_height, third_width, third_height)
                ]
            elif num_players == 10:
                half_width = screen_width // 2
                fifth_height = screen_height // 5
                layouts = [
                    (0, 0, half_width, fifth_height),
                    (half_width, 0, half_width, fifth_height),
                    (0, fifth_height, half_width, fifth_height),
                    (half_width, fifth_height, half_width, fifth_height),
                    (0, 2 * fifth_height, half_width, fifth_height),
                    (half_width, 2 * fifth_height, half_width, fifth_height),
                    (0, 3 * fifth_height, half_width, fifth_height),
                    (half_width, 3 * fifth_height, half_width, fifth_height),
                    (0, 4 * fifth_height, half_width, fifth_height),
                    (half_width, 4 * fifth_height, half_width, fifth_height)
                ]
            else:  # 11+ players
                rows = math.ceil(math.sqrt(num_players))
                cols = math.ceil(num_players / rows)
                cell_width = screen_width // cols
                cell_height = screen_height // rows
                layouts = []
                for i in range(num_players):
                    row = i // cols
                    col = i % cols
                    layouts.append((col * cell_width, row * cell_height, cell_width, cell_height))

            # Apply layouts
            for i, player in enumerate(self.players):
                if i < len(layouts):
                    x, y, width, height = layouts[i]
                    try:
                        # Capture current state
                        current_pos = player.player.get_time() if player.player.get_media() else 0
                        current_playing = player.player.is_playing()
                        # Apply new position and size
                        player.set_size_and_position(width, height, x, y)
                        player.configure_for_width(width)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        if hasattr(player, 'player') and player.player.get_media():
                            # Avoid video_set_scale to prevent potential playback reset
                            # player.player.video_set_scale(0)  # Removed to prevent interference
                            # Verify seek position after resizing
                            actual_pos = player.player.get_time()
                            if abs(actual_pos - current_pos) > 1000:
                                logging.warning("Player %d: Seek position changed after resize, expected %d, got %d", 
                                               player.index + 1, current_pos, actual_pos)
                                player.player.set_time(current_pos)  # Restore seek position
                            # Restore play state
                            if current_playing:
                                player.player.play()
                            else:
                                player.player.pause()
                        logging.debug("Positioned Player %d: %dx%d at (%d, %d)", 
                                     player.index + 1, width, height, x, y)
                    except tk.TclError as e:
                        logging.error("Failed to position Player %d: %s", player.index + 1, e)
                        player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                        self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300, 'customized': False}
                else:
                    player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                    self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300, 'customized': False}
                    logging.warning("No layout for Player %d, using default position", player.index + 1)
            self.canvas.update_idletasks()

    def play_next_for_player(self, index, event=None):
        if 0 <= index < len(self.players):
            self.players[index].play_next_video()
            logging.debug("Triggered next video for Player %d", index + 1)
        else:
            logging.debug("Invalid player index %d for next video", index)
        self.swap_first = None

    def skip_forward_20_percent(self, index, event=None):
        if 0 <= index < len(self.players):
            player = self.players[index]
            if player.player.get_media():
                duration = player.player.get_length()
                if duration > 0:
                    current_time = player.player.get_time()
                    skip_time = int(duration * 0.2)
                    new_time = min(current_time + skip_time, duration)
                    player.player.set_time(new_time)
                    logging.debug("Skipped Player %d forward 20%% to %d ms", index + 1, new_time)
        else:
            logging.debug("Invalid player index %d for 20%% skip", index)
        self.swap_first = None

    def handle_background_drop(self, event):
        logging.debug("Background drop: %s", event.data)
        data = event.data.strip()
        if ' ' in data:
            paths = re.findall(r'(?:{[^{}]*}|[^{}\s]+)', data)
            added_files = [path.strip('{}"\'') for path in paths if os.path.isfile(path.strip('{}"\''))
                        and path.strip('{}"\'').lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg'))]
        else:
            file_path = data[1:-1].strip() if data.startswith('{') and data.endswith('}') else data
            added_files = [file_path] if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')) else []

        if added_files:
            canvas_x = event.x_root - self.canvas.winfo_rootx()
            canvas_y = event.y_root - self.canvas.winfo_rooty()
            x = max(0, min(self.canvas.canvasx(canvas_x), self.canvas.winfo_width() - 400))
            y = max(0, min(self.canvas.canvasy(canvas_y), self.canvas.winfo_height() - 300))
            player = PlayerWidget(self.root, self, self.canvas, x, y, width=400, height=300)
            self.players.append(player)
            player.load_video(added_files[0])
            self.player_positions.append({'x': x, 'y': y, 'width': 400, 'height': 300})
            self._bind_number_keys()
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                logging.debug("Rebuilding DJD for new player (count=%d)", len(self.players))
                self.dj_dashboard.build_player_controls()
                self.dj_dashboard.update()
                self.dj_dashboard.update_dashboard()
            self.arrange_players()
        else:
            logging.debug("No valid video files in drop: %s", data)

    def handle_resize(self, event):
        current_time = time.time()
        if current_time - self.last_resize_time < 0.1 or self.is_maximized or self.is_fullscreen:
            return
        self.last_resize_time = current_time
        self.arrange_players()
        self.update_background(event)

    def arrange_players(self):
        if not self.players or self.is_maximized:
            return
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()
        player_count = len(self.players)
        rows = max(1, int((player_count + 1) ** 0.5))
        cols = (player_count + rows - 1) // rows
        cell_width = canvas_width // cols
        cell_height = canvas_height // rows
        for i, player in enumerate(self.players):
            if not self.canvas.tk.call('winfo', 'exists', player.frame_id):
                continue
            row = i // cols
            col = i % cols
            x = col * cell_width
            y = row * cell_height
            player.set_size_and_position(cell_width, cell_height, x, y)
            self.player_positions[i] = {'x': x, 'y': y, 'width': cell_width, 'height': cell_height}
        self.canvas.update_idletasks()

    def _bind_number_keys(self):
            """Bind number keys for player selection in SMM."""
            for key in range(1, 11):  # Support up to 10 players
                self.root.unbind(str(key))
                self.root.bind(str(key), lambda e, idx=key-1: self._pre_select_player(idx))
            self.root.bind("s", self._switch_to_shuffle)
            self.root.bind(".", self._skip_25_percent)

    def remove_player(self, player):
            """Remove a player and update the SMM layout if applicable."""
            if player not in self.players:
                logging.warning("Attempted to remove a player not in the list: Player %d", player.index + 1)
                return
            index = self.players.index(player)
            self.players.remove(player)
            if len(self.player_positions) > index:
                self.player_positions.pop(index)
            player.close()
            logging.debug("Removed Player %d, total players: %d", player.index + 1, len(self.players))
            
            if self.is_maximized:
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                num_players = len(self.players)

                while len(self.player_positions) < num_players:
                    self.player_positions.append({'x': 0, 'y': 0, 'width': 400, 'height': 300, 'customized': False})
                while len(self.player_positions) > num_players:
                    self.player_positions.pop()

                if num_players == 0:
                    # No players left, exit SMM
                    self.handle_escape(None)
                elif num_players == 1:
                    self.players[0].set_size_and_position(screen_width, screen_height, 0, 0)
                    self.player_positions[0] = {'x': 0, 'y': 0, 'width': screen_width, 'height': screen_height, 'customized': False}
                    self.players[0].configure_for_width(screen_width)
                elif num_players == 2:
                    half_height = screen_height // 2
                    for i, player in enumerate(self.players):
                        x, y = 0, i * half_height
                        player.set_size_and_position(screen_width, half_height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': screen_width, 'height': half_height, 'customized': False}
                        player.configure_for_width(screen_width)
                elif num_players == 3:
                    top_height = int(screen_height * 0.6)
                    bottom_height = screen_height - top_height
                    half_width = screen_width // 2
                    layouts = [
                        (0, 0, screen_width, top_height),
                        (0, top_height, half_width, bottom_height),
                        (half_width, top_height, half_width, bottom_height)
                    ]
                    for i, player in enumerate(self.players):
                        x, y, width, height = layouts[i]
                        player.set_size_and_position(width, height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        player.configure_for_width(width)
                elif num_players == 4:
                    half_width = screen_width // 2
                    half_height = screen_height // 2
                    layouts = [
                        (0, 0, half_width, half_height),
                        (half_width, 0, half_width, half_height),
                        (0, half_height, half_width, half_height),
                        (half_width, half_height, half_width, half_height)
                    ]
                    for i, player in enumerate(self.players):
                        x, y, width, height = layouts[i]
                        player.set_size_and_position(width, height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        player.configure_for_width(width)
                elif num_players == 5:
                    half_width = screen_width // 2
                    half_height = screen_height // 2
                    small_width = half_width // 3
                    small_height = half_height // 3
                    center_x = (screen_width - small_width) // 2
                    center_y = (screen_height - small_height) // 2
                    layouts = [
                        (0, 0, half_width, half_height),
                        (half_width, 0, half_width, half_height),
                        (0, half_height, half_width, half_height),
                        (half_width, half_height, half_width, half_height),
                        (center_x, center_y, small_width, small_height)
                    ]
                    for i, player in enumerate(self.players):
                        x, y, width, height = layouts[i]
                        player.set_size_and_position(width, height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        player.configure_for_width(width)
                elif num_players >= 6:
                    self._finalize_render(screen_width, screen_height)

                self.canvas.update_idletasks()
                logging.debug("Updated SMM layout for %d players after removing Player %d", 
                             len(self.players), player.index + 1)
            else:
                self.arrange_players()

            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.build_player_controls()
                self.dj_dashboard.update_dashboard()
            self._bind_number_keys()

    def _pre_select_player(self, index):
        if not self.is_maximized:
            return
        if not (0 <= index < len(self.players)):
            logging.info("Invalid player index %d", index + 1)
            return
        self.last_selected_player = index
        logging.info("Selected Player %d in SMM. Press 's' to shuffle, '.' to skip 25%%, or 'n' for next.", index + 1)

    def _switch_to_shuffle(self, event):
        if self.is_maximized and self.last_selected_player is not None and 0 <= self.last_selected_player < len(self.players):
            index = self.last_selected_player
            self.players[index].next_mode = "random_repo"
            self.players[index].update_mode_button_text()
            logging.info("Switched Player %d to shuffle", index + 1)
            self.last_selected_player = None

    def _skip_25_percent(self, event):
        if self.is_maximized and self.last_selected_player is not None and 0 <= self.last_selected_player < len(self.players):
            index = self.last_selected_player
            player = self.players[index]
            if player.player.get_media():
                duration = player.player.get_length()
                if duration > 0:
                    new_time = min(player.player.get_time() + duration // 4, duration)
                    player.player.set_time(new_time)
                    logging.info("Skipped 25%% for Player %d to %d ms", index + 1, new_time)
            self.last_selected_player = None

    def raise_screen_5(self, event=None):
        """Raise Player 5 to the top layer, restoring its position if in fullscreen mode."""
        if len(self.players) < 5:
            return
        screen_5 = self.players[4]
        max_layer = max(p.layer for p in self.players) + 1
        screen_5.set_layer(max_layer)
        # Restore pre-fullscreen position if in fullscreen mode
        fullscreen_player = next((p for p in self.players if p.is_fullscreen), None)
        if fullscreen_player and screen_5 != fullscreen_player and hasattr(screen_5, 'pre_fullscreen_coords'):
            x, y = screen_5.pre_fullscreen_coords['x'], screen_5.pre_fullscreen_coords['y']
            width, height = screen_5.pre_fullscreen_coords['width'], screen_5.pre_fullscreen_coords['height']
            screen_5.set_size_and_position(width, height, x, y)
            logging.debug("Restored Player 5 to pre-fullscreen position: x=%d, y=%d, size=%dx%d", 
                         x, y, width, height)
        self.canvas.tag_raise(screen_5.frame_id)
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            self.dj_dashboard.update_dashboard()
        self.canvas.update_idletasks()
        logging.debug("Raised Player 5 to layer %d", max_layer)

    def _finalize_render(self, screen_width, screen_height):
            """Finalize SMM rendering for 6+ players."""
            num_players = len(self.players)
            if num_players <= 10:
                self._arrange_players_in_smm()
            else:
                rows = math.ceil(math.sqrt(num_players))
                cols = math.ceil(num_players / rows)
                cell_width = screen_width // cols
                cell_height = screen_height // rows
                for i, player in enumerate(self.players):
                    row = i // cols
                    col = i % cols
                    x = col * cell_width
                    y = row * cell_height
                    try:
                        player.set_size_and_position(cell_width, cell_height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': cell_width, 'height': cell_height, 'customized': False}
                        player.configure_for_width(cell_width)
                        if hasattr(player, 'player') and player.player.get_media():
                            player.player.video_set_scale(0)  # Reset scale to fit
                        logging.debug("Positioned Player %d in grid: %dx%d at (%d, %d)", 
                                     player.index + 1, cell_width, cell_height, x, y)
                    except tk.TclError as e:
                        logging.error("Failed to position Player %d in grid: %s", player.index + 1, e)
                        player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                        self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300, 'customized': False}
                self.canvas.update_idletasks()

    def toggle_maximize_screen(self, event=None):
            """Toggle Screen Maximize Mode (SMM), preserving pause states and positions."""
            logging.debug("Toggling maximize screen: current maximized=%s", self.is_maximized)
            self.is_maximized = not self.is_maximized
            self.smm_mode = self.is_maximized
            if self.is_maximized:
                self.pre_smm_geometry = self.root.winfo_geometry()
                self.pre_smm_positions = []
                self.all_paused = all(not player.player.is_playing() for player in self.players if player.current_file)
                logging.debug("All players paused before SMM: %s", self.all_paused)

                for player in self.players:
                    try:
                        x, y = self.canvas.coords(player.frame_id)
                    except tk.TclError:
                        x, y = 0, 0
                    width = player.frame.winfo_width()
                    height = player.frame.winfo_height()
                    self.pre_smm_positions.append({'x': x, 'y': y, 'width': width, 'height': height})
                    logging.debug("Stored pre-SMM state for Player %d: %dx%d at (%d, %d)",
                                player.index + 1, width, height, x, y)
                    if self.all_paused and player.current_file:
                        player.player.pause()
                        player.toggle_button.config(text="▶")
                        logging.debug("Ensured pause for Player %d before SMM resizing", player.index + 1)

                self.root.attributes('-fullscreen', True)
                screen_width = self.root.winfo_screenwidth()
                screen_height = self.root.winfo_screenheight()
                num_players = len(self.players)

                while len(self.player_positions) < num_players:
                    self.player_positions.append({'x': 0, 'y': 0, 'width': 400, 'height': 300, 'customized': False})
                while len(self.player_positions) > num_players:
                    self.player_positions.pop()

                if num_players == 1:
                    self.players[0].set_size_and_position(screen_width, screen_height, 0, 0)
                    self.player_positions[0] = {'x': 0, 'y': 0, 'width': screen_width, 'height': screen_height, 'customized': False}
                    self.players[0].configure_for_width(screen_width)
                elif num_players == 2:
                    half_height = screen_height // 2
                    for i, player in enumerate(self.players):
                        x, y = 0, i * half_height
                        player.set_size_and_position(screen_width, half_height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': screen_width, 'height': half_height, 'customized': False}
                        player.configure_for_width(screen_width)
                elif num_players == 3:
                    top_height = int(screen_height * 0.6)
                    bottom_height = screen_height - top_height
                    half_width = screen_width // 2
                    layouts = [
                        (0, 0, screen_width, top_height),
                        (0, top_height, half_width, bottom_height),
                        (half_width, top_height, half_width, bottom_height)
                    ]
                    for i, player in enumerate(self.players):
                        x, y, width, height = layouts[i]
                        player.set_size_and_position(width, height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        player.configure_for_width(width)
                elif num_players == 4:
                    half_width = screen_width // 2
                    half_height = screen_height // 2
                    layouts = [
                        (0, 0, half_width, half_height),
                        (half_width, 0, half_width, half_height),
                        (0, half_height, half_width, half_height),
                        (half_width, half_height, half_width, half_height)
                    ]
                    for i, player in enumerate(self.players):
                        x, y, width, height = layouts[i]
                        player.set_size_and_position(width, height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        player.configure_for_width(width)
                elif num_players == 5:
                    half_width = screen_width // 2
                    half_height = screen_height // 2
                    small_width = half_width // 3
                    small_height = half_height // 3
                    center_x = (screen_width - small_width) // 2
                    center_y = (screen_height - small_height) // 2
                    layouts = [
                        (0, 0, half_width, half_height),
                        (half_width, 0, half_width, half_height),
                        (0, half_height, half_width, half_height),
                        (half_width, half_height, half_width, half_height),
                        (center_x, center_y, small_width, small_height)
                    ]
                    for i, player in enumerate(self.players):
                        x, y, width, height = layouts[i]
                        player.set_size_and_position(width, height, x, y)
                        self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                        player.configure_for_width(width)
                elif num_players >= 6:
                    self._finalize_render(screen_width, screen_height)

                if self.all_paused:
                    for player in self.players:
                        if player.current_file and player.player.is_playing():
                            player.player.pause()
                            player.toggle_button.config(text="▶")
                            logging.debug("Re-enforced pause for Player %d after SMM resizing", player.index + 1)

                self.is_fullscreen = True
                self.canvas.update_idletasks()
                self._bind_number_keys()
                logging.debug("Entered SMM with %d players, all_paused=%s", num_players, self.all_paused)
            else:
                self.handle_escape(None)

    def _resume_updates(self):
        """Resume player playback and allow UI updates after a swap."""
        for player in self.players:
            player.is_swapping = False
            if player.player.get_state() == vlc.State.Paused and not self.all_paused:
                player.player.play()
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            self.dj_dashboard.update_paused = False
            self.dj_dashboard.update_dashboard()

    def trigger_next_for_selected(self, event):
        if self.is_maximized and self.last_selected_player is not None and 0 <= self.last_selected_player < len(self.players):
            self.players[self.last_selected_player].play_next_video()
            self.last_selected_player = None

    def add_player(self, event=None):
            """Add a new player widget at the specified or event coordinates."""
            if len(self.players) >= 12:  # Allow up to 12 for grid layouts
                logging.warning("Maximum player count (12) reached, cannot add more")
                return
            if event:
                x = self.canvas.canvasx(event.x)
                y = self.canvas.canvasy(event.y)
                logging.debug("Right-click event at canvas x=%d, y=%d (screen x=%d, y=%d)", 
                             x, y, event.x_root, event.y_root)
            else:
                x, y = 100, 100
                logging.debug("No event provided, using default coordinates x=%d, y=%d", x, y)
            logging.debug("Adding player at x=%d, y=%d", x, y)
            player = PlayerWidget(self.root, self, self.canvas, x, y)
            self.players.append(player)
            # Set new player’s layer above all others
            fullscreen_player = next((p for p in self.players if p.is_fullscreen), None)
            if fullscreen_player:
                max_layer = max(p.layer for p in self.players if p != player) + 1
                player.set_layer(max_layer)
                self.canvas.tag_raise(player.frame_id, fullscreen_player.frame_id)
                logging.debug("Set new Player %d to layer %d above fullscreened Player %d", 
                             player.index + 1, max_layer, fullscreen_player.index + 1)
            else:
                player.set_layer(len(self.players))
                self.canvas.tag_raise(player.frame_id)
            self.player_positions.append({'x': x, 'y': y, 'width': 400, 'height': 300, 'customized': False})
            logging.debug("Added Player %d, total players: %d", player.index + 1, len(self.players))
            if self.is_maximized:
                self._arrange_players_in_smm()
                logging.debug("Updated SMM layout for %d players after adding Player %d", 
                             len(self.players), player.index + 1)
            else:
                self.arrange_players()
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.build_player_controls()
                self.dj_dashboard.update_dashboard()
            self._bind_number_keys()

    def setup_context_menu(self):
        """Set up the right-click context menu for the canvas."""
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="Add Player", command=lambda: self.add_player_with_event())
        # Add other menu options as needed
        self.canvas.bind("<Button-3>", self.show_context_menu)
        logging.debug("Context menu set up for canvas")
    
    def show_context_menu(self, event):
            """Display the context menu at the right-click location and store canvas coordinates."""
            # Store the canvas coordinates of the right-click position
            canvas_x = self.canvas.canvasx(event.x)
            canvas_y = self.canvas.canvasy(event.y)
            self.last_click_pos = (canvas_x, canvas_y)
            try:
                self.context_menu.post(event.x_root, event.y_root)
                logging.debug("Context menu shown at screen x=%d, y=%d, canvas x=%d, y=%d", 
                             event.x_root, event.y_root, canvas_x, canvas_y)
            finally:
                self.context_menu.grab_release()

    def add_player_with_event(self):
        """Wrapper to pass the stored event to add_player."""
        event = getattr(self, 'last_event', None)
        if event:
            logging.debug("Using stored event for add_player: screen x=%d, y=%d", 
                         event.x_root, event.y_root)
        else:
            logging.warning("No stored event available for add_player")
        self.add_player(event)

    def refresh_bindings(self):
        """Refresh all keyboard bindings to ensure they are active."""
        # Unbind existing bindings to avoid duplicates
        for key in ["<space>", "<Configure>", "<F11>", "f", "b", "d", "<Escape>"] + [str(i) for i in range(1, 10)] + ["n", "."]:
            self.root.unbind_all(key)
        
        # Rebind all keys
        self.root.bind_all("<space>", self.toggle_play_pause)
        self.root.bind("<Configure>", self.handle_resize)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("f", self.toggle_maximize_screen)
        self.root.bind("b", self.raise_screen_5)
        self.root.bind("d", self.toggle_dj_dashboard)
        self.root.bind("<Escape>", self.handle_escape)
        for i in range(1, 10):
            self.root.bind_all(f"{i}", lambda e, idx=i-1: self.select_for_swap(idx))
        self.root.bind_all("n", self.handle_next)
        self.root.bind_all(".", self.handle_skip)
        self._bind_number_keys()  # Rebind SMM number keys
        logging.debug("Refreshed all keyboard bindings")

    def on_focus_in(self):
        """Handle window gaining focus."""
        self.refresh_bindings()
        self.root.focus_force()
        logging.debug("Window regained focus, bindings refreshed")                            

    def check_focus(self):
        """Periodically ensure the app has focus."""
        if self.root.winfo_exists() and not self.root.focus_get():
            self.root.focus_force()
            logging.debug("Forced focus back to app")
        self.root.after(5000, self.check_focus)

    def resize_background(self, width, height):
        """Resize the background image to the specified dimensions."""
        try:
            original_bg = Image.open(self.bg_file_path)
            resized_bg = original_bg.resize((width, height), Image.Resampling.LANCZOS)
            self.background_photo = ImageTk.PhotoImage(resized_bg)
            self.canvas.delete("bg")
            self.canvas.create_image(0, 0, image=self.background_photo, anchor=tk.NW, tags="bg")
            self.canvas.lower("bg")
            logging.debug(f"Resized background to {width}x{height}")
        except Exception as e:
            logging.error(f"Failed to resize background: {e}")

    def handle_escape(self, event=None):
            """Handle Escape key to exit fullscreen or SMM modes, restoring positions."""
            if not (self.is_fullscreen or self.is_maximized):
                return

            fullscreen_player = next((p for p in self.players if p.is_fullscreen), None)

            if fullscreen_player and self.pre_fullscreen_state:
                fullscreen_player.is_fullscreen = False
                fullscreen_player.fullscreen_button.config(text="⛶")
                
                if fullscreen_player.pre_fullscreen_coords:
                    try:
                        fullscreen_player.set_size_and_position(
                            fullscreen_player.pre_fullscreen_coords['width'],
                            fullscreen_player.pre_fullscreen_coords['height'],
                            fullscreen_player.pre_fullscreen_coords['x'],
                            fullscreen_player.pre_fullscreen_coords['y']
                        )
                    except tk.TclError as e:
                        logging.error("Failed to restore fullscreen player size: %s", e)
                    fullscreen_player.pre_fullscreen_coords = None

                if self.pre_fullscreen_state['is_maximized']:
                    self.root.attributes('-fullscreen', True)
                    self.is_maximized = True
                    self.is_fullscreen = True
                    for i, player in enumerate(self.players):
                        if i < len(self.pre_fullscreen_state['players']):
                            try:
                                (x, y), width, height = self.pre_fullscreen_state['players'][i]
                                player.set_size_and_position(width, height, x, y)
                                player.configure_for_width(width)
                            except tk.TclError as e:
                                logging.error("Failed to restore player %d in SMM: %s", i + 1, e)
                    self.pre_fullscreen_state = None
                    logging.debug("Exited per-player fullscreen, restored to fullscreen SMM mode")
                else:
                    self.root.attributes('-fullscreen', False)
                    self.is_maximized = False
                    self.is_fullscreen = False
                    self.smm_mode = False
                    self.root.geometry("800x600")
                    if self.pre_fullscreen_positions:
                        canvas_width = 800
                        canvas_height = 600
                        scale = min(canvas_width / 1200, canvas_height / 800)
                        for i, player in enumerate(self.players):
                            if i < len(self.pre_fullscreen_positions):
                                pos = self.pre_fullscreen_positions[i]
                                new_width = int(pos['width'] * scale)
                                new_height = int(pos['height'] * scale)
                                new_x = min(pos['x'] * scale, canvas_width - new_width)
                                new_y = min(pos['y'] * scale, canvas_height - new_height)
                                new_x = max(0, new_x)
                                new_y = max(0, new_y)
                                try:
                                    player.set_size_and_position(new_width, new_height, new_x, new_y)
                                    player.configure_for_width(new_width)
                                    self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                                    if hasattr(player, 'player') and player.player.get_media():
                                        player.player.video_set_scale(0)
                                    logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                                 player.index + 1, new_width, new_height, new_x, new_y)
                                except tk.TclError as e:
                                    logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                                    player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                                    self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300}
                    else:
                        self.arrange_players()
                    self.pre_fullscreen_state = None
                    self.pre_fullscreen_positions = []
                    self.update_background()
                    logging.debug("Exited per-player fullscreen, restored to normal mode")

            elif self.is_maximized or self.is_fullscreen:
                self.root.attributes('-fullscreen', False)
                self.is_fullscreen = False
                self.is_maximized = False
                self.smm_mode = False
                self.root.geometry("800x600")
                positions = self.pre_smm_positions if self.pre_smm_positions else self.pre_fullscreen_positions
                if positions:
                    canvas_width = 800
                    canvas_height = 600
                    scale = min(canvas_width / 1200, canvas_height / 800)
                    for i, player in enumerate(self.players):
                        if i < len(positions):
                            pos = positions[i]
                            new_width = int(pos['width'] * scale)
                            new_height = int(pos['height'] * scale)
                            new_x = min(pos['x'] * scale, canvas_width - new_width)
                            new_y = min(pos['y'] * scale, canvas_height - new_height)
                            new_x = max(0, new_x)
                            new_y = max(0, new_y)
                            try:
                                player.set_size_and_position(new_width, new_height, new_x, new_y)
                                player.configure_for_width(new_width)
                                self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': new_width, 'height': new_height}
                                if hasattr(player, 'player') and player.player.get_media():
                                    player.player.video_set_scale(0)
                                logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                             player.index + 1, new_width, new_height, new_x, new_y)
                            except tk.TclError as e:
                                logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                                player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                                self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300}
                else:
                    self.arrange_players()
                self.pre_smm_positions = []
                self.pre_fullscreen_positions = []
                self.update_background()
                logging.debug("Exited SMM or fullscreen, restored to normal mode")

            self.canvas.update_idletasks()
            if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                self.dj_dashboard.update_dashboard()

    def toggle_play_pause(self, event=None):
        logging.debug("Toggling play/pause, is_maximized=%s", self.is_maximized)
        all_paused = all(not player.player.is_playing() for player in self.players if player.current_file)
        if all_paused:
            for player in self.players:
                if player.current_file:
                    player.player.play()
                    player.toggle_button.config(text="||")
                    logging.debug("Playing Player %d", player.index + 1)
        else:
            for player in self.players:
                if player.current_file and player.player.is_playing():
                    player.player.pause()
                    player.toggle_button.config(text="▶")
                    logging.debug("Paused Player %d", player.index + 1)
        self.all_paused = all_paused
        self.canvas.update_idletasks()

    def update_background(self, event=None):
        if self.bg_file_path and self.canvas.winfo_width() > 1 and self.canvas.winfo_height() > 1:
            try:
                image = Image.open(self.bg_file_path)
                scale = max(self.canvas.winfo_width() / image.width, self.canvas.winfo_height() / image.height)
                new_width = int(image.width * scale)
                new_height = int(image.height * scale)
                image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)
                self.background_photo = ImageTk.PhotoImage(image)
                if self.background_image:
                    self.canvas.delete(self.background_image)
                self.background_image = self.canvas.create_image(
                    self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2,
                    image=self.background_photo, anchor="center"
                )
                self.canvas.lower(self.background_image)
                for player in self.players:
                    if self.canvas.tk.call('winfo', 'exists', player.frame_id):
                        self.canvas.tag_raise(player.frame_id)
            except Exception as e:
                logging.error("Failed to update background: %s", e)

    def set_background(self):
        file_path = filedialog.askopenfilename(filetypes=[("Image Files", "*.png *.jpg *.jpeg *.gif")])
        if file_path:
            self.bg_file_path = file_path
            self.update_background(None)

    def save_layout(self, event=None):
        """Save the current layout, including smm_mode and all player attributes."""
        file_path = filedialog.asksaveasfilename(defaultextension=".ultilayout",
                                                filetypes=[("Ultiplay Layout", "*.ultilayout"), ("Legacy Playlist", "*.json")])
        if not file_path:
            return
        layout_data = {
            "version": "1.0",
            "background": self.bg_file_path,
            "canvas": {
                "width": self.canvas.winfo_width(),
                "height": self.canvas.winfo_height(),
                "is_maximized": self.is_maximized,
                "is_fullscreen": self.is_fullscreen,
                "smm_mode": self.smm_mode
            },
            "players": []
        }
        for i, player in enumerate(self.players):
            x, y = self.canvas.coords(player.frame_id)
            player_data = {
                "index": i,
                "x": x,
                "y": y,
                "width": player.frame.winfo_width(),
                "height": player.frame.winfo_height(),
                "current_file": player.current_file,
                "mode": player.next_mode,
                "playlist": player.playlist,
                "force_next_file": player.force_next_file,
                "controls_visible": player.controls_visible,
                "volume": player.intended_volume if player.intended_volume is not None else player.player.audio_get_volume(),
                "seek_position": player.player.get_time() if player.player.get_media() else 0,
                "is_playing": player.player.is_playing(),
                "is_fullscreen": player.is_fullscreen
            }
            layout_data["players"].append(player_data)
        try:
            with open(file_path, "w") as f:
                json.dump(layout_data, f, indent=4)
            logging.debug("Saved layout with %d players to %s, smm_mode=%s", len(self.players), file_path, self.smm_mode)
        except Exception as e:
            logging.error("Failed to save layout to %s: %s", file_path, e)

    def load_layout(self):
            """Load a layout, entering SMM if saved in smm_mode, preserving all player attributes."""
            file_path = filedialog.askopenfilename(filetypes=[("Ultiplay Layout", "*.ultilayout"), ("Legacy Playlist", "*.json")])
            if not file_path:
                return
            try:
                with open(file_path, "r") as f:
                    data = json.load(f)

                # Clear existing players
                for player in self.players[:]:
                    player.close()
                self.players.clear()
                self.player_positions.clear()
                self.pre_smm_positions.clear()

                if "version" in data:  # New .ultilayout format
                    # Restore global settings
                    if "background" in data and os.path.isfile(data["background"]):
                        self.bg_file_path = data["background"]
                        self.update_background(None)
                    canvas_data = data.get("canvas", {})
                    smm_mode = canvas_data.get("smm_mode", False)
                    self.is_maximized = canvas_data.get("is_maximized", False)
                    self.is_fullscreen = canvas_data.get("is_fullscreen", False)
                    self.smm_mode = smm_mode

                    # Restore players
                    for p_data in sorted(data.get("players", []), key=lambda x: x.get("index", 0)):
                        player = PlayerWidget(self.root, self, self.canvas,
                                            p_data.get("x", 50), p_data.get("y", 50),
                                            p_data.get("width", 400), p_data.get("height", 300),
                                            p_data.get("playlist", []))
                        self.players.append(player)
                        self.player_positions.append({
                            'x': p_data.get("x", 50), 'y': p_data.get("y", 50),
                            'width': p_data.get("width", 400), 'height': p_data.get("height", 300),
                            'customized': p_data.get('customized', False)
                        })
                        player.next_mode = p_data.get("mode", "random_repo")
                        player.force_next_file = p_data.get("force_next_file", None)
                        player.update_mode_button_text()
                        player.intended_volume = p_data.get("volume", 100)
                        player.player.audio_set_volume(player.intended_volume)
                        player.volume_bar.set(player.intended_volume)
                        if p_data.get("controls_visible", False):
                            player.show_controls()
                        else:
                            player.hide_controls()
                        if p_data.get("current_file") and os.path.isfile(p_data["current_file"]):
                            player.load_video(p_data["current_file"])
                            if player.player.get_media():
                                player.player.set_time(p_data.get("seek_position", 0))
                                if p_data.get("is_playing", False):
                                    player.player.play()
                                    player.toggle_button.config(text="||")
                                else:
                                    player.player.pause()
                                    player.toggle_button.config(text="▶")
                        if p_data.get("is_fullscreen", False):
                            player.toggle_fullscreen()

                    # Store initial positions as pre-SMM positions
                    self.pre_smm_positions = [
                        {'x': p['x'], 'y': p['y'], 'width': p['width'], 'height': p['height']}
                        for p in self.player_positions
                    ]

                    # Handle SMM/fullscreen states
                    if self.is_fullscreen and not smm_mode:
                        self.root.attributes('-fullscreen', True)
                    elif smm_mode:
                        self.toggle_maximize_screen()
                    else:
                        self.root.geometry(f"{canvas_data.get('width', 1200)}x{canvas_data.get('height', 800)}")
                        self.arrange_players()

                else:  # Old .json playlist format
                    # Restore background (if present in old format)
                    if data.get("background") and os.path.isfile(data["background"]):
                        self.bg_file_path = data["background"]
                        self.update_background(None)
                    # Default canvas state (no SMM/maximized/fullscreen data in old format)
                    self.smm_mode = False
                    self.is_maximized = False
                    self.is_fullscreen = False
                    self.root.geometry("1200x800")

                    # Restore players with minimal data
                    for p_data in sorted(data.get("players", []), key=lambda x: x.get("index", 0)):
                        player = PlayerWidget(self.root, self, self.canvas,
                                            p_data.get("x", 50), p_data.get("y", 50),
                                            p_data.get("width", 400), p_data.get("height", 300),
                                            p_data.get("playlist", []))
                        self.players.append(player)
                        self.player_positions.append({
                            'x': p_data.get("x", 50), 'y': p_data.get("y", 50),
                            'width': p_data.get("width", 400), 'height': p_data.get("height", 300),
                            'customized': False
                        })
                        player.next_mode = p_data.get("mode", "random_repo")
                        player.force_next_file = p_data.get("force_next_file", None)
                        player.update_mode_button_text()
                        if p_data.get("current_file") and os.path.isfile(p_data["current_file"]):
                            player.load_video(p_data["current_file"])
                        elif player.playlist:
                            player.load_video(player.playlist[0])

                    # Store initial positions as pre-SMM positions
                    self.pre_smm_positions = [
                        {'x': p['x'], 'y': p['y'], 'width': p['width'], 'height': p['height']}
                        for p in self.player_positions
                    ]
                    self.arrange_players()

                self._bind_number_keys()
                if self.dj_dashboard and self.dj_dashboard.winfo_exists():
                    self.dj_dashboard.build_player_controls()
                    self.dj_dashboard.update_dashboard()
                logging.debug("Loaded %s with %d players from %s, smm_mode=%s",
                            "layout" if "version" in data else "legacy playlist",
                            len(self.players), file_path, self.smm_mode)
            except Exception as e:
                logging.error("Failed to load file: %s", str(e))
                messagebox.showerror("Error", f"Failed to load file: {str(e)}")

    def recover_all_players(self):
            """Respawn all players at the last right-click position with default size."""
            if not hasattr(self, 'last_click_pos'):
                self.last_click_pos = (100, 100)
            base_x, base_y = self.last_click_pos
            offset = 20
            default_width = 400
            default_height = 300
            canvas_width = self.canvas.winfo_width()
            canvas_height = self.canvas.winfo_height()
            for i, player in enumerate(self.players):
                x = base_x + (i % 3) * offset
                y = base_y + (i // 3) * offset
                # Ensure players stay within canvas bounds
                x = min(max(x, 0), canvas_width - default_width)
                y = min(max(y, 0), canvas_height - default_height)
                player.set_size_and_position(default_width, default_height, x, y)
                logging.debug("Recovered Player %d to x=%d, y=%d, size=%dx%d", i + 1, x, y, default_width, default_height)
            self.canvas.update_idletasks()

    def handle_right_click(self, event):
        """Store the right-click position for recovery and show context menu."""
        self.last_click_pos = (event.x, event.y)
        self.context_menu.post(event.x_root, event.y_root)

    def enter_fullscreen(self):
        """Enter Screen Maximize Mode, resizing players to fit monitors."""
        if self.smm_mode:
            return
        self.smm_mode = True
        self.pre_smm_state = {
            'window_geometry': self.root.geometry(),
            'players': [(p.frame_id, self.canvas.coords(p.frame_id), p.frame.winfo_width(), p.frame.winfo_height())
                        for p in self.players]
        }
        self.root.attributes('-fullscreen', True)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        for i, player in enumerate(self.players):
            x = (i % 2) * (screen_width // 2)
            y = (i // 2) * (screen_height // 2)
            width = screen_width // 2
            height = screen_height // 2
            player.set_size_and_position(width, height, x, y)
            logging.debug("Player %d resized to %dx%d at x=%d, y=%d in SMM", i + 1, width, height, x, y)
        logging.debug("Entered SMM")

    def exit_fullscreen(self):
        """Exit Screen Maximize Mode, restoring pre-SMM state."""
        if not self.smm_mode:
            return
        self.smm_mode = False
        self.root.attributes('-fullscreen', False)
        self.root.geometry(self.pre_smm_state['window_geometry'])
        for frame_id, (x, y), width, height in self.pre_smm_state['players']:
            for player in self.players:
                if player.frame_id == frame_id:
                    player.set_size_and_position(width, height, x, y)
                    logging.debug("Restored player at x=%d, y=%d, size=%dx%d", x, y, width, height)
        logging.debug("Exited SMM")

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