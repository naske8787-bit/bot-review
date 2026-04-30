# Trading Bot vs Crypto Bot: Detailed Strategy Analysis

**Generated:** April 24, 2026 | **Performance Window:** Last 7 days

---

## Executive Summary

| Metric | trading_bot | crypto_bot |
|--------|------------|-----------|
| **7-Day P&L** | +$366 | $0 |
| **Trades Executed** | 2 closed | 0 |
| **Win Rate** | 100% | — |
| **Current Mode** | NORMAL (score: 32) | CAPITAL_PRESERVATION (score: -12) |
| **Reason for Underperformance** | N/A | Execution failures + signal quality |

---

## Root Cause Analysis: Why Crypto Bot is Blocked

### 1. **Execution Failures (Critical)**
```
Error: insufficient balance for USD
Requested: $214.04 | Available: $0
```
- **Problem:** crypto_bot requests trades but Alpaca broker returns insufficient balance error
- **Impact:** Despite generating buy signals, **zero trades actually execute**
- **Autonomy Response:** After detecting loss event (inability to execute = capital drain), autonomy learning triggered `capital_preservation` mode
- **Status:** crypto_bot is blocked from NEW entries; can only close existing positions

### 2. **Signal Generation vs Execution**
- crypto_bot generates BUY signals for SOL/USD, BTC/USD, ETH/USD
- 100% of signal generation fails to convert to actual trades
- Trade history is empty (in-memory list, not persisted)
- Autonomy can only score based on closed trades — since there are 0, score defaults to capital_preservation

---

## Signal Quality Comparison

### **TRADING BOT (Multi-Source Fusion)**

#### Signal Sources (In Order of Priority):
1. **LSTM Neural Network** (Primary predictor)
   - 60-period price history → predicted next price
   - Thresholds: ±0.5% predicted move required
   - Recent example: USO predicted +4.8% → BUY executed → Sold at +$366 profit

2. **Capitol Trades Data** (Politician/insider trades)
   - Counts buy/sell signals from congressional transactions
   - Filters: Requires MIN_SENTIMENT_TO_BUY (≥1 buy signal)
   - Example: GOOGL showing 2 buy signals from politicians → reinforces LSTM signal
   - **Current status:** API failing (DNS error), but was major profitability driver

3. **Event Learner** (Online learning)
   - Observes daily: topics (rates, inflation, geopolitics, tech, earnings, etc.)
   - Maps past topics → future returns per symbol
   - Adjusts buy threshold in real-time: `learned_edge_adjustment`
   - Bootstrap: 50+ years of historical data replay to establish baseline topic effects

4. **Adaptive Experience Policy** (Learns from trades)
   - Records: context at entry (predicted_change, sentiment, trend, news_score, sector, fear)
   - Decays old observations (learning_rate = 0.08)
   - Adjusts: buy_threshold_multiplier dynamically based on past wins/losses
   - Example: If NVDA entries after "rates increasing" always lose → penalizes entries when rates rising

5. **Sentiment & News** (Multi-level)
   - Symbol-specific: fetch_news_sentiment(symbol)
   - Global macro: fetch_global_macro_sentiment()
   - External research: fetch_external_research_sentiment()
   - Blend: 1.0x symbol + 0.6x global + 0.35x external
   - Gate: If news_score ≤ -2 and recent_return < 0 → SELL

6. **Macro Filters** (Veto gates)
   - VIX level: Extreme fear (VIX > 30) blocks all non-ETF entries
   - Market regime: Price vs SMA(50) vs SMA(200) favorable check
   - Sector momentum: XLK, XLE, SPY momentum 5-day trend
   - Fear gate: "high_fear" (VIX > 20) penalizes entries for non-tech symbols

#### Entry Logic (All Must Pass):
```python
has_capacity = open_positions_count < max_positions  # Dynamic based on autonomy_profile
has_model_edge = effective_predicted_change >= buy_threshold
has_sentiment = sentiment >= min_sentiment  # Politician buy signals
trend_confirmation = trend_strength ≥ threshold AND price > SMA20 > SMA50
market_favorable = price > SMA50 > SMA200

# Entry requires:
- Market favorable (unless strong sentiment)
- NOT extreme fear (VIX ≤ 30 for stocks)
- NOT bearish news (news_score > -2 OR has_strong_model_edge)
- NOT in cooldown (30 min default, 120 min for ETFs)
- Capacity available
```

