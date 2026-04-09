import os
import cv2
import time
import json
import sqlite3
import ctypes
import hashlib
import datetime as dt
import numpy as np
from pathlib import Path
import argparse
import sys
import winsound
import tkinter as tk
import threading
import random

# =========================
# Configuration
# =========================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "iris_auth.db")

# Recognition thresholds
VERIFY_ACCEPT_THRESHOLD = 0.2
VERIFY_MARGIN_THRESHOLD = 0.02

# SDK Path - MUST BE INSTALLED ON YOUR PC
IDDK_DLL_PATH = r"C:\Program Files (x86)\IriTech\IDDK 2000 3.3.3 x64\SDK\Bin\Iddk2000.dll"

# =========================
# Database & Helpers
# =========================
def utcnow_str():
    return dt.datetime.utcnow().isoformat(timespec="seconds")

def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def np_to_blob(arr: np.ndarray) -> bytes:
    return arr.astype(np.uint8).tobytes()

def blob_to_np(blob: bytes, shape, dtype=np.uint8) -> np.ndarray:
    return np.frombuffer(blob, dtype=dtype).reshape(shape).copy()

def ensure_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, user_hash TEXT NOT NULL, created_at TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS templates (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, eye_side TEXT NOT NULL, radial_res INTEGER NOT NULL, angular_res INTEGER NOT NULL, channels INTEGER NOT NULL, template_blob BLOB NOT NULL, quality_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(user_id) REFERENCES users(user_id))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS auth_state (user_id TEXT PRIMARY KEY, failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT, FOREIGN KEY(user_id) REFERENCES users(user_id))""")
    conn.commit()
    conn.close()

def db_connect():
    return sqlite3.connect(DB_PATH)

def create_user_if_missing(user_id):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
    if cur.fetchone() is None:
        cur.execute("INSERT INTO users (user_id, user_hash, created_at, disabled) VALUES (?, ?, ?, 0)", (user_id, sha256_text(user_id), utcnow_str()))
        cur.execute("INSERT OR IGNORE INTO auth_state (user_id, failed_attempts, locked_until) VALUES (?, 0, NULL)", (user_id,))
        conn.commit()
    conn.close()

def insert_template(user_id, eye_side, template, quality):
    radial_res, angular_res = template.shape[1], template.shape[2]
    channels = template.shape[0]
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("""INSERT INTO templates (user_id, eye_side, radial_res, angular_res, channels, template_blob, quality_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", 
                (user_id, eye_side, radial_res, angular_res, channels, sqlite3.Binary(np_to_blob(template)), json.dumps(quality), utcnow_str()))
    conn.commit()
    conn.close()

def load_templates(user_id, eye_side):
    conn = db_connect()
    cur = conn.cursor()
    cur.execute("SELECT radial_res, angular_res, channels, template_blob FROM templates WHERE user_id=? AND eye_side=?", (user_id, eye_side))
    rows = cur.fetchall()
    conn.close()
    
    out = []
    for radial_res, angular_res, channels, blob in rows:
        out.append(blob_to_np(blob, (channels, radial_res, angular_res), dtype=np.uint8))
    return out

# =========================
# SDK Scanner Integration
# =========================
class IddkImage(ctypes.Structure):
    _fields_ = [("imageKind", ctypes.c_ubyte), ("imageFormat", ctypes.c_ubyte), ("imageWidth", ctypes.c_int), ("imageHeight", ctypes.c_int), ("imageData", ctypes.POINTER(ctypes.c_ubyte)), ("imageDataLen", ctypes.c_int)]

