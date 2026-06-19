# /// script
# dependencies = ["polars", "pyarrow", "chardet", "numpy", "pandas", "plotnine", "matplotlib", "colorama", "bs4", "lxml"]
# ///

import argparse
import shutil
from collections import OrderedDict
from pathlib import Path
import polars as pl
import re
from bs4 import BeautifulSoup


def parse_file_arg(value: str) -> tuple[str, str]:
    """Parse a file argument in 'input$output' format.

    If no '$' is present, input and output are the same path.
    """
    if "$" in value:
        parts = value.split("$", 1)
        return parts[0], parts[1]
    return value, value


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

    blocks = []
    current_block = OrderedDict()

    for line in lines:
        line = line.strip()
        if line == "BEGIN IONS":
            current_block = OrderedDict()

        elif line == "END IONS":
            blocks.append(current_block)
            current_block = OrderedDict()

        elif line.startswith("Num peaks"):
            if "___spectrumData" not in current_block:
                current_block["___spectrumData"] = []
            current_block["___spectrumData"].append(line)

        elif "=" in line:
            key, value = line.split("=", 1)
            current_block[key.strip()] = value.strip()

        else:
            if "___spectrumData" not in current_block:
                current_block["___spectrumData"] = []
            current_block["___spectrumData"].append(line)

    return blocks


def export_mgf_file(blocks, output_file_path):
    """
    Exports parsed MGF blocks to a new MGF file.

    Args:
        blocks (dict): Parsed MGF blocks.
        output_file_path (str): Path to the output MGF file.
    """
    with open(output_file_path, "w") as file:
        for block in blocks:
            file.write("BEGIN IONS\n")
            for key, value in block.items():
                if key != "___spectrumData":
                    file.write(f"{key}={value}\n")
            if "___spectrumData" in block:
                for line in block["___spectrumData"]:
                    file.write(f"{line}\n")
            file.write("END IONS\n\n")


parser = argparse.ArgumentParser(
    description="Process feature table, quantification table, graphml and MGF files."
)
parser.add_argument(
    "--full_feature_table", type=str, help="Path to the full feature table CSV file"
)
parser.add_argument("--annotations", type=str, help="Path to the annotations CSV file")
parser.add_argument(
    "--iimn_fbmn_quant", type=str, help="Path to the IIMN FBMN quantification CSV file"
)
parser.add_argument("--graphml", type=str, help="Path to the graphml file")
parser.add_argument("--mgf", type=str, nargs="+", help="Path to one or more MGF files")

args = parser.parse_args()

full_feature_table = args.full_feature_table
annotations = args.annotations
iimn_fbmn_quant = args.iimn_fbmn_quant
graphml = args.graphml
mgfs = args.mgf

# Parse the input and output file paths
full_feature_table_in, full_feature_table_out = parse_file_arg(full_feature_table)
annotations_in, annotations_out = parse_file_arg(annotations)
iimn_fbmn_quant_in, iimn_fbmn_quant_out = parse_file_arg(iimn_fbmn_quant)
graphml_in, graphml_out = parse_file_arg(graphml)
mgf_pairs = [parse_file_arg(mgf) for mgf in mgfs]


## show brief overview of the parameters
print(f"  - Full Feature Table: {full_feature_table_in} -> {full_feature_table_out}")
print(f"  - Annotations: {annotations_in} -> {annotations_out}")
print(f"  - IIMN FBMN Quantification: {iimn_fbmn_quant_in} -> {iimn_fbmn_quant_out}")
print(f"  - GraphML: {graphml_in} -> {graphml_out}")
print(f"  - MGFs: {mgf_pairs}")


# Copy each input to its output before any modification
def _copy_if_different(src: str, dst: str) -> None:
    if Path(src).resolve() != Path(dst).resolve():
        shutil.copy2(src, dst)


_copy_if_different(full_feature_table_in, full_feature_table_out)
_copy_if_different(annotations_in, annotations_out)
_copy_if_different(iimn_fbmn_quant_in, iimn_fbmn_quant_out)
_copy_if_different(graphml_in, graphml_out)
for mgf_in, mgf_out in mgf_pairs:
    _copy_if_different(mgf_in, mgf_out)

# Read the full feature table CSV file (from output copy)
full_feature_table_df = pl.read_csv(
    full_feature_table_out, separator=",", has_header=True, infer_schema_length=None
)

# Read the annotations CSV file (from output copy)
df_annotations = pl.read_csv(
    annotations_out, separator=",", has_header=True, infer_schema_length=None
)

