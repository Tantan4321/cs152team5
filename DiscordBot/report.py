from enum import Enum, auto
import discord
import re

class State(Enum):
    REPORT_START = auto() # start: user sends report keyword, end: ask for link
    AWAITING_MESSAGE = auto() # start: user sends link, end: ask for abuse type
    AWAITING_ABUSE_TYPE = auto() # start: user sends abuse type, end: ask for type of bullying or thank/ask block
    AWAITING_BULLYING_TYPE = auto() # start: user sends bullying type, end: ask for bullying victim
    AWAITING_VICTIM = auto() # start: user sends victim, end: ask about resources or thank/ask block
    AWAITING_RESOURCES = auto() # start: user sends resource preferences, end: send resources(?), thank/ask block
    AWAITING_BLOCK_TYPE = auto() # start: user sends block preferences, end: block simulated
    REPORT_COMPLETE = auto() # start: block simulated
    VIOLATION_TYPE = auto()
    WAITING_VIOLATION_RECORD = auto()
    AWAITING_BAN_USER = auto()
    AWAITING_VIOLATION_RECORD = auto()

class Report:
    START_KEYWORD = "report"
    CANCEL_KEYWORD = "cancel"
    HELP_KEYWORD = "help"
    REVIEW_KEYWORD = "review"

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
        self.bullying_type = None # 1: threatening/abusive messages, 2: doxxing, 3: nonconsensual images
        self.victim = None # 1: me, 2: someone I know, 3: other

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
    
    async def handle_message(self, message):
        '''
        This function makes up the meat of the user-side reporting flow. It defines how we transition between states and what 
        prompts to offer at each of those states. You're welcome to change anything you want; this skeleton is just here to
        get you started and give you a model for working with Discord. 
        ''' 

        if message.content == self.CANCEL_KEYWORD:
            self.state = State.REPORT_COMPLETE
            return ["Report cancelled."]
        
        if self.state == State.REPORT_START:
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
                message = await channel.fetch_message(int(m.group(3)))
            except discord.errors.NotFound:
                return ["It seems this message was deleted or never existed. Please try again or say `cancel` to cancel."]
            self.report_summary.append('Reported author:' + message.author.name)
            self.report_summary.append('Reported message:' + message.content)
            # Here we've found the message - it's up to you to decide what to do next!
            self.state = State.AWAITING_ABUSE_TYPE
            # prompt user to select sub-category
            return ["I found this message:", "```" + message.author.name + ": " + message.content + "```", \
                    "Please enter the number associated with your reason for reporting this post: ", \
                    "1. Bullying", \
                    "2. Spam", \
                    "3. Offensive Content", \
                    "4. Immenent Danger"]
        
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
            self.state = State.AWAITING_VICTIM
            return ["This content is bullying: ", \
            "1. Me", \
            "2. Someone I Know", \
            "3. Other"]
        
            

        if self.state == State.AWAITING_VICTIM:
            self.report_summary.append('Identity of victim:' + self.victim[message.content])
            self.victim = int(message.content)
            self.handle_bullying_report()
            if message.content == '1':
                self.state = State.AWAITING_RESOURCES
                return ["Would you like to be redirected to a list of mental health help resources? You are not alone. (Y/N)"]
            else:
                self.state = State.AWAITING_BLOCK_TYPE
                return ["Thank you for keeping our community safe from bullying! Your report will be reviewed and appropriate action will be taken.", \
                "Would you like to block this user to prevent seeing their content in the future?", \
                "1. Yes, just this account", \
                "2. Yes, this and any future accounts they create using the same email/phone number", \
                "3. No, I wish to continue seeing this creator's content"]

        if self.state == State.AWAITING_RESOURCES:
            reply = []
            self.state = State.AWAITING_BLOCK_TYPE
            if message.content == "Y":
                reply += ["Mental health resources"]
            reply += ["Thank you for keeping our community safe from bullying! Your report will be reviewed and appropriate action will be taken.", \
            "Would you like to block this user to prevent seeing their content in the future?", \
            "1. Yes, just this account", \
            "2. Yes, this and any future accounts they create using the same email/phone number", \
            "3. No, I wish to continue seeing this creator's content"]
            return reply

        if self.state == State.AWAITING_BLOCK_TYPE:
            self.report_summary.append('Blocking type:' + self.blocking_type[message.content])
            reply = []
            self.state = State.REPORT_COMPLETE
            if message.content == '1':
                reply += ["This user has been blocked."]
            elif message.content == '2':
                reply += ["This user and the accounts they may create have been blocked."]
            else:
                reply += ["The user will not be blocked."]
            reply += ["Thank you for your report!"]
            return reply

        if self.state == State.REPORT_COMPLETE:
            return ["The report has been completed."]

        return []

    async def handle_review(self, message):
        '''
        This function makes up the meat of the moderator-side manual review flow.
        ''' 
        if message.content == self.CANCEL_KEYWORD:
            self.state = State.REVIEW_COMPLETE
            return ["Review cancelled."]
        
        if self.state == State.REPORT_START:
            reply =  "Thank you for starting the reviewing process. "
            reply += "Say `help` at any time for more information.\n\n"
            reply += "Please copy paste the link to the message you want to review.\n"
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
                message = await channel.fetch_message(int(m.group(3)))
            except discord.errors.NotFound:
                return ["It seems this message was deleted or never existed. Please try again or say `cancel` to cancel."]
  
            # Here we've found the message - it's up to you to decide what to do next!
            self.state = State.VIOLATION_TYPE
            # prompt user to select sub-category
            return ["I found this report:", "```" + message.author.name + ": " + message.content + "```", \
                    "Please enter the number associated with the type of violation: ", \
                    "1. Bullying violation", \
                    "2. A Different Violation", \
                    "3. Not a violation", \
                    ]
        
        if self.state == State.VIOLATION_TYPE:
            if message.content == '1':
                self.state = State.AWAITING_VIOLATION_RECORD
                return ["What is the reported user's violation record?: ", \
                "1. No history of violations", \
                "2. 1-3 offenses", \
                "3. 3+ offenses"]


        if self.state == State.AWAITING_VIOLATION_RECORD:
            if message.content == '1':
                self.state = State.REPORT_COMPLETE
                return ["Send warning to user"]
            elif message.content == '2':
                self.state = State.REPORT_COMPLETE
                return ["Temporarily restrict reported user's posting priviledges"]
            elif message.content == '3':
                self.state = State.AWAITING_BAN_USER
                return ["Do you want to ban the user?", \
                "1. Yes", \
                "2. No", 
                ]

        if self.state == State.REPORT_COMPLETE:
            return ["The review has been completed."]

        return []

    def report_complete(self):
        return self.state == State.REPORT_COMPLETE
    


    

