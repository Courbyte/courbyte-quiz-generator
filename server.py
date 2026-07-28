import os
import json
import textwrap
import tempfile
import hashlib
import time
import numpy as np
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template
import pyttsx3
import boto3
from moviepy import (
    VideoClip, AudioFileClip, concatenate_videoclips,
    concatenate_audioclips, AudioArrayClip, CompositeAudioClip, VideoFileClip,
    afx
)
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import requests
from supabase import create_client
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

load_dotenv()

app = Flask(__name__)

# ========== SECURITY ==========
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")
if not SECRET_TOKEN:
    import secrets
    SECRET_TOKEN = secrets.token_hex(32)
    print("[WARNING] SECRET_TOKEN not set in .env - generated a random one for this session only.")
    print("[WARNING] Set SECRET_TOKEN in .env for a stable value across restarts.")

# ========== WEBHOOK CONFIG ==========
MAKE_WEBHOOK_URL = os.getenv("MAKE_WEBHOOK_URL", "")

def notify_make(video_url, topic, filename):
    if not MAKE_WEBHOOK_URL:
        print("[Make] No webhook URL configured.")
        return
    payload = {"video_url": video_url, "topic": topic, "filename": filename}
    try:
        r = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=10)
        print(f"[Make] Webhook sent, status: {r.status_code}")
    except Exception as e:
        print(f"[Make] Webhook failed: {e}")

# ========== CONFIG ==========
FPS = 24
VIDEO_SIZE = (1080, 1920)

# ========== BRAND COLORS ==========
AQUA_DARK = "#00cccc"
AQUA_LIGHT = "#99ffff"
BRAND_BLACK = "#222222"
BRAND_WHITE = "#ffffff"
LIGHT_BG = "#f0f0f0"
GRAY = "#777777"
LIGHT_GRAY = "#dddddd"

def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

FONT_PATH = "arial.ttf"
if not os.path.exists(FONT_PATH):
    for candidate in [
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        if os.path.exists(candidate):
            FONT_PATH = candidate
            break

WATERMARK_PATH = None
for ext in [".png", ".jpg", ".jpeg"]:
    path = "logo" + ext
    if os.path.exists(path):
        WATERMARK_PATH = path
        break

# NOTE: no persistent local videos/ folder anymore - MoviePy still needs a real
# file on disk to encode into, but it's a temp file now, cleaned up after the
# Supabase upload succeeds. Supabase is the only permanent storage.

# ========== BACKGROUND HELPERS ==========
def create_gradient_background(size, top_color, bottom_color):
    """Builds the dark vertical gradient background once per clip (not per frame)."""
    w, h = size
    top = np.array(hex_to_rgb(top_color), dtype=np.float32)
    bottom = np.array(hex_to_rgb(bottom_color), dtype=np.float32)
    t_vals = np.linspace(0, 1, h).reshape(h, 1, 1)
    gradient = (top * (1 - t_vals) + bottom * t_vals).astype(np.uint8)
    gradient = np.repeat(gradient, w, axis=1)
    return Image.fromarray(gradient, 'RGB')

def add_glow(img, center, radius, color_hex, blur_radius=90, alpha=70):
    """Adds a soft aqua glow behind the card content, like the reference design."""
    overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    r, g, b = hex_to_rgb(color_hex)
    odraw.ellipse(
        [center[0]-radius, center[1]-radius, center[0]+radius, center[1]+radius],
        fill=(r, g, b, alpha)
    )
    overlay = overlay.filter(ImageFilter.GaussianBlur(blur_radius))
    img = img.convert('RGBA')
    img = Image.alpha_composite(img, overlay)
    return img.convert('RGB')

def build_base_card_background():
    """The shared dark background + glow used behind every question/reveal frame."""
    bg = create_gradient_background(VIDEO_SIZE, "#0d1f24", BRAND_BLACK)
    bg = add_glow(bg, (VIDEO_SIZE[0] - 150, 250), 420, AQUA_DARK, blur_radius=140, alpha=55)
    return bg

COUNTDOWN_SECONDS = 5
BACKGROUND_MUSIC_PATH = "calm_music.mp3"
OUTRO_VIDEO_PATH = "outro.mp4"

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "quiz-videos")

# Groq API
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

# TTS - lock in one specific voice so it's consistent across every call
# (questions, answers, outro), instead of relying on whatever Windows
# SAPI5 resolves as "default" each time. Leave blank to keep using the
# system default (previous behavior, unchanged).
TTS_VOICE_ID = os.getenv("TTS_VOICE_ID", "")

# TTS engine choice: "pyttsx3" (free, offline, robotic on Linux) or
# "polly" (Amazon Polly Neural voices - natural sounding, small free tier
# for 12 months, then pay-per-character - your usage volume is tiny though).
TTS_ENGINE = os.getenv("TTS_ENGINE", "pyttsx3").lower()
POLLY_VOICE_ID = os.getenv("POLLY_VOICE_ID", "Joanna")
POLLY_REGION = os.getenv("POLLY_REGION", "us-east-1")