# Read the IIMN FBMN quantification CSV file (from output copy)
iimn_fbmn_quant_df = pl.read_csv(
    iimn_fbmn_quant_out,
    separator=",",
    has_header=True,
    truncate_ragged_lines=True,
    infer_schema_length=None,
)

mgfs_data = {}
for _mgf_in, mgf_out in mgf_pairs:
    mgfs_data[mgf_out] = parse_mgf_file(mgf_out)

print("\nNumber of features:", len(full_feature_table_df))

## generate the new IDs and the ID mapping
IDmapping = {}


def makeSafe(name):
    name = re.sub(
        r"[^a-zA-Z0-9_\\-\\.]", "_", name
    )  # Replace non-alphanumeric characters with underscores
    return name


def makeAdd(row):
    add = f"__mz{row['mz'][0]}__rt{row['rt'][0]}"
    if "compound_db_identity:compound_name" in row.columns:
        name = row["compound_db_identity:compound_name"][0]
        if name is not None and name != "":
            add = add + "__" + str(name)
    return makeSafe(add)


for i in range(len(full_feature_table_df)):
    add = ""
    id = full_feature_table_df["id"][i]
    add = makeAdd(full_feature_table_df[i])
    # if add is not None and add != "":
    #    print(f"   - {id}, renaming to {id}{add}")
    # else:
    #    print(f"   - {id}, not adding anything")

    IDmapping[full_feature_table_df["id"][i]] = add

# Update the 'id' column in the full_feature_table_df
full_feature_table_df = full_feature_table_df.with_columns(
    pl.col("id").cast(pl.Utf8)
    + pl.col("id").map_elements(lambda id: IDmapping.get(id, ""), return_dtype=pl.Utf8)
)

# Update the 'id' column in the iimn_fbmn_quant_df
iimn_fbmn_quant_df = iimn_fbmn_quant_df.with_columns(
    pl.col("row ID").cast(pl.Utf8)
    + pl.col("row ID").map_elements(
        lambda id: IDmapping.get(id, ""), return_dtype=pl.Utf8
    )
)

# Update the 'id' column in the df_annotations
df_annotations = df_annotations.with_columns(
    pl.col("id").cast(pl.Utf8)
    + pl.col("id").map_elements(lambda id: IDmapping.get(id, ""), return_dtype=pl.Utf8)
)

# Update the FEATURE_ID in the MGF files
for mgf, blocks in mgfs_data.items():
    print(f"Updating FEATURE_ID in {mgf} (output)")
    for block in blocks:
        if "FEATURE_ID" in block:
            try:
                feature_id = int(block["FEATURE_ID"])
            except ValueError:
                feature_id = block["FEATURE_ID"]
            add = IDmapping.get(feature_id, "")
            block["FEATURE_ID"] = str(feature_id) + add
            # print(f"Updated FEATURE_ID: {block['FEATURE_ID']}")

# Write the updated DataFrames back to the output files
full_feature_table_df.write_csv(full_feature_table_out)
print(f"Exported updated Full Feature Table to {full_feature_table_out}")

iimn_fbmn_quant_df.write_csv(iimn_fbmn_quant_out)
print(f"Exported updated IIMN FBMN Quantification to {iimn_fbmn_quant_out}")

df_annotations.write_csv(annotations_out)
print(f"Exported updated Annotations to {annotations_out}")

for mgf_out, blocks in mgfs_data.items():
    export_mgf_file(blocks, mgf_out)
    print(f"Exported updated MGF to {mgf_out}")

# Parse the graphml file (from output copy)
with open(graphml_out, "r") as file:
    soup = BeautifulSoup(file, "xml")

# Update the node IDs in the graphml file
for node in soup.find_all("node"):
    node_id = node["id"]
    try:
        node_id = int(node_id)
    except ValueError:
        pass
    add = IDmapping.get(node_id, "")
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
    add_source = IDmapping.get(source, "")
    add_target = IDmapping.get(target, "")
    if add_source != "":
        edge["source"] = str(source) + add_source
    if add_target != "":
        edge["target"] = str(target) + add_target

# Write the updated graphml to the output file
with open(graphml_out, "w") as file:
    file.write(
        re.sub(
            "<binary>\\s*(.*)\\s*</binary>",
            "<binary>\\1</binary>",
            soup.prettify().replace("\r", ""),
        )
    )

print(f"Exported updated GraphML to {graphml_out}")
