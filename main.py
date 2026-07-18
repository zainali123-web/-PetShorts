"""
Automated YouTube Shorts Generator - Cat & Dog Facts Channel
----------------------------------------------------------------
This script:
1. Picks an unused fact from facts_database.json (auto-recycles when exhausted)
2. Generates an AI voiceover (free, using edge-tts) with a spoken like/subscribe CTA
3. Downloads matching vertical stock video clips from Pexels (free API, avoids repeats)
4. Combines them into a 60-second-or-less YouTube Short with synced, chunked captions
5. Uploads the video directly to YouTube (free Data API)
"""

import os
import json
import random
import asyncio
import requests

import edge_tts
from moviepy.editor import (
    VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, vfx,
    concatenate_videoclips
)

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------------- CONFIG ----------------
FACTS_FILE = "facts_database.json"
USED_CLIPS_FILE = "used_clips.json"
OUTPUT_VIDEO = "output_short.mp4"
VOICE = "en-US-JennyNeural"        # warm, friendly voice - good fit for a pet channel
VOICE_RATE = "-4%"
VOICE_PITCH = "-2Hz"
VIDEO_SIZE = (1080, 1920)
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY")
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


# ---------------- STEP 1: Pick a fact (auto-recycles, never runs out) ----------------
def get_next_fact():
    with open(FACTS_FILE, "r") as f:
        data = json.load(f)

    unused = [f for f in data["facts"] if not f["used"]]
    if not unused:
        for f in data["facts"]:
            f["used"] = False
        unused = data["facts"]

    fact = random.choice(unused)
    for f in data["facts"]:
        if f["id"] == fact["id"]:
            f["used"] = True
    with open(FACTS_FILE, "w") as f:
        json.dump(data, f, indent=2)

    return fact


# ---------------- STEP 2: Generate voiceover ----------------
async def generate_voiceover(text, output_path="voice.mp3"):
    communicate = edge_tts.Communicate(text, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH)
    await communicate.save(output_path)
    return output_path


# ---------------- STEP 3: Download multiple matching stock clips (avoids repeats) ----------------
def load_used_clip_ids():
    if os.path.exists(USED_CLIPS_FILE):
        with open(USED_CLIPS_FILE, "r") as f:
            return json.load(f)
    return []


def save_used_clip_ids(ids):
    with open(USED_CLIPS_FILE, "w") as f:
        json.dump(ids[-200:], f)


def download_stock_clips(keyword, num_clips=3):
    url = "https://api.pexels.com/videos/search"
    headers = {"Authorization": PEXELS_API_KEY}

    used_ids = load_used_clip_ids()
    all_candidates = []

    for page in range(1, 3):
        params = {"query": keyword, "orientation": "portrait", "per_page": 15, "page": page}
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        results = response.json()
        all_candidates.extend(results.get("videos", []))
        if not results.get("videos"):
            break

    if not all_candidates:
        raise Exception(f"No stock video found for keyword: {keyword}")

    fresh = [v for v in all_candidates if v["id"] not in used_ids]
    pool = fresh if len(fresh) >= num_clips else all_candidates

    chosen = random.sample(pool, min(num_clips, len(pool)))
    paths = []

    for i, video in enumerate(chosen):
        video_files = sorted(video["video_files"], key=lambda v: v.get("height", 0), reverse=True)
        video_url = video_files[0]["link"]
        video_data = requests.get(video_url)
        path = f"clip_{i}.mp4"
        with open(path, "wb") as f:
            f.write(video_data.content)
        paths.append(path)
        used_ids.append(video["id"])

    save_used_clip_ids(used_ids)
    return paths


