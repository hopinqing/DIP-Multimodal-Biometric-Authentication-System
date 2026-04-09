@echo off
color 0B
title Electric Vault - Master Deployment Subsystem

echo =======================================================
echo     MULTIMODAL BIOMETRIC VAULT - SYSTEM INITIALIZATION
echo =======================================================
echo.

:: ---------------------------------------------------------
:: STEP 1: PYTHON PREREQUISITES (Auto-Install if missing)
:: ---------------------------------------------------------
echo [1/5] Verifying Python Installations...

:: Check for Python 3.13
py -3.13 --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Python 3.13 missing. Launching installer...
    echo IMPORTANT: Please check "Add Python to PATH" at the bottom of the installer!
    start /wait "" "0_Prerequisites\python-3.13.5-amd64.exe"
)

:: Check for Python 3.11
py -3.11 --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Python 3.11 missing. Launching installer...
    start /wait "" "0_Prerequisites\python-3.11.2-amd64.exe"
)

:: Check for Python 3.10 32-bit
py -3.10-32 --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [WARNING] Python 3.10 32-bit missing. Launching installer...
    start /wait "" "0_Prerequisites\python-3.10.11-win32.exe"
)
echo Python environments verified.
echo.

:: ---------------------------------------------------------
:: STEP 2: HARDWARE DRIVERS & SDKS (Auto-Install if missing)
:: ---------------------------------------------------------
echo [2/5] Checking Hardware Drivers and SDKs...

:: Futronic Driver Check
IF NOT EXIST "modules\fingerprint\ftrScanAPI.dll" (
    echo [WARNING] Futronic ftrScanAPI.dll not found in modules\fingerprint!
    echo Launching Futronic Driver setup...
    start /wait "" "0_Prerequisites\Futronic_Driver.exe"
    echo.
    echo ACTION REQUIRED: If the installer failed to place ftrScanAPI.dll in your fingerprint folder,
    echo please copy it manually before continuing!
    pause
)

:: IriTech Driver and SDK Check
IF NOT EXIST "C:\Program Files (x86)\IriTech\IDDK 2000 3.3.3 x64\SDK\Bin\Iddk2000.dll" (
    echo [WARNING] IriTech SDK not found!
    echo Launching IriShield USB Driver Setup...
    start /wait "" "0_Prerequisites\IriShield_USB_Driver.exe"
    
    echo Launching IriTech IDDK 2000 SDK Setup...
    start /wait "" "0_Prerequisites\IDDK_2000_x64_Setup.msi"
)
echo Drivers verified.
echo.

:: ---------------------------------------------------------
:: STEP 3: MAIN ENVIRONMENT (Python 3.13)
:: ---------------------------------------------------------
echo [3/5] Checking Main Virtual Environment...
IF NOT EXIST "venv_main\Scripts\activate.bat" (
    echo Creating venv_main...
    py -3.13 -m venv venv_main
    echo Installing dependencies...
    call venv_main\Scripts\activate.bat
    pip install opencv-python deepface numpy mediapipe tf-keras customtkinter Pillow
    call deactivate
) ELSE (
    echo venv_main already exists. Skipping install.
)
echo.

:: ---------------------------------------------------------
:: STEP 4: VOICE ENVIRONMENT (Python 3.11 - 64-bit)
:: ---------------------------------------------------------
echo [4/5] Checking Voice Virtual Environment...
cd modules\voice
IF NOT EXIST "venv_voice\Scripts\activate.bat" (
    echo Creating venv_voice...
    py -3.11 -m venv venv_voice
    echo Installing dependencies...
    call venv_voice\Scripts\activate.bat
    pip install torch torchaudio speechbrain sounddevice soundfile vosk
    call deactivate
) ELSE (
    echo venv_voice already exists. Skipping install.
)
cd ..\..
echo.

:: ---------------------------------------------------------
:: STEP 5: FINGERPRINT ENVIRONMENT (Python 3.10 - 32-bit)
:: ---------------------------------------------------------
echo [5/5] Checking Fingerprint Virtual Environment...
cd modules\fingerprint
IF NOT EXIST "venv_fingerprint\Scripts\activate.bat" (
    echo Creating venv_fingerprint...
    py -3.10-32 -m venv venv_fingerprint
    echo Installing dependencies...
    call venv_fingerprint\Scripts\activate.bat
    pip install opencv-python==4.6.0.66 numpy==1.22.4
    call deactivate
) ELSE (
    echo venv_fingerprint already exists. Skipping install.
)
cd ..\..
echo.

:: ---------------------------------------------------------
:: STEP 6: IRIS ENVIRONMENT (Python 3.11 - 64-bit)
:: ---------------------------------------------------------
echo [6/6] Checking Iris Virtual Environment...
cd modules\iris
IF NOT EXIST "venv_iris\Scripts\activate.bat" (
    echo Creating venv_iris...
    py -3.11 -m venv venv_iris
    echo Installing dependencies...
    call venv_iris\Scripts\activate.bat
    pip install opencv-python numpy
    call deactivate
) ELSE (
    echo venv_iris already exists. Skipping install.
)
cd ..\..
echo.

:: ---------------------------------------------------------
:: LAUNCH APPLICATION
:: ---------------------------------------------------------
echo All environments verified.
echo =======================================================
echo          LAUNCHING ELECTRIC VAULT DASHBOARD
echo =======================================================
echo.

call venv_main\Scripts\activate.bat
python main.py

:: If the app closes, deactivate and pause so the window doesn't instantly vanish
call deactivate
pause