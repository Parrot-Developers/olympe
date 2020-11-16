# -*- coding: UTF-8 -*-

#  Copyright (C) 2019 Parrot Drones SAS
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions
#  are met:
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in
#    the documentation and/or other materials provided with the
#    distribution.
#  * Neither the name of the Parrot Company nor the names
#    of its contributors may be used to endorse or promote products
#    derived from this software without specific prior written
#    permission.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
#  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
#  PARROT COMPANY BE LIABLE FOR ANY DIRECT, INDIRECT,
#  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
#  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS
#  OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
#  AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
#  OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
#  SUCH DAMAGE.


from __future__ import unicode_literals
from __future__ import print_function
from __future__ import absolute_import
from future.builtins import str, bytes
from future.builtins import int
try:
    # Python 2
    from __builtin__ import str as builtin_str
except ImportError:
    # Python 3
    from builtins import str as builtin_str

import arsdkparser
import ctypes
try:
    # Python 2
    import textwrap3 as textwrap
except ImportError:
    # Python 3
    import textwrap

from aenum import OrderedEnum
from collections import OrderedDict
from itertools import starmap
import logging
import olympe_deps as od
import re
from six import with_metaclass

from olympe.arsdkng.enums import ArsdkEnums, ArsdkEnum, list_flags, ArsdkBitfield
from olympe.arsdkng.expectations import ArsdkEventExpectation
from olympe.arsdkng.expectations import ArsdkCommandExpectation
from olympe.arsdkng.expectations import ArsdkWhenAnyExpectation
from olympe.arsdkng.expectations import ArsdkWhenAllExpectations
from olympe.arsdkng.expectations import ArsdkCheckStateExpectation
from olympe.arsdkng.expectations import ArsdkCheckWaitStateExpectation
from olympe.arsdkng.expectations import ExpectPolicy
from olympe.arsdkng.events import ArsdkMessageEvent

from olympe._private import string_from_arsdkxml, DEFAULT_FLOAT_TOL


ARSDK_CLS_DEFAULT_ID = 0

#########################################################################
#                        MAPPING SDK COMMANDS to OLYMPE COMMANDS        #
#########################################################################

ARSDK_OLYMPE_CMD_MAP = {
    "Ardrone3.Animations.Flip": "animation_flip",
    "Ardrone3.Antiflickering.ElectricFrequency": "set_antiflickering_frequency",
    "Ardrone3.Antiflickering.SetMode": "set_antiflickering_mode",
    "Ardrone3.Camera.OrientationV2": "set_camera_orientation_v2",
    "Ardrone3.Camera.Velocity": "set_camera_velocity",
    "Ardrone3.GPSSettings.HomeType": "set_home_type_location",
    "Ardrone3.GPSSettings.ReturnHomeDelay": "set_return_home_delay",
    "Ardrone3.GPSSettings.ReturnHomeMinAltitude": "set_return_home_altitude",
    "Ardrone3.GPSSettings.SendControllerGPS": "set_controller_gps_location",
    "Ardrone3.MediaRecord.PictureV2": "take_picture_v2",

    # "Ardrone3.MediaRecord.VideoV2": ["stop_video_v2", "start_video_v2"],
    # "Ardrone3.MediaStreaming.VideoEnable": ["disable_video_streaming", "enable_video_streaming"],

    "Ardrone3.MediaStreaming.VideoStreamMode": "set_stream_mode",
    "Ardrone3.Network.WifiAuthChannel": "get_wifi_auth_channels",
    "Ardrone3.Network.WifiScan": "wifi_scan",
    # FIXME: set_wifi_security is mapped to two different commands
    "Ardrone3.NetworkSettings.WifiSecurity": "set_wifi_security",
    "Ardrone3.NetworkSettings.WifiSelection": "set_wifi_settings",
    "Ardrone3.PictureSettings.AutoWhiteBalanceSelection": "set_picture_white_balance",
    "Ardrone3.PictureSettings.ExpositionSelection": "set_picture_exposition",
    "Ardrone3.PictureSettings.PictureFormatSelection": "set_picture_format",
    "Ardrone3.PictureSettings.SaturationSelection": "set_picture_saturation",

    # "Ardrone3.PictureSettings.TimelapseSelection":
    #   ["disable_picture_timelapse","enable_picture_timelapse"],
    # "Ardrone3.PictureSettings.VideoAutorecordSelection":
    #  ["disable_autorecord_video","enable_autorecord_video"],

    "Ardrone3.PictureSettings.VideoFramerate": "set_video_framerate",
    "Ardrone3.PictureSettings.VideoRecordingMode": "set_video_recording_mode",
    "Ardrone3.PictureSettings.VideoResolutions": "set_resolutions_mode",
    "Ardrone3.PictureSettings.VideoStabilizationMode": "set_video_stabilization_mode",

    "Ardrone3.Piloting.CancelMoveTo": "piloting_cancel_move_to",
    "Ardrone3.Piloting.Circle": "piloting_circle",
    "Ardrone3.Piloting.Emergency": "emergency",
    "Ardrone3.Piloting.FlatTrim": "flat_trim",
    "Ardrone3.Piloting.Landing": "landing",
    "Ardrone3.Piloting.MoveBy": "piloting_move_by",
    "Ardrone3.Piloting.MoveTo": "piloting_move_to",

    # "Ardrone3.Piloting.NavigateHome": ["stop_piloting_return_home", "start_piloting_return_home"],

    "Ardrone3.Piloting.StartPilotedPOI": "start_piloted_poi",
    "Ardrone3.Piloting.StopPilotedPOI": "stop_piloted_poi",
    "Ardrone3.Piloting.TakeOff": "take_off",
    "Ardrone3.Piloting.UserTakeOff": "set_user_take_off_state",

    # "Ardrone3.PilotingSettings.BankedTurn": ["disable_banked_turn", "enable_banked_turn" ],

    "Ardrone3.PilotingSettings.CirclingAltitude": "set_circling_altitude",
    "Ardrone3.PilotingSettings.CirclingDirection": "set_default_circling_direction",
    "Ardrone3.PilotingSettings.MaxAltitude": "set_max_altitude",
    "Ardrone3.PilotingSettings.MaxDistance": "set_max_distance",
    "Ardrone3.PilotingSettings.MaxTilt": "set_max_tilt",
    "Ardrone3.PilotingSettings.MinAltitude": "set_min_altitude",
    "Ardrone3.PilotingSettings.NoFlyOverMaxDistance": "set_no_fly_over_max_distance",
    "Ardrone3.PilotingSettings.PitchMode": "set_pitch_mode",
    "Ardrone3.PilotingSettings.SetAutonomousFlightMaxHorizontalAcceleration":
        "set_flightplan_max_horizontal_acceleration",

    "Ardrone3.PilotingSettings.SetAutonomousFlightMaxHorizontalSpeed": "",
        # ["set_max_horizontal_speed","set_flightplan_max_horizontal_speed"],

    "Ardrone3.PilotingSettings.SetAutonomousFlightMaxRotationSpeed":
        "set_flightplan_max_rotation_speed",
    "Ardrone3.PilotingSettings.SetAutonomousFlightMaxVerticalAcceleration":
        "set_flightplan_max_vertical_acceleration",
    "Ardrone3.PilotingSettings.SetAutonomousFlightMaxVerticalSpeed":
        "set_flightplan_max_vertical_speed",

    "Ardrone3.SpeedSettings.HullProtection": "set_hull_protection",
    "Ardrone3.SpeedSettings.MaxPitchRollRotationSpeed": "set_max_pitch_roll_rot_speed",
    "Ardrone3.SpeedSettings.MaxRotationSpeed": "set_max_rotation_speed",
    "Ardrone3.SpeedSettings.MaxVerticalSpeed": "set_max_vertical_speed",
    "Animation.Cancel": "animation_cancel",
    "Animation.Start_candle": "animation_start_candle",
    "Animation.Start_dolly_slide": "animation_start_dolly_slide",
    "Animation.Start_dronie": "animation_start_dronie",
    "Animation.Start_flip": "animation_start_flip",
    "Animation.Start_horizontal_panorama": "animation_start_horizontal_panorama",
    "Animation.Start_horizontal_reveal": "animation_start_horizontal_reveal",
    "Animation.Start_parabola": "animation_start_parabola",
    "Animation.Start_spiral": "animation_start_spiral",
    "Animation.Start_vertical_reveal": "animation_start_vertical_reveal",

    "Camera.Unlock_exposure": "",

    "Common.Accessory.Config": "set_accessory_config",
    "Common.Animations.StartAnimation": "start_animation",
    "Common.Animations.StopAllAnimations": "stop_all_animations",
    "Common.Animations.StopAnimation": "stop_animation",

    # "Common.Calibration.MagnetoCalibration":
    #   ["aborted_calibration","start_calibration"],
    # "Common.Calibration.PitotCalibration":
    #   ["aborted_calibration_pitot", "start_calibration_pitot"],

    "Common.Common.AllStates": "get_all_states",

    "Common.Common.Reboot": "reboot",
    "Common.Controller.IsPiloting": "change_hud_state",


    "Common.FlightPlanSettings.ReturnHomeOnDisconnect": "set_rth_during_flightplan",

    "Common.Mavlink.Pause": "mavlink_pause",
    "Common.Mavlink.Start": "mavlink_start",
    "Common.Mavlink.Stop": "mavlink_stop",
    "Common.Settings.AllSettings": "get_all_settings",
    "Common.Settings.AutoCountry": "set_network_auto_country",
    "Common.Settings.Country": "set_network_country_code",
    "Common.Settings.ProductName": "set_product_name",
    "Common.Settings.Reset": "reset_all_settings",
    "Common.WifiSettings.OutdoorSetting": "set_wifi_settings_outdoor",

    "Follow_me.Configure_geographic": "follow_me_configure_geographic_run",
    "Follow_me.Configure_relative": "follow_me_configure_relative_run",
    "Follow_me.Start": "follow_me_start",
    "Follow_me.Stop": "follow_me_stop",
    "Follow_me.Target_framing_position": "follow_me_target_framing_position",
    "Follow_me.Target_image_detection": "follow_me_target_image_detection",

    "Skyctrl.AccessPointSettings.AccessPointSSID": "controller_set_wifi_ap_ssid",

    "Skyctrl.AccessPointSettings.WifiSelection": "controller_set_wifi_ap_settings",
    "Skyctrl.AxisFilters.DefaultAxisFilters": "controller_set_default_axis_filters",
    "Skyctrl.AxisFilters.GetCurrentAxisFilters": "controller_current_axis_filters",
    "Skyctrl.AxisFilters.SetAxisFilter": "controller_set_axis_filter",
    "Skyctrl.AxisMappings.DefaultAxisMapping": "controller_set_default_axis_mapping",
    "Skyctrl.AxisMappings.GetAvailableAxisMappings": "controller_available_axis_mappings",
    "Skyctrl.AxisMappings.GetCurrentAxisMappings": "controller_current_axis_mappings",
    "Skyctrl.AxisMappings.SetAxisMapping": "controller_set_axis_mapping",

    "Skyctrl.ButtonMappings.DefaultButtonMapping": "controller_set_default_button_mapping",
    "Skyctrl.ButtonMappings.GetAvailableButtonMappings": "controller_available_button_mappings",
    "Skyctrl.ButtonMappings.GetCurrentButtonMappings": "controller_current_button_mappings",
    "Skyctrl.ButtonMappings.SetButtonMapping": "controller_set_button_mapping",

    "Skyctrl.CoPiloting.SetPilotingSource": "set_piloting_source_controller",

    "Skyctrl.Common.AllStates": "get_controller_states",

    "Thermal_cam.Activate": "activate_camera_thermal",
    "Thermal_cam.Deactivate": "deactivate_camera_thermal",
    "Thermal_cam.Set_sensitivity": "set_camera_thermal_sensitivity",

    # FIXME: set_wifi_security is mapped to two different commands
    "Wifi.Set_security": "set_wifi_security",
    "Wifi.Update_authorized_channels": "",  # "get_wifi_auth_channels",
}

