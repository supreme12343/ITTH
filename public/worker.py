import discord
from discord import app_commands, ui
from discord.ext import commands
import json
import os
import random
import asyncio
from datetime import datetime, timedelta

# ================== SUPERUSER ==================
SUPERUSER_ID = 823861966664892417

# ================== DATA FILES ==================
ECONOMY_FILE = "economy.json"
WORK_COOLDOWNS_FILE = "work_cooldowns.json"

# ================== CONSTANTS ==================
DAILY_AMOUNT = 500
DAILY_COOLDOWN_HOURS = 20

# ──────────────────────────────────────────────
#  JOBS (name, emoji, min_pay, max_pay, fail_chance, flavor_texts, fail_texts)
# ──────────────────────────────────────────────
JOBS = {
    "hacker": {
        "emoji": "💻",
        "title": "Underground Hacker",
        "min": 300, "max": 900,
        "fail_chance": 0.20,
        "cooldown": 3600,
        "desc": "Break into secure servers for fast cash. High risk, high reward.",
        "success": [
            "You cracked into a corporate server and siphoned **${amount}** before the firewall caught up.",
            "3 AM, red bull in hand. You exploited a zero-day vulnerability and walked away with **${amount}**.",
            "You sold stolen corporate credentials on a dark web forum for **${amount}**. No regrets.",
            "You deployed a stealth keylogger on some CEO's laptop and pocketed **${amount}** in clean crypto.",
        ],
        "fail": [
            "You got traced by an active cybersecurity team. Wiped your drives and ran. Earned nothing.",
            "The target server was a government honeypot. You walked right into it. $0 and a near-arrest.",
            "Your premium VPN connection dropped mid-breach. You got absolutely nothing and went into hiding.",
        ]
    },
    "streamer": {
        "emoji": "🎮",
        "title": "Pro Streamer",
        "min": 100, "max": 600,
        "fail_chance": 0.15,
        "cooldown": 3600,
        "desc": "Go live on the internet, make plays, and look out for donations.",
        "success": [
            "You went live with 3 viewers and somehow got a massive **${amount}** donation from a mystery whale.",
            "Your clips went completely viral overnight. You earned **${amount}** in ad revenue and subscribers.",
            "You did a grueling 6-hour ranked grinding session. Earned **${amount}** in bits and viewer tips.",
            "You screamed loudly at a generic horror game and your chat went feral. Earned **${amount}**.",
        ],
        "fail": [
            "You went live to 0 viewers. Your own mother didn't even tune in. Earned absolutely nothing.",
            "Your internet provider died mid-stream during an intense clutch match. Chat spammed OMEGALUL. $0.",
            "You rage-quit 10 minutes in after getting stream-sniped. Sponsors pulled out. Earned nothing.",
        ]
    },
    "dealer": {
        "emoji": "🃏",
        "title": "Underground Card Dealer",
        "min": 200, "max": 700,
        "fail_chance": 0.25,
        "cooldown": 3600,
        "desc": "Run a shady backroom poker game. Take an untraceable cut of every pot.",
        "success": [
            "You ran a completely clean game tonight. Pocketed **${amount}** as the house commission.",
            "A group of elite high rollers showed up. You quietly skimmed **${amount}** off the top of the table.",
            "You successfully stacked the card deck just enough. Walked away with **${amount}** in cold hard cash.",
            "Three rich, overconfident players sat down. You made **${amount}** before they realized what happened.",
        ],
        "fail": [
            "An observant player caught you cheating. You barely escaped with your limbs intact. Earned nothing.",
            "The authorities raided the building mid-game. Everyone scattered. You lost your tables and made $0.",
            "You got cleared out by an undercover card shark. Lost nothing from your savings but earned $0.",
        ]
    },
    "assassin": {
        "emoji": "🗡️",
        "title": "Contract Assassin",
        "min": 500, "max": 1500,
        "fail_chance": 0.30,
        "cooldown": 7200,
        "desc": "Take secretive high-profile hit contracts. Highest paying job if you survive.",
        "success": [
            "Target eliminated cleanly from a distance. The client paid **${amount}** in unmarked bills.",
            "Clean hit with zero witnesses. **${amount}** was safely wired directly into your offshore bank account.",
            "Two perfect shots, one high-value target. Contract fulfilled successfully. **${amount}** deposited.",
            "You blended in perfectly as a waiter and slipped something into their drink. Collected **${amount}**.",
        ],
        "fail": [
            "The target was guarded by an elite security detail. You had to abort the mission. Earned nothing.",
            "Wrong floor, wrong target. The client immediately cancelled. $0 and now you are compromised.",
            "Your hands shook at the critical moment. You missed the shot and the target escaped. No pay.",
        ]
    },
    "chef": {
        "emoji": "👨‍🍳",
        "title": "Head Chef",
        "min": 150, "max": 450,
        "fail_chance": 0.10,
        "cooldown": 3600,
        "desc": "Cook delicious gourmet meals for the masses. Reliable, low-stress income.",
        "success": [
            "A packed house with incredibly happy customers. Earned **${amount}** in tips and base wages.",
            "Your custom risotto was so legendary that a food critic left you a **${amount}** bonus tip.",
            "Handled a major catering gig for an upscale wedding. Pocketed **${amount}** after food expenses.",
            "You invented a highly trending new dish today. The restaurant owner gave you a bonus of **${amount}**.",
        ],
        "fail": [
            "You accidentally burnt an entire line order. The health inspector showed up. Earned nothing.",
            "You had a severe coughing fit and sneezed directly into the soup. Got sent home immediately. $0.",
            "You mixed up the salt and sugar barrels for 50 fine desserts. Not a great shift. Earned nothing.",
        ]
    },
    "bounty_hunter": {
        "emoji": "🏹",
        "title": "Bounty Hunter",
        "min": 350, "max": 1000,
        "fail_chance": 0.22,
        "cooldown": 5400,
        "desc": "Track down dangerous, elusive fugitives and collect government rewards.",
        "success": [
            "Tracked a bail skip across state lines for 3 days. Brought them in and collected **${amount}**.",
            "The target was hiding out comfortably at their mother's house. Too easy. **${amount}** reward secured.",
            "You cornered the fugitive at an isolated gas station. A quick collar netted you **${amount}**.",
            "The fugitive desperately tried to bribe you to look away. You declined and turned them in for **${amount}**.",
        ],
        "fail": [
            "The target spotted your vehicle first and bolted into a crowd. Lost them completely. $0.",
            "You accidentally tackled the wrong person. Extremely awkward legal mess. Earned absolutely nothing.",
            "They had armed backup waiting in ambush. You did not. Retreated safely but earned nothing.",
        ]
    },
    "crypto_trader": {
        "emoji": "📈",
        "title": "Crypto Trader",
        "min": 50, "max": 2000,
        "fail_chance": 0.40,
        "cooldown": 3600,
        "desc": "Leverage highly volatile altcoins. Massive shifts both ways. Pure, fast gambling.",
        "success": [
            "You bought a sudden market dip at 3 AM. The portfolio skyrocketed, up **${amount}**.",
            "A random meme token pumped 600%. You successfully timed the top. **${amount}** in instant profits.",
            "Your gut intuition told you to go long on a leveraged trade. It paid off. Up **${amount}**.",
            "You resisted panic-selling yesterday and diamond-handed your bags to a **${amount}** green day.",
        ],
        "fail": [
            "The main coin you held got heavily rugpulled. Portfolio decimated. Earned negative money. $0.",
            "You panic-bought the absolute top and sold the rock bottom. Classic trader move. Earned nothing.",
            "Your technical analysis charts were completely wrong. Liquidated instantly. $0 and humiliation.",
        ]
    },
    "mechanic": {
        "emoji": "🔧",
        "title": "Backyard Mechanic",
        "min": 120, "max": 400,
        "fail_chance": 0.12,
        "cooldown": 3600,
        "desc": "Fix sports cars and engine modifications under the table. Honest mechanical labor.",
        "success": [
            "Rebuilt a blown transmission in record time. Charged **${amount}** and received a tip.",
            "A neighbor's vintage car refused to crank over. A quick wiring fix made you **${amount}** cash.",
            "You accurately diagnosed a phantom engine knock that dealerships failed to trace. Earned **${amount}**.",
            "Performed an oil swap, fresh brake pads, and a full radiator flush. Earned **${amount}**.",
        ],
        "fail": [
            "You heavily overtightened a bolt and cracked a custom engine block. Had to waive fees. $0.",
            "Ordered the wrong part numbers from the distributor. Had to turn the client away empty-handed. $0.",
            "You forgot to reconnect a vital ground wire. The car broke down down the street. Customer irate. $0.",
        ]
    },
}

