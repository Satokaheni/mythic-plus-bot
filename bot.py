import os
import discord
import pickle
from pathlib import Path
from discord import Member, Message
from typing import Dict, List
from datetime import datetime
from discord.ext import tasks
from dotenv import load_dotenv
from logging import getLogger, basicConfig, INFO
from raider import Raider
from schedule import Schedule
from utils import schedule_boiler, create_schedules, validate_role_message

# Set up Logging
logger = getLogger()
basicConfig(
    level=INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Global Variables
load_dotenv('.env')
CLIENT_ID = os.getenv('CLIENT_KEY')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

class MyClient(discord.Client):
    raiders: Dict[Member, Raider] = {}
    schedules: Dict[int, Schedule] = {}

    async def setup_hook(self):
        self.check_schedule.start()
    
    async def on_ready(self):
        raider_store = Path('raiders.pkl')
        if raider_store.is_file():
            with open('raiders.pkl', 'rb') as f:
                self.raiders = pickle.load(f)
        
        logger.info(f'Logged on as {self.user}!')

    async def on_message(self, message):
        if message.channel.id == CHANNEL_ID:
            if message.content[:5] == '!role':
                if not message.author.id in self.raiders:
                    raider = validate_role_message(message)
                    if raider:
                        self.raiders[message.author.id] = raider
                        with open('raiders.pkl', 'wb') as f:
                            pickle.dump(self.raiders, f)
                    else:
                        logger.warning(f'Incorrect usage of !role command by {message.author}. Content: {message.content}')
                        await message.reply('Improper !role command', mention_author=True)
                        
            if message.content.lower() == '!sched':
                channel = self.get_channel(CHANNEL_ID)
                # if datetime.now().weekday() == 1:
                await channel.send(schedule_boiler)
                self.schedules = {}
                sched: List[Schedule] = create_schedules()
                for s in sched:
                    mid = await channel.send(s.send_message())
                    self.schedules[mid.id] = s
                # else:
                #     await message.reply('Cannot use that command unless it is reset day (Tuesday)', mention_author=True)

    async def on_reaction_add(self, reaction, user):
        message: Message = reaction.message
        if message.channel.id == CHANNEL_ID and message.id in self.schedules:
            if reaction.emoji == '✅':
                if not user.id in self.raiders:
                    logger.warning(f'User: {user.display_name} did not use the !role command first')
                    await self.get_channel(CHANNEL_ID).send(f'{user.mention} you did not use the !role command first please remove your reaction and run the !role command before reacting to a schedule')
                else:
                    self.schedules[message.id].raider_signup(self.raiders[user.id])
                    await message.edit(content=self.schedules[message.id].send_message())
            elif reaction.emoji == '❌':
                if not user.id in self.raiders:
                    logger.warning(f'User: {user.display_name} did not use the !role command first and removed their reaction')
                else:
                    self.schedules[message.id].raider_remove(self.raiders[user.id])
                    await message.edit(content=self.schedules[message.id].send_message())
                
            

    @tasks.loop(minutes=60.0)
    async def check_schedule(self):
        logger.info('Check Schedule Loop Executed')
        current = datetime.now()
        for _, sched in self.schedules.items():
            time_until = (sched.date - current).total_seconds()
            if time_until < (120*60) and sched.full:
                channel = self.get_channel(CHANNEL_ID)
                await channel.send(f"{sched.send_reminder()} in {time_until//60} minutes")
        pass
    

intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run(CLIENT_ID)