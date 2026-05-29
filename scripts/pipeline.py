#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "textual",
# ]
# ///

import csv
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from time import time

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Footer,
    Header,
    Input,
    RichLog,
    SelectionList,
    Static,
)
from textual.widgets.selection_list import Selection

# ---------------------------------------------------------------------------
# Paths resolved relative to this script
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent.parent.resolve()

DEFAULT_MZMINE = str(
    SCRIPT_DIR / "mzmine_Windows_portable-4.5.20" / "mzmine_console.exe"
)
DEFAULT_SIRIUS = str(SCRIPT_DIR / "sirius-6.2.2-win-x64" / "sirius.exe")
DEFAULT_OUTDIR = str(SCRIPT_DIR.parent / "mzmine__results")

_SCRIPTS = SCRIPT_DIR / "scripts"
FEATURE_RENAME_SCRIPT = _SCRIPTS / "rename_MZMine_results.py"
MGF_FIX_SCRIPT = _SCRIPTS / "fix_SIRIUS_mgfs.py"
DDA_INCLUSION_LIST_SCRIPT = _SCRIPTS / "create_DDA_inclusion_list.py"
COMBINE_EXPERIMENTS_SCRIPT = _SCRIPTS / "combineMZMineWithMEII.py"

# Default paths for optional external tools (all disabled / empty by default)
DEFAULT_UTIL_MGFTOOLS = r"C:\development\util_MGFTools"
DEFAULT_UTIL_FRAGEXTRACT = (
    r"C:\development\util_FragExtract\rust_impl\target\debug\rust_impl.exe"
)
DEFAULT_UTIL_REORDER = r"C:\development\util_ReorderTSVFiles"
DEFAULT_MEII_REF = r"H:\LV_comparison\Data\LV2026\Fullscan\FPS\results.tsv"

# ---------------------------------------------------------------------------
# Task catalogue — loaded from tasks.json next to this script
# ---------------------------------------------------------------------------
_TASKS_FILE = SCRIPT_DIR / "tasks.json"
TASK_PARAMS: dict[str, dict] = json.loads(_TASKS_FILE.read_text(encoding="utf-8"))

# Main processing steps shown at the top level
PROCESSING_STEPS = [
    ("proc_mzmine", "Run MZmine", True),
    ("proc_sirius", "Run SIRIUS", False),
    ("archive_results", "Archive Results (7-Zip)", False),
]

# Pipeline-wide options (not counted toward "at least one step" validation)
PIPELINE_OPTIONS = [
    ("use_subdirectories", "Write each dataset into its own sub-directory", True),
]

# MZmine sub-steps; defaults match active/commented state in the original PS1
MZMINE_SUBSTEPS = [
    ("step_rename_ids", "1.1  Rename feature IDs", True),
    ("step_split_mgf", "1.2  Split MGF files", True),
    ("step_clean_spectra", "1.3  Clean 12C13C spectra", False),
    ("step_combine_meii", "1.4  Combine results with MEII", False),
    ("step_reorder_table", "1.5  Reorder table", False),
    ("step_dda_list", "1.6  Create DDA inclusion list", True),
    ("step_csv_to_tsv", "1.7  Convert CSV \u2192 TSV", True),
]

# ---------------------------------------------------------------------------
# Processing helpers (run in background thread)
# ---------------------------------------------------------------------------


