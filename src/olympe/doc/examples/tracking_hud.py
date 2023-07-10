import argparse
import olympe
import os
import re
import sys
import time
from olympe.video.pdraw import Pdraw, PdrawState
from olympe.video.renderer import PdrawRenderer
from olympe.messages.onboard_tracker import start_tracking_engine
from olympe.video import HudType


DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")
DRONE_RTSP_PORT = os.environ.get("DRONE_RTSP_PORT", "554")


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

    drone_ip = re.search(r"\d+\.\d+\.\d+\.\d+", args.url)

    drone = olympe.Drone(drone_ip.group())
    drone.connect()
    drone(start_tracking_engine(box_proposals=True)).wait()

    pdraw = Pdraw()
    pdraw.play(url=args.url, media_name=args.media_name)
    renderer = PdrawRenderer(pdraw=pdraw, hud_type=HudType.TRACKING)
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

    drone.disconnect()
    assert pdraw.wait(PdrawState.Closed, timeout=timeout)
    renderer.stop()
    pdraw.destroy()


def test_pdraw():
    main([])


if __name__ == "__main__":
    main(sys.argv[1:])
