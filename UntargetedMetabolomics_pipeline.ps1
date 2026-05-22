# Disable output
$ErrorActionPreference = "Stop"





##################################################################################################################################################
## Parameters and defaults
$MZMINE = ".\mzmine_Windows_portable-4.5.20\mzmine_console.exe"
$SIRIUS = ".\sirius-6.2.2-win-x64\sirius.exe"
$WDIR = "."
$OUTDIR = "..\mzmine__results"

$PROCMZMINE = 1
$PROCSIRIUS = 0
$ARCHIVERESULTS = 0

## Define tasks as a dictionary with additional information
$TaskParams = @{
    "data_pos" =  @{
	"MZmine_batch" = "mzmine_batch_files/process_batch_shortMethod_posMode.mzbatch"; 
	"polarity" = "positive"; 
	"input_files" = "..\input_MS1_pos.txt"
    };
    "data_neg" =  @{
	"MZmine_batch" = "mzmine_batch_files/process_batch_shortMethod_negMode.mzbatch"; 
	"polarity" = "negative"; 
	"input_files" = "..\input_MS1_neg.txt"
    };
}
# Default tasks to be processed
$Tasks = ($TaskParams.Keys | Sort-Object | ForEach-Object { $_ }) -join ", "
$Tasks = @("data_pos", "data_neg")

## do not mofify unless expert
$PROCESSBATCHFILE = "$WDIR\scripts\process_batch.mzbatch"
$FEATURERENAMESCRIPT = "$WDIR\scripts\rename_MZMine_results.py"
$MGFFIXILE = "$WDIR\scripts\fix_SIRIUS_mgfs.py"
$SIRIUSGETFINGERPRINTS = "$WDIR\scripts\SIRIUS_getFingerprints.py"
$COMBINEEXPERIMENTS = "$WDIR\scripts\combineMZMineWithMEII.py"
$DDAINCLUSIONLIST = "$WDIR\scripts\create_DDA_inclusion_list.py"





##################################################################################################################################################
## Functions

# convert csv file with delimiter default (,) , to tab-delimited tsv file
function ConvertCsvToTsv {
    param (
        [string]$CsvFilePath,
        [string]$TsvFilePath
    )
    if (Test-Path $CsvFilePath) {
        Import-Csv -Path $CsvFilePath -Delimiter "," | Export-Csv -Path $TsvFilePath -Delimiter "`t" -NoTypeInformation
    } else {
        Write-Host "Error: CSV file not found at $CsvFilePath"
        Write-Host "Press any key to exit..."
        [void][System.Console]::ReadKey($true)
        exit 1
    }
}

# Check if Python, SIRIUS, and MZmine are installed/available
function CheckUV {
    if (Get-Command "uv" -ErrorAction SilentlyContinue) {
    } else {
        Write-Host "UV not found. Please ensure UV is installed and available in the system PATH. If needed, refer to the README for instructions on setting up UV."
        
        Write-Host "Press any key to exit..."
        [void][System.Console]::ReadKey($true)
        exit 1
    }    
}

function CheckSirius {
    param ([string]$SiriusPath)
    if (Test-Path $SiriusPath) {
    } else {
        Write-Host "SIRIUS not found at $SiriusPath"
        Write-Host "Please ensure SIRIUS is available at $SiriusPath or adapt the path. If needed, refer to the README for instructions on setting up SIRIUS."
        
        Write-Host "Press any key to exit..."
        [void][System.Console]::ReadKey($true)
        exit 1
    }
}

function CheckMzmine {
    param ([string]$MzminePath)
    if (Test-Path $MzminePath) {
    } else {
        Write-Host "MZmine not found at $MzminePath"
        Write-Host "Please ensure MZmine is available at $MzminePath or adapt the path. If needed, refer to the README for instructions on downloading MZmine."
        
        Write-Host "Press any key to exit..."
        [void][System.Console]::ReadKey($true)
        exit 1
    }
}