def _run_command(
    cmd: list[str],
    log_fn,
    log_file: Path | None = None,
    cwd: Path | None = None,
    append: bool = False,
) -> int:
    """Run *cmd* and stream every line to *log_fn*.  Optionally tee to a file."""
    log_fn(f"  $ {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
        )
        collected: list[str] = []
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            log_fn(line)
            collected.append(line)
        proc.wait()
        if log_file:
            mode = "a" if append else "w"
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, mode, encoding="utf-8") as fh:
                fh.write("\n".join(collected) + "\n")
        return proc.returncode if proc.returncode is not None else 0
    except FileNotFoundError as exc:
        log_fn(f"  ERROR – command not found: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log_fn(f"  ERROR – {exc}")
        return 1


def _convert_csv_to_tsv(csv_path: Path, tsv_path: Path, log_fn) -> None:
    """Convert a comma-separated CSV to a tab-separated TSV file."""
    try:
        with open(csv_path, newline="", encoding="utf-8") as fin:
            reader = csv.reader(fin)
            with open(tsv_path, "w", newline="", encoding="utf-8") as fout:
                writer = csv.writer(fout, delimiter="\t")
                for row in reader:
                    writer.writerow(row)
        log_fn(f"    {csv_path.name}  →  {tsv_path.name}")
    except Exception as exc:  # noqa: BLE001
        log_fn(f"    WARNING – could not convert {csv_path.name}: {exc}")


def process_task(task: str, config: dict, log_fn) -> bool:
    """Run the full pipeline for a single *task*.  Returns True on success."""
    base_outdir = Path(config["outdir"])
    # When subdirectories are requested each task gets its own folder;
    # otherwise all files land directly in the base output directory.
    outdir = base_outdir / task if config.get("use_subdirectories") else base_outdir
    mzmine = Path(config["mzmine"])
    sirius = Path(config["sirius"])
    params = TASK_PARAMS[task]

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "params_scripts_etc").mkdir(exist_ok=True)
    log_fn(f"  output: {outdir}")

    t0 = time()
    log_fn(f"\n{'=' * 60}")
    log_fn(f"  Task: {task}")
    log_fn(f"{'=' * 60}")

    # ------------------------------------------------------------------
    # Step 1 – MZmine
    # ------------------------------------------------------------------
    if config["proc_mzmine"]:
        mzmine_log = outdir / f"{task}.1_MZmine_log.txt"
        log_fn("\n[Step 1] Running MZmine ...")

        batch_file = SCRIPT_DIR / params["MZmine_batch"]
        input_files_file = (SCRIPT_DIR / params["input_files"]).resolve()

        log_fn(f"  batch : {batch_file}")
        log_fn(f"  inputs: {input_files_file}")

        # Validate required files
        if not batch_file.exists():
            log_fn(f"  ERROR – batch file not found: {batch_file}")
            return False
        if not input_files_file.exists():
            log_fn(f"  ERROR – input files list not found: {input_files_file}")
            return False

        # Copy parameter files to output for reproducibility
        shutil.copy2(str(batch_file), str(outdir / "params_scripts_etc"))
        shutil.copy2(str(input_files_file), str(outdir / "params_scripts_etc"))

        rc = _run_command(
            [
                str(mzmine),
                "-b",
                str(batch_file),
                "-i",
                str(input_files_file),
                "-o",
                str(outdir / f"{task}_"),
            ],
            log_fn,
            mzmine_log,
            cwd=SCRIPT_DIR,
        )
        if rc != 0:
            log_fn(f"  WARNING – MZmine exited with code {rc}")

        # Verify expected output exists
        expected = outdir / f"{task}__full_feature_table.csv"
        if not expected.exists():
            log_fn(
                f"  ERROR – MZmine did not produce expected output: {expected}\n"
                f"  Check log: {mzmine_log}"
            )
            return False

        # Rename project artefacts
        project_dir = outdir / f"{task}_"
        if project_dir.exists():
            dest = outdir / f"{task}__mzmineproject.mzmine"
            if dest.exists():
                shutil.rmtree(dest)
            project_dir.rename(dest)

        annotations_dir = outdir / f"{task}__annotations"
        if annotations_dir.exists():
            annotations_dir.rename(outdir / f"{task}__annotations.csv")

        # --- 1.1 Rename feature IDs ---
        if config.get("step_rename_ids", True):
            log_fn("\n[Step 1.1] Renaming feature IDs ...")
            _run_command(
                [
                    "uv",
                    "run",
                    str(FEATURE_RENAME_SCRIPT),
                    "--full_feature_table",
                    str(outdir / f"{task}__full_feature_table.csv"),
                    "--annotations",
                    str(outdir / f"{task}__annotations.csv"),
                    "--iimn_fbmn_quant",
                    str(outdir / f"{task}__iimn_fbmn_quant.csv"),
                    "--graphml",
                    str(outdir / f"{task}__networks_fbmn.graphml"),
                    "--mgf",
                    str(outdir / f"{task}__iimn_fbmn.mgf"),
                    str(outdir / f"{task}__sirius.mgf"),
                ],
                log_fn,
                mzmine_log,
                cwd=SCRIPT_DIR,
                append=True,
            )
        else:
            log_fn("[Step 1.1] Skipped.")

        # --- 1.2 Split MGF files ---
        if config.get("step_split_mgf", False):
            log_fn("\n[Step 1.2] Splitting MGF files ...")
            mgftools = config.get("util_mgftools_dir", "").strip()
            if mgftools:
                mgftools_main = str(Path(mgftools) / "main.py")
                for mgf_stem in [f"{task}__sirius", f"{task}__iimn_fbmn"]:
                    _run_command(
                        [
                            "uv",
                            "--project",
                            mgftools,
                            "run",
                            mgftools_main,
                            "split",
                            "--input",
                            str(outdir / f"{mgf_stem}.mgf"),
                            "--mslevel",
                        ],
                        log_fn,
                        mzmine_log,
                        cwd=SCRIPT_DIR,
                        append=True,
                    )
            else:
                log_fn("  WARNING \u2013 util_MGFTools path not configured; skipping.")
        else:
            log_fn("[Step 1.2] Skipped.")

        # --- 1.3 Clean 12C13C spectra ---
        if config.get("step_clean_spectra", False):
            log_fn("\n[Step 1.3] Cleaning 12C13C spectra ...")
            fragextract = config.get("util_fragextract_exe", "").strip()
            if fragextract:
                for suffix in ["sirius", "iimn_fbmn"]:
                    mgf_in = outdir / f"{task}__{suffix}__MS2_Pall_FMall_CEall.mgf"
                    folder_out = (
                        outdir
                        / f"{task}__{suffix}__MS2_Pall_FMall_CEall_matchedCleaned"
                    )
                    _run_command(
                        [
                            fragextract,
                            "--input-mgf",
                            str(mgf_in),
                            "--output-folder",
                            str(folder_out),
                        ],
                        log_fn,
                        mzmine_log,
                        cwd=SCRIPT_DIR,
                        append=True,
                    )
            else:
                log_fn(
                    "  WARNING \u2013 util_FragExtract path not configured; skipping."
                )
        else:
            log_fn("[Step 1.3] Skipped.")

        # --- 1.4 Combine results with MEII ---
        if config.get("step_combine_meii", False):
            log_fn("\n[Step 1.4] Combining results with MEII ...")
            meii_ref = config.get("meii_ref_file", "").strip()
            if meii_ref:
                shutil.copy2(
                    str(COMBINE_EXPERIMENTS_SCRIPT), str(outdir / "params_scripts_etc")
                )
                _run_command(
                    [
                        "uv",
                        "run",
                        str(COMBINE_EXPERIMENTS_SCRIPT),
                        "--plot_file",
                        str(outdir / f"{task}_mapped_features.pdf"),
                        "--remove_non_matched",
                        "--polarity",
                        params["polarity"],
                        "--avg_rt_shift",
                        "0.0",
                        "--avg_mz_ppm_shift",
                        "0.0",
                        "--max_rt_difference_min",
                        "0.15",
                        "--max_mz_difference_ppm",
                        "5.0",
                        "--reference_file",
                        meii_ref,
                        "--query_file",
                        str(outdir / f"{task}__full_feature_table.csv"),
                        "--additional_query_file",
                        str(outdir / f"{task}__iimn_fbmn.mgf"),
                        "--additional_query_file",
                        str(outdir / f"{task}__sirius.mgf"),
                    ],
                    log_fn,
                    mzmine_log,
                    cwd=SCRIPT_DIR,
                    append=True,
                )
            else:
                log_fn("  WARNING \u2013 MEII reference file not configured; skipping.")
        else:
            log_fn("[Step 1.4] Skipped.")

        # --- 1.5 Reorder table ---
        if config.get("step_reorder_table", False):
            log_fn("\n[Step 1.5] Reordering table ...")
            reorder_dir = config.get("util_reorder_dir", "").strip()
            if reorder_dir:
                _run_command(
                    [
                        "uv",
                        "run",
                        "--project",
                        reorder_dir,
                        str(Path(reorder_dir) / "main.py"),
                        str(outdir / f"{task}__full_feature_table_combined.tsv"),
                        "--output_file",
                        str(
                            outdir
                            / f"{task}__full_feature_table_combined_reordered.tsv"
                        ),
                        "--sort_regexes",
                        "id.*",
                        "^mz$",
                        "^rt$",
                        "datafile:.*:area",
                        "--not_include_other_columns",
                    ],
                    log_fn,
                    mzmine_log,
                    cwd=SCRIPT_DIR,
                    append=True,
                )
            else:
                log_fn(
                    "  WARNING \u2013 util_ReorderTSVFiles path not configured; skipping."
                )
        else:
            log_fn("[Step 1.5] Skipped.")

        # --- 1.6 DDA inclusion list ---
        if config.get("step_dda_list", True):
            log_fn("\n[Step 1.6] Creating DDA inclusion list ...")
            _run_command(
                [
                    "uv",
                    "run",
                    str(DDA_INCLUSION_LIST_SCRIPT),
                    "--input_file",
                    str(outdir / f"{task}__full_feature_table.csv"),
                    "--output_file",
                    str(outdir / f"{task}__DDA_inclusion_list_IQX.csv"),
                    "--rt_window",
                    "0.1",
                ],
                log_fn,
                mzmine_log,
                cwd=SCRIPT_DIR,
                append=True,
            )
        else:
            log_fn("[Step 1.6] Skipped.")

        # --- 1.7 CSV → TSV ---
        if config.get("step_csv_to_tsv", True):
            log_fn("\n[Step 1.7] Converting CSV files to TSV ...")
            for base in [
                f"{task}__full_feature_table",
                f"{task}__full_feature_table2",
                f"{task}__iimn_fbmn_quant",
                f"{task}__annotations",
                f"{task}__iimn_fbmn_edges_msannotation",
            ]:
                csv_p = outdir / f"{base}.csv"
                if csv_p.exists():
                    _convert_csv_to_tsv(csv_p, outdir / f"{base}.tsv", log_fn)
                else:
                    log_fn(f"    (skipping {csv_p.name} \u2013 not found)")
        else:
            log_fn("[Step 1.7] Skipped.")
    else:
        log_fn("[Step 1] MZmine skipped.")

    # ------------------------------------------------------------------
    # Step 2 – SIRIUS
    # ------------------------------------------------------------------
    if config["proc_sirius"]:
        sirius_log = outdir / f"{task}.2_SIRIUS_log.txt"
        log_fn("\n[Step 2] Running SIRIUS ...")

        # 2.1 Fix MGF
        log_fn("\n[Step 2.1] Fixing MGF file ...")
        shutil.copy2(str(MGF_FIX_SCRIPT), str(outdir / "params_scripts_etc"))
        _run_command(
            [
                "uv",
                "run",
                str(MGF_FIX_SCRIPT),
                "--mgf_file",
                str(outdir / f"{task}__sirius.mgf"),
            ],
            log_fn,
            sirius_log,
            cwd=SCRIPT_DIR,
        )

        # 2.2 Run SIRIUS
        log_fn("\n[Step 2.2] Predicting fingerprints ...")
        if params["polarity"] == "positive":
            fallback_ions = "--AdductSettings.fallback=[[M+H]+,[M+Na]+,[M+K]+,[M-H]-]"
        else:
            fallback_ions = "--AdductSettings.fallback=[[M-H]-]"
        log_fn(f"  fallback ions: {fallback_ions}")

        _run_command(
            [
                str(sirius),
                "--input",
                str(outdir / f"{task}__sirius.mgf"),
                "--output",
                str(outdir / f"{task}__sirius.sirius"),
                "config",
                "--IsotopeSettings.filter=true",
                "--CandidateFormulas=,",
                "--FormulaSettings.enforced=H,C,N,O,P",
                "--Timeout.secondsPerInstance=0",
                "--AlgorithmProfile=orbitrap",
                "--SpectralMatchingMassDeviation.allowedPeakDeviation=5.0ppm",
                "--AdductSettings.ignoreDetectedAdducts=false",
                "--AdductSettings.prioritizeInputFileAdducts=true",
                "--UseHeuristic.useHeuristicAboveMz=300",
                "--IsotopeMs2Settings=IGNORE",
                "--MS2MassDeviation.allowedMassDeviation=5.0ppm",
                "--SpectralMatchingMassDeviation.allowedPrecursorDeviation=5.0ppm",
                "--FormulaSearchSettings.performDeNovoBelowMz=400.0",
                "--FormulaSearchSettings.applyFormulaConstraintsToDatabaseCandidates=false",
                "--EnforceElGordoFormula=true",
                "--NumberOfCandidatesPerIonization=1",
                fallback_ions,
                "--FormulaSearchSettings.performBottomUpAboveMz=0",
                "--FormulaSearchSettings.applyFormulaConstraintsToBottomUp=false",
                "--UseHeuristic.useOnlyHeuristicAboveMz=650",
                "--FormulaSearchDB=,",
                "--Timeout.secondsPerTree=0",
                "--AdductSettings.enforced=,",
                "--FormulaSettings.detectable=B,S,Cl,Se,Br",
                "--NumberOfCandidates=10",
                "--FormulaResultThreshold=true",
                "--ExpansiveSearchConfidenceMode.confidenceScoreSimilarityMode=APPROXIMATE",
                "--StructureSearchDB=BOKU_iBAM,CHEBI,COCONUT,GNPS,KEGG,LOTUS,PLANTCYC,SUPERNATURAL",
                "--RecomputeResults=false",
                "spectra-search",
                "formulas",
                "fingerprints",
                "classes",
                "structures",
                "summaries",
                "--top-k-summary=15",
            ],
            log_fn,
            sirius_log,
            cwd=SCRIPT_DIR,
            append=True,
        )
    else:
        log_fn("[Step 2] SIRIUS skipped.")

    log_fn(f"\nFinished {task} in {time() - t0:.1f} s")
    return True


