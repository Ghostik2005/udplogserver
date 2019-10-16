# coding: utf-8
#
# JSON-RPC CLIENT LIBRARY
# $Id$
#
# an JSON-RPC client interface for Python.
#
# the marshalling and response parser code can also be used to
# implement JSON-RPC servers.
#
# Notes:
# this version is designed to work with Python 2.1 or newer.
#
# History:
# 1999-01-14 fl  Created
# 1999-01-15 fl  Changed dateTime to use localtime
# 1999-01-16 fl  Added Binary/base64 element, default to RPC2 service
# 1999-01-19 fl  Fixed array data element (from Skip Montanaro)
# 1999-01-21 fl  Fixed dateTime constructor, etc.
# 1999-02-02 fl  Added fault handling, handle empty sequences, etc.
# 1999-02-10 fl  Fixed problem with empty responses (from Skip Montanaro)
# 1999-06-20 fl  Speed improvements, pluggable parsers/transports (0.9.8)
# 2000-11-28 fl  Changed boolean to check the truth value of its argument
# 2001-02-24 fl  Added encoding/Unicode/SafeTransport patches
# 2001-02-26 fl  Added compare support to wrappers (0.9.9/1.0b1)
# 2001-03-28 fl  Make sure response tuple is a singleton
# 2001-03-29 fl  Don't require empty params element (from Nicholas Riley)
# 2001-06-10 fl  Folded in _jsonrpclib accelerator support (1.0b2)
# 2001-08-20 fl  Base jsonrpclib.Error on built-in Exception (from Paul Prescod)
# 2001-09-10 fl  Lazy import of urllib, cgi, jsonlib (20x import speedup)
# 2001-10-01 fl  Remove containers from memo cache when done with them
# 2001-10-01 fl  Use faster escape method (80% dumps speedup)
# 2001-10-02 fl  More dumps microtuning
# 2001-10-04 fl  Make sure import expat gets a parser (from Guido van Rossum)
# 2001-10-10 sm  Allow long ints to be passed as ints if they don't overflow
# 2001-10-17 sm  Test for int and long overflow (allows use on 64-bit systems)
# 2001-11-12 fl  Use repr() to marshal doubles (from Paul Felix)
# 2002-03-17 fl  Avoid buffered read when possible (from James Rucker)
# 2002-04-07 fl  Added pythondoc comments
# 2002-04-16 fl  Added __str__ methods to datetime/binary wrappers
# 2002-05-15 fl  Added error constants (from Andrew Kuchling)
# 2002-06-27 fl  Merged with Python CVS version
# 2002-10-22 fl  Added basic authentication (based on code from Phillip Eby)
# 2003-01-22 sm  Add support for the bool type
# 2003-02-27 gvr Remove apply calls
# 2003-04-24 sm  Use cStringIO if available
# 2003-04-25 ak  Add support for nil
# 2003-06-15 gn  Add support for time.struct_time
# 2003-07-12 gp  Correct marshalling of Faults
# 2003-10-31 mvl Add multicall support
# 2004-08-20 mvl Bump minimum supported Python version to 2.1
#
# Copyright (c) 1999-2002 by Secret Labs AB.
# Copyright (c) 1999-2002 by Fredrik Lundh.
#
# info@pythonware.com
# http://www.pythonware.com
#
# --------------------------------------------------------------------
# The JSON-RPC client interface is
#
# Copyright (c) 1999-2002 by Secret Labs AB
# Copyright (c) 1999-2002 by Fredrik Lundh
#
# By obtaining, using, and/or copying this software and/or its
# associated documentation, you agree that you have read, understood,
# and will comply with the following terms and conditions:
#
# Permission to use, copy, modify, and distribute this software and
# its associated documentation for any purpose and without fee is
# hereby granted, provided that the above copyright notice appears in
# all copies, and that both that copyright notice and this permission
# notice appear in supporting documentation, and that the name of
# Secret Labs AB or the author not be used in advertising or publicity
# pertaining to distribution of the software without specific, written
# prior permission.
#
# SECRET LABS AB AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD
# TO THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANT-
# ABILITY AND FITNESS.  IN NO EVENT SHALL SECRET LABS AB OR THE AUTHOR
# BE LIABLE FOR ANY SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY
# DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS,
# WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS
# ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE
# OF THIS SOFTWARE.
# --------------------------------------------------------------------

#
# things to look into some day:

# TODO: sort out True/False/boolean issues for Python 2.3

"""
An JSON-RPC client interface for Python.

The marshalling and response parser code can also be used to
implement JSON-RPC servers.

Exported exceptions:

  Error          Base class for client errors
  ProtocolError  Indicates an HTTP protocol error
  ResponseError  Indicates a broken response package
  Fault          Indicates an JSON-RPC fault package

Exported classes:

  ServerProxy    Represents a logical connection to an JSON-RPC server

  MultiCall      Executor of boxcared jsonrpc requests
  Boolean        boolean wrapper to generate a "boolean" JSON-RPC value
  DateTime       dateTime wrapper for an ISO 8601 string or time tuple or
                 localtime integer value to generate a "dateTime.iso8601"
                 JSON-RPC value
  Binary         binary data wrapper

  Marshaller     Generate an JSON-RPC params chunk from a Python data structure
  Transport      Handles an HTTP transaction to an JSON-RPC server
  SafeTransport  Handles an HTTPS transaction to an JSON-RPC server

Exported constants:

  True
  False

Exported functions:

  boolean        Convert any Python value to an JSON-RPC boolean
  dumps          Convert an argument tuple or a Fault instance to an JSON-RPC
                 request (or response, if the methodresponse option is used).
  loads          Convert an JSON-RPC packet to unmarshalled data plus a method
                 name (None if not present).
"""

