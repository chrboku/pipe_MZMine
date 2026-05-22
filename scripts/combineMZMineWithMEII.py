# /// script
# dependencies = ["polars", "pyarrow", "chardet", "numpy", "pandas", "plotnine", "matplotlib", "colorama", "bs4", "lxml"]
# ///

import argparse
import polars as pl
import chardet
import numpy as np
import plotnine as p9
import matplotlib
import matplotlib.pyplot as plt
import itertools
import pathlib
import warnings
import colorama as ca
from collections import OrderedDict
import re
from bs4 import BeautifulSoup
from time import time
import traceback


offset = int(1e8)


# adapted from plotnine package
def save_as_pdf_pages(
    plots,
    filename=None,
    path=None,
    verbose=True,
    **kwargs,
):
    from matplotlib.backends.backend_pdf import PdfPages

    # as in ggplot.save()
    fig_kwargs = {"bbox_inches": "tight"}
    fig_kwargs.update(kwargs)

    # If plots is already an iterator, this is a no-op; otherwise
    # convert a list, etc. to an iterator
    plots = iter(plots)

    # filename, depends on the object
    if filename is None:
        # Take the first element from the iterator, store it, and
        # use it to generate a file name
        peek = [next(plots)]
        plots = itertools.chain(peek, plots)
        filename = peek[0]._save_filename("pdf")

    if path:
        filename = pathlib.Path(path) / filename

    if verbose:
        warnings.warn(f"Filename: {filename}", p9.exceptions.PlotnineWarning)

    with PdfPages(filename, keep_empty=False) as pdf:
        # Re-add the first element to the iterator, if it was removed
        for plot in plots:
            if isinstance(plot, p9.ggplot):
                fig = plot.draw()
                with p9._utils.context.plot_context(plot).rc_context:
                    # Save as a page in the PDF file
                    pdf.savefig(fig, **fig_kwargs)
            elif isinstance(plot, plt.Figure) or isinstance(plot, matplotlib.table.Table):
                pdf.savefig(plot)
            else:
                raise TypeError(f"Unsupported type {type(plot)}. Must be ggplot or Figure.")


def parse_mgf_file(file_path):
    """
    Parses an MGF file and returns a dictionary containing the parsed data.

    Args:
        file_path (str): Path to the MGF file.

    Returns:
        dict: A dictionary where each key is a FEATURE_ID and the value is a list of blocks.
    """
    with open(file_path, "r") as file:
        lines = file.readlines()

    blocks = OrderedDict()
    current_block = OrderedDict()
    current_feature_id = None

    for line in lines:
        line = line.strip()
        if line == "BEGIN IONS":
            current_block = OrderedDict()

        elif line == "END IONS":
            if current_feature_id is not None:
                if current_feature_id not in blocks:
                    blocks[current_feature_id] = []
                blocks[current_feature_id].append(current_block)
            else:
                raise ValueError("No FEATURE_ID found in the block.")
            current_block = OrderedDict()
            current_feature_id = None

        elif line.startswith("Num peaks"):
            if "spectrumData" not in current_block:
                current_block["spectrumData"] = []
            current_block["spectrumData"].append(line)

        elif "=" in line:
            key, value = line.split("=", 1)
            if key == "FEATURE_ID":
                current_feature_id = value.strip()
            current_block[key.strip()] = value.strip()

        else:
            if "spectrumData" not in current_block:
                current_block["spectrumData"] = []
            current_block["spectrumData"].append(line)

    return blocks


