import threading

from logging import getLogger

import olympe
import olympe.log

from olympe.messages import esim

logger = getLogger(__name__)
olympe.log.update_config(
    {"loggers": {__name__: {"handlers": ["console"], "level": "INFO"}}}
)

DRONE_IP = "192.168.43.1"
ACTIVATION_CODE_1 = "LPA:1$rsp.truphone.com$QRF-BETTERROAMING-PMRDGIR2EARDEIT5"
ACTIVATION_CODE_2 = "LPA:1$rsp.truphone.com$QR-G-5C-KR-1PCDWP9"
OTHER_OPERATION_TIMEOUT=30
# The download timeout is larger because the download operation requests many HTTP back-and-forths
DOWNLOAD_TIMEOUT=120


def wait_for_result(drone:olympe.Drone, operation_type:str, timeout:int):
    """
    Block until an esim.Event.State arrives with `operation_type` in
    profile_operation_status, then return (error_code, op_status).
    Returns None on timeout.
    """
    # Simpler Event than the one Olympe overrides
    event = threading.Event()
    result = {}

    def _on_state(message_event, _):
        if "profile_operation_status" not in message_event.args:
            return
        operation_status = message_event.args["profile_operation_status"]
        if operation_type not in operation_status:
            # Consider only the Event State we are looking for
            return
        result["state"]=message_event.args
        event.set()

    listener = drone.subscribe(_on_state, esim.Event.State(_policy="wait"))
    try:
        if not event.wait(timeout=timeout):
            return None
        return result["state"]
    finally:
        listener.unsubscribe()

def list_profiles(drone:olympe.Drone):
    assert drone.esim.list_profiles(), "Failed to send GetState to the drone"
    assert drone(esim.Event.State(_policy="wait")).wait(_timeout=30).success(), "Timed out waiting for eSIM state message"

    state = drone.get_state(esim.Event.State)
    profiles=state["profile_list"]["profiles"]
    return profiles

