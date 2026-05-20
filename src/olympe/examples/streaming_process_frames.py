import csv
import numpy as np
import os
import queue
import tempfile
import threading
import time

from PIL import Image
from typing import Any, Dict

import olympe

from olympe.video.frame import VideoFrame
from olympe.video.pdraw import h264_coded_data_format, video_frame_type

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

DRONE_IP = os.environ.get("DRONE_IP", "192.168.43.1")
DRONE_RTSP_PORT = os.environ.get("DRONE_RTSP_PORT")


class Stream_WithFrameProcessing:
    def __init__(self):
        self.drone = olympe.Drone(DRONE_IP)

        # h264 stats file
        self.tempd = tempfile.mkdtemp(prefix="olympe_single_stream_test_")
        self.h264_frame_stats = []
        self.h264_stats_file = open(os.path.join(self.tempd, "h264_stats.csv"), "w+")
        self.h264_stats_writer = csv.DictWriter(
            self.h264_stats_file, ["fps", "bitrate"]
        )
        self.h264_stats_writer.writeheader()

        # Queue to store frames that are being processed
        self.frame_queue = queue.Queue()

        # Frame processing has to be done in a different thread
        self.processing_thread = threading.Thread(target=self.yuv_frame_processing)

    def start(self):
        assert self.drone.connect(retry=3)

        if DRONE_RTSP_PORT is not None:
            self.drone.streaming.server_addr = f"{DRONE_IP}:{DRONE_RTSP_PORT}"

        self.drone.streaming.play(
            media_name="Front camera",
            data_formats=[h264_coded_data_format.bytestream],
            raw_cb=self.yuv_frame_cb,
            coded_bytestream_cb=self.h264_frame_cb,
            flush_raw_cb=self.flush_cb,
            flush_coded_bytestream_cb=self.flush_cb,
            drain_raw_cb=self.drain_cb,
            drain_coded_bytestream_cb=self.drain_cb,
            start_raw_cb=self.start_cb,
            start_coded_bytestream_cb=self.start_cb,
            end_raw_cb=self.end_cb,
            end_coded_bytestream_cb=self.end_cb,
            has_renderer=True,
        )

        # Threading
        self.running = True
        self.processing_thread.start()

    def stop(self):
        # Threading
        self.running = False
        self.processing_thread.join()

        assert self.drone.streaming.stop()
        assert self.drone.disconnect()
        self.h264_stats_file.close()

    def yuv_frame_cb(self, yuv_frame):
        # This function will be called by Olympe for each decoded YUV frame.
        # yuv_frame are olympe.VideoFrame type
        # 'Lock' the frame by reference it
        yuv_frame.ref()
        # Put it in the frame queue, so that it is buffered until it is processed. This
        # way, the frame is guaranteed to still exist or not be modified meanwhile
        self.frame_queue.put_nowait(yuv_frame)

    def yuv_frame_processing(self):
        # Frame processing cannot be done in the main thread, because it is heavy operation.
        # Otherwise, it would block the main process too often and for too long. Without
        # communication with the drone for a certain amount of time, the drone would
        # consider Olympe disconnected and we would be kicked off
        while self.running and threading.main_thread().is_alive():
            try:
                # Remove the frame from the buffer, for it to be processed
                yuv_frame = self.frame_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            # ...
            # Frames should be processed here and released (unref) when you're done.
            # Don't hold a reference on your frames for too long to avoid memory leaks
            # and/or memory pool exhaustion.
            yuv_frame.unref()

    def flush_cb(self, stream: Dict[str, Any]):
        if stream["vdef_format"] != olympe.VDEF_I420:
            return True
        # When too many frames are being buffered, and there is no new frame to handle
        # in the meantime, the whole buffer is freed, so that there are these frames to
        # handle again
        while not self.frame_queue.empty():
            self.frame_queue.get_nowait().unref()
        return True

    def drain_cb(self, stream: Dict[str, Any]):
        if stream["vdef_format"] != olympe.VDEF_I420:
            return True
        # When too many frames are being buffered, and there is no new frame to handle
        # in the meantime, the whole buffer is freed, so that there are these frames to
        # handle again
        while not self.frame_queue.empty():
            self.frame_queue.get_nowait().unref()
        return True

    def start_cb(self):
        pass

    def end_cb(self):
        pass

    def h264_frame_cb(self, h264_frame):
        # This function will be called by Olympe for each new h264 frame.
        # yuv_frame are olympe.VideoFrame type

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

    # Unused function, to help with some basic commands relative to frames
    def get_yuv_frame_info(self, window_name, yuv_frame):
        # The VideoFrame.info() dictionary contains useful information such as the
        # video resolution
        info = yuv_frame.info()

        height, width = (  # noqa
            info["raw"]["frame"]["info"]["height"],
            info["raw"]["frame"]["info"]["width"],
        )

        # The VideoFrame.vmeta() dictionary contains additional metadata from the drone
        # (GPS coordinates, battery percentage, ...)
        metadata = yuv_frame.vmeta()  # noqa

        # To convert pdraw YUV flag to OpenCV YUV flag
        import cv2

        cv2_cvt_color_flag = {  # noqa
            olympe.VDEF_I420: cv2.COLOR_YUV2BGR_I420,
            olympe.VDEF_NV12: cv2.COLOR_YUV2BGR_NV12,
        }[yuv_frame.format()]

    # Unused function, to save a frame as human-visible
    def save_yuv_ndarray_as_rgb(self, path: str, yuv_frame: VideoFrame):
        """Save a frame as an image, human-visible

        :param path: path to save the image to
        :type path: str
        :param yuv_frame: frame to convert
        :type yuv_frame: Frame
        :raises ValueError: if the frame array or info is not retrievable
        :raise NotImplementedError: if the frame type is not raw (but H264, that needs a decoder prior to yuv to rgb conversion)
        """
        if yuv_frame.frame_type() == video_frame_type.raw:
            yuv_array = yuv_frame.as_ndarray()
            frame_info = yuv_frame.info()
            if yuv_array is None or frame_info is None:
                raise ValueError(
                    "YUV array of the video frame cannot be retrieved. Image save is not possible"
                )

            height = frame_info["raw"]["frame"]["info"]["height"]
            width = frame_info["raw"]["frame"]["info"]["width"]
            y_size = width * height
            # In YUV420p format, 1 Y pixel is worth 2x2 pixels of U/V
            uv_size = (width // 2) * (height // 2)

            # Clean copy to have a contiguous buffer in memory
            yuv = np.frombuffer(yuv_array.tobytes(), dtype=np.uint8)

            # Process Y values and reform a 2D image format
            y = yuv[:y_size].reshape((height, width))
            # U and V reshaping and upscaling
            # Process U values, that are sub-sampled, to match them to Y format by multiplying them by 2x2 (2 along row, 2 along column)
            u = (
                yuv[y_size : y_size + uv_size]
                .reshape((height // 2, width // 2))
                .repeat(2, 0)  # double rows
                .repeat(2, 1)  # double columns
            )
            # Process V values as well
            v = (
                yuv[y_size + uv_size :]
                .reshape((height // 2, width // 2))
                .repeat(2, 0)  # double rows
                .repeat(2, 1)  # double columns
            )

            # The Y, U and V 2D matrices are assembled into a 3D matrice. The image is recomposed
            ycbcr = np.stack([y, u, v], axis=2).astype("uint8")

            # Convert to RGB, for human readiness
            img = Image.fromarray(ycbcr, mode="YCbCr").convert("RGB")
            # Save image
            img.save(path)

        elif yuv_frame.frame_type() == video_frame_type.coded:
            # To retrieve an image from H264 format, a decoder is needed
            raise NotImplementedError(
                "H264 frames need a decoder and cannot be saved as image as is"
            )

    # Unused function, to replay the recorded stream with VLC if needed
    def replay_with_vlc(self):
        import os
        import shlex
        import subprocess

        mp4_filepath = os.path.join(self.tempd, "streaming.mp4")
        try:
            subprocess.run(
                shlex.split(f"vlc --play-and-exit {mp4_filepath}"), check=True
            )
        except subprocess.CalledProcessError:
            raise RuntimeError("Fail to play VLC")


def test_streaming_with_frame_processing():
    # Instantiate the video stream with frame processing
    single_stream = Stream_WithFrameProcessing()
    # Start the video stream
    single_stream.start()
    # Wait
    time.sleep(10)
    # Stop the video stream
    single_stream.stop()


if __name__ == "__main__":
    test_streaming_with_frame_processing()
