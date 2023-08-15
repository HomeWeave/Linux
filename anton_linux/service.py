import asyncio
import os
import socket
from pathlib import Path
from uuid import getnode
from threading import Thread, Event

from dbus_next.aio import MessageBus

from pyantonlib.plugin import AntonPlugin
from pyantonlib.channel import GenericInstructionController
from pyantonlib.channel import GenericEventController
from pyantonlib.utils import log_info
from anton.plugin_pb2 import PipeType
from anton.events_pb2 import GenericEvent

from anton_linux.media import MediaController
from anton_linux.notifications import NotificationsController
from anton_linux.device import DevicePowerController
from anton_linux.interfaces import Context
from anton_linux.settings import Settings


class AntonLinuxPlugin(AntonPlugin):
    CONTROLLERS = [MediaController, DevicePowerController,
                   NotificationsController]

    def setup(self, plugin_startup_info):
        self.context = Context(loop=asyncio.get_event_loop(),
                               dbus=MessageBus())
        self.loop_thread = Thread(target=self.context.loop.run_forever)

        event_controller = GenericEventController(lambda call_status: 0)
        send_event = event_controller.create_client(0, self.on_response)

        def wrap_send_event(device_id):
            def fn(event):
                event.device_id = device_id
                log_info("Sending: " + str(event))
                send_event(event)
            return fn

        self.controllers = [x(wrap_send_event(hex(getnode())))
                            for x in self.CONTROLLERS]

        apis = {k: v
                for c in self.controllers
                for k, v in c.get_instruction_handlers().items()}
        instruction_controller = GenericInstructionController(apis)

        settings_controller = Settings(plugin_startup_info.data_dir)

        registry = self.channel_registrar()
        registry.register_controller(PipeType.IOT_INSTRUCTION,
                                     instruction_controller)
        registry.register_controller(PipeType.IOT_EVENTS, event_controller)
        registry.register_controller(PipeType.SETTINGS, settings_controller)


    def on_start(self):
        self.context.loop.run_until_complete(self.context.dbus.connect())

        for controller in self.controllers:
            controller.on_start(self.context)

        self.loop_thread.start()


    def on_stop(self):
        self.context.loop.call_soon_threadsafe(self.context.loop.stop)
        self.loop_thread.join()

    def play_pause(self):
        asyncio.run_coroutine_threadsafe(self.media_controller.play_pause(),
                self.loop).result()

    def on_response(self, call_status):
        print("Received response:", call_status)

