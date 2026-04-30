import pandas as pd
import numpy as np
from datetime import datetime

def analyze_trading_bot():
    try:
        trades = pd.read_csv('/workspaces/Capitol_Trades_API/trading_bot/logs/trade_log.csv')
        equity = pd.read_csv('/workspaces/Capitol_Trades_API/trading_bot/logs/equity_log.csv')
        
        # Trading Bot doesn't have PnL in trade_log (only BUY actions seen)
        # We need to estimate or look for SELLs
        sells = trades[trades['action'] == 'SELL']
        
        # If no sells, we can't calculate realized PnL from trade log
        # Look at equity log
        equity['timestamp'] = pd.to_datetime(equity['timestamp'])
        # Filter out 0 value entries which look like artifacts
        equity = equity[equity['portfolio_value'] > 0]
        
        if equity.empty:
            return "Trading Bot: No valid equity data."

        start_date = equity['timestamp'].min()
        end_date = equity['timestamp'].max()
        start_val = equity.iloc[0]['portfolio_value']
        end_val = equity.iloc[-1]['portfolio_value']
        
        total_return = end_val - start_val
        
        # Max Drawdown
        equity['cummax'] = equity['portfolio_value'].cummax()
        equity['drawdown'] = (equity['portfolio_value'] - equity['cummax']) / equity['cummax']
        max_dd = equity['drawdown'].min()
        
        num_trades = len(trades)
        
        return {
            "name": "Trading Bot",
            "date_range": f"{start_date} to {end_date}",
            "num_trades": num_trades,
            "realized_pnl": "N/A (No SELL trades found in log)",
            "avg_pnl": "N/A",
            "win_rate": "N/A",
            "max_drawdown": f"{max_dd:.2%}",
            "total_return": total_return,
            "synthetic": "Highly likely synthetic (identical prices/times or round values)"
        }
    except Exception as e:
        return f"Trading Bot Error: {str(e)}"

def analyze_crypto_bot():
    try:
        trades = pd.read_csv('/workspaces/Capitol_Trades_API/crypto_bot/logs/trade_log.csv')
        if trades.empty:
            return "Crypto Bot: No trade data."

        trades['entry_time'] = pd.to_datetime(trades['entry_time'])
        trades['exit_time'] = pd.to_datetime(trades['exit_time'])
        
        start_date = trades['entry_time'].min()
        end_date = trades['exit_time'].max()
        
        num_trades = len(trades)
        realized_pnl_sum = trades['pnl'].sum()
        avg_pnl = trades['pnl'].mean()
        win_rate = (trades['pnl'] > 0).mean()
        
        # Cumulative PnL for drawdown proxy
        trades = trades.sort_values('exit_time')
        trades['cum_pnl'] = trades['pnl'].cumsum()
        # Assume starting capital of e.g. 1000 for drawdown proxy if not available
        initial_cap = 1000
        trades['equity'] = initial_cap + trades['cum_pnl']
        trades['cummax'] = trades['equity'].cummax()
        trades['drawdown'] = (trades['equity'] - trades['cummax']) / trades['cummax']
        max_dd = trades['drawdown'].min()

        return {
            "name": "Crypto Bot",
            "date_range": f"{start_date} to {end_date}",
            "num_trades": num_trades,
            "realized_pnl": realized_pnl_sum,
            "avg_pnl": avg_pnl,
            "win_rate": f"{win_rate:.2%}",
            "max_drawdown": f"{max_dd:.2%}",
            "total_return": realized_pnl_sum,
            "synthetic": "Likely synthetic (regular intervals, round price increments)"
        }
    except Exception as e:
        return f"Crypto Bot Error: {str(e)}"

print("--- Analysis Results ---")
print(analyze_trading_bot())
print("\n")
print(analyze_crypto_bot())
