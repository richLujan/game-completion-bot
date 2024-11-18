import os
import discord
from discord.ext import commands
from bot import setup

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await setup(bot)
    print("Game tracker loaded!")

if __name__ == "__main__":
    bot.run(os.getenv('DISCORD_TOKEN'))
