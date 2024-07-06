from dataclasses import dataclass
from typing import Any

from dbus_next.aio import MessageBus


class GenericController(object):
    def __init__(self, channel):
        self.channel = channel

    def on_start(self, context):
        pass

    def on_stop(self, context):
        pass

    def fill_capabilities(self, context, capabilities):
        pass

    def handle_instruction(self, context, instruction, responder):
        raise NotImplementedError

    def get_handlers(self):
        raise NotImplementedError


@dataclass
class Context:
    dbus: MessageBus
    loop: Any
