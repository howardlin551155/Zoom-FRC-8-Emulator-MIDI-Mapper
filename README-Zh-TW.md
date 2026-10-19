# FRC-8 Emulator / MIDI Mapper

用一台便宜的通用 MIDI 控制器（例如 Korg nanoKONTROL2），模擬 Zoom 原廠遙控器 **FRC-8**，
透過 Mac 電腦遙控 **Zoom F4** 錄音機。

> **專案範圍**：本專案只做**電腦端 app**（macOS / Python）。不做 ESP32 硬體轉接器版本。

---

## 目錄

- [一、這個專案在做什麼（白話版）](#一這個專案在做什麼白話版)
- [二、安裝與執行](#二安裝與執行)
- [三、操作流程](#三操作流程)
- [四、技術說明](#四技術說明)
- [五、檔案結構](#五檔案結構)
- [六、已知限制與風險](#六已知限制與風險)

---

## 一、這個專案在做什麼（白話版）

### 三個角色

1. **Zoom F4 錄音機** — 真正在錄音的主角，但機身旋鈕小、不好操作。
2. **FRC-8** — Zoom 原廠的專用遙控器，有大滑桿大按鈕，操作很順手，但**很貴**。
3. **通用 MIDI 控制器**（如 nanoKONTROL2）— 市面上便宜好買的滑桿控制盒。

**目的：不用買昂貴的 FRC-8，改用便宜的 MIDI 控制器達到相同效果。**

### 要克服的兩個障礙

- **語言不通**：便宜控制器吐出的訊號，F4 聽不懂（它只認 FRC-8 的格式）。本程式擔任**即時翻譯機**，把控制器的動作翻成 F4 聽得懂的指令；反過來也把 F4 的燈號狀態翻回控制器，讓控制器上的燈同步亮滅。
- **要對暗號**：F4 很「多疑」，接上後會要求對一組私有「握手暗號」確認對方是原廠遙控器，才肯開放控制。本程式一連上就**主動對出正確暗號**，騙過 F4 讓它乖乖聽話。這組暗號是從實機側錄逆向破解而來。

### 一句比喻

> 就像用家裡便宜的萬用遙控器，去操作一台只認原廠遙控器的電視——
> 程式先破解開機暗號讓電視相信「這是自己人」，再當翻譯把每個按鍵轉成電視聽得懂的訊號。

---

## 二、安裝與執行

### 需求
- macOS
- Python 3.12（內建 `tkinter`）
- 相依套件：`mido`、`python-rtmidi`

### 建立虛擬環境並安裝
```bash
cd FRC-8_Emulator_MIDI_Mapper
python3 -m venv venv
./venv/bin/pip install mido python-rtmidi
```

### 執行
```bash
./venv/bin/python midi_frc8_mapper.py
```
啟動後會開啟「MIDI to FRC-8 Mapper」視窗，並自動載入 `preset.txt`（映射表）與 `config.json`（連接埠設定）。

---

## 三、操作流程

1. **選擇連接埠**
   - Controller (Input)：你的 MIDI 控制器輸入埠。
   - Controller (Output for LEDs)：控制器的燈號回授輸出埠（沒有可選 `None`）。
   - Zoom F4：**自動偵測**（抓 port 名稱含 `F Series` 的裝置）。
2. 按 **Connect**。程式會開控制器埠、自動尋找 F4、完成握手，狀態顯示 `Handshake Complete. Ready.`。
3. **Keys Mapping 分頁**：把控制器訊號對應到 F4 動作。
   - 勾選 **Learn Mode** 時，操作任一未映射的控制器，會跳出視窗讓你選對應的 F4 動作。
   - 也可用 **Manual Map** 手動輸入訊號（如 `CC_0_16`）。
4. **LEDs Mapping 分頁**：設定 F4 的哪個燈號要點亮控制器上的哪個目標。
5. 用 **Import / Export Preset (TXT)** 匯入匯出映射設定。

---

## 四、技術說明

### 4.1 系統架構與執行緒模型

本質是一個**雙向 MIDI 路由 + 協議模擬器**，夾在控制器與 F4 之間：

```
          控制器訊號 (CC/Note/PitchBend)              FRC-8 格式指令
 [MIDI 控制器] ─────────────────────▶ [Mapper] ─────────────────────▶ [Zoom F4]
 [MIDI 控制器] ◀─────────────────────  翻譯+握手 ◀───────────────────── [Zoom F4]
            LED 回授 (CC/Note)         狀態機     LED/Meter (Note/Aftertouch)
```

- **mido/rtmidi callback 執行緒**：收到 MIDI 立即丟進 `queue.Queue`（`msg_queue`、`f4_queue`），不在 callback 內做處理。
- **Tk 主執行緒**：`poll_queues()` 每 **20ms**（`root.after(20, …)`）輪詢佇列並處理，確保所有 UI 更新都在主執行緒。
- **keepalive 背景執行緒**：每 **250ms** 送 `active_sensing`（0xFE）維持連線，否則 F4 會判定斷線。

### 4.2 握手協議（Handshake）

F4 作為 USB-Device，開放控制前需通過私有握手。逆向還原出的狀態機：

**Stage 1（Host→F4，每 2 秒重送直到有回應）**
```
F0 52 00 74 00 00 00 63 00 01 F7
```
- `0x52` = Zoom 製造商 ID；`0x74` 為 model/command ID。
- mido 送法：`Message('sysex', data=data[1:-1])`（sysex data 不含頭尾 F0/F7）。

**Stage 2（F4→Host 回應）**
```
80 00 00      # Note Off, ch0, note0, vel0
```
判定條件：`status == 0x80 and d1 == 0 and d2 == 0`。

**Stage 3（Host→F4 收尾四連發）**
```
B7 02 00      # CC ch7 ctrl=2 val=0
91 00 00      # Note On ch1 note0 vel0
93 00 00      # Note On ch3 note0 vel0
CA 00         # Program Change ch10 program0
```
> 這四發以 `root.after(50/100/150, …)` 錯開送出，**不用 `time.sleep`**，避免阻塞 Tk 主迴圈導致 UI 凍結。

**F4 偵測**：以 port 名稱是否含 `"F Series"` 判斷，而非 USB VID/PID。

### 4.3 控制方向編碼（Controller → F4）

`translate_and_send()` 依動作類型轉成 F4 原始 bytes。

**推桿（Fader）** — 單一 PitchBend channel + 把軌道索引打包進 data1：
```python
scaled_255 = int((val_127 / 127.0) * 255)
ll = (chan_idx * 8) + (scaled_255 >> 7)   # 高位 + 軌道索引
mm = scaled_255 & 0x7F                      # 低 7 bit
send_f4_raw([0x90, 0x68 + chan_idx, 0x7F])  # fader touch on（note 0x68~0x70）
send_f4_raw([0xE1, ll, mm])                 # PitchBend ch1 = fader 值
send_f4_raw([0x90, 0x68 + chan_idx, 0x00])  # fader touch off
```
注意這**不是標準 MCU**（標準 MCU 每軌一條 PitchBend channel），而是 Zoom 自訂的「單 channel + 索引打包」法，實際解析度 8-bit（0~255）。

**三類旋鈕用三條 PitchBend channel 區分 bank**：
| 類型 | status | 說明 |
|------|--------|------|
| Trim/Pan | `0xE0`（ch0）| `send_pan()` |
| Fader | `0xE1`（ch1）| `send_fader()` |
| 耳機音量 HP | `0xE2`（ch2）| `send_hp()` |

**按鈕**：`90 note 7F`（按下）/ `90 note 00`（放開），note 為硬編碼對照（Play=0x23、Stop=0x22、Rec=0x24…）。
**選單旋鈕（增量式）**：`B0 3C 41`=增加、`B0 3C 01`=減少。
**Bypass**：軟體狀態切換，開啟時攔截所有 Fader/TrimPan 並點亮 Bypass LED。

### 4.4 回授方向編碼（F4 → Controller）

`process_f4_msg()` 把 F4 狀態反解為控制器燈號：

- **Note → LED** 區段對照：
  `0x00–0x07 → Arm_n_Red`、`24–31 → Arm_n_Green`、`0x08–0x0F → PFL_n`、
  `16/17/18 → Output_LR/MAIN/SUB`、`0x22/0x28/0x29/0x26/0x27 → Stop/Play/Rec/Trim/Pan LED`。
- **電平表（Meter）用 Channel Pressure（Aftertouch, `0xD0`）傳**：以 MIDI channel 號當表頭索引（ch0~7 → Meter_1~8，ch8/9 → Meter_L/R），壓力值即電平。
- **多對一的邏輯 OR 合併**：同一控制器目標可能被多個 F4 來源映射（如 Arm_Red 與 Arm_Green 共用一顆單色燈）。送出時取所有映射到同一目標的來源之**最大亮度**，避免狀態互相蓋掉：
  ```python
  final_val = max(f4_led_states[src] for src, tgt in led_mappings.items() if tgt == target)
  ```

### 4.5 映射資料模型

- 兩張 dict：`mappings`（控制器訊號 → F4 動作）、`led_mappings`（F4 LED 來源 → 控制器目標）。
- 訊號 key 標準化：`get_msg_key()` → `CC_{ch}_{control}` / `Note_{ch}_{note}` / `PB_{ch}`。
- **Learn Mode**：收到未映射訊號跳 dialog；CC value=0 或 note_off（放開）會被忽略，避免誤觸發學習。
- **持久化**：`preset.txt`（自訂 `[KEYS]`/`[LEDS]` 分段格式）、`config.json`（port 與 learn 設定）。

---

## 五、檔案結構

```
FRC-8_Emulator_MIDI_Mapper/
├── midi_frc8_mapper.py     # 主程式（電腦端 app）
├── preset.txt              # 映射表（啟動時載入）
├── config.json             # 連接埠 / Learn Mode 設定
└── venv/                   # Python 虛擬環境
```

---

## 六、已知限制與風險

- **8-bit 解析度**：fader/pan 實際只有 256 階，長行程推桿可能有可聞階梯。
- **協議為逆向還原**：PitchBend 的打包方式與 channel 分配未經官方文件驗證，屬實測歸納。
- **需要實機才能完整測試**：程式本身可正常啟動與載入設定，但推桿、握手、LED 回授等端對端行為需接上實體控制器與 Zoom F4 才能驗證。
- **錯誤處理**：MIDI 送出路徑刻意不印 log（避免高頻洗頻），除錯時可視需要加上輸出。
