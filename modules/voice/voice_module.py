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
import threading
import numpy as np 
import argparse
import difflib 

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
# Configuration & Central Vault
# ===============================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.join(os.path.dirname(BASE_DIR), "..", "Biometric_Repo") 
if not os.path.exists(REPO_DIR):
    os.makedirs(REPO_DIR)

VOSK_MODEL_EN_PATH = os.path.join(BASE_DIR, "vosk-model-small-en-us-0.15")
VOSK_MODEL_CN_PATH = os.path.join(BASE_DIR, "vosk-model-small-cn-0.22")

THRESHOLD = 0.55        
MATCH_RATIO_REQ = 0.40  
NOISE_THRESHOLD = 0.05
TOO_QUIET_THRESHOLD = 0.015 
RECORDING_DURATION = 5.0 

# --- THE DATASETS ---
ENGLISH_SENTENCES = [
    "The birch canoe slid on the smooth planks.",
    "Glue the sheet to the dark blue background.",
    "It is easy to tell the depth of a well.",
    "These days a chicken leg is a rare dish.",
    "Rice is often served in round bowls.",
    "The juice of lemons makes fine punch.",
    "The box was thrown beside the parked truck.",
    "The hogs were fed chopped corn and garbage.",
    "Four hours of steady work faced us.",
    "A large size in stockings is hard to sell."
]

MANDARIN_SENTENCES = [
    "绿 水 青 山 就 是 金 山 银 山", 
    "他 们 乘 坐 高 铁 前 往 上 海 出 差",
    "昨 天 的 晚 会 非 常 精 彩 绝 伦",
    "这 家 餐 厅 的 烤 鸭 味 道 很 正 宗",
    "图 书 馆 里 有 很 多 珍 贵 的 历 史 资 料",
    "科 学 家 们 正 在 研 究 新 型 能 源 技 术",
    "秋 天 的 香 山 满 铺 着 红 色 的 枫 叶",
    "请 帮 我 把 这 份 邮 件 发 送 给 经 理",
    "宇 航 员 成 功 完 成 了 太 空 行 走 任 务",
    "保 护 环 境 是 我 们 每 个 人 的 责 任"
]

ENGLISH_CHALLENGES = [
    "She had your dark suit in greasy wash water all year.", 
    "Don't ask me to carry an oily rag like that.", 
    "Water drops from the heavy rain fell on the dry ground.",
    "A boy was picking peaches from the large tree.",
    "The quick brown fox jumps over the lazy dog."
]

MANDARIN_CHALLENGES = [
    "今 天 的 天 气 非 常 适 合 外 出 游 玩",
    "请 在 滴 声 后 说 出 你 的 语 音 密 码",
    "我 们 需 要 更 多 的 时 间 来 解 决 问 题",
    "智 能 手 机 改 变 了 人 们 的 生 活 方 式",
    "明 天 早 上 八 点 半 在 会 议 室 开 会"
]

# ===============================
# Initialization
# ===============================
print("[INFO] Loading Biometric & STT Models...", flush=True)
try:
    classifier = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cpu"})
    asr_model_en = Model(VOSK_MODEL_EN_PATH)
    
    if os.path.exists(VOSK_MODEL_CN_PATH):
        asr_model_cn = Model(VOSK_MODEL_CN_PATH)
    else:
        print("[WARNING] Chinese Vosk model not found! Mandarin STT verification will be bypassed.", flush=True)
        asr_model_cn = None
except Exception as e:
    print(f"[ERROR] Model loading failed: {e}", flush=True)
    sys.exit(1)

def get_embedding(audio_path):
    signal, fs = sf.read(audio_path)
    signal = torch.tensor(signal).unsqueeze(0)
    with torch.no_grad():
        emb = classifier.encode_batch(signal)
    return emb.squeeze()

def transcribe_audio(audio_path, language="en"):
    wf, fs = sf.read(audio_path)
    wf = (wf * 32768).astype('int16')
    model_to_use = asr_model_cn if language == "cn" and asr_model_cn else asr_model_en
    rec = KaldiRecognizer(model_to_use, fs)
    rec.AcceptWaveform(wf.tobytes())
    return json.loads(rec.Result()).get("text", "")

