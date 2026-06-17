"""
soundtrack.py — Build Xbox ST.DB databases and convert audio to WMA.
"""
import os
import re
import struct
import json
import shutil
import subprocess

MAGIC_MAIN = 0x00000001
MAGIC_SOUNDTRACK = 0x00021371
MAGIC_SONG_GROUP = 0x00031073

BLOCK_SIZE = 512
MAX_SOUNDTRACKS = 100
SONGS_PER_GROUP = 6
MAX_SONG_GROUPS = 84
SONG_NAME_CHARS = 32
SOUNDTRACK_NAME_CHARS = 64

AUDIO_EXTS = {".mp3", ".wma", ".flac", ".ogg", ".aac", ".m4a", ".wav"}

def encode_wchar_field(text, num_chars):
    encoded = text.encode("utf-16-le")
    max_bytes = num_chars * 2
    if len(encoded) > max_bytes - 2:
        encoded = encoded[:max_bytes - 2]
    return encoded + b"\x00" * (max_bytes - len(encoded))

def pad_to(data, size):
    if len(data) > size:
        raise ValueError(f"Data ({len(data)}) exceeds block size ({size})")
    return data + b"\x00" * (size - len(data))

def clean_track_name(filename):
    base = os.path.splitext(filename)[0]
    name = re.sub(r"^\d+[\.\-\s]+\s*", "", base).strip()
    return name[:31] if name else base[:31]

def ffprobe_duration_ms(filepath, ffprobe="ffprobe"):
    try:
        result = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", filepath],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        for stream in info.get("streams", []):
            dur = stream.get("duration")
            if dur: return int(float(dur) * 1000)
    except Exception:
        pass
    return 0

def convert_to_wma(src, dst, codec="wmav1", bitrate="128k", ffmpeg="ffmpeg"):
    result = subprocess.run(
        [ffmpeg, "-y", "-i", src, "-codec:a", codec, "-b:a", bitrate, "-ar", "44100", "-ac", "2", dst],
        capture_output=True, text=True, timeout=300,
    )
    return result.returncode == 0

def build_main_header(num_soundtracks, next_soundtrack_id, soundtrack_ids, next_song_id):
    data = struct.pack("<i", MAGIC_MAIN)
    data += struct.pack("<i", num_soundtracks)
    data += struct.pack("<i", next_soundtrack_id)
    ids = struct.pack(f"<{len(soundtrack_ids)}i", *soundtrack_ids)
    ids += b"\x00" * ((MAX_SOUNDTRACKS - len(soundtrack_ids)) * 4)
    data += ids
    data += struct.pack("<i", next_song_id)
    return pad_to(data, BLOCK_SIZE)

def build_soundtrack_struct(st_id, name, num_songs, song_group_indices, total_ms):
    data = struct.pack("<i", MAGIC_SOUNDTRACK)
    data += struct.pack("<i", st_id)
    data += struct.pack("<I", num_songs)
    ids = struct.pack(f"<{len(song_group_indices)}i", *song_group_indices)
    ids += b"\x00" * ((MAX_SONG_GROUPS - len(song_group_indices)) * 4)
    data += ids
    data += struct.pack("<i", total_ms)
    data += encode_wchar_field(name, SOUNDTRACK_NAME_CHARS)
    return pad_to(data, BLOCK_SIZE)

def build_song_group_struct(st_id, group_id, song_ids, song_times_ms, song_names):
    def pad_list(lst, n, fill=0):
        return list(lst) + [fill] * (n - len(lst))
    song_ids = pad_list(song_ids, SONGS_PER_GROUP, 0)
    song_times_ms = pad_list(song_times_ms, SONGS_PER_GROUP, 0)
    song_names = pad_list(song_names, SONGS_PER_GROUP, "")
    
    data = struct.pack("<i", MAGIC_SONG_GROUP)
    data += struct.pack("<i", st_id)
    data += struct.pack("<i", group_id)
    data += struct.pack("<i", 0)
    data += struct.pack("<6i", *song_ids)
    data += struct.pack("<6i", *song_times_ms)
    for name in song_names:
        data += encode_wchar_field(name, SONG_NAME_CHARS)
    return pad_to(data, BLOCK_SIZE)

def build_stdb(soundtracks):
    num_soundtracks = len(soundtracks)
    all_song_groups = []
    st_structs = []
    next_grp_id = 1000  # Safe boundary
    next_grp_index = 0
    soundtrack_ids = []
    max_song_id = -1

    for st_idx, st in enumerate(soundtracks):
        st_id = st.get("st_id", st_idx + 1)
        soundtrack_ids.append(st_id)
        songs = st["songs"]
        total_ms = sum(s["duration_ms"] for s in songs)
        song_group_indices = []

        for g_start in range(0, len(songs), SONGS_PER_GROUP):
            group_songs = songs[g_start:g_start + SONGS_PER_GROUP]
            grp_id = next_grp_id
            next_grp_id += 1
            song_group_indices.append(next_grp_index)
            next_grp_index += 1

            s_ids, s_times, s_names = [], [], []
            for song in group_songs:
                sid = song["song_id"]
                # Only track the lower 16 bits for the global counter
                max_song_id = max(max_song_id, sid & 0xFFFF)
                s_ids.append(sid)
                s_times.append(song["duration_ms"])
                s_names.append(song["name"])
            
            all_song_groups.append(build_song_group_struct(st_id, grp_id, s_ids, s_times, s_names))
        st_structs.append(build_soundtrack_struct(st_id, st["name"], len(songs), song_group_indices, total_ms))

    main_header = build_main_header(num_soundtracks, max(soundtrack_ids, default=0) + 1, soundtrack_ids, max_song_id + 1)
    st_section = b"".join(st_structs)
    st_section += b"\x00" * (51200 - num_soundtracks * BLOCK_SIZE)
    return main_header + st_section + b"".join(all_song_groups)