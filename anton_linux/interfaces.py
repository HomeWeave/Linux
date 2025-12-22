from dataclasses import dataclass
from typing import Any

from dbus_next.aio import MessageBus


class GenericController(object):

    def __init__(self, device_handler):
        self.device_handler = device_handler

    def on_start(self, context):
        pass

    def on_stop(self, context):
        pass

    def fill_capabilities(self, context, capabilities):
        pass

    def handle_set_device_state(self, state, callback):
        raise NotImplementedError

    def handle_instruction(self, state, callback):
        raise NotImplementedError


@dataclass
class Context:
    dbus: MessageBus
    loop: Any
