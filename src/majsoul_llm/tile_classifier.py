"""UI glyph classification using majsoul_wrapper's MIT weights, never strategy."""
from __future__ import annotations

from pathlib import Path
import threading

import cv2
import numpy as np


CLASSES = [f'{n}{s}' for s in 'mps' for n in range(1,10)] + [f'{n}z' for n in range(1,8)] + ['0m','0p','0s','x']


class TileClassifier:
    def __init__(self):
        path = Path(__file__).parent/'assets/majsoul_wrapper/tile.onnx'
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.lock = threading.Lock()

    def predict(self, images):
        if not images:
            return []
        data = np.stack([cv2.cvtColor(cv2.resize(im,(32,32),interpolation=cv2.INTER_LINEAR),cv2.COLOR_BGR2RGB)
                         for im in images]).astype(np.float32)
        data = np.ascontiguousarray(((data/255-.5)/.5).transpose(0,3,1,2))
        with self.lock:
            self.net.setInput(data)
            logits = self.net.forward()
        probabilities = np.exp(logits-logits.max(axis=1,keepdims=True))
        probabilities /= probabilities.sum(axis=1,keepdims=True)
        return [(CLASSES[int(row.argmax())],float(row.max())) for row in probabilities]