class IrisScannerSDK:
    def __init__(self, dll_path=IDDK_DLL_PATH):
        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"SDK DLL not found at {dll_path}. Please install IriTech SDK.")
        self.iddk = ctypes.CDLL(dll_path)
        self._configure_functions()

    def _configure_functions(self):
        self.iddk.Iddk_ScanDevices.argtypes = [ctypes.POINTER(ctypes.POINTER(ctypes.c_char_p)), ctypes.POINTER(ctypes.c_int)]
        self.iddk.Iddk_OpenDevice.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
        self.iddk.Iddk_InitCamera.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self.iddk.Iddk_StartCapture.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ubyte, ctypes.c_void_p, ctypes.c_void_p]
        self.iddk.Iddk_GetCaptureStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        self.iddk.Iddk_GetResultImage.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ubyte, ctypes.POINTER(ctypes.POINTER(IddkImage)), ctypes.POINTER(ctypes.c_int)]
        self.iddk.Iddk_StopCapture.argtypes = [ctypes.c_void_p]
        self.iddk.Iddk_DeinitCamera.argtypes = [ctypes.c_void_p]
        self.iddk.Iddk_CloseDevice.argtypes = [ctypes.c_void_p]

    def capture_image(self):
        device_descs, device_count = ctypes.POINTER(ctypes.c_char_p)(), ctypes.c_int()
        if self.iddk.Iddk_ScanDevices(ctypes.byref(device_descs), ctypes.byref(device_count)) != 0 or device_count.value == 0:
            raise ValueError("No iris scanner found. Check USB connection.")

        h_device = ctypes.c_void_p()
        self.iddk.Iddk_OpenDevice(device_descs[0], ctypes.byref(h_device))

        try:
            w, h = ctypes.c_int(), ctypes.c_int()
            self.iddk.Iddk_InitCamera(h_device, ctypes.byref(w), ctypes.byref(h))
            self.iddk.Iddk_StartCapture(h_device, 1, 3, 1, 1, 0, 1, None, None)

            status = ctypes.c_int()
            deadline = time.time() + 60.0 
            
            while time.time() < deadline:
                self.iddk.Iddk_GetCaptureStatus(h_device, ctypes.byref(status))
                if status.value == 3: break # IDDK_COMPLETE
                if status.value == 5: raise ValueError("Capture aborted by scanner")
                time.sleep(0.1)
            else:
                raise ValueError("Capture timed out")

            images, max_eye = ctypes.POINTER(IddkImage)(), ctypes.c_int()
            self.iddk.Iddk_GetResultImage(h_device, 1, 2, 1, ctypes.byref(images), ctypes.byref(max_eye))
            
            img = images[0]
            raw = ctypes.string_at(img.imageData, img.imageWidth * img.imageHeight)
            np_img = np.frombuffer(raw, dtype=np.uint8).reshape((img.imageHeight, img.imageWidth)).copy()
            return np_img

        finally:
            try: self.iddk.Iddk_StopCapture(h_device)
            except: pass
            try: self.iddk.Iddk_DeinitCamera(h_device)
            except: pass
            try: self.iddk.Iddk_CloseDevice(h_device)
            except: pass

# =========================
# Iris Processing Pipeline
# =========================
def preprocess(img):
    return cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(cv2.medianBlur(img, 5))

