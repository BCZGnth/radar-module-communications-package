# Multi-Radar Analyzer — Python / PyQt6

Unified live-graphing desktop app for three radar sensors.  
Single file, no build step. Just `pip install` and run.

---

## Quick Start

```bash
pip install PyQt6 pyqtgraph pyserial numpy
python multi_radar_analyzer.py
```

---

## Supported Radars

| Radar | Type | Baud | Frame Header | Output |
|---|---|---|---|---|
| **RD-03E** (Ai-Thinker) | 1D distance | 256 000 | `AA AA` | Distance, presence status |
| **HLK-LD2450** (Hi-Link) | 2D multi-target | 256 000 | `AA FF 03 00` | X/Y/speed for up to 3 targets |
| **HLK-LD2410C** (Hi-Link) | 1D presence | 256 000 | `FD FC FB FA` | Distance, move/static gates, energy |

---

## Features

### All Radars
- COM port browser with descriptions + ⟳ refresh
- Manual radar type selector (sets correct baud automatically)
- Live scrolling distance chart — Y-axis accommodates **negative values** for 2D radars (LD2450 targets behind sensor plane show below zero)
- Draggable threshold line on chart
- Live readout: colour-coded status dot, distance, presence/target count, frame counter
- Sortable data table with time, distance, X, Y, status
- Event log

### HLK-LD2450 (2D tab — "2D Spatial")
- Top-down scatter plot with FOV cone (±60°)
- Up to 3 simultaneous targets, each with a distinct colour
- Per-target position **trail** (last 40 positions)
- Live text readout: X, Y, straight-line distance, speed for each target
- Distance plot shows **Y component** (depth) for each target separately

### HLK-LD2410C (2D tab — "Energy Gates")
- Moving gate energy bar chart (gates 0–8)
- Static gate energy bar chart (gates 0–8)
- Updates live in engineering mode frames

---

## Saving & Loading

**Save**: toolbar → 💾 **Save Test**  
Filename is auto-generated as `<RadarName>_YYYYMMDD_HHMMSS.csv`, e.g.:
```
HLK_LD2450_20260320_143021.csv
RD_03E_20260320_150512.csv
```

**Open**: toolbar → 📂 **Open Test**  
Radar type is inferred from the filename automatically so the correct chart layout loads.

### CSV columns (LD2450 example)
```
time_s, status, t1_x_m, t1_y_m, t1_dist_m, t1_speed, t2_x_m, ...
```

---

## Distance Plot — Sign Convention

For **1D radars** (RD-03E, LD2410C): Y-axis is always ≥ 0, distance in metres.

For **LD2450**: the Y component of each target is plotted **signed** — positive = in front of sensor, negative = behind sensor (uncommon but handled). The live readout always shows `|distance|`.

---

## Hardware Wiring

Connect each radar's TX→RX and RX→TX to a USB-UART adapter (CH340, CP2102, FT232):

```
Radar    →   USB-UART
  VCC    →   5 V (check your adapter — some sensors need 3.3 V)
  GND    →   GND
  TX     →   RX
  RX     →   TX
```

Select the resulting COM port and the matching radar type, then click **Connect**.

---

## Protocol Reference

### RD-03E (7 bytes)
```
AA  AA  [status]  [distLow]  [distHigh]  55  55
status: 0=none  1=static  2=moving
distance = (distHigh<<8 | distLow) / 100  metres
```

### HLK-LD2450 (30 bytes)
```
AA FF 03 00  [T1: x16 y16 spd16 res16]  [T2: ...]  [T3: ...]  55 CC
X/Y: signed int16 mm; if negative: real = -(abs(raw) - 0x8000)
```

### HLK-LD2410C (variable)
```
FD FC FB FA  [len16]  02 AA  [detection_type]  [move_dist16]  [move_energy]
             [stat_dist16]  [stat_energy]  [detect_dist16]  ...  04 03 02 01
```
