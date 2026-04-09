import cv2
import numpy as np
import os
import threading
import random
import time
import tkinter as tk
from tkinter import messagebox
from deepface import DeepFace

import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# ===============================
# Configuration
# ===============================

MODEL = "ArcFace"
MATCH_THRESHOLD = 0.4

ENROLL_FRAMES = 5
AUTH_FRAMES = 3

EMBEDDING_FILE = "stored_face_embeddings.npy"
FACE_MODEL_PATH = "modules/face/face_landmarker.task"

# Liveness & Tracking
LIVENESS_TIMEOUT = 15
ANGLE_THRESHOLD_YAW = 18
ANGLE_THRESHOLD_PITCH = 15
REQUIRED_SUCCESS_FRAMES = 5
SMOOTHING_WINDOW = 5
LIVENESS_STEPS = 3
AUTO_STABLE_FRAMES = 3

# Security constraints
FACE_TRACKING_THRESHOLD = 0.15  # Max allowed jump distance
MAX_LOST_FRAMES = 3             # Max frames the face can be missing

# ===============================
# ArcFace 5-Point Template & Landmarks
# ===============================

ARC_FACE_TEMPLATE_5 = np.array([
    [38.2946, 51.6963],   # left eye center
    [73.5318, 51.5014],   # right eye center
    [56.0252, 71.7366],   # nose tip
    [41.5493, 92.3655],   # left mouth corner
    [70.7299, 92.2041]    # right mouth corner
], dtype=np.float32)

# Using multiple points to average the center of the eye
LEFT_EYE_INDICES = [33, 133, 159, 145]
RIGHT_EYE_INDICES = [362, 263, 386, 374]

NOSE_TIP = 1
LEFT_MOUTH = 61
RIGHT_MOUTH = 291

# Used strictly for UI drawing
DRAW_POINTS = [33, 263, NOSE_TIP, LEFT_MOUTH, RIGHT_MOUTH] 

# ===============================
# UI Setup
# ===============================

root = tk.Tk()
root.title("Face Authentication System")
root.geometry("420x200")
root.resizable(False, False)

status_label = tk.Label(root, text="Status: Loading models...", font=("Arial", 12))
status_label.pack(pady=10)

btn_frame = tk.Frame(root)
btn_frame.pack(pady=20)

btn_register = tk.Button(btn_frame, text="Enroll / Register Face",
                         width=25, height=2, state="disabled")
btn_register.grid(row=0, column=0, padx=10)

btn_auth = tk.Button(btn_frame, text="Authenticate Face",
                     width=25, height=2, state="disabled")
btn_auth.grid(row=0, column=1, padx=10)

root.update()

# ===============================
# Load Models
# ===============================

DeepFace.build_model(MODEL)

base_options = python.BaseOptions(model_asset_path=FACE_MODEL_PATH)
face_options = vision.FaceLandmarkerOptions(
    base_options=base_options,
    num_faces=1,
    output_facial_transformation_matrixes=True,
    running_mode=vision.RunningMode.IMAGE
)
face_landmarker = vision.FaceLandmarker.create_from_options(face_options)

status_label.config(text="Status: Ready")
btn_register.config(state="normal")
btn_auth.config(state="normal")

# ===============================
# Utilities
# ===============================

