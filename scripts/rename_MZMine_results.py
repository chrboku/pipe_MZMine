import argparse
from collections import OrderedDict
import polars as pl
import random
import re
from bs4 import BeautifulSoup



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

                

parser = argparse.ArgumentParser(description='Process feature table, quantification table, graphml and MGF files.')
parser.add_argument('--full_feature_table', type=str, help='Path to the full feature table CSV file')
parser.add_argument('--annotations', type=str, help='Path to the annotations CSV file')
parser.add_argument('--iimn_fbmn_quant', type=str, help='Path to the IIMN FBMN quantification CSV file')
parser.add_argument('--graphml', type=str, help='Path to the graphml file')
parser.add_argument('--mgf', type=str, nargs='+', help='Path to one or more MGF files')
parser.add_argument('--file_suffix', type=str, help='Suffix to add to the output file names.  If not specified, will overwrite the existing files', default="")

args = parser.parse_args()

full_feature_table = args.full_feature_table
iimn_fbmn_quant = args.iimn_fbmn_quant
graphml = args.graphml
mgfs = args.mgf
file_suffix = args.file_suffix

## show brief overview of the parameters
print(f"  - Full Feature Table: {full_feature_table}")
print(f"  - Annotations: {args.annotations}")
print(f"  - IIMN FBMN Quantification: {iimn_fbmn_quant}")
print(f"  - GraphML: {graphml}")
print(f"  - MGFs: {mgfs}")


# Read the full feature table CSV file
full_feature_table_df = pl.read_csv(full_feature_table, separator=",", has_header=True)

# Read the annotations CSV file
df_annotations = pl.read_csv(args.annotations, separator=",", has_header=True)

# Read the IIMN FBMN quantification CSV file
iimn_fbmn_quant_df = pl.read_csv(iimn_fbmn_quant, separator=",", has_header=True, truncate_ragged_lines=True)

mgfs_data = {}
for mgf in mgfs:
    mgfs_data[mgf] = parse_mgf_file(mgf)

print("\nNumber of features:", len(full_feature_table_df))

## generate the new IDs and the ID mapping
IDmapping = {}
def makeSafe(name):
    name = re.sub(r'[^a-zA-Z0-9_\\-\\.]', '_', name)  # Replace non-alphanumeric characters with underscores
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
    if add is not None and add != "":
        print(f"   - {id}, renaming to {id}{add}")
    else:
        print(f"   - {id}, not adding anything")

    IDmapping[full_feature_table_df["id"][i]] = add

# Update the 'id' column in the full_feature_table_df
full_feature_table_df = full_feature_table_df.with_columns(
    pl.col("id").cast(pl.Utf8) + pl.col("id").map_elements(lambda id: IDmapping.get(id, ""), return_dtype=pl.Utf8)
)

# Update the 'id' column in the iimn_fbmn_quant_df
iimn_fbmn_quant_df = iimn_fbmn_quant_df.with_columns(
    pl.col("row ID").cast(pl.Utf8) + pl.col("row ID").map_elements(lambda id: IDmapping.get(id, ""), return_dtype=pl.Utf8)
)

# Update the 'id' column in the df_annotations
df_annotations = df_annotations.with_columns(
    pl.col("id").cast(pl.Utf8) + pl.col("id").map_elements(lambda id: IDmapping.get(id, ""), return_dtype=pl.Utf8)
)

# Update the FEATURE_ID in the MGF files
for mgf, blocks in mgfs_data.items():
    print(f"Updating FEATURE_ID in {mgf}")
    for block in blocks:
        if "FEATURE_ID" in block:
            try:
                feature_id = int(block["FEATURE_ID"])
            except ValueError:
                feature_id = block["FEATURE_ID"]
            add = IDmapping.get(feature_id, "")
            block["FEATURE_ID"] = str(feature_id) + add
            #print(f"Updated FEATURE_ID: {block['FEATURE_ID']}")

# Export the updated DataFrames to CSV files
output_full_feature_table_path = full_feature_table.replace(".csv", f"{file_suffix}.csv")
full_feature_table_df.write_csv(output_full_feature_table_path)
print(f"Exported updated Full Feature Table to {output_full_feature_table_path}")

output_iimn_fbmn_quant_path = iimn_fbmn_quant.replace(".csv", f"{file_suffix}.csv")
iimn_fbmn_quant_df.write_csv(output_iimn_fbmn_quant_path)
print(f"Exported updated IIMN FBMN Quantification to {output_iimn_fbmn_quant_path}")

output_annotations_path = args.annotations.replace(".csv", f"{file_suffix}.csv")
df_annotations.write_csv(output_annotations_path)
print(f"Exported updated Annotations to {output_annotations_path}")

for mgf, blocks in mgfs_data.items():
    # Export the updated MGF file
    output_mgf_path = mgf.replace(".mgf", f"{file_suffix}.mgf")
    export_mgf_file(blocks, output_mgf_path)
    print(f"Exported updated MGF to {output_mgf_path}")

# Parse the graphml file
with open(graphml, "r") as file:
    soup = BeautifulSoup(file, 'xml')

# Update the node IDs in the graphml file
for node in soup.find_all('node'):
    node_id = node['id']
    try:
        node_id = int(node_id)
    except ValueError:
        pass
    add = IDmapping.get(node_id, "")
    if add != "":
        new_node_id = str(node_id) + add
        node['id'] = new_node_id

# Update the target and source attributes in the edges
for edge in soup.find_all('edge'):
    source = edge['source']
    target = edge['target']
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
        edge['source'] = str(source) + add_source
    if add_target != "":
        edge['target'] = str(target) + add_target

# Write the updated graphml to a new file
output_graphml_path = graphml.replace(".graphml", f"{file_suffix}.graphml")
with open(output_graphml_path, "w") as file:
    file.write(re.sub("<binary>\\s*(.*)\\s*</binary>", "<binary>\\1</binary>", soup.prettify().replace("\r", "")))

print(f"Exported updated GraphML to {output_graphml_path}")