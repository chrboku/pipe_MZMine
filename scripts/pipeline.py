#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "textual",
#   "httpx",
# ]
# ///

import csv
import json
import re
import shutil
import subprocess
import zipfile
import io
from datetime import datetime
from pathlib import Path
from time import time

import httpx
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
SOFTWARE_DIR = SCRIPT_DIR / "software"
LIBRARIES_DIR = SCRIPT_DIR / "libraries"
LIBRARIES_CONFIG_FILE = SCRIPT_DIR / "spectral_libraries.json"
SOFTWARE_PACKAGES_FILE = SCRIPT_DIR / "software_packages.json"
MYPROJECT_FILE = SCRIPT_DIR / "myproject.json"

# ---------------------------------------------------------------------------
# Spectral libraries – loaded from spectral_libraries.json (name → url)
# ---------------------------------------------------------------------------


def load_software_packages() -> dict[str, dict[str, str]]:
    try:
        data = json.loads(SOFTWARE_PACKAGES_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    normalized: dict[str, dict[str, str]] = {}
    for version, info in data.items():
        if isinstance(info, dict):
            normalized[str(version)] = {
                str(key): str(value) for key, value in info.items() if value is not None
            }
    return normalized


def load_library_config() -> dict[str, str | list[str]]:
    try:
        return json.loads(LIBRARIES_CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


def load_project_version() -> str:
    try:
        data = json.loads(MYPROJECT_FILE.read_text(encoding="utf-8-sig"))
        version = str(data.get("version", "unknown")).strip()
        return version or "unknown"
    except Exception:
        return "unknown"


def normalize_library_config(
    config: dict[str, str | list[str]],
) -> dict[str, list[str]]:
    """Normalize library config so all values are lists of URLs."""
    normalized = {}
    for name, urls in config.items():
        if isinstance(urls, str):
            normalized[name] = [urls]
        elif isinstance(urls, list):
            normalized[name] = urls
    return normalized


LIBRARY_PACKAGES_RAW: dict[str, str | list[str]] = load_library_config()
LIBRARY_PACKAGES: dict[str, list[str]] = normalize_library_config(LIBRARY_PACKAGES_RAW)
SOFTWARE_PACKAGES: dict[str, dict[str, str]] = load_software_packages()
PROJECT_VERSION = load_project_version()


def _software_id(version: str) -> str:
    return version.replace(" ", "-").replace(".", "-").lower()


def get_software_package(version: str) -> dict[str, str] | None:
    return SOFTWARE_PACKAGES.get(version)


def _library_slug(name: str) -> str:
    """Return a safe widget-id slug for a library name."""
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _url_filename(url: str) -> str:
    """Derive a local filename from a download URL."""
    name = url.split("/")[-1].split("?")[0]
    return name if name else "library.bin"


def find_library_file(url: str) -> "Path | None":
    """Return the Path to an already-downloaded library file, or None."""
    filename = _url_filename(url)
    if filename.endswith(".zip"):
        stem = Path(filename).stem
        # look for any extracted spectral file matching the zip stem
        for ext in (".mgf", ".msp", ".json", ".csv"):
            candidate = LIBRARIES_DIR / (stem + ext)
            if candidate.exists():
                return candidate
        # broader search inside LIBRARIES_DIR
        for f in LIBRARIES_DIR.glob(f"{stem}*"):
            if f.suffix.lower() in (".mgf", ".msp", ".json"):
                return f
        return None
    candidate = LIBRARIES_DIR / filename
    return candidate if candidate.exists() else None


def count_library_entries(file_path: "Path") -> "int | None":
    """Count spectra entries in a downloaded spectral library file."""
    try:
        suffix = file_path.suffix.lower()
        if suffix == ".mgf":
            count = 0
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip().upper() == "BEGIN IONS":
                        count += 1
            return count
        elif suffix == ".msp":
            count = 0
            with open(file_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.strip().upper().startswith("NAME:"):
                        count += 1
            return count
        elif suffix == ".json":
            data = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return len(data)
        return None
    except Exception:
        return None


def download_library(name: str, url: str, log_fn) -> "bool":
    """Download (and unzip if necessary) a spectral library file."""
    LIBRARIES_DIR.mkdir(parents=True, exist_ok=True)
    filename = _url_filename(url)
    dest = LIBRARIES_DIR / filename

    log_fn(f"Downloading {name} …")
    try:
        with httpx.Client(follow_redirects=True, timeout=600) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        fh.write(chunk)

        if filename.endswith(".zip"):
            log_fn(f"Extracting {filename} …")
            with zipfile.ZipFile(dest) as z:
                z.extractall(LIBRARIES_DIR)
            dest.unlink(missing_ok=True)
            log_fn(f"Extracted to {LIBRARIES_DIR}")
        else:
            log_fn(f"Saved to {dest}")
        return True
    except Exception as exc:
        log_fn(f"Failed to download {name}: {exc}")
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False


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
TASK_FILES = sorted(list(SCRIPT_DIR.glob("tasks*.json")))


def load_tasks(file_path: Path) -> dict[str, dict]:
    try:
        return json.loads(file_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}


# We'll initialize these later in the App if needed,
# but for now we pick the first one as default if it exists.
_TASKS_FILE = TASK_FILES[0] if TASK_FILES else SCRIPT_DIR / "tasks.json"
TASK_PARAMS: dict[str, dict] = load_tasks(_TASKS_FILE)

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


def download_and_unpack(version: str, log_fn) -> bool:
    """Download and unpack a software package."""
    info = get_software_package(version)
    if not info:
        log_fn(f"Unknown software version: {version}")
        return False

    display_name = info.get("display_name", version)
    url = info["url"]
    dest_folder = SOFTWARE_DIR / info["folder"]

    if dest_folder.exists():
        log_fn(f"Software already exists at {dest_folder}")
        return True

    log_fn(f"Downloading {display_name} from {url} ...")
    try:
        SOFTWARE_DIR.mkdir(parents=True, exist_ok=True)
        with httpx.Client(follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            content = response.content

        log_fn(f"Unpacking {display_name} to {dest_folder} ...")
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            # Extract to the subfolder defined in SOFTWARE_PACKAGES
            z.extractall(dest_folder)

        log_fn(f"Successfully installed {display_name}.")
        return True
    except Exception as e:
        log_fn(f"Failed to download/unpack {display_name}: {e}")
        if dest_folder.exists():
            shutil.rmtree(dest_folder)
        return False


def _run_command(
    cmd: list[str],
    log_fn,
    log_file: Path | None = None,
    cwd: Path | None = None,
    append: bool = False,
) -> int:
    """Run *cmd* and stream every line to *log_fn*.  Optionally tee to a file."""
    log_fn(f"  $ {' '.join(cmd)}")
    t_start = time()
    # Write header to log file before the command starts
    if log_file:
        mode = "a" if append else "w"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, mode, encoding="utf-8") as fh:
            fh.write(f"\nCommand: {' '.join(cmd)}\n")
            fh.write("#" * 80 + "\n")
            fh.write("\n")
    try:
        # On Windows, running batch files or certain executables may require shell=True
        use_shell = any(arg.lower().endswith(".bat") for arg in cmd) or any(
            arg.lower().endswith(".cmd") for arg in cmd
        )
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
            shell=use_shell,
        )
        collected: list[str] = []
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            log_fn(line)
            collected.append(line)
        proc.wait()
        elapsed = time() - t_start
        if log_file:
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write("\n".join(collected) + "\n")
                fh.write("\n")
                fh.write(f"\n{'#' * 80}\n")
                fh.write(f"Elapsed: {elapsed:.1f} s\n")
                fh.write("\n")
        return proc.returncode if proc.returncode is not None else 0
    except FileNotFoundError as exc:
        log_fn(f"  ERROR – command not found: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        log_fn(f"  ERROR – {exc}")
        return 1


def _tool_log_file(outdir: Path, order: int, label: str) -> Path:
    """Return a numbered log filename for one processing tool."""
    safe_label = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return outdir / f"{order:02d}___{safe_label}.log.txt"


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

    params = TASK_PARAMS[task]

    mzmine_version = str(params.get("mzmine_version", config["mzmine_version"]))
    mzmine_info = get_software_package(mzmine_version)
    if not mzmine_info:
        log_fn(f"Missing MZmine package for version {mzmine_version}")
        return False
    mzmine = SOFTWARE_DIR / mzmine_info["folder"] / mzmine_info["executable"]

    sirius_version = str(params.get("sirius_version", config["sirius_version"]))
    sirius_info = get_software_package(sirius_version)
    if not sirius_info:
        log_fn(f"Missing SIRIUS package for version {sirius_version}")
        return False
    sirius = SOFTWARE_DIR / sirius_info["folder"] / sirius_info["executable"]

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "params_scripts_etc").mkdir(exist_ok=True)

    # Emit dataset marker for UI
    log_fn(f"Task: {task}")
    log_fn(f"Output: {outdir}")

    t0 = time()

    # ------------------------------------------------------------------
    # Step 1 – MZmine
    # ------------------------------------------------------------------
    if config["proc_mzmine"]:
        mzmine_log = _tool_log_file(outdir, 1, "MZmine")
        log_fn("[Step 1] Running MZmine")

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

        # Build optional --libraries argument
        library_paths: list[str] = config.get("library_paths", [])
        if library_paths:
            libs_dest = outdir / "params_scripts_etc" / "spectral_libraries"
            libs_dest.mkdir(exist_ok=True)
            copied_paths: list[str] = []
            for lp in library_paths:
                src = Path(lp)
                dst = libs_dest / src.name
                shutil.copy2(src, dst)
                log_fn(f"  copied library: {src.name}")
                copied_paths.append(str(dst))
            libraries_txt = outdir / "params_scripts_etc" / "spectral_libraries.txt"
            libraries_txt.write_text("\n".join(copied_paths) + "\n", encoding="utf-8")
            log_fn(f"  libraries: {libraries_txt} ({len(copied_paths)} file(s))")
            libraries_args = ["--libraries", str(libraries_txt)]
        else:
            libraries_args = []

        rc = _run_command(
            [
                str(mzmine),
                *libraries_args,
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
            rename_log = _tool_log_file(outdir, 2, "rename_feature_ids")
            for f in ["__full_feature_table.csv", "__iimn_fbmn.mgf", "__sirius.mgf"]:
                src = str(outdir / f"{task}{f}")
                dest = str(outdir / f"{task}_4GNPS_{f}")
                # copy file
                if Path(src).exists():
                    shutil.copy2(src, dest)
                    log_fn(f"  copied {src} -> {dest}")
                else:
                    log_fn(f"  WARNING – expected file not found: {src}")
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
                rename_log,
                cwd=SCRIPT_DIR,
                append=True,
            )
        else:
            log_fn("[Step 1.1] Skipped.")

        # --- 1.2 Split MGF files ---
        if config.get("step_split_mgf", False):
            log_fn("\n[Step 1.2] Splitting MGF files ...")
            split_log = _tool_log_file(outdir, 3, "split_mgf")
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
                        split_log,
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
            clean_log = _tool_log_file(outdir, 4, "clean_spectra")
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
                        clean_log,
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
            combine_log = _tool_log_file(outdir, 5, "combine_meii")
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
                    combine_log,
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
            reorder_log = _tool_log_file(outdir, 6, "reorder_table")
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
                    reorder_log,
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
            dda_log = _tool_log_file(outdir, 7, "dda_inclusion_list")
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
                dda_log,
                cwd=SCRIPT_DIR,
                append=True,
            )
        else:
            log_fn("[Step 1.6] Skipped.")

        # --- 1.7 CSV → TSV ---
        if config.get("step_csv_to_tsv", True):
            log_fn("\n[Step 1.7] Converting CSV files to TSV ...")
            csv_tsv_log = _tool_log_file(outdir, 8, "csv_to_tsv")
            tool_log_fn = log_fn

            def csv_tsv_log_fn(msg: str, _log_file: Path = csv_tsv_log) -> None:
                tool_log_fn(msg)
                _log_file.parent.mkdir(parents=True, exist_ok=True)
                with open(_log_file, "a", encoding="utf-8") as fh:
                    fh.write(msg + "\n")

            log_fn = csv_tsv_log_fn
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
        log_fn("[Step 2] Running SIRIUS")

        # 2.1 Fix MGF
        log_fn("  [Step 2.1] Fixing MGF file ...")
        fix_mgf_log = _tool_log_file(outdir, 9, "fix_mgf")
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
            fix_mgf_log,
            cwd=SCRIPT_DIR,
        )

        # 2.2 Run SIRIUS
        log_fn("  [Step 2.2] Predicting fingerprints ...")
        sirius_log = _tool_log_file(outdir, 10, "predict_fingerprints")
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
                "--AdductSettings.ignoreDetectedAdducts=false",
                "--AdductSettings.prioritizeInputFileAdducts=true",
                "--UseHeuristic.useHeuristicAboveMz=300",
                "--IsotopeMs2Settings=IGNORE",
                "--MS2MassDeviation.allowedMassDeviation=15.0ppm",
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
        log_fn("Task: Archive Results")
        log_fn("[Step 3] Archiving Results")
        outdir = Path(config["outdir"])
        archive_log = _tool_log_file(outdir, 11, "archive_results")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = SCRIPT_DIR.parent / f"Archive__{outdir.name}__{timestamp}.7z"
        seven_zip = Path(r"C:\Program Files\7-Zip\7z.exe")
        if seven_zip.exists():
            _run_command(
                [str(seven_zip), "a", str(archive_name), str(outdir) + "\\"],
                log_fn,
                archive_log,
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
        border: round $warning;
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
        max-height: 22;
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

    .sel-buttons Input {
        width: 1fr;
    }

    .sel-buttons Static {
        width: auto;
        margin-right: 1;
    }

    .software-row {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    .software-name {
        width: 2fr;
        content-align: left middle;
    }

    .software-status {
        width: 1fr;
        content-align: left middle;
    }

    .download-btn {
        width: 1fr;
    }

    .download-all-btn {
        margin-bottom: 1;
    }

    .library-row {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }

    .library-name {
        width: 2fr;
        content-align: left middle;
    }

    .library-status {
        width: 1fr;
        content-align: left middle;
    }

    .library-checkbox {
        width: 2fr;
        padding: 0 1 0 0;
    }

    .step-row {
        height: 3;
    }

    .substep-row {
        height: 3;
        padding-left: 4;
    }

    .substep-group-title {
        margin: 1 0 0 2;
        color: $text-disabled;
    }

    .mzmine-substep-box {
        margin: 0 0 0 2;
        padding: 0 1 0 1;
        border: solid $panel;
        height: auto;
    }

    .mzmine-substep-row {
        height: auto;
        margin: 0;
    }

    .mzmine-substep-row Checkbox {
        width: 1fr;
    }

    Collapsible {
        border: none;
        padding: 0;
        margin-top: 0;
    }

    #dataset-split {
        height: auto;
        max-height: 32;
    }

    #dataset-left {
        width: 1fr;
        height: auto;
    }

    #dataset-right {
        width: 1fr;
        height: auto;
        border-left: solid $panel;
        padding: 0 0 0 1;
    }

    #dataset-details {
        height: auto;
        color: $text-disabled;
    }

    #dataset-count {
        height: 1;
        margin-top: 1;
        color: $text-disabled;
    }

    #dataset-filter {
        height: 3;
    }

    #action-bar {
        height: 7;
        align: center middle;
        padding: 2 0 3 0;
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
            # ---- Software Selection ----------------------------------
            with Container(classes="section"):
                with Collapsible(
                    title="Software Versions", collapsed=False, id="coll-software"
                ):
                    yield Button(
                        "Download All",
                        id="dl-all-software",
                        variant="default",
                        classes="download-all-btn",
                    )
                    for version, info in SOFTWARE_PACKAGES.items():
                        display_name = info.get("display_name", version)
                        name_id = _software_id(version)
                        with Horizontal(
                            classes="software-row",
                            id=f"row-{name_id}",
                        ):
                            yield Static(display_name, classes="software-name")
                            yield Static(
                                "[yellow]Checking…[/yellow]",
                                classes="software-status",
                                id=f"status-{name_id}",
                            )
                            yield Button(
                                "Download",
                                id=f"dl-{name_id}",
                                classes="download-btn",
                                variant="default",
                            )

            # ---- Spectral Libraries ---------------------------------
            with Container(classes="section"):
                with Collapsible(
                    title="Spectral Libraries", collapsed=False, id="coll-libraries"
                ):
                    if not LIBRARY_PACKAGES:
                        yield Static(
                            f"No libraries configured. Edit {LIBRARIES_CONFIG_FILE.name}",
                            classes="field-label",
                        )
                    with Horizontal(classes="library-action-row"):
                        yield Button(
                            "Download All",
                            id="dl-all-libraries",
                            variant="default",
                            classes="download-all-btn",
                        )
                        yield Button(
                            "Select All",
                            id="lib-select-all",
                            variant="success",
                            classes="download-all-btn",
                        )
                        yield Button(
                            "Deselect All",
                            id="lib-deselect-all",
                            variant="warning",
                            classes="download-all-btn",
                        )
                    lib_idx = 0
                    for lib_name, lib_urls in LIBRARY_PACKAGES.items():
                        # Library collection header with download all button (only if multiple URLs)
                        with Horizontal(
                            classes="library-row", id=f"lib-header-{lib_idx}"
                        ):
                            yield Static(
                                f"[bold]{lib_name}[/bold]",
                                classes="library-name",
                                id=f"lib-header-name-{lib_idx}",
                            )
                            if len(lib_urls) > 1:
                                yield Button(
                                    "Download All",
                                    id=f"lib-collection-dl-{lib_idx}",
                                    variant="default",
                                    classes="download-btn",
                                )
                        # Individual URLs for this library
                        for url_idx, lib_url in enumerate(lib_urls):
                            url_filename = _url_filename(lib_url)
                            with Horizontal(
                                classes="library-row",
                                id=f"lib-url-row-{lib_idx}-{url_idx}",
                            ):
                                yield Checkbox(
                                    f"  {url_filename}",
                                    value=False,
                                    disabled=True,
                                    id=f"lib-url-chk-{lib_idx}-{url_idx}",
                                    classes="library-checkbox",
                                )
                                yield Static(
                                    "Checking…",
                                    classes="library-status",
                                    id=f"lib-url-status-{lib_idx}-{url_idx}",
                                )
                                yield Button(
                                    "Download",
                                    id=f"lib-url-dl-{lib_idx}-{url_idx}",
                                    classes="download-btn",
                                    variant="default",
                                )
                        lib_idx += 1

            # ---- Task File Selection ---------------------------------
            with Container(classes="section"):
                yield Static("Task Configuration File", classes="section-title")
                yield SelectionList(
                    *[
                        Selection(str(f.name), str(f), f == _TASKS_FILE)
                        for f in TASK_FILES
                    ],
                    id="task-file-list",
                )

            # ---- Output path ----------------------------------------
            with Container(classes="section"):
                yield Static("Output path", classes="section-title")

                yield Static("Output directory:", classes="field-label")
                yield Input(
                    value=DEFAULT_OUTDIR,
                    id="outdir",
                    placeholder="Path to output folder",
                )
                for step_id, label, default in PIPELINE_OPTIONS:
                    with Horizontal(classes="step-row"):
                        yield Checkbox(label, value=default, id=step_id)

            # ---- Dataset selection ------------------------------------
            with Container(classes="section"):
                yield Static(
                    "Datasets (space / click to toggle)", classes="section-title"
                )
                with Horizontal(id="dataset-split"):
                    with Container(id="dataset-left"):
                        with Horizontal(classes="sel-buttons"):
                            yield Static("Filter:", classes="field-label")
                            yield Input(
                                placeholder="Filter datasets…",
                                id="dataset-filter",
                            )
                        with Horizontal(classes="sel-buttons"):
                            yield Button("Select All", id="sel-all", variant="default")
                            yield Button(
                                "Deselect All", id="sel-none", variant="default"
                            )
                        yield Static("", id="dataset-count")
                        yield SelectionList(
                            *[
                                Selection(task, task, True)
                                for task in sorted(TASK_PARAMS.keys())
                            ],
                            id="dataset-list",
                        )
                    with Container(id="dataset-right"):
                        yield Static(
                            "Highlight a dataset to see details.",
                            id="dataset-details",
                        )

            # ---- Processing steps ------------------------------------
            with Container(classes="section"):
                yield Static("Processing Steps", classes="section-title")
                for step_id, label, default in PROCESSING_STEPS:
                    with Horizontal(classes="step-row"):
                        yield Checkbox(label, value=default, id=step_id)
                    if step_id == "proc_mzmine":
                        yield Static("MZmine substeps", classes="substep-group-title")
                        with Container(classes="mzmine-substep-box"):
                            for row_start in range(0, len(MZMINE_SUBSTEPS), 3):
                                with Horizontal(classes="mzmine-substep-row"):
                                    for (
                                        sub_id,
                                        sub_label,
                                        sub_default,
                                    ) in MZMINE_SUBSTEPS[row_start : row_start + 3]:
                                        yield Checkbox(
                                            sub_label, value=sub_default, id=sub_id
                                        )

            # ---- Action bar -----------------------------------------
            yield Static("", id="validation-msg")
            with Horizontal(id="action-bar"):
                yield Button(
                    "▶  Start Processing",
                    id="start-btn",
                    variant="success",
                )

    def on_mount(self) -> None:
        """Call on_mount to refresh status or set initial state."""
        self._selected_tasks: set[str] = set(sorted(TASK_PARAMS.keys()))
        self._filter_text: str = ""
        # Ensure we have a valid task file selected in UI
        if TASK_FILES:
            sl = self.query_one("#task-file-list", SelectionList)
            sl.select(str(_TASKS_FILE))
        # Check which spectral libraries are already present
        if LIBRARY_PACKAGES:
            self._refresh_library_statuses()
        # Check software status
        self._check_software_status()
        self._update_dataset_count()
        # Initialize dataset details with first task
        if TASK_PARAMS:
            first_task = next(iter(sorted(TASK_PARAMS.keys())))
            try:
                self.query_one("#dataset-details", Static).update(
                    self._format_task_details(first_task)
                )
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dl-all-software":
            self._download_all_software()
            return
        if event.button.id == "dl-all-libraries":
            self._download_all_libraries()
            return
        if event.button.id == "lib-select-all":
            for chk in self.query(".library-checkbox").results(Checkbox):
                if not chk.disabled:
                    chk.value = True
            return
        if event.button.id == "lib-deselect-all":
            for chk in self.query(".library-checkbox").results(Checkbox):
                if not chk.disabled:
                    chk.value = False
            return

        if event.button.id and event.button.id.startswith("dl-"):
            name_id = event.button.id[3:]
            # Find the original version from SOFTWARE_PACKAGES
            target_version = None
            for version in SOFTWARE_PACKAGES:
                if _software_id(version) == name_id:
                    target_version = version
                    break
            if target_version:
                self._download_software(target_version)
            return

        if event.button.id and event.button.id.startswith("lib-collection-dl-"):
            # Download all files in a library collection
            lib_idx_str = event.button.id.replace("lib-collection-dl-", "")
            try:
                lib_idx = int(lib_idx_str)
                lib_items = list(LIBRARY_PACKAGES.items())
                if 0 <= lib_idx < len(lib_items):
                    lib_name, lib_urls = lib_items[lib_idx]
                    self._download_library_collection(lib_idx, lib_name, lib_urls)
            except (ValueError, IndexError):
                pass
            return

        if event.button.id and event.button.id.startswith("lib-url-dl-"):
            # Parse lib-url-dl-{lib_idx}-{url_idx}
            parts = event.button.id.replace("lib-url-dl-", "").split("-")
            if len(parts) >= 2:
                try:
                    lib_idx = int(parts[0])
                    url_idx = int(parts[1])
                    lib_items = list(LIBRARY_PACKAGES.items())
                    if 0 <= lib_idx < len(lib_items):
                        lib_name, lib_urls = lib_items[lib_idx]
                        if 0 <= url_idx < len(lib_urls):
                            lib_url = lib_urls[url_idx]
                            self._download_library(lib_idx, url_idx, lib_name, lib_url)
                except (ValueError, IndexError):
                    pass
            return

        if event.button.id == "sel-all":
            self._selected_tasks = set(sorted(TASK_PARAMS.keys()))
            self._apply_dataset_filter()
            return
        if event.button.id == "sel-none":
            self._selected_tasks = set()
            self._apply_dataset_filter()
            return
        if event.button.id == "start-btn":
            self._start_processing()

    @work(thread=True)
    def _download_software(self, version: str) -> None:
        name_id = _software_id(version)
        btn = self.query_one(f"#dl-{name_id}", Button)
        status = self.query_one(f"#status-{name_id}", Static)

        self.app.call_from_thread(btn.set_loading, True)
        self.app.call_from_thread(status.update, "Downloading...")

        def log_fn(msg):
            self.app.call_from_thread(status.update, msg)

        success = download_and_unpack(version, log_fn)

        self.app.call_from_thread(btn.set_loading, False)
        if success:
            info = get_software_package(version)
            if not info:
                self.app.call_from_thread(status.update, "[red]Download failed[/red]")
                return
            path = SOFTWARE_DIR / info["folder"] / info["executable"]
            self.app.call_from_thread(
                status.update, f"[green]Installed: {path.name}[/green]"
            )
            self.app.call_from_thread(setattr, btn, "label", "Redownload")
            self.app.call_from_thread(setattr, btn, "variant", "default")
        else:
            self.app.call_from_thread(status.update, "[red]Download failed[/red]")

    # ------------------------------------------------------------------
    # Library workers
    # ------------------------------------------------------------------

    @work(thread=True)
    @work(thread=True)
    def _refresh_library_statuses(self) -> None:
        """Check every library entry and update its status widget."""
        lib_idx = 0
        for lib_name, lib_urls in LIBRARY_PACKAGES.items():
            for url_idx, lib_url in enumerate(lib_urls):
                self._set_library_status(lib_idx, url_idx, lib_name, lib_url)
            lib_idx += 1

    def _set_library_status(
        self, lib_idx: int, url_idx: int, lib_name: str, url: str
    ) -> None:
        """Update one URL's status widget and checkbox."""
        status = self.query_one(f"#lib-url-status-{lib_idx}-{url_idx}", Static)
        btn = self.query_one(f"#lib-url-dl-{lib_idx}-{url_idx}", Button)
        chk = self.query_one(f"#lib-url-chk-{lib_idx}-{url_idx}", Checkbox)
        lib_file = find_library_file(url)

        if lib_file is None:
            self.app.call_from_thread(status.update, "[red]Missing[/red]")
            self.app.call_from_thread(setattr, btn, "variant", "primary")
            self.app.call_from_thread(setattr, btn, "label", "Download")
            self.app.call_from_thread(setattr, chk, "disabled", True)
            self.app.call_from_thread(setattr, chk, "value", False)
        else:
            self.app.call_from_thread(status.update, "Counting entries…")
            count = count_library_entries(lib_file)
            if count is not None:
                self.app.call_from_thread(
                    status.update, f"[green]Downloaded[/green]  ({count:,} entries)"
                )
            else:
                size_mb = lib_file.stat().st_size / 1024 / 1024
                self.app.call_from_thread(
                    status.update,
                    f"[green]Downloaded[/green]  ({size_mb:.1f} MB)",
                )
            self.app.call_from_thread(setattr, btn, "label", "Re-download")
            self.app.call_from_thread(setattr, btn, "variant", "default")
            # Enable individual file checkbox
            self.app.call_from_thread(setattr, chk, "disabled", False)
            self.app.call_from_thread(setattr, chk, "value", True)

    @work(thread=True)
    def _download_library(
        self, lib_idx: int, url_idx: int, lib_name: str, lib_url: str
    ) -> None:
        """Download a single spectral library URL in the background."""
        status = self.query_one(f"#lib-url-status-{lib_idx}-{url_idx}", Static)
        btn = self.query_one(f"#lib-url-dl-{lib_idx}-{url_idx}", Button)

        self.app.call_from_thread(btn.set_loading, True)
        self.app.call_from_thread(status.update, "[yellow]Downloading…[/yellow]")

        def log_fn(msg: str) -> None:
            self.app.call_from_thread(status.update, f"[yellow]{msg}[/yellow]")

        success = download_library(lib_name, lib_url, log_fn)

        self.app.call_from_thread(btn.set_loading, False)
        if success:
            self._set_library_status(lib_idx, url_idx, lib_name, lib_url)
        else:
            self.app.call_from_thread(status.update, "[red]Download failed[/red]")
            self.app.call_from_thread(setattr, btn, "variant", "primary")
            self.app.call_from_thread(setattr, btn, "label", "Download")

    @work(thread=True)
    def _download_all_software(self) -> None:
        """Download all software packages sequentially."""
        for version, info in SOFTWARE_PACKAGES.items():
            name_id = _software_id(version)
            btn = self.query_one(f"#dl-{name_id}", Button)
            status = self.query_one(f"#status-{name_id}", Static)
            self.app.call_from_thread(btn.set_loading, True)
            self.app.call_from_thread(status.update, "Downloading…")

            def log_fn(msg: str, _status: Static = status) -> None:
                self.app.call_from_thread(_status.update, msg)

            success = download_and_unpack(version, log_fn)
            self.app.call_from_thread(btn.set_loading, False)
            if success:
                path = SOFTWARE_DIR / info["folder"] / info["executable"]
                self.app.call_from_thread(
                    status.update, f"[green]Installed: {path.name}[/green]"
                )
                self.app.call_from_thread(setattr, btn, "label", "Redownload")
                self.app.call_from_thread(setattr, btn, "variant", "default")
            else:
                self.app.call_from_thread(status.update, "[red]Download failed[/red]")

    @work(thread=True)
    def _download_all_libraries(self) -> None:
        """Download all spectral library URLs sequentially."""
        lib_idx = 0
        for lib_name, lib_urls in LIBRARY_PACKAGES.items():
            for url_idx, lib_url in enumerate(lib_urls):
                status = self.query_one(f"#lib-url-status-{lib_idx}-{url_idx}", Static)
                btn = self.query_one(f"#lib-url-dl-{lib_idx}-{url_idx}", Button)
                self.app.call_from_thread(btn.set_loading, True)
                self.app.call_from_thread(
                    status.update, "[yellow]Downloading…[/yellow]"
                )

                def log_fn(msg: str, _status: Static = status) -> None:
                    self.app.call_from_thread(_status.update, f"[yellow]{msg}[/yellow]")

                success = download_library(lib_name, lib_url, log_fn)
                self.app.call_from_thread(btn.set_loading, False)
                if success:
                    self._set_library_status(lib_idx, url_idx, lib_name, lib_url)
                else:
                    self.app.call_from_thread(
                        status.update, "[red]Download failed[/red]"
                    )
                    self.app.call_from_thread(setattr, btn, "variant", "primary")
                    self.app.call_from_thread(setattr, btn, "label", "Download")
            lib_idx += 1

    @work(thread=True)
    def _download_library_collection(
        self, lib_idx: int, lib_name: str, lib_urls: list[str]
    ) -> None:
        """Download all files in a library collection sequentially."""
        for url_idx, lib_url in enumerate(lib_urls):
            status = self.query_one(f"#lib-url-status-{lib_idx}-{url_idx}", Static)
            btn = self.query_one(f"#lib-url-dl-{lib_idx}-{url_idx}", Button)
            self.app.call_from_thread(btn.set_loading, True)
            self.app.call_from_thread(status.update, "[yellow]Downloading…[/yellow]")

            def log_fn(msg: str, _status: Static = status) -> None:
                self.app.call_from_thread(_status.update, f"[yellow]{msg}[/yellow]")

            success = download_library(lib_name, lib_url, log_fn)
            self.app.call_from_thread(btn.set_loading, False)
            if success:
                self._set_library_status(lib_idx, url_idx, lib_name, lib_url)
            else:
                self.app.call_from_thread(status.update, "[red]Download failed[/red]")
                self.app.call_from_thread(setattr, btn, "variant", "primary")
                self.app.call_from_thread(setattr, btn, "label", "Download")

    @work(thread=True)
    def _check_software_status(self) -> None:
        """Check software installation status and update buttons."""
        for version, info in SOFTWARE_PACKAGES.items():
            name_id = _software_id(version)
            status = self.query_one(f"#status-{name_id}", Static)
            btn = self.query_one(f"#dl-{name_id}", Button)

            path = SOFTWARE_DIR / info["folder"] / info["executable"]
            exists = path.exists()

            if exists:
                self.app.call_from_thread(
                    status.update, f"[green]Installed: {path.name}[/green]"
                )
                self.app.call_from_thread(setattr, btn, "label", "Redownload")
            else:
                self.app.call_from_thread(status.update, "[red]Not installed[/red]")
                self.app.call_from_thread(setattr, btn, "label", "Download")
                self.app.call_from_thread(setattr, btn, "variant", "primary")

    def on_input_changed(self, event: "Input.Changed") -> None:
        """Filter dataset list when the filter input changes."""
        if event.input.id != "dataset-filter":
            return
        self._filter_text = event.value.lower()
        self._apply_dataset_filter()

    def _apply_dataset_filter(self) -> None:
        """Rebuild the dataset list according to the current filter text."""
        sl = self.query_one("#dataset-list", SelectionList)
        sl.clear_options()
        matching_tasks = [
            task
            for task in sorted(TASK_PARAMS.keys())
            if self._filter_text in task.lower()
        ]
        for task in matching_tasks:
            sl.add_option(Selection(task, task, task in self._selected_tasks))
        self._update_dataset_count()
        # Update details with first matching task or clear if no matches
        if matching_tasks:
            first_match = matching_tasks[0]
            try:
                self.query_one("#dataset-details", Static).update(
                    self._format_task_details(first_match)
                )
            except Exception:
                pass
        else:
            try:
                self.query_one("#dataset-details", Static).update(
                    "No matching datasets."
                )
            except Exception:
                pass

    def _update_dataset_count(self) -> None:
        """Refresh the selected / not-selected count label."""
        total = len(TASK_PARAMS)
        selected = len(self._selected_tasks)
        try:
            self.query_one("#dataset-count", Static).update(
                f"Selected: {selected}  ·  Not selected: {total - selected}  ·  Total: {total}"
            )
        except Exception:
            pass

    def _reload_tasks_from_file(self, task_file: Path) -> None:
        """Reload TASK_PARAMS from *task_file* and refresh the dataset list."""
        global TASK_PARAMS, _TASKS_FILE
        _TASKS_FILE = task_file
        TASK_PARAMS = load_tasks(_TASKS_FILE)
        self._selected_tasks = set(sorted(TASK_PARAMS.keys()))
        self._filter_text = ""
        try:
            self.query_one("#dataset-filter", Input).value = ""
        except Exception:
            pass
        sl = self.query_one("#dataset-list", SelectionList)
        sl.clear_options()
        for task in sorted(TASK_PARAMS.keys()):
            sl.add_option(Selection(task, task, True))
        self._update_dataset_count()
        if TASK_PARAMS:
            first_task = next(iter(sorted(TASK_PARAMS.keys())))
            try:
                self.query_one("#dataset-details", Static).update(
                    self._format_task_details(first_task)
                )
            except Exception:
                pass

    def _format_task_details(self, task: str) -> str:
        """Format task parameters for the details panel."""
        params = TASK_PARAMS.get(task, {})
        lines = [f"[bold]{task}[/bold]", ""]
        for key, val in params.items():
            lines.append(f"[dim]{key}:[/dim]  {val}")
            # If this is input_files, try to count the lines in the file
            if key == "input_files" and val:
                try:
                    input_file_path = Path(val)
                    if input_file_path.exists() and input_file_path.is_file():
                        with open(input_file_path, "r") as f:
                            line_count = sum(1 for _ in f)
                        lines.append(f"[dim]  → {line_count} input files[/dim]")
                except Exception:
                    pass
        return "\n".join(lines)

    def on_selection_list_selected_changed(
        self, event: SelectionList.SelectedChanged
    ) -> None:
        if event.selection_list.id == "task-file-list":
            selected = event.selection_list.selected
            if selected:
                task_file_value = next(iter(selected))
                self._reload_tasks_from_file(Path(task_file_value))
        elif event.selection_list.id == "dataset-list":
            # Sync _selected_tasks for visible items, preserving filtered-out items
            visible_tasks = {
                task for task in TASK_PARAMS.keys() if self._filter_text in task.lower()
            }
            selected_in_list = set(event.selection_list.selected)
            self._selected_tasks = (
                self._selected_tasks - visible_tasks
            ) | selected_in_list
            self._update_dataset_count()

    def on_selection_list_selection_highlighted(
        self, event: SelectionList.SelectionHighlighted
    ) -> None:
        """Update dataset details when cursor navigates the dataset list."""
        if event.selection_list.id == "dataset-list":
            if event.selection:
                task_key = event.selection.value
                if task_key in TASK_PARAMS:
                    try:
                        self.query_one("#dataset-details", Static).update(
                            self._format_task_details(task_key)
                        )
                    except Exception:
                        pass

    def _start_processing(self) -> None:
        selected_tasks: list[str] = sorted(self._selected_tasks)

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

        # Determine default executables (fallback if not in task)
        # We'll just pick the first installed MZmine and SIRIUS
        def_mzmine = ""
        def_sirius = ""
        for version, info in SOFTWARE_PACKAGES.items():
            path = SOFTWARE_DIR / info["folder"] / info["executable"]
            if path.exists():
                if info.get("product") == "MZmine" and not def_mzmine:
                    def_mzmine = version
                elif info.get("product") == "SIRIUS" and not def_sirius:
                    def_sirius = version

        if not def_mzmine and self.query_one("#proc_mzmine", Checkbox).value:
            msg_widget.update("MZmine not found. Please download it first.")
            return
        if not def_sirius and self.query_one("#proc_sirius", Checkbox).value:
            msg_widget.update("SIRIUS not found. Please download it first.")
            return

        msg_widget.update("")

        # Collect selected spectral library file paths
        selected_library_paths: list[str] = []
        lib_idx = 0
        for lib_name, lib_urls in LIBRARY_PACKAGES.items():
            for url_idx, lib_url in enumerate(lib_urls):
                try:
                    chk = self.query_one(f"#lib-url-chk-{lib_idx}-{url_idx}", Checkbox)
                    if chk.value and not chk.disabled:
                        lib_file = find_library_file(lib_url)
                        if lib_file is not None:
                            selected_library_paths.append(str(lib_file))
                except Exception:
                    pass
            lib_idx += 1

        config = {
            # Paths
            "outdir": self.query_one("#outdir", Input).value.strip(),
            "mzmine_version": def_mzmine,
            "sirius_version": def_sirius,
            "util_mgftools_dir": DEFAULT_UTIL_MGFTOOLS,
            "util_fragextract_exe": DEFAULT_UTIL_FRAGEXTRACT,
            "meii_ref_file": DEFAULT_MEII_REF,
            "util_reorder_dir": DEFAULT_UTIL_REORDER,
            "library_paths": selected_library_paths,
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

    #pipeline-log {
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
        yield RichLog(id="pipeline-log", highlight=True, markup=False, wrap=True)
        with Horizontal(id="status-bar"):
            yield Static("Running …", id="status-label")
            yield Button(
                "Back to Config", id="back-btn", variant="default", disabled=True
            )
            yield Button("Exit", id="exit-btn", variant="error", disabled=True)

    def on_mount(self) -> None:
        log = self.query_one("#pipeline-log", RichLog)
        log.write(
            f"Untargeted Metabolomics Pipeline – started {datetime.now():%Y-%m-%d %H:%M:%S}"
        )
        log.write(f"Output: {self._config['outdir']}")
        log.write(f"Datasets: {', '.join(self._config['tasks'])}")
        log.write(
            f"Steps: MZmine={'yes' if self._config['proc_mzmine'] else 'no'}  "
            f"SIRIUS={'yes' if self._config['proc_sirius'] else 'no'}  "
            f"Archive={'yes' if self._config['archive_results'] else 'no'}"
        )
        log.write("")
        self._run_pipeline()

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _log_fn(self, text: str) -> None:
        self.app.call_from_thread(self._write_log, text)

    def _write_log(self, text: str) -> None:
        try:
            log = self.query_one("#pipeline-log", RichLog)
            log.write(text)
            log.scroll_end(animate=False)
        except Exception:
            pass

    @work(thread=True)
    def _run_pipeline(self) -> None:
        run_all(self._config, self._log_fn)
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

    TITLE = f"Untargeted Metabolomics Pipeline v{PROJECT_VERSION}"
    SUB_TITLE = "MZmine · SIRIUS"

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.push_screen(ConfigScreen())


if __name__ == "__main__":
    PipelineApp().run()
