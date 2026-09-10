import os
import sys
import asyncio
import uuid
import json
import random
import re
import time
import datetime
from pathlib import Path
from urllib.parse import urlparse
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ─── Utility Modülleri ────────────────────────
try:
    from data.database import (
        init_db, get_user_xp, add_user_xp,
        get_leaderboard_data, get_user_rank_position,
        get_afk, set_afk, remove_afk,
        add_warn, get_warns, clear_warns,
        add_ticket, get_ticket, remove_ticket,
        check_and_increment_quota
    )
    from utils.rank_card import create_rank_card
    from utils.progress import LiveProgressTracker
    from utils.vip import is_user_vip, verify_user_quota
    DB_AVAILABLE = True
except ImportError as _ie:
    DB_AVAILABLE = False
    print(f"[!] Utility import hatası: {_ie} — JSON fallback kullanılıyor")

# ──────────────────────────────────────────────
# CONFIG & ENV PARSER
# ──────────────────────────────────────────────
load_dotenv()

def get_env_int(key: str, default: int = 0) -> int:
    val = os.getenv(key)
    if not val:
        return default
    try:
        clean = val.split('#')[0].strip()
        return int(clean) if clean else default
    except Exception:
        return default

def get_env_color(key: str, default: int = 0x9B59B6) -> int:
    val = os.getenv(key)
    if not val:
        return default
    try:
        clean = val.split('#')[0].strip()
        return int(clean, 16) if clean.startswith("0x") else int(clean)
    except Exception:
        return default

TOKEN                  = os.getenv("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
GUILD_ID               = get_env_int("GUILD_ID", 0)
MOD_LOG_CHANNEL        = get_env_int("MOD_LOG_CHANNEL", 0)
WELCOME_CHANNEL        = get_env_int("WELCOME_CHANNEL", 0)
TICKET_CATEGORY        = get_env_int("TICKET_CATEGORY", 0)
TICKET_LOG             = get_env_int("TICKET_LOG", 0)
VERIFY_ROLE            = get_env_int("VERIFY_ROLE", 0)
AUTO_ROLE              = get_env_int("AUTO_ROLE", 0)
STARBOARD_CH           = get_env_int("STARBOARD_CHANNEL", 0)
STAR_THRESHOLD         = get_env_int("STAR_THRESHOLD", 3)

# ─── Gelişmiş Ayarlar ─────────────────────────
MAX_FILE_SIZE_MB       = get_env_int("MAX_FILE_SIZE_MB", 50)
ENGINE_TIMEOUT_SECONDS = get_env_int("ENGINE_TIMEOUT_SECONDS", 180)
MAX_CONCURRENT_TASKS   = max(1, get_env_int("MAX_CONCURRENT_TASKS", 2))
DEFAULT_DEOBF_ENGINE   = os.getenv("DEFAULT_DEOBF_ENGINE", "auto").strip().lower()
DEFAULT_OBF_PRESET     = os.getenv("DEFAULT_OBF_PRESET", "aggressive").strip().lower()
STATUS_ACTIVITY        = os.getenv("STATUS_ACTIVITY", "⚡ /help | SuS Suite v4.0").strip()
LOG_LEVEL              = os.getenv("LOG_LEVEL", "INFO").strip()

BRAND_COLOR   = get_env_color("BRAND_COLOR", 0x9B59B6)
FOOTER_TEXT   = "⚡ SuS Cracker Suite v4.1 | sus-cracker.dev"
DANGER_COLOR  = 0xE74C3C
SUCCESS_COLOR = 0x2ECC71
INFO_COLOR    = 0x3498DB
WARNING_COLOR = 0xF39C12

BASE_DIR   = Path(__file__).parent.resolve()
ENGINE_BIN = BASE_DIR / "bin"
ENGINE_LIB = BASE_DIR / "engine" / "lib"
TEMP_DIR   = BASE_DIR / "temp"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR   = BASE_DIR / "data"

for _d in [TEMP_DIR, OUTPUT_DIR, DATA_DIR]:
    _d.mkdir(exist_ok=True)

TASK_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

# ──────────────────────────────────────────────
# ATOMIC DATA HELPERS
# ──────────────────────────────────────────────
def load_json(name: str) -> dict:
    p = DATA_DIR / f"{name}.json"
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_json(name: str, data: dict):
    tmp_path = DATA_DIR / f"{name}.tmp"
    final_path = DATA_DIR / f"{name}.json"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(final_path)
    except Exception as ex:
        print(f"[-] save_json error ({name}): {ex}")

# ──────────────────────────────────────────────
# BOT SETUP
# ──────────────────────────────────────────────
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
BOT_START_TIME = time.time()

def mk_embed(title: str, desc: str = "", color: int = BRAND_COLOR, *, footer: bool = True) -> discord.Embed:
    e = discord.Embed(title=title, description=desc, color=color,
                      timestamp=discord.utils.utcnow())
    if footer:
        e.set_footer(text=FOOTER_TEXT)
    return e

def get_cp() -> str:
    sep = ";" if sys.platform.startswith("win") else ":"
    return f"{ENGINE_BIN}{sep}{ENGINE_LIB}/*"


def ensure_engine() -> bool:
    """Bot acilirken motorun derli oldugunu dogrula, yoksa otomatik derlemeyi dene.

    Ubuntu'da en sik gorulen hata: bin/ derlenmemis -> ClassNotFound.
    Bu fonksiyon bota girmeden sorunu acikca soyler ve mumkunse cozer.
    """
    import shutil
    import subprocess

    engine_class = ENGINE_BIN / "sus" / "cracker" / "SusBytecodeEngine.class"
    engine_src = BASE_DIR / "engine" / "src" / "sus" / "cracker" / "SusBytecodeEngine.java"
    if engine_class.exists():
        return True

    print("=" * 62)
    print("[!] Java motoru derlenmemis: bin/sus/cracker/SusBytecodeEngine.class yok")
    lib_jars = list(ENGINE_LIB.glob("*.jar")) if ENGINE_LIB.exists() else []
    if not lib_jars:
        print("[!] engine/lib/*.jar BOS! Kutuphaneler VDS'ye gelmemis.")
        print("[!] .gitignore'da *.jar engeli vardi — guncel repoyu cekip lib'leri scp ile at:")
        print("[!]   scp -i my-server-key.pem -r discord_bot/engine/lib ubuntu@SUNUCU_IP:~/obs/discord_bot/engine/")
        print("=" * 62)
        return False
    javac = shutil.which("javac")
    if not javac:
        print("[!] 'javac' yok (sadece JRE kurulu). Kur: sudo apt install -y openjdk-21-jdk")
        print("=" * 62)
        return False
    print("[*] Motor otomatik derleniyor...")
    try:
        ENGINE_BIN.mkdir(parents=True, exist_ok=True)
        sep = ";" if sys.platform.startswith("win") else ":"
        cp = f"{ENGINE_LIB}{sep}*"
        r = subprocess.run(
            [javac, "-encoding", "UTF-8", "-cp", cp, "-d", str(ENGINE_BIN), str(engine_src)],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0 and engine_class.exists():
            print("[+] Motor otomatik derlendi.")
            return True
        print(f"[-] javac basarisiz (kod {r.returncode}):")
        print((r.stdout or "")[-1500:])
        print((r.stderr or "")[-1500:])
        print("[*] Elle derle: javac -encoding UTF-8 -cp \"discord_bot/engine/lib/*\" -d discord_bot/bin discord_bot/engine/src/sus/cracker/SusBytecodeEngine.java  (Linux)")
        print("=" * 62)
        return False
    except Exception as ex:
        print(f"[-] Otomatik derleme hatasi: {ex}")
        print("=" * 62)
        return False


# Motoru acilista dogrula — eksikse Discord'a baglanmadan net hata ver
ENGINE_OK = ensure_engine()
if not ENGINE_OK:
    print("[!] Bot baslatildi ama Java motoru HAZIR DEGIL — /jar* komutlari ClassNotFound verecek.")

async def run_engine(mode: str, inp, out=None, *extras):
    cmd = ["java", "-cp", get_cp(), "sus.cracker.SusBytecodeEngine", mode, str(inp)]
    if out:   cmd.append(str(out))
    for ext in extras:
        if ext is not None:
            cmd.append(str(ext))

    # Motor yoksa Java'yi bosuna calistirip ClassNotFound ile ugrasma
    if not (ENGINE_BIN / "sus" / "cracker" / "SusBytecodeEngine.class").exists():
        return -1, "", ("Java motoru derlenmemis (SusBytecodeEngine.class yok). "
                        "Cozum: ./start_bot.sh ile baslat veya "
                        "javac -encoding UTF-8 -cp \"discord_bot/engine/lib/*\" -d discord_bot/bin "
                        "discord_bot/engine/src/sus/cracker/SusBytecodeEngine.java")

    async with TASK_SEMAPHORE:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return -1, "", "Sistemde 'java' bulunamadi! Kur: sudo apt install -y openjdk-21-jdk"
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=float(ENGINE_TIMEOUT_SECONDS))
            out_s, err_s = stdout.decode(errors="replace"), stderr.decode(errors="replace")
            if proc.returncode != 0 and ("ClassNotFoundException" in err_s or "Could not find or load main class" in err_s):
                err_s += ("\n[Cozum] Motor classpath hatasi: ./start_bot.sh ile baslat (motoru derler). "
                          "Elle: javac -encoding UTF-8 -cp \"discord_bot/engine/lib/*\" -d discord_bot/bin "
                          "discord_bot/engine/src/sus/cracker/SusBytecodeEngine.java")
            return proc.returncode, out_s, err_s
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.communicate(), timeout=10)
            except Exception:
                pass
            try:
                await proc.wait()
            except Exception:
                pass
            return -1, "", f"İşlem zaman aşımına uğradı ({ENGINE_TIMEOUT_SECONDS} sn)."

# ──────────────────────────────────────────────
# XP & AFK COOLDOWNS
# ──────────────────────────────────────────────
XP_COOLDOWN: dict[str, float] = {}

# ══════════════════════════════════════════════
# PERSISTENT VIEWS
# ══════════════════════════════════════════════
class VerifyButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✅ Doğrula", style=discord.ButtonStyle.success, custom_id="sus_verify_btn")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ Bu buton sadece sunucu içinde kullanılabilir.", ephemeral=True)
        if not VERIFY_ROLE:
            return await interaction.response.send_message(
                "❌ VERIFY_ROLE ayarlanmamış. .env dosyasına ekle.", ephemeral=True)
        role = interaction.guild.get_role(VERIFY_ROLE)
        if not role:
            return await interaction.response.send_message("❌ Doğrulama rolü bulunamadı.", ephemeral=True)
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await interaction.response.send_message("❌ Üye bilgisi alınamadı.", ephemeral=True)
        if role in member.roles:
            return await interaction.response.send_message("✅ Zaten doğrulanmışsın!", ephemeral=True)
        try:
            await member.add_roles(role, reason="Verify button")
            await interaction.response.send_message(
                f"🎉 Doğrulandın! **{role.name}** rolü verildi.", ephemeral=True)
        except discord.Forbidden:
            try:
                await interaction.response.send_message("❌ Bot bu rolü verme yetkisine sahip değil.", ephemeral=True)
            except Exception:
                pass
        except (discord.NotFound, discord.HTTPException):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Doğrulama sırasında bir hata oluştu.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Doğrulama sırasında bir hata oluştu.", ephemeral=True)
            except Exception:
                pass


class TicketCategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Teknik Destek", description="Mod, kurulum veya bot ile ilgili genel teknik yardım", emoji="🛠️", value="destek"),
            discord.SelectOption(label="Crack Talebi", description="Özel mod/JAR crack ve analiz talepleri", emoji="🔓", value="crack"),
            discord.SelectOption(label="Lisans Satın Alım", description="SuS Suite VIP, Bot ve Özel Lisans işlemleri", emoji="💎", value="lisans"),
        ]
        super().__init__(placeholder="Destek kategorisi seçin...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "❌ Ticket sistemi sadece sunucu içinde kullanılabilir.", ephemeral=True)
        category_choice = self.values[0]
        cat_info = {
            "destek": ("destek", "🛠️ Teknik Destek", 0x3498DB),
            "crack":  ("crack",  "🔓 Crack Talebi",   0xE74C3C),
            "lisans": ("lisans", "💎 Lisans Satın Alım", 0x9B59B6),
        }
        slug, title, col = cat_info.get(category_choice, ("ticket", "🎫 Genel Destek", BRAND_COLOR))

        guild = interaction.guild
        category = guild.get_channel(TICKET_CATEGORY) if TICKET_CATEGORY else None
        safe_name = "".join(c for c in interaction.user.name.lower() if c.isalnum() or c == "-")[:15]
        ch_name = f"t-{slug}-{safe_name}"

        existing = discord.utils.get(guild.text_channels, name=ch_name)
        if existing:
            return await interaction.response.send_message(
                f"❌ Zaten bu kategoride açık bir ticketin var: {existing.mention}", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user:   discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True),
            guild.me:           discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
        }
        try:
            ch = await guild.create_text_channel(
                ch_name, category=category, overwrites=overwrites,
                topic=f"Kategori: {title} | {interaction.user} ({interaction.user.id})")
        except discord.Forbidden:
            return await interaction.followup.send("❌ Bot kanal oluşturma yetkisine sahip değil.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send("❌ Kanal oluşturulamadı (isim çakışması olabilir, tekrar dene).", ephemeral=True)

        e = mk_embed(f"{title} — Ticket Açıldı",
            f"Merhaba {interaction.user.mention}! Destek ekibimiz seninle ilgilenecektir.\n\n"
            f"📌 **Seçilen Kategori:** `{title}`\n"
            "Lütfen talebini, ilgili dosyaları veya ekran görüntülerini detaylıca yaz.\n"
            "Ticket'ı kapatmak istediğinde aşağıdaki butona basabilirsin.", col)
        await ch.send(content=interaction.user.mention, embed=e, view=CloseTicketView())
        await interaction.followup.send(f"✅ Ticket açıldı: {ch.mention}", ephemeral=True)

        if DB_AVAILABLE:
            try:
                await add_ticket(str(ch.id), str(interaction.user.id), title)
            except Exception:
                pass
        tdata = load_json("tickets")
        tdata[str(ch.id)] = {"user": interaction.user.id, "category": title, "opened": time.time()}
        save_json("tickets", tdata)


class TicketCategoryView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(TicketCategorySelect())


class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 Ticket Aç", style=discord.ButtonStyle.primary, custom_id="sus_open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        e = mk_embed("🎫 Destek Kategorisi Seçin",
            "Lütfen açmak istediğiniz bilet türünü aşağıdaki menüden seçin:\n\n"
            "🛠️ **Teknik Destek:** Genel mod, kurulum veya motor yardımı\n"
            "🔓 **Crack Talebi:** Özel JAR deobfuscate ve crack istekleri\n"
            "💎 **Lisans Satın Alım:** VIP erişim, KeyAuth ve lisans hizmetleri", BRAND_COLOR)
        await interaction.response.send_message(embed=e, view=TicketCategoryView(), ephemeral=True)



class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 Ticket'ı Kapat", style=discord.ButtonStyle.danger, custom_id="sus_close_ticket")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        ch = interaction.channel
        if ch is None or not hasattr(ch, "name") or not (ch.name.startswith("ticket-") or ch.name.startswith("t-")):
            return await interaction.response.send_message("❌ Bu bir ticket kanalı değil.", ephemeral=True)

        await interaction.response.defer()
        await interaction.followup.send("🔒 Ticket 5 saniye içinde kapatılıyor...")

        transcript_lines = []
        try:
            async for msg in ch.history(limit=300, oldest_first=True):
                ts = msg.created_at.strftime("%d.%m.%Y %H:%M")
                content = redact_urls(msg.content or "")[:500]
                transcript_lines.append(f"[{ts}] {msg.author} ({msg.author.id}): {content}")
        except Exception:
            pass

        txt = "\n".join(transcript_lines) or "(Mesaj bulunamadı)"

        if TICKET_LOG and interaction.guild is not None:
            log_ch = interaction.guild.get_channel(TICKET_LOG)
            if log_ch:
                f_path = TEMP_DIR / f"transcript_{ch.id}_{uuid.uuid4().hex[:6]}.txt"
                try:
                    f_path.write_text(txt, encoding="utf-8")
                    log_e = mk_embed(
                        "📋 Ticket Transkripti",
                        f"**Kanal:** {ch.name}\n**Kapatan:** {interaction.user.mention}\n"
                        f"**Kullanıcı:** <@{load_json('tickets').get(str(ch.id), {}).get('user', '?')}>",
                        INFO_COLOR)
                    dfile = discord.File(f_path, filename=f"transcript_{ch.name}.txt")
                    try:
                        await log_ch.send(embed=log_e, file=dfile)
                    finally:
                        close_discord_files([dfile])
                except Exception:
                    pass
                finally:
                    safe_unlink(f_path)

        await asyncio.sleep(5)
        try:
            await ch.delete(reason=f"Ticket kapatıldı - {interaction.user}")
        except discord.NotFound:
            pass

# ══════════════════════════════════════════════
# EVENTS
# ══════════════════════════════════════════════
@bot.event
async def on_ready():
    print(f"[+] {bot.user} ({bot.user.id}) online | discord.py {discord.__version__}")
    bot.add_view(VerifyButton())
    bot.add_view(TicketView())
    bot.add_view(CloseTicketView())

    # SQLite veritabanını başlat
    if DB_AVAILABLE:
        try:
            await init_db()
            print("[+] SQLite database initialized (WAL mode)")
        except Exception as exc:
            print(f"[-] DB init error: {exc}")

    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=STATUS_ACTIVITY))
    except Exception as exc:
        print(f"[-] Activity error: {exc}")

    try:
        synced = await bot.tree.sync()
        print(f"[+] {len(synced)} slash commands synced")
    except Exception as exc:
        print(f"[-] Sync error: {exc}")

# ──────────────────────────────────────────────
# MEMBER JOIN / LEAVE
# ──────────────────────────────────────────────
@bot.event
async def on_member_join(member: discord.Member):
    if AUTO_ROLE:
        role = member.guild.get_role(AUTO_ROLE)
        if role:
            try:
                await member.add_roles(role, reason="Auto-Role on join")
            except discord.Forbidden:
                pass

    if WELCOME_CHANNEL:
        ch = member.guild.get_channel(WELCOME_CHANNEL)
        if ch and isinstance(ch, discord.TextChannel):
            e = mk_embed(
                f"👋 Hoş Geldin, {member.display_name}!",
                f"**{member.guild.name}** sunucusuna katıldın!\n"
                f"Sen sunucunun **{member.guild.member_count}.** üyesisin.\n\n"
                "Kuralları oku ve doğrulama yap! 🎉",
                SUCCESS_COLOR)
            e.set_thumbnail(url=member.display_avatar.url)
            await ch.send(embed=e)


@bot.event
async def on_member_remove(member: discord.Member):
    if WELCOME_CHANNEL:
        ch = member.guild.get_channel(WELCOME_CHANNEL)
        if ch and isinstance(ch, discord.TextChannel):
            e = mk_embed(
                f"👋 Güle Güle, {member.display_name}!",
                f"**{member.display_name}** sunucudan ayrıldı. Toplam üye: **{member.guild.member_count}**",
                DANGER_COLOR)
            e.set_thumbnail(url=member.display_avatar.url)
            await ch.send(embed=e)

# ──────────────────────────────────────────────
# STARBOARD
# ──────────────────────────────────────────────
@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if not reaction.message.guild or user.bot:
        return
    if str(reaction.emoji) != "⭐":
        return
    if reaction.count < STAR_THRESHOLD or not STARBOARD_CH:
        return

    ch = reaction.message.guild.get_channel(STARBOARD_CH)
    if not ch:
        return

    starred = load_json("starboard")
    msg_id  = str(reaction.message.id)
    if msg_id in starred:
        return

    e = mk_embed(
        f"⭐ {reaction.count} Yıldız!",
        reaction.message.content or "[Medya / Embed mesajı]",
        color=0xF1C40F)
    e.add_field(name="Kaynak", value=f"[Mesaja git]({reaction.message.jump_url})")
    e.set_author(name=reaction.message.author.display_name,
                 icon_url=reaction.message.author.display_avatar.url)
    if reaction.message.attachments:
        e.set_image(url=reaction.message.attachments[0].url)
    await ch.send(embed=e)

    starred[msg_id] = {"stars": reaction.count, "author": str(reaction.message.author)}
    save_json("starboard", starred)

# ──────────────────────────────────────────────
# MESSAGE EVENT (XP + AFK)
# ──────────────────────────────────────────────
@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    await bot.process_commands(message)

    uid = str(message.author.id)

    if DB_AVAILABLE:
        # ─── 1. AFK Check (DB) ────────────────────
        afk_info = await get_afk(uid)
        if afk_info:
            await remove_afk(uid)
            e = mk_embed("👋 Tekrar Hoş Geldin!",
                f"{message.author.mention}, AFK modundan çıktın.\n**Eski Sebep:** {afk_info['reason']}",
                SUCCESS_COLOR)
            await message.channel.send(embed=e, delete_after=6)

        # ─── 2. AFK Mention Check (DB) ────────────
        if message.mentions:
            for mentioned in message.mentions:
                info = await get_afk(str(mentioned.id))
                if info:
                    since = f"<t:{int(info['time'])}:R>"
                    e = mk_embed("💤 Kullanıcı AFK",
                        f"**{mentioned.display_name}** şu an AFK.\n**Sebep:** {info['reason']}\n**Süre:** {since}",
                        WARNING_COLOR)
                    await message.channel.send(embed=e, delete_after=8)
                    break

        # ─── 3. XP System (DB) ────────────────────
        now = time.time()
        if now - XP_COOLDOWN.get(uid, 0) >= 60:
            XP_COOLDOWN[uid] = now
            xp_amount = random.randint(15, 30)
            leveled_up, new_level, new_xp = await add_user_xp(uid, xp_amount)
            if leveled_up:
                e = mk_embed("🎉 Seviye Atladın!",
                    f"Tebrikler {message.author.mention}! Artık **Seviye {new_level}** oldun! 🚀",
                    0xF39C12)
                await message.channel.send(embed=e)
    else:
        # ─── Fallback: JSON tabanlı ────────────────
        afk_data = load_json("afk")
        if uid in afk_data:
            reason = afk_data.pop(uid).get("reason", "Belirtilmedi")
            save_json("afk", afk_data)
            e = mk_embed("👋 Tekrar Hoş Geldin!", f"{message.author.mention}, AFK modundan çıktın.\n**Eski Sebep:** {reason}", SUCCESS_COLOR)
            await message.channel.send(embed=e, delete_after=6)
        if message.mentions:
            afk_data = load_json("afk")
            for mentioned in message.mentions:
                m_id = str(mentioned.id)
                if m_id in afk_data:
                    info = afk_data[m_id]
                    since = f"<t:{int(info['time'])}:R>"
                    e = mk_embed("💤 Kullanıcı AFK", f"**{mentioned.display_name}** şu an AFK.\n**Sebep:** {info['reason']}\n**Süre:** {since}", WARNING_COLOR)
                    await message.channel.send(embed=e, delete_after=8)
                    break
        now = time.time()
        if now - XP_COOLDOWN.get(uid, 0) >= 60:
            XP_COOLDOWN[uid] = now
            xp_data = load_json("xp")
            user_data = xp_data.setdefault(uid, {"xp": 0, "level": 1})
            user_data["xp"] += random.randint(15, 30)
            needed = user_data["level"] * 100
            if user_data["xp"] >= needed:
                user_data["xp"] -= needed
                user_data["level"] += 1
                lvl = user_data["level"]
                e = mk_embed("🎉 Seviye Atladın!", f"Tebrikler {message.author.mention}! Artık **Seviye {lvl}** oldun! 🚀", 0xF39C12)
                await message.channel.send(embed=e)
            save_json("xp", xp_data)

# ══════════════════════════════════════════════
# SLASH COMMANDS
# ══════════════════════════════════════════════

# ─── 1. HELP COMMAND ──────────────────────────
class HelpSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Bytecode Güvenlik & Deobf Araçları", description="Obfuscator, Cracker, Scanner, Deobfuscator", emoji="🛡️", value="bytecode"),
            discord.SelectOption(label="Moderasyon Komutları", description="Ban, Kick, Mute, Warn, Clear, Unban", emoji="👮", value="mod"),
            discord.SelectOption(label="Sunucu & Bot Yönetimi", description="Settings, BotReload, Ticket, Verify, Giveaway", emoji="⚙️", value="admin"),
            discord.SelectOption(label="Kullanıcı & XP", description="Rank, Leaderboard, Userinfo, AFK, Poll", emoji="📈", value="user"),
        ]
        super().__init__(placeholder="Komut kategorisi seç...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "bytecode":
            e = mk_embed("🛡️ Bytecode Güvenlik & Deobf Komutları",
                "• `/jarobfuscator [preset] [rename]` – JAR dosyasını polimorfik şifreleme ve matematiksel invariantlarla şifrele.\n"
                "• `/jardeobfuscator [engine]` – Şifreli JAR'ı temizle + hem temiz JAR hem kaynak ZIP al.\n"
                "• `/jardeobfuscationsrc [engine]` – JAR'ın şifresini çözüp tüm .java kaynak kodlarını ve raporu ZIP olarak indir.\n"
                "• `/jarcracker [target]` – **43 metot** ile crack: Meteor Addon, Fabric Mod, Forge Mod, Lunar/Badlion/LabyMod veya Genel.\n"
                "• `/jarscanner` – JAR dosyasını RAT, Webhook Logger ve zararlı kodlar için derinlemesine tara.\n"
                "• `/jarclear [honeypot_url]` – JAR'daki Webhook, RAT ve Grabber kodlarını temizle, saldırgan Webhook'unu ifşa et ve tuzağa/honeypot'a yönlendir.\n"
                "• `/jarprotect [hwid] [expire_days] [alarm_webhook]` – Mod geliştiricileri için HWID kilidi, Süre Sınırı ve Güvenlik Kalkanı enjekte et.\n"
                "• `/jardiff [original_file] [modified_file]` – İki JAR arasındaki bytecode, sınıf ve yapısal farkları karşılaştırıp raporla.", BRAND_COLOR)
        elif val == "mod":
            e = mk_embed("👮 Moderasyon Komutları",
                "• `/ban [user] [sebep]` – Kullanıcıyı banla.\n"
                "• `/kick [user] [sebep]` – Kullanıcıyı at.\n"
                "• `/mute [user] [dakika] [sebep]` – Kullanıcıyı sustur.\n"
                "• `/unmute [user]` – Susturmayı kaldır.\n"
                "• `/warn [user] [sebep]` – Kullanıcıyı uyar.\n"
                "• `/warns [user]` – Uyarıları listele.\n"
                "• `/clearwarns [user]` – Uyarıları sil.\n"
                "• `/clear [sayı]` – Mesajları sil.\n"
                "• `/unban [user_id]` – Ban kaldır.", DANGER_COLOR)
        elif val == "admin":
            e = mk_embed("⚙️ Sunucu & Bot Yönetimi",
                "• `/settings` – Botun tüm yapılandırma ayarlarını ve durumunu görüntüle.\n"
                "• `/botreload` – Konfigürasyon ve veritabanını bota yeniden yükle.\n"
                "• `/ticket-panel` – Ticket destek panelini gönder.\n"
                "• `/verify-panel` – Doğrulama panelini gönder.\n"
                "• `/giveaway [dakika] [ödül] [kazanan]` – Çekiliş başlat.\n"
                "• `/products` – Ürün ve hizmetler paneli.", SUCCESS_COLOR)
        else:
            e = mk_embed("📈 Kullanıcı & Seviye Komutları",
                "• `/rank [user]` – Seviye ve XP durumunu gör.\n"
                "• `/leaderboard` – XP sıralaması.\n"
                "• `/afk [sebep]` – AFK moduna geç.\n"
                "• `/poll [soru] [seçenekler]` – Oylama başlat.\n"
                "• `/userinfo [user]` – Kullanıcı detayları.\n"
                "• `/serverinfo` – Sunucu istatistikleri.\n"
                "• `/health` – Bot ve sistem durumunu göster.\n"
                "• `/ping` – Gecikme ölç.", INFO_COLOR)
        await interaction.response.edit_message(embed=e)


class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(HelpSelect())


@bot.tree.command(name="help", description="❓ Bot komutları ve yardım menüsü.")
async def help_cmd(interaction: discord.Interaction):
    e = mk_embed("⚡ SuS Cracker Bot v4.1 — Yardım Menüsü",
        "Aşağıdaki açılır menüden kategorileri seçerek detaylı komut listesini görebilirsin!\n\n"
        "🛡️ **Bytecode:** Obfuscator / Cracker (43 metot) / Scanner / Deobf / Honeypot / JarProtect / JarDiff\n"
        "⚙️ **Yönetim:** Settings / BotReload / Ticket / Verify / Giveaway\n"
        "👮 **Moderasyon:** Ban / Kick / Mute / Warn / Clear\n"
        "📈 **Kullanıcı:** Görsel Rank Kartı / Sayfalamalı Leaderboard / AFK / Poll", BRAND_COLOR)
    await interaction.response.send_message(embed=e, view=HelpView())

# ─── 2. AFK COMMAND ───────────────────────────
@bot.tree.command(name="afk", description="💤 AFK moduna geç.")
@app_commands.describe(sebep="AFK olma sebebi")
async def afk_cmd(interaction: discord.Interaction, sebep: str = "Belirtilmedi"):
    uid = str(interaction.user.id)
    if DB_AVAILABLE:
        await set_afk(uid, sebep)
    else:
        afk_data = load_json("afk")
        afk_data[uid] = {"reason": sebep, "time": time.time()}
        save_json("afk", afk_data)
    e = mk_embed("💤 AFK Modu", f"{interaction.user.mention} artık AFK!\n**Sebep:** {sebep}", WARNING_COLOR)
    await interaction.response.send_message(embed=e)

async def safe_defer(interaction: discord.Interaction, thinking: bool = True):
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=thinking)
    except (discord.NotFound, discord.HTTPException):
        pass