if __name__ == "__main__":
    with  olympe.Drone(DRONE_IP) as drone:
        assert drone.connect(), "Connection failed"

        ### list profiles ###
        ###
        profiles = list_profiles(drone)
        assert len(profiles)==0

        ### download a profile ###
        ###
        assert drone.esim.download_profile(activation_code=ACTIVATION_CODE_1), f"Failed to download profile '{ACTIVATION_CODE_1}'"

        result = wait_for_result(drone, "download_profile_status", timeout=DOWNLOAD_TIMEOUT)
        assert result is not None, "Timed out waiting for profile download"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to download profile: error_code({result['profile_operation_status']['error_code']})"
        downloaded_iccid = result["profile_operation_status"]["download_profile_status"]["profile"]["iccid"]
        logger.info(f"Profile '{downloaded_iccid}' has successfully been downloaded")

        ### check profiles
        ###
        profiles = list_profiles(drone)
        assert len(profiles)==1
        assert any(p["iccid"]==downloaded_iccid for p in profiles), f"Failed to find profile '{downloaded_iccid}' in profiles list after download"

        ### disable the profile
        ###
        assert drone.esim.enable_profile(iccid=downloaded_iccid, enable=False), f"Failed to disable profile '{downloaded_iccid}'"

        ### check profile has been disabled
        ###
        result = wait_for_result(drone, "enable_profile_status", timeout=OTHER_OPERATION_TIMEOUT)
        assert result is not None, "Timed out waiting for profile disable"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to disable profile: error_code({result['profile_operation_status']['error_code']})"
        assert result["profile_operation_status"]["enable_profile_status"]["iccid"] ==  downloaded_iccid
        assert result["profile_operation_status"]["enable_profile_status"]["enabled"] is False, f"Profile '{downloaded_iccid}' should be disabled"
        logger.info(f"Profile '{downloaded_iccid}' has successfully been disabled")

        ### re-enable the profile
        ###
        assert drone.esim.enable_profile(iccid=downloaded_iccid, enable=True), f"Failed to re-enable profile '{downloaded_iccid}'"

        ### check profile has been re-enabled
        ###
        result = wait_for_result(drone, "enable_profile_status", timeout=OTHER_OPERATION_TIMEOUT)
        assert result is not None, "Timed out waiting for profile re-enable"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to re-enable profile: error_code({result['profile_operation_status']['error_code']})"
        assert result["profile_operation_status"]["enable_profile_status"]["iccid"] ==  downloaded_iccid
        assert result["profile_operation_status"]["enable_profile_status"]["enabled"] is True, f"Profile '{downloaded_iccid}' should be enabled"
        logger.info(f"Profile '{downloaded_iccid}' has successfully been re-enabled")

        ### download another profile
        ###
        assert drone.esim.download_profile(activation_code=ACTIVATION_CODE_2), f"Failed to download profile '{ACTIVATION_CODE_2}'"

        result = wait_for_result(drone, "download_profile_status", timeout=DOWNLOAD_TIMEOUT)
        assert result is not None, "Timed out waiting for profile download"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to download profile: error_code({result['profile_operation_status']['error_code']})"
        downloaded_iccid_2 = result["profile_operation_status"]["download_profile_status"]["profile"]["iccid"]
        logger.info(f"Profile '{downloaded_iccid_2}' has successfully been downloaded")

        ### check profiles
        ###
        profiles = list_profiles(drone)
        assert len(profiles)==2
        assert any(p["iccid"]==downloaded_iccid_2 for p in profiles), f"Failed to find profile '{downloaded_iccid_2}' in profiles list after download"

        ### delete first profile
        ###

        ###### disable first. Cannot delete if enabled
        ###
        assert drone.esim.enable_profile(iccid=downloaded_iccid, enable=False), f"Failed to disable profile '{downloaded_iccid}'"

        ###### check profile has been disabled
        ###
        result = wait_for_result(drone, "enable_profile_status", timeout=OTHER_OPERATION_TIMEOUT)
        assert result is not None, "Timed out waiting for profile disable"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to disable profile: error_code({result['profile_operation_status']['error_code']})"
        assert result["profile_operation_status"]["enable_profile_status"]["iccid"] ==  downloaded_iccid
        assert result["profile_operation_status"]["enable_profile_status"]["enabled"] is False, f"Profile '{downloaded_iccid}' should be disabled"
        logger.info(f"Profile '{downloaded_iccid}' has successfully been disabled")

        assert drone.esim.delete_profile(iccid=downloaded_iccid), f"Failed to delete profile '{downloaded_iccid}'"

        result = wait_for_result(drone,"delete_profile_status", timeout=OTHER_OPERATION_TIMEOUT)
        assert result is not None, "Timed out waiting for profile deletion"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to delete profile: error_code({result['profile_operation_status']['error_code']})"
        assert result["profile_operation_status"]["delete_profile_status"]["iccid"] ==  downloaded_iccid
        logger.info(f"Profile '{downloaded_iccid}' has successfully been deleted")

        ### check profiles
        ###
        profiles = list_profiles(drone)
        assert len(profiles)==1
        assert not any(p["iccid"]==downloaded_iccid for p in profiles), f"Failed to delete profile '{downloaded_iccid}' from profiles list"

        ### delete second profile - to clean drone
        ###

        ######### disable first. Cannot delete if enabled
        ###
        assert drone.esim.enable_profile(iccid=downloaded_iccid_2, enable=False), f"Failed to disable profile '{downloaded_iccid}'"

        ###### check profile has been disabled
        ###
        result = wait_for_result(drone, "enable_profile_status", timeout=OTHER_OPERATION_TIMEOUT)
        assert result is not None, "Timed out waiting for profile disable"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to disable profile: error_code({result['profile_operation_status']['error_code']})"
        assert result["profile_operation_status"]["enable_profile_status"]["iccid"] ==  downloaded_iccid_2
        assert result["profile_operation_status"]["enable_profile_status"]["enabled"] is False, f"Profile '{downloaded_iccid_2}' should be disabled"
        logger.info(f"Profile '{downloaded_iccid_2}' has successfully been disabled")

        assert drone.esim.delete_profile(iccid=downloaded_iccid_2), f"Failed to delete profile '{downloaded_iccid_2}'"

        result = wait_for_result(drone,"delete_profile_status", timeout=OTHER_OPERATION_TIMEOUT)
        assert result is not None, "Timed out waiting for profile deletion"
        assert result["profile_operation_status"]["error_code"] == 0, f"Failed to delete profile: error_code({result['profile_operation_status']['error_code']})"
        assert result["profile_operation_status"]["delete_profile_status"]["iccid"] ==  downloaded_iccid_2
        logger.info(f"Profile '{downloaded_iccid_2}' has successfully been deleted")

        ### check profiles
        ###
        profiles = list_profiles(drone)
        assert len(profiles)==0
        assert not any(p["iccid"]==downloaded_iccid_2 for p in profiles), f"Failed to delete profile '{downloaded_iccid_2}' from profiles list"

        logger.info(" ### All eSIM operations have been checked ### ")