import sys, re, string, time, operator, urllib, urllib2, random
from urlparse import urlparse
import traceback
try:
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context
except:
    pass
# JSON library importing
try:
    import cjson as json
except ImportError:
    try:
        import json
    except ImportError:
        try:
            import simplejson as json
        except ImportError:
            raise ImportError(
                'You must have the cjson, json, or simplejson ' +
                'module(s) available.'
            )

from types import *
import socket
import errno
import httplib
try:
    import gzip
except ImportError:
    gzip = None #python can be built without zlib/gzip support

# --------------------------------------------------------------------
# Internal stuff

try:
    unicode
except NameError:
    unicode = None # unicode support not available

try:
    import datetime
except ImportError:
    datetime = None

try:
    _bool_is_builtin = False.__class__.__name__ == "bool"
except NameError:
    _bool_is_builtin = 0

def _decode(data, encoding, is8bit=re.compile("[\x80-\xff]").search):
    # decode non-ascii string (if possible)
    if unicode and encoding and is8bit(data):
        data = unicode(data, encoding)
    return data

def escape(s, replace=string.replace):
    s = replace(s, "&", "&amp;")
    s = replace(s, "<", "&lt;")
    return replace(s, ">", "&gt;",)

if unicode:
    def _stringify(string):
        # convert to 7-bit ascii if possible
        try:
            return string.encode("ascii")
        except UnicodeError:
            return string
else:
    def _stringify(string):
        return string

# used in User-Agent header sent
__version__ = "%s.4.17"  % sys.version.split('.', 1)[0]

# jsonrpc integer limits
MAXINT =  2L**31-1
MININT = -2L**31

# --------------------------------------------------------------------
# Error constants (from Dan Libby's specification at
# http://jsonrpc-epi.sourceforge.net/specs/rfc.fault_codes.php)

# Ranges of errors
PARSE_ERROR       = -32700
SERVER_ERROR      = -32600
APPLICATION_ERROR = -32500
SYSTEM_ERROR      = -32400
TRANSPORT_ERROR   = -32300

# Specific errors
NOT_WELLFORMED_ERROR  = -32700
UNSUPPORTED_ENCODING  = -32701
INVALID_ENCODING_CHAR = -32702
INVALID_JSONRPC        = -32600
METHOD_NOT_FOUND      = -32601
INVALID_METHOD_PARAMS = -32602
INTERNAL_ERROR        = -32603

# --------------------------------------------------------------------
# Exceptions

##
# Base class for all kinds of client-side errors.

class Error(Exception):
    """Base class for client errors."""
    def __str__(self):
        return repr(self)

##
# Indicates an HTTP-level protocol error.  This is raised by the HTTP
# transport layer, if the server returns an error code other than 200
# (OK).
#
# @param url The target URL.
# @param errcode The HTTP error code.
# @param errmsg The HTTP error message.
# @param headers The HTTP header dictionary.

class ProtocolError(Error):
    """Indicates an HTTP protocol error."""
    def __init__(self, url, errcode, errmsg, headers):
        Error.__init__(self)
        self.url = url
        self.errcode = errcode
        self.errmsg = errmsg
        self.headers = headers
    def __repr__(self):
        return (
            "<ProtocolError for %s: %s %s>" %
            (self.url, self.errcode, self.errmsg)
            )

##
# Indicates a broken JSON-RPC response package.  This exception is
# raised by the unmarshalling layer, if the JSON-RPC response is
# malformed.

class ResponseError(Error):
    """Indicates a broken response package."""
    pass

##
# Indicates an JSON-RPC fault response package.  This exception is
# raised by the unmarshalling layer, if the JSON-RPC response contains
# a fault string.  This exception can also used as a class, to
# generate a fault JSON-RPC message.
#
# @param faultCode The JSON-RPC fault code.
# @param faultString The JSON-RPC fault string.

class Fault(Error):
    """Indicates an JSON-RPC fault package."""
    def __init__(self, faultCode, faultString, **extra):
        Error.__init__(self)
        self.faultCode = faultCode
        self.faultString = faultString
    def __repr__(self):
        return (
            "<Fault %s: %s>" %
            (self.faultCode, repr(self.faultString))
            )

# --------------------------------------------------------------------
# Special values


##
# Wrapper for JSON-RPC DateTime values.  This converts a time value to
# the format used by JSON-RPC.
# <p>
# The value can be given as a string in the format
# "yyyymmddThh:mm:ss", as a 9-item time tuple (as returned by
# time.localtime()), or an integer value (as returned by time.time()).
# The wrapper uses time.localtime() to convert an integer to a time
# tuple.
#
# @param value The time, given as an ISO 8601 string, a time
#              tuple, or a integer time value.

