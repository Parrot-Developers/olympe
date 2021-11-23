import argparse
import olympe
import os
import sys
import time
from olympe.video.pdraw import Pdraw, PdrawState
from olympe.video.renderer import PdrawRenderer


DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")
DRONE_RTSP_PORT = os.environ.get("DRONE_RTSP_PORT", "554")


def yuv_frame_cb(yuv_frame):
    """
    This function will be called by Olympe for each decoded YUV frame.

        :type yuv_frame: olympe.VideoFrame
    """
    import cv2

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
    cv2_cvt_color_flag = {
        olympe.VDEF_I420: cv2.COLOR_YUV2BGR_I420,
        olympe.VDEF_NV12: cv2.COLOR_YUV2BGR_NV12,
    }[yuv_frame.format()]

    # yuv_frame.as_ndarray() is a 2D numpy array with the proper "shape"
    # i.e (3 * height / 2, width) because it's a YUV I420 or NV12 frame

    # Use OpenCV to convert the yuv frame to RGB
    cv2frame = cv2.cvtColor(yuv_frame.as_ndarray(), cv2_cvt_color_flag)  # noqa


def main(argv):
    parser = argparse.ArgumentParser(description="Olympe Pdraw Example")
    parser.add_argument(
        "-u",
        "--url",
        default=f"rtsp://{DRONE_IP}:{DRONE_RTSP_PORT}/live",
        help=(
            "Media resource (rtsp:// or file://) URL.\n"
            "See olympe.Pdraw.play documentation"
        ),
    )
    parser.add_argument("-m", "--media-name", default="DefaultVideo")
    args = parser.parse_args(argv)
    pdraw = Pdraw()
    # Uncomment the following line, to test this OpenCV frame processing callback function
    # This function requires `pip3 install opencv-python`.
    # pdraw.set_callbacks(raw_cb=yuv_frame_cb)
    pdraw.play(url=args.url, media_name=args.media_name)
    renderer = PdrawRenderer(pdraw=pdraw)
    assert pdraw.wait(PdrawState.Playing, timeout=5)
    if args.url.endswith("/live"):
        # Let's see the live video streaming for 10 seconds
        time.sleep(10)
        pdraw.close()
        timeout = 5
    else:
        # When replaying a video, the pdraw stream will be closed automatically
        # at the end of the video
        # For this is example, this is the replayed video maximal duration:
        timeout = 90
    assert pdraw.wait(PdrawState.Closed, timeout=timeout)
    renderer.stop()
    pdraw.dispose()


def test_pdraw():
    main([])


if __name__ == "__main__":
    main(sys.argv[1:])
