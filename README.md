# Untargeted Metabolomics Pipeline

version 0.8.0

An automated pipeline for LC-HRMS data processing using MZMine and SIRIUS for untargeted metabolomics analysis.

## Aim

- Automated LC-HRMS data processing in an untargeted manner with MZMine
- Export quantification and MSMS information to TSV and MGF files
- Annotate detected compounds with SIRIUS
- Perform all steps in a reusable, reproducible pipeline

## Features

- Batch processing of multiple datasets
- Support for both positive and negative ionization modes
- Automated feature detection and alignment
- Customizable via separate MZMine batch settings
- MS/MS spectral export for molecular networking
- Compound annotation using SIRIUS

## Initial Setup by the User

Note: currently only tested on Windows OS and PowerShell terminal only available on Windows OS.

1. **Conda Installation**
   - Install [https://github.com/conda-forge/miniforge](https://github.com/conda-forge/miniforge)

2. **PySirius Environment Setup**
   - Create a conda environment for PySirius according to [https://github.com/sirius-ms/sirius-client-openAPI/tree/master/client-api_python#installation--usage](https://github.com/sirius-ms/sirius-client-openAPI/tree/master/client-api_python#installation--usage)
   ```bash
   conda create -n pysirius python=3.8
   conda activate pysirius
   conda install -c conda-forge py-sirius-ms
   conda install polars
   ```

3. **Download MZMine**
   - Download the latest MZMine version (recommended: 4.5.20) as a portable archive file
   - [https://github.com/mzmine/mzmine/releases/download/v4.5.20/mzmine_Windows_portable-4.5.20.zip](https://github.com/mzmine/mzmine/releases/download/v4.5.20/mzmine_Windows_portable-4.5.20.zip)
   - Extract it into this repository folder
   - Expected directory: `mzmine_Windows_portable_4.5.20/`

4. **Download SIRIUS**
   - Download the latest SIRIUS version (recommended: 6.2.2) as a portable archive file
   - [https://github.com/sirius-ms/sirius/releases/download/v6.1.0/sirius-6.1.0-win-x64.zip](https://github.com/sirius-ms/sirius/releases/download/v6.1.0/sirius-6.1.0-win-x64.zip)
   - Extract it into this repository folder
   - Expected directory: `sirius-6.2.2-win-x64/`

5. **Configure Pipeline Script**
   - Open `UntargetedMetabolomics_pipeline.ps1`
   - Adapt the parameters to reflect the paths of the three software requirements ($MZMINE, $SIRIUS, $PY)

## Usage

1. **Configure Processing Parameters**
   - Edit the `$TaskParams` variable in `UntargetedMetabolomics_pipeline.ps1`
   - For each dataset to process, set:
     - `MZmine_batch`: The MZMine batch configuration file to be used (e.g., `process_batch_shortMethod_posMode.mzbatch`)
     - `polarity`: The ionization polarity for annotation (`positive` or `negative`)
     - `input_files`: A text file containing paths to mzML files for processing (one path per line)

2. **Run the Pipeline**
   - Open PowerShell terminal
   - Navigate to the repository directory
   - Execute the script: `powershell .\UntargetedMetabolomics_pipeline.ps1`
   - The script will first ask a couple of question (which tasks shall be processed, which parts of the pipeline shall be executed) and then run autonomously. 

## Output

The pipeline generates the following outputs into the subfolder specified in the variable $OUTDIR:

- **Quantification tables**: Feature intensity matrices for statistical analysis
- **MGF files**: MS/MS spectra in Mascot Generic Format for downstream analysis
- **Annotation tables**: Compound identifications according to the MZMine batch file configuration
  - Source for MS/MS annotation and compound database searches
- **Molecular networks**: Graph files for Cytoscape visualization
- **SIRIUS annotations**: Structural predictions and molecular formula assignments

## Acknowledgements

This work builds upon the contributions of many people, in particular:

- The [MZMine](https://github.com/mzmine/mzmine) project for mass spectrometry data processing
- The [SIRIUS](https://github.com/boecker-lab/sirius) project for compound annotation and structure prediction

## License

Please refer to the individual licenses of MZMine and SIRIUS for their respective components.

## Support

For issues related to:
- MZMine processing: [MZMine documentation](https://mzmine.github.io/)
- SIRIUS annotation: [SIRIUS documentation](https://boecker-lab.github.io/docs.sirius.github.io/)
