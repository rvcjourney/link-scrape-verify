$python = "C:\Users\Prathmesh.Piyush_MOTM\anaconda3\python.exe"
$script = Join-Path $PSScriptRoot "app.py"

Write-Host "Open http://localhost:5000" -ForegroundColor Cyan
& $python $script
