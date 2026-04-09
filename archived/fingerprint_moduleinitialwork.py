import cv2
import numpy as np
import os
import time
import ctypes
import tkinter as tk
from tkinter import messagebox
import sys
import argparse

# ==========================================
# PATH CONFIGURATION
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DLL_PATH = os.path.join(BASE_DIR, "ftrScanAPI.dll")
REPO_DIR = os.path.join(BASE_DIR, "Biometric_Repo")

if not os.path.exists(REPO_DIR): 
    os.makedirs(REPO_DIR)

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
    def __init__(self, root, mode):
        self.root = root
        self.root.title("FS88 Live Scanner")
        self.root.geometry("400x200")
        self.mode = mode
        
        tk.Label(root, text="FUTRONIC FS88 ACTIVE", font=("Arial", 14, "bold")).pack(pady=20)
        
        self.status = tk.Label(root, text="Status: Initializing...", font=("Arial", 10, "italic"))
        self.status.pack(pady=20)

        if self.mode == "enroll":
            self.root.after(500, self.register_flow)
        else:
            self.root.after(500, self.authenticate_flow)

    def set_status(self, text):
        self.status.config(text=f"Status: {text}")
        self.root.update()

    def capture_fingerprint(self, user_id, scan_type="Master"):
        expected_file = os.path.join(REPO_DIR, f"{user_id}__{scan_type}.bmp")
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
                messagebox.showerror("Scanner Error", "Could not connect to FS88 scanner. Is it plugged in?")
                return None

            # --- THE NEW COUNTDOWN TIMER ---
            for i in range(3, 0, -1):
                self.set_status(f"Get ready... Place finger in {i}")
                time.sleep(1)
            
            self.set_status(f"SCANNING NOW! Keep finger still...")
            # -------------------------------

            class FTRSCAN_IMAGE_SIZE(ctypes.Structure):
                _fields_ = [("nWidth", ctypes.c_int), ("nHeight", ctypes.c_int), ("nImageSize", ctypes.c_int)]

            img_size = FTRSCAN_IMAGE_SIZE()
            if ftr_api.ftrScanGetImageSize(hDevice, ctypes.byref(img_size)):
                buffer = (ctypes.c_ubyte * img_size.nImageSize)()

                success = False
                for _ in range(20): 
                    success = ftr_api.ftrScanGetImage(hDevice, 4, buffer)
                    if success: 
                        break
                    time.sleep(0.5)
                    self.root.update() 

                if success:
                    self.set_status("Scan captured! Processing...")
                    
                    raw_data = bytes(buffer)
                    img_array = np.frombuffer(raw_data, dtype=np.uint8)
                    img_reshaped = img_array.reshape((img_size.nHeight, img_size.nWidth))

                    cv2.imwrite(expected_file, img_reshaped)
                    ftr_api.ftrScanCloseDevice(hDevice)
                    return expected_file
                else:
                    messagebox.showwarning("Timeout", "No finger detected within 10 seconds.")
                    self.set_status("Scan Timeout.")

            ftr_api.ftrScanCloseDevice(hDevice)
            return None

        except Exception as e:
            messagebox.showerror("Hardware Crash", f"Error interacting with scanner:\n{e}")
            self.set_status("Hardware Error.")
            return None
    
    def register_flow(self):
        user_id = "DemoUser" 
        
        captured_file = self.capture_fingerprint(user_id, scan_type="Master")
        if captured_file:
            messagebox.showinfo("Success", f"Fingerprint registered successfully!")
            sys.exit(0)
        else:
            sys.exit(1)

    def authenticate_flow(self):
        user_id = "DemoUser"

        master_path = os.path.join(REPO_DIR, f"{user_id}__Master.bmp")
        if not os.path.exists(master_path):
            messagebox.showerror("Error", "No Master Template found. Please enroll first.")
            sys.exit(1)

        captured_live = self.capture_fingerprint(user_id, scan_type="Live")
        
        if not captured_live:
            sys.exit(1)
            
        kp_live, des_live, live_enhanced = get_fs88_features_direct(captured_live)
        kp_master, des_master, master_enhanced = get_fs88_features_direct(master_path)
            
        score = 0
        if des_master is not None and des_live is not None and len(des_master) > 1 and len(des_live) > 1:
            bf = cv2.BFMatcher()
            matches = bf.knnMatch(des_master, des_live, k=2)
            
            good = []
            for match in matches:
                if len(match) == 2:
                    m, n = match
                    if m.distance < 0.75 * n.distance:
                        good.append(m)
            
            score = len(good)

            if score >= 75:
                messagebox.showinfo("GRANTED", f"Access Granted!\nMatch Score: {score}")
                sys.exit(0) 
            else:
                messagebox.showwarning("DENIED", f"Access Denied.\nMatch Score: {score}")
                sys.exit(1) 
        else:
            messagebox.showerror("Error", "Could not extract features.")
            sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enroll", action="store_true")
    args = parser.parse_args()
    
    mode = "enroll" if args.enroll else "auth"
    
    root = tk.Tk()
    app = FutronicBiometricApp(root, mode)
    root.mainloop()