async def safe_followup(interaction: discord.Interaction, *args, **kwargs):
    try:
        return await interaction.followup.send(*args, **kwargs)
    except (discord.NotFound, discord.HTTPException):
        try:
            if interaction.channel:
                return await interaction.channel.send(*args, **kwargs)
        except Exception:
            pass

def check_file_size(file: discord.Attachment) -> bool:
    max_bytes = MAX_FILE_SIZE_MB * 1024 * 1024
    return file.size <= max_bytes

def safe_unlink(path, retries: int = 3, delay: float = 0.2):
    """Windows WinError 32 (dosya kilitli) durumuna dayanıklı silme.
    discord.File handle'ı veya antivirus/Java kilidi açık kalmış olabilir,
    bu yüzden birkaç kez retry yapar, asla exception fırlatmaz.
    NOT: event-loop'u bloklamamak için delay kısa tutuldu (max ~0.6sn)."""
    try:
        p = Path(path)
    except Exception:
        return
    for attempt in range(retries):
        try:
            p.unlink(missing_ok=True)
            return
        except PermissionError:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            print(f"[!] safe_unlink: dosya kilitli, silinemedi: {p}")
            return
        except FileNotFoundError:
            return
        except OSError as ex:
            if attempt < retries - 1:
                time.sleep(delay)
                continue
            print(f"[!] safe_unlink OSError ({p}): {ex}")
            return

def close_discord_files(files):
    for f in files:
        try:
            f.close()
        except Exception:
            pass

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

def safe_filename(name: str, max_len: int = 50, default: str = "file.jar") -> str:
    """Attachment filename'den path traversal ve geçersiz karakterleri temizler.
    'a/b', '..\\', 'C:\\x', kontrol karakterleri -> güvenli flat isim."""
    try:
        base = Path(name or default).name
    except Exception:
        base = default
    base = _SAFE_NAME_RE.sub("_", base).strip("._") or default
    if len(base) > max_len:
        stem, dot, ext = base.rpartition(".")
        ext = f".{ext}" if dot else ""
        base = stem[: max_len - len(ext)] + ext
    if "." not in base:
        base += ".jar"
    return base

def assert_inside(path: Path, parent: Path) -> Path:
    """Symlink/traversal'e karşı resolve edip parent içinde olduğunu doğrula."""
    try:
        rp = Path(path).resolve()
        pp = Path(parent).resolve()
        if rp == pp or pp in rp.parents:
            return rp
    except Exception:
        pass
    raise ValueError(f"Güvensiz dosya yolu: {path}")

_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

def redact_urls(text: str) -> str:
    """Log/embed'e basmadan önce URL/token sızıntısını maskeler."""
    if not text:
        return text
    return _URL_RE.sub("[redacted-url]", text)

def is_safe_http_url(url: str, max_len: int = 500) -> bool:
    try:
        if not url or len(url) > max_len:
            return False
        u = urlparse(url.strip())
        if u.scheme not in ("http", "https"):
            return False
        host = (u.hostname or "").lower()
        if not host:
            return False
        # SSRF koruması: localhost + cloud metadata + file/javascript şemaları zaten elendi
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1",
                    "169.254.169.254", "213.0.0.0", "metadata.google.internal"):
            return False
        if host.endswith((".internal", ".local")):
            return False
        return True
    except Exception:
        return False

_HWID_RE = re.compile(r"^[A-Za-z0-9._-]{0,64}$")

def sanitize_hwid(hwid: str | None) -> str:
    h = (hwid or "").strip()
    if not h:
        return "NONE"
    if not _HWID_RE.match(h):
        return "NONE"
    return h

def _warn_field(w) -> tuple[str, str]:
    """get_warns tuple (id, reason, moderator, ts) veya JSON dict — ikisini de destekler."""
    if isinstance(w, (tuple, list)):
        reason = str(w[1]) if len(w) > 1 else "Belirtilmedi"
        mod = str(w[2]) if len(w) > 2 else "?"
        return reason, mod
    if isinstance(w, dict):
        reason = str(w.get("reason", w.get("sebep", "Belirtilmedi")))
        mod = str(w.get("moderator", w.get("yetkili", "?")))
        return reason, mod
    return "Belirtilmedi", "?"

# ─── 3. POLL COMMAND ───────────────────────────
@bot.tree.command(name="poll", description="📊 Hızlı oylama başlat.")
@app_commands.describe(soru="Oylama konusu", secenekler="Seçenekleri virgülle ayır (ör: Evet, Hayır)")
async def poll_cmd(interaction: discord.Interaction, soru: str, secenekler: str = "Evet, Hayır"):
    opts = [s.strip() for s in secenekler.split(",") if s.strip()][:10]
    if len(opts) < 2:
        return await interaction.response.send_message("❌ En az 2 seçenek yazmalısın!", ephemeral=True)

    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    desc_lines = [f"{emojis[i]} **{opt}**" for i, opt in enumerate(opts)]

    e = mk_embed(f"📊 OYLAMA: {soru}", "\n".join(desc_lines), INFO_COLOR)
    e.set_footer(text=f"Oylamayı başlatan: {interaction.user.display_name} | {FOOTER_TEXT}")

    await interaction.response.send_message("✅ Oylama gönderildi.", ephemeral=True)
    msg = await interaction.channel.send(embed=e)
    for i in range(len(opts)):
        await msg.add_reaction(emojis[i])

# ─── 4. HEALTH / SYSTEM STATUS ────────────────
@bot.tree.command(name="health", description="💻 Bot ve sistem sağlık durumunu göster.")
async def health_cmd(interaction: discord.Interaction):
    uptime_sec = int(time.time() - BOT_START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    temp_files = list(TEMP_DIR.glob("*"))
    output_files = list(OUTPUT_DIR.glob("*"))

    e = mk_embed("💻 Sistem & Bot Sağlık Raporu", color=SUCCESS_COLOR)
    e.add_field(name="⏱️ Uptime", value=uptime_str, inline=True)
    e.add_field(name="🏓 Ping", value=f"{round(bot.latency*1000)}ms", inline=True)
    e.add_field(name="🐍 Python", value=sys.version.split()[0], inline=True)
    e.add_field(name="📁 Geçici Dosyalar", value=f"Temp: {len(temp_files)} | Output: {len(output_files)}", inline=True)
    e.add_field(name="🏰 Sunucular", value=str(len(bot.guilds)), inline=True)
    try:
        _slots = TASK_SEMAPHORE._value
    except Exception:
        _slots = "?"
    e.add_field(name="⚙️ Java Engine", value=f"ASM 9.7 (Hazır, Slot: {_slots}/{MAX_CONCURRENT_TASKS})", inline=True)
    await interaction.response.send_message(embed=e)

# ─── 5. SETTINGS & BOTRELOAD COMMANDS ──────────
@bot.tree.command(name="settings", description="⚙️ Botun tüm yapılandırma ayarlarını ve durumunu görüntüle.")
@app_commands.default_permissions(manage_guild=True)
async def settings_cmd(interaction: discord.Interaction):
    guild = interaction.guild

    mod_ch = guild.get_channel(MOD_LOG_CHANNEL) if MOD_LOG_CHANNEL else None
    wel_ch = guild.get_channel(WELCOME_CHANNEL) if WELCOME_CHANNEL else None
    tck_ca = guild.get_channel(TICKET_CATEGORY) if TICKET_CATEGORY else None
    tck_lg = guild.get_channel(TICKET_LOG) if TICKET_LOG else None
    v_role = guild.get_role(VERIFY_ROLE) if VERIFY_ROLE else None
    a_role = guild.get_role(AUTO_ROLE) if AUTO_ROLE else None
    s_ch   = guild.get_channel(STARBOARD_CH) if STARBOARD_CH else None

    e = mk_embed("⚙️ SuS Cracker Bot — Yapılandırma Paneli", color=BRAND_COLOR)

    # Motor ayarları
    e.add_field(name="🛡️ Motor Ayarları",
        value=f"• **Maks Dosya Boyutu:** `{MAX_FILE_SIZE_MB} MB`\n"
              f"• **Zaman Aşımı:** `{ENGINE_TIMEOUT_SECONDS} sn`\n"
              f"• **Eşzamanlı Görev:** `{MAX_CONCURRENT_TASKS}`\n"

              f"• **Varsayılan Deobf Motoru:** `{DEFAULT_DEOBF_ENGINE}`\n"
              f"• **Varsayılan Obf Seviyesi:** `{DEFAULT_OBF_PRESET}`", inline=False)

    # Kanal ve roller
    e.add_field(name="📌 Kanal & Rol Bağlantıları",
        value=f"• **Mod Log:** {mod_ch.mention if mod_ch else '`Kapalı (0)`'}\n"
              f"• **Hoş Geldin:** {wel_ch.mention if wel_ch else '`Kapalı (0)`'}\n"
              f"• **Ticket Kategori:** {tck_ca.name if tck_ca else '`Kapalı (0)`'}\n"
              f"• **Ticket Log:** {tck_lg.mention if tck_lg else '`Kapalı (0)`'}\n"
              f"• **Starboard:** {s_ch.mention if s_ch else '`Kapalı (0)`'} (⭐ {STAR_THRESHOLD})\n"
              f"• **Doğrulama Rolü:** {v_role.mention if v_role else '`Kapalı (0)`'}\n"
              f"• **Oto Rol:** {a_role.mention if a_role else '`Kapalı (0)`'}", inline=False)

    e.add_field(name="💡 Ayarları Düzenleme",
        value="Tüm ayarlar `discord_bot/.env` dosyasından okunur. Değişiklik yaptıktan sonra `/botreload` komutuyla güncelleyebilirsiniz.", inline=False)

    await interaction.response.send_message(embed=e, ephemeral=True)


@bot.tree.command(name="botreload", description="🔄 Konfigürasyon ve verileri bota yeniden yükle.")
@app_commands.default_permissions(manage_guild=True)
async def botreload_cmd(interaction: discord.Interaction):
    global MAX_FILE_SIZE_MB, ENGINE_TIMEOUT_SECONDS, MAX_CONCURRENT_TASKS
    global DEFAULT_DEOBF_ENGINE, DEFAULT_OBF_PRESET, STATUS_ACTIVITY, BRAND_COLOR, LOG_LEVEL

    load_dotenv(override=True)
    MAX_FILE_SIZE_MB       = get_env_int("MAX_FILE_SIZE_MB", 50)
    ENGINE_TIMEOUT_SECONDS = get_env_int("ENGINE_TIMEOUT_SECONDS", 180)
    MAX_CONCURRENT_TASKS   = max(1, get_env_int("MAX_CONCURRENT_TASKS", 2))
    DEFAULT_DEOBF_ENGINE   = os.getenv("DEFAULT_DEOBF_ENGINE", "auto").strip().lower()
    DEFAULT_OBF_PRESET     = os.getenv("DEFAULT_OBF_PRESET", "aggressive").strip().lower()
    STATUS_ACTIVITY        = os.getenv("STATUS_ACTIVITY", "⚡ /help | SuS Suite v4.1").strip()
    LOG_LEVEL              = os.getenv("LOG_LEVEL", "INFO").strip()
    BRAND_COLOR            = get_env_color("BRAND_COLOR", 0x9B59B6)

    try:
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=STATUS_ACTIVITY))
    except Exception:
        pass

    e = mk_embed("🔄 Ayarlar Yeniden Yüklendi",
        f"✅ `.env` konfigürasyonu başarıyla güncellendi!\n\n"
        f"• **Maks Dosya:** `{MAX_FILE_SIZE_MB}MB`\n"
        f"• **Motor Zaman Aşımı:** `{ENGINE_TIMEOUT_SECONDS}sn`\n"
        f"• **Eşzamanlılık:** `{MAX_CONCURRENT_TASKS}`\n"
        f"• **Varsayılan Deobf:** `{DEFAULT_DEOBF_ENGINE}`\n"
        f"• **Varsayılan Obf:** `{DEFAULT_OBF_PRESET}`\n"
        f"• **Durum Metni:** `{STATUS_ACTIVITY}`", SUCCESS_COLOR)
    await interaction.response.send_message(embed=e, ephemeral=True)