def _strftime(value):
    if datetime:
        if isinstance(value, datetime.datetime):
            return "%04d%02d%02dT%02d:%02d:%02d" % (
                value.year, value.month, value.day,
                value.hour, value.minute, value.second)

    if not isinstance(value, (TupleType, time.struct_time)):
        if value == 0:
            value = time.time()
        value = time.localtime(value)

    return "%04d%02d%02dT%02d:%02d:%02d" % value[:6]

class DateTime:
    """DateTime wrapper for an ISO 8601 string or time tuple or
    localtime integer value to generate 'dateTime.iso8601' JSON-RPC
    value.
    """

    def __init__(self, value=0):
        if isinstance(value, StringType):
            self.value = value
        else:
            self.value = _strftime(value)

    def make_comparable(self, other):
        if isinstance(other, DateTime):
            s = self.value
            o = other.value
        elif datetime and isinstance(other, datetime.datetime):
            s = self.value
            o = other.strftime("%Y%m%dT%H:%M:%S")
        elif isinstance(other, (str, unicode)):
            s = self.value
            o = other
        elif hasattr(other, "timetuple"):
            s = self.timetuple()
            o = other.timetuple()
        else:
            otype = (hasattr(other, "__class__")
                     and other.__class__.__name__
                     or type(other))
            raise TypeError("Can't compare %s and %s" %
                            (self.__class__.__name__, otype))
        return s, o

    def __lt__(self, other):
        s, o = self.make_comparable(other)
        return s < o

    def __le__(self, other):
        s, o = self.make_comparable(other)
        return s <= o

    def __gt__(self, other):
        s, o = self.make_comparable(other)
        return s > o

    def __ge__(self, other):
        s, o = self.make_comparable(other)
        return s >= o

    def __eq__(self, other):
        s, o = self.make_comparable(other)
        return s == o

    def __ne__(self, other):
        s, o = self.make_comparable(other)
        return s != o

    def timetuple(self):
        return time.strptime(self.value, "%Y%m%dT%H:%M:%S")

    def __cmp__(self, other):
        s, o = self.make_comparable(other)
        return cmp(s, o)

    ##
    # Get date/time value.
    #
    # @return Date/time value, as an ISO 8601 string.

    def __str__(self):
        return self.value

    def __repr__(self):
        return "<DateTime %s at %x>" % (repr(self.value), id(self))

    def decode(self, data):
        data = str(data)
        self.value = string.strip(data)

    def encode(self, out):
        out.write('"')
        out.write(self.value)
        out.write('"')

def _datetime(data):
    # decode json element contents into a DateTime structure.
    value = DateTime()
    value.decode(data)
    return value

def _datetime_type(data):
    t = time.strptime(data, "%Y%m%dT%H:%M:%S")
    return datetime.datetime(*tuple(t)[:6])

##
# Wrapper for binary data.  This can be used to transport any kind
# of binary data over JSON-RPC, using BASE64 encoding.
#
# @param data An 8-bit string containing arbitrary data.

import base64
try:
    import cStringIO as StringIO
except ImportError:
    import StringIO

class Binary:
    """Wrapper for binary data."""

    def __init__(self, data=None):
        self.data = data

    ##
    # Get buffer contents.
    #
    # @return Buffer contents, as an 8-bit string.

    def __str__(self):
        return self.data or ""

    def __cmp__(self, other):
        if isinstance(other, Binary):
            other = other.data
        return cmp(self.data, other)

    def decode(self, data):
        self.data = base64.decodestring(data)

    def encode(self, out=None):
        if out:
            #out.write('"')
            base64.encode(StringIO.StringIO(self.data), out)
            #out.write('"')
        else:
            return base64.b64encode(self.data)

def _binary(data):
    # decode json element contents into a Binary structure
    value = Binary()
    value.decode(data)
    return value

WRAPPERS = (DateTime, Binary)
if not _bool_is_builtin:
    WRAPPERS = WRAPPERS + (Boolean,)


## Multicall support
#

class _MultiCallMethod:
    # some lesser magic to store calls made to a MultiCall object
    # for batch execution
    def __init__(self, call_list, name):
        self.__call_list = call_list
        self.__name = name
    def __getattr__(self, name):
        return _MultiCallMethod(self.__call_list, "%s.%s" % (self.__name, name))
    def __call__(self, *args, **kwargs):
        self.__call_list.append((self.__name, args, kwargs))

class MultiCallIterator:
    """Iterates over the results of a multicall. Exceptions are
    raised in response to jsonrpc faults."""
    _type_dict = type({})
    _type_list = type([])

    def __init__(self, results):
        self.results = results

    def __getitem__(self, i):
        item = self.results[i]
        if type(item) == self._type_dict:
            if "error" in item:
                return Fault(*item.pop("error"))
            else:
                return Fault(item['faultCode'], item['faultString'])
            #raise Fault(item['faultCode'], item['faultString'])
        elif type(item) == self._type_list:
            return item[0]
        else:
            return ValueError("unexpected type in multicall result: %s" % item)
            #raise ValueError,\
            #      "unexpected type in multicall result"

