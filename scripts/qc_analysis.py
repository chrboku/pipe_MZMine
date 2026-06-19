#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "polars",
#   "pandas",
#   "matplotlib",
#   "scikit-learn",
#   "scipy",
#   "plotnine",
#   "natsort",
#   "numpy",
#   "pyarrow",
# ]
# ///

import argparse
import json
import math
import re
import sys
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import polars as pl
import pandas as _pd
from matplotlib.backends.backend_pdf import PdfPages
from natsort import natsorted
from plotnine import (
    aes,
    element_blank,
    element_text,
    geom_line,
    geom_path,
    geom_point,
    geom_text,
    geom_tile,
    ggplot,
    labs,
    scale_color_manual,
    scale_fill_gradientn,
    scale_x_discrete,
    theme,
    theme_bw,
)
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

# Fraction of the max linkage distance used to cut feature-profile dendrogram.
# Lower value -> more, smaller, more-homogeneous clusters.
_FEATURE_CLUSTER_DIST_THRESHOLD: float = 0.10

# Maximum number of features shown per cluster page.
# When a cluster exceeds this, the features closest to the cluster centroid are kept.
_MAX_FEATURES_PER_PAGE: int = 50


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="QC group analysis for MZmine feature tables"
    )
    parser.add_argument(
        "--feature_table",
        required=True,
        help="Path to MZmine full feature table CSV",
    )
    parser.add_argument(
        "--qc_groups",
        required=True,
        help="JSON string mapping group name -> list of regex patterns",
    )
    parser.add_argument(
        "--output_dir",
        required=True,
        help="Directory where per-group PDF files will be written",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

_AREA_COL_RE = re.compile(r"^datafile:(.+):area$", re.IGNORECASE)


def _sample_name(col: str) -> str | None:
    """Return the sample name embedded in a 'datafile:NAME:area' column, else None."""
    m = _AREA_COL_RE.match(col)
    return m.group(1) if m else None


def _short_label(col: str) -> str:
    """Return a compact label: stem of the sample filename."""
    name = _sample_name(col) or col
    return Path(name).stem


def select_qc_columns(area_cols: list[str], regex_list: list[str]) -> list[str]:
    """Return columns whose sample name matches any pattern in *regex_list*."""
    compiled = [re.compile(p) for p in regex_list]
    selected = []
    for col in area_cols:
        sname = _sample_name(col)
        if sname and any(pat.search(sname) for pat in compiled):
            selected.append(col)
    return selected


def natural_sort_columns(cols: list[str]) -> list[str]:
    """Natural-sort a list of area columns by their embedded sample names."""
    return natsorted(cols, key=lambda c: _sample_name(c) or c)


# ---------------------------------------------------------------------------
# Polars helpers
# ---------------------------------------------------------------------------


def _area_numpy(df: pl.DataFrame, cols: list[str]) -> np.ndarray:
    """Return (n_samples × n_features) float64 array, NaN -> 0."""
    return df.select(cols).fill_null(0.0).to_numpy().T.astype(np.float64)


def _log1p_expr(col_name: str) -> pl.Expr:
    """Polars expression for log(x + 1) (natural log)."""
    return (pl.col(col_name).fill_null(0.0) + 1.0).log(base=math.e)


# ---------------------------------------------------------------------------
# PDF helper
# ---------------------------------------------------------------------------


def _save_plotnine(pdf: PdfPages, p, fig_w: float, fig_h: float) -> None:
    """Draw a plotnine plot and append it to *pdf*."""
    p = p + theme(figure_size=(fig_w, fig_h))
    fig = p.draw()
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Plot functions
# ---------------------------------------------------------------------------


def plot_sample_list(pdf: PdfPages, cols: list[str], group_name: str) -> None:
    """First PDF page: numbered list of QC samples in natural-sort order."""
    short_labels = [_short_label(c) for c in cols]
    lines = [f"{i + 1:>4d}.  {label}" for i, label in enumerate(short_labels)]

    fig_h = max(4.0, len(lines) * 0.22 + 2.0)
    fig, ax = plt.subplots(figsize=(10, fig_h))
    ax.axis("off")

    title = f"QC Group: {group_name}\nSamples in natural-sort order  ({len(short_labels)} total)"
    ax.text(
        0.05,
        0.98,
        title,
        transform=ax.transAxes,
        fontsize=11,
        fontweight="bold",
        va="top",
        ha="left",
    )
    ax.text(
        0.05,
        0.88,
        "\n".join(lines),
        transform=ax.transAxes,
        fontsize=8,
        va="top",
        ha="left",
        family="monospace",
    )

    fig.tight_layout()
    pdf.savefig(fig)
    plt.close(fig)


def plot_pca(pdf: PdfPages, df: pl.DataFrame, cols: list[str], group_name: str) -> None:
    """PCA scatter plot of QC samples (plotnine)."""
    mat_full = _area_numpy(df, cols)  # (n_samples, n_features)
    labels = [_short_label(c) for c in cols]
    n_samples, n_feats_total = mat_full.shape

    # Keep only features present (>0) in at least 30 % of QC samples
    feat_mask = (mat_full > 0).mean(axis=0) >= 0.30
    mat = mat_full[:, feat_mask]
    n_feats_used = int(feat_mask.sum())

    if n_samples < 2 or n_feats_used < 2:
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.text(
            0.5,
            0.5,
            f"Not enough samples ({n_samples}) or features with >=30 % presence "
            f"({n_feats_used}) for PCA.",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.set_title(f"PCA – {group_name}")
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)
        return

    try:
        scaled = StandardScaler().fit_transform(mat)
        n_comp = min(2, n_samples, n_feats_used)
        pca = PCA(n_components=n_comp)
        scores = pca.fit_transform(scaled)
        evr = pca.explained_variance_ratio_

        pc1_label = f"PC1 ({evr[0] * 100:.1f} %)"
        pc2_label = f"PC2 ({evr[1] * 100:.1f} %)" if n_comp > 1 else "PC2 (n/a)"

        pca_pl = pl.DataFrame(
            {
                "PC1": scores[:, 0].tolist(),
                "PC2": (scores[:, 1].tolist() if n_comp > 1 else [0.0] * n_samples),
                "sample": labels,
            }
        )
        pca_pd = pca_pl.to_pandas()
        # constant group column so geom_path connects all points in data order
        pca_pd["grp"] = 1

        title = (
            f"PCA – {group_name}\n"
            f"{n_feats_used} of {n_feats_total} features used "
            f"(>=30 % present in {n_samples} QC samples)\n"
            f"z-score standardised"
        )
        p = (
            ggplot(pca_pd, aes(x="PC1", y="PC2", label="sample"))
            + theme_bw()
            + geom_path(aes(group="grp"), color="#999999", size=0.5)
            + geom_point(size=3, color="steelblue")
            + geom_text(nudge_y=0.05, size=7, ha="center", va="bottom")
            + labs(title=title, x=pc1_label, y=pc2_label)
        )
        _save_plotnine(pdf, p, fig_w=10, fig_h=7)
    except Exception as exc:
        fig, ax = plt.subplots(figsize=(10, 7))
        ax.text(
            0.5,
            0.5,
            f"PCA failed: {exc}",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        ax.set_title(f"PCA – {group_name}")
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)


def plot_heatmap(
    pdf: PdfPages, df: pl.DataFrame, cols: list[str], group_name: str
) -> None:
    """Clustered heatmap: per-feature normalised, features present in all QC samples only."""
    from scipy.cluster.hierarchy import leaves_list, linkage

    arr = df.select(cols).fill_null(0.0).to_numpy().astype(np.float64)
    # (n_features, n_samples) – NaN already replaced with 0

    # --- filter: keep features detected in >= 30 % of selected QC samples ---
    # Missing values (up to 70 %) are kept as 0.
    present_mask = (arr > 0.0).mean(axis=1) >= 0.30
    arr = arr[present_mask]
    n_kept = int(present_mask.sum())
    n_orig = int(present_mask.size)
    print(
        f"    Heatmap: {n_kept} of {n_orig} features present in >=30 % of QC samples."
    )

    if n_kept == 0:
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            "No features present in >=30 % of QC samples.",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
        fig.tight_layout()
        pdf.savefig(fig)
        plt.close(fig)
        return

    # --- per-feature normalisation: divide each row by its maximum value ---
    row_max = arr.max(axis=1, keepdims=True)  # guaranteed > 0 after filter
    norm_arr = arr / row_max  # values in [0, 1]

    short_labels = [_short_label(c) for c in cols]

    # --- hierarchical clustering on normalised values ---
    try:
        Z = linkage(norm_arr, method="ward")
        cluster_order = leaves_list(Z)
    except Exception as exc:
        print(f"    Clustering failed ({exc}); falling back to variance sort.")
        cluster_order = np.argsort(norm_arr.var(axis=1))[::-1]

    chunk_size = 200
    n_chunks = max(1, math.ceil(n_kept / chunk_size))
    fig_w = max(10, len(cols) * 0.9)

    for chunk_idx in range(n_chunks):
        start = chunk_idx * chunk_size
        end = min(start + chunk_size, n_kept)
        indices = cluster_order[start:end]
        sub_norm = norm_arr[indices, :]
        n_show = len(indices)

        feature_ids = [str(i) for i in range(n_show)]
        rows: list[dict] = []
        for fi, norm_row in enumerate(sub_norm):
            for si, val in enumerate(norm_row):
                rows.append(
                    {
                        "feature": feature_ids[fi],
                        "sample": short_labels[si],
                        "norm_area": float(val),
                    }
                )
        long_pd = pl.DataFrame(rows).to_pandas()
        # Reverse categories so cluster_order[0] appears at the top of the y-axis
        long_pd["feature"] = _pd.Categorical(
            long_pd["feature"], categories=list(reversed(feature_ids)), ordered=True
        )

        p = (
            ggplot(long_pd, aes(x="sample", y="feature", fill="norm_area"))
            + theme_bw()
            + geom_tile()
            + scale_fill_gradientn(
                colors=["#440154", "#31688e", "#35b779", "#fde725"],
                limits=[0.0, 1.0],
            )
            + scale_x_discrete(limits=short_labels)
            + labs(
                title=(
                    f"Feature Heatmap – {group_name}  "
                    f"(page {chunk_idx + 1}/{n_chunks}, "
                    f"cluster positions {start + 1}–{end} of {n_kept} "
                    f"[{n_orig} total; >=30 % present; normalised per feature])"
                ),
                x="Sample",
                y="Feature (clustered)",
                fill="Norm. area",
            )
            + theme(
                axis_text_x=element_text(angle=45, hjust=1, size=7),
                axis_text_y=element_blank(),
                axis_ticks_y=element_blank(),
            )
        )
        fig_h = max(8, n_show * 0.05 + 3)
        _save_plotnine(pdf, p, fig_w=fig_w, fig_h=fig_h)


def plot_overview_lines(
    pdf: PdfPages, df: pl.DataFrame, cols: list[str], group_name: str
) -> None:
    """Overview line-plot: each feature normalized independently so its max = 100."""
    arr = df.select(cols).fill_null(0.0).to_numpy().astype(np.float64)

    # Per-feature normalization: max across QC samples -> 100; missing/zero -> 0
    row_max = arr.max(axis=1, keepdims=True)
    safe_max = np.where(row_max == 0.0, 1.0, row_max)  # avoid division by zero
    norm_arr = arr / safe_max * 100.0  # features with all-zero stay at 0

    short_labels = [_short_label(c) for c in cols]
    n_features, n_samples = norm_arr.shape

    rows: list[dict] = []
    for fi in range(n_features):
        for si in range(n_samples):
            rows.append(
                {
                    "feature": str(fi),
                    "sample": short_labels[si],
                    "rel_area": float(norm_arr[fi, si]),
                }
            )
    long_pl = pl.DataFrame(rows)

    p = (
        ggplot(long_pl.to_pandas(), aes(x="sample", y="rel_area", group="feature"))
        + theme_bw()
        + geom_line(alpha=0.12, size=0.5, color="steelblue")
        + scale_x_discrete(limits=short_labels)
        + labs(
            title=f"Feature Overview (each feature normalized, max = 100) – {group_name}",
            x="Sample (natural-sorted)",
            y="% of max peak area",
        )
        + theme(axis_text_x=element_text(angle=45, hjust=1, size=7))
    )

    fig_w = max(12, len(cols) * 0.7)
    _save_plotnine(pdf, p, fig_w=fig_w, fig_h=7)


def plot_top_feature_lines(
    pdf: PdfPages, df: pl.DataFrame, cols: list[str], group_name: str
) -> None:
    """One plot per page of dendrogram-defined feature clusters (normalised, with alpha)."""
    import matplotlib.colors as mcolors
    from scipy.cluster.hierarchy import fcluster, leaves_list, linkage

    meta_cols = [c for c in ("id", "mz", "rt") if c in df.columns]

    arr = df.select(cols).fill_null(0.0).to_numpy().astype(np.float64)
    # arr: (n_features, n_samples)
    totals = arr.sum(axis=1)
    top_order = np.argsort(totals)[::-1][:1000]
    top_arr = arr[top_order]  # (n_top, n_samples)

    feat_df = df.select(meta_cols + cols).fill_null(0.0)[top_order.tolist()]
    short_labels = [_short_label(c) for c in cols]
    col_rename = dict(zip(cols, short_labels))
    feat_df = feat_df.rename(col_rename)

    def _feat_label(row: dict) -> str:
        parts = []
        if "id" in row and row["id"] is not None:
            parts.append(f"ID {row['id']}")
        if "mz" in row and row["mz"] is not None:
            parts.append(f"m/z {float(row['mz']):.4f}")
        if "rt" in row and row["rt"] is not None:
            parts.append(f"RT {float(row['rt']):.2f}")
        return "  ·  ".join(parts) if parts else "Feature"

    feat_labels = [_feat_label(feat_df.row(i, named=True)) for i in range(len(feat_df))]
    feat_df = feat_df.with_columns(pl.Series("_label", feat_labels))

    total = len(feat_df)
    fig_w = max(12, len(cols) * 0.7)

    # Normalise each top feature to max = 1 for clustering
    row_max = top_arr.max(axis=1, keepdims=True)
    safe_max = np.where(row_max == 0.0, 1.0, row_max)
    norm_top = top_arr / safe_max  # (n_top, n_samples)

    # Build page groups: hierarchical clustering of normalised profiles
    pages: list[list[int]] = []
    if total >= 2:
        try:
            Z = linkage(norm_top, method="ward")
            max_dist = float(Z[-1, 2])
            threshold = max_dist * _FEATURE_CLUSTER_DIST_THRESHOLD
            cluster_ids = fcluster(Z, t=threshold, criterion="distance")
            order = leaves_list(Z)
            # Group consecutive dendrogram-order features that share a cluster id
            cur_cid = int(cluster_ids[order[0]])
            cur_page: list[int] = [int(order[0])]
            for idx in order[1:]:
                if int(cluster_ids[idx]) == cur_cid:
                    cur_page.append(int(idx))
                else:
                    pages.append(cur_page)
                    cur_cid = int(cluster_ids[idx])
                    cur_page = [int(idx)]
            pages.append(cur_page)
        except Exception as exc:
            print(
                f"    Feature clustering failed ({exc}); using sequential pages of 10."
            )
            pages = [list(range(i, min(i + 10, total))) for i in range(0, total, 10)]
    else:
        pages = [list(range(total))]

    # Sort clusters: fewest missing values (highest mean presence) first.
    def _mean_presence(indices: list[int]) -> float:
        return float((norm_top[indices] > 0.0).mean())

    pages.sort(key=_mean_presence, reverse=True)

    n_pages = len(pages)
    print(
        f"    Feature plots: {n_pages} clusters from {total} features "
        f"(threshold = {_FEATURE_CLUSTER_DIST_THRESHOLD:.0%} of max dist)."
    )

    for page_idx, page_indices in enumerate(pages):
        # If the cluster exceeds the per-page cap, keep only the features
        # with the smallest Euclidean distance to the cluster centroid.
        if len(page_indices) > _MAX_FEATURES_PER_PAGE:
            sub = norm_top[page_indices]  # (k, n_samples)
            centroid = sub.mean(axis=0)
            dists = np.linalg.norm(sub - centroid, axis=1)
            closest = np.argsort(dists)[:_MAX_FEATURES_PER_PAGE]
            page_indices = [page_indices[i] for i in closest]
        n = len(page_indices)
        chunk = feat_df[page_indices]
        chunk_labels = chunk["_label"].to_list()

        # Colors: tab20 for ≤20 features, hsv-cycle for larger clusters
        if n <= 20:
            colors = [mcolors.to_hex(plt.cm.tab20(i / 20)) for i in range(n)]
        else:
            colors = [mcolors.to_hex(plt.cm.hsv(i / n)) for i in range(n)]

        rows: list[dict] = []
        for fi in range(n):
            row_data = chunk.row(fi, named=True)
            vals = np.array([float(row_data.get(sl) or 0.0) for sl in short_labels])
            feat_max = vals.max()
            norm_vals = (vals / feat_max * 100.0) if feat_max > 0.0 else vals
            for si, sl in enumerate(short_labels):
                rows.append(
                    {
                        "feature": chunk_labels[fi],
                        "sample": sl,
                        "area": float(norm_vals[si]),
                    }
                )
        long_pd = pl.DataFrame(rows).to_pandas()
        long_pd["feature"] = _pd.Categorical(
            long_pd["feature"], categories=chunk_labels, ordered=True
        )

        p = (
            ggplot(long_pd, aes(x="sample", y="area", color="feature", group="feature"))
            + theme_bw()
            + geom_line(size=0.8, alpha=0.5)
            + geom_point(size=1.5, alpha=0.5)
            + scale_x_discrete(limits=short_labels)
            + scale_color_manual(values=colors)
            + labs(
                title=(
                    f"{group_name} – cluster {page_idx + 1}/{n_pages}  ({n} features)"
                ),
                x="Sample",
                y="% of max peak area",
            )
            + theme(
                axis_text_x=element_text(angle=45, hjust=1, size=6),
                legend_position="none",
            )
        )
        _save_plotnine(pdf, p, fig_w=fig_w, fig_h=7)


# ---------------------------------------------------------------------------
# Per-group analysis driver
# ---------------------------------------------------------------------------


def analyse_group(
    df: pl.DataFrame,
    area_cols: list[str],
    group_name: str,
    regex_list: list[str],
    output_dir: Path,
) -> None:
    selected = select_qc_columns(area_cols, regex_list)
    if not selected:
        print(f"    [{group_name}] No samples matched any pattern – skipping.")
        return

    selected = natural_sort_columns(selected)
    print(f"    [{group_name}] {len(selected)} samples selected.")

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", group_name)
    pdf_path = output_dir / f"qc_{safe_name}.pdf"

    with PdfPages(pdf_path) as pdf:
        # 0) Sample list (first page)
        plot_sample_list(pdf, selected, group_name)
        # a) PCA
        plot_pca(pdf, df, selected, group_name)
        # b) Heatmap
        plot_heatmap(pdf, df, selected, group_name)
        # c) Overview line-plot
        plot_overview_lines(pdf, df, selected, group_name)
        # d) Top-1000 feature line-plots (10 per page)
        plot_top_feature_lines(pdf, df, selected, group_name)

    print(f"    [{group_name}] Saved -> {pdf_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = parse_args()

    feature_table_path = Path(args.feature_table)
    if not feature_table_path.exists():
        print(f"ERROR: feature table not found: {feature_table_path}", file=sys.stderr)
        sys.exit(1)

    try:
        qc_groups: dict[str, list[str]] = json.loads(args.qc_groups)
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON for --qc_groups: {exc}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading feature table: {feature_table_path}")
    # Read everything as strings first so mixed-type columns don't cause schema errors,
    # then cast area columns to Float64.
    df = pl.read_csv(feature_table_path, infer_schema_length=0)
    area_cols = [c for c in df.columns if _AREA_COL_RE.match(c)]
    if not area_cols:
        print(
            "ERROR: no 'datafile:...:area' columns found in feature table.",
            file=sys.stderr,
        )
        sys.exit(1)

    df = df.with_columns([pl.col(c).cast(pl.Float64, strict=False) for c in area_cols])
    print(
        f"  {len(df)} features, {len(df.columns)} columns, {len(area_cols)} area columns"
    )

    for group_name, regex_list in qc_groups.items():
        analyse_group(df, area_cols, group_name, regex_list, output_dir)


if __name__ == "__main__":
    main()