def export_mgf_file(blocks, output_file_path):
    """
    Exports parsed MGF blocks to a new MGF file.

    Args:
        blocks (dict): Parsed MGF blocks.
        output_file_path (str): Path to the output MGF file.
    """
    with open(output_file_path, "w") as file:
        for feature_id, feature_blocks in blocks.items():
            for block in feature_blocks:
                file.write("BEGIN IONS\n")
                for key, value in block.items():
                    if key != "spectrumData":
                        file.write(f"{key}={value}\n")
                if "spectrumData" in block:
                    for line in block["spectrumData"]:
                        file.write(f"{line}\n")
                file.write("END IONS\n\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Combine Met Experiments")

    parser.add_argument(
        "--reference_file",
        type=str,
        required=True,
        help="Path to the reference file (mandatory).",
    )
    parser.add_argument(
        "--ref_cols",
        type=str,
        default="Num,MZ,RT,Ionisation_Mode,Xn",
        help="Comma-separated list of ID, MZ, RT, Ionisation_Mode, Xn column names in the reference file (defaults to MetExtractII columns).",
    )
    parser.add_argument(
        "--ref_rtunit",
        type=str,
        choices=["sec", "min"],
        default="min",
        help="Unit of the RT column in the reference file (sec or min, defaults to min).",
    )
    parser.add_argument(
        "--polarity",
        type=str,
        choices=[None, "positive", "negative"],
        default=None,
        help="Filter for the MetExtract II Ionisation_Mode column (default: None).",
    )
    parser.add_argument(
        "--query_file",
        type=str,
        required=True,
        help="Path to the query file (mandatory).",
    )
    parser.add_argument(
        "--que_cols",
        type=str,
        default="id,mz,rt",
        help="Comma-separated list of ID, MZ, and RT column names in the query file (defaults to columns of MZmine 4).",
    )
    parser.add_argument(
        "--que_rtunit",
        type=str,
        choices=["sec", "min"],
        default="min",
        help="Unit of the RT column in the query file (sec or min, defaults to min).",
    )
    parser.add_argument(
        "--additional_query_file",
        type=str,
        action="append",
        default=[],
        help="Additional query files to apply the mapping to. Can be specified multiple times.",
    )
    parser.add_argument(
        "--max_mz_difference_ppm",
        type=float,
        default=5.0,
        help="Maximum allowed ppm difference (default: 5.0).",
    )
    parser.add_argument(
        "--max_rt_difference_min",
        type=float,
        default=0.25,
        help="Maximum allowed RT difference in minutes (default: 0.25).",
    )
    parser.add_argument(
        "--plot_file",
        type=str,
        default="./mapping.pdf",
        help="Output file for generated plots (default: ./mapping.pdf).",
    )
    parser.add_argument(
        "--new_files_suffix",
        type=str,
        default="_combined",
        help="Suffix for new files (default: _combined).",
    )
    parser.add_argument(
        "--avg_rt_shift",
        type=float,
        default=0.0,
        help="Constant average RT shift to apply to the reference data (default: 0.0).",
    )
    parser.add_argument(
        "--avg_mz_ppm_shift",
        type=float,
        default=0.0,
        help="Constant average m/z shift in ppm to apply to the reference data (default: 0.0).",
    )
    parser.add_argument(
        "--remove_non_matched",
        action="store_true",
        default=False,
        help="Remove non-matched features from output files (default: False).",
    )

    return parser.parse_args()


def import_file(file_path):
    """
    Imports a file and determines the separator dynamically.

    Args:
        file_path (str): Path to the file to import.

    Returns:
        pl.DataFrame: Imported DataFrame.
    """
    # Detect file encoding
    with open(file_path, "rb") as f:
        raw_data = f.read()
        result = chardet.detect(raw_data)
        encoding = result["encoding"]
        print(f"   - Detected encoding: {encoding}")

    # Test different delimiters iteratively
    for sep in [",", "\t", " "]:
        try:
            df = pl.read_csv(
                file_path,
                separator=sep,
                #encoding=encoding,
                comment_prefix="#",
                infer_schema=True,
                infer_schema_length=10000,
            )
            sepPrint = sep if sep != "\t" else "tab"
            print(f"   - Detected separator: {sepPrint} with {len(df.columns)} columns")
            return df
        except Exception:
            continue
    raise ValueError(f"Failed to determine separator for file: {file_path}")


def extract_standardized_columns(df, id_column, mz_column, rt_column, rt_unit, ionMode_column=None, xn_column=None):
    """
    Extracts and standardizes the ID, MZ, and RT columns from a DataFrame.
    Converts RT to minutes if necessary.

    Args:
        df (pl.DataFrame): The input DataFrame.
        id_column (str): Name of the ID column.
        mz_column (str): Name of the MZ column.
        rt_column (str): Name of the RT column.
        rt_unit (str): Unit of the RT column ("sec" or "min").

    Returns:
        pl.DataFrame: DataFrame with standardized columns: ID, MZ, RT (in minutes).
    """
    try:
        result = df.select(
            [
                pl.col(id_column).alias("ID"),
                pl.col(mz_column).alias("MZ"),
                pl.col(rt_column).alias("RT"),
            ]
        )

        if ionMode_column is not None:
            x = df.select(pl.col(ionMode_column).alias("Ionisation_Mode"))
            result = pl.concat([result, x], how="horizontal")
        if xn_column is not None:
            x = df.select(pl.col(xn_column).alias("Xn"))
            result = pl.concat([result, x], how="horizontal")
        # Convert MZ and RT columns to numeric values
        result = result.with_columns(
            [pl.col("MZ").cast(str).str.strip_chars().cast(pl.Float64), pl.col("RT").cast(str).str.strip_chars().cast(pl.Float64)]
        )
    except Exception as e:
        print(f"Error: Could not find one or more columns: {id_column}, {mz_column}, {rt_column}")
        print("Available columns:", df.columns)
        print("Error message:", e)
        traceback.print_exc()
        exit(1)
    if rt_unit == "sec":
        result = result.with_columns((pl.col("RT") / 60).alias("RT"))
    return result