# Make.com API (for live credit usage display)
MAKE_API_TOKEN = os.getenv("MAKE_API_TOKEN", "")
MAKE_ZONE = os.getenv("MAKE_ZONE", "eu1")
MAKE_TEAM_ID = os.getenv("MAKE_TEAM_ID", "")
MAKE_CREDIT_LIMIT = int(os.getenv("MAKE_CREDIT_LIMIT", "1000"))

# ========== OFFLINE TTS ==========
def make_tts_polly(text):
    """Amazon Polly Neural voice - natural sounding, requires AWS credentials
    (either via env vars AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, or - if running
    on an EC2 instance - an IAM Role attached to that instance, no keys needed)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp.close()
    polly = boto3.client("polly", region_name=POLLY_REGION)
    response = polly.synthesize_speech(
        Text=text,
        OutputFormat="mp3",
        VoiceId=POLLY_VOICE_ID,
        Engine="neural"
    )
    with open(tmp.name, "wb") as f:
        f.write(response["AudioStream"].read())
    clip = AudioFileClip(tmp.name)
    return clip, tmp.name

def make_tts(text, lang='en', slow=False):
    if TTS_ENGINE == "polly":
        return make_tts_polly(text)

    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    engine = pyttsx3.init()
    engine.setProperty('rate', 150)
    engine.setProperty('volume', 1.0)
    if TTS_VOICE_ID:
        engine.setProperty('voice', TTS_VOICE_ID)
    engine.save_to_file(text, tmp.name)
    engine.runAndWait()
    engine.stop()
    del engine

    # pyttsx3's runAndWait() can return on Windows before the WAV file is
    # fully flushed to disk, leaving MoviePy to read an empty/incomplete
    # file (duration: N/A). Give it a few short retries before giving up.
    max_wait_attempts = 10
    for attempt in range(max_wait_attempts):
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 44:
            break
        time.sleep(0.2)

    clip = AudioFileClip(tmp.name)
    return clip, tmp.name

# ========== AUDIO HELPERS ==========
def make_beep_sound(duration, interval=1.0, freq=1000, sr=44100):
    total_samples = int(sr * duration)
    audio = np.zeros((total_samples, 2), dtype=np.float32)
    t_beep = np.arange(0, min(0.1, interval), 1/sr)
    beep = 0.25 * np.sin(2 * np.pi * freq * t_beep).astype(np.float32)
    beep = np.column_stack([beep, beep])
    beep_samples = len(beep)
    for start in range(0, total_samples - beep_samples, int(sr * interval)):
        audio[start:start+beep_samples] += beep
    audio = np.clip(audio, -1, 1)
    return AudioArrayClip(audio, fps=sr)

def make_ding_sound(duration=0.2, freq=1200, sr=44100):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    envelope = np.exp(-t * 8)
    wave = 0.3 * np.sin(2 * np.pi * freq * t) * envelope
    stereo = np.column_stack([wave, wave]).astype(np.float32)
    return AudioArrayClip(stereo, fps=sr)

# ========== TEXT WRAPPING HELPER ==========
def wrap_text_lines(draw, text, font, max_width):
    words = text.split()
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

# ========== VIDEO FRAME RENDERERS ==========
def make_question_frame_function(question_text, options, q_dur, countdown_seconds, q_index, q_total):
    base_bg = build_base_card_background()
    font_pill = ImageFont.truetype(FONT_PATH, 32)
    font_badge_num = ImageFont.truetype(FONT_PATH, 56)
    font_q = ImageFont.truetype(FONT_PATH, 50)
    font_opt = ImageFont.truetype(FONT_PATH, 36)
    font_opt_badge = ImageFont.truetype(FONT_PATH, 36)
    font_timer = ImageFont.truetype(FONT_PATH, 120)
    font_brand = ImageFont.truetype(FONT_PATH, 26)
    letters = ['A', 'B', 'C', 'D', 'E', 'F']
    margin = 60
    content_w = VIDEO_SIZE[0] - margin * 2

    def make_frame(t):
        img = base_bg.copy()
        draw = ImageDraw.Draw(img)

        # Top pill: "Question X of Y"
        pill_text = f"Question {q_index} of {q_total}"
        bbox = draw.textbbox((0, 0), pill_text, font=font_pill)
        pill_w = (bbox[2] - bbox[0]) + 60
        pill_h = 64
        draw.rounded_rectangle([margin, 60, margin + pill_w, 60 + pill_h], radius=32,
                                outline=AQUA_DARK, width=3, fill=hex_to_rgb(BRAND_BLACK))
        draw.text((margin + 30, 60 + (pill_h - (bbox[3] - bbox[1])) // 2 - bbox[1]),
                   pill_text, font=font_pill, fill=AQUA_LIGHT)

        # Circular question-number badge, top-right
        badge_cx, badge_cy, badge_r = VIDEO_SIZE[0] - 140, 95, 70
        draw.ellipse([badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r],
                      outline=AQUA_DARK, width=6, fill=hex_to_rgb(BRAND_BLACK))
        num_text = str(q_index)
        bbox = draw.textbbox((0, 0), num_text, font=font_badge_num)
        draw.text((badge_cx - (bbox[2] - bbox[0]) / 2, badge_cy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                   num_text, font=font_badge_num, fill=AQUA_LIGHT)

        # Question text
        lines = wrap_text_lines(draw, question_text.upper(), font_q, content_w)
        y = 260
        for line in lines:
            draw.text((margin, y), line, font=font_q, fill=BRAND_WHITE)
            y += 64
        y += 40

        # Option cards
        card_h = 130
        for i, opt in enumerate(options):
            card_y = y
            draw.rounded_rectangle([margin, card_y, margin + content_w, card_y + card_h],
                                    radius=24, fill=hex_to_rgb(BRAND_BLACK), outline="#333333", width=2)
            badge_r2 = 42
            bcx, bcy = margin + 70, card_y + card_h // 2
            draw.ellipse([bcx - badge_r2, bcy - badge_r2, bcx + badge_r2, bcy + badge_r2], fill=hex_to_rgb(AQUA_DARK))
            letter = letters[i] if i < len(letters) else str(i + 1)
            bbox = draw.textbbox((0, 0), letter, font=font_opt_badge)
            draw.text((bcx - (bbox[2] - bbox[0]) / 2, bcy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                       letter, font=font_opt_badge, fill=hex_to_rgb(BRAND_BLACK))
            opt_lines = wrap_text_lines(draw, opt, font_opt, content_w - 180)
            oy = card_y + (card_h - len(opt_lines) * 46) // 2
            for line in opt_lines:
                draw.text((margin + 150, oy), line, font=font_opt, fill=BRAND_WHITE)
                oy += 46
            y += card_h + 24

        # Countdown number - only during the post-question countdown segment, 5 down to 1
        if t >= q_dur:
            remaining = countdown_seconds - (t - q_dur)
            remaining_num = max(1, min(countdown_seconds, int(remaining) + 1))
            timer_text = str(remaining_num)
            circle_r = 100
            cx = VIDEO_SIZE[0] // 2
            cy = y + 40 + circle_r
            draw.ellipse([cx - circle_r, cy - circle_r, cx + circle_r, cy + circle_r], outline=AQUA_DARK, width=6)
            bbox = draw.textbbox((0, 0), timer_text, font=font_timer)
            draw.text((cx - (bbox[2] - bbox[0]) / 2, cy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                       timer_text, font=font_timer, fill=AQUA_LIGHT)

        # Brand tag
        brand_text = "@courbyte arena"
        bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
        draw.text(((VIDEO_SIZE[0] - (bbox[2] - bbox[0])) // 2, VIDEO_SIZE[1] - 70),
                   brand_text, font=font_brand, fill=GRAY)

        if WATERMARK_PATH:
            logo = Image.open(WATERMARK_PATH).resize((150, 150), Image.LANCZOS)
            img.paste(logo, (VIDEO_SIZE[0] - 170, VIDEO_SIZE[1] - 240), logo.convert('RGBA'))
        return np.array(img)
    return make_frame

def make_reveal_frame_function(letter, answer_text):
    base_bg = build_base_card_background()
    font_header = ImageFont.truetype(FONT_PATH, 70)
    font_badge = ImageFont.truetype(FONT_PATH, 48)
    font_answer = ImageFont.truetype(FONT_PATH, 42)
    font_brand = ImageFont.truetype(FONT_PATH, 26)
    margin = 80
    content_w = VIDEO_SIZE[0] - margin * 2

    def make_frame(t):
        img = base_bg.copy()
        draw = ImageDraw.Draw(img)

        header_lines = ["THE", "CORRECT"]
        y = 480
        for line in header_lines:
            bbox = draw.textbbox((0, 0), line, font=font_header)
            draw.text(((VIDEO_SIZE[0] - (bbox[2] - bbox[0])) // 2, y), line, font=font_header, fill=BRAND_WHITE)
            y += 100

        opt_lines = wrap_text_lines(draw, answer_text, font_answer, content_w - 200)
        card_y = y + 60
        card_h = max(160, 60 + len(opt_lines) * 54)
        draw.rounded_rectangle([margin, card_y, margin + content_w, card_y + card_h],
                                radius=32, fill=hex_to_rgb(AQUA_DARK))

        badge_r = 46
        bcx, bcy = margin + 90, card_y + card_h // 2
        draw.ellipse([bcx - badge_r, bcy - badge_r, bcx + badge_r, bcy + badge_r], fill=hex_to_rgb(BRAND_WHITE))
        bbox = draw.textbbox((0, 0), letter, font=font_badge)
        draw.text((bcx - (bbox[2] - bbox[0]) / 2, bcy - (bbox[3] - bbox[1]) / 2 - bbox[1]),
                   letter, font=font_badge, fill=hex_to_rgb(AQUA_DARK))

        oy = card_y + (card_h - len(opt_lines) * 54) // 2
        for line in opt_lines:
            draw.text((margin + 180, oy), line, font=font_answer, fill=hex_to_rgb(BRAND_BLACK))
            oy += 54

        brand_text = "@courbyte arena"
        bbox = draw.textbbox((0, 0), brand_text, font=font_brand)
        draw.text(((VIDEO_SIZE[0] - (bbox[2] - bbox[0])) // 2, VIDEO_SIZE[1] - 70),
                   brand_text, font=font_brand, fill=GRAY)

        if WATERMARK_PATH:
            logo = Image.open(WATERMARK_PATH).resize((150, 150), Image.LANCZOS)
            img.paste(logo, (VIDEO_SIZE[0] - 170, VIDEO_SIZE[1] - 240), logo.convert('RGBA'))
        return np.array(img)
    return make_frame

# ========== CREATE ONE QUESTION CLIP ==========
def create_question_clip(question_text, options, answer_text, bg_music=None, q_index=1, q_total=1):
    temp_files = []
    q_tts, q_path = make_tts(question_text)
    temp_files.append(q_path)

    idx = options.index(answer_text) if answer_text in options else 0
    letter = chr(65 + idx)
    ans_speech = f"Option {letter}. {answer_text}"
    ans_tts, ans_path = make_tts(ans_speech)
    temp_files.append(ans_path)

    ding = make_ding_sound(duration=0.3)
    countdown_audio = make_beep_sound(COUNTDOWN_SECONDS, interval=1.0)

    q_dur = q_tts.duration
    question_phase_duration = q_dur + COUNTDOWN_SECONDS + ding.duration
    question_audio = concatenate_audioclips([q_tts, countdown_audio, ding])

    question_frame_func = make_question_frame_function(question_text, options, q_dur, COUNTDOWN_SECONDS, q_index, q_total)
    question_clip = VideoClip(question_frame_func, duration=question_phase_duration)
    question_clip = question_clip.with_audio(question_audio)

    reveal_duration = ans_tts.duration + 0.4
    reveal_frame_func = make_reveal_frame_function(letter, answer_text)
    reveal_clip = VideoClip(reveal_frame_func, duration=reveal_duration)
    reveal_clip = reveal_clip.with_audio(ans_tts)

    combined = concatenate_videoclips([question_clip, reveal_clip], method="compose")

    if bg_music:
        music_clip = bg_music.with_effects([
            afx.AudioLoop(duration=combined.duration),
            afx.MultiplyVolume(0.15)
        ])
        combined = combined.with_audio(CompositeAudioClip([combined.audio, music_clip]))

    combined._temp_audio_files = temp_files
    return combined

# ========== OUTRO CLIP ==========
def create_outro_clip():
    if os.path.exists(OUTRO_VIDEO_PATH):
        clip = VideoFileClip(OUTRO_VIDEO_PATH)
        if clip.size != VIDEO_SIZE:
            clip = clip.resized(VIDEO_SIZE)
        return clip
    else:
        outro_text = "Courbyte Arena – Your AI quiz coming"
        tts_clip, tts_path = make_tts(outro_text)
        duration = tts_clip.duration + 0.5
        base_bg = build_base_card_background()
        def outro_frame(t):
            img = base_bg.copy()
            draw = ImageDraw.Draw(img)
            font = ImageFont.truetype(FONT_PATH, 50)
            lines = textwrap.wrap(outro_text, width=30)
            y = 700
            for line in lines:
                bbox = draw.textbbox((0,0), line, font=font)
                w = bbox[2] - bbox[0]
                draw.text(((VIDEO_SIZE[0]-w)//2, y), line, fill=BRAND_WHITE, font=font)
                y += 60
            brand_font = ImageFont.truetype(FONT_PATH, 30)
            bbox = draw.textbbox((0,0), "@courbyte arena", font=brand_font)
            draw.text(((VIDEO_SIZE[0]-(bbox[2]-bbox[0]))//2, VIDEO_SIZE[1]-100), "@courbyte arena", fill=AQUA_LIGHT, font=brand_font)
            return np.array(img)
        video_clip = VideoClip(outro_frame, duration=duration)
        video_clip = video_clip.with_audio(tts_clip)
        video_clip._temp_audio_files = [tts_path]
        return video_clip

# ========== GENERATE FULL VIDEO ==========
def generate_combined_video(questions, output_filename="quiz_all.mp4"):
    clips = []
    all_temp_files = []

    bg_music = None
    if os.path.exists(BACKGROUND_MUSIC_PATH):
        bg_music = AudioFileClip(BACKGROUND_MUSIC_PATH)

    total_questions = len(questions)
    hook_text = f"Can you score {total_questions} out of {total_questions} in this quiz?"
    hook_tts, hook_path = make_tts(hook_text)
    all_temp_files.append(hook_path)
    hook_bg = build_base_card_background()
    def hook_frame(t):
        img = hook_bg.copy()
        draw = ImageDraw.Draw(img)
        font = ImageFont.truetype(FONT_PATH, 60)
        lines = textwrap.wrap(hook_text, width=20)
        y = 700
        for line in lines:
            bbox = draw.textbbox((0,0), line, font=font)
            w = bbox[2] - bbox[0]
            draw.text(((VIDEO_SIZE[0]-w)//2, y), line, fill=BRAND_WHITE, font=font)
            y += 74
        return np.array(img)
    hook_clip = VideoClip(hook_frame, duration=hook_tts.duration + 0.3)
    hook_clip = hook_clip.with_audio(hook_tts)
    clips.append(hook_clip)

    for i, q in enumerate(questions):
        q_text = q['text']
        options = [opt['text'] for opt in q['options']]
        answer = [opt['text'] for opt in q['options'] if opt['isCorrect']][0]

        if len(questions) > 1:
            intro_text = f"Question {i+1}"
            intro_tts, intro_path = make_tts(intro_text)
            all_temp_files.append(intro_path)
            intro_bg = build_base_card_background()
            def intro_frame(t, num=i+1):
                img = intro_bg.copy()
                draw = ImageDraw.Draw(img)
                font = ImageFont.truetype(FONT_PATH, 80)
                text = f"Question {num}"
                bbox = draw.textbbox((0,0), text, font=font)
                w = bbox[2] - bbox[0]
                draw.text(((VIDEO_SIZE[0]-w)//2, 600), text, fill=BRAND_WHITE, font=font)
                return np.array(img)
            intro_clip = VideoClip(lambda t, n=i+1: intro_frame(t, n), duration=intro_tts.duration)
            intro_clip = intro_clip.with_audio(intro_tts)
            clips.append(intro_clip)

        q_clip = create_question_clip(q_text, options, answer, bg_music, q_index=i+1, q_total=len(questions))
        clips.append(q_clip)
        all_temp_files.extend(q_clip._temp_audio_files)

    outro_clip = create_outro_clip()
    clips.append(outro_clip)
    if hasattr(outro_clip, '_temp_audio_files'):
        all_temp_files.extend(outro_clip._temp_audio_files)

    cta_text = "Follow and subscribe for more quizzes!"
    cta_tts, cta_path = make_tts(cta_text)
    all_temp_files.append(cta_path)
    cta_bg = build_base_card_background()
    def cta_frame(t):
        img = cta_bg.copy()
        draw = ImageDraw.Draw(img)
        font_btn = ImageFont.truetype(FONT_PATH, 44)
        btn_w, btn_h = 420, 130
        gap = 50
        total_h = btn_h * 2 + gap
        start_y = (VIDEO_SIZE[1] - total_h) // 2
        cx = VIDEO_SIZE[0] // 2

        # Follow button
        y1 = start_y
        draw.rounded_rectangle([cx-btn_w//2, y1, cx+btn_w//2, y1+btn_h], radius=btn_h//2, fill=hex_to_rgb(AQUA_DARK))
        text = "FOLLOW"
        bbox = draw.textbbox((0,0), text, font=font_btn)
        draw.text((cx-(bbox[2]-bbox[0])/2, y1+btn_h//2-(bbox[3]-bbox[1])/2-bbox[1]), text, font=font_btn, fill=hex_to_rgb(BRAND_BLACK))

        # Subscribe button
        y2 = start_y + btn_h + gap
        draw.rounded_rectangle([cx-btn_w//2, y2, cx+btn_w//2, y2+btn_h], radius=btn_h//2, outline=AQUA_DARK, width=5, fill=hex_to_rgb(BRAND_BLACK))
        text = "SUBSCRIBE"
        bbox = draw.textbbox((0,0), text, font=font_btn)
        draw.text((cx-(bbox[2]-bbox[0])/2, y2+btn_h//2-(bbox[3]-bbox[1])/2-bbox[1]), text, font=font_btn, fill=AQUA_LIGHT)

        return np.array(img)
    cta_clip = VideoClip(cta_frame, duration=cta_tts.duration + 0.5)
    cta_clip = cta_clip.with_audio(cta_tts)
    clips.append(cta_clip)

    final_clip = concatenate_videoclips(clips, method="compose")
    output_path = os.path.join(tempfile.gettempdir(), output_filename)
    final_clip.write_videofile(output_path, fps=FPS, codec='libx264', audio_codec='aac')

    for f in all_temp_files:
        try: os.unlink(f)
        except: pass
    if bg_music:
        bg_music.close()
    return output_path

# ========== SUPABASE UPLOAD ==========
def upload_to_supabase(file_path, bucket_name, remote_name):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    with open(file_path, 'rb') as f:
        supabase.storage.from_(bucket_name).upload(remote_name, f, {"content-type": "video/mp4"})
    url = supabase.storage.from_(bucket_name).get_public_url(remote_name)

    # Once it's safely in Supabase, the local copy is just disk clutter.
    # Set KEEP_LOCAL_VIDEOS=true in .env if you want to keep local copies too.
    if url and os.getenv("KEEP_LOCAL_VIDEOS", "false").lower() != "true":
        try:
            os.remove(file_path)
        except Exception as e:
            print(f"[CLEANUP] Could not delete local file {file_path}: {e}")

    return url

# ========== QUIZ GENERATION (Groq + Supabase cache with retries) ==========
def generate_quiz_from_topic(topic, num_questions=5, retries=3):
    if not GROQ_API_KEY:
        raise Exception("Groq API key not configured.")

    # Try Supabase cache first (with retry)
    if SUPABASE_URL and SUPABASE_KEY:
        for attempt in range(1, retries + 1):
            try:
                supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                res = supabase.table("quiz_questions").select("questions_json").eq("topic", topic).execute()
                if res.data:
                    return res.data[0]["questions_json"]
                break
            except Exception as e:
                print(f"Supabase fetch attempt {attempt} failed: {e}")
                if attempt == retries:
                    print("Supabase fetch failed after retries, generating fresh.")
                else:
                    time.sleep(2 ** (attempt - 1))

    # Generate with Groq (with retry)
    prompt = f"""Generate {num_questions} multiple-choice quiz questions about '{topic}'.
