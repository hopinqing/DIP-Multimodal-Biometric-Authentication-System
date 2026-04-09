# DIP-Multimodal-Biometric-Authentication-System
Finalised Code for Multimodal Biometric Authentication System (AY 25/26)

# Multimodal Biometric Authentication System

Built with a secure 4-Tier Risk Policy architecture, the system allows administrators to dynamically scale security requirements from single-vector authentication up to an absolute hardware lockdown requiring all four biometric signatures simultaneously.

---

## 1. Hardware Requirements
To run the full suite of biometric modules, the following hardware is required:
* **Webcam:** For facial feature extraction and enrollment.
* **Microphone:** For voice capture and audio processing.
* **Futronic FS88 Scanner:** For fingerprint minutiae extraction.
* **IriShield USB MK2120UL:** For near-infrared (NIR) iris scanning.

---

## 2. Software & Prerequisites
Due to the specific architectural requirements of proprietary hardware SDKs (32-bit vs. 64-bit), this system isolates its modules into dedicated virtual environments. 

Before running the deployment script, ensure the following installer files are placed exactly as named inside a folder called `0_Prerequisites` in the root directory:

**Python Installers:**
* `python-3.13.5-amd64.exe` (Main Dashboard)
* `python-3.11.2-amd64.exe` (Voice & Iris Modules)
* `python-3.10.11-win32.exe` (Fingerprint Module - **Must be 32-bit**)

**Hardware Drivers & SDKs:**
* `Futronic_Driver.exe` (Version 10.0.0.1 for Windows)
* `IriShield_USB_Driver.exe`
* `IDDK_2000_x64_Setup.exe` (IriTech SDK C/C++)

---

## 3. Installation & Deployment
The system utilizes an automated Windows Batch script to verify prerequisites, install missing dependencies, and build the isolated virtual environments.

**Step 1:** Double-click `DEPLOY_VAULT.bat`.

**Step 2:** The script will automatically check for the required Python versions. If a version is missing, it will launch the installer. *(Note: Ensure you check "Add Python to PATH" when installing Python 3.13).*

**Step 3:** The script will verify hardware drivers. If the required `.dll` files are missing, it will launch the necessary hardware setups.

**Step 4:** Allow the script to automatically build `venv_main`, `venv_voice`, `venv_fingerprint`, and `venv_iris`, and download all necessary PIP libraries.

**Step 5:** Upon completion, the script will automatically launch the Electric Vault dashboard.

*(For subsequent launches, you only need to double-click the `.bat` file, and it will skip directly to launching the app in under 2 seconds).*

---

## 4. How to Use the System

### A. System Administration (Risk Policies)
Click the **⚙️ Admin Panel** button to configure the active hardware and set the Time-Sensitive Risk Policy.
* **LOW:** Requires any 1 biometric vector to pass.
* **MEDIUM:** Requires any 2 biometric vectors to pass.
* **HIGH:** Requires any 3 biometric vectors to pass.
* **MAXIMUM:** Absolute lockdown; requires all active biometric modules to pass.

### B. Identity Enrollment
1. Enter a unique username in the **ACCESS ID** field.
2. Click **ENROLL**.
3. Follow the on-screen and audible prompts to register your biometrics.
   * **Note:** If a specific hardware module fails or is occluded during enrollment, the system will pause and ask if you want to retry that specific module without forcing you to restart the entire sequence.

### C. Authentication (Login)
1. Enter your registered username in the **ACCESS ID** field.
2. Click **LOGIN**.
3. The system will randomize the order of the active biometric challenges to prevent replay attacks.
4. Pass the required number of challenges based on the current Risk Policy to unlock the vault.

---

## 5. Security & Privacy Notes
* **Data Storage:** Facial vectors, fingerprint templates, and audio samples are securely processed into mathematical arrays.
* **Iris Vault:** Iris templates are strictly securely hashed and stored within a local SQLite database (`iris_auth.db`). 
* **1:1 Verification:** The system utilizes 1:1 verification, actively filtering the database by the requested Access ID before comparing mathematical distances, ensuring zero risk of cross-user false acceptance.
