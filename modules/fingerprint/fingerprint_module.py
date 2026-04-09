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
PRESENCE_THRESHOLD = 20.0 
SPOOF_THRESHOLD = 45.0   
FRAMES_TO_HOLD = 15      
SPOOF_KILL_LIMIT = 10    

# --- SECURITY TUNING ---
MATCH_PASS_SCORE = 35    
RATIO_TEST_STRICTNESS = 0.70

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
        self.target_user = target_user 
        
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
            
            window_name = f"FS88 Live Feed - {finger_name}"
            cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window_name, 400, 550) 
            
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
                    h, w = frame.shape
                    
                    blur_score = cv2.Laplacian(frame, cv2.CV_64F).var()
                    contrast_score = np.std(frame) 
                    
                    # --- FIXED: TRUE BOUNDING BOX FOR WHITE RIDGES ---
                    # 1. Isolate the bright white ridges (ignoring black background)
                    _, binary_mask = cv2.threshold(frame, 80, 255, cv2.THRESH_BINARY)
                    kernel = np.ones((5,5), np.uint8)
                    clean_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, kernel)
                    
                    # 2. Check if the center of the scanner has enough ridge density
                    roi = clean_mask[int(h*0.3):int(h*0.7), int(w*0.3):int(w*0.7)]
                    center_density = np.sum(roi == 255) / roi.size
                    
                    # 3. Find absolute limits of all white pixels on the screen
                    y_indices, x_indices = np.where(clean_mask == 255)
                    vertical_ratio = 0.0
                    horizontal_ratio = 0.0
                    box_coords = None
                    
                    if len(y_indices) > 0:
                        ymin, ymax = np.min(y_indices), np.max(y_indices)
                        xmin, xmax = np.min(x_indices), np.max(x_indices)
                        cw = xmax - xmin
                        ch = ymax - ymin
                        vertical_ratio = ch / h
                        horizontal_ratio = cw / w
                        box_coords = (xmin, ymin, cw, ch)
                        
                    display_frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
                    
                    # Logic Gates
                    is_finger_present = contrast_score > PRESENCE_THRESHOLD
                    is_centered = center_density > 0.25 # Center must be at least 25% white ridges
                    is_large_enough = (vertical_ratio > 0.85) and (horizontal_ratio > 0.85)
                    is_sharp = blur_score > BLUR_THRESHOLD
                    is_live = contrast_score > SPOOF_THRESHOLD
                    
                    # Draw visual bounding box
                    if box_coords and is_finger_present:
                        cx, cy, cw, ch = box_coords
                        if not is_large_enough:
                            box_color = (0, 0, 255) # Red = Too small/half finger
                        elif not is_centered:
                            box_color = (0, 165, 255) # Orange = Off center
                        else:
                            box_color = (0, 255, 0) # Green = Perfect placement
                        cv2.rectangle(display_frame, (cx, cy), (cx+cw, cy+ch), box_color, 2)
                    
                    # 1. EMPTY GLASS CHECK
                    if not is_finger_present:
                        if current_state != "empty":
                            current_state = "empty"
                        good_frames_held = 0
                        spoof_frames_held = 0
                        color = (150, 150, 150)
                        msg = f"WAITING FOR FINGER..."

                    # 2. POOR PLACEMENT / HALF FINGER CHECK
                    elif not is_large_enough:
                        if current_state != "coverage":
                            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                            current_state = "coverage"
                        good_frames_held = 0
                        spoof_frames_held = 0
                        color = (0, 0, 255) # Red
                        msg = f"PRESS FLATTER & HARDER!"

                    # 3. OFF-CENTER CHECK
                    elif not is_centered:
                        if current_state != "center":
                            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                            current_state = "center"
                        good_frames_held = 0
                        spoof_frames_held = 0
                        color = (0, 165, 255) # Orange
                        msg = f"CENTER FINGER ON GLASS!"

                    # 4. BLUR CHECK
                    elif not is_sharp:
                        if current_state != "blur":
                            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION) 
                            current_state = "blur"
                        good_frames_held = 0
                        spoof_frames_held = 0 
                        color = (0, 165, 255) # Orange
                        msg = f"TOO BLURRY! HOLD STILL..."

                    # 5. ACTIVE SPOOF DETECTION (ONLY DURING LOGIN)
                    elif scan_type == "Live" and not is_live:
                        if current_state != "spoof":
                            winsound.MessageBeep(winsound.MB_ICONHAND) 
                            current_state = "spoof"
                        good_frames_held = 0
                        spoof_frames_held += 1
                        color = (0, 0, 255) # Red
                        msg = f"SPOOF DETECTED! Lock in: {SPOOF_KILL_LIMIT - spoof_frames_held}"
                        
                        if spoof_frames_held >= SPOOF_KILL_LIMIT:
                            cv2.putText(display_frame, "SYSTEM LOCKDOWN!", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                            cv2.imshow(window_name, display_frame)
                            cv2.waitKey(500)
                            messagebox.showerror("SECURITY ALERT", "Presentation Attack Detected.\nSystem Aborting.")
                            break 
                            
                    # 6. PERFECT CAPTURE
                    else:
                        if current_state != "good":
                            winsound.MessageBeep(winsound.MB_OK) 
                            current_state = "good"
                        spoof_frames_held = 0 
                        good_frames_held += 1
                        color = (0, 255, 0) # Green
                        msg = f"PERFECT! HOLD STILL... ({good_frames_held}/{FRAMES_TO_HOLD})"
                        if scan_type == "Master": time.sleep(0.1)
                        
                    cv2.putText(display_frame, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
                    cv2.imshow(window_name, display_frame)
                    
                    if good_frames_held >= FRAMES_TO_HOLD:
                        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS) 
                        cv2.imwrite(expected_file, frame)
                        
                        if scan_type == "Master":
                            cv2.putText(display_frame, "SUCCESS: 15/15 Captured", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
                            cv2.imshow(window_name, display_frame)
                            cv2.waitKey(1000) 
                            
                        captured = True
                        break
                        
                if cv2.waitKey(100) & 0xFF == 27: 
                    break
                self.root.update()

            cv2.destroyAllWindows()
            ftr_api.ftrScanCloseDevice(hDevice)
            
            if captured: return expected_file
            return None

        except Exception as e:
            messagebox.showerror("Hardware Crash", f"Error interacting with scanner:\n{e}")
            self.set_status("Hardware Error.", "#e74c3c")
            return None
    
    def register_flow(self):
        user_id = self.target_user 
        self.set_status(f"CLEANING DATA FOR {user_id.upper()}...", "#f1c40f")
        old_files = glob.glob(os.path.join(REPO_DIR, f"{user_id}__*.bmp"))
        for file_path in old_files:
            try: os.remove(file_path)
            except Exception: pass
        
        for finger in FINGERS_TO_ENROLL:
            self.set_status(f"PREPARE: {finger.upper()}", "#f1c40f")
            time.sleep(1.5)
            captured_file = self.capture_fingerprint_live(user_id, scan_type="Master", finger_name=finger)
            if not captured_file: sys.exit(1)

        self.set_status("ENROLLMENT COMPLETE", "#2ecc71")
        time.sleep(0.5)
        sys.exit(0)

    def authenticate_flow(self):
        user_id = self.target_user 
        for finger in FINGERS_TO_ENROLL:
            safe_finger_name = finger.replace(" ", "")
            if not os.path.exists(os.path.join(REPO_DIR, f"{user_id}___Master.bmp".replace("__Master", f"__{safe_finger_name}_Master"))):
                pass 
                
        requested_finger = random.choice(FINGERS_TO_ENROLL)
        self.set_status(f"CHALLENGE: Place {requested_finger.upper()}", "#f1c40f")
        time.sleep(1.5)
        
        captured_live = self.capture_fingerprint_live(user_id, scan_type="Live", finger_name=requested_finger)
        if not captured_live: sys.exit(1)
            
        kp_live, des_live, _ = get_fs88_features_direct(captured_live)
        if des_live is None or len(des_live) < 2:
            print("[DEBUG] No valid features found in live scan.", flush=True)
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
                    if m.distance < RATIO_TEST_STRICTNESS * n.distance:
                        good.append(m)
            best_score = len(good)

        print(f"\n[DEBUG] Fingerprint Verification for {user_id}", flush=True)
        print(f"[DEBUG] Finger Checked: {requested_finger}", flush=True)
        print(f"[DEBUG] Target Score: {MATCH_PASS_SCORE}", flush=True)
        print(f"[DEBUG] Actual Score: {best_score}", flush=True)
        
        if best_score >= MATCH_PASS_SCORE: 
            print("[DEBUG] RESULT: MATCH PASSED\n", flush=True)
            self.set_status("VERIFIED", "#2ecc71")
            sys.exit(0) 
        else:
            print("[DEBUG] RESULT: MATCH FAILED\n", flush=True)
            self.set_status("DENIED", "#e74c3c")
            sys.exit(1) 

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enroll", action="store_true")
    parser.add_argument("--user", type=str, default="DemoUser", help="The ID of the user")
    args = parser.parse_args()
    
    mode = "enroll" if args.enroll else "auth"
    root = tk.Tk()
    app = FutronicBiometricApp(root, mode, args.user)
    root.mainloop()