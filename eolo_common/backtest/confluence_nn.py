"""
confluence_nn.py — Red Neuronal LSTM para Confluencia Multi-TF

LSTM que entrena sobre:
  - 28 features multi-TF (input)
  - Ganador/Perdedor (output binario)

Output: Confluency score 0-1 (probabilidad de ganador)

Requiere: tensorflow/keras o alternativa ligera (usando numpy para PoC)
"""

import numpy as np
import json
from typing import Tuple, Dict, Any
from pathlib import Path


class SimpleNNConfluence:
    """
    Red neuronal simple (3 capas) para confluencia.
    PoC sin dependencias pesadas. En producción usar TensorFlow.
    """

    def __init__(self, input_size: int = 28, hidden_sizes: list = None):
        """
        Args:
            input_size: Número de features (28)
            hidden_sizes: Tamaño de capas ocultas [128, 64, 32]
        """
        if hidden_sizes is None:
            hidden_sizes = [128, 64, 32]

        self.input_size = input_size
        self.hidden_sizes = hidden_sizes
        self.weights = {}
        self.biases = {}
        self.is_trained = False

        # Inicializar pesos con He initialization
        self._initialize_weights()

    def _initialize_weights(self):
        """Inicializa pesos usando He initialization."""
        prev_size = self.input_size

        for i, hidden_size in enumerate(self.hidden_sizes):
            # Xavier/He initialization
            std = np.sqrt(2.0 / prev_size)
            self.weights[f"w_{i}"] = np.random.normal(0, std, (prev_size, hidden_size))
            self.biases[f"b_{i}"] = np.zeros((1, hidden_size))
            prev_size = hidden_size

        # Output layer (1 neurona para binary classification)
        std = np.sqrt(2.0 / prev_size)
        self.weights["w_out"] = np.random.normal(0, std, (prev_size, 1))
        self.biases["b_out"] = np.zeros((1, 1))

    @staticmethod
    def _relu(x: np.ndarray) -> np.ndarray:
        """ReLU activation."""
        return np.maximum(0, x)

    @staticmethod
    def _relu_derivative(x: np.ndarray) -> np.ndarray:
        """ReLU derivative."""
        return (x > 0).astype(float)

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        """Sigmoid activation."""
        return 1 / (1 + np.exp(-np.clip(x, -500, 500)))

    @staticmethod
    def _sigmoid_derivative(x: np.ndarray) -> np.ndarray:
        """Sigmoid derivative."""
        return x * (1 - x)

    def forward(self, X: np.ndarray) -> Tuple[np.ndarray, Dict]:
        """
        Forward pass.

        Args:
            X: (N, 28) features

        Returns:
            (predictions: (N, 1), cache: activations para backprop)
        """
        cache = {"activations": [X]}
        A = X

        # Hidden layers con ReLU
        for i in range(len(self.hidden_sizes)):
            Z = np.dot(A, self.weights[f"w_{i}"]) + self.biases[f"b_{i}"]
            A = self._relu(Z)
            cache["activations"].append(A)

        # Output layer con Sigmoid
        Z_out = np.dot(A, self.weights["w_out"]) + self.biases["b_out"]
        predictions = self._sigmoid(Z_out)
        cache["z_values"] = Z_out
        cache["z_last_hidden"] = A

        return predictions, cache

    def backward(
        self,
        X: np.ndarray,
        y: np.ndarray,
        cache: Dict,
        learning_rate: float = 0.01
    ):
        """
        Backward pass y actualización de pesos.

        Args:
            X: (N, 28) features
            y: (N, 1) labels binarios
            cache: Activaciones del forward pass
            learning_rate: Tasa de aprendizaje
        """
        m = X.shape[0]

        # Derivative de loss (MSE)
        dA = (cache["predictions"] - y) / m

        # Backprop through output layer
        A_hidden = cache["z_last_hidden"]
        dW_out = np.dot(A_hidden.T, dA) / m
        db_out = np.sum(dA, axis=0, keepdims=True) / m

        dA = np.dot(dA, self.weights["w_out"].T)

        # Backprop through hidden layers
        for i in range(len(self.hidden_sizes) - 1, -1, -1):
            dA = dA * self._relu_derivative(cache["activations"][i + 1])
            A_prev = cache["activations"][i]

            dW = np.dot(A_prev.T, dA) / m
            db = np.sum(dA, axis=0, keepdims=True) / m

            # Update weights
            self.weights[f"w_{i}"] -= learning_rate * dW
            self.biases[f"b_{i}"] -= learning_rate * db

            if i > 0:
                dA = np.dot(dA, self.weights[f"w_{i}"].T)

        # Update output layer
        self.weights["w_out"] -= learning_rate * dW_out
        self.biases["b_out"] -= learning_rate * db_out

    def train(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        epochs: int = 50,
        batch_size: int = 32,
        learning_rate: float = 0.01,
        validation_split: float = 0.2,
        verbose: bool = True
    ) -> Dict[str, list]:
        """
        Entrena la red.

        Args:
            X_train: (N, 28) features
            y_train: (N,) labels binarios
            epochs: Número de épocas
            batch_size: Tamaño de batch
            learning_rate: Tasa de aprendizaje
            validation_split: Fracción de validación
            verbose: Imprimir progreso

        Returns:
            history: {'train_loss': [...], 'val_loss': [...]}
        """
        # Reshape y (N,) -> (N, 1)
        y_train = y_train.reshape(-1, 1).astype(np.float32)

        # Split train/val
        split_idx = int(len(X_train) * (1 - validation_split))
        X_val = X_train[split_idx:]
        y_val = y_train[split_idx:]
        X_train = X_train[:split_idx]
        y_train = y_train[:split_idx]

        history = {"train_loss": [], "val_loss": []}

        for epoch in range(epochs):
            epoch_loss = 0
            num_batches = len(X_train) // batch_size

            for batch in range(num_batches):
                start = batch * batch_size
                end = start + batch_size

                X_batch = X_train[start:end]
                y_batch = y_train[start:end]

                # Forward
                predictions, cache = self.forward(X_batch)
                cache["predictions"] = predictions

                # Loss (MSE)
                batch_loss = np.mean((predictions - y_batch) ** 2)
                epoch_loss += batch_loss

                # Backward
                self.backward(X_batch, y_batch, cache, learning_rate)

            # Validación
            val_predictions, _ = self.forward(X_val)
            val_loss = np.mean((val_predictions - y_val) ** 2)

            history["train_loss"].append(epoch_loss / num_batches)
            history["val_loss"].append(val_loss)

            if verbose and (epoch + 1) % 10 == 0:
                print(f"Epoch {epoch + 1}/{epochs} — "
                      f"Train Loss: {history['train_loss'][-1]:.4f}, "
                      f"Val Loss: {history['val_loss'][-1]:.4f}")

        self.is_trained = True
        return history

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Predice confluency scores.

        Args:
            X: (N, 28) features

        Returns:
            predictions: (N, 1) scores 0-1
        """
        if not self.is_trained:
            raise ValueError("NN no está entrenada. Llama a .train() primero")

        predictions, _ = self.forward(X)
        return predictions

    def predict_class(self, X: np.ndarray, threshold: float = 0.5) -> np.ndarray:
        """
        Predice clases binarias (0 o 1).

        Args:
            X: (N, 28) features
            threshold: Umbral de decisión

        Returns:
            classes: (N,) 0 o 1
        """
        predictions = self.predict(X)
        return (predictions >= threshold).astype(int).flatten()

    def save(self, filepath: Path):
        """Guarda pesos a JSON."""
        weights_dict = {}

        for key, val in self.weights.items():
            weights_dict[key] = val.tolist()

        for key, val in self.biases.items():
            weights_dict[key] = val.tolist()

        config = {
            "input_size": self.input_size,
            "hidden_sizes": self.hidden_sizes,
            "weights": weights_dict,
            "is_trained": self.is_trained
        }

        with open(filepath, 'w') as f:
            json.dump(config, f)

    @classmethod
    def load(cls, filepath: Path) -> "SimpleNNConfluence":
        """Carga pesos de JSON."""
        with open(filepath) as f:
            config = json.load(f)

        nn = cls(
            input_size=config["input_size"],
            hidden_sizes=config["hidden_sizes"]
        )

        for key, val in config["weights"].items():
            if key.startswith("w_"):
                nn.weights[key] = np.array(val)
            elif key.startswith("b_"):
                nn.biases[key] = np.array(val)

        nn.is_trained = config["is_trained"]
        return nn


if __name__ == "__main__":
    print("\n" + "="*80)
    print("🧠 TEST: Neural Network Confluency")
    print("="*80)

    # Crear dummy data
    X_train = np.random.randn(1000, 28)
    y_train = np.random.randint(0, 2, 1000)

    nn = SimpleNNConfluence(input_size=28, hidden_sizes=[128, 64, 32])

    print("\n📚 Entrenando NN (50 épocas)...")
    history = nn.train(X_train, y_train, epochs=50, verbose=True)

    print("\n✅ NN Entrenada!")
    print(f"✅ Final Train Loss: {history['train_loss'][-1]:.4f}")
    print(f"✅ Final Val Loss: {history['val_loss'][-1]:.4f}")

    # Test prediction
    X_test = np.random.randn(10, 28)
    predictions = nn.predict(X_test)
    classes = nn.predict_class(X_test)

    print(f"\n📊 Predicciones (primeras 5):")
    print(f"  Scores: {predictions[:5].flatten()}")
    print(f"  Clases: {classes[:5]}")
