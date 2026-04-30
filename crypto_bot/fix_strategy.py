import sys

with open('/workspaces/Capitol_Trades_API/crypto_bot/strategy.py', 'r') as f:
    content = f.read()

# Fix Indentation: remove the "Added to fix" line and re-align
import re
content = re.sub(r'# Added to fix IndentationError\s+', '', content)

# Fix UnboundLocalError
# Move variable definitions for macd_bullish and oversold_rebound
# to the beginning of the analyze_signal function (after other core variables)

insertion_point = "trend_strength = (ema_fast - ema_slow) / max(abs(ema_slow), 1e-9)"
setup_vars = """
        macd_line, macd_signal, macd_hist = self._compute_macd(
            close, CRYPTO_MACD_FAST, CRYPTO_MACD_SLOW, CRYPTO_MACD_SIGNAL
        )
        macd_bullish = macd_line > macd_signal and macd_hist > 0
        oversold_rebound = rsi <= CRYPTO_RSI_BUY_THRESHOLD and momentum_pct >= 0 and macd_bullish
"""

# Remote existing redundant definitions to avoid confusion and clean up
content = content.replace("            macd_bullish = macd_line > macd_signal and macd_hist > 0", "")
content = content.replace("            oversold_rebound = rsi <= CRYPTO_RSI_BUY_THRESHOLD and momentum_pct >= 0 and macd_bullish", "")
content = content.replace('            macd_line, macd_signal, macd_hist = self._compute_macd(\n                close, CRYPTO_MACD_FAST, CRYPTO_MACD_SLOW, CRYPTO_MACD_SIGNAL\n            )', "")

if insertion_point in content:
    content = content.replace(insertion_point, insertion_point + setup_vars)
    print("Injected variables at top of function.")
else:
    print("Failed to find insertion point.")

with open('/workspaces/Capitol_Trades_API/crypto_bot/strategy.py', 'w') as f:
    f.write(content)
