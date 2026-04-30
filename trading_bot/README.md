# Automated Stock Trading Bot

This project contains a simple machine-learning trading bot that combines market data with Capitol Trades activity and sends paper trades through Alpaca.

## Features
- Fetches stock price history with `yfinance`
- Pulls recent Capitol Trades activity for simple sentiment signals
- Trains and loads an LSTM-based prediction model
- Learns from closed trades with an adaptive experience policy
- Supports smoke testing and basic backtesting

## Setup
1. `cd trading_bot`
2. Install dependencies: `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your Alpaca credentials
4. If model artifacts are missing, generate them with `python train.py`

## Useful commands
- Safe smoke test: `python test_bot.py`
- Backtest: `python backtest.py`
- Run the bot: `python main.py`
- Background run: `./run_bot.sh`
- Persistent `tmux` run with auto-restart: `./run_tmux.sh`
- Reattach to the persistent session: `./attach_tmux.sh`
- Stop the bot or tmux session: `./stop_bot.sh`
- Check status: `./status_bot.sh`
- Performance report: `python report.py`

## Performance tracking
- The bot now writes paper-trading logs to `trading_bot/logs/`
- `trade_log.csv` records each executed buy/sell
- `equity_log.csv` records periodic portfolio snapshots for the month-long trial
- `supervisor.log` records automatic restarts when using `./run_tmux.sh`

## Adaptive learning
- The strategy now combines:
	- Model prediction edge
	- Event/news impact learner
	- Experience policy adjustment from realized trade outcomes
- Experience policy state is saved in `trading_bot/models/experience_policy_state.json`
- Tune behavior with these `.env` values:
	- `ADAPTIVE_POLICY_ENABLED`
	- `ADAPTIVE_POLICY_LEARNING_RATE`
	- `ADAPTIVE_POLICY_DECAY`
	- `ADAPTIVE_POLICY_MAX_ADJUSTMENT_PCT`
- Safety note: this layer only nudges entry confidence and does not bypass stop-loss, take-profit, max positions, cooldown, or market regime filters

## Unattended running
- For the most reliable local setup, start the bot with `./run_tmux.sh`
- This keeps it inside a `tmux` session and automatically restarts it if the Python process exits
- In this dev container, the workspace is also configured to auto-run the bot when the folder opens in VS Code
- It still depends on the machine or dev container staying awake and active

## Start automatically on Linux boot
- Use `./boot_start_bot.sh` to launch the bot safely during startup
- On a normal Linux VM/server with cron installed, run `./install_autostart.sh` once to register an `@reboot` job
- The startup script waits briefly for networking, then starts `./run_tmux.sh` only if the bot is not already running
- Boot-time output is appended to `startup.log`

## Recommended for a real Ubuntu VPS: `systemd`
- Create a virtual environment and install deps: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Review `trading-bot.service.example` for the service layout
- Install with `sudo ./install_systemd_service.sh`
- Check status with `sudo systemctl status trading-bot`
- View logs with `journalctl -u trading-bot -n 50 --no-pager`

## Notes
- Use an Alpaca **paper trading** account first.
- `.env`, logs, PID files, and generated model artifacts should stay out of the PR.

## Disclaimer
Automated trading involves risk. Use paper trading first and review the logic before enabling any live execution.