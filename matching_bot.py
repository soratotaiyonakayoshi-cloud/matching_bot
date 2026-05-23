import discord
from discord import app_commands
import random
import asyncio
import os
import io
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

# --- 🎵 音声再生用の環境セットアップ ---
try:
    import static_ffmpeg
    static_ffmpeg.add_paths() 
except Exception as e:
    print(f"FFmpegセットアップ警告: {e}", flush=True)

# --- Webサーバー設定 ---
app = Flask('')
@app.route('/')
def home():
    return "キャンパスアシストBotは正常稼働中です！"

def run_server():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# --- 環境変数と初期設定 ---
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CONFIG_CHANNEL_ID = int(os.getenv("CONFIG_CHANNEL_ID", "0"))
WORK_LOG_CHANNEL_ID = int(os.getenv("WORK_LOG_CHANNEL_ID", "0"))
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", "0"))
AUDIO_DB_CHANNEL_ID = int(os.getenv("AUDIO_DB_CHANNEL_ID", "0")) 
JST = timezone(timedelta(hours=9))

# マッチング必要人数（デフォルト値）
COUNT_CHAT = 3  
COUNT_LOVE = 2  
COUNT_WORK = 4  

# 待機リスト
waiting_chat = []
waiting_love = []
waiting_work = [] 

created_temp_channels = []    
work_vc_start_times = {}  
work_vc_contents = {}     
active_pomodoros = {}     

# 👤 NGリスト (Key: user_id(int) -> Value: list of blocked_user_ids(int))
ng_relations = {}

# 🎵 音声URL設定
AUDIO_WORK_END_URL = ""   
AUDIO_BREAK_END_URL = ""  

# ⏱️ ポモドーロタイマーの時間設定
POMODORO_WORK_MIN = 25
POMODORO_BREAK_MIN = 5

# 🗣️ お題リスト
ODAI_CHAT = [
    "🏫「農工大の周辺で、ぶっちゃけ一番おすすめのご飯屋さんは？」",
    "📚「今期履修している中で、一番面白い（またはヤバい）講講義は？」",
    "☕「最近のマイブームや、新しく始めた趣味について！」"
]

ODAI_LOVE = [
    "💓「ぶっちゃけ、初恋って何歳のときだった？」",
    "💓「理想の休日のデートコースを妄想で語って！」",
    "💓「恋人に求める条件、どうしても譲れないものは？」"
]

# --- データベース保存・復元 ---
async def save_all_config():
    config_channel = bot.get_channel(CONFIG_CHANNEL_ID)
    if config_channel:
        lines = ["===NG_START==="]
        for user_id, ng_list in ng_relations.items():
            if ng_list: lines.append(f"{user_id}>{','.join(map(str, ng_list))}")
        lines.append("===NG_END===")
        
        lines.append("===ODAI_CHAT_START===")
        for odai in ODAI_CHAT: lines.append(odai)
        lines.append("===ODAI_CHAT_END===")

        lines.append("===ODAI_LOVE_START===")
        for odai in ODAI_LOVE: lines.append(odai)
        lines.append("===ODAI_LOVE_END===")
        
        lines.append("===AUDIO_START===")
        lines.append(f"WORK_END>{AUDIO_WORK_END_URL}")
        lines.append(f"BREAK_END>{AUDIO_BREAK_END_URL}")
        lines.append("===AUDIO_END===")
        
        lines.append("===POMODORO_TIME_START===")
        lines.append(f"WORK_MIN>{POMODORO_WORK_MIN}")
        lines.append(f"BREAK_MIN>{POMODORO_BREAK_MIN}")
        lines.append("===POMODORO_TIME_END===")

        lines.append("===MATCH_COUNT_START===")
        lines.append(f"CHAT>{COUNT_CHAT}")
        lines.append(f"LOVE>{COUNT_LOVE}")
        lines.append(f"WORK>{COUNT_WORK}")
        lines.append("===MATCH_COUNT_END===")
        
        await config_channel.send("\n".join(lines))

async def load_all_config():
    global ng_relations, ODAI_CHAT, ODAI_LOVE, AUDIO_WORK_END_URL, AUDIO_BREAK_END_URL, POMODORO_WORK_MIN, POMODORO_BREAK_MIN
    global COUNT_CHAT, COUNT_LOVE, COUNT_WORK
    config_channel = bot.get_channel(CONFIG_CHANNEL_ID)
    if config_channel:
        async for message in config_channel.history(limit=1):
            if not message.content: return
            try:
                lines = message.content.split("\n")
                mode = None
                temp_ng, temp_chat, temp_love = {}, [], []
                for line in lines:
                    if line == "===NG_START===": mode = "NG"; continue
                    elif line == "===NG_END===": mode = None; continue
                    elif line == "===ODAI_CHAT_START===": mode = "CHAT"; continue
                    elif line == "===ODAI_CHAT_END===": mode = None; continue
                    elif line == "===ODAI_LOVE_START===": mode = "LOVE"; continue
                    elif line == "===ODAI_LOVE_END===": mode = None; continue
                    elif line == "===AUDIO_START===": mode = "AUDIO"; continue
                    elif line == "===AUDIO_END===": mode = None; continue
                    elif line == "===POMODORO_TIME_START===": mode = "POMODORO"; continue
                    elif line == "===POMODORO_TIME_END===": mode = None; continue
                    elif line == "===MATCH_COUNT_START===": mode = "MATCH_COUNT"; continue
                    elif line == "===MATCH_COUNT_END===": mode = None; continue
                        
                    if mode == "NG" and ">" in line:
                        uid, nlist = line.split(">")