# Generic function to check if a file exists
function CheckFileExists {
    param (
        [string]$FilePath,
        [string]$ErrorMessage
    )
    if (!(Test-Path $FilePath)) {
        Write-Host "Error: $ErrorMessage not found at $FilePath"
        Write-Host "Press any key to exit..."
        [void][System.Console]::ReadKey($true)
        exit 1
    }
}

# Process tasks
function Process {
    param ([string]$Task)

    if ($Task -notlike "*_pos" -and $Task -notlike "*_neg") {
        Write-Host "Error: Task '$Task' does not end with '_pos' or '_neg'. Please check the task name."
        Write-Host "Press any key to exit..."
        [void][System.Console]::ReadKey($true)
        exit 1
    }

    $StartTime = Get-Date
    Write-Host "Processing $Task"
    Write-Host "`n`n`n`n`n`n`n`n`n`n`n`n###########################################################"

    if ($PROCMZMINE -eq 1) {
        Write-Host "`n`n`n-----------------------------------------------------"
        Write-Host "$Task.1. Running MZmine" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt"
        $PROCESSBATCHFILE = $TaskParams[$Task].MZmine_batch
        $INPUTFILESFILE = $TaskParams[$Task].input_files
        Write-Host "   .. using mzbatch file ${PROCESSBATCHFILE} to process $INPUTFILESFILE" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        Copy-Item "$PROCESSBATCHFILE" "$WDIR\$OUTDIR\params_scripts_etc\"
        Copy-Item "$FEATURERENAMESCRIPT" "$WDIR\$OUTDIR\params_scripts_etc\"
        Copy-Item "$INPUTFILESFILE" "$WDIR\$OUTDIR\params_scripts_etc\"

        # Process with MZmine
        & $MZMINE -b "$PROCESSBATCHFILE" -i "$INPUTFILESFILE" -o "$WDIR\$OUTDIR\${Task}_" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append

        # Test if the necessary output file was generated, if not, stop the script with an error message
        if (!(Test-Path "$WDIR\$OUTDIR\${Task}__full_feature_table.csv")) {
            Write-Host "Error: MZmine processing for $Task did not generate the expected output file ${Task}__full_feature_table.csv. Please check the MZmine log file ${Task}.1_MZmine_log.txt for details." -ForegroundColor Red
            Write-Host "Press any key to exit..."
            [void][System.Console]::ReadKey($true)
            exit 1
        }
        
        # Rename mzmine project and annotations file
        if (Test-Path "$WDIR\$OUTDIR\${Task}_") {
            Move-Item "$WDIR\$OUTDIR\${Task}_" "$WDIR\$OUTDIR\${Task}__mzmineproject.mzmine" -Force
        }
        if (Test-Path "$WDIR\$OUTDIR\${Task}__annotations") {
            Move-Item "$WDIR\$OUTDIR\${Task}__annotations" "$WDIR\$OUTDIR\${Task}__annotations.csv" -Force
        }

        # Rename the full feature table and quantification files
        Write-Host "`n`n`n-----------------------------------------------------"
        Write-Host "$Task.1.1. Renaming feature IDs" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        & uv run "$FEATURERENAMESCRIPT" `
          --full_feature_table "$WDIR\$OUTDIR\${Task}__full_feature_table.csv" `
          --annotations "$WDIR\$OUTDIR\${Task}__annotations.csv" `
          --iimn_fbmn_quant "$WDIR\$OUTDIR\${Task}__iimn_fbmn_quant.csv" `
          --graphml "$WDIR\$OUTDIR\${Task}__networks_fbmn.graphml" `
          --mgf "$WDIR\$OUTDIR\${Task}__iimn_fbmn.mgf" "$WDIR\$OUTDIR\${Task}__sirius.mgf" `
          | Tee-Object  `
          -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt"  `
          -Append

        ## Split MGF file
        #Write-Host "`n`n`n-----------------------------------------------------"
        #Write-Host "$Task.1.2. Splitting MGF file" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        #uv --project C:\development\util_MGFTools run C:\development\util_MGFTools\main.py split --input "$WDIR\$OUTDIR\${Task}__sirius.mgf" --mslevel | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        #uv --project C:\development\util_MGFTools run C:\development\util_MGFTools\main.py split --input "$WDIR\$OUTDIR\${Task}__iimn_fbmn.mgf" --mslevel | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append

        ## Clean 12C13C spectra
        #Write-Host "`n`n`n-----------------------------------------------------"
        #Write-Host "$Task.1.3. Cleaning 12C13C spectra" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        #C:\development\util_FragExtract\rust_impl\target\debug\rust_impl.exe --input-mgf "$WDIR\$OUTDIR\${Task}__sirius__MS2_Pall_FMall_CEall.mgf" --output-folder "$WDIR\$OUTDIR\${Task}__sirius__MS2_Pall_FMall_CEall_matchedCleaned"
        #C:\development\util_FragExtract\rust_impl\target\debug\rust_impl.exe --input-mgf "$WDIR\$OUTDIR\${Task}__iimn_fbmn__MS2_Pall_FMall_CEall.mgf" --output-folder "$WDIR\$OUTDIR\${Task}__iimn_fbmn__MS2_Pall_FMall_CEall_matchedCleaned"

        ## Combine results with MEII processing
        #Write-Host "`n`n`n-----------------------------------------------------"
        #Write-Host "$Task.1.4. Combining results with MEII" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        #$MEIIREFFILE = "H:\LV_comparison\Data\LV2026\Fullscan\FPS\results.tsv"
        #& uv run "$COMBINEEXPERIMENTS" `
        #  --plot_file "$WDIR\$OUTDIR\${Task}_mapped_features.pdf" `
        #  --remove_non_matched `
        #  --polarity $TaskParams[$Task].polarity `
        #  --avg_rt_shift 0.0 `
        #  --avg_mz_ppm_shift 0.0 `
        #  --max_rt_difference_min 0.15 `
        #  --max_mz_difference_ppm 5.0 `
        #  --reference_file "$MEIIREFFILE" `
        #  --query_file "$WDIR\$OUTDIR\${TASK}__full_feature_table.csv" `
        #  --additional_query_file "$WDIR\$OUTDIR\${TASK}__iimn_fbmn.mgf" `
        #  --additional_query_file "$WDIR\$OUTDIR\${TASK}__sirius.mgf" 
        
        ## Reorder table
        #Write-Host "`n`n`n-----------------------------------------------------"
        #Write-Host "$Task.1.5. Reordering the full feature table and quantification files" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        #& uv run --project  C:\development\util_ReorderTSVFiles C:\development\util_ReorderTSVFiles\main.py "$WDIR\$OUTDIR\${TASK}__full_feature_table_combined.tsv" --output_file "${TASK}__full_feature_table_combined_reordered.tsv" --sort_regexes "id.*" "^mz$" "^rt$" "datafile:.*:area" --not_include_other_columns

        # Create DDA inclusion list from full feature table
        Write-Host "`n`n`n-----------------------------------------------------"
        Write-Host "$Task.1.6. Creating DDA inclusion list" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        & uv run "$DDAINCLUSIONLIST" `
          --input_file "$WDIR\$OUTDIR\${Task}__full_feature_table.csv" `
          --output_file "$WDIR\$OUTDIR\${Task}__DDA_inclusion_list_IQX.csv" `
          --rt_window 0.1 `
          | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append

        # Convert csv files to tsv file for easier opening with Excel
        Write-Host "`n`n`n-----------------------------------------------------"
        Write-Host "$Task.1.7. Converting CSV files to TSV files" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.1_MZmine_log.txt" -Append
        ConvertCsvToTsv -CsvFilePath "$WDIR\$OUTDIR\${Task}__full_feature_table.csv" -TsvFilePath "$WDIR\$OUTDIR\${Task}__full_feature_table.tsv"
        ConvertCsvToTsv -CsvFilePath "$WDIR\$OUTDIR\${Task}__iimn_fbmn_quant.csv" -TsvFilePath "$WDIR\$OUTDIR\${Task}__iimn_fbmn_quant.tsv"
        ConvertCsvToTsv -CsvFilePath "$WDIR\$OUTDIR\${Task}__annotations.csv" -TsvFilePath "$WDIR\$OUTDIR\${Task}__annotations.tsv"
        ConvertCsvToTsv -CsvFilePath "$WDIR\$OUTDIR\${Task}__iimn_fbmn_edges_msannotation.csv" -TsvFilePath "$WDIR\$OUTDIR\${Task}__iimn_fbmn_edges_msannotation.tsv"
        
    } else {
        Write-Host "$Task.1. Skipping MZmine step as PROCMZMINE is not set to 1"
    }

    if ($PROCSIRIUS -eq 1) {
        Write-Host "$Task.2. Running SIRIUS"

        Write-Host "$Task.2.1. Fixing MGF file" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt"
        Copy-Item "$MGFFIXILE" "$WDIR\$OUTDIR\params_scripts_etc\"
        
        # Fix the MGF file for SIRIUS
        & uv run "$MGFFIXILE" --mgf_file "$WDIR\$OUTDIR\${Task}__sirius.mgf" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt" -Append

        # Process with SIRIUS
        Write-Host "$Task.2.2. Predicting fingerprints" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt" -Append

        $FALLBACKIONSPOS = "--AdductSettings.fallback=[[M+H]+,[M+Na]+,[M+K]+,[M-H]-]"
        $FALLBACKIONSNEG = "--AdductSettings.fallback=[[M-H]-]"
        if ($Task -like "*_pos") {
            $FALLBACKIONS = $FALLBACKIONSPOS
        } elseif ($Task -ilike "*_neg") {
            $FALLBACKIONS = $FALLBACKIONSNEG
        }
        Write-Host "   .. using fallback ions $FALLBACKIONS" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt" -Append

        & $SIRIUS --input "$WDIR\$OUTDIR\${Task}__sirius.mgf" --output "$WDIR\$OUTDIR\${Task}__sirius.sirius" config --IsotopeSettings.filter=true --CandidateFormulas=, "--FormulaSettings.enforced=H,C,N,O,P" --Timeout.secondsPerInstance=0 --AlgorithmProfile=orbitrap --SpectralMatchingMassDeviation.allowedPeakDeviation=5.0ppm --AdductSettings.ignoreDetectedAdducts=false --AdductSettings.prioritizeInputFileAdducts=true --UseHeuristic.useHeuristicAboveMz=300 --IsotopeMs2Settings=IGNORE --MS2MassDeviation.allowedMassDeviation=5.0ppm --SpectralMatchingMassDeviation.allowedPrecursorDeviation=5.0ppm --FormulaSearchSettings.performDeNovoBelowMz=400.0 --FormulaSearchSettings.applyFormulaConstraintsToDatabaseCandidates=false --EnforceElGordoFormula=true --NumberOfCandidatesPerIonization=1 "$FALLBACKIONS" --FormulaSearchSettings.performBottomUpAboveMz=0 --FormulaSearchSettings.applyFormulaConstraintsToBottomUp=false --UseHeuristic.useOnlyHeuristicAboveMz=650 --FormulaSearchDB=, --Timeout.secondsPerTree=0 --AdductSettings.enforced=, "--FormulaSettings.detectable=B,S,Cl,Se,Br" --NumberOfCandidates=10 --FormulaResultThreshold=true --ExpansiveSearchConfidenceMode.confidenceScoreSimilarityMode=APPROXIMATE "--StructureSearchDB=BOKU_iBAM,CHEBI,COCONUT,GNPS,KEGG,LOTUS,PLANTCYC,SUPERNATURAL" --RecomputeResults=false spectra-search formulas fingerprints classes structures summaries --top-k-summary=15 | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt" -Append

        #Write-Host "$Task.2.3. Exporting SIRIUS fingerprints" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt" -Append
        #Copy-Item "$SIRIUSGETFINGERPRINTS" "$WDIR\$OUTDIR\params_scripts_etc\"
        #& uv "$SIRIUSGETFINGERPRINTS" --sirius_path $SIRIUS --sirius_project "$WDIR\$OUTDIR\${Task}__sirius.sirius" --output_json "$WDIR/$OUTDIR/${Task}__sirius_fingerprints.json" | Tee-Object -FilePath "$WDIR\$OUTDIR\${Task}.2_SIRIUS_log.txt" -Append

    } else {
        Write-Host "$Task.2. Skipping SIRIUS step as PROCSIRIUS is not set to 1"
    }

    $EndTime = Get-Date
    $Duration = $EndTime - $StartTime
    Write-Host "Finished $Task in $($Duration.TotalSeconds) seconds"
}





