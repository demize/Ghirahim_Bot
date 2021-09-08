#!/usr/bin/env python

from dataclasses import dataclass, field
import datetime
from functools import total_ordering
import irc.bot, irc.connection
import re
import ssl
from urlextract import URLExtract
import urllib.parse
import yaml

from ghirahim_db.GhirahimDB import Channel, GhirahimDB, UserRole

class GhirahimBot(irc.bot.SingleServerIRCBot):
    def __init__(self):
        # Load the config
        with open('ghirahim.yaml', 'r') as f:
            config = yaml.load(f, Loader=yaml.BaseLoader)
            self.username = config["ghirahim"]["username"]
            self.password = config["ghirahim"]["password"]
            # Set up the DB
            self.db = GhirahimDB(config["mongo"]["connect_string"], 
                                 config["redis"]["host"], config["redis"]["port"], config["redis"]["db"])

        # Load the URLExtract engine and tell it to use @ as a left stop char
        self.extractor = URLExtract()
        self.extractor.update()
        stop_chars = self.extractor.get_stop_chars_left().copy()
        stop_chars.add("@")
        self.extractor.set_stop_chars_left(stop_chars)

        # Set up a regex we'll need later
        self.urlregex = re.compile(r"^[a-zA-Z0-9]+://")

        # Connect to Twitch
        server = "irc.chat.twitch.tv"
        port = 6697
        factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
        print(f'Connecting to {server} on {port} as {self.username} with SSL...')
        irc.bot.SingleServerIRCBot.__init__(self, [(server, port, self.password)],
                                            self.username, self.username, connect_factory=factory)

        # Schedule twice daily updates of the TLD list
        self.connection.reactor.scheduler.execute_every(period=datetime.timedelta(hours=12), func=self.extractor.update)

        # Set the rate limit. This should probably go in the config file, but it's fine here for now.
        self.connection.set_rate_limit(80/30)

        # Set up the list of joined channels for later
        self.joined_channels = set()


    def on_welcome(self, c, e):
        print("Connected.")
        # The tags cap is necessary so we can get information about messages, including user role and message ID.
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        # Join the bot's own channel
        c.join('#' + self.username)
        # Join every other channel
        channels = self.db.getChannels()
        if channels is not None:
            for channel in self.db.getChannels():
                c.join('#' + channel)

    def on_join(self, c, e):
        if e.source.nick == self.username:
            if e.target == "#" + self.username:
                # This is good confirmation the bot is actually running, but we don't need to log *every* join
                print(f"Joined {e.target}")
            self.joined_channels.add(e.target[1:])

    def on_part(self, c, e):
        if e.source.nick == self.username:
            self.joined_channels.remove(e.target[1:])

    def parse_badges(badges) -> UserRole:
        """Parse the list of a user's badges to determine their role.
        This works because a user with a special role will always have the badge
        for that role, and aside from the subscriber badge they will never have
        more than one role badge.

        Args:
            badges: The value of the badges tag you want to parse.

        Returns:
            UserRole: The highest role the user has.
        """

        role = UserRole.USER
        if badges is None:
            return role
        for badge in badges.split(","):
            new = role
            match badge.split("/")[0].lower():
                case "broadcaster":
                    new = UserRole.BROADCASTER
                case "moderator":
                    new = UserRole.MODERATOR
                case "vip":
                    new = UserRole.VIP
                case "subscriber":
                    new = UserRole.SUBSCRIBER
            if new > role:
                role = new
        return role

    def extract_urls(self, message: str, slash: bool, subdomains: bool, allow_list: list) -> set:
        """Finds the all domains in a given message.

        Args:
            message: The message to extract domains from.

        Returns:
            set: The set containing all the extracted domains.
        """

        urls = self.extractor.find_urls(message)
        domains = set()
        for url in urls:
            if ((slash) and "/" in message) or (not slash):
                if self.urlregex.match(url) is None:
                    url = "//" + url
                domain = urllib.parse.urlparse(url).netloc
                if subdomains:
                    if not any(item in domain for item in allow_list):
                        domains.add(domain)
                else:
                    if domain not in allow_list:
                        domains.add(domain)
        return domains

    def pubmsg_ownchannel(self, c, e):
        # There's only two commands to worry about here, nothing else matters
        message = " ".join(e.arguments)
        match message.lower().strip():
            case "!join":
                if(not self.db.getChannel(e.source.nick)): # Only create the channel if it doesn't exist
                    newChan = Channel.fromDefaults(e.source.nick)
                    self.db.setChannel(newChan)
                    c.join('#' + newChan.name)
                    c.privmsg(e.target, f"Joined #{newChan.name} with default settings.")
                elif(e.source.nick not in self.joined_channels): # If it does exist and we're not in it, join it
                    c.join('#' + e.source.nick)
            case "!leave":
                if(self.db.getChannel(e.source.nick)): # Only delete the channel from the DB if it exists
                    self.db.delChannel(e.source.nick)
                    c.part('#' + e.source.nick)
                elif(e.source.nick in self.joined_channels): # If it doesn't exist, but we're in it, part it
                    c.part('#' + e.source.nick)

    def get_reply(self, chan: Channel, user: str) -> str | None:
        """Generate a reply for a given user's message in a given channel.
        Returns None if the channel's replies are disabled.
        """
        if chan.reply.lower().replace("__user__, ", "") == "off":
            return None
        elif chan.reply.lower().replace("__user__, ", "") == "default":
            return f"@{user}, please ask for permission before posting a link."
        return chan.reply.replace("__user__", user)

    def chat_command(self, c, e, chan: Channel):
        """Parse chat commands."""
        earguments = " ".join(e.arguments)
        # All commands need at least two parts, the command and the args
        if len(earguments.split(" ")) < 2:
            return
        (command, args) = (earguments.split(" ", 1)[0], earguments.split(" ", 1)[1])
        match command.lower():
            # Permit takes only one user at once
            case "!permit":
                user = args.split(" ")[0]
                self.db.issuePermit(chan, user)
                c.privmsg(e.target, f'{user} may post any link for the next 5 minutes.')
            case "!links":
                # List only needs one part, but the others need at least two parts, the subcommand and its arguments
                if len(args.split(" ")) < 2:
                    subcommand = args
                    subargs = None
                else:
                    subcommand, subargs = (args.split(" ", 1)[0], args.split(" ", 1)[1])
                match subcommand.lower():
                    case "allow":
                        if subargs is not None:
                            for domain in subargs.split(" "):
                                if domain not in chan.allow_list:
                                    chan.allow_list.append(domain)
                            self.db.setChannel(chan)
                    case "deny":
                        if subargs is not None:
                            for domain in subargs.split(" "):
                                while domain in chan.allow_list:
                                    chan.allow_list.remove(domain)
                            self.db.setChannel(chan)
                    case "list":
                        current = ", ".join(chan.allow_list)
                        c.privmsg(e.target, f"Current allow list for {chan.name}: {current}")
                    case "slash":
                        if subargs.strip() in ["true", "yes"]:
                            chan.slash = True
                            self.db.setChannel(chan)
                        elif subargs.strip() in ["false", "no"]:
                            chan.slash = True
                            self.db.setChannel(chan)
                    case "subdomains":
                        if subargs.strip() in ["true", "yes"]:
                            chan.subdomains = True
                            self.db.setChannel(chan)
                        elif subargs.strip() in ["false", "no"]:
                            chan.subdomains = True
                            self.db.setChannel(chan)
                    case "role":
                        role = UserRole.fromStr(subargs.strip())
                        if role is not None:
                            chan.userlevel = role
                            self.db.setChannel(chan)
                    case "reply":
                        if not "__user__" in subargs:
                            subargs = "__user__, " + subargs
                        chan.reply = subargs
                        new_reply = self.get_reply(chan, e.source.nick)
                        if new_reply is None:
                            c.privmsg(e.target, "Replies disabled.")
                        else:
                            c.privmsg(e.target, f'New reply will be: "{new_reply}"')
                            self.db.setChannel(chan)

    def pubmsg_otherchannel(self, c, e):
        # Check if we're supposed to be in the channel and leave if not
        chan = self.db.getChannel(e.target[1:])
        if chan is None:
            c.part(e.target)
            return

        # Extract the user's role
        role = GhirahimBot.parse_badges(next(tag["value"] for tag in e.tags if tag["key"] == "badges"))

        # Mods and up are always immune, and are the only ones allowed to use commands
        if role >= UserRole.MODERATOR:
            if e.arguments[0][0] == '!':
                self.chat_command(c, e, chan)
        elif role >= chan.userlevel:
            pass # If the user's not a mod, but they're allowed to send links, ignore their message
        else:
            # Extract the user's display name and the message ID
            display_name = next(tag["value"] for tag in e.tags if tag["key"] == "display-name")
            msg_id = next(tag["value"] for tag in e.tags if tag["key"] == "id")
            domains = self.extract_urls(" ".join(e.arguments), chan.slash, chan.subdomains, chan.allow_list)
            # Delete the message if it has any non-allowed URL in it
            if domains:
                c.privmsg(e.target, f'/delete {msg_id}')
                reply = self.get_reply(chan, e.source.nick)
                if reply is not None:
                    c.privmsg(e.target, reply)

    def on_pubmsg(self, c, e):
        # Check if this message is in our own channel or another channel, and parse it accordingly
        if e.target[1:] == self.username:
            return self.pubmsg_ownchannel(c, e)
        self.pubmsg_otherchannel(c, e)

    def on_pubnotice(self, c, e):
        print(e)

    def on_privnotice(self, c, e):
        print(e)

    def on_notice(self, c, e):
        print(e)

def main():
    bot = GhirahimBot()
    bot.start()

if __name__ == "__main__":
    main()