class MultiCall:
    """server -> a object used to boxcar method calls

    server should be a ServerProxy object.

    Methods can be added to the MultiCall using normal
    method call syntax e.g.:

    multicall = MultiCall(server_proxy)
    multicall.add(2,3)
    multicall.get_address("Guido")

    To execute the multicall, call the MultiCall object e.g.:

    add_result, address = multicall()
    """

    def __init__(self, server):
        self.__server = server
        self.__call_list = []

    def __repr__(self):
        return "<MultiCall at %x>" % id(self)

    __str__ = __repr__

    def __getattr__(self, name):
        return _MultiCallMethod(self.__call_list, name)

    def __call__(self):
        marshalled_list = []
        #for name, params, kwargs in self.__call_list:
        while self.__call_list:
            name, params, kwargs = self.__call_list.pop(0)
            f = {"method": name}
            if params:
                f["params"] = params
            if kwargs:
                f["kwargs"] = kwargs
            marshalled_list.append(f)
        return MultiCallIterator(self.__server.system.multicall(marshalled_list))

##
# Convert a Python tuple or a Fault instance to an JSON-RPC packet.
#
# @def dumps(params, **options)
# @param params A tuple or Fault instance.
# @keyparam methodname If given, create a methodCall request for
#     this method name.
# @keyparam methodresponse If given, create a methodResponse packet.
#     If used with a tuple, the tuple must be a singleton (that is,
#     it must contain exactly one element).
# @keyparam encoding The packet encoding.
# @return A string containing marshalled data.

import decimal
class ExtJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Binary):
            return {'__binary__': obj.encode()}
        elif isinstance(obj, decimal.Decimal):
            return float(obj)
        elif isinstance(obj, datetime.datetime):
            return str(obj)
        elif isinstance(obj, datetime.date):
            return str(obj)
        elif isinstance(obj, set):
            return list(obj)
        return json.JSONEncoder.default(self, obj)

def dumps(params, kwargs=None, methodname=None, methodresponse=None, encoding=None,
          allow_none=1):
    """data [,options] -> marshalled data

    Convert an argument tuple or a Fault instance to an JSON-RPC
    request (or response, if the methodresponse option is used).

    In addition to the data object, the following options can be given
    as keyword arguments:

        methodname: the method name for a methodCall packet

        methodresponse: true to create a methodResponse packet.
        If this option is used with a tuple, the tuple must be
        a singleton (i.e. it can contain only one element).

        encoding: the packet encoding (default is UTF-8)

    All 8-bit strings in the data structure are assumed to use the
    packet encoding.  Unicode strings are automatically converted,
    where necessary.
    """
    #print "call:", params, kwargs, methodname
    if not encoding:
        encoding = "utf-8"

    error = None
    if isinstance(params, Fault):
        methodresponse = 1
        error = [params.faultCode, params.faultString]

    kwdata = None
    if error is None:
        try:
            data = json.dumps(params, ensure_ascii=False, cls=ExtJSONEncoder)
            try: data = data.encode(encoding)
            except: pass
        except:
            data = json.dumps(params, ensure_ascii=True, cls=ExtJSONEncoder)
        if kwargs:
            try:
                kwdata = json.dumps(kwargs, ensure_ascii=False, cls=ExtJSONEncoder)
                try: kwdata = kwdata.encode(encoding)
                except: pass
            except:
                kwdata = json.dumps(kwargs, ensure_ascii=True, cls=ExtJSONEncoder)
    else:
        try:
            data = json.dumps(error, ensure_ascii=False, cls=ExtJSONEncoder)
            try: data = data.encode(encoding)
            except: pass
        except:
            data = json.dumps(error, ensure_ascii=True, cls=ExtJSONEncoder)

    # standard JSON-RPC wrappings
    if methodname:
        data = '{"method": %s%s%s}' % (
            json.dumps(methodname, ensure_ascii=False, cls=ExtJSONEncoder).encode(encoding),
            ',\n "params": {0}'.format(data) if params else '',
            ',\n "kwargs": {0}\n'.format(kwdata) if kwargs else ''
        )
    elif methodresponse:
        # a method response, or a fault structure
        data = "{%s: %s}" % ('"result"' if error is None else '"error"', data)
    return data  # return as is

##
# Convert an JSON-RPC packet to a Python object.  If the JSON-RPC packet
# represents a fault condition, this function raises a Fault exception.
#
# @param data An JSON-RPC packet, given as an 8-bit string.
# @return A tuple containing the unpacked data, and the method name
#     (None if not present).
# @see Fault


def loads(data, use_datetime=0):
    """data -> unmarshalled data, method name

    Convert an JSON-RPC packet to unmarshalled data plus a method
    name (None if not present).

    If the JSON-RPC packet represents a fault condition, this function
    raises a Fault exception.
    """
    r = json.loads(data, object_hook=_object_hook)
    if "method" in r:
        params = r.pop("params") if "params" in r else []
        kwargs = r.pop("kwargs") if "kwargs" in r else {}
        return params, kwargs, r['method']
    elif "result" in r:
        return r.pop("result"), {}, None
    else:
        raise Fault(*r.pop("error"))

