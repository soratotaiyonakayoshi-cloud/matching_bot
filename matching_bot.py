import discord
from discord import app_commands
import random
import asyncio
import os
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

# --- 🎵 音声再生用の環境セットアップ ---
try:
    import static_ffmpeg
    static_ffmpeg.add_paths() # Render上でFFmpegを自動で使えるようにする神ライブラリ
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
AUDIO_DB_CHANNEL_ID = int(os.getenv("AUDIO_DB_CHANNEL_ID", "0")) # ★新：音声保管用チャンネル
JST = timezone(timedelta(hours=9))

# マッチング必要人数
COUNT_CHAT = 3  
COUNT_LOVE = 3  
COUNT_WORK = 4  

# 待機リスト
waiting_chat = []
waiting_love = []
waiting_work = [] 

created_temp_channels = []    
work_vc_start_times = {}  
work_vc_contents = {}     
active_pomodoros = {}     

# 👤 NGリスト
ng_relations = {}

# 🎵 音声URL設定（デフォルトは空。コマンドで登録されます）
AUDIO_WORK_END_URL = ""   # 作業終了（休憩開始）の音
AUDIO_BREAK_END_URL = ""  # 休憩終了（作業再開）の音

# 🗣️ お題リスト
ODAI_CHAT = [
    "🏫「農工大の周辺で、一番おすすめのご飯屋さんは？」",
    "📚「今期履修している中で、一番面白い（またはヤバい）講義は？」",
    "☕「最近のマイブームや、新しく始めた趣味について！」"
]

ODAI_LOVE = [
    "💓「初恋って何歳のときだった？」",
    "💓「理想の休日のデートコースを妄想で語って！」",
    "💓「恋人に求める条件、どうしても譲れないものは？」"
]

# --- データベース保存・復元（音声URLに対応） ---
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
        
        # ★新：音声URLの保存
        lines.append("===AUDIO_START===")
        lines.append(f"WORK_END>{AUDIO_WORK_END_URL}")
        lines.append(f"BREAK_END>{AUDIO_BREAK_END_URL}")
        lines.append("===AUDIO_END===")
        
        await config_channel.send("\n".join(lines))

async def load_all_config():
    global ng_relations, ODAI_CHAT, ODAI_LOVE, AUDIO_WORK_END_URL, AUDIO_BREAK_END_URL
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
                        
                    if mode == "NG" and ">" in line:
                        uid, nlist = line.split(">")
                        temp_ng[int(uid)] = list(map(int, nlist.split(",")))
                    elif mode == "CHAT" and line.strip(): temp_chat.append(line)
                    elif mode == "LOVE" and line.strip(): temp_love.append(line)
                    elif mode == "AUDIO" and ">" in line:
                        key, url = line.split(">")
                        if key == "WORK_END": AUDIO_WORK_END_URL = url
                        elif key == "BREAK_END": AUDIO_BREAK_END_URL = url
                        
                if temp_ng: ng_relations = temp_ng
                if temp_chat: ODAI_CHAT = temp_chat
                if temp_love: ODAI_LOVE = temp_love
                print("★過去の設定（NG・お題・音声URL）を復元しました。", flush=True)
            except Exception as e:
                print(f"設定復元エラー: {e}", flush=True)

def check_compatibility(potential_group):
    for user_a in potential_group:
        for user_b in potential_group:
            if user_a == user_b: continue
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
            "※ `/matching_guard` で苦手な人をこっそりブロック可能です。"
        ),
        color=discord.Color.blurple()
    )

# --- 🎲 臨時VC内での話題再ガチャView ---
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

# --- 🎵 新機能：VC音声再生システム ---
async def play_notification_audio(vc_channel, audio_url):
    if not audio_url:
        return # 音声URLが登録されていなければ何もしない
    try:
        # VCに接続
        vc = await vc_channel.connect()
        
        # ネットワーク切断対策を入れたFFmpeg再生設定
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }
        vc.play(discord.FFmpegPCMAudio(audio_url, **ffmpeg_options))
        
        # 音声が鳴り終わるまで待つ
        while vc.is_playing():
            await asyncio.sleep(1)
            
        # 鳴り終わったら切断
        await vc.disconnect()
    except Exception as e:
        print(f"🔊 音声再生エラー: {e}", flush=True)
        # エラーが起きたら確実に切断するセーフティ
        try:
            for v in bot.voice_clients:
                if v.channel.id == vc_channel.id: await v.disconnect()
        except: pass

