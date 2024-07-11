import asyncio
import os
import socket
from pathlib import Path
from uuid import getnode
from threading import Thread, Event

from dbus_next.aio import MessageBus

from pyantonlib.plugin import AntonPlugin
from pyantonlib.channel import DeviceHandlerBase, SettingsHandlerBase
from pyantonlib.channel import DefaultProtoChannel
from pyantonlib.utils import log_info
from anton.state_pb2 import DeviceState
from anton.settings_pb2 import SettingsResponse
from anton.plugin_messages_pb2 import GenericPluginToPlatformMessage
from anton.plugin_pb2 import PipeType
from anton.device_pb2 import DEVICE_STATUS_ONLINE, DEVICE_KIND_COMPUTER

from anton_linux.media import MediaController
from anton_linux.notifications import NotificationsController
from anton_linux.device import DevicePowerController
from anton_linux.interfaces import Context
from anton_linux.settings import Settings


class LocalLinuxInstance(DeviceHandlerBase):
    CONTROLLERS = [
        MediaController, DevicePowerController, NotificationsController
    ]

    def __init__(self, context):
        self.context = context
        self.channel = None  # Will be set by DefaultProtoChannel.

    def on_start(self):
        self.controllers = [x(self.channel) for x in self.CONTROLLERS]
        self.handlers = {
            key: value
            for controller in self.controllers
            for key, value in controller.get_handlers().items()
        }

        event = DeviceState()
        event.friendly_name = socket.gethostname()
        event.kind = DEVICE_KIND_COMPUTER
        event.device_status = DEVICE_STATUS_ONLINE

        for controller in self.controllers:
            controller.on_start(self.context)
            controller.fill_capabilities(self.context, event.capabilities)

        req = GenericPluginToPlatformMessage(device_state_updated=event)
        self.channel.query(req, lambda resp: None)

        for controller in self.controllers:
            controller.on_start(self.context)

    def handle_instruction(self, msg, responder):
        pass

    def handle_set_device_state(self, msg, responder):
        pass


class SettingsHandler(SettingsHandlerBase):

    def __init__(self, path):
        self.channel = None  # Will be set by DefaultProtoChannel.
        self.path = path
        self.settings = Settings(path)

    def on_request(self, msg, responder):
        settings_request = msg.settings_request

        if settings_request.WhichOneof('request_type') == 'get_settings_ui':
            responder(
                GenericPluginToPlatformMessage(
                    settings_response=SettingsResponse(
                        settings_ui_response=self.settings.get_settings_ui()),
                    request_id=msg.request_id))
            return

        if settings_request.WhichOneof('request_type') == 'custom_request':
            res = self.handle_custom_request(settings_request.custom_request)
            responder(
                GenericPluginToPlatformMessage(
                    settings_response=SettingsResponse(custom_response=res),
                    request_id=msg.request_id))
            return

    def handle_custom_request(self, custom_request):
        payload = custom_request.payload
        if payload is None:
            return CustomMessage()

        request = json.loads(payload)

        payload = None
        if request.get('action') == 'get_all_settings':
            payload = json.dumps({
                "type": "settings",
                "payload": self.settings.props
            })
        else:
            payload = None

        return CustomMessage(index=custom_request.index, payload=payload)


class Channel(DefaultProtoChannel):
    pass


class AntonLinuxPlugin(AntonPlugin):

    def setup(self, plugin_startup_info):
        self.context = Context(loop=asyncio.get_event_loop(),
                               dbus=MessageBus())
        self.loop_thread = Thread(target=self.context.loop.run_forever)

        self.device_handler = LocalLinuxInstance(self.context)
        self.settings_handler = SettingsHandler(plugin_startup_info.data_dir)
        self.channel = Channel(self.device_handler, self.settings_handler)

        registry = self.channel_registrar()
        registry.register_controller(PipeType.DEFAULT, self.channel)

    def on_start(self):
        self.context.loop.run_until_complete(self.context.dbus.connect())

        self.device_handler.on_start()

        self.loop_thread.start()

    def on_stop(self):
        self.context.loop.call_soon_threadsafe(self.context.loop.stop)
        self.loop_thread.join()

    def play_pause(self):
        asyncio.run_coroutine_threadsafe(self.media_controller.play_pause(),
                                         self.loop).result()

    def on_response(self, call_status):
        print("Received response:", call_status)
