from dataclasses import dataclass
from enum import Enum
import asyncio

from dbus_next.errors import InterfaceNotFoundError

DBUS_SERVICE = 'org.freedesktop.DBus'
DBUS_OBJECT = '/org/freedesktop/DBus'
MPRIS_NAME_PREFIX = "org.mpris.MediaPlayer2"
MP2_OBJECT = "/org/mpris/MediaPlayer2"
MEDIA_IFACE = "org.mpris.MediaPlayer2.Player"
PLAYER_IFACE = "org.mpris.MediaPlayer2"
PROPERTIES_IFACE = 'org.freedesktop.DBus.Properties'


MEDIA_UPDATED_EVENT = 'MEDIA_UPDATED'
PLAYBACK_CHANGED_EVENT = 'PLAYBACK_CHANGED'

async def get_dbus_proxy(dbus, uri, path):
    introspect = await dbus.introspect(uri, path)
    return dbus.get_proxy_object(uri, path, introspect)


class PlayState(Enum):
    PLAYING = 0
    PAUSED = 1
    STOPPED = 2


@dataclass
class MediaState:
    title: str = None
    artist: str = None
    url: str = None
    album_art_url: str = None


class Player:
    def __init__(self, dbus, uri, loop, callbacks):
        self.uri = uri
        self.dbus = dbus
        self.loop = loop
        self.callbacks = callbacks
        self.proxy = None
        self.media_interface = None
        self.properties_interface = None

        self.current_media = None
        self.play_state = None
        self.player_name = None

    async def connect(self):
        self.proxy = await get_dbus_proxy(self.dbus, self.uri, MP2_OBJECT)
        try:
            self.media_interface = self.proxy.get_interface(MEDIA_IFACE)
        except InterfaceNotFoundError as e:
            raise e

        try:
            self.properties_interface = self.proxy.get_interface(
                    PROPERTIES_IFACE)
        except InterfaceNotFoundError as e:
            raise e

        try:
            self.player_interface = self.proxy.get_interface(PLAYER_IFACE)
        except InterfaceNotFoundError as e:
            raise e

        print("Connected to:", self.uri)
        self.media_interface.on_seeked(self.on_seeked)
        self.properties_interface.on_properties_changed(self.on_state_changed)

        self.update_metadata(await self.media_interface.get_metadata(), {})
        self.update_play_state(await self.media_interface.get_playback_status(),                               {})
        self.player_name = await self.player_interface.get_identity()

    async def disconnect(self):
        pass

    async def play_pause(self):
        return await self.media_interface.call_play_pause()

    def on_seeked(self, time):
        print("Seek: ", time)

    def on_state_changed(self, iface, new_data, old_props):
        # print("New:", new_data)
        if 'Metadata' in new_data:
            self.update_metadata(new_data['Metadata'].value, old_props)

        if 'PlaybackStatus' in new_data:
            self.update_play_state(new_data['PlaybackStatus'].value, old_props)

    def update_metadata(self, obj, old_props):
        changed = False

        if self.current_media is None:
            self.current_media = MediaState()

        title = obj.get('xesam:title', None)
        title = title.value if title else '(No title)'

        artist = obj.get('xesam:artist', None)
        artist = artist.value if artist else '(No artist)'

        url = obj.get('xesam:url', None)
        url = url.value if url else ''

        album_art_url = obj.get('mpris:artUrl', None)
        album_art_url = album_art_url.value if album_art_url else ''

        if self.current_media.title != title:
            self.current_media.title = title
            changed = True

        if self.current_media.artist != artist:
            self.current_media.artist = artist
            changed = True

        if self.current_media.url != url:
            self.current_media.url = url
            changed = True

        if self.current_media.album_art_url != album_art_url:
            self.current_media.album_art_url = album_art_url
            changed = True

        if not changed:
            return

        media_updated_func = self.callbacks.get(MEDIA_UPDATED_EVENT, None)
        if media_updated_func:
            media_updated_func(self)

    def update_play_state(self, obj, old_props):
        changed = False

        new_state = PlayState[obj.upper()]

        if self.play_state != new_state:
            self.play_state = new_state

            play_state_updated_func = self.callbacks.get(PLAYBACK_CHANGED_EVENT,
                                                         None)
            if play_state_updated_func:
                play_state_updated_func(self)


class MediaController:
    PLAYER_OPENED = 'PLAYER_OPENED'
    PLAYER_CLOSED = 'PLAYER_CLOSED'

    def __init__(self, dbus, loop, callbacks):
        self.dbus = dbus
        self.loop = loop
        self.players = {}
        self.recent_players = []
        self.current_player = None
        self.callbacks = callbacks

    async def start(self):
        dbus_proxy = await get_dbus_proxy(self.dbus, DBUS_SERVICE, DBUS_OBJECT)
        dbus_interface = dbus_proxy.get_interface(DBUS_SERVICE)
        dbus_interface.on_name_owner_changed(self.refresh_players_sync)

        for name in (await dbus_interface.call_list_names()):
            await self.refresh_players(name, "", "dummy")


    async def refresh_players(self, player_uri, old_owner=None, new_owner=None):
        if not player_uri.startswith(MPRIS_NAME_PREFIX):
            return

        if new_owner and not old_owner:
            player = Player(self.dbus, player_uri, self.loop, self.callbacks)
            try:
                await player.connect()
            except:
                return

            self.players[player_uri] = player
            self.recent_players.append(player)
            self.current_player = player

        elif old_owner and not new_owner:
            player = self.players.pop(player_uri, None)
            if player:
                await player.disconnect()

    def refresh_players_sync(self, player_uri, old_owner=None, new_owner=None):
        asyncio.ensure_future(
                self.refresh_players(player_uri, old_owner, new_owner),
                loop=self.loop)

    async def play_pause(self):
        return await self.current_player.play_pause()