import discord
from discord import app_commands
import random
import asyncio
import os
import aiohttp
from flask import Flask
import threading
from datetime import datetime, timezone, timedelta

# --- 🎵 音声再生用の環境セットアップ ---
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception as e:
    print(f"FFmpegセットアップ警告: {e}", flush=True)

# --- Webサーバー（Render keepalive）---
app = Flask('')
@app.route('/')
def home():
    return "キャンパスアシストBotは正常稼働中です！"
def run_server():
    app.run(host='0.0.0.0', port=8080, debug=False, use_reloader=False)

# --- 環境変数 ---
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
WORK_LOG_CHANNEL_ID = int(os.getenv("WORK_LOG_CHANNEL_ID", "0"))
PANEL_CHANNEL_ID = int(os.getenv("PANEL_CHANNEL_ID", "0"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://minadeankinew.nibiroiro.workers.dev")
JST = timezone(timedelta(hours=9))

# ============================================================
#  Supabase REST ヘルパー（設定の永続化・作業時間の記録）
# ============================================================
def _supa_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }

async def supa_select(table, query):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        return []
    url = f"{SUPABASE_URL}/rest/v1/{table}?{query}"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=_supa_headers()) as r:
                if r.status >= 300:
                    print(f"Supabase select エラー {r.status}: {await r.text()}", flush=True)
                    return []
                return await r.json()
    except Exception as e:
        print(f"Supabase select 失敗: {e}", flush=True)
        return []

async def supa_upsert(table, rows, on_conflict=None, merge=False):
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY or not rows:
        return
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    if on_conflict:
        url += f"?on_conflict={on_conflict}"
    headers = dict(_supa_headers())
    headers["Prefer"] = ("resolution=merge-duplicates," if merge else "") + "return=minimal"
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(url, headers=headers, json=rows) as r:
                if r.status >= 300:
                    print(f"Supabase upsert エラー {r.status}: {await r.text()}", flush=True)
    except Exception as e:
        print(f"Supabase upsert 失敗: {e}", flush=True)

# ============================================================
#  設定（Supabase bot_config に jsonb で保持）
# ============================================================
GAME_PANEL_CHANNEL_ID = 0
COUNT_CHAT, COUNT_LOVE, COUNT_WORK, COUNT_GAME = 3, 2, 4, 2
waiting_chat, waiting_love, waiting_work = [], [], []
waiting_games = {}
ng_relations = {}

AUDIO_WORK_END_URL = ""
AUDIO_BREAK_END_URL = ""
POMODORO_WORK_MIN, POMODORO_BREAK_MIN = 25, 5

game_list = ["GeoGuessr", "Gartic Phone", "お絵描きチャット", "Splatoon"]
ODAI_CHAT = [
    "🏫「農工大の周辺で、ぶっちゃけ一番おすすめのご飯屋さんは？」",
    "📚「今期履修している中で、一番面白い（またはヤバい）講義は？」",
    "☕「最近のマイブームや、新しく始めた趣味について！」",
]
ODAI_LOVE = [
    "💓「ぶっちゃけ、初恋って何歳のときだった？」",
    "💓「理想の休日のデートコースを妄想で語って！」",
    "💓「恋人に求める条件、どうしても譲れないものは？」",
]

# --- 作業時間トラッキング（各自の入退室を秒単位で集計）---
work_channel_ids = set()          # 作業用の臨時VCの id
work_content = {}                 # ch_id -> {user_id: 作業内容}
work_times = {}                   # ch_id -> {user_id: {"join": dt|None, "accrued": float, "name": str}}
work_room_start = {}             # ch_id -> 部屋作成時刻
created_temp_channels = []
active_pomodoros = {}

