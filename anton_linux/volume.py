import threading
import time
from typing import Optional

import pulsectl

from anton.media_pb2 import VolumeControl
from anton.state_pb2 import DeviceState
from pyantonlib.utils import log_info, log_warn

from .interfaces import GenericController


def get_default_sink(
        pulse: pulsectl.Pulse) -> Optional[pulsectl.PulseSinkInfo]:
    """Retrieve the default output sink from PulseAudio/PipeWire."""
    try:
        server_info = pulse.server_info()
        sink_name = getattr(server_info, "default_sink_name", None)
        if sink_name:
            try:
                return pulse.get_sink_by_name(sink_name)
            except Exception:
                pass
    except Exception:
        pass

    try:
        sinks = pulse.sink_list()
        return sinks[0] if sinks else None
    except Exception:
        return None


class VolumeController(GenericController):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.context = None
        self.current_volume: Optional[int] = None
        self._stop_event = threading.Event()
        self._listener_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def on_start(self, context):
        self.context = context
        self._stop_event.clear()

        # Start background listener thread for volume/sink events
        self._listener_thread = threading.Thread(target=self._listen_volume,
                                                 name="VolumeListenerThread",
                                                 daemon=True)
        self._listener_thread.start()

    def on_stop(self, context):
        self._stop_event.set()
        if self._listener_thread and self._listener_thread.is_alive():
            self._listener_thread.join(timeout=2.0)

    def fill_capabilities(self, context, capabilities):
        capabilities.media.supports_volume_set = True
        capabilities.media.volume_controls[:] = [
            VolumeControl.VOLUME_UP,
            VolumeControl.VOLUME_DOWN,
            VolumeControl.VOLUME_MUTE,
        ]

    def handle_set_device_state(self, state, callback):
        vol = getattr(state, "volume_state", None)
        has_field = False
        try:
            has_field = state.HasField("volume_state")
        except ValueError:
            has_field = vol is not None and vol >= 0

        if has_field and vol is not None:
            self.set_volume(vol)

        if callback:
            callback(None)

    def handle_instruction(self, instruction, callback):
        vol_instruction = getattr(instruction, "volume_instruction",
                                  VolumeControl.VOLUME_CONTROL_UNKNOWN)
        if vol_instruction != VolumeControl.VOLUME_CONTROL_UNKNOWN:
            self.change_volume_discrete(vol_instruction)
        else:
            try:
                has_vol = instruction.HasField("volume_state")
            except ValueError:
                has_vol = False
            if has_vol or (hasattr(instruction, "volume_state")
                           and instruction.volume_state > 0):
                self.set_volume(instruction.volume_state)

        if callback:
            callback(None)

    def set_volume(self, level: int):
        level = max(0, min(100, int(level)))
        with self._lock:
            try:
                with pulsectl.Pulse("anton-volume-control") as pulse:
                    sink = get_default_sink(pulse)
                    if sink:
                        pulse.volume_set_all_chans(sink, level / 100.0)
                        self._check_and_report_volume(pulse)
                    else:
                        log_warn("No default sink found to set volume.")
            except Exception as e:
                log_warn(f"Failed to set volume to {level}% via pulsectl: {e}")

    def change_volume_discrete(self, control: int):
        with self._lock:
            try:
                with pulsectl.Pulse("anton-volume-control") as pulse:
                    sink = get_default_sink(pulse)
                    if not sink:
                        log_warn(
                            "No default sink found for discrete volume change."
                        )
                        return

                    if control == VolumeControl.VOLUME_UP:
                        pulse.volume_change_all_chans(sink, 0.05)
                    elif control == VolumeControl.VOLUME_DOWN:
                        pulse.volume_change_all_chans(sink, -0.05)
                    elif control == VolumeControl.VOLUME_MUTE:
                        new_mute = not sink.mute
                        pulse.sink_mute(sink.index, mute=new_mute)

                    self._check_and_report_volume(pulse)
            except Exception as e:
                log_warn(
                    f"Failed to execute volume instruction {control} via pulsectl: {e}"
                )

    def _listen_volume(self):
        while not self._stop_event.is_set():
            try:
                with pulsectl.Pulse("anton-volume-listener") as pulse:
                    log_info("Connected to PulseAudio for event listening.")

                    # Flag to signal that a relevant event was received.
                    # PulseLoopStop raised in the callback causes event_listen()
                    # to return normally — it does NOT re-raise the exception.
                    event_fired = threading.Event()

                    def on_event(ev):
                        fac = str(getattr(ev, "facility", "")).lower()
                        if "sink" in fac or "server" in fac:
                            event_fired.set()
                            raise pulsectl.PulseLoopStop()

                    pulse.event_mask_set("all")
                    pulse.event_callback_set(on_event)

                    # Initial volume query and report on connection
                    with self._lock:
                        self._check_and_report_volume()

                    while not self._stop_event.is_set():
                        event_fired.clear()
                        pulse.event_listen(timeout=1.0)

                        if event_fired.is_set():
                            with self._lock:
                                self._check_and_report_volume()
            except Exception as e:
                log_warn(f"Error in pulsectl event listener: {e}")
                if not self._stop_event.is_set():
                    time.sleep(1)

    def _check_and_report_volume(self, pulse: Optional[pulsectl.Pulse] = None):
        try:
            with pulsectl.Pulse("anton-volume-check") as p:
                sink = get_default_sink(p)
                if sink and sink.volume:
                    vol = int(round(sink.volume.value_flat * 100))
                    self._on_volume_update(vol)
        except Exception as e:
            log_warn(f"Failed to query current volume: {e}")

    def _on_volume_update(self, volume_level: int):
        volume_level = max(0, min(100, int(volume_level)))
        if self.current_volume != volume_level:
            self.current_volume = volume_level
            self._report_volume(volume_level)

    def _report_volume(self, volume_level: int):
        volume_level = max(0, min(100, int(volume_level)))
        state = DeviceState()
        try:
            state.volume_state = volume_level
        except Exception as e:
            log_warn(f"Failed to set state.volume_state: {e}")
        if hasattr(state, "set_volume_state") and callable(
                getattr(state, "set_volume_state")):
            try:
                state.set_volume_state(volume_level)
            except Exception as e:
                log_warn(f"Failed to call state.set_volume_state: {e}")
        self.device_handler.send_device_state_updated(state)
