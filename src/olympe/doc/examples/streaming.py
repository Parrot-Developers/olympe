#!/usr/bin/env python

# NOTE: Line numbers of this example are referenced in the user guide.
# Don't forget to update the user guide after every modification of this example.

import csv
import math
import os
import queue
import shlex
import subprocess
import tempfile
import threading

import olympe
from olympe.messages.ardrone3.Piloting import TakeOff, Landing
from olympe.messages.ardrone3.Piloting import moveBy
from olympe.messages.ardrone3.PilotingState import FlyingStateChanged
from olympe.messages.ardrone3.PilotingSettings import MaxTilt
from olympe.messages.ardrone3.PilotingSettingsState import MaxTiltChanged
from olympe.messages.ardrone3.GPSSettingsState import GPSFixStateChanged
from olympe.video.renderer import PdrawRenderer


olympe.log.update_config({"loggers": {"olympe": {"level": "WARNING"}}})

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")
DRONE_RTSP_PORT = os.environ.get("DRONE_RTSP_PORT")


class StreamingExample:
    def __init__(self):
        # Create the olympe.Drone object from its IP address
        self.drone = olympe.Drone(DRONE_IP)
        self.tempd = tempfile.mkdtemp(prefix="olympe_streaming_test_")
        print(f"Olympe streaming example output dir: {self.tempd}")
        self.h264_frame_stats = []
        self.h264_stats_file = open(os.path.join(self.tempd, "h264_stats.csv"), "w+")
        self.h264_stats_writer = csv.DictWriter(
            self.h264_stats_file, ["fps", "bitrate"]
        )
        self.h264_stats_writer.writeheader()
        self.frame_queue = queue.Queue()
        self.processing_thread = threading.Thread(target=self.yuv_frame_processing)
        self.renderer = None

    def start(self):
        # Connect to drone
        assert self.drone.connect(retry=3)

        if DRONE_RTSP_PORT is not None:
            self.drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"

        # You can record the video stream from the drone if you plan to do some
        # post processing.
        self.drone.streaming.set_output_files(
            video=os.path.join(self.tempd, "streaming.mp4"),
            metadata=os.path.join(self.tempd, "streaming_metadata.json"),
        )

        # Setup your callback functions to do some live video processing
        self.drone.streaming.set_callbacks(
            raw_cb=self.yuv_frame_cb,
            h264_cb=self.h264_frame_cb,
            start_cb=self.start_cb,
            end_cb=self.end_cb,
            flush_raw_cb=self.flush_cb,
        )
        # Start video streaming
        self.drone.streaming.start()
        self.renderer = PdrawRenderer(pdraw=self.drone.streaming)
        self.running = True
        self.processing_thread.start()

    def stop(self):
        self.running = False
        self.processing_thread.join()
        if self.renderer is not None:
            self.renderer.stop()
        # Properly stop the video stream and disconnect
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()
        self.h264_stats_file.close()

    def yuv_frame_cb(self, yuv_frame):
        """
        This function will be called by Olympe for each decoded YUV frame.

            :type yuv_frame: olympe.VideoFrame
        """
        yuv_frame.ref()
        self.frame_queue.put_nowait(yuv_frame)

    def yuv_frame_processing(self):
        while self.running:
            try:
                yuv_frame = self.frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            # You should process your frames here and release (unref) them when you're done.
            # Don't hold a reference on your frames for too long to avoid memory leaks and/or memory
            # pool exhaustion.
            yuv_frame.unref()

    def flush_cb(self, stream):
        if stream["vdef_format"] != olympe.VDEF_I420:
            return True
        while not self.frame_queue.empty():
            self.frame_queue.get_nowait().unref()
        return True

    def start_cb(self):
        pass

    def end_cb(self):
        pass

    def h264_frame_cb(self, h264_frame):
        """
        This function will be called by Olympe for each new h264 frame.

            :type yuv_frame: olympe.VideoFrame
        """

        # Get a ctypes pointer and size for this h264 frame
        frame_pointer, frame_size = h264_frame.as_ctypes_pointer()

        # For this example we will just compute some basic video stream stats
        # (bitrate and FPS) but we could choose to resend it over an another
        # interface or to decode it with our preferred hardware decoder..

        # Compute some stats and dump them in a csv file
        info = h264_frame.info()
        frame_ts = info["ntp_raw_timestamp"]
        if not bool(info["is_sync"]):
            while len(self.h264_frame_stats) > 0:
                start_ts, _ = self.h264_frame_stats[0]
                if (start_ts + 1e6) < frame_ts:
                    self.h264_frame_stats.pop(0)
                else:
                    break
            self.h264_frame_stats.append((frame_ts, frame_size))
            h264_fps = len(self.h264_frame_stats)
            h264_bitrate = 8 * sum(map(lambda t: t[1], self.h264_frame_stats))
            self.h264_stats_writer.writerow({"fps": h264_fps, "bitrate": h264_bitrate})

    def show_yuv_frame(self, window_name, yuv_frame):
        # the VideoFrame.info() dictionary contains some useful information
        # such as the video resolution
        info = yuv_frame.info()

        height, width = (  # noqa
            info["raw"]["frame"]["info"]["height"],
            info["raw"]["frame"]["info"]["width"],
        )

        # yuv_frame.vmeta() returns a dictionary that contains additional
        # metadata from the drone (GPS coordinates, battery percentage, ...)

        # convert pdraw YUV flag to OpenCV YUV flag
        # import cv2
        # cv2_cvt_color_flag = {
        #     olympe.VDEF_I420: cv2.COLOR_YUV2BGR_I420,
        #     olympe.VDEF_NV12: cv2.COLOR_YUV2BGR_NV12,
        # }[yuv_frame.format()]

    def fly(self):
        # Takeoff, fly, land, ...
        print("Takeoff if necessary...")
        self.drone(
            FlyingStateChanged(state="hovering", _policy="check")
            | FlyingStateChanged(state="flying", _policy="check")
            | (
                GPSFixStateChanged(fixed=1, _timeout=10, _policy="check_wait")
                >> (
                    TakeOff(_no_expect=True)
                    & FlyingStateChanged(
                        state="hovering", _timeout=10, _policy="check_wait"
                    )
                )
            )
        ).wait()
        maxtilt = self.drone.get_state(MaxTiltChanged)["max"]
        self.drone(MaxTilt(maxtilt)).wait()

        for i in range(4):
            print(f"Moving by ({i + 1}/4)...")
            self.drone(moveBy(10, 0, 0, math.pi, _timeout=20)).wait().success()

        print("Landing...")
        self.drone(Landing() >> FlyingStateChanged(state="landed", _timeout=5)).wait()
        print("Landed\n")

    def replay_with_vlc(self):
        # Replay this MP4 video file using VLC
        mp4_filepath = os.path.join(self.tempd, "streaming.mp4")
        subprocess.run(shlex.split(f"vlc --play-and-exit {mp4_filepath}"), check=True)


def test_streaming():
    streaming_example = StreamingExample()
    # Start the video stream
    streaming_example.start()
    # Perform some live video processing while the drone is flying
    streaming_example.fly()
    # Stop the video stream
    streaming_example.stop()
    # Recorded video stream postprocessing
    # streaming_example.replay_with_vlc()


if __name__ == "__main__":
    test_streaming()