async def save_all_config():
    cfg = {
        "game_panel_channel_id": GAME_PANEL_CHANNEL_ID,
        "ng": {str(k): v for k, v in ng_relations.items() if v},
        "games": game_list,
        "odai_chat": ODAI_CHAT,
        "odai_love": ODAI_LOVE,
        "audio_work_end": AUDIO_WORK_END_URL,
        "audio_break_end": AUDIO_BREAK_END_URL,
        "pomo_work": POMODORO_WORK_MIN,
        "pomo_break": POMODORO_BREAK_MIN,
        "count_chat": COUNT_CHAT,
        "count_love": COUNT_LOVE,
        "count_work": COUNT_WORK,
        "count_game": COUNT_GAME,
    }
    await supa_upsert("bot_config", [{"id": 1, "data": cfg}], on_conflict="id", merge=True)

async def load_all_config():
    global GAME_PANEL_CHANNEL_ID, ng_relations, game_list, ODAI_CHAT, ODAI_LOVE
    global AUDIO_WORK_END_URL, AUDIO_BREAK_END_URL, POMODORO_WORK_MIN, POMODORO_BREAK_MIN
    global COUNT_CHAT, COUNT_LOVE, COUNT_WORK, COUNT_GAME
    rows = await supa_select("bot_config", "id=eq.1&select=data")
    if not rows:
        return
    cfg = rows[0].get("data") or {}
    GAME_PANEL_CHANNEL_ID = cfg.get("game_panel_channel_id", GAME_PANEL_CHANNEL_ID)
    ng_relations = {int(k): [int(x) for x in v] for k, v in (cfg.get("ng") or {}).items()}
    game_list = cfg.get("games") or game_list
    ODAI_CHAT = cfg.get("odai_chat") or ODAI_CHAT
    ODAI_LOVE = cfg.get("odai_love") or ODAI_LOVE
    AUDIO_WORK_END_URL = cfg.get("audio_work_end", "")
    AUDIO_BREAK_END_URL = cfg.get("audio_break_end", "")
    POMODORO_WORK_MIN = cfg.get("pomo_work", POMODORO_WORK_MIN)
    POMODORO_BREAK_MIN = cfg.get("pomo_break", POMODORO_BREAK_MIN)
    COUNT_CHAT = cfg.get("count_chat", COUNT_CHAT)
    COUNT_LOVE = cfg.get("count_love", COUNT_LOVE)
    COUNT_WORK = cfg.get("count_work", COUNT_WORK)
    COUNT_GAME = cfg.get("count_game", COUNT_GAME)
    print("★設定をSupabaseから復元しました", flush=True)

# ============================================================
#  共通ユーティリティ
# ============================================================
def check_compatibility(group):
    for a in group:
        for b in group:
            if a.id == b.id:
                continue
            if b.id in ng_relations.get(a.id, []) or a.id in ng_relations.get(b.id, []):
                return False
    return True

def create_panel_embed():
    return discord.Embed(
        title="🚪 キャンパス ラウンジガチャ",
        description=(
            "今の気分に合わせてボタンを押してね！\n"
            "人数が集まると、**専用VC**に自動で集合します。\n\n"
            f"☕ **雑談**（{COUNT_CHAT}人〜）\n"
            f"💓 **恋バナ**（{COUNT_LOVE}人〜）\n"
            f"📝 **作業**（{COUNT_WORK}人〜）：解散時に各自の作業時間が記録され、通信簿に反映されます✍️\n\n"
            "💡 稼働中の部屋には「🚪稼働中の部屋に合流」から途中参加できます。\n"
            "※ `/matching_guard` で苦手な人をこっそりブロック可能。"
        ),
        color=discord.Color.blurple(),
    )

def create_game_panel_embed():
    return discord.Embed(
        title="🎮 ゲーム待合パネル",
        description=(
            "遊びたいゲームのボタンを押してね！\n"
            f"**{COUNT_GAME}人** 揃うと専用VCが自動作成されます。\n"
            "管理者は `/add_game` で新しいゲームを追加できます！"
        ),
        color=discord.Color.brand_green(),
    )

# --- 作業時間トラッキング補助 ---
def _work_entry(ch_id, member):
    d = work_times.setdefault(ch_id, {})
    e = d.get(member.id)
    if e is None:
        e = {"join": None, "accrued": 0.0, "name": member.display_name}
        d[member.id] = e
    return e

