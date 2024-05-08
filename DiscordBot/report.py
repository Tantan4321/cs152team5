from enum import Enum, auto
import discord
import re

class State(Enum):
    REPORT_START = auto()
    AWAITING_MESSAGE = auto()
    AWAITING_ABUSE = auto()
    IS_BULLYING = auto()
    BTYPE_IDENTIFIED = auto()
    VICTIM_SELF = auto()
    AWAITING_BLOCK = auto()
    BLOCK_RECIEVED = auto()
    REPORT_COMPLETE = auto()

class Report:
    START_KEYWORD = "report"
    CANCEL_KEYWORD = "cancel"
    HELP_KEYWORD = "help"

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
        self.btype = None
        self.bvictim = None
    
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

            # Here we've found the message - it's up to you to decide what to do next!
            self.state = State.AWAITING_ABUSE
            # prompt user to select sub-category
            return ["I found this message:", "```" + message.author.name + ": " + message.content + "```", \
                    "Please classify this message by inputting its associated number: ", \
                    "1. Offensive Content", \
                    "2. Spam", \
                    "3. Bullying", \
                    "4. Immenent Danger"]
        
        if self.state == State.AWAITING_ABUSE:
            if message.content == '3':
                self.state = State.IS_BULLYING
                return ["Please specify the type of bullying: ", \
                "1. Threatening or Abusive Messages", \
                "2. Doxxing or Exposing Private Information", \
                "3. Sharing Nonconsentual Image(s)"]
            else:
                self.state = State.AWAITING_BLOCK
                return ["Thank you for keeping our community safe!"]

        if self.state == State.IS_BULLYING:
            self.btype = int(message.content)
            self.state = State.BTYPE_IDENTIFIED
            return ["This content is bullying: ", \
            "1. Me", \
            "2. Someone I Know", \
            "3. Other"]

        if self.state == State.BTYPE_IDENTIFIED:
            self.bvictim = int(message.content)
            handle_bullying_report()
            if message.content == '1':
                self.state = State.VICTIM_SELF
                return ["Your report is being reviewed," \
                "Would you like to be redirected to a list of mental health resources? (Y/N)"]
            else:
                self.state = State.AWAITING_BLOCK
                return ["Your report is being reviewed. Thank you for keeping your community safe!"]

        if self.state == State.VICTIM_SELF:
            self.state = State.AWAITING_BLOCK
            if message.content == "Y":
                return ["Mental health resources"]
            else:
                return ["Thank you for keeping your community safe!"]

        if self.state == State.AWAITING_BLOCK:
            self.state = State.BLOCK_RECIEVED
            return ["Would your like to block this user?", \
            "1. Yes, just this account", \
            "2. Yes, and future accounts they create", \
            "3. No"]

        if self.state == State.BLOCK_RECIEVED:
            self.state = State.REPORT_COMPLETE
            if message.content == '1':
                return ["This user has been blocked."]
            elif message.content == '2':
                return ["This user and the accounts they may create have been blocked."]
            else:
                return ["The user will not be blocked."]

        if self.state == State.REPORT_COMPLETE:
            return ["The report has been completed."]

        return []


    def handle_bullying_report(self):
        return 0

    def report_complete(self):
        return self.state == State.REPORT_COMPLETE
    


    

