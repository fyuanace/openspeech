"""Tool names and arguments shared by the device and the platform."""
SET_VOLUME = "set_volume"
VOLUME_TOOL = {
    "type": "function",
    "name": SET_VOLUME,
    "description": "用户明确要求把当前电脑的系统音量设置为指定百分比时使用。没有明确数值时先询问。",
    "parameters": {
        "type": "object",
        "properties": {"percent": {"type": "integer", "minimum": 0, "maximum": 100}},
        "required": ["percent"],
        "additionalProperties": False,
    },
}


def valid_percent(value):
    return type(value) is int and 0 <= value <= 100


def valid_volume_arguments(arguments):
    return isinstance(arguments, dict) and arguments.keys() == {"percent"} and valid_percent(arguments["percent"])


def windows_set_volume(percent):
    from comtypes import CoInitialize, CoUninitialize
    from pycaw.pycaw import AudioUtilities

    CoInitialize()
    try:
        endpoint = AudioUtilities.GetSpeakers().EndpointVolume
        endpoint.SetMasterVolumeLevelScalar(percent / 100, None)
        return round(endpoint.GetMasterVolumeLevelScalar() * 100)
    finally:
        endpoint = None
        CoUninitialize()
