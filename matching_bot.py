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
    if not config_channel:
        return
        
    lines = ["===NG_START==="]
    for user_id, ng_list in ng_relations.items():
        if ng_list: 
            lines.append(f"{user_id}>{','.join(map(str, ng_list))}")
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
    if not config_channel:
        return

    async for message in config_channel.history(limit=1):
        if not message.content:
            return
        try:
            lines = message.content.split("\n")
            mode = None
            temp_ng = {}
            temp_chat = []
            temp_love = []
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                if line == "===NG_START===":
                    mode = "NG"
                elif line == "===NG_END===":
                    mode = None
                elif line == "===ODAI_CHAT_START===":
                    mode = "CHAT"
                elif line == "===ODAI_CHAT_END===":
                    mode = None
                elif line == "===ODAI_LOVE_START===":
                    mode = "LOVE"
                elif line == "===ODAI_LOVE_END===":
                    mode = None
                elif line == "===AUDIO_START===":
                    mode = "AUDIO"
                elif line == "===AUDIO_END===":
                    mode = None
                elif line == "===POMODORO_TIME_START===":
                    mode = "POMODORO"
                elif line == "===POMODORO_TIME_END===":
                    mode = None
                elif line == "===MATCH_COUNT_START===":
                    mode = "MATCH_COUNT"
                elif line == "===MATCH_COUNT_END===":
                    mode = None
                else:
                    # 各モードに応じたデータ読み込み
                    if mode == "NG" and ">" in line:
                        uid_str, nlist_str = line.split(">", 1)
                        if nlist_str:
                            temp_ng[int(uid_str)] = [int(x) for x in nlist_str.split(",") if x.strip()]
                    elif mode == "CHAT":
                        temp_chat.append(line)
                    elif mode == "LOVE":
                        temp_love.append(line)
                    elif mode == "AUDIO" and ">" in line:
                        key, url = line.split(">", 1)
                        if key == "WORK_END":
                            AUDIO_WORK_END_URL = url
                        elif key == "BREAK_END":
                            AUDIO_BREAK_END_URL = url
                    elif mode == "POMODORO" and ">" in line:
                        key, val = line.split(">", 1)
                        if key == "WORK_MIN":
                            POMODORO_WORK_MIN = int(val)
                        elif key == "BREAK_MIN":
                            POMODORO_BREAK_MIN = int(val)
                    elif mode == "MATCH_COUNT" and ">" in line:
                        key, val = line.split(">", 1)
                        if key == "CHAT":
                            COUNT_CHAT = int(val)
                        elif key == "LOVE":
                            COUNT_LOVE = int(val)
                        elif key == "WORK":
                            COUNT_WORK = int(val)
                            
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
        waiting_work = [w for w in waiting_work if w["user"].id != user.id]

        waiting_work.append({"user": user, "content": work_content})
        self.update_labels()
        await interaction.response.edit_message(view=self)

        if len(waiting_work) >= COUNT_WORK:
            current_combination = waiting_work[:COUNT_WORK]
            users_to_match = [w["user"] for w in current_combination]
            
            if check_compatibility(users_to_match):
                guild = interaction.guild
                temp_channel = await guild.create_voice_channel(name=f"📝 臨時作業VC-#{guild.id % 1000:03d}", category=user.voice.channel.category)
                created_temp_channels.append(temp_channel.id)

                work_vc_start_times[temp_channel.id] = datetime.now(JST)
                work_vc_contents[temp_channel.id] = [{"name": w["user"].display_name, "content": w["content"]} for w in current_combination]

                for item in current_combination:
                    if item in waiting_work: waiting_work.remove(item)
                    try: await item["user"].move_to(temp_channel)
                    except: pass

                self.update_labels()
                await interaction.message.edit(view=self)

                await temp_channel.send(
                    f"🎉 **作業マッチング成立！**\n"
                    f"🤖 **Botメッセージ：** 解散時に全員の作業時間を自動記録します。\n"
                    f"ポモドーロタイマー（{POMODORO_WORK_MIN}分/{POMODORO_BREAK_MIN}分）を使いたい場合は、下のボタンを押してね！",
                    view=PomodoroView(temp_channel.id, POMODORO_WORK_MIN, POMODORO_BREAK_MIN)
                )
            else:
                waiting_work = [w for w in waiting_work if w["user"].id != user.id]
                await interaction.followup.send("⏳ 相性調整のためマッチングを待機しています。そのままお待ちください！", ephemeral=True)
                waiting_work.append({"user": user, "content": work_content})

    @discord.ui.button(label="☕ 雑談 (0/3人)", style=discord.ButtonStyle.blurple, custom_id="btn_chat")
    async def btn_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_standard_entry(interaction, waiting_chat, COUNT_CHAT, "雑談", "☕", "chat")

    @discord.ui.button(label="💓 恋バナ (0/2人)", style=discord.ButtonStyle.red, custom_id="btn_love")
    async def btn_love(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_standard_entry(interaction, waiting_love, COUNT_LOVE, "恋バナ", "💓", "love")

    @discord.ui.button(label="📝 作業 (0/4人)", style=discord.ButtonStyle.green, custom_id="btn_work")
    async def btn_work(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        global waiting_work
        
        existing = [w for w in waiting_work if w["user"].id == user.id]
        if existing:
            waiting_work = [w for w in waiting_work if w["user"].id != user.id]
            self.update_labels()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("➔ 📝 作業へのエントリーを取り消しました。", ephemeral=True)
            return
            
        await interaction.response.send_modal(WorkModal(self))

class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        self.add_view(MatchingView())
        await self.tree.sync()

bot = MyBot()

@bot.event
async def on_ready():
    print(f"====================================", flush=True)
    print(f"ログイン成功: {bot.user.name} が起動しました！", flush=True)
    await load_all_config()
    
    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel:
        has_panel = False
        async for message in panel_channel.history(limit=10):
            if message.author == bot.user and message.components:
                if any(component.custom_id == "btn_chat" for action_row in message.components for component in action_row.children):
                    v = MatchingView()
                    v.update_labels()
                    await message.edit(embed=create_panel_embed(), view=v)
                    has_panel = True; break
        if not has_panel:
            v = MatchingView()
            v.update_labels()
            await panel_channel.send(embed=create_panel_embed(), view=v)
    print(f"====================================", flush=True)

@bot.event
async def on_voice_state_update(member, before, after):
    global created_temp_channels, work_vc_start_times, work_vc_contents, active_pomodoros
    if before.channel is not None and "臨時" in before.channel.name:
        humans = [m for m in before.channel.members if not m.bot]
        if len(humans) == 0:
            channel_id = before.channel.id
            channel_name = before.channel.name
            try:
                if channel_id in active_pomodoros:
                    task = active_pomodoros.pop(channel_id)
                    task.cancel()

                await before.channel.delete()
                if channel_id in created_temp_channels: created_temp_channels.remove(channel_id)
                
                if channel_id in work_vc_start_times:
                    start_time = work_vc_start_times.pop(channel_id)
                    contents = work_vc_contents.pop(channel_id, [])
                    duration = datetime.now(JST) - start_time
                    hours, remainder = divmod(int(duration.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    time_str = f"{hours}時間 {minutes}分" if hours > 0 else f"{minutes}分"
                    
                    log_channel = bot.get_channel(WORK_LOG_CHANNEL_ID)
                    if log_channel:
                        detail_text = ""
                        for c in contents:
                            detail_text += f"・**{c['name']}** さん ： *{c['content']}*\n"

                        embed = discord.Embed(
                            title="📝 本日の作業レポート",
                            description=f"**{channel_name}** が解散しました！\n総作業時間: **{time_str}**\n\n**🎯 それぞれの作業内容：**\n{detail_text}\nみんなでお互いお疲れ様！👏",
                            color=discord.Color.green()
                        )
                        await log_channel.send(embed=embed)
            except Exception as e:
                print(f"チャンネル削除エラー: {e}", flush=True)

@bot.tree.command(name="set_match_count", description="【管理者用】各募集のマッチング必要人数を変更します")
@app_commands.describe(category="変更する項目", count="必要人数 (1〜10人)")
@app_commands.choices(category=[
    app_commands.Choice(name="☕ 雑談", value="chat"),
    app_commands.Choice(name="💓 恋バナ", value="love"),
    app_commands.Choice(name="📝 作業", value="work")
])
@app_commands.checks.has_permissions(administrator=True)
async def set_match_count_command(interaction: discord.Interaction, category: str, count: int):
    global COUNT_CHAT, COUNT_LOVE, COUNT_WORK
    if count < 1 or count > 10:
        await interaction.response.send_message("❌ 人数は1人〜10人の間で指定してください。", ephemeral=True)
        return
        
    await interaction.response.defer()
    
    if category == "chat": COUNT_CHAT = count
    elif category == "love": COUNT_LOVE = count
    elif category == "work": COUNT_WORK = count
    
    await save_all_config()
    
    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel:
        async for message in panel_channel.history(limit=10):
            if message.author == bot.user and message.components:
                if any(component.custom_id == "btn_chat" for action_row in message.components for component in action_row.children):
                    v = MatchingView()
                    v.update_labels()
                    await message.edit(embed=create_panel_embed(), view=v)
                    break

    labels = {"chat": "雑談", "love": "恋バナ", "work": "作業"}
    await interaction.followup.send(f"✅ **{labels[category]}** のマッチング必要人数を **{count}人** に変更し、パネルを更新しました！")

@bot.tree.command(name="set_pomo_time", description="【管理者用】ポモドーロタイマーの時間を設定します")
@app_commands.describe(work_minutes="集中時間（分）", break_minutes="休憩時間（分）")
@app_commands.checks.has_permissions(administrator=True)
async def set_pomo_time_command(interaction: discord.Interaction, work_minutes: int, break_minutes: int):
    global POMODORO_WORK_MIN, POMODORO_BREAK_MIN
    if work_minutes <= 0 or break_minutes <= 0:
        await interaction.response.send_message("❌ 時間は1分以上で指定してください。", ephemeral=True)
        return
    POMODORO_WORK_MIN = work_minutes
    POMODORO_BREAK_MIN = break_minutes
    await save_all_config() 
    await interaction.response.send_message(
        f"✅ **ポモドーロタイマーの時間を設定しました！**\n⏱️ 集中: {work_minutes}分 / 休憩: {break_minutes}分"
    )

@bot.tree.command(name="set_pomo_audio", description="【管理者用】ポモドーロタイマーの通知音を設定します")
@app_commands.describe(timing="タイミング", file="音声ファイル")
@app_commands.choices(timing=[
    app_commands.Choice(name="🔔 作業終了時", value="work_end"),
    app_commands.Choice(name="⚔️ 休憩終了時", value="break_end")
])
@app_commands.checks.has_permissions(administrator=True)
async def set_pomo_audio_command(interaction: discord.Interaction, timing: str, file: discord.Attachment):
    global AUDIO_WORK_END_URL, AUDIO_BREAK_END_URL
    await interaction.response.defer()
    db_channel = bot.get_channel(AUDIO_DB_CHANNEL_ID)
    if not db_channel:
        await interaction.followup.send("❌ 音声保存用チャンネルが見つかりません。")
        return
    try:
        file_bytes = await file.read()
        bytes_io = io.BytesIO(file_bytes)
        discord_file = discord.File(fp=bytes_io, filename=file.filename)
        db_message = await db_channel.send(content=f"🎵 通知音: {timing}", file=discord_file)
        saved_url = db_message.attachments[0].url
        if timing == "work_end":
            AUDIO_WORK_END_URL = saved_url
        else:
            AUDIO_BREAK_END_URL = saved_url
        await save_all_config() 
        await interaction.followup.send(f"✅ 通知音を登録しました！")
    except Exception as e:
        await interaction.followup.send(f"❌ エラーが発生しました: {e}")

@bot.tree.command(name="setup_matching", description="【管理者用】マッチング受付パネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def setup_matching_command(interaction: discord.Interaction):
    v = MatchingView()
    v.update_labels()
    await interaction.response.send_message(embed=create_panel_embed(), view=v)

@bot.tree.command(name="matching_guard", description="指定したユーザーとマッチングしないようにブロック・解除します")
@app_commands.describe(target_member="ブロック（または解除）するメンバー")
async def matching_guard_command(interaction: discord.Interaction, target_member: discord.Member):
    global ng_relations
    user = interaction.user
    if target_member.id == user.id:
        await interaction.response.send_message("❌ 自分自身をブロックすることはできません。", ephemeral=True)
        return
    
    if user.id not in ng_relations: 
        ng_relations[user.id] = []
        
    if target_member.id not in ng_relations[user.id]:
        ng_relations[user.id].append(target_member.id)
        await save_all_config()
        await interaction.response.send_message(f"🔒 **{target_member.display_name}** さんに対するマッチングガードを設定しました（今後は同じ部屋になりません）。", ephemeral=True)
    else:
        ng_relations[user.id].remove(target_member.id)
        await save_all_config()
        await interaction.response.send_message(f"🔓 **{target_member.display_name}** さんへのマッチングガードを解除しました。", ephemeral=True)

@bot.tree.command(name="add_odai", description="マッチング時の『お題』を追加します")
@app_commands.describe(category="どのお題に追加しますか？", text="お題の文章")
@app_commands.choices(category=[
    app_commands.Choice(name="☕ 雑談", value="chat"),
    app_commands.Choice(name="💓 恋バナ", value="love")
])
async def add_odai_command(interaction: discord.Interaction, category: str, text: str):
    global ODAI_CHAT, ODAI_LOVE
    formatted_odai = f"「{text}」"
    if category == "chat":
        formatted_odai = "☕" + formatted_odai
        ODAI_CHAT.append(formatted_odai)
    else:
        formatted_odai = "💓" + formatted_odai
        ODAI_LOVE.append(formatted_odai)
    await save_all_config()
    await interaction.response.send_message(f"✅ 新しいお題を追加しました！\n> **{formatted_odai}**")

server_thread = threading.Thread(target=run_server)
server_thread.daemon = True
server_thread.start()

if TOKEN: bot.run(TOKEN)