# ===============================
# UX ENROLLMENT LOGIC 
# ===============================
def run_voice_enrollment(user_id):
    target_sentences = []
    target_langs = []
    
    picker = tk.Tk()
    picker.title("Select Language")
    picker.geometry("400x250")
    picker.configure(bg="#1e1e24")
    picker.attributes("-topmost", True)
    
    picker.update_idletasks()
    x = (picker.winfo_screenwidth() // 2) - (400 // 2)
    y = (picker.winfo_screenheight() // 2) - (250 // 2)
    picker.geometry(f"+{x}+{y}")

    tk.Label(picker, text=f"Enrollment: {user_id.upper()}", font=("Segoe UI", 14, "bold"), bg="#1e1e24", fg="#9b59b6").pack(pady=10)
    tk.Label(picker, text="Select your preferred language:", font=("Segoe UI", 12), bg="#1e1e24", fg="white").pack(pady=10)

    def set_lang(choice):
        nonlocal target_sentences, target_langs
        if choice == "en":
            target_sentences = ENGLISH_SENTENCES
            target_langs = ["en"] * 10
        elif choice == "cn":
            target_sentences = MANDARIN_SENTENCES
            target_langs = ["cn"] * 10
        elif choice == "both":
            target_sentences = ENGLISH_SENTENCES + MANDARIN_SENTENCES
            target_langs = ["en"] * 10 + ["cn"] * 10
            
        pref_path = os.path.join(REPO_DIR, f"{user_id}__Voice_Lang.json")
        with open(pref_path, "w") as f:
            json.dump({"pref": choice}, f)
            
        picker.destroy()

    btn_frame = tk.Frame(picker, bg="#1e1e24")
    btn_frame.pack(pady=10)
    tk.Button(btn_frame, text="English (10)", font=("Segoe UI", 10, "bold"), bg="#3498db", fg="white", width=12, command=lambda: set_lang("en")).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Mandarin (10)", font=("Segoe UI", 10, "bold"), bg="#e74c3c", fg="white", width=12, command=lambda: set_lang("cn")).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Both (20)", font=("Segoe UI", 10, "bold"), bg="#9b59b6", fg="white", width=12, command=lambda: set_lang("both")).pack(side="left", padx=5)

    picker.mainloop()

    if not target_sentences:
        sys.exit(1) 

    root = tk.Tk()
    root.title(f"Voice Enrollment - {user_id.upper()}")
    root.geometry("750x350")
    root.configure(bg="#1e1e24")
    root.attributes("-topmost", True)
    
    root.update_idletasks()
    root.geometry(f"+{x-175}+{y}")

    tk.Label(root, text="READ THE PHRASE ALOUD", font=("Segoe UI", 16, "bold"), bg="#1e1e24", fg="#9b59b6").pack(pady=20)
    lbl_step = tk.Label(root, text="", font=("Segoe UI", 11), bg="#1e1e24", fg="#718093")
    lbl_step.pack()

    phrase_frame = tk.Frame(root, bg="#1e1e24")
    phrase_frame.pack(pady=30)
    
    lbl_timer = tk.Label(root, text="INITIALIZING...", font=("Consolas", 18, "bold"), bg="#1e1e24", fg="#f1c40f")
    lbl_timer.pack(pady=10)

    word_labels = []
    embeddings = []

    def update_ui_for_sentence(index):
        for label in word_labels: label.destroy()
        word_labels.clear()
        
        lbl_step.config(text=f"PROGRESS: {index+1} / {len(target_sentences)}")
        
        words = target_sentences[index].split()
        for w in words:
            l = tk.Label(phrase_frame, text=w, font=("Microsoft YaHei", 15, "bold"), bg="#1e1e24", fg="#444b5e")
            l.pack(side="left", padx=3)
            word_labels.append(l)

    def automated_enrollment_pipeline():
        fs = 16000
        time.sleep(1) 
        
        for i in range(len(target_sentences)):
            root.after(0, update_ui_for_sentence, i)
            current_lang = target_langs[i]
            
            success = False
            while not success:
                # 1. Check Room Noise
                root.after(0, lbl_timer.config, {"text": "CHECKING ROOM NOISE...", "fg": "#f1c40f"})
                ambient_audio = sd.rec(int(fs * 1.5), samplerate=fs, channels=1, blocking=True)
                if np.sqrt(np.mean(np.square(ambient_audio))) > NOISE_THRESHOLD:
                    root.after(0, lbl_timer.config, {"text": "ROOM TOO NOISY! FIND QUIET SPACE.", "fg": "#e74c3c"})
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                    time.sleep(3)
                    continue 

                # 2. Fixed 5-Second Recording
                root.after(0, lbl_timer.config, {"text": f"RECORDING ({int(RECORDING_DURATION)}s)...", "fg": "#e74c3c"})
                winsound.Beep(1000, 200) 
                
                audio_data = sd.rec(int(fs * RECORDING_DURATION), samplerate=fs, channels=1, blocking=True)
                
                # 3. Analyze Audio Level
                root.after(0, lbl_timer.config, {"text": "ANALYZING...", "fg": "#3498db"})
                vocal_rms = np.sqrt(np.mean(np.square(audio_data)))
                
                if vocal_rms < TOO_QUIET_THRESHOLD:
                    root.after(0, lbl_timer.config, {"text": "TOO QUIET! PLEASE REPEAT.", "fg": "#e74c3c"})
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                    time.sleep(2)
                    continue 

                filename = os.path.join(BASE_DIR, f"temp_enroll.wav")
                sf.write(filename, audio_data[:, 0], fs)
                
                spoken_text = transcribe_audio(filename, language=current_lang).lower()
                
                # --- THE STRICT ENROLLMENT SEQUENCE FIX ---
                if current_lang == "en":
                    clean_challenge_words = [label.cget("text").lower().strip(".,!?;:\"") for label in word_labels]
                    spoken_words = spoken_text.split()
                    match_ratio = difflib.SequenceMatcher(None, clean_challenge_words, spoken_words).ratio()
                else:
                    clean_challenge = target_sentences[i].replace(" ", "")
                    spoken_text_nospaces = spoken_text.replace(" ", "")
                    match_ratio = difflib.SequenceMatcher(None, clean_challenge, spoken_text_nospaces).ratio()

                print(f"[DEBUG] Enrollment Sentence {i+1} | STT Ratio: {match_ratio:.4f}", flush=True)

                if match_ratio < MATCH_RATIO_REQ:
                    root.after(0, lbl_timer.config, {"text": "WORDS NOT RECOGNIZED! REPEAT.", "fg": "#e74c3c"})
                    winsound.MessageBeep(winsound.MB_ICONHAND)
                    time.sleep(2)
                    continue 

                embeddings.append(get_embedding(filename))
                winsound.Beep(1500, 300) 
                success = True
                time.sleep(1.0) 
                
        # Finalize and Save to Central Vault
        avg_embedding = torch.mean(torch.stack(embeddings), dim=0)
        save_path = os.path.join(REPO_DIR, f"{user_id}__Voice_Master.pt")
        torch.save(avg_embedding, save_path)
        
        if os.path.exists(os.path.join(BASE_DIR, "temp_enroll.wav")):
            os.remove(os.path.join(BASE_DIR, "temp_enroll.wav"))
            
        root.after(0, root.destroy)
        os._exit(0)

    threading.Thread(target=automated_enrollment_pipeline, daemon=True).start()
    root.mainloop()

# ===============================
# UX VERIFICATION LOGIC 
# ===============================
def run_voice_verification(user_id):
    emb_path = os.path.join(REPO_DIR, f"{user_id}__Voice_Master.pt")
    lang_path = os.path.join(REPO_DIR, f"{user_id}__Voice_Lang.json")
    
    if not os.path.exists(emb_path):
        print(f"[ERROR] No voice template found for {user_id}", flush=True)
        sys.exit(1)
        
    master_embedding = torch.load(emb_path)
    
    pref = "en"
    if os.path.exists(lang_path):
        with open(lang_path, "r") as f:
            pref = json.load(f).get("pref", "en")
            
    if pref == "en":
        challenge_text = random.choice(ENGLISH_CHALLENGES)
        challenge_lang = "en"
    elif pref == "cn":
        challenge_text = random.choice(MANDARIN_CHALLENGES)
        challenge_lang = "cn"
    else: 
        challenges = [(c, "en") for c in ENGLISH_CHALLENGES] + [(c, "cn") for c in MANDARIN_CHALLENGES]
        challenge_text, challenge_lang = random.choice(challenges)

    vroot = tk.Tk()
    vroot.title(f"Voice Liveness - {user_id.upper()}")
    vroot.geometry("550x250")
    vroot.configure(bg="#1e1e24")
    vroot.attributes("-topmost", True)
    
    vroot.update_idletasks()
    x = (vroot.winfo_screenwidth() // 2) - (550 // 2)
    y = (vroot.winfo_screenheight() // 2) - (250 // 2)
    vroot.geometry(f"+{x}+{y}")
    
    tk.Label(vroot, text="READ ALOUD TO VERIFY:", font=("Segoe UI", 10), bg="#1e1e24", fg="white").pack(pady=10)
    tk.Label(vroot, text=challenge_text, font=("Microsoft YaHei", 14, "bold"), bg="#1e1e24", fg="#0984e3", wraplength=500).pack()
    
    lbl_vstatus = tk.Label(vroot, text="INITIALIZING...", font=("Consolas", 12), bg="#1e1e24", fg="#f1c40f")
    lbl_vstatus.pack(pady=20)

    def auth_worker():
        fs = 16000
        time.sleep(1)
        
        # 1. Check Room Noise
        vroot.after(0, lbl_vstatus.config, {"text": "CHECKING ROOM NOISE...", "fg": "#f1c40f"})
        ambient_audio = sd.rec(int(fs * 1.0), samplerate=fs, channels=1, blocking=True)
        if np.sqrt(np.mean(np.square(ambient_audio))) > NOISE_THRESHOLD:
            vroot.after(0, lbl_vstatus.config, {"text": "ROOM TOO NOISY! RETRY.", "fg": "#e74c3c"})
            winsound.MessageBeep(winsound.MB_ICONHAND)
            time.sleep(2)
            os._exit(1)

        # 2. Fixed 5-Second Recording
        vroot.after(0, lbl_vstatus.config, {"text": f"RECORDING ({int(RECORDING_DURATION)}s)...", "fg": "#e74c3c"})
        winsound.Beep(1000, 200)
        
        audio_data = sd.rec(int(fs * RECORDING_DURATION), samplerate=fs, channels=1, blocking=True)
        
        vroot.after(0, lbl_vstatus.config, {"text": "ANALYZING...", "fg": "#3498db"})
        live_file = os.path.join(BASE_DIR, "temp_live.wav")
        sf.write(live_file, audio_data[:, 0], fs)
        
        test_emb = get_embedding(live_file)
        spoken_text = transcribe_audio(live_file, language=challenge_lang)
        
        best_score = cosine_similarity(test_emb, master_embedding, dim=0).item()
        
        # --- THE STRICT VERIFICATION SEQUENCE FIX ---
        if challenge_lang == "en":
            clean_challenge_words = [w.strip(".,!?;:\"").lower() for w in challenge_text.split()]
            spoken_words = [w.strip(".,!?;:\"").lower() for w in spoken_text.split()]
            matcher = difflib.SequenceMatcher(None, clean_challenge_words, spoken_words)
            match_ratio = matcher.ratio()
        else:
            clean_challenge = challenge_text.replace(" ", "")
            spoken_text_nospaces = spoken_text.replace(" ", "")
            matcher = difflib.SequenceMatcher(None, clean_challenge, spoken_text_nospaces)
            match_ratio = matcher.ratio()

        if os.path.exists(live_file):
            os.remove(live_file)

        # The Fix: Update the UI directly with the scores so they are never swallowed by main.py
        score_text = f"BIO SCORE: {best_score:.2f} | WORD MATCH: {match_ratio:.2f}"
        print(f"[DEBUG] {score_text}", flush=True)

        if best_score >= THRESHOLD and match_ratio >= MATCH_RATIO_REQ:
            vroot.after(0, lbl_vstatus.config, {"text": f"ACCESS GRANTED\n{score_text}", "fg": "#2ecc71"})
            winsound.Beep(1500, 250)
            time.sleep(2.5) # Wait so you can read the score!
            os._exit(0) # Force exit code 0 (Success)
        else:
            vroot.after(0, lbl_vstatus.config, {"text": f"ACCESS DENIED\n{score_text}", "fg": "#e74c3c"})
            winsound.Beep(400, 500)
            time.sleep(2.5) # Wait so you can read the score!
            os._exit(1) # Force exit code 1 (Failure)

    threading.Thread(target=auth_worker, daemon=True).start()
    vroot.mainloop()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--enroll", action="store_true")
    parser.add_argument("--user", type=str, default="DemoUser")
    args = parser.parse_args()
    
    if args.enroll:
        run_voice_enrollment(args.user)
    else:
        run_voice_verification(args.user)