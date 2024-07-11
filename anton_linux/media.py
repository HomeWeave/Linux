from dataclasses import dataclass
from enum import Enum
import asyncio

from dbus_next.errors import InterfaceNotFoundError

from pyantonlib.utils import log_info
from anton.call_status_pb2 import Status
from anton.state_pb2 import DeviceState
from anton.media_pb2 import Media, PlayStatus
from anton.plugin_messages_pb2 import GenericPluginToPlatformMessage

from .interfaces import GenericController

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

    def __init__(self, uri, callbacks):
        self.uri = uri
        self.callbacks = callbacks
        self.proxy = None
        self.media_interface = None
        self.properties_interface = None

        self.current_media = None
        self.play_state = None
        self.player_name = None

    async def connect(self, context):
        self.proxy = await get_dbus_proxy(context.dbus, self.uri, MP2_OBJECT)
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
        self.update_play_state(
            await self.media_interface.get_playback_status(), {})
        self.player_name = await self.player_interface.get_identity()

    async def disconnect(self):
        self.proxy = None

    async def play(self):
        return await self.media_interface.call_play()

    async def pause(self):
        return await self.media_interface.call_pause()

    async def next(self):
        return await self.media_interface.call_next()

    async def previous(self):
        return await self.media_interface.call_previous()

    async def stop(self):
        return await self.media_interface.stop()

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
            self.current_media = Media()

        title = obj.get('xesam:title', None)
        title = title.value if title else '(No title)'

        artist_obj = obj.get('xesam:artist', None)
        if not artist_obj or not artist_obj.value or not artist_obj.value[0]:
            artist = None
        else:
            artist = artist_obj.value[0]

        url = obj.get('xesam:url', None)
        url = url.value if url else ''

        album_art_url = obj.get('mpris:artUrl', None)
        album_art_url = album_art_url.value if album_art_url else ''

        if self.current_media.track_name != title:
            self.current_media.track_name = title
            changed = True

        if self.current_media.artist != artist:
            self.current_media.artist = artist
            changed = True

        if self.current_media.url != url:
            self.current_media.url = url
            changed = True

        if self.current_media.album_art != album_art_url:
            self.current_media.album_art = album_art_url
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

            play_state_updated_func = self.callbacks.get(
                PLAYBACK_CHANGED_EVENT, None)
            if play_state_updated_func:
                play_state_updated_func(self)


class MediaController(GenericController):

    def on_start(self, context):
        self.players = {}
        self.recent_players = []
        self.current_player = None

        context.loop.run_until_complete(self.async_start(context))

    def fill_capabilities(self, context, capabilities):
        pass

    def handle_instruction(self, context, instruction):
        print("Handling:", instruction)

    def get_handlers(self):
        return {"media": self.handle_instruction}

    async def async_start(self, context):
        dbus_proxy = await get_dbus_proxy(context.dbus, DBUS_SERVICE,
                                          DBUS_OBJECT)
        dbus_interface = dbus_proxy.get_interface(DBUS_SERVICE)

        def refresh_players(player_uri, old_owner=None, new_owner=None):
            asyncio.ensure_future(self.refresh_players(context, player_uri,
                                                       old_owner, new_owner),
                                  loop=context.loop)

        dbus_interface.on_name_owner_changed(refresh_players)

        for name in (await dbus_interface.call_list_names()):
            await self.refresh_players(context, name, "", "dummy")

    async def refresh_players(self,
                              context,
                              player_uri,
                              old_owner=None,
                              new_owner=None):
        if not player_uri.startswith(MPRIS_NAME_PREFIX):
            return

        log_info("Found MPRIS instance: " + player_uri)

        if new_owner and not old_owner:
            callbacks = {
                MEDIA_UPDATED_EVENT: self.media_updated,
                PLAYBACK_CHANGED_EVENT: self.media_updated,
            }
            player = Player(player_uri, callbacks)
            try:
                await player.connect(context)
            except:
                return

            self.players[player_uri] = player
            self.recent_players.append(player)
            self.current_player = player

        elif old_owner and not new_owner:
            player = self.players.pop(player_uri, None)
            if player:
                await player.disconnect()

    def media_updated(self, player):
        media = player.current_media
        media_changed_event = Media()
        media_changed_event.player_id = player.uri
        if player.player_name:
            media_changed_event.player_name = player.player_name
        if media.track_name:
            media_changed_event.track_name = media.track_name
        if media.artist:
            media_changed_event.artist = media.artist
        if media.url:
            media_changed_event.url = media.url
        if media.album_art:
            media_changed_event.album_art = media.album_art

        mapping = {
            PlayState.PLAYING: PlayStatus.PLAYING,
            PlayState.PAUSED: PlayStatus.PAUSED,
            PlayState.STOPPED: PlayStatus.STOPPED
        }
        media_changed_event.play_status = mapping.get(player.play_state,
                                                      PlayStatus.STOPPED)

        def callback(status):
            if status.call_status.code != Status.STATUS_OK:
                print("Failed to report Media Update:", status)

        self.channel.query(
            GenericPluginToPlatformMessage(device_state_updated=DeviceState(
                media_state=[media_changed_event])), callback)

    async def play(self, uri=None):
        player = self.players.get(uri, self.current_player)
        return await self.current_player.play()

    async def pause(self, uri=None):
        player = self.players.get(uri, self.current_player)
        return await self.current_player.pause()

    async def stop(self, uri=None):
        player = self.players.get(uri, self.current_player)
        return await self.current_player.stop()

    async def next(self):
        player = self.players.get(uri, self.current_player)
        return await self.current_player.next()

    async def previous(self):
        player = self.players.get(uri, self.current_player)
        return await self.current_player.previous()