##################################################################################################################################################
## Short info
Write-Host "-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-"
Write-Host "Untargeted Metabolomics pipeline v0.1.0"
Write-Host "This script processes the MZmine and SIRIUS parts of the pipeline."
Write-Host "It is designed to be run in the directory where the mzML files are located."
Write-Host "-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-"
Write-Host ""

## Print all tasks and their properties
Write-Host "Defined and available tasks and their properties:"
foreach ($TaskName in $TaskParams.Keys) {
    Write-Host "$TaskName"
    $TaskProperties = $TaskParams[$TaskName]
    foreach ($PropertyName in $TaskProperties.Keys) {
        Write-Host "   - ${PropertyName}: $($TaskProperties[$PropertyName])"
    }
    CheckFileExists -FilePath $TaskParams[$TaskName].MZmine_batch -ErrorMessage "$TaskProperties.MZmine_batch"
}
Write-Host ""





##################################################################################################################################################
## Check if the required software is installed/available
CheckUV
CheckSirius $SIRIUS
CheckMzmine $MZMINE

# Check if required files exist
CheckFileExists -FilePath $MGFFIXILE -ErrorMessage "fix_SIRIUS_mgfs.py"
CheckFileExists -FilePath $SIRIUSGETFINGERPRINTS -ErrorMessage "SIRIUS_getFingerprints.py"

