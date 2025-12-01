import sys
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QMessageBox,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTextEdit, QComboBox
)
from PyQt5.QtCore import Qt

import pyPowerGEM.pyTARA as pt

# ------------------------------------------------------------
# RAW VERSION – adjust if you use a different RAW format
# ------------------------------------------------------------
RAW_VERSION = 35


# ------------------------------------------------------------
# DATA STRUCTURES
# ------------------------------------------------------------
@dataclass
class LoadInfo:
    bus: int
    load_id: str
    p: float
    q: float
    status: int


# ------------------------------------------------------------
# Helper: get ALL loads at a bus using official TARA API
# ------------------------------------------------------------
def get_loads_at_bus(tara, bus_num: int) -> List[LoadInfo]:
    """
    Use tara.getLoad(busNum=..., loadId=...) to pull all loads at a bus.
    """
    loads_here: List[LoadInfo] = []

    # Typical ID patterns: 1–9, A–Z, 01–99
    candidate_ids = (
        [str(i) for i in range(1, 10)] +
        list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") +
        [f"{i:02d}" for i in range(1, 100)]
    )

    for lid in candidate_ids:
        ld = tara.getLoad(busNum=bus_num, loadId=lid)
        if ld is None:
            continue

        loads_here.append(
            LoadInfo(
                bus=bus_num,
                load_id=str(ld.id),
                p=float(ld.pConstantPower),
                q=float(ld.qConstantPower),
                status=int(ld.status),
            )
        )

    return loads_here


def compare_load_sets(
    base: List[LoadInfo],
    scen: List[LoadInfo]
) -> Tuple[List[Tuple[LoadInfo, LoadInfo]], List[LoadInfo], List[LoadInfo]]:
    """
    Compare two lists of LoadInfo (BASE vs scenario) keyed by (bus, id).

    Returns:
        changed: list of (base_load, scen_load) where P/Q/status differ
        only_base: loads present only in BASE
        only_scen: loads present only in SCEN
    """
    base_dict: Dict[Tuple[int, str], LoadInfo] = {
        (ld.bus, ld.load_id): ld for ld in base
    }
    scen_dict: Dict[Tuple[int, str], LoadInfo] = {
        (ld.bus, ld.load_id): ld for ld in scen
    }

    keys_base = set(base_dict.keys())
    keys_scen = set(scen_dict.keys())

    common_keys = keys_base & keys_scen
    only_base_keys = keys_base - keys_scen
    only_scen_keys = keys_scen - keys_base

    changed: List[Tuple[LoadInfo, LoadInfo]] = []
    for key in common_keys:
        b = base_dict[key]
        s = scen_dict[key]
        if (
            abs(b.p - s.p) > 1e-6
            or abs(b.q - s.q) > 1e-6
            or b.status != s.status
        ):
            changed.append((b, s))

    only_base = [base_dict[k] for k in sorted(only_base_keys)]
    only_scen = [scen_dict[k] for k in sorted(only_scen_keys)]

    return changed, only_base, only_scen