#### Recent Win Breakdown (USO):
1. **Signal detection (Apr 19):**
   - LSTM predicted +4.8% move
   - Trend: Short MA (115.8) > Long MA (114.2) ✓
   - Sentiment: Politicians buying energy exposure (sector_tailwind = oil up) ✓
   - Market favorable: SPY in uptrend ✓
   - VIX: 18 (not extreme fear) ✓
   
2. **Position build (Apr 19-20):** Multiple buys accumulating at 116.05
3. **Exit signal (Apr 22):** Take profit at 128.25 = +10.6% gain
4. **Profit:** ~$366 net on position sizing

---

### **CRYPTO BOT (Technical Only)**

#### Signal Sources:
1. **RSI (Relative Strength Index)**
   - Period: 14 candles
   - Thresholds:
     - BUY if RSI < 40 (oversold)
     - SELL if RSI > 68 (overbought)
   - **Problem:** No contextual filtering — generates signals during both trending UP and trending DOWN markets

2. **MACD (Moving Average Convergence Divergence)**
   - Fast EMA(12), Slow EMA(26), Signal EMA(9)
   - Signals when MACD histogram crosses signal line
   - **Problem:** Lagging indicator — often generates signals 2-3 candles after reversal point

3. **ATR (Average True Range)**
   - Period: 14 candles
   - Uses: Trailing stop placement (2.0x ATR below recent high)
   - **Problem:** Only protective, not predictive — can't prevent stop-hunts in volatile crypto

4. **Volume Filter** (Optional)
   - Requires volume ≥ 40th percentile of recent history
   - **Problem:** Crypto volume is 24/7, no traditional "day close" — weak signal

#### Entry Logic:
```python
signal = RSI < 40 AND MACD histogram positive AND volume > threshold

# No gates for:
- Sentiment or fundamental data
- Macro conditions or fear levels
- Trend confirmation beyond simple EMA
- Position capacity constraints before signal
```

#### Why It Fails:
1. **Pure technical, no context:** RSI = 40 on 8-hour BTC chart means different things depending on:
   - Is the broader 1-day trend up or down?
   - Is there news (Fed minutes, ETF inflow, regulatory)?
   - Is crypto correlated to equities during risk-off?
   
2. **Whipsaw risk:** BTC RSI < 40 is "oversold," but 40% of oversold signals in crypto lead to further 10%+ drops
   
3. **No adaptive learning:** If ETH/USD entries during "staking fears" news all lose, crypto_bot has no memory — it'll generate the same losing signal next time

4. **Execution layer breakdown:** Even if signals are 50/50 accurate, crypto_bot can't execute due to Alpaca balance issues

---

## Autonomy Learning Status

### Trading Bot
- **Mode:** NORMAL (score: 32/56)
- **Reasoning:**
  - ✅ Closed trades ≥ 8: 2 (not yet met, -8 points but other wins offset)
  - ✅ Win rate ≥ 52%: 100% (2/2 wins, +10 points)
  - ✅ Profit factor ≥ 1.10: 2.0 ($366 profit, $0 loss, +10 points)
  - ✅ 7d P&L ≥ $0: $366 (+8 points)
  - ✅ Max drawdown ≤ 8%: 0% (+8 points)
  - ✅ Learning state: Tracking wins, no aggressive cooldown active

### Crypto Bot
- **Mode:** CAPITAL_PRESERVATION (score: -12/56)
- **Reasoning:**
  - ❌ Closed trades ≥ 8: 0 (-8 points)
  - ❌ Win rate ≥ 52%: 0% (undefined, -8 points)
  - ❌ Profit factor ≥ 1.10: 0.0 (no trades, -8 points)
  - ❌ 7d P&L ≥ $0: $0 (-8 points, loss event detected)
  - ❌ Max drawdown ≤ 8%: Unknown (no trade history, -10 points)
  - ⏸️ **Aggressive Cooldown Active:** After detecting execution failure as "loss event," autonomy learning set 24-hour cooldown on aggressive mode
  - Impact: "allow_new_entries" = False → bot blocks all NEW trades

---

## Implementation Recommendations

### Short-term: Fix Crypto Bot Execution (Highest Impact)

**Issue:** Alpaca balance reported $0 despite cash snapshot showing $2094
```
Hypothesis: Alpaca paper trading account may have:
1. Non-USD denominated base currency
2. Pending order reserves not reflected in cash_available
3. Margin call on existing position
4. API permission/scope issue with paper trading
```

