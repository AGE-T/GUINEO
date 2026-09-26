"""
SpeechStudio Benchmark Dialog.

UI Specification, section 22:
    Compare: Temperature, Top P, Top K, Emotion, Prosody, Voice.
    Displays: Generation Time, Output Duration, Realtime Factor.
"""

from __future__ import annotations
from typing import Optional, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QDoubleSpinBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QProgressBar, QGroupBox,
)

from ui.theme import Palette
from engine.models import GenerationParameters, GenerationResult


class BenchmarkDialog(QDialog):
    """Benchmark window for comparing generation performance.

    Runs multiple generations with different parameters and displays
    the results in a comparison table.
    """

    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self.setWindowTitle("Benchmark")
        self.resize(800, 600)
        self.setMinimumSize(600, 400)
        self._results: List[dict] = []
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # --- Test configuration ---
        config_group = QGroupBox("Test Configuration")
        config_layout = QGridLayout(config_group)

        # Prompt text
        config_layout.addWidget(QLabel("Test Prompt:"), 0, 0)
        self._prompt = QLineEdit("The quick brown fox jumps over the lazy dog.")
        config_layout.addWidget(self._prompt, 0, 1, 1, 3)

        # Parameter ranges to compare
        config_layout.addWidget(QLabel("Compare:"), 1, 0)
        self._compare_param = QComboBox()
        self._compare_param.addItems([
            "Temperature", "Top P", "Top K", "Max Tokens",
        ])
        config_layout.addWidget(self._compare_param, 1, 1)

        config_layout.addWidget(QLabel("Values:"), 2, 0)
        self._values = QLineEdit("0.5, 0.7, 1.0, 1.5")
        config_layout.addWidget(self._values, 2, 1, 1, 3)

        # Base parameters
        config_layout.addWidget(QLabel("Base Temperature:"), 3, 0)
        self._base_temp = QDoubleSpinBox()
        self._base_temp.setRange(0.1, 2.0)
        self._base_temp.setValue(1.0)
        config_layout.addWidget(self._base_temp, 3, 1)

        config_layout.addWidget(QLabel("Base Top P:"), 3, 2)
        self._base_top_p = QDoubleSpinBox()
        self._base_top_p.setRange(0.1, 1.0)
        self._base_top_p.setValue(0.95)
        config_layout.addWidget(self._base_top_p, 3, 3)

        config_layout.addWidget(QLabel("Base Top K:"), 4, 0)
        self._base_top_k = QSpinBox()
        self._base_top_k.setRange(1, 500)
        self._base_top_k.setValue(50)
        config_layout.addWidget(self._base_top_k, 4, 1)

        config_layout.addWidget(QLabel("Base Max Tokens:"), 4, 2)
        self._base_max_tokens = QSpinBox()
        self._base_max_tokens.setRange(128, 4096)
        self._base_max_tokens.setValue(2048)
        config_layout.addWidget(self._base_max_tokens, 4, 3)

        layout.addWidget(config_group)

        # --- Run button ---
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("Run Benchmark")
        self._run_btn.setProperty("accent", "true")
        self._run_btn.setToolTip("Run the benchmark with the configured parameters")
        self._run_btn.clicked.connect(self._on_run)
        btn_row.addWidget(self._run_btn)

        self._clear_btn = QPushButton("Clear Results")
        self._clear_btn.clicked.connect(self._on_clear)
        btn_row.addWidget(self._clear_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # --- Progress ---
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # --- Results table ---
        results_group = QGroupBox("Results")
        results_layout = QVBoxLayout(results_group)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "#", "Parameter Value", "Generation Time (s)",
            "Output Duration (s)", "RTF", "Status"
        ])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        results_layout.addWidget(self._table)

        layout.addWidget(results_group, 1)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_run(self) -> None:
        """Run the benchmark."""
        if self._engine is None:
            return
        if not self._engine.is_model_loaded:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Benchmark",
                                "The model must be loaded to run the benchmark.")
            return

        prompt = self._prompt.text().strip()
        if not prompt:
            return

        # Parse comparison values
        values_text = self._values.text().strip()
        try:
            values = [float(v.strip()) for v in values_text.split(",")]
        except ValueError:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Benchmark",
                                "Invalid values format. Use comma-separated numbers.")
            return

        param_name = self._compare_param.currentText()

        # Configure progress
        self._progress.setRange(0, len(values))
        self._progress.setValue(0)
        self._progress.setVisible(True)
        self._run_btn.setEnabled(False)

        # Run each benchmark
        for i, value in enumerate(values):
            params = GenerationParameters(
                temperature=float(self._base_temp.value()),
                top_p=float(self._base_top_p.value()),
                top_k=int(self._base_top_k.value()),
                max_new_tokens=int(self._base_max_tokens.value()),
            )

            # Override the compared parameter
            if param_name == "Temperature":
                params.temperature = value
            elif param_name == "Top P":
                params.top_p = value
            elif param_name == "Top K":
                params.top_k = int(value)
            elif param_name == "Max Tokens":
                params.max_new_tokens = int(value)

            # Run generation (synchronous for benchmark)
            from engine.models import GenerationRequest
            request = GenerationRequest(text=prompt, parameters=params)

            self._progress.setFormat(
                "Run {0}/{1}: {2}={3} %p%".format(i + 1, len(values), param_name, value))

            try:
                # Use the engine's generation pipeline directly
                result = self._engine._execute_generation(request)
                self._add_result(i + 1, param_name, value, result)
            except Exception as exc:
                self._add_result(i + 1, param_name, value, None, str(exc))

            self._progress.setValue(i + 1)

        self._run_btn.setEnabled(True)
        self._progress.setVisible(False)

    def _add_result(self, num: int, param_name: str, value: float,
                    result: Optional[GenerationResult], error: str = "") -> None:
        """Add a benchmark result to the table."""
        row = self._table.rowCount()
        self._table.insertRow(row)

        self._table.setItem(row, 0, QTableWidgetItem(str(num)))
        self._table.setItem(row, 1, QTableWidgetItem("{0}={1}".format(param_name, value)))

        if result and result.success:
            self._table.setItem(row, 2, QTableWidgetItem("{0:.2f}".format(result.generation_time)))
            self._table.setItem(row, 3, QTableWidgetItem("{0:.2f}".format(result.output_duration)))
            self._table.setItem(row, 4, QTableWidgetItem("{0:.2f}".format(result.realtime_factor)))
            self._table.setItem(row, 5, QTableWidgetItem("OK"))
        else:
            self._table.setItem(row, 2, QTableWidgetItem("--"))
            self._table.setItem(row, 3, QTableWidgetItem("--"))
            self._table.setItem(row, 4, QTableWidgetItem("--"))
            self._table.setItem(row, 5, QTableWidgetItem("Failed: {0}".format(error[:50])))

    def _on_clear(self) -> None:
        """Clear all results."""
        self._table.setRowCount(0)
        self._results.clear()
