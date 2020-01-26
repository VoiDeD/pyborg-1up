"""
pyborg discord module
"""
import asyncio
import logging
import re
import random
from functools import partial
from types import ModuleType
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Union

import aiohttp
import attr
import discord
import toml
import venusian

from .. import pyborg as pyb_core
import pyborg.commands as builtin_commands
from pyborg.util.awoo import normalize_awoos

logger = logging.getLogger(__name__)


@attr.s
class PyborgDiscord(discord.Client):
    """This is the pyborg discord client module.
    It connects over http to a running pyborg/http service."""

    toml_file: str = attr.ib()  # any old path
    multi_port: int = attr.ib(default=2001)
    multiplexing: bool = attr.ib(default=True)
    multi_server: str = attr.ib(default="localhost")
    multi_protocol: str = attr.ib(default="http")
    registry = attr.ib(default=attr.Factory(lambda self: Registry(self), takes_self=True))
    aio_session: aiohttp.ClientSession = attr.ib(init=False)
    save_status_count: int = attr.ib(default=0, init=False)
    pyborg: Optional[pyb_core.pyborg] = attr.ib(default=None)
    scanner: venusian.Scanner = attr.ib(default=None)
    loop: Optional[asyncio.BaseEventLoop] = attr.ib(default=None)
    settings: MutableMapping[str, Any] = attr.ib(default=None)

    def __attrs_post_init__(self) -> None:
        self.settings = toml.load(self.toml_file)
        try:
            self.multiplexing = self.settings["pyborg"]["multiplex"]
            self.multi_server = self.settings["pyborg"]["multiplex_server"]
            self.multi_port = self.settings["pyborg"]["multiplex_port"]
        except KeyError:
            logger.info("Missing config key, you get defaults.")
        if not self.multiplexing:
            # self.pyborg = pyborg.pyborg.pyborg()
            # pyb config parsing isn't ready for python 3.
            raise NotImplementedError
        else:
            self.pyborg = None
        self.aio_session = aiohttp.ClientSession()
        super().__init__(loop=self.loop)  # this might create a asyncio.loop!

    def our_start(self) -> None:
        "launch discord.Client main event loop (calls Client.run)"

        self.scan()
        if "token" in self.settings["discord"]:
            self.run(self.settings["discord"]["token"])
        else:
            logger.error("No Token. Set one in your conf file.")

    async def fancy_login(self) -> None:
        if "token" in self.settings["discord"]:
            await self.login(self.settings["discord"]["token"])
        else:
            logger.error("No Token. Set one in your conf file.")

    async def on_ready(self) -> None:
        print("Logged in as")
        print(self.user.name)
        print(self.user.id)
        print("------")

    def clean_msg(self, contents: str, message: discord.Message) -> str:
        """cleans up an incoming message to be processed by pyborg"""
        # first fix up any nickname flagged mentions, as fixed versions are needed for `_replace_mentions`
        incoming_message = self._fix_mentions(contents)
        # replace all emojis with their text versions
        incoming_message = self._replace_emojis(incoming_message, message)
        # and all mentions with text versions
        incoming_message = self._replace_mentions(incoming_message, message)
        # normalize awoos
        incoming_message = normalize_awoos(incoming_message)

        return incoming_message

    def _replace_emojis(self, content: str, message: discord.Message) -> str:
        """replaces all emojis within an incoming discord message and returns the cleaned up message contents"""

        def _extract_emoji(emoji_name: str) -> str:
            return emoji_name

        return re.sub(r"<:(?P<emoji>\w+):\d+>", lambda x: _extract_emoji(x.group("emoji")), content)

    def _replace_mentions(self, content: str, message: discord.Message) -> str:
        """replaces all mentions within an incoming discord message and returns the cleaned up message contents"""

        def _extract_member_name(user_id: str) -> str:
            try:
                int_id = int(user_id)
            except ValueError:
                logger.error("Discord user_id wasn't an integer!")
                return user_id
            
            member: Optional[discord.Member] = message.guild.get_member(int_id)

            if member is not None:
                return member.display_name

            # if we couldn't find the member in the guild... shit's fucked
            logger.error("Unable to find guild member with user_id: %s", user_id)
            return user_id

        return re.sub(r"<@(?P<userid>\d+)>", lambda x: _extract_member_name(x.group("userid")), content)

    def _fix_mentions(self, content: str) -> str:
        """replaces discord's nickname flagged mentions (ex: <@!123>) with regular mentions (ex: <@123>)"""
        return re.sub(r"<@!(\d+)>", lambda x: f"<@{x.group(1)}>", content)

    async def on_message(self, message: discord.Message) -> None:
        """message.content  ~= <@221134985560588289> you should play dota"""

        if message.type != discord.MessageType.default:
            # ignore non-chat messages
            return

        if message.author.bot:
            # ignore messages from bots
            return

        if message.author == self.user:
            # ignore messages from ourselves - likely covered in the bot case but whatever
            return
        
        if not message.content:
            # if we somehow don't have any message content, we can't do anything
            return

        # handle commands first
        if message.content[0] == "!":
            command_name = message.content.split()[0][1:]

            if command_name in ["list", "help"]:
                help_text = "I have a bunch of commands:"
                for k, _ in self.registry.registered.items():
                    help_text += " !{}".format(k)
                await message.channel.send(help_text)

            else:
                if command_name in self.registry.registered:
                    command = self.registry.registered[command_name]
                    logger.debug("cmd: Running command %s", command)
                    logger.debug("cmd: pass message?: %s", command.pass_msg)
                    if command.pass_msg:
                        await message.channel.send(command(msg=message.content))
                    else:
                        await message.channel.send(command())
            return

        logger.info("raw message: %s", message.content)

        if self.save_status_count % 5 == 0:
            async with self.aio_session.get(
                f"http://{self.multi_server}:{self.multi_port}/meta/status.json", raise_for_status=True
            ) as ret_status:
                data = await ret_status.json()
                if data["status"]:
                    await self.change_presence(activity=discord.Game("Saving brain..."))
                else:
                    await self.change_presence(activity=discord.Game("hack the planet"))

        self.save_status_count += 1

        # were we mentioned by an actual user (and not a bot)?
        was_mentioned = self.user.mentioned_in(message) or self._plaintext_mentioned(message)

        if self.settings["discord"]["learning"] and not was_mentioned:
            # learn any text that didn't mention us, since we won't be replying
            incoming_message = self.clean_msg(message.content, message)
            logger.info("learning: %s", incoming_message)

            await self.learn(incoming_message)

        if was_mentioned:
            # we were mentioned, so lets see if we should reply
            content = self._fix_mentions(message.content)
            split_content = content.split()
            my_name = self.user.display_name

            # if we were mentioned as the first word of the message, we need to strip the mention so we don't reply to that word
            if split_content[0] == self.user.mention:
                # we were discord mentioned, so simply strip that word
                content = " ".join(split_content[1:])
            elif content.lower().startswith(my_name.lower()):
                # we were plaintext mentioned, so strip that plaintext off the content
                content = content[len(my_name) + 1:]

            if not content:
                # we stripped off the first word mentions, and if now there is no remaining message content we don't want to do anything
                return

            incoming_message = self.clean_msg(content, message)
            logger.info("input message: %s", incoming_message)
                
            async with message.channel.typing():
                # retrieve reply from pyborg
                msg = await self.reply(incoming_message)
                logger.info("replying with: %s", msg)

                if msg:
                    # go through every word in the reply and see if we can randomly replace it with a matching emoji
                    emoji_map = {x.name: x for x in message.guild.emojis}
                    for word in msg.split():
                        if word in emoji_map and random.random() <= 0.05:  # 5% chance to replace text with a server emoji
                            e = emoji_map[word]
                            msg = msg.replace(word, "<:{}:{}>".format(e.name, e.id))

                    # make sure we don't annoy people
                    msg = msg.replace("@everyone", "`@everyone`")
                    msg = msg.replace("@here", "`@here`")

                    await message.channel.send(msg)
                else:
                    await message.channel.send("I don't know anything about that yet :(")

    def _plaintext_mentioned(self, message: discord.Message) -> bool:
        "returns true if should ping with plaintext nickname per-server if configured"
        try:
            if self.settings["discord"]["plaintext_ping"]:
                return message.guild.me.display_name.lower() in message.content.lower()
            else:
                return False
        except KeyError:
            return False

    async def learn(self, body: str) -> None:
        """thin wrapper for learn to switch to multiplex mode"""
        if self.settings["pyborg"]["multiplex"]:
            await self.aio_session.post(f"http://{self.multi_server}:{self.multi_port}/learn", data={"body": body}, raise_for_status=True)

    async def reply(self, body: str) -> Union[str, None]:
        """thin wrapper for reply to switch to multiplex mode: now coroutine"""
        if self.settings["pyborg"]["multiplex"]:
            url = f"http://{self.multi_server}:{self.multi_port}/reply"
            async with self.aio_session.post(url, data={"body": body}, raise_for_status=True) as ret:
                reply = await ret.text()
                logger.debug("got reply: %s", reply)
            return reply
        else:
            raise NotImplementedError

    async def teardown(self) -> None:
        "turn off the bot"
        await self.aio_session.close()

    def scan(self, module: ModuleType = builtin_commands) -> None:
        "look for commands to add to registry"
        self.scanner = venusian.Scanner(registry=self.registry)
        self.scanner.scan(module)


#
# class FancyCallable(Callable):
#    pass_msg: bool


class Registry:
    """Command registry of decorated pyborg commands"""

    def __init__(self, mod: PyborgDiscord) -> None:
        self.registered: Dict[str, Callable] = {}
        self.mod = mod

    def __str__(self):
        return f"{self.mod} command registry with {len(self.registered.keys())}"

    def add(self, name: str, ob: Callable, internals: bool, pass_msg: bool) -> None:
        "add command to the registry. takes two config options."
        self.registered[name] = ob
        if internals:
            self.registered[name] = partial(
                ob, self.mod.multiplexing, multi_server="http://{}:{}/".format(self.mod.multi_server, self.mod.multi_port)
            )
            self.registered[name].pass_msg = False
        if pass_msg:
            self.registered[name].pass_msg = True
        else:
            self.registered[name].pass_msg = False
