import os
import re

import joblib
import numpy as np
from keras.layers import LSTM, Dense, Dropout
from keras.models import Sequential, load_model
from sklearn.preprocessing import MinMaxScaler

from config import MODEL_PATH

SCALER_PATH = os.path.join(os.path.dirname(MODEL_PATH), "scaler.pkl")


def _artifact_suffix(symbol=None):
    if not symbol:
        return ""
    safe_symbol = re.sub(r"[^A-Za-z0-9_-]+", "_", symbol.upper())
    return f"_{safe_symbol}"


def get_artifact_paths(symbol=None):
    """Return the model and scaler paths for a specific symbol."""
    suffix = _artifact_suffix(symbol)
    model_dir = os.path.dirname(MODEL_PATH)
    model_path = os.path.join(model_dir, f"trading_model{suffix}.h5") if suffix else MODEL_PATH
    scaler_path = os.path.join(model_dir, f"scaler{suffix}.pkl") if suffix else SCALER_PATH
    return model_path, scaler_path


def create_model(input_shape):
    """Create LSTM model for price prediction."""
    model = Sequential()
    model.add(LSTM(50, return_sequences=True, input_shape=input_shape))
    model.add(Dropout(0.2))
    model.add(LSTM(50, return_sequences=False))
    model.add(Dropout(0.2))
    model.add(Dense(1))
    model.compile(optimizer="adam", loss="mean_squared_error")
    return model


def train_model(data, epochs=50, symbol=None):
    """Train the model on historical data and save symbol-specific artifacts."""
    scaler = MinMaxScaler(feature_range=(0, 1))
    scaled_data = scaler.fit_transform(data["Close"].values.reshape(-1, 1))

    x_train, y_train = [], []
    for i in range(60, len(scaled_data)):
        x_train.append(scaled_data[i - 60:i, 0])
        y_train.append(scaled_data[i, 0])

    x_train, y_train = np.array(x_train), np.array(y_train)
    x_train = np.reshape(x_train, (x_train.shape[0], x_train.shape[1], 1))

    model = create_model((x_train.shape[1], 1))
    model.fit(x_train, y_train, epochs=epochs, batch_size=32)

    model_path, scaler_path = get_artifact_paths(symbol)
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    model.save(model_path)
    joblib.dump(scaler, scaler_path)
    return model, scaler


def load_trained_model(symbol=None):
    """Load pre-trained model artifacts, preferring a symbol-specific model when available."""
    candidate_paths = [get_artifact_paths(symbol)]
    if symbol:
        candidate_paths.append(get_artifact_paths())

    for model_path, scaler_path in candidate_paths:
        if os.path.exists(model_path) and os.path.exists(scaler_path):
            model = load_model(model_path, compile=False)
            scaler = joblib.load(scaler_path)
            return model, scaler

    requested = f" for {symbol}" if symbol else ""
    raise FileNotFoundError(
        f"Model artifacts{requested} not found. Run `python train.py` from `trading_bot/` first."
    )


def predict_price(model, scaler, recent_data):
    """Predict the next price with minimal TensorFlow overhead."""
    recent_array = np.asarray(recent_data, dtype=np.float32).reshape(-1, 1)
    scaled_data = scaler.transform(recent_array)
    x_input = np.array([scaled_data[-60:]], dtype=np.float32)
    x_input = np.reshape(x_input, (x_input.shape[0], x_input.shape[1], 1))
    prediction = model(x_input, training=False).numpy()
    return float(scaler.inverse_transform(prediction)[0][0])