"""Tools for working with MongoDB `ObjectIds
<http://dochub.mongodb.org/core/objectids>`_.
"""

__all__ = ["ObjectId"]

import sys
import binascii, base64
import calendar
import datetime
try:
    import hashlib
    _md5func = hashlib.md5
except ImportError:  # for Python < 2.5
    import md5
    _md5func = md5.new
import os
import random
import socket
import struct
import threading
import time

class InvalidId(Exception):
    """Raised when trying to create an ObjectId from invalid data.
    """

PY3 = sys.version_info[0] > 2

if PY3:
    import codecs

    from io import BytesIO as StringIO

    def b(s):
        return codecs.latin_1_encode(s)[0]

    def bytes_from_hex(h):
        return bytes.fromhex(h)

    binary_type = bytes
    text_type   = str
    next_item   = "__next__"

else:
    try:
        from cStringIO import StringIO
    except ImportError:
        from StringIO import StringIO

    def b(s):
        # See comments above. In python 2.x b('foo') is just 'foo'.
        return s

    def bytes_from_hex(h):
        return h.decode('hex')

    binary_type = str
    # 2to3 will convert this to "str". That's okay
    # since we won't ever get here under python3.
    text_type   = unicode
    next_item   = "next"

string_types = (binary_type, text_type)

class FixedOffset(datetime.tzinfo):
    """Fixed offset timezone, in minutes east from UTC.

    Implementation based from the Python `standard library documentation
    <http://docs.python.org/library/datetime.html#tzinfo-objects>`_.
    Defining __getinitargs__ enables pickling / copying.
    """
    ZERO = datetime.timedelta(0)

    def __init__(self, offset, name):
        if isinstance(offset, datetime.timedelta):
            self.__offset = offset
        else:
            self.__offset = datetime.timedelta(minutes=offset)
        self.__name = name

    def __getinitargs__(self):
        return self.__offset, self.__name

    def utcoffset(self, dt):
        return self.__offset

    def tzname(self, dt):
        return self.__name

    def dst(self, dt):
        return self.ZERO

utc = FixedOffset(0, "UTC")


EMPTY = b("")
ZERO  = b("\x00")

def _machine_bytes():
    """Get the machine portion of an ObjectId.
    """
    machine_hash = _md5func()
    if PY3:
        # gethostname() returns a unicode string in python 3.x
        # while update() requires a byte string.
        machine_hash.update(socket.gethostname().encode())
    else:
        # Calling encode() here will fail with non-ascii hostnames
        machine_hash.update(socket.gethostname())
    return machine_hash.digest()[0:3]


def _raise_invalid_id(oid):
    raise InvalidId(
        "%r is not a valid ObjectId, it must be a 12-byte input"
        " of type %r or a 16-character base64 string or a 24-character hex string" % (
            oid, binary_type.__name__))


