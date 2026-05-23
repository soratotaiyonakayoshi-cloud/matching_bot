import discord
from discord import app_commands
import random
import asyncio
import os
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

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
WORK_LOG_CHANNEL_ID = int(os.getenv("WORK_LOG_CHANNEL_ID", "0")) # 作業記録用
JST = timezone(timedelta(hours=9))

MATCH_TARGET_COUNT = 3  # マッチングする人数

# --- 👥 目的別のマッチング必要人数（お好みで数字を変えてください！） ---
COUNT_CHAT = 3  # 雑談は3人集まったら発動
COUNT_LOVE = 2  # 恋バナは2人集まったら発動（サシで話しやすくする）
COUNT_WORK = 4  # 作業通話は4人集まったら発動（大人数でピアプレッシャー）

# 目的別の待機リスト
waiting_chat = []
waiting_love = []
waiting_work = []

created_temp_channels = []    
work_vc_start_times = {} # 作業VCの開始時間を記録 {チャンネルID: datetime}

# 👤 NGリスト
ng_relations = {}

# （中略：お題リストやデータベース機能はそのまま残す）

# --- 🙋‍♂️ 3つの目的が選べるボタンUI ---
class MatchingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def handle_entry(self, interaction: discord.Interaction, target_list: list, target_count: int, mode_name: str, emoji: str, odai_list: list):
        user = interaction.user

        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にどこかのボイスチャンネル（VC）に入室してからボタンを押してください！", ephemeral=True)
            return

        # 別の待機リストにいたら外す
        for w_list in [waiting_chat, waiting_love, waiting_work]:
            if user in w_list: w_list.remove(user)

        target_list.append(user)
        
        # UIの人数表示を更新
        self.btn_chat.label = f"☕ 雑談 ({len(waiting_chat)}/{COUNT_CHAT}人)"
        self.btn_love.label = f"💓 恋バナ ({len(waiting_love)}/{COUNT_LOVE}人)"
        self.btn_work.label = f"📝 作業 ({len(waiting_work)}/{COUNT_WORK}人)"
        await interaction.response.edit_message(view=self)

        # ★目的ごとの設定人数（target_count）に達したかチェック
        if len(target_list) >= target_count:
            current_combination = target_list[:target_count]
            
            if check_compatibility(current_combination):
                guild = interaction.guild
                category = user.voice.channel.category
                
                # VC作成
                temp_channel = await guild.create_voice_channel(
                    name=f"{emoji} 臨時{mode_name}VC-#{guild.id % 1000:03d}",
                    category=category
                )
                created_temp_channels.append(temp_channel.id)

                if mode_name == "作業":
                    work_vc_start_times[temp_channel.id] = datetime.now(JST)

                mentions = []
                for member in current_combination:
                    mentions.append(member.mention)
                    target_list.remove(member)
                    try:
                        if member.voice and member.voice.channel:
                            await member.move_to(temp_channel)
                    except Exception:
                        pass

                # パネルの人数表示をリセット
                self.btn_chat.label = f"☕ 雑談 ({len(waiting_chat)}/{COUNT_CHAT}人)"
                self.btn_love.label = f"💓 恋バナ ({len(waiting_love)}/{COUNT_LOVE}人)"
                self.btn_work.label = f"📝 作業 ({len(waiting_work)}/{COUNT_WORK}人)"
                await interaction.message.edit(view=self)

                # メッセージとお題の送信
                msg = f"🎉 **{mode_name}マッチング成立！**\n{', '.join(mentions)}\n{temp_channel.mention} に集合しました！"
                if odai_list:
                    selected_odai = random.choice(odai_list)
                    msg += f"\n\n🤖 **Botからのお題ガチャ：**\n> **{selected_odai}**"
                elif mode_name == "作業":
                    msg += f"\n\n🤖 **Botからのメッセージ：**\n> 集中して頑張りましょう！解散時に作業時間を記録します✍️"

                await interaction.followup.send(msg)
            else:
                target_list.remove(user)
                await interaction.followup.send("⏳ 現在、他のメンバーとのマッチングを調整中です。少しお待ちください！", ephemeral=True)
                target_list.append(user)

    @discord.ui.button(label=f"☕ 雑談 (0/{COUNT_CHAT}人)", style=discord.ButtonStyle.blurple, custom_id="btn_chat")
    async def btn_chat(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_entry(interaction, waiting_chat, COUNT_CHAT, "雑談", "☕", ODAI_CHAT)

    @discord.ui.button(label=f"💓 恋バナ (0/{COUNT_LOVE}人)", style=discord.ButtonStyle.red, custom_id="btn_love")
    async def btn_love(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_entry(interaction, waiting_love, COUNT_LOVE, "恋バナ", "💓", ODAI_LOVE)

    @discord.ui.button(label=f"📝 作業 (0/{COUNT_WORK}人)", style=discord.ButtonStyle.green, custom_id="btn_work")
    async def btn_work(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_entry(interaction, waiting_work, COUNT_WORK, "作業", "📝", [])

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
    print(f"ログイン成功: {bot.user.name} が起動しました！", flush=True)
    await load_all_config()

# 臨時VCの自動削除＆作業時間の計算
@bot.event
async def on_voice_state_update(member, before, after):
    global created_temp_channels, work_vc_start_times
    if before.channel is not None and before.channel.id in created_temp_channels:
        humans = [m for m in before.channel.members if not m.bot]
        if len(humans) == 0:
            channel_id = before.channel.id
            channel_name = before.channel.name
            try:
                await before.channel.delete()
                created_temp_channels.remove(channel_id)
                
                # 作業VCだった場合、時間を計算して報告
                if channel_id in work_vc_start_times:
                    start_time = work_vc_start_times.pop(channel_id)
                    duration = datetime.now(JST) - start_time
                    hours, remainder = divmod(int(duration.total_seconds()), 3600)
                    minutes, _ = divmod(remainder, 60)
                    
                    time_str = f"{hours}時間 {minutes}分" if hours > 0 else f"{minutes}分"
                    
                    log_channel = bot.get_channel(WORK_LOG_CHANNEL_ID)
                    if log_channel:
                        embed = discord.Embed(
                            title="📝 作業記録",
                            description=f"**{channel_name}** が解散しました！\n今回の作業時間: **{time_str}**\nお疲れ様でした👏",
                            color=discord.Color.green()
                        )
                        await log_channel.send(embed=embed)
                        
            except Exception as e:
                print(f"チャンネル削除エラー: {e}", flush=True)

# 【コマンド】パネル設置（管理者用）
@bot.tree.command(name="setup_matching", description="【管理者用】ランダム通話マッチングの受付パネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def setup_matching_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🚪 キャンパス ラウンジガチャ",
        description=(
            "今の気分に合わせてボタンを押してね！\n"
            f"**{MATCH_TARGET_COUNT} 人** 集まった瞬間、専用のVCに自動で引きずり込まれます。\n\n"
            "☕ **雑談**：気軽な話題でワイワイ\n"
            "💓 **恋バナ**：専用の甘酸っぱいお題が出ます\n"
            "📝 **作業**：集中モード！解散時に作業時間が記録されます\n\n"
            "※ `/matching_guard` で苦手な人をこっそりブロック可能です。"
        ),
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=MatchingView())

# 【コマンド】秘密のNG登録
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

# 【コマンド】お題の追加
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
