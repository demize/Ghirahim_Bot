#!/usr/bin/env python

import irc.bot, irc.connection
import re
import ssl
from urlextract import URLExtract
import urllib.parse
import yaml

from ghirahim_db.GhirahimDB import GhirahimDB, UserRole

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

    def on_welcome(self, c, e):
        print(f'Connected. Joining #{self.username}.')
        # Request the tags cap, the only cap necessary for the functions of this bot
        c.cap('REQ', ':twitch.tv/tags')
        # Join the bot's own channel
        c.join('#' + self.username)

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
            if(new > role):
                role = new
        return role

    def extract_urls(self, message) -> set:
        """Finds the all domains in a given message.

        Args:
            message: The message to extract domains from.

        Returns:
            set: The set containing all the extracted domains.
        """

        urls = self.extractor.find_urls(message)
        domains = set()
        for url in urls:
            if(self.urlregex.match(url) is None):
                url = "//" + url
            domains.add(urllib.parse.urlparse(url).netloc)
        return domains

    def pubmsg_ownchannel(self, c, e):
        # There's only two commands to worry about here, nothing else matters
        message = " ".join(e.arguments)
        match message.lower().strip():
            case "!join":
                sourceUser = e.source.nick
                print(f"Would join {sourceUser} if joining was implemented")
            case "!leave":
                sourceUser = e.source.nick
                print(f"Would part {sourceUser} if joining was implemented")

    def on_pubmsg(self, c, e):
        # Check if this message is in our own channel or another channel, and parse it accordingly
        if(e.target == "#" + self.username):
            return self.pubmsg_ownchannel(c, e)
        # Extract the user's role, display name, and the message ID
        role = GhirahimBot.parse_badges(next(tag["value"] for tag in e.tags if tag["key"] == "badges"))
        display_name = next(tag["value"] for tag in e.tags if tag["key"] == "display-name")
        msg_id = next(tag["value"] for tag in e.tags if tag["key"] == "id")
        domains = self.extract_urls(" ".join(e.arguments))
        # Print basic information about the message; useful during development
        print(f'Received message from {display_name} ({role}) with ID {msg_id} and links to the following domains: {domains}')
        # Delete the message if it has any URL in it
        if domains:
            c.privmsg(e.target, f'/delete {msg_id}')


def main():
    bot = GhirahimBot()
    bot.start()

if __name__ == "__main__":
    main()