def cosine_distance(a, b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    return 1 - np.dot(a, b)

def open_camera():
    # --- ISSUE 1 FIX: cv2.CAP_DSHOW forces Windows to open USB webcams instantly ---
    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap

def is_blurry(frame, threshold=100):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var() < threshold

def get_landmark_dist(pt1, pt2):
    return np.sqrt((pt1[0] - pt2[0])**2 + (pt1[1] - pt2[1])**2)

def rotation_matrix_to_angles(R):
    sy = np.sqrt(R[0,0]**2 + R[1,0]**2)
    singular = sy < 1e-6
    if not singular:
        x = np.arctan2(R[2,1], R[2,2])
        y = np.arctan2(-R[2,0], sy)
        z = np.arctan2(R[1,0], R[0,0])
    else:
        x = np.arctan2(-R[1,2], R[1,1])
        y = np.arctan2(-R[2,0], sy)
        z = 0
    return np.degrees(x), np.degrees(y), np.degrees(z)

def get_head_pose(result):
    if not result or not result.facial_transformation_matrixes: return None, None
    matrix = result.facial_transformation_matrixes[0]
    mat = np.array(matrix).reshape(4,4)
    R = mat[:3, :3]
    pitch, yaw, roll = rotation_matrix_to_angles(R)
    return yaw, pitch

# ===============================
# Face Overlay
# ===============================

def draw_live_facemesh(frame, result, mesh_color=(0, 255, 0)):
    if not result or not result.face_landmarks:
        return frame
        
    h, w, _ = frame.shape
    landmarks = result.face_landmarks[0]
    
    for lm in landmarks:
        x, y = int(lm.x * w), int(lm.y * h)
        cv2.circle(frame, (x, y), 1, mesh_color, -1)
        
    for idx in DRAW_POINTS:
        lm = landmarks[idx]
        x, y = int(lm.x * w), int(lm.y * h)
        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1) 
        
    return frame

def countdown_with_feed(cap, window_name, seconds, message):
    start_time = time.time()
    while True:
        elapsed = time.time() - start_time
        if elapsed >= seconds: break
            
        ret, frame = cap.read()
        if not ret: continue
        
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)
        
        frame = draw_live_facemesh(frame, result, mesh_color=(200, 200, 200))
        
        remaining = int(seconds - elapsed) + 1
        cv2.putText(frame, message, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        
        text = str(remaining)
        text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 4.0, 5)[0]
        text_x = (frame.shape[1] - text_size[0]) // 2
        text_y = (frame.shape[0] + text_size[1]) // 2
        cv2.putText(frame, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (0, 255, 0), 5)
        
        cv2.imshow(window_name, frame)
        if cv2.waitKey(1) & 0xFF == 27: return False 
    return True

def stabilize_feed(cap, window_name, seconds, message, tracked_center):
    start_time = time.time()
    current_center = tracked_center
    lost_frames = 0
    
    while True:
        elapsed = time.time() - start_time
        if elapsed >= seconds: break
            
        ret, frame = cap.read()
        if not ret: continue
        
        frame = cv2.flip(frame, 1)
        display = frame.copy()
        
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)
        
        display = draw_live_facemesh(display, result, mesh_color=(0, 255, 255))
        
        if not result.face_landmarks:
            lost_frames += 1
            if lost_frames > MAX_LOST_FRAMES:
                return False, None
        else:
            lost_frames = 0
            landmarks = result.face_landmarks[0]
            new_center = (landmarks[NOSE_TIP].x, landmarks[NOSE_TIP].y)
            
            if current_center is not None:
                if get_landmark_dist(new_center, current_center) > FACE_TRACKING_THRESHOLD:
                    return False, None
            current_center = new_center
        
        remaining = int(seconds - elapsed) + 1
        cv2.putText(display, message, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        
        text = str(remaining)
        text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 4.0, 5)[0]
        text_x = (display.shape[1] - text_size[0]) // 2
        text_y = (display.shape[0] + text_size[1]) // 2
        cv2.putText(display, text, (text_x, text_y), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (0, 255, 0), 5)
        
        cv2.imshow(window_name, display)
        if cv2.waitKey(1) & 0xFF == 27: return False, None
    return True, current_center

# ===============================
# 5-Point Alignment (Improved)
# ===============================

