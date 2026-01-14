import discord
import pickle
from dotenv import load_dotenv
from logging import getLogger
from raider import Raider

logging = getLogger(__name__)
ak = load_dotenv('.env')

raiders = {}

class MyClient(discord.Client):
    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    async def on_message(self, message):
        print(f'Message from {message.author}. Member Roles: {message.author.roles}. Message: {message.content}. Channel: {message.channel}')

        if message.channel == 'mythic-plus':
            if message.content[:5] == '!role':
                if not message.author in raiders:
                    m = message.content.split(' ')
                    raider = Raider(message.author, message[1], message[2:])
                    
                    raiders[message.author] = raider
                    with open('raiders.pkl', 'wb') as f:
                        pickle.dump(raiders, f)
            
                    

    async def on_reaction_add(self, reaction, user):
        print(f'Reaction added: {reaction}. From User: {user}')


intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run(ak['CLIENT_KEY'])