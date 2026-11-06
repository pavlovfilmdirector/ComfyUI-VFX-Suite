# PowerShell script to download missing official LTX IC-LoRAs from Hugging Face
# Target directories:
#   B:\StabilityMatrix2\ComfyUI\models\loras\LTX-2.3
#   B:\StabilityMatrix2\ComfyUI\models\loras\LTX-2.0

$LorasDir23 = "B:\StabilityMatrix2\ComfyUI\models\loras\LTX-2.3"
$LorasDir20 = "B:\StabilityMatrix2\ComfyUI\models\loras\LTX-2.0"

if (!(Test-Path $LorasDir23)) { New-Item -ItemType Directory -Path $LorasDir23 -Force }
if (!(Test-Path $LorasDir20)) { New-Item -ItemType Directory -Path $LorasDir20 -Force }

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "   Downloading Missing LTX IC-LoRAs from Hugging Face     " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. LTX-2.3 Instant-Shave IC-LoRA (Digital Beard Removal)
$shaveFile = "$LorasDir23\ltx-2.3-22b-ic-lora-instant-shave-0.9.safetensors"
if (Test-Path $shaveFile) {
    Write-Host "[1/2] Instant-Shave IC-LoRA already exists, skipping." -ForegroundColor Green
} else {
    Write-Host "`n[1/2] Downloading LTX-2.3 Instant-Shave IC-LoRA (~1.3 GB)..." -ForegroundColor Yellow
    hf download Lightricks/LTX-2.3-22b-IC-LoRA-Instant-Shave ltx-2.3-22b-ic-lora-instant-shave-0.9.safetensors --local-dir $LorasDir23
}

# 2. LTX-2.0 Pose-Control IC-LoRA (19B)
$poseFile = "$LorasDir20\ltx-2-19b-ic-lora-pose-control.safetensors"
if (Test-Path $poseFile) {
    Write-Host "[2/2] Pose-Control IC-LoRA already exists, skipping." -ForegroundColor Green
} else {
    Write-Host "`n[2/2] Downloading LTX-2-19b Pose-Control IC-LoRA..." -ForegroundColor Yellow
    hf download Lightricks/LTX-2-19b-IC-LoRA-Pose-Control ltx-2-19b-ic-lora-pose-control.safetensors --local-dir $LorasDir20
}

Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host "   Download complete! All official IC-LoRAs are ready.    " -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Cyan
