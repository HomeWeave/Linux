import socket

from anton.events_pb2 import GenericEvent
from anton.device_pb2 import DEVICE_STATUS_ONLINE, DEVICE_KIND_COMPUTER
from anton.power_pb2 import POWER_OFF, SCREEN_OFF, SLEEP

from .interfaces import GenericController


class DevicePowerController(GenericController):
    def on_start(self, context):
        hostname = socket.gethostname()

        event = GenericEvent()
        event.device.friendly_name = hostname
        event.device.device_kind = DEVICE_KIND_COMPUTER
        event.device.device_status = DEVICE_STATUS_ONLINE

        self.send_event(event)

    def fill_capabilities(self, context, capabilities):
        capabilities.power_state.supported_power_states[:] = [
                POWER_OFF, SCREEN_OFF, SLEEP]


    def get_instruction_handlers(self):
        return {}
