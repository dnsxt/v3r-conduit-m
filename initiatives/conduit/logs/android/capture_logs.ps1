# capture_logs.ps1
# Captures 30 seconds of unfiltered ADB logcat output
# and saves it as an XML file to Desktop\android logs\
# Run from PowerShell. Device must be connected via ADB.

# Output directory
$outputDir = "$env:USERPROFILE\Desktop\android logs"

# Create directory if it does not exist
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

# Timestamp for unique filename
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outputFile = "$outputDir\logcat_$timestamp.xml"

Write-Host "Starting 30-second log capture..."
Write-Host "Output: $outputFile"
Write-Host "Do not close this window."

# Clear existing logcat buffer before capture
adb logcat -c

# Capture 30 seconds of unfiltered logcat
# -v long gives full metadata per line
$logLines = @()
$job = Start-Job -ScriptBlock {
    adb logcat -v long 2>&1
}

Start-Sleep -Seconds 30

Stop-Job $job
$logLines = Receive-Job $job
Remove-Job $job

# Build XML structure
$xml = New-Object System.Xml.XmlDocument
$declaration = $xml.CreateXmlDeclaration("1.0", "UTF-8", $null)
$xml.AppendChild($declaration) | Out-Null

$root = $xml.CreateElement("LogCapture")
$root.SetAttribute("device", (adb devices | Select-String "device$" | Select-Object -First 1))
$root.SetAttribute("capturedAt", (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
$root.SetAttribute("durationSeconds", "30")
$xml.AppendChild($root) | Out-Null

$lineCount = 0
foreach ($line in $logLines) {
    if ($line -and $line.Trim() -ne "") {
        $entry = $xml.CreateElement("LogLine")
        $entry.SetAttribute("index", $lineCount)

        # Wrap raw text in CDATA to preserve special characters
        $cdata = $xml.CreateCDataSection($line)
        $entry.AppendChild($cdata) | Out-Null

        $root.AppendChild($entry) | Out-Null
        $lineCount++
    }
}

# Save XML file
$xml.Save($outputFile)

Write-Host ""
Write-Host "Capture complete. $lineCount lines saved."
Write-Host "File: $outputFile"
