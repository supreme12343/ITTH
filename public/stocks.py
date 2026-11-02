import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import random
from datetime import datetime

# ================== DATA CONFIG ==================
ECONOMY_FILE = "economy.json"
STOCKS_FILE = "stocks_data.json"

# Stock Configurations with custom imagery and descriptions
STOCKS_CONFIG = {
    "EDWIN": {
        "name": "Edwin's Meat Co.",
        "emoji": "🥩",
        "base_price": 100.0,
        "desc": "Leading global supplier of premium raw meats and legendary server beatings.",
        "image_url": "https://i.imgur.com/7pM6S6G.png"
    },
    "ATERNOS": {
        "name": "Server Lag Futures",
        "emoji": "🔌",
        "base_price": 50.0,
        "desc": "A volatile short-index tracking active server loading queues and unexplainable crashes.",
        "image_url": "https://i.imgur.com/O6Sj9bA.png"
    },
    "COIN": {
        "name": "Meme Doge Crypto",
        "emoji": "🪙",
        "base_price": 10.0,
        "desc": "Pure speculative internet gold backed by absolutely nothing except hype.",
        "image_url": "https://i.imgur.com/bY33Teb.png"
    },
    "BRROT": {
        "name": "Skibidi Brainrot Corp",
        "emoji": "🧠",
        "base_price": 250.0,
        "desc": "Generates record high attention spans through hyper-stimulating content loops.",
        "image_url": "https://i.imgur.com/S9pTzZ0.png"
    },
    "GRND": {
        "name": "Sigma Grindset LLC",
        "emoji": "🏋️",
        "base_price": 500.0,
        "desc": "Consultancy specialized in ignoring family, working 28 hours a day, and holding bags.",
        "image_url": "https://i.imgur.com/C3fE77C.png"
    }
}

# ================== DATA MANAGEMENT ==================
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

def get_profile(data, user_id):
    uid = str(user_id)
    if uid not in data:
        data[uid] = {}
    p = data[uid]
    p.setdefault("balance", 100)
    p.setdefault("stocks", {}) # Format: {"TICKER": SharesAmount}
    return p

