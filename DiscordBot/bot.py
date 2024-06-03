import discord
import numpy as np
from discord.ext import commands
import os
import json
import logging
import re
import requests
from report import Report
import pdb
import vertexai
from vertexai.generative_models import GenerativeModel, Part, Image
from vertexai import generative_models
import io
import csv

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
        await mod_channel.send(self.code_format(scores))

        # # # Handle image attachments in the referenced message (if any)
        # if message.reference:
        #     referenced_message = await message.channel.fetch_message(message.reference.message_id)
        #     referenced_image_urls = [attachment.url for attachment in referenced_message.attachments if attachment.content_type.startswith('image')]
        #     if referenced_image_urls:
        #         for url in referenced_image_urls:
        #             await mod_channel.send(f'Forwarded referenced image:\n{referenced_message.author.name}: {url}')

    async def eval_dataset(self, message, dataset_parsed, text_key, label_key):
        MAX_MESSAGES = 100
        count = 1

        # Initialize the confusion matrix
        confusion_matrix = np.zeros((2, 2), dtype=int)

        for example in dataset_parsed:
            if count > MAX_MESSAGES:
                break
            _, score = await self.eval_text(example[text_key])
            predicted_label = 1 if score == 'yes' else 0
            true_label = example[label_key]

            print('true_label', true_label)
            print('true_label', type(true_label))

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

        return percentages.tolist()

    async def eval_text(self, message_content, referenced_image_urls=None):
        '''
        Evaluate the message content and image using Vertex AI.
        '''
        vertexai.init(project='cs152team5', location="us-central1")

        print('message_content', message_content)

        model = GenerativeModel(model_name="gemini-1.0-pro-vision-001")

        parts = []

        # Download images and add them to the parts list
        if referenced_image_urls:
            for image_url in referenced_image_urls:
                image_path = await self.save_image(image_url, image_path='reference_image.jpg')
                parts.append(Part.from_image(Image.load_from_file(image_path)))
        
        # parts.append('Response image:')
        # # Download images and add them to the parts list
        # for image_url in image_urls:
        #     image_path = await self.save_image(image_url, image_path='image.jpg')
        #     parts.append(Part.from_image(Image.load_from_file(image_path)))

        # policy_text = """
        #         Cyberbullying Policy:

        #         Cyberbullying is strictly prohibited on this platform. This includes content that targets an individual (including by name, handle, or image, regardless of whether or not that individual is directly tagged in the post itself) with one or more threatening or abusive messages, doxxes or exposes private information about an individual, and/or shares one or more nonconsensual images of an individual with malicious intent.

        #         We recognize that public figures (define) are in a unique position on our platform and that it is in the public interest to allow for some level of discourse and criticism on these figures. Therefore, we do permit some negative or critical comments about public figures. However, posts that constitute significant bullying (i.e., threatening to or following through with doxxing an individual or expressing a desire to harm an individual) are not permitted against public figures.

        #         Threatening or abusive messages can include but are not limited to:
        #         - Offensive name calling
        #         - Spreading of false rumors
        #         - Degrading statements about appearance
        #         - Threats of physical harm
        #         - Negative comments in reference to an individual’s sexual identity
        #         - Incitements to harm oneself
        #         - Encouragement of others to harass an individual

        #         Exposing the private information of an individual can include but is not limited to:
        #         - Threatening to or revealing an individual’s address, phone number, or email address

        #         Sharing a nonconsensual image with malicious intent includes but is not limited to:
        #         - Sharing sexually explicit/thematic images without consent (18+)
        #         - Sharing images of an individual in a degrading/embarrassing context or situation
        #         - Sharing any photo of an individual along with text meant to degrade, harass, or share private information about them
        #         - Photoshopping or using deepfake/AI to create or facilitate any of the above scenarios

        #         We recognize that context is necessary in certain scenarios to understand the intent and impact behind a given post. Our reporting system allows for victims of cyberbullying posts to identify themselves when reporting, and our moderators take this into account when making decisions.
        # """


        policy_text = """
                Cyberbullying Policy:

                Cyberbullying is strictly prohibited on this platform. This includes content that targets an individual (including by name, handle, or image, regardless of whether or not that individual is directly tagged in the post itself) with one or more threatening or abusive messages, doxxes or exposes private information about an individual, and/or shares one or more nonconsensual images of an individual with malicious intent.
                We recognize that public figures (define) are in a unique position on our platform and that it is in the public interest to allow for some level of discourse and criticism on these figures. Therefore, we do permit some negative or critical comments about public figures. However, posts that constitute significant bullying (i.e., threatening to or following through with doxxing an individual or expressing a desire to harm an individual) are not permitted against public figures.
        """

        # parts.append(policy_text)

        # policy_text += f"Would you consider the image after Response image a violation of the policy if there is an image? If there is no image would you consider the following comment a violation of platforms like instagram given the policy above? {message_content}, respond with only one 'yes' or one 'no' nothing else."

        policy_text += f"Would you consider the following comment a violation of platforms given the policy above? comment: {message_content}, respond with only one 'yes' or one 'no' and give explanation"
        parts.append(policy_text)
        # parts.append(f"Would you consider the following comment a violation of platforms like instagram given the policy above? Respond with only 'yes' or 'no', all lower case: {message_content}")


        # Safety config
        safety_config = [
            generative_models.SafetySetting(
                category=generative_models.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                threshold=generative_models.HarmBlockThreshold.BLOCK_NONE,
            ),
            generative_models.SafetySetting(
                category=generative_models.HarmCategory.HARM_CATEGORY_HARASSMENT,
                threshold=generative_models.HarmBlockThreshold.BLOCK_NONE,
            ),
            generative_models.SafetySetting(
                category=generative_models.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                threshold=generative_models.HarmBlockThreshold.BLOCK_NONE,
            ),
            generative_models.SafetySetting(
                category=generative_models.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                threshold=generative_models.HarmBlockThreshold.BLOCK_NONE,
            ),
        ]

        response = model.generate_content(parts, safety_settings=safety_config)

        # print('response.text', response.text)

        # return message_content, response.text
        return message_content, response.text

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
