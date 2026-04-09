import cv2
import numpy as np
import os
import time
import ctypes
import tkinter as tk
from tkinter import messagebox
import sys
import argparse
import winsound  
import random 
import glob 

# ==========================================
# PATH CONFIGURATION
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(BASE_DIR, "ftrScanAPI.dll")
REPO_DIR = os.path.join(BASE_DIR, "Biometric_Repo")

if not os.path.exists(REPO_DIR): 
    os.makedirs(REPO_DIR)

# --- TUNED BIOMETRIC CONSTANTS ---
BLUR_THRESHOLD = 150.0   
SPOOF_THRESHOLD = 45.0   
FRAMES_TO_HOLD = 15      
MATCH_PASS_SCORE = 90    
SPOOF_KILL_LIMIT = 10    

FINGERS_TO_ENROLL = ["Left Thumb", "Right Thumb", "Left Index", "Right Index"]

# ==========================================
# 1. CORE FEATURE EXTRACTION
# ==========================================
def get_fs88_features_direct(image_path):
    img = cv2.imread(image_path, 0)
    if img is None: return None, None, None
    
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(img)
    
    sift = cv2.SIFT_create(edgeThreshold=10, contrastThreshold=0.03)
    kp, des = sift.detectAndCompute(enhanced, None)
    
    return kp, des, enhanced

