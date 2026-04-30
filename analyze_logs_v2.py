import re
from datetime import datetime
from pathlib import Path

ROOT = Path('/workspaces/Capitol_Trades_API')
try:
    CHANGE_TS = datetime.fromtimestamp((ROOT / 'trading_bot/.env').stat().st_mtime)
except:
    CHANGE_TS = datetime.now()

# Match signals and snippets with PnL or value
SIGNAL_RX = re.compile(r'([A-Z]+): (BUY|SELL|HOLD)', re.I)
VALUE_RX = re.compile(r'value=\$?([0-9.]+)', re.I)
PNL_RX = re.compile(r'pnl_7d=([-0-9.]+)', re.I)

def summarize_log(path: Path):
    signals = []
    values = []
    pnls = []
    
    with path.open('r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # Since we lack precise timestamps per line, we treat the whole log as 'current' or try to chunk it.
            # But for this task, let's just count global stats.
            sig = SIGNAL_RX.search(line)
            if sig: signals.append(sig.group(2).upper())
            
            val = VALUE_RX.search(line)
            if val: values.append(float(val.group(1)))
            
            pnl = PNL_RX.search(line)
            if pnl: pnls.append(float(pnl.group(1)))
            
    return {'signals': signals, 'values': values, 'pnls': pnls}

for rel in ['crypto_bot/bot.log', 'trading_bot/bot.log']:
    p = ROOT / rel
    if p.exists():
        res = summarize_log(p)
        print(f"File: {rel}")
        print(f"  Signals: BUY={res['signals'].count('BUY')}, SELL={res['signals'].count('SELL')}, HOLD={res['signals'].count('HOLD')}")
        if res['values']:
            print(f"  Portfolio Value: Latest=${res['values'][-1]:.2f}, Max=${max(res['values']):.2f}, Min=${min(res['values']):.2f}")
        if res['pnls']:
            print(f"  7d PnL: Latest={res['pnls'][-1]:.2f}, Max={max(res['pnls']):.2f}")