def register_work_channel(ch_id, matched):
    work_channel_ids.add(ch_id)
    work_content[ch_id] = {w["user"].id: w["content"] for w in matched}
    work_room_start[ch_id] = datetime.now(JST)

# ============================================================
#  ゲーム用 UI
# ============================================================
class GameCodeModal(discord.ui.Modal):
    def __init__(self, target_message):
        super().__init__(title="部屋情報を共有", timeout=None)
        self.target_message = target_message
        self.code_input = discord.ui.TextInput(label="部屋コードやURLを入力", style=discord.TextStyle.short,
                                               placeholder="例: ABCD-EFGH または https://...", required=True)
        self.add_item(self.code_input)
    async def on_submit(self, interaction: discord.Interaction):
        base = self.target_message.content.split("\n\n🎫")[0]
        await self.target_message.edit(content=f"{base}\n\n🎫 **現在の部屋情報:**\n`{self.code_input.value}`\n*(更新者: {interaction.user.display_name})*")
        await interaction.response.send_message("✅ 部屋情報を更新しました！", ephemeral=True)

class GameRoomCodeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="🎫 部屋コード/URLを設定", style=discord.ButtonStyle.success, custom_id="btn_set_game_code")
    async def set_code(self, interaction, button):
        await interaction.response.send_modal(GameCodeModal(interaction.message))

async def update_all_game_panels():
    if GAME_PANEL_CHANNEL_ID == 0:
        return
    channel = bot.get_channel(GAME_PANEL_CHANNEL_ID)
    if not channel:
        return
    async for message in channel.history(limit=10):
        if message.author == bot.user and message.embeds and "ゲーム待合" in (message.embeds[0].title or ""):
            await message.edit(embed=create_game_panel_embed(), view=GameMatchingView())
            break

class GameButton(discord.ui.Button):
    def __init__(self, game):
        count = len(waiting_games.get(game, []))
        super().__init__(label=f"🎮 {game} ({count}/{COUNT_GAME}人)", style=discord.ButtonStyle.blurple, custom_id=f"btn_game_{game}")
        self.game = game
    async def callback(self, interaction):
        user = interaction.user
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にボイスチャンネルに入室してください！", ephemeral=True)
            return
        target = waiting_games.setdefault(self.game, [])
        if user in target:
            target.remove(user)
            await interaction.response.defer()
            await update_all_game_panels()
            await interaction.followup.send(f"➔ {self.game} のエントリーを取り消しました。", ephemeral=True)
            return
        target.append(user)
        await interaction.response.defer()
        await update_all_game_panels()
        if len(target) >= COUNT_GAME:
            combo = target[:COUNT_GAME]
            if check_compatibility(combo):
                guild = interaction.guild
                role = await guild.create_role(name=f"⏳-{self.game}-#{guild.id % 100:02d}", reason="臨時ゲームVC用ロール")
                for m in combo:
                    try: await m.add_roles(role)
                    except: pass
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
                    guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
                    role: discord.PermissionOverwrite(view_channel=True, connect=True),
                }
                ch = await guild.create_voice_channel(name=f"🎮 {self.game}-#{guild.id % 100:02d}", category=user.voice.channel.category, overwrites=overwrites)
                created_temp_channels.append(ch.id)
                for m in combo:
                    if m in target: target.remove(m)
                    try: await m.move_to(ch)
                    except: pass
                await update_all_game_panels()
                msg = await ch.send(f"🎉 **{self.game} のマッチング成立！**\nホストを決めて部屋コード/URLを共有してね！", view=GameRoomCodeView())
                try: await msg.pin()
                except: pass

class GameMatchingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for game in game_list:
            self.add_item(GameButton(game))

class OdaiRerollView(discord.ui.View):
    def __init__(self, category):
        super().__init__(timeout=None)
        self.category = category
    @discord.ui.button(label="🎲 次のお題を引く", style=discord.ButtonStyle.blurple, custom_id="btn_reroll_odai")
    async def reroll(self, interaction, button):
        lst = ODAI_CHAT if self.category == "chat" else ODAI_LOVE
        if not lst:
            await interaction.response.send_message("❌ お題リストが空です！", ephemeral=True)
            return
        await interaction.response.send_message(f"🎲 **新しいお題！**\n> **{random.choice(lst)}**")

