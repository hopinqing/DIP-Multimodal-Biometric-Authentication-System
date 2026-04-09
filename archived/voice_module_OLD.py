import os
import sys
import torch
import sounddevice as sd
import soundfile as sf
import random
import json
import winsound
import warnings
import tkinter as tk
from tkinter import messagebox
import requests 
import time

# --- FIXES FOR VERSION CLASHES ---
import torchaudio
if not hasattr(torchaudio, 'list_audio_backends'):
    torchaudio.list_audio_backends = lambda: ["soundfile"]

import huggingface_hub.file_download
_original_hf_download = huggingface_hub.file_download.hf_hub_download
def _patched_hf_download(*args, **kwargs):
    kwargs.pop('use_auth_token', None) 
    try:
        return _original_hf_download(*args, **kwargs)
    except Exception as e:
        if "custom.py" in str(e) or "custom.py" in kwargs.get("filename", ""):
            resp = requests.Response()
            resp.status_code = 404
            raise requests.exceptions.HTTPError("404 Client Error", response=resp)
        raise
huggingface_hub.file_download.hf_hub_download = _patched_hf_download
huggingface_hub.hf_hub_download = _patched_hf_download
# ---------------------------------------------

from speechbrain.inference import EncoderClassifier
from torch.nn.functional import cosine_similarity
from vosk import Model, KaldiRecognizer

warnings.filterwarnings("ignore")

# ===============================
# Configuration
# ===============================
VOICE_DIR = os.path.dirname(os.path.abspath(__file__))
EMBEDDINGS_FOLDER = os.path.join(VOICE_DIR, "embeddings")
os.makedirs(EMBEDDINGS_FOLDER, exist_ok=True) 
VOSK_MODEL_PATH = os.path.join(VOICE_DIR, "vosk-model-small-en-us-0.15")

THRESHOLD = 0.55        
MATCH_RATIO_REQ = 0.40  

ENROLLMENT_SENTENCES = [
    "The quick brown fox jumps over the lazy dog.",
    "Bright stars shine quietly above the silent city tonight.",
    "Please bring the blue notebook from the wooden table.",
    "A gentle breeze moved the tall grass beside the river.",
    "My favorite hobby is solving difficult programming problems.",
    "The early morning sun warmed the quiet mountain valley.",
    "She carefully packed the fragile glass into the box.",
    "Modern technology allows computers to learn from data.",
    "The small puppy followed the child across the garden.",
    "Engineers design complex systems to solve real world problems."
]

# ===============================
# Initialization
# ===============================
try:
    classifier = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cpu"})
    asr_model = Model(VOSK_MODEL_PATH)
except Exception:
    sys.exit(1)

# ===============================
# Core Audio Logic
# ===============================
def get_embedding(audio_path):
    signal, fs = sf.read(audio_path)
    signal = torch.tensor(signal).unsqueeze(0)
    with torch.no_grad():
        emb = classifier.encode_batch(signal)
    return emb.squeeze()

def transcribe_audio(audio_path):
    wf, fs = sf.read(audio_path)
    wf = (wf * 32768).astype('int16')
    rec = KaldiRecognizer(asr_model, fs)
    rec.AcceptWaveform(wf.tobytes())
    return json.loads(rec.Result()).get("text", "")

