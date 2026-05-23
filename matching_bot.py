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

# マッチング必要人数（デフォルト値。コマンドで可変になります）
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
    "📚「今期履修している中で、一番面白い（またはヤバい）講義は？」",
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
                        temp_ng[int(uid)] = list(map(int, nlist.split(",")))
                    elif mode == "CHAT" and line.strip(): temp_chat.append(line)
                    elif mode == "LOVE" and line.strip(): temp_love.append(line)
                    elif mode == "AUDIO" and ">" in line:
                        key, url = line.split(">")
                        if key == "WORK_END": AUDIO_WORK_END_URL = url
                        elif key == "BREAK_END": AUDIO_BREAK_END_URL = url
                    elif mode == "POMODORO" and ">" in line:
                        key, val = line.split(">")
                        if key == "WORK_MIN": POMODORO_WORK_MIN = int(val)
                        elif key == "BREAK_MIN": POMODORO_BREAK_MIN = int(val)
                    elif mode == "MATCH_COUNT" and ">" in line:
                        key, val = line.split(">")
                        if key == "CHAT": COUNT_CHAT = int(val)
                        elif key == "LOVE": COUNT_LOVE = int(val)
                        elif key == "WORK": COUNT_WORK = int(val)
                        
                if temp_ng: ng_relations = temp_ng
                if temp_chat: ODAI_CHAT = temp_chat
                if temp_love: ODAI_LOVE = temp_love
                print(f"★設定復元：雑談({COUNT_CHAT}人) 恋バナ({COUNT_LOVE}人) 作業({COUNT_WORK}人)", flush=True)
            except Exception as e:
                print(f"設定復元エラー: {e}", flush=True)

def check_compatibility(potential_group):
    for user_a in potential_group:
        for user_b in potential_group:
            if user_a.id == user_b.id: continue
            if user_b.id in ng_relations.get(user_a.id, []) or user_a.id in ng_relations.get(user_b.id, []):
                return False
    return True

def create_panel_embed():
    return discord.Embed(
        title="🚪 キャンパス ラウンジガチャ",
        description=(
            "今の気分に合わせてボタンを押してね！\n"
            "必要な人数が集まった瞬間、専用のVCに自動で引きずり込まれます。\n\n"
            f"☕ **雑談**（{COUNT_CHAT}人）：気軽な話題でワイワイ\n"
            f"💓 **恋バナ**（{COUNT_LOVE}人）：専用の甘酸っぱいお題が出ます\n"
            f"📝 **作業**（{COUNT_WORK}人）：集中モード！解散時に作業内容と時間が記録されます✍️\n\n"
            "💡 **キャンセル方法**：エントリー中に**同じボタンをもう一度押す**と取り消せます。\n"
            "※ `/matching_guard` で苦手な人をこっそりブロック可能です。"
        ),
        color=discord.Color.blurple()
    )

class OdaiRerollView(discord.ui.View):
    def __init__(self, category: str):
        super().__init__(timeout=None)
        self.category = category

    @discord.ui.button(label="🎲 次のお題を引く", style=discord.ButtonStyle.blurple, custom_id="btn_reroll_odai")
    async def reroll_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        global ODAI_CHAT, ODAI_LOVE
        odai_list = ODAI_CHAT if self.category == "chat" else ODAI_LOVE
        if not odai_list:
            await interaction.response.send_message("❌ お題リストが空っぽです！", ephemeral=True)
            return
        selected_odai = random.choice(odai_list)
        await interaction.response.send_message(f"🎲 **新しくお題を引いたよ！**\n> **{selected_odai}**")

async def play_notification_audio(vc_channel, audio_url):
    if not audio_url: return
    try:
        vc = await vc_channel.connect()
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        vc.play(discord.FFmpegPCMAudio(audio_url, **ffmpeg_options))
        while vc.is_playing(): await asyncio.sleep(1)
        await vc.disconnect()
    except Exception as e:
        print(f"🔊 音声再生エラー: {e}", flush=True)
        try:
            for v in bot.voice_clients:
                if v.channel.id == vc_channel.id: await v.disconnect()
        except: pass

async def pomodoro_loop(channel, work_min, break_min):
    try:
        while True:
            await asyncio.sleep(work_min * 60)
            await channel.send(f"🔔 **【ポモドーロ】{work_min}分が経ちました！{break_min}分間の【休憩】に入ってください！** @here")
            asyncio.create_task(play_notification_audio(channel, AUDIO_WORK_END_URL)) 
            
            await asyncio.sleep(break_min * 60)
            await channel.send(f"⚔️ **【ポモドーロ】{break_min}分が経ちました！【作業再開】です。集中していきましょう！** @here")
            asyncio.create_task(play_notification_audio(channel, AUDIO_BREAK_END_URL)) 
    except asyncio.CancelledError:
        pass

