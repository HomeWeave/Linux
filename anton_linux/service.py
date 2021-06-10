import asyncio
import os
import socket
from uuid import getnode
from threading import Thread, Event

from dbus_next.aio import MessageBus

from pyantonlib.plugin import AntonPlugin
from pyantonlib.channel import GenericInstructionController
from pyantonlib.channel import GenericEventController
from pyantonlib.utils import log_info
from anton.plugin_pb2 import PipeType
from anton.events_pb2 import GenericEvent
from anton.media_pb2 import PlayStatus
from anton.device_pb2 import DeviceKind
from anton.device_pb2 import DEVICE_STATUS_ONLINE, DEVICE_KIND_COMPUTER
from anton.media_pb2 import MediaChangedEvent
from anton.power_pb2 import POWER_OFF, SCREEN_OFF, SLEEP

from anton_linux.media import MediaController, PlayState
from anton_linux.media import MEDIA_UPDATED_EVENT, PLAYBACK_CHANGED_EVENT

def send_notification(instruction):
    os.system('notify-send "' + instruction.simple_notification_instruction.text
              + '"')


APIS = {
    "simple_notification_instruction": send_notification,
}

class EventWrapper:
    def __init__(self):
        self.hostname = socket.gethostname()
        self.mac = hex(getnode())

    def online_event(self):
        event = GenericEvent(device_id=self.mac)
        event.device.friendly_name = self.hostname
        event.device.device_kind = DEVICE_KIND_COMPUTER
        event.device.device_status = DEVICE_STATUS_ONLINE

        capabilities = event.device.capabilities
        capabilities.notifications.simple_text_notification_supported = True
        capabilities.notifications.media_notification_supported = True
        capabilities.power_state.supported_power_states[:] = [
                POWER_OFF, SCREEN_OFF, SLEEP]

        return event

    def media_event(self, media_changed_event):
        return GenericEvent(device_id=self.mac, media=media_changed_event)


class AntonLinuxPlugin(AntonPlugin):
    def setup(self, plugin_startup_info):
        instruction_controller = GenericInstructionController(APIS)
        event_controller = GenericEventController(lambda call_status: 0)
        self.send_event = event_controller.create_client(0, self.on_response)

        self.event_wrapper = EventWrapper()

        registry = self.channel_registrar()
        registry.register_controller(PipeType.IOT_INSTRUCTION,
                                     instruction_controller)
        registry.register_controller(PipeType.IOT_EVENTS, event_controller)

        self.loop = asyncio.get_event_loop()
        self.loop_thread = Thread(target=self.loop.run_forever)

        self.session_bus = MessageBus()
        callbacks = {
            MEDIA_UPDATED_EVENT: self.media_updated,
            PLAYBACK_CHANGED_EVENT: self.media_updated,
        }
        self.media_controller = MediaController(self.session_bus, self.loop,
                                                callbacks)

    def on_start(self):
        self.send_event(self.event_wrapper.online_event())

        self.loop.run_until_complete(self.session_bus.connect())
        self.loop.run_until_complete(self.media_controller.start())

        self.loop_thread.start()

    def start_event_loop(self):
        self.loop.run_forever()

    def on_stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join()

    def play_pause(self):
        asyncio.run_coroutine_threadsafe(self.media_controller.play_pause(),
                self.loop).result()

    def on_response(self, call_status):
        print("Received response:", call_status)

    def media_updated(self, player):
        media = player.current_media
        media_changed_event = MediaChangedEvent()
        if player.uri:
            media_changed_event.media.player_id = player.uri
        if player.player_name:
            media_changed_event.media.player_name = player.player_name
        if media.title:
            media_changed_event.media.track_name = media.title
        if media.artist:
            media_changed_event.media.artist = media.artist
        if media.url:
            media_changed_event.media.url = media.url
        if media.album_art_url:
            media_changed_event.media.album_art = media.album_art_url

        mapping = {PlayState.PLAYING: PlayStatus.PLAYING,
                   PlayState.PAUSED: PlayStatus.PAUSED,
                   PlayState.STOPPED: PlayStatus.STOPPED}
        media_changed_event.media.play_status = (
                mapping.get(player.play_state, PlayStatus.STOPPED))

        self.send_event(self.event_wrapper.media_event(media_changed_event))
