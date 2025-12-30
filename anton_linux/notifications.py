from anton.state_pb2 import DeviceState

from .interfaces import GenericController


class NotificationsController(GenericController):

    def fill_capabilities(self, context, capabilities):
        capabilities.notifications.simple_text_notification_supported = True
        capabilities.notifications.media_notification_supported = True

    def handle_instruction(self, instruction, callback):
        if instruction.HasField('notification_action_instruction'):
            os.system('notify-send "' +
                      instruction.notification.standard_notification.text +
                      '"')