def _object_hook(obj):
    if '__binary__' in obj:
        return _binary(obj.pop('__binary__'))
    else:
        return obj

##
# Encode a string using the gzip content encoding such as specified by the
# Content-Encoding: gzip
# in the HTTP header, as described in RFC 1952
#
# @param data the unencoded data
# @return the encoded data

def gzip_encode(data):
    """data -> gzip encoded data

    Encode data using the gzip content encoding as described in RFC 1952
    """
    if not gzip:
        raise NotImplementedError
    f = StringIO.StringIO()
    gzf = gzip.GzipFile(mode="wb", fileobj=f, compresslevel=1)
    if data.__class__.__name__ in ("generator", "list", "tuple"):
        for row in data:
            gzf.write(row)
    elif hasattr(data, "read"):
        part = data.read(4096)
        while part:
            gzf.write(part)
            part = data.read(4096)
    else:
        gzf.write(data)
    gzf.close()
    encoded = f.getvalue()
    f.close()
    return encoded

##
# Decode a string using the gzip content encoding such as specified by the
# Content-Encoding: gzip
# in the HTTP header, as described in RFC 1952
#
# @param data The encoded data
# @return the unencoded data
# @raises ValueError if data is not correctly coded.

def gzip_decode(data):
    """gzip encoded data -> unencoded data

    Decode data using the gzip content encoding as described in RFC 1952
    """
    if not gzip:
        raise NotImplementedError
    f = StringIO.StringIO(data)
    gzf = gzip.GzipFile(mode="rb", fileobj=f)
    try:
        decoded = gzf.read()
    except IOError:
        raise ValueError("invalid data")
    f.close()
    gzf.close()
    return decoded

##
# Return a decoded file-like object for the gzip encoding
# as described in RFC 1952.
#
# @param response A stream supporting a read() method
# @return a file-like object that the decoded data can be read() from

class GzipDecodedResponse(gzip.GzipFile if gzip else object):
    """a file-like object to decode a response encoded with the gzip
    method, as described in RFC 1952.
    """
    def __init__(self, response):
        #response doesn't support tell() and read(), required by
        #GzipFile
        if not gzip:
            raise NotImplementedError
        self.stringio = StringIO.StringIO(response.read())
        gzip.GzipFile.__init__(self, mode="rb", fileobj=self.stringio)

    def close(self):
        gzip.GzipFile.close(self)
        self.stringio.close()


# --------------------------------------------------------------------
# request dispatcher

class _Method:
    # some magic to bind an JSON-RPC method to an RPC server.
    # supports "nested" methods (e.g. examples.getStateName)
    def __init__(self, send, name):
        self.__send = send
        self.__name = name
    def __getattr__(self, name):
        return _Method(self.__send, "%s.%s" % (self.__name, name))
    def __call__(self, *args, **kwargs):
        return self.__send(self.__name, args, kwargs)

##
# Standard transport class for JSON-RPC over HTTP.
# <p>
# You can create custom transports by subclassing this method, and
# overriding selected methods.