Return ONLY a valid JSON array with this exact structure (no markdown, no extra text):
[
  {{
    "text": "question?",
    "options": ["option A","option B","option C","option D"],
    "answer": "exact text of correct option"
  }}
]"""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7
    }

    last_exception = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"Groq API error: {resp.text}")
            content = resp.json()['choices'][0]['message']['content'].strip()
            if content.startswith("```"):
                lines = content.splitlines()
                lines = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
                content = "\n".join(lines)
            questions = json.loads(content)

            formatted = []
            for q in questions:
                opts = [{"label": chr(97+i), "text": opt, "isCorrect": opt == q['answer']} for i, opt in enumerate(q['options'])]
                formatted.append({"text": q['text'], "options": opts})

            # Save to cache (best effort)
            if SUPABASE_URL and SUPABASE_KEY:
                try:
                    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                    supabase.table("quiz_questions").insert({
                        "topic": topic,
                        "questions_json": formatted
                    }).execute()
                except Exception as e:
                    if hasattr(e, 'code') and e.code == '23505':
                        print(f"Cache exists for: {topic}")
                    else:
                        print(f"Failed to cache: {e}")

            return formatted
        except Exception as e:
            last_exception = e
            if attempt < retries:
                wait = 2 ** (attempt - 1)
                print(f"Groq API attempt {attempt} failed, retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"Groq API failed after {retries} attempts.")
                raise last_exception

    raise Exception("Failed to generate questions")

# ========== AUTH ==========
def check_credentials(username, pin):
    # Used for the delete-day confirmation (still PIN-based)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    pin_hash = hashlib.sha256(pin.encode()).hexdigest()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        res = supabase.table("users").select("id").eq("username", username).eq("pin_hash", pin_hash).execute()
        return len(res.data) > 0
    except Exception as e:
        print(f"Auth error: {e}")
        return False

def check_login_credentials(username, password):
    # Used for dashboard login (now password-based, not PIN-based)
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    try:
        res = supabase.table("users").select("id").eq("username", username).eq("password_hash", password_hash).execute()
        return len(res.data) > 0
    except Exception as e:
        print(f"Auth error: {e}")
        return False

# ========== ROUTES ==========
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.get_json()
    username = data.get('username', '')
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'message': 'Missing credentials'}), 400
    if check_login_credentials(username, password):
        return jsonify({'success': True, 'token': SECRET_TOKEN, 'username': username})
    return jsonify({'success': False, 'message': 'Invalid credentials'}), 401

def verify_token(req):
    auth = req.headers.get('Authorization', '')
    return auth == f"Bearer {SECRET_TOKEN}"

@app.route('/api/schedule', methods=['GET'])
def get_schedule():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    schedule = load_schedule()
    return jsonify(schedule)

@app.route('/api/make-credits', methods=['GET'])
def get_make_credits():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    if not MAKE_API_TOKEN or not MAKE_TEAM_ID:
        return jsonify({'success': False, 'error': 'Make.com API not configured'}), 200
    try:
        url = f"https://{MAKE_ZONE}.make.com/api/v2/scenarios/consumptions"
        headers = {"Authorization": f"Token {MAKE_API_TOKEN}"}
        params = {"teamId": MAKE_TEAM_ID}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        if resp.status_code != 200:
            return jsonify({'success': False, 'error': f"Make API error: {resp.text}"}), 200
        data = resp.json()
        total_centicredits = sum(s.get('centicredits', 0) for s in data.get('scenarioConsumptions', []))
        used = total_centicredits / 100
        remaining = max(0, MAKE_CREDIT_LIMIT - used)
        return jsonify({
            'success': True,
            'used': round(used, 2),
            'limit': MAKE_CREDIT_LIMIT,
            'remaining': round(remaining, 2),
            'lastReset': data.get('lastReset')
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 200

@app.route('/api/schedule', methods=['POST'])
def update_schedule():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    new_schedule = request.get_json()
    save_schedule(new_schedule)
    return jsonify({'success': True})

@app.route('/api/generate-questions', methods=['POST'])
def generate_questions():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json()
    topic = data.get('topic', '')
    num = data.get('num', 5)
    if not topic:
        return jsonify({'success': False, 'error': 'Topic required'}), 400
    try:
        questions = generate_quiz_from_topic(topic, num)
        return jsonify({'success': True, 'questions': questions, 'topic': topic})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/create-video', methods=['POST'])
def create_video():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json()
    questions = data.get('questions')
    topic = data.get('topic', '')
    num = data.get('num', 5)
    auto_post = data.get('auto_post', True)
    if not questions and not topic:
        return jsonify({'success': False, 'error': 'Either questions or topic required'}), 400

    try:
        if questions:
            formatted_questions = questions
        else:
            formatted_questions = generate_quiz_from_topic(topic, num)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"quiz_{timestamp}.mp4"
        path = generate_combined_video(formatted_questions, output_filename=filename)
        url = upload_to_supabase(path, SUPABASE_BUCKET, filename)
        if url and auto_post:
            notify_make(url, topic or (formatted_questions[0]['text'][:30]), filename)
        return jsonify({'success': True, 'files': [filename], 'supabase_url': url, 'auto_post': auto_post})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/generate-slot', methods=['POST'])
def generate_slot():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json()
    date_str = data.get('date')
    slot_time = data.get('time')
    if not date_str or not slot_time:
        return jsonify({'success': False, 'error': 'Missing date or time'}), 400
    schedule = load_schedule()
    for day in schedule:
        if day['date'] == date_str:
            for slot in day['slots']:
                if slot['time'] == slot_time and slot['topic'].strip():
                    topic = slot['topic'].strip()
                    num_questions = slot.get('questions', 5)
                    auto_post = slot.get('auto_post', True)
                    try:
                        questions = generate_quiz_from_topic(topic, num_questions)
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                        filename = f"quiz_{timestamp}.mp4"
                        path = generate_combined_video(questions, output_filename=filename)
                        url = upload_to_supabase(path, SUPABASE_BUCKET, filename)
                        if url and auto_post:
                            notify_make(url, topic, filename)
                        return jsonify({'success': True, 'files': [filename], 'supabase_url': url, 'topic': topic, 'auto_post': auto_post})
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        return jsonify({'success': False, 'error': str(e)}), 500
            return jsonify({'success': False, 'error': 'Slot not found or no topic'}), 404
    return jsonify({'success': False, 'error': 'Date not found'}), 404

@app.route('/api/delete-day', methods=['POST'])
def delete_day():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json()
    username = data.get('username', '')
    pin = data.get('pin', '')
    date_str = data.get('date', '')
    if not username or not pin or not date_str:
        return jsonify({'success': False, 'error': 'Missing fields'}), 400

    if not check_credentials(username, pin):
        return jsonify({'success': False, 'error': 'Invalid PIN'}), 403

    schedule = load_schedule()
    new_schedule = []
    found = False
    for day in schedule:
        if day['date'] == date_str:
            found = True
            if SUPABASE_URL and SUPABASE_KEY:
                supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
                for slot in day['slots']:
                    if slot['topic'].strip():
                        try:
                            supabase.table("quiz_questions").delete().eq("topic", slot['topic'].strip()).execute()
                        except Exception as e:
                            print(f"Failed to delete questions: {e}")
        else:
            new_schedule.append(day)
    if not found:
        return jsonify({'success': False, 'error': 'Date not found'}), 404
    save_schedule(new_schedule)
    return jsonify({'success': True})

@app.route('/api/suggest-topics', methods=['POST'])
def suggest_topics():
    if not verify_token(request):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    data = request.get_json()
    selected_dates = data.get('dates', [])
    if not selected_dates:
        return jsonify({'success': False, 'error': 'No dates provided'}), 400
    schedule = load_schedule()

    used_topics = get_used_topics()
    exclusion_note = f" Do NOT suggest any of these already-used topics: {', '.join(used_topics[-50:])}." if used_topics else ""
    prompt = f"""For the following dates: {', '.join(selected_dates)}, suggest two educational quiz topics per day (one for 09:00 and one for 18:00). The topics should be different each day and suitable for short quiz videos.{exclusion_note} Return a JSON object where keys are dates and values are arrays of two strings (first for 09:00, second for 18:00). Example: {{"2026-07-14":["Photosynthesis","World Capitals"],"2026-07-15":["Algebra Basics","Ancient Rome"]}}. Only return the JSON, no other text."""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.9}
    try:
        resp = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise Exception("Groq API error")
        content = resp.json()['choices'][0]['message']['content'].strip()
        if content.startswith("```"):
            lines = content.splitlines()
            lines = lines[1:-1] if lines[-1].startswith("```") else lines[1:]
            content = "\n".join(lines)
        suggested = json.loads(content)
    except Exception as e:
        fallback = ["Photosynthesis","World capitals","Basic algebra","Newton's laws of motion","Human digestive system","Introduction to Python","Solar system","Shakespeare plays","Types of rocks","African geography"]
        suggested = {}
        for i, dt in enumerate(selected_dates):
            suggested[dt] = [fallback[(i*2) % len(fallback)], fallback[(i*2+1) % len(fallback)]]

    for dt, topics in suggested.items():
        found = False
        for day in schedule:
            if day['date'] == dt:
                if len(topics) >= 1:
                    day['slots'][0]['topic'] = topics[0]
                if len(topics) >= 2:
                    day['slots'][1]['topic'] = topics[1]
                found = True
                break
        if not found:
            new_day = {"date": dt, "slots": [
                {"time": "09:00", "topic": topics[0] if len(topics)>=1 else "", "questions": 5, "posted": False, "auto_post": True},
                {"time": "18:00", "topic": topics[1] if len(topics)>=2 else "", "questions": 5, "posted": False, "auto_post": True}
            ]}
            schedule.append(new_day)

    save_schedule(schedule)
    return jsonify({'success': True, 'schedule': schedule})

# ========== SCHEDULE FILE ==========
SCHEDULE_FILE = "schedule.json"

def load_schedule():
    if not os.path.exists(SCHEDULE_FILE):
        today = datetime.now().strftime("%Y-%m-%d")
        default = [{
            "date": today,
            "slots": [
                {"time": "09:00", "topic": "", "questions": 5, "posted": False, "auto_post": True},
                {"time": "18:00", "topic": "", "questions": 5, "posted": False, "auto_post": True}
            ]
        }]
        return default
    with open(SCHEDULE_FILE, "r") as f:
        return json.load(f)

def save_schedule(schedule):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(schedule, f, indent=2)

# ========== SCHEDULER ==========
FALLBACK_TOPICS = [
    "Photosynthesis", "World capitals", "Basic algebra", "Newton's laws of motion",
    "Human digestive system", "Introduction to Python", "Solar system",
    "Shakespeare plays", "Types of rocks", "African geography"
]

def get_used_topics():
    """Returns every topic we've already generated a quiz for, so we can avoid repeats."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        res = supabase.table("quiz_questions").select("topic").execute()
        return [row["topic"] for row in res.data]
    except Exception as e:
        print(f"[SCHEDULER] Could not fetch used topics: {e}")
        return []

