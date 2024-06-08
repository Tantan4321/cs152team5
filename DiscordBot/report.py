from enum import Enum, auto
import discord
import re

import json

import vertexai
from vertexai.generative_models import GenerativeModel, Part, Image
from vertexai import generative_models
import requests


def update_adversarial_reports(username, count):
    username = str(username)
    with open('adversarial_data.json', 'r+') as file:
        data = json.load(file)
        if username in data:
            data[username]['count'] += count
        else:
            data[username] = {'count': count}
        file.seek(0)
        json.dump(data, file, indent=4)


def update_violation_reports(username, count):
    username = str(username)
    with open('violation_data.json', 'r+') as file:
        data = json.load(file)
        if username in data:
            data[username]['count'] += count
        else:
            data[username] = {'count': count}
        file.seek(0)
        json.dump(data, file, indent=4)


def read_adversarial_reports(username):
    username = str(username)
    with open('adversarial_data.json', 'r') as file:
        data = json.load(file)
        if str(username) in data:
            return int(data[username]['count'])
        else:
            return 0


def read_violation_reports(username):
    username = str(username)
    with open('violation_data.json', 'r') as file:
        data = json.load(file)
        if username in data:
            return int(data[username]['count'])
        else:
            return 0



class State(Enum):
    REPORT_START = auto() # start: user sends report keyword, end: ask for link
    AWAITING_MESSAGE = auto() # start: user sends link, end: ask for abuse type
    AWAITING_ABUSE_TYPE = auto() # start: user sends abuse type, end: ask for type of bullying or thank/ask block
    AWAITING_BULLYING_TYPE = auto() # start: user sends bullying type, end: ask for bullying victim
    AWAITING_VICTIM_BLOCK = auto()
    AWAITING_VICTIM_TYPE = auto()
    AWAITING_VICTIM = auto() # start: user sends victim, end: ask about resources or thank/ask block
    AWAITING_RESOURCES = auto() # start: user sends resource preferences, end: send resources(?), thank/ask block
    AWAITING_BLOCK_TYPE = auto() # start: user sends block preferences, end: block simulated
    AWAITING_REVIEW = auto() # start: reporting user has just finished filling out the review workflow
    VIOLATION_TYPE = auto()
    AWAITING_BAN_POSTER = auto()
    AWAITING_BAN_REPORTER = auto()
    AWAITING_ADVERSARIAL_DECISION = auto() # start: reviewer thinks report isn't bullying, end: decision on # adversarial
    AWAITING_OTHER_VIOLATION_TYPE = auto()
    REPORT_COMPLETE = auto() # start: block simulated


