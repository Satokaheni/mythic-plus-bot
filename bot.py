import discord

raiders = {}

class MyClient(discord.Client):
    async def on_ready(self):
        print(f'Logged on as {self.user}!')

    async def on_message(self, message):
        print(f'Message from {message.author}. Member Roles: {message.author.roles}. Message: {message.content}. Channel: {message.channel}')

        if message.channel == 'mythic-plus':
            if message.content[:5] == '!role':
                if not message.author in raiders:
                    raiders[message.author] = {'roles': [], 'lust': False}

                m = message.content.split(' ')
                raiders[message.author]['lust'] = True if m[-1].lower() == 'yes' else False
                raiders[message.author]['roles'] = m[1:-1]

    async def on_reaction_add(self, reaction, user):
        print(f'Reaction added: {reaction}. From User: {user}')


intents = discord.Intents.default()
intents.message_content = True

client = MyClient(intents=intents)
client.run()