$projectName = "MonitorIT"

$directories = @(
    "$projectName/app/static",
    "$projectName/profiles"
)

$files = @(
    "$projectName/app/__init__.py",
    "$projectName/app/open_browser.py",
    "$projectName/app/icmp_engine.py",
    "$projectName/app/main.py",
    "$projectName/app/profile_manager.py",
    "$projectName/app/static/app.js",
    "$projectName/app/static/index.html",
    "$projectName/app/static/style.css",
    "$projectName/profiles/sample_profile.json",
    "$projectName/LaunchIT.bat",
    "$projectName/README.md",
    "$projectName/TROUBLESHOOTING.md",
    "$projectName/requirements.txt"
)

foreach ($dir in $directories) {
    if (-not (Test-Path -Path $dir)) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
}

foreach ($file in $files) {
    if (-not (Test-Path -Path $file)) {
        New-Item -ItemType File -Force -Path $file | Out-Null
    }
}

Write-Host "Project structure for '$projectName' successfully created!" -ForegroundColor Green