from collections import namedtuple

GPS = namedtuple("GPS", ["latitude", "longitude", "altitude"])
GPS.__doc__ = "Namedtuple class " + str(GPS.__doc__)
