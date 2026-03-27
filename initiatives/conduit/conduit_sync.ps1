# conduit_sync.ps1
# V3R CONDUIT — Master Sync Script
# Run at end of every V3R/NEXUS/CONDUIT session

$rclone = "C:\Users\Source-1\Desktop\rclone\rclone.exe"
$filter = "C:\Users\Source-1\Desktop\rclone\v3r_filter.txt"
$logFile = "C:\Users\Source-1\Desktop\V3R\conduit\logs\session_sync_log.txt"
$conduitScript = "C:\Users\Source-1\Desktop\V3R\conduit\rotate_versions.ps1"
$sourceEcr = "C:\Users\Source-1\Desktop\rag-deploy\ecr-build"
$sourceHf = "C:\Users\Source-1\Desktop\rag-deploy\hf-space"
$sourceReportsCore = "C:\Users\Source-1\Desktop\V3R\core\reports"
$sourceFrameworks = "C:\Users\Source-1\Desktop\V3R\frameworks"
$sourceConduit = "C:\Users\Source-1\Desktop\V3R\conduit"
$sourceNexus = "C:\Users\Source-1\Desktop\V3R\nexus"
$sourceData = "C:\Users\Source-1\Desktop\V3R\data"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

New-Item -ItemType Directory -Force -Path "C:\Users\Source-1\Desktop\V3R\conduit\logs" | Out-Null

Write-Host "========================================" -ForegroundColor magenta
Write-Host "  V3R CONDUIT SYNC — $timestamp" -ForegroundColor magenta
Write-Host "========================================" -ForegroundColor magenta
Add-Content -Path $logFile -Value "`n========================================"
Add-Content -Path $logFile -Value "CONDUIT SYNC — $timestamp"
Add-Content -Path $logFile -Value "========================================"

Write-Host "`n[Stage 1] Running version rotation..." -ForegroundColor Yellow
& $conduitScript
Add-Content -Path $logFile -Value "[Stage 1] Version rotation complete"

Write-Host "`n[Stage 2] Syncing ecr-build code to Drive V3R/code/current/..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter $sourceEcr gdrive:V3R/code/current/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 2] ecr-build sync complete"

Write-Host "`n[Stage 3] Syncing hf-space app.py to Drive V3R/code/current/..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter $sourceHf gdrive:V3R/code/current/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 3] hf-space sync complete"

Write-Host "`n[Stage 4] Syncing version backup layers to Drive..." -ForegroundColor Yellow
& $rclone copy  "C:\Users\Source-1\Desktop\V3R\code\last" gdrive:V3R/code/last/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
& $rclone copy  "C:\Users\Source-1\Desktop\V3R\code\previous" gdrive:V3R/code/previous/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 4] Version backup sync complete"

Write-Host "`n[Stage 5] Syncing core reports to Drive..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter "$sourceReportsCore\project" gdrive:V3R/core/reports/project/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
& $rclone copy --filter-from $filter "$sourceReportsCore\coding" gdrive:V3R/core/reports/coding/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
& $rclone copy --filter-from $filter "$sourceReportsCore\handoffs" gdrive:V3R/core/reports/handoffs/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 5] Core reports sync complete"

Write-Host "`n[Stage 6] Syncing frameworks to Drive..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter "$sourceFrameworks\current" gdrive:V3R/frameworks/current/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 6] Frameworks sync complete"

Write-Host "`n[Stage 7] Syncing CONDUIT scripts to Drive..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter $sourceConduit gdrive:V3R/conduit/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 7] CONDUIT sync complete"

Write-Host "`n[Stage 8] Syncing NEXUS reports to Drive..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter "$sourceNexus\reports" gdrive:V3R/nexus/reports/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 8] NEXUS sync complete"

Write-Host "`n[Stage 9] Syncing data files to Drive..." -ForegroundColor Yellow
& $rclone copy --filter-from $filter $sourceData gdrive:V3R/data/ -v 2>&1 | Tee-Object -Append -FilePath $logFile
Add-Content -Path $logFile -Value "[Stage 9] Data sync complete"

$endTime = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  CONDUIT SYNC COMPLETE — $endTime" -ForegroundColor Green
Write-Host "  Run Apps Script converter for new Doc pairs." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Add-Content -Path $logFile -Value "SYNC COMPLETE — $endTime"
Add-Content -Path $logFile -Value "Reminder: Run Apps Script converter for new Doc pairs."