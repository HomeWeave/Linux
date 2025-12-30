import os

from anton.power_pb2 import PowerState, PowerInstruction
from anton.state_pb2 import DeviceState

from .interfaces import GenericController


class DevicePowerController(GenericController):

    def on_start(self, context):
        state = DeviceState()
        state.power_state = PowerState.POWER_STATE_ON
        self.device_handler.send_device_state_updated(state)

    def fill_capabilities(self, context, capabilities):
        capabilities.power_state.supported_power_states[:] = [
            PowerState.POWER_STATE_OFF, PowerState.POWER_STATE_ON
        ]
        capabilities.power_state.supported_custom_power_instructions[:] = [
            PowerInstruction.SCREEN_OFF, PowerInstruction.SLEEP
        ]

    def handle_set_device_state(self, state, callback):
        if state.power_state == PowerState.POWER_STATE_OFF:
            print("TODO: Shutdown")

    def handle_instruction(self, instruction, callback):
        if instruction.custom_power_instruction == PowerInstruction.SCREEN_OFF:
            print("TODO: Screen off")