def detect_pupil(img):
    circles = cv2.HoughCircles(255 - cv2.GaussianBlur(img, (9, 9), 2), cv2.HOUGH_GRADIENT, 1.5, img.shape[0] // 4, param1=100, param2=20, minRadius=20, maxRadius=80)
    if circles is None: raise ValueError("OCCLUSION: Pupil not detected. Open eyes wider.")
    return np.round(circles[0][0]).astype(int) 

def detect_iris(img, pupil):
    px, py, pr = pupil
    return (px, py, max(pr + 20, int(pr * 2.3))) 

def normalize_iris(img, pupil, iris, radial_res=64, angular_res=256):
    px, py, pr = pupil
    ix, iy, ir = iris
    theta = np.linspace(0, 2 * np.pi, angular_res, endpoint=False)
    r = np.linspace(0, 1, radial_res)
    strip = np.zeros((radial_res, len(theta)), dtype=np.uint8)

    for j, t in enumerate(theta):
        xs = np.clip((1.0 - r) * (px + pr * np.cos(t)) + r * (ix + ir * np.cos(t)), 0, img.shape[1] - 1)
        ys = np.clip((1.0 - r) * (py + pr * np.sin(t)) + r * (iy + ir * np.sin(t)), 0, img.shape[0] - 1)
        strip[:, j] = img[ys.astype(int), xs.astype(int)]
    return strip

def build_gabor_kernels():
    return [cv2.getGaborKernel((21, 21), s, 0, l, 0.5, p, cv2.CV_32F) for s, l, p in [(9.0, 8.0, 0.0), (9.0, 16.0, 0.0), (12.0, 8.0, np.pi/2), (12.0, 16.0, np.pi/2)]]

GABOR_KERNELS = build_gabor_kernels()

def extract_template(strip):
    img = strip.astype(np.float32) / 255.0
    return np.stack([(cv2.filter2D(img, cv2.CV_32F, k) > 0).astype(np.uint8) for k in GABOR_KERNELS], axis=0)

def image_to_template(img):
    prep = preprocess(img)
    pupil = detect_pupil(prep)
    iris = detect_iris(prep, pupil)
    strip = normalize_iris(prep, pupil, iris)
    
    # --- NEW: EYELID & EYELASH OCCLUSION CHECK ---
    # Bottom of eye maps to ~column 64. Top of eye maps to ~column 192.
    bottom_region = strip[:, 45:85] 
    top_region = strip[:, 170:210]  
    
    # Eyelashes and heavy shadows register as very dark pixels (< 50) in NIR
    bottom_dark_ratio = np.sum(bottom_region < 50) / bottom_region.size
    top_dark_ratio = np.sum(top_region < 50) / top_region.size
    
    # If more than 35% of the top or bottom area is pitch black, it's occluded!
    if top_dark_ratio > 0.35 or bottom_dark_ratio > 0.35:
        raise ValueError("OCCLUSION: Eyelids/Lashes blocking scan.")
        
    tpl = extract_template(strip)
    return tpl, {"quality": float(np.std(strip))}, pupil, iris

def hamming_distance(t1, t2):
    return np.mean(t1 != t2)

def match_templates(query, enrolled, max_shift=16):
    return min(hamming_distance(np.roll(query, shift=s, axis=2), enrolled) for s in range(-max_shift, max_shift + 1))

# =========================
# UX GUI & FLOW
# =========================
class IrisApp:
    def __init__(self, mode, user_id):
        self.root = tk.Tk()
        self.root.title(f"IriShield Scanner - {user_id.upper()}")
        self.root.geometry("550x300")
        self.root.configure(bg="#0D1321")
        self.root.attributes("-topmost", True)
        
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() // 2) - (550 // 2)
        y = (self.root.winfo_screenheight() // 2) - (300 // 2)
        self.root.geometry(f"+{x}+{y}")

        self.user_id = user_id
        
        self.lbl_title = tk.Label(self.root, text="IRISHIELD ACTIVE", font=("Segoe UI", 16, "bold"), bg="#0D1321", fg="#00D4FF")
        self.lbl_title.pack(pady=(20, 10))
        
        self.lbl_instruction = tk.Label(self.root, text="INITIALIZING...", font=("Consolas", 20, "bold"), bg="#0D1321", fg="#FFFFFF")
        self.lbl_instruction.pack(pady=20)
        
        self.lbl_status = tk.Label(self.root, text="Please wait.", font=("Segoe UI", 12), bg="#0D1321", fg="#8A9BB3")
        self.lbl_status.pack(pady=10)

        ensure_db()
        create_user_if_missing(self.user_id)
        
        if mode == "enroll":
            threading.Thread(target=self.enroll_worker, daemon=True).start()
        else:
            threading.Thread(target=self.auth_worker, daemon=True).start()

    def update_ui(self, instruction, status, color="#FFFFFF"):
        self.lbl_instruction.config(text=instruction, fg=color)
        self.lbl_status.config(text=status)
        self.root.update()

    def show_visual_feedback(self, img, pupil, iris, title="Biometric Capture"):
        try:
            disp = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            cv2.circle(disp, (pupil[0], pupil[1]), pupil[2], (0, 255, 0), 2) 
            cv2.circle(disp, (iris[0], iris[1]), iris[2], (0, 165, 255), 2)  
            
            cv2.imshow(title, disp)
            cv2.waitKey(1500) 
            cv2.destroyAllWindows()
        except Exception as e:
            pass

    def enroll_worker(self):
        try:
            scanner = IrisScannerSDK()
            eyes = ["LEFT", "RIGHT"]
            
            for eye in eyes:
                self.update_ui(f"PREPARE: {eye} EYE", "Place scanner over eye. Starting in 4 seconds...", "#F39C12")
                winsound.Beep(800, 200)
                time.sleep(1)
                winsound.Beep(800, 200)
                time.sleep(1)
                winsound.Beep(800, 200)
                time.sleep(1)
                winsound.Beep(1200, 500) 
                
                samples_captured = 0
                while samples_captured < 3:
                    self.update_ui(f"SCANNING {eye} EYE", f"Capturing sample {samples_captured+1}/3. Look straight...", "#00D4FF")
                    img = scanner.capture_image()
                    
                    try:
                        self.update_ui("PROCESSING...", "Extracting Gabor features...")
                        tpl, quality, pupil, iris_coords = image_to_template(img)
                        
                        winsound.Beep(1500, 200) 
                        self.show_visual_feedback(img, pupil, iris_coords, title=f"{eye} EYE - Scan {samples_captured+1}/3")
                        insert_template(self.user_id, eye.lower(), tpl, quality)
                        samples_captured += 1
                        time.sleep(0.5)
                        
                    except ValueError as ve:
                        # --- RETRY LOOP TRIGGERED IF OCCLUDED ---
                        if "OCCLUSION" in str(ve):
                            self.update_ui("POOR SCAN", "OPEN EYES WIDER! Retrying...", "#FFA500")
                            winsound.Beep(400, 500)
                            time.sleep(2)
                        else:
                            raise ve # True hardware failure
            
            self.update_ui("ENROLLMENT COMPLETE", "All vectors secured.", "#00FFA3")
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS)
            time.sleep(2)
            self.root.after(0, self.root.destroy)
            os._exit(0)
            
        except Exception as e:
            print(f"[ERROR] {e}")
            self.update_ui("HARDWARE ERROR", str(e), "#FF3366")
            winsound.MessageBeep(winsound.MB_ICONHAND)
            time.sleep(4)
            self.root.after(0, self.root.destroy)
            os._exit(1)

    def auth_worker(self):
        try:
            scanner = IrisScannerSDK()
            target_eye = random.choice(["LEFT", "RIGHT"])
            
            enrolled_templates = load_templates(self.user_id, target_eye.lower())
            if not enrolled_templates:
                self.update_ui("ACCESS DENIED", f"No {target_eye} eye templates found in vault.", "#FF3366")
                winsound.MessageBeep(winsound.MB_ICONHAND)
                time.sleep(3)
                os._exit(1)

            self.update_ui(f"CHALLENGE: {target_eye} EYE", "Place scanner over eye. Starting in 3 seconds...", "#F39C12")
            winsound.Beep(800, 200)
            time.sleep(1)
            winsound.Beep(800, 200)
            time.sleep(1)
            winsound.Beep(1200, 500)
            
            # --- RETRY LOOP FOR AUTHENTICATION ---
            success = False
            for attempt in range(3): # Give them 3 tries to open their eyes wider
                self.update_ui(f"SCANNING {target_eye} EYE", "Look straight and hold still...", "#00D4FF")
                img = scanner.capture_image()
                self.update_ui("ANALYZING...", "Comparing mathematical distances...")
                
                try:
                    query_tpl, _, pupil, iris_coords = image_to_template(img)
                    success = True
                    break # Escapes the retry loop if successful
                except ValueError as ve:
                    if "OCCLUSION" in str(ve):
                        self.update_ui("POOR SCAN", "OPEN EYES WIDER! Retrying...", "#FFA500")
                        winsound.Beep(400, 500)
                        time.sleep(2)
                    else:
                        raise ve
            
            if not success:
                self.update_ui("ACCESS DENIED", "Failed to capture clear eye structure.", "#FF3366")
                winsound.Beep(400, 500)
                time.sleep(3)
                os._exit(1)

            self.show_visual_feedback(img, pupil, iris_coords, title=f"Verification: {target_eye} EYE")
            
            best_score = min(match_templates(query_tpl, t) for t in enrolled_templates)
            
            print(f"\n[DEBUG] Iris Verification for {self.user_id}", flush=True)
            print(f"[DEBUG] Eye Checked: {target_eye}", flush=True)
            print(f"[DEBUG] Target Threshold: <= {VERIFY_ACCEPT_THRESHOLD}", flush=True)
            print(f"[DEBUG] Actual Score: {best_score:.4f}", flush=True)

            if best_score <= VERIFY_ACCEPT_THRESHOLD:
                self.update_ui(f"ACCESS GRANTED", f"Iris Match Score: {best_score:.4f}", "#00FFA3")
                winsound.Beep(1500, 250)
                time.sleep(2.5)
                self.root.after(0, self.root.destroy)
                os._exit(0)
            else:
                self.update_ui(f"ACCESS DENIED", f"Iris Match Score: {best_score:.4f}", "#FF3366")
                winsound.Beep(400, 500)
                time.sleep(2.5)
                self.root.after(0, self.root.destroy)
                os._exit(1)

        except Exception as e:
            print(f"[ERROR] {e}")
            self.update_ui("HARDWARE ERROR", str(e), "#FF3366")
            winsound.MessageBeep(winsound.MB_ICONHAND)
            time.sleep(4)
            self.root.after(0, self.root.destroy)
            os._exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enroll", action="store_true")
    parser.add_argument("--user", type=str, required=True)
    args = parser.parse_args()
    
    mode = "enroll" if args.enroll else "auth"
    app = IrisApp(mode, args.user)
    app.root.mainloop()