async def play_notification_audio(vc_channel, audio_url):
    if not audio_url:
        return
    try:
        vc = await vc_channel.connect()
        opts = {'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', 'options': '-vn'}
        vc.play(discord.FFmpegPCMAudio(audio_url, **opts))
        while vc.is_playing():
            await asyncio.sleep(1)
        await vc.disconnect()
    except Exception as e:
        print(f"🔊 音声再生エラー: {e}", flush=True)
        try:
            for v in bot.voice_clients:
                if v.channel.id == vc_channel.id:
                    await v.disconnect()
        except: pass

async def pomodoro_loop(channel, work_min, break_min):
    try:
        while True:
            await asyncio.sleep(work_min * 60)
            await channel.send(f"🔔 **{work_min}分経過！{break_min}分休憩へ。** @here")
            asyncio.create_task(play_notification_audio(channel, AUDIO_WORK_END_URL))
            await asyncio.sleep(break_min * 60)
            await channel.send(f"⚔️ **{break_min}分経過！作業再開です。** @here")
            asyncio.create_task(play_notification_audio(channel, AUDIO_BREAK_END_URL))
    except asyncio.CancelledError:
        pass

class PomodoroView(discord.ui.View):
    def __init__(self, channel_id, work_min, break_min):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.work_min = work_min
        self.break_min = break_min
        self.start_pomo.label = f"⏱️ ポモドーロ開始 ({work_min}分/{break_min}分)"
    @discord.ui.button(label="⏱️ ポモドーロ開始", style=discord.ButtonStyle.green, custom_id="btn_pomo_start")
    async def start_pomo(self, interaction, button):
        if self.channel_id in active_pomodoros:
            await interaction.response.send_message("⚠️ 既に作動中です！", ephemeral=True)
            return
        await interaction.response.send_message(f"⏱️ ポモドーロ開始！（{self.work_min}分作業 ➔ {self.break_min}分休憩）")
        active_pomodoros[self.channel_id] = asyncio.create_task(pomodoro_loop(interaction.channel, self.work_min, self.break_min))
    @discord.ui.button(label="⏹️ タイマー停止", style=discord.ButtonStyle.red, custom_id="btn_pomo_stop")
    async def stop_pomo(self, interaction, button):
        if self.channel_id in active_pomodoros:
            active_pomodoros.pop(self.channel_id).cancel()
            await interaction.response.send_message("⏹️ タイマーを停止しました。")
        else:
            await interaction.response.send_message("❌ 作動中のタイマーはありません。", ephemeral=True)

class ActiveVCDropdown(discord.ui.Select):
    def __init__(self, active_vcs):
        options = []
        for vc in active_vcs:
            humans = [m for m in vc.members if not m.bot]
            options.append(discord.SelectOption(label=vc.name, description=f"現在 {len(humans)}人 参加中", value=str(vc.id)))
        super().__init__(placeholder="合流する部屋を選択...", options=options[:25])
    async def callback(self, interaction):
        vc = interaction.guild.get_channel(int(self.values[0]))
        user = interaction.user
        if not vc:
            await interaction.response.send_message("❌ その部屋は解散したか見つかりません。", ephemeral=True)
            return
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にボイスチャンネルに入室してください！", ephemeral=True)
            return
        if not check_compatibility([m for m in vc.members if not m.bot] + [user]):
            await interaction.response.send_message("🔒 相性調整の制限により合流できません。", ephemeral=True)
            return
        role = None
        for target in vc.overwrites:
            if isinstance(target, discord.Role) and target.name.startswith("⏳-"):
                role = target
                break
        try:
            if role:
                await user.add_roles(role)
            else:
                ow = vc.overwrites_for(user)
                ow.view_channel = True
                ow.connect = True
                await vc.set_permissions(user, overwrite=ow)
            await user.move_to(vc)
            await interaction.response.send_message(f"✅ **{vc.name}** に合流しました！", ephemeral=True)
            await vc.send(f"👋 **{user.display_name}** さんが合流しました！")
        except Exception as e:
            await interaction.response.send_message(f"❌ 合流中にエラー: {e}", ephemeral=True)