# ------------------------------------------------------------
# MAIN WINDOW
# ------------------------------------------------------------
class TaraLoadCompareWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TARA Load Comparison Tool")
        self.resize(900, 700)

        self.tara = pt.taraAPI()

        self.case_folder: str = ""
        self.available_cases: List[str] = []

        self._build_ui()

    # ---- UI construction ---- #
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # --- Case folder selection row ---
        folder_row = QHBoxLayout()
        folder_label = QLabel("Case Folder:")
        self.folder_edit = QLineEdit()
        browse_btn = QPushButton("Browse…")
        load_cases_btn = QPushButton("Load Cases")

        browse_btn.clicked.connect(self.browse_folder)
        load_cases_btn.clicked.connect(self.populate_case_list)

        folder_row.addWidget(folder_label)
        folder_row.addWidget(self.folder_edit)
        folder_row.addWidget(browse_btn)
        folder_row.addWidget(load_cases_btn)
        main_layout.addLayout(folder_row)

        # --- Available cases list (checkbox list) ---
        main_layout.addWidget(QLabel("Available Cases"))
        self.case_list = QListWidget()
        self.case_list.setSelectionMode(QListWidget.MultiSelection)
        main_layout.addWidget(self.case_list)

        # --- BASE case selection row ---
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("BASE case:"))
        self.base_combo = QComboBox()
        base_row.addWidget(self.base_combo)
        main_layout.addLayout(base_row)

        # --- Run row: bus number + Run button ---
        run_row = QHBoxLayout()
        run_row.addWidget(QLabel("Bus number:"))
        self.bus_edit = QLineEdit()
        self.bus_edit.setPlaceholderText("e.g. 888888")
        run_btn = QPushButton("Run Comparison")
        run_btn.clicked.connect(self.run_comparison)

        run_row.addWidget(self.bus_edit)
        run_row.addWidget(run_btn)
        main_layout.addLayout(run_row)

        # --- Results text box ---
        main_layout.addWidget(QLabel("Results"))
        self.results_text = QTextEdit()
        self.results_text.setReadOnly(True)
        main_layout.addWidget(self.results_text)

    # ---- Event handlers ---- #
    def browse_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Select Case Folder", ""
        )
        if folder:
            self.folder_edit.setText(folder)
            self.case_folder = folder

    def populate_case_list(self):
        folder = self.folder_edit.text().strip()
        if not folder:
            QMessageBox.warning(self, "No Folder", "Please select a folder first.")
            return
        if not os.path.isdir(folder):
            QMessageBox.critical(self, "Invalid Folder", f"Not a directory:\n{folder}")
            return

        self.case_folder = folder
        self.case_list.clear()
        self.base_combo.clear()
        self.available_cases = []

        # Simple filter for .raw files
        for fname in sorted(os.listdir(folder)):
            if fname.lower().endswith(".raw"):
                self.available_cases.append(fname)
                item = QListWidgetItem(fname)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked)  # default: all selected
                self.case_list.addItem(item)
                self.base_combo.addItem(fname)

        if not self.available_cases:
            QMessageBox.information(
                self,
                "No Cases",
                "No .RAW files found in the selected folder."
            )

    def run_comparison(self):
        # Validate bus
        try:
            bus_num = int(self.bus_edit.text().strip())
        except ValueError:
            QMessageBox.warning(
                self,
                "Invalid Bus",
                "Please enter a valid integer bus number."
            )
            return

        # BASE case file name
        base_fname = self.base_combo.currentText()
        if not base_fname:
            QMessageBox.warning(
                self,
                "No BASE Case",
                "Please select a BASE case from the dropdown."
            )
            return
        base_path = os.path.join(self.case_folder, base_fname)

        # Scenario cases = checked items
        scen_files: List[str] = []
        for i in range(self.case_list.count()):
            item = self.case_list.item(i)
            if item.checkState() == Qt.Checked:
                scen_files.append(item.text())

        # Ensure BASE is in the list (for summary and ordering)
        if base_fname not in scen_files:
            scen_files.insert(0, base_fname)

        # UI header
        self.results_text.clear()
        self.append_result_line("============================================================")
        self.append_result_line(" Running: Load Comparison Across Cases")
        self.append_result_line("============================================================")
        self.append_result_line("")

        start_total = time.perf_counter()

        # --------------------------------------------------------
        # Load BASE and get loads (DIRECT library call)
        # --------------------------------------------------------
        self.append_result_line(f"Loading BASE case: {base_fname}")

        try:
            self.tara.loadRawCase(os.path.abspath(base_path), rawVer=RAW_VERSION)
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error Loading BASE",
                f"'taraAPI' error while opening BASE case:\n{e}"
            )
            return

        base_loads = get_loads_at_bus(self.tara, bus_num)
        self.append_result_line(f"Found {len(base_loads)} loads at bus {bus_num} in BASE.\n")

        # --------------------------------------------------------
        # Compare to each scenario
        # --------------------------------------------------------
        case_times: Dict[str, float] = {"BASE": 0.0}
        comparisons = []

        for scen_fname in scen_files:
            if scen_fname == base_fname:
                continue  # skip self-comparison; BASE is reference

            scen_path = os.path.join(self.case_folder, scen_fname)
            self.append_result_line(
                "------------------------------------------------------------"
            )
            self.append_result_line(
                f" BASE vs {scen_fname}   (bus {bus_num})"
            )
            self.append_result_line(
                "------------------------------------------------------------"
            )

            t0 = time.perf_counter()
            try:
                self.tara.loadRawCase(os.path.abspath(scen_path), rawVer=RAW_VERSION)
            except Exception as e:
                self.append_result_line(
                    f"*** ERROR opening scenario case '{scen_fname}': {e}\n"
                )
                continue

            loads_scen = get_loads_at_bus(self.tara, bus_num)
            t1 = time.perf_counter()
            case_times[scen_fname] = t1 - t0

            changed, only_base, only_scen = compare_load_sets(base_loads, loads_scen)

            # Print differences
            if changed:
                self.append_result_line("--- Changed loads ---")
                for b, s in changed:
                    self.append_result_line(
                        f" ID '{b.load_id}': "
                        f"BASE P={b.p:.3f},Q={b.q:.3f},St={b.status}  "
                        f"SCEN P={s.p:.3f},Q={s.q:.3f},St={s.status}"
                    )
                self.append_result_line("")
            if only_base:
                self.append_result_line(f"--- Loads ONLY in BASE (missing in {scen_fname}) ---")
                for ld in only_base:
                    self.append_result_line(
                        f" Bus={ld.bus}, ID='{ld.load_id}', "
                        f"P={ld.p}, Q={ld.q}, Status={ld.status}"
                    )
                self.append_result_line("")
            if only_scen:
                self.append_result_line(f"--- Loads ONLY in {scen_fname} (new loads) ---")
                for ld in only_scen:
                    self.append_result_line(
                        f" SCEN: Bus={ld.bus}, ID='{ld.load_id}', "
                        f"P={ld.p}, Q={ld.q}, Status={ld.status}"
                    )
                self.append_result_line("")

            comparisons.append((scen_fname, changed, only_base, only_scen))

        # --------------------------------------------------------
        # Summary
        # --------------------------------------------------------
        total_time = time.perf_counter() - start_total

        self.append_result_line("")
        self.append_result_line("============================================================")
        self.append_result_line(f" SUMMARY FOR BUS {bus_num}")
        self.append_result_line("============================================================")
        self.append_result_line("")

        for scen_fname, changed, only_base, only_scen in comparisons:
            self.append_result_line(f"{scen_fname}:")
            self.append_result_line(f"  Changed loads        : {len(changed)}")
            self.append_result_line(f"  Only in BASE         : {len(only_base)}")
            self.append_result_line(f"  Only in {scen_fname} : {len(only_scen)}")
            if only_scen:
                self.append_result_line("  New loads:")
                for ld in only_scen:
                    self.append_result_line(
                        f"     Bus={ld.bus}, ID='{ld.load_id}', "
                        f"P={ld.p}, Q={ld.q}, Status={ld.status}"
                    )
            self.append_result_line("")

        self.append_result_line("============================================================")
        self.append_result_line(" CASE LOAD TIMES")
        self.append_result_line("============================================================")
        for name, t in case_times.items():
            self.append_result_line(f"{name:>8} : {t:5.3f} sec")
        self.append_result_line("")
        self.append_result_line(f"Total analysis time: {total_time:5.3f} seconds")
        self.append_result_line("============================================================")
        self.append_result_line("")

    def append_result_line(self, txt: str):
        self.results_text.append(txt)


# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = TaraLoadCompareWindow()
    win.show()
    sys.exit(app.exec_())
