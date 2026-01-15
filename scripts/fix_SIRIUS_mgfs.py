import argparse
from collections import OrderedDict
import re


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


def main():
    parser = argparse.ArgumentParser(description="Process an MGF file.")
    parser.add_argument("--mgf_file", type=str, help="Path to the MGF file")
    parser.add_argument("--mgf_outputfile", type=str, help="Path to the MGF file", default="::SAME")
    args = parser.parse_args()

    print(f"Processing MGF file: {args.mgf_file}")

    blocks = parse_mgf_file(args.mgf_file)
    print("   .. Parsed MGF file with successfully.")
    num_blocks = sum(len(feature_blocks) for feature_blocks in blocks.values())
    num_unique_feature_ids = len(blocks)
    print(f"   .. Number of parsed blocks: {num_blocks}")
    print(f"   .. Number of unique FEATURE_IDs: {num_unique_feature_ids}")

    new_blocks = OrderedDict()
    unique_id_counter = 1
    for feature_id, feature_blocks in blocks.items():
        ms1_blocks = [block for block in feature_blocks if block.get("MSLEVEL") == "1"]
        ms2_blocks = [block for block in feature_blocks if block.get("MSLEVEL") == "2"]
        print(f"Feature ID: {feature_id}, MS1 spectra: {len(ms1_blocks)}, MS2 spectra: {len(ms2_blocks)}")

        assert(len(ms1_blocks) <= 1)

        for ms2_block in ms2_blocks:
            ms2_block = ms2_block.copy()

            # Check if the FEATURE_ID starts with a number followed by two underscores
            x = ms2_block['FEATURE_ID']
            if re.match(r"^\d+__", x):
                x = f"{unique_id_counter}_{ms2_block['FEATURE_ID']}"
                print(f"   .. updating FEATURE_ID to {x}")
                ms2_block['FEATURE_ID'] = x
            else:
                print(f"   .. not updating as it seems already fixed: {x}")

            # generate new blocks
            if len(ms1_blocks) > 0:
                ms1 = ms1_blocks[0].copy()
                ms1["FEATURE_ID"] = f"{ms2_block['FEATURE_ID']}"
                new_blocks[x] = [ms1, ms2_block]
            else:
                new_blocks[x] = [ms2_block]
            unique_id_counter += 1

    # Export the new blocks to a new MGF file
    output_file_path = args.mgf_outputfile
    if output_file_path == "::SAME":
        output_file_path = args.mgf_file
    export_mgf_file(new_blocks, output_file_path)
    print(f"   .. Exported new MGF file to: {output_file_path}")

if __name__ == "__main__":
    main()