Write-Host "`All scripts and software tools seem to be available. Proceeding with the pipeline.`n`n"





##################################################################################################################################################
## Prompts for user input

# Output directory prompt
do {
    $OUTDIRInput = Read-Host "Specify the output directory [Default: '$WDIR\$OUTDIR', press enter for default]"
    if ($OUTDIRInput -eq "") {
        Write-Host "Using default: '$OUTDIR'" -ForegroundColor DarkGray
        break
    } else {
        $OUTDIR = $OUTDIRInput
        Write-Host "Using custom output directory: '$OUTDIRFinal'" -ForegroundColor DarkGray
        break
    }
} while ($true)

# Tasks prompt
do {
    $TasksInput = Read-Host "Specify the tasks to be processed, separated by commas [Default: '$Tasks', press enter for default]"
    if ($TasksInput -eq "") {
        Write-Host "Using default: '$Tasks'" -ForegroundColor DarkGray
        break
    } else {
        # Validate that all entered tasks are in $TaskParams.Keys
        $EnteredTasks = $TasksInput -split ",\s*"
        $InvalidTasks = $EnteredTasks | Where-Object { $_ -notin $TaskParams.Keys }
        if ($InvalidTasks.Count -eq 0) {
            $Tasks = $TasksInput -split ",\s*"
            break
        } else {
            Write-Host "Invalid task(s): $($InvalidTasks -join ', '). Please enter valid task names from: $($TaskParams.Keys -join ', ')" -ForegroundColor Red
        }
    }
} while ($true)
if ($Tasks -is [string]) {
    $Tasks = $Tasks -split ",\s*"
}