class Transport:
    """Handles an HTTP transaction to an JSON-RPC server."""

    # client identifier (may be overridden)
    user_agent = "jsonrpclib.py/%s (by www.ms71.org)" % __version__

    #if true, we'll request gzip encoding
    accept_gzip_encoding = True

    # if positive, encode request using gzip if it exceeds this threshold
    # note that many server will get confused, so only use it if you know
    # that they can decode such a request
    encode_threshold = 1400  # a common MTU


    def __init__(self, use_datetime=0, api_key="", host_name=""):
        self._use_datetime = use_datetime
        self._connection = (None, None)
        self._extra_headers = []
        self._api_key = api_key
        self._host_name = host_name
        self._timeout = socket._GLOBAL_DEFAULT_TIMEOUT
        self._http_method = 'POST'
        self._api_headers = None
        self._hosts301 = {}

    ##
    # Send a complete request, and parse the response.
    # Retry request if a cached connection has disconnected.
    #
    # @param host Target host.
    # @param handler Target PRC handler.
    # @param request_body JSON-RPC request body.
    # @param verbose Debugging flag.
    # @return Parsed response.

    def request(self, host, handler, request_body, verbose=0):
        #retry request once if cached connection has gone cold
        for i in (0, 1):
            try:
                return self.single_request(host, handler, request_body, verbose)
            except socket.error, e:
                if i or e.errno not in (errno.ECONNRESET, errno.ECONNABORTED, errno.EPIPE):
                    raise
            except httplib.BadStatusLine: #close after we sent request
                if i:
                    raise

    ##
    # Send a complete request, and parse the response.
    #
    # @param host Target host.
    # @param handler Target PRC handler.
    # @param request_body JSON-RPC request body.
    # @param verbose Debugging flag.
    # @return Parsed response.

    def single_request(self, host, handler, request_body, verbose=0):
        # issue JSON-RPC request

        h = self.make_connection(host)
        if verbose:
            h.set_debuglevel(1)

        try:
            self.send_request(h, handler, request_body)
            self.send_host(h, host)
            self.send_user_agent(h)
            self.send_content(h, request_body)
            response = h.getresponse(buffering=True)

            location = response.getheader("location", "")
            if location:
                #print(333, location)
                #localhost:4005 /db/query b'["select * from foo", "select * from foo1", "select 1, 2, 3 union all select \'\\u041f\\u0440\\u0438\\u0432\\u0435\\u0442\', \'\\u041c\\u0438\\u0440\', \'!\'"]' False
                #print(333, host, handler, request_body, verbose)
                _up = urlparse(location)
                host2 = _up.netloc
                fg2 = 'https' == _up.scheme
                #print(333, host2, handler, request_body, verbose)
                #sys.exit(0)
                if host2:
                    fg1 = 'SafeTransport' == self.__class__.__name__
                    if fg1 != fg2:
                        chost, self._extra_headers, x509 = self.get_host_info(host2)
                        if fg1:
                            self._connection = host2, httplib.HTTPConnection(chost, timeout=self._timeout)
                        else:
                            kw = x509 or {}
                            kw['timeout'] = self._timeout
                            self._connection = host2, httplib.HTTPSConnection(chost, None, **kw)
                        h = self._connection[1]
                    else:
                        h = self.make_connection(host2)

                    if verbose:
                        h.set_debuglevel(1)
                    self.send_request(h, handler, request_body)
                    self.send_host(h, host2)
                    self.send_user_agent(h)
                    self.send_content(h, request_body)
                    response = h.getresponse(buffering=True)
                    if host2 in self._hosts301:
                        del self._hosts301[host2]
                    self._hosts301[host] = host2
                else:
                    return (location,)
            if response.status == 200:
                self.verbose = verbose
                return self.parse_response(response)
        except Fault:
            raise
        except Exception:
            # All unexpected errors leave connection in
            # a strange state, so we clear it.
            self.close()
            raise

        #discard any response data and raise exception
        #if (response.getheader("content-length", 0)):
        body = response.read()
        if body.startswith(b"\x1f\x8b\x08\x00"):
            body = gzip_decode(body).decode()
        else:
            body = body.decode()

        if body:
            raise ProtocolError(
                host + handler,
                response.status, response.reason + '\n' + body,
                response.msg,
            )
        else:
            raise ProtocolError(
                host + handler,
                response.status, response.reason,
                response.msg,
            )


    ##
    # Get authorization info from host parameter
    # Host may be a string, or a (host, x509-dict) tuple; if a string,
    # it is checked for a "user:pw@host" format, and a "Basic
    # Authentication" header is added if appropriate.
    #
    # @param host Host descriptor (URL or (URL, x509 info) tuple).
    # @return A 3-tuple containing (actual host, extra headers,
    #     x509 info).  The header and x509 fields may be None.

    def get_host_info(self, host):

        x509 = {}
        if isinstance(host, TupleType):
            host, x509 = host

        auth, host = urllib.splituser(host)

        if auth:
            auth = base64.encodestring(urllib.unquote(auth))
            auth = string.join(string.split(auth), "") # get rid of whitespace
            extra_headers = [
                ("Authorization", "Basic " + auth)
                ]
        else:
            extra_headers = None

        return host, extra_headers, x509

    ##
    # Connect to server.
    #
    # @param host Target host.
    # @return A connection handle.

    def make_connection(self, host):
        #return an existing connection if possible.  This allows
        #HTTP/1.1 keep-alive.
        if host in self._hosts301:
            #print('301', host, self._hosts301[host])
            host = self._hosts301[host]
        if self._connection and host == self._connection[0]:
            return self._connection[1]

        # create a HTTP connection object from a host descriptor
        chost, self._extra_headers, x509 = self.get_host_info(host)
        #store the host argument along with the connection object
        self._connection = host, httplib.HTTPConnection(chost, timeout=self._timeout)
        return self._connection[1]

    ##
    # Clear any cached connection object.
    # Used in the event of socket errors.
    #
    def close(self):
        if self._connection[1]:
            self._connection[1].close()
            self._connection = (None, None)

    ##
    # Send request header.
    #
    # @param connection Connection handle.
    # @param handler Target RPC handler.
    # @param request_body JSON-RPC body.

    def send_request(self, connection, handler, request_body):
        if (self.accept_gzip_encoding and gzip):
            connection.putrequest(self._http_method if request_body else "GET", handler, skip_host=self._host_name, skip_accept_encoding=True)
            if self._host_name:
                connection.putheader("Host", self._host_name)
            connection.putheader("Accept-Encoding", "gzip")
        else:
            connection.putrequest(self._http_method if request_body else "GET", handler, skip_host=self._host_name)
            if self._host_name:
                connection.putheader("Host", self._host_name)
        if self._api_key:
            connection.putheader("X-API-Key", self._api_key)

        #if self._api_headers:
        #    od = OrderedDict(headers + self._api_headers)
        #    for key, val in od.items():
        #        #print(f"header2: {key} = {val}")
        #        connection.putheader(key, val)
        #else:

    ##
    # Send host name.
    #
    # @param connection Connection handle.
    # @param host Host name.
    #
    # Note: This function doesn't actually add the "Host"
    # header anymore, it is done as part of the connection.putrequest() in
    # send_request() above.

    def send_host(self, connection, host):
        extra_headers = self._extra_headers
        if extra_headers:
            if isinstance(extra_headers, DictType):
                extra_headers = extra_headers.items()
            for key, value in extra_headers:
                connection.putheader(key, value)

    ##
    # Send user-agent identifier.
    #
    # @param connection Connection handle.

    def send_user_agent(self, connection):
        connection.putheader("User-Agent", self.user_agent)

    ##
    # Send request body.
    #
    # @param connection Connection handle.
    # @param request_body JSON-RPC request body.

    def send_content(self, connection, request_body):
        connection.putheader("Content-Type", "application/json")

        #optionally encode the request
        if gzip and (request_body.__class__.__name__ in ("generator", "list", "tuple") or (self.encode_threshold is not None and self.encode_threshold < len(request_body))):
            connection.putheader("Content-Encoding", "gzip")
            request_body = gzip_encode(request_body)

        connection.putheader("Content-Length", str(len(request_body)))
        connection.endheaders(request_body)

    ##
    # Parse response.
    #
    # @param file Stream.
    # @return Response tuple and target method.

    def parse_response(self, response):
        # read response data from httpresponse, and parse it
        fg_text = False
        # Check for new http response object, else it is a file object
        if hasattr(response, 'getheader'):
            #print dir(response)
            #print response.getheaders()
            if response.getheader("Content-Encoding", "") == "gzip":
                stream = GzipDecodedResponse(response)
            else:
                stream = response
            if response.getheader("Content-Type", "").startswith("text"):
                fg_text = True
        else:
            stream = response
        if fg_text:
            if hasattr(stream, 'msg'):
                r = stream.read().splitlines()
            else:
                r = stream.readlines()
        else:
            #r = json.load(stream)
            r = json.load(stream, object_hook=_object_hook)

        if self.verbose:
            print r
        if stream is not response:
            stream.close()

        if fg_text or 'GET' == self._http_method:
            return r

        if 'method' in r:
            params = r.pop("params") if "params" in r else []
            kwargs = r.pop("kwargs") if "kwargs" in r else {}
            if kwargs:
                return [params, kwargs]
            else:
                return params
        elif 'result' in r:
            return r.pop('result')
        elif 'error' in r:
            raise Fault(*r.pop('error'))
        else:
            return r

