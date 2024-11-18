import discord
from discord.ext import commands
import anthropic
import os
import json
from datetime import datetime
import aiohttp
import asyncio

def load_data():
    try:
        with open('user_games.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_data(data):
    with open('user_games.json', 'w') as f:
        json.dump(data, f, indent=4)

class GameTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.data = load_data()
        self.steam_api_key = os.getenv('STEAM_API_KEY')

    async def fetch_steam_achievements(self, game_name):
        """Fetch achievements from Steam"""
        try:
            async with aiohttp.ClientSession() as session:
                # Search for the game
                search_url = f"https://api.steampowered.com/ISteamApps/GetAppList/v2/"
                async with session.get(search_url) as response:
                    apps = await response.json()
                    
                # Find the game ID
                game_id = None
                for app in apps['applist']['apps']:
                    if game_name.lower() in app['name'].lower():
                        game_id = app['appid']
                        break
                        
                if not game_id:
                    return []

                # Get achievements
                schema_url = f"https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/?key={self.steam_api_key}&appid={game_id}"
                async with session.get(schema_url) as response:
                    schema = await response.json()

                achievements = schema.get('game', {}).get('availableGameStats', {}).get('achievements', [])

                # Get achievement stats
                stats_url = f"https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid={game_id}"
                async with session.get(stats_url) as response:
                    stats = await response.json()

                # Add rarity to achievements
                for ach in achievements:
                    for stat in stats.get('achievementpercentages', {}).get('achievements', []):
                        if ach['name'] == stat['name']:
                            ach['rarity'] = stat['percent']

                return achievements
        except Exception as e:
            print(f"Steam API error: {e}")
            return []

    async def generate_guide(self, game_name, achievements):
        """Generate a completion guide using Claude"""
        context = f"""
        Create a detailed completion guide for {game_name} with these achievements:
        {[ach['name'] for ach in achievements]}

        Include:
        1. Optimal achievement order
        2. Any missable achievements
        3. Prerequisites or requirements
        4. Estimated completion time
        5. Tips and strategies

        Format with clear sections and bullet points.
        """

        response = self.client.messages.create(
            model="claude-3-sonnet-20240229",
            max_tokens=2048,
            temperature=0.7,
            messages=[{
                "role": "user",
                "content": context
            }]
        )
        
        return response.content

    @commands.group(invoke_without_command=True)
    async def game(self, ctx):
        """Show available game commands."""
        embed = discord.Embed(
            title="🎮 Game Completion Tracker",
            description="**Track your journey to 100% completion!**",
            color=discord.Color.blue()
        )
        
        commands_text = (
            "**Essential Commands:**\n"
            "• `!game add <game_name>` - Start tracking a game\n"
            "• `!game show <game_name>` - View progress\n"
            "• `!game check <game_name> <ID>` - Mark achievements\n\n"
            "**Additional Commands:**\n"
            "• `!game list` - See all tracked games\n"
            "• `!game remove <game_name>` - Stop tracking a game"
        )
        
        embed.add_field(name="📋 Command Guide", value=commands_text, inline=False)
        await ctx.send(embed=embed)

    @game.command(name="add")
    async def add_game(self, ctx, *, game_name: str):
        """Add a game and fetch its achievements."""
        user_id = str(ctx.author.id)
        
        if user_id not in self.data:
            self.data[user_id] = {}
            
        if game_name.lower() in self.data[user_id]:
            await ctx.send("❌ **You're already tracking this game!**")
            return

        status_message = await ctx.send("🔍 **Searching for game achievements...**")
        
        try:
            # Fetch achievements from Steam
            achievements = await self.fetch_steam_achievements(game_name)
            
            # Generate completion guide
            guide = await self.generate_guide(game_name, achievements)
            
            # Initialize game data
            self.data[user_id][game_name.lower()] = {
                "name": game_name,
                "achievements": {},
                "guide": guide,
                "progress": 0,
                "started_date": str(datetime.now())
            }
            
            # Add achievements
            for i, ach in enumerate(achievements, 1):
                self.data[user_id][game_name.lower()]["achievements"][str(i)] = {
                    "name": ach["name"],
                    "description": ach.get("description", "No description available"),
                    "rarity": ach.get("rarity", 0),
                    "completed": False,
                    "date_added": str(datetime.now())
                }
            
            save_data(self.data)
            
            embed = discord.Embed(
                title="🎮 Game Added Successfully!",
                color=discord.Color.green()
            )
            
            description = (
                f"**Game:** {game_name}\n"
                f"**Achievements Found:** {len(achievements)}\n\n"
                "**Next Steps:**\n"
                f"• Use `!game show {game_name}` to view achievements\n"
                f"• Use `!game check {game_name} <ID>` to mark completions"
            )
            
            embed.description = description
            await status_message.edit(content=None, embed=embed)
            
        except Exception as e:
            print(f"Error adding game: {e}")
            embed = discord.Embed(
                title="🎮 Game Added (Manual Mode)",
                color=discord.Color.yellow()
            )
            
            description = (
                f"**Game Added:** {game_name}\n\n"
                "**Note:** Couldn't fetch achievements automatically.\n"
                "You'll need to check achievements manually.\n"
                f"Use `!game show {game_name}` to view progress"
            )
            
            embed.description = description
            await status_message.edit(content=None, embed=embed)

    @game.command(name="show")
    async def show_game(self, ctx, *, game_name: str):
        """Show game progress and achievements."""
        user_id = str(ctx.author.id)
        game_name = game_name.lower()
        
        if user_id not in self.data or game_name not in self.data[user_id]:
            await ctx.send("❌ **Game not found!** Use `!game add <game_name>` first.")
            return
            
        game = self.data[user_id][game_name]
        achievements = game["achievements"]
        
        if achievements:
            completed = sum(1 for ach in achievements.values() if ach["completed"])
            total = len(achievements)
            percentage = (completed / total) * 100
        else:
            percentage = 0
            
        progress_bar = "▰" * int(percentage / 10) + "▱" * (10 - int(percentage / 10))
        
        embed = discord.Embed(
            title=f"🎮 {game['name']} Progress",
            color=discord.Color.blue()
        )
        
        # Add progress section
        embed.add_field(
            name="📊 Progress",
            value=f"{progress_bar} {percentage:.1f}%",
            inline=False
        )
        
        # Sort achievements by completion and rarity
        sorted_achievements = sorted(
            achievements.items(),
            key=lambda x: (-x[1]["completed"], x[1].get("rarity", 0))
        )
        
        # Split into completed and incomplete
        incomplete = []
        completed = []
        
        for ach_id, ach in sorted_achievements:
            status = "☑️" if ach["completed"] else "⬜"
            rarity = f"({ach.get('rarity', 0):.1f}%)" if "rarity" in ach else ""
            text = f"{status} `{ach_id}` **{ach['name']}** {rarity}\n└ *{ach['description']}*\n"
            
            if ach["completed"]:
                completed.append(text)
            else:
                incomplete.append(text)

        # Add achievements to embed
        if incomplete:
            incomplete_text = "**Remaining Achievements:**\n" + "\n".join(incomplete)
            if len(incomplete_text) > 1024:
                chunks = [incomplete_text[i:i+1024] for i in range(0, len(incomplete_text), 1024)]
                for i, chunk in enumerate(chunks):
                    embed.add_field(
                        name="📝 Remaining" if i == 0 else "\u200b",
                        value=chunk,
                        inline=False
                    )
            else:
                embed.add_field(name="📝 Remaining", value=incomplete_text, inline=False)

        if completed:
            completed_text = "**Completed Achievements:**\n" + "\n".join(completed)
            if len(completed_text) > 1024:
                chunks = [completed_text[i:i+1024] for i in range(0, len(completed_text), 1024)]
                for i, chunk in enumerate(chunks):
                    embed.add_field(
                        name="✅ Completed" if i == 0 else "\u200b",
                        value=chunk,
                        inline=False
                    )
            else:
                embed.add_field(name="✅ Completed", value=completed_text, inline=False)

        await ctx.send(embed=embed)

        # Send guide if it exists
        if guide := game.get("guide"):
            guide_embed = discord.Embed(
                title="📘 Completion Guide",
                description=guide[:4096],  # Discord's limit
                color=discord.Color.green()
            )
            await ctx.send(embed=guide_embed)

    @game.command(name="check")
    async def toggle_achievement(self, ctx, game_name: str, achievement_id: str):
        """Toggle achievement completion."""
        user_id = str(ctx.author.id)
        game_name = game_name.lower()
        
        if user_id not in self.data or game_name not in self.data[user_id]:
            await ctx.send("❌ **Game not found!**")
            return
            
        if achievement_id not in self.data[user_id][game_name]["achievements"]:
            await ctx.send("❌ **Achievement not found!**")
            return
            
        # Toggle achievement
        achievement = self.data[user_id][game_name]["achievements"][achievement_id]
        achievement["completed"] = not achievement["completed"]
        
        # Calculate new percentage
        achievements = self.data[user_id][game_name]["achievements"]
        completed = sum(1 for ach in achievements.values() if ach["completed"])
        total = len(achievements)
        percentage = (completed / total) * 100
        
        save_data(self.data)
        
        # Send update
        embed = discord.Embed(
            title="🎯 Achievement Update",
            description=(
                f"**Achievement:** {achievement['name']}\n"
                f"**Status:** {'Completed ✅' if achievement['completed'] else 'Unchecked ⬜'}\n"
                f"**Progress:** {percentage:.1f}% ({completed}/{total} achievements)"
            ),
            color=discord.Color.green() if achievement["completed"] else discord.Color.red()
        )
        await ctx.send(embed=embed)

    @game.command(name="list")
    async def list_games(self, ctx):
        """Show all tracked games."""
        user_id = str(ctx.author.id)
        
        if user_id not in self.data or not self.data[user_id]:
            await ctx.send("❌ **You're not tracking any games!** Use `!game add <game_name>` to start.")
            return
            
        embed = discord.Embed(
            title="🎮 Your Game Collection",
            description="**Here are all your tracked games:**\n",
            color=discord.Color.blue()
        )
        
        for game_name, game in self.data[user_id].items():
            achievements = game["achievements"]
            if achievements:
                completed = sum(1 for ach in achievements.values() if ach["completed"])
                total = len(achievements)
                percentage = (completed / total) * 100
                progress_bar = "▰" * int(percentage / 10) + "▱" * (10 - int(percentage / 10))
            else:
                percentage = 0
                progress_bar = "▱" * 10
                completed = 0
                total = 0
                
            game_text = (
                f"**Progress:** {progress_bar} {percentage:.1f}%\n"
                f"**Achievements:** {completed}/{total}\n"
                "─────────────────"
            )
            embed.add_field(name=f"📌 {game['name']}", value=game_text, inline=False)
            
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(GameTracker(bot))