DEFAULT_TIMEOUT = 10
TIMEOUT_BY_COMMAND = {
    "Animation.Cancel": 5,
    "Animation.Start_candle": 5,
    "Animation.Start_dolly_slide": 5,
    "Animation.Start_dronie": 5,
    "Animation.Start_flip": 5,
    "Animation.Start_horizontal_panorama": 5,
    "Animation.Start_horizontal_reveal": 5,
    "Animation.Start_parabola": 5,
    "Animation.Start_spiral": 5,
    "Animation.Start_vertical_reveal": 5,
    "Ardrone3.Animations.Flip": 5,
    "Ardrone3.Antiflickering.ElectricFrequency": 5,
    "Ardrone3.Antiflickering.SetMode": 5,
    "Ardrone3.Camera.OrientationV2": 20,
    "Ardrone3.GPSSettings.HomeType": 20,
    "Ardrone3.GPSSettings.ReturnHomeDelay": 20,
    "Ardrone3.GPSSettings.ReturnHomeMinAltitude": 20,
    "Ardrone3.MediaRecord.PictureV2": 20,
    "Ardrone3.MediaRecord.VideoV2": 15,
    "Ardrone3.MediaStreaming.VideoEnable": 3,
    "Ardrone3.PictureSettings.ExpositionSelection": 20,
    "Ardrone3.PictureSettings.PictureFormatSelection": 20,
    "Ardrone3.Piloting.CancelMoveTo": 5,
    "Ardrone3.Piloting.Emergency": 10,
    "Ardrone3.Piloting.FlatTrim": 5,
    "Ardrone3.Piloting.MoveBy": 20,
    "Ardrone3.Piloting.MoveTo": 20,
    "Ardrone3.Piloting.NavigateHome": 7,
    "Ardrone3.Piloting.StartPilotedPOI": 5,
    "Ardrone3.Piloting.StopPilotedPOI": 5,
    "Ardrone3.PilotingSettings.CirclingAltitude": 3,
    "Ardrone3.PilotingSettings.CirclingDirection": 3,
    "Ardrone3.PilotingSettings.MaxAltitude": 20,
    "Ardrone3.PilotingSettings.MinAltitude": 20,
    "Ardrone3.PilotingSettings.PitchMode": 3,
    "Common.Calibration.MagnetoCalibration": 3,
    "Common.Calibration.PitotCalibration": 3,
    "Common.FlightPlanSettings.ReturnHomeOnDisconnect": 20,
    "Common.Mavlink.Pause": 20,
    "Common.Mavlink.Start": 20,
    "Common.Mavlink.Stop": 20,
    "Generic.Default": 5,
    "Generic.SetDroneSettings": 5,
    "Thermal_cam.Activate": 5,
    "Thermal_cam.Deactivate": 5,
    "Thermal_cam.Set_sensitivity": 5,
}