def load_stocks():
    if not os.path.exists(STOCKS_FILE):
        # Initialize default stock prices and history
        data = {}
        for ticker, config in STOCKS_CONFIG.items():
            data[ticker] = {
                "price": config["base_price"],
                "history": [config["base_price"]] * 10,
                "event": "Stable Market"
            }
        save_stocks(data)
        return data
    try:
        with open(STOCKS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_stocks(data):
    with open(STOCKS_FILE, "w") as f:
        json.dump(data, f, indent=4)


# ================== CHART GENERATOR ==================
def generate_pixel_chart(history):
    """
    Renders a 5x10 visual trend chart using colored emojis.
    Replicates the Wordle grid aesthetic inside Discord Embeds.
    """
    cols = 10
    rows = 5

    # Pad or slice history to fit exactly 10 data points
    pts = list(history)
    while len(pts) < cols:
        pts.insert(0, pts[0])
    if len(pts) > cols:
        pts = pts[-cols:]

    min_p = min(pts)
    max_p = max(pts)
    span = max_p - min_p

    # Flatline handler
    if span == 0:
        grid = [["⬛" for _ in range(cols)] for _ in range(rows)]
        for c in range(cols):
            grid[rows // 2][c] = "🟦"
        return "\n".join("".join(row) for row in grid)

    # Normalize values into row indexes (0 to rows-1)
    normalized = []
    for p in pts:
        ratio = (p - min_p) / span
        row_idx = int(ratio * (rows - 1))
        normalized.append(row_idx)

    grid = [["⬛" for _ in range(cols)] for _ in range(rows)]

    # Render coordinate values along the columns
    for c in range(cols):
        r_idx = normalized[c]
        if c == 0:
            color = "🟦" # Anchor starting color
        else:
            color = "🟩" if pts[c] >= pts[c-1] else "🟥" # Up trend vs Down trend

        grid[rows - 1 - r_idx][c] = color

    return "\n".join("".join(row) for row in grid)


# ================== CORE COG CLASS ==================
class Stocks(commands.Cog):
    def __init__(self, client):
        self.client = client
        self.market_loop.start()

    def cog_unload(self):
        self.market_loop.cancel()

    # ================== MARKET SIMULATION LOOP ==================
    @tasks.loop(seconds=60.0)
    async def market_loop(self):
        """Fluctuates prices and logs history records every minute."""
        stocks = load_stocks()
        global_event = None

        # 5% chance for a server-wide economic catalyst
        event_roll = random.random()
        if event_roll < 0.05:
            global_event = random.choice(["BOOM", "CRASH", "STIMULUS"])

        for ticker in stocks:
            stock = stocks[ticker]
            curr_price = stock["price"]

            # Base organic movement coefficient (-4.5% to +5.5%)
            multiplier = 1.0 + random.uniform(-0.045, 0.055)
            stock["event"] = "Normal Fluctuations"

            # Apply global event modifiers
            if global_event == "BOOM":
                multiplier = random.uniform(1.10, 1.30)
                stock["event"] = "⚡ GLOBAL BULL RUN"
            elif global_event == "CRASH":
                multiplier = random.uniform(0.60, 0.80)
                stock["event"] = "📉 GLOBAL MARKET CRASH"
            elif global_event == "STIMULUS":
                multiplier = random.uniform(1.05, 1.15)
                stock["event"] = "🏛️ GOVERNMENT STIMULUS"
            else:
                # 3% chance for localized anomaly (Rugpull / To the moon)
                local_roll = random.random()
                if local_roll < 0.03:
                    if random.random() < 0.5:
                        multiplier = random.uniform(0.05, 0.20)
                        stock["event"] = "🚨 RUGPULL EVENT"
                    else:
                        multiplier = random.uniform(2.50, 4.50)
                        stock["event"] = "🚀 TO THE MOON"

            # Apply final price adjustment
            new_price = round(max(0.1, curr_price * multiplier), 2)
            stock["price"] = new_price

            # Log history
            stock["history"].append(new_price)
            if len(stock["history"]) > 10:
                stock["history"].pop(0)

        save_stocks(stocks)

    @market_loop.before_loop
    async def before_market_loop(self):
        await self.client.wait_until_ready()

    # ================== INTERACTIVE COMMANDS ==================
    stocks_group = app_commands.Group(name="stocks", description="Trade, view, and analyze speculative meme shares.")

    @stocks_group.command(name="board", description="Inspect the active meme market board directory.")
    async def stocks_board(self, interaction: discord.Interaction):
        stocks = load_stocks()
        embed = discord.Embed(
            title="📈 Speculative Meme Asset Directory",
            description="The market ticks and shifts every **60 seconds**. Utilize `/stocks view` to analyze detailed graphs.",
            color=discord.Color.blue()
        )

        for ticker, data in stocks.items():
            config = STOCKS_CONFIG[ticker]
            hist = data["history"]
            change = ((data["price"] - hist[-2]) / hist[-2]) * 100 if len(hist) >= 2 else 0.0
            trend_emoji = "📈" if change >= 0 else "📉"

            field_name = f"{config['emoji']} ${ticker} ({config['name']})"
            field_value = (
                f"Value: **${data['price']:,}**\n"
                f"Last Tick Trend: {trend_emoji} `{change:+.2f}%`\n"
                f"Operational State: *{data.get('event', 'Stable')}*"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)

        await interaction.response.send_message(embed=embed)

    @stocks_group.command(name="view", description="Analyze a specific coin's Wordle-style graph chart and imagery.")
    @app_commands.describe(ticker="Target coin sticker symbol")
    @app_commands.choices(ticker=[
        app_commands.Choice(name=f"{v['emoji']} ${k}", value=k)
        for k, v in STOCKS_CONFIG.items()
    ])
    async def stocks_view(self, interaction: discord.Interaction, ticker: str):
        stocks = load_stocks()
        if ticker not in stocks:
            return await interaction.response.send_message("❌ Target coin ticker not recognized.", ephemeral=True)

        data = stocks[ticker]
        config = STOCKS_CONFIG[ticker]
        history = data["history"]

        # Calculate visual 5x10 Wordle-Style chart representation
        visual_chart = generate_pixel_chart(history)

        # Calculate high, low, and percentage statistics
        high_24h = max(history)
        low_24h = min(history)
        change_10t = ((data["price"] - history[0]) / history[0]) * 100 if history[0] > 0 else 0.0

        # User current holdings checking
        eco = load_economy()
        p = get_profile(eco, interaction.user.id)
        shares_owned = p["stocks"].get(ticker, 0)

        embed = discord.Embed(
            title=f"{config['emoji']} ${ticker}: {config['name']}",
            description=f"*{config['desc']}*\n\n📈 **Trend Visualizer (Last 10 Ticks):**\n```\n{visual_chart}\n```\n`🟦 Start`  `🟩 Upward`  `🟥 Downward`  `⬛ Space`",
            color=discord.Color.green() if change_10t >= 0 else discord.Color.red()
        )

        embed.add_field(name="💵 Current Share Value", value=f"**${data['price']:,}**", inline=True)
        embed.add_field(name="🕒 10-Tick Trend", value=f"`{change_10t:+.2f}%`", inline=True)
        embed.add_field(name="📊 Operational State", value=f"*{data.get('event', 'Normal')}*", inline=True)
        embed.add_field(name="📈 10-Tick High", value=f"${high_24h:,}", inline=True)
        embed.add_field(name="📉 10-Tick Low", value=f"${low_24h:,}", inline=True)
        embed.add_field(name="💼 Stored Holdings", value=f"`{shares_owned}` Shares (Valued at **${shares_owned * data['price']:,}**)", inline=True)

        # Display the coin image
        embed.set_thumbnail(url=config["image_url"])
        embed.set_footer(text="Prices update live in general memory buffers every 60s.")

        await interaction.response.send_message(embed=embed)

    @stocks_group.command(name="buy", description="Acquire speculative shares utilizing your cash balances.")
    @app_commands.describe(ticker="Symbol to purchase", amount="Exact share count or keyphrase 'all'")
    @app_commands.choices(ticker=[
        app_commands.Choice(name=f"{v['emoji']} ${k}", value=k)
        for k, v in STOCKS_CONFIG.items()
    ])
    async def stocks_buy(self, interaction: discord.Interaction, ticker: str, amount: str):
        stocks = load_stocks()
        if ticker not in stocks:
            return await interaction.response.send_message("❌ Target coin ticker not recognized.", ephemeral=True)

        data = load_economy()
        p = get_profile(data, interaction.user.id)
        share_value = stocks[ticker]["price"]

        if amount.lower() == "all":
            shares_to_buy = int(p["balance"] // share_value)
        else:
            try:
                shares_to_buy = int(amount)
            except ValueError:
                return await interaction.response.send_message("❌ Amount parameter must represent an integer or 'all'.", ephemeral=True)

        if shares_to_buy <= 0:
            return await interaction.response.send_message("❌ Transaction amounts must exceed zero shares.", ephemeral=True)

        total_cost = round(shares_to_buy * share_value, 2)
        if p["balance"] < total_cost:
            return await interaction.response.send_message(f"❌ Purchase insolvency! `{shares_to_buy}` shares requires **${total_cost:,}**, but wallet balance holds **${p['balance']:,}**.", ephemeral=True)

        p["balance"] -= total_cost
        p["stocks"][ticker] = p["stocks"].get(ticker, 0) + shares_to_buy

        save_economy(data)

        embed = discord.Embed(
            title="💳 Share Purchase Confirmed",
            description=f"You successfully acquired `{shares_to_buy}` shares of **${ticker}** at **${share_value:,}** per share.\n\n"
                        f"💰 Total Debited: **-${total_cost:,}**\n"
                        f"💼 Stored Share Portfolio: `{p['stocks'][ticker]}` shares.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=STOCKS_CONFIG[ticker]["image_url"])
        await interaction.response.send_message(embed=embed)

    @stocks_group.command(name="sell", description="Liquidate stock positions back into cash.")
    @app_commands.describe(ticker="Symbol to sell", amount="Exact share count or keyphrase 'all'")
    @app_commands.choices(ticker=[
        app_commands.Choice(name=f"{v['emoji']} ${k}", value=k)
        for k, v in STOCKS_CONFIG.items()
    ])
    async def stocks_sell(self, interaction: discord.Interaction, ticker: str, amount: str):
        stocks = load_stocks()
        if ticker not in stocks:
            return await interaction.response.send_message("❌ Target coin ticker not recognized.", ephemeral=True)

        data = load_economy()
        p = get_profile(data, interaction.user.id)
        shares_held = p["stocks"].get(ticker, 0)

        if shares_held <= 0:
            return await interaction.response.send_message(f"❌ You do not own any shares of **${ticker}**.", ephemeral=True)

        if amount.lower() == "all":
            shares_to_sell = shares_held
        else:
            try:
                shares_to_sell = int(amount)
            except ValueError:
                return await interaction.response.send_message("❌ Amount parameter must represent an integer or 'all'.", ephemeral=True)

        if shares_to_sell <= 0:
            return await interaction.response.send_message("❌ Transaction amounts must exceed zero shares.", ephemeral=True)

        if shares_to_sell > shares_held:
            return await interaction.response.send_message(f"❌ Transaction rejected. Your portfolio only holds `{shares_held}` shares of **${ticker}**.", ephemeral=True)

        share_value = stocks[ticker]["price"]
        total_earnings = round(shares_to_sell * share_value, 2)

        p["balance"] += total_earnings
        p["stocks"][ticker] -= shares_to_sell

        # Clear dictionary entry if count drops to zero
        if p["stocks"][ticker] <= 0:
            del p["stocks"][ticker]

        save_economy(data)

        embed = discord.Embed(
            title="💵 Positions Liquidated",
            description=f"You successfully sold `{shares_to_sell}` shares of **${ticker}** at **${share_value:,}** per share.\n\n"
                        f"💰 Total Credited: **+${total_earnings:,}**\n"
                        f"💼 Stored Share Portfolio: `{p['stocks'].get(ticker, 0)}` shares left.",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=STOCKS_CONFIG[ticker]["image_url"])
        await interaction.response.send_message(embed=embed)


async def setup(client):
    await client.add_cog(Stocks(client))