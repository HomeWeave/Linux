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
from anton.events_pb2 import GenericEvent, DeviceOnlineEvent
from anton.events_pb2 import MediaEvent
from anton.capabilities_pb2 import Capabilities, NotificationCapabilities

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
        notification_capability = NotificationCapabilities(
                simple_text_notification_supported=True)
        capabilities = Capabilities(
                notification_capabilities=notification_capability)

        discovery_event = DeviceOnlineEvent(friendly_name=self.hostname,
                                            capabilities=capabilities)
        device_discovery_event = GenericEvent(device_id=self.mac,
                                              device_online=discovery_event)
        return device_discovery_event

    def media_event(self, media):
        return GenericEvent(device_id=self.mac, media=media)


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
        media_event = MediaEvent(
                player_id=player.uri, player_name=player.player_name,
                track_name=media.title, artist=media.artist, url=media.url,
                album_art=media.album_art_url)
        mapping = {PlayState.PLAYING: MediaEvent.PlayStatus.PLAYING,
                   PlayState.PAUSED: MediaEvent.PlayStatus.PAUSED,
                   PlayState.STOPPED: MediaEvent.PlayStatus.STOPPED}
        media_event.play_status = mapping.get(player.play_state,
                                              MediaEvent.PlayStatus.STOPPED)

        log_info("Media Changed: " + str(media_event))

        self.send_event(self.event_wrapper.media_event(media_event))