FLOAT_TOLERANCE_BY_FEATURE = {
    "gimbal": (1e-1, 1e-1)  # yaw/pitch/roll angles in degrees
}


class ArsdkMessageMeta(type):

    _base = None

    def __new__(mcls, *args, **kwds):
        """
        ArsdkMessage constructor
        @type obj: arsdkparser.ArMsg
        @param name_path: the full xml path of the message as a list of names
            ["<feature_name>", "<class_name>" or None, "message_name"]
        @param id_path: the full xml path of the message as a list ids
            [<feature_id>, <class_id> or None, message_id]
        @type enums: olympe.arsdkng.ArsdkEnums
        """
        if mcls._base is None:
            cls = type.__new__(mcls, *args, **kwds)
            mcls._base = cls
            return cls

        obj, name_path, id_path, enums = args

        fullPath = tuple(filter(None, name_path))
        fullName = '.'.join(fullPath)

        cls = type.__new__(mcls, builtin_str(fullName), (mcls._base,), {})

        cls.fullName = fullName
        cls.prefix = fullPath[:-1]
        cls.FULL_NAME = '_'.join(fullPath).upper()
        cls.Full_Name = '_'.join((name[0].upper() + name[1:] for name in fullPath))
        cls.FullName = '.'.join((name[0].upper() + name[1:] for name in fullPath))

        cls.obj = obj
        cls.name_path = name_path
        cls.id_path = id_path

        cls.args_pos = OrderedDict()
        cls.args_enum = OrderedDict()
        cls.args_bitfield = OrderedDict()

        cls.callback_type = ArsdkMessageCallbackType.from_arsdk(cls.obj.listType)
        cls.message_type = ArsdkMessageType.from_arsdk(type(cls.obj))
        cls.buffer_type = ArsdkMessageBufferType.from_arsdk(cls.obj.bufferType)

        cls.loglevel = logging.INFO
        if (cls.message_type is ArsdkMessageType.EVT and
           cls.buffer_type is not ArsdkMessageBufferType.ACK):
            # Avoid being flooded by spontaneous event messages sent by the drone
            cls.loglevel = logging.DEBUG
        elif cls.fullName in \
            ("ardrone3.PilotingState.AltitudeChanged",
             "ardrone3.PilotingState.AltitudeAboveGroundChanged",
             "ardrone3.PilotingState.AttitudeChanged",
             "ardrone3.PilotingState.GpsLocationChanged",
             "ardrone3.PilotingState.PositionChanged",
             "ardrone3.PilotingState.SpeedChanged",
             "skyctrl.SkyControllerState.AttitudeChanged",
             "mapper.button_mapping_item",
             "mapper.axis_mapping_item",
             "mapper.expo_map_item",
             "mapper.inverted_map_item",):
            cls.loglevel = logging.DEBUG

        cls.feature_name = name_path[0]
        cls.FeatureName = cls.feature_name[0].upper() + cls.feature_name[1:]
        cls.class_name = name_path[1]
        if (cls.class_name is not None and
           ("state" in cls.class_name.lower() or "event" in cls.class_name.lower())):
            cls.message_type = ArsdkMessageType.EVT
        cls.name = name_path[2]

        cls.feature_id = id_path[0]
        cls.class_id = id_path[1] or ARSDK_CLS_DEFAULT_ID
        cls.msg_id = id_path[2]

        cls.id = cls.feature_id << 24 | cls.class_id << 16 | cls.msg_id
        cls.id_name = '-'.join(map(str, filter(lambda x: x is not None, cls.id_path)))

        # build a list of olympe command name aliases
        cls.aliases = []
        if cls.FullName in ARSDK_OLYMPE_CMD_MAP.keys():
            mapped_value = ARSDK_OLYMPE_CMD_MAP[cls.FullName]
            if isinstance(mapped_value, str) and mapped_value != "":
                cls.aliases = [cls.Full_Name, mapped_value]
            else:
                cls.aliases = [cls.Full_Name]

        elif not cls.FullName[0].isdigit():
            cls.aliases = [cls.Full_Name]

        # process arguments information
        for i, arg in enumerate(cls.obj.args):
            cls.args_pos[arg.name] = i
            if isinstance(arg.argType, arsdkparser.ArEnum):
                enum_name = arg.argType.name
                if cls.class_name is not None:
                    prefix = cls.class_name + "_"
                    if arg.argType.name.startswith(prefix):
                        enum_name = arg.argType.name[len(prefix):]
                cls.args_enum[arg.name] = enums[cls.FeatureName][enum_name]
            elif isinstance(arg.argType, arsdkparser.ArBitfield):
                cls.args_bitfield[arg.name] = \
                    enums[cls.FeatureName][arg.argType.enum.name]._bitfield_type_

        cls.args_name = [arg.name for arg in cls.obj.args]

        cls.key_name = None
        if cls.obj.listType == arsdkparser.ArCmdListType.MAP:
            cls.key_name = cls.obj.mapKey and cls.obj.mapKey.name or cls.obj.args[0].name
        elif "cam_id" in cls.args_name:
            # FIXME: workaround missing MAP_ITEMS in camera.xml
            cls.callback_type = ArsdkMessageCallbackType.MAP
            cls.key_name = "cam_id"
        elif "gimbal_id" in cls.args_name:
            # FIXME: workaround missing MAP_ITEMS in gimbal.xml
            cls.callback_type = ArsdkMessageCallbackType.MAP
            cls.key_name = "gimbal_id"
        elif ("list_flags" in cls.args_bitfield and
              cls.args_bitfield["list_flags"] == list_flags._bitfield_type_):
            cls.callback_type = ArsdkMessageCallbackType.LIST

        if cls.obj.args:
            cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args = map(list, zip(*(
                cls._ar_argtype_encode_type(ar_arg.argType)
                for ar_arg in cls.obj.args
            )))
        else:
            cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args = [], [], []

        cls.args_type = OrderedDict()
        for argname, ar_arg in zip(cls.args_name, cls.obj.args):
            cls.args_type[argname] = cls._ar_argtype_to_python(argname, ar_arg.argType)

        cls.timeout = TIMEOUT_BY_COMMAND.get(cls.FullName, DEFAULT_TIMEOUT)

        cls.float_tol = FLOAT_TOLERANCE_BY_FEATURE.get(cls.feature_name, DEFAULT_FLOAT_TOL)

        cls.send = None

        cls._expectation = None

        # Get information on callback ctypes arguments
        cls.arsdk_desc = od.arsdk_cmd_find_desc(od.struct_arsdk_cmd.bind({
            "prj_id": cls.feature_id,
            "cls_id": cls.class_id,
            "cmd_id": cls.msg_id,
        }))

        cls.decode_ctypes_args = []

        decode_ctypes_args_map = {
            od.ARSDK_ARG_TYPE_I8: ctypes.c_int8,
            od.ARSDK_ARG_TYPE_U8: ctypes.c_uint8,
            od.ARSDK_ARG_TYPE_I16: ctypes.c_int16,
            od.ARSDK_ARG_TYPE_U16: ctypes.c_uint16,
            od.ARSDK_ARG_TYPE_I32: ctypes.c_int32,
            od.ARSDK_ARG_TYPE_U32: ctypes.c_uint32,
            od.ARSDK_ARG_TYPE_I64: ctypes.c_int64,
            od.ARSDK_ARG_TYPE_U64: ctypes.c_uint64,
            od.ARSDK_ARG_TYPE_FLOAT: ctypes.c_float,
            od.ARSDK_ARG_TYPE_DOUBLE: ctypes.c_double,
            od.ARSDK_ARG_TYPE_STRING: ctypes.c_char_p,
            od.ARSDK_ARG_TYPE_ENUM: ctypes.c_int,
        }

        for i in range(cls.arsdk_desc.contents.arg_desc_count):
            arg_type = cls.arsdk_desc.contents.arg_desc_table[i].type
            cls.decode_ctypes_args.append(decode_ctypes_args_map[arg_type])

        # Fixup missing list_flags arguments for LIST_ITEM/MAP_ITEM messages
        if ("list_flags" not in cls.args_name) and (
            cls.message_type is ArsdkMessageType.EVT and
            cls.callback_type in (ArsdkMessageCallbackType.LIST, ArsdkMessageCallbackType.MAP)
        ):
            cls.args_pos["list_flags"] = len(cls.args_pos)
            cls.args_name.append("list_flags")
            cls.args_bitfield["list_flags"] = list_flags._bitfield_type_
            cls.args_type[argname] = int
            cls.decode_ctypes_args.append(ctypes.c_uint8)
            cls.encode_ctypes_args.append(ctypes.c_uint8)

        if cls.message_type is ArsdkMessageType.CMD:
            cls.args_default = ArsdkMessages._default_arguments.get(cls.FullName, OrderedDict())
        else:
            cls.args_default = OrderedDict(zip(cls.args_name, [None] * len(cls.args_name)))
        cls.args_default_str = ", ".join((
            "{}={}".format(argname, cls.args_default[argname])
            if argname in cls.args_default else argname
            for argname in cls.args_name + ['**kwds']
        ))

        # docstring
        cls.doc_todos = u""
        cls.docstring = cls._py_ar_cmd_docstring()
        cls.__doc__ = cls.docstring + "\n"

        cls.__call__ = cls._create_call()
        return cls

    def _py_ar_cmd_docstring(cls):
        """
        Returns a python docstring from an ArCmd object
        """
        docstring = u"\n\n".join(
            [cls.FullName] +
            [cls._py_ar_comment_docstring(
                cls.obj.doc,
                cls._py_ar_args_docstring(cls.obj.args),
                cls.obj.isDeprecated)]
        )
        return docstring

    def _py_ar_arg_directive(cls, directive, argname, doc):
        directive = u":{} {}: ".format(directive, argname)
        doc = u"{}{}".format(directive, doc)
        doc = textwrap.fill(
            doc,
            subsequent_indent=(' ' * len(directive)),
            break_long_words=False
        )
        return doc

    def _py_ar_args_docstring(cls, ar_args):
        if cls.message_type == ArsdkMessageType.CMD:
            extra_params_docstring = (
                "\n\n" +
                ":param _timeout: command message timeout (defaults to {})\n".format(cls.timeout) +
                ":type _timeout: int\n" +
                ":param _no_expect: if True for,do not expect the usual command expectation " +
                "(defaults to False)\n" +
                ":type _no_expect: bool\n"
            )
        else:
            extra_params_docstring = (
                "\n\n" +
                ":param _policy: specify how to check the expectation. Possible values are " +
                "'check', 'wait' and 'check_wait' (defaults to 'check_wait')\n" +
                ":type _policy: `olympe.arsdkng.expectations.ExpectPolicy`\n"
            )
        extra_params_docstring += (
            ":param _float_tol: specify the float comparison tolerance, a 2-tuple containing a " +
            "relative tolerance float value and an absolute tolerate float value " +
            "(default to {}). ".format(cls.float_tol) + "See python 3 stdlib `math.isclose` " +
            "documentation for more information\n" +
            ":type _float_tol: `tuple`\n"
        )
        return "\n".join((cls._py_ar_arg_docstring(arg) for arg in ar_args)) + extra_params_docstring

    def _py_ar_arg_docstring(cls, ar_arg):
        """
        Returns a python docstring from an ArArg object
        """
        if isinstance(ar_arg.argType, (int,)):
            type_ = cls._py_ar_arg_directive(
                "type", ar_arg.name, arsdkparser.ArArgType.TO_STRING[ar_arg.argType])
        elif isinstance(ar_arg.argType, (arsdkparser.ArBitfield,)):
            enum = ":py:class:`olympe.enums.{}.{}`".format(
                ".".join(cls.prefix), cls.args_bitfield[ar_arg.name]._enum_type_.__name__)
            doc = "BitfieldOf({}, {})".format(
                enum,
                arsdkparser.ArArgType.TO_STRING[ar_arg.argType.btfType],
            )
            type_ = cls._py_ar_arg_directive("type", ar_arg.name, doc)
        elif isinstance(ar_arg.argType, (arsdkparser.ArEnum,)):
            doc = ":py:class:`olympe.enums.{}.{}`".format(
                ".".join(cls.prefix), cls.args_enum[ar_arg.name].__name__)
            type_ = cls._py_ar_arg_directive("type", ar_arg.name, doc)
        else:
            raise RuntimeError("Unknown argument type {}".format(
                type(ar_arg.argType)))

        param = cls._py_ar_arg_directive(
            "param", ar_arg.name, cls._py_ar_comment_docstring(ar_arg.doc))
        return u"\n\n{}\n\n{}".format(type_, param)

    def _py_ar_supported(cls, supported_devices, deprecated):
        unsupported_notice = "**Unsupported message**"
        if not cls.feature_name == "debug":
            unsupported_notice += (
                "\n\n.. todo::\n    "
                "Remove unsupported message {}\n".format(cls.fullName)
            )
        deprecation_notice = (
            "**Deprecated message**\n\n.. warning::\n    "
            "This message is deprecated and should no longer be used"
        )
        if deprecated:
            unsupported_notice += "\n\n" + deprecation_notice
        if not supported_devices:
            return unsupported_notice
        supported_devices = string_from_arsdkxml(supported_devices)
        if supported_devices == "drones":
            return "**Supported by every drone product**"
        elif supported_devices == "none":
            return unsupported_notice
        supported_devices = supported_devices.split(';')
        supported_devices = list(
            map(lambda s: s.split(':', maxsplit=2), supported_devices))
        try:
            supported_devices = list(
                map(lambda s: (int(s[0], base=16), *s[1:]), supported_devices))
        except ValueError:
            return unsupported_notice
        ret = []
        for device in supported_devices:
            device_str, *versions = device
            versions = iter(versions)
            device_str = od.string_cast(od.arsdk_device_type_str(device_str))
            since = next(versions, None)
            until = next(versions, None)
            mapping = {
                "ANAFI4K": "Anafi/AnafiFPV",
                "ANAFI_THERMAL": "AnafiThermal",
                "SKYCTRL_3": "SkyController3",
            }
            device_str = mapping.get(device_str, device_str)
            if "anafi" in device_str.lower() or "skycontroller" in device_str.lower():
                if until:
                    ret.append("    :{}: since {} and until {} firmware release".format(
                        device_str,
                        since,
                        until
                    ))
                else:
                    ret.append("    :{}: with an up to date firmware".format(device_str))
        if not ret:
            return unsupported_notice

        ret = "\n".join(ret)
        ret = "\n\n" + ret + "\n\n"
        ret = "**Supported by**: " + ret
        if deprecated:
            ret += "\n\n" + deprecation_notice
        return ret

    def _py_ar_triggered(cls, triggered):
        ret = string_from_arsdkxml(triggered)
        if not ret.startswith("Triggered "):
            ret = "Triggered " + ret
        return textwrap.fill(ret, break_long_words=False)

    def _py_ar_comment_docstring(cls, ar_comment, ar_args_doc=None, ar_is_deprecated=False):
        """
        Returns a python docstring from an ArComment object
        """
        if isinstance(ar_comment, (str, bytes)):
            return string_from_arsdkxml(str(ar_comment))
        ret = u""
        if ar_comment.title and not ar_comment.desc:
            ret += u"\n\n{}".format(
                textwrap.fill(string_from_arsdkxml(ar_comment.title), break_long_words=False),
            )
        elif ar_comment.desc:
            ret += u"\n\n{}".format(
                textwrap.fill(string_from_arsdkxml(ar_comment.desc), break_long_words=False),
            )
        if ar_args_doc is not None:
            ret += ar_args_doc
        # FIXME: arsdk-xml "support" attribute is currently unreliable
        ret += "\n\n{}".format(
            cls._py_ar_supported(ar_comment.support, ar_is_deprecated),
        )
        if ar_comment.triggered:
            ret += "\n\n{}".format(
                cls._py_ar_triggered(ar_comment.triggered),
            )
        if ar_comment.result:
            ret += "\n\n**Result**: {}".format(
                textwrap.fill(string_from_arsdkxml(ar_comment.result), break_long_words=False),
            )
        return ret

    def _py_ar_cmd_expectation_docstring(cls):
        ret = u""
        if cls.message_type == ArsdkMessageType.CMD:
            for i, expectation in enumerate(cls._expectation):
                if isinstance(expectation, ArsdkWhenAnyExpectation):
                    ret += cls._py_ar_cmd_any_expectation_docstring(expectation)
                else:
                    ret += (
                        "#" + expectation.expected_message.id_name +
                        cls._py_ar_cmd_expectation_args_docstring(
                            expectation.expected_args)
                    )
                if i < len(cls._expectation) - 1:
                    ret += " & "
        if ret:
            ret = "**Expectations**: {}".format(ret)
        return ret

    def _py_ar_cmd_any_expectation_docstring(cls, any_expectations):
        ret = u"("
        for i, expectation in enumerate(any_expectations):
            ret += (
                "#" + expectation.expected_message.id_name +
                cls._py_ar_cmd_expectation_args_docstring(
                    expectation.expected_args)
            )
            if i < len(any_expectations) - 1:
                ret += " | "
        ret += u")"
        return ret

    def _py_ar_cmd_expectation_args_docstring(cls, args):
        args = args.copy()
        args.update(_policy="'wait'")
        ret = u"("
        ret += u", ".join((argname + "=" + cls._py_ar_cmd_expectation_argval_docstring(
            argname, argval) for argname, argval in args.items()))
        ret += u")"
        ret = ret.replace('this.', 'self.')
        return ret

    def _py_ar_cmd_expectation_argval_docstring(cls, argname, argval):
        if isinstance(argval, ArsdkEnum):
            return "'" + argval._name_ + "'"
        elif isinstance(argval, ArsdkBitfield):
            return argval.pretty()
        elif callable(argval):
            command_args = OrderedDict(((arg, "this.{}".format(arg)) for arg in cls.args_name))
            try:
                return argval(cls, command_args)
            except KeyError:
                cls.doc_todos += u"\n\n.. todo::\n    {}".format(
                    "Fix wrong expectation definition for {}:\n    {}".format(
                        cls.fullName,
                        "Invalid parameter value for the '{}' expectation parameter\n".format(
                            argname)
                    )
                )
                return "InternalError"
        else:
            return str(argval)

    def get_source(cls):
        args = ", ".join(cls.args_name + ["**kwds"])
        docstring = cls.docstring
        # The docstring needs to be correctly indented in order to be
        # interpreted just below
        docstring = u"\n" + u"\n".join(
            [u" " * 16 + doc.strip() for doc in docstring.splitlines()])
        return textwrap.dedent(
            u"""
            def {name}(self, {defaulted_args}):
                u\"""{docstring}
                \"""
                try:
                    return self._expect({args})
                except Exception as e:
                    import logging
                    logging.exception("")
                    raise
            """.format(
                name=cls.name,
                defaulted_args=cls.args_default_str,
                args=args, docstring=docstring))

    def _create_call(cls):
        """
        Returns a python function that sends a specific ARSDK command

        The name of the returned python function is cls.name
        The parameters of the returned function repsect the naming of arsdk-xml.
        The docstring of the returned function is also extracted from the XMLs.

        @param send_command: ArCmd object provided by the arsdk-xml parser

        """
        exec(cls.get_source(), locals())
        return locals()[cls.name]

    def _ar_arsdk_encode_type_info(cls, ar_argtype):
        arsdk_encode_type_info_map = {
            arsdkparser.ArArgType.I8: (od.ARSDK_ARG_TYPE_I8, "i8", ctypes.c_int8),
            arsdkparser.ArArgType.U8: (od.ARSDK_ARG_TYPE_U8, "u8", ctypes.c_uint8),
            arsdkparser.ArArgType.I16: (od.ARSDK_ARG_TYPE_I16, "i16", ctypes.c_int16),
            arsdkparser.ArArgType.U16: (od.ARSDK_ARG_TYPE_U16, "u16", ctypes.c_uint16),
            arsdkparser.ArArgType.I32: (od.ARSDK_ARG_TYPE_I32, "i32", ctypes.c_int32),
            arsdkparser.ArArgType.U32: (od.ARSDK_ARG_TYPE_U32, "u32", ctypes.c_uint32),
            arsdkparser.ArArgType.I64: (od.ARSDK_ARG_TYPE_I64, "i64", ctypes.c_int64),
            arsdkparser.ArArgType.U64: (od.ARSDK_ARG_TYPE_U64, "u64", ctypes.c_uint64),
            arsdkparser.ArArgType.FLOAT: (od.ARSDK_ARG_TYPE_FLOAT, "f32", ctypes.c_float),
            arsdkparser.ArArgType.DOUBLE: (od.ARSDK_ARG_TYPE_DOUBLE, "f64", ctypes.c_double),
            arsdkparser.ArArgType.STRING: (od.ARSDK_ARG_TYPE_STRING, "cstr", od.char_pointer_cast),
            arsdkparser.ArArgType.ENUM: (od.ARSDK_ARG_TYPE_ENUM, "i32", ctypes.c_int32),
        }
        return arsdk_encode_type_info_map[ar_argtype]

    def _ar_argtype_encode_type(cls, ar_argtype):
        """
        Returns the ctypes type associated to the ar_argtype from arsdkparser
        """
        arbitfield = isinstance(ar_argtype, arsdkparser.ArBitfield)
        ar_bitfield_value = ar_argtype == arsdkparser.ArArgType.BITFIELD

        if isinstance(ar_argtype, arsdkparser.ArEnum):
            return od.ARSDK_ARG_TYPE_ENUM, "i32", ctypes.c_int32
        elif (arbitfield or ar_bitfield_value):
            return cls._ar_arsdk_encode_type_info(ar_argtype.btfType)
        else:
            return cls._ar_arsdk_encode_type_info(ar_argtype)

    def _ar_argtype_to_python(cls, argname, ar_argtype):
        """
        Returns the python type associated to the ar_argtype from arsdkparser
        """

        if argname in cls.args_enum:
            return cls.args_enum[argname]
        elif argname in cls.args_bitfield:
            return cls.args_bitfield[argname]
        elif ar_argtype in (arsdkparser.ArArgType.I8,
                            arsdkparser.ArArgType.U8,
                            arsdkparser.ArArgType.I16,
                            arsdkparser.ArArgType.U16,
                            arsdkparser.ArArgType.I32,
                            arsdkparser.ArArgType.U32,
                            arsdkparser.ArArgType.I64,
                            arsdkparser.ArArgType.U64):
            return int
        elif ar_argtype in (arsdkparser.ArArgType.DOUBLE, arsdkparser.ArArgType.FLOAT):
            return float
        elif ar_argtype == arsdkparser.ArArgType.STRING:
            return str
        else:
            return None

    def _is_list_item(self):
        return self.callback_type is ArsdkMessageCallbackType.LIST

    def _is_map_item(self):
        return self.callback_type is ArsdkMessageCallbackType.MAP

    def _resolve_expectations(cls, messages):
        if cls.message_type == ArsdkMessageType.CMD:
            if cls.obj.expect is not None:
                expectation_objs = cls.obj.expect.immediate + cls.obj.expect.delayed
            else:
                expectation_objs = []
            cls._expectation = ArsdkCommandExpectation(cls)
            for expectation_obj in expectation_objs:
                if not isinstance(expectation_obj, list):
                    cls._expectation.append(
                        ArsdkEventExpectation.from_arsdk(messages, expectation_obj))
                else:
                    cls._expectation.append(
                        ArsdkWhenAnyExpectation.from_arsdk(messages, expectation_obj))
        else:
            cls._expectation = ArsdkEventExpectation(
                cls, OrderedDict((zip(cls.args_name, [None] * len(cls.args_name)))))

    def _resolve_doc(cls, messages):
        cls.docstring += "\n\n" + cls._py_ar_cmd_expectation_docstring()

        def message_ref(m):
            try:
                message = messages.by_id_name[m.group(1)]
            except KeyError:
                return m.group(0)
            if m.lastindex > 1:
                args = "(" + m.group(2) + ")"
            else:
                args = "()"
            return ":py:func:`{}{}<olympe.messages.{}>`".format(
                message.name,
                args,
                message.fullName)
        cls.docstring = re.sub(r"\[[\w_\\]+\]\(#([\d-]+)\)", message_ref, cls.docstring)
        cls.docstring = re.sub(r"#([\d-]+)\(([^)]*)\)", message_ref, cls.docstring)
        cls.docstring = re.sub(r"#([\d-]+)", message_ref, cls.docstring)
        if cls.doc_todos:
            cls.docstring += cls.doc_todos
        cls.__doc__ = cls.docstring


