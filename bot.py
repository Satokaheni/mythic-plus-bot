import discord
import pickle
from typing import Dict, List
from datetime import datetime
from discord.ext import tasks
from dotenv import load_dotenv
from logging import getLogger
from raider import Raider
from schedule import Schedule
from utils import message, create_schedules

logging = getLogger(__name__)
ak = load_dotenv('.env')

raiders = {}
schedules: Dict[int, Schedule] = {}

class MyClient(discord.Client):
    async def setup_hook(self):
        self.check_schedule.start()
    
    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    async def on_message(self, message):
        print(f'Message from {message.author}. Member Roles: {message.author.roles}. Message: {message.content}. Channel: {message.channel}')

        if message.channel.id == ak['CHANNEL_ID']:
            if message.content[:5] == '!role':
                if not message.author in raiders:
                    m = message.content.split(' ')
                    raider = Raider(message.author, message[1], message[2:])
                    
                    raiders[message.author] = raider
                    with open('raiders.pkl', 'wb') as f:
                        pickle.dump(raiders, f)
                        
            if message.content[:6] == '!sched':
                channel = self.get_channel(ak['CHANNEL_ID'])
                # if datetime.now().weekday() == 1:
                schedules = {}
                sched: List[Schedule] = create_schedules()
                await channel.send(message)
                for s in sched:
                    mid = await channel.send(s.send_message())
                    schedules[mid] = s
                # else:
                #     await channel.send('Cannot use that command unless it is reset day (Tuesday)')
                

    async def on_reaction_add(self, reaction, user):
        print(f'Reaction added: {reaction}. From User: {user}')
        
    @tasks.loop(minutes=60.0)
    async def check_schedule(self):
        current = datetime.now()
        for _, sched in schedules.items():
            time_until = (sched.date - current).total_seconds()
            if time_until < (120*60) and sched.full:
                channel = self.get_channel(ak['CHANNEL_ID'])
                await channel.send(f"{sched.send_reminder()} in {time_until//60} minutes")
        pass
    

intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run(ak['CLIENT_KEY'])