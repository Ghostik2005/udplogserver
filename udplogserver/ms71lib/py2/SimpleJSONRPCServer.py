# coding: utf-8
r"""Simple JSON-RPC Server.

This module can be used to create simple JSON-RPC servers
by creating a server and either installing functions, a
class instance, or by extending the SimpleJSONRPCServer
class.

It can also be used to handle JSON-RPC requests in a CGI
environment using CGIJSONRPCRequestHandler.

A list of possible usage patterns follows:

1. Install functions:

server = SimpleJSONRPCServer(("localhost", 8000))
server.register_function(pow)
server.register_function(lambda x,y: x+y, 'add')
server.serve_forever()

2. Install an instance:

class MyFuncs:
    def __init__(self):
        # make all of the string functions available through
        # string.func_name
        import string
        self.string = string
    def _listMethods(self):
        # implement this method so that system.listMethods
        # knows to advertise the strings methods
        return list_public_methods(self) + \
                ['string.' + method for method in list_public_methods(self.string)]
    def pow(self, x, y): return pow(x, y)
    def add(self, x, y) : return x + y

server = SimpleJSONRPCServer(("localhost", 8000))
server.register_introspection_functions()
server.register_instance(MyFuncs())
server.serve_forever()

3. Install an instance with custom dispatch method:

class Math:
    def _listMethods(self):
        # this method must be present for system.listMethods
        # to work
        return ['add', 'pow']
    def _methodHelp(self, method):
        # this method must be present for system.methodHelp
        # to work
        if method == 'add':
            return "add(2,3) => 5"
        elif method == 'pow':
            return "pow(x, y[, z]) => number"
        else:
            # By convention, return empty
            # string if no help is available
            return ""
    def _dispatch(self, method, params):
        if method == 'pow':
            return pow(*params)
        elif method == 'add':
            return params[0] + params[1]
        else:
            raise 'bad method'

server = SimpleJSONRPCServer(("localhost", 8000))
server.register_introspection_functions()
server.register_instance(Math())
server.serve_forever()

4. Subclass SimpleJSONRPCServer:

class MathServer(SimpleJSONRPCServer):
    def _dispatch(self, method, params):
        try:
            # We are forcing the 'export_' prefix on methods that are
            # callable through JSON-RPC to prevent potential security
            # problems
            func = getattr(self, 'export_' + method)
        except AttributeError:
            raise Exception('method "%s" is not supported' % method)
        else:
            return func(*params)

    def export_add(self, x, y):
        return x + y

server = MathServer(("localhost", 8000))
server.serve_forever()

5. CGI script:

server = CGIJSONRPCRequestHandler()
server.register_function(pow)
server.handle_request()
"""

__version__ = "2.2"

# Written by Brian Quinlan (brian@sweetapp.com).
# Based on code written by Fredrik Lundh.

import jsonrpclib
from jsonrpclib import Fault
import SocketServer
import BaseHTTPServer
import sys
import os
import traceback
import re
try:
    import fcntl
except ImportError:
    fcntl = None
import urllib
import base64, uuid, threading

def resolve_dotted_attribute(obj, attr, allow_dotted_names=True):
    """resolve_dotted_attribute(a, 'b.c.d') => a.b.c.d

    Resolves a dotted attribute name to an object.  Raises
    an AttributeError if any attribute in the chain starts with a '_'.

    If the optional allow_dotted_names argument is false, dots are not
    supported and this function operates similar to getattr(obj, attr).
    """
    if hasattr(obj, '_prefix') and obj._prefix and attr.startswith(obj._prefix):
        attr = attr[len(obj._prefix):]

    if allow_dotted_names:
        attrs = attr.split('.')
    else:
        attrs = [attr]

    for i in attrs:
        if i.startswith('_'):
            raise AttributeError(
                'attempt to access private attribute "%s"' % i
                )
        else:
            obj = getattr(obj,i)
    return obj