class ActiveVCDropdownView(discord.ui.View):
    def __init__(self, active_vcs):
        super().__init__(timeout=120)
        self.add_item(ActiveVCDropdown(active_vcs))

class WorkModal(discord.ui.Modal, title="📝 今日の作業内容を入力"):
    content = discord.ui.TextInput(label="今から何をする？", placeholder="例：レポート作成", required=True, max_length=50)
    def __init__(self, matching_view):
        super().__init__()
        self.m_view = matching_view
    async def on_submit(self, interaction):
        await self.m_view.handle_work_entry(interaction, self.content.value)

class MatchingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    def update_labels(self):
        self.btn_chat.label = f"☕ 雑談 ({len(waiting_chat)}/{COUNT_CHAT}人)"
        self.btn_love.label = f"💓 恋バナ ({len(waiting_love)}/{COUNT_LOVE}人)"
        self.btn_work.label = f"📝 作業 ({len(waiting_work)}/{COUNT_WORK}人)"

    async def _create_temp_vc(self, guild, user, role_name, ch_name):
        role = await guild.create_role(name=role_name, reason="臨時VC用ロール")
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
            role: discord.PermissionOverwrite(view_channel=True, connect=True),
        }
        ch = await guild.create_voice_channel(name=ch_name, category=user.voice.channel.category, overwrites=overwrites)
        created_temp_channels.append(ch.id)
        return role, ch

    async def handle_standard_entry(self, interaction, target_list, target_count, mode_name, emoji, category):
        user = interaction.user
        if user in target_list:
            target_list.remove(user)
            self.update_labels()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send(f"➔ {emoji} {mode_name}のエントリーを取り消しました。", ephemeral=True)
            return
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にボイスチャンネルに入室してください！", ephemeral=True)
            return
        if user in waiting_chat: waiting_chat.remove(user)
        if user in waiting_love: waiting_love.remove(user)
        global waiting_work
        waiting_work = [w for w in waiting_work if w["user"].id != user.id]
        target_list.append(user)
        self.update_labels()
        await interaction.response.edit_message(view=self)
        if len(target_list) >= target_count:
            combo = target_list[:target_count]
            if check_compatibility(combo):
                guild = interaction.guild
                role, ch = await self._create_temp_vc(guild, user, f"⏳-{mode_name}-#{guild.id % 100:02d}", f"{emoji} 臨時{mode_name}VC-#{guild.id % 100:02d}")
                for m in combo:
                    try: await m.add_roles(role)
                    except: pass
                for m in combo:
                    if m in target_list: target_list.remove(m)
                    try: await m.move_to(ch)
                    except: pass
                self.update_labels()
                await interaction.message.edit(view=self)
                lst = ODAI_CHAT if category == "chat" else ODAI_LOVE
                odai = random.choice(lst) if lst else "自由にお喋りしてください！"
                msg = await ch.send(f"🎉 **{mode_name}マッチング成立！**\n🤖 **最初のお題：**\n> **{odai}**", view=OdaiRerollView(category))
                try: await msg.pin()
                except: pass
            else:
                if user in target_list: target_list.remove(user)
                await interaction.followup.send("⏳ 相性調整のため待機中です。", ephemeral=True)
                if user not in target_list: target_list.append(user)

    async def handle_work_entry(self, interaction, work_content):
        user = interaction.user
        global waiting_work
        if user.voice is None or user.voice.channel is None:
            await interaction.response.send_message("❌ 先にボイスチャンネルに入室してください！", ephemeral=True)
            return
        if user in waiting_chat: waiting_chat.remove(user)
        if user in waiting_love: waiting_love.remove(user)
        waiting_work = [w for w in waiting_work if w["user"].id != user.id]
        waiting_work.append({"user": user, "content": work_content})
        self.update_labels()
        await interaction.response.edit_message(view=self)
        if len(waiting_work) >= COUNT_WORK:
            combo = waiting_work[:COUNT_WORK]
            users = [w["user"] for w in combo]
            if check_compatibility(users):
                guild = interaction.guild
                role, ch = await self._create_temp_vc(guild, user, f"⏳-作業-#{guild.id % 100:02d}", f"📝 臨時作業VC-#{guild.id % 100:02d}")
                for m in users:
                    try: await m.add_roles(role)
                    except: pass
                register_work_channel(ch.id, combo)
                for w in combo:
                    if w in waiting_work: waiting_work.remove(w)
                    try: await w["user"].move_to(ch)
                    except: pass
                self.update_labels()
                await interaction.message.edit(view=self)
                await ch.send("🎉 **作業マッチング成立！**\n🤖 解散時に各自の作業時間を記録し、通信簿に反映します。", view=PomodoroView(ch.id, POMODORO_WORK_MIN, POMODORO_BREAK_MIN))
            else:
                waiting_work = [w for w in waiting_work if w["user"].id != user.id]
                await interaction.followup.send("⏳ 相性調整のため待機中です。", ephemeral=True)
                waiting_work.append({"user": user, "content": work_content})

    @discord.ui.button(label="☕ 雑談", style=discord.ButtonStyle.blurple, custom_id="btn_chat")
    async def btn_chat(self, interaction, button):
        await self.handle_standard_entry(interaction, waiting_chat, COUNT_CHAT, "雑談", "☕", "chat")
    @discord.ui.button(label="💓 恋バナ", style=discord.ButtonStyle.red, custom_id="btn_love")
    async def btn_love(self, interaction, button):
        await self.handle_standard_entry(interaction, waiting_love, COUNT_LOVE, "恋バナ", "💓", "love")
    @discord.ui.button(label="📝 作業", style=discord.ButtonStyle.green, custom_id="btn_work")
    async def btn_work(self, interaction, button):
        user = interaction.user
        global waiting_work
        if [w for w in waiting_work if w["user"].id == user.id]:
            waiting_work = [w for w in waiting_work if w["user"].id != user.id]
            self.update_labels()
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("➔ 📝 作業のエントリーを取り消しました。", ephemeral=True)
            return
        await interaction.response.send_modal(WorkModal(self))
    @discord.ui.button(label="🚪 稼働中の部屋に合流", style=discord.ButtonStyle.secondary, custom_id="btn_join_active", row=1)
    async def btn_join_active(self, interaction, button):
        guild = interaction.guild
        active = [guild.get_channel(cid) for cid in created_temp_channels if guild.get_channel(cid)]
        if not active:
            await interaction.response.send_message("❌ 現在稼働中の臨時VCはありません。", ephemeral=True)
            return
        await interaction.response.send_message("合流したい部屋を選んでください！", view=ActiveVCDropdownView(active), ephemeral=True)