# ─── 6. BYTECODE COMMANDS ──────────────────────

@bot.tree.command(name="jarobfuscator", description="🛡️ Minecraft mod/JAR dosyasını gelişmiş yöntemlerle obfuscate et.")
@app_commands.describe(
    file="Obfuscate edilecek .jar dosyası",
    preset="Obfuscation seviyesi: standard, aggressive veya extreme",
    rename_classes="Dahili sınıfları ve alanları görünmez Unicode ile gizle"
)
@app_commands.choices(preset=[
    app_commands.Choice(name="Standard (Temel XOR & Sabit Karıştırma)", value="standard"),
    app_commands.Choice(name="Aggressive (Polimorfik XOR + Opaque Predicates)", value="aggressive"),
    app_commands.Choice(name="Extreme (Maksimum Şifreleme + Tam İsim Gizleme)", value="extreme")
])
async def jarobfuscator(
    interaction: discord.Interaction,
    file: discord.Attachment,
    preset: app_commands.Choice[str] = None,
    rename_classes: bool = True
):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    # VIP Quota Check
    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid = str(uuid.uuid4())[:8]
    inp = TEMP_DIR  / f"{tid}_{safe_filename(file.filename)}"
    out = OUTPUT_DIR / f"{tid}_obfuscated_{safe_filename(file.filename)}"
    chosen_preset = preset.value if preset else DEFAULT_OBF_PRESET

    # Canlı progress tracker
    tracker = LiveProgressTracker(interaction, "🛡️ SuS Obfuscator — Şifreleniyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    start_time = time.time()
    try:
        await file.save(inp)
        ret, stdout, stderr = await run_engine("obfuscate", inp, out, chosen_preset, str(rename_classes).lower())
        elapsed = round(time.time() - start_time, 2)

        if tracker:
            await tracker.stop()

        if ret != 0 or not out.exists():
            log = redact_urls(stdout + stderr)[:1000]
            return await safe_followup(interaction, f"❌ Obfuscation hatası:\n```\n{log}\n```")

        out_size_mb = out.stat().st_size / (1024 * 1024)
        if out_size_mb > 24.5:
            return await safe_followup(interaction, f"⚠️ İşlem tamamlandı ancak çıktı dosyası (`{out_size_mb:.1f} MB`) Discord'un 25MB yükleme sınırını aşıyor!")

        e = mk_embed(f"🛡️ SuS Obfuscator v4.2 — Tamamlandı [{chosen_preset.upper()}]",
            f"**`{file.filename}`** başarıyla kırılamaz seviyede şifrelendi! (⏱️ `{elapsed}s`)\n\n"
            "🔒 **Uygulanan Üst Düzey Güvenlik Katmanları:**\n"
            "• **Çok Katmanlı Dinamik String Şifreleme** (İndeks bazlı polinomik XOR)\n"
            "• **Control Flow Flattening** (Doğrusal akış kırıcı durum makineleri)\n"
            "• **Değişken & Parametre İsimlerinin Yok Edilmesi** (`var_` kalıntıları temizlendi)\n"
            "• **Aritmetik & Bitwise Sabit Karıştırma** (Çok adımlı nötrleyici zincirler)\n"
            "• **Matematiksel Invariant & Sahte Hata Tuzakları** (Anti-Decompiler)\n"
            "• **Görünmez Unicode / Homoglyph Gizleme**\n"
            "• **Debug, LineNumber & SourceFile Bilgilerinin Sıfırlanması**", SUCCESS_COLOR)

        e.add_field(name="📊 Dosya Detayları",
            value=f"• Orijinal: `{file.size / 1024:.1f} KB`\n"
                  f"• Korunan: `{out.stat().st_size / 1024:.1f} KB`\n"
                  f"• Seviye: `{chosen_preset.upper()}`", inline=True)

        dfile = discord.File(out, filename=f"obfuscated_{safe_filename(file.filename)}")
        try:
            await safe_followup(interaction, embed=e, file=dfile)
        finally:
            close_discord_files([dfile])
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp, out]:
            safe_unlink(_f)



@bot.tree.command(name="jardeobfuscator", description="⚡ JAR'ı deobfuscate et + temiz JAR ve kaynak kodu al.")
@app_commands.describe(
    file="Deobfuscate edilecek .jar dosyası",
    engine="Decompiler motor tercihi (Auto / CFR / Vineflower)"
)
@app_commands.choices(engine=[
    app_commands.Choice(name="Auto (Önce CFR, hata durumunda Vineflower)", value="auto"),
    app_commands.Choice(name="Vineflower (Gelişmiş Decompiler & Member Renamer)", value="vineflower"),
    app_commands.Choice(name="CFR (Agresif Anti-Obf & Decompile Motoru)", value="cfr")
])
async def jardeobfuscator(
    interaction: discord.Interaction,
    file: discord.Attachment,
    engine: app_commands.Choice[str] = None
):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum limit: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid   = str(uuid.uuid4())[:8]
    inp   = TEMP_DIR  / f"{tid}_{safe_filename(file.filename)}"
    clean = OUTPUT_DIR / f"{tid}_deobfuscated_{safe_filename(file.filename)}"
    src   = OUTPUT_DIR / f"{tid}_secure_source.zip"
    chosen_engine = engine.value if engine else DEFAULT_DEOBF_ENGINE

    tracker = LiveProgressTracker(interaction, "⚡ SuS Deobfuscator — Çözülüyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    start_time = time.time()
    try:
        await file.save(inp)
        ret, stdout, stderr = await run_engine("deobfuscate", inp, clean, src, chosen_engine)
        elapsed = round(time.time() - start_time, 2)

        if tracker:
            await tracker.stop()

        if ret != 0 or not clean.exists():
            log = redact_urls(stdout + stderr)[:1000]
            return await safe_followup(interaction, f"❌ DeObfuscation hatası:\n```\n{log}\n```")

        e = mk_embed("⚡ SuS Deobfuscator v4.1 — Tamamlandı",
            f"`{file.filename}` başarıyla çözüldü, optimize edildi ve kaynak kodları çıkarıldı! (⏱️ `{elapsed}s`)\n\n"
            "📦 **Teslim Edilen Paketler:**\n"
            f"• `deobfuscated_{file.filename}` – Temiz bytecode JAR\n"
            "• `source_code.zip` – Eksiksiz `.java` kaynak kodları + `DEOBFUSCATION_REPORT.md`", 0x1ABC9C)

        files_out = []
        if clean.exists() and clean.stat().st_size <= 24.5 * 1024 * 1024:
            files_out.append(discord.File(clean, filename=f"deobfuscated_{safe_filename(file.filename)}"))
        if src.exists() and src.stat().st_size <= 24.5 * 1024 * 1024:
            files_out.append(discord.File(src, filename=f"source_code_{safe_filename(file.filename).replace('.jar','')}.zip"))

        if not files_out:
            return await safe_followup(interaction, "⚠️ Çıktı dosyaları Discord'un 25MB yükleme sınırını aşıyor!")

        try:
            await safe_followup(interaction, embed=e, files=files_out)
        finally:
            close_discord_files(files_out)
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp, clean, src]:
            safe_unlink(_f)


@bot.tree.command(name="jardeobfuscationsrc", description="⚡ JAR modunun şifresini çözüp tüm .java kaynak kodlarını ve raporu ZIP olarak verir.")
@app_commands.describe(
    file="Şifresi çözülecek ve .java kaynak kodları çıkarılacak .jar dosyası",
    engine="Decompiler motor tercihi (Auto / CFR / Vineflower)"
)
@app_commands.choices(engine=[
    app_commands.Choice(name="Auto (Önce CFR, hata durumunda Vineflower)", value="auto"),
    app_commands.Choice(name="Vineflower (Gelişmiş Decompiler & Member Renamer)", value="vineflower"),
    app_commands.Choice(name="CFR (Agresif Anti-Obf & Decompile Motoru)", value="cfr")
])
async def jardeobfuscationsrc(
    interaction: discord.Interaction,
    file: discord.Attachment,
    engine: app_commands.Choice[str] = None
):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum limit: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid   = str(uuid.uuid4())[:8]
    inp   = TEMP_DIR  / f"{tid}_{safe_filename(file.filename)}"
    clean = OUTPUT_DIR / f"{tid}_deobfuscated_{safe_filename(file.filename)}"
    src   = OUTPUT_DIR / f"{tid}_secure_source.zip"
    chosen_engine = engine.value if engine else DEFAULT_DEOBF_ENGINE

    tracker = LiveProgressTracker(interaction, "⚡ SuS DeobfSrc — Kaynak Kod Çıkarılıyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    start_time = time.time()
    try:
        await file.save(inp)
        ret, stdout, stderr = await run_engine("deobfuscate", inp, clean, src, chosen_engine)
        elapsed = round(time.time() - start_time, 2)

        if tracker:
            await tracker.stop()

        if ret != 0 or not src.exists():
            log = redact_urls(stdout + stderr)[:1000]
            return await safe_followup(interaction, f"❌ DeObfuscation / Kaynak Kod Çıkarma Hatası:\n```\n{log}\n```")

        if src.stat().st_size > 24.5 * 1024 * 1024:
            return await safe_followup(interaction, f"⚠️ Kaynak kodu ZIP arşivi (`{src.stat().st_size / (1024*1024):.1f} MB`) Discord'un 25MB sınırını aşıyor!")

        e = mk_embed("⚡ SuS Deobfuscator — Java Kaynak Kodu Arşivi",
            f"**`{file.filename}`** modunun şifrelemesi çözüldü ve asıl **.java** kaynak kodları çıkarıldı! (⏱️ `{elapsed}s`)\n\n"
            "📦 **Arşiv İçeriği:**\n"
            "• Tüm temizlenmiş `.java` sınıfları\n"
            "• `fabric.mod.json`, `mods.toml`, `assets/` ve `data/` varlıkları\n"
            "• `DEOBFUSCATION_REPORT.md` ayrıntılı analiz raporu", 0x1ABC9C)

        e.add_field(name="⚙️ Decompiler Tercihi", value=f"`{chosen_engine.upper()}`", inline=True)
        e.add_field(name="📦 Arşiv Boyutu", value=f"`{src.stat().st_size / 1024:.1f} KB`", inline=True)

        dfile = discord.File(src, filename=f"source_code_{safe_filename(file.filename).replace('.jar', '')}.zip")
        try:
            await safe_followup(interaction, embed=e, file=dfile)
        finally:
            close_discord_files([dfile])
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp, clean, src]:
            safe_unlink(_f)


@bot.tree.command(name="deobfuscationsrc", description="⚡ JAR modunun şifresini çözüp tüm .java kaynak kodlarını ve raporu ZIP olarak verir.")
@app_commands.describe(
    file="Şifresi çözülecek ve .java kaynak kodları çıkarılacak .jar dosyası",
    engine="Decompiler motor tercihi (Auto / CFR / Vineflower)"
)
@app_commands.choices(engine=[
    app_commands.Choice(name="Auto (Önce CFR, hata durumunda Vineflower)", value="auto"),
    app_commands.Choice(name="Vineflower (Gelişmiş Decompiler & Member Renamer)", value="vineflower"),
    app_commands.Choice(name="CFR (Agresif Anti-Obf & Decompile Motoru)", value="cfr")
])
async def deobfuscationsrc(
    interaction: discord.Interaction,
    file: discord.Attachment,
    engine: app_commands.Choice[str] = None
):
    await jardeobfuscationsrc.callback(interaction, file, engine)