class ArsdkMessage(with_metaclass(ArsdkMessageMeta)):

    def __init__(self):
        self.send = None
        self._reset_state()

    @classmethod
    def new(cls):
        self = cls.__new__(cls, cls.__name__, ArsdkMessage, {})
        self.__init__()
        return self

    def copy(self):
        other = self.new()
        other.send = self.send
        other._last_event = self._last_event
        other._state = self._state.copy()
        return other

    def _bind_send_command(self, send_command):
        """
        Returns a python function that sends a specific ARSDK command

        The name of the returned python function is self.name
        The parameters of the returned function repsect the naming of arsdk-xml.
        The docstring of the returned function is also extracted from the XMLs.

        @param send_command: ArCmd object provided by the arsdk-xml parser

        """
        args = ", ".join(self.args_name + ["**kwds"])
        docstring = self.docstring
        # The docstring needs to be correctly indented in order to be
        # interpreted just below
        docstring = u"\n" + u"\n".join(
            [u" " * 16 + doc.strip() for doc in docstring.splitlines()])
        # TODO: remove backward compatibility for the 'timeout' message parameter
        # this parameter has been replaced by '_timeout'
        exec(textwrap.dedent(
            u"""
            def {name}_SEND({defaulted_args}):
                u\"""{docstring}
                \"""
                try:
                    kwds['_timeout'] = kwds.pop('timeout')
                except KeyError:
                    pass
                kwds['_deprecated_statedict'] = True
                return send_command(self, {args})
            """.format(
                name=self.name,
                defaulted_args=self.args_default_str,
                args=args, docstring=docstring)), locals())
        self.send = locals()[self.name + "_SEND"]

    @classmethod
    def _argsmap_from_args(cls, *args, **kwds):
        args = OrderedDict((zip(map(lambda a: a, cls.args_name), args)))
        args_set = set(args.keys())
        kwds_set = set(kwds.keys())
        if not args_set.isdisjoint(kwds_set):
            raise RuntimeError(
                "Message `{}` got multiple values for argument(s) {}".format(
                    cls.fullName, list(args_set & kwds_set)))
        args.update(kwds)

        # filter out None value
        args = OrderedDict([(k, v) for k, v in args.items() if v is not None])

        # enum conversion
        args = OrderedDict(starmap(
            lambda name, value: (name, cls.args_enum[name][value])
            if (name in cls.args_enum and isinstance(value, (bytes, str))) else (name, value),
            args.items()
        ))

        # bitfield conversion
        args = OrderedDict(starmap(
            lambda name, value: (name, cls.args_bitfield[name](value))
            if name in cls.args_bitfield else (name, value),
            args.items()
        ))

        args = OrderedDict(starmap(lambda k, v: (k, v), args.items()))
        return args

    @classmethod
    def _expectation_from_args(cls, *args, **kwds):
        expected_args = cls._argsmap_from_args(*args, **kwds)
        return ArsdkEventExpectation(cls, expected_args)

    @classmethod
    def _event_from_args(cls, *args, **kwds):
        args = cls._argsmap_from_args(*args, **kwds)
        return ArsdkMessageEvent(cls, args)

    def last_event(self, key=None):
        if self._last_event is None:
            return None
        if key is None:
            return self._last_event
        else:
            return self._last_event[key]

    def _set_last_event(self, event):
        if event.message.id != self.id:
            raise RuntimeError("Cannot set message {} last event to {}".format(
                self.fullName, event.message.fullName))

        if self.callback_type == ArsdkMessageCallbackType.STANDARD:
            self._last_event = event
            self._state = event.args
        elif self.callback_type == ArsdkMessageCallbackType.MAP:
            if self._last_event is None:
                self._last_event = OrderedDict()
            key = event.args[self.key_name]
            if (not event.args["list_flags"] or
                    event.args["list_flags"] == [list_flags.Last]):
                self._state[key] = event.args
            if list_flags.First in event.args["list_flags"]:
                self._state = OrderedDict()
                self._state[key] = event.args
            if list_flags.Empty in event.args["list_flags"]:
                self._state = OrderedDict()
            if list_flags.Remove in event.args["list_flags"]:
                # remove the received element from the current map
                if key in self._state:
                    self._state.pop(key)
            else:
                self._last_event[key] = event
        elif self.callback_type == ArsdkMessageCallbackType.LIST:
            if (not event.args["list_flags"] or
                    event.args["list_flags"] == [list_flags.Last]):
                # append to the current list
                insert_pos = next(reversed(self._state), -1) + 1
                self._state[insert_pos] = event.args
            if list_flags.First in event.args["list_flags"]:
                self._state = OrderedDict()
                self._state[0] = event.args
            if list_flags.Empty in event.args["list_flags"]:
                self._state = OrderedDict()
            if list_flags.Remove in event.args["list_flags"]:
                # remove the received element from the current list
                for k, v in self._state:
                    for argname, argval in v.items():
                        if argname == "list_flags":
                            continue
                        if argval != event.args[argname]:
                            break
                    else:
                        # if all arguments have matched except "list_flags"
                        self._state.pop(k, None)
            else:
                self._last_event = event

    def state(self):
        if self._last_event is None:
            raise RuntimeError("{} state is uninitialized".format(self.fullName))
        return self._state

    def _reset_state(self):
        self._last_event = None
        self._state = OrderedDict()

    def _expect(cls, *args, **kwds):
        """
        For a command message, returns the list of expectations for this message with the provided
        command arguments.

        @param args: the command message arguments
        @param _no_expect: if True for a command message, do not expect the usual command
            expectation (defaults to False)
        """
        default_timeout = cls.timeout if cls.message_type is ArsdkMessageType.CMD else None
        default_float_tol = cls.float_tol
        timeout = kwds.pop('_timeout', default_timeout)
        float_tol = kwds.pop('_float_tol', default_float_tol)
        no_expect = kwds.pop('_no_expect', False)
        send_command = kwds.pop('_send_command', True)
        policy = kwds.pop('_policy', "check_wait")
        if isinstance(policy, (bytes, str)):
            policy = ExpectPolicy[policy]
        else:
            raise RuntimeError("policy argument must be a string")

        if not send_command and no_expect:
            raise RuntimeError(
                "Invalid argument combination " +
                "Message._expect(send_command=False, no_expect=True)")

        args = cls._argsmap_from_args(*args, **kwds)
        # enum conversion
        args = OrderedDict(starmap(
            lambda name, value: (name, cls.args_enum[name][value])
            if (name in cls.args_enum and isinstance(value, (bytes, str))) else (name, value),
            args.items()
        ))

        # bitfield conversion
        args = OrderedDict(starmap(
            lambda name, value: (name, cls.args_bitfield[name](value))
            if name in cls.args_bitfield else (name, value),
            args.items()
        ))

        # int -> float conversion
        args = OrderedDict(starmap(
            lambda name, value: (name, float(value))
            if isinstance(value, int) and cls.args_type[name] is float else (name, value),
            args.items()
        ))

        if policy != ExpectPolicy.check:
            if not send_command and cls.message_type == ArsdkMessageType.CMD:
                expectations = ArsdkWhenAllExpectations(cls._expectation.copy().expectations)
            else:
                expectations = cls._expectation.copy()
                if cls.message_type == ArsdkMessageType.CMD:
                    expectations.no_expect(no_expect)

            expectations._fill_default_arguments(cls, args)

            if policy == ExpectPolicy.check_wait and cls.message_type is ArsdkMessageType.EVT:
                check_expectation = ArsdkCheckStateExpectation(cls, args)
                expectations = ArsdkCheckWaitStateExpectation(check_expectation, expectations)
            expectations.set_timeout(timeout)
            expectations.set_float_tol(float_tol)
            return expectations
        else:
            expectations = ArsdkCheckStateExpectation(cls, args)
            expectations.set_float_tol(float_tol)
            return expectations

    @classmethod
    def _encode_args(cls, *args):
        """
        Encode python message arguments to ctypes. This also perform the necessary enum, bitfield
        and unicode conversions.
        """
        if len(args) != len(cls.obj.args):
            raise TypeError("{}() takes exactly {} arguments ({} given)".format(
                cls.FullName, len(cls.obj.args), len(args)))

        encoded_args = args
        # enum conversion (string --> enum type)
        encoded_args = list(starmap(
            lambda name, value: cls.args_enum[name][value]
            if (name in cls.args_enum and isinstance(value, (bytes, str))) else value,
            zip(cls.args_name, encoded_args)
        ))

        # enum conversion (enum type --> integer)
        encoded_args = list(starmap(
            lambda name, value: value._value_
            if (name in cls.args_enum) and isinstance(value, ArsdkEnum) else value,
            zip(cls.args_name, encoded_args)
        ))

        # bitfield conversion ([string, enum list, bitfield] --> integer)
        encoded_args = list(starmap(
            lambda name, value: cls.args_bitfield[name](value).to_int()
            if name in cls.args_bitfield else value,
            zip(cls.args_name, encoded_args)
        ))

        # unicode -> str utf-8 encoding
        encoded_args = list(map(
            lambda a: a.encode('utf-8') if isinstance(a, str) else a, encoded_args))

        # python -> ctypes -> struct_arsdk_value argv conversion
        encode_args_len = len(cls.arsdk_type_args)
        argv = (od.struct_arsdk_value * encode_args_len)()
        for (i, arg, sdktype, value_attr, ctype) in zip(
            range(encode_args_len), encoded_args, cls.arsdk_type_args, cls.arsdk_value_attr, cls.encode_ctypes_args):
            argv[i].type = sdktype
            setattr(argv[i].data, value_attr, ctype(arg))
        return argv

    @classmethod
    def _decode_args(cls, message_buffer):
        """
        Decode a ctypes message buffer into a list of python typed arguments. This also perform the
        necessary enum, bitfield and unicode conversions.
        """
        decoded_args = list(map(lambda ctype: ctypes.pointer(ctype()), cls.decode_ctypes_args))
        decoded_args_type = list(map(lambda ctype: ctypes.POINTER(ctype), cls.decode_ctypes_args))
        od.arsdk_cmd_dec.argtypes = od.arsdk_cmd_dec.argtypes[:2] + decoded_args_type

        res = od.arsdk_cmd_dec(message_buffer, cls.arsdk_desc, *decoded_args)

        # ctypes -> python type conversion
        decoded_args = list(map(
            lambda a: a.contents.value, decoded_args
        ))

        # bytes utf-8 -> str conversion
        decoded_args = list(map(
            lambda a: str(a, 'utf-8')
            if isinstance(a, bytes) else a,
            decoded_args
        ))

        # enum conversion
        decoded_args = list(starmap(
            lambda name, value: cls.args_enum[name](value)
            if name in cls.args_enum and value in cls.args_enum[name]._value2member_map_
            else value,
            zip(cls.args_name, decoded_args)
        ))

        # bitfield conversion
        decoded_args = list(map(
            lambda t: cls.args_bitfield[t[0]](t[1])
            if t[0] in cls.args_bitfield else t[1],
            zip(cls.args_name, decoded_args)
        ))

        return (res, decoded_args)