def generate_fresh_ai_topic(used_topics):
    """Asks Groq for one brand-new quiz topic that hasn't been used before."""
    if not GROQ_API_KEY:
        return None
    exclusion_list = ", ".join(used_topics[-50:]) if used_topics else "none yet"
    prompt = f"""Suggest ONE single educational quiz topic suitable for a short quiz video.
It must be DIFFERENT from all of these already-used topics: {exclusion_list}
Reply with ONLY the topic name, nothing else - no quotes, no explanation, no markdown."""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "llama-3.3-70b-versatile", "messages": [{"role": "user", "content": prompt}], "temperature": 0.9}
    try:
        resp = requests.post(GROQ_API_URL, json=payload, headers=headers, timeout=20)
        if resp.status_code != 200:
            return None
        topic = resp.json()['choices'][0]['message']['content'].strip().strip('"')
        if topic and topic not in used_topics:
            return topic
        return None
    except Exception as e:
        print(f"[SCHEDULER] Fresh AI topic generation failed: {e}")
        return None

def get_scheduled_slot():
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    schedule = load_schedule()
    for day in schedule:
        if day['date'] == today_str:
            for slot in day['slots']:
                if slot['time'] == current_time and slot['topic'].strip():
                    return slot['topic'].strip(), slot.get('questions', 5), slot.get('auto_post', True)
    return None, None, True