# Prompt for MZmine processing
$MZmineOptions = @("1", "0")
do {
    Write-Host "Do you want to process the MZmine part?"
    Write-Host "(Default: $PROCMZMINE - Press Enter to use this default)"
    Write-Host "Options:"
    Write-Host " - 1: Yes"
    Write-Host " - 0: No"
    $userInput = Read-Host "Your choice"
    if ($userInput -eq "") {
        Write-Host "Using default: $PROCMZMINE" -ForegroundColor DarkGray
        break
    } elseif ($userInput -in $MZmineOptions) {
        $PROCMZMINE = [int]$userInput
        break
    } else {
        Write-Host "Invalid input '$userInput'. Only 0, 1, or Enter is allowed. Please try again." -ForegroundColor Red
    }
} while ($true)
Write-Host ""

# Prompt for SIRIUS processing
$SiriusOptions = @("1", "0")
do {
    Write-Host "Do you want to process the SIRIUS part?"
    Write-Host "(Default: $PROCSIRIUS - Press Enter to use this default)"
    Write-Host "Options:"
    Write-Host " - 1: Yes"
    Write-Host " - 0: No"
    $userInput = Read-Host "Your choice"
    if ($userInput -eq "") {
        Write-Host "Using default: $PROCSIRIUS" -ForegroundColor DarkGray
        break
    } elseif ($userInput -in $SiriusOptions) {
        $PROCSIRIUS = [int]$userInput
        break
    } else {
        Write-Host "Invalid input '$userInput'. Only 0, 1, or Enter is allowed. Please try again." -ForegroundColor Red
    }
} while ($true)
Write-Host ""