@bot.tree.command(name="jarcracker", description="🔓 [SuS Cracker v4.1] JAR'ı 43 metotla crack et — Meteor/Fabric/Client/Forge destekli.")
@app_commands.describe(
    file="Crack edilecek .jar dosyası",
    target="Hedef platform türü (varsayılan: any)"
)
@app_commands.choices(target=[
    app_commands.Choice(name="🌠 Any (Genel — Tüm Tipler)",                   value="any"),
    app_commands.Choice(name="☄️ Meteor Client Addon",                         value="meteor"),
    app_commands.Choice(name="🧵 Fabric Mod (1.20.x / 1.21.x)",               value="fabric"),
    app_commands.Choice(name="🔧 Forge Mod (1.12 / 1.16 / 1.20+)",            value="forge"),
    app_commands.Choice(name="🌙 Lunar Client / Badlion / LabyMod / Impact",   value="client"),
])
async def jarcracker(
    interaction: discord.Interaction,
    file: discord.Attachment,
    target: app_commands.Choice[str] = None
):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum limit: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    chosen_target = target.value if target else "any"
    target_labels = {
        "any":    "🌠 Any (Genel)",
        "meteor": "☄️ Meteor Client Addon",
        "fabric": "🧵 Fabric Mod",
        "forge":  "🔧 Forge Mod",
        "client": "🌙 Lunar / Badlion / LabyMod / Impact",
    }
    target_label = target_labels.get(chosen_target, chosen_target.upper())

    await safe_defer(interaction, thinking=True)
    tid = str(uuid.uuid4())[:8]
    inp = TEMP_DIR  / f"{tid}_{safe_filename(file.filename)}"
    out = OUTPUT_DIR / f"{tid}_cracked_{safe_filename(file.filename)}"

    tracker = LiveProgressTracker(interaction, f"🔓 SuS Cracker — {target_label} İşleniyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    start_time = time.time()
    try:
        await file.save(inp)
        ret, stdout, stderr = await run_engine("crack", inp, out, chosen_target)
        elapsed = round(time.time() - start_time, 2)

        if tracker:
            await tracker.stop()

        if ret != 0 or not out.exists():
            log_err = redact_urls(stdout + stderr)[:1200]
            return await safe_followup(interaction, f"❌ Crack hatası:\n```\n{log_err}\n```")

        lines = [l for l in stdout.strip().splitlines() if l.strip().startswith("*") or l.strip().startswith("[")]
        patched_count_line = next((l for l in stdout.splitlines() if "Patches Applied" in l or "Methods Applied" in l), "")
        classes_line       = next((l for l in stdout.splitlines() if "Classes Scanned" in l), "")
        patched_num  = int(patched_count_line.split(":")[-1].strip()) if patched_count_line and patched_count_line.split(":")[-1].strip().isdigit() else len(lines)
        scanned_num  = int(classes_line.split(":")[-1].strip()) if classes_line and classes_line.split(":")[-1].strip().isdigit() else 0

        cats = {
            "🔐 Auth & License":    [],
            "🌐 Network & API":     [],
            "🛡️ Anti-Analysis":    [],
            "🎭 Cosmetic & VIP":    [],
            "⚙️ System & Module":   [],
            "🧬 Bytecode & Struct": [],
        }
        for l in lines:
            ll = l.lower()
            if any(k in ll for k in ["hwid", "keyauth", "license", "auth", "reflect", "static field", "heuristic", "threadlocal", "annotation", "class.forname"]):
                cats["🔐 Auth & License"].append(l.strip())
            elif any(k in ll for k in ["webhook", "http", "endpoint", "cloud", "ws", "ip", "grabber", "keyauth api"]):
                cats["🌐 Network & API"].append(l.strip())
            elif any(k in ll for k in ["anti-vm", "anti-debug", "stacktrace", "agent", "cert", "freeze", "runtimeexec"]):
                cats["🛡️ Anti-Analysis"].append(l.strip())
            elif any(k in ll for k in ["vip", "cosmetic", "premium", "baritone", "scape", "badge", "ranked", "subscriber"]):
                cats["🎭 Cosmetic & VIP"].append(l.strip())
            elif any(k in ll for k in ["module", "meteor", "exit", "config", "json", "timer", "scheduler", "fabric", "forge", "native", "hash"]):
                cats["⚙️ System & Module"].append(l.strip())
            else:
                cats["🧬 Bytecode & Struct"].append(l.strip())

        max_methods = 43
        pct = min(patched_num / max(max_methods, 1), 1.0)
        filled = int(pct * 22)
        bar = "▓" * filled + "░" * (22 - filled)
        bar_pct = int(pct * 100)

        out_size_kb = out.stat().st_size / 1024
        orig_size_kb = file.size / 1024
        over_limit = out.stat().st_size > 24.5 * 1024 * 1024

        e = mk_embed(
            "🔓 SuS Cracker v4.1 — Crack Tamamlandı!",
            f"**`{file.filename}`** başarıyla crack edildi! ⏱️ `{elapsed}s`\n"
            f"**Hedef Platform:** {target_label}\n"
            f"**Toplam Patch:** `{patched_num}` uygulama\n"
            f"**Tarama:** `{scanned_num if scanned_num else 'N/A'}` class\n\n"
            f"**İlerleme:**\n`{bar}` `{bar_pct}%` — `{patched_num}`/`{max_methods}` metot aktif",
            0xF1C40F
        )

        methods_table = (
            "```"
            "M01 HWID Spoof      │ M02 KeyAuth Strip  │ M03 Webhook NOP\n"
            "M04 Anti-VM         │ M05 Anti-Debug     │ M06 Meteor Lock\n"
            "M07 TimeBomb        │ M08 HTTP 200 Mock  │ M09 Hash Bypass\n"
            "M10 Native NOP      │ M11 VIP Unlock     │ M12 Cloud Auth\n"
            "M13 Exit Defuse     │ M14 IP Blacklist   │ M15 Discord Role\n"
            "M16 JavaAgent       │ M17 Cosmetics Unlk │ M18 Freeze NOP\n"
            "M19 CertPin         │ M20 Baritone Allow │ M21 Token Secure\n"
            "M22 RSA/AES Bypass  │ M23 WS Auth        │ M24 Config Key\n"
            "M25 Watermark Strip │ M26 Smart HWID     │ M27 Mixin Hook\n"
            "M28 Branch Reversal │ M29 JSON Meta Clean│ M30 KeyAuth Mock\n"
            "M31 Reflect Auth    │ M32 Static Flag    │ M33 Timer Kill\n"
            "M34 StackTrace Defz │ M35 Heuristic Auth │ M36 Meteor Ext\n"
            "M37 Client Premium  │ M38 Forge VerLock  │ M39 ProcBld Deep\n"
            "M40 forName Bypass  │ M41 ThreadLocal    │ M42 URL Redir\n"
            "M43 Annotation Strip"
            "```"
        )
        e.add_field(name="🗂️ Crack Metot Tablosu (43/43)", value=methods_table, inline=False)

        cat_lines = []
        for cat_name, cat_items in cats.items():
            if cat_items:
                cat_lines.append(f"**{cat_name}** — `{len(cat_items)}` patch")
        if cat_lines:
            e.add_field(name="📊 Kategori Dağılımı", value="\n".join(cat_lines), inline=True)

        e.add_field(name="📁 Dosya Bilgisi",
            value=f"• Orijinal: `{orig_size_kb:.1f} KB`\n"
                  f"• Crack'li: `{out_size_kb:.1f} KB`\n"
                  f"• Platform: `{chosen_target.upper()}`",
            inline=True)

        visible_patches = [redact_urls(l.strip().lstrip("* ")) for l in lines if l.strip()][-12:]
        if visible_patches:
            patch_log_str = "\n".join(visible_patches)
            if len(patch_log_str) > 900:
                patch_log_str = patch_log_str[:900] + "\n..."
            e.add_field(name="📋 Son Patch Logu", value=f"```\n{patch_log_str}\n```", inline=False)
        else:
            raw_log = redact_urls(stdout.strip() + stderr.strip())[-600:]
            if raw_log:
                e.add_field(name="📋 Engine Çıktısı", value=f"```\n{raw_log}\n```", inline=False)

        if over_limit:
            e.add_field(name="⚠️ Uyarı",
                value=f"Crack'li dosya (`{out.stat().st_size / (1024*1024):.1f} MB`) Discord'un 25MB limitini aşıyor!",
                inline=False)
            await safe_followup(interaction, embed=e)
        else:
            dfile = discord.File(out, filename=f"cracked_{safe_filename(file.filename)}")
            try:
                await safe_followup(interaction, embed=e, file=dfile)
            finally:
                close_discord_files([dfile])

    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp, out]:
            safe_unlink(_f)


class CleanScanView(discord.ui.View):
    def __init__(self, filename: str):
        super().__init__(timeout=180)
        self.filename = filename

    @discord.ui.button(label="🧹 Dosyayı Zararlılardan Temizle", style=discord.ButtonStyle.danger, custom_id="sus_clean_scan_btn")
    async def clean_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            f"💡 `{self.filename}` dosyasını temizlemek için `/jarclear` komutunu kullanarak dosyayı yükleyebilirsin!",
            ephemeral=True
        )