# ===============================
# UX ENROLLMENT LOGIC
# ===============================
def run_voice_enrollment():
    root = tk.Tk()
    root.title("Voice Enrollment")
    root.geometry("600x450")
    root.configure(bg="#1e1e24")
    root.attributes("-topmost", True)
    
    root.update_idletasks()
    x = (root.winfo_screenwidth() // 2) - (600 // 2)
    y = (root.winfo_screenheight() // 2) - (450 // 2)
    root.geometry(f"+{x}+{y}")

    tk.Label(root, text="NEW USER ENROLLMENT", font=("Segoe UI", 16, "bold"), bg="#1e1e24", fg="#9b59b6").pack(pady=20)
    
    lbl_step = tk.Label(root, text="", font=("Segoe UI", 11), bg="#1e1e24", fg="#718093")
    lbl_step.pack()

    # Phrase Display Area (Flow Layout)
    phrase_frame = tk.Frame(root, bg="#1e1e24")
    phrase_frame.pack(pady=30)
    
    lbl_timer = tk.Label(root, text="READY", font=("Consolas", 24, "bold"), bg="#1e1e24", fg="#f1c40f")
    lbl_timer.pack(pady=10)

    btn_record = tk.Button(root, text="Start Recording", font=("Segoe UI", 12, "bold"), bg="#2ecc71", fg="white", width=20, cursor="hand2")
    btn_record.pack(pady=20)

    embeddings = []
    current_step = [0]
    word_labels = []

    def update_ui():
        for label in word_labels: label.destroy()
        word_labels.clear()
        
        sentence = ENROLLMENT_SENTENCES[current_step[0]]
        lbl_step.config(text=f"PROGRESS: {current_step[0]+1} / {len(ENROLLMENT_SENTENCES)}")
        
        # Word wrapping simulation for labels
        words = sentence.split()
        for w in words:
            l = tk.Label(phrase_frame, text=w, font=("Segoe UI", 15, "bold"), bg="#1e1e24", fg="#444b5e")
            l.pack(side="left", padx=2)
            word_labels.append(l)
            
        btn_record.config(state="normal", text="Start Recording", bg="#2ecc71")
        lbl_timer.config(text="READY", fg="#f1c40f")

    def record_step():
        btn_record.config(state="disabled", text="LISTENING...", bg="#2b2b36")
        
        fs = 16000
        duration = 5
        audio = sd.rec(int(duration * fs), samplerate=fs, channels=1)
        
        # Countdown
        for i in range(duration, 0, -1):
            lbl_timer.config(text=f"00:0{i}", fg="#e74c3c")
            root.update()
            time.sleep(1)
            
        sd.wait()
        lbl_timer.config(text="ANALYZING...", fg="#3498db")
        root.update()
        
        filename = f"temp_enroll_{current_step[0]}.wav"
        sf.write(filename, audio, fs)
        
        # Processing
        emb = get_embedding(filename)
        embeddings.append(emb)
        spoken_text = transcribe_audio(filename).lower()
        spoken_words = spoken_text.split()
        
        # SMARTER HIGHLIGHTING: Strip punctuation and check
        for label in word_labels:
            clean_word = label.cget("text").lower().strip(".,!?;:\"")
            if clean_word in spoken_words:
                label.config(fg="#2ecc71") # Bright Green
            else:
                label.config(fg="#e74c3c") # Red (Softly indicate it wasn't captured)
        
        winsound.Beep(1500, 300) 
        root.update()
        time.sleep(1.5) 
        
        current_step[0] += 1
        if current_step[0] < len(ENROLLMENT_SENTENCES):
            update_ui()
        else:
            avg_embedding = torch.mean(torch.stack(embeddings), dim=0)
            torch.save(avg_embedding, os.path.join(EMBEDDINGS_FOLDER, "enrolled_user.pt"))
            messagebox.showinfo("Success", "Voice Profile Created!")
            root.destroy()
            sys.exit(0)

    btn_record.config(command=record_step)
    update_ui()
    root.mainloop()

# (run_voice_verification remains same)
def run_voice_verification():
    if not os.path.exists(EMBEDDINGS_FOLDER): sys.exit(1)
    enrolled_embeddings = {}
    for file in os.listdir(EMBEDDINGS_FOLDER):
        if file.endswith(".pt"):
            enrolled_embeddings[file.split(".pt")[0]] = torch.load(os.path.join(EMBEDDINGS_FOLDER, file))
    if not enrolled_embeddings: sys.exit(1)

    challenge = random.choice(["The fish twisted and turned on the bent hook", "The swan dive was far short of perfect", "Python programming is fun when you focus"])
    
    vroot = tk.Tk()
    vroot.title("Liveness Check")
    vroot.geometry("400x200")
    vroot.configure(bg="#1e1e24")
    vroot.attributes("-topmost", True)
    tk.Label(vroot, text="READ ALOUD:", font=("Segoe UI", 10), bg="#1e1e24", fg="white").pack(pady=10)
    tk.Label(vroot, text=challenge, font=("Segoe UI", 12, "bold"), bg="#1e1e24", fg="#0984e3", wraplength=350).pack()
    lbl_vstatus = tk.Label(vroot, text="RECORDING IN 1s...", font=("Consolas", 12), bg="#1e1e24", fg="#f1c40f")
    lbl_vstatus.pack(pady=20)
    vroot.update()
    time.sleep(1)

    fs, duration = 16000, 5
    audio = sd.rec(int(duration * fs), samplerate=fs, channels=1)
    lbl_vstatus.config(text="🎙️ LISTENING...", fg="#e74c3c")
    vroot.update()
    sd.wait()
    vroot.destroy()
    
    sf.write("live_voice.wav", audio, fs)
    test_emb = get_embedding("live_voice.wav")
    spoken_text = transcribe_audio("live_voice.wav")
    
    best_score = max([cosine_similarity(test_emb, emb, dim=0).item() for emb in enrolled_embeddings.values()])
    match_ratio = len(set(challenge.lower().split()) & set(spoken_text.lower().split())) / len(challenge.split())

    if best_score >= THRESHOLD and match_ratio >= MATCH_RATIO_REQ:
        winsound.Beep(1500, 250); sys.exit(0)
    else:
        winsound.Beep(400, 500); sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--enroll": run_voice_enrollment()
    else: run_voice_verification()