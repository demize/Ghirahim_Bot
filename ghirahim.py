#!/usr/bin/env python

import irc.bot, irc.connection
import yaml
import ssl

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
        c.cap('REQ', ':twitch.tv/membership')
        c.cap('REQ', ':twitch.tv/tags')
        c.cap('REQ', ':twitch.tv/commands')
        c.join('#' + self.username)

    def on_pubmsg(self, c, e):
        display_name = next(tag for tag in e.tags if tag["key"] == "display-name")["value"]
        msg_id = next(tag for tag in e.tags if tag["key"] == "id")["value"]
        print(f'Received message from {display_name} with ID {msg_id}')
        c.privmsg(e.target, f'/delete {msg_id}')


def main():
    bot = GhirahimBot()
    bot.start()

if __name__ == "__main__":
    main()