# ──────────────────────────────────────────────
#  FISHING DATABASE
# ──────────────────────────────────────────────
FISH_TABLE = [
    ("Old Boot",       "👟", 1,   15,    10),
    ("Plastic Bag",    "🛍️", 1,   10,    8),
    ("Sardine",        "🐟", 15,  40,    30),
    ("Carp",           "🎣", 35,  75,    25),
    ("Catfish",        "🐈", 55,  110,   20),
    ("Bass",           "🐠", 75,  150,   18),
    ("Salmon",         "🍣", 110, 220,   14),
    ("Tuna",           "🐟", 160, 310,   10),
    ("Swordfish",      "⚔️", 250, 500,   7),
    ("Shark",          "🦈", 450, 950,   4),
    ("Giant Squid",    "🦑", 700, 1500,  2),
    ("Golden Koi",     "✨", 1200, 3000, 1),
]

FISH_WEIGHTS = [f[4] for f in FISH_TABLE]

ROD_TIERS = {
    "none":      {"name": "Bare Hands",     "emoji": "🤲", "bonus": 0,    "rare_boost": 0},
    "basic":     {"name": "Basic Rod",      "emoji": "🎣", "bonus": 0.05, "rare_boost": 0.02, "price": 200},
    "silver":    {"name": "Silver Rod",     "emoji": "🥈", "bonus": 0.20, "rare_boost": 0.08, "price": 800},
    "golden":    {"name": "Golden Rod",     "emoji": "🥇", "bonus": 0.45, "rare_boost": 0.18, "price": 2500},
    "legendary": {"name": "Legendary Rod",  "emoji": "🌟", "bonus": 0.75, "rare_boost": 0.35, "price": 8000},
}

# ──────────────────────────────────────────────
#  FARMING DATABASE
# ──────────────────────────────────────────────
CROPS = {
    "wheat":      {"emoji": "🌾", "grow_time": 60,   "cost": 30,  "min_yield": 70,  "max_yield": 140,  "slots": 1},
    "carrot":     {"emoji": "🥕", "grow_time": 180,  "cost": 60,  "min_yield": 150, "max_yield": 240,  "slots": 1},
    "corn":       {"emoji": "🌽", "grow_time": 360,  "cost": 100, "min_yield": 250, "max_yield": 400,  "slots": 2},
    "tomato":     {"emoji": "🍅", "grow_time": 600,  "cost": 150, "min_yield": 380, "max_yield": 600,  "slots": 2},
    "watermelon": {"emoji": "🍉", "grow_time": 1200, "cost": 300, "min_yield": 750, "max_yield": 1200, "slots": 3},
    "pumpkin":    {"emoji": "🎃", "grow_time": 2400, "cost": 500, "min_yield": 1200,"max_yield": 2000, "slots": 4},
    "dragonfruit":{"emoji": "🐉", "grow_time": 3600, "cost": 1000,"min_yield":2500,"max_yield": 4500, "slots": 5},
}

FARM_SLOTS_DEFAULT = 6
FARM_SLOT_UPGRADE_LIMIT = 15

# ──────────────────────────────────────────────
#  SHOP ITEMS
# ──────────────────────────────────────────────
SHOP_ITEMS = {
    "basic_rod":     {"name": "Basic Rod",      "emoji": "🎣", "price": 200,  "type": "rod",  "rod_key": "basic", "desc": "Slightly better than catching with your hands."},
    "silver_rod":    {"name": "Silver Rod",     "emoji": "🥈", "price": 800,  "type": "rod",  "rod_key": "silver", "desc": "Solid aluminum build. Boosts fish yield value by 20%."},
    "golden_rod":    {"name": "Golden Rod",     "emoji": "🥇", "price": 2500, "type": "rod",  "rod_key": "golden", "desc": "Highly resilient gold coating. Buffs rare fish rates."},
    "legendary_rod": {"name": "Legendary Rod",  "emoji": "🌟", "price": 8000, "type": "rod",  "rod_key": "legendary", "desc": "Blessed ocean relic. +75% value and immense luck."},
    "farm_slot":     {"name": "Extra Farm Slot","emoji": "🌱", "price": 2000, "type": "farm_slot", "desc": "Expand your farm acreage to plant more multi-slot seeds."},
    "fertilizer":    {"name": "Fertilizer Bag", "emoji": "💊", "price": 400,  "type": "consumable", "effect": "fertilizer", "desc": "Cuts the growth time of any crop in half instantly."},
    "lucky_charm":   {"name": "Lucky Charm",    "emoji": "🍀", "price": 600,  "type": "consumable", "effect": "lucky", "desc": "Carrying this boosts gambling payouts and work safety."},
}

