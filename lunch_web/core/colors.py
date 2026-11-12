"""Stable colors stored with options and copied into historical records."""

import colorsys


def option_color(option_id: int) -> str:
    hue = (option_id * 0.618033988749895) % 1
    channels = colorsys.hsv_to_rgb(hue, 0.48, 0.85)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in channels)
