# This directory is a Python package.

import sys

__all__ = ['PY3', 'client', 'server', 'ServerProxy', 'MultiCall', 'gzip_encode', 'gzip_decode', 'SimpleJSONRPCDispatcher']

"""
try:
   from . import urllib3
   urllib3.disable_warnings()
   sys.modules['urllib3'] = urllib3
except:
    pass
"""
#from . import requests
#requests.packages.urllib3.disable_warnings()
#sys.modules['requests'] = requests

PY3 = sys.version_info[0] > 2
if PY3:
    from . import _client as client
    from . import _server as server
    from ._client import sse
    from ._client import ServerProxy, MultiCall
    from ._client import gzip_encode, gzip_decode
    from ._server import SimpleJSONRPCDispatcher
    del _client
    del _server
    del sys.modules[__name__ + '._client']
    del sys.modules[__name__ + '._server']
else:
    from .py2 import jsonrpclib as client
    from .py2 import SimpleJSONRPCServer as server
    from .py2.jsonrpclib import sse
    from .py2.jsonrpclib import ServerProxy, MultiCall
    from .py2.jsonrpclib import gzip_encode, gzip_decode
    from .py2.SimpleJSONRPCServer import SimpleJSONRPCDispatcher
    del py2
sys.modules[__name__ + '.client'] = client
sys.modules[__name__ + '.server'] = server