# ==========================================
# 2. THE BIOMETRIC APP CLASS
# ==========================================
class FutronicBiometricApp:
    def __init__(self, root, mode, target_user):
        self.root = root
        self.root.title("FS88 Live Scanner")
        self.root.geometry("400x200")
        self.mode = mode
        self.target_user = target_user # NEW: The app now knows exactly who is scanning!
        
        tk.Label(root, text="FUTRONIC FS88 ACTIVE", font=("Arial", 14, "bold")).pack(pady=20)
        self.status = tk.Label(root, text="Status: Initializing...", font=("Arial", 12, "bold"), fg="#0984e3")
        self.status.pack(pady=20)

        if self.mode == "enroll":
            self.root.after(500, self.register_flow)
        else:
            self.root.after(500, self.authenticate_flow)

    def set_status(self, text, color="#0984e3"):
        self.status.config(text=text, fg=color)
        self.root.update()

    def capture_fingerprint_live(self, user_id, scan_type="Master", finger_name=""):
        safe_finger_name = finger_name.replace(" ", "")
        expected_file = os.path.join(REPO_DIR, f"{user_id}__{safe_finger_name}_{scan_type}.bmp")
        
        if os.path.exists(expected_file): 
            os.remove(expected_file)

        if not os.path.exists(DLL_PATH):
            messagebox.showerror("DLL Missing", f"Could not find {DLL_PATH}")
            return None
        
        try:
            ftr_api = ctypes.WinDLL(DLL_PATH)
            ftr_api.ftrScanOpenDevice.restype = ctypes.c_void_p
            hDevice = ftr_api.ftrScanOpenDevice()

            if not hDevice:
                messagebox.showerror("Scanner Error", "Could not connect to FS88 scanner.")
                return None

            class FTRSCAN_IMAGE_SIZE(ctypes.Structure):
                _fields_ = [("nWidth", ctypes.c_int), ("nHeight", ctypes.c_int), ("nImageSize", ctypes.c_int)]

            img_size = FTRSCAN_IMAGE_SIZE()
            if not ftr_api.ftrScanGetImageSize(hDevice, ctypes.byref(img_size)):
                return None

            buffer = (ctypes.c_ubyte * img_size.nImageSize)()
            
            cv2.namedWindow(f"FS88 Live Feed - {finger_name}", cv2.WINDOW_NORMAL)
            
            good_frames_held = 0
            spoof_frames_held = 0  
            captured = False
            
            current_state = "neutral" 
            
            while True:
                success = ftr_api.ftrScanGetImage(hDevice, 4, buffer)
                
                if success:
                    raw_data = bytes(buffer)
                    img_array = np.frombuffer(raw_data, dtype=np.uint8)
                    frame = img_array.reshape((img_size.nHeight, img_size.nWidth))
                    
                    blur_score = cv2.Laplacian(frame, cv2.CV_64F).var()
                    liveness_score = np.std(frame)
                    
                    display_frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    
                    is_sharp = blur_score > BLUR_THRESHOLD
                    is_live = liveness_score > SPOOF_THRESHOLD
                    
                    if not is_live:
                        if current_state != "spoof":
                            winsound.MessageBeep(winsound.MB_ICONHAND) 
                            current_state = "spoof"
                            
                        good_frames_held = 0
                        spoof_frames_held += 1
                        
                        color = (0, 0, 255)
                        msg = f"SPOOF DETECTED! Lock in: {SPOOF_KILL_LIMIT - spoof_frames_held}"
                        
                        if spoof_frames_held >= SPOOF_KILL_LIMIT:
                            cv2.putText(display_frame, "SYSTEM LOCKDOWN!", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                            cv2.imshow(f"FS88 Live Feed - {finger_name}", display_frame)
                            cv2.waitKey(500)
                            messagebox.showerror("SECURITY ALERT", "Presentation Attack Detected.\\nSystem Aborting.")
                            break 
                            
                    elif not is_sharp:
                        if current_state != "blur":
                            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION) 
                            current_state = "blur"
                            
                        good_frames_held = 0
                        spoof_frames_held = 0 
                        
                        color = (0, 165, 255) 
                        msg = f"TOO BLURRY! PRESS FLAT & HOLD..."
                        
                    else:
                        if current_state != "good":
                            winsound.MessageBeep(winsound.MB_OK) 
                            current_state = "good"
                            
                        spoof_frames_held = 0 
                        
                        color = (0, 255, 0)
                        msg = f"PERFECT! HOLD STILL... ({good_frames_held}/{FRAMES_TO_HOLD})"
                        good_frames_held += 1
                        
                    cv2.putText(display_frame, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    cv2.imshow(f"FS88 Live Feed - {finger_name}", display_frame)
                    
                    if good_frames_held >= FRAMES_TO_HOLD:
                        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS) 
                        cv2.imwrite(expected_file, frame)
                        captured = True
                        break
                        
                if cv2.waitKey(100) & 0xFF == 27: 
                    break
                self.root.update()

            cv2.destroyAllWindows()
            ftr_api.ftrScanCloseDevice(hDevice)
            
            if captured:
                return expected_file
            return None

        except Exception as e:
            messagebox.showerror("Hardware Crash", f"Error interacting with scanner:\n{e}")
            self.set_status("Hardware Error.", "#e74c3c")
            return None
    
    def register_flow(self):
        user_id = self.target_user # NEW: Uses the ID passed from main.py!
        
        self.set_status(f"CLEANING DATA FOR {user_id.upper()}...", "#f1c40f")
        old_files = glob.glob(os.path.join(REPO_DIR, f"{user_id}__*.bmp"))
        for file_path in old_files:
            try:
                os.remove(file_path)
            except Exception:
                pass
        
        for finger in FINGERS_TO_ENROLL:
            self.set_status(f"PREPARE: {finger.upper()}", "#f1c40f")
            time.sleep(1.5)
            
            captured_file = self.capture_fingerprint_live(user_id, scan_type="Master", finger_name=finger)
            
            if not captured_file:
                sys.exit(1)

        self.set_status("ENROLLMENT COMPLETE", "#2ecc71")
        time.sleep(0.5)
        sys.exit(0)

    def authenticate_flow(self):
        user_id = self.target_user # NEW: Uses the ID passed from main.py!

        for finger in FINGERS_TO_ENROLL:
            safe_finger_name = finger.replace(" ", "")
            if not os.path.exists(os.path.join(REPO_DIR, f"{user_id}__{safe_finger_name}_Master.bmp")):
                messagebox.showerror("Error", f"User '{user_id}' has no template for {finger}. Please enroll.")
                sys.exit(1)

        requested_finger = random.choice(FINGERS_TO_ENROLL)

        self.set_status(f"CHALLENGE: Place {requested_finger.upper()}", "#f1c40f")
        time.sleep(1.5)
        
        captured_live = self.capture_fingerprint_live(user_id, scan_type="Live", finger_name=requested_finger)
        
        if not captured_live:
            sys.exit(1)
            
        kp_live, des_live, _ = get_fs88_features_direct(captured_live)
        
        if des_live is None or len(des_live) < 2:
            sys.exit(1)

        best_score = 0
        bf = cv2.BFMatcher()
        
        safe_finger_name = requested_finger.replace(" ", "")
        master_path = os.path.join(REPO_DIR, f"{user_id}__{safe_finger_name}_Master.bmp")
        _, des_master, _ = get_fs88_features_direct(master_path)
        
        if des_master is not None and len(des_master) > 1:
            matches = bf.knnMatch(des_master, des_live, k=2)
            good = []
            for match in matches:
                if len(match) == 2:
                    m, n = match
                    if m.distance < 0.75 * n.distance:
                        good.append(m)
            
            best_score = len(good)

        if best_score >= MATCH_PASS_SCORE: 
            self.set_status("VERIFIED", "#2ecc71")
            sys.exit(0) 
        else:
            self.set_status("DENIED", "#e74c3c")
            sys.exit(1) 

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enroll", action="store_true")
    # NEW: We add a listener for the user ID. It defaults to "DemoUser" just in case.
    parser.add_argument("--user", type=str, default="DemoUser", help="The ID of the user")
    args = parser.parse_args()
    
    mode = "enroll" if args.enroll else "auth"
    
    root = tk.Tk()
    # Pass the user ID into the app
    app = FutronicBiometricApp(root, mode, args.user)
    root.mainloop()