# --- ⏱️ ポモドーロタイマーのロジック（音声対応版） ---
async def pomodoro_loop(channel):
    try:
        while True:
            # 25分作業 (テスト時はここを 10 などに変えると10秒で実験できます)
            await asyncio.sleep(25 * 60)
            await channel.send("🔔 **【ポモドーロ】25分が経ちました！5分間の【休憩】に入ってください！** @here")
            asyncio.create_task(play_notification_audio(channel, AUDIO_WORK_END_URL)) # ★作業終了の音
            
            # 5分休憩
            await asyncio.sleep(5 * 60)
            await channel.send("⚔️ **【ポモドーロ】5分が経ちました！【作業再開】です。集中していきましょう！** @here")
            asyncio.create_task(play_notification_audio(channel, AUDIO_BREAK_END_URL)) # ★休憩終了の音
    except asyncio.CancelledError:
        pass

class PomodoroView(discord.ui.View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    @discord.ui.button(label="⏱️ ポモドーロ開始 (25分/5分)", style=discord.ButtonStyle.green, custom_id="btn_pomo_start")
    async def start_pomo(self, interaction: discord.Interaction, button: discord.ui.Button):
        global active_pomodoros
        channel = interaction.channel
        if self.channel_id in active_pomodoros:
            await interaction.response.send_message("⚠️ 既にこの部屋でタイマーが作動中です！", ephemeral=True)
            return
        await interaction.response.send_message("⏱️ **ポモドーロタイマーを開始しました！**\n（25分作業 ➔ 5分休憩 のサイクルを繰り返します）")
        task = asyncio.create_task(pomodoro_loop(channel))
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

# --- 📝 作業内容を入力してもらうポップアップ ---
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

# --- 🙋‍♂️ メインの募集パネルUI ---
class MatchingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    def update_labels(self):
        self.btn_chat.label = f"☕ 雑談 ({len(waiting_chat)}/{COUNT_CHAT}人)"
        self.btn_love.label = f"💓 恋バナ ({len(waiting_love)}/{COUNT_LOVE}人)"
        self.btn_work.label = f"📝 作業 ({len(waiting_work)}/{COUNT_WORK}人)"

    async def handle_standard_entry(self, interaction: discord.Interaction, target_list: list, target_count: int, mode_name: str, emoji: str, category: str):
        user = interaction.user
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にどこかのボイスチャンネル（VC）に入室してからボタンを押してください！", ephemeral=True)
            return

        if user in waiting_chat: waiting_chat.remove(user)
        if user in waiting_love: waiting_love.remove(user)
        global waiting_work
        waiting_work = [w for w in waiting_work if w["user"] != user]

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
                    target_list.remove(member)
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
                target_list.remove(user)
                await interaction.followup.send("⏳ マッチングを調整中です。そのままお待ちください！", ephemeral=True)
                target_list.append(user)

    async def handle_work_entry(self, interaction: discord.Interaction, work_content: str):
        user = interaction.user
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にどこかのボイスチャンネル（VC）に入室してからボタンを押してください！", ephemeral=True)
            return

        if user in waiting_chat: waiting_chat.remove(user)
        if user in waiting_love: waiting_love.remove(user)
        global waiting_work
        waiting_work = [w for w in waiting_work if w["user"] != user]

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
                    waiting_work.remove(item)
                    try: await item["user"].move_to(temp_channel)
                    except: pass

                self.update_labels()
                await interaction.message.edit(view=self)

                await temp_channel.send(
                    f"🎉 **作業マッチング成立！**\n"
                    f"🤖 **Botメッセージ：** 解散時に全員の作業時間を自動記録します。\n"
                    f"ポモドーロタイマー（25分/5分）を使いたい場合は、下のボタンを押してね！",
                    view=PomodoroView(temp_channel.id)
                )
            else:
                waiting_work = [w for w in waiting_work if w["user"] != user]
                await interaction.followup.send("⏳ マッチングを調整中です。そのままお待ちください！", ephemeral=True)
                waiting_work.append({"user": user, "content": work_content})

    @discord.ui.button(label=f"☕ 雑談 (0/{COUNT_CHAT}人)", style=discord.ButtonStyle.blurple, custom_id="btn_chat")
    async def btn_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_standard_entry(interaction, waiting_chat, COUNT_CHAT, "雑談", "☕", "chat")

    @discord.ui.button(label=f"💓 恋バナ (0/{COUNT_LOVE}人)", style=discord.ButtonStyle.red, custom_id="btn_love")
    async def btn_love(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_standard_entry(interaction, waiting_love, COUNT_LOVE, "恋バナ", "💓", "love")

    @discord.ui.button(label=f"📝 作業 (0/{COUNT_WORK}人)", style=discord.ButtonStyle.green, custom_id="btn_work")
    async def btn_work(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WorkModal(self))

# --- Bot本体 ---
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
                    has_panel = True; break
        if not has_panel:
            await panel_channel.send(embed=create_panel_embed(), view=MatchingView())
    print(f"====================================", flush=True)

# 臨時VCの自動削除＆作業時間の計算
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

# --- 🛠️ 新機能：音声をセットする管理者用スラッシュコマンド ---
@bot.tree.command(name="set_pomo_audio", description="【管理者用】ポモドーロタイマーの通知音を設定・上書きします")
@app_commands.describe(timing="どのタイミングの音を設定しますか？", file="音声ファイル（mp3など）をドロップしてね")
@app_commands.choices(timing=[
    app_commands.Choice(name="🔔 作業終了時（休憩開始の合図）", value="work_end"),
    app_commands.Choice(name="⚔️ 休憩終了時（作業再開の合図）", value="break_end")
])
@app_commands.checks.has_permissions(administrator=True)
async def set_pomo_audio_command(interaction: discord.Interaction, timing: str, file: discord.Attachment):
    global AUDIO_WORK_END_URL, AUDIO_BREAK_END_URL
    
    # 1. まず処理中であることをDiscordに伝える
    await interaction.response.defer()
    
    # 2. 音声保管用のチャンネルを取得
    db_channel = bot.get_channel(AUDIO_DB_CHANNEL_ID)
    if not db_channel:
        await interaction.followup.send("❌ 環境変数 `AUDIO_DB_CHANNEL_ID` のチャンネルが見つかりません。設定を確認してください。")
        return
        
    try:
        # 3. ユーザーがコマンドに添付したファイルを、Botが音声保管チャンネルに「転送（アップロード）」する
        file_bytes = await file.read()
        discord_file = discord.File(fp=bytes_io := __import__('io').BytesIO(file_bytes), filename=file.filename)
        
        db_message = await db_channel.send(
            content=f"🎵 設定された通知音: **{timing}**\n登録日時: {datetime.now(JST).strftime('%Y/%m/%d %H:%M')}",
            file=discord_file
        )
        
        # 4. 転送したメッセージの「添付ファイルURL」を抽出
        saved_url = db_message.attachments[0].url
        
        # 5. 変数に格納して保存
        if timing == "work_end":
            AUDIO_WORK_END_URL = saved_url
            label = "作業終了（休憩開始）"
        else:
            AUDIO_BREAK_END_URL = saved_url
            label = "休憩終了（作業再開）"
            
        await save_all_config() # データベース(CONFIG_CHANNEL)にURLを書き込み
        await interaction.followup.send(f"✅ **{label}** の通知音を新しく登録・保存しました！\n🔗 音声URL: {saved_url}")
        
    except Exception as e:
        await interaction.followup.send(f"❌ 音声ファイルの登録中にエラーが発生しました: {e}")

# 管理者用手動コマンド
@bot.tree.command(name="setup_matching", description="【管理者用】手動でマッチング受付パネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def setup_matching_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=create_panel_embed(), view=MatchingView())

# 秘密のNG登録
@bot.tree.command(name="matching_guard", description="指定したユーザーとマッチングしないように秘密裏にブロックします")
@app_commands.describe(target_member="マッチングを避けたいメンバーを選択")
async def matching_guard_command(interaction: discord.Interaction, target_member: discord.Member):
    global ng_relations
    user = interaction.user
    if target_member.id == user.id:
        await interaction.response.send_message("❌ 自分自身をブロックすることはできません。", ephemeral=True)
        return
    if user.id not in ng_relations: ng_relations[user.id] = []
    if target_member.id not in ng_relations[user.id]:
        ng_relations[user.id].append(target_member.id)
        await save_all_config()
        await interaction.response.send_message(f"🔒 ガードを設定しました。", ephemeral=True)
    else:
        ng_relations[user.id].remove(target_member.id)
        await save_all_config()
        await interaction.response.send_message(f"🔓 ガードを解除しました。", ephemeral=True)

# お題の追加
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