def find_mapping(
    reference_df,
    query_df,
    max_mz_difference_ppm,
    max_rt_difference_min,
    weight_mz=200.0,
    weight_rt=1.0,
):
    """
    Processes the reference and query DataFrames according to the specified parameters.

    Args:
        reference_df (pl.DataFrame): The reference DataFrame.
        query_df (pl.DataFrame): The query DataFrame.
        max_mz_difference_ppm (float): Maximum allowed ppm difference.
        max_rt_difference_min (float): Maximum allowed RT difference in minutes.
        weight_mz (float): Weight for m/z differences (default: 200.0).
        weight_rt (float): Weight for RT differences (default: 1.0).

    Returns:
        None
    """
    print("Processing data with the following parameters:")
    print(f"   - max_mz_difference_ppm: {max_mz_difference_ppm}")
    print(f"   - max_rt_difference_min: {max_rt_difference_min}")
    print(f"   - weight_mz: {weight_mz}")
    print(f"   - weight_rt: {weight_rt}")

    # For each query, find the best matching reference within the given tolerances

    # Convert to numpy for efficient computation
    ref_id = reference_df["ID"].to_numpy()
    ref_mz = reference_df["MZ"].to_numpy()
    ref_rt = reference_df["RT"].to_numpy()
    ref_ionMode = reference_df["Ionisation_Mode"].to_numpy()
    ref_Xn = reference_df["Xn"].to_numpy()

    que_id = query_df["ID"].to_numpy()
    que_mz = query_df["MZ"].to_numpy()
    que_rt = query_df["RT"].to_numpy()

    print(ca.Fore.BLUE + f"Finding matches for {len(que_id)} query features against {len(ref_id)} reference features..." + ca.Style.RESET_ALL)

    mappings = []

    non_unique_matches = 0
    resolved_non_unique_matches = 0
    no_matches = 0

    for i in range(len(que_mz)):
        mz = que_mz[i]
        rt = que_rt[i]
        qid = que_id[i]

        # Calculate ppm difference and RT difference for all references
        ppm_diff = (ref_mz - mz) / ref_mz * 1e6
        rt_diff = ref_rt - rt

        # Mask for candidates within tolerances
        mask = np.where((np.abs(ppm_diff) <= max_mz_difference_ppm) & (np.abs(rt_diff) <= max_rt_difference_min))[0]
        if len(mask) < 1:
            no_matches += 1

            continue

        # Compute score for candidates
        score = (np.abs(ppm_diff[mask]) / weight_mz) ** 2 + (np.abs(rt_diff[mask]) / weight_rt) ** 2
        if len(score) > 1:
            ## TODO implement fix for inconclusive matches
            non_unique_matches += 1
            print(ca.Fore.YELLOW + f"   - WARNING: Found {len(mask)} candidates for query {qid} (mz: {que_mz[i]}, rt: {que_rt[i]} min), these are:")
            for j in range(len(mask)):
                print(
                    f"     . Num {ref_id[mask[j]]} (mz: {ref_mz[mask[j]]:.4f}, rt: {ref_rt[mask[j]]:.2f} min, IonMode: {ref_ionMode[mask[j]]}, Xn: {ref_Xn[mask[j]]})"
                )

            min_ppm = np.min(ppm_diff[mask])
            min_rt = np.min(rt_diff[mask])
            max_ppm = np.max(ppm_diff[mask])
            max_rt = np.max(rt_diff[mask])
            if max_ppm - min_ppm <= 1 and max_rt - min_rt <= 0.02:
                print(ca.Fore.YELLOW + "     - MZ and RTs of matched are similar, selecting the one with the highest Xn count")

                # Select the match with the highest Xn count
                mask = np.array([mask[np.argmax(ref_Xn[mask])]])

                # Update mask and score to reflect the selected match
                score = (np.abs(ppm_diff[mask]) / weight_mz) ** 2 + (np.abs(rt_diff[mask]) / weight_rt) ** 2
                print(
                    f"        -> Num {ref_id[mask[0]]} (mz: {ref_mz[mask[0]]:.4f}, rt: {ref_rt[mask[0]]:.2f} min, IonMode: {ref_ionMode[mask[0]]}, Xn: {ref_Xn[mask[0]]})"
                )

                resolved_non_unique_matches += 1
                print("")

            print(ca.Style.RESET_ALL, end="")

        if len(mask) == 1:
            ref_idx = mask[0]

            mappings.append(
                {
                    "query_id": qid,
                    "reference_id": ref_id[ref_idx],
                    "ppm_diff": ppm_diff[ref_idx],
                    "rt_diff": rt_diff[ref_idx],
                    "score": score[0],
                    "query_mz": mz,
                    "query_rt": rt,
                    "reference_mz": ref_mz[ref_idx],
                    "reference_rt": ref_rt[ref_idx],
                }
            )
            print(ca.Fore.GREEN + f'   - Mapped query {qid} (mz: {que_mz[i]}, rt: {que_rt[i]} min) to reference {ref_id[ref_idx]} (mz: {ref_mz[ref_idx]:.4f}, rt: {ref_rt[ref_idx]:.2f} min), ppm diff: {ppm_diff[ref_idx]:.2f} ppm, rt diff: {rt_diff[ref_idx]:.2f} min, score: {score[0]:.2f}' + ca.Style.RESET_ALL + "\n")
        else:
            print(ca.Fore.RED + "     - Could not resolve ambiguity, ignoring query feature for now" + ca.Style.RESET_ALL + "\n")

    # Print mapping summary
    print(f"   - Found {no_matches} features with no match in the reference")
    print(f"   - Found {len(mappings)} matches between {query_df.shape[0]} compounds and {reference_df.shape[0]} reference features.")
    if non_unique_matches > 0:
        print(ca.Fore.RED + f"   - WARNING: Found {non_unique_matches} queries with multiple matches.")
        if resolved_non_unique_matches > 0:
            print(
                ca.Fore.YELLOW
                + f"      - Resolved {resolved_non_unique_matches} non unique matches via selecting the most likely representative reference feature"
            )
        print(ca.Style.RESET_ALL)

    # Count how many entries in mappings have the same reference_id
    reference_id_counts = {}
    for mapping in mappings:
        ref_id = mapping["reference_id"]
        reference_id_counts[ref_id] = reference_id_counts.get(ref_id, 0) + 1

    print("Reference ID counts:")
    remove_ids = []
    for ref_id, count in sorted(reference_id_counts.items(), key=lambda x: x[1], reverse=True):
        if count > 1:
            remove_ids.append(ref_id)
    print(ca.Fore.RED + f"Removing {len(remove_ids)} duplicate reference IDs..." + ca.Style.RESET_ALL)
    # Print the reference features that are in remove_ids
    for ref_id in remove_ids:
        print(
            ca.Fore.RED
            + f"Reference ID {ref_id} is duplicated and will be removed. MZ: {reference_df.filter(pl.col('ID') == ref_id)['MZ'][0]}, RT: {reference_df.filter(pl.col('ID') == ref_id)['RT'][0]}"
            + ca.Style.RESET_ALL
        )
    mappings = [mapping for mapping in mappings if mapping["reference_id"] not in remove_ids]

    # Convert mappings to DataFrame for plotting
    mappings_df = pl.DataFrame(mappings)

    if not mappings_df.is_empty():
        plots = []
        # Calculate median and mean values
        rt_median = mappings_df["rt_diff"].median()
        rt_mean = mappings_df["rt_diff"].mean()
        ppm_median = mappings_df["ppm_diff"].median()
        ppm_mean = mappings_df["ppm_diff"].mean()

        p = (
            p9.ggplot(mappings_df, p9.aes(x="rt_diff", y="ppm_diff"))
            + p9.geom_point(alpha=0.6)
            + p9.geom_vline(xintercept=rt_median, color="red", linetype="dashed", alpha=0.7)
            + p9.geom_vline(xintercept=rt_mean, color="blue", linetype="dashed", alpha=0.7)
            + p9.geom_hline(yintercept=ppm_median, color="red", linetype="dashed", alpha=0.7)
            + p9.geom_hline(yintercept=ppm_mean, color="blue", linetype="dashed", alpha=0.7)
            + p9.theme_bw()
            + p9.labs(
                title="Matched Features: RT vs PPM Difference",
                subtitle=f"lines show the median (red) and mean (blue) values\nMZ: {ppm_median:.2f} / {ppm_mean:.2f} ppm, RT: {rt_median:.2f} / {rt_mean:.2f} min",
                x="RT Difference (min)",
                y="PPM Difference",
            )
            + p9.scale_x_continuous()
            + p9.scale_y_continuous()
        )
        plots.append(p)

        # Plot all features of reference and query in different facets
        ref_plot_df = reference_df.select(
            [
                pl.col("ID").cast(pl.Utf8).alias("ID"),
                pl.col("MZ"),
                pl.col("RT"),
                pl.col("ID").is_in(mappings_df["reference_id"]).alias("Mapped"),
                pl.lit("Reference").alias("Type"),
            ]
        )
        que_plot_df = query_df.select(
            [
                pl.col("ID").cast(pl.Utf8).alias("ID"),
                pl.col("MZ"),
                pl.col("RT"),
                pl.col("ID").is_in(mappings_df["query_id"]).alias("Mapped"),
                pl.lit("Query").alias("Type"),
            ]
        )
        all_features_df = pl.concat([ref_plot_df, que_plot_df], how="vertical")

        p_all = (
            p9.ggplot(all_features_df, p9.aes(x="RT", y="MZ", color="Mapped"))
            + p9.geom_point(alpha=0.5)
            + p9.facet_wrap("~Type")
            + p9.theme_bw()
            + p9.labs(title="All Features: Reference vs Query", x="RT (min)", y="MZ")
        )
        plots.append(p_all)

        if len(plots) > 0:
            print("   - Generating plot...")
            save_as_pdf_pages(plots, filename=args.plot_file, path=".")
            # Calculate and print statistics for RT and MZ columns in mappings_df
            rt_stats = mappings_df.select(
                [
                    pl.col("rt_diff").median().alias("RT Difference Median"),
                    pl.col("rt_diff").mean().alias("RT Difference Mean"),
                    pl.col("rt_diff").quantile(0.10).alias("RT Difference P10"),
                    pl.col("rt_diff").quantile(0.25).alias("RT Difference P25"),
                    pl.col("rt_diff").quantile(0.75).alias("RT Difference P75"),
                    pl.col("rt_diff").quantile(0.90).alias("RT Difference P90"),
                ]
            )
            mz_stats = mappings_df.select(
                [
                    pl.col("ppm_diff").median().alias("PPM Difference Median"),
                    pl.col("ppm_diff").mean().alias("PPM Difference Mean"),
                    pl.col("ppm_diff").quantile(0.10).alias("PPM Difference P10"),
                    pl.col("ppm_diff").quantile(0.25).alias("PPM Difference P25"),
                    pl.col("ppm_diff").quantile(0.75).alias("PPM Difference P75"),
                    pl.col("ppm_diff").quantile(0.90).alias("PPM Difference P90"),
                ]
            )

        print("The average shifts are: ")
        print("RT Statistics:")
        print(rt_stats)
        print("MZ Statistics:")
        print(mz_stats)

    else:
        print("   - No matches found, plot not generated.")

    return mappings_df


