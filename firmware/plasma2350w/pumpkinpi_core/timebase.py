"""Portable conversion between Unix timestamps and the port's time epoch."""

UNIX_2000_OFFSET = 946684800
MIN_UNIX_UTC = 1577836800
MAX_UNIX_UTC = 4102444800


def valid_unix_utc(value):
    return isinstance(value, int) and not isinstance(value, bool) \
        and MIN_UNIX_UTC <= value <= MAX_UNIX_UTC


def port_epoch_offset(time_module):
    return UNIX_2000_OFFSET if time_module.gmtime(0)[0] == 2000 else 0


def unix_gmtime(epoch, time_module):
    if not valid_unix_utc(epoch):
        raise ValueError("Unix UTC is out of range")
    return time_module.gmtime(epoch - port_epoch_offset(time_module))
