"""
ml_classifier_service.py
-------------------------
Service for SAR (Synthetic Aperture Radar) satellite image target classification.
Uses a PyTorch Custom CNN trained on the Statoil/CNOOC SAR Iceberg vs. Vessel dataset.

Bands:
- band_1: HH polarization radar backscatter float values (75x75)
- band_2: HV polarization radar backscatter float values (75x75)
- band_3: composite (band_1 + band_2) / 2.0
"""

import os
import json
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("app.services.ml_classifier_service")

# Global PyTorch model state
MODEL = None
TRAIN_MEAN = None
TRAIN_STD = None
DEVICE = None
IS_INITIALIZED = False

# Pre-computed fallback mean & std constants from Kaggle Statoil dataset split
# Shape: (1, 1, 1, 3) for [band_1, band_2, band_3]
DEFAULT_MEAN = [-22.502, -26.974, -24.738]
DEFAULT_STD = [5.602, 5.097, 4.986]


def _get_custom_cnn_class():
    import torch
    import torch.nn as nn

    class CustomCNN(nn.Module):
        def __init__(self):
            super(CustomCNN, self).__init__()
            self.conv1 = nn.Conv2d(3, 64, kernel_size=3, padding=1)
            self.bn1 = nn.BatchNorm2d(64)
            self.pool1 = nn.MaxPool2d(2, 2)

            self.conv2 = nn.Conv2d(64, 128, kernel_size=3, padding=1)
            self.bn2 = nn.BatchNorm2d(128)
            self.pool2 = nn.MaxPool2d(2, 2)

            self.conv3 = nn.Conv2d(128, 128, kernel_size=3, padding=1)
            self.bn3 = nn.BatchNorm2d(128)

            self.gap = nn.AdaptiveAvgPool2d(1)
            self.fc1 = nn.Linear(128, 128)
            self.dropout = nn.Dropout(0.3)
            self.fc2 = nn.Linear(128, 1)

        def forward(self, x):
            x = self.pool1(torch.relu(self.bn1(self.conv1(x))))
            x = self.pool2(torch.relu(self.bn2(self.conv2(x))))
            x = torch.relu(self.bn3(self.conv3(x)))
            x = self.gap(x).view(x.size(0), -1)
            x = torch.relu(self.fc1(x))
            x = self.dropout(x)
            x = torch.sigmoid(self.fc2(x))
            return x

    return CustomCNN


def init_sar_classifier() -> bool:
    """
    Loads PyTorch CustomCNN weights and normalization stats into memory on startup.
    Returns True if initialized successfully, False otherwise.
    """
    global MODEL, TRAIN_MEAN, TRAIN_STD, DEVICE, IS_INITIALIZED

    if IS_INITIALIZED and MODEL is not None:
        return True

    try:
        import torch
        import numpy as np
    except ImportError as exc:
        logger.warning("PyTorch/NumPy not available in environment: %s", exc)
        return False

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    weights_path = os.path.join(base_dir, "ml", "weights", "best_custom_cnn.pth")
    artifacts_dir = os.path.join(base_dir, "ml", "artifacts")
    norm_path = os.path.join(artifacts_dir, "normalization.npz")

    # Determine PyTorch device
    DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("Initializing SAR Iceberg Classifier on device: %s", DEVICE)

    # 1. Load Normalization Statistics
    if os.path.exists(norm_path):
        try:
            data = np.load(norm_path)
            TRAIN_MEAN = data["mean"].reshape(1, 1, 3)
            TRAIN_STD = data["std"].reshape(1, 1, 3)
            logger.info("Loaded pre-computed normalization parameters from %s", norm_path)
        except Exception as err:
            logger.warning("Could not read normalization.npz (%s), using fallback constants", err)
            TRAIN_MEAN = np.array(DEFAULT_MEAN, dtype=np.float32).reshape(1, 1, 3)
            TRAIN_STD = np.array(DEFAULT_STD, dtype=np.float32).reshape(1, 1, 3)
    else:
        TRAIN_MEAN = np.array(DEFAULT_MEAN, dtype=np.float32).reshape(1, 1, 3)
        TRAIN_STD = np.array(DEFAULT_STD, dtype=np.float32).reshape(1, 1, 3)


    # 2. Load PyTorch Model Weights
    if not os.path.exists(weights_path):
        logger.error("Missing model checkpoint at %s", weights_path)
        return False

    try:
        CustomCNN = _get_custom_cnn_class()
        MODEL = CustomCNN()
        MODEL.load_state_dict(torch.load(weights_path, map_location=DEVICE))
        MODEL.to(DEVICE)
        MODEL.eval()
        IS_INITIALIZED = True
        logger.info("SAR CustomCNN model loaded successfully into RAM and set to eval mode.")
        return True
    except Exception as exc:
        logger.error("Failed to load CustomCNN model: %s", exc)
        return False


def classify_sar_target(band_1: List[float], band_2: List[float], sample_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes real-time SAR radar image classification.
    Inputs:
    - band_1: 5625 float values (75x75 matrix)
    - band_2: 5625 float values (75x75 matrix)
    Returns:
    - Dict with prediction (0=Vessel, 1=Iceberg), confidence, probability, class_label
    """
    global MODEL, TRAIN_MEAN, TRAIN_STD, DEVICE, IS_INITIALIZED

    if not IS_INITIALIZED or MODEL is None:
        success = init_sar_classifier()
        if not success:
            raise RuntimeError("SAR Classifier model is not initialized or PyTorch is unavailable.")

    import torch
    import numpy as np

    if len(band_1) != 5625 or len(band_2) != 5625:
        raise ValueError(f"Invalid SAR band dimensions: expected 5625 values (75x75), got {len(band_1)} & {len(band_2)}")

    b1 = np.array(band_1, dtype=np.float32).reshape(75, 75)
    b2 = np.array(band_2, dtype=np.float32).reshape(75, 75)
    b3 = (b1 + b2) / 2.0

    X = np.stack([b1, b2, b3], axis=-1)  # shape: (75, 75, 3)
    X_norm = (X - TRAIN_MEAN) / (TRAIN_STD + 1e-6)  # shape: (75, 75, 3)

    # Transpose to PyTorch layout: (3, 75, 75)
    X_transposed = np.transpose(X_norm, (2, 0, 1))

    # Add batch dimension: (1, 3, 75, 75)
    input_tensor = torch.tensor(X_transposed, dtype=torch.float32).unsqueeze(0).to(DEVICE)

    with torch.no_grad():
        output = MODEL(input_tensor)
        probability = float(output.cpu().numpy().flatten()[0])

    prediction = 1 if probability >= 0.5 else 0
    confidence = probability if prediction == 1 else (1.0 - probability)

    return {
        "status": "success",
        "sample_id": sample_id or "custom_target",
        "prediction": prediction,
        "class_label": "Iceberg" if prediction == 1 else "Vessel",
        "confidence": round(confidence * 100.0, 2),
        "probability": round(probability, 4),
        "model_used": "PyTorch CustomCNN (Statoil SAR Dataset)",
        "is_threat": bool(prediction == 1),
    }
