# FRC-8 Emulator / MIDI Mapper

Use a cheap generic MIDI controller (e.g. a Korg nanoKONTROL2) to emulate Zoom's official
**FRC-8** remote and control a **Zoom F4** recorder from a Mac.

> **Scope**: This project targets the **computer-side app only** (macOS / Python). There is no ESP32 hardware-adapter version.

---

## Table of Contents

- [1. What this project does (plain language)](#1-what-this-project-does-plain-language)
- [2. Install & run](#2-install--run)
- [3. Usage flow](#3-usage-flow)
- [4. Technical details](#4-technical-details)
- [5. File layout](#5-file-layout)
- [6. Known limitations & risks](#6-known-limitations--risks)

---

## 1. What this project does (plain language)

### The three players

1. **Zoom F4 recorder** — the device that actually records audio, but its on-body knobs are tiny and awkward.
2. **FRC-8** — Zoom's dedicated remote, with large faders and buttons; great to operate, but **expensive**.
3. **Generic MIDI controller** (e.g. nanoKONTROL2) — a cheap, widely available fader/control box.

**Goal: skip the pricey FRC-8 and get the same result with a cheap MIDI controller.**

### Two obstacles to overcome

- **Different languages**: the signals a cheap controller emits mean nothing to the F4 (it only understands the FRC-8 format). This app acts as a **real-time translator**, turning controller actions into commands the F4 understands — and, in reverse, translating the F4's LED/state back to the controller so its lights track the F4.
- **A secret handshake**: the F4 is "suspicious" — once connected, it demands a private handshake to confirm the peer is a genuine remote before it unlocks control. This app **sends the correct handshake automatically** on connect, convincing the F4 to accept commands. That handshake was reverse-engineered by sniffing a real unit.

### An analogy

> It's like using a cheap universal remote to drive a TV that only accepts its original remote —
> the app first cracks the power-on secret so the TV believes "this is one of mine," then acts as
> a translator turning every keypress into signals the TV understands.

---

## 2. Install & run

### Requirements
- macOS
- Python 3.12 (ships with `tkinter`)
- Dependencies: `mido`, `python-rtmidi`

### Create a virtualenv and install
```bash
cd FRC-8_Emulator_MIDI_Mapper
python3 -m venv venv
./venv/bin/pip install mido python-rtmidi
```

### Run
```bash
./venv/bin/python midi_frc8_mapper.py
```
This opens the "MIDI to FRC-8 Mapper" window and auto-loads `preset.txt` (the mapping table) and `config.json` (port settings).

---

## 3. Usage flow

1. **Pick ports**
   - Controller (Input): your MIDI controller's input port.
   - Controller (Output for LEDs): the controller's LED-feedback output port (choose `None` if it has none).
   - Zoom F4: **auto-detected** (matches a port whose name contains `F Series`).
2. Click **Connect**. The app opens the controller port, finds the F4, completes the handshake, and shows `Handshake Complete. Ready.`.
3. **Keys Mapping tab**: map controller signals to F4 actions.
   - With **Learn Mode** checked, operating any unmapped control pops up a dialog to pick the matching F4 action.
   - Or use **Manual Map** to type a signal directly (e.g. `CC_0_16`).
4. **LEDs Mapping tab**: choose which F4 LED lights up which target on the controller.
5. Use **Import / Export Preset (TXT)** to load/save mapping sets.

---

## 4. Technical details

### 4.1 Architecture & threading model

At its core this is a **bidirectional MIDI router + protocol emulator** sitting between the controller and the F4:

```
          controller signals (CC/Note/PitchBend)        FRC-8-format commands
 [MIDI controller] ───────────────────▶ [Mapper] ───────────────────▶ [Zoom F4]
 [MIDI controller] ◀───────────────────  translate ◀─────────────────── [Zoom F4]
            LED feedback (CC/Note)        + handshake   LED/Meter (Note/Aftertouch)
```

- **mido/rtmidi callback thread**: incoming MIDI is pushed straight into a `queue.Queue` (`msg_queue`, `f4_queue`); no processing happens inside the callback.
- **Tk main thread**: `poll_queues()` drains the queues every **20 ms** (`root.after(20, …)`) and does the real work, so all UI updates stay on the main thread.
- **keepalive background thread**: sends `active_sensing` (0xFE) every **250 ms** to keep the link alive, otherwise the F4 considers it disconnected.

### 4.2 Handshake protocol

As a USB device, the F4 requires a private handshake before unlocking control. The reverse-engineered state machine:

**Stage 1 (Host→F4, resent every 2 s until answered)**
```
F0 52 00 74 00 00 00 63 00 01 F7
```
- `0x52` = Zoom manufacturer ID; `0x74` is the model/command ID.
- Sent via mido as `Message('sysex', data=data[1:-1])` (sysex data excludes the F0/F7 framing).

**Stage 2 (F4→Host reply)**
```
80 00 00      # Note Off, ch0, note0, vel0
```
Detected with `status == 0x80 and d1 == 0 and d2 == 0`.

**Stage 3 (Host→F4, four closing messages)**
```
B7 02 00      # CC ch7 ctrl=2 val=0
91 00 00      # Note On ch1 note0 vel0
93 00 00      # Note On ch3 note0 vel0
CA 00         # Program Change ch10 program0
```
> These four are staggered with `root.after(50/100/150, …)` — **not `time.sleep`** — so they don't block the Tk main loop and freeze the UI.

**F4 detection**: by whether a port name contains `"F Series"`, not by USB VID/PID.

### 4.3 Control direction encoding (Controller → F4)

`translate_and_send()` converts each action type into raw F4 bytes.

**Faders** — a single PitchBend channel with the track index packed into data1:
```python
scaled_255 = int((val_127 / 127.0) * 255)
ll = (chan_idx * 8) + (scaled_255 >> 7)    # high bit + track index
mm = scaled_255 & 0x7F                       # low 7 bits
send_f4_raw([0x90, 0x68 + chan_idx, 0x7F])   # fader touch on (notes 0x68–0x70)
send_f4_raw([0xE1, ll, mm])                  # PitchBend ch1 = fader value
send_f4_raw([0x90, 0x68 + chan_idx, 0x00])   # fader touch off
```
Note this is **not standard MCU** (standard MCU uses one PitchBend channel per track); it's Zoom's custom "single channel + packed index" scheme, with an effective resolution of 8 bits (0–255).

**Three knob classes use three PitchBend channels as banks:**
| Type | status | Function |
|------|--------|----------|
| Trim/Pan | `0xE0` (ch0) | `send_pan()` |
| Fader | `0xE1` (ch1) | `send_fader()` |
| Headphone volume (HP) | `0xE2` (ch2) | `send_hp()` |

**Buttons**: `90 note 7F` (press) / `90 note 00` (release), with hardcoded notes (Play=0x23, Stop=0x22, Rec=0x24, …).
**Menu encoder (incremental)**: `B0 3C 41` = increment, `B0 3C 01` = decrement.
**Bypass**: a software toggle; while on, it blocks all Fader/TrimPan output and lights the Bypass LED.

### 4.4 Feedback direction encoding (F4 → Controller)

`process_f4_msg()` turns F4 state into controller LEDs:

- **Note → LED** ranges:
  `0x00–0x07 → Arm_n_Red`, `24–31 → Arm_n_Green`, `0x08–0x0F → PFL_n`,
  `16/17/18 → Output_LR/MAIN/SUB`, `0x22/0x28/0x29/0x26/0x27 → Stop/Play/Rec/Trim/Pan LED`.
- **Meters travel as Channel Pressure (Aftertouch, `0xD0`)**: the MIDI channel number is the meter index (ch0–7 → Meter_1–8, ch8/9 → Meter_L/R), and the pressure value is the level.
- **Many-to-one logical OR merge**: one controller target may be mapped from several F4 sources (e.g. Arm_Red and Arm_Green sharing a single monochrome LED). On send, the **maximum brightness** across all sources mapped to the same target is used, so states don't cancel each other out:
  ```python
  final_val = max(f4_led_states[src] for src, tgt in led_mappings.items() if tgt == target)
  ```

### 4.5 Mapping data model

- Two dicts: `mappings` (controller signal → F4 action) and `led_mappings` (F4 LED source → controller target).
- Signal keys are normalized by `get_msg_key()` → `CC_{ch}_{control}` / `Note_{ch}_{note}` / `PB_{ch}`.
- **Learn Mode**: an unmapped signal pops a dialog; CC value=0 or note_off (release) is ignored to avoid triggering learning on release.
- **Persistence**: `preset.txt` (custom `[KEYS]`/`[LEDS]` sections) and `config.json` (ports + learn setting).

---

## 5. File layout

```
FRC-8_Emulator_MIDI_Mapper/
├── midi_frc8_mapper.py     # main program (computer-side app)
├── preset.txt              # mapping table (loaded on startup)
├── config.json             # port / Learn Mode settings
└── venv/                   # Python virtual environment
```

---

## 6. Known limitations & risks

- **8-bit resolution**: faders/pan have only 256 steps, so long fader travel may show audible stepping.
- **Reverse-engineered protocol**: the PitchBend packing and channel assignments are empirical and not verified against official documentation.
- **Hardware needed for full testing**: the app starts and loads settings fine, but end-to-end behavior (faders, handshake, LED feedback) can only be verified with a physical controller and a Zoom F4 connected.
- **Error handling**: MIDI send paths deliberately stay quiet (to avoid log spam at high rates); add output as needed when debugging.
