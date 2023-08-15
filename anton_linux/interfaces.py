from dataclasses import dataclass
from typing import Any

from dbus_next.aio import MessageBus


class GenericController(object):
    def __init__(self, event_sender):
        self.event_sender = event_sender

    def on_start(self, context):
        pass

    def on_stop(self, context):
        pass

    def fill_capabilities(self, context, capabilities):
        pass

    def handle_instruction(self, context, instruction):
        raise NotImplementedError

    def send_event(self, event):
        self.event_sender(event)

    def get_instruction_handlers(self):
        raise NotImplementedError


@dataclass
class Context:
    dbus: MessageBus
    loop: Any