# ---------------- STEP 4: Combine multiple clips into final dynamic Short ----------------
def create_short(clip_paths, audio_path, caption_text, output_path=OUTPUT_VIDEO):
    audio = AudioFileClip(audio_path)
    duration = min(audio.duration, 59)

    per_clip = duration / len(clip_paths)
    processed_clips = []

    for path in clip_paths:
        clip = VideoFileClip(path)
        safe_start = min(1.0, max(0, clip.duration - per_clip - 0.5))
        if clip.duration < per_clip + safe_start:
            clip = clip.fx(vfx.loop, duration=per_clip)
        else:
            clip = clip.subclip(safe_start, safe_start + per_clip)

        # "Cover" resize - fills the full 1080x1920 frame with no black bars,
        # whatever the source clip's original aspect ratio is
        scale = max(VIDEO_SIZE[0] / clip.w, VIDEO_SIZE[1] / clip.h)
        clip = clip.resize(scale)
        clip = clip.crop(
            x_center=clip.w / 2, y_center=clip.h / 2,
            width=VIDEO_SIZE[0], height=VIDEO_SIZE[1]
        )
        if clip.w < VIDEO_SIZE[0]:
            scale_factor = VIDEO_SIZE[0] / clip.w
            clip = clip.resize(scale_factor)
            clip = clip.crop(x_center=clip.w / 2, y_center=clip.h / 2,
                              width=VIDEO_SIZE[0], height=VIDEO_SIZE[1])

        clip = clip.fx(vfx.resize, lambda t: 1 + 0.03 * t)
        clip = clip.fx(vfx.fadein, 0.25).fx(vfx.fadeout, 0.25)
        processed_clips.append(clip)

    video = concatenate_videoclips(processed_clips, method="compose")

    # Dynamic captions: short chunks appear one at a time, timed proportionally
    # to word count so they sync naturally with the voiceover
    words = caption_text.split()
    chunk_size = 6
    chunks = [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]

    caption_clips = []
    total_words = sum(len(c.split()) for c in chunks)
    current_time = 0
    for chunk in chunks:
        chunk_words = len(chunk.split())
        chunk_duration = duration * (chunk_words / total_words)
        txt_clip = TextClip(
            chunk,
            fontsize=70,
            color="white",
            font="DejaVu-Sans-Bold",
            method="caption",
            size=(VIDEO_SIZE[0] - 160, None),
            stroke_color="black",
            stroke_width=3,
        ).set_position(("center", "center")).set_start(current_time).set_duration(chunk_duration).crossfadein(0.15)
        caption_clips.append(txt_clip)
        current_time += chunk_duration

    final = CompositeVideoClip([video] + caption_clips).set_audio(audio.subclip(0, duration))
    final = final.fx(vfx.fadein, 0.3).fx(vfx.fadeout, 0.4)
    final.write_videofile(
        output_path,
        fps=30,
        codec="libx264",
        audio_codec="aac",
        bitrate="8000k",
        audio_bitrate="192k",
        preset="slow",
    )
    return output_path


# ---------------- STEP 5: Upload to YouTube ----------------
def get_youtube_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(__import__("google.auth.transport.requests", fromlist=["Request"]).Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", YOUTUBE_SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_short(youtube, video_path, title, description):
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": [
                "catfacts", "dogfacts", "animalbehavior", "petpsychology",
                "catbehavior", "dogbehavior", "animalscience", "didyouknow",
                "petsofyoutube", "shorts"
            ],
            "categoryId": "15",  # Pets & Animals
        },
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = request.execute()
    print(f"Uploaded! Video ID: {response['id']}")
    return response["id"]


# ---------------- MAIN ----------------
def main():
    print("Picking a fact...")
    fact = get_next_fact()
    print(f"Fact chosen: {fact['text']}")

    # Spoken + captioned CTA at the end - built into the same narration text
    # so it's automatically in sync in both the voice and the captions
    cta = " If you love animals, hit subscribe for a new cat or dog fact every day."
    narration_text = fact["text"] + cta

    print("Generating voiceover...")
    asyncio.run(generate_voiceover(narration_text))

    print("Downloading stock video clips...")
    clip_paths = download_stock_clips(fact["search_keyword"], num_clips=3)

    print("Creating final short (multi-cut edit)...")
    create_short(clip_paths, "voice.mp3", narration_text)

    print("Uploading to YouTube...")
    youtube = get_youtube_service()
    title = "🐾 " + fact["text"][:55] + "... #shorts #catfacts #dogfacts"
    description = (
        f"{fact['text']} 😂🐶🐱\n\n"
        "#shorts #catfacts #dogfacts #animalbehavior #petpsychology "
        "#catbehavior #dogbehavior #animalscience #didyouknow #petsofyoutube"
    )
    upload_short(youtube, OUTPUT_VIDEO, title, description)

    print("Done! Cleaning up temp files...")
    for temp_file in ["voice.mp3", OUTPUT_VIDEO] + clip_paths:
        if os.path.exists(temp_file):
            os.remove(temp_file)


if __name__ == "__main__":
    main()
