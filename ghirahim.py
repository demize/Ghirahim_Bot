#!/usr/bin/env python

from enum import Enum
from functools import total_ordering
import irc.bot, irc.connection
import ssl
import yaml

@total_ordering
class UserRole(Enum):
    """Represents a user's role in chat.
    Implements ordering such that USER < SUBSCRIBER < VIP < MODERATOR < BROADCASTER.
    """
    USER = 0
    SUBSCRIBER = 1
    VIP = 2
    MODERATOR = 3
    BROADCASTER = 4

    def __gt__(self, other) -> bool:
        if(self.__class__ is other.__class__):
            return self.value > other.value
        return NotImplemented

    def __eq__(self, other) -> bool:
        if(self.__class__ is other.__class__):
            return self.value == other.value
        return False

class GhirahimBot(irc.bot.SingleServerIRCBot):
    def __init__(self):
        with open('ghirahim.yaml', 'r') as f:
            config = yaml.load(f, Loader=yaml.BaseLoader)
        self.username = config["ghirahim"]["username"]
        self.password = config["ghirahim"]["password"]

        server = "irc.chat.twitch.tv"
        port = 6697
        factory = irc.connection.Factory(wrapper=ssl.wrap_socket)
        print(f'Connecting to {server} on {port} as {self.username} with SSL...')
        irc.bot.SingleServerIRCBot.__init__(self, [(server, port, self.password)], self.username, self.username, connect_factory=factory)

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

    def on_pubmsg(self, c, e):
        # Extract the user's role, display name, and the message ID
        role = GhirahimBot.parse_badges(next(tag["value"] for tag in e.tags if tag["key"] == "badges"))
        display_name = next(tag["value"] for tag in e.tags if tag["key"] == "display-name")
        msg_id = next(tag["value"] for tag in e.tags if tag["key"] == "id")
        # Print basic information about the message; useful during development
        print(f'Received message from {display_name} ({role}) with ID {msg_id}')
        # Delete the message indiscriminately
        c.privmsg(e.target, f'/delete {msg_id}')


def main():
    bot = GhirahimBot()
    bot.start()

if __name__ == "__main__":
    main()