class PomodoroView(discord.ui.View):
    def __init__(self, channel_id: int, work_min: int, break_min: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.work_min = work_min
        self.break_min = break_min
        self.start_pomo.label = f"⏱️ ポモドーロ開始 ({work_min}分/{break_min}分)"

    @discord.ui.button(label="⏱️ ポモドーロ開始", style=discord.ButtonStyle.green, custom_id="btn_pomo_start")
    async def start_pomo(self, interaction: discord.Interaction, button: discord.ui.Button):
        global active_pomodoros
        channel = interaction.channel
        if self.channel_id in active_pomodoros:
            await interaction.response.send_message("⚠️ 既にこの部屋でタイマーが作動中です！", ephemeral=True)
            return
        await interaction.response.send_message(f"⏱️ **ポモドーロタイマーを開始しました！**\n（{self.work_min}分作業 ➔ {self.break_min}分休憩 を繰り返します）")
        task = asyncio.create_task(pomodoro_loop(channel, self.work_min, self.break_min))
        active_pomodoros[self.channel_id] = task

    @discord.ui.button(label="⏹️ タイマー停止", style=discord.ButtonStyle.red, custom_id="btn_pomo_stop")
    async def stop_pomo(self, interaction: discord.Interaction, button: discord.ui.Button):
        global active_pomodoros
        if self.channel_id in active_pomodoros:
            task = active_pomodoros.pop(self.channel_id)
            task.cancel()
            await interaction.response.send_message("⏹️ ポモドーロタイマーを停止しました。")
        else:
            await interaction.response.send_message("❌ 現在作動中のタイマーはありません。", ephemeral=True)

class WorkModal(discord.ui.Modal, title="📝 今日の作業内容を入力"):
    content = discord.ui.TextInput(
        label="今から何をする？（みんなにシェアされます）",
        placeholder="例：数Ⅰのレポート、イラスト練習、積読の消化",
        required=True,
        max_length=50
    )

    def __init__(self, matching_view):
        super().__init__()
        self.m_view = matching_view

    async def on_submit(self, interaction: discord.Interaction):
        await self.m_view.handle_work_entry(interaction, self.content.value)

class MatchingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def update_labels(self):
        self.btn_chat.label = f"☕ 雑談 ({len(waiting_chat)}/{COUNT_CHAT}人)"
        self.btn_love.label = f"💓 恋バナ ({len(waiting_love)}/{COUNT_LOVE}人)"
        self.btn_work.label = f"📝 作業 ({len(waiting_work)}/{COUNT_WORK}人)"

    async def handle_standard_entry(self, interaction: discord.Interaction, target_list: list, target_count: int, mode_name: str, emoji: str, category: str):
        user = interaction.user
        
        if user in target_list:
            target_list.remove(user)
            self.update_labels()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"➔ {emoji} {mode_name}へのエントリーを取り消しました。", ephemeral=True)
            return

        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にどこかのボイスチャンネル（VC）に入室してからボタンを押してください！", ephemeral=True)
            return

        if user in waiting_chat: waiting_chat.remove(user)
        if user in waiting_love: waiting_love.remove(user)
        global waiting_work
        waiting_work = [w for w in waiting_work if w["user"].id != user.id]

        target_list.append(user)
        self.update_labels()
        await interaction.response.edit_message(view=self)

        if len(target_list) >= target_count:
            current_combination = target_list[:target_count]
            if check_compatibility(current_combination):
                guild = interaction.guild
                temp_channel = await guild.create_voice_channel(name=f"{emoji} 臨時{mode_name}VC-#{guild.id % 1000:03d}", category=user.voice.channel.category)
                created_temp_channels.append(temp_channel.id)

                for member in current_combination:
                    if member in target_list: target_list.remove(member)
                    try: await member.move_to(temp_channel)
                    except: pass

                self.update_labels()
                await interaction.message.edit(view=self)

                odai_list = ODAI_CHAT if category == "chat" else ODAI_LOVE
                selected_odai = random.choice(odai_list) if odai_list else "自由にお喋りしてください！"
                
                odai_msg = await temp_channel.send(
                    f"🎉 **{mode_name}マッチング成立！**\n"
                    f"🤖 **最初のお喋りお題：**\n> **{selected_odai}**\n\n"
                    f"※このメッセージはピン留めされています。話題を変えたいときは下のボタンをどうぞ！",
                    view=OdaiRerollView(category)
                )
                try: await odai_msg.pin()
                except: pass
            else:
                if user in target_list: target_list.remove(user)
                await interaction.followup.send("⏳ 相性調整のためマッチングを待機しています。そのままお待ちください！", ephemeral=True)
                if user not in target_list: target_list.append(user)

    async def handle_work_entry(self, interaction: discord.Interaction, work_content: str):
        user = interaction.user
        global waiting_work

        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にどこかのボイスチャンネル（VC）に入室してからボタンを押してください！", ephemeral=True)
            return

        if user in waiting_chat: waiting_chat.remove(user)
        if user in waiting_love: waiting_love.remove(user)
        waiting_work =