##
# Standard transport class for JSON-RPC over HTTPS.

class SafeTransport(Transport):
    """Handles an HTTPS transaction to an JSON-RPC server."""

    # FIXME: mostly untested

    def make_connection(self, host):
        if host in self._hosts301:
            #print('301', host, self._hosts301[host])
            host = self._hosts301[host]
        if self._connection and host == self._connection[0]:
            return self._connection[1]
        # create a HTTPS connection object from a host descriptor
        # host may be a string, or a (host, x509-dict) tuple
        try:
            HTTPS = httplib.HTTPSConnection
        except AttributeError:
            raise NotImplementedError(
                "your version of httplib doesn't support HTTPS"
                )
        else:
            chost, self._extra_headers, x509 = self.get_host_info(host)
            kw = x509 or {}
            kw['timeout'] = self._timeout
            self._connection = host, HTTPS(chost, None, **kw)
            return self._connection[1]

##
# Standard server proxy.  This class establishes a virtual connection
# to an JSON-RPC server.
# <p>
# This class is available as ServerProxy and Server.  New code should
# use ServerProxy, to avoid confusion.
#
# @def ServerProxy(uri, **options)
# @param uri The connection point on the server.
# @keyparam transport A transport factory, compatible with the
#    standard transport class.
# @keyparam encoding The default encoding used for 8-bit strings
#    (default is UTF-8).
# @keyparam verbose Use a true value to enable debugging output.
#    (printed to standard output).
# @see Transport

