"""
LSTM model for ASX stock price prediction with online (incremental) learning.

Trains on historical OHLCV + indicator bars, then continuously fine-tunes
after every new intraday bar — so the model keeps adapting to today's
market microstructure.

Architecture:
  LSTM(128) → Dropout → LSTM(64) → Dropout → Dense(32) → Dense(1)

Online learning:
  After each new bar the model runs 1 epoch of gradient update on the most
  recent `lookback` bars — cheap enough to run every 5 minutes.
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

from config import LOOKBACK_BARS, MODEL_DIR

FEATURE_COLS = [
    "Close", "EMA_9", "EMA_21", "RSI", "ATR",
    "MACD", "MACD_hist",
    "BB_width", "VWAP", "Volume_ratio",
]


def _safe_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", symbol.upper())


def _paths(symbol: str) -> Tuple[str, str]:
    name = _safe_name(symbol)
    os.makedirs(MODEL_DIR, exist_ok=True)
    return (
        os.path.join(MODEL_DIR, f"asx_model_{name}.keras"),
        os.path.join(MODEL_DIR, f"asx_scaler_{name}.pkl"),
    )


def _build_model(n_features: int, lookback: int):
    from keras.layers import LSTM, Dense, Dropout
    from keras.models import Sequential

    model = Sequential([
        LSTM(128, return_sequences=True, input_shape=(lookback, n_features)),
        Dropout(0.2),
        LSTM(64, return_sequences=False),
        Dropout(0.2),
        Dense(32, activation="relu"),
        Dense(1),
    ])
    model.compile(optimizer="adam", loss="huber")   # Huber loss — less sensitive to outliers
    return model


class ASXModel:
    """
    LSTM wrapper with:
    - bulk training on historical data
    - incremental fine-tuning per bar (online learning)
    - automatic save/load to disk
    """

    def __init__(self, symbol: str):
        self.symbol       = symbol
        self.model_path, self.scaler_path = _paths(symbol)
        self._model       = None
        self._scaler: Optional[MinMaxScaler] = None
        self._feature_cols = FEATURE_COLS.copy()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _available_features(self, df: pd.DataFrame) -> list[str]:
        return [c for c in self._feature_cols if c in df.columns]

    def _prepare_sequences(
        self, df: pd.DataFrame, lookback: int
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Scale data and build (X, y) sequence arrays."""
        cols = self._available_features(df)
        data = df[cols].values.astype(float)

        if self._scaler is None:
            self._scaler = MinMaxScaler()
            scaled = self._scaler.fit_transform(data)
        else:
            scaled = self._scaler.transform(data)

        close_idx = cols.index("Close") if "Close" in cols else 0

        X, y = [], []
        for i in range(lookback, len(scaled)):
            X.append(scaled[i - lookback:i])
            y.append(scaled[i, close_idx])   # predict next close (scaled)

        return np.array(X), np.array(y)

    # ── Public API ────────────────────────────────────────────────────────────

    def is_trained(self) -> bool:
        return self._model is not None

    def train(self, df: pd.DataFrame, epochs: int = 30) -> None:
        """Full (re)train on a historical DataFrame."""
        cols = self._available_features(df)
        if len(cols) < 2:
            raise ValueError(f"[ASXModel:{self.symbol}] Too few feature columns: {df.columns.tolist()}")
        self._feature_cols = cols
        self._scaler = None   # reset scaler for fresh fit

        X, y = self._prepare_sequences(df, LOOKBACK_BARS)
        if len(X) == 0:
            raise ValueError(f"[ASXModel:{self.symbol}] Not enough bars after sequencing ({len(df)} bars)")

        self._model = _build_model(len(cols), LOOKBACK_BARS)
        self._model.fit(X, y, epochs=epochs, batch_size=32, verbose=0)
        self.save()
        print(f"  [Model:{self.symbol}] Trained on {len(X)} sequences ({len(cols)} features)")

    def update(self, df: pd.DataFrame) -> None:
        """
        Incremental fine-tune on the most recent window.
        Called after every new bar — fast 1-epoch update.
        """
        if self._model is None or self._scaler is None:
            return
        cols = self._available_features(df)
        if len(df) < LOOKBACK_BARS + 1:
            return

        # Only use the most recent 2*lookback bars to keep it cheap
        window = df.iloc[-(LOOKBACK_BARS * 2):]
        data   = window[cols].values.astype(float)

        try:
            scaled = self._scaler.transform(data)
        except Exception:
            return  # scaler mismatch — skip until next full retrain

        close_idx = cols.index("Close") if "Close" in cols else 0
        X = scaled[:-1].reshape(1, -1, len(cols))[:, -LOOKBACK_BARS:, :]
        y = np.array([scaled[-1, close_idx]])

        if X.shape[1] == LOOKBACK_BARS:
            self._model.fit(X, y, epochs=1, verbose=0)

    def predict_next_close(self, df: pd.DataFrame) -> Optional[float]:
        """
        Predict the next bar's close price (in original AUD scale).
        Returns None if model not ready.
        """
        if self._model is None or self._scaler is None:
            return None

        cols = self._available_features(df)
        if len(df) < LOOKBACK_BARS:
            return None

        window = df.iloc[-LOOKBACK_BARS:][cols].values.astype(float)
        try:
            scaled = self._scaler.transform(window)
        except Exception:
            return None

        X = scaled.reshape(1, LOOKBACK_BARS, len(cols))
        pred_scaled = float(self._model.predict(X, verbose=0)[0, 0])

        # Inverse-transform: reconstruct a dummy full row to reverse scaling
        close_idx = cols.index("Close") if "Close" in cols else 0
        dummy = scaled[-1].copy()
        dummy[close_idx] = pred_scaled
        dummy_2d = dummy.reshape(1, -1)
        restored = self._scaler.inverse_transform(dummy_2d)
        return float(restored[0, close_idx])

    def save(self) -> None:
        if self._model is not None:
            self._model.save(self.model_path)
        if self._scaler is not None:
            joblib.dump(self._scaler, self.scaler_path)

    def load(self) -> bool:
        """Load model and scaler from disk. Returns True on success."""
        try:
            from keras.models import load_model as keras_load
            if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
                self._model  = keras_load(self.model_path)
                self._scaler = joblib.load(self.scaler_path)
                return True
        except Exception as e:
            print(f"  [Model:{self.symbol}] Load failed: {e}")
        return False
