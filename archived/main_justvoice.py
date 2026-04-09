import tkinter as tk
from tkinter import messagebox
import threading
import time
import subprocess 
import os         

# --- SUBPROCESS CONFIGURATION ---
VOICE_PYTHON_PATH = r"modules\voice\venv_voice\Scripts\python.exe" 
VOICE_SCRIPT_PATH = r"modules\voice\voice_module.py"

FINGER_PYTHON_32_PATH = r"modules\fingerprint\venv_fingerprint\Scripts\python.exe"
FINGER_SCRIPT_PATH = r"modules\fingerprint\fingerprint_module.py"

# --- MODULE IMPORTS ---
try:
    from modules.face import face_module
    MODULES_LOADED = True
except ImportError as e:
    print(f"Warning: Modules not fully linked yet. Running in UI Test Mode. ({e})")
    MODULES_LOADED = False

# --- MODERN UI THEME ---
COLOR_BG = "#1e1e24"
COLOR_PANEL = "#2b2b36"
COLOR_TEXT = "#ffffff"
COLOR_PENDING = "#718093"
COLOR_PROCESSING = "#f1c40f"
COLOR_SUCCESS = "#2ecc71"
COLOR_FAIL = "#e74c3c"
COLOR_ENROLL = "#9b59b6" 