if __name__ == "__main__":
    tic = time()
    args = parse_args()

    print("----------------------------------------------------------------")
    print("Tool to combine Metabolomics Experiments")
    print("----------------------------------------------------------------\n")

    print("Arguments:")
    print("Reference file:", args.reference_file)
    print("   - Columns:", args.ref_cols)
    print("   - RT unit:", args.ref_rtunit)
    print("   - Polarity filter:", args.polarity)
    print("Query file:", args.query_file)
    print("   - Columns:", args.que_cols)
    print("   - RT unit:", args.que_rtunit)
    print("Additional query files:", args.additional_query_file)
    print("Max ppm difference:", args.max_mz_difference_ppm)
    print("Max RT difference (min):", args.max_rt_difference_min)
    print("Plot file:", args.plot_file)
    print("New files suffix:", args.new_files_suffix)
    print("----------------------------------------------------------------")
    print("Shifts applied to reference (user-provided):")
    print("   - RT:", args.avg_rt_shift)
    print("   - MZ:", args.avg_mz_ppm_shift)
    print("----------------------------------------------------------------\n")

    print("Loading data...")
    print("Loading reference file...")
    ref_id, ref_mz, ref_rt, ref_ionMode, ref_Xn = args.ref_cols.split(",")
    reference_df = import_file(args.reference_file)
    print(f"   - has {reference_df.shape[0]} rows and {reference_df.shape[1]} columns.")
    # Check if ref_id column is string and starts with "FP", then clean and convert
    if reference_df[ref_id].dtype == pl.Utf8:
        sample_values = reference_df[ref_id].head(10).to_list()
        if all(str(val).startswith("FP") for val in sample_values if val is not None):
            print(f"   - Detected 'FP' prefix in {ref_id} column, removing prefix and converting to integer (e.g., FP1 -> 1).")
            reference_df = reference_df.with_columns(pl.col(ref_id).str.extract(r"FP([0-9]*)", 1).cast(pl.Int64).alias(ref_id))
        elif all(str(val).startswith("MET") for val in sample_values if val is not None):
            print(f"   - Detected 'MET' prefix in {ref_id} column, removing prefix and converting to integer (e.g., MET1 -> 1).")
            reference_df = reference_df.with_columns(pl.col(ref_id).str.extract(r"MET[0-9]*_FP([0-9]*)", 1).cast(pl.Int64).alias(ref_id))
    if args.polarity is not None:
        reference_df = reference_df.filter(pl.col("Ionisation_Mode") == ("+" if args.polarity == "positive" else "-"))
        print(f"   - Filtered for polarity {args.polarity}, {reference_df.shape[0]} rows remaining.")
    reference_df_sta = extract_standardized_columns(
        reference_df,
        ref_id,
        ref_mz,
        ref_rt,
        args.ref_rtunit,
        ref_ionMode,
        ref_Xn,
    )
    print(f"   - has {reference_df.shape[0]} rows and {reference_df.shape[1]} columns.")

    print("  - Applying average shifts...")
    reference_df_sta = reference_df_sta.with_columns(
        (pl.col("RT") + args.avg_rt_shift).alias("RT"),
        (pl.col("MZ") * (1.0 + args.avg_mz_ppm_shift / 1.0e6)).alias("MZ"),
    )
    print("----------------------------------------------------------------\n")

    print("Loading query file...")
    query_df = import_file(args.query_file)
    que_id, que_mz, que_rt = args.que_cols.split(",")
    query_df_sta = extract_standardized_columns(
        query_df,
        que_id,
        que_mz,
        que_rt,
        args.que_rtunit,
    )
    print(f"  - has {query_df.shape[0]} rows and {query_df.shape[1]} columns.")
    print("----------------------------------------------------------------\n")

    print("Finding mapping...")
    mappings_df = find_mapping(
        reference_df_sta,
        query_df_sta,
        args.max_mz_difference_ppm,
        args.max_rt_difference_min,
    )
    print(f"   - Found {len(mappings_df)} mapped features.")

    # Update IDs in query_df_sta: for IDs found in mappings_df["query_id"], set to corresponding mappings_df["reference_id"]
    mapping_dict = dict(zip([i for i in mappings_df["query_id"].to_list()], mappings_df["reference_id"].to_list()))

    print("----------------------------------------------------------------\n")

    print("Updating files...")
    que_id_int_expr = pl.coalesce(
        [
            pl.col(que_id).cast(pl.Int64, strict=False),
            pl.col(que_id).cast(pl.Utf8).str.extract(r"([0-9]+)$", 1).cast(pl.Int64, strict=False),
        ]
    )
    # add old mzmine and metextract II id column
    query_df = query_df.with_columns(pl.col(que_id).alias("id_mzmine4"))
    query_df = query_df.with_columns(pl.col(que_id).replace_strict(mapping_dict, default=None).cast(pl.Utf8).fill_null("").alias("id_metextractII"))
    # update id column: matched -> reference id as string, non-matched -> offset+original as string
    query_df = query_df.with_columns(
        pl.coalesce(
            [
                pl.col(que_id).replace_strict(mapping_dict, default=None).cast(pl.Utf8),
                (pl.lit(offset) + que_id_int_expr).cast(pl.Utf8),
            ]
        ).alias(que_id)
    )
    # generate label column
    query_df = query_df.with_columns(
        (
            "U" 
            + pl.col(que_id).cast(str)
            + "\n"
            + pl.col(que_rt).cast(pl.Float64).round(2).cast(str)
            + "min\nmz"
            + pl.col(que_mz).cast(pl.Float64).round(4).cast(str)
        ).alias("label")
    )
    # Reorder columns so all columns starting with 'id' (case-insensitive) are at the beginning
    id_cols = [col for col in query_df.columns if col.lower().startswith("id") or col.lower() == "label"]
    other_cols = [col for col in query_df.columns if col not in id_cols]
    query_df = query_df.select(id_cols + other_cols)
    # Write the updated DataFrame to a new file
    query_file_path = pathlib.Path(args.query_file)
    query_file_path = query_file_path.with_name(query_file_path.stem + args.new_files_suffix + ".tsv")
    query_df.write_csv(query_file_path, separator="\t")
    print(f"   - Updated query file saved as: {query_file_path}")

    # Export a matched-only table.
    matched_query_df = query_df.filter(pl.col("id_metextractII") != "")
    matched_query_file_path = pathlib.Path(args.query_file)
    matched_query_file_path = matched_query_file_path.with_name(
        matched_query_file_path.stem + args.new_files_suffix + "_matched.tsv"
    )
    matched_query_df.write_csv(matched_query_file_path, separator="\t")
    print(f"   - Matched-only query file saved as: {matched_query_file_path}")

    for file in args.additional_query_file:
        print(f"   - Processing additional query file: {file}")

        if file.endswith(".tsv") or file.endswith(".txt") or file.endswith(".csv"):
            print("     table file detected.")
            additional_query_df = import_file(file)
            print(f"     has {additional_query_df.shape[0]} rows and {additional_query_df.shape[1]} columns.")
            additional_query_df = additional_query_df.with_columns(pl.col(que_id).alias("id_mzmine4"))
            additional_query_df = additional_query_df.with_columns(
                pl.col(que_id).replace_strict(mapping_dict, default=None).cast(pl.Utf8).fill_null("").alias("id_metextractII")
            )
            # update id column: matched -> reference id as string, non-matched -> offset+original as string
            additional_que_id_int_expr = pl.coalesce(
                [
                    pl.col(que_id).cast(pl.Int64, strict=False),
                    pl.col(que_id).cast(pl.Utf8).str.extract(r"([0-9]+)$", 1).cast(pl.Int64, strict=False),
                ]
            )
            additional_query_df = additional_query_df.with_columns(
                pl.coalesce(
                    [
                        pl.col(que_id).replace_strict(mapping_dict, default=None).cast(pl.Utf8),
                        (pl.lit(offset) + additional_que_id_int_expr).cast(pl.Utf8),
                    ]
                ).alias(que_id)
            )
            # Reorder columns so all columns starting with 'id' (case-insensitive) are at the beginning
            id_cols = [col for col in additional_query_df.columns if col.lower().startswith("id") or col.lower() == "label"]
            other_cols = [col for col in additional_query_df.columns if col not in id_cols]
            additional_query_df = additional_query_df.select(id_cols + other_cols)
            
            # Write the combined file (all features)
            query_file_path = pathlib.Path(file)
            query_file_path = query_file_path.with_name(query_file_path.stem + args.new_files_suffix + ".tsv")
            additional_query_df.write_csv(query_file_path, separator="\t")
            print(f"     - Updated combined file saved as: {query_file_path}")
            
            # Write the matched-only file
            matched_additional_query_df = additional_query_df.filter(pl.col("id_metextractII") != "")
            matched_query_file_path = pathlib.Path(file)
            matched_query_file_path = matched_query_file_path.with_name(
                matched_query_file_path.stem + args.new_files_suffix + "_matched.tsv"
            )
            matched_additional_query_df.write_csv(matched_query_file_path, separator="\t")
            print(f"     - Matched-only file saved as: {matched_query_file_path}")

        elif file.endswith(".mgf"):
            print("     mgf file detected.")
            blocks = parse_mgf_file(file)
            num_blocks = sum(len(feature_blocks) for feature_blocks in blocks.values())
            print(f"     Number of parsed entries: {num_blocks}")

            ## Update blocks with new IDs - for combined file (all features)
            newMGF_combined = OrderedDict()
            newMGF_matched = OrderedDict()
            for k in blocks.keys():
                x = blocks[k]
                if k in mapping_dict:
                    for i in range(len(x)):
                        if "FEATURE_ID" in x[i]:
                            x[i]["FEATURE_ID"] = mapping_dict[k]
                    newMGF_combined[mapping_dict[k]] = x
                    newMGF_matched[mapping_dict[k]] = x
                else:
                    for i in range(len(x)):
                        if "FEATURE_ID" in x[i]:
                            x[i]["FEATURE_ID"] = k
                    newMGF_combined[k] = x

            # Export combined file
            combined_mgf_path = file.replace(".mgf", args.new_files_suffix + ".mgf")
            export_mgf_file(newMGF_combined, combined_mgf_path)
            print(f"     - Combined file saved as: {combined_mgf_path}")
            
            # Export matched-only file
            matched_mgf_path = file.replace(".mgf", args.new_files_suffix + "_matched.mgf")
            export_mgf_file(newMGF_matched, matched_mgf_path)
            print(f"     - Matched-only file saved as: {matched_mgf_path}")

        elif file.endswith(".graphml"):
            print("     graphml file detected.")
            # Parse the graphml file
            with open(file, "r") as file:
                soup = BeautifulSoup(file, "xml")

            # Create two copies for combined and matched-only versions
            from copy import deepcopy
            soup_matched = deepcopy(soup)

            # Update the combined file (all nodes and edges)
            for node in soup.find_all("node"):
                node_id = offset + int(node["id"])
                add = mapping_dict.get(node_id, "")
                if add != "":
                    new_node_id = str(node_id) + add
                    node["id"] = new_node_id

            # Update the target and source attributes in the edges
            for edge in soup.find_all("edge"):
                source = edge["source"]
                target = edge["target"]
                try:
                    source = int(source)
                except ValueError:
                    pass
                try:
                    target = int(target)
                except ValueError:
                    pass
                add_source = mapping_dict.get(source, "")
                add_target = mapping_dict.get(target, "")
                if add_source != "":
                    edge["source"] = str(source) + str(add_source)
                if add_target != "":
                    edge["target"] = str(target) + str(add_target)

            # Write the combined graphml to a new file
            output_graphml_path = file.replace(".graphml", args.new_files_suffix + ".graphml")
            with open(output_graphml_path, "w") as f:
                f.write(re.sub("<binary>\\s*(.*)\\s*</binary>", "<binary>\\1</binary>", soup.prettify().replace("\r", "")))
            print(f"     - Combined file saved as: {output_graphml_path}")

            # Update the matched-only file (only mapped nodes and their connected edges)
            mapped_node_ids = set(mapping_dict.keys())
            
            # Remove unmapped nodes from matched version
            for node in soup_matched.find_all("node"):
                node_id = offset + int(node["id"])
                if node_id not in mapped_node_ids:
                    node.decompose()

            # Update remaining nodes in matched version
            for node in soup_matched.find_all("node"):
                node_id = offset + int(node["id"])
                add = mapping_dict.get(node_id, "")
                if add != "":
                    new_node_id = str(node_id) + str(add)
                    node["id"] = new_node_id

            # Remove edges that reference removed nodes and update remaining edges
            for edge in soup_matched.find_all("edge"):
                source = edge["source"]
                target = edge["target"]
                try:
                    source_int = int(source)
                except ValueError:
                    source_int = None
                try:
                    target_int = int(target)
                except ValueError:
                    target_int = None
                
                # Check if both source and target are mapped
                source_mapped = (source_int + offset in mapped_node_ids) if source_int is not None else False
                target_mapped = (target_int + offset in mapped_node_ids) if target_int is not None else False
                
                if source_mapped and target_mapped:
                    add_source = mapping_dict.get(source_int + offset, "")
                    add_target = mapping_dict.get(target_int + offset, "")
                    if add_source != "":
                        edge["source"] = str(source_int + offset) + str(add_source)
                    if add_target != "":
                        edge["target"] = str(target_int + offset) + str(add_target)
                else:
                    edge.decompose()

            # Write the matched-only graphml to a new file
            output_graphml_matched_path = file.replace(".graphml", args.new_files_suffix + "_matched.graphml")
            with open(output_graphml_matched_path, "w") as f:
                f.write(re.sub("<binary>\\s*(.*)\\s*</binary>", "<binary>\\1</binary>", soup_matched.prettify().replace("\r", "")))
            print(f"     - Matched-only file saved as: {output_graphml_matched_path}")
    print("----------------------------------------------------------------\n")

    toc = time()
    print(f"Total time: {toc - tic:.2f} seconds")
    print("")
