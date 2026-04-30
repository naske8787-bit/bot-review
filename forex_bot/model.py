"""
LSTM model for forex price prediction with online (incremental) learning.

The model is trained on historical bars and then continuously updated
after each new bar — so it keeps learning from live market behaviour.
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

# Feature columns used for training (must match data_fetcher output)
FEATURE_COLS = ["Close", "EMA_9", "EMA_21", "RSI", "ATR", "MACD", "MACD_hist"]


def _safe_name(pair: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", pair.upper())


def _paths(pair: str) -> Tuple[str, str]:
    name = _safe_name(pair)
    os.makedirs(MODEL_DIR, exist_ok=True)
    return (
        os.path.join(MODEL_DIR, f"forex_model_{name}.h5"),
        os.path.join(MODEL_DIR, f"forex_scaler_{name}.pkl"),
    )


# ── Lazy Keras import so the module loads even without TensorFlow ────────────

def _build_model(n_features: int, lookback: int):
    from keras.layers import LSTM, Dense, Dropout
    from keras.models import Sequential

    model = Sequential([
        LSTM(64, return_sequences=True, input_shape=(lookback, n_features)),
        Dropout(0.2),
        LSTM(64, return_sequences=False),
        Dropout(0.2),
        Dense(32, activation="relu"),
        Dense(1),
    ])
    model.compile(optimizer="adam", loss="mean_squared_error")
    return model


# ── Public API ────────────────────────────────────────────────────────────────

class ForexModel:
    """
    Wraps an LSTM with:
    - initial bulk training from historical bars
    - incremental online fine-tuning after each new bar
    - automatic save/load from disk
    """

    def __init__(self, pair: str):
        self.pair = pair
        self.model_path, self.scaler_path = _paths(pair)
        self._model  = None
        self._scaler: Optional[MinMaxScaler] = None
        self._bar_count = 0          # bars seen since last retrain
        self._feature_cols = FEATURE_COLS

    # ── Training ─────────────────────────────────────────────────────────────

    def train(self, df: pd.DataFrame, epochs: int = 30) -> None:
        """Full (re)train on a DataFrame of bars with indicator columns."""
        available = [c for c in self._feature_cols if c in df.columns]
        if len(available) < 2:
            raise ValueError(f"[ForexModel] Not enough feature columns in df: {df.columns.tolist()}")
        self._feature_cols = available

        data = df[self._feature_cols].values.astype(np.float32)

        scaler = MinMaxScaler(feature_range=(0, 1))
        scaled = scaler.fit_transform(data)
        self._scaler = scaler

        X, y = self._make_sequences(scaled)
        if len(X) == 0:
            raise ValueError("[ForexModel] Not enough data to build sequences.")

        self._model = _build_model(X.shape[2], X.shape[1])
        self._model.fit(X, y, epochs=epochs, batch_size=32, verbose=0)
        self._bar_count = 0
        self.save()
        print(f"[ForexModel] {self.pair} trained on {len(df)} bars.")

    def update(self, df: pd.DataFrame) -> None:
        """
        Incremental fine-tune on the most recent bars (online learning).
        Called after each new bar arrives.
        """
        if self._model is None or self._scaler is None:
            return
        available = [c for c in self._feature_cols if c in df.columns]
        data  = df[available].values[-LOOKBACK_BARS - 10:].astype(np.float32)
        scaled = self._scaler.transform(data)
        X, y  = self._make_sequences(scaled)
        if len(X) == 0:
            return
        if getattr(self._model, "optimizer", None) is None:
            # Some legacy model loads may produce an uncompiled model object.
            self._model.compile(optimizer="adam", loss="mean_squared_error")
        try:
            self._model.fit(X, y, epochs=1, batch_size=len(X), verbose=0)
        except Exception as e:
            msg = str(e)
            if "compile()" in msg or "must call `compile()`" in msg.lower():
                self._model.compile(optimizer="adam", loss="mean_squared_error")
                self._model.fit(X, y, epochs=1, batch_size=len(X), verbose=0)
            else:
                raise
        self._bar_count += 1

    # ── Prediction ────────────────────────────────────────────────────────────

    def predict_next_close(self, df: pd.DataFrame) -> Optional[float]:
        """Return the predicted next close price, or None if model not ready."""
        if self._model is None or self._scaler is None:
            return None
        available = [c for c in self._feature_cols if c in df.columns]
        data  = df[available].values[-LOOKBACK_BARS:].astype(np.float32)
        if len(data) < LOOKBACK_BARS:
            return None
        scaled = self._scaler.transform(data)
        X = np.array([scaled], dtype=np.float32)
        pred_scaled = self._model(X, training=False).numpy()
        # Inverse-transform only the Close column (index 0)
        dummy = np.zeros((1, len(available)), dtype=np.float32)
        dummy[0, 0] = pred_scaled[0, 0]
        result = self._scaler.inverse_transform(dummy)
        return float(result[0, 0])

    def is_ready(self) -> bool:
        return self._model is not None and self._scaler is not None

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self) -> None:
        if self._model is not None:
            self._model.save(self.model_path)
        if self._scaler is not None:
            joblib.dump(self._scaler, self.scaler_path)

    def load(self) -> bool:
        """Load saved model from disk. Returns True if successful."""
        try:
            from keras.models import load_model as _load
            if os.path.exists(self.model_path) and os.path.exists(self.scaler_path):
                self._model  = _load(self.model_path, compile=False)
                # Re-compile after load so online fine-tuning via fit() remains available.
                self._model.compile(optimizer="adam", loss="mean_squared_error")
                self._scaler = joblib.load(self.scaler_path)
                print(f"[ForexModel] {self.pair} loaded from disk.")
                return True
        except Exception as e:
            print(f"[ForexModel] Failed to load {self.pair}: {e}")
        return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _make_sequences(self, scaled: np.ndarray):
        X, y = [], []
        for i in range(LOOKBACK_BARS, len(scaled)):
            X.append(scaled[i - LOOKBACK_BARS:i])
            y.append(scaled[i, 0])          # predict Close (column 0)
        return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)
