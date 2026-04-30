import csv
import glob
import os
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

# File paths
files = [
    "data_bot/input_data/12CM27-06451 Sensors - Metrics 1-4-data-2026-04-22 19_54_36.csv",
    "data_bot/input_data/12CM27-06451 Sensors - Metrics 1-4-data-2026-04-22 19_55_10.csv",
    "data_bot/input_data/12CM27-06451 Sensors - Metrics 1-4-data-2026-04-22 19_55_16.csv"
]

DISPLAY_NAMES = {
    "CM_Cutt_Mtr1_Rt_Ph1_Cur_A": "Right Cutter Motor",
    "CM_Cutt_Mtr1_Lft_Ph1_Cur_A": "Left Cutter Motor",
    "CM_Dust_Fan1_Mtr1_Ph1_Cur_A": "Dust Fan Motor",
}

CUTTER_COLUMNS = [
    "CM_Cutt_Mtr1_Rt_Ph1_Cur_A",
    "CM_Cutt_Mtr1_Lft_Ph1_Cur_A",
]

def parse_csv(filepath):
    data = []
    motor_col = None
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for col in reader.fieldnames:
            if col != 'TIME':
                motor_col = col
                break
        for row in reader:
            if row['TIME'] and row[motor_col]:
                try:
                    ts = datetime.strptime(row['TIME'], '%Y-%m-%d %H:%M:%S')
                    val = float(row[motor_col])
                    data.append({'ts': ts, 'val': val})
                except ValueError:
                    continue
    data.sort(key=lambda x: x['ts'])
    return motor_col, data

motor_data = {}
for f in files:
    col_name, data = parse_csv(f)
    motor_data[col_name] = data

def get_intervals(data, threshold, motor_name, status):
    intervals = []
    on_points = [d for d in data if d['val'] > threshold]
    if not on_points:
        return []
    
    start_ts = on_points[0]['ts']
    prev_ts = on_points[0]['ts']
    
    for i in range(1, len(on_points)):
        curr_ts = on_points[i]['ts']
        if (curr_ts - prev_ts).total_seconds() > 90:
            intervals.append((motor_name, start_ts, prev_ts, status))
            start_ts = curr_ts
        prev_ts = curr_ts
    intervals.append((motor_name, start_ts, prev_ts, status))
    return intervals

all_intervals = []
thresholds = {}

# Individual motors
for motor_name, data in motor_data.items():
    max_val = max(d['val'] for d in data)
    thresh = max(0.5, 0.01 * max_val)
    thresholds[motor_name] = thresh
    display_name = DISPLAY_NAMES.get(motor_name, motor_name)
    all_intervals.extend(get_intervals(data, thresh, display_name, "DRAWING_CURRENT"))

# Derived series: both cutter motors individually above 55A
cutter_motors = [m for m in CUTTER_COLUMNS if m in motor_data]
if len(cutter_motors) == 2:
    all_ts = sorted(list(set(d['ts'] for m in cutter_motors for d in motor_data[m])))
    
    def get_locf_series(ts_list, data):
        vals = {}
        idx = 0
        current_val = 0.0
        for ts in ts_list:
            while idx < len(data) and data[idx]['ts'] <= ts:
                current_val = data[idx]['val']
                idx += 1
            vals[ts] = current_val
        return vals

    locf_series = {m: get_locf_series(all_ts, motor_data[m]) for m in cutter_motors}
    under_load_data = []
    for ts in all_ts:
        right_current = locf_series[cutter_motors[0]][ts]
        left_current = locf_series[cutter_motors[1]][ts]
        if right_current > 55 and left_current > 55:
            under_load_data.append({'ts': ts, 'val': 1.0})

    all_intervals.extend(
        get_intervals(under_load_data, 0.5, "Cutter Motors Under Load", "UNDER_LOAD")
    )

# Write CSV
output_csv = "data_bot/input_data/JM6451_three_motors_gantt_intervals.csv"
with open(output_csv, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(["motor", "start_time", "end_time", "status"])
    for row in all_intervals:
        writer.writerow([row[0], row[1].strftime('%Y-%m-%d %H:%M:%S'), row[2].strftime('%Y-%m-%d %H:%M:%S'), row[3]])

# Global window
all_timestamps = [d['ts'] for m in motor_data for d in motor_data[m]]
window_start = min(all_timestamps)
window_end = max(all_timestamps)

# Output summary
print("Motor columns and thresholds:")
for m, t in thresholds.items():
    display_name = DISPLAY_NAMES.get(m, m)
    print(f"  {display_name} ({m}): threshold={t:.2f}")

print("\nInterval counts:")
counts = Counter(row[0] for row in all_intervals)
for m, c in counts.items():
    print(f"  {m}: {c} intervals")

print(f"\nGlobal Window: {window_start} to {window_end}")
print(f"Output CSV: {output_csv}")

# Run motor_gantt.py
output_html = "data_bot/static/JM6451_three_motors_gantt.html"
os.makedirs("data_bot/static", exist_ok=True)
cmd = [
    "/usr/bin/python3", "data_bot/motor_gantt.py",
    "--input", output_csv,
    "--output", output_html,
    "--start", window_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
    "--end", window_end.strftime('%Y-%m-%dT%H:%M:%SZ')
]
subprocess.run(cmd, check=True)
print(f"Output HTML: {output_html}")
