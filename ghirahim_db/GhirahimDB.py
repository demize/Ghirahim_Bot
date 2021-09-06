from pymongo import MongoClient
import redis
from enum import Enum
from functools import total_ordering

'''
Sample channel:
{
    "name": "demize95",
    "slash": true,
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

class Channel():
    def __init__(self, name: str, slash: bool, userlevel: UserRole, reply: str, allow_list: set):
        self.name = name
        self.slash = slash
        self.userlevel = userlevel
        self.reply = reply
        self.allow_list = allow_list

class GhirahimDB:
    def __init__(self, mongoConnectStr: str, redisHost: str, redisPort: int, redisDb: int) -> None:
        self.mongo = MongoClient(mongoConnectStr).get_default_database()
        self.redis = redis.Redis(redisHost, redisPort, redisDb)

    def getChannels(self) -> list:
        # Go straight to Mongo for this, we won't be caching it
        collection = self.mongo.get_collection("channels")
        channels = list()
        for doc in collection:
            channels.append(doc["name"])

    def _getChannelRedis(self, name: str) -> Channel:
        redisName = "channel:" + name
        if self.redis.exists(redisName + ":config", redisName + ":allowlist") < 2:
            return None
        slash = self.redis.hget(redisName + ":config", "slash")==b"True"
        match self.redis.hget(redisName + ":config", "userlevel").upper():
            case b"BROADCASTER":
                userlevel = UserRole.BROADCASTER
            case b"MODERATOR":
                userlevel = UserRole.MODERATOR
            case b"VIP":
                userlevel = UserRole.VIP
        reply = str(self.redis.hget(redisName + ":config", "reply"), 'utf-8')
        allow_list = self.redis.smembers(redisName + ":allowlist")

        return Channel(name, slash, userlevel, reply, allow_list)

    def _getChannelMongo(self, name: str):
        pass


    def getChannel(self, name: str):
        # Try Redis first, then go to Mongo
        if(self._getChannelRedis(name) is None):
            pass

    def setChannel(self, channel: Channel):
        # Set in both Redis and Mongo

        # Convert some things to strings first
        slashStr = str(channel.slash)
        roleStr = str(channel.userlevel)

        redisName = "channel:" + channel.name
        self.redis.hset(redisName + ":config", "name", channel.name)
        self.redis.hset(redisName + ":config", "slash", slashStr)
        self.redis.hset(redisName + ":config", "userlevel", roleStr)
        self.redis.hset(redisName + ":config", "reply", channel.reply)
        self.redis.delete(redisName + ":allowlist")
        for domain in channel.allow_list:
            self.redis.sadd(redisName + ":allowlist", domain)
