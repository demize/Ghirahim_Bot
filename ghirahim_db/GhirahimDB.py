from typing import Collection
from pymongo import MongoClient
import redis
from enum import Enum
from functools import total_ordering
import datetime

'''
Sample channel:
{
    "name": "demize95",
    "slash": true,
    "subdomains": true,
    "userlevel": UserRole.VIP,
    "reply": "default",
    "allow_list": ["youtube.com", "twitch.tv", "twitter.com", "docs.google.com", "prnt.sc", "gyazo.com", "youtu.be"]
}
'''


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

    def __str__(self) -> str:
        match self:
            case UserRole.USER:
                return "USER"
            case UserRole.SUBSCRIBER:
                return "SUBSCRIBER"
            case UserRole.VIP:
                return "VIP"
            case UserRole.MODERATOR:
                return "MODERATOR"
            case UserRole.BROADCASTER:
                return "BROADCASTER"
            case _:
                return "UNKNOWN"

    @staticmethod
    def fromStr(string: str):
        """Returns a UserRole based on the given string.
        Will return None for invalid inputs.
        """
        match string.upper():
            case "USER":
                return UserRole.USER
            case "SUBSCRIBER":
                return UserRole.SUBSCRIBER
            case "VIP":
                return UserRole.VIP
            case "MODERATOR":
                return UserRole.MODERATOR
            case "BROADCASTER":
                return UserRole.BROADCASTER
            case _:
                return None


class Channel():
    def __init__(self, name: str, slash: bool, subdomains: bool, userlevel: UserRole, reply: str, allow_list: list):
        """Initializes a channel with the given options.
        """
        self.name = name
        self.slash = slash
        self.subdomains = subdomains
        self.userlevel = userlevel
        self.reply = reply
        self.allow_list = allow_list

    @classmethod
    def fromDefaults(cls, name: str):
        """Returns a new channel with "safe" default settings.
        These settings require a slash in order to count a URL, allow VIPs and higher to post links,
        use the default reply, and have no links in the allow list.

        Args: 
            name: The name of the channel you're creating.

        Returns:
            Channel: A Channel with the supplied name and the default settings.
        """
        return cls(name,
                   slash=True,
                   subdomains=True,
                   userlevel=UserRole.VIP,
                   reply="default",
                   allow_list=list())

    @classmethod
    def fromDict(cls, fromDict: dict):
        """Returns an instance of Channel based on the supplied dict.
        The dict should include all arguments required by __init__.
        """
        allow_list = list()
        for entry in fromDict["allow_list"]:
            if isinstance(entry, bytes):
                entry = str(entry, 'utf-8')
            allow_list.append(entry)

        return cls(fromDict["name"],
                   fromDict["slash"],
                   fromDict["subdomains"],
                   UserRole.fromStr(fromDict["userlevel"]),
                   fromDict["reply"],
                   allow_list)

    def toDict(self) -> dict:
        """Returns a dict based on the instance of the class.
        """
        return {
            "name": self.name,
            "slash": self.slash,
            "subdomains": self.subdomains,
            "userlevel": str(self.userlevel),
            "reply": self.reply,
            "allow_list": list(self.allow_list),
        }


class GhirahimDB:
    def __init__(self, mongoConnectStr: str, redisHost: str, redisPort: int, redisDb: int) -> None:
        self.mongo = MongoClient(mongoConnectStr).get_default_database()
        self.redis = redis.Redis(redisHost, redisPort, redisDb)

    def getChannels(self) -> list:
        """Gets the list of channels from MongoDB.
        """
        # Go straight to Mongo for this, we won't be caching it
        return self.mongo.get_collection("channels").distinct("name")

    def _getChannelRedis(self, name: str) -> Channel | None:
        redisName = "channel:" + name
        if self.redis.exists(redisName + ":config", redisName + ":allowlist") < 2:
            return None
        slash = self.redis.hget(redisName + ":config", "slash") == b"True"
        subdomains = self.redis.hget(
            redisName + ":config", "subdomains") == b"True"
        userlevel = UserRole.fromStr(
            str(self.redis.hget(redisName + ":config", "userlevel"), 'utf-8').upper())
        reply = str(self.redis.hget(redisName + ":config", "reply"), 'utf-8')
        allow_list = list()
        for entry in self.redis.smembers(redisName + ":allowlist"):
            if isinstance(entry, bytes):
                entry = str(entry, 'utf-8')
            allow_list.append(entry)

        return Channel(name=name, slash=slash, subdomains=subdomains, userlevel=userlevel, reply=reply, allow_list=allow_list)

    def _getChannelMongo(self, name: str) -> Channel | None:
        chan = self.mongo.get_collection("channels").find_one({"name": name})
        if chan is None:
            return None
        return Channel.fromDict(chan)

    def getChannel(self, name: str):
        # Try Redis first, then go to Mongo
        channel = self._getChannelRedis(name)
        if(channel is None):
            channel = self._getChannelMongo(name)
            if(channel is not None):
                self._setChannelRedis(channel)
        return channel

    def _setChannelRedis(self, channel: Channel):
        # Convert some things to strings first
        slashStr = str(channel.slash)
        roleStr = str(channel.userlevel)

        redisName = "channel:" + channel.name
        self.redis.hset(redisName + ":config", "name", channel.name)
        self.redis.hset(redisName + ":config", "slash", slashStr)
        self.redis.hset(redisName + ":config", "subdomains", slashStr)
        self.redis.hset(redisName + ":config", "userlevel", roleStr)
        self.redis.hset(redisName + ":config", "reply", channel.reply)
        self.redis.delete(redisName + ":allowlist")
        for domain in channel.allow_list:
            self.redis.sadd(redisName + ":allowlist", domain)
        self.redis.expire(redisName + ":config", 1800)
        self.redis.expire(redisName + ":allowlist", 1800)

    def _setChannelMongo(self, channel: Channel):
        self.mongo.get_collection("channels").replace_one(
            {"name": channel.name}, channel.toDict(), upsert=True)

    def setChannel(self, channel: Channel):
        """Inserts or updates a channel in both MongoDB and Redis.
        """
        self._setChannelRedis(channel)
        self._setChannelMongo(channel)

    def delChannel(self, channel: Channel | str):
        if isinstance(channel, Channel):
            channel = channel.name
        self.redis.delete("channel:" + channel + ":config",
                          "channel:" + channel + ":allowlist")
        self.mongo.get_collection("channels").delete_one({"name": channel})

    def issuePermit(self, channel: Channel, username: str):
        """Adds a permit to Redis for the specified user in the specified channel.
        """
        self.redis.setex("permit:" + channel.name + ":" +
                         username, datetime.timedelta(minutes=5), "Yes")

    def checkPermit(self, channel: Channel, username: str) -> bool:
        """Checks Redis to see if the specified user has a valid permit in the specified channel.
        """
        return self.redis.get("permit:" + channel.name + ":" + username) is not None

    def setChannelCooldown(self, channel: str):
        self.redis.setex("cooldown:" + channel, datetime.timedelta(minutes=5), "Yes")

    def checkChannelCooldown(self, channel: str) -> bool:
        return self.redis.get("cooldown:" + channel) is not None