class ArsdkMessageType(OrderedEnum):
    CMD, EVT = range(2)

    @classmethod
    def from_arsdk(cls, value):
        return {
            arsdkparser.ArCmd: cls.CMD,
            arsdkparser.ArEvt: cls.EVT,
        }[value]


class ArsdkMessageCallbackType(OrderedEnum):
    STANDARD, LIST, MAP = range(3)

    @classmethod
    def from_arsdk(cls, value):
        return {
            arsdkparser.ArCmdListType.NONE: cls.STANDARD,
            arsdkparser.ArCmdListType.LIST: cls.LIST,
            arsdkparser.ArCmdListType.MAP: cls.MAP,
        }[value]


class ArsdkMessageBufferType(OrderedEnum):
    NON_ACK, ACK, HIGH_PRIO = range(3)

    @classmethod
    def from_arsdk(cls, value):
        return {
            arsdkparser.ArCmdBufferType.NON_ACK: cls.NON_ACK,
            arsdkparser.ArCmdBufferType.ACK: cls.ACK,
            arsdkparser.ArCmdBufferType.HIGH_PRIO: cls.HIGH_PRIO,
        }[value]


_PRESET_SETTINGS_VIDEO = {
    "max_altitude": {"is_set": 1, "value": 150.},
    "max_tilt": {"is_set": 1, "value": 35.},
    "max_distance": {"is_set": 1, "value": 2000.},
    "no_fly_over_max_distance": {"is_set": 1, "value": 0},
    "max_vertical_speed": {"is_set": 1, "value": 6.},
    "max_rotation_speed": {"is_set": 1, "value": 150.},
    "max_pitch_roll_rotation_speed": {"is_set": 1, "value": 300.},
    "return_home_delay": {"is_set": 1, "value": 120},
    "home_type": {"is_set": 1, "value": 0},
    "video_stabilization_mode": {"is_set": 1, "value": 0},
    "banked_turn": {"is_set": 1, "value": 1},
}