class ServerProxy:
    """uri [,options] -> a logical connection to an JSON-RPC server

    uri is the connection point on the server, given as
    scheme://host/target.

    The standard implementation always supports the "http" scheme.  If
    SSL socket support is available (Python 2.0), it also supports
    "https".

    If the target part and the slash preceding it are both omitted,
    "/RPC2" is assumed.

    The following options can be given as keyword arguments:

        transport: a transport factory
        encoding: the request encoding (default is UTF-8)

    All 8-bit strings passed to the server proxy are assumed to use
    the given encoding.
    """

    def __init__(self, uri, transport=None, encoding=None, verbose=0,
                 allow_none=0, use_datetime=0, api_key="", host_name=""):
        # establish a "logical" server connection

        if isinstance(uri, unicode):
            uri = uri.encode('ISO-8859-1')

        # get the url
        type, uri = urllib.splittype(uri)
        if type not in ("http", "https"):
            raise IOError, "unsupported JSON-RPC protocol"
        self.__host, self.__handler = urllib.splithost(uri)
        if not self.__handler:
            self.__handler = "/RPC2"

        if transport is None:
            if type == "https":
                transport = SafeTransport(use_datetime=use_datetime, api_key=api_key, host_name=host_name)
            else:
                transport = Transport(use_datetime=use_datetime, api_key=api_key, host_name=host_name)
        self.__transport = transport

        self.__encoding = encoding
        self.__verbose = verbose
        self.__allow_none = allow_none

    def __close(self):
        self.__transport.close()

    def __request(self, methodname, params, kwargs):
        # call a method on the remote server

        request = dumps(params, kwargs, methodname, encoding=self.__encoding,
                        allow_none=self.__allow_none)

        response = self.__transport.request(
            self.__host,
            self.__handler,
            request,
            verbose=self.__verbose
            )
        if len(response) == 1:
            response = response[0]

        return response

    def __repr__(self):
        return (
            "<ServerProxy for %s%s>" %
            (self.__host, self.__handler)
            )

    __str__ = __repr__

    def __getattr__(self, name):
        # magic method dispatcher
        return _Method(self.__request, name)

    # note: to call a remote object with an non-standard name, use
    # result getattr(server, "strange-python-name")(args)

    def __call__(self, attr):
        """A workaround to get special attributes on the ServerProxy
           without interfering with the magic __getattr__
        """
        if attr == "close":
            return self.__close
        elif attr == "transport":
            return self.__transport
        elif attr == "request":
            #return lambda request: self.__transport.request(
            #    self.__host,
            #    self.__handler,
            #    request,
            #    verbose=self.__verbose
            #)
            def _f(request, handler=None, headers=None):
                if handler:
                    self.__handler = handler
                self.__transport._api_headers = headers
                return self.__transport.request(
                    self.__host,
                    self.__handler,
                    request,
                    verbose=self.__verbose
                )
            return _f
        elif attr == "handler":
            def _f(handler, headers=None):
                self.__handler = handler
                self.__transport._api_headers = headers
                return self
            return _f
        raise AttributeError("Attribute %r not found" % (attr,))


def sse(url, payload=None, last_event_id=None, api_key=None, host_name=None, fg_ping=False):
    """
    g = sse(url)
    for i, [event, data, event_id] in enumerate(g):
        i += 1
        print event, data, event_id
        if i >= 20:
            g.close()
    """
    headers = {
        "Connection": "keep-alive",
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
    }
    if host_name:
        headers["Host"] = host_name
    if api_key:
        headers["X-API-Key"] = api_key
    retry = 2000
    _fg = True
    socket.setdefaulttimeout(5)
    while _fg:
        ff = None
        f = None
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        try:
            event, data, event_id = "", None, ""
            #f = urllib.request.urlopen(urllib.request.Request(url, data=payload, headers=headers), timeout=5+random.random())
            #for v in f:
            #print('-------')
            #sys.stdout.flush()
            ff = urllib2.urlopen(urllib2.Request(url, data=payload, headers=headers), timeout=5+random.random())
            #print('+++++++')
            #sys.stdout.flush()
            #print(1, url)
            #print(2, ff.geturl())
            f = ff.fp._sock.fp
            for v in f:
                v = v.decode()
                #print('in:', repr(v))
                if '' == v:
                    break
                elif '\n' == v:
                    if data or event_id:
                        if event:
                            yield [event, data, event_id]
                        else:
                            yield ["message", data, event_id]
                    elif event:
                        yield [event, data, event_id]
                    event, data, event_id = "", None, ""
                else:
                    try:
                        k, v = v.split(':', 1)
                    except:
                        break
                    v = v[1:-1] if v and v[0] == ' ' else v[:-1]
                    #print('k:', k, 'v:', v)
                    if not k and fg_ping:
                        event = 'ping'
                    elif "chunk" == k:
                        if data:
                            data.append(f.read(int(v)))
                        else:
                            data = [f.read(int(v)),]
                        f.readline()
                    elif "event" == k:
                        event = v
                    elif "data" == k:
                        if data:
                            data.append(v)
                        else:
                            data = [v,]
                    elif "id" == k:
                        event_id = v
                        last_event_id = v
                    elif "retry" == k:
                        retry = int(v)
        except (KeyboardInterrupt, SystemExit) as e:
            _fg = False
            #print('=======')
            #sys.stdout.flush()
            break
        except Exception as e:
            yield ["error", e, last_event_id]
            #yield ["error", traceback.format_exc(), last_event_id]
        finally:
            if ff:
                try: ff.close()
                except: pass
        try: time.sleep(retry / 1000.0 + random.random())
        except: break

# compatibility

Server = ServerProxy

# --------------------------------------------------------------------
# test code

if __name__ == "__main__":

    # simple test program (from the JSON-RPC specification)

    server = ServerProxy("http://localhost:8602", verbose=False) # local server
    #server = ServerProxy("http://time.jsonrpc.com/RPC2")

    print server
    """
    #print server.system.listMethods()
    #sys.exit(0)
    print server.pow(2, 4)
    print server.add(2, 3)
    #sys.exit(0)
    """
    print server.echo(u"Привет, Мир!", add=1, new="22")
    print
    sys.stdout.flush()

    multi = MultiCall(server)
    multi.echo(u"Привет, Мир1")
    multi.pow(2, 4)
    multi.add(2, 3)
    multi.echo(u"Привет, Мир2", add=1, new="22")
    try:
        for response in multi():
            print response
    except Error, v:
        print "ERROR", v
    print
    print server.echo(u"Привет, Мир3")
    print
    sys.stdout.flush()