topic_index = 0

def get_next_fallback_topic():
    global topic_index
    used_topics = get_used_topics()

    # First choice: ask the AI for something genuinely new
    fresh_topic = generate_fresh_ai_topic(used_topics)
    if fresh_topic:
        return fresh_topic

    # Second choice: pick a static-list topic that hasn't been used yet
    unused_static = [t for t in FALLBACK_TOPICS if t not in used_topics]
    if unused_static:
        return unused_static[0]

    # Last resort: everything has been used before - cycle as before
    # rather than fail outright.
    topics = FALLBACK_TOPICS.copy()
    topic = topics[topic_index % len(topics)]
    topic_index += 1
    return topic

def scheduled_video_generation():
    try:
        topic, num_questions, auto_post = get_scheduled_slot()
        if not topic:
            topic = get_next_fallback_topic()
            num_questions = 5
            auto_post = True
        print(f"\n[SCHEDULER] Generating quiz for topic: {topic} ({num_questions} questions)")
        questions = generate_quiz_from_topic(topic, num_questions)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"quiz_{timestamp}.mp4"
        path = generate_combined_video(questions, output_filename=filename)
        url = upload_to_supabase(path, SUPABASE_BUCKET, filename)
        if url and auto_post:
            notify_make(url, topic, filename)
        print(f"[SCHEDULER] Video created: {filename}")
    except Exception as e:
        print(f"[SCHEDULER] Error: {e}")

scheduler = BackgroundScheduler()
scheduler.add_job(func=scheduled_video_generation, trigger="cron", hour=9, minute=0)
scheduler.add_job(func=scheduled_video_generation, trigger="cron", hour=18, minute=0)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

if __name__ == '__main__':
    app.run(debug=False, port=5000)