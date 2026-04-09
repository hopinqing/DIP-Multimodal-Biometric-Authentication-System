import customtkinter as ctk
import tkinter.messagebox as messagebox
import threading
import time
import subprocess 
import os         
import random     
from PIL import Image 
from datetime import datetime

# --- MODULE IMPORTS ---
try:
    from modules.face import face_module
    MODULES_LOADED = True
except ImportError as e:
    print(f"Warning: Face module not fully linked yet. Running in fallback mode. ({e})")
    MODULES_LOADED = False

# --- ELECTRIC VAULT THEME CONFIGURATION ---
ctk.set_appearance_mode("dark")  

BG_COLOR = "#05080F"          
PANEL_COLOR = "#0D1321"       
ACCENT_COLOR = "#0066FF"      
ACCENT_HOVER = "#0052CC"      
TEXT_MAIN = "#FFFFFF"         
TEXT_SUB = "#8A9BB3"          
BORDER_COLOR = "#1C273C"      

COLOR_PENDING = "#4A5D77"     
COLOR_PROCESSING = "#00D4FF"  
COLOR_SUCCESS = "#00FFA3"     
COLOR_FAIL = "#FF3366"        
COLOR_SKIPPED = "#111826"     

# --- SUBPROCESS CONFIGURATION ---
VOICE_PYTHON_PATH = r"modules\voice\venv_voice\Scripts\python.exe" 
VOICE_SCRIPT_PATH = r"modules\voice\voice_module.py"

FINGER_PYTHON_32_PATH = r"modules\fingerprint\venv_fingerprint\Scripts\python.exe"
FINGER_SCRIPT_PATH = r"modules\fingerprint\fingerprint_module.py"

IRIS_PYTHON_PATH = r"modules\iris\venv_iris\Scripts\python.exe"
IRIS_SCRIPT_PATH = r"modules\iris\iris_module.py"