class ObjectId(object):
    """A MongoDB ObjectId.
    """

    _inc = random.randint(0, 0xFFFFFF)
    _inc_lock = threading.Lock()

    _machine_bytes = _machine_bytes()

    __slots__ = ('__id')

    _type_marker = 7

    def __init__(self, oid=None):
        """Initialize a new ObjectId.

        An ObjectId is a 12-byte unique identifier consisting of:

          - a 4-byte value representing the seconds since the Unix epoch,
          - a 3-byte machine identifier,
          - a 2-byte process id, and
          - a 3-byte counter, starting with a random value.

        By default, ``ObjectId()`` creates a new unique identifier. The
        optional parameter `oid` can be an :class:`ObjectId`, or any 12
        :class:`bytes` or, in Python 2, any 12-character :class:`str`.

        For example, the 12 bytes b'foo-bar-quux' do not follow the ObjectId
        specification but they are acceptable input::

          >>> ObjectId(b'foo-bar-quux')
          ObjectId('666f6f2d6261722d71757578')

        `oid` can also be a :class:`unicode` or :class:`str` of 24 hex digits::

          >>> ObjectId('0123456789ab0123456789ab')
          ObjectId('0123456789ab0123456789ab')
          >>>
          >>> # A u-prefixed unicode literal:
          >>> ObjectId(u'0123456789ab0123456789ab')
          ObjectId('0123456789ab0123456789ab')

        Raises :class:`~InvalidId` if `oid` is not 12 bytes nor
        24 hex digits, or :class:`TypeError` if `oid` is not an accepted type.

        :Parameters:
          - `oid` (optional): a valid ObjectId.

        .. versionadded:: 1.2.1
           The `oid` parameter can be a ``unicode`` instance (that contains
           24 hexadecimal digits).

        .. mongodoc:: objectids
        """
        if oid is None:
            self.__generate()
        else:
            self.__validate(oid)

    @classmethod
    def from_datetime(cls, generation_time):
        """Create a dummy ObjectId instance with a specific generation time.

        This method is useful for doing range queries on a field
        containing :class:`ObjectId` instances.

        .. warning::
           It is not safe to insert a document containing an ObjectId
           generated using this method. This method deliberately
           eliminates the uniqueness guarantee that ObjectIds
           generally provide. ObjectIds generated with this method
           should be used exclusively in queries.

        `generation_time` will be converted to UTC. Naive datetime
        instances will be treated as though they already contain UTC.

        An example using this helper to get documents where ``"_id"``
        was generated before January 1, 2010 would be:

        >>> gen_time = datetime.datetime(2010, 1, 1)
        >>> dummy_id = ObjectId.from_datetime(gen_time)
        >>> result = collection.find({"_id": {"$lt": dummy_id}})

        :Parameters:
          - `generation_time`: :class:`~datetime.datetime` to be used
            as the generation time for the resulting ObjectId.

        .. versionchanged:: 1.8
           Properly handle timezone aware values for
           `generation_time`.

        .. versionadded:: 1.6
        """
        if generation_time.utcoffset() is not None:
            generation_time = generation_time - generation_time.utcoffset()
        ts = calendar.timegm(generation_time.timetuple())
        oid = struct.pack(">i", int(ts)) + ZERO * 8
        return cls(oid)

    @classmethod
    def is_valid(cls, oid):
        """Checks if a `oid` string is valid or not.

        :Parameters:
          - `oid`: the object id to validate

        .. versionadded:: 2.3
        """
        if not oid:
            return False

        try:
            ObjectId(oid)
            return True
        except (InvalidId, TypeError):
            return False

    def __generate(self):
        """Generate a new value for this ObjectId.
        """
        oid = EMPTY

        # 4 bytes current time
        oid += struct.pack(">i", int(time.time()))

        # 3 bytes machine
        oid += ObjectId._machine_bytes

        # 2 bytes pid
        oid += struct.pack(">H", os.getpid() % 0xFFFF)

        # 3 bytes inc
        ObjectId._inc_lock.acquire()
        oid += struct.pack(">i", ObjectId._inc)[1:4]
        ObjectId._inc = (ObjectId._inc + 1) % 0xFFFFFF
        ObjectId._inc_lock.release()

        self.__id = oid

    def __validate(self, oid):
        """Validate and use the given id for this ObjectId.

        Raises TypeError if id is not an instance of
        (:class:`basestring` (:class:`str` or :class:`bytes`
        in python 3), ObjectId) and InvalidId if it is not a
        valid ObjectId.

        :Parameters:
          - `oid`: a valid ObjectId
        """
        if isinstance(oid, ObjectId):
            self.__id = oid.__id
        elif isinstance(oid, string_types):
            len_oid = len(oid)
            if len_oid == 12:
                if isinstance(oid, binary_type):
                    self.__id = oid
                else:
                    _raise_invalid_id(oid)
            elif len_oid == 16:
                try:
                    self.__id = base64.urlsafe_b64decode(oid)
                except (TypeError, ValueError):
                    _raise_invalid_id(oid)
            elif len_oid == 24:
                try:
                    self.__id = bytes_from_hex(oid)
                except (TypeError, ValueError):
                    _raise_invalid_id(oid)
            else:
                _raise_invalid_id(oid)
        else:
            raise TypeError("id must be an instance of (%s, %s, ObjectId), "
                            "not %s" % (binary_type.__name__,
                                        text_type.__name__, type(oid)))

    @property
    def binary(self):
        """12-byte binary representation of this ObjectId.
        """
        return self.__id

    @property
    def txt(self):
        """16-byte text representation of this ObjectId.
        """
        if PY3:
            return base64.urlsafe_b64encode(self.__id).decode()
        return base64.urlsafe_b64encode(self.__id)

    @property
    def hex(self):
        """24-byte text representation of this ObjectId.
        """
        if PY3:
            return binascii.hexlify(self.__id).decode()
        return binascii.hexlify(self.__id)

    @property
    def generation_time(self):
        """A :class:`datetime.datetime` instance representing the time of
        generation for this :class:`ObjectId`.

        The :class:`datetime.datetime` is timezone aware, and
        represents the generation time in UTC. It is precise to the
        second.

        .. versionchanged:: 1.8
           Now return an aware datetime instead of a naive one.

        .. versionadded:: 1.2
        """
        t = struct.unpack(">i", self.__id[0:4])[0]
        return datetime.datetime.fromtimestamp(t, utc)

    def __getstate__(self):
        """return value of object for pickling.
        needed explicitly because __slots__() defined.
        """
        return self.__id

    def __setstate__(self, value):
        """explicit state set from pickling
        """
        # Provide backwards compatability with OIDs
        # pickled with pymongo-1.9 or older.
        if isinstance(value, dict):
            oid = value["_ObjectId__id"]
        else:
            oid = value
        # ObjectIds pickled in python 2.x used `str` for __id.
        # In python 3.x this has to be converted to `bytes`
        # by encoding latin-1.
        if PY3 and isinstance(oid, text_type):
            self.__id = oid.encode('latin-1')
        else:
            self.__id = oid

    def __str__(self):
        if PY3:
            return binascii.hexlify(self.__id).decode()
        return binascii.hexlify(self.__id)

    def __repr__(self):
        return "ObjectId('%s')" % (str(self),)

    def __eq__(self, other):
        if isinstance(other, ObjectId):
            return self.__id == other.__id
        return NotImplemented

    def __ne__(self, other):
        if isinstance(other, ObjectId):
            return self.__id != other.__id
        return NotImplemented

    def __lt__(self, other):
        if isinstance(other, ObjectId):
            return self.__id < other.__id
        return NotImplemented

    def __le__(self, other):
        if isinstance(other, ObjectId):
            return self.__id <= other.__id
        return NotImplemented

    def __gt__(self, other):
        if isinstance(other, ObjectId):
            return self.__id > other.__id
        return NotImplemented

    def __ge__(self, other):
        if isinstance(other, ObjectId):
            return self.__id >= other.__id
        return NotImplemented

    def __hash__(self):
        """Get a hash value for this :class:`ObjectId`.

        .. versionadded:: 1.1
        """
        return hash(self.__id)