def list_public_methods(obj):
    """Returns a list of attribute strings, found in the specified
    object, which represent callable attributes"""
    if hasattr(obj, '_prefix') and obj._prefix:
        return [obj._prefix + member for member in dir(obj)
                    if not member.startswith('_') and
                        hasattr(getattr(obj, member), '__call__')]
    else:
        return [member for member in dir(obj)
                    if not member.startswith('_') and
                        hasattr(getattr(obj, member), '__call__')]

def remove_duplicates(lst):
    """remove_duplicates([2,2,2,1,3,3]) => [3,1,2]

    Returns a copy of a list without duplicates. Every list
    item must be hashable and the order of the items in the
    resulting list is not defined.
    """
    u = {}
    for x in lst:
        u[x] = 1

    return u.keys()

class SimpleJSONRPCDispatcher:
    """Mix-in class that dispatches JSON-RPC requests.

    This class is used to register JSON-RPC method handlers
    and then to dispatch them. This class doesn't need to be
    instanced directly when used by SimpleJSONRPCServer but it
    can be instanced when used by the MultiPathJSONRPCServer.
    """

    def __init__(self, allow_none=True, encoding=None):
        self.funcs = {}
        self.instance = None
        self.allow_none = allow_none
        self.encoding = encoding
        self._path = "/"

    def register_instance(self, instance, allow_dotted_names=False, prefix=None):
        """Registers an instance to respond to JSON-RPC requests.

        Only one instance can be installed at a time.

        If the registered instance has a _dispatch method then that
        method will be called with the name of the JSON-RPC method and
        its parameters as a tuple
        e.g. instance._dispatch('add',(2,3))

        If the registered instance does not have a _dispatch method
        then the instance will be searched to find a matching method
        and, if found, will be called. Methods beginning with an '_'
        are considered private and will not be called by
        SimpleJSONRPCServer.

        If a registered function matches a JSON-RPC request, then it
        will be called instead of the registered instance.

        If the optional allow_dotted_names argument is true and the
        instance does not have a _dispatch method, method names
        containing dots are supported and resolved, as long as none of
        the name segments start with an '_'.

            *** SECURITY WARNING: ***

            Enabling the allow_dotted_names options allows intruders
            to access your module's global variables and may allow
            intruders to execute arbitrary code on your machine.  Only
            use this option on a secure, closed network.

        """

        self.instance = instance
        self.allow_dotted_names = allow_dotted_names

        if self.instance:
            if prefix:
                self.instance._prefix = prefix
            if hasattr(self.instance, '_path') and self.instance._path:
                self._path = self.instance._path

    def register_function(self, function, name = None):
        """Registers a function to respond to JSON-RPC requests.

        The optional name argument can be used to set a Unicode name
        for the function.
        """

        if name is None:
            name = function.__name__
        self.funcs[name] = function

    def register_introspection_functions(self):
        """Registers the JSON-RPC introspection methods in the system
        namespace.

        see http://jsonrpc.usefulinc.com/doc/reserved.html
        """

        self.funcs.update({'system.listMethods' : self.system_listMethods,
                      'system.methodSignature' : self.system_methodSignature,
                      'system.methodHelp' : self.system_methodHelp})

    def register_multicall_functions(self):
        """Registers the JSON-RPC multicall method in the system
        namespace.

        see http://www.jsonrpc.com/discuss/msgReader$1208"""

        self.funcs.update({'system.multicall' : self.system_multicall})

    _type_function = type(lambda: None)

    def _marshaled_dispatch(self, data, dispatch_method = None, path = None):
        """Dispatches an JSON-RPC method from marshalled (JSON) data.

        JSON-RPC methods are dispatched from the marshalled (JSON) data
        using the _dispatch method and the result is returned as
        marshalled data. For backwards compatibility, a dispatch
        function can be provided as an argument (see comment in
        SimpleJSONRPCRequestHandler.do_POST) but overriding the
        existing method through subclassing is the preferred means
        of changing method dispatch behavior.
        """

        try:
            params, kwargs, method = jsonrpclib.loads(data)
            #print "method:", method

            # generate response
            if dispatch_method is not None:
                response = dispatch_method(method, params, kwargs)
            else:
                response = self._dispatch(method, params, kwargs)
            #print 333, response
            #sys.stdout.flush()
            if isinstance(response, self._type_function):
                return response
            # wrap response in a singleton tuple
            response = (response,)
            response = jsonrpclib.dumps(response, None, methodresponse=1,
                                       allow_none=self.allow_none, encoding=self.encoding)
        except Fault, fault:
            response = jsonrpclib.dumps(fault, None, allow_none=self.allow_none,
                                       encoding=self.encoding)
        except:
            # report exception back to server
            exc_type, exc_value, exc_tb = sys.exc_info()
            if hasattr(self, '_send_traceback_header') and self._send_traceback_header:
                exc_value = str(exc_value) + '\n' + traceback.format_exc()
                print exc_value
                sys.stdout.flush()
            response = jsonrpclib.dumps(
                jsonrpclib.Fault(1, "%s:%s" % (exc_type, exc_value)), None,
                encoding=self.encoding, allow_none=self.allow_none,
                )
        return response

    def system_listMethods(self):
        """system.listMethods() => ['add', 'subtract', 'multiple']

        Returns a list of the methods supported by the server."""

        methods = self.funcs.keys()
        if self.instance is not None:
            # Instance can implement _listMethod to return a list of
            # methods
            if hasattr(self.instance, '_listMethods'):
                methods = remove_duplicates(
                        methods + self.instance._listMethods()
                    )
            # if the instance has a _dispatch method then we
            # don't have enough information to provide a list
            # of methods
            elif not hasattr(self.instance, '_dispatch'):
                methods = remove_duplicates(
                        methods + list_public_methods(self.instance)
                    )
        methods.sort()
        return methods

    def system_methodSignature(self, method_name):
        """system.methodSignature('add') => [double, int, int]

        Returns a list describing the signature of the method. In the
        above example, the add method takes two integers as arguments
        and returns a double result.

        This server does NOT support system.methodSignature."""

        # See http://jsonrpc.usefulinc.com/doc/sysmethodsig.html

        return 'signatures not supported'

    def system_methodHelp(self, method_name):
        """system.methodHelp('add') => "Adds two integers together"

        Returns a string containing documentation for the specified method."""

        method = None
        if method_name in self.funcs:
            method = self.funcs[method_name]
        elif self.instance is not None:
            # Instance can implement _methodHelp to return help for a method
            if hasattr(self.instance, '_methodHelp'):
                return self.instance._methodHelp(method_name)
            # if the instance has a _dispatch method then we
            # don't have enough information to provide help
            elif not hasattr(self.instance, '_dispatch'):
                try:
                    method = resolve_dotted_attribute(
                                self.instance,
                                method_name,
                                self.allow_dotted_names
                                )
                except AttributeError:
                    pass

        # Note that we aren't checking that the method actually
        # be a callable object of some kind
        if method is None:
            return ""
        else:
            import pydoc
            return pydoc.getdoc(method)

    def system_multicall(self, call_list):
        """system.multicall([{'method': 'add', 'params': [2, 2]}, ...]) => \
[[4], ...]

        Allows the caller to package multiple JSON-RPC calls into a single
        request.

        See http://www.jsonrpc.com/discuss/msgReader$1208
        """

        results = []
        system_emit = None  # lambda *a, **kw: None
        for call in call_list:
            method_name = call["method"]
            if "params" in call:
                params = call["params"]
            else:
                params = []
            if "kwargs" in call:
                kwargs = call["kwargs"]
            else:
                kwargs = {}
            id_async = ""
            try:
                # XXX A marshalling error in any response will fail the entire
                # multicall. If someone cares they should fix this.
                if method_name.startswith('async:'):
                    _, id_async, method_name, = method_name.split(':', 2)
                    if not id_async:
                        id_async = base64.urlsafe_b64encode(uuid.uuid4().bytes)
                    #print "method_name:", method_name, id_async, params, kwargs
                    #system.emit#
                    #results.append([id_async,])
                    if "system.emit" == method_name:
                        #u'sse:0:12345678', u'urn:uuid'] {u'url': u'https://application.org/hook', u'X_API_Key': u'mykey'}
                        #print 222
                        #print "call:", call
                        ssekey, eventname = params[0], params[1]
                        _url = kwargs["url"]
                        _api_key = kwargs["api_key"]
                        def system_emit():
                            rpc = jsonrpclib.ServerProxy(_url, api_key=_api_key)
                            def _emit(_id, _res):
                                rpc.ann.emit_async(ssekey, eventname, _id)
                                rpc("close")()
                            return _emit
                    else:
                        #print 111, system_emit
                        if system_emit is None:
                            results.append([None,])
                            f = None
                        else:
                            results.append([id_async,])
                            f = system_emit()
                        #self._system_emit(id_async, method_name, params, kwargs, f)
                        t = threading.Thread(target=self._system_emit, args=[id_async, method_name, params, kwargs, f])
                        t.daemon = True
                        t.start()
                else:
                    results.append([self._dispatch(method_name, params, kwargs)])
            except Fault, fault:
                results.append(
                    {'error': [fault.faultCode, fault.faultString]}
                )
            except:
                exc_type, exc_value, exc_tb = sys.exc_info()
                if hasattr(self, '_send_traceback_header') and self._send_traceback_header:
                    exc_value = str(exc_value) + '\n' + traceback.format_exc()
                results.append(
                    {'error': [1, "%s:%s" % (exc_type, exc_value)]}
                )
        return results

    def _system_emit(self, id_async, method_name, params, kwargs, emit=None):
        #print 111; sys.stdout.flush()
        try:
            _res = self._dispatch(method_name, params, kwargs)
        except Fault, fault:
            _res = {'error': [fault.faultCode, fault.faultString]}
        except:
            exc_type, exc_value, exc_tb = sys.exc_info()
            if hasattr(self, '_send_traceback_header') and self._send_traceback_header:
                exc_value = str(exc_value) + '\n' + traceback.format_exc()
            _res = {'error': [1, "%s:%s" % (exc_type, exc_value)]}
        #print 222; sys.stdout.flush()
        if emit:
            emit(id_async, _res)
        #print 333; sys.stdout.flush()

    def _dispatch(self, method, params, kwargs):
        """Dispatches the JSON-RPC method.

        JSON-RPC calls are forwarded to a registered function that
        matches the called JSON-RPC method name. If no such function
        exists then the call is forwarded to the registered instance,
        if available.

        If the registered instance has a _dispatch method then that
        method will be called with the name of the JSON-RPC method and
        its parameters as a tuple
        e.g. instance._dispatch('add',(2,3))

        If the registered instance does not have a _dispatch method
        then the instance will be searched to find a matching method
        and, if found, will be called.

        Methods beginning with an '_' are considered private and will
        not be called.
        """

        func = None
        try:
            # check to see if a matching function has been registered
            func = self.funcs[method]
        except KeyError:
            if self.instance is not None:
                # check for a _dispatch method
                if hasattr(self.instance, '_dispatch'):
                    return self.instance._dispatch(method, params, kwargs)
                else:
                    # call instance method directly
                    try:
                        func = resolve_dotted_attribute(
                            self.instance,
                            method,
                            self.allow_dotted_names
                            )
                    except AttributeError:
                        pass

        if func is None:
            raise Exception('method "%s" is not supported' % method)
        else:
            result = func(*params, **kwargs)
            return result

class SimpleJSONRPCRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):
    """Simple JSON-RPC request handler class.

    Handles all HTTP POST requests and attempts to decode them as
    JSON-RPC requests.
    """

    # Class attribute listing the accessible path components;
    # paths not on this list will result in a 404 error.
    rpc_paths = ('/', '/RPC2')

    #if not None, encode responses larger than this, if possible
    encode_threshold = 1400 #a common MTU

    #Override form StreamRequestHandler: full buffering of output
    #and no Nagle.
    wbufsize = -1
    disable_nagle_algorithm = True

    # a re to match a gzip Accept-Encoding
    aepattern = re.compile(r"""
                            \s* ([^\s;]+) \s*            #content-coding
                            (;\s* q \s*=\s* ([0-9\.]+))? #q
                            """, re.VERBOSE | re.IGNORECASE)

    def accept_encodings(self):
        r = {}
        ae = self.headers.get("Accept-Encoding", "")
        for e in ae.split(","):
            match = self.aepattern.match(e)
            if match:
                v = match.group(3)
                v = float(v) if v else 1.0
                r[match.group(1)] = v
        return r

    def is_rpc_path_valid(self):
        if self.rpc_paths:
            return self.path in self.rpc_paths
        else:
            # If .rpc_paths is empty, just assume all paths are legal
            return True

    def do_GET(self):
        """
        self.send_response(501)
        response = 'Not Implemented (%s): %s' %  (self.path, self.rpc_paths)
        self.send_header("Content-type", "text/plain")
        self.send_header("Content-length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)
        """
        pq = self.path.split('?', 1)
        if pq[0] and pq[0][-1] == '/':
            pq[0] = pq[0][:-1]
        path = pq[0].split('/')
        method =  path.pop(-1)
        path = '/'.join(path)
        if self.rpc_paths:
            if not (path in self.rpc_paths):
                self.report_404()
                return
        #print "1111", path, method
        try:
            params = []
            kwargs = {}
            if len(pq) > 1:
                for kv in pq.pop(1).split('&'):
                    kv = kv.split('=', 1)
                    if len(kv) > 1:
                        kwargs[urllib.unquote(kv[0]).strip()] = urllib.unquote(kv[1]).strip()
                    else:
                        params.append(urllib.unquote(kv[0]).strip())

            data = jsonrpclib.dumps(params, kwargs, method, methodresponse=None, encoding=None, allow_none=1)
            #print self.server
            #print data
            response = self.server._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None), path
                )
        except Exception, e: # This should only happen if the module is buggy
            # internal error, report as HTTP server error
            self.send_response(500)

            # Send information about the exception if requested
            if hasattr(self.server, '_send_traceback_header') and \
                    self.server._send_traceback_header:
                self.send_header("X-exception", str(e))
                self.send_header("X-traceback", traceback.format_exc())

            self.send_header("Cache-control", "no-cache")
            self.send_header("Content-length", "0")
            self.end_headers()
        else:
            # got a valid JSON RPC response
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=UTF-8")
            self.send_header("Cache-control", "no-cache")
            if self.encode_threshold is not None:
                if len(response) > self.encode_threshold:
                    q = self.accept_encodings().get("gzip", 0)
                    if q:
                        try:
                            response = jsonrpclib.gzip_encode(response)
                            self.send_header("Content-Encoding", "gzip")
                        except NotImplementedError:
                            pass
            self.send_header("Content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    def do_POST(self):
        """Handles the HTTP POST request.

        Attempts to interpret all HTTP POST requests as JSON-RPC calls,
        which are forwarded to the server's _dispatch method for handling.
        """

        # Check that the path is legal
        if not self.is_rpc_path_valid():
            self.report_404()
            return

        try:
            # Get arguments by reading body of request.
            # We read this in chunks to avoid straining
            # socket.read(); around the 10 or 15Mb mark, some platforms
            # begin to have problems (bug #792570).
            max_chunk_size = 10*1024*1024
            size_remaining = int(self.headers["content-length"])
            L = []
            while size_remaining:
                chunk_size = min(size_remaining, max_chunk_size)
                chunk = self.rfile.read(chunk_size)
                if not chunk:
                    break
                L.append(chunk)
                size_remaining -= len(L[-1])
            data = ''.join(L)

            data = self.decode_request_content(data)
            if data is None:
                return #response has been sent

            # In previous versions of SimpleJSONRPCServer, _dispatch
            # could be overridden in this class, instead of in
            # SimpleJSONRPCDispatcher. To maintain backwards compatibility,
            # check to see if a subclass implements _dispatch and dispatch
            # using that method if present.
            response = self.server._marshaled_dispatch(
                    data, getattr(self, '_dispatch', None), self.path
                )
        except Exception, e: # This should only happen if the module is buggy
            # internal error, report as HTTP server error
            self.send_response(500)

            # Send information about the exception if requested
            if hasattr(self.server, '_send_traceback_header') and \
                    self.server._send_traceback_header:
                self.send_header("X-exception", str(e))
                self.send_header("X-traceback", traceback.format_exc())

            self.send_header("Content-length", "0")
            self.end_headers()
        else:
            # got a valid JSON RPC response
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            if self.encode_threshold is not None:
                if len(response) > self.encode_threshold:
                    q = self.accept_encodings().get("gzip", 0)
                    if q:
                        try:
                            response = jsonrpclib.gzip_encode(response)
                            self.send_header("Content-Encoding", "gzip")
                        except NotImplementedError:
                            pass
            self.send_header("Content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    def decode_request_content(self, data):
        #support gzip encoding of request
        encoding = self.headers.get("content-encoding", "identity").lower()
        if encoding == "identity":
            return data
        if encoding == "gzip":
            try:
                return jsonrpclib.gzip_decode(data)
            except NotImplementedError:
                self.send_response(501, "encoding %r not supported" % encoding)
            except ValueError:
                self.send_response(400, "error decoding gzip content")
        else:
            self.send_response(501, "encoding %r not supported" % encoding)
        self.send_header("Content-length", "0")
        self.end_headers()

    def report_404 (self):
            # Report a 404 error
        self.send_response(404)
        response = 'No such page'
        self.send_header("Content-type", "text/plain")
        self.send_header("Content-length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_request(self, code='-', size='-'):
        """Selectively log an accepted request."""

        if self.server.logRequests:
            BaseHTTPServer.BaseHTTPRequestHandler.log_request(self, code, size)

    server_version = "JsonRPC/" + __version__


class SimpleJSONRPCServer(SocketServer.TCPServer,
                         SimpleJSONRPCDispatcher):
    """Simple JSON-RPC server.

    Simple JSON-RPC server that allows functions and a single instance
    to be installed to handle requests. The default implementation
    attempts to dispatch JSON-RPC calls to the functions or instance
    installed in the server. Override the _dispatch method inhereted
    from SimpleJSONRPCDispatcher to change this behavior.
    """

    allow_reuse_address = True

    # Warning: this is for debugging purposes only! Never set this to True in
    # production code, as will be sending out sensitive information (exception
    # and stack trace details) when exceptions are raised inside
    # SimpleJSONRPCRequestHandler.do_POST
    _send_traceback_header = False

    def __init__(self, addr, requestHandler=SimpleJSONRPCRequestHandler,
                 logRequests=True, allow_none=True, encoding=None, bind_and_activate=True):
        self.logRequests = logRequests

        SimpleJSONRPCDispatcher.__init__(self, allow_none, encoding)
        SocketServer.TCPServer.__init__(self, addr, requestHandler, bind_and_activate)

        # [Bug #1222790] If possible, set close-on-exec flag; if a
        # method spawns a subprocess, the subprocess shouldn't have
        # the listening socket open.
        if fcntl is not None and hasattr(fcntl, 'FD_CLOEXEC'):
            flags = fcntl.fcntl(self.fileno(), fcntl.F_GETFD)
            flags |= fcntl.FD_CLOEXEC
            fcntl.fcntl(self.fileno(), fcntl.F_SETFD, flags)

class MultiPathJSONRPCServer(SimpleJSONRPCServer):
    """Multipath JSON-RPC Server
    This specialization of SimpleJSONRPCServer allows the user to create
    multiple Dispatcher instances and assign them to different
    HTTP request paths.  This makes it possible to run two or more
    'virtual JSON-RPC servers' at the same port.
    Make sure that the requestHandler accepts the paths in question.
    """
    def __init__(self, addr, requestHandler=SimpleJSONRPCRequestHandler,
                 logRequests=True, allow_none=True, encoding=None, bind_and_activate=True):

        SimpleJSONRPCServer.__init__(self, addr, requestHandler, logRequests, allow_none,
                                    encoding, bind_and_activate)
        self.dispatchers = {}
        self.allow_none = allow_none
        self.encoding = encoding

    def add_dispatcher(self, path, dispatcher):
        self.dispatchers[path] = dispatcher
        return dispatcher

    def get_dispatcher(self, path):
        return self.dispatchers[path]

    def _marshaled_dispatch(self, data, dispatch_method = None, path = None):
        #print 2222, self.dispatchers
        try:
            response = self.dispatchers[path]._marshaled_dispatch(
               data, dispatch_method, path)
        except:
            # report low level exception back to server
            # (each dispatcher should have handled their own
            # exceptions)
            exc_type, exc_value = sys.exc_info()[:2]
            response = jsonrpclib.dumps(
                jsonrpclib.Fault(1, "%s:%s" % (exc_type, exc_value)), None,
                encoding=self.encoding, allow_none=self.allow_none)
        return response

class CGIJSONRPCRequestHandler(SimpleJSONRPCDispatcher):
    """Simple handler for JSON-RPC data passed through CGI."""

    def __init__(self, allow_none=True, encoding=None):
        SimpleJSONRPCDispatcher.__init__(self, allow_none, encoding)

    def handle_jsonrpc(self, request_text):
        """Handle a single JSON-RPC request"""

        response = jsonrpclib.gzip_encode(self._marshaled_dispatch(request_text))

        print 'Content-Type: application/json'
        print 'Content-Encoding: gzip'
        print 'Content-Length: %d' % len(response)
        print
        sys.stdout.write(response)

    def handle_get(self):
        """Handle a single HTTP GET request.

        Default implementation indicates an error because
        JSON-RPC uses the POST method.
        """

        code = 400
        message, explain = \
                 BaseHTTPServer.BaseHTTPRequestHandler.responses[code]

        response = BaseHTTPServer.DEFAULT_ERROR_MESSAGE % \
            {
             'code' : code,
             'message' : message,
             'explain' : explain
            }
        print 'Status: %d %s' % (code, message)
        print 'Content-Type: %s' % BaseHTTPServer.DEFAULT_ERROR_CONTENT_TYPE
        print 'Content-Length: %d' % len(response)
        print
        sys.stdout.write(response)

    def handle_request(self, request_text = None):
        """Handle a single JSON-RPC request passed through a CGI post method.

        If no JSON data is given then it is read from stdin. The resulting
        JSON-RPC response is printed to stdout along with the correct HTTP
        headers.
        """

        if request_text is None and \
            os.environ.get('REQUEST_METHOD', None) == 'GET':
            self.handle_get()
        else:
            # POST data is normally available through stdin
            try:
                length = int(os.environ.get('CONTENT_LENGTH', None))
            except (TypeError, ValueError):
                length = -1
            if request_text is None:
                request_text = sys.stdin.read(length)

            self.handle_jsonrpc(request_text)

class WSGIJSONRPCRequestHandler(SimpleJSONRPCDispatcher):
    """Simple handler for JSON-RPC data passed through CGI."""

    def __init__(self, allow_none=True, encoding=None):
        SimpleJSONRPCDispatcher.__init__(self, allow_none, encoding)

    def handle_request(self, environ, start_response, request_text = None):
        """Handle a single JSON-RPC request passed through a CGI post method.

        If no JSON data is given then it is read from stdin. The resulting
        JSON-RPC response is printed to stdout along with the correct HTTP
        headers.
        """

        #print 111, environ
        #sys.stdout.flush()

        if request_text is None and environ.get('REQUEST_METHOD', None) == 'GET':
            """Handle a single HTTP GET request.

            Default implementation indicates an error because
            JSON-RPC uses the POST method.
            """

            pq = environ.get("REQUEST_URI", '').split('?', 1)
            if pq[0] and pq[0][-1] == '/':
                pq[0] = pq[0][:-1]
            method =  pq[0].split('/')[-1]
            params = []
            kwargs = {}
            if len(pq) > 1:
                for kv in pq.pop(1).split('&'):
                    kv = kv.split('=', 1)
                    if len(kv) > 1:
                        kwargs[urllib.unquote(kv[0]).strip()] = urllib.unquote(kv[1]).strip()
                    else:
                        params.append(urllib.unquote(kv[0]).strip())
            length = -1
            request_text = jsonrpclib.dumps(params, kwargs, method, methodresponse=None, encoding=None, allow_none=1)

            """Handle a single JSON-RPC request"""

            response = jsonrpclib.gzip_encode(self._marshaled_dispatch(request_text))
            start_response("200 OK", [
                ("Content-Type", "application/json; charset=UTF-8"),
                ("Cache-Control", "no-cache"),
                ("Content-Encoding", "gzip"),
                ("Content-Length", str(len(response))),
            ])
            """
            code = 501
            #code = 400
            message, explain = BaseHTTPServer.BaseHTTPRequestHandler.responses[code]
            response = BaseHTTPServer.DEFAULT_ERROR_MESSAGE % {
                'code': code,
                'message': message,
                'explain': explain,
            }
            start_response("%d %s" % (code, message), [
                ("Content-Type", BaseHTTPServer.DEFAULT_ERROR_CONTENT_TYPE),
                ("Content-Length", str(len(response))),
            ])
            """
        else:
            # POST data is normally available through stdin
            try:
                length = int(environ.get("CONTENT_LENGTH", None))
            except (TypeError, ValueError):
                length = -1
            if request_text is None:
                request_text = environ["wsgi.input"].read(length)
                if "gzip" == environ.get("HTTP_CONTENT_ENCODING", environ.get("CONTENT_ENCODING")):
                    request_text = jsonrpclib.gzip_decode(request_text)

            """Handle a single JSON-RPC request"""

            response = jsonrpclib.gzip_encode(self._marshaled_dispatch(request_text))
            start_response("200 OK", [
                ("Content-Type", "application/json"),
                ("Content-Encoding", "gzip"),
                ("Content-Length", str(len(response))),
            ])
        return [response,]

class MyFuncs:
  def div(self, x, y): return x // y

def test_cgi():
    handler = CGIJSONRPCRequestHandler()
    handler.register_function(pow)
    handler.register_function(lambda x,y: x+y, 'add')
    handler.register_introspection_functions()
    handler.register_instance(MyFuncs())
    handler.handle_request()

def test_wsgi():
    import wsgiref
    import wsgiref.simple_server
    handler = WSGIJSONRPCRequestHandler()
    handler.register_function(pow)
    handler.register_function(lambda x,y: x+y, 'add')
    handler.register_instance(MyFuncs())
    handler.register_function(lambda *a, **kw: ["echo", a, kw], 'echo')
    handler.register_function(lambda *a, **kw: ["echo", a, kw], 'echo1')
    handler.register_function(lambda *a, **kw: ["echo", a, kw], 'echo2')
    handler.register_function(lambda *a, **kw: ["echo", a, kw], 'redis2.echo')
    handler.register_function(lambda *a, **kw: ["echo", a, kw], 'redis3.echo')
    handler.register_multicall_functions()
    handler.register_introspection_functions()
    httpd = wsgiref.simple_server.make_server('', 8602, handler.handle_request)
    print "Serving on port 8602..."
    httpd.serve_forever()
    sys.exit(0)


if __name__ == '__main__':
    test_wsgi()

    print 'Running JSON-RPC server on port 8602'
    server = SimpleJSONRPCServer(("localhost", 8602), logRequests=True, allow_none=True)
    server._send_traceback_header = True
    server.register_function(pow)
    server.register_function(lambda x,y: x+y, 'add')
    server.register_function(lambda *a, **kw: ["echo", a, kw], 'echo')
    server.register_multicall_functions()
    server.register_introspection_functions()
    server.serve_forever()
