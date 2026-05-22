# /// script
# dependencies = ["polars"]
# ///

import argparse
import polars as pl


def main():
    parser = argparse.ArgumentParser(
        description="Create a DDA inclusion list from an MZmine full feature table."
    )
    parser.add_argument(
        "--input_file",
        required=True,
        help="Path to the MZmine full feature table CSV file.",
    )
    parser.add_argument(
        "--output_file",
        required=True,
        help="Path for the output inclusion list CSV file.",
    )
    parser.add_argument(
        "--rt_window",
        type=float,
        default=0.25,
        help="Retention time window in minutes (± from the feature RT). Default: 0.5 min.",
    )
    args = parser.parse_args()

    df = pl.read_csv(args.input_file)

    inclusion = df.select(
        [
            pl.col("id").alias("Compound"),
            pl.col("mz").alias("m/z"),
            (pl.col("rt") - args.rt_window).alias("t start (min)"),
            (pl.col("rt") + args.rt_window).alias("t stop (min)"),
            pl.lit("0").alias("Intensity Threshold"),
        ]
    )

    inclusion.write_csv(args.output_file)
    print(
        f"DDA inclusion list written to {args.output_file} ({len(inclusion)} entries)"
    )


if __name__ == "__main__":
    main()
