import yaml
from pymongo import MongoClient

def main():
    # Load the config
    with open('ghirahim.yaml', 'r') as f:
        config = yaml.load(f, Loader=yaml.BaseLoader)
    mongo = MongoClient(config["mongo"]["connect_string"]).get_default_database()
    for channel in mongo.get_collection("channels").find():
        updated = False
        if channel.get("name", None) is None:
            raise ValueError(f"Missing name. {channel}")
        if channel.get("slash", None) is None:
            print(f"Missing slash in {channel['name']}. Updating.")
            channel["slash"] = True
            updated = True
        if channel.get("dot", None) is None:
            print(f"Missing dot in {channel['name']}. Updating.")
            channel["dot"] = True
            updated = True
        if channel.get("subdomains", None) is None:
            print(f"Missing subdomains in {channel['name']}. Updating.")
            channel["subdomains"] = True
            updated = True
        if channel.get("userlevel", None) is None:
            print(f"Missing userlevel in {channel['name']}. Updating.")
            channel["userlevel"] = "VIP"
            updated = True
        if channel.get("reply", None) is None:
            print(f"Missing reply in {channel['name']}. Updating.")
            channel["reply"] = "@__user__, please ask for permission before posting a link."
            updated = True
        if channel.get("allow_list", None) is None:
            print(f"Missing allow_list in {channel['name']}. Updating.")
            channel["allow_list"] = []
            updated = True
        if updated:
            mongo.get_collection("channels").replace_one({ "name": channel["name"] }, channel)
"""

"slash": true,
    "dot": false,
    "subdomains": true,
    "userlevel": UserRole.VIP,
    "reply": "default",
    "allow_list": ["youtube.com", "twitch.tv", "twitter.com", "docs.google.com", "prnt.sc", "gyazo.com", "youtu.be"]

"""

if __name__ == "__main__":
    main()