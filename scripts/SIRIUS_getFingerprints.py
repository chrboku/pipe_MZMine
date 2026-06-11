sirius_path = (
    "I:/Tomas_PrenylatedCompounds/analysis/FPS/sirius-6.1.0-win-x64/sirius.exe"
)

# Argument parser
import argparse

parser = argparse.ArgumentParser(description="Process SIRIUS parameters.")
parser.add_argument(
    "--sirius_path",
    type=str,
    default=sirius_path,
    help="Path to the SIRIUS executable (optional).",
)
parser.add_argument(
    "--workspace_name", type=str, default="generic", help="Name of workspace"
)
parser.add_argument(
    "--sirius_project", type=str, help="Path to the mandatory SIRIUS project file."
)
parser.add_argument(
    "--output_json",
    type=str,
    required=True,
    help="Path to the output JSON file where fingerprints will be saved.",
)

args = parser.parse_args()


# Update variables with parsed arguments
sirius_path = args.sirius_path
sirius_project = args.sirius_project
workspace_name = args.workspace_name
output_json = args.output_json


# Imports
import PySirius
import json


print("Starting SIRIUS SDK...")
sdk = PySirius.SiriusSDK()
try:
    api = sdk.start_sirius(sirius_path=sirius_path, port=8080)
    print("Processing...")
    api_response = api.projects().open_project(
        workspace_name, path_to_project=sirius_project
    )

    fingerprints = {}
    for feature in api.features().get_aligned_features(workspace_name):
        try:
            featureId = feature.aligned_feature_id
            formula = (
                api.features()
                .get_aligned_feature(
                    workspace_name,
                    featureId,
                    [PySirius.AlignedFeatureOptField.TOPANNOTATIONS],
                )
                .top_annotations.formula_annotation
            )
            print(feature.name, formula.molecular_formula)
            tree = api.features().get_frag_tree(
                workspace_name, featureId, formula.formula_id
            )
            fingerprint = api.features().get_fingerprint_prediction(
                workspace_name, featureId, formula.formula_id
            )
            fingerprints[feature.name] = {
                "fingerprint": fingerprint,
                "molecular_formula": formula.molecular_formula,
            }
        except Exception as e:
            print(f"Error processing feature {feature.name}: {e}")

    # Save fingerprints to the specified JSON file
    with open(output_json, "w") as json_file:
        json.dump(fingerprints, json_file, indent=4)

    print(f"Fingerprints saved to {output_json}")

except Exception as e:
    print(f"An error occurred: {e}")
    print("Please check the SIRIUS project file and try again.")

finally:
    sdk.shutdown_sirius()