class MultimodalDashboard:
    def __init__(self):
        self.root = ctk.CTk(fg_color=BG_COLOR)
        self.root.title("Multimodal Biometric Authentication System")
        self.root.geometry("950x700") 
        
        # --- SYSTEM STATE ---
        self.risk_level = ctk.StringVar(value="MAXIMUM") # NEW: Defaulting to the new highest tier
        self.use_face = ctk.BooleanVar(value=True)
        self.use_voice = ctk.BooleanVar(value=True)
        self.use_finger = ctk.BooleanVar(value=True)
        self.use_iris = ctk.BooleanVar(value=True) 
        
        # --- ASSET LOADING ---
        self.icons = {}
        icon_size = (32, 32) 
        base_dir = os.path.dirname(os.path.abspath(__file__))
        
        def load_icon(filename):
            path = os.path.join(base_dir, "icons", filename)
            if os.path.exists(path):
                return ctk.CTkImage(light_image=Image.open(path), dark_image=Image.open(path), size=icon_size)
            return None 

        self.icons["FACE"] = load_icon("face.png")
        self.icons["VOICE"] = load_icon("voice.png")
        self.icons["FINGER"] = load_icon("fingerprint.png")
        self.icons["IRIS"] = load_icon("iris.png")
        self.icons["GEAR"] = load_icon("gear.png") 

        # ==========================================
        # MAIN LAYOUT CONTAINERS
        # ==========================================
        self.top_container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.top_container.pack(fill="both", expand=True, padx=40, pady=(40, 10))

        self.left_col = ctk.CTkFrame(self.top_container, fg_color="transparent", width=320)
        self.left_col.pack(side="left", fill="y", padx=(0, 40))

        self.right_col = ctk.CTkFrame(self.top_container, fg_color="transparent")
        self.right_col.pack(side="right", fill="both", expand=True)

        self.bottom_container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.bottom_container.pack(fill="x", side="bottom", padx=40, pady=(0, 30))

        # ==========================================
        # LEFT COLUMN (CONTROLS)
        # ==========================================
        header_frame = ctk.CTkFrame(self.left_col, fg_color="transparent")
        header_frame.pack(fill="x", pady=(0, 20))

        self.header_label = ctk.CTkLabel(header_frame, text="IDENTITY\nAUTHENTICATION", 
                                         font=ctk.CTkFont(family="Segoe UI", size=32, weight="bold"), 
                                         text_color=TEXT_MAIN, justify="left")
        self.header_label.pack(anchor="w")
        
        btn_admin = ctk.CTkButton(header_frame, text="⚙️ Admin Panel", image=self.icons.get("GEAR"), 
                                  font=ctk.CTkFont(weight="bold", size=12), fg_color="transparent", 
                                  text_color=ACCENT_COLOR, hover_color=PANEL_COLOR, width=120,
                                  command=self.open_admin_settings)
        btn_admin.pack(anchor="w", pady=(10, 0))

        self.status_label = ctk.CTkLabel(self.left_col, text="SYSTEM READY", 
                                         font=ctk.CTkFont(family="Consolas", size=13, weight="bold"), 
                                         text_color=TEXT_SUB)
        self.status_label.pack(anchor="w", pady=(20, 5))

        ctk.CTkLabel(self.left_col, text="ACCESS ID:", font=ctk.CTkFont(family="Consolas", weight="bold"), text_color=TEXT_SUB).pack(anchor="w")
        self.entry_user = ctk.CTkEntry(self.left_col, font=ctk.CTkFont(family="Consolas", size=15), height=45,
                                       fg_color=PANEL_COLOR, border_color=BORDER_COLOR, text_color=TEXT_MAIN, corner_radius=10)
        self.entry_user.insert(0, "DemoUser")
        self.entry_user.pack(fill="x", pady=(5, 30))

        self.btn_auth = ctk.CTkButton(self.left_col, text="LOGIN", 
                                      font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
                                      fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER, 
                                      text_color="#FFFFFF", corner_radius=20, height=50,
                                      command=self.start_auth_thread)
        self.btn_auth.pack(fill="x", pady=(0, 15))

        self.btn_enroll = ctk.CTkButton(self.left_col, text="ENROLL", 
                                        font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
                                        fg_color=PANEL_COLOR, hover_color=BORDER_COLOR, 
                                        text_color=TEXT_MAIN, corner_radius=20, height=50,
                                        border_width=1, border_color=BORDER_COLOR,
                                        command=self.start_enroll_thread)
        self.btn_enroll.pack(fill="x")

        # ==========================================
        # RIGHT COLUMN (2x2 GRID PANELS)
        # ==========================================
        self.right_col.grid_columnconfigure(0, weight=1)
        self.right_col.grid_columnconfigure(1, weight=1)
        self.right_col.grid_rowconfigure(0, weight=1)
        self.right_col.grid_rowconfigure(1, weight=1)

        self.panels = {}
        self.create_grid_panel(0, 0, "FACE", "Facial\nScan", self.icons.get("FACE"))
        self.create_grid_panel(0, 1, "VOICE", "Voice\nRecognition", self.icons.get("VOICE"))
        self.create_grid_panel(1, 0, "FINGER", "Fingerprint\nScan", self.icons.get("FINGER"))
        self.create_grid_panel(1, 1, "IRIS", "Iris\nScan", self.icons.get("IRIS")) 

        # ==========================================
        # BOTTOM CONTAINER (TERMINAL)
        # ==========================================
        self.terminal = ctk.CTkTextbox(self.bottom_container, height=90, fg_color=PANEL_COLOR, 
                                       text_color=ACCENT_COLOR, font=ctk.CTkFont(family="Consolas", size=12),
                                       border_width=1, border_color=BORDER_COLOR, corner_radius=10)
        self.terminal.pack(fill="x")
        self.terminal.configure(state="disabled") 

    # ==========================================
    # UI HELPERS
    # ==========================================
    def log_to_terminal(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {message}\n"
        
        self.terminal.configure(state="normal")
        self.terminal.insert("end", log_entry)
        self.terminal.see("end") 
        self.terminal.configure(state="disabled")
        self.root.update_idletasks()

    def create_grid_panel(self, row, col, key, text, icon_img):
        frame = ctk.CTkFrame(self.right_col, fg_color=PANEL_COLOR, corner_radius=15, 
                             border_width=1, border_color=BORDER_COLOR)
        frame.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")
        
        content = ctk.CTkFrame(frame, fg_color="transparent")
        content.pack(expand=True)

        if icon_img:
            lbl_icon = ctk.CTkLabel(content, text="", image=icon_img)
            lbl_icon.pack(pady=(0, 10))
            
        lbl_name = ctk.CTkLabel(content, text=text, 
                                font=ctk.CTkFont(family="Segoe UI", size=16, weight="bold"), 
                                text_color=TEXT_MAIN, justify="center")
        lbl_name.pack(pady=(0, 15))
        
        lbl_status = ctk.CTkLabel(content, text="PENDING", 
                                  font=ctk.CTkFont(family="Consolas", size=13, weight="bold"), 
                                  text_color=COLOR_PENDING)
        lbl_status.pack()
        
        self.panels[key] = {"frame": frame, "status": lbl_status}

    def update_panel(self, key, status_text, color):
        self.panels[key]["status"].configure(text=status_text, text_color=color)
        self.panels[key]["frame"].configure(border_color=color)
        
        if color == COLOR_SKIPPED:
            self.panels[key]["frame"].configure(fg_color=COLOR_SKIPPED)
        else:
            self.panels[key]["frame"].configure(fg_color=PANEL_COLOR)
            
        self.root.update_idletasks()

    def update_main_status(self, text, color):
        self.status_label.configure(text=text, text_color=color)
        self.root.update_idletasks()

    def reset_ui(self):
        time.sleep(3)
        self.update_main_status("SYSTEM READY", TEXT_SUB)
        for key in self.panels:
            if key == "FACE" and not self.use_face.get():
                self.update_panel(key, "OFFLINE", COLOR_SKIPPED)
            elif key == "VOICE" and not self.use_voice.get():
                self.update_panel(key, "OFFLINE", COLOR_SKIPPED)
            elif key == "FINGER" and not self.use_finger.get():
                self.update_panel(key, "OFFLINE", COLOR_SKIPPED)
            elif key == "IRIS" and not self.use_iris.get():
                self.update_panel(key, "OFFLINE", COLOR_SKIPPED)
            else:
                self.update_panel(key, "PENDING", COLOR_PENDING)
            self.panels[key]["frame"].configure(border_color=BORDER_COLOR, fg_color=PANEL_COLOR) 

    # ==========================================
    # ADMIN SETTINGS
    # ==========================================
    def open_admin_settings(self):
        admin_win = ctk.CTkToplevel(self.root)
        admin_win.title("Security Policies")
        admin_win.geometry("400x470") # Slightly expanded height to fit the 4th tier
        admin_win.attributes("-topmost", True)
        admin_win.configure(fg_color=BG_COLOR)
        
        ctk.CTkLabel(admin_win, text="ACTIVE HARDWARE MODULES", font=ctk.CTkFont(weight="bold", size=13), text_color=TEXT_MAIN).pack(pady=(20, 10))
        
        hw_frame = ctk.CTkFrame(admin_win, fg_color="transparent")
        hw_frame.pack(fill="x", padx=50)
        
        ctk.CTkCheckBox(hw_frame, text="Facial Camera", variable=self.use_face, text_color=TEXT_MAIN, fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER).pack(anchor="w", pady=5)
        ctk.CTkCheckBox(hw_frame, text="Microphone (Voice)", variable=self.use_voice, text_color=TEXT_MAIN, fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER).pack(anchor="w", pady=5)
        ctk.CTkCheckBox(hw_frame, text="Futronic Fingerprint", variable=self.use_finger, text_color=TEXT_MAIN, fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER).pack(anchor="w", pady=5)
        ctk.CTkCheckBox(hw_frame, text="IriShield Scanner", variable=self.use_iris, text_color=TEXT_MAIN, fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER).pack(anchor="w", pady=5)

        ctk.CTkLabel(admin_win, text="TIME-SENSITIVE RISK POLICY", font=ctk.CTkFont(weight="bold", size=13), text_color=TEXT_MAIN).pack(pady=(25, 10))
        
        def set_risk(choice):
            self.log_to_terminal(f"ADMIN: Policy updated to {choice.upper()} RISK.")
            self.reset_ui() 
            
        # --- NEW: FULL 4-TIER MENU ---
        radio_low = ctk.CTkRadioButton(admin_win, text="LOW (Any 1 Biometric)", variable=self.risk_level, value="LOW", command=lambda: set_risk("LOW"), fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER)
        radio_low.pack(pady=6, anchor="w", padx=50)
        
        radio_med = ctk.CTkRadioButton(admin_win, text="MEDIUM (Any 2 Biometrics)", variable=self.risk_level, value="MEDIUM", command=lambda: set_risk("MEDIUM"), fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER)
        radio_med.pack(pady=6, anchor="w", padx=50)
        
        radio_high = ctk.CTkRadioButton(admin_win, text="HIGH (Any 3 Biometrics)", variable=self.risk_level, value="HIGH", command=lambda: set_risk("HIGH"), fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER)
        radio_high.pack(pady=6, anchor="w", padx=50)

        radio_max = ctk.CTkRadioButton(admin_win, text="MAXIMUM (All Active Biometrics)", variable=self.risk_level, value="MAXIMUM", command=lambda: set_risk("MAXIMUM"), fg_color=ACCENT_COLOR, hover_color=ACCENT_HOVER)
        radio_max.pack(pady=6, anchor="w", padx=50)

    # ==========================================
    # ENROLLMENT LOGIC (RETRY LOOPS)
    # ==========================================
    def start_enroll_thread(self):
        user_id = self.entry_user.get().strip()
        if not user_id:
            self.log_to_terminal("ERROR: User ID cannot be blank.")
            return
            
        self.btn_enroll.configure(state="disabled")
        self.btn_auth.configure(state="disabled")
        threading.Thread(target=self.execute_enrollment, args=(user_id,), daemon=True).start()

    def execute_enrollment(self, user_id):
        self.log_to_terminal(f"Starting Identity Creation for: {user_id}")
        
        try:
            # --- FACE RETRY LOOP ---
            if self.use_face.get():
                self.update_main_status("ENROLLING: ALIGN FACE...", COLOR_PROCESSING)
                while True:
                    if MODULES_LOADED:
                        face_success = face_module.run_face_enrollment(user_id) 
                    else:
                        time.sleep(1); face_success = True
                    
                    if face_success:
                        self.update_panel("FACE", "ENROLLED", COLOR_SUCCESS)
                        self.log_to_terminal("Facial vectors enrolled.")
                        break # Success! Break the loop and move to the next module.
                    else:
                        self.update_panel("FACE", "FAILED", COLOR_FAIL)
                        self.log_to_terminal("Face enrollment failed.")
                        if not messagebox.askretrycancel("Enrollment Error", "Face scan failed or was aborted.\n\nWould you like to try again?"):
                            self.log_to_terminal("Enrollment aborted by user.")
                            return # Cancel stops the whole enrollment process
            
            # --- VOICE RETRY LOOP ---
            if self.use_voice.get():
                self.update_main_status("ENROLLING: VOICE...", COLOR_PROCESSING)
                while True:
                    if os.path.exists(VOICE_SCRIPT_PATH):
                        result = subprocess.run([VOICE_PYTHON_PATH, VOICE_SCRIPT_PATH, "--enroll", "--user", user_id])
                        voice_success = (result.returncode == 0)
                    else:
                        time.sleep(1); voice_success = True
                        
                    if voice_success:
                        self.update_panel("VOICE", "ENROLLED", COLOR_SUCCESS)
                        self.log_to_terminal("Voice biometrics enrolled.")
                        break 
                    else:
                        self.update_panel("VOICE", "FAILED", COLOR_FAIL)
                        self.log_to_terminal("Voice enrollment failed.")
                        if not messagebox.askretrycancel("Enrollment Error", "Voice recording failed or was aborted.\n\nWould you like to try again?"):
                            self.log_to_terminal("Enrollment aborted by user.")
                            return

            # --- FINGERPRINT RETRY LOOP ---
            if self.use_finger.get():
                self.update_main_status("ENROLLING: FINGERPRINT...", COLOR_PROCESSING)
                while True:
                    if os.path.exists(FINGER_SCRIPT_PATH):
                        result = subprocess.run([FINGER_PYTHON_32_PATH, FINGER_SCRIPT_PATH, "--enroll", "--user", user_id])
                        finger_success = (result.returncode == 0)
                    else:
                        time.sleep(1); finger_success = True
                        
                    if finger_success:
                        self.update_panel("FINGER", "ENROLLED", COLOR_SUCCESS)
                        self.log_to_terminal("Fingerprint minutiae enrolled.")
                        break 
                    else:
                        self.update_panel("FINGER", "FAILED", COLOR_FAIL)
                        self.log_to_terminal("Fingerprint enrollment failed.")
                        if not messagebox.askretrycancel("Enrollment Error", "Fingerprint scan failed or was aborted.\n\nWould you like to try again?"):
                            self.log_to_terminal("Enrollment aborted by user.")
                            return

            # --- IRIS RETRY LOOP ---
            if self.use_iris.get():
                self.update_main_status("ENROLLING: SCAN IRIS 3 TIMES...", COLOR_PROCESSING)
                while True:
                    if os.path.exists(IRIS_SCRIPT_PATH):
                        result = subprocess.run([IRIS_PYTHON_PATH, IRIS_SCRIPT_PATH, "--enroll", "--user", user_id])
                        iris_success = (result.returncode == 0)
                    else:
                        time.sleep(1); iris_success = True
                        
                    if iris_success:
                        self.update_panel("IRIS", "ENROLLED", COLOR_SUCCESS)
                        self.log_to_terminal("Iris vectors enrolled.")
                        break 
                    else:
                        self.update_panel("IRIS", "FAILED", COLOR_FAIL)
                        self.log_to_terminal("Iris enrollment failed.")
                        if not messagebox.askretrycancel("Enrollment Error", "Iris scan failed or was aborted.\n\nWould you like to try again?"):
                            self.log_to_terminal("Enrollment aborted by user.")
                            return

            self.update_main_status("IDENTITY SECURED", COLOR_SUCCESS)
            
        except Exception as e:
            self.log_to_terminal(f"CRITICAL ERROR: {e}")
            
        finally:
            self.btn_enroll.configure(state="normal")
            self.btn_auth.configure(state="normal")
            self.reset_ui()

    # ==========================================
    # AUTHENTICATION LOGIC (NOW 4-TIER!)
    # ==========================================
    def start_auth_thread(self):
        user_id = self.entry_user.get().strip()
        if not user_id:
            self.log_to_terminal("ERROR: User ID cannot be blank.")
            return
            
        self.btn_enroll.configure(state="disabled")
        self.btn_auth.configure(state="disabled")
        for key in self.panels:
            self.update_panel(key, "PENDING", COLOR_PENDING)
            self.panels[key]["frame"].configure(border_color=BORDER_COLOR, fg_color=PANEL_COLOR) 
            
        threading.Thread(target=self.execute_authentication_sequence, args=(user_id,), daemon=True).start()

    def execute_authentication_sequence(self, user_id):
        current_risk = self.risk_level.get()
        
        active_modules = []
        if self.use_face.get(): active_modules.append("FACE")
        if self.use_voice.get(): active_modules.append("VOICE")
        if self.use_finger.get(): active_modules.append("FINGER")
        if self.use_iris.get(): active_modules.append("IRIS") 
        
        random.shuffle(active_modules)
        
        if not active_modules:
            self.log_to_terminal("ERROR: All hardware modules are disabled.")
            self.btn_enroll.configure(state="normal")
            self.btn_auth.configure(state="normal")
            self.reset_ui()
            return

        # --- NEW: 4-TIER REQUIREMENT LOGIC ---
        if current_risk == "LOW":
            required_passes = 1
        elif current_risk == "MEDIUM":
            required_passes = 2
        elif current_risk == "HIGH":
            required_passes = 3
        else: # MAXIMUM
            required_passes = len(active_modules)
            
        if required_passes > len(active_modules):
            required_passes = len(active_modules)

        self.log_to_terminal(f"Unlock Request: {user_id}. Policy: Require {required_passes} of {len(active_modules)} vectors.")
        
        passed_count = 0
        failed_count = 0
        
        try:
            for step_name in active_modules:
                self.update_main_status(f"CHECKING {step_name}...", COLOR_PROCESSING)
                self.update_panel(step_name, "PROCESSING...", COLOR_PROCESSING)
                
                success = False
                if step_name == "FACE":
                    if MODULES_LOADED:
                        success = face_module.run_face_verification(user_id) 
                    else:
                        time.sleep(1); success = True
                        
                elif step_name == "VOICE":
                    if os.path.exists(VOICE_SCRIPT_PATH):
                        result = subprocess.run([VOICE_PYTHON_PATH, VOICE_SCRIPT_PATH, "--user", user_id])
                        success = (result.returncode == 0)
                    else:
                        time.sleep(1); success = True
                        
                elif step_name == "FINGER":
                    if os.path.exists(FINGER_SCRIPT_PATH):
                        result = subprocess.run([FINGER_PYTHON_32_PATH, FINGER_SCRIPT_PATH, "--user", user_id])
                        success = (result.returncode == 0)
                    else:
                        time.sleep(1); success = True
                        
                elif step_name == "IRIS":
                    if os.path.exists(IRIS_SCRIPT_PATH):
                        result = subprocess.run([IRIS_PYTHON_PATH, IRIS_SCRIPT_PATH, "--user", user_id])
                        success = (result.returncode == 0)
                    else:
                        time.sleep(1); success = True

                if success:
                    self.update_panel(step_name, "VERIFIED", COLOR_SUCCESS)
                    self.log_to_terminal(f"{step_name} Verified.")
                    passed_count += 1
                else:
                    self.update_panel(step_name, "FAILED", COLOR_FAIL)
                    self.log_to_terminal(f"{step_name} Failed.")
                    failed_count += 1

                if passed_count >= required_passes:
                    self.log_to_terminal("Clearance met. Short-circuiting remaining checks.")
                    break 
                
                remaining_modules = len(active_modules) - (passed_count + failed_count)
                if (passed_count + remaining_modules) < required_passes:
                    self.log_to_terminal("Mathematical impossibility to clear security. Aborting.")
                    break

            for mod in active_modules:
                if self.panels[mod]["status"].cget("text") == "PENDING":
                    self.update_panel(mod, "SKIPPED", COLOR_SKIPPED)

            if passed_count >= required_passes:
                self.update_main_status("VAULT UNLOCKED", COLOR_SUCCESS)
                self.log_to_terminal(f"ACCESS GRANTED. Welcome, {user_id}.")
                messagebox.showinfo("Security Clearance", f"✅ Identity Confirmed. Access Granted.")
            else:
                self.update_main_status(f"ACCESS DENIED ({passed_count}/{required_passes} passed)", COLOR_FAIL)
                self.log_to_terminal(f"DENIED: Insufficient biometric clearance.")
            
        except Exception as e:
            self.log_to_terminal(f"CRITICAL ERROR: {e}")
            
        finally:
            self.btn_enroll.configure(state="normal")
            self.btn_auth.configure(state="normal")
            self.reset_ui()

if __name__ == "__main__":
    app = MultimodalDashboard()
    app.root.mainloop()