from anton.events_pb2 import GenericEvent

from .interfaces import GenericController


class NotificationsController(GenericController):
    def get_instruction_handlers(self):
        return {"notification": self.handle_instruction}

    def fill_capabilities(self, context, capabilities):
        capabilities.notifications.simple_text_notification_supported = True
        capabilities.notifications.media_notification_supported = True

    def handle_instruction(self, context, instruction):
        subtype = instruction.notification.WhichOneof('notification_type')
        if subtype == 'standard_notification':
            os.system('notify-send "' +
                      instruction.notification.standard_notification.text + '"')