@bot.tree.command(name="jarscanner", description="🔍 JAR dosyasını malware/RAT/SilentNet için tara (SilentNet bulursa otomatik temizler).")
@app_commands.describe(file="Taranacak .jar dosyası")
async def jarscanner(interaction: discord.Interaction, file: discord.Attachment):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum limit: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid = str(uuid.uuid4())[:8]
    sname = safe_filename(file.filename)
    inp = TEMP_DIR / f"{tid}_{sname}"
    out = None

    tracker = LiveProgressTracker(interaction, "🔍 SuS Scanner — Tehdit Taraması Yapılıyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    try:
        await file.save(inp)
        ret, stdout, stderr = await run_engine("scan", inp)
        if tracker:
            await tracker.stop()

        raw = stdout or stderr or ""
        output = redact_urls(raw)

        # ── SilentNet imza ayrıştırma ──
        silentnet = "[SILENTNET_DETECT]" in raw
        github_hits = [l.replace("[GITHUB_PAYLOAD]", "").strip()
                       for l in raw.splitlines() if "[GITHUB_PAYLOAD]" in l]
        encrypted_hits = [l.replace("[ENCRYPTED_CLASS]", "").strip()
                          for l in raw.splitlines() if "[ENCRYPTED_CLASS]" in l]

        if silentnet:
            # Otomatik temizlik: github payload'u sil + fabric.mod.json onar
            out = OUTPUT_DIR / f"{tid}_cleaned_{sname}"
            ret2, stdout2, stderr2 = await run_engine(
                "clean", inp, out, "http://127.0.0.1:9999/cleaned_webhook")
            removed, fabric_fixed = 0, False
            m = re.search(r"\[SILENTNET_CLEANED\]\s*removed=(\d+)\s*fabricPatched=(true|false)",
                          stdout2 or "")
            if m:
                removed, fabric_fixed = int(m.group(1)), m.group(2) == "true"

            e = mk_embed("🚨 SILENTNET DETECT — Zararlı Temizlendi",
                         f"**`{sname[:100]}`** içinde SilentNet stealer payload'u tespit edildi ve etkisiz hale getirildi!",
                         DANGER_COLOR)
            e.add_field(name="📁 Zararlı Klasör",
                        value=f"`github/` — `{len(github_hits)}` girdi", inline=True)
            e.add_field(name="🔒 Şifreli Class",
                        value=f"`{len(encrypted_hits)}` adet", inline=True)
            e.add_field(name="🧹 Temizlenen",
                        value=f"`{removed}` dosya silindi", inline=True)
            e.add_field(name="🧵 fabric.mod.json",
                        value="✅ Onarıldı (github referansları temizlendi)" if fabric_fixed else "ℹ️ Değişiklik gerekmedi",
                        inline=True)
            shown = [f"`{redact_urls(g)[:80]}`" for g in github_hits[:10]]
            if len(github_hits) > 10:
                shown.append(f"*+{len(github_hits) - 10} girdi daha…*")
            if shown:
                e.add_field(name="🗑️ Silinen Payload", value="\n".join(shown)[:1000], inline=False)
            shown_enc = [f"`{redact_urls(x)[:80]}`" for x in encrypted_hits[:10]]
            if len(encrypted_hits) > 10:
                shown_enc.append(f"*+{len(encrypted_hits) - 10} class daha…*")
            if shown_enc:
                e.add_field(name="🔐 Şifreli Classlar", value="\n".join(shown_enc)[:1000], inline=False)

            if ret2 == 0 and out.exists() and out.stat().st_size <= 24.5 * 1024 * 1024:
                dfile = discord.File(out, filename=f"cleaned_{sname}")
                try:
                    await safe_followup(interaction, embed=e, file=dfile)
                finally:
                    close_discord_files([dfile])
            elif ret2 == 0 and out.exists():
                e.add_field(name="⚠️ Uyarı",
                            value=f"Temizlenmiş dosya (`{out.stat().st_size / (1024*1024):.1f} MB`) Discord'un 25MB sınırını aşıyor!",
                            inline=False)
                await safe_followup(interaction, embed=e)
            else:
                log = redact_urls((stdout2 + stderr2))[:800]
                e.add_field(name="⚠️ Temizlik Notu",
                            value=f"Otomatik temizlik tamamlanamadı, `/jarclear` ile tekrar dene.\n```\n{log}\n```",
                            inline=False)
                await safe_followup(interaction, embed=e)
            return

        has_threats = any(w in output for w in ("MALICIOUS", "HIGH RISK", "RAT", "STEAL", "CRITICAL", "HIGH", "SUSPICIOUS"))
        color = DANGER_COLOR if has_threats else INFO_COLOR
        e = mk_embed("🔍 SuS Scanner Raporu", f"`{sname[:100]}`", color)
        chunks = [output[i:i+900] for i in range(0, min(len(output), 3600), 900)]
        for idx, chunk in enumerate(chunks[:4]):
            e.add_field(name=f"📋 Sonuç{' (devam)' if idx else ''}", value=f"```\n{chunk}\n```", inline=False)

        if has_threats:
            e.add_field(name="💡 Öneri", value="Aşağıdaki **🧹 Dosyayı Zararlılardan Temizle** butonunu veya `/jarclear` komutunu kullanarak zararlı kodları etkisiz hale getirebilirsin!", inline=False)
            await safe_followup(interaction, embed=e, view=CleanScanView(sname))
        else:
            await safe_followup(interaction, embed=e)
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        safe_unlink(inp)
        if out is not None:
            safe_unlink(out)


# ─── B. CANLI WEBHOOK HONEYPOT (TUZAK LOGGER AVCISI) ──────────────────────────
@bot.tree.command(
    name="jarclear",
    description="🧹 JAR'daki Webhook, RAT ve IP Grabber kodlarını temizler ve tuzağa/honeypot'a yönlendirir."
)
@app_commands.describe(
    file="Temizlenecek .jar dosyası",
    honeypot_url="İsteğe bağlı: Webhook'ların yönlendirileceği tuzak URL (varsayılan: dahili honeypot)"
)
async def jarclear(
    interaction: discord.Interaction,
    file: discord.Attachment,
    honeypot_url: str = None
):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum limit: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid = str(uuid.uuid4())[:8]
    inp = TEMP_DIR / f"{tid}_{safe_filename(file.filename)}"
    out = OUTPUT_DIR / f"{tid}_cleaned_{safe_filename(file.filename)}"

    tracker = LiveProgressTracker(interaction, "🧹 SuS Honeypot Cleaner — Zararlılar Avlanıyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    try:
        await file.save(inp)
        if honeypot_url and honeypot_url.strip():
            cand = honeypot_url.strip()
            target_hp = cand if is_safe_http_url(cand) else "http://127.0.0.1:9999/cleaned_webhook"
        else:
            target_hp = "http://127.0.0.1:9999/cleaned_webhook"
        ret, stdout, stderr = await run_engine("clean", inp, out, target_hp)

        if tracker:
            await tracker.stop()

        if ret != 0 or not out.exists():
            log = redact_urls(stdout + stderr)[:1000]
            return await safe_followup(interaction, f"❌ Temizleme hatası:\n```\n{log}\n```")

        # Parse exposed webhooks from stdout
        exposed_webhooks = []
        for line in stdout.splitlines():
            if "[EXPOSED_WEBHOOK]" in line:
                wh = line.replace("[EXPOSED_WEBHOOK]", "").strip()
                if wh and wh not in exposed_webhooks:
                    exposed_webhooks.append(wh)

        # Motor sayaçları (dürüst rapor için)
        purged, b64purged, susp_count, silent_count = 0, 0, 0, 0
        m_purged = re.search(r"Threats Purged:\s*(\d+)", stdout)
        if m_purged:
            purged = int(m_purged.group(1))
        m_b64 = re.search(r"Obfuscated \(Base64\) Neutralized:\s*(\d+)", stdout)
        if m_b64:
            b64purged = int(m_b64.group(1))
        m_susp = re.search(r"Suspicious Methods:\s*(\d+)", stdout)
        if m_susp:
            susp_count = int(m_susp.group(1))
        m_sil = re.search(r"\[SILENTNET_CLEANED\]\s*removed=(\d+)", stdout)
        if m_sil:
            silent_count = int(m_sil.group(1))
        susp_methods = [l.replace("[SUSPICIOUS_METHOD]", "").strip()
                        for l in stdout.splitlines() if "[SUSPICIOUS_METHOD]" in l]

        disp_name = safe_filename(file.filename)[:100]

        # Hiçbir şey bulunamadıysa sahte başarı yazma
        if purged == 0 and silent_count == 0 and susp_count == 0:
            e = mk_embed("✅ SuS Cleaner — Temiz Görünüyor",
                f"**`{disp_name}`** tarandı, bilinen zararlı gösterge bulunamadı — işlem yapılmadı, jar aynen iletiliyor.",
                SUCCESS_COLOR)
            dfile = discord.File(inp, filename=f"checked_{safe_filename(file.filename)}")
            try:
                await safe_followup(interaction, embed=e, file=dfile)
            finally:
                close_discord_files([dfile])
            return

        e = mk_embed("🧹 SuS Cleaner & Honeypot — Zararlı Kodlar Temizlendi",
            f"**`{disp_name}`** dosyasındaki tehditler etkisiz hale getirildi ve güvenli hale getirildi!\n\n"
            f"• **Honeypot Hedefi:** `{redact_urls(target_hp)}`\n"
            f"• **Etkisiz Hale Getirilen:** `{purged}` tehdit"
            + (f" (`{b64purged}` Base64-gizli)" if b64purged else "")
            + (f"\n• **SilentNet Payload:** `{silent_count}` dosya silindi" if silent_count else "")
            + (f"\n• **Şüpheli Metot:** `{susp_count}` (elle incele)" if susp_count else ""),
            SUCCESS_COLOR)

        # Webhook Honeypot Exposed Alert
        if exposed_webhooks:
            wh_report = []
            for wh in exposed_webhooks:
                # Saldırgan webhook'unun tamamını asla kanala basma — sadece maskeli ID.
                # NOT: ID ham URL'den çıkarılmalı (redact sonrası çıkarılamaz).
                masked_wh = "[redacted-url]"
                try:
                    m_id = re.search(r"/webhooks/(\d+)/", wh)
                    if m_id:
                        masked_wh = f"https://discord.com/api/webhooks/{m_id.group(1)}/********************"
                except Exception:
                    masked_wh = "[redacted-url]"
                wh_report.append(f"• 🚨 **Saldırgan Webhook:** `{masked_wh}`")

            e.add_field(
                name="🕵️ [LOGGER AVCISI] İfşa Edilen Saldırgan Webhook'ları",
                value=f"`{len(exposed_webhooks)}` adet zararlı webhook etkisiz hale getirildi.\n" + "\n".join(wh_report) + "\n\n💡 *Bu webhook'lar devre dışı bırakıldı ve tuzak loglayıcımıza yönlendirildi.*",
                inline=False
            )

            # Send Security Alert to MOD_LOG_CHANNEL
            if MOD_LOG_CHANNEL and interaction.guild is not None:
                try:
                    m_ch = interaction.guild.get_channel(MOD_LOG_CHANNEL)
                    if m_ch:
                        log_e = mk_embed("🚨 Tehdit Yakalandı: Discord Webhook Logger",
                            f"**Kullanıcı:** {interaction.user.mention} ({interaction.user.id})\n"
                            f"**Dosya:** `{disp_name}`\n"
                            f"**Yakalanan webhook sayısı:** `{len(exposed_webhooks)}`",
                            DANGER_COLOR)
                        await m_ch.send(embed=log_e)
                except Exception:
                    pass

        clean_log = redact_urls(stdout.strip())[-500:] if stdout.strip() else "Temizleme logu yok."
        e.add_field(name="📋 Temizleme Raporu", value=f"```\n{clean_log}\n```", inline=False)

        if susp_methods:
            shown_s = [f"`{redact_urls(s)[:80]}`" for s in susp_methods[:10]]
            if len(susp_methods) > 10:
                shown_s.append(f"*+{len(susp_methods) - 10} metot daha…*")
            e.add_field(name="🕵️ Şüpheli Metotlar (Temizlenemedi — Elle İncele)",
                        value="\n".join(shown_s)[:1000], inline=False)

        dfile = discord.File(out, filename=f"cleaned_{safe_filename(file.filename)}")
        try:
            await safe_followup(interaction, embed=e, file=dfile)
        finally:
            close_discord_files([dfile])
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp, out]:
            safe_unlink(_f)


# ─── C. GELİŞTİRİCİLER İÇİN LİSANS & KORUMA ENJEKTÖRÜ (/jarprotect) ───────────
@bot.tree.command(
    name="jarprotect",
    description="🛡️ Geliştirici Koruma Enjektörü: JAR moduna HWID kilidi, Süre Sınırı ve Güvenlik Kalkanı ekle."
)
@app_commands.describe(
    file="Lisans ve koruma kalkanı enjekte edilecek .jar dosyası",
    hwid="Kilitlenecek donanım kimliği / kullanıcı adı (boş bırakılabilir)",
    expire_days="Lisans geçerlilik süresi (gün olarak, örn: 30 gün sonra patlar)",
    alarm_webhook="İhlal durumunda uyarı gönderecek Discord Webhook URL'si"
)
async def jarprotect(
    interaction: discord.Interaction,
    file: discord.Attachment,
    hwid: str = None,
    expire_days: int = None,
    alarm_webhook: str = None
):
    if not file.filename.lower().endswith((".jar", ".zip")):
        return await interaction.response.send_message("❌ Geçerli bir `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok, msg = await verify_user_quota(interaction, file)
        if not ok:
            return await interaction.response.send_message(msg, ephemeral=True)
    elif not check_file_size(file):
        return await interaction.response.send_message(f"❌ Dosya çok büyük! Maksimum limit: `{MAX_FILE_SIZE_MB} MB`", ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid = str(uuid.uuid4())[:8]
    inp = TEMP_DIR / f"{tid}_{safe_filename(file.filename)}"
    out = OUTPUT_DIR / f"{tid}_protected_{safe_filename(file.filename)}"

    tracker = LiveProgressTracker(interaction, "🛡️ SuS Protector — Lisans Kalkanı Enjekte Ediliyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    start_time = time.time()
    try:
        await file.save(inp)

        # Calculate epoch expiry (0-3650 gün clamp)
        expiry_epoch = 0
        if expire_days and expire_days > 0:
            expire_days = min(expire_days, 3650)
            expiry_epoch = int((time.time() + (expire_days * 86400)) * 1000)

        target_hwid = sanitize_hwid(hwid)
        if alarm_webhook and alarm_webhook.strip():
            cand = alarm_webhook.strip()
            wh_alarm = cand if is_safe_http_url(cand) else "NONE"
        else:
            wh_alarm = "NONE"

        ret, stdout, stderr = await run_engine("protect", inp, out, target_hwid, str(expiry_epoch), wh_alarm)
        elapsed = round(time.time() - start_time, 2)

        if tracker:
            await tracker.stop()

        if ret != 0 or not out.exists():
            log = redact_urls(stdout + stderr)[:1000]
            return await safe_followup(interaction, f"❌ Koruma enjeksiyonu hatası:\n```\n{log}\n```")

        disp_name = safe_filename(file.filename)[:100]
        e = mk_embed("🛡️ SuS Protector v4.1 — Lisans Kalkanı Enjekte Edildi",
            f"**`{disp_name}`** başarıyla koruma altına alındı ve lisans kalkanı entegre edildi! (⏱️ `{elapsed}s`)\n\n"
            "🔒 **Enjekte Edilen Güvenlik Özellikleri:**\n"
            f"• **HWID / Kullanıcı Kilidi:** `{target_hwid if target_hwid != 'NONE' else 'Devre Dışı'}`\n"
            f"• **TimeBomb Süre Kilidi:** `{f'{expire_days} Gün Sonra' if expire_days else 'Sınırsız / Yok'}`\n"
            f"• **Alarm Bildirim Ağı:** `{'Aktif Webhook' if wh_alarm != 'NONE' else 'Bağlanmadı'}`\n"
            "• **Anti-Tamper & Entrypoint Muhafızı:** Sınıf yüklenişinde yetkisiz çalıştırmayı doğrudan sonlandırır.",
            0x2ECC71)

        e.add_field(name="📊 Dosya Bilgileri",
            value=f"• Orijinal Boyut: `{file.size / 1024:.1f} KB`\n"
                  f"• Korumalı Boyut: `{out.stat().st_size / 1024:.1f} KB`", inline=True)

        dfile = discord.File(out, filename=f"protected_{safe_filename(file.filename)}")
        try:
            await safe_followup(interaction, embed=e, file=dfile)
        finally:
            close_discord_files([dfile])
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp, out]:
            safe_unlink(_f)


# ─── D. BYTECODE KARŞILAŞTIRICI (/jardiff) ────────────────────────────────────
@bot.tree.command(
    name="jardiff",
    description="🔍 İki JAR dosyası arasındaki bytecode, sınıf ve yapısal farkları karşılaştırıp raporlar."
)
@app_commands.describe(
    original_file="Orijinal .jar dosyası",
    modified_file="Değiştirilmiş / Cracklenmiş / Temizlenmiş .jar dosyası"
)
async def jardiff(
    interaction: discord.Interaction,
    original_file: discord.Attachment,
    modified_file: discord.Attachment
):
    if not (original_file.filename.lower().endswith((".jar", ".zip")) and modified_file.filename.lower().endswith((".jar", ".zip"))):
        return await interaction.response.send_message("❌ Lütfen iki adet geçerli `.jar` dosyası yükle!", ephemeral=True)

    if DB_AVAILABLE:
        ok1, msg1 = await verify_user_quota(interaction, original_file)
        if not ok1:
            return await interaction.response.send_message(msg1, ephemeral=True)

    await safe_defer(interaction, thinking=True)
    tid = str(uuid.uuid4())[:8]
    inp1 = TEMP_DIR / f"{tid}_orig_{safe_filename(original_file.filename)}"
    inp2 = TEMP_DIR / f"{tid}_mod_{safe_filename(modified_file.filename)}"
    rep  = OUTPUT_DIR / f"diff_report_{tid}.txt"

    tracker = LiveProgressTracker(interaction, "🔍 SuS Differ — Bytecode Karşılaştırılıyor") if DB_AVAILABLE else None
    if tracker:
        await tracker.start()

    start_time = time.time()
    try:
        await original_file.save(inp1)
        await modified_file.save(inp2)

        ret, stdout, stderr = await run_engine("diff", inp1, inp2, rep)
        elapsed = round(time.time() - start_time, 2)

        if tracker:
            await tracker.stop()

        diff_summary = redact_urls(stdout.strip())[-1000:] if stdout.strip() else "Karşılaştırma tamamlandı."

        e = mk_embed("🔍 SuS Differ — JAR Karşılaştırma Raporu",
            f"**`{original_file.filename}`** ➔ **`{modified_file.filename}`** (⏱️ `{elapsed}s`)\n\n"
            f"```\n{diff_summary}\n```", 0x3498DB)

        e.add_field(name="📊 Boyut Karşılaştırması",
            value=f"• Orijinal: `{original_file.size / 1024:.1f} KB`\n"
                  f"• Değiştirilmiş: `{modified_file.size / 1024:.1f} KB`\n"
                  f"• Fark: `{(modified_file.size - original_file.size) / 1024:+.1f} KB`", inline=True)

        if rep.exists():
            dfile = discord.File(rep, filename=f"diff_report_{safe_filename(original_file.filename)}.txt")
            try:
                await safe_followup(interaction, embed=e, file=dfile)
            finally:
                close_discord_files([dfile])
        else:
            await safe_followup(interaction, embed=e)
    except Exception as exc:
        if tracker:
            await tracker.stop()
        await safe_followup(interaction, f"❌ Beklenmedik hata: {exc}")
    finally:
        for _f in [inp1, inp2, rep]:
            safe_unlink(_f)


# ─── VERIFY PANEL ─────────────────────────────
@bot.tree.command(name="verify-panel", description="✅ Doğrulama panelini kanalına gönder.")
@app_commands.default_permissions(manage_guild=True)
async def verify_panel(interaction: discord.Interaction):
    if interaction.guild is None or interaction.channel is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    now = discord.utils.utcnow().strftime("%d.%m.%Y %H:%M")
    e = discord.Embed(title="✅ DOĞRULAMA",
        description="Sunucudaki kanallara erişim sağlamak ve bot korumasını geçmek için aşağıdaki butona tıklayın.",
        color=SUCCESS_COLOR)
    e.set_footer(text=f"Decompleder Platform • Enterprise Security • {now}")
    try:
        await interaction.channel.send(embed=e, view=VerifyButton())
    except discord.Forbidden:
        return await interaction.response.send_message("❌ Bu kanala mesaj gönderme yetkim yok.", ephemeral=True)
    except discord.HTTPException:
        return await interaction.response.send_message("❌ Panel gönderilemedi, tekrar dene.", ephemeral=True)
    await interaction.response.send_message("✅ Doğrulama paneli gönderildi.", ephemeral=True)

# ─── TICKET PANEL ─────────────────────────────
@bot.tree.command(name="ticket-panel", description="🎫 Ticket destek panelini kanalına gönder.")
@app_commands.default_permissions(manage_guild=True)
async def ticket_panel(interaction: discord.Interaction):
    if interaction.guild is None or interaction.channel is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    e = mk_embed("🎫 Destek Sistemi",
        "Herhangi bir sorun, öneri veya satın alma talebi için ticket aç!\n\n"
        "💡 **Nasıl çalışır?**\n"
        "1. Aşağıdaki butona tıkla\n"
        "2. Sana özel bir kanal açılır\n"
        "3. Destek ekibi seninle ilgilenir", BRAND_COLOR)
    try:
        await interaction.channel.send(embed=e, view=TicketView())
    except discord.Forbidden:
        return await interaction.response.send_message("❌ Bu kanala mesaj gönderme yetkim yok.", ephemeral=True)
    except discord.HTTPException:
        return await interaction.response.send_message("❌ Panel gönderilemedi, tekrar dene.", ephemeral=True)
    await interaction.response.send_message("✅ Ticket paneli gönderildi.", ephemeral=True)

# ─── GIVEAWAY ─────────────────────────────────
@bot.tree.command(name="giveaway", description="🎁 Çekiliş başlat.")
@app_commands.describe(sure="Kaç dakika sürsün", odul="Ödül nedir", kazanan="Kaç kişi kazanacak")
@app_commands.default_permissions(manage_guild=True)
async def giveaway(interaction: discord.Interaction, sure: int, odul: str, kazanan: int = 1):
    if sure < 1 or sure > 10080:
        return await interaction.response.send_message("❌ Süre 1-10080 dakika arasında olmalı.", ephemeral=True)

    ends_at  = discord.utils.utcnow() + datetime.timedelta(minutes=sure)
    channel  = interaction.channel

    e = mk_embed(f"🎁 ÇEKİLİŞ: {odul}",
        f"🏆 **Ödül:** {odul}\n"
        f"👥 **Kazanan Sayısı:** {kazanan}\n"
        f"⏰ **Bitiş:** <t:{int(ends_at.timestamp())}:R>\n\n"
        "Katılmak için **🎉** reaksiyonu bırak!",
        0xF39C12)
    e.set_footer(text=f"Düzenleyen: {interaction.user.display_name} | {FOOTER_TEXT}")

    msg = await channel.send(embed=e)
    await msg.add_reaction("🎉")
    await interaction.response.send_message("✅ Çekiliş başlatıldı!", ephemeral=True)

    g_data = load_json("giveaways")
    g_data[str(msg.id)] = {
        "channel": channel.id, "ends_at": ends_at.timestamp(),
        "winners": kazanan, "prize": odul
    }
    save_json("giveaways", g_data)

    await asyncio.sleep(sure * 60)

    try:
        msg = await channel.fetch_message(msg.id)
    except (discord.NotFound, discord.HTTPException):
        return

    reaction = discord.utils.get(msg.reactions, emoji="🎉")
    if not reaction:
        return await channel.send("❌ Çekilişe kimse katılmadı.")

    users = [u async for u in reaction.users() if not u.bot]
    if not users:
        return await channel.send("❌ Çekilişe kimse katılmadı.")

    winners  = random.sample(users, min(kazanan, len(users)))
    wins_str = ", ".join(w.mention for w in winners)
    e2 = mk_embed("🎊 Çekiliş Sonuçlandı!",
        f"**Ödül:** {odul}\n**Kazanan(lar):** {wins_str}\n\nTebrikler! 🎉", SUCCESS_COLOR)
    await channel.send(embed=e2)

    g_data = load_json("giveaways")
    g_data.pop(str(msg.id), None)
    save_json("giveaways", g_data)

# ─── MODERATION ───────────────────────────────
async def _mod_log(guild: discord.Guild, title: str, desc: str, color: int = DANGER_COLOR):
    if MOD_LOG_CHANNEL:
        ch = guild.get_channel(MOD_LOG_CHANNEL)
        if ch and isinstance(ch, discord.TextChannel):
            try:
                await ch.send(embed=mk_embed(title, desc, color))
            except discord.Forbidden:
                pass


@bot.tree.command(name="ban", description="🔨 Kullanıcıyı sunucudan banla.")
@app_commands.describe(user="Kullanıcı", sebep="Sebep")
@app_commands.default_permissions(ban_members=True)
async def ban_cmd(interaction: discord.Interaction, user: discord.Member, sebep: str = "Belirtilmedi"):
    sebep = (sebep or "Belirtilmedi")[:300]
    if interaction.guild is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    if interaction.guild.owner_id != interaction.user.id and user.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ Bu kullanıcıyı banlayamazsın (daha yüksek/eşit rol).", ephemeral=True)
    if user.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ Botun rolü bu kullanıcıdan düşük, işlem yapamam.", ephemeral=True)
    try:
        await user.ban(reason=f"{sebep} | Yetkili: {interaction.user}")
        await interaction.response.send_message(
            embed=mk_embed("🔨 Ban", f"{user.mention} banlandı.\n**Sebep:** {sebep}", DANGER_COLOR))
        await _mod_log(interaction.guild, "🔨 Ban",
            f"**Kullanıcı:** {user} ({user.id})\n**Yetkili:** {interaction.user}\n**Sebep:** {sebep}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bu kullanıcıyı banlama yetkim yok.", ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Ban işlemi başarısız oldu.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Ban işlemi başarısız oldu.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="kick", description="👢 Kullanıcıyı sunucudan at.")
@app_commands.describe(user="Kullanıcı", sebep="Sebep")
@app_commands.default_permissions(kick_members=True)
async def kick_cmd(interaction: discord.Interaction, user: discord.Member, sebep: str = "Belirtilmedi"):
    sebep = (sebep or "Belirtilmedi")[:300]
    if interaction.guild is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    if interaction.guild.owner_id != interaction.user.id and user.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ Bu kullanıcıyı atamazsın.", ephemeral=True)
    if user.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ Botun rolü bu kullanıcıdan düşük, işlem yapamam.", ephemeral=True)
    try:
        await user.kick(reason=f"{sebep} | Yetkili: {interaction.user}")
        await interaction.response.send_message(
            embed=mk_embed("👢 Kick", f"{user.mention} atıldı.\n**Sebep:** {sebep}", DANGER_COLOR))
        await _mod_log(interaction.guild, "👢 Kick",
            f"**Kullanıcı:** {user} ({user.id})\n**Yetkili:** {interaction.user}\n**Sebep:** {sebep}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bu kullanıcıyı atma yetkim yok.", ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Kick işlemi başarısız oldu.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Kick işlemi başarısız oldu.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="mute", description="🔇 Kullanıcıyı sustur (timeout).")
@app_commands.describe(user="Kullanıcı", dakika="Süre dakika cinsinden (1-40320)", sebep="Sebep")
@app_commands.default_permissions(moderate_members=True)
async def mute_cmd(interaction: discord.Interaction, user: discord.Member, dakika: int = 10, sebep: str = "Belirtilmedi"):
    sebep = (sebep or "Belirtilmedi")[:300]
    if interaction.guild is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    if interaction.guild.owner_id != interaction.user.id and user.top_role >= interaction.user.top_role:
        return await interaction.response.send_message("❌ Bu kullanıcıyı susturamassın.", ephemeral=True)
    if user.top_role >= interaction.guild.me.top_role:
        return await interaction.response.send_message("❌ Botun rolü bu kullanıcıdan düşük, işlem yapamam.", ephemeral=True)
    dakika = max(1, min(dakika, 40320))
    try:
        until = discord.utils.utcnow() + datetime.timedelta(minutes=dakika)
        await user.timeout(until, reason=f"{sebep} | Yetkili: {interaction.user}")
        await interaction.response.send_message(
            embed=mk_embed("🔇 Mute",
                f"{user.mention} **{dakika}dk** susturuldu.\n**Sebep:** {sebep}", DANGER_COLOR))
        await _mod_log(interaction.guild, "🔇 Mute",
            f"**Kullanıcı:** {user} ({user.id})\n**Süre:** {dakika}dk\n"
            f"**Yetkili:** {interaction.user}\n**Sebep:** {sebep}")
    except discord.Forbidden:
        await interaction.response.send_message("❌ Bu kullanıcıyı susturma yetkim yok.", ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Susturma işlemi başarısız oldu.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Susturma işlemi başarısız oldu.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="unmute", description="🔊 Kullanıcının timeout'unu kaldır.")
@app_commands.describe(user="Kullanıcı")
@app_commands.default_permissions(moderate_members=True)
async def unmute_cmd(interaction: discord.Interaction, user: discord.Member):
    if interaction.guild is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    try:
        await user.timeout(None, reason=f"Unmute | Yetkili: {interaction.user}")
        await interaction.response.send_message(
            embed=mk_embed("🔊 Unmute", f"{user.mention} susturması kaldırıldı.", SUCCESS_COLOR))
        await _mod_log(interaction.guild, "🔊 Unmute",
            f"**Kullanıcı:** {user} ({user.id})\n**Yetkili:** {interaction.user}", SUCCESS_COLOR)
    except discord.Forbidden:
        await interaction.response.send_message("❌ Yetkim yok.", ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Unmute işlemi başarısız oldu.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Unmute işlemi başarısız oldu.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="warn", description="⚠️ Kullanıcıya uyarı ver.")
@app_commands.describe(user="Kullanıcı", sebep="Sebep")
@app_commands.default_permissions(manage_messages=True)
async def warn_cmd(interaction: discord.Interaction, user: discord.Member, sebep: str):
    sebep = (sebep or "Belirtilmedi")[:300]
    uid = str(user.id)
    if DB_AVAILABLE:
        count = await add_warn(uid, sebep, str(interaction.user))
    else:
        data = load_json("warns")
        data.setdefault(uid, []).append({
            "sebep": sebep, "yetkili": str(interaction.user), "zaman": time.time()
        })
        save_json("warns", data)
        count = len(data[uid])

    await interaction.response.send_message(
        embed=mk_embed("⚠️ Uyarı",
            f"{user.mention} uyarıldı! (**{count}. uyarı**)\n**Sebep:** {sebep}", 0xE67E22))
    await _mod_log(interaction.guild, "⚠️ Uyarı",
        f"**Kullanıcı:** {user} ({user.id})\n**Uyarı #{count}**\n"
        f"**Sebep:** {sebep}\n**Yetkili:** {interaction.user}", 0xE67E22)


@bot.tree.command(name="warns", description="📋 Kullanıcının uyarılarını görüntüle.")
@app_commands.describe(user="Kullanıcı")
@app_commands.default_permissions(manage_messages=True)
async def warns_cmd(interaction: discord.Interaction, user: discord.Member):
    uid = str(user.id)
    if DB_AVAILABLE:
        warns = await get_warns(uid)
    else:
        warns = load_json("warns").get(uid, [])

    if not warns:
        return await interaction.response.send_message(
            embed=mk_embed("📋 Uyarılar", f"{user.mention} için aktif uyarı bulunamadı.", INFO_COLOR))

    lines = [f"**{i+1}.** {_warn_field(w)[0]} — *{_warn_field(w)[1]}*" for i, w in enumerate(warns)]
    await interaction.response.send_message(
        embed=mk_embed(f"📋 {user.display_name} Uyarıları ({len(warns)})", "\n".join(lines), 0xE67E22))


@bot.tree.command(name="clearwarns", description="🗑️ Kullanıcının tüm uyarılarını temizle.")
@app_commands.describe(user="Kullanıcı")
@app_commands.default_permissions(manage_guild=True)
async def clearwarns_cmd(interaction: discord.Interaction, user: discord.Member):
    uid = str(user.id)
    if DB_AVAILABLE:
        await clear_warns(uid)
    else:
        data = load_json("warns")
        data.pop(uid, None)
        save_json("warns", data)

    await interaction.response.send_message(
        embed=mk_embed("🗑️ Uyarılar Temizlendi", f"{user.mention} kullanıcısının tüm uyarıları silindi.", SUCCESS_COLOR))


@bot.tree.command(name="clear", description="🧹 Kanaldan belirtilen sayıda mesaj sil.")
@app_commands.describe(sayi="Silinecek mesaj sayısı (1-100)")
@app_commands.default_permissions(manage_messages=True)
async def clear_cmd(interaction: discord.Interaction, sayi: int):
    sayi = max(1, min(sayi, 100))
    if interaction.guild is None or interaction.channel is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=sayi)
        await interaction.followup.send(
            embed=mk_embed("🧹 Temizlendi", f"**{len(deleted)}** mesaj silindi.", SUCCESS_COLOR),
            ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ Mesaj silme yetkim yok.", ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        try:
            await interaction.followup.send("❌ Mesajlar silinirken bir hata oluştu.", ephemeral=True)
        except Exception:
            pass


@bot.tree.command(name="unban", description="✅ Kullanıcının banını kaldır.")
@app_commands.describe(user_id="Kullanıcı ID'si")
@app_commands.default_permissions(ban_members=True)
async def unban_cmd(interaction: discord.Interaction, user_id: str):
    if interaction.guild is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    try:
        uid  = int(user_id)
        user = await bot.fetch_user(uid)
        await interaction.guild.unban(user, reason=f"Yetkili: {interaction.user}")
        await interaction.response.send_message(
            embed=mk_embed("✅ Unban", f"**{user}** (`{uid}`) banı kaldırıldı.", SUCCESS_COLOR))
        await _mod_log(interaction.guild, "✅ Unban",
            f"**Kullanıcı:** {user} ({uid})\n**Yetkili:** {interaction.user}", SUCCESS_COLOR)
    except ValueError:
        await interaction.response.send_message("❌ Geçerli bir kullanıcı ID'si gir.", ephemeral=True)
    except discord.NotFound:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Bu ID banlı listesinde bulunamadı.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Bu ID banlı listesinde bulunamadı.", ephemeral=True)
        except Exception:
            pass
    except discord.Forbidden:
        await interaction.response.send_message("❌ Yetkim yok.", ephemeral=True)
    except discord.HTTPException:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Unban işlemi başarısız oldu.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Unban işlemi başarısız oldu.", ephemeral=True)
        except Exception:
            pass

# ─── UTILITY COMMANDS ─────────────────────────
@bot.tree.command(name="ping", description="🏓 Bot gecikmesini göster.")
async def ping_cmd(interaction: discord.Interaction):
    lat   = round(bot.latency * 1000)
    color = SUCCESS_COLOR if lat < 100 else (0xE67E22 if lat < 200 else DANGER_COLOR)
    await interaction.response.send_message(
        embed=mk_embed("🏓 Pong!", f"API Gecikmesi: **{lat}ms**", color))


@bot.tree.command(name="botinfo", description="🤖 Bot hakkında bilgi al.")
async def botinfo_cmd(interaction: discord.Interaction):
    total_members = sum(g.member_count for g in bot.guilds if g.member_count)
    e = mk_embed("🤖 SuS Cracker Bot",
        f"**Sunucu Sayısı:** {len(bot.guilds)}\n"
        f"**Toplam Üye:** {total_members:,}\n"
        f"**API Ping:** {round(bot.latency*1000)}ms\n"
        f"**Versiyon:** v4.1 (Enhanced Edition)\n"
        f"**discord.py:** {discord.__version__}\n\n"
        "**Özellikler:**\n"
        "JAR Obfuscator • DeObfuscator • DeobfSrc • Cracker (43 Metot) • Scanner\n"
        "Webhook Honeypot • JarProtect (Lisans Enjektörü) • JarDiff Karşılaştırıcı\n"
        "Görsel Rank Kartı • Sayfalamalı Leaderboard • SQLite WAL • VIP Öncelik",
        BRAND_COLOR)
    e.set_thumbnail(url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="userinfo", description="👤 Kullanıcı bilgilerini görüntüle.")
@app_commands.describe(user="Kullanıcı (boş bırakırsan kendin)")
async def userinfo_cmd(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    if DB_AVAILABLE:
        user_xp = await get_user_xp(str(user.id))
        warns = await get_warns(str(user.id))
        lvl = user_xp["level"]
        xp = user_xp["xp"]
        warn_c = len(warns)
    else:
        xp_d   = load_json("xp").get(str(user.id), {"xp": 0, "level": 1})
        lvl    = xp_d["level"]
        xp     = xp_d["xp"]
        warn_c = len(load_json("warns").get(str(user.id), []))

    e = mk_embed(f"👤 {user.display_name}", color=BRAND_COLOR)
    e.add_field(name="ID",      value=str(user.id),    inline=True)
    if isinstance(user, discord.Member) and getattr(user, "joined_at", None):
        e.add_field(name="Katılım", value=f"<t:{int(user.joined_at.timestamp())}:R>", inline=True)
    else:
        e.add_field(name="Katılım", value="Sunucu dışında görüntülenemiyor", inline=True)
    e.add_field(name="Kayıt",   value=f"<t:{int(user.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Seviye",  value=f"**{lvl}** (XP: {xp})", inline=True)
    e.add_field(name="Uyarı",   value=str(warn_c), inline=True)
    e.add_field(name="Bot?",    value="✅" if user.bot else "❌", inline=True)
    if isinstance(user, discord.Member):
        try:
            roles_str = " ".join(r.mention for r in reversed(user.roles[1:])) or "Yok"
        except Exception:
            roles_str = "Yok"
        if len(roles_str) > 1000:
            roles_str = roles_str[:1000] + "…"
        e.add_field(name="Roller", value=roles_str, inline=False)
    else:
        e.add_field(name="Roller", value="Sunucu dışında görüntülenemiyor", inline=False)
    e.set_thumbnail(url=user.display_avatar.url)
    await interaction.response.send_message(embed=e)


@bot.tree.command(name="serverinfo", description="🏠 Sunucu bilgilerini görüntüle.")
async def serverinfo_cmd(interaction: discord.Interaction):
    g = interaction.guild
    if g is None:
        return await interaction.response.send_message("❌ Bu komut sadece sunucu içinde kullanılabilir.", ephemeral=True)
    e = mk_embed(f"🏠 {g.name}", color=BRAND_COLOR)
    e.add_field(name="Üye",          value=f"{g.member_count:,}", inline=True)
    e.add_field(name="Kanallar",      value=str(len(g.channels)),  inline=True)
    e.add_field(name="Roller",        value=str(len(g.roles)),     inline=True)
    e.add_field(name="Sahip",         value=g.owner.mention if g.owner else "?", inline=True)
    e.add_field(name="Oluşturulma",   value=f"<t:{int(g.created_at.timestamp())}:R>", inline=True)
    e.add_field(name="Boost Seviyesi",value=f"Level {g.premium_tier} ({g.premium_subscription_count} boost)", inline=True)
    e.add_field(name="Emojis",        value=str(len(g.emojis)), inline=True)
    e.add_field(name="Stickers",      value=str(len(g.stickers)), inline=True)
    if g.icon:
        e.set_thumbnail(url=g.icon.url)
    await interaction.response.send_message(embed=e)


# ─── B. GÖRSEL RANK KARTI (/rank) ─────────────────────────────────────────────
@bot.tree.command(name="rank", description="📈 XP seviyeni ve görsel neon rank kartını görüntüle.")
@app_commands.describe(user="Kullanıcı (boş bırakırsan kendin)")
async def rank_cmd(interaction: discord.Interaction, user: discord.Member = None):
    await safe_defer(interaction, thinking=True)
    target_user = user or interaction.user

    if DB_AVAILABLE:
        user_d = await get_user_xp(str(target_user.id))
        rank_pos = await get_user_rank_position(str(target_user.id))
        lvl    = user_d["level"]
        xp     = user_d["xp"]
        needed = lvl * 100
        try:
            # Pillow Canvas Rank Card Generation
            card_buf = await create_rank_card(
                username=target_user.name,
                avatar_url=target_user.display_avatar.url,
                current_xp=xp,
                needed_xp=needed,
                level=lvl,
                rank_pos=rank_pos
            )
            return await safe_followup(
                interaction,
                file=discord.File(card_buf, filename=f"rank_{target_user.name}.png")
            )
        except Exception as exc:
            print(f"[-] Rank card render error: {exc}")

    # Fallback Embed
    xp_d   = load_json("xp").get(str(target_user.id), {"xp": 0, "level": 1})
    lvl    = xp_d["level"]
    xp     = xp_d["xp"]
    needed = lvl * 100
    pct    = min(xp / needed, 1.0)
    filled = int(pct * 20)
    bar    = "█" * filled + "░" * (20 - filled)
    e = mk_embed(f"📈 {target_user.display_name} — Rank", color=0xF39C12)
    e.add_field(name="Seviye",    value=f"**{lvl}**",        inline=True)
    e.add_field(name="XP",        value=f"{xp} / {needed}", inline=True)
    e.add_field(name="İlerleme",  value=f"`{bar}` {int(pct*100)}%", inline=False)
    e.set_thumbnail(url=target_user.display_avatar.url)
    await safe_followup(interaction, embed=e)


# ─── C. SAYFALAMALI LİDERLİK TABLOSU (/leaderboard) ───────────────────────────
class LeaderboardPaginationView(discord.ui.View):
    def __init__(self, current_page: int, total_pages: int, total_users: int):
        super().__init__(timeout=180)
        self.page = current_page
        self.total_pages = max(1, total_pages)
        self.total_users = total_users
        self._update_btn_states()

    def _update_btn_states(self):
        self.first_btn.disabled = (self.page <= 0)
        self.prev_btn.disabled  = (self.page <= 0)
        self.next_btn.disabled  = (self.page >= self.total_pages - 1)
        self.last_btn.disabled  = (self.page >= self.total_pages - 1)

    async def _render(self, interaction: discord.Interaction):
        self._update_btn_states()
        embed = await self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _build_embed(self) -> discord.Embed:
        offset = self.page * 10
        if DB_AVAILABLE:
            rows, total = await get_leaderboard_data(limit=10, offset=offset)
            self.total_users = total
            self.total_pages = max(1, (total + 9) // 10)
        else:
            xp_data = load_json("xp")
            sorted_all = sorted(
                xp_data.items(),
                key=lambda x: (x[1].get("level", 1), x[1].get("xp", 0)),
                reverse=True
            )
            self.total_users = len(sorted_all)
            self.total_pages = max(1, (self.total_users + 9) // 10)
            rows = [(uid, d.get("xp", 0), d.get("level", 1)) for uid, d in sorted_all[offset:offset+10]]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for idx, (uid, xp, lvl) in enumerate(rows):
            pos = offset + idx + 1
            badge = medals[pos - 1] if pos <= 3 else f"`#{pos:02d}`"
            lines.append(f"{badge} <@{uid}> — **Seviye {lvl}** • `{xp:,} XP`")

        e = mk_embed(
            f"🏆 Sunucu Liderlik Tablosu (Sayfa {self.page + 1}/{self.total_pages})",
            f"Toplam **{self.total_users}** kayıtlı kullanıcı arasından sıralama:\n\n" +
            ("\n".join(lines) if lines else "*Bu sayfada kullanıcı bulunmuyor.*"),
            0xF1C40F
        )
        e.set_footer(text=f"Sayfa {self.page + 1}/{self.total_pages} • Toplam {self.total_users} Üye • SuS XP Sistemi")
        return e

    @discord.ui.button(label="⏮️ İlk", style=discord.ButtonStyle.secondary, custom_id="lb_btn_first")
    async def first_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        await self._render(interaction)

    @discord.ui.button(label="◀️ Geri", style=discord.ButtonStyle.primary, custom_id="lb_btn_prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        await self._render(interaction)

    @discord.ui.button(label="▶️ İleri", style=discord.ButtonStyle.primary, custom_id="lb_btn_next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page < self.total_pages - 1:
            self.page += 1
        await self._render(interaction)

    @discord.ui.button(label="⏭️ Son", style=discord.ButtonStyle.secondary, custom_id="lb_btn_last")
    async def last_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.total_pages - 1
        await self._render(interaction)


@bot.tree.command(name="leaderboard", description="🏆 İnteraktif sayfalamalı XP sıralamasını göster.")
async def leaderboard_cmd(interaction: discord.Interaction):
    await safe_defer(interaction, thinking=True)
    if DB_AVAILABLE:
        _, total = await get_leaderboard_data(limit=10, offset=0)
    else:
        total = len(load_json("xp"))

    total_pages = max(1, (total + 9) // 10)
    view = LeaderboardPaginationView(current_page=0, total_pages=total_pages, total_users=total)
    embed = await view._build_embed()
    await safe_followup(interaction, embed=embed, view=view)



# ─── PRODUCT PANEL ────────────────────────────
@bot.tree.command(name="products", description="🛒 SuS Cracker ürün ve hizmetlerini görüntüle.")
async def products_cmd(interaction: discord.Interaction):
    e = mk_embed("🛒 SuS Cracker Suite v4.1 — Ürünler & Hizmetler",
        "Profesyonel bytecode güvenlik, analiz ve lisanslama çözümleri\n\n"
        "**🛡️ JAR Obfuscator (3 Seviye)**\n"
        "> Polimorfik sınıf içi string şifreleme, matematiksel opaque predicates, bitwise sabit karıştırma, Unicode/homoglyph sınıf & üye gizleme.\n\n"
        "**⚡ JAR DeObfuscator & DeobfSrc**\n"
        "> ASM ClassRemapper güvenli remapping, sabit katlama, ölü kod budama + CFR & Vineflower çift decompiler boru hattı ile tam `.java` kaynak kodları.\n\n"
        "**🔓 JarCracker (43 Metot)**\n"
        "> Meteor Client Addon, Fabric Mod, Forge Mod, Lunar/Client için HWID, KeyAuth, AntiVM, AntiDebug, Mixin, Reflection bypass.\n\n"
        "**🧹 JarClear & Webhook Honeypot**\n"
        "> RAT ve logger temizliği, saldırganın gizli Discord Webhook'unu ifşa etme ve tuzağa/honeypot'a yönlendirme.\n\n"
        "**🛡️ JarProtect (Geliştirici Lisans Enjektörü)**\n"
        "> Mod geliştiricileri için modlarına HWID kilidi, Süre Sınırı (TimeBomb) ve alarm webhook'u enjekte etme.\n\n"
        "**🔍 JarDiff (Bytecode Karşılaştırıcı)**\n"
        "> İki JAR arasındaki bytecode farklarını, eklenen/silinen sınıfları analiz eden differ.\n\n"
        "**📩 Komutlar:**\n"
        "`/jarobfuscator` • `/jardeobfuscator` • `/jardeobfuscationsrc` • `/jarcracker` • `/jarscanner` • `/jarclear` • `/jarprotect` • `/jardiff`",
        BRAND_COLOR)
    e.set_thumbnail(url=bot.user.display_avatar.url)
    await interaction.response.send_message(embed=e)


# ──────────────────────────────────────────────
# ENTRYPOINT
# ──────────────────────────────────────────────
if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE" or not TOKEN:
        print("=" * 62)
        print("[!] .env dosyasına DISCORD_BOT_TOKEN giriniz!")
        print("[!] Örnek: DISCORD_BOT_TOKEN=MTIzNDU2Nzg5...")
        print("[!]")
        print("[!] Opsiyonel ayarlar (0 bırakılabilir):")
        print("[!]   GUILD_ID, VERIFY_ROLE, AUTO_ROLE,")
        print("[!]   TICKET_CATEGORY, TICKET_LOG, WELCOME_CHANNEL,")
        print("[!]   MOD_LOG_CHANNEL, STARBOARD_CHANNEL, STAR_THRESHOLD")
        print("=" * 62)
        sys.exit(1)
    bot.run(TOKEN)
