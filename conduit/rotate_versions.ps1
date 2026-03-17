# rotate_versions.ps1
# V3R CONDUIT — Version Rotation Script
# Maintains three-layer local backup: current, last, previous
# Run before every conduit_sync.ps1 execution

$sourceEcr = "C:\Users\Source-1\Desktop\rag-deploy\ecr-build"
$sourceHf = "C:\Users\Source-1\Desktop\rag-deploy\hf-space"
$backupRoot = "C:\Users\Source-1\Desktop\V3R\code"
$current = "$backupRoot\current"
$last = "$backupRoot\last"
$previous = "$backupRoot\previous"

$trackedFiles = @("lambda_ingest_v4.py","lambda_retrieve.py","lambda_chat.py","lambda_graph.py","lambda_orchestrator.py","lambda_ideagen.py","Dockerfile-ml-offline","orchestrator_config.json")

$trackedHfFiles = @("app.py")

Write-Host "=== V3R CONDUIT Version Rotation ===" -ForegroundColor magenta
Write-Host "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Starting rotation..." -ForegroundColor Gray

Write-Host "Stage 1: Clearing previous layer..." -ForegroundColor Yellow
Remove-Item -Path "$previous\*" -Force -ErrorAction SilentlyContinue

Write-Host "Stage 2: Shifting last -> previous..." -ForegroundColor Yellow
$lastFiles = Get-ChildItem -Path $last -ErrorAction SilentlyContinue
foreach ($file in $lastFiles) { Copy-Item -Path $file.FullName -Destination $previous -Force }

Write-Host "Stage 3: Shifting current -> last..." -ForegroundColor Yellow
$currentFiles = Get-ChildItem -Path $current -ErrorAction SilentlyContinue
foreach ($file in $currentFiles) { Copy-Item -Path $file.FullName -Destination $last -Force }

Write-Host "Stage 4: Capturing current state from rag-deploy..." -ForegroundColor Yellow
$copied = 0
foreach ($file in $trackedFiles) { $sourcePath = "$sourceEcr\$file"; if (Test-Path $sourcePath) { Copy-Item -Path $sourcePath -Destination $current -Force; $copied++ } else { Write-Host "  WARNING: $file not found in ecr-build" -ForegroundColor Red } }
foreach ($file in $trackedHfFiles) { $sourcePath = "$sourceHf\$file"; if (Test-Path $sourcePath) { Copy-Item -Path $sourcePath -Destination $current -Force; $copied++ } else { Write-Host "  WARNING: $file not found in hf-space" -ForegroundColor Red } }

Write-Host "Rotation complete. $copied files captured in current layer." -ForegroundColor Green