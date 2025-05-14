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
import win32api
import win32com.client
import ctypes
from screeninfo import get_monitors

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

# Disable DPI scaling for the application
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception as e:
    logging.warning("Failed to set DPI awareness: %s", e)
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
        self.is_playing = False

        # Initialize minimum dimensions for resizing
        self.min_width = 200  # Minimum width to prevent the player from becoming too small
        self.min_height = 150  # Minimum height to prevent the player from becoming too small

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
            logging.error(f"VLC initialization failed: %s", e)
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
        self.resize_handle_right = tk.Label(self.button_frame, text="↘", bg="gray", fg="white", cursor="sizing")  # Bottom-right handle
        self.resize_handle_left = tk.Label(self.button_frame, text="↖", bg="gray", fg="white", cursor="sizing")  # Bottom-left handle
        self.title_label = tk.Label(self.button_frame, text="No video loaded", bg="gray", fg="white", cursor="fleur")
        # Bind drag events to title_label
        self.title_label.bind("<Button-1>", self.start_drag)
        self.title_label.bind("<B1-Motion>", self.drag)
        self.title_label.bind("<ButtonRelease-1>", self.stop_drag)

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
        # Bind resize events for both handles
        self.resize_handle_right.bind("<Button-1>", lambda e: self.start_resize(e, "right"))
        self.resize_handle_right.bind("<B1-Motion>", lambda e: self.resize(e, "right"))
        self.resize_handle_left.bind("<Button-1>", lambda e: self.start_resize(e, "left"))
        self.resize_handle_left.bind("<B1-Motion>", lambda e: self.resize(e, "left"))

        self.drag_start_x = 0
        self.drag_start_y = 0
        self.resize_start_x = 0
        self.resize_start_y = 0
        self.start_width = 0
        self.start_height = 0
        self.start_x = 0  # To store initial x position for left-side resizing

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
            self.resize_handle_right.pack(side=tk.RIGHT, padx=2)
            self.close_button.pack(side=tk.RIGHT, padx=1)
            self.fullscreen_button.pack(side=tk.RIGHT, padx=1)
            self.playlist_button.pack(side=tk.RIGHT, padx=1)
            self.mode_button.pack(side=tk.RIGHT, padx=1)
            self.next_button.pack(side=tk.RIGHT, padx=1)
            self.prev_button.pack(side=tk.RIGHT, padx=1)
            self.toggle_button.pack(side=tk.RIGHT, padx=1)
            self.resize_handle_left.pack(side=tk.LEFT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)  # Fill remaining space
        # Special case: Player 5 (index 4) in SMM with 5+ players, right-aligned
        elif self.app.is_maximized and len(self.app.players) >= 5 and self.index == 4:
            for widget in self.slider_frame.winfo_children() + self.button_frame.winfo_children():
                widget.pack_forget()
            self.slider_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.seek_bar.pack(side=tk.RIGHT, padx=2)
            self.time_label.pack(side=tk.RIGHT, padx=2)
            self.volume_bar.pack(side=tk.RIGHT, padx=2)
            self.button_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.resize_handle_right.pack(side=tk.RIGHT, padx=2)
            self.close_button.pack(side=tk.RIGHT, padx=1)
            self.fullscreen_button.pack(side=tk.RIGHT, padx=1)
            self.playlist_button.pack(side=tk.RIGHT, padx=1)
            self.mode_button.pack(side=tk.RIGHT, padx=1)
            self.next_button.pack(side=tk.RIGHT, padx=1)
            self.prev_button.pack(side=tk.RIGHT, padx=1)
            self.toggle_button.pack(side=tk.RIGHT, padx=1)
            self.resize_handle_left.pack(side=tk.LEFT, padx=2)
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
            self.resize_handle_left.pack(side=tk.LEFT, padx=2)
            self.toggle_button.pack(side=tk.LEFT, padx=1)
            self.prev_button.pack(side=tk.LEFT, padx=1)
            self.next_button.pack(side=tk.LEFT, padx=1)
            self.mode_button.pack(side=tk.LEFT, padx=1)
            self.playlist_button.pack(side=tk.LEFT, padx=1)
            self.fullscreen_button.pack(side=tk.LEFT, padx=1)
            self.close_button.pack(side=tk.LEFT, padx=1)
            self.resize_handle_right.pack(side=tk.RIGHT, padx=2)
            self.title_label.pack(side=tk.LEFT, padx=5)

            
    def start_drag(self, event):
        """Initiate dragging of the player, unless clicking on a button or slider."""
        if self.app.is_maximized and not self.app.is_fullscreen:
            logging.info("Dragging disabled in SMM. Use 1-5 to swap or F11 for manipulation.")
            return
        widget = event.widget
        if isinstance(widget, tk.Scale):
            return
        frame_x, frame_y = self.canvas.coords(self.frame_id)
        self.drag_start_x = event.x_root - frame_x
        self.drag_start_y = event.y_root - frame_y
        self.canvas.tag_raise(self.frame_id)
        logging.debug("Started dragging Player %d from %s", self.index + 1, widget.__class__.__name__)
        # Reset hide timer on drag start to keep controls visible
        self.schedule_hide_controls()

    def drag(self, event):
        """Handle dragging of the player."""
        if self.app.is_maximized and not self.app.is_fullscreen:
            return
        new_x = max(0, min(event.x_root - self.drag_start_x, self.canvas.winfo_width() - self.frame.winfo_width()))
        new_y = max(0, min(event.y_root - self.drag_start_y, self.canvas.winfo_height() - self.frame.winfo_height()))
        self.canvas.coords(self.frame_id, new_x, new_y)
        logging.debug("Dragging Player %d to x=%d, y=%d", self.index + 1, new_x, new_y)
        # Reset hide timer on drag motion
        self.schedule_hide_controls()

    def stop_drag(self, event):
        """End dragging and update control sizes."""
        if self.app.is_maximized and not self.app.is_fullscreen:
            return
        width = self.frame.winfo_width()
        slider_length = int(width * 0.75)
        self.seek_bar.config(length=max(100, slider_length - 100))
        self.volume_bar.config(length=min(100, int(slider_length * 0.25)))
        logging.debug("Stopped dragging Player %d", self.index + 1)
        # Reset hide timer on drag stop
        self.schedule_hide_controls()

    def start_resize(self, event, side):
        """Initiate resizing from the specified corner."""
        if self.app.is_maximized and not self.app.is_fullscreen:
            logging.info("Resizing disabled in SMM. Use F11 for manipulation.")
            return
        self.resize_side = side
        self.resize_start_x = event.x_root
        self.resize_start_y = event.y_root
        self.start_width = self.frame.winfo_width()
        self.start_height = self.frame.winfo_height()
        self.start_x, self.start_y = self.canvas.coords(self.frame_id)
        logging.debug("Started resizing Player %d from %s corner", self.index + 1, side)
        # Reset hide timer on resize start
        self.schedule_hide_controls()

    def resize(self, event, side):
        """Handle resizing from the specified corner."""
        if self.app.is_maximized and not self.app.is_fullscreen:
            return
        delta_x = event.x_root - self.resize_start_x
        delta_y = event.y_root - self.resize_start_y
        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        if side == "right":
            new_width = max(self.min_width, self.start_width + delta_x)
            new_height = max(self.min_height, self.start_height + delta_y)
            new_x = self.start_x
            new_y = self.start_y
        else:  # side == "left"
            new_width = max(self.min_width, self.start_width - delta_x)
            new_height = max(self.min_height, self.start_height + delta_y)
            new_x = min(self.start_x + delta_x, self.start_x + self.start_width - self.min_width)
            new_y = self.start_y

        # Apply canvas boundary constraints
        new_width = min(new_width, canvas_width)
        new_height = min(new_height, canvas_height)
        new_x = max(0, min(new_x, canvas_width - new_width))
        new_y = max(0, min(new_y, canvas_height - new_height))

        # Update frame size and position
        self.canvas.itemconfig(self.frame_id, width=new_width, height=new_height)
        self.canvas.coords(self.frame_id, new_x, new_y)
        self.frame.lift()

        # Update seek and volume bar lengths
        slider_length = int(new_width * 0.75)
        self.seek_bar.config(length=max(100, slider_length - 100))
        self.volume_bar.config(length=min(100, int(slider_length * 0.25)))

        # Repack controls to reflect layout changes
        self.pack_controls()
        logging.debug("Resized Player %d to %dx%d at x=%d, y=%d", self.index + 1, new_width, new_height, new_x, new_y)

        # Reset hide timer on resize
        self.schedule_hide_controls()

    def set_size_and_position(self, width, height, x, y):
        """Set player size and position, respecting minimum dimensions."""
        width = max(self.min_width, width)
        height = max(self.min_height, height)
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
        """Update control sizes based on player width."""
        width = max(self.min_width, width)
        slider_length = int(width * 0.75)
        self.seek_bar.config(length=max(100, slider_length - 100))
        self.volume_bar.config(length=min(100, int(slider_length * 0.25)))
        self.pack_controls()
        if self.controls_visible:
            self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.video_frame.lift()
        self.frame.update_idletasks()

    def show_controls(self, event=None):
        """Show the control interface and schedule hiding after inactivity."""
        if not self.controls_visible:
            self.controls_visible = True
            self.controls_frame.pack(side=tk.BOTTOM, fill=tk.X)
            self.frame.update_idletasks()
            logging.debug("Showed controls for Player %d", self.index + 1)
        self.schedule_hide_controls()

    def schedule_hide_controls(self, event=None):
        """Schedule the controls to hide after 2 seconds of inactivity."""
        if self.hide_timeout:
            self.parent.after_cancel(self.hide_timeout)
        self.hide_timeout = self.parent.after(2000, self.hide_controls)

    def hide_controls(self):
        """Hide the control interface if visible."""
        if self.controls_visible:
            self.controls_frame.pack_forget()
            self.controls_visible = False
            self.frame.update_idletasks()
            logging.debug("Hid controls for Player %d", self.index + 1)
        self.hide_timeout = None

    def set_layer(self, layer):
        """Set the player's layer and redraw all players in layer order."""
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
                player.controls_visible = False
                player.hide_timeout = None
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
                player.resize_handle_left = tk.Label(player.button_frame, text="↖", bg="gray", fg="white", cursor="sizing")
                player.resize_handle_right = tk.Label(player.button_frame, text="↘", bg="gray", fg="white", cursor="sizing")
                player.toggle_button = tk.Button(player.button_frame, text="▶", command=player.toggle_play_pause, width=3)
                player.toggle_button.bind("<Control-Button-1>", player.universal_toggle)
                player.prev_button = tk.Button(player.button_frame, text="<<", command=player.play_previous_video, width=3)
                player.prev_button.bind("<Control-Button-1>", player.universal_prev)
                player.next_button = tk.Button(player.button_frame, text=">>", command=player.play_next_video, width=3)
                player.next_button.bind("<Control-1>", player.universal_next)
                player.mode_button = tk.Button(player.button_frame, text="Shuf", command=player.toggle_next_mode, width=4)
                player.playlist_button = tk.Button(player.button_frame, text="Play", command=player.edit_playlist, width=4)
                player.fullscreen_button = tk.Button(player.button_frame, text="⛶", command=player.toggle_fullscreen, width=3)
                player.close_button = tk.Button(player.button_frame, text="X", command=player.close, width=3)
                player.title_label = tk.Label(
                    player.button_frame, text=os.path.basename(player.current_file) if player.current_file else "No video loaded",
                    bg="gray", fg="white"
                )
                player.pack_controls()

                player.frame.bind("<Enter>", player.show_controls)
                player.frame.bind("<Motion>", player.show_controls)
                for widget in [player.controls_frame, player.slider_frame, player.time_label, player.button_frame, player.title_label]:
                    widget.bind("<Button-1>", player.start_drag)
                    widget.bind("<B1-Motion>", player.drag)
                    widget.bind("<ButtonRelease-1>", player.stop_drag)
                for widget in [player.toggle_button, player.prev_button, player.next_button, player.mode_button,
                               player.playlist_button, player.fullscreen_button, player.close_button,
                               player.resize_handle_left, player.resize_handle_right, player.seek_bar, player.volume_bar]:
                    widget.bind("<Button-1>", lambda e: "break")

                player.resize_handle_left.bind("<Button-1>", lambda e: player.start_resize(e, "left"))
                player.resize_handle_left.bind("<B1-Motion>", lambda e: player.resize(e, "left"))
                player.resize_handle_right.bind("<Button-1>", lambda e: player.start_resize(e, "right"))
                player.resize_handle_right.bind("<B1-Motion>", lambda e: player.resize(e, "right"))

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

    def update_mode_button_text(self):
        mode_labels = {
            "random_repo": "Shuf",
            "playlist": "Play",
            "next_repo": "Repo",
            "random_playlist": "Rand",
            "repeat": "R"
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
            self.is_transitioning = True
            def load_in_thread():
                try:
                    self.player.stop()
                    time.sleep(0.5)
                    media = self.instance.media_new(file_path)
                    media.parse_with_options(vlc.MediaParseFlag.local, 0)
                    self.player.set_media(media)
                    self.player.set_hwnd(self.video_frame.winfo_id())
                    self.player.play()
                    self.player.audio_set_volume(100)
                    self.intended_volume = 100
                    self.volume_bar.set(100)
                    start_time = time.time()
                    max_wait = 5
                    while time.time() - start_time < max_wait:
                        state = self.player.get_state()
                        if state in (vlc.State.Playing, vlc.State.Paused, vlc.State.Ended):
                            break
                        time.sleep(0.1)
                    if state not in (vlc.State.Playing, vlc.State.Paused, vlc.State.Ended):
                        logging.warning("Player %d: Media %s not playable after %d seconds, state: %s", 
                                       self.index + 1, file_path, max_wait, state)
                    self.toggle_button.config(text="||")
                    self.seek_bar.set(0)
                    self.parent.after(200, self.refresh_vlc)
                    self.parent.after(2000, self.check_playback_start)
                    logging.debug("Loaded and playing: %s with duration %d ms", file_path, self.player.get_length())
                    self.parent.after(0, self.update_title_label)
                    if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                        self.app.dj_dashboard.update_dashboard()
                except Exception as e:
                    logging.error("Failed to load video %s: %s", file_path, e)
                    self.current_file = None
                    self.parent.after(0, self.update_title_label)
                    self.toggle_button.config(text="▶")
                    self.reset_vlc()
                finally:
                    self.is_transitioning = False
                    logging.debug("Player %d transition completed, is_transitioning set to False", self.index + 1)
            threading.Thread(target=load_in_thread, daemon=True).start()
        else:
            logging.error("Invalid video file: %s", file_path)
            self.current_file = None
            self.update_title_label()
            self.toggle_button.config(text="▶")
            self.is_transitioning = False
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
            self.load_video([self.playlist/self.current_playlist_index])

    def toggle_play_pause(self):
        """Toggle play/pause state of the player."""
        if self.player.is_playing():
            self.player.pause()
            self.is_playing = False
            self.toggle_button.config(text="▶")
            logging.debug("Paused Player %d", self.index + 1)
        else:
            self.player.play()
            self.is_playing = True
            self.toggle_button.config(text="⏸")
            logging.debug("Playing Player %d", self.index + 1)

    def prepare_for_swap(self):
        """Prepare the player for swapping by capturing its state and pausing it."""
        state = {
            'was_playing': self.player.get_state() == vlc.State.Playing,
            'position': self.player.get_time() if self.player.get_media() else 0
        }
        if self.player.get_media():
            self.player.pause()
        logging.debug("Player %d prepared for swap, was_playing=%s, position=%d", self.index + 1, state['was_playing'], state['position'])
        return state

    def restore_after_swap(self, was_playing=True, position=0, root=None):
        """Restore the player after swapping, reattaching HWND and setting play/pause state."""
        hwnd = self.video_frame.winfo_id()  # Get the HWND of the video frame
        if hwnd:
            self.player.set_hwnd(hwnd)
            logging.debug("Player %d HWND re-attached after swap: %d", self.index + 1, hwnd)
        
        # Ensure the media is loaded before setting the state
        if self.player.get_media():
            # Set the position
            self.player.set_time(position)
            # Wait for VLC to stabilize
            if root:
                root.after(200)  # Delay to ensure media and position are set
            # Briefly play to update the frame, then restore the original state
            self.player.play()
            if root:
                def restore_state():
                    if was_playing:
                        self.player.play()
                        self.toggle_button.config(text="||")
                        logging.debug("Restored Player %d to playing state after swap at position %d", self.index + 1, position)
                    else:
                        self.player.pause()
                        self.toggle_button.config(text="▶")
                        logging.debug("Restored Player %d to paused state after swap at position %d", self.index + 1, position)
                root.after(100, restore_state)
            else:
                # Fallback if root is not provided
                if was_playing:
                    self.player.play()
                    self.toggle_button.config(text="||")
                    logging.debug("Restored Player %d to playing state after swap at position %d", self.index + 1, position)
                else:
                    self.player.pause()
                    self.toggle_button.config(text="▶")
                    logging.debug("Restored Player %d to paused state after swap at position %d", self.index + 1, position)
            self.player.video_set_scale(0)
        else:
            logging.warning("No media loaded for Player %d after swap", self.index + 1)

    def cycle_mode(self):
        modes = ["random_repo", "playlist", "next_repo", "random_playlist", "repeat"]
        current_idx = modes.index(self.next_mode)
        self.next_mode = modes[(current_idx + 1) % len(modes)]
        self.mode_button.config(text=self.next_mode)
        logging.debug("Player %d mode changed to %s", self.index + 1, self.next_mode)

    def universal_toggle(self, event):
        if self.is_transitioning:
            logging.debug("Player %d: Universal toggle blocked during transition", self.index + 1)
            return
        logging.debug("Universal toggle triggered")
        any_playing = any(p.player.is_playing() for p in self.app.players)
        for player in self.app.players:
            if player.is_transitioning:
                continue
            if any_playing and player.player.is_playing():
                player.player.pause()
                player.toggle_button.config(text="▶")
            elif not any_playing and not player.player.is_playing():
                if not player.current_file and player.playlist:
                    player.load_first_video()
                else:
                    player.player.play()
                    player.toggle_button.config(text="||")
            player.schedule_hide_controls()
        self.app.canvas.update_idletasks()

    def seek(self, value):
        if self.is_transitioning:
            logging.debug("Player %d: Seek blocked during transition", self.index + 1)
            return
        if not self.seek_bar.in_use and self.player.get_media():
            duration = self.player.get_length()
            if duration > 0:
                position = int(float(value) / 100 * duration)
                self.player.set_time(position)
            self.seek_bar.in_use = False
        self.schedule_hide_controls()

    def seek_to_position(self, event):
        if self.is_transitioning:
            logging.debug("Player %d: Seek to position blocked during transition", self.index + 1)
            return "break"
        logging.debug("Seek bar clicked at x=%d", event.x)
        if self.player.get_media():
            duration = self.player.get_length()
            if duration > 0 and self.seek_bar.winfo_width() > 0:
                percentage = min(100, max(0, (event.x / self.seek_bar.winfo_width()) * 100))
                position = int((percentage / 100) * duration)
                self.player.set_time(position)
                self.seek_bar.set(percentage)
        self.schedule_hide_controls()
        return "break"

    def set_volume(self, value):
        volume = int(float(value))
        self.intended_volume = volume
        self.player.audio_set_volume(volume)
        self.volume_bar.set(volume)
        logging.debug("Attempting to set volume to %d for Player %d", volume, self.index + 1)
        self.parent.after(100, self._verify_volume)
        self.schedule_hide_controls()

    def _verify_volume(self):
        if self.intended_volume is None:
            self.intended_volume = 100
        try:
            current_volume = self.player.audio_get_volume()
            if current_volume != self.intended_volume:
                logging.warning("Volume mismatch for Player %d: expected %d, got %d. Correcting.",
                               self.index + 1, self.intended_volume, current_volume)
                self.player.audio_set_volume(self.intended_volume)
                self.volume_bar.set(self.intended_volume)
                current_volume = self.player.audio_get_volume()
                if current_volume == self.intended_volume:
                    logging.debug("Volume verified for Player %d: %d", self.index + 1, self.intended_volume)
                else:
                    logging.error("Failed to verify volume for Player %d: expected %d, got %d",
                                self.index + 1, self.intended_volume, current_volume)
                    self.player.audio_set_volume(self.intended_volume)
                    self.volume_bar.set(self.intended_volume)
            else:
                logging.debug("Volume verified for Player %d: %d", self.index + 1, self.intended_volume)
        except Exception as e:
            logging.error("Failed to verify volume for Player %d: %s", self.index + 1, e)

    def jump_to_volume_position(self, event):
        slider_width = self.volume_bar.winfo_width()
        if slider_width <= 0:
            return "break"
        current_value = self.volume_bar.get()
        handle_width = 8
        handle_center = (current_value / 100) * (slider_width - handle_width) + (handle_width / 2)
        handle_left = handle_center - (handle_width / 2)
        handle_right = handle_center + (handle_width / 2)
        if handle_left <= event.x <= handle_right:
            return
        percentage = min(100, max(0, (event.x / slider_width) * 100))
        volume = int(percentage)
        self.intended_volume = volume
        self.player.audio_set_volume(volume)
        self.volume_bar.set(volume)
        logging.debug("Clicked volume bar for Player %d to %d%% (%d)", self.index + 1, percentage, volume)
        self.parent.after(100, self._verify_volume)
        self.schedule_hide_controls()
        return "break"

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
        self.parent.after(500, self.update_seek_bar)

    def toggle_fullscreen(self):
        if not self.is_fullscreen:
            was_playing = self.player.is_playing()
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
            self.app.root.attributes('-fullscreen', True)
            screen_width = self.app.root.winfo_screenwidth()
            screen_height = self.app.root.winfo_screenheight()
            self.set_size_and_position(screen_width, screen_height, 0, 0)
            self.canvas.tag_raise(self.frame_id)
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
            self.app.handle_escape(None)
        if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
            self.app.dj_dashboard.update_dashboard()
        logging.debug("Player %d fullscreen state: %s", self.index + 1, self.is_fullscreen)
        self.schedule_hide_controls()

    def toggle_player_fullscreen(self):
        """Toggle fullscreen for this individual player."""
        if self.is_fullscreen:
            # Exit fullscreen
            self.is_fullscreen = False
            if self.pre_fullscreen_coords:
                x, y = self.pre_fullscreen_coords
                self.canvas.coords(self.frame_id, x, y)
            width, height = self.pre_fullscreen_size if hasattr(self, 'pre_fullscreen_size') else (400, 300)
            self.frame.config(width=width, height=height)
            self.player.set_fullscreen(False)
            self.fullscreen_button.config(text="⛶")
            logging.debug("Player %d exited fullscreen", self.index + 1)
        else:
            # Enter fullscreen
            self.pre_fullscreen_coords = self.canvas.coords(self.frame_id)
            self.pre_fullscreen_size = (self.frame.winfo_width(), self.frame.winfo_height())
            monitor = self.app.get_monitor_for_window()
            screen_x, screen_y, screen_width, screen_height = monitor
            self.canvas.coords(self.frame_id, 0, 0)
            self.frame.config(width=screen_width, height=screen_height)
            self.player.set_fullscreen(True)
            self.is_fullscreen = True
            self.fullscreen_button.config(text="⏍")
            logging.debug("Player %d entered fullscreen on monitor at %dx%d+%d+%d", 
                         self.index + 1, screen_width, screen_height, screen_x, screen_y)

    def drop_video(self, event):
        logging.debug("Drop on video frame: %s", event.data)
        data = event.data.strip()
        file_path = data[1:-1].strip() if data.startswith("{") and data.endswith("}") else data
        if os.path.isfile(file_path) and file_path.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.webm', '.gif', '.mpeg', '.mpg')):
            self.load_video(file_path)
        self.schedule_hide_controls()

    def close(self):
        logging.debug("Closing player %d", self.index + 1)
        if self.player:
            self.player.stop()
        self.canvas.delete(self.frame_id)
        self.app.remove_player(self)
        if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
            self.app.dj_dashboard.build_player_controls()
            self.app.dj_dashboard.adjust_height()
            self.app.dj_dashboard.update_dashboard()

    def toggle_next_mode(self):
        modes = ["random_repo", "playlist", "next_repo", "random_playlist", "repeat"]
        self.next_mode = modes[(modes.index(self.next_mode) + 1) % len(modes)]
        self.update_mode_button_text()
        if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
            self.app.dj_dashboard.mode_vars[self.index].set(self.app.dj_dashboard._display_mode(self.next_mode))
            logging.debug("Synced DJD mode for Player %d to %s", self.index + 1, self.next_mode)
        self.schedule_hide_controls()

    def universal_prev(self, event):
        for player in self.app.players:
            player.play_previous_video()
        self.schedule_hide_controls()

    def universal_next(self, event):
        for player in self.app.players:
            player.play_next_video()
        self.schedule_hide_controls()

    def edit_playlist(self):
        x = self.parent.winfo_pointerx()
        y = self.parent.winfo_pointery()
        playlist_window = tk.Toplevel(self.parent)
        playlist_window.title(f"Playlist - Player {self.index + 1}")
        # Set the Ultiplay icon for the playlist window
        icon_path = r"C:\Users\march\OneDrive\Desktop\Ultiplay\appicon.ico"
        try:
            playlist_window.iconbitmap(icon_path)
            logging.debug("Set playlist window icon to %s for Player %d", icon_path, self.index + 1)
        except tk.TclError as e:
            logging.error("Failed to set playlist window icon for Player %d: %s", self.index + 1, e)
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
        self.schedule_hide_controls()

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
        self.schedule_hide_controls()

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
        self.schedule_hide_controls()

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
        self.schedule_hide_controls()

    def add_to_playlist(self):
        files = filedialog.askopenfilenames(filetypes=[("Video Files", "*.mp4 *.avi *.mkv *.mov *.webm *.gif *.mpeg *.mpg")])
        if files:
            self.playlist.extend(files)
            if hasattr(self, 'playlist_listbox') and self.playlist_listbox.winfo_exists():
                self.update_playlist_listbox(self.playlist_listbox)
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.update_playlist(self, self.app.dj_dashboard.playlist_boxes[self.index])
        self.schedule_hide_controls()

    def play_next(self, player, listbox):
        if not player.playlist:
            logging.debug("No playlist items to queue for Player %d", player.index + 1)
            return
        selection = listbox.curselection()
        if not selection or not (0 <= selection[0] < len(player.playlist)):
            logging.debug("No valid selection for Player %d", self.index + 1)
            return
        index = selection[0]
        player.force_next_file = player.playlist[index]
        logging.debug("Queued %s to play next for Player %d", os.path.basename(player.force_next_file), player.index + 1)
        scroll_pos = listbox.yview()[0]
        self.update_playlist(player, listbox)
        listbox.yview_moveto(scroll_pos)
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(index)
        if hasattr(player, 'playlist_listbox') and player.playlist_listbox.winfo_exists():
            player.update_playlist_listbox(player.playlist_listbox)
            player.playlist_listbox.yview_moveto(scroll_pos)
        self.schedule_hide_controls()

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
        self.schedule_hide_controls()

    def update_playlist(self, player, listbox):
        # Placeholder for updating playlist (not fully implemented in provided code)
        pass

    def update_playlist_listbox(self, listbox):
        listbox.delete(0, tk.END)
        current_idx = self.current_playlist_index
        next_idx = None
        if self.force_next_file and self.force_next_file in self.playlist:
            next_idx = self.playlist.index(self.force_next_file)
        elif self.next_mode == "playlist" and self.playlist:
            next_idx = (current_idx + 1) % len(self.playlist)
        elif self.next_mode == "random_playlist" and self.playlist:
            next_idx = self.next_playlist_index
        for i, file in enumerate(self.playlist):
            name = os.path.basename(file)
            if i == current_idx and file == self.current_file:
                listbox.insert(tk.END, f"▶ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#006400'})
            elif i == next_idx and next_idx is not None:
                listbox.insert(tk.END, f"→ {name}")
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': '#98FB98'})
            else:
                listbox.insert(tk.END, name)
                listbox.itemconfig(tk.END, {'fg': 'black', 'bg': 'white'})

    def on_video_end(self, event):
            logging.debug("Player %d: MediaPlayerEndReached event triggered", self.index + 1)
            # Delegate to DJDashboard's transition queue to ensure staggered transitions
            if self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
                self.app.dj_dashboard.queue_video_transition(self)
            else:
                self.play_next_video()

    def play_next_video(self):
        """Play the next video based on the current mode."""
        if self.is_transitioning:
            logging.debug("Player %d: play_next_video blocked during transition", self.index + 1)
            return

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
            self.is_transitioning = False
        
        # Update DJD if open
        if hasattr(self.app, 'dj_dashboard') and self.app.dj_dashboard and self.app.dj_dashboard.winfo_exists():
            self.app.dj_dashboard.update_dashboard()

    def play_previous_video(self):
            if self.is_transitioning:
                logging.debug("Player %d: play_previous_video blocked during transition", self.index + 1)
                return
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
                        # Removed 95% check; rely on MediaPlayerEndReached event
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
            self.after(500, self._finish_transition)  # Increased delay to 500ms

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
        self.action_just_completed = False
        self.icon_path = r"C:\Users\march\OneDrive\Desktop\Ultiplay\appicon.ico"
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
        self.swap_first = None  # For normal mode swapping
        self.last_selected_player = None  # For SMM mode swapping
        self.swap_history = []
        self.last_resize_time = 0
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

        # Initial bindings and focus
        self.refresh_bindings()
        self.root.focus_force()
        self.root.bind("<FocusIn>", lambda e: self.on_focus_in())
        self.root.after(5000, self.check_focus)

    def create_default_background(self):
        default_path = os.path.join(sys._MEIPASS, 'default_background.png') if getattr(sys, 'frozen', False) else r"C:\Users\march\OneDrive\Desktop\Ultiplay\default_background.png"
        if not os.path.exists(default_path):
            img = Image.new('RGB', (1200, 800), color='black')
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.truetype("arial.ttf", 40)
            except Exception:
                font = ImageFont.load_default()
            draw.text((600, 400), "Ultiplay", fill='white', anchor='mm', font=font)
            img.save(default_path)
            logging.debug("Created default background at %s", default_path)
        return default_path

    def select_for_swap(self, index):
        """Handle player selection for swapping in both SMM and normal modes."""
        # Ignore key press if an action just completed
        if self.action_just_completed:
            self.action_just_completed = False  # Reset the flag
            return

        if not (0 <= index < len(self.players)):
            logging.info("Invalid player index %d for swap", index + 1)
            return

        if self.is_maximized:
            # In SMM mode, handle two-step selection for swap
            if self.last_selected_player is None:
                # First selection
                self.last_selected_player = index
                logging.info("Selected Player %d in SMM. Press 's' to shuffle, '.' to skip 25%%, 'n' for next, or another number to swap", index + 1)
            else:
                # Second selection
                if self.last_selected_player != index:
                    # Different player selected, perform the swap
                    logging.info("Swapping Player %d with Player %d in SMM", self.last_selected_player + 1, index + 1)
                    self.swap_players(self.last_selected_player, index)
                    self.action_just_completed = True  # Set flag after action
                else:
                    # Same player selected, keep selection active
                    logging.info("Cannot swap Player %d with itself, please select a different player", index + 1)
        else:
            # Normal mode: handle swap, next, or skip
            if self.swap_first is None:
                self.swap_first = index
                logging.info("Selected Player %d. Press 'n' for next, '.' to skip 20%%, or another number to swap", index + 1)
            else:
                if self.swap_first != index:
                    self.swap_players(self.swap_first, index)
                    self.action_just_completed = True  # Set flag after action
                else:
                    logging.info("Cannot swap Player %d with itself, please select a different player", index + 1)

    def swap_players(self, idx1, idx2, event=None):
        """Swap two players, maintaining SMM layout and seek positions, while preserving play/pause states."""
        if not (0 <= idx1 < len(self.players) and 0 <= idx2 < len(self.players)) or idx1 == idx2:
            logging.info("Invalid swap indices: %d, %d", idx1 + 1, idx2 + 1)
            return

        player1, player2 = self.players[idx1], self.players[idx2]
        logging.debug("Swapping Player %d with Player %d", idx1 + 1, idx2 + 1)

        # Capture the state of all players to preserve non-swapped players' states
        player_states = []
        for p in self.players:
            state = {
                'was_playing': p.player.get_state() == vlc.State.Playing,
                'position': p.player.get_time() if p.player.get_media() else 0
            }
            player_states.append(state)

        # Prepare players for swap and capture their play/pause states and positions
        p1_state = player1.prepare_for_swap()
        p2_state = player2.prepare_for_swap()

        # Capture current state for both players
        p1_pos = p1_state['position']
        p2_pos = p2_state['position']
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
        player1.layer, player2.layer = p2_layer, p2_layer
        player1.update_mode_button_text()
        player2.update_mode_button_text()

        # Reload players with swapped content, passing the root reference
        self._reload_player(player1, p2_file, p2_pos, self.root)
        self._reload_player(player2, p1_file, p1_pos, self.root)

        # Restore player states after swap, preserving their original play/pause states
        player1.restore_after_swap(was_playing=p1_state['was_playing'], position=p1_state['position'], root=self.root)
        player2.restore_after_swap(was_playing=p2_state['was_playing'], position=p2_state['position'], root=self.root)

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
            self._arrange_players_in_smm(self.canvas.winfo_width(), self.canvas.winfo_height())
            logging.debug("Reapplied SMM layout after swapping Player %d with Player %d", idx1 + 1, idx2 + 1)
        else:
            self.arrange_players()
            logging.debug("Reapplied normal layout after swapping Player %d with Player %d", idx1 + 1, idx2 + 1)

        # Force a visual refresh for all players to ensure the swap is visible
        for player in [player1, player2]:
            player.video_frame.update()

        # Restore the state of non-swapped players
        for i, player in enumerate(self.players):
            if i not in [idx1, idx2]:  # Skip the swapped players
                if player_states[i]['was_playing']:
                    player.player.play()
                    player.toggle_button.config(text="||")
                    logging.debug("Restored Player %d to playing state", i + 1)
                else:
                    player.player.pause()
                    player.toggle_button.config(text="▶")
                    logging.debug("Restored Player %d to paused state", i + 1)
                # Ensure the position is correct
                if player.player.get_media():
                    player.player.set_time(player_states[i]['position'])

        # Refresh DJ Dashboard
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            self.dj_dashboard.update_dashboard()
            self.root.after(100, self.dj_dashboard.update_dashboard)

        # Log swap history
        self.swap_history.append((idx1 + 1, idx2 + 1))
        # Reset selection state fully
        self.swap_first = None
        self.last_selected_player = None
        self.action_just_completed = True
        logging.debug("Swap completed: Player %d <-> Player %d", idx1 + 1, idx2 + 1)

    def _reload_player(self, player, file_path, position, root):
        """Reload the player with the given file and seek to the specified position."""
        if file_path and os.path.exists(file_path):
            media = player.instance.media_new(file_path)
            player.player.set_media(media)
            # Wait for VLC to load the media and set the position
            def set_position():
                player.player.set_time(position)
                # Verify the position was set correctly
                current_pos = player.player.get_time()
                if current_pos != position and current_pos != -1:
                    logging.warning("Position not set correctly for Player %d, retrying...", player.index + 1)
                    player.player.set_time(position)
            root.after(200, set_position)  # Delay to ensure media is ready
            logging.debug("Reloaded Player %d with file %s at position %d", player.index + 1, file_path, position)
        else:
            logging.warning("File %s does not exist for Player %d", file_path, player.index + 1)

    def handle_shuffle(self, event):
        """Handle 's' key to set shuffle mode for the selected player in SMM."""
        if self.is_maximized and self.last_selected_player is not None and 0 <= self.last_selected_player < len(self.players):
            index = self.last_selected_player
            self.players[index].next_mode = "random_repo"
            self.players[index].update_mode_button_text()
            logging.info("Switched Player %d to shuffle mode", index + 1)
            # Reset both selection states
            self.last_selected_player = None
            self.swap_first = None
            self.action_just_completed = True  # Set flag after action

    def handle_skip(self, event):
        """Handle '.' key to skip 20% for the selected player."""
        if self.is_maximized and self.last_selected_player is not None and 0 <= self.last_selected_player < len(self.players):
            self.skip_forward_20_percent(self.last_selected_player)
            # Reset both selection states
            self.last_selected_player = None
            self.swap_first = None
            self.action_just_completed = True  # Set flag after action
        elif self.swap_first is not None and 0 <= self.swap_first < len(self.players):
            self.skip_forward_20_percent(self.swap_first)
            # Reset both selection states
            self.swap_first = None
            self.last_selected_player = None
            self.action_just_completed = True  # Set flag after action

    def handle_next(self, event):
        """Handle 'n' key to play the next video for the selected player."""
        if self.is_maximized and self.last_selected_player is not None and 0 <= self.last_selected_player < len(self.players):
            self.play_next_for_player(self.last_selected_player)
            # Reset both selection states
            self.last_selected_player = None
            self.swap_first = None
            self.action_just_completed = True  # Set flag after action
        elif self.swap_first is not None and 0 <= self.swap_first < len(self.players):
            self.play_next_for_player(self.swap_first)
            # Reset both selection states
            self.swap_first = None
            self.last_selected_player = None
            self.action_just_completed = True  # Set flag after action

    def play_next_for_player(self, index, event=None):
        """Play the next video for the specified player."""
        if 0 <= index < len(self.players):
            self.players[index].play_next_video()
            logging.debug("Triggered next video for Player %d", index + 1)
        else:
            logging.debug("Invalid player index %d for next video", index)

    def skip_forward_20_percent(self, index, event=None):
        """Skip forward 20% in the video for the specified player."""
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

    def _arrange_players_in_smm(self, screen_width=None, screen_height=None):
        """Arrange players in Screen Maximize Mode (SMM) layout."""
        if not screen_width or not screen_height:
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

        num_players = len(self.players)
        logging.debug("Arranging %d players in SMM with screen size %dx%d", num_players, screen_width, screen_height)

        # Define reference resolution for scaling (based on original layout design)
        ref_width, ref_height = 1920, 1200
        scale_x = screen_width / ref_width
        scale_y = screen_height / ref_height

        # Ensure player_positions matches the number of players
        while len(self.player_positions) < num_players:
            self.player_positions.append({'x': 0, 'y': 0, 'width': 400, 'height': 300, 'customized': False})
        while len(self.player_positions) > num_players:
            self.player_positions.pop()

        # Check for customized positions
        all_customized = all(pos.get('customized', False) for pos in self.player_positions[:num_players])
        if all_customized and len(self.player_positions) == num_players:
            logging.debug("Using customized SMM positions for %d players", num_players)
            for i, player in enumerate(self.players):
                pos = self.player_positions[i]
                try:
                    current_pos = player.player.get_time() if player.player.get_media() else 0
                    current_playing = player.player.is_playing()
                    # Scale the customized positions
                    scaled_x = int(pos['x'] * scale_x)
                    scaled_y = int(pos['y'] * scale_y)
                    scaled_width = int(pos['width'] * scale_x)
                    scaled_height = int(pos['height'] * scale_y)
                    logging.debug("Setting Player %d size to %dx%d at (%d, %d)", 
                                 player.index + 1, scaled_width, scaled_height, scaled_x, scaled_y)
                    player.set_size_and_position(scaled_width, scaled_height, scaled_x, scaled_y)
                    player.configure_for_width(scaled_width)
                    if hasattr(player, 'player') and player.player.get_media():
                        actual_pos = player.player.get_time()
                        if abs(actual_pos - current_pos) > 1000:
                            logging.warning("Player %d: Seek position changed after resize, expected %d, got %d", 
                                           player.index + 1, current_pos, actual_pos)
                            player.player.set_time(current_pos)
                        if current_playing:
                            player.player.play()
                        else:
                            player.player.pause()
                    logging.debug("Applied custom position for Player %d: %dx%d at (%d, %d)", 
                                 player.index + 1, scaled_width, scaled_height, scaled_x, scaled_y)
                except tk.TclError as e:
                    logging.error("Failed to apply custom position for Player %d: %s", player.index + 1, e)
            self.canvas.update_idletasks()
            return

        # Define layouts based on reference resolution and scale them
        layouts = []
        if num_players == 1:
            layouts = [(0, 0, ref_width, ref_height)]
        elif num_players == 2:
            half_height = ref_height // 2
            layouts = [(0, 0, ref_width, half_height), (0, half_height, ref_width, half_height)]
        elif num_players == 3:
            top_height = int(ref_height * 0.6)
            bottom_height = ref_height - top_height
            half_width = ref_width // 2
            layouts = [
                (0, 0, ref_width, top_height),
                (0, top_height, half_width, bottom_height),
                (half_width, top_height, half_width, bottom_height)
            ]
        elif num_players == 4:
            half_width = ref_width // 2
            half_height = ref_height // 2
            layouts = [
                (0, 0, half_width, half_height),
                (half_width, 0, half_width, half_height),
                (0, half_height, half_width, half_height),
                (half_width, half_height, half_width, half_height)
            ]
            logging.debug("4-player SMM layout: Players: %dx%d (scaled to %dx%d)", 
                         half_width, half_height, int(half_width * scale_x), int(half_height * scale_y))
        elif num_players == 5:
            half_width = ref_width // 2
            half_height = ref_height // 2
            small_width = ref_width // 6
            small_height = ref_height // 6
            center_x = (ref_width - small_width) // 2
            center_y = (ref_height - small_height) // 2
            layouts = [
                (0, 0, half_width, half_height),
                (half_width, 0, half_width, half_height),
                (0, half_height, half_width, half_height),
                (half_width, half_height, half_width, half_height),
                (center_x, center_y, small_width, small_height)
            ]
            logging.debug("5-player SMM layout: Players 1-4: %dx%d (scaled to %dx%d), Player 5: %dx%d at (%d, %d) (scaled to %dx%d at (%d, %d))", 
                         half_width, half_height, int(half_width * scale_x), int(half_height * scale_y),
                         small_width, small_height, center_x, center_y, 
                         int(small_width * scale_x), int(small_height * scale_y), int(center_x * scale_x), int(center_y * scale_y))
        elif num_players == 6:
            player1_width = (ref_width * 3) // 4
            player1_height = int(player1_width / (16/9))
            bottom_height = ref_height - player1_height
            right_width = ref_width - player1_width
            right_half_height = player1_height // 2
            layouts = [
                (0, 0, player1_width, player1_height),
                (0, player1_height, ref_width // 3, bottom_height),
                (ref_width // 3, player1_height, ref_width // 3, bottom_height),
                (2 * (ref_width // 3), player1_height, ref_width // 3, bottom_height),
                (player1_width, 0, right_width, right_half_height),
                (player1_width, right_half_height, right_width, right_half_height)
            ]
        elif num_players == 7:
            half_width = ref_width // 2
            half_height = ref_height // 2
            overlap_width = half_width // 2
            overlap_height = int(overlap_width / (16/9))
            center_x = (ref_width - overlap_width) // 2
            center_y = (ref_height - overlap_height) // 2
            layouts = [
                (0, 0, half_width, half_height),
                (half_width, 0, half_width, half_height),
                (0, half_height, half_width, half_height),
                (half_width, half_height, half_width, half_height),
                (center_x, 0, overlap_width, overlap_height),
                (center_x, ref_height - overlap_height, overlap_width, overlap_height),
                (center_x, center_y, overlap_width, overlap_height)
            ]
            logging.debug("7-player SMM layout: Corner players: %dx%d (scaled to %dx%d), Overlap players: %dx%d (scaled to %dx%d), Center at (%d, %d) (scaled to (%d, %d))", 
                         half_width, half_height, int(half_width * scale_x), int(half_height * scale_y),
                         overlap_width, overlap_height, int(overlap_width * scale_x), int(overlap_height * scale_y),
                         center_x, center_y, int(center_x * scale_x), int(center_y * scale_y))
        elif num_players == 8:
            third_width = ref_width // 3
            third_height = ref_height // 3
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
            third_width = ref_width // 3
            third_height = ref_height // 3
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
            half_width = ref_width // 2
            fifth_height = ref_height // 5
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
            cell_width = ref_width // cols
            cell_height = ref_height // rows
            layouts = []
            for i in range(num_players):
                row = i // cols
                col = i % cols
                layouts.append((col * cell_width, row * cell_height, cell_width, cell_height))

        # Apply layouts with scaling
        for i, player in enumerate(self.players):
            if i < len(layouts):
                x, y, width, height = layouts[i]
                # Scale the layout dimensions
                scaled_x = int(x * scale_x)
                scaled_y = int(y * scale_y)
                scaled_width = int(width * scale_x)
                scaled_height = int(height * scale_y)
                try:
                    current_pos = player.player.get_time() if player.player.get_media() else 0
                    current_playing = player.player.is_playing()
                    player.set_size_and_position(scaled_width, scaled_height, scaled_x, scaled_y)
                    player.configure_for_width(scaled_width)
                    self.player_positions[i] = {'x': scaled_x, 'y': scaled_y, 'width': scaled_width, 'height': scaled_height, 'customized': False}
                    if hasattr(player, 'player') and player.player.get_media():
                        actual_pos = player.player.get_time()
                        if abs(actual_pos - current_pos) > 1000:
                            logging.warning("Player %d: Seek position changed after resize, expected %d, got %d", 
                                           player.index + 1, current_pos, actual_pos)
                            player.player.set_time(current_pos)
                        if current_playing:
                            player.player.play()
                        else:
                            player.player.pause()
                    logging.debug("Positioned Player %d: %dx%d at (%d, %d)", 
                                 player.index + 1, scaled_width, scaled_height, scaled_x, scaled_y)
                except tk.TclError as e:
                    logging.error("Failed to position Player %d: %s", player.index + 1, e)
                    scaled_x = int((50 + (i * 20)) * scale_x)
                    scaled_y = int((50 + (i * 20)) * scale_y)
                    scaled_width = int(400 * scale_x)
                    scaled_height = int(300 * scale_y)
                    player.set_size_and_position(scaled_width, scaled_height, scaled_x, scaled_y)
                    self.player_positions[i] = {'x': scaled_x, 'y': scaled_y, 'width': scaled_width, 'height': scaled_height, 'customized': False}
            else:
                scaled_x = int((50 + (i * 20)) * scale_x)
                scaled_y = int((50 + (i * 20)) * scale_y)
                scaled_width = int(400 * scale_x)
                scaled_height = int(300 * scale_y)
                player.set_size_and_position(scaled_width, scaled_height, scaled_x, scaled_y)
                self.player_positions[i] = {'x': scaled_x, 'y': scaled_y, 'width': scaled_width, 'height': scaled_height, 'customized': False}
                logging.warning("No layout for Player %d, using default position", player.index + 1)
        self.canvas.update_idletasks()

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
            self.player_positions.append({'x': x, 'y': y, 'width': 400, 'height': 300, 'customized': False})
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
            self.player_positions[i] = {'x': x, 'y': y, 'width': cell_width, 'height': cell_height, 'customized': False}
        self.canvas.update_idletasks()

    def _bind_number_keys(self):
        """Bind number keys for player selection."""
        for i in range(10):
            self.root.bind(str(i), lambda event, idx=i-1: self.select_for_swap(idx))
        logging.debug("Bound number keys for player selection")


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

    def raise_screen_5(self, event=None):
        """Raise Player 5 to the top layer, restoring its position if in fullscreen mode."""
        if len(self.players) < 5:
            return
        screen_5 = self.players[4]
        max_layer = max(p.layer for p in self.players) + 1
        screen_5.set_layer(max_layer)
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
            self._arrange_players_in_smm(screen_width, screen_height)
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
                        player.player.video_set_scale(0)
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

            # Get the monitor where the window is currently located
            monitor = self.get_monitor_for_window()
            screen_x, screen_y, screen_width, screen_height = monitor
            logging.debug("SMM targeting monitor: %dx%d+%d+%d",
                         screen_width, screen_height, screen_x, screen_y)

            # Move window to target monitor
            self.set_window_to_monitor(screen_x, screen_y, screen_width, screen_height)
            self.set_fullscreen_properties()
            self.is_fullscreen = True
            self.canvas.config(width=screen_width, height=screen_height)
            self.root.update_idletasks()

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

            self.canvas.update_idletasks()
            # Verify final window position
            hwnd = self.root.winfo_id()
            rect = win32gui.GetWindowRect(hwnd)
            logging.debug("SMM window position: %dx%d+%d+%d",
                         rect[2] - rect[0], rect[3] - rect[1], rect[0], rect[1])
            logging.debug("Entered SMM on monitor %dx%d+%d+%d",
                         screen_width, screen_height, screen_x, screen_y)
        else:
            self.handle_escape(None)

        self._bind_number_keys()

    def refresh_vlc_players(self):
        """Force refresh of all VLC players to prevent rendering artifacts."""
        for player in self.players:
            try:
                if player.player.get_media():
                    player.player.video_set_scale(0)  # Reset scaling
                    player.player.set_hwnd(player.frame.winfo_id())  # Reattach HWND
                    logging.debug("Refreshed VLC for Player %d, frame_id: %d",
                                 player.index + 1, player.frame.winfo_id())
            except Exception as e:
                logging.error("Failed to refresh VLC for Player %d: %s", player.index + 1, e)

    def add_player(self, event=None):
        """Add a new player widget at the specified or event coordinates."""
        if len(self.players) >= 12:
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
        self.context_menu.add_command(label="Set Background", command=self.set_background)
        self.context_menu.add_command(label="Save Layout", command=self.save_layout)
        self.context_menu.add_command(label="Load Layout", command=self.load_layout)
        self.context_menu.add_command(label="Open DJD", command=self.toggle_dj_dashboard)
        self.context_menu.add_command(label="Reset Key Bindings", command=self.refresh_bindings)
        self.context_menu.add_command(label="Recover All Players", command=self.recover_all_players)
        self.canvas.bind("<Button-3>", self.show_context_menu)
        self.root.bind_all("<Control-s>", self.save_layout)
        logging.debug("Context menu set up for canvas")

    def show_context_menu(self, event):
        """Display the context menu at the right-click location and store canvas coordinates."""
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
        for key in ["<space>", "<Configure>", "<F11>", "f", "b", "d", "<Escape>", "<Control-s>", "s", "n", "."] + [str(i) for i in range(1, 11)]:
            self.root.unbind_all(key)

        self.root.bind_all("<space>", self.toggle_play_pause)
        self.root.bind("<Configure>", self.handle_resize)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("f", self.toggle_maximize_screen)
        self.root.bind("b", self.raise_screen_5)
        self.root.bind("d", self.toggle_dj_dashboard)
        self.root.bind("<Escape>", self.handle_escape)
        self.root.bind_all("<Control-s>", self.save_layout)
        for i in range(1, 11):
            self.root.bind_all(f"{i}", lambda e, idx=i-1: self.select_for_swap(idx))
        self.root.bind_all("s", self.handle_shuffle)
        self.root.bind_all("n", self.handle_next)
        self.root.bind_all(".", self.handle_skip)
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
        logging.debug("Handling Escape: is_fullscreen=%s, is_maximized=%s, pre_fullscreen_state=%s, pre_smm_state=%s", 
                    self.is_fullscreen, self.is_maximized, 
                    getattr(self, 'pre_fullscreen_state', None), 
                    getattr(self, 'pre_smm_state', None))
        if not (self.is_fullscreen or self.is_maximized):
            logging.debug("Not in fullscreen or SMM, ignoring Escape")
            return

        # Ensure exit from fullscreen early
        try:
            self.root.attributes('-fullscreen', False)
            logging.debug("Set window to non-fullscreen")
        except tk.TclError as e:
            logging.error("Failed to disable fullscreen attribute: %s", e)

        # Restore window styles for main window only
        hwnd = self.root.winfo_id()
        try:
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            style |= (win32con.WS_CAPTION | win32con.WS_THICKFRAME | win32con.WS_SYSMENU)
            style &= ~win32con.WS_POPUP
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
            logging.debug("Restored window styles for HWND %d: style=%x, ex_style=%x", 
                        hwnd, style, win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE))
        except Exception as e:
            logging.error("Failed to restore window styles for HWND %d: %s", hwnd, e)

        # Update state flags
        self.is_fullscreen = False
        self.is_maximized = False
        self.smm_mode = False

        fullscreen_player = next((p for p in self.players if p.is_fullscreen), None)

        if fullscreen_player and hasattr(self, 'pre_fullscreen_state') and self.pre_fullscreen_state:
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
                    logging.debug("Restored fullscreen Player %d to %dx%d at (%d, %d)", 
                                fullscreen_player.index + 1,
                                fullscreen_player.pre_fullscreen_coords['width'],
                                fullscreen_player.pre_fullscreen_coords['height'],
                                fullscreen_player.pre_fullscreen_coords['x'],
                                fullscreen_player.pre_fullscreen_coords['y'])
                except tk.TclError as e:
                    logging.error("Failed to restore fullscreen player size: %s", e)
                fullscreen_player.pre_fullscreen_coords = None

            if self.pre_fullscreen_state.get('is_maximized', False):
                self.root.attributes('-fullscreen', True)
                self.is_maximized = True
                self.is_fullscreen = True
                self.smm_mode = True
                for i, player in enumerate(self.players):
                    if i < len(self.pre_fullscreen_state['players']):
                        try:
                            (x, y), width, height = self.pre_fullscreen_state['players'][i]
                            player.set_size_and_position(width, height, x, y)
                            player.configure_for_width(width)
                            logging.debug("Restored Player %d to SMM layout: %dx%d at (%d, %d)", 
                                        player.index + 1, width, height, x, y)
                        except tk.TclError as e:
                            logging.error("Failed to restore Player %d in SMM: %s", player.index + 1, e)
                self.pre_fullscreen_state = None
                logging.debug("Exited per-player fullscreen, restored to fullscreen SMM mode")
            else:
                # Restore geometry for non-SMM mode
                geometry = self.pre_fullscreen_state.get('geometry', "1200x800+100+100") if isinstance(self.pre_fullscreen_state, dict) else "1200x800+100+100"
                try:
                    self.root.geometry(geometry)
                    width = int(geometry.split('x')[0])
                    height = int(geometry.split('x')[1].split('+')[0])
                    self.canvas.config(width=width, height=height)
                    logging.debug("Set canvas to %dx%d", width, height)
                except (ValueError, tk.TclError) as e:
                    logging.error("Failed to parse or set geometry %s: %s", geometry, e)
                    self.root.geometry("1200x800+100+100")
                    self.canvas.config(width=1200, height=800)
                # Reapply the Ultiplay icon
                try:
                    self.root.iconbitmap(self.icon_path)
                    logging.debug("Reapplied window icon to %s after exiting per-player fullscreen", self.icon_path)
                except tk.TclError as e:
                    logging.error("Failed to reapply window icon after exiting per-player fullscreen: %s", e)
                if self.pre_fullscreen_positions:
                    for i, player in enumerate(self.players):
                        if i < len(self.pre_fullscreen_positions):
                            x, y, width, height = self.pre_fullscreen_positions[i]
                            try:
                                player.set_size_and_position(width, height, x, y)
                                player.configure_for_width(width)
                                self.player_positions[i] = {'x': x, 'y': y, 'width': width, 'height': height, 'customized': False}
                                if hasattr(player, 'player') and player.player.get_media():
                                    player.player.video_set_scale(0)
                                logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                            player.index + 1, width, height, x, y)
                            except tk.TclError as e:
                                logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                                player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                                self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300, 'customized': False}
                else:
                    self.arrange_players()
                self.pre_fullscreen_state = None
                self.pre_fullscreen_positions = []
                self.update_background()
                logging.debug("Exited per-player fullscreen, restored to normal mode")

        else:
            # Restore geometry for SMM or global fullscreen
            geometry = "1200x800+100+100"
            if hasattr(self, 'pre_smm_state') and isinstance(self.pre_smm_state, dict):
                geometry = self.pre_smm_state.get('geometry', geometry)
            elif hasattr(self, 'pre_fullscreen_state') and isinstance(self.pre_fullscreen_state, dict):
                geometry = self.pre_fullscreen_state.get('geometry', geometry)
            try:
                self.root.geometry(geometry)
                width = int(geometry.split('x')[0])
                height = int(geometry.split('x')[1].split('+')[0])
                self.canvas.config(width=width, height=height)
                logging.debug("Set canvas to %dx%d", width, height)
            except (ValueError, tk.TclError) as e:
                logging.error("Failed to parse or set geometry %s: %s", geometry, e)
                self.root.geometry("1200x800+100+100")
                self.canvas.config(width=1200, height=800)
            # Reapply the Ultiplay icon
            try:
                self.root.iconbitmap(self.icon_path)
                logging.debug("Reapplied window icon to %s after exiting SMM/fullscreen", self.icon_path)
            except tk.TclError as e:
                logging.error("Failed to reapply window icon after exiting SMM/fullscreen: %s", e)
            # Ensure player windows are reparented and styles reset
            for player in self.players:
                try:
                    player_hwnd = player.video_frame.winfo_id()
                    style = win32gui.GetWindowLong(player_hwnd, win32con.GWL_STYLE)
                    style &= ~(win32con.WS_CAPTION | win32con.WS_SYSMENU | win32con.WS_THICKFRAME)
                    win32gui.SetWindowLong(player_hwnd, win32con.GWL_STYLE, style)
                    win32gui.SetParent(player_hwnd, self.canvas.winfo_id())
                    logging.debug("Reset styles and reparented Player %d HWND %d to canvas", 
                                player.index + 1, player_hwnd)
                except Exception as e:
                    logging.error("Failed to reset styles or reparent Player %d HWND: %s", 
                                player.index + 1, e)
            positions = self.pre_smm_positions if self.pre_smm_positions else self.pre_fullscreen_positions
            logging.debug("Pre-SMM positions: %s", positions)
            if positions:
                canvas_width = self.canvas.winfo_width()
                canvas_height = self.canvas.winfo_height()
                logging.debug("Restoring %d players with canvas size %dx%d", len(positions), canvas_width, canvas_height)
                for i, player in enumerate(self.players):
                    if i < len(positions):
                        pos = positions[i]
                        try:
                            x, y, width, height = map(float, pos)  # Ensure all values are numeric
                            new_x = max(0, min(x, canvas_width - width))
                            new_y = max(0, min(y, canvas_height - height))
                            player.set_size_and_position(int(width), int(height), int(new_x), int(new_y))
                            player.configure_for_width(int(width))
                            self.player_positions[i] = {'x': new_x, 'y': new_y, 'width': width, 'height': height, 'customized': False}
                            if hasattr(player, 'player') and player.player.get_media():
                                player.player.video_set_scale(0)
                            logging.debug("Restored Player %d to %dx%d at (%d, %d)", 
                                        player.index + 1, int(width), int(height), int(new_x), int(new_y))
                        except (ValueError, tk.TclError) as e:
                            logging.error("Failed to restore Player %d at position %s: %s", 
                                        player.index + 1, pos, e)
                            player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                            self.player_positions[i] = {'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300, 'customized': False}
            else:
                logging.debug("No positions available, arranging players in grid")
                self.arrange_players()
            self.pre_smm_positions = []
            self.pre_fullscreen_positions = []
            if hasattr(self, 'pre_smm_state'):
                self.pre_smm_state = None
            if hasattr(self, 'pre_fullscreen_state'):
                self.pre_fullscreen_state = None
            self.update_background()
            logging.debug("Exited SMM or fullscreen, restored to normal mode")

        self.swap_first = None
        self.last_selected_player = None
        self.canvas.update_idletasks()
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            self.dj_dashboard.update_dashboard()
        self.root.focus_force()
        self.refresh_bindings()
        logging.debug("Escape handling completed, window state: fullscreen=%s, geometry=%s, canvas=%dx%d", 
                    self.root.attributes('-fullscreen'), self.root.winfo_geometry(),
                    self.canvas.winfo_width(), self.canvas.winfo_height())

    def set_window_icon(self):
        """Set the window icon using both Tkinter and win32gui for reliability."""
        hwnd = self.root.winfo_id()
        try:
            if os.path.exists(self.icon_path):
                self.root.iconbitmap(self.icon_path)
                logging.debug("Set Tkinter icon to %s", self.icon_path)

                hicon = win32gui.LoadImage(0, self.icon_path, win32con.IMAGE_ICON,
                                          0, 0, win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, hicon)
                win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, hicon)
                logging.debug("Set win32gui icon to %s for HWND %d", self.icon_path, hwnd)
            else:
                logging.warning("Icon file not found at %s", self.icon_path)
        except Exception as e:
            logging.error("Failed to set window icon: %s", e)

    def restore_players_from_positions(self, positions):
        """Restore player positions and sizes from stored positions without scaling."""
        if not positions:
            self.arrange_players()
            return
        # Use actual window size for canvas dimensions
        canvas_width = self.root.winfo_width()
        canvas_height = self.root.winfo_height()
        for i, player in enumerate(self.players):
            if i < len(positions):
                x, y, width, height = positions[i]
                # Ensure positions are within canvas bounds
                new_x = max(0, min(float(x), canvas_width - width))
                new_y = max(0, min(float(y), canvas_height - height))
                try:
                    player.set_size_and_position(int(width), int(height), int(new_x), int(new_y))
                    player.configure_for_width(int(width))
                    self.player_positions[i] = {
                        'x': new_x, 'y': new_y, 'width': width, 'height': height, 'customized': False
                    }
                    if player.player.get_media():
                        player.player.video_set_scale(0)
                    logging.debug("Restored Player %d: %dx%d at (%d, %d)", 
                                 player.index + 1, int(width), int(height), int(new_x), int(new_y))
                except tk.TclError as e:
                    logging.error("Failed to restore Player %d: %s", player.index + 1, e)
                    player.set_size_and_position(400, 300, 50 + (i * 20), 50 + (i * 20))
                    self.player_positions[i] = {
                        'x': 50 + (i * 20), 'y': 50 + (i * 20), 'width': 400, 'height': 300, 'customized': False
                    }
        self.refresh_vlc_players()

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

            for player in self.players[:]:
                player.close()
            self.players.clear()
            self.player_positions.clear()
            self.pre_smm_positions.clear()

            if "version" in data:
                if "background" in data and os.path.isfile(data["background"]):
                    self.bg_file_path = data["background"]
                    self.update_background(None)
                canvas_data = data.get("canvas", {})
                smm_mode = canvas_data.get("smm_mode", False)
                self.is_maximized = canvas_data.get("is_maximized", False)
                self.is_fullscreen = canvas_data.get("is_fullscreen", False)
                self.smm_mode = smm_mode

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

                self.pre_smm_positions = [
                    {'x': p['x'], 'y': p['y'], 'width': p['width'], 'height': p['height']}
                    for p in self.player_positions
                ]

                if self.is_fullscreen and not smm_mode:
                    self.root.attributes('-fullscreen', True)
                elif smm_mode:
                    self.toggle_maximize_screen()
                else:
                    self.root.geometry(f"{canvas_data.get('width', 1200)}x{canvas_data.get('height', 800)}")
                    self.arrange_players()

            else:
                if data.get("background") and os.path.isfile(data["background"]):
                    self.bg_file_path = data["background"]
                    self.update_background(None)
                self.smm_mode = False
                self.is_maximized = False
                self.is_fullscreen = False
                self.root.geometry("1200x800")

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

    def get_monitor_for_window(self):
        """Get the monitor that the window is currently on based on its center."""
        try:
            self.root.update_idletasks()
            # Use win32gui for more reliable coordinates
            hwnd = self.root.winfo_id()
            rect = win32gui.GetWindowRect(hwnd)
            window_x = (rect[0] + rect[2]) // 2
            window_y = (rect[1] + rect[3]) // 2
            tk_window_x = self.root.winfo_x() + self.root.winfo_width() // 2
            tk_window_y = self.root.winfo_y() + self.root.winfo_height() // 2
            logging.debug("Window center: Tkinter (%d, %d), Win32 (%d, %d)",
                         tk_window_x, tk_window_y, window_x, window_y)

            monitors = get_monitors()
            for i, monitor in enumerate(monitors):
                logging.debug("Monitor %d: %dx%d+%d+%d (primary=%s)",
                             i, monitor.width, monitor.height, monitor.x, monitor.y, monitor.is_primary)

            for monitor in monitors:
                x, y, w, h = monitor.x, monitor.y, monitor.width, monitor.height
                if (x <= window_x < x + w) and (y <= window_y < y + h):
                    logging.debug("Selected monitor %d: %dx%d+%d+%d",
                                 i, w, h, x, y)
                    return (x, y, w, h)

            # Fallback to primary monitor
            for monitor in monitors:
                if monitor.is_primary:
                    logging.warning("No monitor contains window center (%d, %d), using primary: %dx%d+%d+%d",
                                   window_x, window_y, monitor.width, monitor.height, monitor.x, monitor.y)
                    return (monitor.x, monitor.y, monitor.width, monitor.height)
            # Default if no primary found
            logging.warning("No primary monitor found, using default: 1920x1080+0+0")
            return (0, 0, 1920, 1080)
        except Exception as e:
            logging.error("Failed to get monitor: %s", e)
            return (0, 0, 1920, 1080)
        
    def set_window_to_monitor(self, x, y, width, height):
        """Explicitly position the window on the target monitor with fullscreen properties."""
        hwnd = self.root.winfo_id()
        try:
            # Apply offset to correct for observed mismatch (1928,152 vs 1920,121)
            offset_x, offset_y = x - 8, y - 31
            self.root.update_idletasks()

            # Set window position and size
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, offset_x, offset_y, width, height,
                                 win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
            self.root.geometry(f"{width}x{height}+{offset_x}+{offset_y}")
            self.root.update_idletasks()

            # Verify actual position
            rect = win32gui.GetWindowRect(hwnd)
            actual_x, actual_y, actual_right, actual_bottom = rect
            actual_width = actual_right - actual_x
            actual_height = actual_bottom - actual_y
            logging.debug("Set window to %dx%d+%d+%d, actual position: %dx%d+%d+%d",
                         width, height, offset_x, offset_y, actual_width, actual_height, actual_x, actual_y)
            if actual_x != offset_x or actual_y != offset_y:
                logging.warning("Window position mismatch: requested %d,%d, got %d,%d",
                               offset_x, offset_y, actual_x, actual_y)

            # Retry position to ensure accuracy
            for _ in range(3):
                rect = win32gui.GetWindowRect(hwnd)
                actual_x, actual_y = rect[0], rect[1]
                if actual_x == offset_x and actual_y == offset_y:
                    break
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, offset_x, offset_y, width, height,
                                     win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE)
                self.root.update_idletasks()
                rect = win32gui.GetWindowRect(hwnd)
                actual_x, actual_y = rect[0], rect[1]
                logging.debug("Retry position: %dx%d+%d+%d",
                             rect[2] - rect[0], rect[3] - rect[1], actual_x, actual_y)
                if actual_x == x and actual_y == y:
                    break
                # Adjust offset if mismatch persists
                offset_x = x - (actual_x - x)
                offset_y = y - (actual_y - y)
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOP, offset_x, offset_y, width, height,
                                     win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE)
                rect = win32gui.GetWindowRect(hwnd)
                logging.debug("Adjusted position: %dx%d+%d+%d",
                             rect[2] - rect[0], rect[3] - rect[1], rect[0], rect[1])

            # Ensure window stays on target monitor
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, offset_x, offset_y, width, height,
                                 win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE)
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, offset_x, offset_y, width, height,
                                 win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE)
            rect = win32gui.GetWindowRect(hwnd)
            logging.debug("After topmost adjustment, actual position: %dx%d+%d+%d",
                         rect[2] - rect[0], rect[3] - rect[1], rect[0], rect[1])

        except Exception as e:
            logging.error("Failed to set window position: %s", e)
        
    def set_fullscreen_properties(self):
        """Set window properties for true fullscreen, hiding the taskbar and title bar."""
        hwnd = self.root.winfo_id()
        try:
            # Store current styles for restoration
            self.pre_window_styles = {
                'style': win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE),
                'ex_style': win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            }
            logging.debug("Stored window styles: style=%x, ex_style=%x",
                         self.pre_window_styles['style'], self.pre_window_styles['ex_style'])

            # Remove all title bar and border styles
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME | win32con.WS_SYSMENU |
                      win32con.WS_OVERLAPPED | win32con.WS_BORDER)
            style |= win32con.WS_POPUP
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

            # Remove extended styles that may cause borders
            ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            ex_style &= ~(win32con.WS_EX_WINDOWEDGE | win32con.WS_EX_CLIENTEDGE |
                         win32con.WS_EX_DLGMODALFRAME | win32con.WS_EX_STATICEDGE)
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)

            # Force redraw
            win32gui.RedrawWindow(hwnd, None, None,
                                 win32con.RDW_INVALIDATE | win32con.RDW_UPDATENOW | win32con.RDW_ALLCHILDREN)

            # Ensure window covers taskbar
            monitor = self.get_monitor_for_window()
            x, y, width, height = monitor
            offset_x, offset_y = x - 8, y - 31
            win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, offset_x, offset_y, width, height,
                                 win32con.SWP_FRAMECHANGED | win32con.SWP_NOACTIVATE)
            logging.debug("Set fullscreen properties for HWND %d at %dx%d+%d+%d",
                         hwnd, width, height, offset_x, offset_y)

            # Verify final position and styles
            rect = win32gui.GetWindowRect(hwnd)
            final_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            final_ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            logging.debug("After fullscreen properties, position: %dx%d+%d+%d, style=%x, ex_style=%x",
                         rect[2] - rect[0], rect[3] - rect[1], rect[0], rect[1],
                         final_style, final_ex_style)

        except Exception as e:
            logging.error("Failed to set fullscreen properties: %s", e)

    def restore_window_properties(self):
        """Restore window styles to pre-fullscreen state."""
        hwnd = self.root.winfo_id()
        try:
            if self.pre_window_styles:
                # Restore original styles
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, self.pre_window_styles['style'])
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, self.pre_window_styles['ex_style'])
                logging.debug("Restored window styles: style=%x, ex_style=%x",
                             self.pre_window_styles['style'], self.pre_window_styles['ex_style'])
                self.pre_window_styles = None
            else:
                # Fallback to standard window styles
                style = (win32con.WS_OVERLAPPED | win32con.WS_CAPTION | win32con.WS_SYSMENU |
                         win32con.WS_THICKFRAME | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX)
                ex_style = win32con.WS_EX_APPWINDOW | win32con.WS_EX_WINDOWEDGE
                win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
                logging.debug("Applied fallback window styles: style=%x, ex_style=%x", style, ex_style)

            # Force redraw
            win32gui.RedrawWindow(hwnd, None, None,
                                 win32con.RDW_INVALIDATE | win32con.RDW_UPDATENOW | win32con.RDW_ALLCHILDREN)

            # Ensure window is not topmost
            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                 win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
                                 win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)

            # Ensure Tkinter window is resizable
            self.root.resizable(True, True)
            self.root.update_idletasks()
            logging.debug("Restored window manipulability for HWND %d", hwnd)

        except Exception as e:
            logging.error("Failed to restore window properties: %s", e)
    
    def toggle_dj_dashboard(self, event=None):
        """Toggle the DJ Dashboard open or closed with a single key press."""
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            self.dj_dashboard.destroy()
            self.dj_dashboard = None
            logging.debug("Closed DJ Dashboard")
        else:
            self.dj_dashboard = DJDashboard(self.root, self)
            self.dj_dashboard.update_dashboard()
            self.root.update_idletasks()
            logging.debug("Opened DJ Dashboard")

    def toggle_fullscreen(self, event=None):
        """Toggle F11 fullscreen mode on the current monitor, preserving play states."""
        logging.debug("Toggling fullscreen: is_fullscreen=%s, is_maximized=%s",
                     self.is_fullscreen, self.is_maximized)

        if self.is_maximized:
            self.handle_escape(None)
            return

        if not self.is_fullscreen:
            # Store pre-fullscreen state
            self.pre_fullscreen_state = {
                'geometry': self.root.winfo_geometry(),
                'is_maximized': self.is_maximized,
                'is_fullscreen': self.is_fullscreen,
                'play_states': [p.player.is_playing() if p.player.get_media() else False for p in self.players]
            }
            self.pre_fullscreen_positions = []
            for player in self.players:
                try:
                    x, y = self.canvas.coords(player.frame_id)
                except tk.TclError:
                    x, y = 0, 0
                width = player.frame.winfo_width()
                height = player.frame.winfo_height()
                self.pre_fullscreen_positions.append({'x': x, 'y': y, 'width': width, 'height': height})
                logging.debug("Stored pre-fullscreen state for Player %d: %dx%d at (%d, %d)",
                             player.index + 1, width, height, x, y)

            # Get the monitor where the window is currently located
            monitor = self.get_monitor_for_window()
            screen_x, screen_y, screen_width, screen_height = monitor
            logging.debug("Fullscreen targeting monitor: %dx%d+%d+%d",
                         screen_width, screen_height, screen_x, screen_y)

            # Move window to target monitor
            self.set_window_to_monitor(screen_x, screen_y, screen_width, screen_height)
            self.set_fullscreen_properties()
            self.is_fullscreen = True
            self.canvas.config(width=screen_width, height=screen_height)
            self.root.update_idletasks()

            # Scale players to fit the monitor
            orig_width, orig_height = 1200, 800  # Reference resolution
            scale_x = screen_width / orig_width
            scale_y = screen_height / orig_height
            for i, player in enumerate(self.players):
                if i < len(self.pre_fullscreen_positions):
                    pos = self.pre_fullscreen_positions[i]
                    new_x = pos['x'] * scale_x
                    new_y = pos['y'] * scale_y
                    new_width = pos['width'] * scale_x
                    new_height = pos['height'] * scale_y
                    # Cap player size to window bounds
                    new_width = min(new_width, screen_width - new_x)
                    new_height = min(new_height, screen_height - new_y)
                    new_x = max(0, new_x)
                    new_y = max(0, new_y)
                    try:
                        player.set_size_and_position(int(new_width), int(new_height), int(new_x), int(new_y))
                        player.configure_for_width(int(new_width))
                        if player.player.get_media():
                            player.player.video_set_scale(0)
                            # Restore play state
                            if self.pre_fullscreen_state['play_states'][i]:
                                player.player.play()
                                player.toggle_button.config(text="||")
                            else:
                                player.player.pause()
                                player.toggle_button.config(text="▶")
                        logging.debug("Positioned Player %d: %dx%d at (%d, %d)",
                                     player.index + 1, int(new_width), int(new_height), int(new_x), int(new_y))
                    except tk.TclError as e:
                        logging.error("Failed to position Player %d: %s", player.index + 1, e)
            self.refresh_vlc_players()
            self.update_background()
            self.canvas.update_idletasks()
            # Verify final window position
            hwnd = self.root.winfo_id()
            rect = win32gui.GetWindowRect(hwnd)
            logging.debug("Fullscreen window position: %dx%d+%d+%d",
                         rect[2] - rect[0], rect[3] - rect[1], rect[0], rect[1])
            logging.debug("Entered fullscreen on monitor %dx%d+%d+%d",
                         screen_width, screen_height, screen_x, screen_y)
        else:
            self.handle_escape(None)

        self._bind_number_keys()
        if self.dj_dashboard and self.dj_dashboard.winfo_exists():
            self.dj_dashboard.update_dashboard()

    def resize_players_to_fit(self, window_width, window_height):
        """Resize all players to fit within the specified window dimensions."""
        if not self.players:
            logging.debug("No players to resize")
            return
        scale = min(window_width / 1200, window_height / 800)

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