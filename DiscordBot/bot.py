import discord
import numpy as np
from discord.ext import commands
import os
import json
import logging
import re
import requests
from report import Report, State
import pdb
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Image
from vertexai import generative_models
import io
import csv
import matplotlib.pyplot as plt

# Set up logging to the console
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# There should be a file called 'tokens.json' inside the same folder as this file
token_path = 'tokens.json'
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    tokens = json.load(f)
    discord_token = tokens['discord']


class ModBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.messages = True
        intents.guilds = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.group_num = None
        self.mod_channels = {}  # Map from guild to the mod channel id for that guild
        self.reports = {}  # Map from user IDs to the state of their report

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search('[gG]roup (\d+) [bB]ot', self.user.name)
        if match:
            self.group_num = match.group(1)
        else:
            raise Exception("Group number not found in bot's name. Name format should be \"Group # Bot\".")

        # Find the mod channel in each guild that this bot should report to
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f'group-{self.group_num}-mod':
                    self.mod_channels[guild.id] = channel

    async def on_message(self, message):
        '''
        This function is called whenever a message is sent in a channel that the bot can see (including DMs). 
        Currently the bot is configured to only handle messages that are sent over DMs or in your group's "group-#" channel. 
        '''
        # Ignore messages from the bot 
        if message.author.id == self.user.id:
            return

        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        # Handle a help message
        if message.content == Report.HELP_KEYWORD:
            reply = "Use the `report` command to begin the reporting process.\n"
            reply += "Use the `review` command to begin the moderation process.\n"
            reply += "Use the `cancel` command to cancel the report process.\n"
            await message.channel.send(reply)
            return

        author_id = message.author.id
        responses = []

        # Only respond to messages if they're part of a reporting flow
        if author_id not in self.reports and not message.content.startswith(Report.START_KEYWORD):
            return

        # If we don't currently have an active report for this user, add one
        if author_id not in self.reports:
            self.reports[author_id] = Report(self)

        # Let the report class handle this message; forward all the messages it returns to us
        responses = await self.reports[author_id].handle_message(message)
        for r in responses:
            await message.channel.send(r)

        # If the report is complete or cancelled, remove it from our map
        if self.reports[author_id].report_complete():
            # Forward the message to the mod channel
            mod_channel = list(self.mod_channels.values())[
                0]  # temp hack, need to change if we have multiple mod channels
            await mod_channel.send(
                f'Forwarded message:\n{message.author.name}: "{self.reports[author_id].report_summary}"')
            self.reports.pop(author_id)

    async def handle_channel_message(self, message):
        # Only handle messages sent in the "group-#" channel
        if message.channel.name == f'group-{self.group_num}-mod':
            if message.content.startswith("eval "):
                with open(message.content[5:], encoding='utf-8') as csvfile:
                    confusion_matrix = await self.eval_dataset(message, csv.DictReader(csvfile), "Text", "oh_label")

            if message.content == Report.HELP_KEYWORD:
                reply = "Use the `review` command to begin the moderation process.\n"
                reply += "Use the `cancel` command to cancel the report process.\n"
                await message.channel.send(reply)
                return

            responses = []

            # Only respond to messages if they're part of a reporting flow
            if len(self.reports) == 0 and message.content.startswith(Report.REVIEW_KEYWORD):
                await message.channel.send("No active moderation reports found!")
                return

            author_id = next(iter(self.reports))  # get the author id of the first report

            # Let the report class handle this message; forward all the messages it returns to us
            responses = await self.reports[author_id].handle_review(message)
            for r in responses:
                await message.channel.send(r)

            if self.reports[author_id].report_complete():
                self.reports.pop(author_id)
            return

        if not message.channel.name == f'group-{self.group_num}':
            return

        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')

        # Handle image attachments in the original message
        image_urls = [attachment.url for attachment in message.attachments if attachment.content_type.startswith('image')]

        referenced_image_urls = None
        # # Handle image attachments in the referenced message (if any)
        referenced_image_urls = None
        if message.reference:
            referenced_message = await message.channel.fetch_message(message.reference.message_id)
            referenced_image_urls = [attachment.url for attachment in referenced_message.attachments if
                                     attachment.content_type.startswith('image')]
            if referenced_image_urls:
                for url in referenced_image_urls:
                    await mod_channel.send(f'Forwarded referenced image:\n{referenced_message.author.name}: {url}')

        # Forward images from the original message to the mod channel
        if image_urls:
            for url in image_urls:
                await mod_channel.send(f'Forwarded image:\n{message.author.name}: {url}')

        scores = await self.eval_text(message.content, image_urls, referenced_image_urls)
        if scores[1].startswith('yes'):
            print("Found a violation msg")
            author_id = message.author.id
            if author_id not in self.reports:
                print("creating report")
                self.reports[author_id] = Report(self)
                self.reports[author_id].report_message = message
                self.reports[author_id].msg_poster = message.author
                self.reports[author_id].state = State.AWAITING_REVIEW

        await mod_channel.send(self.code_format(scores))

    async def eval_dataset(self, message, dataset_parsed, text_key, label_key):
        MAX_MESSAGES = 1000
        count = 1

        # Initialize the confusion matrix
        confusion_matrix = np.zeros((2, 2), dtype=int)

        for example in dataset_parsed:
            if count > MAX_MESSAGES:
                break
            print('(example[text_key]', example[text_key])
            _, score = await self.eval_text(example[text_key])
            print('score', score)

            predicted_label = 1 if score == 'yes' else 0
            true_label = example[label_key]

            print('true_label', true_label)
            print('type true_label', type(true_label))

            # Update the confusion matrix
            confusion_matrix[int(true_label), int(predicted_label)] += 1
            count += 1
            print(count)

        # Calculate the total number of examples
        total = confusion_matrix.sum()
        percentages = (confusion_matrix / total) * 100
        # Format the confusion matrix as a string
        formatted_matrix = (
            f"Confusion Matrix:\n"
            f"                 Predicted No    Predicted Yes\n"
            f"Actual No      {confusion_matrix[0, 0]:12} ({percentages[0, 0]:6.2f}%)    {confusion_matrix[0, 1]:12} ({percentages[0, 1]:6.2f}%)\n"
            f"Actual Yes     {confusion_matrix[1, 0]:12} ({percentages[1, 0]:6.2f}%)    {confusion_matrix[1, 1]:12} ({percentages[1, 1]:6.2f}%)"
        )

        # Print the formatted confusion matrix
        print(formatted_matrix)

        fig, ax = plt.subplots()
        cax = ax.matshow(confusion_matrix, cmap='Blues')
        plt.title('Confusion Matrix')
        fig.colorbar(cax)
        ax.set_xticklabels([''] + ['No', 'Yes'])
        ax.set_yticklabels([''] + ['No', 'Yes'])
        plt.xlabel('Predicted')
        plt.ylabel('Actual')

        for (i, j), val in np.ndenumerate(confusion_matrix):
            plt.text(j, i, f'{val}\n({percentages[i, j]:.2f}%)', ha='center', va='center', color='red')

        # Save the confusion matrix as a JPG file
        plt.savefig('confusion_matrix_full_policy_1000_oai.jpg', format='jpg')
        plt.show()

        return percentages.tolist()
    
    async def eval_text(self, message_content, image_urls=None, referenced_image_urls=None):

        print('message_content', message_content)

        from openai import OpenAI
        client = OpenAI(
            api_key='',
        )

        #no policy
        policy_text = ''

        # short policy
        # policy_text = """
        #         Cyberbullying Policy:

        #         Cyberbullying is strictly prohibited on this platform. This includes content that targets an individual (including by name, handle, or image, regardless of whether or not that individual is directly tagged in the post itself) with one or more threatening or abusive messages, doxxes or exposes private information about an individual, and/or shares one or more nonconsensual images of an individual with malicious intent.
        # """

        # full policy
        policy_text = """
                Cyberbullying Policy:

                Cyberbullying is strictly prohibited on this platform. This includes content that targets an individual (including by name, handle, or image, regardless of whether or not that individual is directly tagged in the post itself) with one or more threatening or abusive messages, doxxes or exposes private information about an individual, and/or shares one or more nonconsensual images of an individual with malicious intent.

                We recognize that public figures (define) are in a unique position on our platform and that it is in the public interest to allow for some level of discourse and criticism on these figures. Therefore, we do permit some negative or critical comments about public figures. However, posts that constitute significant bullying (i.e., threatening to or following through with doxxing an individual or expressing a desire to harm an individual) are not permitted against public figures.

                Threatening or abusive messages can include but are not limited to:
                - Offensive name calling
                - Spreading of false rumors
                - Degrading statements about appearance
                - Threats of physical harm
                - Negative comments in reference to an individual’s sexual identity
                - Incitements to harm oneself
                - Encouragement of others to harass an individual

                Exposing the private information of an individual can include but is not limited to:
                - Threatening to or revealing an individual’s address, phone number, or email address

                Sharing a nonconsensual image with malicious intent includes but is not limited to:
                - Sharing sexually explicit/thematic images without consent (18+)
                - Sharing images of an individual in a degrading/embarrassing context or situation
                - Sharing any photo of an individual along with text meant to degrade, harass, or share private information about them
                - Photoshopping or using deepfake/AI to create or facilitate any of the above scenarios

                We recognize that context is necessary in certain scenarios to understand the intent and impact behind a given post. Our reporting system allows for victims of cyberbullying posts to identify themselves when reporting, and our moderators take this into account when making decisions.

                Consider that there are other forms of violation and the above policy may not cover all types of abuses. 
        """


        print('referenced_image_urls', referenced_image_urls)
        print('image_urls', image_urls)

        if referenced_image_urls:
            if image_urls:
                response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                    "role": "user",
                    "content": [
                        {
                        "type": "text",
                        "text": policy_text,
                        },
                        {
                        "type": "text",
                        "text": "Let's say the first image is a instagram post and the second image is a response to the post, would you consider the response a violation of cyberbullying policy? Please start answer with only 'yes' or 'no' ",
                        },
                        {
                        "type": "image_url",
                        "image_url": {
                            "url": referenced_image_urls[0],
                        },
                        },
                        {
                        "type": "image_url",
                        "image_url": {
                            "url": image_urls[0],
                        },
                        },
                    ],
                    }
                ],
                max_tokens=300,
                )

                print('response', response)
                first_word = response.choices[0].message.content.strip().lower().split(' ')[0]
                print('first_word', first_word)
            else:
                response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                    "role": "user",
                    "content": [
                        {
                        "type": "text",
                        "text": policy_text,
                        },
                        {
                        "type": "text",
                        "text": "Let's say the first image is a instagram post and the second text is a response to the post, would you consider the response a violation of cyberbullying policy? Please answer with only a 'yes' or 'no'",
                        },
                        {
                        "type": "image_url",
                        "image_url": {
                            "url": referenced_image_urls[0],
                        },
                        },
                        {
                        "type": "text",
                        "text": message_content,
                        },
                    ],
                    }
                ],
                max_tokens=300,
                )
                print('response', response)

            first_word = response.choices[0].message.content.strip().lower().split(' ')[0]
            print('first_word', first_word)
        elif image_urls:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                    "role": "user",
                    "content": [
                        {
                        "type": "text",
                        "text": policy_text,
                        },
                        {
                        "type": "text",
                        "text": "Let's say the image is a instagram post, would you consider the response a violation of cyberbullying policy? Please answer with only a 'yes' or 'no'",
                        },
                        {
                        "type": "image_url",
                        "image_url": {
                            "url": image_urls[0],
                        },
                        },
                    ],
                    }
                ],
                max_tokens=300,
                )
            print('response.choices[0].message.content.strip()', response.choices[0].message.content.strip())
            first_word = response.choices[0].message.content.strip().lower().split(' ')[0]
            print('first_word', first_word)
        else:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                    "role": "user",
                    "content": [
                        {
                        "type": "text",
                        "text": policy_text,
                        },
                        {
                        "type": "text",
                        "text": "Let's say the following is a comment on twitter, would you consider the response a violation of cyberbullying policy? Please answer with only a 'yes' or 'no'",
                        },
                        {
                        "type": "text",
                        "text": message_content,
                        },
                    ],
                    }
                ],
                max_tokens=300,
                )
            print('response.choices[0].message.content.strip()', response.choices[0].message.content.strip())
            first_word = response.choices[0].message.content.strip().lower().split(' ')[0]
            print('first_word', first_word)

        return message_content, first_word

    async def save_image(self, image_url, image_path):
        '''
        Download an image from a URL and save it locally.
        '''
        response = requests.get(image_url)
        image_data = response.content

        # image_path = 'image.jpg'
        with open(image_path, 'wb') as f:
            f.write(image_data)

        return image_path

    def code_format(self, text):
        '''
        Format the evaluated message and result.
        '''
        msg, eval = text
        eval_cleaned = eval.lower().replace(' ', '').strip()
        print('eval_cleaned'+eval)
        if 'yes' in eval_cleaned:
            return f"Evaluated: '{msg}' as a violation"
        return f"Evaluated: '{msg}' as not a violation"


if __name__ == "__main__":
    client = ModBot()
    client.run(discord_token)