def run_all(config: dict, log_fn) -> None:
    """Process all selected tasks and optionally archive results."""
    tasks: list[str] = config["tasks"]
    if not tasks:
        log_fn("No datasets selected – nothing to do.")
        return

    overall_t0 = time()
    failed: list[str] = []

    for task in tasks:
        ok = process_task(task, config, log_fn)
        if not ok:
            failed.append(task)
            log_fn(f"  Task {task} finished with errors.")

    # ------------------------------------------------------------------
    # Archive
    # ------------------------------------------------------------------
    if config["archive_results"]:
        log_fn(f"\n{'=' * 60}")
        log_fn("Archiving results ...")
        outdir = Path(config["outdir"])
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = SCRIPT_DIR.parent / f"Archive__{outdir.name}__{timestamp}.7z"
        seven_zip = Path(r"C:\Program Files\7-Zip\7z.exe")
        if seven_zip.exists():
            _run_command(
                [str(seven_zip), "a", str(archive_name), str(outdir) + "\\"],
                log_fn,
            )
            log_fn(f"  Archive created: {archive_name}")
        else:
            log_fn("  WARNING – 7-Zip not found at expected path; skipping archive.")

    elapsed = time() - overall_t0
    log_fn(f"\n{'=' * 60}")
    if failed:
        log_fn(f"COMPLETED WITH ERRORS in {elapsed:.1f} s")
        log_fn(f"Failed tasks: {', '.join(failed)}")
    else:
        log_fn(f"ALL TASKS COMPLETED SUCCESSFULLY in {elapsed:.1f} s")


