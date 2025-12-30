import asyncio
import os

from dbus_next.errors import InterfaceNotFoundError

from pyantonlib.utils import log_info, log_warn
from anton.call_status_pb2 import Status
from anton.state_pb2 import DeviceState
from anton.media_pb2 import Media, PlayStatus, PlayerCapabilities, Image, VolumeControl
from anton.plugin_messages_pb2 import GenericPluginToPlatformMessage
from pyantonlib.exceptions import ResourceNotFound

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


def get_album_art(url):
    img = Image()
    if url.startswith("file://"):
        local_path = url.replace("file://", "")
        if os.path.exists(local_path):
            try:
                with open(local_path, "rb") as f:
                    img.data_raw = f.read()
                    img.mime_type = "image/jpeg"  # Default or sniff mime
            except Exception:
                img.url = ""  # Fallback
    else:
        img.url = url
    return img


class Player:

    def __init__(self, uri, callbacks):
        self.uri = uri
        self.callbacks = callbacks
        self.proxy = None
        self.media_iface = None
        self.prop_iface = None
        self.root_iface = None

        self.player_name = "Unknown Player"
        self.play_status = PlayStatus.STOPPED
        self.current_media = Media()
        self.capabilities = PlayerCapabilities(player_id=uri)

    async def connect(self, context):
        self.proxy = await get_dbus_proxy(context.dbus, self.uri, MP2_OBJECT)

        self.media_iface = self.proxy.get_interface(MEDIA_IFACE)
        self.prop_iface = self.proxy.get_interface(PROPERTIES_IFACE)
        self.root_iface = self.proxy.get_interface(PLAYER_IFACE)

        self.prop_iface.on_properties_changed(self._on_dbus_properties_changed)
        props = await self.prop_iface.call_get_all(MEDIA_IFACE)
        root_props = await self.prop_iface.call_get_all(PLAYER_IFACE)

        self.player_name = root_props.get('Identity').value or "Unknown Player"
        self._update_capabilities(root_props)
        self._update_internal_state(props)
        print("Connected to:", self.uri)

    def fill_capabilities(self, player_capabilities):
        player_capabilities.supported_states[:] = [
            PlayStatus.PLAYING, PlayStatus.PAUSED, PlayStatus.STOPPED
        ]

    async def disconnect(self):
        self.proxy = None

    async def play(self):
        return await self.media_iface.call_play()

    async def pause(self):
        return await self.media_iface.call_pause()

    async def next(self):
        return await self.media_iface.call_next()

    async def previous(self):
        return await self.media_iface.call_previous()

    async def stop(self):
        return await self.media_iface.stop()

    def _on_dbus_properties_changed(self, iface, changed, invalidated):
        if iface == MEDIA_IFACE:
            self._update_internal_state(changed)
        elif iface == PLAYER_IFACE:
            self._update_capabilities(changed)

    def _update_capabilities(self, root_props):
        self.capabilities.supports_play_next = (
            'CanGoNext' in root_props and root_props.get('CanGoNext').value
            or False)
        self.capabilities.supports_play_now = (
            'CanControl' in root_props and root_props.get('CanControl').value
            or False)

        states = [PlayStatus.STOPPED]
        if 'CanPlay' in root_props and root_props.get('CanPlay').value:
            states.append(PlayStatus.PLAYING)
        if 'CanPause' in root_props and root_props.get('CanPause').value:
            states.append(PlayStatus.PAUSED)
        self.capabilities.supported_states[:] = states

    def _update_internal_state(self, changed_props):
        changed = False

        if 'PlaybackStatus' in changed_props:
            val = changed_props['PlaybackStatus'].value.upper()
            mapping = {
                "PLAYING": PlayStatus.PLAYING,
                "PAUSED": PlayStatus.PAUSED,
                "STOPPED": PlayStatus.STOPPED
            }
            new_status = mapping.get(val, PlayStatus.PLAY_STATUS_UNKNOWN)
            if self.play_status != new_status:
                self.play_status = new_status
                changed = True

        if 'Metadata' in changed_props:
            meta = changed_props['Metadata'].value

            title = ('xesam:title' in meta and meta.get('xesam:title').value
                     or "Unknown Track")
            if self.current_media.track_name != title:
                self.current_media.track_name = title
                changed = True

            artists = 'xesam:artist' in meta and meta.get(
                'xesam:artist').value or []
            artist_str = artists[0] if artists else "Unknown Artist"
            if self.current_media.artist != artist_str:
                self.current_media.artist = artist_str
                changed = True

            art_url = 'mpris:artUrl' in meta and meta.get(
                'mpris:artUrl').value or ""
            if art_url:
                new_img = get_album_art(art_url)
                if self.current_media.album_art.SerializeToString(
                ) != new_img.SerializeToString():
                    self.current_media.album_art.CopyFrom(new_img)
                    changed = True

        if changed and MEDIA_UPDATED_EVENT in self.callbacks:
            self.callbacks[MEDIA_UPDATED_EVENT](self)


class MediaController(GenericController):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.players = {}

    def on_start(self, context):
        self.players = {}
        self.recent_players = []
        self.current_player = None

        context.loop.run_until_complete(self.async_start(context))

    def fill_capabilities(self, context, capabilities):
        for player in self.players.values():
            capabilities.media.player_capabilities.append(player.capabilities)

    def handle_set_device_state(self, state, callback):
        print("Handling:", state)

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
                log_info("Connected to MPRIS instance: " + player_uri)
            except Exception as e:
                log_warn("Unable to connect to: " + player_uri + ". " + str(e))
                return

            self.players[player_uri] = player
            self.recent_players.append(player)
            self.current_player = player

        elif old_owner and not new_owner:
            player = self.players.pop(player_uri, None)
            if player:
                await player.disconnect()

        self.device_handler.report_capabilities()

    def media_updated(self, player):
        log_info("Media updated..")
        media_changed_event = Media()
        media_changed_event.CopyFrom(player.current_media)
        media_changed_event.player_id = player.uri
        media_changed_event.player_name = player.player_name
        media_changed_event.play_status = player.play_status

        self.device_handler.send_device_state_updated(
            DeviceState(media_state=[media_changed_event]))

    def handle_instruction(self, instruction, callback):
        log_info("Handling instruction: " + str(instruction))
        if instruction.HasField('playlist_instruction'):
            playlist_instruction = instruction.playlist_instruction
            player = self.get_player(playlist_instruction.player_id)
            handle_playlist_instruction(player, playlist_instruction, callback)

        if instruction.HasField('media_instruction'):
            media_instruction = instruction.media_instruction
            player = self.get_player(media_instruction.player_id)
            handle_media_instruction(player, media_instruction, callback)

        if (instruction.volume_instruction
                != VolumeControl.VOLUME_CONTROL_UNKNOWN):
            volume_instruction = instruction.volume_instruction
            handle_volume_instruction(volume_instruction, callback)

    def get_player(self, uri):
        player = self.players.get(uri, None)
        if player is None:
            raise ResourceNotFound(uri)
        return player


def handle_playlist_instruction(player, playlist_instruction, callback):
    if playlist_instruction.WhichOneof('type') == 'next_track':
        asyncio.run(player.next())
        callback(None)


def handle_media_instruction(player, media_instruction, callback):
    if media_instruction.play_state_instruction == PlayStatus.PAUSED:
        asyncio.run(player.pause())
    elif media_instruction.play_state_instruction == PlayStatus.PLAYING:
        asyncio.run(player.play())
    callback(None)