class Report:
    START_KEYWORD = "report"
    CANCEL_KEYWORD = "cancel"
    HELP_KEYWORD = "help"
    REVIEW_KEYWORD = "review"

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.report_message = None  # message contents of the report
        self.bullying_type = None # 1: threatening/abusive messages, 2: doxxing, 3: nonconsensual images
        self.victim = None # 1: me, 2: someone I know, 3: other
        self.msg_poster = None  # The author who posted the reported message
        self.msg_reporter = None  # the author who opened the report

        self.abuse_type = {
                            '1': "Bullying",
                            '2' : "Spam",
                            '3': "Offensive Content",
                            '4': "Immenent Danger"
                    } # abuse type dictionary, used for report summary

        self.bullying_type = {
                            '1': "Threatening/abusive message(s)",
                            '2': "Doxxing/exposing private information",
                            '3': "Sharing nonconsensual image(s)"
                    } # bullying type dictionary, used for report summary

        self.blocking_type = {
                            '1': "Block just this account",
                            '2': "Block this and any future accounts they create using the same email/phone number",
                            '3': "Do not block"
                    } # blocking type dictionary, used for report summary

        self.victim = {
                            '1': "User",
                            '2': "Someone the user knows",
                            '3': "Other"
                    } # who the victim is dictionary, used for report summary

        self.report_summary = []
        self.policy_text = """
                Cyberbullying Policy:

                Cyberbullying is strictly prohibited on this platform. This includes content that targets an individual (including by name, handle, or image, regardless of whether or not that individual is directly tagged in the post itself) with one or more threatening or abusive messages, doxxes or exposes private information about an individual, and/or shares one or more nonconsensual images of an individual with malicious intent.
        """
    
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

    async def handle_message(self, message):
        '''
        This function makes up the meat of the user-side reporting flow. It defines how we transition between states and what 
        prompts to offer at each of those states. You're welcome to change anything you want; this skeleton is just here to
        get you started and give you a model for working with Discord. 
        '''
        
        # Handle image attachments in the original message
        image_urls = [attachment.url for attachment in message.attachments if attachment.content_type.startswith('image')]
        referenced_image_urls = []

        vertexai.init(project='cs152team5', location="us-central1")
        self.model = GenerativeModel(model_name="gemini-1.0-pro-vision-001")

        # print('image_urls', image_urls)

        # self.image_urls = referenced_image_urls
        # # Handle image attachments in the referenced message (if any)
        if message.reference:
            referenced_message = await message.channel.fetch_message(message.reference.message_id)
            referenced_image_urls = [attachment.url for attachment in referenced_message.attachments if attachment.content_type.startswith('image')]

        if message.content == self.CANCEL_KEYWORD:
            self.state = State.REPORT_COMPLETE
            return ["Report cancelled."]

        if self.state == State.REPORT_START:
            self.msg_reporter = message.author
            reply =  "Thank you for starting the reporting process. "
            reply += "Say `help` at any time for more information.\n\n"
            reply += "Please copy paste the link to the message you want to report.\n"
            reply += "You can obtain this link by right-clicking the message and clicking `Copy Message Link`."
            self.state = State.AWAITING_MESSAGE
            return [reply]

        if self.state == State.AWAITING_MESSAGE:
            # Parse out the three ID strings from the message link
            m = re.search('/(\d+)/(\d+)/(\d+)', message.content)
            if not m:
                return ["I'm sorry, I couldn't read that link. Please try again or say `cancel` to cancel."]
            guild = self.client.get_guild(int(m.group(1)))
            if not guild:
                return ["I cannot accept reports of messages from guilds that I'm not in. Please have the guild owner add me to the guild and try again."]
            channel = guild.get_channel(int(m.group(2)))
            if not channel:
                return ["It seems this channel was deleted or never existed. Please try again or say `cancel` to cancel."]
            try:
                self.report_message = await channel.fetch_message(int(m.group(3)))
            except discord.errors.NotFound:
                return ["It seems this message was deleted or never existed. Please try again or say `cancel` to cancel."]
            self.report_summary.append('Reported author:' + self.report_message.author.name)
            self.report_summary.append('Reported message:' + self.report_message.content)
            self.msg_poster = message.author

            referenced_image_urls = [attachment.url for attachment in self.report_message.attachments if attachment.content_type.startswith('image')]

            # Here we've found the message - it's up to you to decide what to do next!
            self.state = State.AWAITING_ABUSE_TYPE
            # prompt user to select sub-category

            return_list = ["I found this message:", "```" + self.report_message.author.name + ": " + self.report_message.content + "```"]
            if referenced_image_urls:
                for url in referenced_image_urls:
                    return_list.append(f'Forwarded image:\n{message.author.name}: {url}')
            return_list.extend(["Please enter the number associated with your reason for reporting this post: ", \
                    "1. Bullying", \
                    "2. Spam", \
                    "3. Offensive Content", \
                    "4. Immenent Danger"])

            return return_list

        if self.state == State.AWAITING_ABUSE_TYPE:
            self.report_summary.append('Abuse type:' + self.abuse_type[message.content])
            if message.content == '1':
                self.state = State.AWAITING_BULLYING_TYPE
                return ["Please specify the type of bullying: ", \
                "1. Threatening/abusive message(s)", \
                "2. Doxxing/exposing private information", \
                "3. Sharing nonconsensual image(s)"]
            else:
                self.state = State.AWAITING_BLOCK_TYPE
                return ["Thank you for keeping our community safe! Your report will be reviewed and appropriate action will be taken.", \
                "Would you like to block this user to prevent seeing their content in the future?", \
                "1. Yes, just this account", \
                "2. Yes, this and any future accounts they create using the same email/phone number", \
                "3. No, I wish to continue seeing this creator's content"]


        if self.state == State.AWAITING_BULLYING_TYPE:
            self.report_summary.append('Bullying type:' + self.bullying_type[message.content])
            self.bullying_type = int(message.content)
            self.state = State.AWAITING_VICTIM_BLOCK
            return ["Would you like to block this user to prevent seeing their content in the future?", \
                "1. Yes, just this account", \
                "2. Yes, this and any future accounts they create using the same email/phone number", \
                "3. No, I wish to continue seeing this creator's content"]


        if self.state == State.AWAITING_VICTIM_BLOCK:
            self.report_summary.append('Blocking type:' + self.blocking_type[message.content])
            reply = []
            self.state = State.AWAITING_VICTIM_TYPE
            if message.content == '1':
                reply += ["This user has been blocked."]
            elif message.content == '2':
                reply += ["This user and the accounts they may create have been blocked."]
            else:
                reply += ["The user will not be blocked."]
            reply += ["Thank you for your report!"]
            return reply

        if self.state == State.AWAITING_VICTIM_TYPE:
            self.state = State.AWAITING_VICTIM
            return ["This content is bullying: ", \
             "1. Me", \
             "2. Someone I Know", \
             "3. Other"]

        if self.state == State.AWAITING_VICTIM:
            self.report_summary.append('Identity of victim:' + self.victim[message.content])
            self.victim = int(message.content)
            if message.content == '1':
                self.state = State.AWAITING_RESOURCES
                return ["Would you like to be redirected to a list of mental health help resources? You are not alone. (Y/N)"]
            else:
                self.state = State.AWAITING_BLOCK_TYPE
                return ["Thank you for keeping our community safe from bullying! Your report will be reviewed and appropriate action will be taken."]

        if self.state == State.AWAITING_RESOURCES:
            reply = []
            self.state = State.AWAITING_REVIEW
            if message.content == "Y":
                parts = []

                # print('referenced_image_urls', referenced_image_urls)
                # print('image_urls', image_urls)
                
                for image_url in referenced_image_urls:
                    image_path = await self.save_image(image_url, image_path='reference_image.jpg')
                    parts.append(Part.from_image(Image.load_from_file(image_path)))
                
                for image_url in image_urls:
                    image_path = await self.save_image(image_url, image_path='image.jpg')
                    parts.append(Part.from_image(Image.load_from_file(image_path)))

                # # Download images and add them to the parts list
                # for image_url in referenced_image_urls:
                #     image_path = await self.save_image(image_url, image_path='reference_image.jpg')
                #     parts.append(Part.from_image(Image.load_from_file(image_path)))

                mental_health_resources = """
                            Mental Health Resources

                            National Suicide Prevention Lifeline (U.S.)

                            Phone: 1-800-273-TALK (1-800-273-8255)
                            Website: suicidepreventionlifeline.org
                            Available 24/7 for free and confidential support.
                            
                            National Eating Disorders Association (NEDA)
                            
                            Phone: 1-800-931-2237
                            Website: nationaleatingdisorders.org
                            Offers support, resources, and treatment options for individuals affected by eating disorders and body image issues.
                            
                            Eating Disorders Anonymous (EDA)
                            
                            Website: eatingdisordersanonymous.org
                            Provides fellowship for individuals seeking recovery from eating disorders, following a 12-step program.
                            
                            Body Positive
                            
                            Website: thebodypositive.org
                            Promotes positive body image and self-love through educational resources and community support.
                            
                            Project HEAL
                            
                            Website: theprojectheal.org
                            Provides access to treatment and support for those struggling with eating disorders, particularly for individuals who face financial and insurance barriers.
                            
                            ANAD (National Association of Anorexia Nervosa and Associated Disorders)
                            
                            Phone: 1-888-375-7767
                            Website: anad.org
                            Offers free support groups, mentorship programs, and educational resources for individuals affected by eating disorders.
                            
                            Binge Eating Disorder Association (BEDA)
                            
                            Website: bedaonline.com
                            Provides support and resources specifically for individuals struggling with binge eating disorder.
                            
                            Mental Health America (MHA) - Body Image Resources
                            
                            Website: mhanational.org
                            Offers information and resources on body image and its impact on mental health.
                            
                            The Body Image Movement
                            
                            Website: bodyimagemovement.com
                            Advocates for positive body image and self-acceptance through education and community engagement.
                            
                            988 Suicide and Crisis Lifeline
                            
                            Phone: 988
                            Website: 988lifeline.org
                            Provides 24/7 access to trained crisis counselors offering free and confidential support for people in suicidal crisis or emotional distress.
                            
                            Crisis Text Line
                            
                            Text: "Home" to 741-741
                            Website: crisistextline.org
                            Offers support for any crisis via text, helping individuals move from a hot moment to a cool moment.
                            
                            SAMHSA's National Helpline
                            
                            Phone: 1-800-662-HELP (4357)
                            Website: samhsa.gov/find-help/national-helpline
                            A free, confidential, 24/7, treatment referral and information service for individuals and families facing mental and/or substance use disorders. Available in English and Spanish.
                            
                            Teen Line
                            
                            Phone: 1-800-852-8336
                            Text: "TEEN" to 839-863
                            Website: teenline.org
                            Provides support for teens by teens on various issues, ensuring non-judgmental listening and practical solutions.
                            
                            The Trevor Project
                            
                            Phone: 1-866-488-7386
                            Text: "START" to 678-678
                            Website: thetrevorproject.org
                            Provides crisis intervention and suicide prevention services to LGBTQ+ young people under 25.
                            
                            California Peer-Run Warm Line
                            
                            Phone: 1-855-845-7415
                            Website: mentalhealthsf.org/peer-run-warmline
                            Offers peer support and assistance for mental and emotional health issues.
                            
                            LGBTQ+ National Youth Talkline
                            
                            Phone: 1-800-246-7743
                            Website: glbthotline.org/youth-talkline.html
                            Provides confidential peer-support, information, and resources for LGBTQ+ youth under 25.
                            
                            California Youth Crisis Line
                            
                            Phone: 1-800-843-5200
                            Website: calyouth.org
                            Provides crisis intervention and referrals for youth ages 12-24 and their supporters, available 24/7.
                            
                            California Parent & Youth Helpline
                            
                            Phone: 1-855-427-2736
                            Website: caparentyouthhelpline.org
                            Offers support for parenting, mental health, and family issues in multiple languages, provided by Parents Anonymous® Inc.
                            
                            Together for Wellness
                            
                            Website: calhope.semel.ucla.edu
                            Description: A collaborative effort offering a set of digital resources (apps, websites, articles, videos) to help people through challenging times.
                """

                parts.append('Summary: ')
                parts.extend(self.report_summary)
                parts.append(mental_health_resources)

                #test

                # parts.append()

                # policy_text += f"Would you consider the image after Response image a violation of the policy if there is an image? If there is no image would you consider the following comment a violation of platforms like instagram given the policy above? {message_content}, respond with only one 'yes' or one 'no' nothing else."

                # policy_text += f"Given the resources and "
                parts.append(f"Given the resources and the report summary, can you select the relevant mental health resources and return their information? Only select up to 3 resources")
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

                response = self.model.generate_content(parts, safety_settings=safety_config)

                # reply += [' ', ]
                reply += ["Mental health resources", response.text]
            reply += ["Thank you for keeping our community safe from bullying! Your report will be reviewed and appropriate action will be taken.", \
            "Would you like to block this user to prevent seeing their content in the future?", \
            "1. Yes, just this account", \
            "2. Yes, this and any future accounts they create using the same email/phone number", \
            "3. No, I wish to continue seeing this creator's content"]
            return reply

        if self.state == State.AWAITING_BLOCK_TYPE:
            self.report_summary.append('Blocking type:' + self.blocking_type[message.content])
            reply = []
            self.state = State.AWAITING_REVIEW
            if message.content == '1':
                reply += ["This user has been blocked."]
            elif message.content == '2':
                reply += ["This user and the accounts they may create have been blocked."]
            else:
                reply += ["The user will not be blocked."]
            reply += ["Thank you for your report!"]
            return reply

        if self.state == State.AWAITING_REVIEW:
            return ["The report is awaiting moderator review."]

        return []

    async def handle_review(self, message):
        '''
        This function makes up the meat of the moderator-side manual review flow.
        '''
        if message.content == self.CANCEL_KEYWORD:
            self.state = State.REVIEW_COMPLETE
            return ["Review cancelled."]

        if self.state == State.AWAITING_REVIEW:
            reply = ["Thank you for starting the reviewing process. "]
            reply += ["Say `help` at any time for more information.\n\n"]

            self.state = State.VIOLATION_TYPE
            reply += ["I found this report:",
                    "```" + self.report_message.author.name + ": " + self.report_message.content + "```", \
                    "Please enter the number associated with the type of violation: ", \
                    "1. Bullying violation", \
                    "2. A Different Violation", \
                    "3. Not a violation", \
                    ]
            return reply

        if self.state == State.VIOLATION_TYPE:
            if message.content == '1':
                num_violations = read_violation_reports(self.msg_poster.id)
                reply = "This reported user has " + str(num_violations) + " previous violations. \n"
                update_violation_reports(self.msg_poster.id, 1)

                if num_violations == 0:
                    self.state = State.REPORT_COMPLETE

                    parts = [self.policy_text]
                    parts.append('Reported message:' + self.report_message.content)

                    referenced_image_urls = [attachment.url for attachment in self.report_message.attachments if attachment.content_type.startswith('image')]
                    
                    if referenced_image_urls:
                        for image_url in referenced_image_urls:
                            image_path = await self.save_image(image_url, image_path='reference_image.jpg')
                            parts.append('Reported image:')
                            parts.append(Part.from_image(Image.load_from_file(image_path)))

                    parts.append('Explain why the text or image is a violation of a social media platform?')

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

                    response = self.model.generate_content(parts, safety_settings=safety_config)
                    # print(['response.text', response.text])
                    await self.msg_poster.send('Reported message:' + self.report_message.content)
                    await self.msg_poster.send(response.text)
                    await self.msg_poster.send("Please don't bully people, that's bad :(")
                    reply += "Warning sent!"
                elif num_violations < 3:
                    self.state = State.REPORT_COMPLETE
                    reply += "Temporarily restrict reported user's posting privileges."
                else:
                    self.state = State.AWAITING_BAN_POSTER
                    reply += "Do you want to ban the user?\n 1. Yes\n 2. No"

                return [reply]

            elif message.content == '2':
                self.state = State.AWAITING_OTHER_VIOLATION_TYPE
                return ["What is the violation type? ", \
                "1. Spam", \
                "2. Offensive Content", \
                "3. Imminent Danger"]

            elif message.content == '3':
                self.state = State.AWAITING_ADVERSARIAL_DECISION
                return ["Is this report adversarial?: ", \
                "1. Yes", \
                "2. No"]
        
        if self.state == State.AWAITING_OTHER_VIOLATION_TYPE:
            self.state = State.REPORT_COMPLETE
            return ["The report has been sent to another moderation team"]

        if self.state == State.AWAITING_ADVERSARIAL_DECISION:
            if message.content == '1':
                num_violations = read_adversarial_reports(self.msg_reporter.id)
                reply = "This reporting user has " + str(num_violations) + " previous adversarial reports. \n"
                update_adversarial_reports(self.msg_reporter.id, 1)

                if num_violations <= 1:
                    self.state = State.REPORT_COMPLETE
                    await self.msg_reporter.send("Please don't abuse the message reporting feature!")
                    reply += "Warning sent!"
                elif num_violations <= 3:
                    self.state = State.REPORT_COMPLETE
                    reply += "Temporarily restrict reported user's reporting priviledges."
                else:
                    self.state = State.AWAITING_BAN_POSTER
                    reply += "Do you want to ban the user?\n 1. Yes\n 2. No"

                return [reply]

            elif message.content == '2':
                self.state = State.REPORT_COMPLETE
                await self.msg_reporter.send("The reported message does not violate our guidelines!")
                return ["Warning sent!"]

        if self.state == State.AWAITING_BAN_REPORTER:
            if message.content == '1':
                self.state = State.REPORT_COMPLETE
                return ["Ban the user"]
            elif message.content == '2':
                self.state = State.REPORT_COMPLETE
                return ["Temporarily restrict reported user's reporting priviledges"]

        if self.state == State.AWAITING_BAN_POSTER:
            if message.content == '1':
                self.state = State.REPORT_COMPLETE
                return ["Ban the user"]
            elif message.content == '2':
                self.state = State.REPORT_COMPLETE
                return ["Temporarily restrict reported user's posting priviledges"]

        if self.state == State.REPORT_COMPLETE:
            return ["The review has been completed."]

        return []

    def report_complete(self):
        return self.state == State.REPORT_COMPLETE





