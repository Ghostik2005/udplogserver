"""
"""

__version__ = '2017-02-18 0352'

import sys
import socket
sys.HOSTNAME = socket.gethostname()

try:
    sys.FG_DEBUG
except:
    sys.FG_DEBUG = False

try:
    sys.argv.remove("debug")
    sys.FG_DEBUG = True
except:
    pass

try:
    sys.ID_WORKER
except:
    sys.ID_WORKER=-1

try:
    sys.FG_GEVENT
except:
    sys.FG_GEVENT = False
try:
    sys.argv.remove("gevent")
    import gevent
    from gevent import monkey
    monkey.patch_all()
    sys.FG_GEVENT = True
except:
    pass

import os, time, traceback
#import threading

import ms71jsonrpc
import ms71jsonrpc.server
from multiprocessing import Process, Queue, current_process, freeze_support

import socketserver

class SimpleThreadedJSONRPCServer(socketserver.ThreadingMixIn, ms71jsonrpc.server.SimpleJSONRPCServer):
    daemon_threads = True
    pass

class MultiPathThreadedJSONRPCServer(socketserver.ThreadingMixIn, ms71jsonrpc.server.MultiPathJSONRPCServer):
    daemon_threads = True
    pass

def serve_forever(address, number_of_processes=1, init=None):
    freeze_support()
    server = MultiPathThreadedJSONRPCServer(address, logRequests=True, allow_none=True)
    server._send_traceback_header = sys.FG_DEBUG
    server.RequestHandlerClass.rpc_paths = ()
    server.serviceinfo = {"name": None, "api": None, "init": None}
    server.mainqueue = Queue()
    if init:
        server.serviceinfo = init(server)
        if not server.serviceinfo:
            server.serviceinfo = {}
    else:
        d = ms71jsonrpc.server.SimpleJSONRPCDispatcher()
        d.register_multicall_functions()
        d.register_introspection_functions()
        #d.register_instance(api1, True)
        d.register_function(lambda *a, **kw: [a, kw], 'test')
        server.add_dispatcher("/", d)
        server.add_dispatcher("/RPC2", d)

    port = server.server_address[1]
    print("Running JSON-RPC {0} on TCP address {1}:{2}{3}{4}".format(
        server.serviceinfo.get("name", "server"),
        address[0], port,
        " (port auto-assigned)" if 0 == address[1] else "",
        ", gevent on" if sys.FG_GEVENT else ""
    ))
    sys.stdout.flush()
    # create child processes to act as workers
    for i in range(number_of_processes-1):
        Process(target=_serve_forever, args=(server, i+1)).start()

    time.sleep(0.05)
    # main process also acts as a worker
    _serve_forever(server, 0)
    #print "\rdone{0}.".format(id_worker)
    #sys.stdout.flush()
    try: time.sleep(0.05)
    except: pass
    print("\rshutdown")
    sys.stdout.flush()

def _serve_forever(server, _id):
    sys.ID_WORKER = _id
    #sa = server.socket.getsockname()[:2]
    if sys.ID_WORKER:
        print("spawned JSON-RPC worker {0} (pid: {1})".format(sys.ID_WORKER, os.getpid()))
    else:
        print("spawned JSON-RPC master {0} (pid: {1})".format(sys.ID_WORKER, os.getpid()))
    sys.stdout.flush()
    api_init = server.serviceinfo.get("init")
    try:
        if api_init:
            api_init()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except SystemExit:
        pass
    except Exception:
        print("JSON-RPC{0}: {1}".format(sys.ID_WORKER, traceback.format_exc()))
        sys.stdout.flush()
    finally:
        try: server.server_close()
        except: pass
    s = None
    #print "\rdone{0}.".format(ID_WORKER)
    #sys.stdout.flush()


if __name__ == '__main__':
    print("ONLY TEST")
    serve_forever(('', 0), 3)
