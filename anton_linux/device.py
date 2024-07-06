import os

from anton.power_pb2 import POWER_OFF, SCREEN_OFF, SLEEP

from .interfaces import GenericController


class DevicePowerController(GenericController):

    def fill_capabilities(self, context, capabilities):
        capabilities.power_state.supported_power_instructions[:] = [
            POWER_OFF, SCREEN_OFF, SLEEP
        ]

    def get_handlers(self):
        return {"power_instruction": self.handle_power_instruction}

    def handle_power_instruction(self, instruction):
        if instruction.power_instruction == SCREEN_OFF:
            # For X only.
            os.system("xset dpms force off")
