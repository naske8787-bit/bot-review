import csv
from datetime import datetime, timedelta
import os

input_file = "12CM27-06451 Sensors - Metrics 1-4-data-2026-04-22 17_20_46.csv"
output_file = "data_bot/input_data/JM6451_motor_intervals.csv"
os.makedirs(os.path.dirname(output_file), exist_ok=True)

data = []
with open(input_file, 'r') as f:
    reader = csv.DictReader(f)
    for row in reader:
        try:
            ts = datetime.strptime(row['TIME'], '%Y-%m-%d %H:%M:%S')
            val = float(row['CM_Cutt_Mtr1_Rt_Ph1_Cur_A'])
            data.append((ts, val))
        except (ValueError, KeyError):
            continue

data.sort(key=lambda x: x[0])

if not data:
    print("No data found")
    exit(1)

max_current = max(d[1] for d in data)
min_current = min(d[1] for d in data)
threshold = max(0.5, 0.01 * max_current)

intervals = []
current_interval = None

for i in range(len(data)):
    ts, val = data[i]
    is_on = val > threshold
    
    if is_on:
        if current_interval is None:
            current_interval = [ts, ts]
        else:
            gap = (ts - current_interval[1]).total_seconds()
            if gap > 90:
                intervals.append(current_interval)
                current_interval = [ts, ts]
            else:
                current_interval[1] = ts
    else:
        if current_interval is not None:
            intervals.append(current_interval)
            current_interval = None

if current_interval is not None:
    intervals.append(current_interval)

with open(output_file, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['motor', 'start_time', 'end_time', 'status'])
    for start, end in intervals:
        writer.writerow(['JM6451_Motor_Current', start.strftime('%Y-%m-%d %H:%M:%S'), end.strftime('%Y-%m-%d %H:%M:%S'), 'DRAWING_CURRENT'])

start_win = data[0][0].strftime('%Y-%m-%dT%H:%M:%SZ')
end_win = data[-1][0].strftime('%Y-%m-%dT%H:%M:%SZ')

print(f"Summary:")
print(f"Rows read: {len(data)}")
print(f"Min current: {min_current}")
print(f"Max current: {max_current}")
print(f"Threshold used: {threshold}")
print(f"Intervals count: {len(intervals)}")
print(f"Output file path: {output_file}")
print(f"Window: {start_win} to {end_win}")

# For usage in next step
with open('window.txt', 'w') as f:
    f.write(f"{start_win} {end_win}")
