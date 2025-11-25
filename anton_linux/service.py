import asyncio
import os
import socket
from pathlib import Path
from uuid import getnode
from threading import Thread, Event

from dbus_next.aio import MessageBus

from pyantonlib.plugin import AntonPlugin
from pyantonlib.channel import AppHandlerBase, DeviceHandlerBase
from pyantonlib.channel import DefaultProtoChannel
from pyantonlib.utils import log_info
from anton.state_pb2 import DeviceState
from anton.plugin_messages_pb2 import GenericPluginToPlatformMessage
from anton.plugin_pb2 import PipeType
from anton.device_pb2 import DEVICE_STATUS_ONLINE, DEVICE_KIND_COMPUTER
from anton.call_status_pb2 import CallStatus, Status
from anton.ui_pb2 import Page, CustomMessage, DynamicAppRequestType

from anton_linux.media import MediaController
from anton_linux.notifications import NotificationsController
from anton_linux.device import DevicePowerController
from anton_linux.interfaces import Context
from anton_linux.settings import Settings


class LocalLinuxInstance(DeviceHandlerBase):
    CONTROLLERS = [
        MediaController, DevicePowerController, NotificationsController
    ]

    def __init__(self, context, settings):
        super().__init__()
        self.settings = settings
        self.context = context

    def on_start(self):
        self.controllers = [x(self) for x in self.CONTROLLERS]

        event = DeviceState()
        event.friendly_name = socket.gethostname()
        event.kind = DEVICE_KIND_COMPUTER
        event.device_status = DEVICE_STATUS_ONLINE

        for controller in self.controllers:
            controller.on_start(self.context)
            controller.fill_capabilities(self.context, event.capabilities)

        self.send_device_state_updated(event)

    def handle_set_device_state(self, state, callback):
        for controller in self.controllers:
            controller.handle_set_device_state(state, callback)

    def send_device_state_updated(self, state):
        state.device_id = str(getnode())
        super().send_device_state_updated(state)


class AppHandler(AppHandlerBase):

    def __init__(self, plugin_startup_info, device_handler):
        super().__init__(plugin_startup_info, incoming_message_key='action')
        self.settings = Settings(plugin_startup_info.data_dir)
        self.device_handler = device_handler

    def get_ui_path(self, app_type):
        if app_type == DynamicAppRequestType.SETTINGS:
            return "ui/settings_ui.pbtxt"


class Channel(DefaultProtoChannel):
    pass


class AntonLinuxPlugin(AntonPlugin):

    def setup(self, plugin_startup_info):
        self.context = Context(loop=asyncio.get_event_loop(),
                               dbus=MessageBus())
        self.loop_thread = Thread(target=self.context.loop.run_forever)

        self.settings = Settings(plugin_startup_info.data_dir)
        self.device_handler = LocalLinuxInstance(self.context, self.settings)
        self.app_handler = AppHandler(plugin_startup_info, self.device_handler)

        self.channel = Channel(self.device_handler, self.app_handler)
        registry = self.channel_registrar()
        registry.register_controller(PipeType.DEFAULT, self.channel)

    def on_start(self):
        self.context.loop.run_until_complete(self.context.dbus.connect())

        # All on_starts are sync, asyncio loop starts later.
        self.device_handler.on_start()

        self.loop_thread.start()

    def on_stop(self):
        self.context.loop.call_soon_threadsafe(self.context.loop.stop)
        self.loop_thread.join()

    def on_response(self, call_status):
        print("Received response:", call_status)
