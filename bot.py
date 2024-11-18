import discord
from discord.ext import commands
import anthropic
import os
import json
from datetime import datetime
import requests
import time
import aiohttp
import asyncio
from bs4 import BeautifulSoup

class GameTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.client = anthropic.Anthropic(api_key=os.getenv('ANTHROPIC_API_KEY'))
        self.data = load_data()
        # API credentials
        self.igdb_client_id = os.getenv('TWITCH_CLIENT_ID')
        self.igdb_client_secret = os.getenv('TWITCH_CLIENT_SECRET')
        self.steam_api_key = os.getenv('STEAM_API_KEY')
        self.access_token = None
        self.token_expires = 0

    async def fetch_steam_achievements(self, game_name):
        """Fetch achievements from Steam"""
        async with aiohttp.ClientSession() as session:
            # First search for the game
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

            # Get achievements for the game
            schema_url = f"https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/?key={self.steam_api_key}&appid={game_id}"
            async with session.get(schema_url) as response:
                schema = await response.json()

            if 'game' not in schema or 'availableGameStats' not in schema['game']:
                return []

            achievements = schema['game']['availableGameStats'].get('achievements', [])
            
            # Get global achievement stats for rarity
            stats_url = f"https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid={game_id}"
            async with session.get(stats_url) as response:
                global_stats = await response.json()

            # Merge achievement info with rarity
            for ach in achievements:
                for stat in global_stats.get('achievementpercentages', {}).get('achievements', []):
                    if ach['name'] == stat['name']:
                        ach['rarity'] = stat['percent']

            return achievements

    async def fetch_walkthrough(self, game_name):
        """Fetch walkthrough information"""
        # Use Claude to generate a structured walkthrough
        context = f"""
        Create a detailed walkthrough guide for achieving 100% completion in {game_name}.
        Include:
        1. Main story progression
        2. Optimal order for side content
        3. Missable achievements/items
        4. Recommended character builds or strategies
        5. Key items and their locations
        6. End-game content
        
        Format with clear headers, bullet points, and sections.
        Focus on efficiency and preventing missable content.
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

    async def get_game_info(self, game_name):
        """Fetch additional game information like release date, genres, etc."""
        token = self.get_access_token()
        
        game_search_url = "https://api.igdb.com/v4/games"
        headers = {
            "Client-ID": self.igdb_client_id,
            "Authorization": f"Bearer {token}"
        }
        
        body = f"""
        search "{game_name}";
        fields name,genres.name,first_release_date,summary,platforms.name,aggregated_rating;
        limit 1;
        """
        
        async with aiohttp.ClientSession() as session:
            async with session.post(game_search_url, headers=headers, data=body) as response:
                games = await response.json()
                
        return games[0] if games else None

    @game.command(name="add")
    async def add_game(self, ctx, *, game_name: str):
        """Start tracking a new game with auto-fetched achievements and guides."""
        user_id = str(ctx.author.id)
        
        if user_id not in self.data:
            self.data[user_id] = {}
            
        if game_name.lower() in self.data[user_id]:
            await ctx.send("❌ **You're already tracking this game!**")
            return

        status_message = await ctx.send("🔍 **Initializing game tracking...**")
        
        try:
            # Fetch from multiple sources concurrently
            tasks = [
                self.fetch_steam_achievements(game_name),
                self.fetch_igdb_achievements(game_name),
                self.get_game_info(game_name),
                self.fetch_walkthrough(game_name)
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            steam_achievements, igdb_achievements, game_info, walkthrough = results
            
            # Combine achievements from both sources
            all_achievements = []
            seen_names = set()
            
            for ach in steam_achievements:
                if ach['name'] not in seen_names:
                    all_achievements.append({
                        'name': ach['name'],
                        'description': ach.get('description', ''),
                        'rarity': ach.get('rarity', 0),
                        'source': 'Steam'
                    })
                    seen_names.add(ach['name'])
                    
            for ach in igdb_achievements:
                if ach['name'] not in seen_names:
                    all_achievements.append({
                        'name': ach['name'],
                        'description': ach.get('description', ''),
                        'source': 'IGDB'
                    })
                    seen_names.add(ach['name'])

            # Initialize game data
            self.data[user_id][game_name.lower()] = {
                "name": game_name,
                "achievements": {},
                "progress": 0,
                "started_date": str(datetime.now()),
                "game_info": game_info,
                "walkthrough": walkthrough
            }
            
            # Add achievements
            for i, ach in enumerate(all_achievements, 1):
                self.data[user_id][game_name.lower()]["achievements"][str(i)] = {
                    "name": ach["name"],
                    "description": ach["description"],
                    "completed": False,
                    "rarity": ach.get("rarity", None),
                    "source": ach["source"],
                    "date_added": str(datetime.now())
                }
            
            save_data(self.data)
            
            # Create detailed embed response
            embed = discord.Embed(
                title=f"🎮 {game_name} Added Successfully!",
                color=discord.Color.green()
            )
            
            if game_info:
                embed.add_field(
                    name="Game Information",
                    value=(
                        f"**Release Date:** {datetime.fromtimestamp(game_info.get('first_release_date', 0)).strftime('%Y-%m-%d')}\n"
                        f"**Rating:** {game_info.get('aggregated_rating', 'N/A'):.1f}/100\n"
                        f"**Platforms:** {', '.join(p['name'] for p in game_info.get('platforms', []))}\n"
                        f"**Genres:** {', '.join(g['name'] for g in game_info.get('genres', []))}"
                    ),
                    inline=False
                )
            
            embed.add_field(
                name="Achievements Found",
                value=(
                    f"**Total Achievements:** {len(all_achievements)}\n"
                    f"**Steam Achievements:** {len(steam_achievements)}\n"
                    f"**IGDB Achievements:** {len(igdb_achievements)}"
                ),
                inline=False
            )
            
            embed.add_field(
                name="Available Commands",
                value=(
                    f"• `!game show {game_name}` - View achievements and progress\n"
                    f"• `!game guide {game_name}` - View completion guide\n"
                    f"• `!game check {game_name} <ID>` - Mark achievements complete"
                ),
                inline=False
            )
            
            await status_message.edit(content=None, embed=embed)
            
        except Exception as e:
            # Fallback to manual mode
            self.data[user_id][game_name.lower()] = {
                "name": game_name,
                "achievements": {},
                "progress": 0,
                "started_date": str(datetime.now())
            }
            save_data(self.data)
            
            embed = discord.Embed(
                title="🎮 Game Added (Manual Mode)",
                color=discord.Color.yellow()
            )
            
            description = (
                f"**Game Added:** {game_name}\n\n"
                "**Note:** Couldn't fetch achievements automatically.\n"
                "You can add them manually using:\n"
                f"• `!game achievement {game_name} <achievement_name>`\n"
                f"• `!game show {game_name}` to view progress"
            )
            
            embed.description = description
            await status_message.edit(content=None, embed=embed)

    @game.command(name="guide")
    async def show_guide(self, ctx, *, game_name: str):
        """Show the completion guide for a game."""
        user_id = str(ctx.author.id)
        game_name = game_name.lower()
        
        if user_id not in self.data or game_name not in self.data[user_id]:
            await ctx.send("❌ **Game not found!** Use `!game add <game_name>` first.")
            return
            
        game = self.data[user_id][game_name]
        walkthrough = game.get("walkthrough")
        
        if not walkthrough:
            # Generate new walkthrough if none exists
            walkthrough = await self.fetch_walkthrough(game["name"])
            self.data[user_id][game_name]["walkthrough"] = walkthrough
            save_data(self.data)
        
        # Split walkthrough into chunks if needed
        chunks = [walkthrough[i:i+4096] for i in range(0, len(walkthrough), 4096)]
        
        for i, chunk in enumerate(chunks):
            embed = discord.Embed(
                title=f"📘 {game['name']} - Completion Guide {f'({i+1}/{len(chunks)})' if len(chunks) > 1 else ''}",
                description=chunk,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

    # ... (rest of the existing commands remain the same)

async def setup(bot):
    await bot.add_cog(GameTracker(bot))
