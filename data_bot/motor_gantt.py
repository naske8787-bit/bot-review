#!/usr/bin/env python3
"erate an HTML Gantt chart for motor runtime intervals.

CSV input must include at least:
- motor (or --motor-col)
- start_time (or --start-col)
- end_time (or --end-col)

Optional column:
- status (or --status-col), used for color coding.

Example:
python data_bot/motor_gantt.py \
  --input motor_runs.csv \
  --start 2026-04-21T00:00:00 \
  --end 2026-04-22T00:00:00 \
  --output motor_gantt.html
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import pathlib
import random
from dataclasses import dataclass


@dataclass
class MotorInterval:
    motor: str
    start: dt.datetime
    end: dt.datetime
    status: str


def parse_iso_datetime(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"Invalid datetime: {value}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def color_for_key(key: str) -> str:
    rng = random.Random(key)
    hue = rng.randint(0, 359)
    sat = 60 + rng.randint(0, 20)
    light = 45 + rng.randint(0, 10)
    return f"hsl({hue}, {sat}%, {light}%)"


def load_intervals(
    csv_path: pathlib.Path,
    motor_col: str,
    start_col: str,
    end_col: str,
    status_col: str,
) -> list[MotorInterval]:
    intervals: list[MotorInterval] = []
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = {motor_col, start_col, end_col}
        missing = expected.difference(reader.fieldnames or [])
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"Missing required CSV columns: {missing_text}")

        for idx, row in enumerate(reader, start=2):
            motor_raw = (row.get(motor_col) or "").strip()
            start_raw = (row.get(start_col) or "").strip()
            end_raw = (row.get(end_col) or "").strip()
            status_raw = (row.get(status_col) or "RUNNING").strip() if status_col else "RUNNING"

            if not motor_raw or not start_raw or not end_raw:
                continue

            try:
                start_dt = parse_iso_datetime(start_raw)
                end_dt = parse_iso_datetime(end_raw)
            except ValueError as exc:
                raise ValueError(f"Row {idx}: {exc}") from exc

            if end_dt <= start_dt:
                continue

            intervals.append(
                MotorInterval(
                    motor=motor_raw,
                    start=start_dt,
                    end=end_dt,
                    status=status_raw or "RUNNING",
                )
            )
    return intervals