# ---------------------------------------------------------------------------
# Configuration screen
# ---------------------------------------------------------------------------

_SORTED_TASKS = sorted(TASK_PARAMS.keys())


class ConfigScreen(Screen):
    """Main configuration screen."""

    CSS = """
    ConfigScreen {
        background: $surface;
    }

    #config-scroll {
        height: 1fr;
        padding: 0 1;
    }

    .section {
        border: round $panel;
        padding: 1 2;
        margin-bottom: 1;
        height: auto;
    }

    .section-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }

    .field-label {
        margin-top: 1;
        color: $text-disabled;
    }

    Input {
        margin-bottom: 1;
    }

    SelectionList {
        height: auto;
        max-height: 18;
        border: solid $panel;
    }

    .sel-buttons {
        height: 3;
        margin-top: 1;
    }

    .sel-buttons Button {
        margin-right: 1;
        min-width: 18;
    }

    .step-row {
        height: 3;
    }

    .substep-row {
        height: 3;
        padding-left: 4;
    }

    Collapsible {
        border: none;
        padding: 0;
        margin-top: 1;
    }

    #action-bar {
        height: 4;
        align: center middle;
        padding: 1 0;
    }

    #start-btn {
        min-width: 36;
    }

    #validation-msg {
        color: $error;
        text-align: center;
        height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Footer()
        with ScrollableContainer(id="config-scroll"):
            # ---- Paths -----------------------------------------------
            with Container(classes="section"):
                yield Static("Paths & Directories", classes="section-title")

                yield Static("Output directory:", classes="field-label")
                yield Input(
                    value=DEFAULT_OUTDIR,
                    id="outdir",
                    placeholder="Path to output folder",
                )

            # ---- Dataset selection ------------------------------------
            with Container(classes="section"):
                yield Static(
                    "Datasets  (space / click to toggle)", classes="section-title"
                )
                yield SelectionList(
                    *[Selection(task, task, True) for task in _SORTED_TASKS],
                    id="dataset-list",
                )
                with Horizontal(classes="sel-buttons"):
                    yield Button("Select All", id="sel-all", variant="default")
                    yield Button("Deselect All", id="sel-none", variant="default")

            # ---- Processing steps ------------------------------------
            with Container(classes="section"):
                yield Static("Processing Steps", classes="section-title")
                for step_id, label, default in PROCESSING_STEPS:
                    with Horizontal(classes="step-row"):
                        yield Checkbox(label, value=default, id=step_id)
                with Collapsible(title="MZmine sub-steps", collapsed=True):
                    for step_id, label, default in MZMINE_SUBSTEPS:
                        with Horizontal(classes="substep-row"):
                            yield Checkbox(label, value=default, id=step_id)

            # ---- Options ---------------------------------------------
            with Container(classes="section"):
                yield Static("Options", classes="section-title")
                for step_id, label, default in PIPELINE_OPTIONS:
                    with Horizontal(classes="step-row"):
                        yield Checkbox(label, value=default, id=step_id)

            # ---- Action bar -----------------------------------------
            yield Static("", id="validation-msg")
            with Horizontal(id="action-bar"):
                yield Button(
                    "▶  Start Processing",
                    id="start-btn",
                    variant="success",
                )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sel-all":
            sl: SelectionList = self.query_one("#dataset-list", SelectionList)
            for task in _SORTED_TASKS:
                sl.select(task)
            return
        if event.button.id == "sel-none":
            sl = self.query_one("#dataset-list", SelectionList)
            for task in _SORTED_TASKS:
                sl.deselect(task)
            return
        if event.button.id == "start-btn":
            self._start_processing()

    def _start_processing(self) -> None:
        sl: SelectionList = self.query_one("#dataset-list", SelectionList)
        selected_tasks: list[str] = list(sl.selected)

        msg_widget = self.query_one("#validation-msg", Static)

        if not selected_tasks:
            msg_widget.update("Please select at least one dataset.")
            return

        steps_active = [
            step_id
            for step_id, _label, _default in PROCESSING_STEPS
            if self.query_one(f"#{step_id}", Checkbox).value
        ]
        if not steps_active:
            msg_widget.update("Please enable at least one processing step.")
            return

        msg_widget.update("")

        config = {
            # Paths
            "outdir": self.query_one("#outdir", Input).value.strip(),
            "mzmine": DEFAULT_MZMINE,
            "sirius": DEFAULT_SIRIUS,
            "util_mgftools_dir": DEFAULT_UTIL_MGFTOOLS,
            "util_fragextract_exe": DEFAULT_UTIL_FRAGEXTRACT,
            "meii_ref_file": DEFAULT_MEII_REF,
            "util_reorder_dir": DEFAULT_UTIL_REORDER,
            # Datasets
            "tasks": sorted(selected_tasks),
            # Main steps
            **{
                sid: self.query_one(f"#{sid}", Checkbox).value
                for sid, _, _ in PROCESSING_STEPS
            },
            # MZmine sub-steps
            **{
                sid: self.query_one(f"#{sid}", Checkbox).value
                for sid, _, _ in MZMINE_SUBSTEPS
            },
            # Options
            **{
                sid: self.query_one(f"#{sid}", Checkbox).value
                for sid, _, _ in PIPELINE_OPTIONS
            },
        }

        self.app.push_screen(ProcessingScreen(config))


# ---------------------------------------------------------------------------
# Processing / log screen
# ---------------------------------------------------------------------------


class ProcessingScreen(Screen):
    """Full-screen log view that runs the pipeline in a background thread."""

    CSS = """
    ProcessingScreen {
        background: $surface;
    }

    #log {
        height: 1fr;
        border: none;
        padding: 0 1;
        scrollbar-gutter: stable;
    }

    #status-bar {
        dock: bottom;
        height: 3;
        align: center middle;
        background: $panel;
        padding: 0 2;
    }

    #status-bar Button {
        min-width: 18;
        margin: 0 1;
    }

    #status-label {
        width: 1fr;
        text-align: left;
        color: $text-disabled;
        content-align: left middle;
    }
    """

    BINDINGS = [
        Binding("q", "request_quit", "Quit", show=False),
    ]

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config
        self._done = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield RichLog(id="log", highlight=True, markup=False, wrap=True)
        with Horizontal(id="status-bar"):
            yield Static("Running …", id="status-label")
            yield Button(
                "Back to Config", id="back-btn", variant="default", disabled=True
            )
            yield Button("Exit", id="exit-btn", variant="error", disabled=True)

    def on_mount(self) -> None:
        log = self.query_one("#log", RichLog)
        log.write(
            f"Untargeted Metabolomics Pipeline – started {datetime.now():%Y-%m-%d %H:%M:%S}"
        )
        log.write(f"Output directory : {self._config['outdir']}")
        log.write(f"Selected datasets: {', '.join(self._config['tasks'])}")
        log.write(
            f"Steps            : "
            f"MZmine={'yes' if self._config['proc_mzmine'] else 'no'}  "
            f"SIRIUS={'yes' if self._config['proc_sirius'] else 'no'}  "
            f"Archive={'yes' if self._config['archive_results'] else 'no'}"
        )
        log.write("")
        self._run_pipeline()

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    @work(thread=True)
    def _run_pipeline(self) -> None:
        log: RichLog = self.query_one("#log", RichLog)

        def log_fn(text: str) -> None:
            self.app.call_from_thread(log.write, text)

        run_all(self._config, log_fn)

        self._done = True
        self.app.call_from_thread(self._on_pipeline_done)

    def _on_pipeline_done(self) -> None:
        self.query_one("#status-label", Static).update("Done.")
        self.query_one("#back-btn", Button).disabled = False
        self.query_one("#exit-btn", Button).disabled = False

    # ------------------------------------------------------------------
    # Button events
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back-btn":
            self.app.pop_screen()
        elif event.button.id == "exit-btn":
            self.app.exit()

    def action_request_quit(self) -> None:
        if self._done:
            self.app.exit()


# ---------------------------------------------------------------------------
# Application entry point
# ---------------------------------------------------------------------------


class PipelineApp(App):
    """Untargeted Metabolomics Pipeline TUI."""

    TITLE = "Untargeted Metabolomics Pipeline"
    SUB_TITLE = "MZmine · SIRIUS"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.push_screen(ConfigScreen())


if __name__ == "__main__":
    PipelineApp().run()
