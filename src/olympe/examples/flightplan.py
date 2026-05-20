from pathlib import Path

import olympe

from olympe.enums.flight_plan import mavlink_type
from olympe.messages.common.MavlinkState import (
    MavlinkFilePlayingStateChanged,
    MissionItemExecuted,
)
from olympe.messages.flight_plan import start_at_v2


DRONE_IP = "10.202.0.1"

if __name__ == "__main__":
    drone = olympe.Drone(DRONE_IP)
    drone.connect()
    # Upload first flightplan
    flightplan_id = drone.flightplan.upload(
        Path("flightplan_data_test_1.mavlink").read_bytes()
    )
    # Launch flightplan
    expectation = drone(
        start_at_v2(
            flightplan_id,
            custom_id="",
            type=mavlink_type.flightPlanV2,
            item=0,
            continue_on_disconnect=0,
        )
        >> MissionItemExecuted(idx=0.0)
        >> MissionItemExecuted(idx=1.0)
        >> MissionItemExecuted(idx=2.0)
        >> MissionItemExecuted(idx=3.0)
        >> MavlinkFilePlayingStateChanged(state="stopped")
    ).wait(_timeout=200)
    assert expectation

    # Check there is only one flightplan uploaded
    assert len(drone.flightplan.download_all()) == 1

    # Upload second flightplan
    flightplan_id = drone.flightplan.upload(
        Path("flightplan_data_test_2.mavlink").read_bytes()
    )

    # Launch flightplan
    expectation = drone(
        start_at_v2(
            flightplan_id,
            custom_id="",
            type=mavlink_type.flightPlanV2,
            item=0,
            continue_on_disconnect=0,
        )
        >> MissionItemExecuted(idx=0.0)
        >> MissionItemExecuted(idx=1.0)
        >> MissionItemExecuted(idx=2.0)
        >> MissionItemExecuted(idx=3.0)
        >> MavlinkFilePlayingStateChanged(state="stopped")
    ).wait(_timeout=200)
    assert expectation

    # Check there is now two flightplans uploaded
    assert len(drone.flightplan.download_all()) == 2

    drone.disconnect()
