import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import json
import os
import threading
import queue
import time
import mido
import mido.backends.rtmidi

PRESET_FILE = "preset.txt"
CONFIG_FILE = "config.json"

FRC8_ACTIONS = [
    "Fader_1", "Fader_2", "Fader_3", "Fader_4", "Fader_5", "Fader_6", "Fader_7", "Fader_8", "Fader_Master",
    "TrimPan_1", "TrimPan_2", "TrimPan_3", "TrimPan_4", "TrimPan_5", "TrimPan_6", "TrimPan_7", "TrimPan_8",
    "HP_Vol",
    "Arm_1", "Arm_2", "Arm_3", "Arm_4", "Arm_5", "Arm_6", "Arm_7", "Arm_8",
    "PFL_1", "PFL_2", "PFL_3", "PFL_4", "PFL_5", "PFL_6", "PFL_7", "PFL_8",
    "Play", "Stop", "Rec", "Prev", "Next",
    "Menu", "Menu_OK", "Menu_Up", "Menu_Down",
    "Output", "TrimPanToggle",
    "User_1", "User_2", "User_3",
    "Scene_Inc", "Scene_Name", "Note", "Track_Name", "False_Take", "TC",
    "Bypass"
]

FRC8_LEDS = [
    "Arm_1_Red", "Arm_2_Red", "Arm_3_Red", "Arm_4_Red", "Arm_5_Red", "Arm_6_Red", "Arm_7_Red", "Arm_8_Red",
    "Arm_1_Green", "Arm_2_Green", "Arm_3_Green", "Arm_4_Green", "Arm_5_Green", "Arm_6_Green", "Arm_7_Green", "Arm_8_Green",
    "PFL_1", "PFL_2", "PFL_3", "PFL_4", "PFL_5", "PFL_6", "PFL_7", "PFL_8",
    "Meter_1", "Meter_2", "Meter_3", "Meter_4", "Meter_5", "Meter_6", "Meter_7", "Meter_8", "Meter_L", "Meter_R",
    "Output_LR", "Output_MAIN", "Output_SUB",
    "Play_LED", "Rec_LED", "Stop_LED",
    "Trim_LED", "Pan_LED",
    "Bypass_LED"
]

def get_msg_key(msg):
    if msg.type == 'control_change':
        return f"CC_{msg.channel}_{msg.control}"
    elif msg.type in ('note_on', 'note_off'):
        return f"Note_{msg.channel}_{msg.note}"
    elif msg.type == 'pitchwheel':
        return f"PB_{msg.channel}"
    return None

class MidiMapperApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MIDI to FRC-8 Mapper")
        self.root.geometry("800x600")
        self.root.minsize(600, 450)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.mappings = {}      # Controller Signal -> FRC-8 Action
        self.led_mappings = {}  # FRC-8 LED Source -> Controller Signal Target
        self.f4_led_states = {} # Tracks current brightness/val of each F4 LED Source

        self.midi_in = None
        self.midi_out = None # Out to controller for LEDs
        self.f4_in = None
        self.f4_out = None

        self.f4_connected = False
        self.handshake_step = 0
        self.keepalive_active = False

        self.msg_queue = queue.Queue()
        self.f4_queue = queue.Queue()
        
        self.learn_mode_var = tk.BooleanVar(value=True)
        self.is_mapping = False # lock for mapping dialog
        self.is_learning_led = False
        self.learning_led_target = None
        self.is_bypassed = False
        self.map_dialog_geom = None

        self.setup_ui()
        self.load_preset(PRESET_FILE, silent=True)
        self.load_config()
        
        # Polling loops
        self.root.after(20, self.poll_queues)
        self.running = True

    def load_preset(self, filepath, silent=False):
        if not os.path.exists(filepath):
            return
        try:
            with open(filepath, 'r') as f:
                section = None
                self.mappings.clear()
                self.led_mappings.clear()
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("="): continue
                    if line == "[KEYS]":
                        section = "KEYS"
                    elif line == "[LEDS]":
                        section = "LEDS"
                    elif "=" in line or "->" in line:
                        delim = "=" if "=" in line else "->"
                        k, v = line.split(delim, 1)
                        k, v = k.strip(), v.strip()
                        if section == "KEYS":
                            self.mappings[k] = v
                        elif section == "LEDS":
                            self.led_mappings[k] = v
            if hasattr(self, 'update_tree'):
                self.update_tree()
                self.update_led_tree()
            if not silent:
                messagebox.showinfo("Success", "Preset loaded successfully.")
        except Exception as e:
            if not silent:
                messagebox.showerror("Error", f"Error loading preset: {e}")

    def save_preset(self, filepath, silent=False):
        try:
            with open(filepath, 'w') as f:
                f.write("[KEYS]\n")
                for k, v in sorted(self.mappings.items()):
                    f.write(f"{k}={v}\n")
                f.write("\n[LEDS]\n")
                for k, v in sorted(self.led_mappings.items()):
                    f.write(f"{k}={v}\n")
            if not silent:
                messagebox.showinfo("Success", f"Preset saved to {filepath}")
        except Exception as e:
            if not silent:
                messagebox.showerror("Error", f"Error saving preset: {e}")

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    config = json.load(f)
                    if 'controller_port' in config:
                        self.cb_controller.set(config['controller_port'])
                    if 'controller_out_port' in config:
                        self.cb_controller_out.set(config['controller_out_port'])
                    if 'learn_mode' in config:
                        self.learn_mode_var.set(config['learn_mode'])
            except Exception as e:
                print(f"Error loading config: {e}")

    def save_config(self):
        config = {
            'controller_port': self.cb_controller.get(),
            'controller_out_port': getattr(self, 'cb_controller_out', None) and self.cb_controller_out.get() or "None",
            'learn_mode': self.learn_mode_var.get()
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=4)
            messagebox.showinfo("Success", "Configuration saved!")
        except Exception as e:
            messagebox.showerror("Error", f"Error saving config: {e}")

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)

        # Connection Frame
        conn_frame = ttk.LabelFrame(main_frame, text="Connections", padding="10")
        conn_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        conn_frame.columnconfigure(1, weight=1)

        ttk.Label(conn_frame, text="Controller (Input):").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        self.cb_controller = ttk.Combobox(conn_frame, values=mido.get_input_names())
        self.cb_controller.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(conn_frame, text="Controller (Output for LEDs):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        self.cb_controller_out = ttk.Combobox(conn_frame, values=["None"] + mido.get_output_names())
        self.cb_controller_out.grid(row=1, column=1, sticky="ew", padx=5, pady=5)
        self.cb_controller_out.current(0)

        ttk.Label(conn_frame, text="Zoom F4 (I/O Port):").grid(row=2, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Label(conn_frame, text="Auto-Detect (F Series MIDI)").grid(row=2, column=1, sticky="w", padx=5, pady=5)

        btn_conn_frame = ttk.Frame(conn_frame)
        btn_conn_frame.grid(row=0, column=2, rowspan=3, padx=5, pady=5)
        
        self.btn_refresh = ttk.Button(btn_conn_frame, text="Refresh", command=self.refresh_ports)
        self.btn_refresh.pack(fill=tk.X, pady=2)

        self.btn_save_config = ttk.Button(btn_conn_frame, text="Save Config", command=self.save_config)
        self.btn_save_config.pack(fill=tk.X, pady=2)

        self.btn_connect = ttk.Button(btn_conn_frame, text="Connect", command=self.toggle_connection)
        self.btn_connect.pack(fill=tk.X, pady=2)

        self.status_var = tk.StringVar(value="Disconnected")
        lbl_status = ttk.Label(conn_frame, textvariable=self.status_var, font=("TkDefaultFont", 10, "bold"))
        lbl_status.grid(row=3, column=0, columnspan=3, sticky=tk.W, padx=5, pady=5)

        # Notebook for Tabs
        self.notebook = ttk.Notebook(main_frame)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        # --- TAB 1: KEYS MAPPING ---
        self.tab_keys = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_keys, text="Keys Mapping (Ctrl -> F4)")
        self.tab_keys.columnconfigure(0, weight=1)
        self.tab_keys.rowconfigure(1, weight=1)

        top_keys = ttk.Frame(self.tab_keys)
        top_keys.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(top_keys, text="Learn Mode (Pop-up on unmapped MIDI)", variable=self.learn_mode_var).pack(side=tk.LEFT, pady=5)
        
        self.lbl_bypass_status = ttk.Label(top_keys, text="[Bypass: OFF]", foreground="gray")
        self.lbl_bypass_status.pack(side=tk.RIGHT, pady=5, padx=10)

        self.tree = ttk.Treeview(self.tab_keys, columns=("Controller", "FRC8"), show="headings")
        self.tree.heading("Controller", text="Controller Signal", command=lambda: self.sort_column(self.tree, "Controller", False))
        self.tree.heading("FRC8", text="FRC-8 Action", command=lambda: self.sort_column(self.tree, "FRC8", False))
        self.tree.grid(row=1, column=0, sticky="nsew", pady=5)
        
        bot_keys = ttk.Frame(self.tab_keys)
        bot_keys.grid(row=2, column=0, sticky="ew")
        self.btn_delete = ttk.Button(bot_keys, text="Delete Selected", command=self.delete_mapping)
        self.btn_delete.pack(side=tk.LEFT, pady=5, padx=5)
        self.btn_clear_all = ttk.Button(bot_keys, text="Clear All", command=self.clear_all_mappings)
        self.btn_clear_all.pack(side=tk.LEFT, pady=5, padx=5)
        
        self.btn_manual_key = ttk.Button(bot_keys, text="Manual Map", command=self.open_manual_key_dialog)
        self.btn_manual_key.pack(side=tk.LEFT, pady=5, padx=5)

        self.tree.bind("<Delete>", lambda e: self.delete_mapping())
        self.tree.bind("<BackSpace>", lambda e: self.delete_mapping())

        # --- TAB 2: LEDS MAPPING ---
        self.tab_leds = ttk.Frame(self.notebook, padding="10")
        self.notebook.add(self.tab_leds, text="LEDs Mapping (F4 -> Ctrl)")
        self.tab_leds.columnconfigure(0, weight=1)
        self.tab_leds.rowconfigure(1, weight=1)

        top_leds = ttk.Frame(self.tab_leds)
        top_leds.grid(row=0, column=0, sticky="ew")
        ttk.Label(top_leds, text="Select F4 LED:").pack(side=tk.LEFT, padx=5)
        self.cb_led_action = ttk.Combobox(top_leds, values=FRC8_LEDS, state="readonly", width=25)
        self.cb_led_action.pack(side=tk.LEFT, padx=5)
        if FRC8_LEDS: self.cb_led_action.current(0)
        
        self.btn_learn_led = ttk.Button(top_leds, text="Learn Target MIDI", command=self.start_led_learn)
        self.btn_learn_led.pack(side=tk.LEFT, padx=5)
        
        self.btn_manual_led = ttk.Button(top_leds, text="Manual Target", command=self.open_manual_led_dialog)
        self.btn_manual_led.pack(side=tk.LEFT, padx=5)
        
        self.lbl_led_status = ttk.Label(top_leds, text="")
        self.lbl_led_status.pack(side=tk.LEFT, padx=5)

        self.led_tree = ttk.Treeview(self.tab_leds, columns=("FRC8", "Controller"), show="headings")
        self.led_tree.heading("FRC8", text="FRC-8 LED Source", command=lambda: self.sort_column(self.led_tree, "FRC8", False))
        self.led_tree.heading("Controller", text="Controller Target Signal", command=lambda: self.sort_column(self.led_tree, "Controller", False))
        self.led_tree.grid(row=1, column=0, sticky="nsew", pady=5)
        
        bot_leds = ttk.Frame(self.tab_leds)
        bot_leds.grid(row=2, column=0, sticky="ew")
        self.btn_delete_led = ttk.Button(bot_leds, text="Delete Selected", command=self.delete_led_mapping)
        self.btn_delete_led.pack(side=tk.LEFT, pady=5, padx=5)
        self.btn_clear_all_led = ttk.Button(bot_leds, text="Clear All", command=self.clear_all_led_mappings)
        self.btn_clear_all_led.pack(side=tk.LEFT, pady=5, padx=5)

        self.led_tree.bind("<Delete>", lambda e: self.delete_led_mapping())
        self.led_tree.bind("<BackSpace>", lambda e: self.delete_led_mapping())

        # --- GLOBAL PRESET BUTTONS ---
        preset_frame = ttk.Frame(main_frame)
        preset_frame.grid(row=2, column=0, sticky="ew")
        
        ttk.Button(preset_frame, text="Import Preset (TXT)", command=self.import_txt).pack(side=tk.LEFT, pady=5, padx=5)
        ttk.Button(preset_frame, text="Export Preset (TXT)", command=self.export_txt).pack(side=tk.LEFT, pady=5, padx=5)

    def sort_column(self, tv, col, reverse):
        l = [(tv.set(k, col), k) for k in tv.get_children('')]
        l.sort(reverse=reverse)
        for index, (val, k) in enumerate(l):
            tv.move(k, '', index)
        tv.heading(col, command=lambda _col=col: self.sort_column(tv, _col, not reverse))

    def export_txt(self):
        filename = filedialog.asksaveasfilename(
            defaultextension=".txt", 
            filetypes=[("Text Files", "*.txt")],
            title="Export Mappings to TXT"
        )
        if not filename: return
        self.save_preset(filename)

    def import_txt(self):
        filename = filedialog.askopenfilename(
            defaultextension=".txt", 
            filetypes=[("Text Files", "*.txt")],
            title="Import Mappings from TXT"
        )
        if not filename: return
        self.load_preset(filename)
        self.save_preset(PRESET_FILE, silent=True) # sync internal

    def refresh_ports(self):
        self.cb_controller['values'] = mido.get_input_names()
        self.cb_controller_out['values'] = ["None"] + mido.get_output_names()

    def toggle_connection(self):
        if self.midi_in or self.f4_out:
            self.disconnect()
        else:
            self.connect()

    def connect(self):
        c_port = self.cb_controller.get()
        c_out_port = self.cb_controller_out.get()

        if not c_port:
            messagebox.showerror("Error", "Please select Controller Input port.")
            return

        try:
            self.midi_in = mido.open_input(c_port, callback=lambda msg: self.msg_queue.put(msg))
            
            # Open output to controller for LED feedback (may not exist for all controllers)
            if c_out_port and c_out_port != "None":
                try:
                    self.midi_out = mido.open_output(c_out_port)
                except Exception as e:
                    print(f"Note: Could not open output port to controller for LEDs (Port: {c_out_port}). {e}")
                    self.midi_out = None
            else:
                self.midi_out = None
            
            self.btn_connect.config(text="Disconnect")
            self.status_var.set("Waiting")
            self.listening_for_f4 = True
            self.auto_connect_f4()

        except Exception as e:
            messagebox.showerror("Connection Error", str(e))
            self.disconnect()

    def auto_connect_f4(self):
        if not getattr(self, 'listening_for_f4', False):
            return
            
        try:
            ports = list(set(mido.get_input_names() + mido.get_output_names()))
            target_port = None
            for p in ports:
                if "F Series" in p:
                    target_port = p
                    break
                    
            if target_port:
                self.listening_for_f4 = False
                self.f4_in = mido.open_input(target_port, callback=lambda msg: self.f4_queue.put(msg))
                self.f4_out = mido.open_output(target_port)
                
                self.f4_connected = True
                self.keepalive_active = True
                threading.Thread(target=self.keepalive_loop, daemon=True).start()
                
                self.status_var.set("Connected")
                
                # Send initial Handshake Stage 1
                self.send_f4_sysex([0xF0, 0x52, 0x00, 0x74, 0x00, 0x00, 0x00, 0x63, 0x00, 0x01, 0xF7])
            else:
                self.root.after(1000, self.auto_connect_f4)

        except Exception as e:
            print(f"Auto-connect error: {e}")
            self.root.after(1000, self.auto_connect_f4)

    def disconnect(self):
        self.listening_for_f4 = False
        self.keepalive_active = False
        self.f4_connected = False
        self.handshake_step = 0
        if self.midi_in: self.midi_in.close(); self.midi_in = None
        if self.midi_out: self.midi_out.close(); self.midi_out = None
        if self.f4_in: self.f4_in.close(); self.f4_in = None
        if self.f4_out: self.f4_out.close(); self.f4_out = None
        
        self.status_var.set("Disconnected")
        self.btn_connect.config(text="Connect")

    def keepalive_loop(self):
        while self.f4_connected and self.keepalive_active:
            if self.f4_out:
                try: self.f4_out.send(mido.Message('active_sensing'))
                except Exception: pass
            time.sleep(0.25)

    def send_f4_sysex(self, data):
        if not self.f4_connected or not self.f4_out: return
        try:
            msg = mido.Message('sysex', data=data[1:-1])
            self.f4_out.send(msg)
        except Exception as e:
            print(f"Sysex write error: {e}")

    def send_f4_raw(self, data):
        if not self.f4_connected or not self.f4_out: return
        try:
            status = data[0]
            high = status & 0xF0
            chan = status & 0x0F
            if high == 0xB0: msg = mido.Message('control_change', channel=chan, control=data[1], value=data[2])
            elif high == 0x90: msg = mido.Message('note_on', channel=chan, note=data[1], velocity=data[2])
            elif high == 0x80: msg = mido.Message('note_off', channel=chan, note=data[1], velocity=data[2])
            elif high == 0xC0: msg = mido.Message('program_change', channel=chan, program=data[1])
            elif high == 0xE0: 
                val = (data[2] << 7) | data[1]
                msg = mido.Message('pitchwheel', channel=chan, pitch=val - 8192)
            else:
                msg = mido.Message('control_change', channel=chan, control=data[1], value=data[2])
            self.f4_out.send(msg)
        except Exception: pass
        
    def send_c_raw(self, data):
        if not self.midi_out: return
        try:
            status = data[0]
            high = status & 0xF0
            chan = status & 0x0F
            if high == 0xB0: msg = mido.Message('control_change', channel=chan, control=data[1], value=data[2])
            elif high == 0x90: msg = mido.Message('note_on', channel=chan, note=data[1], velocity=data[2])
            elif high == 0x80: msg = mido.Message('note_off', channel=chan, note=data[1], velocity=data[2])
            else: return
            self.midi_out.send(msg)
        except Exception: pass

    def poll_queues(self):
        while not self.msg_queue.empty():
            msg = self.msg_queue.get()
            self.process_controller_msg(msg)
            
        while not self.f4_queue.empty():
            msg = self.f4_queue.get()
            self.process_f4_msg(msg)
            
        if self.running:
            self.root.after(20, self.poll_queues)

    def process_f4_msg(self, msg):
        if msg.type == 'note_off': bytes_msg = [0x80 | msg.channel, msg.note, msg.velocity]
        elif msg.type == 'note_on': bytes_msg = [0x90 | msg.channel, msg.note, msg.velocity]
        elif msg.type == 'control_change': bytes_msg = [0xB0 | msg.channel, msg.control, msg.value]
        elif msg.type == 'aftertouch': bytes_msg = [0xD0 | msg.channel, msg.value]
        else: return

        status = bytes_msg[0]
        high = status & 0xF0
        chan = status & 0x0F
        d1 = bytes_msg[1]
        d2 = bytes_msg[2] if len(bytes_msg) > 2 else 0

        # Handshake logic
        if status == 0x80 and d1 == 0x00 and d2 == 0x00:
            if self.handshake_step == 0:
                print("Handshake 1 received.")
                self.handshake_step = 1
                # Stagger the remaining handshake messages without blocking the
                # Tk main thread (time.sleep here would freeze the UI).
                self.send_f4_raw([0xB7, 0x02, 0x00])
                self.root.after(50, lambda: self.send_f4_raw([0x91, 0x00, 0x00]))
                self.root.after(100, lambda: self.send_f4_raw([0x93, 0x00, 0x00]))
                self.root.after(150, lambda: self.send_f4_raw([0xCA, 0x00, 0x00]))
                self.status_var.set("Status: Handshake Complete. Ready.")
                
        # LED Routing (Note On/Off)
        if high == 0x90 or high == 0x80:
            note = d1
            is_on = (d2 > 0 and high == 0x90)
            led_action = None
            if 0x00 <= note <= 0x07: led_action = f"Arm_{note+1}_Red"
            elif 24 <= note <= 31: led_action = f"Arm_{note-24+1}_Green"
            elif 0x08 <= note <= 0x0F: led_action = f"PFL_{note-8+1}"
            elif note == 16: led_action = "Output_LR"
            elif note == 17: led_action = "Output_MAIN"
            elif note == 18: led_action = "Output_SUB"
            elif note == 0x22: led_action = "Stop_LED"
            elif note == 0x28: led_action = "Play_LED"
            elif note == 0x29: led_action = "Rec_LED"
            elif note == 0x26: led_action = "Trim_LED"
            elif note == 0x27: led_action = "Pan_LED"
            
            if led_action:
                self.send_to_controller_led(led_action, is_on=is_on)
                
        # LED Routing (Meters / Aftertouch)
        elif high == 0xD0:
            val = d1
            meter_idx = chan
            led_action = None
            if meter_idx < 8: led_action = f"Meter_{meter_idx+1}"
            elif meter_idx == 8: led_action = "Meter_L"
            elif meter_idx == 9: led_action = "Meter_R"
            
            if led_action:
                self.send_to_controller_led(led_action, val=val)

    def send_to_controller_led(self, led_action, is_on=False, val=None):
        if not self.midi_out: return
        if led_action not in self.led_mappings: return
        
        target = self.led_mappings[led_action]
        
        # Track the state of the current F4 LED
        self.f4_led_states[led_action] = val if val is not None else (127 if is_on else 0)
        
        # Check all F4 LEDs mapped to this same target key, and use the highest value (Logical OR)
        final_val = 0
        for src, tgt in self.led_mappings.items():
            if tgt == target:
                v = self.f4_led_states.get(src, 0)
                if v > final_val:
                    final_val = v

        if target.startswith("Note_"):
            parts = target.split("_")
            chan = int(parts[1])
            note = int(parts[2])
            self.send_c_raw([0x90 | chan, note, final_val])
        elif target.startswith("CC_"):
            parts = target.split("_")
            chan = int(parts[1])
            cc = int(parts[2])
            self.send_c_raw([0xB0 | chan, cc, final_val])

    def process_controller_msg(self, msg):
        key = get_msg_key(msg)
        if not key: return

        if self.is_learning_led:
            # We are waiting for a MIDI signal to map to an LED
            if msg.type == 'control_change' and msg.value == 0: return # ignore release
            if msg.type == 'note_off': return # ignore release
            
            self.led_mappings[self.learning_led_target] = key
            self.save_preset(PRESET_FILE, silent=True)
            self.update_led_tree()
            self.lbl_led_status.config(text=f"Mapped {self.learning_led_target} to {key}!")
            self.is_learning_led = False
            return

        if self.is_mapping: return # Ignore incoming while keys mapping dialog is open
        
        if key in self.mappings:
            action = self.mappings[key]
            self.translate_and_send(msg, action)
        elif self.learn_mode_var.get() and msg.type != 'note_off':
            if msg.type == 'control_change' and msg.value == 0: return
            self.open_mapping_dialog(key, msg)
            
    def start_led_learn(self):
        led_action = self.cb_led_action.get()
        if not led_action: return
        self.learning_led_target = led_action
        self.is_learning_led = True
        self.lbl_led_status.config(text=f"Waiting for MIDI signal for '{led_action}'...")

    def open_mapping_dialog(self, key, msg):
        self.is_mapping = True
        
        dlg = tk.Toplevel(self.root)
        dlg.title("Map MIDI Signal")
        if getattr(self, 'map_dialog_geom', None):
            dlg.geometry(self.map_dialog_geom)
        else:
            dlg.geometry("350x200")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(dlg, text=f"Received: {key}").pack(pady=10)
        ttk.Label(dlg, text="Select FRC-8 Action:").pack(pady=5)

        cb_action = ttk.Combobox(dlg, values=FRC8_ACTIONS, state="readonly", width=30)
        cb_action.pack(pady=5)
        if FRC8_ACTIONS: cb_action.current(0)

        def on_map():
            action = cb_action.get()
            self.mappings[key] = action
            self.save_preset(PRESET_FILE, silent=True)
            self.update_tree()
            self.map_dialog_geom = dlg.geometry()
            dlg.destroy()
            self.is_mapping = False

        def on_cancel():
            self.map_dialog_geom = dlg.geometry()
            dlg.destroy()
            self.is_mapping = False

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(pady=10)
        ttk.Button(btn_frame, text="Map", command=on_map).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Cancel", command=on_cancel).pack(side=tk.LEFT, padx=5)
        
        dlg.protocol("WM_DELETE_WINDOW", on_cancel)

    def delete_mapping(self):
        sel = self.tree.selection()
        if not sel: return
        item = self.tree.item(sel[0])
        key = item['values'][0]
        if key in self.mappings:
            del self.mappings[key]
            self.save_preset(PRESET_FILE, silent=True)
            self.update_tree()

    def open_manual_key_dialog(self):
        from tkinter import simpledialog
        signal = simpledialog.askstring("Manual Map", "Enter MIDI Signal (e.g., CC_0_16, Note_0_60):", parent=self.root)
        if signal:
            self.open_mapping_dialog(signal, None)

    def open_manual_led_dialog(self):
        led_action = self.cb_led_action.get()
        if not led_action: return
        from tkinter import simpledialog
        signal = simpledialog.askstring("Manual LED Target", f"Enter target MIDI signal for '{led_action}':\n(e.g., CC_0_16, Note_0_60)", parent=self.root)
        if signal:
            self.led_mappings[led_action] = signal
            self.save_preset(PRESET_FILE, silent=True)
            self.update_led_tree()
            self.lbl_led_status.config(text=f"Manually mapped to {signal}")

    def clear_all_mappings(self):
        if messagebox.askyesno("Confirm", "Are you sure you want to delete ALL Keys mappings?"):
            self.mappings.clear()
            self.save_preset(PRESET_FILE, silent=True)
            self.update_tree()

    def update_tree(self):
        for item in self.tree.get_children(): self.tree.delete(item)
        for k, v in self.mappings.items(): self.tree.insert("", "end", values=(k, v))
            
    def delete_led_mapping(self):
        sel = self.led_tree.selection()
        if not sel: return
        item = self.led_tree.item(sel[0])
        key = item['values'][0]
        if key in self.led_mappings:
            del self.led_mappings[key]
            self.save_preset(PRESET_FILE, silent=True)
            self.update_led_tree()
            
    def clear_all_led_mappings(self):
        if messagebox.askyesno("Confirm", "Are you sure you want to delete ALL LED mappings?"):
            self.led_mappings.clear()
            self.save_preset(PRESET_FILE, silent=True)
            self.update_led_tree()
            
    def update_led_tree(self):
        for item in self.led_tree.get_children(): self.led_tree.delete(item)
        for k, v in self.led_mappings.items(): self.led_tree.insert("", "end", values=(k, v))

    def translate_and_send(self, msg, action):
        val = 0
        is_on = False
        
        if msg.type == 'control_change':
            val = msg.value
            is_on = val > 0
        elif msg.type == 'note_on':
            val = msg.velocity
            is_on = val > 0
        elif msg.type == 'note_off':
            val = 0
            is_on = False
        elif msg.type == 'pitchwheel':
            val = int(((msg.pitch + 8192) / 16383.0) * 127)
            is_on = val > 64

        def send_button(note):
            if is_on: self.send_f4_raw([0x90, note, 0x7F])
            else: self.send_f4_raw([0x90, note, 0x00])

        def send_fader(chan_idx, val_127):
            fader_val = int((val_127 / 127.0) * 16383)
            scaled_255 = int((fader_val / 16383.0) * 255)
            ll = (chan_idx * 8) + (scaled_255 >> 7)
            mm = scaled_255 & 0x7F
            # Always send the position (including 0); the value encodes the fader
            # level, so gating on is_on would make it impossible to pull to zero.
            self.send_f4_raw([0x90, 0x68 + chan_idx, 0x7F])
            self.send_f4_raw([0xE1, ll, mm])
            self.send_f4_raw([0x90, 0x68 + chan_idx, 0x00])

        def send_pan(chan_idx, val_127):
            scaled_255 = int((val_127 / 127.0) * 255)
            ll = (chan_idx * 8) + (scaled_255 >> 7)
            mm = scaled_255 & 0x7F
            self.send_f4_raw([0xE0, ll, mm])
            
        def send_hp(val_127):
            scaled_255 = int((val_127 / 127.0) * 255)
            ll = (0 * 8) + (scaled_255 >> 7)
            mm = scaled_255 & 0x7F
            self.send_f4_raw([0xE2, ll, mm])

        if action == "Bypass":
            if is_on: # Toggle on button press down
                self.is_bypassed = not self.is_bypassed
                if self.is_bypassed:
                    self.lbl_bypass_status.config(text="[Bypass: ON]", foreground="red")
                else:
                    self.lbl_bypass_status.config(text="[Bypass: OFF]", foreground="gray")
                self.send_to_controller_led("Bypass_LED", is_on=self.is_bypassed)
            return

        if action.startswith("Fader_"):
            if self.is_bypassed: return
            idx = 8 if action == "Fader_Master" else int(action.split("_")[1]) - 1
            send_fader(idx, val)
        elif action.startswith("TrimPan_"):
            if self.is_bypassed: return
            idx = int(action.split("_")[1]) - 1
            send_pan(idx, val)
        elif action == "HP_Vol":
            send_hp(val)
        elif action.startswith("Arm_"):
            idx = int(action.split("_")[1]) - 1
            send_button(idx)
        elif action.startswith("PFL_"):
            idx = int(action.split("_")[1]) - 1
            send_button(0x08 + idx)
        elif action == "Play": send_button(0x23)
        elif action == "Stop": send_button(0x22)
        elif action == "Rec": send_button(0x24)
        elif action == "Prev": send_button(0x25)
        elif action == "Next": send_button(0x26)
        elif action == "Output": send_button(0x18)
        elif action == "TrimPanToggle": send_button(0x1F)
        elif action == "Menu": send_button(0x20)
        elif action == "Menu_OK": send_button(0x21)
        elif action == "Menu_Up":
            if is_on: self.send_f4_raw([0xB0, 0x3C, 0x41])
            else: self.send_f4_raw([0xB0, 0x3C, 0x00])
        elif action == "Menu_Down":
            if is_on: self.send_f4_raw([0xB0, 0x3C, 0x01])
            else: self.send_f4_raw([0xB0, 0x3C, 0x00])
        elif action == "User_1": send_button(0x10)
        elif action == "User_2": send_button(0x11)
        elif action == "User_3": send_button(0x12)
        elif action == "Scene_Inc": send_button(0x13)
        elif action == "Scene_Name": send_button(0x14)
        elif action == "Note": send_button(0x15)
        elif action == "Track_Name": send_button(0x16)
        elif action == "False_Take": send_button(0x17)
        elif action == "TC": send_button(0x19)

    def on_closing(self):
        self.running = False
        self.disconnect()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = MidiMapperApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()
