# ===================== CONFIG (edit if needed) ===============================

# Temporary folder for the virtual environment
$TempDir = 'C:\temp'

# Project folder.
# Uses the folder where this script is located.
$DevRoot = $PSScriptRoot

# Base Python interpreter
$BasePython = 'C:\Users\Grro\AppData\Local\Programs\Python\Python313\python.exe'

# Virtual environment
$VenvName = 'pdfminimalproofreader\_venv'
$VenvDir  = Join-Path $TempDir $VenvName

# Files/executables inside the venv
$Py       = Join-Path $VenvDir 'Scripts\python.exe'
$Pip      = Join-Path $VenvDir 'Scripts\pip.exe'
$Uvicorn  = Join-Path $VenvDir 'Scripts\uvicorn.exe'
$Activate = Join-Path $VenvDir 'Scripts\Activate.ps1'

# Requirements file
$Requirements = Join-Path $DevRoot 'requirements.txt'

# FastAPI application
$App  = 'app.main:app'
$Port = 8060

# ===================== END CONFIG ===========================================


$ErrorActionPreference = 'Stop'


function Invoke-InDir {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][scriptblock]$ScriptBlock
    )

    Push-Location $Path

    try {
        & $ScriptBlock

        if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            throw "Command failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
}


function Assert-LastExitCode {
    param(
        [Parameter(Mandatory)][string]$Operation
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}


$CurrentDir = $PWD.Path


try {

    Write-Host ""
    Write-Host "PDF Minimal ProofReader" -ForegroundColor Cyan
    Write-Host "=======================" -ForegroundColor Cyan
    Write-Host ""


    # ------------------------------------------------------------------------
    # Basic checks
    # ------------------------------------------------------------------------

    if (-not (Test-Path $BasePython)) {
        throw "Python interpreter not found: $BasePython"
    }

    if (-not (Test-Path $DevRoot)) {
        throw "Project directory not found: $DevRoot"
    }

    if (-not (Test-Path $Requirements)) {
        throw "requirements.txt not found: $Requirements"
    }


    # ------------------------------------------------------------------------
    # Prepare temp folder
    # ------------------------------------------------------------------------

    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null


    # ------------------------------------------------------------------------
    # Create virtual environment
    #
    # Important:
    # We deliberately use --without-pip here.
    #
    # On this machine, Python 3.13 has been observed to hang when "venv"
    # automatically invokes ensurepip during environment creation.
    #
    # We therefore:
    #   1. create the venv without pip
    #   2. run ensurepip explicitly
    #   3. upgrade pip explicitly
    # ------------------------------------------------------------------------

    if (-not (Test-Path $Py)) {

        Write-Host "Creating virtual environment:" -ForegroundColor Yellow
        Write-Host "  $VenvDir" -ForegroundColor Yellow
        Write-Host ""

        & $BasePython -m venv --without-pip $VenvDir
        Assert-LastExitCode "Creating virtual environment"


        Write-Host "Installing pip in virtual environment..." -ForegroundColor Cyan
        Write-Host ""

        & $Py -m ensurepip --upgrade
        Assert-LastExitCode "Installing pip"


        Write-Host "Upgrading pip..." -ForegroundColor Cyan
        Write-Host ""

        & $Py -m pip install --upgrade pip
        Assert-LastExitCode "Upgrading pip"
    }
    else {
        Write-Host "Using existing virtual environment:" -ForegroundColor Green
        Write-Host "  $VenvDir"
        Write-Host ""
    }


    # ------------------------------------------------------------------------
    # Activate virtual environment
    # ------------------------------------------------------------------------

    if (-not (Test-Path $Activate)) {
        throw "Virtual environment activation script not found: $Activate"
    }

    . $Activate


    # ------------------------------------------------------------------------
    # Show Python environment
    # ------------------------------------------------------------------------

    Write-Host "Python environment:" -ForegroundColor Cyan

    & $Py --version
    Assert-LastExitCode "Checking Python version"

    & $Py -m pip --version
    Assert-LastExitCode "Checking pip version"

    Write-Host ""


    # ------------------------------------------------------------------------
    # Update repository
    # ------------------------------------------------------------------------

    Write-Host "Updating Git repository..." -ForegroundColor Cyan
    Write-Host ""

    Invoke-InDir -Path $DevRoot -ScriptBlock {
        git.exe pull
    }


    # ------------------------------------------------------------------------
    # Install/update project dependencies
    # ------------------------------------------------------------------------

    Write-Host ""
    Write-Host "Installing Python requirements..." -ForegroundColor Cyan
    Write-Host ""

    Invoke-InDir -Path $DevRoot -ScriptBlock {
        & $Py -m pip install -r $Requirements
    }


    # ------------------------------------------------------------------------
    # Ensure Uvicorn standard extras are installed
    # ------------------------------------------------------------------------

    Write-Host ""
    Write-Host "Updating Uvicorn..." -ForegroundColor Cyan
    Write-Host ""

    & $Py -m pip install --upgrade "uvicorn[standard]"
    Assert-LastExitCode "Updating Uvicorn"


    # ------------------------------------------------------------------------
    # Check that the application can be imported
    # ------------------------------------------------------------------------

    Write-Host ""
    Write-Host "Checking FastAPI application..." -ForegroundColor Cyan
    Write-Host ""

    Invoke-InDir -Path $DevRoot -ScriptBlock {
        & $Py -c "from app.main import app; print('FastAPI application imported successfully.')"
    }


    # ------------------------------------------------------------------------
    # Start service
    # ------------------------------------------------------------------------

    Write-Host ""
    Write-Host "Starting PDF Minimal ProofReader..." -ForegroundColor Green
    Write-Host ""
    Write-Host "URL: http://127.0.0.1:$Port" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Press Ctrl+C to stop the service." -ForegroundColor DarkGray
    Write-Host ""


    Invoke-InDir -Path $DevRoot -ScriptBlock {

        & $Py -m uvicorn `
            $App `
            --reload `
            --host 127.0.0.1 `
            --port $Port `
            --log-level debug
    }

}
catch {

    Write-Host ""
    Write-Host "ERROR" -ForegroundColor Red
    Write-Host "-----" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""

    exit 1

}
finally {

    if (Get-Command deactivate -ErrorAction SilentlyContinue) {
        deactivate
    }

    Set-Location $CurrentDir
}