class ArsdkMessages(object):
    """
    A python class to represent arsdk messages commands and events alike.
    """

    _single = None

    @classmethod
    def get(cls):
        if cls._single is None:
            cls._single = cls()
        return cls._single

    _default_arguments = {
        'Ardrone3.GPSSettings.SendControllerGPS':
            dict(horizontalAccuracy=1.0, verticalAccuracy=1.0),
        'Ardrone3.NetworkSettings.WifiSelection': dict(channel=0),
        'Ardrone3.PictureSettings.VideoAutorecordSelection': dict(mass_storage_id=0),
        'Common.Mavlink.Start': dict(type="'flightPlan'"),
        'Generic.SetDroneSettings': dict(preset=_PRESET_SETTINGS_VIDEO),
        'Gimbal.Reset_orientation': dict(gimbal_id=0),
        'Gimbal.Start_offsets_update': dict(gimbal_id=0),
        'Gimbal.Stop_offsets_update': dict(gimbal_id=0),
    }

    def __init__(self, arsdk_enums=None):
        """
        ArsdkMessages constructor
        @type arsdk_enums: olympe.arsdkng.Enums
        """
        self.enums = arsdk_enums
        if self.enums is None:
            self.enums = ArsdkEnums.get()
        self._ctx = self.enums._ctx
        self.BY_NAME = OrderedDict()
        self.By_Name = OrderedDict()
        self.ByName = OrderedDict()
        self.by_id = OrderedDict()
        self.by_id_name = OrderedDict()
        self.by_prefix = OrderedDict()
        self.by_feature = OrderedDict()
        self._feature_name_by_id = OrderedDict()

        self._populate_messages()
        self._resolve_expectations()
        self._resolve_doc()

    def _populate_messages(self):
        for featureId in sorted(self._ctx.featuresById.keys()):
            featureObj = self._ctx.featuresById[featureId]
            # Workaround messages from the "generic" feature may contain
            # "multisettings" arguments that Olympe doesn't handle.
            # Here we simply ignore these messages to avoid any further error
            if featureObj.name == "generic":
                continue
            if featureObj.classes and len(featureObj.classes) != 0:
                for classId in sorted(featureObj.classesById.keys()):
                    classObj = featureObj.classesById[classId]
                    for msgId in sorted(classObj.cmdsById.keys()):
                        msgObj = classObj.cmdsById[msgId]
                        self._add_arsdk_message(
                            msgObj,
                            [featureObj.name, classObj.name, msgObj.name],
                            [featureId, classId, msgId],
                        )

            elif len(featureObj.getMsgs()) != 0:
                for msgId in sorted(featureObj.getMsgsById().keys()):
                    msgObj = featureObj.getMsgsById()[msgId]
                    self._add_arsdk_message(
                        msgObj,
                        [featureObj.name, None, msgObj.name],
                        [featureId, None, msgId],
                    )

    def _add_arsdk_message(self, msgObj, name_path, id_path):

        message = ArsdkMessageMeta.__new__(
            ArsdkMessageMeta, msgObj, name_path, id_path, self.enums)
        self.BY_NAME[message.FULL_NAME] = message
        self.By_Name[message.Full_Name] = message
        self.ByName[message.FullName] = message
        self.by_id[message.id] = message
        self.by_id_name[message.id_name] = message
        feature_id = (message.id & 0xFF000000) >> 24
        class_id = (message.id & 0x00FF0000) >> 16
        self._feature_name_by_id[(feature_id, class_id)] = (
            message.feature_name, message.class_name)
        if message.prefix not in self.by_prefix:
            self.by_prefix[message.prefix] = OrderedDict()
        self.by_prefix[message.prefix][message.name] = message
        if message.feature_name not in self.by_feature:
            self.by_feature[message.feature_name] = OrderedDict()
        if message.class_name is not None:
            if message.class_name not in self.by_feature[message.feature_name]:
                self.by_feature[message.feature_name][message.class_name] = OrderedDict()
            self.by_feature[message.feature_name][message.class_name][message.name] = message
        else:
            self.by_feature[message.feature_name][message.name] = message

    def walk_enums(self):
        for prefix, messages in self.by_prefix.items():
            for message_name, message in messages.items():
                for argname, enum in message.args_enum.items():
                    for enum_label, enum_value in enum.__members__.items():
                        yield prefix, message_name, argname, enum_label, enum_value

    def walk_args(self):
        for prefix, messages in self.by_prefix.items():
            for message_name, message in messages.items():
                for argname in message.args_pos.keys():
                    yield prefix, message_name, message, argname

    def unknown_message_info(self, message_id):
        feature_id = (message_id & 0xFF000000) >> 24
        class_id = (message_id & 0x00FF0000) >> 16
        msg_id = message_id & 0x0000FFFF
        feature_name, class_name = self._feature_name_by_id.get(
            (feature_id, class_id),
            (None, None)
        )
        if feature_name is None:
            return (None, None, message_id)
        else:
            return (feature_name, class_name, msg_id)

    def _resolve_expectations(self):
        for message in self.by_id.values():
            message._resolve_expectations(self)

    def _resolve_doc(self):
        for message in self.by_id.values():
            message._resolve_doc(self)