# Prompt for archiving results
$ArchiveOptions = @("1", "0")
do {
    Write-Host "Do you want to create an archive of the results?"
    Write-Host "(Default: $ARCHIVERESULTS - Press Enter to use this default)"
    Write-Host "Options:"
    Write-Host " - 1: Yes"
    Write-Host " - 0: No"
    $userInput = Read-Host "Your choice"
    if ($userInput -eq "") {
        Write-Host "Using default: $ARCHIVERESULTS" -ForegroundColor DarkGray
        break
    } elseif ($userInput -in $ArchiveOptions) {
        $ARCHIVERESULTS = [int]$userInput
        break
    } else {
        Write-Host "Invalid input '$userInput'. Only 0, 1, or Enter is allowed. Please try again." -ForegroundColor Red
    }
} while ($true)
Write-Host ""





##################################################################################################################################################
## Processing the datasets

# Create results directories
New-Item -ItemType Directory -Force -Path "$WDIR\$OUTDIR"
New-Item -ItemType Directory -Force -Path "$WDIR\$OUTDIR\params_scripts_etc"

# Track execution time for all Process calls together
$OverallStartTime = Get-Date

# Process each task in the array
foreach ($Task in $Tasks) {
    Process $Task
}

$OverallEndTime = Get-Date
$OverallDuration = $OverallEndTime - $OverallStartTime
Write-Host "`n`n`n`n`nAll tasks completed in $($OverallDuration.TotalSeconds) seconds"

# Archive results
if ($ARCHIVERESULTS -eq 1) {
    Write-Host "Archiving results"
    $Timestamp = (Get-Date -Format "yyyyMMdd_HHmmss")
    $Filename = "$WDIR\Archive__${OUTDIR}__$Timestamp.7z"
    & "$env:ProgramFiles\7-Zip\7z.exe" a "$Filename" "$WDIR\$OUTDIR\"
    & "$env:ProgramFiles\7-Zip\7z.exe" a "$Filename" "$WDIR\classification\prenylatedPolyphenols.ipynb" "$WDIR\classification\prenylatedPolyphenols.html" "$WDIR\classification\output"
    Write-Host "Created archive $Filename"
}

Write-Host "Press any key to exit..."
[void][System.Console]::ReadKey($true)