def extract_aligned_face(frame, landmarks):
    h, w, _ = frame.shape
    
    # Calculate approximate centers of the eyes for more accurate alignment
    left_x = sum(landmarks[idx].x for idx in LEFT_EYE_INDICES) / len(LEFT_EYE_INDICES) * w
    left_y = sum(landmarks[idx].y for idx in LEFT_EYE_INDICES) / len(LEFT_EYE_INDICES) * h
    
    right_x = sum(landmarks[idx].x for idx in RIGHT_EYE_INDICES) / len(RIGHT_EYE_INDICES) * w
    right_y = sum(landmarks[idx].y for idx in RIGHT_EYE_INDICES) / len(RIGHT_EYE_INDICES) * h

    src = np.array([
        [left_x, left_y],
        [right_x, right_y],
        [landmarks[NOSE_TIP].x * w, landmarks[NOSE_TIP].y * h],
        [landmarks[LEFT_MOUTH].x * w, landmarks[LEFT_MOUTH].y * h],
        [landmarks[RIGHT_MOUTH].x * w, landmarks[RIGHT_MOUTH].y * h]
    ], dtype=np.float32)

    M, _ = cv2.estimateAffinePartial2D(src, ARC_FACE_TEMPLATE_5)
    if M is None: raise ValueError("Alignment failed")

    aligned = cv2.warpAffine(frame, M, (112, 112), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    if is_blurry(aligned): raise ValueError("Blurry face")
    return aligned

# ===============================
# Head Pose for Liveness
# ===============================

CHALLENGES = ["LEFT", "RIGHT", "UP", "DOWN"]

def perform_liveness_check(cap):
    sequence = random.sample(CHALLENGES, LIVENESS_STEPS)
    baseline = []
    
    last_face_center = None
    lost_frames = 0

    while len(baseline) < 15:
        ret, frame = cap.read()
        if not ret: continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)

        frame = draw_live_facemesh(frame, result, mesh_color=(0, 255, 0))

        if result.facial_transformation_matrixes:
            yaw, pitch = get_head_pose(result)
            if yaw is not None: baseline.append((yaw, pitch))

        cv2.putText(frame, "Look Straight to Calibrate...", (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255,255,0), 2)
        cv2.imshow("Face Authentication", frame)
        if cv2.waitKey(1) & 0xFF == 27: return False, None

    baseline_yaw = np.mean([b[0] for b in baseline])
    baseline_pitch = np.mean([b[1] for b in baseline])

    step_index = 0
    success_frames = 0
    history = []
    
    waiting_for_center = False

    start = cv2.getTickCount()
    freq = cv2.getTickFrequency()

    while True:
        ret, frame = cap.read()
        if not ret: continue

        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)

        mesh_color = (0, 255, 255) if waiting_for_center else (0, 255, 0)
        frame = draw_live_facemesh(frame, result, mesh_color=mesh_color)

        if result.facial_transformation_matrixes and result.face_landmarks:
            lost_frames = 0
            landmarks = result.face_landmarks[0]
            new_center = (landmarks[NOSE_TIP].x, landmarks[NOSE_TIP].y)

            if last_face_center is not None:
                if get_landmark_dist(new_center, last_face_center) > FACE_TRACKING_THRESHOLD:
                    return False, None
            last_face_center = new_center

            yaw, pitch = get_head_pose(result)
            if yaw is None: continue

            delta_yaw = yaw - baseline_yaw
            delta_pitch = pitch - baseline_pitch

            history.append((delta_yaw, delta_pitch))
            if len(history) > SMOOTHING_WINDOW: history.pop(0)

            smoothed_yaw = np.mean([h[0] for h in history])
            smoothed_pitch = np.mean([h[1] for h in history])

            if waiting_for_center:
                cv2.putText(frame, "Return to CENTER", (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,255), 2)
                
                if abs(smoothed_yaw) < 10 and abs(smoothed_pitch) < 10:
                    success_frames += 1
                else:
                    success_frames = 0

                if success_frames >= REQUIRED_SUCCESS_FRAMES:
                    waiting_for_center = False
                    success_frames = 0
                    history.clear()
            else:
                direction = sequence[step_index]
                cv2.putText(frame, f"Step {step_index+1}/{LIVENESS_STEPS}: TURN {direction}",
                            (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
                
                # Omni-directional strict penalty
                turned_left = smoothed_yaw < -ANGLE_THRESHOLD_YAW
                turned_right = smoothed_yaw > ANGLE_THRESHOLD_YAW
                turned_up = smoothed_pitch < -ANGLE_THRESHOLD_PITCH
                turned_down = smoothed_pitch > ANGLE_THRESHOLD_PITCH

                # --- ISSUE 2 FIX: Relaxed Wrong Movement Penalty ---
                wrong_movement = False
                if direction == "LEFT" and turned_right:
                    wrong_movement = True
                elif direction == "RIGHT" and turned_left:
                    wrong_movement = True
                elif direction == "UP" and turned_down:
                    wrong_movement = True
                elif direction == "DOWN" and turned_up:
                    wrong_movement = True

                if wrong_movement:
                    text_size = cv2.getTextSize("WRONG MOVEMENT", cv2.FONT_HERSHEY_SIMPLEX, 1.2, 4)[0]
                    text_x = (frame.shape[1] - text_size[0]) // 2
                    cv2.putText(frame, "WRONG MOVEMENT", (text_x, 200), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 4)
                    cv2.imshow("Face Authentication", frame)
                    cv2.waitKey(1200) 
                    return False, None

                # --- ISSUE 2 FIX: Relaxed Success Condition (Increased cross-axis tolerance to 18) ---
                condition_met = False
                if direction == "LEFT" and smoothed_yaw < -ANGLE_THRESHOLD_YAW and abs(smoothed_pitch) < 18:
                    condition_met = True
                elif direction == "RIGHT" and smoothed_yaw > ANGLE_THRESHOLD_YAW and abs(smoothed_pitch) < 18:
                    condition_met = True
                elif direction == "UP" and smoothed_pitch < -ANGLE_THRESHOLD_PITCH and abs(smoothed_yaw) < 18:
                    condition_met = True
                elif direction == "DOWN" and smoothed_pitch > ANGLE_THRESHOLD_PITCH and abs(smoothed_yaw) < 18:
                    condition_met = True

                if condition_met:
                    success_frames += 1
                else:
                    success_frames = 0

                if success_frames >= REQUIRED_SUCCESS_FRAMES:
                    step_index += 1
                    success_frames = 0
                    history.clear()

                    if step_index >= LIVENESS_STEPS:
                        return True, last_face_center
                    else:
                        waiting_for_center = True

        else:
            lost_frames += 1
            if lost_frames > MAX_LOST_FRAMES: return False, None

        cv2.imshow("Face Authentication", frame)

        if (cv2.getTickCount() - start)/freq > LIVENESS_TIMEOUT: return False, None
        if cv2.waitKey(1) & 0xFF == 27: return False, None

# ===============================
# Embedding Capture (Improved)
# ===============================

def capture_embeddings(cap, num_frames, tracked_center=None):
    embeddings = []
    stable = 0
    lost_frames = 0
    current_center = tracked_center

    while len(embeddings) < num_frames:
        ret, frame = cap.read()
        if not ret: continue

        frame = cv2.flip(frame, 1)
        display = frame.copy()

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = face_landmarker.detect(mp_image)

        display = draw_live_facemesh(display, result, mesh_color=(0, 255, 0))

        if not result.face_landmarks:
            lost_frames += 1
            if lost_frames > MAX_LOST_FRAMES and tracked_center is not None: return None 
            continue
        
        lost_frames = 0
        landmarks = result.face_landmarks[0]
        new_center = (landmarks[NOSE_TIP].x, landmarks[NOSE_TIP].y)

        if current_center is not None:
            if get_landmark_dist(new_center, current_center) > FACE_TRACKING_THRESHOLD: return None
        current_center = new_center

        # STRICT FRONTAL GATING
        if result.facial_transformation_matrixes:
            yaw, pitch = get_head_pose(result)
            if yaw is None or abs(yaw) > 6 or abs(pitch) > 6:
                stable = 0
                cv2.putText(display, "Look Straight to Capture!", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.imshow("Face Authentication", display)
                if cv2.waitKey(1) & 0xFF == 27: break
                continue

        try:
            aligned_face = extract_aligned_face(frame, landmarks)
            stable += 1
            if stable >= AUTO_STABLE_FRAMES:
                embedding = DeepFace.represent(img_path=aligned_face, model_name=MODEL, detector_backend="skip", enforce_detection=False)[0]["embedding"]
                
                # Normalize immediately
                emb_array = np.array(embedding)
                emb_norm = emb_array / np.linalg.norm(emb_array)
                
                embeddings.append(emb_norm)
                stable = 0
        except Exception as e:
            stable = 0

        cv2.putText(display, f"Capturing {len(embeddings)}/{num_frames}...", (20,40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
        cv2.imshow("Face Authentication", display)

        if cv2.waitKey(1) & 0xFF == 27: break

    if len(embeddings) < num_frames: return None
    
    # Return the entire array, NOT the average
    return np.array(embeddings)

# ===============================
# Enrollment & Authentication (Improved)
# ===============================

def enroll():
    threading.Thread(target=_enroll_worker, daemon=True).start()

def _enroll_worker():
    status_label.config(text="Status: Preparing camera...")
    cap = open_camera()
    
    if not cap.isOpened():
        messagebox.showerror("Error", "Could not open camera.")
        status_label.config(text="Status: Ready")
        return
        
    try:
        if not countdown_with_feed(cap, "Face Authentication", 3, "Look into Camera to Enroll..."):
            return

        status_label.config(text="Status: Enrolling...")
        embeddings_array = capture_embeddings(cap, ENROLL_FRAMES)
        
        if embeddings_array is not None:
            # Save the full array of high-quality enrollments
            np.save(EMBEDDING_FILE, embeddings_array)
            messagebox.showinfo("Success", "Face enrolled successfully")
        else:
            messagebox.showerror("Error", "Enrollment cancelled or failed.")
            
    except Exception as e:
        messagebox.showerror("Error", f"An unexpected error occurred: {e}")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        status_label.config(text="Status: Ready")

def authenticate():
    threading.Thread(target=_auth_worker, daemon=True).start()

def _auth_worker():
    if not os.path.exists(EMBEDDING_FILE):
        messagebox.showerror("Error", "No enrolled face found")
        return

    status_label.config(text="Status: Preparing camera...")
    cap = open_camera()
    
    if not cap.isOpened():
        messagebox.showerror("Error", "Could not open camera.")
        status_label.config(text="Status: Ready")
        return
        
    try:
        if not countdown_with_feed(cap, "Face Authentication", 3, "Look into Camera..."):
            return

        status_label.config(text="Status: Liveness check...")
        passed_liveness, tracked_center = perform_liveness_check(cap)

        if not passed_liveness:
            messagebox.showerror("Liveness Failed", "Liveness check failed. Wrong movement or timeout.")
            return

        status_label.config(text="Status: Stabilizing...")
        passed_stab, tracked_center = stabilize_feed(cap, "Face Authentication", 2, "Look Straight & Hold Still...", tracked_center)

        if not passed_stab:
            messagebox.showerror("Security Error", "Authentication aborted. Face lost or swapped!")
            return

        status_label.config(text="Status: Capturing face...")
        live_embeddings = capture_embeddings(cap, AUTH_FRAMES, tracked_center)

        if live_embeddings is None:
            messagebox.showerror("Security Error", "Authentication aborted. Face lost or swapped!")
            return

        # Load the stored gallery array
        stored_embeddings = np.load(EMBEDDING_FILE)
        
        # Find the absolute best match out of all captured vs stored frames
        min_distance = float('inf')
        for live_emb in live_embeddings:
            for stored_emb in stored_embeddings:
                dist = cosine_distance(stored_emb, live_emb)
                if dist < min_distance:
                    min_distance = dist

        match = min_distance < MATCH_THRESHOLD

        if match:
            messagebox.showinfo("Result", f"Authentication Successful\nBest Match Distance: {min_distance:.4f}")
        else:
            messagebox.showerror("Result", f"Authentication Failed\nBest Match Distance: {min_distance:.4f}")

    except Exception as e:
        messagebox.showerror("Error", f"An unexpected error occurred: {e}")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        status_label.config(text="Status: Ready")

# ===============================
# Bind Buttons
# ===============================

btn_register.config(command=enroll)
btn_auth.config(command=authenticate)

root.protocol("WM_DELETE_WINDOW", root.destroy)
root.mainloop()