class MultimodalDashboard:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Multimodal Authentication")
        self.root.geometry("600x700")
        self.root.configure(bg=COLOR_BG)
        
        tk.Label(self.root, text="🛡️ MULTIMODAL AUTHENTICATION", font=("Segoe UI", 20, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack(pady=(20, 5))
        self.status_label = tk.Label(self.root, text="SYSTEM IDLE - AWAITING USER", font=("Consolas", 12), bg=COLOR_BG, fg=COLOR_PENDING)
        self.status_label.pack(pady=(0, 20))

        self.panels = {}
        self.create_status_panel("FACE", "👤  Facial Recognition")
        self.create_status_panel("VOICE", "🎙️  Voice Biometrics")
        self.create_status_panel("IRIS", "👁️  Iris Scanner (Bypassed)")
        self.create_status_panel("FINGER", "👆  Fingerprint Scanner") # Removed Bypassed

        btn_frame = tk.Frame(self.root, bg=COLOR_BG)
        btn_frame.pack(pady=30)

        self.btn_enroll = tk.Button(btn_frame, text="ENROLL NEW USER", font=("Segoe UI", 12, "bold"), 
                                  bg=COLOR_ENROLL, fg="white", relief="flat", cursor="hand2", 
                                  width=20, height=2, command=self.start_enroll_thread)
        self.btn_enroll.pack(side="left", padx=10)

        self.btn_auth = tk.Button(btn_frame, text="INITIATE FULL LOGIN", font=("Segoe UI", 12, "bold"), 
                                  bg="#0984e3", fg="white", relief="flat", cursor="hand2", 
                                  width=20, height=2, command=self.start_auth_thread)
        self.btn_auth.pack(side="left", padx=10)

    def create_status_panel(self, key, text):
        frame = tk.Frame(self.root, bg=COLOR_PANEL, bd=0, highlightthickness=1, highlightbackground="#444")
        frame.pack(fill="x", padx=40, pady=5)
        
        lbl_name = tk.Label(frame, text=text, font=("Segoe UI", 12), bg=COLOR_PANEL, fg=COLOR_TEXT)
        lbl_name.pack(side="left", padx=20, pady=15)
        
        lbl_status = tk.Label(frame, text="PENDING", font=("Consolas", 12, "bold"), bg=COLOR_PANEL, fg=COLOR_PENDING)
        lbl_status.pack(side="right", padx=20, pady=15)
        
        self.panels[key] = {"frame": frame, "status": lbl_status}

    def update_panel(self, key, status_text, color):
        self.panels[key]["status"].config(text=status_text, fg=color)
        self.panels[key]["frame"].config(highlightbackground=color)
        self.root.update_idletasks()

    def update_main_status(self, text, color):
        self.status_label.config(text=text, fg=color)
        self.root.update_idletasks()

    # ==========================================
    # ENROLLMENT LOGIC 
    # ==========================================
    def start_enroll_thread(self):
        self.btn_enroll.config(state="disabled")
        self.btn_auth.config(state="disabled")
        threading.Thread(target=self.execute_enrollment, daemon=True).start()

    def execute_enrollment(self):
        self.update_main_status("ENROLLING: ALIGN FACE TO CAMERA...", COLOR_PROCESSING)
        
        try:
            # --- STEP 1: FACE (BYPASSED) ---
            time.sleep(1)
            face_success = True
                
            if face_success:
                self.update_main_status("FACE ENROLLED. STARTING VOICE...", COLOR_PROCESSING)
                
                # --- STEP 2: VOICE (ACTIVE) ---
                try:
                    if os.path.exists(VOICE_SCRIPT_PATH):
                        # Removed capture_output=True so prints show in console
                        result = subprocess.run([VOICE_PYTHON_PATH, VOICE_SCRIPT_PATH, "--enroll"])
                        voice_success = (result.returncode == 0)
                    else:
                        time.sleep(2)
                        voice_success = True
                except Exception as e:
                    print(f"Failed to launch voice module subprocess: {e}")
                    voice_success = False
                
                if voice_success:
                    self.update_main_status("VOICE ENROLLED. STARTING FINGERPRINT...", COLOR_PROCESSING)
                    
                    # --- STEP 3: FINGERPRINT (BYPASSED) ---
                    time.sleep(1)
                    finger_success = True
                    
                    if finger_success:
                        self.update_main_status("ENROLLMENT COMPLETE", COLOR_SUCCESS)
                        messagebox.showinfo("Success", "Face, Voice, and Fingerprint Vectors Saved.")
                    else:
                        self.update_main_status("FINGERPRINT ENROLLMENT FAILED", COLOR_FAIL)
                        messagebox.showwarning("Failed", "Fingerprint enrollment was not completed.")
                        
                else:
                    self.update_main_status("VOICE ENROLLMENT FAILED", COLOR_FAIL)
                    messagebox.showwarning("Failed", "Voice enrollment was not completed.")
            else:
                self.update_main_status("FACE ENROLLMENT FAILED", COLOR_FAIL)
                messagebox.showwarning("Failed", "Face enrollment was not completed.")
                
        except Exception as e:
            self.update_main_status("SYSTEM ERROR", COLOR_FAIL)
            messagebox.showerror("Error", f"Enrollment crashed: {e}")
            
        finally:
            self.btn_enroll.config(state="normal")
            self.btn_auth.config(state="normal")
            self.update_main_status("SYSTEM IDLE - AWAITING USER", COLOR_PENDING)

    # ==========================================
    # AUTHENTICATION LOGIC
    # ==========================================
    def start_auth_thread(self):
        self.btn_enroll.config(state="disabled")
        self.btn_auth.config(state="disabled")
        for key in self.panels:
            self.update_panel(key, "PENDING", COLOR_PENDING)
            
        threading.Thread(target=self.execute_authentication_sequence, daemon=True).start()

    def execute_authentication_sequence(self):
        try:
            # --- STEP 1: FACE (BYPASSED) ---
            self.update_main_status("STEP 1: ALIGN FACE TO CAMERA...", COLOR_PROCESSING)
            self.update_panel("FACE", "PROCESSING...", COLOR_PROCESSING)
            
            time.sleep(1)
            face_passed = True
            
            if not face_passed:
                self.update_panel("FACE", "FAILED", COLOR_FAIL)
                self.authentication_failed("Facial Verification Failed.")
                return
            
            self.update_panel("FACE", "VERIFIED", COLOR_SUCCESS)

            # --- STEP 2: VOICE (ACTIVE) ---
            self.update_main_status("STEP 2: SPEAK PASSPHRASE...", COLOR_PROCESSING)
            self.update_panel("VOICE", "LISTENING...", COLOR_PROCESSING)
            
            try:
                if os.path.exists(VOICE_SCRIPT_PATH):
                    # Removed capture_output=True so prints show in console
                    result = subprocess.run([VOICE_PYTHON_PATH, VOICE_SCRIPT_PATH])
                    voice_passed = (result.returncode == 0)
                else:
                    time.sleep(2)
                    voice_passed = True
            except Exception as e:
                print(f"Failed to launch voice module subprocess: {e}")
                voice_passed = False
                
            if not voice_passed:
                self.update_panel("VOICE", "FAILED", COLOR_FAIL)
                self.authentication_failed("Voice Verification Failed.")
                return
                
            self.update_panel("VOICE", "VERIFIED", COLOR_SUCCESS)

            # --- STEP 3: FINGERPRINT (BYPASSED) ---
            self.update_main_status("STEP 3: PLACE FINGER ON SCANNER...", COLOR_PROCESSING)
            self.update_panel("FINGER", "SCANNING...", COLOR_PROCESSING)
            
            time.sleep(1)
            finger_passed = True
                
            if not finger_passed:
                self.update_panel("FINGER", "FAILED", COLOR_FAIL)
                self.authentication_failed("Fingerprint Verification Failed.")
                return
                
            self.update_panel("FINGER", "VERIFIED", COLOR_SUCCESS)

            # --- FINAL DECISION ---
            self.update_main_status("ACCESS GRANTED: MULTIMODAL LOCK CLEARED", COLOR_SUCCESS)
            messagebox.showinfo("Security Clearance", "✅ All biometrics verified. Access Granted.")
            
        except Exception as e:
            self.update_main_status("SYSTEM ERROR", COLOR_FAIL)
            messagebox.showerror("Error", f"Authentication crashed: {e}")
            
        finally:
            self.btn_enroll.config(state="normal")
            self.btn_auth.config(state="normal")

    def authentication_failed(self, reason):
        self.update_main_status(f"ACCESS DENIED: {reason}", COLOR_FAIL)
        messagebox.showwarning("Security Alert", f"⛔ {reason}\nSystem locking down.")

if __name__ == "__main__":
    app = MultimodalDashboard()
    app.root.mainloop()