# ============================================================
#  Bot 本体
# ============================================================
class MyBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
    async def setup_hook(self):
        await self.tree.sync()

bot = MyBot()

@bot.event
async def on_ready():
    print("====================================", flush=True)
    print(f"ログイン成功: {bot.user.name}", flush=True)
    await load_all_config()
    bot.add_view(MatchingView())
    bot.add_view(GameMatchingView())
    bot.add_view(GameRoomCodeView())
    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel:
        has_panel = False
        async for message in panel_channel.history(limit=10):
            if message.author == bot.user and message.components and any(
                c.custom_id == "btn_chat" for row in message.components for c in row.children
            ):
                v = MatchingView(); v.update_labels()
                await message.edit(embed=create_panel_embed(), view=v)
                has_panel = True
                break
        if not has_panel:
            v = MatchingView(); v.update_labels()
            await panel_channel.send(embed=create_panel_embed(), view=v)
    await update_all_game_panels()
    print("====================================", flush=True)

async def finalize_work_channel(channel, channel_name):
    """作業VC解散時：各自の作業時間を集計し、レポート投稿＋Supabase記録。"""
    ch_id = channel.id
    if ch_id not in work_channel_ids:
        return
    now = datetime.now(JST)
    times = work_times.pop(ch_id, {})
    contents = work_content.pop(ch_id, {})
    start = work_room_start.pop(ch_id, now)
    work_channel_ids.discard(ch_id)

    rows = []
    detail_lines = []
    for uid, e in times.items():
        if e["join"] is not None:
            e["accrued"] += (now - e["join"]).total_seconds()
            e["join"] = None
        sec = int(e["accrued"])
        if sec < 60:
            continue  # 1分未満は記録しない
        c = contents.get(uid, "作業")
        rows.append({
            "discord_id": str(uid),
            "display_name": e["name"],
            "content": c,
            "duration_sec": sec,
            "started_at": start.isoformat(),
            "ended_at": now.isoformat(),
            "channel_name": channel_name,
            "guild_id": str(channel.guild.id),
        })
        h, rem = divmod(sec, 3600)
        m, _ = divmod(rem, 60)
        tstr = f"{h}時間{m}分" if h else f"{m}分"
        detail_lines.append(f"・**{e['name']}** ： {tstr}（*{c}*）")

    if rows:
        await supa_upsert("work_sessions", rows)

    log_channel = bot.get_channel(WORK_LOG_CHANNEL_ID)
    if log_channel:
        if detail_lines:
            desc = (f"**{channel_name}** が解散しました！\n\n**🎯 各自の作業時間：**\n"
                    + "\n".join(detail_lines)
                    + f"\n\nお互いお疲れさま！👏\n📊 通信簿で成績を確認 → {WEB_APP_URL}")
        else:
            desc = f"**{channel_name}** が解散しました（記録対象の作業時間はありませんでした）。"
        embed = discord.Embed(title="📝 作業レポート", description=desc, color=discord.Color.green())
        await log_channel.send(embed=embed)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        # bot自身の入退室は無視（ただし解散判定は人間で行う）
        pass

    # --- 作業VCの入退室を秒単位でトラッキング ---
    if not member.bot:
        if after.channel and after.channel.id in work_channel_ids:
            _work_entry(after.channel.id, member)["join"] = datetime.now(JST)
        if before.channel and before.channel.id in work_channel_ids and (
            after.channel is None or after.channel.id != before.channel.id
        ):
            e = _work_entry(before.channel.id, member)
            if e["join"] is not None:
                e["accrued"] += (datetime.now(JST) - e["join"]).total_seconds()
                e["join"] = None

    # --- 臨時VCの解散処理 ---
    if before.channel is not None and (
        "臨時" in before.channel.name or "部屋" in before.channel.name or "🎮" in before.channel.name
    ):
        humans = [m for m in before.channel.members if not m.bot]
        if len(humans) == 0:
            ch = before.channel
            ch_id, ch_name = ch.id, ch.name
            try:
                if ch_id in active_pomodoros:
                    active_pomodoros.pop(ch_id).cancel()
                # 作業VCなら先に集計（チャンネル削除前）
                await finalize_work_channel(ch, ch_name)
                # 臨時ロール削除
                for target in ch.overwrites:
                    if isinstance(target, discord.Role) and target.name.startswith("⏳-"):
                        try: await target.delete(reason="臨時VC解散")
                        except Exception as re:
                            print(f"臨時ロール削除失敗: {re}", flush=True)
                await ch.delete()
                if ch_id in created_temp_channels:
                    created_temp_channels.remove(ch_id)
            except Exception as e:
                print(f"チャンネル削除エラー: {e}", flush=True)

