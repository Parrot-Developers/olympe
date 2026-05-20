import logging
import olympe
import os
import time

olympe.log.update_config({"loggers": {"olympe": {"level": "INFO"}}})

DRONE_IP = os.environ.get("DRONE_IP", "10.202.0.1")


logger = logging.getLogger("olympe.streaming_seeking")


class Stream_WithSeeking:
    def __init__(self):
        self.drone = olympe.Drone(DRONE_IP)

    def start(self):
        assert self.drone.connect(retry=3)

        self.drone.streaming.play(
            url="file://streaming_seeking.MP4",
            resource_name="replay/streaming_seeking.MP4",
            has_renderer=True,
        )

    def restart(self):
        assert self.drone.streaming.play()

    def pause(self):
        assert self.drone.streaming.pause()

    def get_replay_duration(self):
        duration = self.drone.streaming.get_replay_duration()
        logger.info(f"--- DURATION: {duration * 10 ** (-6)}s")
        return duration  # in microseconds

    def get_replay_current_time(self):
        timestamp = self.drone.streaming.get_replay_current_time()
        logger.info(f"--- CURRENT TIME: {timestamp * 10 ** (-6)}s")
        return timestamp  # in microseconds

    def seek_next_frame(self):
        res, play_data = self.drone.streaming.seek_next_frame()
        if play_data:
            logger.info(
                f"--- NEW TIMESTAMP: {play_data.timestamp*10**(-6)}s, SPEED: {play_data.speed}"
            )
        return res

    def seek_previous_frame(self):
        res, play_data = self.drone.streaming.seek_previous_frame()
        if play_data:
            logger.info(
                f"--- NEW TIMESTAMP: {play_data.timestamp*10**(-6)}s, SPEED: {play_data.speed}"
            )
        return res

    def seek_from(self, delta):
        self.get_replay_current_time()
        res, play_data = self.drone.streaming.seek_from(delta=delta)
        if play_data:
            logger.info(
                f"--- NEW TIMESTAMP: {play_data.timestamp*10**(-6)}s, SPEED: {play_data.speed}"
            )
        self.get_replay_current_time()
        return res

    def seek_to(self, timestamp):
        self.get_replay_current_time()
        res, play_data = self.drone.streaming.seek_to(timestamp=timestamp)
        if play_data:
            logger.info(
                f"--- NEW TIMESTAMP: {play_data.timestamp*10**(-6)}s, SPEED: {play_data.speed}"
            )
        self.get_replay_current_time()
        return res

    def stop(self):
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()


class Stream_WithSpeed:
    def __init__(self):
        self.drone = olympe.Drone(DRONE_IP)

    def start(self):
        assert self.drone.connect(retry=3)

        self.drone.streaming.play(
            url="file://streaming_seeking.MP4",
            resource_name="replay/streaming_seeking.MP4",
            has_renderer=True,
        )

    def restart_with_speed(self, speed: float):
        assert self.drone.streaming.play(speed=speed)

    def pause(self):
        assert self.drone.streaming.pause()

    def stop(self):
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()


class Stream_WithChapterList:
    def __init__(self):
        self.drone = olympe.Drone(DRONE_IP)

    def start(self):
        assert self.drone.connect(retry=3)

        self.drone.streaming.play(
            url="file://streaming_seeking.MP4",
            resource_name="replay/streaming_seeking.MP4",
            has_renderer=True,
        )

    def get_chapter_list(self):
        chapter_list = self.drone.streaming.get_chapter_list()
        return chapter_list

    def stop(self):
        assert self.drone.streaming.stop()
        assert self.drone.disconnect()


def test_streaming_with_seeking():
    # Instantiate the video stream and the drone objects
    seek_stream = Stream_WithSeeking()
    # Start the video stream
    seek_stream.start()

    # Get and print the replayed video duration
    duration = seek_stream.get_replay_duration()
    assert duration == 16.55 * 10**6

    # Check seek_next or previous frame does not work when the stream is not paused
    assert not seek_stream.seek_next_frame()
    assert not seek_stream.seek_previous_frame()

    time.sleep(5)  # Video stats: 150 frames in 5sec => 30 frames/s
    # Pause to seek frame by frame
    seek_stream.pause()
    ## Check that the current frame is 149/150

    time.sleep(2)
    # Seek next frame
    assert seek_stream.seek_next_frame()
    time.sleep(2)
    assert seek_stream.seek_next_frame()
    time.sleep(2)
    # Seek previous frame
    assert seek_stream.seek_previous_frame()
    time.sleep(2)
    assert seek_stream.seek_next_frame()
    time.sleep(2)
    ## Check that the current frame is 151/152

    # Check seek_from does not work when the stream is paused
    assert not seek_stream.seek_from(delta=1000000)
    # Restart video stream to seek from a certain timelapse
    seek_stream.restart()
    assert seek_stream.seek_from(delta=1000000)  # 1sec => 30 frames later
    seek_stream.pause()
    ## Check that the current frame is 181/182
    time.sleep(2)
    # Restart video stream to seek backward from a certain timelapse
    seek_stream.restart()
    assert seek_stream.seek_from(delta=-500000)  # 0.5sec => 15 frames previous
    seek_stream.pause()
    ## Check that the current frame is 167/168

    time.sleep(2)
    # Check seek_to does not work when the stream is paused
    assert not seek_stream.seek_to(timestamp=duration - 1 * 10**6)
    # Restart video stream to seek to a specific frame, meaning of time
    seek_stream.restart()
    assert seek_stream.seek_to(timestamp=duration - 1 * 10**6)  # 1sec before the end
    seek_stream.pause()
    # Check that the current frame is 465/466 (total of 494 frames - 30frames for the 1sec)
    time.sleep(2)
    # Restart video stream to seek to previous frame
    seek_stream.restart()
    assert seek_stream.seek_to(timestamp=1 * 10**6)  # 1sec after the beginning
    seek_stream.pause()
    # Check that the current frame is 28/29

    time.sleep(5)
    # Stop the video stream
    seek_stream.stop()


def test_streaming_with_speed():
    # Instantiate the video stream and the drone objects
    speed_stream = Stream_WithSpeed()
    # Start the video stream
    speed_stream.start()
    speed_stream.pause()

    # Speed up
    speed_stream.restart_with_speed(speed=2)
    time.sleep(2)
    speed_stream.pause()
    # Check that the current frame is 120/121
    time.sleep(2)

    # Speed down
    speed_stream.restart_with_speed(speed=0.5)
    time.sleep(2)
    speed_stream.pause()
    # Check that the current frame is 121*1.25, ie 151/152
    time.sleep(2)

    # Reverse
    speed_stream.restart_with_speed(speed=-1)
    time.sleep(2)
    speed_stream.pause()
    # Check that the current frame is 152-121/2, ie 91
    time.sleep(2)

    # Stop the video stream
    speed_stream.stop()


def test_streaming_with_chapterlist():
    # Instantiate the video stream and the drone objects
    chapterlist_stream = Stream_WithChapterList()
    # Start the video stream
    chapterlist_stream.start()

    # Get chapter list
    assert chapterlist_stream.get_chapter_list() is not None

    # Stop the video stream
    chapterlist_stream.stop()


if __name__ == "__main__":
    test_streaming_with_seeking()
    test_streaming_with_speed()
    test_streaming_with_chapterlist()