# ──────────────────────────────────────────────────────────────────────────────
#  DATA PERSISTENCE CORE HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def load_economy():
    if not os.path.exists(ECONOMY_FILE):
        return {}
    try:
        with open(ECONOMY_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_economy(data):
    with open(ECONOMY_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_profile(data: dict, user_id: int) -> dict:
    uid = str(user_id)
    if uid not in data:
        data[uid] = {
            "balance": 100,
            "bank": 0,
            "rod": "none",
            "farm_slots": FARM_SLOTS_DEFAULT,
            "farm": {},           # Slot index matching string representations
            "inventory": {},      # Item storage keys
            "job_cooldowns": {},  
            "fish_cooldown": 0,
            "daily_cooldown": 0,
            "total_earned": 100,
            "fish_caught": 0,
            "harvests": 0,
            "gamble_wins": 0,
            "shifts_worked": 0
        }
    # Ensure nested keys are backfilled safely for existing accounts
    defaults = {
        "balance": 100, "bank": 0, "rod": "none", "farm_slots": FARM_SLOTS_DEFAULT,
        "farm": {}, "inventory": {}, "job_cooldowns": {}, "fish_cooldown": 0,
        "daily_cooldown": 0, "total_earned": 100, "fish_caught": 0, "harvests": 0,
        "gamble_wins": 0, "shifts_worked": 0
    }
    for k, v in defaults.items():
        if k not in data[uid]:
            data[uid][k] = v
    return data[uid]

def fmt_money(amount: int) -> str:
    return f"**${amount:,}**"

def time_remaining(unix_ts: float) -> str:
    now = datetime.utcnow().timestamp()
    remaining = unix_ts - now
    if remaining <= 0:
        return "Ready!"
    hours, rem = divmod(int(remaining), 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    elif minutes:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

# ══════════════════════════════════════════════════════════════════════════════
#  INTERACTIVE WORK MINIGAME UI SYSTEM
# ══════════════════════════════════════════════════════
class WorkMinigameView(ui.View):
    def __init__(self, target_user: discord.User, target_color: str, expected_label: str, alternatives: list):
        super().__init__(timeout=15.0)
        self.target_user = target_user
        self.success = False
        self.expected_label = expected_label

        choices = [(expected_label, target_color)] + alternatives
        random.shuffle(choices)

        style_map = {
            "green": discord.ButtonStyle.success,
            "blue": discord.ButtonStyle.primary,
            "grey": discord.ButtonStyle.secondary,
            "red": discord.ButtonStyle.danger
        }

        for index, (label, color) in enumerate(choices):
            btn = ui.Button(label=label, style=style_map.get(color, discord.ButtonStyle.secondary), custom_id=f"game_btn_{index}")
            btn.callback = self.make_callback(label)
            self.add_item(btn)

    def make_callback(self, label: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.target_user.id:
                return await interaction.response.send_message("❌ This is not your minigame session!", ephemeral=True)
            if label == self.expected_label:
                self.success = True
            self.stop()
            await interaction.response.defer()
        return callback

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN COG INTERACTION CLASS
# ══════════════════════════════════════════════════════════════════════════════
class Work(commands.Cog):
    def __init__(self, client):
        self.client = client

    async def cog_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            minutes, seconds = divmod(error.retry_after, 60)
            ts = f"{int(minutes)}m {int(seconds)}s" if minutes else f"{int(seconds)}s"
            msg = f"⏳ **Cooldown active!** Try executing this again in **{ts}**."
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "🚫 Access denied. You do not have permissions to use this administrative utility."
        else:
            msg = f"❌ An unhandled system exception occurred: `{error}`"
            print(f"System Error Exception: {error}")

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    # ══════════════════════════════════════════════════════
    #  💰 BALANCE
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="balance", description="Check your current wallet cash and secured bank deposits.")
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        data = load_economy()
        p = get_profile(data, target.id)
        save_economy(data)

        rod_info = ROD_TIERS.get(p.get("rod", "none"), ROD_TIERS["none"])
        wallet = p["balance"]
        bank = p["bank"]
        net_worth = wallet + bank

        embed = discord.Embed(
            title=f"💳  {target.display_name}'s Financial Profile",
            color=discord.Color.from_rgb(114, 137, 218)
        )
        embed.add_field(name="💵 Wallet Cash",       value=f"${wallet:,}", inline=True)
        embed.add_field(name="🏦 Bank Balance",      value=f"${bank:,}", inline=True)
        embed.add_field(name="💎 Total Net Worth",   value=f"${net_worth:,}", inline=True)
        embed.add_field(name="🎣 Equipped Fishing Gear", value=f"{rod_info['emoji']} {rod_info['name']}", inline=True)
        embed.add_field(name="🌾 Unlocked Farm Land", value=f"{p['farm_slots']} Plots", inline=True)
        embed.add_field(name="📊 Career Revenue", value=f"${p.get('total_earned', 0):,}", inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="Secure your wallet funds from getting robbed by executing /deposit.")
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🏦 DEPOSIT / WITHDRAW
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="deposit", description="Transfer liquid cash from your wallet into your vault safe.")
    @app_commands.describe(amount="Exact numeric cash value to deposit, or keyphrase 'all'")
    async def deposit(self, interaction: discord.Interaction, amount: str):
        data = load_economy()
        p = get_profile(data, interaction.user.id)

        if amount.lower() == "all":
            amt = p["balance"]
        else:
            try:
                amt = int(amount)
            except ValueError:
                return await interaction.response.send_message("❌ Input parameters invalid. Please enter an integer or 'all'.", ephemeral=True)

        if amt <= 0:
            return await interaction.response.send_message("❌ Transaction amounts must remain non-negative and above zero.", ephemeral=True)
        if amt > p["balance"]:
            return await interaction.response.send_message(f"❌ Transaction failure. Your wallet holds a balance of: **${p['balance']:,}**.", ephemeral=True)

        p["balance"] -= amt
        p["bank"] += amt
        save_economy(data)

        embed = discord.Embed(
            description=f"🏦 **Vault Deposit Successful!**\nMoved **${amt:,}** into safe storage.\n💳 Liquid Wallet Left: **${p['balance']:,}**", 
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="withdraw", description="Extract secured cash assets out from your vault bank.")
    @app_commands.describe(amount="Exact numeric cash value to withdraw, or keyphrase 'all'")
    async def withdraw(self, interaction: discord.Interaction, amount: str):
        data = load_economy()
        p = get_profile(data, interaction.user.id)

        if amount.lower() == "all":
            amt = p["bank"]
        else:
            try:
                amt = int(amount)
            except ValueError:
                return await interaction.response.send_message("❌ Input parameters invalid. Please enter an integer or 'all'.", ephemeral=True)

        if amt <= 0:
            return await interaction.response.send_message("❌ Transaction extraction parameters must exceed zero.", ephemeral=True)
        if amt > p["bank"]:
            return await interaction.response.send_message(f"❌ Extraction failure. Vault assets contain a max depth of: **${p['bank']:,}**.", ephemeral=True)

        p["bank"] -= amt
        p["balance"] += amt
        save_economy(data)

        embed = discord.Embed(
            description=f"💵 **Vault Withdrawal Complete!**\nReturned **${amt:,}** back to active pockets.\n🏦 Total Secured Assets Remaining: **${p['bank']:,}**", 
            color=discord.Color.dark_green()
        )
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  💸 PAY
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="pay", description="Transfer clean funds directly to another user's wallet space.")
    @app_commands.describe(member="Target player recipient", amount="Financial currency valuation amount")
    async def pay(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if member.bot or member.id == interaction.user.id:
            return await interaction.response.send_message("❌ Financial transfer parameters cannot contain bots or yourself.", ephemeral=True)
        if amount <= 0:
            return await interaction.response.send_message("❌ Transfer valuations must match a positive scale value.", ephemeral=True)

        data = load_economy()
        sender = get_profile(data, interaction.user.id)

        if sender["balance"] < amount:
            return await interaction.response.send_message(f"❌ Remittance denied. Your available wallet balance sits short at: **${sender['balance']:,}**.", ephemeral=True)

        receiver = get_profile(data, member.id)
        sender["balance"] -= amount
        receiver["balance"] += amount
        save_economy(data)

        embed = discord.Embed(
            description=f"💸 {interaction.user.mention} executed an immediate transfer of **${amount:,}** over to {member.mention}!",
            color=discord.Color.teal()
        )
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  📅 DAILY
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="daily", description="Claim your routine calendar allowance reward allocation.")
    async def daily(self, interaction: discord.Interaction):
        data = load_economy()
        p = get_profile(data, interaction.user.id)
        now = datetime.utcnow().timestamp()
        cooldown_until = p.get("daily_cooldown", 0)

        if now < cooldown_until:
            tr = time_remaining(cooldown_until)
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"⏳ **Calendar Lockout!** You've claimed your reward already. Return in **{tr}**.", color=discord.Color.red()),
                ephemeral=True
            )

        p["balance"] += DAILY_AMOUNT
        p["total_earned"] = p.get("total_earned", 0) + DAILY_AMOUNT
        p["daily_cooldown"] = now + (DAILY_COOLDOWN_HOURS * 3600)
        save_economy(data)

        embed = discord.Embed(
            title="📅  Routine Allowance Disbursed!",
            description=f"You successfully collected your clean **${DAILY_AMOUNT:,}** allocation!\nUpdated liquid funds: **${p['balance']:,}**",
            color=discord.Color.gold()
        )
        embed.set_footer(text=f"The next recurring daily calendar check clears in {DAILY_COOLDOWN_HOURS} hours.")
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  💼 JOBS LIST
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="jobs", description="Inspect all structural recruitment industries and operational payouts.")
    async def jobs(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="💼  Industrial Employment Board",
            description="Execute shifts inside operations with `/work <job_choice>`. High stakes roles contain dangerous structural failure outcomes.",
            color=discord.Color.orange()
        )
        for key, job in JOBS.items():
            cooldown_str = f"{job['cooldown']//3600}h" if job['cooldown'] >= 3600 else f"{job['cooldown']//60}m"
            embed.add_field(
                name=f"{job['emoji']}  {job['title']}",
                value=(
                    f"Salary Pay-Scale: **${job['min']:,} – ${job['max']:,}**\n"
                    f"Operational Threat: `{int(job['fail_chance']*100)}% Risk`\n"
                    f"Cooldown Metric: `{cooldown_str}`\n"
                    f"_*Description: {job['desc']}_*"
                ),
                inline=True
            )
        embed.set_footer(text="Higher operational risk profiles translate to extensive payment yields.")
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  💼 WORK WITH DANK MEMER MINIGAMES
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="work", description="Clock into active industrial fields to navigate an operational assignment.")
    @app_commands.describe(job="Choose an industrial trade category assignment sector")
    @app_commands.choices(job=[
        app_commands.Choice(name=f"{v['emoji']} {v['title']}", value=k)
        for k, v in JOBS.items()
    ])
    async def work(self, interaction: discord.Interaction, job: str):
        if job not in JOBS:
            return await interaction.response.send_message("❌ Specified employment branch is unrecognized inside our database.", ephemeral=True)

        data = load_economy()
        p = get_profile(data, interaction.user.id)
        now = datetime.utcnow().timestamp()
        job_data = JOBS[job]

        cooldown_key = f"job_{job}"
        cooldown_until = p["job_cooldowns"].get(cooldown_key, 0)

        if now < cooldown_until:
            tr = time_remaining(cooldown_until)
            return await interaction.response.send_message(
                embed=discord.Embed(
                    description=f"{job_data['emoji']} **{job_data['title']}** shifts are maxed. Shift rest requires another **{tr}**.",
                    color=discord.Color.red()
                ),
                ephemeral=True
            )

        # Dank Memer Reflex Minigame Setup
        prompt_options = [
            ("Safety Override Switch", "green", [("Main Engine Vent", "red"), ("Fuel Valve", "blue")]),
            ("System Server Purge", "blue", [("Firewall Feed", "grey"), ("Data Core Dump", "red")]),
            ("Thermal Core Stabilizer", "grey", [("Coolant Injector", "blue"), ("Radiation Leak", "red")]),
            ("Emergency Shutdown Lever", "red", [("Standard Cycle", "green"), ("Compressor Mod", "grey")])
        ]

        target_prompt, target_color, alternatives = random.choice(prompt_options)

        game_embed = discord.Embed(
            title=f"🚨  DANK MEMER FIELD EVENT: Quick Action Required!",
            description=f"{interaction.user.mention}, critical scenario encountered during your **{job_data['title']}** shift!\n\n**CLICK THE {target_color.upper()} BUTTON STAMPED:**\n▶️ `{target_prompt}`\n\n*Time Limit: 15 Seconds!*",
            color=discord.Color.from_rgb(255, 69, 0)
        )

        view = WorkMinigameView(interaction.user, target_color, target_prompt, alternatives)
        await interaction.response.send_message(embed=game_embed, view=view)

        # Wait for interaction to complete or time out
        await view.wait()

        # Reload economy to prevent data overwriting after delay
        data = load_economy()
        p = get_profile(data, interaction.user.id)

        # Check for Lucky Charm safety perk
        has_charm = p["inventory"].get("lucky_charm", 0) > 0
        roll = random.random()
        failed = not view.success

        if failed and has_charm and random.random() < 0.40:
            failed = False
            p["inventory"]["lucky_charm"] -= 1
            if p["inventory"]["lucky_charm"] <= 0:
                del p["inventory"]["lucky_charm"]
            field_insurance_perk = "🍀 **Your Lucky Charm dissolved but saved your shift from terminal error!**"
        else:
            field_insurance_perk = ""

        if failed:
            fail_text = random.choice(job_data["fail"])
            embed = discord.Embed(
                title=f"{job_data['emoji']}  Operational Failure — {job_data['title']}",
                description=f"❌ **Assignment Ruined.**\n{fail_text}",
                color=discord.Color.red()
            )
            embed.add_field(name="Shift Salary Payoff", value="$0", inline=True)
        else:
            earned = random.randint(job_data["min"], job_data["max"])
            p["balance"] += earned
            p["total_earned"] = p.get("total_earned", 0) + earned
            p["shifts_worked"] = p.get("shifts_worked", 0) + 1

            success_text = random.choice(job_data["success"]).replace("${amount}", f"{earned:,}")
            embed = discord.Embed(
                title=f"{job_data['emoji']}  Shift Performance Evaluation — {job_data['title']}",
                description=f"✅ **Assignment Success!**\n{success_text}\n{field_insurance_perk}",
                color=discord.Color.green()
            )
            embed.add_field(name="💵 Earned Value", value=f"${earned:,}", inline=True)
            embed.add_field(name="💳 Wallet Total", value=f"${p['balance']:,}", inline=True)

        p["job_cooldowns"][cooldown_key] = datetime.utcnow().timestamp() + job_data["cooldown"]
        save_economy(data)

        cooldown_readable = f"{job_data['cooldown']//3600}h" if job_data['cooldown'] >= 3600 else f"{job_data['cooldown']//60}m"
        embed.set_footer(text=f"The agency permits your next industrial assignment in {cooldown_readable}.")
        await interaction.followup.send(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🎣 FISH
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="fish", description="Cast out deep lines into open coastal waters to hook sealife assets.")
    async def fish(self, interaction: discord.Interaction):
        data = load_economy()
        p = get_profile(data, interaction.user.id)
        now = datetime.utcnow().timestamp()

        FISH_COOLDOWN = 120
        if now < p.get("fish_cooldown", 0):
            tr = time_remaining(p["fish_cooldown"])
            return await interaction.response.send_message(
                embed=discord.Embed(description=f"🎣 Your line reels are currently locking. Please await clearance for another **{tr}**.", color=discord.Color.blue()),
                ephemeral=True
            )

        await interaction.response.defer()
        await asyncio.sleep(random.uniform(2.0, 3.0))

        rod_key = p.get("rod", "none")
        rod = ROD_TIERS.get(rod_key, ROD_TIERS["none"])
        weights = list(FISH_WEIGHTS)

        if rod["rare_boost"] > 0:
            for i in range(len(weights)):
                if i >= len(weights) - 4:
                    weights[i] = weights[i] * (1 + rod["rare_boost"] * 5)

        miss_chance = max(0.02, 0.12 - rod["bonus"] * 0.1)
        if random.random() < miss_chance:
            p["fish_cooldown"] = now + FISH_COOLDOWN
            save_economy(data)

            misses = [
                "An aggressive wave ripped away your bait hooks. Got absolute void.",
                "Line snapped on jagged marine structures. Skill issue.",
                "Sat completely static for minutes only to pull back empty weeds.",
                "A clever fish clean-stripped your lines and escaped into deep waters."
            ]
            embed = discord.Embed(
                title="🎣  Offshore Expedition Log",
                description=random.choice(misses),
                color=discord.Color.dark_grey()
            )
            embed.set_footer(text=f"Recast windows open in 2 mins. Tooling: {rod['emoji']} {rod['name']}")
            return await interaction.followup.send(embed=embed)

        fish_data = random.choices(FISH_TABLE, weights=weights, k=1)[0]
        name, emoji, min_val, max_val, _ = fish_data
        value = random.randint(min_val, max_val)

        if rod["bonus"] > 0:
            value = int(value * (1 + rod["bonus"]))

        p["balance"] += value
        p["total_earned"] = p.get("total_earned", 0) + value
        p["fish_caught"] = p.get("fish_caught", 0) + 1
        p["fish_cooldown"] = now + FISH_COOLDOWN
        save_economy(data)

        if value >= 600:
            color = discord.Color.gold()
            headline = "🌟  **LEGENDARY SPECTRAL AQUATIC HAUL!!**"
        elif value >= 180:
            color = discord.Color.purple()
            headline = "🔮  **Exotic Marine Discovery!**"
        elif value >= 70:
            color = discord.Color.blue()
            headline = "🔵  **Premium Catch Secured!**"
        else:
            color = discord.Color.green()
            headline = "✅  **Standard Catch Logged.**"

        embed = discord.Embed(
            title="🎣 Fishing Net Manifest",
            description=f"{headline}\n{emoji} **{name}** — market liquidated instantly for {fmt_money(value)}!",
            color=color
        )
        embed.add_field(name="⚓ Lifetime Hauls", value=str(p["fish_caught"]), inline=True)
        embed.add_field(name="💳 Pocket Value", value=f"${p['balance']:,}", inline=True)
        embed.add_field(name="🛠️ Tackle Employed", value=f"{rod['emoji']} {rod['name']}", inline=True)
        embed.set_footer(text="Purchase higher utility tier fishing reels within the /shop.")
        await interaction.followup.send(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🌾 FARM — PLANT WITH MULTI-SLOT MECHANICS FIXED
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="plant", description="Sow crop seeds across your agricultural land registry slots.")
    @app_commands.describe(crop="Agricultural seed selection matrix", slot="Starting index land plot location number")
    @app_commands.choices(crop=[
        app_commands.Choice(name=f"{v['emoji']} {k.title()} (Takes {v['slots']} slots, Yields up to ${v['max_yield']})", value=k)
        for k, v in CROPS.items()
    ])
    async def plant(self, interaction: discord.Interaction, crop: str, slot: int):
        if crop not in CROPS:
            return await interaction.response.send_message("❌ Selection crop variant is absent from agrarian archives.", ephemeral=True)

        data = load_economy()
        p = get_profile(data, interaction.user.id)
        crop_data = CROPS[crop]
        needed_slots = crop_data["slots"]

        if slot < 1 or (slot + needed_slots - 1) > p["farm_slots"]:
            return await interaction.response.send_message(f"❌ Target bounds invalid. Planting a `{crop}` requires `{needed_slots}` consecutive open plots starting from slot `{slot}` up to your max allocation of `{p['farm_slots']}`.", ephemeral=True)

        # Multi-slot structural allocation checks
        checked_slots = [str(i) for i in range(slot, slot + needed_slots)]
        for s in checked_slots:
            if s in p["farm"]:
                return await interaction.response.send_message(f"❌ Land conflict detected! Plot slot `{s}` is currently pre-occupied by a growing plant module.", ephemeral=True)

        if p["balance"] < crop_data["cost"]:
            return await interaction.response.send_message(f"❌ Purchase insolvency. Sowing seed modules requires a market listing fee of: **${crop_data['cost']}**.", ephemeral=True)

        p["balance"] -= crop_data["cost"]
        now = datetime.utcnow().timestamp()

        # Allocate anchor structure along with structural pointers to handle large multi-slot structures
        p["farm"][str(slot)] = {
            "crop": crop,
            "planted_at": now,
            "fertilized": False,
            "is_anchor": True,
            "span_slots": checked_slots
        }

        # Link children slots back to lock them down
        for child_slot in checked_slots[1:]:
            p["farm"][child_slot] = {
                "crop": crop,
                "is_anchor": False,
                "parent_anchor": str(slot)
            }

        save_economy(data)
        embed = discord.Embed(
            description=f"🌱 **Sowing Verification Protocol Approved!**\nPlanted {crop_data['emoji']} **{crop.title()}** into land sector spans: `{', '.join(checked_slots)}`.\n💳 Investment Cost: **${crop_data['cost']}**",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🌾 FARM — VISUAL EMBED GRID DISPLAY PANEL
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="farm", description="Inspect your agricultural estate grid layout status metrics.")
    async def farm(self, interaction: discord.Interaction):
        data = load_economy()
        p = get_profile(data, interaction.user.id)

        embed = discord.Embed(
            title=f"🚜  {interaction.user.display_name}'s Agrarian Fields Grid",
            description="Manage your fields and reap yields. Upgrade slots using the `/shop` market grid.",
            color=discord.Color.from_rgb(34, 139, 34)
        )

        now = datetime.utcnow().timestamp()

        for i in range(1, p["farm_slots"] + 1):
            slot_key = str(i)
            if slot_key not in p["farm"]:
                status_txt = "🟫 **Empty Dirt Patch**\nStatus: `Ready for Planting`"
                embed.add_field(name=f"Plot Sector [{i}]", value=status_txt, inline=True)
            else:
                sdata = p["farm"][slot_key]
                if not sdata.get("is_anchor", True):
                    # Child slot reference point display logic
                    parent_idx = sdata.get("parent_anchor")
                    parent_crop = sdata.get("crop", "plant")
                    emoji = CROPS.get(parent_crop, {}).get("emoji", "🌱")
                    status_txt = f"{emoji} **Bound Growth**\nExtension of `Plot {parent_idx}`"
                    embed.add_field(name=f"Plot Sector [{i}]", value=status_txt, inline=True)
                    continue

                c_key = sdata["crop"]
                c_info = CROPS[c_key]
                duration = c_info["grow_time"]
                if sdata.get("fertilized", False):
                    duration = duration // 2

                elapsed = now - sdata["planted_at"]
                remaining = duration - elapsed

                if remaining <= 0:
                    status_txt = f"{c_info['emoji']} **Ripe {c_key.title()}**\nStatus: 🟢 `Ready for /harvest`"
                else:
                    m, s = divmod(int(remaining), 60)
                    time_str = f"{m}m {s}s" if m else f"{s}s"
                    fert_tag = " [💊 Fert]" if sdata.get("fertilized") else ""
                    status_txt = f"{c_info['emoji']} **Sprouting**\nTime Left: `{time_str}`{fert_tag}"

                embed.add_field(name=f"Plot Sector [{i}] (Anchor)", value=status_txt, inline=True)

        save_economy(data)
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🌾 FARM — HARVEST COMMENCEMENT LOGIC FIXED
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="harvest", description="Extract matured yield objects out from targeted farming layouts.")
    @app_commands.describe(slot="Target sector layout index containing matured agricultural payloads")
    async def harvest(self, interaction: discord.Interaction, slot: int):
        data = load_economy()
        p = get_profile(data, interaction.user.id)
        slot_key = str(slot)

        if slot_key not in p["farm"]:
            return await interaction.response.send_message("❌ Target land plot coordinates are barren of lifecycle elements.", ephemeral=True)

        sdata = p["farm"][slot_key]

        # Route processing straight down into anchor definitions if targeted entry lands on linked sub-slots
        if not sdata.get("is_anchor", True):
            anchor_key = sdata["parent_anchor"]
            slot = int(anchor_key)
            slot_key = anchor_key
            sdata = p["farm"][slot_key]

        c_key = sdata["crop"]
        c_info = CROPS[c_key]
        duration = c_info["grow_time"]

        if sdata.get("fertilized", False):
            duration = duration // 2

        now = datetime.utcnow().timestamp()
        if (now - sdata["planted_at"]) < duration:
            rem = duration - (now - sdata["planted_at"])
            return await interaction.response.send_message(f"❌ Harvesting block active. This plant structure requires `{int(rem)}` more validation seconds to ripen.", ephemeral=True)

        # Clear entire footprint spanning child properties
        all_allocated_slots = sdata.get("span_slots", [slot_key])
        for target_clearance in all_allocated_slots:
            if target_clearance in p["farm"]:
                del p["farm"][target_clearance]

        # Financial yield generation math
        yield_payout = random.randint(c_info["min_yield"], c_info["max_yield"])
        p["balance"] += yield_payout
        p["total_earned"] = p.get("total_earned", 0) + yield_payout
        p["harvests"] = p.get("harvests", 0) + 1

        save_economy(data)
        embed = discord.Embed(
            description=f"🧺 **Agrarian Crop Yield Harvested!**\nReaped {c_info['emoji']} **{c_key.title()}** inside fields across sectors `{', '.join(all_allocated_slots)}`.\n💰 Market Delivery Valued at: {fmt_money(yield_payout)}",
            color=discord.Color.gold()
        )
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🛒 SHOP PANEL UI
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="shop", description="Browse systemic market tools, upgrades, and structural modifiers.")
    async def shop(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🛒  Global Hardware & Provisioning Superstore",
            description="Invest your capital back into performance tooling. Purchase options using `/buy <item_id>`.",
            color=discord.Color.from_rgb(235, 180, 50)
        )
        for key, item in SHOP_ITEMS.items():
            embed.add_field(
                name=f"{item['emoji']}  {item['name']} (`{key}`)",
                value=f"Cost: **${item['price']:,}**\n_*Effect: {item['desc']}_*",
                inline=False
            )
        embed.set_footer(text="Ensure your current wallet cash accounts accommodate listings prior to checkout.")
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🛒 BUY
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="buy", description="Aquire commercial products straight from market inventories.")
    @app_commands.describe(item="Inventory listing key target identifying phrase", quantity="Total asset count multiplier volume request")
    @app_commands.choices(item=[
        app_commands.Choice(name=f"{v['emoji']} {v['name']} (${v['price']:,})", value=k)
        for k, v in SHOP_ITEMS.items()
    ])
    async def buy(self, interaction: discord.Interaction, item: str, quantity: int = 1):
        if quantity <= 0:
            return await interaction.response.send_message("❌ Asset purchase quantity scaling demands a value higher than zero.", ephemeral=True)

        if item not in SHOP_ITEMS:
            return await interaction.response.send_message("❌ Requested market listing code is unidentified.", ephemeral=True)

        data = load_economy()
        p = get_profile(data, interaction.user.id)
        item_data = SHOP_ITEMS[item]
        total_cost = item_data["price"] * quantity

        if p["balance"] < total_cost:
            return await interaction.response.send_message(f"❌ Purchase insolvency. Transaction overhead requires **${total_cost:,}** cash, but your wallet contains: **${p['balance']:,}**.", ephemeral=True)

        # Operational branching checking for specialized custom tool tracking values
        if item_data["type"] == "rod":
            if quantity > 1:
                return await interaction.response.send_message("❌ Tactical fishing equipment lines are limited to single identity items.", ephemeral=True)
            p["rod"] = item_data["rod_key"]
        elif item_data["type"] == "farm_slot":
            if (p["farm_slots"] + quantity) > FARM_SLOT_UPGRADE_LIMIT:
                return await interaction.response.send_message(f"❌ Zoning boundaries maxed. Land plot expansions cannot exceed structural thresholds of `{FARM_SLOT_UPGRADE_LIMIT}` sectors.", ephemeral=True)
            p["farm_slots"] += quantity
        else:
            # Handle standard stacking database entries inside character asset spaces
            if "inventory" not in p:
                p["inventory"] = {}
            p["inventory"][item] = p["inventory"].get(item, 0) + quantity

        p["balance"] -= total_cost
        save_economy(data)

        embed = discord.Embed(
            description=f"📦 **Invoice Transaction Complete!**\nAcquired `{quantity}x` {item_data['emoji']} **{item_data['name']}**.\n💳 Total Debited Asset Cost: **${total_cost:,}**",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🎒 INVENTORY PANEL UI
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="inventory", description="Display held structural materials and stock inventories.")
    async def inventory(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        data = load_economy()
        p = get_profile(data, target.id)
        save_economy(data)

        embed = discord.Embed(
            title=f"🎒  {target.display_name}'s Private Vault Inventory",
            color=discord.Color.from_rgb(155, 89, 182)
        )

        inv = p.get("inventory", {})
        usable_items_exist = False

        for k, v in inv.items():
            if v > 0 and k in SHOP_ITEMS:
                sitem = SHOP_ITEMS[k]
                embed.add_field(name=f"{sitem['emoji']} {sitem['name']} (`{k}`)", value=f"Stored Units: `{v}`\n_*Usage Command: /use item:{k}_*", inline=True)
                usable_items_exist = True

        rod_tier = p.get("rod", "none")
        r_info = ROD_TIERS.get(rod_tier, ROD_TIERS["none"])
        embed.add_field(name="⚓ Core Infrastructure Gear", value=f"Equipped Rod: {r_info['emoji']} **{r_info['name']}**", inline=False)

        if not usable_items_exist:
            embed.description = "*Your physical inventory storage space currently sits empty of consumable components.*"

        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  💊 USE CONSUMABLE
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="use", description="Consume item assets out from local storage variables.")
    @app_commands.describe(item="Stored item code string phrase identification matrix")
    @app_commands.choices(item=[
        app_commands.Choice(name="💊 Fertilizer Bag", value="fertilizer")
    ])
    async def use(self, interaction: discord.Interaction, item: str):
        data = load_economy()
        p = get_profile(data, interaction.user.id)

        if p.get("inventory", {}).get(item, 0) <= 0:
            return await interaction.response.send_message("❌ Action aborted. You lack target structural provisions inside your backpack.", ephemeral=True)

        if item == "fertilizer":
            # Find leftmost anchor node currently processing growth cycles
            target_found = None
            for idx in sorted(p["farm"].keys(), key=lambda x: int(x)):
                sdata = p["farm"][idx]
                if sdata.get("is_anchor", True) and not sdata.get("fertilized", False):
                    c_info = CROPS[sdata["crop"]]
                    elapsed = datetime.utcnow().timestamp() - sdata["planted_at"]
                    if elapsed < c_info["grow_time"]:
                        target_found = idx
                        break

            if not target_found:
                return await interaction.response.send_message("❌ Application targets missing. No un-fertilized growing anchors require acceleration processing.", ephemeral=True)

            p["farm"][target_found]["fertilized"] = True
            p["inventory"]["fertilizer"] -= 1
            if p["inventory"]["fertilizer"] <= 0:
                del p["inventory"]["fertilizer"]

            save_economy(data)
            await interaction.response.send_message(f"💊 **Agrochemical Infusion Executed!** Speeds up growth cycles inside land plot slot `[{target_found}]` anchors by 50%.")

    # ══════════════════════════════════════════════════════
    #  📊 LEADERBOARD (RICHEST)
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="richest", description="Behold high net worth capitalization profiles inside this context.")
    async def richest(self, interaction: discord.Interaction):
        data = load_economy()
        leaderboard = []

        for uid, p in data.items():
            try:
                user_obj = self.client.get_user(int(uid))
                name = user_obj.name if user_obj else f"User_{uid}"
            except:
                name = f"User_{uid}"
            net = p.get("balance", 0) + p.get("bank", 0)
            leaderboard.append((name, net))

        leaderboard.sort(key=lambda x: x[1], reverse=True)
        top_ten = leaderboard[:10]

        embed = discord.Embed(
            title="🏆  Context Financial Capitalization Matrix Leaderboards",
            color=discord.Color.gold()
        )

        description_lines = []
        for index, (name, net) in enumerate(top_ten, start=1):
            description_lines.append(f"`#{index}` **{name}** — Net Assets: ${net:,}")

        embed.description = "\n".join(description_lines) if description_lines else "*No active economic profiles registered inside database systems.*"
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  📊 USER STATISTICS
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="stats", description="Audit your operational metric trackers and production milestones.")
    async def stats(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        data = load_economy()
        p = get_profile(data, target.id)
        save_economy(data)

        embed = discord.Embed(
            title=f"📈 Analytics Dossier: {target.display_name}",
            color=discord.Color.blue()
        )
        embed.add_field(name="💼 Career Shifts Worked", value=f"`{p.get('shifts_worked', 0)}` Shifts", inline=True)
        embed.add_field(name="⚓ Marine Extractions", value=f"`{p.get('fish_caught', 0)}` Fish Caught", inline=True)
        embed.add_field(name="🧺 Agricultural Harvests", value=f"`{p.get('harvests', 0)}` Crops Reaped", inline=True)
        embed.add_field(name="🎰 Risk Management Wins", value=f"`{p.get('gamble_wins', 0)}` System Bets Won", inline=True)
        embed.add_field(name="💵 Gross Capital Reaped", value=f"${p.get('total_earned', 0):,}", inline=True)
        embed.set_thumbnail(url=target.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🎰 GAMBLING
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="slots", description="Risk wallet funds inside a systemic matching row machine interface.")
    async def slots(self, interaction: discord.Interaction, bet: int):
        if bet <= 0:
            return await interaction.response.send_message("❌ Positive wager required.", ephemeral=True)
        data = load_economy()
        p = get_profile(data, interaction.user.id)
        if p["balance"] < bet:
            return await interaction.response.send_message(f"❌ You only have **${p['balance']:,}**.", ephemeral=True)

        symbols = ["💎", "🍒", "🍋", "🔔", "🌟", "🔥"]
        r1, r2, r3 = [random.choice(symbols) for _ in range(3)]

        if r1 == r2 == r3:
            payout = bet * 4
            color = discord.Color.gold()
            msg = f"🎉 **JACKPOT! (4x)** Earned {fmt_money(payout)}!"
        elif r1 == r2 or r2 == r3 or r1 == r3:
            payout = int(bet * 1.5)
            color = discord.Color.green()
            msg = f"✅ **Pair Match! (1.5x)** Earned {fmt_money(payout)}."
        else:
            payout = -bet
            color = discord.Color.red()
            msg = f"❌ **Lost.** Absorbed wager -${bet:,}."

        p["balance"] += payout
        if payout > 0:
            p["gamble_wins"] = p.get("gamble_wins", 0) + 1
        save_economy(data)

        embed = discord.Embed(
            title="🎰 Automaton Slots",
            description=f"{r1} {r2} {r3}\n\n{msg}\n💳 Wallet: **${p['balance']:,}**",
            color=color
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="coinflip", description="Execute binary probability allocation betting cycles.")
    @app_commands.choices(side=[app_commands.Choice(name="🦅 Heads", value="heads"), app_commands.Choice(name="👑 Tails", value="tails")])
    async def coinflip(self, interaction: discord.Interaction, bet: int, side: str):
        if bet <= 0:
            return await interaction.response.send_message("❌ Need positive wager.", ephemeral=True)
        data = load_economy()
        p = get_profile(data, interaction.user.id)
        if p["balance"] < bet:
            return await interaction.response.send_message(f"❌ You only have **${p['balance']:,}**.", ephemeral=True)

        outcome = random.choice(["heads", "tails"])
        if side == outcome:
            p["balance"] += bet
            p["gamble_wins"] = p.get("gamble_wins", 0) + 1
            color = discord.Color.green()
            msg = f"🎉 **Landed on {outcome.upper()}!** Won {fmt_money(bet)}!"
        else:
            p["balance"] -= bet
            color = discord.Color.red()
            msg = f"❌ **Landed on {outcome.upper()}.** Lost -{fmt_money(bet)}."

        save_economy(data)
        embed = discord.Embed(title="🪙 Coinflip Result", description=f"{msg}\n💳 Wallet: **${p['balance']:,}**", color=color)
        await interaction.response.send_message(embed=embed)

    # ══════════════════════════════════════════════════════
    #  🛡️ ADMIN UTILITIES
    # ══════════════════════════════════════════════════════
    @app_commands.command(name="givemoney", description="[Admin] Inject financial currency tokens into targeted user.")
    async def givemoney(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("🚫 Admin only.", ephemeral=True)
        data = load_economy()
        p = get_profile(data, member.id)
        p["balance"] += amount
        save_economy(data)
        await interaction.response.send_message(embed=discord.Embed(description=f"✅ Gave **${amount:,}** to {member.mention}.", color=discord.Color.green()))

    @app_commands.command(name="reseteconomy", description="[Admin] Erase data profiles for a user.")
    async def reseteconomy(self, interaction: discord.Interaction, member: discord.Member):
        if interaction.user.id != SUPERUSER_ID and not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("🚫 Admin only.", ephemeral=True)
        data = load_economy()
        if str(member.id) in data:
            del data[str(member.id)]
            save_economy(data)
        await interaction.response.send_message(embed=discord.Embed(description=f"🗑️ Cleared data for {member.mention}.", color=discord.Color.orange()), ephemeral=True)

# Module-level setup function to add the cog
async def setup(client):
    await client.add_cog(Work(client))