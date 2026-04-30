# Crypto Trading Bot

This is a separate **paper-trading crypto bot** built alongside the existing stock bot.

## Strategy
It uses a simple automatic ruleset based on:
- fast/slow EMA trend direction
- RSI oversold / take-profit checks
- basic risk controls with stop loss and position sizing

## Default pairs
- `BTC/USD`
- `ETH/USD`
- `SOL/USD`

## Setup
1. Use the same Alpaca paper-trading credentials already configured for the repo.
2. Optional: create `crypto_bot/.env` or add these to your existing env file:
   - `CRYPTO_WATCHLIST=BTC/USD,ETH/USD,SOL/USD`
   - `CRYPTO_LOOP_INTERVAL_SECONDS=300`
   - `CRYPTO_RISK_PER_TRADE=0.10`
   - `CRYPTO_PAPER_ONLY=true`
3. Install requirements if needed:
   ```bash
   pip install -r requirements.txt
   ```

## Run
```bash
cd crypto_bot
python test_bot.py        # safe smoke test
python main.py            # run the crypto bot
bash run_tmux.sh          # run with auto-restart in tmux
bash status_bot.sh        # check status
bash stop_bot.sh          # stop it
```

## Important
- It is configured for **paper trading by default**.
- Test and tune the thresholds before using any real money.
- Crypto markets run 24/7, so keep position sizing conservative.