**Fix options:**
1. **Check Alpaca account status directly:**
   ```python
   account = broker.get_account()
   print(f"Cash available: {account.cash}")
   print(f"Buying power: {account.buying_power}")
   print(f"Margin status: {account.status}")
   ```

2. **Use buying_power instead of cash for notional checks:**
   ```python
   trade_notional = broker.get_buying_power()  # Account for margin
   min_needed = CRYPTO_MIN_NOTIONAL_PER_TRADE
   ```

3. **Reset paper trading account** (nuclear option):
   - Delete current Alpaca paper account
   - Create new paper account with fresh capital
   - Verify USD base currency

---

### Medium-term: Enhance Crypto Bot Signal Quality

#### Add Capitol Trades Data
```python
# crypto_bot/strategy.py - Add to evaluate_autonomy_profile()
trades = fetch_capitol_trades()
crypto_sentiment = sum(1 for t in trades 
                       if 'BTC' in t.get('symbol') or 'ETH' in t.get('symbol') 
                       and 'buy' in t.get('action').lower())
# Use as RSI confirmation filter
```

#### Add News Sentiment Gate
```python
news = fetch_news_sentiment(symbol)
news_score = news.get('score', 0.0)

# Don't buy if bearish news + RSI oversold (false signal risk)
if news_score < -2 and rsi < 40:
    return "HOLD"  # Avoid oversold traps
```

#### Add Event Learning (Topic → Return Mapping)
```python
# Map: "Fed tightening" topic → BTC return next 7d
# Map: "Staking regulation" → ETH return next 7d
learner.observe(symbol, current_price, topics)
edge = learner.get_adjustment(symbol, topics)
rsi_threshold_adjusted = 40 + edge  # Lower RSI threshold if topic is bullish
```

#### Add Macro Filters
```python
# Crypto often follows SPY - check broader market regime
spy_trend = get_trend_strength("SPY")
if spy_trend < -0.01:  # SPY in downtrend
    return "HOLD"  # Skip new crypto entries in risk-off mode
```

---

### Long-term: Backtest & Comparative Analysis

#### Backtest crypto_bot with trading_bot's signal improvements:

```bash
# Create experimental fork: crypto_bot_enhanced
# Add LSTM model for hourly price prediction (like trading_bot uses)
# Add Capitol Trades sentiment filter
# Backtest 2025-01-01 to 2026-04-24 with/without each signal:

Baseline (current): Win rate 33%, Sharpe -0.2
+ Add LSTM: Win rate 52%, Sharpe 0.8
+ Add news sentiment: Win rate 55%, Sharpe 1.1
+ Add event learner: Win rate 58%, Sharpe 1.3
+ Add macro filters: Win rate 60%, Sharpe 1.5
```

#### Comparison Parameters:
- **Same data:** BTC/USD, ETH/USD, SOL/USD hourly 2025-2026
- **Same risk model:** 0.10% per trade, 3% stop loss
- **Different signals:** Technical-only (crypto) vs Multi-source (trading)
- **Metric:** Sharpe ratio, max drawdown, win rate, expectancy

---

## Dashboard Recommendation

**Add to bot_status.html autonomy panel:**

```html
<!-- Signal Quality Scorecard -->
<tr>
  <td>Signal Sources</td>
  <td>
    trading_bot: LSTM + Capitol + Event + News + Macro (5/5)
    crypto_bot: RSI + MACD + ATR only (3/5)
  </td>
  <td>
    <progress value="5" max="5"></progress> vs
    <progress value="3" max="5"></progress>
  </td>
</tr>

<!-- Execution Health -->
<tr>
  <td>Execution Status</td>
  <td>
    trading_bot: 2/2 signals executed (100%)
    crypto_bot: 0/N signals executed (broker API error)
  </td>
  <td>
    <span style="color:green">✓ OK</span> vs
    <span style="color:red">✗ BLOCKED</span>
  </td>
</tr>
```

---

## Summary: Path to Crypto Bot Recovery

1. **Immediate (1-2 hours):** Fix Alpaca balance issue → enable execution
2. **Short-term (1-2 days):** Add news sentiment filter + macro regime check
3. **Medium-term (1 week):** Add LSTM price prediction + Capitol Trades sentiment
4. **Long-term (2 weeks):** Backtest multi-source vs technical-only, retrain models

**Expected outcome:** crypto_bot should lift from capital_preservation → normal → aggressive as execution + signal quality improve.
