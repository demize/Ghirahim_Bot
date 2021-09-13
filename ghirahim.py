#!/usr/bin/env python

from dataclasses import dataclass, field
import datetime
from functools import total_ordering
import irc.bot
import irc.connection
import logging
import logging.handlers
import numpy
import re
import ssl
import sys
from urlextract import URLExtract
import urllib.parse
import yaml

from ghirahim_db.GhirahimDB import Channel, GhirahimDB, UserRole
from ghirahim_utils import ignore_notices, cooldown_notices, leave_notices


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
        print(
            f'Connecting to {server} on {port} as {self.username} with SSL...')
        irc.bot.SingleServerIRCBot.__init__(self, [(server, port, self.password)],
                                            self.username, self.username, connect_factory=factory)

        # Schedule twice daily updates of the TLD list
        self.connection.reactor.scheduler.execute_every(
            period=datetime.timedelta(hours=12), func=self.extractor.update)

        # Set the rate limit. This should probably go in the config file, but it's fine here for now.
        self.connection.set_rate_limit(80/30)

        # Set up the list of joined channels for later
        self.joined_channels = set()

        # Set up logging
        # We need a formatter for both files (as well as the console, if enabled)
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        formatter.formatTime = (lambda self, record: datetime.datetime.fromtimestamp(
            self.created, datetime.timezone.utc).astimezone().isoformat())

        # Each file needs its own handler. We'll limit each file to 100MB with two backups.
        priv_handler = logging.handlers.RotatingFileHandler(
            f'{config["ghirahim"]["log"]["logdir"]}/privnotice.log', maxBytes=1024 * 1024 * 100, backupCount=2, encoding='utf-8')
        pub_handler = logging.handlers.RotatingFileHandler(
            f'{config["ghirahim"]["log"]["logdir"]}/pubnotice.log', maxBytes=1024 * 1024 * 100, backupCount=2, encoding='utf-8')

        # Assign the formatter to each handler
        priv_handler.setFormatter(formatter)
        pub_handler.setFormatter(formatter)

        # Create the loggers, set the level to INFO, and assign the handler to each
        self.priv_logger = logging.getLogger("ghirahim.privnotice")
        self.pub_logger = logging.getLogger("ghirahim.pubnotice")
        self.priv_logger.setLevel(logging.INFO)
        self.pub_logger.setLevel(logging.INFO)
        self.priv_logger.addHandler(priv_handler)
        self.pub_logger.addHandler(pub_handler)

        # Mostly for debugging, but also just nice to have sometimes
        if config["ghirahim"]["log"]["console"]:
            stdhandler = logging.StreamHandler(sys.stdout)
            stdhandler.setFormatter(formatter)
            self.pub_logger.addHandler(stdhandler)
            self.priv_logger.addHandler(stdhandler)

    def send_privmsg(self, c, target: str, message: str):
        if not self.db.checkChannelCooldown(target[1:]):
            c.privmsg(target, message)

    def check_channels(self):
        # Make sure we're in all the channels we're supposed to be, including our own
        channels = numpy.union1d(self.db.getChannels(), [self.username])
        to_join = numpy.setdiff1d(list(channels), list(self.joined_channels))
        for channel in to_join:
            self.connection.join('#' + channel)
        # Make sure we're not in any channels we're not supposed to be
        to_part = numpy.setdiff1d(list(self.joined_channels), list(channels))
        for channel in to_part:
            self.connection.part('#' + channel)

    def on_welcome(self, c, e):
        print("Connected.")
        # The tags cap is necessary so we can get information about messages, including user role and message ID.
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        # Join the bot's own channel
        c.join('#' + self.username)
        c.join("#nightbot")
        # Join every other channel
        c.reactor.scheduler.execute_after(
            delay=datetime.timedelta(seconds=5), func=self.check_channels)
        # Check hourly that we're in all the channels we need to be
        c.reactor.scheduler.execute_every(
            period=datetime.timedelta(hours=1), func=self.check_channels)

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
                # Only create the channel if it doesn't exist
                if(not self.db.getChannel(e.source.nick)):
                    newChan = Channel.fromDefaults(e.source.nick)
                    self.db.setChannel(newChan)
                    c.join('#' + newChan.name)
                    self.send_privmsg(
                        c, e.target, f"Joined #{newChan.name} with default settings.")
                # If it does exist and we're not in it, join it
                elif(e.source.nick not in self.joined_channels):
                    c.join('#' + e.source.nick)
            case "!leave":
                # Only delete the channel from the DB if it exists
                if(self.db.getChannel(e.source.nick)):
                    self.db.delChannel(e.source.nick)
                    c.part('#' + e.source.nick)
                # If it doesn't exist, but we're in it, part it
                elif(e.source.nick in self.joined_channels):
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
        command = earguments.split(" ", 1)[0]
        args = earguments.split(" ", 1)[1]
        match command.lower():
            # Permit takes only one user at once
            case "!permit":
                user = args.split(" ")[0]
                if user[0] == "@":
                    user = user[1:]
                self.db.issuePermit(chan, user)
                self.send_privmsg(
                    c, e.target, f'{user} may post any link for the next 5 minutes.')
            case "!links":
                # List only needs one part, but the others need at least two parts, the subcommand and its arguments
                if len(args.split(" ")) < 2:
                    subcommand = args
                    subargs = None
                else:
                    subcommand = args.split(" ", 1)[0]
                    subargs = args.split(" ", 1)[1]
                match subcommand.lower():
                    case ("allow"|"add"):
                        if subargs is not None:
                            for domain in subargs.split(" "):
                                if domain not in chan.allow_list:
                                    chan.allow_list.append(domain)
                            self.db.setChannel(chan)
                            current = ", ".join(chan.allow_list)
                            self.send_privmsg(
                                c, e.target, f"New allow list for {chan.name}: {current}")
                    case ("deny"|"del"|"remove"):
                        if subargs is not None:
                            for domain in subargs.split(" "):
                                while domain in chan.allow_list:
                                    chan.allow_list.remove(domain)
                            self.db.setChannel(chan)
                            current = ", ".join(chan.allow_list)
                            self.send_privmsg(
                                c, e.target, f"New allow list for {chan.name}: {current}")
                    case "list":
                        current = ", ".join(chan.allow_list)
                        self.send_privmsg(
                            c, e.target, f"Current allow list for {chan.name}: {current}")
                    case "slash":
                        if subargs is not None:
                            if subargs.strip() in ["true", "yes"]:
                                chan.slash = True
                                self.db.setChannel(chan)
                                self.send_privmsg(
                                    c, e.target, f"Slashes now required in {chan.name}")
                            elif subargs.strip() in ["false", "no"]:
                                chan.slash = True
                                self.db.setChannel(chan)
                                self.send_privmsg(
                                    c, e.target, f"Slashes now ignored in {chan.name}")
                        elif chan.slash:
                            self.send_privmsg(
                                    c, e.target, f"Slashes currently required in {chan.name}")
                        else:
                            self.send_privmsg(
                                    c, e.target, f"Slashes currently NOT required in {chan.name}")
                    case "subdomains":
                        if subargs is not None:
                            if subargs.strip() in ["true", "yes"]:
                                chan.subdomains = True
                                self.db.setChannel(chan)
                                self.send_privmsg(
                                    c, e.target, f"Subdomain matching enabled in {chan.name}")
                            elif subargs.strip() in ["false", "no"]:
                                chan.subdomains = True
                                self.db.setChannel(chan)
                                self.send_privmsg(
                                    c, e.target, f"Subdomain matching disabled in {chan.name}")
                        elif chan.subdomains:
                            self.send_privmsg(
                                    c, e.target, f"Subdomain matching currently enabled in {chan.name}")
                        else:
                            self.send_privmsg(
                                    c, e.target, f"Subdomain matching currently disabled in {chan.name}")
                    case "role":
                        if subargs is not None:
                            role = UserRole.fromStr(subargs.strip())
                            if role is not None:
                                chan.userlevel = role
                                self.db.setChannel(chan)
                                self.send_privmsg(
                                    c, e.target, f"Allowed userlevel set to {role} in {chan.name}")
                            else:
                                self.send_privmsg(c, e.target, "Invalid role specified!")
                        else:
                            self.send_privmsg(
                                    c, e.target, f"Allowed userlevel in {chan.name} is {chan.userlevel}")
                    case "reply":
                        if subargs is not None:
                            if not "__user__" in subargs:
                                subargs = "__user__, " + subargs
                            chan.reply = subargs
                            new_reply = self.get_reply(chan, e.source.nick)
                            if new_reply is None:
                                self.send_privmsg(c, e.target, "Replies disabled.")
                            else:
                                self.send_privmsg(
                                    c, e.target, f'New reply will be: "{new_reply}"')
                                self.db.setChannel(chan)
                        else:
                            self.send_privmsg(c, e.target, f"Current reply in {chan.name}: {self.get_reply(chan, e.source.nick)}")

    def pubmsg_otherchannel(self, c, e):
        # Check if we're supposed to be in the channel and leave if not
        chan = self.db.getChannel(e.target[1:])
        if chan is None:
            c.part(e.target)
            return

        # Extract the user's role
        role = GhirahimBot.parse_badges(
            next(tag["value"] for tag in e.tags if tag["key"] == "badges"))

        # Mods and up are always immune, and are the only ones allowed to use commands
        if role >= UserRole.MODERATOR:
            if e.arguments[0][0] == '!':
                self.chat_command(c, e, chan)
        elif role >= chan.userlevel:
            pass  # If the user's not a mod, but they're allowed to send links, ignore their message
        else:
            # Extract the user's display name and the message ID
            display_name = next(tag["value"]
                                for tag in e.tags if tag["key"] == "display-name")
            msg_id = next(tag["value"] for tag in e.tags if tag["key"] == "id")
            domains = self.extract_urls(
                " ".join(e.arguments), chan.slash, chan.subdomains, chan.allow_list)
            # Delete the message if it has any non-allowed URL in it
            if domains:
                self.send_privmsg(c, e.target, f'/delete {msg_id}')
                reply = self.get_reply(chan, e.source.nick)
                if reply is not None:
                    self.send_privmsg(c, e.target, reply)

    def on_pubmsg(self, c, e):
        # Check if this message is in our own channel or another channel, and parse it accordingly
        if e.target[1:] == self.username:
            return self.pubmsg_ownchannel(c, e)
        self.pubmsg_otherchannel(c, e)

    def on_pubnotice(self, c, e):
        notice_type = next(tag["value"]
                           for tag in e.tags if tag["key"] == "msg-id")
        if notice_type in ignore_notices:
            # These notices are not relevant to bot functions
            return
        elif notice_type in cooldown_notices:
            # Cooldown
            self.pub_logger.info(
                f"Received {notice_type} in {e.target}; adding to cooldown")
            self.db.setChannelCooldown(e.target[1:])
        elif notice_type in leave_notices:
            # These notices are a good indication we should leave
            self.pub_logger.info(
                f"Received {notice_type} in {e.target}; leaving")
            if(self.db.getChannel(e.target)):
                self.db.delChannel(e.target)
                c.part(e.target)
        else:
            self.pub_logger.info(
                f"Received unknown notice ({notice_type}) in {e.target}")

    def on_privnotice(self, c, e):
        notice_type = next(tag["value"]
                           for tag in e.tags if tag["key"] == "msg-id")
        self.priv_logger(
            f"Received unknown private notice ({notice_type}) in {e.target}")


def main():
    bot = GhirahimBot()
    bot.start()


if __name__ == "__main__":
    main()