# ============================================================
#  スラッシュコマンド
# ============================================================
@bot.tree.command(name="setup_matching", description="【管理者用】通常マッチングパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def setup_matching_command(interaction):
    v = MatchingView(); v.update_labels()
    await interaction.response.send_message(embed=create_panel_embed(), view=v)

@bot.tree.command(name="setup_game_panel", description="【管理者用】ゲーム専用マッチングパネルを設置します")
@app_commands.checks.has_permissions(administrator=True)
async def setup_game_panel_command(interaction):
    global GAME_PANEL_CHANNEL_ID
    GAME_PANEL_CHANNEL_ID = interaction.channel.id
    await save_all_config()
    await interaction.response.send_message(embed=create_game_panel_embed(), view=GameMatchingView())

@bot.tree.command(name="set_match_count", description="【管理者用】各マッチングの最低人数を変更します")
@app_commands.describe(chat="雑談", love="恋バナ", work="作業", game="ゲーム")
@app_commands.checks.has_permissions(administrator=True)
async def set_match_count_command(interaction, chat: int = None, love: int = None, work: int = None, game: int = None):
    global COUNT_CHAT, COUNT_LOVE, COUNT_WORK, COUNT_GAME
    changes = []
    if chat is not None and chat >= 1: COUNT_CHAT = chat; changes.append(f"雑談={chat}")
    if love is not None and love >= 1: COUNT_LOVE = love; changes.append(f"恋バナ={love}")
    if work is not None and work >= 1: COUNT_WORK = work; changes.append(f"作業={work}")
    if game is not None and game >= 1: COUNT_GAME = game; changes.append(f"ゲーム={game}")
    if not changes:
        await interaction.response.send_message("変更したい人数を1以上で指定してください。", ephemeral=True)
        return
    await save_all_config()
    panel_channel = bot.get_channel(PANEL_CHANNEL_ID)
    if panel_channel:
        async for message in panel_channel.history(limit=10):
            if message.author == bot.user and message.components and any(
                c.custom_id == "btn_chat" for row in message.components for c in row.children
            ):
                v = MatchingView(); v.update_labels()
                await message.edit(embed=create_panel_embed(), view=v)
                break
    await update_all_game_panels()
    await interaction.response.send_message(f"✅ 最低人数を更新： {', '.join(changes)}", ephemeral=True)

@bot.tree.command(name="add_game", description="【管理者用】マッチング用ゲームを追加します")
@app_commands.describe(game_name="追加するゲーム名")
@app_commands.checks.has_permissions(administrator=True)
async def add_game_command(interaction, game_name: str):
    global game_list
    if game_name in game_list:
        await interaction.response.send_message(f"⚠️ 「{game_name}」は既に登録済みです！", ephemeral=True)
        return
    game_list.append(game_name)
    await save_all_config()
    await update_all_game_panels()
    await interaction.response.send_message(f"✅ 「{game_name}」を追加しました！")

@bot.tree.command(name="remove_game", description="【管理者用】マッチング用ゲームを削除します")
@app_commands.describe(game_name="削除するゲーム名")
@app_commands.checks.has_permissions(administrator=True)
async def remove_game_command(interaction, game_name: str):
    global game_list
    if game_name not in game_list:
        await interaction.response.send_message(f"⚠️ 「{game_name}」が見つかりません。", ephemeral=True)
        return
    game_list.remove(game_name)
    if game_name in waiting_games: del waiting_games[game_name]
    await save_all_config()
    await update_all_game_panels()
    await interaction.response.send_message(f"🗑️ 「{game_name}」を削除しました。")

@bot.tree.command(name="matching_guard", description="指定ユーザーとマッチングしないようブロック/解除します")
@app_commands.describe(target_member="ブロック（または解除）するメンバー")
async def matching_guard_command(interaction, target_member: discord.Member):
    global ng_relations
    user = interaction.user
    if target_member.id == user.id:
        await interaction.response.send_message("❌ 自分自身はブロックできません。", ephemeral=True)
        return
    ng_relations.setdefault(user.id, [])
    if target_member.id not in ng_relations[user.id]:
        ng_relations[user.id].append(target_member.id)
        await save_all_config()
        await interaction.response.send_message(f"🔒 **{target_member.display_name}** をブロックしました。", ephemeral=True)
    else:
        ng_relations[user.id].remove(target_member.id)
        await save_all_config()
        await interaction.response.send_message(f"🔓 **{target_member.display_name}** のブロックを解除しました。", ephemeral=True)

# --- 起動 ---
server_thread = threading.Thread(target=run_server)
server_thread.daemon = True
server_thread.start()

if TOKEN:
    bot.run(TOKEN)