def filter_and_clamp(
    intervals: list[MotorInterval],
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> list[MotorInterval]:
    clamped: list[MotorInterval] = []
    for interval in intervals:
        if interval.end <= window_start or interval.start >= window_end:
            continue
        start = max(interval.start, window_start)
        end = min(interval.end, window_end)
        if end > start:
            clamped.append(MotorInterval(interval.motor, start, end, interval.status))
    return clamped


def build_ticks(window_start: dt.datetime, window_end: dt.datetime) -> list[tuple[float, str]]:
    total_seconds = (window_end - window_start).total_seconds()
    if total_seconds <= 0:
        return []

    hours = total_seconds / 3600.0
    if hours <= 6:
        step = dt.timedelta(minutes=30)
    elif hours <= 24:
        step = dt.timedelta(hours=1)
    elif hours <= 72:
        step = dt.timedelta(hours=3)
    elif hours <= 168:
        step = dt.timedelta(hours=6)
    else:
        step = dt.timedelta(hours=24)

    first_tick = window_start.replace(minute=0, second=0, microsecond=0)
    if first_tick < window_start:
        first_tick += step

    ticks: list[tuple[float, str]] = []
    cur = first_tick
    while cur <= window_end:
        pct = ((cur - window_start).total_seconds() / total_seconds) * 100.0
        label = cur.strftime("%Y-%m-%d %H:%M")
        ticks.append((pct, label))
        cur += step
    return ticks


def render_html(
    intervals: list[MotorInterval],
    window_start: dt.datetime,
    window_end: dt.datetime,
) -> str:
        grouped: dict[str, list[MotorInterval]] = {}
        for interval in intervals:
                grouped.setdefault(interval.motor, []).append(interval)

        for motor_intervals in grouped.values():
                motor_intervals.sort(key=lambda item: item.start)

        motors = sorted(grouped.keys())
        total_seconds = (window_end - window_start).total_seconds()
        ticks = build_ticks(window_start, window_end)

        rows_html: list[str] = []
        for motor in motors:
                bars: list[str] = []
                for item in grouped[motor]:
                        left = ((item.start - window_start).total_seconds() / total_seconds) * 100.0
                        width = ((item.end - item.start).total_seconds() / total_seconds) * 100.0
                        color = color_for_key(item.status)
                        title = html.escape(
                                f"{motor} | {item.status} | {item.start.isoformat()} -> {item.end.isoformat()}"
                        )
                        bars.append(
                                f'<div class="bar" title="{title}" '
                                f'style="left:{left:.4f}%; width:{width:.4f}%; background:{color};"></div>'
                        )

                rows_html.append(
                        f'<div class="row">'
                        f'<div class="label">{html.escape(motor)}</div>'
                    f'<div class="track">'
                    f'<div class="cursor-line"></div>'
                    f'{"".join(bars)}'
                    f'</div>'
                        f"</div>"
                )

        ticks_html = "".join(
                f'<div class="tick" style="left:{pct:.4f}%"><span>{html.escape(label)}</span></div>'
                for pct, label in ticks
        )

        comparison_dataset = {
                motor: [
                        {
                                "start": item.start.isoformat(),
                                "end": item.end.isoformat(),
                                "status": item.status,
                        }
                        for item in grouped[motor]
                ]
                for motor in motors
        }
        comparison_options = "".join(
                f'<option value="{html.escape(motor)}">{html.escape(motor)}</option>' for motor in motors
        )
        comparison_html = f"""
        <div class="comparison-panel">
            <div class="comparison-header">
                <h2>Motor Comparison</h2>
                <div class="comparison-subtitle">Compare runtime overlap between any two motors in the current window.</div>
            </div>
            <div class="comparison-controls">
                <label class="comparison-field">
                    <span>Motor 1</span>
                    <select id="compare-motor-a">{comparison_options}</select>
                </label>
                <label class="comparison-field">
                    <span>Motor 2</span>
                    <select id="compare-motor-b">{comparison_options}</select>
                </label>
            </div>
            <div class="comparison-stats" id="comparison-stats"></div>
        </div>
        """

        controls_html = """
        <div class="controls" id="zoom-controls">
            <div class="zoom-group">
                <button type="button" class="zoom-button" id="zoom-out">-</button>
                <label for="zoom-slider">Zoom</label>
                <input id="zoom-slider" type="range" min="1" max="12" step="0.5" value="1" />
                <button type="button" class="zoom-button" id="zoom-in">+</button>
                <button type="button" class="zoom-button" id="zoom-reset">Fit</button>
                <span class="zoom-readout" id="zoom-readout">100%</span>
            </div>
            <div class="controls-right">
                <button type="button" class="pdf-button" id="export-pdf" onclick="window.print()">&#128438; Download PDF</button>
                <div class="controls-hint">Drag the slider or use Ctrl + mouse wheel over the chart to zoom the timeline.</div>
            </div>
        </div>
        """
        timeline_meta = {
                "windowStartMs": int(window_start.timestamp() * 1000),
                "windowEndMs": int(window_end.timestamp() * 1000),
        }

        script_html = """
        <script>
            (() => {
                const viewport = document.getElementById("timeline-viewport");
                const canvas = document.getElementById("timeline-canvas");
                const slider = document.getElementById("zoom-slider");
                const readout = document.getElementById("zoom-readout");
                const zoomIn = document.getElementById("zoom-in");
                const zoomOut = document.getElementById("zoom-out");
                const zoomReset = document.getElementById("zoom-reset");
                const comparisonStats = document.getElementById("comparison-stats");
                const comparisonMotorA = document.getElementById("compare-motor-a");
                const comparisonMotorB = document.getElementById("compare-motor-b");
                const tracks = Array.from(document.querySelectorAll(".track"));
                const cursorLines = Array.from(document.querySelectorAll(".cursor-line"));
                const tickLines = Array.from(document.querySelectorAll(".tick"));
                const cursorLabel = document.getElementById("cursor-label");
                const intervalData = __INTERVAL_DATA__;
                const timelineMeta = __TIMELINE_META__;
                const motorNames = Object.keys(intervalData);

                if (!viewport || !canvas || !slider || !readout) {
                    return;
                }

                const BASE_MIN_WIDTH = 960;

                function currentTicksLeft() {
                    const raw = getComputedStyle(document.documentElement)
                        .getPropertyValue("--ticks-left")
                        .trim()
                        .replace("px", "");
                    return Number.parseFloat(raw) || 180;
                }

                function setZoom(nextZoom) {
                    const zoom = Math.min(12, Math.max(1, nextZoom));
                    const priorScrollable = Math.max(1, canvas.scrollWidth - viewport.clientWidth);
                    const priorRatio = priorScrollable > 0 ? viewport.scrollLeft / priorScrollable : 0;
                    const ticksLeft = currentTicksLeft();
                    const baseWidth = Math.max(viewport.clientWidth, BASE_MIN_WIDTH);
                    const trackBaseWidth = Math.max(240, baseWidth - ticksLeft);
                    canvas.style.width = `${ticksLeft + trackBaseWidth * zoom}px`;
                    slider.value = String(zoom);
                    readout.textContent = `${Math.round(zoom * 100)}%`;

                    requestAnimationFrame(() => {
                        const nextScrollable = Math.max(0, canvas.scrollWidth - viewport.clientWidth);
                        viewport.scrollLeft = nextScrollable * priorRatio;
                    });
                }

                slider.addEventListener("input", () => setZoom(Number.parseFloat(slider.value)));
                zoomIn.addEventListener("click", () => setZoom(Number.parseFloat(slider.value) + 0.5));
                zoomOut.addEventListener("click", () => setZoom(Number.parseFloat(slider.value) - 0.5));
                zoomReset.addEventListener("click", () => setZoom(1));
                viewport.addEventListener(
                    "wheel",
                    (event) => {
                        if (!event.ctrlKey) {
                            return;
                        }
                        event.preventDefault();
                        const delta = event.deltaY < 0 ? 0.5 : -0.5;
                        setZoom(Number.parseFloat(slider.value) + delta);
                    },
                    { passive: false },
                );
                window.addEventListener("resize", () => setZoom(Number.parseFloat(slider.value)));

                function parseIntervals(motorName) {
                    return (intervalData[motorName] || []).map((item) => ({
                        start: new Date(item.start).getTime(),
                        end: new Date(item.end).getTime(),
                    }));
                }

                function mergeIntervals(items) {
                    if (items.length === 0) {
                        return [];
                    }

                    const sorted = [...items].sort((a, b) => a.start - b.start);
                    const merged = [sorted[0]];

                    for (let i = 1; i < sorted.length; i += 1) {
                        const current = sorted[i];
                        const last = merged[merged.length - 1];

                        if (current.start <= last.end) {
                            last.end = Math.max(last.end, current.end);
                        } else {
                            merged.push({ ...current });
                        }
                    }

                    return merged;
                }

                function sumDuration(items) {
                    return items.reduce((total, item) => total + Math.max(0, item.end - item.start), 0);
                }

                function overlappingIntervalDuration(reference, candidate) {
                    const normalizedReference = mergeIntervals(reference);
                    const normalizedCandidate = mergeIntervals(candidate);
                    let total = 0;
                    let referenceIndex = 0;
                    let candidateIndex = 0;

                    while (referenceIndex < normalizedReference.length && candidateIndex < normalizedCandidate.length) {
                        const referenceInterval = normalizedReference[referenceIndex];
                        const candidateInterval = normalizedCandidate[candidateIndex];

                        if (candidateInterval.end <= referenceInterval.start) {
                            candidateIndex += 1;
                            continue;
                        }
                        if (referenceInterval.end <= candidateInterval.start) {
                            referenceIndex += 1;
                            continue;
                        }

                        const overlapStart = Math.max(referenceInterval.start, candidateInterval.start);
                        const overlapEnd = Math.min(referenceInterval.end, candidateInterval.end);
                        total += Math.max(0, overlapEnd - overlapStart);

                        if (referenceInterval.end <= candidateInterval.end) {
                            referenceIndex += 1;
                        } else {
                            candidateIndex += 1;
                        }
                    }

                    return total;
                }

                function formatDuration(milliseconds) {
                    const totalSeconds = Math.round(milliseconds / 1000);
                    const hours = Math.floor(totalSeconds / 3600);
                    const minutes = Math.floor((totalSeconds % 3600) / 60);
                    const seconds = totalSeconds % 60;
                    if (hours > 0) {
                        return `${hours}h ${minutes}m ${seconds}s`;
                    }
                    if (minutes > 0) {
                        return `${minutes}m ${seconds}s`;
                    }
                    return `${seconds}s`;
                }

                function formatPercent(numerator, denominator) {
                    if (!denominator) {
                        return "0.0%";
                    }
                    return `${((numerator / denominator) * 100).toFixed(1)}%`;
                }

                function renderComparison() {
                    if (!comparisonStats || !comparisonMotorA || !comparisonMotorB || motorNames.length === 0) {
                        return;
                    }
                    const motorA = comparisonMotorA.value;
                    const motorB = comparisonMotorB.value;
                    const intervalsA = parseIntervals(motorA);
                    const intervalsB = parseIntervals(motorB);
                    const durationA = sumDuration(intervalsA);
                    const durationB = sumDuration(intervalsB);
                    const comparedRuntime = overlappingIntervalDuration(intervalsA, intervalsB);

                    comparisonStats.innerHTML = `
                        <div class="comparison-pill"><span>${motorA} runtime</span><strong>${formatDuration(durationA)}</strong></div>
                        <div class="comparison-pill"><span>${motorB} runtime</span><strong>${formatDuration(durationB)}</strong></div>
                        <div class="comparison-pill"><span>${motorB} runtime while ${motorA} is running</span><strong>${formatDuration(comparedRuntime)}</strong></div>
                        <div class="comparison-pill"><span>${motorB} running within ${motorA} runtime window</span><strong>${formatPercent(comparedRuntime, durationA)}</strong></div>
                        <div class="comparison-pill"><span>${motorB} total runtime compared to ${motorA}</span><strong>${formatPercent(durationB, durationA)}</strong></div>
                    `;
                }

                function showCursor(positionPercent) {
                    cursorLines.forEach((line) => {
                        line.style.left = `${positionPercent}%`;
                        line.style.opacity = "1";
                    });
                    if (cursorLabel) {
                        const span = timelineMeta.windowEndMs - timelineMeta.windowStartMs;
                        const timestamp = timelineMeta.windowStartMs + (span * positionPercent) / 100;
                        cursorLabel.textContent = new Date(timestamp).toLocaleString();
                        cursorLabel.style.left = `${positionPercent}%`;
                        cursorLabel.style.opacity = "1";
                    }
                    tickLines.forEach((line) => {
                        line.classList.add("cursor-active");
                    });
                }

                function hideCursor() {
                    cursorLines.forEach((line) => {
                        line.style.opacity = "0";
                    });
                    if (cursorLabel) {
                        cursorLabel.style.opacity = "0";
                    }
                    tickLines.forEach((line) => {
                        line.classList.remove("cursor-active");
                    });
                }

                tracks.forEach((track) => {
                    track.addEventListener("mousemove", (event) => {
                        const rect = track.getBoundingClientRect();
                        if (rect.width <= 0) {
                            return;
                        }
                        const clamped = Math.min(Math.max(event.clientX - rect.left, 0), rect.width);
                        const positionPercent = (clamped / rect.width) * 100;
                        showCursor(positionPercent);
                    });
                    track.addEventListener("mouseenter", (event) => {
                        const rect = track.getBoundingClientRect();
                        if (rect.width <= 0) {
                            return;
                        }
                        const clamped = Math.min(Math.max(event.clientX - rect.left, 0), rect.width);
                        const positionPercent = (clamped / rect.width) * 100;
                        showCursor(positionPercent);
                    });
                    track.addEventListener("mouseleave", hideCursor);
                });

                viewport.addEventListener("mouseleave", hideCursor);

                if (comparisonMotorA && comparisonMotorB && motorNames.length > 0) {
                    comparisonMotorA.value = motorNames[0];
                    comparisonMotorB.value = motorNames[Math.min(1, motorNames.length - 1)];
                    comparisonMotorA.addEventListener("change", renderComparison);
                    comparisonMotorB.addEventListener("change", renderComparison);
                    renderComparison();
                }

                setZoom(1);
            })();
        </script>
        """
        script_html = script_html.replace("__INTERVAL_DATA__", json.dumps(comparison_dataset))
        script_html = script_html.replace("__TIMELINE_META__", json.dumps(timeline_meta))

        return f"""<!doctype html>
<html lang=\"en\">
<head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Motor Runtime Gantt</title>
    <style>
        :root {{
            --bg: #0f172a;
            --panel: #111827;
            --text: #e5e7eb;
            --muted: #94a3b8;
            --track: #1f2937;
            --line: #334155;
            --label-width: 170px;
            --ticks-left: 180px;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: "Segoe UI", Tahoma, sans-serif;
            background: radial-gradient(circle at top, #1e293b 0%, var(--bg) 65%);
            color: var(--text);
            padding: 22px;
        }}
        .card {{
            max-width: 1200px;
            margin: 0 auto;
            background: color-mix(in oklab, var(--panel) 92%, black);
            border: 1px solid #243244;
            border-radius: 14px;
            padding: 18px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
        }}
        h1 {{ margin: 0 0 6px; font-size: 1.35rem; }}
        .meta {{ color: var(--muted); margin-bottom: 16px; }}
        .controls {{
            display: flex;
            gap: 16px;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            margin-bottom: 14px;
        }}
        .zoom-group {{
            display: flex;
            align-items: center;
            gap: 10px;
            flex-wrap: wrap;
        }}
        .zoom-group label {{
            color: var(--muted);
            font-size: 0.9rem;
        }}
        .zoom-group input[type="range"] {{
            width: 220px;
            accent-color: #38bdf8;
        }}
        .zoom-button {{
            appearance: none;
            border: 1px solid #314255;
            background: #172131;
            color: var(--text);
            border-radius: 6px;
            min-width: 38px;
            height: 34px;
            cursor: pointer;
            font-size: 0.95rem;
        }}
        .zoom-button:hover {{
            background: #1d2a3d;
        }}
        .zoom-readout {{
            color: #cbd5e1;
            font-variant-numeric: tabular-nums;
            min-width: 48px;
        }}
        .controls-hint {{
            color: var(--muted);
            font-size: 0.85rem;
        }}
        .comparison-panel {{
            border: 1px solid #243244;
            background: rgba(15, 23, 42, 0.65);
            border-radius: 12px;
            padding: 14px;
            margin-bottom: 14px;
        }}
        .comparison-header {{
            margin-bottom: 10px;
        }}
        .comparison-header h2 {{
            margin: 0 0 4px;
            font-size: 1rem;
        }}
        .comparison-subtitle {{
            color: var(--muted);
            font-size: 0.85rem;
        }}
        .comparison-controls {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
            margin-bottom: 12px;
        }}
        .comparison-field {{
            display: flex;
            flex-direction: column;
            gap: 6px;
            min-width: 220px;
            color: var(--muted);
            font-size: 0.85rem;
        }}
        .comparison-field select {{
            appearance: none;
            background: #172131;
            color: var(--text);
            border: 1px solid #314255;
            border-radius: 8px;
            padding: 9px 12px;
        }}
        .comparison-stats {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 10px;
        }}
        .comparison-pill {{
            border: 1px solid #243244;
            background: #111827;
            padding: 10px 12px;
            border-radius: 10px;
        }}
        .comparison-pill span {{
            display: block;
            color: var(--muted);
            font-size: 0.8rem;
            margin-bottom: 4px;
        }}
        .comparison-pill strong {{
            color: #e2e8f0;
            font-size: 1rem;
        }}
        .timeline-viewport {{
            overflow-x: auto;
            overflow-y: hidden;
            padding-bottom: 6px;
        }}
        .timeline-canvas {{
            min-width: 100%;
        }}
        .timeline {{ position: relative; border-top: 1px solid var(--line); padding-top: 34px; }}
        .ticks {{ position: absolute; left: var(--ticks-left); right: 0; top: 0; height: 30px; }}
        .cursor-label {{
            position: absolute;
            top: 4px;
            transform: translateX(-50%);
            background: rgba(15, 23, 42, 0.94);
            color: #f8fafc;
            border: 1px solid #334155;
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 11px;
            white-space: nowrap;
            opacity: 0;
            pointer-events: none;
            z-index: 8;
            box-shadow: 0 6px 14px rgba(0, 0, 0, 0.28);
        }}
        .tick {{
            position: absolute;
            top: 0;
            bottom: 0;
            width: 1px;
            background: var(--line);
        }}
        .tick span {{
            position: absolute;
            top: -2px;
            transform: translateX(-50%);
            color: var(--muted);
            font-size: 11px;
            white-space: nowrap;
            background: var(--panel);
            padding: 0 3px;
        }}
        .row {{
            display: grid;
            grid-template-columns: var(--label-width) 1fr;
            gap: 10px;
            align-items: center;
            min-height: 44px;
        }}
        .label {{
            text-align: right;
            color: #cbd5e1;
            font-weight: 600;
            padding-right: 8px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .track {{
            position: relative;
            height: 22px;
            background: var(--track);
            border-radius: 0;
            overflow: hidden;
            border: 1px solid #273548;
        }}
        .cursor-line {{
            position: absolute;
            top: -1px;
            bottom: -1px;
            width: 2px;
            background: rgba(255, 255, 255, 0.9);
            box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.7);
            transform: translateX(-1px);
            opacity: 0;
            pointer-events: none;
            z-index: 5;
        }}
        .bar {{
            position: absolute;
            top: 0;
            bottom: 0;
            border-radius: 0;
            opacity: 0.95;
            border: 1px solid rgba(255, 255, 255, 0.18);
            z-index: 2;
        }}
        .tick.cursor-active {{
            background: rgba(255, 255, 255, 0.5);
        }}
        .controls-right {{
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 6px;
        }}
        .pdf-button {{
            appearance: none;
            border: 1px solid #38bdf8;
            background: rgba(56, 189, 248, 0.12);
            color: #38bdf8;
            border-radius: 6px;
            padding: 6px 16px;
            cursor: pointer;
            font-size: 0.9rem;
            white-space: nowrap;
        }}
        .pdf-button:hover {{
            background: rgba(56, 189, 248, 0.22);
        }}
        @media print {{
            @page {{ size: A4 landscape; margin: 12mm 10mm; }}
            body {{
                background: #fff !important;
                color: #111 !important;
                padding: 0 !important;
                -webkit-print-color-adjust: exact;
                print-color-adjust: exact;
            }}
            .card {{
                max-width: 100% !important;
                background: #fff !important;
                border: none !important;
                box-shadow: none !important;
                border-radius: 0 !important;
                padding: 0 !important;
            }}
            h1 {{ color: #111 !important; }}
            .meta {{ color: #555 !important; }}
            #zoom-controls, .comparison-controls, .comparison-subtitle {{ display: none !important; }}
            .comparison-panel {{
                border: 1px solid #ccc !important;
                background: #f9fafb !important;
                border-radius: 6px !important;
            }}
            .comparison-header h2 {{ color: #111 !important; }}
            .comparison-pill {{
                border: 1px solid #ccc !important;
                background: #f3f4f6 !important;
            }}
            .comparison-pill span {{ color: #555 !important; }}
            .comparison-pill strong {{ color: #111 !important; }}
            .timeline-viewport {{
                overflow: visible !important;
            }}
            .timeline-canvas {{
                width: 100% !important;
            }}
            .cursor-line, #cursor-label {{ display: none !important; }}
            .track {{
                background: #e5e7eb !important;
                border-color: #d1d5db !important;
            }}
            .label {{ color: #111 !important; }}
            .tick {{ background: #cbd5e1 !important; }}
            .tick span {{ color: #555 !important; background: #fff !important; }}
        }}
        .empty {{
            color: #fca5a5;
            border: 1px solid #7f1d1d;
            background: rgba(127, 29, 29, 0.2);
            border-radius: 10px;
            padding: 10px 12px;
            font-size: 0.95rem;
        }}
        @media (max-width: 900px) {{
            :root {{
                --label-width: 120px;
                --ticks-left: 130px;
            }}
            .label {{ font-size: 0.85rem; }}
            .tick span {{ font-size: 10px; }}
            .controls {{ align-items: flex-start; }}
            .zoom-group input[type="range"] {{ width: 160px; }}
        }}
    </style>
</head>
<body>
    <div class=\"card\">
        <h1>Motor Runtime Gantt</h1>
        <div class=\"meta\">Window: {html.escape(window_start.isoformat())} to {html.escape(window_end.isoformat())}</div>
        {
            '<div class="empty">No intervals found in this time range.</div>'
            if not motors
            else (
                comparison_html
                +
                controls_html
                + '<div class="timeline-viewport" id="timeline-viewport">'
                + '<div class="timeline-canvas" id="timeline-canvas">'
                + '<div class="timeline">'
                + f'<div class="ticks">{ticks_html}</div>'
                + '<div class="cursor-label" id="cursor-label"></div>'
                + "".join(rows_html)
                + "</div>"
                + "</div>"
                + "</div>"
                + script_html
            )
        }
    </div>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a motor runtime Gantt chart.")
    parser.add_argument("--input", required=True, help="Path to CSV file with runtime intervals.")
    parser.add_argument("--output", default="motor_gantt.html", help="Output HTML file path.")
    parser.add_argument("--start", required=True, help="Window start datetime (ISO format).")
    parser.add_argument("--end", required=True, help="Window end datetime (ISO format).")
    parser.add_argument("--motor-col", default="motor", help="CSV column name for motor ID.")
    parser.add_argument("--start-col", default="start_time", help="CSV column for start timestamp.")
    parser.add_argument("--end-col", default="end_time", help="CSV column for end timestamp.")
    parser.add_argument(
        "--status-col",
        default="status",
        help="Optional CSV column for status (used in bar color grouping).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_path = pathlib.Path(args.input)
    output_path = pathlib.Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input CSV not found: {input_path}")

    window_start = parse_iso_datetime(args.start)
    window_end = parse_iso_datetime(args.end)
    if window_end <= window_start:
        raise ValueError("--end must be after --start")

    intervals = load_intervals(
        csv_path=input_path,
        motor_col=args.motor_col,
        start_col=args.start_col,
        end_col=args.end_col,
        status_col=args.status_col,
    )
    intervals = filter_and_clamp(intervals, window_start, window_end)

    html_output = render_html(intervals, window_start, window_end)
    output_path.write_text(html_output, encoding="utf-8")

    print(f"Saved Gantt chart: {output_path}")
    print(f"Intervals in window: {len(intervals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
